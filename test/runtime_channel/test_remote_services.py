"""Paired channel exercises real central routing, native services and providers."""

import asyncio
import contextlib
import re
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.runtime_channel_schema import RemoteBase
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers import codex
from cli_agent_orchestrator.providers.manager import ProviderManager
from cli_agent_orchestrator.runtime_channel.bridge import Bridge
from cli_agent_orchestrator.runtime_channel.durable_connection import DurableRuntimeConnection
from cli_agent_orchestrator.runtime_channel.protocol import CommandType, decode
from cli_agent_orchestrator.runtime_channel.registry import runtime_registry
from cli_agent_orchestrator.runtime_channel.server import (
    LaunchRequest,
    accept_result,
    launch_remote,
)
from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore
from cli_agent_orchestrator.services import remote_terminal_service as remote
from cli_agent_orchestrator.services import terminal_service
from cli_agent_orchestrator.services.status_monitor import status_monitor


@pytest.fixture
def pair(tmp_path, monkeypatch):
    engines = [
        create_engine(f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False})
        for name in ["central.sqlite", "bridge.sqlite"]
    ]
    database.Base.metadata.create_all(engines[0])
    RemoteBase.metadata.create_all(engines[0])
    # The physical runtime must work with the real minimal initializer, not a
    # test-only full central schema that could conceal missing dependencies.
    with monkeypatch.context() as boot:
        boot.setattr(database, "engine", engines[1])
        boot.setattr(constants, "DATABASE_FILE", tmp_path / "bridge.sqlite")
        database.init_runtime_db()
    monkeypatch.setattr(constants, "LOCK_DIR", tmp_path / "locks")
    monkeypatch.setattr(constants, "DATABASE_FILE", tmp_path / "central.sqlite")
    monkeypatch.setattr(database, "engine", engines[0])
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engines[0]))
    monkeypatch.setattr(codex, "CAO_HOME_DIR", tmp_path / "home")
    monkeypatch.setattr(terminal_service, "TERMINAL_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(terminal_service, "clear_session_env", lambda _: None)
    monkeypatch.setattr(terminal_service, "get_session_env", lambda _: {})
    monkeypatch.setattr(terminal_service, "get_herdr_inbox_service", lambda: None)
    monkeypatch.setattr(terminal_service, "managed_launch_required", lambda: False)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.work_launch_mode.managed_launch_required", lambda: False
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.agent_plugins.mcp_delivery.apply_plugin_mcp_servers",
        lambda *a, **k: None,
    )
    profile = AgentProfile(name="review", description="Review", model="model-one", allowedTools=[])
    monkeypatch.setattr(terminal_service, "load_agent_profile", lambda _: profile)
    monkeypatch.setattr(codex, "load_agent_profile", lambda _: profile)
    monkeypatch.setattr(codex, "_with_plugin_mcp", lambda loaded, *args, **kwargs: loaded)
    monkeypatch.setattr(codex, "wait_for_shell", AsyncMock(return_value=True))
    monkeypatch.setattr(codex, "wait_until_status", AsyncMock(return_value=True))
    monkeypatch.setattr(codex.CodexProvider, "_handle_trust_prompt", AsyncMock())
    real_sleep = asyncio.sleep

    async def quick_sleep(_):
        await real_sleep(0)

    monkeypatch.setattr(codex.asyncio, "sleep", quick_sleep)
    manager = ProviderManager()
    monkeypatch.setattr(terminal_service, "provider_manager", manager)
    from cli_agent_orchestrator.services import turn_recovery_service

    monkeypatch.setattr(turn_recovery_service, "provider_manager", manager)
    monkeypatch.setattr(status_monitor, "get_status", lambda _: TerminalStatus.IDLE)
    monkeypatch.setattr(status_monitor, "get_buffer", lambda _: "")
    monkeypatch.setattr(status_monitor, "publish_observed_status", Mock())
    native = Mock()
    native.session_exists.return_value = False
    native.supports_event_inbox.return_value = True
    native.get_pane_working_directory.return_value = str(tmp_path)
    native.get_pane_current_command.return_value = "bash"
    native.get_native_status.return_value = None
    transcript = [""]

    def write(session, window, message, *args, **kwargs):
        if "codex " in message:
            native.get_pane_current_command.return_value = "codex"
        match = re.search(r"CAO-TURN-RECEIPT-[0-9a-f]{32}", message)
        if match:
            transcript[0] = (
                f"› task\n• remote answer\n{match.group(0)}\n\n  done 1:43 PM\n\n"
                "  Approaching rate limits\n  Switch to gpt-5.6-luna for lower credit usage?\n\n"
                "› 1. Switch to gpt-5.6-luna                 Fast and affordable model.\n"
                "  2. Keep current model\n  3. Keep current model (never show again)\n\n"
                "  Press enter to confirm or esc to go back\n"
            )

    native.send_keys.side_effect = write
    native.get_history.side_effect = lambda *a, **k: transcript[0]
    from cli_agent_orchestrator.backends import registry

    monkeypatch.setattr(registry, "_backend", native)
    from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult

    native.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.DELETED
    )
    old = (database.engine, database.SessionLocal, constants.DATABASE_FILE)

    @contextlib.contextmanager
    def local():
        before = (database.engine, database.SessionLocal, constants.DATABASE_FILE)
        database.engine, database.SessionLocal, constants.DATABASE_FILE = (
            engines[1],
            sessionmaker(bind=engines[1]),
            tmp_path / "bridge.sqlite",
        )
        try:
            yield
        finally:
            database.engine, database.SessionLocal, constants.DATABASE_FILE = before

    yield engines, local, native, transcript
    for engine in engines:
        engine.dispose()
    runtime_registry._runtimes.clear()
    runtime_registry._placement.clear()
    runtime_registry._status.clear()


