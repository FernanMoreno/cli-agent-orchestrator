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
