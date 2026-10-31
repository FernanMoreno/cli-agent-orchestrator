"""Remote input admission belongs to the execution process and physical worker."""

import asyncio
import time
from contextlib import asynccontextmanager
from test.runtime_channel.test_remote_integration import journal
from test.runtime_channel.test_remote_services import pair
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


def bound(store, slots, *, physical_storage_slots=None):
    from cli_agent_orchestrator.services.admission_policy import (
        AdmissionPolicy,
        CapacityObservation,
    )
    from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

    owner = DeliveryAdmission(AdmissionPolicy())

    # A logical input holds an advisory session while it uses a second session
    # for SQL. Keep the original number of deliveries limited by RAM; physical
    # session headroom is a separate quantity, including an explicit denial case.
    memory = 80 * 1024 * 1024 * max(1, slots)
    assert owner.observe(
        CapacityObservation(
            10.0,
            memory,
            memory,
            16000,
            16000,
            2 * slots if physical_storage_slots is None else physical_storage_slots,
        ),
        now=10.0,
        generation=owner.generation,
    )
    assert owner.snapshot(now=10.0).limit == min(
        slots, 2 * slots if physical_storage_slots is None else physical_storage_slots
    )
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
        from cli_agent_orchestrator.clients.postgres import connect

        with connect(journal.target, readonly=True) as connection:
            assert connection.execute("SELECT 42").fetchone()[0] == 42
            assert owner.snapshot(now=10.0).storage_connection_loans == 1
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


@pytest.mark.parametrize("phase", ["next_revision", "settle"])
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.asyncio
async def test_input_credit_survives_physical_journal_completion(pair, monkeypatch, phase, cancel):
    bridge, owner, launched, journal, local, native = await paired(pair, 1)
    placement = remote.placement(launched.id)
    command = input_command(
        terminal_id=launched.id,
        payload={
            "message": "journal completion ownership",
            "task_delivery": True,
            "frozen_memory": "",
            "_launch_op_id": "launch",
            "_session_incarnation_id": placement["session_incarnation_id"],
        },
    )
    entered, release, completed = Event(), Event(), Event()
    original = getattr(journal, phase)

    def physical(*args, **kwargs):
        entered.set()
        try:
            assert release.wait(15), "journal test owner was not released"
            from cli_agent_orchestrator.clients.postgres import connect

            with connect(journal.target, readonly=True) as connection:
                assert connection.execute("SELECT 42").fetchone()[0] == 42
                assert owner.snapshot(now=10.0).storage_connection_loans == 1
            return original(*args, **kwargs)
        finally:
            completed.set()

    monkeypatch.setattr(journal, phase, physical)
    sends_before = native.send_keys.call_count

    async def deliver():
        with local():
            await bridge.handle(command)

    worker = asyncio.create_task(deliver())
    try:
        assert await asyncio.to_thread(entered.wait, 15)
        assert owner.snapshot(now=10.0).started == 1
        assert owner.try_reserve("other", now=10.0).reservation is None
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
        assert completed.is_set()
        assert owner.snapshot(now=10.0).inflight == 0
        expected = "reconcile" if cancel and phase == "next_revision" else "settled"
        assert journal.get(command.op_id)["state"] == expected
        assert native.send_keys.call_count == sends_before + 1
        bridge.admit_epoch(2)
        with local():
            await bridge.handle(command.model_copy(update={"connection_epoch": 2}))
        result = bridge._send.call_args.args[0]
        assert result.ok is (expected == "settled")
        assert native.send_keys.call_count == sends_before + 1
    finally:
        release.set()
        await asyncio.gather(worker, return_exceptions=True)
        assert await asyncio.to_thread(completed.wait, 15)


