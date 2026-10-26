"""Actual launch admission must precede teardown of an initializing terminal."""

import asyncio
import threading
from test.services.test_terminal_service_inbox_registration import TERMINAL_ID, create_mocks
from unittest.mock import Mock, patch

import pytest

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers import manager as providers
from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider
from cli_agent_orchestrator.services import provider_lifecycle as lifecycle
from cli_agent_orchestrator.services import remote_terminal_service
from cli_agent_orchestrator.services import terminal_service as module


@pytest.fixture(autouse=True)
def isolated_startup_registry(monkeypatch):
    monkeypatch.setattr(lifecycle, "_startups", {})
    monkeypatch.setattr(lifecycle, "_retirements", {})
    monkeypatch.setattr(module, "_active_deferred_init_external_owner_ids", set())
    monkeypatch.setattr(module, "_initial_task_pending_ids", set())


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["constructor", "initializer"])
@pytest.mark.parametrize("deferred_policy", [None, True, False])
async def test_actual_creation_refuses_delete_before_backend_effects(
    create_mocks, monkeypatch, gate, deferred_policy
):
    manager = providers.ProviderManager()
    before_deferred = set(module._deferred_init_tasks)
    monkeypatch.setattr(
        module, "_deferred_failure_delete_from_creation", lambda *_a, **_k: deferred_policy
    )
    create_mocks.provider_manager.create_provider.side_effect = manager.create_provider
    monkeypatch.setattr(providers, "registered_provider_descriptor", lambda _p: None)
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _k: None)

    def publish_row(*args, **kwargs):
        assert args[0] == TERMINAL_ID
        assert TERMINAL_ID in lifecycle._startups
        return db.create_terminal(*args, **kwargs)

    create_mocks.db_create_terminal.side_effect = publish_row
    create_mocks.backend.kill_window.return_value = True
    monkeypatch.setattr(module, "capture_terminal_snapshot", db.get_terminal_metadata)
    fifo, monitor = Mock(), Mock()
    monkeypatch.setattr(module, "fifo_manager", fifo)
    monkeypatch.setattr(module, "status_monitor", monitor)
    entered, release = threading.Event(), threading.Event()
    candidate = None

    def build(*args, **kwargs):
        nonlocal candidate
        candidate = ClaudeCodeProvider(*args, **kwargs)
        candidate.cleanup = Mock(return_value=True)
        candidate.get_status = Mock(return_value=TerminalStatus.IDLE)

        async def initialize():
            if gate == "initializer":
                entered.set()
                assert await asyncio.to_thread(release.wait, 5)
            return True

        candidate.initialize = initialize
        if gate == "constructor":
            entered.set()
            assert release.wait(timeout=5)
        return candidate

    monkeypatch.setattr(providers, "ClaudeCodeProvider", build)
    task = asyncio.create_task(
        module.create_terminal(
            provider="claude_code",
            agent_profile="developer",
            session_name="cao-test-session",
            defer_init=deferred_policy is not None,
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert db.get_terminal_metadata(TERMINAL_ID) is not None
        assert await asyncio.to_thread(module.delete_terminal, TERMINAL_ID) is False
        create_mocks.backend.kill_window.assert_not_called()
        fifo.stop_reader.assert_not_called()
        monitor.clear_terminal.assert_not_called()
        assert db.get_terminal_metadata(TERMINAL_ID) is not None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(
            *(module._deferred_init_tasks - before_deferred), return_exceptions=True
        )
    terminal = task.result()
    assert terminal.id == TERMINAL_ID
    assert manager._providers[terminal.id] is candidate

    assert lifecycle._startups == {}
    assert lifecycle._retirements == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_cancelled_initializer_retains_row_until_worker_stop_is_proven(
    create_mocks, monkeypatch, deferred
):
    create_mocks.db_create_terminal.side_effect = db.create_terminal
    monkeypatch.setattr(module, "_deferred_failure_delete_from_creation", lambda *_a, **_k: False)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    before = set(module._deferred_init_tasks)

    def blocking():
        entered.set()
        try:
            assert release.wait(timeout=5)
        finally:
            finished.set()

    async def initialize():
        await asyncio.to_thread(blocking)

    create_mocks.provider_manager.create_provider.return_value.initialize = initialize
    task = asyncio.create_task(
        module.create_terminal(
            provider="claude_code",
            agent_profile="developer",
            session_name="cao-test-session",
            defer_init=deferred,
        )
    )
    target = task
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        if deferred:
            await task
            (target,) = module._deferred_init_tasks - before
        target.cancel()
        await asyncio.gather(target, return_exceptions=True)
        await asyncio.sleep(0)
        assert not finished.is_set()
        assert lifecycle._startups[TERMINAL_ID].uncertain
        assert db.get_terminal_metadata(TERMINAL_ID) is not None
        assert await asyncio.to_thread(module.delete_terminal, TERMINAL_ID) is False
        create_mocks.backend.kill_window.assert_not_called()
    finally:
        release.set()
        await asyncio.gather(task, target, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 5)
    # Even a late worker's return is not a general proof of all provider work;
    # this conservative owner survives until process-owner reconciliation.
    assert lifecycle._startups[TERMINAL_ID].uncertain


@pytest.mark.asyncio
async def test_deferred_owner_can_delete_its_own_failure(create_mocks, monkeypatch):
    create_mocks.db_create_terminal.side_effect = db.create_terminal
    monkeypatch.setattr(module, "_deferred_failure_delete_from_creation", lambda *_a, **_k: True)
    before = set(module._deferred_init_tasks)
    observed = []

    async def fail():
        raise RuntimeError("controlled initialization failure")

    async def surface(terminal_id, **_kwargs):
        with lifecycle.retirement_scope(terminal_id) as admitted:
            observed.append(admitted)
        assert module.delete_terminal_row(terminal_id, db.get_terminal_metadata(terminal_id))

    create_mocks.provider_manager.create_provider.return_value.initialize = fail
    monkeypatch.setattr(module, "_surface_deferred_init_failure", surface)
    await module.create_terminal(
        provider="claude_code",
        agent_profile="developer",
        session_name="cao-test-session",
        defer_init=True,
    )
    await asyncio.gather(*(module._deferred_init_tasks - before))
    assert observed == [True]
    assert db.get_terminal_metadata(TERMINAL_ID) is None
    assert lifecycle._startups == lifecycle._retirements == {}


@pytest.mark.asyncio
async def test_cancelled_constructor_finishes_before_releasing_startup(create_mocks):
    create_mocks.db_create_terminal.side_effect = db.create_terminal
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    candidate = create_mocks.provider_manager.create_provider.return_value

    def build(*_args, **_kwargs):
        entered.set()
        try:
            assert release.wait(timeout=5)
            return candidate
        finally:
            finished.set()

    create_mocks.provider_manager.create_provider.side_effect = build
    task = asyncio.create_task(
        module.create_terminal(
            provider="claude_code", agent_profile="developer", session_name="cao-test-session"
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert TERMINAL_ID in lifecycle._startups
        assert await asyncio.to_thread(module.delete_terminal, TERMINAL_ID) is False
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled() and finished.is_set()
    candidate.initialize.assert_not_awaited()
    assert lifecycle._startups == lifecycle._retirements == {}
    assert db.get_terminal_metadata(TERMINAL_ID) is not None


@pytest.mark.asyncio
async def test_deleted_registration_cannot_publish_actual_launch(create_mocks, monkeypatch):
    create_mocks.db_create_terminal.side_effect = db.create_terminal
    manager = providers.ProviderManager()
    create_mocks.provider_manager.create_provider.side_effect = manager.create_provider
    monkeypatch.setattr(providers, "registered_provider_descriptor", lambda _p: None)
    entered, release = threading.Event(), threading.Event()

    def build(*args, **kwargs):
        candidate = ClaudeCodeProvider(*args, **kwargs)
        entered.set()
        assert release.wait(timeout=5)
        return candidate

    monkeypatch.setattr(providers, "ClaudeCodeProvider", build)
    task = asyncio.create_task(
        module.create_terminal(
            provider="claude_code", agent_profile="developer", session_name="cao-test-session"
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert db.get_terminal_metadata(TERMINAL_ID) is not None
        assert db.delete_terminal(TERMINAL_ID)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    with pytest.raises(ValueError, match="registration changed"):
        task.result()
    assert manager._providers == manager._restore_locks == {}
    assert lifecycle._startups == lifecycle._retirements == {}


@pytest.mark.parametrize(
    "path",
    ["session", "flow", "stale", "row", "manager", "herdr_ghost", "herdr_retain", "herdr_empty"],
)
def test_foreign_startup_refuses_other_cleanup_entries_before_effects(
    isolated_memory_db, monkeypatch, tmp_path, path
):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from cli_agent_orchestrator.services import cleanup_service, flow_service, session_service

    key, session = "active-cleanup", "cao-active-cleanup"
    db.create_terminal(
        key, session, "worker", "codex", "reviewer", session_incarnation_id="active-generation"
    )
    if path in {"session", "flow"}:
        db.create_terminal(
            "0-idle-peer",
            session,
            "peer",
            "codex",
            "reviewer",
            session_incarnation_id="active-generation",
        )
    owner = lifecycle.reserve_startup(key)
    backend, fifo, monitor = MagicMock(), MagicMock(), MagicMock()
    from cli_agent_orchestrator.backends import registry

    monkeypatch.setattr(registry, "_backend", backend)
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _k: None)
    manager = providers.ProviderManager()
    monkeypatch.setattr(module, "provider_manager", manager)
    monkeypatch.setattr(module, "fifo_manager", fifo)
    monkeypatch.setattr(module, "status_monitor", monitor)
    try:
        if path == "session":
            monkeypatch.setattr(remote_terminal_service, "session_terminals", lambda _s: None)
            monkeypatch.setattr(session_service, "get_backend", lambda: backend)
            monkeypatch.setattr(
                session_service, "terminal_dispatch_lock", lambda *_a: nullcontext()
            )
            result = session_service.delete_session(session)
            assert result["deleted"] == [] and result["errors"]
        elif path == "flow":
            monkeypatch.setattr(flow_service, "get_backend", lambda: backend)
            monkeypatch.setattr(flow_service, "fifo_manager", fifo)
            monkeypatch.setattr(flow_service, "status_monitor", monitor)
            monkeypatch.setattr(flow_service, "_verified_flow_terminal_rows", lambda *_a: [])
            monkeypatch.setattr(
                flow_service,
                "list_current_session_terminals",
                lambda *_a, **_k: db.list_terminals_by_session(session),
            )
            assert flow_service._recycle_flow_session("active", session) is False
        elif path == "stale":
            fake_db, factory = MagicMock(), MagicMock()
            fake_db.query.return_value.filter.return_value.all.return_value = [
                SimpleNamespace(id=key)
            ]
            fake_db.query.return_value.filter.return_value.delete.return_value = 0
            factory.return_value.__enter__.return_value = fake_db
            monkeypatch.setattr(cleanup_service, "SessionLocal", factory)
            monkeypatch.setattr(cleanup_service, "fifo_manager", fifo)
            monkeypatch.setattr(cleanup_service, "status_monitor", monitor)
            monkeypatch.setattr(cleanup_service, "delete_old_handoff_results", lambda **_k: 0)
            monkeypatch.setattr(
                module, "should_retain_deferred_failure_tombstone", lambda *_a: False
            )
            for name in ("LOG_DIR", "TERMINAL_LOG_DIR", "MEMORY_BASE_DIR"):
                monkeypatch.setattr(cleanup_service, name, tmp_path / name)
            cleanup_service.cleanup_old_data()
        elif path.startswith("herdr"):
            from cli_agent_orchestrator.services.herdr_inbox_service import (
                HerdrInboxService,
                _retain_deferred_failure_tombstone_unlocked,
            )

            if path == "herdr_ghost":
                with pytest.raises(RuntimeError, match="startup"):
                    HerdrInboxService._delete_ghost_terminal(key)
            elif path == "herdr_retain":
                assert _retain_deferred_failure_tombstone_unlocked(key) is True
            else:
                assert db.delete_terminal(key)
                assert HerdrInboxService._kill_empty_session_if_unowned(session, {key}) is False
        elif path == "row":
            assert module.delete_terminal_row(key, db.get_terminal_metadata(key)) is False
        else:
            assert manager.cleanup_provider(key) is False
        backend.kill_session.assert_not_called()
        backend.kill_window.assert_not_called()
        backend.cleanup_terminal_exact.assert_not_called()
        fifo.stop_reader.assert_not_called()
        monitor.clear_terminal.assert_not_called()
        assert (db.get_terminal_metadata(key) is not None) == (path != "herdr_empty")
    finally:
        lifecycle.finish_startup(owner)
    assert lifecycle._startups == lifecycle._retirements == {}


@pytest.mark.asyncio
async def test_bridge_refuses_native_cleanup_of_an_active_startup(isolated_memory_db, monkeypatch):
    from unittest.mock import MagicMock

    from cli_agent_orchestrator.backends import registry
    from cli_agent_orchestrator.runtime_channel.bridge import Bridge
    from cli_agent_orchestrator.runtime_channel.protocol import Command, CommandType

    key, generation = "active-bridge", "bridge-generation"
    db.create_terminal(
        key, "cao-bridge", "worker", "codex", "reviewer", session_incarnation_id=generation
    )
    owner = lifecycle.reserve_startup(key)
    backend = MagicMock()
    monkeypatch.setattr(registry, "_backend", backend)
    store = MagicMock()
    store.get.return_value = {
        "command_type": "launch",
        "state": "settled",
        "result": {
            "ok": True,
            "payload": {"terminal": {"id": key, "session_incarnation_id": generation}},
        },
    }
    bridge = Bridge("ws://unused", "runtime", "unused-test-token", store=store)
    command = Command(
        op_id="delete-active",
        type=CommandType.DELETE,
        terminal_id=key,
        payload={"_launch_op_id": "launch-active", "_session_incarnation_id": generation},
    )
    try:
        assert await bridge.execute(command) == {"deleted": False}
        backend.cleanup_terminal_exact.assert_not_called()
        assert db.get_terminal_metadata(key) is not None
    finally:
        lifecycle.finish_startup(owner)


@pytest.mark.asyncio
async def test_cancelled_creation_compensator_keeps_real_db_worker_owned(create_mocks):
    from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult

    entered, release = threading.Event(), threading.Event()

    def publish(*args, **kwargs):
        result = db.create_terminal(*args, **kwargs)
        entered.set()
        assert release.wait(timeout=5)
        return result

    create_mocks.db_create_terminal.side_effect = publish
    create_mocks.backend.kill_window.return_value = True
    create_mocks.backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.ABSENT
    )
    task = asyncio.create_task(
        module.create_terminal(
            provider="claude_code", agent_profile="developer", session_name="cao-test-session"
        )
    )
    compensator = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        for _ in range(10):
            await asyncio.sleep(0)
            active = list(module._create_compensations)
            if active:
                (compensator,) = active
                break
        assert compensator is not None
        assert not isinstance(compensator, asyncio.Task)
        task.cancel()
        await asyncio.sleep(0)
        assert not compensator.done()
        assert TERMINAL_ID in lifecycle._startups
        assert db.get_terminal_metadata(TERMINAL_ID) is not None
    finally:
        release.set()
        await asyncio.gather(task, *([compensator] if compensator else []), return_exceptions=True)
    assert db.get_terminal_metadata(TERMINAL_ID) is None
    assert lifecycle._startups == lifecycle._retirements == {}
    assert not module._create_compensations


@pytest.mark.asyncio
async def test_real_deferred_failure_notify_delete_inherits_owner(create_mocks, monkeypatch):
    from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult

    create_mocks.db_create_terminal.side_effect = db.create_terminal
    create_mocks.backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.ABSENT
    )
    monkeypatch.setattr(module, "_deferred_failure_delete_from_creation", lambda *_a, **_kw: True)
    inbox = Mock()
    monkeypatch.setattr(module, "create_inbox_message", inbox)
    before = set(module._deferred_init_tasks)

    async def fail():
        raise RuntimeError("controlled real deferred failure")

    create_mocks.provider_manager.create_provider.return_value.initialize = fail
    await module.create_terminal(
        provider="claude_code",
        agent_profile="developer",
        session_name="cao-test-session",
        caller_id="supervisor",
        defer_init=True,
    )
    await asyncio.gather(*(module._deferred_init_tasks - before))
    inbox.assert_called_once()
    assert inbox.call_args.kwargs["receiver_id"] == "supervisor"
    create_mocks.backend.cleanup_terminal_exact.assert_called()
    assert db.get_terminal_metadata(TERMINAL_ID) is None
    assert lifecycle._startups == lifecycle._retirements == {}


@pytest.mark.asyncio
async def test_recreate_can_transfer_its_retired_stale_id_to_startup(create_mocks, tmp_path):
    with patch.object(module, "TERMINAL_LOG_DIR", tmp_path):
        db.create_terminal(
            TERMINAL_ID,
            "cao-test-session",
            "stale-window",
            "claude_code",
            "developer",
            session_incarnation_id="stale-generation",
        )
        create_mocks.db_create_terminal.side_effect = db.create_terminal
        create_mocks.backend.session_exists.return_value = False
        create_mocks.backend.create_session.return_value = "replacement-window"
        terminal = await module.create_terminal(
            provider="claude_code",
            agent_profile="developer",
            session_name="cao-test-session",
            new_session=True,
        )
        assert terminal.id == TERMINAL_ID
        row = db.get_terminal_metadata(TERMINAL_ID)
        assert row["tmux_window"] == create_mocks.generate_window_name.return_value
        assert row["tmux_window"] != "stale-window"
        assert row["session_incarnation_id"] != "stale-generation"
        assert lifecycle._startups == lifecycle._retirements == {}


@pytest.mark.asyncio
async def test_post_initialize_cancel_waits_for_owned_db_worker_then_releases(
    create_mocks, monkeypatch
):
    create_mocks.db_create_terminal.side_effect = db.create_terminal
    entered, release = threading.Event(), threading.Event()
    original = module.transition_native_child

    def transition(key, state, **kwargs):
        if state == "acknowledged":
            entered.set()
            assert release.wait(timeout=5)
        return original(key, state, **kwargs)

    monkeypatch.setattr(module, "transition_native_child", transition)
    task = asyncio.create_task(
        module.create_terminal(
            provider="claude_code", agent_profile="developer", session_name="cao-test-session"
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert not lifecycle._startups[TERMINAL_ID].uncertain
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert lifecycle._startups == lifecycle._retirements == {}
    assert db.get_terminal_metadata(TERMINAL_ID) is not None


@pytest.mark.asyncio
async def test_deferred_post_create_callback_has_no_rollback_authority(create_mocks, monkeypatch):
    create_mocks.db_create_terminal.side_effect = db.create_terminal
    monkeypatch.setattr(module, "_deferred_failure_delete_from_creation", lambda *_a, **_k: True)
    attempts = []
    release = asyncio.Event()
    before = set(module._deferred_init_tasks)

    async def initialize():
        await release.wait()

    def callback(_registry, event, payload):
        if event == "post_create_terminal":
            attempts.append(module.delete_terminal(payload.terminal_id))

    create_mocks.provider_manager.create_provider.return_value.initialize = initialize
    create_mocks.dispatch_plugin_event.side_effect = callback
    try:
        await module.create_terminal(
            provider="claude_code",
            agent_profile="developer",
            session_name="cao-test-session",
            defer_init=True,
        )
        assert attempts == [False]
        create_mocks.backend.kill_window.assert_not_called()
        create_mocks.backend.cleanup_terminal_exact.assert_not_called()
        assert db.get_terminal_metadata(TERMINAL_ID) is not None
    finally:
        release.set()
        await asyncio.gather(*(module._deferred_init_tasks - before), return_exceptions=True)
    assert lifecycle._startups == lifecycle._retirements == {}


@pytest.mark.asyncio
async def test_reporting_error_after_handoff_cannot_rollback_deferred_owner(
    create_mocks, monkeypatch
):
    create_mocks.db_create_terminal.side_effect = db.create_terminal
    before = set(module._deferred_init_tasks)
    release = asyncio.Event()

    async def initialize():
        await release.wait()

    create_mocks.provider_manager.create_provider.return_value.initialize = initialize
    monkeypatch.setattr(
        module, "Terminal", Mock(side_effect=ValueError("controlled reporting error"))
    )
    try:
        with pytest.raises(ValueError, match="reporting error"):
            await module.create_terminal(
                provider="claude_code",
                agent_profile="developer",
                session_name="cao-test-session",
                defer_init=True,
            )
        assert db.get_terminal_metadata(TERMINAL_ID) is not None
        assert TERMINAL_ID in lifecycle._startups
        create_mocks.backend.kill_window.assert_not_called()
        create_mocks.backend.kill_session.assert_not_called()
        create_mocks.provider_manager.cleanup_provider.assert_not_called()
    finally:
        release.set()
        await asyncio.gather(*(module._deferred_init_tasks - before), return_exceptions=True)
    assert lifecycle._startups == lifecycle._retirements == {}


@pytest.mark.offline_database
def test_startup_fixtures_restore_service_objects_and_lazy_imports():
    """A passing isolated test must not leave mocks for later service tests."""
    from pathlib import Path

    from cli_agent_orchestrator.backends import registry
    from cli_agent_orchestrator.services import turn_recovery_service

    assert isinstance(module.TERMINAL_LOG_DIR, Path)
    assert not isinstance(module.provider_manager, Mock)
    assert not isinstance(module.status_monitor, Mock)
    assert turn_recovery_service.get_backend is registry.get_backend
    assert (
        turn_recovery_service.ensure_terminal_is_not_work_owned
        is module.ensure_terminal_is_not_work_owned
    )


@pytest.mark.asyncio
async def test_herdr_reconcile_keeps_active_startup_routing(isolated_memory_db, monkeypatch):
    from unittest.mock import MagicMock

    from cli_agent_orchestrator.backends import registry
    from cli_agent_orchestrator.services.herdr_inbox_service import HerdrInboxService

    key, session = "active-herdr-pane", "cao-active-herdr"
    db.create_terminal(
        key, session, "initializing", "codex", "reviewer", session_incarnation_id="herdr-generation"
    )
    owner = lifecycle.reserve_startup(key)
    backend = MagicMock()
    monkeypatch.setattr(registry, "_backend", backend)
    service = HerdrInboxService(socket_path="/unused-test.sock")
    service.register_terminal(key, "stale-pane", is_kiro=True)
    service._working_since[key] = 1.0
    monkeypatch.setattr(
        service, "_fetch_snapshot", lambda: {"panes": [], "tabs": [], "workspaces": []}
    )
    monkeypatch.setattr(service, "_label_still_live", lambda _window: False)
    try:
        await service._reconcile()
        assert db.get_terminal_metadata(key) is not None
        assert service._terminal_to_pane[key] == "stale-pane"
        assert service._pane_to_terminal["stale-pane"] == key
        assert key in service._working_since and key in service._kiro_terminals
        backend.kill_session.assert_not_called()
        backend.cleanup_terminal_exact.assert_not_called()
    finally:
        lifecycle.finish_startup(owner)
    assert lifecycle._retirements == {}
