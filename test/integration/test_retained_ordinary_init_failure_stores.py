"""T164 follow-up draft: durable fallback and local-only failure fencing."""

from test.api.conftest import client
from test.services.test_terminal_service_inbox_registration import (
    SESSION_NAME,
    WINDOW_NAME,
    create_mocks,
)
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult
from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import provider_lifecycle
from cli_agent_orchestrator.services import terminal_service as terminal


async def _create_ordinary_failure_with_unproven_stop(
    create_mocks, monkeypatch, tmp_path, terminal_id
):
    """Commit a real row, then raise from a provider stub with stop unproven."""
    assert db.engine.dialect.name == "postgresql"
    assert terminal_id not in provider_lifecycle._startups
    assert terminal_id not in terminal._initial_task_pending_ids
    monkeypatch.delenv("CAO_REGISTERED_PROJECTS", raising=False)

    backend = create_mocks.backend
    create_mocks.generate_terminal_id.return_value = terminal_id
    create_mocks.generate_window_name.return_value = WINDOW_NAME
    create_mocks.db_create_terminal.side_effect = db.create_terminal
    backend.session_exists.return_value = False
    backend.create_session.return_value = WINDOW_NAME
    backend.kill_session.side_effect = RuntimeError("controlled stop failure")
    backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.STILL_PRESENT
    )

    initial_monitor = Mock()
    initial_monitor.get_status.return_value = TerminalStatus.UNKNOWN
    monkeypatch.setattr(terminal, "status_monitor", initial_monitor)
    monkeypatch.setattr(terminal, "fifo_manager", Mock())
    monkeypatch.setattr(terminal, "inject_memory_context", lambda message, *_a, **_k: message)

    init_error = RuntimeError("controlled synchronous provider initialization failure")
    failed_provider = Mock()

    async def fail_after_row_publication():
        assert db.get_terminal_metadata(terminal_id) is not None
        raise init_error

    failed_provider.initialize = fail_after_row_publication
    create_mocks.provider_manager.create_provider.return_value = failed_provider

    with pytest.raises(RuntimeError) as raised:
        await terminal.create_terminal(
            provider="claude_code",
            agent_profile="developer",
            session_name=SESSION_NAME,
            new_session=True,
            working_directory=str(tmp_path),
        )

    assert raised.value is init_error
    assert db.get_terminal_metadata(terminal_id) is not None
    return backend, init_error


def _install_fresh_unknown_monitor(monkeypatch):
    monitor = Mock()
    monitor.get_status.return_value = TerminalStatus.UNKNOWN
    monitor.turn_state.return_value = (0, False)
    monitor.notify_input_sent.return_value = 1
    monkeypatch.setattr(terminal, "status_monitor", monitor)
    return monitor