@pytest.mark.asyncio
async def test_input_credit_retires_before_a_stalled_ack(pair, monkeypatch):
    bridge, owner, launched, journal, local, native = await paired(pair, 1)
    placement = remote.placement(launched.id)
    command = input_command(
        terminal_id=launched.id,
        payload={
            "message": "durable before ack",
            "task_delivery": True,
            "frozen_memory": "",
            "_launch_op_id": "launch",
            "_session_incarnation_id": placement["session_incarnation_id"],
        },
    )
    entered, release = asyncio.Event(), asyncio.Event()
    original_send = bridge._send
    unknown = []
    original_unknown = journal.mark_unknown

    async def paused_ack(result):
        entered.set()
        await release.wait()
        await original_send(result)

    def mark_unknown(op_id):
        unknown.append(op_id)
        return original_unknown(op_id)

    bridge._send = AsyncMock(side_effect=paused_ack)
    monkeypatch.setattr(journal, "mark_unknown", mark_unknown)

    async def deliver():
        with local():
            await bridge.handle(command)

    worker = asyncio.create_task(deliver())
    try:
        await asyncio.wait_for(entered.wait(), 15)
        assert journal.get(command.op_id)["state"] == "settled"
        assert owner.snapshot(now=10.0).inflight == 0
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
        assert unknown == []
        assert owner.snapshot(now=10.0).inflight == 0
    finally:
        release.set()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_unconfirmed_settlement_cannot_ack_success_and_reconciles_under_credit(
    pair, monkeypatch
):
    bridge, owner, launched, journal, local, native = await paired(pair, 1)
    placement = remote.placement(launched.id)
    command = input_command(
        terminal_id=launched.id,
        payload={
            "message": "settlement refused",
            "task_delivery": True,
            "frozen_memory": "",
            "_launch_op_id": "launch",
            "_session_incarnation_id": placement["session_incarnation_id"],
        },
    )
    original_unknown = journal.mark_unknown

    def mark_unknown(op_id):
        assert owner.snapshot(now=10.0).started == 1
        return original_unknown(op_id)

    monkeypatch.setattr(journal, "settle", lambda *args, **kwargs: False)
    monkeypatch.setattr(journal, "mark_unknown", mark_unknown)
    bridge._send.reset_mock()
    writes = native.send_keys.call_count
    with local():
        with pytest.raises(RuntimeError, match="not persisted"):
            await bridge.handle(command)
    assert owner.snapshot(now=10.0).inflight == 0
    assert journal.get(command.op_id)["state"] == "reconcile"
    assert native.send_keys.call_count == writes + 1
    bridge._send.assert_not_awaited()


@pytest.mark.parametrize("after_transport", [False, True])
@pytest.mark.asyncio
async def test_storage_denial_has_typed_effect_boundary_and_never_replays(
    pair, monkeypatch, after_transport
):
    from cli_agent_orchestrator.clients.storage_admission import (
        StorageAdmissionUnavailable,
        note_storage_denial,
    )

    bridge, owner, launched, journal, local, native = await paired(pair, 1)
    placement = remote.placement(launched.id)
    payload = {
        "message": "storage refusal boundary",
        "task_delivery": True,
        "_launch_op_id": "launch",
        "_session_incarnation_id": placement["session_incarnation_id"],
    }
    physical_send = native.send_keys
    writes = physical_send.call_count
    if after_transport:
        payload["frozen_memory"] = ""

        def send(*args, **kwargs):
            result = physical_send(*args, **kwargs)
            note_storage_denial()
            return result

        monkeypatch.setattr(native, "send_keys", send)
    else:
        with terminal_service._memory_injected_lock:
            terminal_service._memory_injected_terminals.discard(launched.id)

        def memory(*args, **kwargs):
            note_storage_denial()
            raise StorageAdmissionUnavailable()

        monkeypatch.setattr(terminal_service, "remote_memory_url", lambda: None)
        monkeypatch.setattr(terminal_service.MemoryService, "get_curated_memory_context", memory)
    command = input_command(terminal_id=launched.id, payload=payload)
    with local():
        await bridge.handle(command)
    result = bridge._send.call_args.args[0]
    assert not result.ok and result.error_kind == "input_blocked"
    assert result.error_detail["delivery_may_have_occurred"] is after_transport
    assert physical_send.call_count == writes + int(after_transport)
    if not after_transport:
        assert launched.id not in terminal_service._memory_injected_terminals
    assert owner.snapshot(now=10.0).inflight == 0
    assert journal.get(command.op_id)["state"] == "settled"
    with local():
        await bridge.handle(command)
    assert bridge._send.call_args.args[0] == result
    assert physical_send.call_count == writes + int(after_transport)