@pytest.mark.asyncio
async def test_paired_real_services_launch_input_output_key_directory_exit_delete(pair):
    engines, local, native, transcript = pair
    central = RemoteOperationStore(engines[0])
    journal = RemoteOperationStore(engines[1])
    epoch = central.activate_runtime("rt", "inc")
    bridge = Bridge("ws://isolated", "rt", "test", store=journal, incarnation_id="inc")
    bridge.admit_epoch(epoch)
    results = []
    bridge._send = AsyncMock(side_effect=lambda result: results.append(result))

    async def send(raw):
        with local():
            await bridge.handle(decode(raw))
        await accept_result(connection, results.pop())

    connection = DurableRuntimeConnection("rt", "inc", epoch, central, send)
    runtime_registry.register("rt", send, connection=connection)
    runtime_registry.activate(connection)
    launched = await launch_remote(
        "rt",
        LaunchRequest(
            agent_profile="review",
            provider="codex",
            allowed_tools=[],
            model="closed-model",
            op_id="launch",
        ),
    )
    assert launched.runtime_id == "rt" and launched.allowed_tools == []
    assert native.create_session.call_count == 1
    command = native.send_keys.call_args.args[2]
    assert "closed-model" in command
    terminal_id = launched.id
    sequence = await asyncio.to_thread(
        terminal_service.send_input, terminal_id, "task", frozen_memory="", task_delivery=True
    )
    assert isinstance(sequence, int) and sequence > 0
    turn = (await asyncio.to_thread(terminal_service.get_terminal, terminal_id))["turn"]
    assert isinstance(turn, dict) and turn["generation"]
    output = await asyncio.to_thread(
        terminal_service.get_output,
        terminal_id,
        terminal_service.OutputMode.LAST,
        expected_generation=turn["generation"],
    )
    assert output == "remote answer"
    assert await asyncio.to_thread(terminal_service.get_working_directory, terminal_id)
    assert await asyncio.to_thread(terminal_service.send_special_key, terminal_id, "Escape")
    await asyncio.to_thread(terminal_service.exit_terminal_cli, terminal_id)
    native.kill_window.reset_mock()
    assert await asyncio.to_thread(terminal_service.delete_terminal, terminal_id)
    native.kill_window.assert_not_called()
    assert remote.placement(terminal_id) is None


@pytest.mark.asyncio
async def test_paired_verify_and_duplicate_cancel_reset_once_without_task_redelivery(pair):
    engines, local, native, _ = pair
    central, journal = RemoteOperationStore(engines[0]), RemoteOperationStore(engines[1])
    epoch = central.activate_runtime("rt", "inc")
    bridge = Bridge("ws://isolated", "rt", "test", store=journal, incarnation_id="inc")
    bridge.admit_epoch(epoch)
    results = []
    bridge._send = AsyncMock(side_effect=lambda result: results.append(result))

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
    await asyncio.to_thread(
        terminal_service.send_input, launched.id, "task", frozen_memory="", task_delivery=True
    )
    from cli_agent_orchestrator.services import turn_recovery_service as recovery

    generation = remote.terminal_projection(launched.id)["turn"]["generation"]
    verified = await asyncio.to_thread(recovery.verify_turn, launched.id, generation)
    assert verified["state"] == "verified"
    await asyncio.to_thread(
        terminal_service.send_input,
        launched.id,
        "second task",
        frozen_memory="",
        task_delivery=True,
    )
    row = remote.placement(launched.id)
    generation = remote.terminal_projection(launched.id)["turn"]["generation"]
    payload = {"_launch_op_id": "launch", "_session_incarnation_id": row["session_incarnation_id"]}
    before = sum("CAO-TURN-RECEIPT-" in call.args[2] for call in native.send_keys.call_args_list)
    first = await connection.call(
        CommandType.CANCEL, payload, launched.id, generation=generation, op_id="cancel-once"
    )
    assert first["turn"]["state"] == "cancelled"
    assert (
        await connection.call(
            CommandType.CANCEL, payload, launched.id, generation=generation, op_id="cancel-once"
        )
        == first
    )
    assert native.reset_window.call_count == 1
    assert (
        sum("CAO-TURN-RECEIPT-" in call.args[2] for call in native.send_keys.call_args_list)
        == before
    )


