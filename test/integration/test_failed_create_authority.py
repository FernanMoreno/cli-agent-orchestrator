"""A rejected creation must not compensate another row that holds its generated ID."""

from test.services.test_terminal_service_inbox_registration import (
    SESSION_NAME,
    TERMINAL_ID,
    WINDOW_NAME,
    create_mocks,
)
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.providers.manager import ProviderManager
from cli_agent_orchestrator.services import provider_lifecycle, terminal_service


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["insert_collision", "backend_failure"])
@pytest.mark.parametrize("existing_marks", [False, True])
async def test_failed_creation_preserves_existing_same_id_terminal(
    create_mocks, monkeypatch, failure, existing_marks
):
    assert db.engine.dialect.name == "postgresql"
    db.create_terminal(
        TERMINAL_ID,
        "cao-existing-owner",
        "ready-window",
        "mock_cli",
        "ready",
        session_incarnation_id="existing-generation",
    )
    assert db.begin_terminal_turn_receipt(TERMINAL_ID, "mock_cli", "1" * 32, "a" * 64)
    row_before = db.get_terminal_metadata(TERMINAL_ID)
    receipt_before = db.get_terminal_turn_receipt(TERMINAL_ID)
    manager = ProviderManager()
    existing = manager.create_provider(
        "mock_cli", TERMINAL_ID, "cao-existing-owner", "ready-window"
    )
    existing.cleanup = Mock(return_value=True)
    create_mocks.provider_manager.create_provider.side_effect = manager.create_provider
    create_mocks.provider_manager.cleanup_provider.side_effect = manager.cleanup_provider
    create_mocks.provider_manager.retirement_fence.side_effect = manager.retirement_fence
    create_mocks.db_create_terminal.side_effect = db.create_terminal
    failure_error = RuntimeError("controlled backend allocation failure")
    if failure == "backend_failure":
        create_mocks.backend.create_window.side_effect = failure_error
    fifo, monitor, capabilities = Mock(), Mock(), Mock()
    transition = Mock(wraps=terminal_service.transition_native_child)
    monkeypatch.setattr(terminal_service, "fifo_manager", fifo)
    monkeypatch.setattr(terminal_service, "status_monitor", monitor)
    monkeypatch.setattr(terminal_service, "_remove_interaction_capability_files", capabilities)
    monkeypatch.setattr(terminal_service, "transition_native_child", transition)

    if existing_marks:
        terminal_service._mark_initial_task_pending(TERMINAL_ID)
        terminal_service._mark_deferred_init_external_owner_active(TERMINAL_ID)
    with pytest.raises(IntegrityError if failure == "insert_collision" else RuntimeError) as error:
        await terminal_service.create_terminal(
            "claude_code", "developer", session_name=SESSION_NAME, defer_init=True
        )
    with terminal_service._active_deferred_init_external_owner_lock:
        pending = TERMINAL_ID in terminal_service._initial_task_pending_ids
        active = TERMINAL_ID in terminal_service._active_deferred_init_external_owner_ids
    terminal_service._clear_deferred_init_external_owner_active(TERMINAL_ID)
    assert pending is existing_marks
    assert active is existing_marks
    if failure == "backend_failure":
        assert error.value is failure_error
    assert db.get_terminal_metadata(TERMINAL_ID) == row_before
    assert db.get_terminal_turn_receipt(TERMINAL_ID) == receipt_before
    assert manager._providers[TERMINAL_ID] is existing
    existing.cleanup.assert_not_called()
    fifo.stop_reader.assert_not_called()
    monitor.clear_terminal.assert_not_called()
    capabilities.assert_not_called()
    transition.assert_not_called()
    create_mocks.provider_manager.cleanup_provider.assert_not_called()
    create_mocks.backend.kill_session.assert_not_called()
    if failure == "insert_collision":
        create_mocks.backend.kill_window.assert_called_once_with(SESSION_NAME, WINDOW_NAME)
    else:
        create_mocks.backend.kill_window.assert_not_called()
    assert TERMINAL_ID not in provider_lifecycle._startups


@pytest.mark.asyncio
async def test_ambiguous_insert_return_does_not_delete_published_evidence(
    create_mocks, monkeypatch
):
    error = RuntimeError("commit succeeded but acknowledgment failed")

    def publish_then_raise(*args, **kwargs):
        db.create_terminal(*args, **kwargs)
        raise error

    create_mocks.db_create_terminal.side_effect = publish_then_raise
    cleanup = Mock()
    monkeypatch.setattr(terminal_service, "delete_terminal_row", cleanup)
    with pytest.raises(RuntimeError) as raised:
        await terminal_service.create_terminal(
            "claude_code", "developer", session_name=SESSION_NAME
        )
    assert raised.value is error
    assert db.get_terminal_metadata(TERMINAL_ID) is not None
    cleanup.assert_not_called()
    create_mocks.provider_manager.cleanup_provider.assert_not_called()
    create_mocks.backend.kill_window.assert_called_once_with(SESSION_NAME, WINDOW_NAME)
    assert TERMINAL_ID not in provider_lifecycle._startups


@pytest.mark.asyncio
@pytest.mark.parametrize("allocated", [False, True])
async def test_worktree_rollback_requires_successful_allocation(create_mocks, tmp_path, allocated):
    error = RuntimeError("controlled allocation failure")
    with patch.object(terminal_service, "worktree_service") as worktrees:
        worktrees.find_repo_root.return_value = str(tmp_path)
        if allocated:
            worktrees.create_worktree.return_value = str(tmp_path)
            create_mocks.backend.create_window.side_effect = error
        else:
            worktrees.create_worktree.side_effect = error
        with pytest.raises(RuntimeError) as raised:
            await terminal_service.create_terminal(
                "claude_code",
                "developer",
                session_name=SESSION_NAME,
                use_worktree=True,
                working_directory=str(tmp_path),
            )
        assert raised.value is error
        if allocated:
            worktrees.remove_worktree.assert_called_once_with(str(tmp_path), TERMINAL_ID)
        else:
            worktrees.remove_worktree.assert_not_called()
        create_mocks.db_create_terminal.assert_not_called()
        create_mocks.provider_manager.cleanup_provider.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_receipt", [False, True])
async def test_failed_creation_settles_only_its_own_planned_child(create_mocks, existing_receipt):
    if existing_receipt:
        db.plan_native_child(
            parent_terminal_id="original-parent",
            terminal_id=TERMINAL_ID,
            provider="mock_cli",
            agent_profile="original",
            lease_seconds=60,
        )
    before = db.get_native_child_for_terminal(TERMINAL_ID)
    error = RuntimeError("backend allocation refused")
    create_mocks.backend.create_window.side_effect = error
    with pytest.raises(IntegrityError if existing_receipt else RuntimeError):
        await terminal_service.create_terminal(
            "claude_code", "developer", session_name=SESSION_NAME, caller_id="new-parent"
        )
    child = db.get_native_child_for_terminal(TERMINAL_ID)
    if existing_receipt:
        assert child == before
        create_mocks.backend.create_window.assert_not_called()
    else:
        assert child["state"] == "failed"
        assert child["error_kind"] == "create_error"
        assert child["parent_terminal_id"] == "new-parent"
    assert db.get_terminal_metadata(TERMINAL_ID) is None
