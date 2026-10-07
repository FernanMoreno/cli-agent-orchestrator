"""Production remote seams use real SQLite claims before native effects."""

import time
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine

from cli_agent_orchestrator.runtime_channel.bridge import Bridge
from cli_agent_orchestrator.runtime_channel.protocol import Command, CommandType
from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore


@pytest.fixture
def journal(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'remote.sqlite'}", connect_args={"check_same_thread": False}
    )
    store = RemoteOperationStore(engine)
    store.create_schema()
    from unittest.mock import Mock

    from cli_agent_orchestrator.backends import registry

    monkeypatch.setattr(registry, "get_backend", lambda: Mock())
    yield store
    engine.dispose()


@pytest.mark.asyncio
async def test_bridge_restart_duplicate_has_one_physical_effect(journal):
    bridge = Bridge("ws://isolated", "rt", "test", store=journal, incarnation_id="inc")
    bridge.admit_epoch(1)
    bridge.execute = AsyncMock(return_value={"success": 1})
    bridge._send = AsyncMock()
    command = Command(
        op_id="op",
        type=CommandType.INPUT,
        terminal_id="term",
        runtime_incarnation_id="inc",
        connection_epoch=1,
        deadline=time.time() + 60,
        payload={"message": "one effect"},
    )
    await bridge.handle(command)
    restarted = Bridge("ws://isolated", "rt", "test", store=journal, incarnation_id="inc")
    restarted.admit_epoch(2)
    restarted.execute = AsyncMock(return_value={"success": 2})
    restarted._send = AsyncMock()
    await restarted.handle(command.model_copy(update={"connection_epoch": 2}))
    bridge.execute.assert_awaited_once()
    restarted.execute.assert_not_awaited()
    assert restarted._send.call_args.args[0].payload == bridge._send.call_args.args[0].payload


@pytest.mark.asyncio
async def test_bridge_expired_or_replaced_identity_never_executes(journal):
    bridge = Bridge("ws://isolated", "rt", "test", store=journal, incarnation_id="inc")
    bridge.admit_epoch(2)
    bridge.execute = AsyncMock()
    bridge._send = AsyncMock()
    for inc, epoch, deadline in [
        ("old", 2, time.time() + 60),
        ("inc", 1, time.time() + 60),
        ("inc", 2, time.time() - 1),
    ]:
        await bridge.handle(
            Command(
                op_id=f"{inc}-{epoch}-{deadline}",
                type=CommandType.KEY,
                terminal_id="term",
                runtime_incarnation_id=inc,
                connection_epoch=epoch,
                deadline=deadline,
                payload={"key": "Enter"},
            )
        )
    bridge.execute.assert_not_awaited()


def test_bridge_bootstrap_has_receipt_and_lifecycle_tables_without_work_authority(
    tmp_path, monkeypatch
):
    from sqlalchemy import inspect

    from cli_agent_orchestrator.clients import database

    engine = create_engine(f"sqlite:///{tmp_path / 'bootstrap.sqlite'}")
    monkeypatch.setattr(database, "engine", engine)
    from cli_agent_orchestrator import constants

    monkeypatch.setattr(constants, "DATABASE_FILE", tmp_path / "bootstrap.sqlite")
    database.init_runtime_db()
    tables = set(inspect(engine).get_table_names())
    assert {
        "terminals",
        "session_incarnations",
        "terminal_turn_receipts",
        "terminal_turn_recovery",
        "native_children",
        "remote_operations",
    } <= tables
    assert "work_attempts" not in tables
    engine.dispose()


def test_remote_input_routes_without_local_provider_or_backend(monkeypatch):
    from unittest.mock import Mock

    from cli_agent_orchestrator.services import remote_terminal_service as remote
    from cli_agent_orchestrator.services import terminal_service

    metadata = {
        "id": "term",
        "tmux_session": "remote-session",
        "tmux_window": "0",
        "provider": "codex",
    }
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda _: metadata)
    monkeypatch.setattr(remote, "placement", lambda _: {"runtime_id": "rt"})
    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", lambda _: None)
    call = Mock(return_value={"turn_sequence": 7})
    monkeypatch.setattr(remote, "call", call)
    backend = Mock(side_effect=AssertionError("remote input touched local backend"))
    monkeypatch.setattr(terminal_service, "get_backend", backend)
    assert terminal_service.send_input("term", "task", task_delivery=True) == 7
    assert call.call_args.kwargs == {}
    assert call.call_args.args[2]["task_delivery"] is True
    backend.assert_not_called()


