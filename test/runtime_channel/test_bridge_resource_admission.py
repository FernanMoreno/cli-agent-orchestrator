"""Remote input admission belongs to the execution process and physical worker."""

import asyncio
import time
from contextlib import asynccontextmanager
from test.runtime_channel.test_remote_integration import journal
from test.runtime_channel.test_remote_services import pair
from test.services.test_inbox_resource_admission import service
from threading import Event
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.backends.registry import get_backend as configured_backend_port
from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError
from cli_agent_orchestrator.runtime_channel.bridge import Bridge
from cli_agent_orchestrator.runtime_channel.durable_connection import DurableRuntimeConnection
from cli_agent_orchestrator.runtime_channel.protocol import Command, CommandType, decode
from cli_agent_orchestrator.runtime_channel.registry import runtime_registry
from cli_agent_orchestrator.runtime_channel.server import (
    LaunchRequest,
    accept_result,
    launch_remote,
)
from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore
from cli_agent_orchestrator.services import remote_terminal_service as remote
from cli_agent_orchestrator.services import terminal_service

pytestmark = pytest.mark.postgres


def bound(store, slots):
    _, owner = service(slots)
    bridge = Bridge(
        "ws://isolated",
        "rt",
        "test",
        store=store,
        incarnation_id="inc",
        resource_admission=owner,
        resource_clock=lambda: 10.0,
    )
    bridge.admit_epoch(1)
    bridge._send = AsyncMock()
    return bridge, owner


def input_command(**changes):
    return Command(
        op_id="resource-input",
        type=CommandType.INPUT,
        terminal_id="term",
        runtime_incarnation_id="inc",
        connection_epoch=1,
        deadline=time.time() + 60,
        payload={"message": "controlled input"},
    ).model_copy(update=changes)


@pytest.mark.asyncio
async def test_error_mapping_keeps_canonical_backend_port_after_lazy_import(journal):
    bridge, _ = bound(journal, 0)
    await bridge.handle(input_command())
    from cli_agent_orchestrator.services import turn_recovery_service

    # Replacing the imported function can leave a stale fake in a lazily loaded
    # consumer after fixture teardown and bypass its real ContextVar fence.
    assert turn_recovery_service.get_backend is configured_backend_port


@pytest.mark.asyncio
async def test_denial_is_settled_before_terminal_turn_and_preserves_no_effect(journal, monkeypatch):
    bridge, owner = bound(journal, 0)
    bridge.execute = AsyncMock()

    @asynccontextmanager
    async def forbidden(_):
        pytest.fail("denied input acquired the terminal turn")
        yield

    monkeypatch.setattr(bridge, "_terminal_turn", forbidden)
    command = input_command()
    await bridge.handle(command)
    result = bridge._send.call_args.args[0]
    assert not result.ok and result.error_kind == "input_blocked"
    assert result.error_detail["delivery_may_have_occurred"] is False
    assert journal.get(command.op_id)["state"] == "settled"
    assert owner.snapshot(now=10.0).inflight == 0
    bridge.execute.assert_not_awaited()
    bridge.admit_epoch(2)
    await bridge.handle(command.model_copy(update={"connection_epoch": 2}))
    assert bridge._send.call_args.args[0] == result
    bridge.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_reservation_precedes_turn_and_is_cancelled_when_no_worker_starts(
    journal, monkeypatch
):
    bridge, owner = bound(journal, 1)

    @asynccontextmanager
    async def inspect(_):
        assert owner.snapshot(now=10.0).reserved == 1
        assert owner.try_reserve("other", now=10.0).reservation is None
        yield

    monkeypatch.setattr(bridge, "_terminal_turn", inspect)
    bridge.execute = AsyncMock(return_value={"success": 1})
    await bridge.handle(input_command())
    assert bridge._send.call_args.args[0].ok
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.asyncio
async def test_bound_execute_cannot_bypass_handle_reservation(journal):
    bridge, owner = bound(journal, 1)
    with pytest.raises(TerminalInputBlockedError) as error:
        await bridge.execute(input_command())
    assert error.value.delivery_may_have_occurred is False
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.asyncio
async def test_failed_capability_construction_retires_unstarted_reservation(journal, monkeypatch):
    from cli_agent_orchestrator.runtime_channel import bridge as bridge_module

    bridge, owner = bound(journal, 1)
    bridge.execute = AsyncMock()

    def unavailable(*args):
        raise RuntimeError("controlled capability construction failure")

    monkeypatch.setattr(bridge_module, "_AdmittedInput", unavailable)
    await bridge.handle(input_command())
    assert not bridge._send.call_args.args[0].ok
    assert owner.snapshot(now=10.0).inflight == 0
    bridge.execute.assert_not_awaited()


