"""Parent assignment and Herdr retirement share real session/PG lock ordering."""

import asyncio
import threading
from contextlib import contextmanager
from pathlib import Path
from test.services.test_terminal_service_inbox_registration import TERMINAL_ID, create_mocks
from unittest.mock import Mock, patch

import pytest

from cli_agent_orchestrator.backends import registry
from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult
from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.services import herdr_inbox_service as herdr
from cli_agent_orchestrator.services import terminal_service as terminal
from cli_agent_orchestrator.services import work_terminal

PARENT = "lock-order-parent"
SESSION = "cao-parent-lock-order"
WINDOW = "parent-window"


@pytest.fixture
def logged_create_mocks(create_mocks, tmp_path):
    # Exit before create_mocks restores its own patch. A later monkeypatch
    # teardown would resurrect that fixture's MagicMock for subsequent tests.
    before = terminal.TERMINAL_LOG_DIR
    with patch.object(terminal, "TERMINAL_LOG_DIR", tmp_path):
        yield create_mocks
    assert terminal.TERMINAL_LOG_DIR is before


@pytest.mark.asyncio
async def test_parent_creation_allows_herdr_to_finish_while_waiting_for_session(
    logged_create_mocks, monkeypatch, caplog
):
    """Both actors use production locks; no timeout or lock implementation is replaced."""
    assert db.engine.dialect.name == "postgresql"
    db.create_terminal(PARENT, SESSION, WINDOW, "claude_code", "developer")
    m = logged_create_mocks
    m.db_create_terminal.side_effect = db.create_terminal
    monkeypatch.setattr(registry, "_backend", m.backend)
    m.backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.UNKNOWN
    )
    service = herdr.HerdrInboxService(socket_path="/tmp/caos-lock-order-unused.sock")
    ghost_has_session = threading.Event()
    create_attempted_session = threading.Event()
    ghost_acquired_dispatch = threading.Event()
    observed = []
    real_session_lock = terminal.session_lifecycle_lock
    real_dispatch_lock = work_terminal.terminal_dispatch_lock

    def snapshot():
        # Called by the actual Herdr helper after its session lock and DB read.
        ghost_has_session.set()
        assert create_attempted_session.wait(15), "creation never attempted its session lock"
        return {"workspaces": [{"workspace_id": "workspace", "label": SESSION}], "tabs": []}

    monkeypatch.setattr(service, "_fetch_snapshot", snapshot)

    @contextmanager
    def create_session_lock(name):
        if name == SESSION:
            observed.append("create_session_attempt")
            create_attempted_session.set()
        with real_session_lock(name):
            if name == SESSION:
                observed.append("create_session_acquired")
            yield

    @contextmanager
    def create_dispatch_lock(target, key):
        with real_dispatch_lock(target, key):
            if key == PARENT:
                observed.append("create_dispatch_acquired")
            yield

    @contextmanager
    def ghost_dispatch_lock(target, key):
        with real_dispatch_lock(target, key):
            if key == PARENT:
                observed.append("ghost_dispatch_acquired")
                ghost_acquired_dispatch.set()
            yield

    monkeypatch.setattr(terminal, "session_lifecycle_lock", create_session_lock)
    monkeypatch.setattr(terminal, "terminal_dispatch_lock", create_dispatch_lock)
    monkeypatch.setattr(work_terminal, "terminal_dispatch_lock", ghost_dispatch_lock)
    ghost_task = asyncio.create_task(
        terminal._await_provider_operation(service._cleanup_ghost_session, SESSION)
    )
    create_task = None
    try:
        assert await asyncio.to_thread(ghost_has_session.wait, 15)
        create_task = asyncio.create_task(
            terminal.create_terminal(
                provider="claude_code",
                agent_profile="developer",
                session_name=SESSION,
                caller_id=PARENT,
                fence_parent=True,
            )
        )
        ghost_result, child = await asyncio.gather(ghost_task, create_task)
    finally:
        # Drain real owned workers even if a gate/assertion fails.
        create_attempted_session.set()
        await asyncio.gather(
            ghost_task, *([create_task] if create_task else []), return_exceptions=True
        )

    assert (
        ghost_acquired_dispatch.is_set()
    ), f"Herdr lost dispatch admission: {observed}; {caplog.text}"
    assert observed.index("create_session_acquired") < observed.index("create_dispatch_acquired")
    assert observed.index("ghost_dispatch_acquired") < observed.index("create_session_acquired")
    assert "timed out waiting for terminal dispatch lock" not in caplog.text
    assert ghost_result[:3] == (set(), {PARENT}, {PARENT})
    m.backend.cleanup_terminal_exact.assert_called_once_with(PARENT, SESSION, WINDOW, close=False)
    assert child.id == TERMINAL_ID
    assert db.get_terminal_metadata(PARENT) is not None
    child_row = db.get_terminal_metadata(TERMINAL_ID)
    assert child_row["caller_id"] == PARENT
    assert child_row["tmux_session"] == SESSION
    m.provider_manager.create_provider.return_value.initialize.assert_awaited_once()
    m.backend.kill_session.assert_not_called()
    m.backend.kill_window.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["incarnation", "generation", "session"])