@pytest.mark.asyncio
async def test_db_failure_uses_real_sidecar_for_fresh_public_observer(
    create_mocks, monkeypatch, client, tmp_path
):
    terminal_id = "a1b2c3e6"
    database_write = Mock(side_effect=RuntimeError("controlled database write outage"))
    monkeypatch.setattr(terminal, "update_terminal_deferred_init_failure", database_write)

    backend, _ = await _create_ordinary_failure_with_unproven_stop(
        create_mocks, monkeypatch, tmp_path, terminal_id
    )

    sidecar = terminal._deferred_failure_fallback_path(terminal_id)
    failure = terminal._read_deferred_failure_fallback(terminal_id)
    assert database_write.call_count == terminal._DEFERRED_INIT_FAILURE_PERSIST_ATTEMPTS
    assert sidecar.is_file()
    assert failure == {
        "exception_type": "RuntimeError",
        "kind": "initialization_error",
        "message": "Terminal initialization failed; reconcile this terminal.",
        "phase": "deferred_init",
    }
    assert terminal_id not in provider_lifecycle._startups
    assert terminal_id not in terminal._initial_task_pending_ids
    backend.kill_session.assert_called_once_with(SESSION_NAME)
    backend.cleanup_terminal_exact.assert_called_once_with(
        terminal_id, SESSION_NAME, WINDOW_NAME, close=False
    )
    create_mocks.provider_manager.cleanup_provider.assert_not_called()

    _install_fresh_unknown_monitor(monkeypatch)
    status_response = client.get(f"/terminals/{terminal_id}")
    input_response = client.post(
        f"/terminals/{terminal_id}/input", params={"message": "controlled ordinary input"}
    )
    status_payload = status_response.json()
    input_detail = input_response.json().get("detail")

    assert status_response.status_code == 200, status_response.text
    assert status_payload["status"] == "error"
    assert status_payload["deferred_init_failure"]["kind"] == "initialization_error"
    assert input_response.status_code == 409
    assert isinstance(input_detail, dict)
    assert input_detail["action"] == "reconcile"
    assert input_detail["delivery_may_have_occurred"] is False
    create_mocks.provider_manager.get_provider.assert_not_called()
    backend.send_keys.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("database_outcome", "fallback_outcome"),
    [
        pytest.param("false", "not-called", id="db-refused-write-no-orphan-sidecar"),
        pytest.param("raises", "false", id="database-raises-sidecar-false"),
        pytest.param("raises", "raises", id="database-and-sidecar-raise"),
    ],
)
async def test_unavailable_failure_stores_keep_local_quarantine_before_finish_startup(
    create_mocks,
    monkeypatch,
    client,
    tmp_path,
    database_outcome,
    fallback_outcome,
):
    terminal_id = "a1b2c3e7"
    database_write = (
        Mock(return_value=False)
        if database_outcome == "false"
        else Mock(side_effect=RuntimeError("controlled database write outage"))
    )
    if fallback_outcome == "not-called":
        fallback_write = Mock(
            side_effect=AssertionError("false DB result must not publish an orphan sidecar")
        )
    elif fallback_outcome == "false":
        fallback_write = Mock(return_value=False)
    else:
        fallback_write = Mock(side_effect=OSError("controlled sidecar write outage"))
    monkeypatch.setattr(terminal, "update_terminal_deferred_init_failure", database_write)
    monkeypatch.setattr(terminal, "_publish_deferred_failure_fallback", fallback_write)

    real_finish_startup = terminal.finish_startup
    observations_before_finish = []

    def observe_finish_startup(owner):
        if owner is not None and owner.terminal_id == terminal_id:
            observations_before_finish.append(
                (
                    owner,
                    owner.uncertain,
                    terminal_id in terminal._initial_task_pending_ids,
                )
            )
        return real_finish_startup(owner)

    monkeypatch.setattr(terminal, "finish_startup", observe_finish_startup)
    owner = None
    try:
        backend, _ = await _create_ordinary_failure_with_unproven_stop(
            create_mocks, monkeypatch, tmp_path, terminal_id
        )

        owner = provider_lifecycle._startups.get(terminal_id)
        assert owner is not None and owner.uncertain is True
        assert terminal_id in terminal._initial_task_pending_ids
        assert observations_before_finish == [(owner, True, True)]
        assert db.get_terminal_metadata(terminal_id) is not None
        assert db.get_terminal_metadata(terminal_id)["deferred_init_failure"] is None
        assert not terminal._deferred_failure_fallback_path(terminal_id).exists()
        if database_outcome == "false":
            fallback_write.assert_not_called()
        else:
            fallback_write.assert_called_once()

        backend.kill_session.assert_not_called()
        backend.cleanup_terminal_exact.assert_not_called()
        create_mocks.provider_manager.cleanup_provider.assert_not_called()

        _install_fresh_unknown_monitor(monkeypatch)
        status_response = client.get(f"/terminals/{terminal_id}")
        input_response = client.post(
            f"/terminals/{terminal_id}/input", params={"message": "controlled ordinary input"}
        )
        assert status_response.status_code == 200, status_response.text
        assert status_response.json()["status"] == "unknown"
        assert status_response.json()["deferred_init_failure"] is None
        assert input_response.status_code == 409
        detail = input_response.json()["detail"]
        assert detail["action"] == "inspect"
        assert detail["delivery_may_have_occurred"] is False
        create_mocks.provider_manager.get_provider.assert_not_called()
        backend.send_keys.assert_not_called()
    finally:
        # Remove only this test's intentionally retained process-local quarantine.
        if owner is None:
            owner = provider_lifecycle._startups.get(terminal_id)
        if owner is not None:
            with provider_lifecycle._guard:
                if provider_lifecycle._startups.get(terminal_id) is owner:
                    del provider_lifecycle._startups[terminal_id]
        terminal._clear_initial_task_pending(terminal_id)
