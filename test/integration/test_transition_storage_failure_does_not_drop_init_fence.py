"""Child-state settlement storage faults must not bypass init failure fencing."""

from test.api.conftest import client
from test.services.test_terminal_service_inbox_registration import (
    SESSION_NAME,
    WINDOW_NAME,
    create_mocks,
)
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult
from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import provider_lifecycle
from cli_agent_orchestrator.services import terminal_service as terminal


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inject_storage_fault", [True, False], ids=["storage-outage", "healthy-settlement"]
)
@pytest.mark.parametrize("planned_child", [False, True], ids=["root-no-receipt", "planned-child"])
async def test_native_child_settlement_storage_error_keeps_failed_create_fenced(
    create_mocks, monkeypatch, client, tmp_path, planned_child, request, inject_storage_fault
):
    """Inject one SessionLocal open failure at the real transition helper boundary."""
    assert db.engine.dialect.name == "postgresql"
    terminal_id = "a1b2c3e8"
    parent_id = "t165-parent" if planned_child else None
    assert terminal_id not in provider_lifecycle._startups
    assert terminal_id not in terminal._initial_task_pending_ids

    def cleanup_test_owned_quarantine():
        owner = provider_lifecycle._startups.get(terminal_id)
        if owner is not None:
            with provider_lifecycle._guard:
                if provider_lifecycle._startups.get(terminal_id) is owner:
                    del provider_lifecycle._startups[terminal_id]
        terminal._clear_initial_task_pending(terminal_id)

    request.addfinalizer(cleanup_test_owned_quarantine)
    monkeypatch.delenv("CAO_REGISTERED_PROJECTS", raising=False)

    if planned_child:
        db.create_terminal(
            parent_id,
            "cao-t165-parent",
            "parent-window",
            "claude_code",
            "developer",
        )

    backend = create_mocks.backend
    create_mocks.generate_terminal_id.return_value = terminal_id
    create_mocks.generate_window_name.return_value = WINDOW_NAME
    create_mocks.db_create_terminal.side_effect = db.create_terminal
    backend.session_exists.return_value = False
    backend.create_session.return_value = WINDOW_NAME
    backend.kill_session.side_effect = RuntimeError("controlled unproven session stop")
    backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.STILL_PRESENT
    )

    initial_monitor = Mock()
    initial_monitor.get_status.return_value = TerminalStatus.UNKNOWN
    monkeypatch.setattr(terminal, "status_monitor", initial_monitor)
    monkeypatch.setattr(terminal, "fifo_manager", Mock())
    monkeypatch.setattr(terminal, "inject_memory_context", lambda message, *_a, **_k: message)

    real_session_local = db.SessionLocal
    storage_fault = {"armed": False, "raised": False}
    storage_error = RuntimeError("controlled native-child transition storage outage")

    def session_local_with_one_transition_failure(*args, **kwargs):
        if storage_fault["armed"] and not storage_fault["raised"]:
            storage_fault["raised"] = True
            raise storage_error
        return real_session_local(*args, **kwargs)

    monkeypatch.setattr(db, "SessionLocal", session_local_with_one_transition_failure)
    real_transition = terminal.transition_native_child
    transition_calls = []
    transition_failures = []

    def observe_transition(*args, **kwargs):
        transition_calls.append((args, kwargs))
        try:
            return real_transition(*args, **kwargs)
        except Exception as transition_error:
            transition_failures.append((args[:2], transition_error is storage_error))
            raise

    monkeypatch.setattr(terminal, "transition_native_child", observe_transition)

    init_error = RuntimeError("controlled synchronous provider initialization failure")
    failed_provider = Mock()

    async def fail_after_publication():
        assert db.get_terminal_metadata(terminal_id) is not None
        if planned_child:
            planned = db.get_native_child_for_terminal(terminal_id)
            assert planned is not None and planned["state"] == "planned"
        storage_fault["armed"] = inject_storage_fault
        raise init_error

    failed_provider.initialize = fail_after_publication
    create_mocks.provider_manager.create_provider.return_value = failed_provider

    caught = None
    try:
        await terminal.create_terminal(
            provider="claude_code",
            agent_profile="developer",
            session_name=SESSION_NAME,
            new_session=True,
            working_directory=str(tmp_path),
            caller_id=parent_id,
        )
    except RuntimeError as error:
        caught = error

    # Permit status/input observation after the one injected storage failure.
    storage_fault["armed"] = False
    row = db.get_terminal_metadata(terminal_id)
    durable_failure = row.get("deferred_init_failure") if row else None
    if durable_failure is None:
        durable_failure = terminal._read_deferred_failure_fallback(terminal_id)
    child = db.get_native_child_for_terminal(terminal_id)
    if planned_child:
        assert child is not None
        assert child["state"] == ("planned" if inject_storage_fault else "failed")
    else:
        assert child is None
    owner = provider_lifecycle._startups.get(terminal_id)
    local_quarantine = bool(
        owner is not None
        and owner.uncertain is True
        and terminal_id in terminal._initial_task_pending_ids
    )

    fresh_monitor = Mock()
    fresh_monitor.get_status.return_value = TerminalStatus.UNKNOWN
    fresh_monitor.turn_state.return_value = (0, False)
    fresh_monitor.notify_input_sent.return_value = 1
    monkeypatch.setattr(terminal, "status_monitor", fresh_monitor)

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

    status_response = client.get(f"/terminals/{terminal_id}")
    input_response = client.post(
        f"/terminals/{terminal_id}/input", params={"message": "controlled ordinary input"}
    )
    status_payload = status_response.json()
    detail = input_response.json().get("detail")
    safe_durable_failure = (
        isinstance(durable_failure, dict)
        and durable_failure.get("kind") == "initialization_error"
        and status_payload.get("status") == "error"
    )
    safe_local_quarantine = local_quarantine and status_payload.get("status") == "unknown"
    observed = {
        "caught_exception": repr(caught),
        "original_init_error_preserved": caught is init_error,
        "transition_calls": [args[:2] for args, _kwargs in transition_calls],
        "storage_failure_triggered": storage_fault["raised"],
        "durable_failure_kind": (
            durable_failure.get("kind") if isinstance(durable_failure, dict) else None
        ),
        "startup_owner_uncertain": owner.uncertain if owner is not None else None,
        "initial_task_pending": terminal_id in terminal._initial_task_pending_ids,
        "get_status": status_payload.get("status"),
        "input_status_code": input_response.status_code,
        "input_action": detail.get("action") if isinstance(detail, dict) else None,
        "send_keys_calls": backend.send_keys.call_count,
    }

    assert (
        caught is init_error
        and storage_fault["raised"] is inject_storage_fault
        and (((terminal_id, "failed"), True) in transition_failures) is inject_storage_fault
        and any(args[:2] == (terminal_id, "failed") for args, _kwargs in transition_calls)
        and row is not None
        and (safe_durable_failure or safe_local_quarantine)
        and status_response.status_code == 200
        and input_response.status_code == 409
        and isinstance(detail, dict)
        and detail.get("action") in {"reconcile", "inspect"}
        and detail.get("delivery_may_have_occurred") is False
        and backend.send_keys.call_count == 0
    ), observed