@pytest.mark.parametrize("phase", ["read_terminal", "next_revision"])
@pytest.mark.asyncio
async def test_post_transport_storage_denial_is_reconcile_even_when_swallowed(
    pair, monkeypatch, phase
):
    from cli_agent_orchestrator.clients.storage_admission import note_storage_denial

    bridge, owner, launched, journal, local, native = await paired(pair, 1)
    placement = remote.placement(launched.id)
    command = input_command(
        terminal_id=launched.id,
        payload={
            "message": "post transport refusal",
            "task_delivery": True,
            "frozen_memory": "",
            "_launch_op_id": "launch",
            "_session_incarnation_id": placement["session_incarnation_id"],
        },
    )
    target, name = (
        (terminal_service, "get_terminal") if phase == "read_terminal" else (journal, phase)
    )
    original = getattr(target, name)

    def denied(*args, **kwargs):
        result = original(*args, **kwargs)
        note_storage_denial()
        return result

    monkeypatch.setattr(target, name, denied)
    writes = native.send_keys.call_count
    with local():
        await bridge.handle(command)
    result = bridge._send.call_args.args[0]
    assert not result.ok and result.error_kind == "input_blocked"
    assert result.error_detail["delivery_may_have_occurred"] is True
    assert native.send_keys.call_count == writes + 1
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.asyncio
async def test_pre_transport_denial_preserves_prior_memory_injection_marker(pair, monkeypatch):
    from cli_agent_orchestrator.clients.storage_admission import note_storage_denial

    bridge, owner, launched, journal, local, native = await paired(pair, 1)
    placement = remote.placement(launched.id)
    command = input_command(
        terminal_id=launched.id,
        payload={
            "message": "keep prior marker",
            "task_delivery": True,
            "frozen_memory": "",
            "_launch_op_id": "launch",
            "_session_incarnation_id": placement["session_incarnation_id"],
        },
    )
    original = terminal_service.status_monitor.clear_rolling_buffer

    def denied(*args, **kwargs):
        result = original(*args, **kwargs)
        note_storage_denial()
        return result

    monkeypatch.setattr(terminal_service.status_monitor, "clear_rolling_buffer", denied)
    with terminal_service._memory_injected_lock:
        terminal_service._memory_injected_terminals.add(launched.id)
    writes = native.send_keys.call_count
    try:
        with local():
            await bridge.handle(command)
        result = bridge._send.call_args.args[0]
        assert not result.ok and result.error_detail["delivery_may_have_occurred"] is False
        assert launched.id in terminal_service._memory_injected_terminals
        assert native.send_keys.call_count == writes
        assert owner.snapshot(now=10.0).inflight == 0
    finally:
        with terminal_service._memory_injected_lock:
            terminal_service._memory_injected_terminals.discard(launched.id)


@pytest.mark.asyncio
async def test_input_two_session_requirement_denies_before_turn_or_worker(journal, monkeypatch):
    bridge, owner = bound(journal, 1, physical_storage_slots=1)
    bridge.execute = AsyncMock()

    @asynccontextmanager
    async def forbidden(_):
        pytest.fail("input without its two physical session credits acquired turn")
        yield

    monkeypatch.setattr(bridge, "_terminal_turn", forbidden)
    await bridge.handle(input_command())
    result = bridge._send.call_args.args[0]
    assert not result.ok and result.error_kind == "input_blocked"
    assert result.error_detail["delivery_may_have_occurred"] is False
    assert owner.snapshot(now=10.0).inflight == 0
    bridge.execute.assert_not_awaited()


