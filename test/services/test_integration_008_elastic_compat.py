"""Retain readiness/default-provider behavior at the durable allocation boundary."""

from test.services.test_integration_008_assignment_intent import store  # noqa:F401
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.services import elastic_assignment_service as service


@pytest.fixture
def elastic_context(store, monkeypatch, tmp_path):
    monkeypatch.setenv("CAO_ELASTIC_BROKER_URL", "http://broker.example:9890")
    monkeypatch.setenv("CAO_ELASTIC_BROKER_TOKEN", "test-token")
    monkeypatch.setenv("CAO_ELASTIC_CALLBACK_URL", "http://callback.example:9890")
    monkeypatch.setattr("cli_agent_orchestrator.constants.LOCK_DIR", tmp_path / "locks")
    monkeypatch.setattr(service, "load_agent_profile", lambda _: None)
    monkeypatch.setattr(
        service.terminal_service, "ensure_terminal_is_not_work_owned", lambda _: None
    )
    lease = Mock(status_code=200)
    lease.json.return_value = {
        "worker_id": "worker-1",
        "target_host": "worker.example",
        "working_directory": str(tmp_path),
        "session_name": "cao-worker-1",
    }
    allocate = Mock(return_value=lease)
    remote = Mock(return_value={"success": True, "state": "submitted", "terminal_id": "child-1"})
    monkeypatch.setattr(service.requests, "post", allocate)
    monkeypatch.setattr(service, "_assign_remote", remote)
    return allocate, remote


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting", "expected"), [("soon", 120.0), ("-3", 0.0), ("7.5", 7.5), ("900", 300.0)]
)
async def test_readiness_setting_preserves_safe_default_before_delivery(
    elastic_context, monkeypatch, setting, expected
):
    allocate, remote = elastic_context
    monkeypatch.setenv("CAO_ELASTIC_WORKER_READY_WAIT", setting)
    payload = service.ElasticAssignmentRequest(
        operation_key="readiness-key-1", agent_profile="developer", message="Exact task"
    )
    first = await service.assign("1234abcd", payload, owner="operator")
    assert first["state"] == "submitted" and first["success"]
    assert remote.call_args.kwargs["ready_wait_seconds"] == expected
    assert "provider" not in allocate.call_args.kwargs["json"]
    assert "complete_assignment" in remote.call_args.kwargs["worker_message"]
    assert await service.assign("1234abcd", payload, owner="operator") == first
    assert allocate.call_count == 1 and remote.call_count == 1


@pytest.mark.asyncio
async def test_explicit_provider_forwarded_once(elastic_context):
    allocate, _ = elastic_context
    payload = service.ElasticAssignmentRequest(
        operation_key="provider-key-1",
        agent_profile="developer",
        message="Exact task",
        provider="claude_code",
    )
    result = await service.assign("1234abcd", payload, owner="operator")
    assert result["success"] and allocate.call_args.kwargs["json"]["provider"] == "claude_code"
