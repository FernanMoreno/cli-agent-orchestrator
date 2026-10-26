"""Regression draft: ordinary init errors must not retire a live runtime."""

from pathlib import Path
from test.integration.test_cancelled_worktree_creation import repo
from test.services.test_terminal_service_inbox_registration import (
    SESSION_NAME,
    TERMINAL_ID,
    WINDOW_NAME,
    create_mocks,
)
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult
from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.services import terminal_service, worktree_service


@pytest.mark.asyncio
@pytest.mark.parametrize("new_session", [False, True])
@pytest.mark.parametrize(
    ("stop_confirmed", "cleanup", "row_result"),
    [
        pytest.param(False, True, "delete", id="stop-unproven-cleanup-complete"),
        pytest.param(False, False, "delete", id="stop-unproven-cleanup-deferred"),
        pytest.param(True, True, "delete", id="stop-acknowledged-cleanup-complete"),
        pytest.param(True, False, "delete", id="stopped-provider-defers"),
        pytest.param(True, "error", "delete", id="stopped-provider-errors"),
        pytest.param(True, True, "refuse", id="stopped-row-refuses"),
        pytest.param(True, True, "error", id="stopped-row-errors"),
        pytest.param("probe-error", True, "delete", id="native-proof-errors"),
    ],
)
async def test_sync_initializer_error_retains_worktree_until_backend_stop_is_proven(
    create_mocks, repo, monkeypatch, stop_confirmed, cleanup, row_result, new_session
):
    """Real PG/Git, control backend and ordinary synchronous init failure only."""
    assert db.engine.dialect.name == "postgresql"
    backend = create_mocks.backend
    backend.session_exists.return_value = not new_session
    window = create_mocks.generate_window_name.return_value if new_session else WINDOW_NAME
    backend.create_session.return_value = window
    runtime = {"window_alive": True}

    if stop_confirmed is True:

        def acknowledge_stop(*_args):
            runtime["window_alive"] = False
            return True

        backend.kill_window.side_effect = acknowledge_stop
        backend.kill_session.side_effect = acknowledge_stop
        backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
            TerminalCleanupOutcome.ABSENT
        )
    else:

        def fail_to_stop(*_args):
            raise RuntimeError("controlled backend stop failure")

        backend.kill_window.side_effect = fail_to_stop
        backend.kill_session.side_effect = fail_to_stop
        backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
            TerminalCleanupOutcome.STILL_PRESENT
        )

    if stop_confirmed == "probe-error":
        backend.cleanup_terminal_exact.side_effect = RuntimeError("controlled proof failure")
    if cleanup == "error":
        create_mocks.provider_manager.cleanup_provider.side_effect = RuntimeError("cleanup failed")
    else:
        create_mocks.provider_manager.cleanup_provider.return_value = cleanup
    if row_result == "refuse":
        refused = Mock(return_value=False)
        monkeypatch.setattr(terminal_service, "delete_terminal_row", refused)
    elif row_result == "error":
        refused = Mock(side_effect=RuntimeError("controlled SQL failure"))
        monkeypatch.setattr(terminal_service, "delete_terminal_row", refused)
    init_error = RuntimeError("controlled synchronous provider initialization failure")

    async def initialize():
        # The real DB delegate below confirms creation was committed before init.
        assert db.get_terminal_metadata(TERMINAL_ID) is not None
        raise init_error

    create_mocks.provider_manager.create_provider.return_value.initialize = initialize
    worktree_path = Path(worktree_service.worktree_path_for(str(repo), TERMINAL_ID))

    with pytest.raises(RuntimeError) as raised:
        await terminal_service.create_terminal(
            provider="claude_code",
            agent_profile="developer",
            session_name=SESSION_NAME,
            new_session=new_session,
            working_directory=str(repo),
            use_worktree=True,
        )

    assert raised.value is init_error
    if new_session:
        backend.create_session.assert_called_once()
        backend.kill_session.assert_called_once_with(SESSION_NAME)
        backend.kill_window.assert_not_called()
    else:
        backend.create_window.assert_called_once()
        backend.kill_window.assert_called_once_with(SESSION_NAME, WINDOW_NAME)
        backend.kill_session.assert_not_called()
    backend.cleanup_terminal_exact.assert_called_once_with(
        TERMINAL_ID, SESSION_NAME, window, close=False
    )
    if stop_confirmed is True:
        create_mocks.provider_manager.cleanup_provider.assert_called_once_with(TERMINAL_ID)
    else:
        create_mocks.provider_manager.cleanup_provider.assert_not_called()
    if row_result != "delete":
        refused.assert_called_once()

    assert runtime["window_alive"] is (stop_confirmed is not True)

    if stop_confirmed is True and cleanup is True and row_result == "delete":
        assert db.get_terminal_metadata(TERMINAL_ID) is None
        assert not worktree_path.exists()
    else:
        assert db.get_terminal_metadata(TERMINAL_ID) is not None
        assert worktree_path.is_dir()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary", ["binding-before-stop", "binding-after-proof", "incarnation", "retirement-refusal"]
)
async def test_failed_initializer_never_retires_unowned_registration(
    create_mocks, repo, monkeypatch, boundary
):
    from contextlib import contextmanager

    from cli_agent_orchestrator.services import provider_lifecycle

    backend = create_mocks.backend
    error = RuntimeError("controlled ordinary init failure")
    snapshots = []

    def replace_row():
        with db.SessionLocal() as session:
            if boundary == "incarnation":
                pointer = session.get(db.SessionIncarnationModel, SESSION_NAME)
                pointer.incarnation_id = "replacement-incarnation"
            else:
                row = session.get(db.TerminalModel, TERMINAL_ID)
                row.tmux_window = "replacement-window"
            session.commit()
        snapshots.append(db.get_terminal_metadata(TERMINAL_ID))

    async def initialize():
        assert db.get_terminal_metadata(TERMINAL_ID) is not None
        if boundary in ("binding-before-stop", "incarnation"):
            replace_row()
        elif boundary == "retirement-refusal":

            @contextmanager
            def refuse(*_args):
                yield False

            monkeypatch.setattr(terminal_service, "retirement_scope", refuse)
        raise error

    def prove(*args, **kwargs):
        replace_row()
        return TerminalCleanupResult(TerminalCleanupOutcome.ABSENT)

    backend.cleanup_terminal_exact.side_effect = prove
    create_mocks.provider_manager.create_provider.return_value.initialize = initialize
    with pytest.raises(RuntimeError) as raised:
        await terminal_service.create_terminal(
            "claude_code",
            "developer",
            session_name=SESSION_NAME,
            working_directory=str(repo),
            use_worktree=True,
        )
    assert raised.value is error
    assert db.get_terminal_metadata(TERMINAL_ID) is not None
    if snapshots:
        assert db.get_terminal_metadata(TERMINAL_ID) == snapshots[-1]
    assert Path(worktree_service.worktree_path_for(str(repo), TERMINAL_ID)).is_dir()
    create_mocks.provider_manager.cleanup_provider.assert_not_called()
    if boundary == "binding-after-proof":
        backend.kill_window.assert_called_once_with(SESSION_NAME, WINDOW_NAME)
        backend.cleanup_terminal_exact.assert_called_once_with(
            TERMINAL_ID, SESSION_NAME, WINDOW_NAME, close=False
        )
    else:
        backend.kill_window.assert_not_called()
        backend.cleanup_terminal_exact.assert_not_called()
    assert TERMINAL_ID not in provider_lifecycle._startups