async def test_fenced_parent_rejection_precedes_backend_effects(
    logged_create_mocks, monkeypatch, changed
):
    db.create_terminal(PARENT, SESSION, WINDOW, "claude_code", "developer")
    m = logged_create_mocks
    m.db_create_terminal.side_effect = db.create_terminal
    options = dict(
        provider="claude_code",
        agent_profile="developer",
        session_name=SESSION,
        caller_id=PARENT,
        fence_parent=True,
    )
    if changed == "incarnation":
        options["expected_parent_incarnation_id"] = "a" * 32
    elif changed == "generation":
        options["expected_parent_generation"] = "b" * 32
    else:
        options["session_name"] = SESSION + "-other"
    with pytest.raises(ValueError, match="assignment parent"):
        await terminal.create_terminal(**options)
    m.backend.session_exists.assert_not_called()
    m.backend.create_window.assert_not_called()
    m.backend.create_session.assert_not_called()
    m.db_create_terminal.assert_not_called()
    m.provider_manager.create_provider.assert_not_called()
    assert db.get_terminal_metadata(PARENT) is not None
    assert db.get_terminal_metadata(TERMINAL_ID) is None


@pytest.mark.asyncio
async def test_fenced_parent_sql_failure_rolls_back_child_under_both_locks(
    logged_create_mocks, monkeypatch
):
    db.create_terminal(PARENT, SESSION, WINDOW, "claude_code", "developer")
    m = logged_create_mocks
    failure = RuntimeError("controlled child registration failure")
    m.db_create_terminal.side_effect = failure
    held = set()
    rollback_locks = []
    real_session_lock = terminal.session_lifecycle_lock
    real_dispatch_lock = terminal.terminal_dispatch_lock

    @contextmanager
    def session_lock(name):
        with real_session_lock(name):
            held.add("session")
            try:
                yield
            finally:
                held.remove("session")

    @contextmanager
    def dispatch_lock(target, key):
        with real_dispatch_lock(target, key):
            held.add("dispatch")
            try:
                yield
            finally:
                held.remove("dispatch")

    def rollback(*_args, **_kwargs):
        # The production rollback logs/catches backend exceptions, including
        # AssertionError. Inspect the snapshot outside that catch boundary.
        rollback_locks.append(frozenset(held))

    monkeypatch.setattr(terminal, "session_lifecycle_lock", session_lock)
    monkeypatch.setattr(terminal, "terminal_dispatch_lock", dispatch_lock)
    m.backend.kill_window.side_effect = rollback
    with pytest.raises(RuntimeError, match="controlled child registration failure") as raised:
        await terminal.create_terminal(
            provider="claude_code",
            agent_profile="developer",
            session_name=SESSION,
            caller_id=PARENT,
            fence_parent=True,
        )
    assert raised.value is failure
    assert rollback_locks == [frozenset({"session", "dispatch"})]
    m.backend.kill_window.assert_called_once()
    m.backend.kill_session.assert_not_called()
    m.provider_manager.create_provider.assert_not_called()
    assert held == set()
    assert db.get_terminal_metadata(PARENT) is not None
    assert db.get_terminal_metadata(TERMINAL_ID) is None


@pytest.mark.offline_database
def test_parent_assignment_fixtures_restore_service_objects():
    """Later admission checks must see the actual path and service objects."""
    assert isinstance(terminal.TERMINAL_LOG_DIR, Path)
    assert not isinstance(terminal.provider_manager, Mock)
    assert not isinstance(terminal.status_monitor, Mock)
