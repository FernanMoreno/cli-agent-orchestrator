"""T164 regression draft: retained synchronous init failure must stay inspect-only."""

from test.api.conftest import client
from test.services.test_terminal_service_inbox_registration import (
    SESSION_NAME,
    WINDOW_NAME,
    create_mocks,
)
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult
from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import provider_lifecycle
from cli_agent_orchestrator.services import terminal_service as terminal

KEY = "a1b2c3d4"


@pytest.mark.asyncio
async def test_public_routes_classify_and_block_retained_ordinary_init_failure(
    create_mocks, monkeypatch, client, tmp_path
):
    """Use a real committed row and a controlled backend; no provider CLI runs."""
    assert db.engine.dialect.name == "postgresql"
    assert KEY not in provider_lifecycle._startups
    assert KEY not in terminal._initial_task_pending_ids
    monkeypatch.delenv("CAO_REGISTERED_PROJECTS", raising=False)

    backend = create_mocks.backend
    create_mocks.generate_terminal_id.return_value = KEY
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
    monkeypatch.setattr(terminal, "inject_memory_context", lambda message, *_a, **_k: message)

    init_error = RuntimeError("controlled synchronous provider initialization failure")
    failed_provider = Mock()
    failed_provider.initialize = AsyncMock(side_effect=init_error)
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
    row = db.get_terminal_metadata(KEY)
    assert row is not None
    assert KEY not in provider_lifecycle._startups  # actual create_terminal finally path
    assert KEY not in terminal._initial_task_pending_ids
    backend.kill_session.assert_called_once_with(SESSION_NAME)
    backend.cleanup_terminal_exact.assert_called_once_with(
        KEY, SESSION_NAME, WINDOW_NAME, close=False
    )
    create_mocks.provider_manager.cleanup_provider.assert_not_called()

    # Model a fresh status observer after the create call has released its real
    # startup owner. This keeps in-process status state from hiding the durable gap.
    fresh_monitor = Mock()
    fresh_monitor.get_status.return_value = TerminalStatus.UNKNOWN
    fresh_monitor.turn_state.return_value = (0, False)
    fresh_monitor.notify_input_sent.return_value = 1
    monkeypatch.setattr(terminal, "status_monitor", fresh_monitor)

    # A reconstructed provider may be absent or a new adapter instance; this
    # minimal contract stub gives the real send_input path everything needed to
    # reach the backend transport without invoking a provider executable.
    input_provider = SimpleNamespace(
        requires_turn_receipt=False,
        assume_processing_on_dispatch=False,
        blocks_new_task_input_for_reconciliation=False,
        paste_enter_count=1,
        paste_submit_delay=0,
        mark_input_received=Mock(),
        record_dispatched_message=Mock(),
    )
    create_mocks.provider_manager.get_provider.return_value = input_provider

    status_response = client.get(f"/terminals/{KEY}")
    input_response = client.post(
        f"/terminals/{KEY}/input", params={"message": "controlled ordinary input"}
    )

    status_payload = status_response.json()
    failure = status_payload.get("deferred_init_failure")
    detail = input_response.json().get("detail")
    observations = {
        "get_status_code": status_response.status_code,
        "terminal_status": status_payload.get("status"),
        "failure_kind": failure.get("kind") if isinstance(failure, dict) else None,
        "input_status_code": input_response.status_code,
        "input_action": detail.get("action") if isinstance(detail, dict) else None,
        "input_delivery_may_have_occurred": (
            detail.get("delivery_may_have_occurred") if isinstance(detail, dict) else None
        ),
        "backend_send_keys_calls": backend.send_keys.call_count,
    }

    # The ordinary-init failure kind is intentionally not prescribed in this
    # draft, but it must remain distinct from cancellation before initialization.
    assert (
        status_response.status_code == 200
        and status_payload.get("status") == "error"
        and isinstance(failure, dict)
        and failure.get("kind") != "creation_cancelled"
        and input_response.status_code == 409
        and isinstance(detail, dict)
        and detail.get("action") == "reconcile"
        and detail.get("delivery_may_have_occurred") is False
        and backend.send_keys.call_count == 0
    ), observations


@pytest.fixture(autouse=True)
def isolated_ordinary_failure_admission(monkeypatch):
    monkeypatch.setattr(provider_lifecycle, "_startups", {})
    monkeypatch.setattr(provider_lifecycle, "_retirements", {})
    monkeypatch.setattr(terminal, "_initial_task_pending_ids", set())
    monkeypatch.setattr(terminal, "_active_deferred_init_external_owner_ids", set())


@pytest.mark.asyncio
async def test_unknown_binding_read_keeps_local_admission_without_writing_evidence(
    create_mocks, monkeypatch, tmp_path, client
):
    create_mocks.generate_terminal_id.return_value = KEY
    create_mocks.db_create_terminal.side_effect = db.create_terminal
    original = RuntimeError("controlled init failure")
    persist = Mock(wraps=terminal._persist_create_failure)
    monkeypatch.setattr(terminal, "_persist_create_failure", persist)

    async def initialize():
        assert db.get_terminal_metadata(KEY) is not None
        monkeypatch.setattr(
            terminal, "get_terminal_metadata", Mock(side_effect=RuntimeError("binding read outage"))
        )
        raise original

    create_mocks.provider_manager.create_provider.return_value.initialize = initialize
    with pytest.raises(RuntimeError) as raised:
        await terminal.create_terminal(
            "claude_code",
            "developer",
            session_name=SESSION_NAME,
            working_directory=str(tmp_path),
        )
    assert raised.value is original
    assert KEY in provider_lifecycle._startups
    assert provider_lifecycle._startups[KEY].uncertain is True
    assert KEY in terminal._initial_task_pending_ids
    assert db.get_terminal_metadata(KEY)["deferred_init_failure"] is None
    persist.assert_not_called()
    create_mocks.backend.kill_window.assert_not_called()
    create_mocks.provider_manager.cleanup_provider.assert_not_called()

    # Once metadata reads recover, the same-process admission fence still
    # refuses bytes without inventing durable evidence or restart safety.
    monkeypatch.setattr(terminal, "get_terminal_metadata", db.get_terminal_metadata)
    response = client.post(f"/terminals/{KEY}/input", params={"message": "controlled input"})
    assert response.status_code == 409, response.text
    create_mocks.backend.send_keys.assert_not_called()