def physical_input_command(launched):
    placement = remote.placement(launched.id)
    return input_command(
        terminal_id=launched.id,
        payload={
            "message": "preflight storage ownership",
            "task_delivery": True,
            "frozen_memory": "",
            "_launch_op_id": "launch",
            "_session_incarnation_id": placement["session_incarnation_id"],
        },
    )


@pytest.mark.parametrize("phase", ["effect_is_current", "ensure_terminal_is_not_work_owned", "get"])
@pytest.mark.asyncio
async def test_input_preflight_owns_actual_sql_before_send(pair, monkeypatch, phase):
    from cli_agent_orchestrator.clients.postgres import connect

    bridge, owner, launched, journal, local, native = await paired(pair, 1)
    command = physical_input_command(launched)
    target = terminal_service if phase == "ensure_terminal_is_not_work_owned" else journal
    original = getattr(target, phase)
    observed = []

    def query(*args, **kwargs):
        snapshot = owner.snapshot(now=10.0)
        assert snapshot.started == 1
        with connect(journal.target, readonly=True) as connection:
            assert connection.execute("SELECT 42").fetchone()[0] == 42
            assert owner.snapshot(now=10.0).storage_connection_loans >= 1
        observed.append(phase)
        return original(*args, **kwargs)

    writes = native.send_keys.call_count
    with monkeypatch.context() as patch:
        patch.setattr(target, phase, query)
        with local():
            await bridge.handle(command)
    assert observed
    assert bridge._send.call_args.args[0].ok
    assert native.send_keys.call_count == writes + 1
    assert owner.snapshot(now=10.0).inflight == 0
    assert journal.get(command.op_id)["state"] == "settled"


@pytest.mark.asyncio
async def test_input_preflight_checkout_denial_has_no_effect_and_replays(pair, monkeypatch):
    from cli_agent_orchestrator.services.delivery_admission import DeliveryReservation

    bridge, owner, launched, journal, local, native = await paired(pair, 1)
    command = physical_input_command(launched)
    entering = Event()
    original_guard = terminal_service.ensure_terminal_is_not_work_owned
    original_checkout = DeliveryReservation.try_acquire_storage_connection
    denied = []
    queries = []

    def query(*args, **kwargs):
        queries.append(True)
        if len(queries) == 1:
            entering.set()
        try:
            return original_guard(*args, **kwargs)
        finally:
            entering.clear()

    def checkout(lease, *args, **kwargs):
        if lease._owner is owner and entering.is_set():
            denied.append(True)
            return None
        return original_checkout(lease, *args, **kwargs)

    writes = native.send_keys.call_count
    with monkeypatch.context() as patch:
        patch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", query)
        patch.setattr(DeliveryReservation, "try_acquire_storage_connection", checkout)
        with local():
            await bridge.handle(command)
    result = bridge._send.call_args.args[0]
    assert denied
    assert not result.ok and result.error_kind == "input_blocked"
    assert result.error_detail["delivery_may_have_occurred"] is False
    assert result.error_detail["action"] == "inspect"
    assert native.send_keys.call_count == writes
    assert owner.snapshot(now=10.0).inflight == 0
    assert journal.get(command.op_id)["state"] == "settled"
    with local():
        await bridge.handle(command)
    assert bridge._send.call_args.args[0] == result
    assert native.send_keys.call_count == writes


