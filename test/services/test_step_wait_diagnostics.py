"""Completion timeout observations are facts, never receipt or provider text."""

import asyncio
import dataclasses
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import agent_step

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize(
    "status,probe,working,errors,seen",
    [
        (TerminalStatus.IDLE, None, False, 0, False),
        (TerminalStatus.PROCESSING, None, True, 0, False),
        (TerminalStatus.WAITING_USER_ANSWER, None, True, 0, False),
        (TerminalStatus.PROCESSING, "秘密: café answer", True, 0, True),
        (TerminalStatus.PROCESSING, ValueError("秘密: café receipt"), True, 1, False),
    ],
)
def test_completion_timeout_keeps_safe_observations(
    monkeypatch, status, probe, working, errors, seen
):
    monkeypatch.setattr(agent_step, "_interaction_wait_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_step.status_monitor, "get_status", lambda _: status)

    def get_receipt(_):
        if isinstance(probe, Exception):
            raise probe
        return probe

    monkeypatch.setattr(agent_step.terminal_service, "probe_post_turn_receipt_result", get_receipt)
    with pytest.raises(agent_step.StepExecutionError) as caught:
        asyncio.run(agent_step._wait_for_completion("test1234", timeout=0, prompt_redelivery=False))
    error = caught.value
    assert error.kind == "timeout"
    assert error.delivery_may_have_occurred is True
    diagnostic = dataclasses.asdict(error.wait_diagnostics)
    assert diagnostic == {
        "phase": "completion",
        "last_status": status.value,
        "observed_working": working,
        "receipt_probe_errors": errors,
        "receipt_candidate_seen": seen,
        "stable_receipt_observations": int(seen),
        "receipt_probe_calls": 1,
        "prompt_redelivery_attempts": 0,
        "delivery_verified": False,
        "idle_extraction_attempted": False,
    }
    assert "秘密" not in repr(diagnostic)
    assert "café" not in repr(diagnostic)


@pytest.mark.parametrize(
    "status,kind",
    [
        (TerminalStatus.ERROR, "error"),
        (TerminalStatus.WAITING_QUOTA, "quota_wait"),
    ],
)
def test_existing_error_and_quota_precedence_is_preserved(monkeypatch, status, kind):
    monkeypatch.setattr(agent_step, "_interaction_wait_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_step.status_monitor, "get_status", lambda _: status)
    monkeypatch.setattr(agent_step.terminal_service, "quota_pause_may_auto_resume", lambda _: False)
    with pytest.raises(agent_step.StepExecutionError) as caught:
        asyncio.run(agent_step._wait_for_completion("test1234", timeout=0))
    assert caught.value.kind == kind
    assert caught.value.wait_diagnostics is None


def test_legacy_step_error_does_not_invent_diagnostics():
    assert agent_step.StepExecutionError("readiness timeout").wait_diagnostics is None


def test_run_step_http_timeout_projects_only_closed_observations(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cli_agent_orchestrator.api import main

    diagnostic = agent_step.CompletionWaitDiagnostics(
        last_status=TerminalStatus.WAITING_USER_ANSWER,
        observed_working=True,
        receipt_probe_errors=1,
        receipt_candidate_seen=False,
        stable_receipt_observations=0,
        idle_extraction_attempted=False,
        receipt_probe_calls=3,
        prompt_redelivery_attempts=2,
        delivery_verified=True,
    )
    error = agent_step.StepExecutionError(
        "completion timeout",
        terminal_id="test1234",
        delivery_may_have_occurred=True,
        wait_diagnostics=diagnostic,
    )
    monkeypatch.setattr(main, "run_agent_step", AsyncMock(side_effect=error))
    monkeypatch.setattr(main, "get_plugin_registry", lambda _: None)
    app = FastAPI()
    app.dependency_overrides[main._run_step_scopes] = lambda: []
    app.dependency_overrides[main._optional_run_step_principal] = lambda: None
    app.add_api_route("/run-step", main.run_step, methods=["POST"])
    with TestClient(app) as client:
        response = client.post(
            "/run-step",
            json={
                "provider": "claude_code",
                "agent": "developer",
                "prompt": "秘密 café",
            },
        )
    assert response.status_code == 504
    detail = response.json()["detail"]
    assert detail["kind"] == "timeout"
    assert detail["retryable"] is False
    assert detail["delivery_may_have_occurred"] is True
    assert detail["wait_diagnostics"] == {
        "phase": "completion",
        "last_status": "waiting_user_answer",
        "observed_working": True,
        "receipt_probe_errors": 1,
        "receipt_candidate_seen": False,
        "stable_receipt_observations": 0,
        "receipt_probe_calls": 3,
        "prompt_redelivery_attempts": 2,
        "delivery_verified": True,
        "idle_extraction_attempted": False,
    }
    assert "秘密" not in response.text
    assert "café" not in response.text


def test_failed_live_matrix_retains_diagnostics_in_sanitized_evidence(monkeypatch):
    import json
    from test.e2e import test_real_provider_matrix as matrix
    from types import SimpleNamespace

    detail = {
        "kind": "timeout",
        "message": "秘密 café",
        "terminal_id": "private-id",
        "wait_diagnostics": {
            "phase": "completion",
            "last_status": "waiting_user_answer",
            "observed_working": True,
            "receipt_probe_errors": 1,
            "receipt_candidate_seen": False,
            "stable_receipt_observations": 0,
            "idle_extraction_attempted": False,
        },
    }
    monkeypatch.setattr(
        matrix,
        "_request",
        lambda *a, **kw: SimpleNamespace(status_code=504, json=lambda: {"detail": detail}),
    )
    properties = {}
    with pytest.raises(AssertionError):
        matrix._run_cross_provider_step(
            SimpleNamespace(url="http://unused.invalid"),
            session_name="unused",
            parent_id="unused",
            cell_id="test",
            provider="claude_code",
            profile="test",
            record_property=properties.__setitem__,
        )
    evidence = json.loads(properties["matrix_evidence"])
    assert evidence["step_failure"] == {
        "http_status": 504,
        "kind": "timeout",
        "wait_diagnostics": detail["wait_diagnostics"],
    }
    assert "秘密" not in properties["matrix_evidence"]
    assert "private-id" not in properties["matrix_evidence"]


@pytest.mark.parametrize(
    "change",
    [
        {"last_status": "private-provider-text"},
        {"phase": "private-path"},
        {"receipt_probe_errors": "private-error"},
        {"receipt_probe_errors": True},
        {"receipt_probe_errors": -1},
        {"receipt_probe_errors": 2**40},
        {"unknown": "private-receipt"},
        {"observed_working": "private-prompt"},
    ],
)
def test_failure_evidence_rejects_nonclosed_diagnostic_values(change):
    from test.e2e import test_real_provider_matrix as matrix
    from types import SimpleNamespace

    diagnostic = {
        "phase": "completion",
        "last_status": "processing",
        "observed_working": True,
        "receipt_probe_errors": 0,
        "receipt_candidate_seen": False,
        "stable_receipt_observations": 0,
        "idle_extraction_attempted": False,
        **change,
    }
    response = SimpleNamespace(
        status_code=504,
        json=lambda: {
            "detail": {
                "kind": "timeout",
                "wait_diagnostics": diagnostic,
                "message": "private-sentinel",
            }
        },
    )
    assert matrix._safe_step_failure(response) == {"http_status": 504, "kind": "timeout"}


def test_idle_extraction_attempt_is_observed_without_changing_completion(monkeypatch):
    now = {"value": 0.0}
    observations = {"count": 0}

    def status(_):
        observations["count"] += 1
        if observations["count"] == 1:
            return TerminalStatus.PROCESSING
        now["value"] = 10.0
        return TerminalStatus.IDLE

    monkeypatch.setattr(agent_step.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(agent_step, "_COMPLETION_POLL_INTERVAL", 0)
    monkeypatch.setattr(agent_step, "_IDLE_STABLE_POLLS", 1)
    monkeypatch.setattr(agent_step, "_interaction_wait_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_step.status_monitor, "get_status", status)
    monkeypatch.setattr(
        agent_step.terminal_service, "probe_post_turn_receipt_result", lambda _: None
    )
    monkeypatch.setattr(agent_step.terminal_service, "get_output", lambda *args: "")
    with pytest.raises(agent_step.StepExecutionError) as caught:
        asyncio.run(
            agent_step._wait_for_completion("test1234", timeout=10, prompt_redelivery=False)
        )
    assert caught.value.wait_diagnostics.idle_extraction_attempted is True
    assert caught.value.wait_diagnostics.observed_working is True
    assert caught.value.wait_diagnostics.last_status is TerminalStatus.IDLE


@pytest.mark.parametrize("recovery", [False, True, ValueError("秘密 recovery")])
def test_timeout_records_recovery_attempt_even_when_send_fails(monkeypatch, recovery):
    clock = {"now": 0.0, "polls": 0}

    def status(_):
        clock["polls"] += 1
        clock["now"] = clock["polls"] * 2.0
        return TerminalStatus.IDLE

    def recover(*args, **kwargs):
        if isinstance(recovery, Exception):
            raise recovery
        return recovery

    monkeypatch.setattr(agent_step.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(agent_step, "_PROMPT_PICKUP_GRACE", 1)
    monkeypatch.setattr(agent_step, "_interaction_wait_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_step.status_monitor, "get_status", status)
    monkeypatch.setattr(
        agent_step.terminal_service, "probe_post_turn_receipt_result", lambda _: None
    )
    monkeypatch.setattr(agent_step.terminal_service, "redeliver_dropped_message", recover)
    with pytest.raises(agent_step.StepExecutionError) as caught:
        asyncio.run(agent_step._wait_for_completion("test1234", timeout=4, prompt="秘密 café"))
    diagnostic = caught.value.wait_diagnostics
    assert diagnostic.receipt_probe_calls == 2
    assert diagnostic.receipt_probe_errors == 0
    assert diagnostic.prompt_redelivery_attempts == 1
    assert diagnostic.delivery_verified is (recovery is True)
    assert diagnostic.observed_working is False
    assert "秘密" not in repr(diagnostic)


def test_timeout_distinguishes_unreached_probe_from_empty_result(monkeypatch):
    monkeypatch.setattr(agent_step, "_interaction_wait_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_step.status_monitor, "get_status", lambda _: TerminalStatus.UNKNOWN)
    probe = AsyncMock(side_effect=AssertionError("probe must not run for UNKNOWN"))
    monkeypatch.setattr(agent_step.terminal_service, "probe_post_turn_receipt_result", probe)
    with pytest.raises(agent_step.StepExecutionError) as caught:
        asyncio.run(agent_step._wait_for_completion("test1234", timeout=0))
    assert caught.value.wait_diagnostics.receipt_probe_calls == 0
    probe.assert_not_called()


@pytest.mark.parametrize("legacy", [True, False])
def test_matrix_accepts_complete_legacy_or_extended_diagnostic(legacy):
    from test.e2e import test_real_provider_matrix as matrix
    from types import SimpleNamespace

    diagnostic = {
        "phase": "completion",
        "last_status": "idle",
        "observed_working": False,
        "receipt_probe_errors": 0,
        "receipt_candidate_seen": False,
        "stable_receipt_observations": 0,
        "idle_extraction_attempted": False,
    }
    if not legacy:
        diagnostic.update(
            receipt_probe_calls=5, prompt_redelivery_attempts=2, delivery_verified=False
        )
    response = SimpleNamespace(
        status_code=504, json=lambda: {"detail": {"wait_diagnostics": diagnostic}}
    )
    assert matrix._safe_step_failure(response)["wait_diagnostics"] == diagnostic


@pytest.mark.parametrize(
    "changes",
    [
        {"receipt_probe_calls": True},
        {"receipt_probe_calls": -1},
        {"receipt_probe_calls": 2**40},
        {"prompt_redelivery_attempts": "秘密"},
        {"delivery_verified": 1},
        {"unknown": "秘密"},
        {"receipt_probe_calls": None},
    ],
)
def test_matrix_rejects_invalid_extended_observations(changes):
    from test.e2e import test_real_provider_matrix as matrix
    from types import SimpleNamespace

    diagnostic = {
        "phase": "completion",
        "last_status": "idle",
        "observed_working": False,
        "receipt_probe_errors": 0,
        "receipt_candidate_seen": False,
        "stable_receipt_observations": 0,
        "idle_extraction_attempted": False,
        "receipt_probe_calls": 5,
        "prompt_redelivery_attempts": 2,
        "delivery_verified": False,
        **changes,
    }
    response = SimpleNamespace(
        status_code=504, json=lambda: {"detail": {"wait_diagnostics": diagnostic}}
    )
    assert matrix._safe_step_failure(response) == {"http_status": 504}


@pytest.mark.parametrize(
    "field", ["receipt_probe_calls", "prompt_redelivery_attempts", "delivery_verified"]
)
def test_matrix_rejects_partial_extended_shape(field):
    from test.e2e import test_real_provider_matrix as matrix
    from types import SimpleNamespace

    diagnostic = {
        "phase": "completion",
        "last_status": "idle",
        "observed_working": False,
        "receipt_probe_errors": 0,
        "receipt_candidate_seen": False,
        "stable_receipt_observations": 0,
        "idle_extraction_attempted": False,
        field: False if field == "delivery_verified" else 1,
    }
    response = SimpleNamespace(
        status_code=504, json=lambda: {"detail": {"wait_diagnostics": diagnostic}}
    )
    assert matrix._safe_step_failure(response) == {"http_status": 504}