def test_runtime_router_is_registered_before_static_mount():
    from cli_agent_orchestrator.api.main import app

    paths = [getattr(route, "path", "") for route in app.routes]
    assert "/runtime/channel" in paths
    assert "/runtimes/{runtime_id}/terminals" in paths
    assert paths.index("/runtime/channel") < paths.index("") if "" in paths else True


def test_native_write_rechecks_epoch_after_provider_wait(journal):
    from unittest.mock import Mock

    from cli_agent_orchestrator.runtime_channel import backend as remote_backend

    epoch = journal.activate_runtime("rt", "inc")
    journal.prepare("late-write", "rt", "inc", "launch", {}, deadline=time.time() + 60)
    assert journal.claim("late-write", connection_epoch=epoch)
    native = Mock()
    fence = remote_backend.OrdinaryRemoteEffectFence(native, journal, "late-write", epoch)
    journal.activate_runtime("rt", "inc")
    from cli_agent_orchestrator.runtime_channel.registry import RemoteOutcomeUnknownError

    with pytest.raises(RemoteOutcomeUnknownError):
        fence.send_keys("session", "window", "late provider launch")
    native.send_keys.assert_not_called()


def test_remote_session_delete_never_probes_or_kills_local_session(monkeypatch):
    from unittest.mock import Mock

    from cli_agent_orchestrator.services import remote_terminal_service as remote
    from cli_agent_orchestrator.services import session_service

    monkeypatch.setattr(
        remote,
        "session_terminals",
        lambda _: [{"id": "term", "session_incarnation_id": "session-inc"}],
    )
    deleted = Mock(return_value=True)
    monkeypatch.setattr(remote, "delete_remote", deleted)
    monkeypatch.setattr(
        session_service, "get_backend", Mock(side_effect=AssertionError("local backend touched"))
    )
    result = session_service.delete_session("remote-rt-cao-session")
    assert result["deleted"] == ["remote-rt-cao-session"]
    deleted.assert_called_once_with("term")


def test_bridge_reconstruction_requires_exact_native_ownership(journal, monkeypatch):
    from unittest.mock import Mock

    from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.runtime_channel import bridge as module

    journal.activate_runtime("rt", "old-inc")
    monkeypatch.setattr(
        database,
        "list_all_terminals",
        lambda: [{"id": "term", "tmux_session": "cao-test", "tmux_window": "0"}],
    )
    backend = Mock()
    backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.STILL_PRESENT
    )
    assert module.restore_incarnation("rt", journal, backend) == "old-inc"
    backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.UNKNOWN
    )
    assert module.restore_incarnation("rt", journal, backend) != "old-inc"
    backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.ABSENT
    )
    assert module.restore_incarnation("rt", journal, backend) != "old-inc"


@pytest.mark.asyncio
async def test_cancelled_launch_retains_durable_compensation_request(journal, monkeypatch):
    import asyncio

    from cli_agent_orchestrator.runtime_channel.durable_connection import DurableRuntimeConnection
    from cli_agent_orchestrator.runtime_channel.registry import runtime_registry
    from cli_agent_orchestrator.runtime_channel.server import LaunchRequest, launch_remote

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.work_launch_mode.managed_launch_required", lambda: False
    )
    sent = asyncio.Event()

    async def send(_):
        sent.set()

    epoch = journal.activate_runtime("rt", "inc")
    conn = DurableRuntimeConnection("rt", "inc", epoch, journal, send)
    conn.active = True
    monkeypatch.setattr(runtime_registry, "connection", lambda _: conn)
    task = asyncio.create_task(
        launch_remote("rt", LaunchRequest(agent_profile="review", op_id="cancel-launch"))
    )
    await sent.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert journal.get("cancel-launch")["state"] == "reconcile"
    assert journal.cancellation_requested("cancel-launch") is True