@pytest.mark.asyncio
async def test_input_preflight_cancellation_joins_real_reader(pair, monkeypatch):
    bridge, owner, launched, journal, local, native = await paired(pair, 1)
    command = physical_input_command(launched)
    entered, release = Event(), Event()
    original = terminal_service.ensure_terminal_is_not_work_owned

    def query(*args, **kwargs):
        value = original(*args, **kwargs)
        entered.set()
        assert release.wait(15), "preflight reader was not released"
        return value

    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", query)
    writes = native.send_keys.call_count
    bridge._send.reset_mock()

    async def deliver():
        with local():
            await bridge.handle(command)

    worker = asyncio.create_task(deliver())
    try:
        assert await asyncio.to_thread(entered.wait, 15)
        assert owner.snapshot(now=10.0).started == 1
        worker.cancel()
        await asyncio.sleep(0)
        worker.cancel()
        await asyncio.sleep(0)
        assert not worker.done()
        assert owner.snapshot(now=10.0).started == 1
        bridge._send.assert_not_awaited()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(worker), 15)
        assert owner.snapshot(now=10.0).inflight == 0
        assert journal.get(command.op_id)["state"] == "reconcile"
        assert native.send_keys.call_count == writes
    finally:
        release.set()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_input_capability_is_exact_identity_and_single_use(pair, monkeypatch):
    from copy import copy

    bridge, owner, launched, journal, local, native = await paired(pair, 1)
    command = physical_input_command(launched)
    execute = bridge.execute
    checks = []

    async def checked(command, *, _admitted_input=None):
        with pytest.raises(TerminalInputBlockedError):
            await execute(command, _admitted_input=copy(_admitted_input))
        checks.append("copy rejected")
        genuine = asyncio.create_task(execute(command, _admitted_input=_admitted_input))
        try:
            await asyncio.sleep(0)
            with pytest.raises(TerminalInputBlockedError):
                await execute(command, _admitted_input=_admitted_input)
            checks.append("concurrent reuse rejected")
            result = await genuine
        finally:
            await asyncio.gather(genuine, return_exceptions=True)
        with pytest.raises(TerminalInputBlockedError):
            await execute(command, _admitted_input=_admitted_input)
        checks.append("reuse rejected")
        return result

    monkeypatch.setattr(bridge, "execute", checked)
    writes = native.send_keys.call_count
    with local():
        await bridge.handle(command)
    assert checks == ["copy rejected", "concurrent reuse rejected", "reuse rejected"]
    assert bridge._send.call_args.args[0].ok
    assert native.send_keys.call_count == writes + 1
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.asyncio
async def test_input_advisory_checkout_denial_precedes_transport(pair, monkeypatch):
    from cli_agent_orchestrator.services.delivery_admission import DeliveryReservation

    bridge, owner, launched, journal, local, native = await paired(pair, 1)
    command = physical_input_command(launched)
    entering = Event()
    original_send = terminal_service.send_input
    original_checkout = DeliveryReservation.try_acquire_storage_connection
    denied = []

    def send_input(*args, **kwargs):
        entering.set()
        try:
            return original_send(*args, **kwargs)
        finally:
            entering.clear()

    def checkout(lease, *args, **kwargs):
        if lease._owner is owner and entering.is_set():
            denied.append(True)
            return None
        return original_checkout(lease, *args, **kwargs)

    writes = native.send_keys.call_count
    with monkeypatch.context() as patch:
        patch.setattr(terminal_service, "send_input", send_input)
        patch.setattr(DeliveryReservation, "try_acquire_storage_connection", checkout)
        with local():
            await bridge.handle(command)
    result = bridge._send.call_args.args[0]
    assert denied
    assert not result.ok and result.error_kind == "input_blocked"
    assert result.error_detail["delivery_may_have_occurred"] is False
    assert result.error_detail["action"] == "inspect"
    assert native.send_keys.call_count == writes
    assert owner.snapshot(now=10.0).inflight == 0
    assert journal.get(command.op_id)["state"] == "settled"
    with local():
        await bridge.handle(command)
    assert bridge._send.call_args.args[0] == result
    assert native.send_keys.call_count == writes