async def paired(pair, slots):
    central_target, bridge_target, _, _, local, native, _ = pair
    central = RemoteOperationStore(central_target)
    journal = RemoteOperationStore(bridge_target)
    epoch = central.activate_runtime("rt", "inc")
    bridge, owner = bound(journal, slots)
    assert epoch == bridge.connection_epoch
    results = []
    bridge._send = AsyncMock(side_effect=results.append)

    async def send(raw):
        with local():
            await bridge.handle(decode(raw))
        await accept_result(connection, results.pop())

    connection = DurableRuntimeConnection("rt", "inc", epoch, central, send)
    runtime_registry.register("rt", send, connection=connection)
    runtime_registry.activate(connection)
    launched = await launch_remote(
        "rt", LaunchRequest(agent_profile="review", provider="codex", op_id="launch")
    )
    return bridge, owner, launched, journal, local, native


@pytest.mark.asyncio
async def test_paired_resource_refusal_preserves_typed_no_effect_at_central(pair):
    bridge, owner, launched, journal, _, native = await paired(pair, 0)
    writes = native.send_keys.call_count
    with pytest.raises(TerminalInputBlockedError) as error:
        await asyncio.to_thread(
            terminal_service.send_input, launched.id, "denied", frozen_memory="", task_delivery=True
        )
    assert error.value.delivery_may_have_occurred is False
    assert native.send_keys.call_count == writes
    assert owner.snapshot(now=10.0).inflight == 0
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.models.inbox import MessageStatus
    from cli_agent_orchestrator.services.inbox_service import InboxService

    queued = database.create_inbox_message("sender", launched.id, "pending resource task")
    inbox = InboxService()
    await asyncio.to_thread(inbox.deliver_pending, launched.id)
    with database.SessionLocal() as session:
        assert session.get(database.InboxModel, queued.id).status == MessageStatus.PENDING.value
    assert native.send_keys.call_count == writes
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.asyncio
async def test_paired_physical_input_keeps_lease_through_receipts_and_cancellation(
    pair, monkeypatch, cancel
):
    bridge, owner, launched, journal, local, native = await paired(pair, 1)
    placement = remote.placement(launched.id)
    command = input_command(
        terminal_id=launched.id,
        payload={
            "message": "physical task",
            "task_delivery": True,
            "frozen_memory": "",
            "_launch_op_id": "launch",
            "_session_incarnation_id": placement["session_incarnation_id"],
        },
    )
    entered, release = Event(), Event()
    original = terminal_service.send_input
    effects = []

    def send_input(*args, **kwargs):
        assert owner.snapshot(now=10.0).started == 1
        entered.set()
        assert release.wait(15), "physical test input was not released"
        value = original(*args, **kwargs)
        effects.append(value)
        assert owner.snapshot(now=10.0).started == 1
        return value

    monkeypatch.setattr(terminal_service, "send_input", send_input)

    async def deliver():
        with local():
            await bridge.handle(command)

    worker = asyncio.create_task(deliver())
    try:
        assert await asyncio.to_thread(entered.wait, 15)
        assert owner.snapshot(now=10.0).started == 1
        if cancel:
            worker.cancel()
            await asyncio.sleep(0)
            worker.cancel()
            await asyncio.sleep(0)
            assert not worker.done()
            assert owner.snapshot(now=10.0).started == 1
        release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(worker), 15)
        else:
            await asyncio.wait_for(asyncio.shield(worker), 15)
            assert bridge._send.call_args.args[0].ok
        assert len(effects) == 1
        assert owner.snapshot(now=10.0).inflight == 0
        assert journal.get(command.op_id)["state"] == ("reconcile" if cancel else "settled")
        bridge.admit_epoch(2)
        with local():
            await bridge.handle(command.model_copy(update={"connection_epoch": 2}))
        assert len(effects) == 1
        assert bridge._send.call_args.args[0].error_kind == ("outcome_unknown" if cancel else None)
    finally:
        release.set()
        await asyncio.gather(worker, return_exceptions=True)