@pytest.mark.asyncio
async def test_response_loss_reconnect_uses_real_bridge_receipt_without_another_paste(pair):
    engines, local, native, _ = pair
    central, journal = RemoteOperationStore(engines[0]), RemoteOperationStore(engines[1])
    epoch = central.activate_runtime("rt", "inc")
    bridge = Bridge("ws://isolated", "rt", "test", store=journal, incarnation_id="inc")
    bridge.admit_epoch(epoch)
    results = []
    bridge._send = AsyncMock(side_effect=lambda result: results.append(result))
    lose = [False]

    async def send(raw):
        with local():
            await bridge.handle(decode(raw))
        if not lose[0]:
            await accept_result(connection, results.pop())

    connection = DurableRuntimeConnection("rt", "inc", epoch, central, send)
    runtime_registry.register("rt", send, connection=connection)
    runtime_registry.activate(connection)
    launched = await launch_remote(
        "rt", LaunchRequest(agent_profile="review", provider="codex", op_id="launch")
    )
    row = remote.placement(launched.id)
    payload = {
        "message": "task",
        "task_delivery": True,
        "frozen_memory": "",
        "_launch_op_id": "launch",
        "_session_incarnation_id": row["session_incarnation_id"],
    }
    lose[0] = True
    from cli_agent_orchestrator.runtime_channel.registry import RemoteOutcomeUnknownError

    with pytest.raises(RemoteOutcomeUnknownError):
        await connection.call(
            CommandType.INPUT, payload, launched.id, op_id="input-once", timeout=1
        )
    writes = len(native.send_keys.call_args_list)
    epoch = central.activate_runtime("rt", "inc")
    restarted = Bridge("ws://isolated", "rt", "test", store=journal, incarnation_id="inc")
    restarted.admit_epoch(epoch)
    replacement = DurableRuntimeConnection("rt", "inc", epoch, central, send)
    replacement.active = True
    await replacement.resolve_durable(results.pop())
    settled = await replacement.call(CommandType.INPUT, payload, launched.id, op_id="input-once")
    assert settled["turn_sequence"] > 0
    assert len(native.send_keys.call_args_list) == writes
    assert journal.get("input-once")["state"] == "settled"


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", [False, True])
async def test_actual_inbox_remote_unsent_pending_or_postsend_reconcile(pair, monkeypatch, lost):
    engines, local, native, _ = pair
    central, journal = RemoteOperationStore(engines[0]), RemoteOperationStore(engines[1])
    epoch = central.activate_runtime("rt", "inc")
    bridge = Bridge("ws://isolated", "rt", "test", store=journal, incarnation_id="inc")
    bridge.admit_epoch(epoch)
    results = []
    bridge._send = AsyncMock(side_effect=lambda result: results.append(result))
    drop = [False]

    async def send(raw):
        with local():
            await bridge.handle(decode(raw))
        if not drop[0]:
            await accept_result(connection, results.pop())

    connection = DurableRuntimeConnection("rt", "inc", epoch, central, send)
    runtime_registry.register("rt", send, connection=connection)
    runtime_registry.activate(connection)
    launched = await launch_remote(
        "rt", LaunchRequest(agent_profile="review", provider="codex", op_id="launch")
    )
    original_call = connection.call

    async def bounded(*args, **kwargs):
        kwargs["timeout"] = 1
        return await original_call(*args, **kwargs)

    monkeypatch.setattr(connection, "call", bounded)
    if lost:
        drop[0] = True
    else:
        runtime_registry.unregister("rt", connection)
    from cli_agent_orchestrator.models.inbox import MessageStatus
    from cli_agent_orchestrator.services.inbox_service import InboxService

    queued = database.create_inbox_message("sender", launched.id, "queued task")
    service = InboxService()
    await asyncio.to_thread(service.deliver_pending, launched.id)
    with database.SessionLocal() as db:
        assert db.get(database.InboxModel, queued.id).status == (
            MessageStatus.RECONCILE.value if lost else MessageStatus.PENDING.value
        )
    before = len(native.send_keys.call_args_list)
    await asyncio.to_thread(service.deliver_pending, launched.id)
    assert len(native.send_keys.call_args_list) == before


@pytest.mark.asyncio
async def test_late_cancelled_launch_exact_compensation_cannot_resurrect(pair):
    engines, local, native, _ = pair
    central, journal = RemoteOperationStore(engines[0]), RemoteOperationStore(engines[1])
    epoch = central.activate_runtime("rt", "inc")
    bridge = Bridge("ws://isolated", "rt", "test", store=journal, incarnation_id="inc")
    bridge.admit_epoch(epoch)
    results = []
    bridge._send = AsyncMock(side_effect=lambda result: results.append(result))

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
    from cli_agent_orchestrator.runtime_channel import server
    from cli_agent_orchestrator.runtime_channel.protocol import Result

    proof = Result.model_validate(central.get("launch")["result"])
    central.request_cancellation("launch")
    await accept_result(connection, proof)
    await asyncio.gather(*list(server._compensations.values()))
    assert remote.placement(launched.id) is None
    assert database.get_terminal_metadata(launched.id) is None
    assert native.cleanup_terminal_exact.call_count == 1
    native.kill_window.assert_not_called()
    assert await accept_result(connection, proof)
    assert remote.placement(launched.id) is None
    assert native.cleanup_terminal_exact.call_count == 1
