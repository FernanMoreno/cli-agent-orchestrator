"""Malformed broker replies cannot authorize delivery or physical retirement."""

from test.services.test_integration_008_assignment_intent import store
from test.services.test_integration_008_elastic_compat import elastic_context
from unittest.mock import Mock, patch

import pytest

from cli_agent_orchestrator.services import elastic_assignment_service as service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("worker_id", {"coerced": "identity"}),
        ("worker_id", "../another-worker"),
        ("worker_id", "worker-1\n"),
        ("worker_id", ""),
        ("target_host", True),
        ("working_directory", ["/unexpected"]),
        ("session_name", {"session": "unexpected"}),
    ],
)
async def test_invalid_lease_stops_before_remote_effect_and_does_not_replay(
    elastic_context, field, value
):
    allocate, remote = elastic_context
    allocate.return_value.json.return_value[field] = value
    allocate.return_value.json.return_value["release_token"] = "private-token-must-not-be-persisted"
    payload = service.ElasticAssignmentRequest(
        operation_key="invalid-broker-shape", agent_profile="developer", message="Exact task"
    )
    with patch.object(service.requests, "delete") as release:
        result = await service.assign("1234abcd", payload, owner="operator")
        assert result["state"] == "reconcile" and not result["success"]
        assert result["worker_id"] == (None if field == "worker_id" else "worker-1")
        assert not remote.called and not release.called
        assert "private-token-must-not-be-persisted" not in str(result)
        assert await service.assign("1234abcd", payload, owner="operator") == result
    assert allocate.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,receipt",
    [
        (200, {"released": True, "workload_present": True}),
        (200, {"released": True}),
        (200, {"released": "true", "workload_present": False}),
        (200, {"released": True, "workload_present": 0}),
        (200, {"released": False, "workload_present": False}),
        (200, None),
        (404, {}),
    ],
)
async def test_status_alone_does_not_prove_worker_retirement(elastic_context, status, receipt):
    allocate, remote = elastic_context
    remote.return_value = {"success": False, "state": "refused"}
    response = Mock(status_code=status)
    response.json.return_value = receipt
    payload = service.ElasticAssignmentRequest(
        operation_key="uncertain-broker-release", agent_profile="developer", message="Exact task"
    )
    with patch.object(service.requests, "delete", return_value=response) as release:
        result = await service.assign("1234abcd", payload, owner="operator")
        assert result["state"] == "reconcile" and not result["success"]
        assert result["worker_id"] == "worker-1"
        assert not result.get("worker_released", False)
        assert await service.assign("1234abcd", payload, owner="operator") == result
    assert allocate.call_count == remote.call_count == release.call_count == 1


@pytest.mark.asyncio
async def test_retirement_requires_positive_typed_broker_absence_receipt(elastic_context):
    _, remote = elastic_context
    remote.return_value = {"success": False, "state": "refused"}
    response = Mock(status_code=200)
    response.json.return_value = {"released": True, "workload_present": False}
    payload = service.ElasticAssignmentRequest(
        operation_key="positive-broker-release", agent_profile="developer", message="Exact task"
    )
    with patch.object(service.requests, "delete", return_value=response):
        result = await service.assign("1234abcd", payload, owner="operator")
    assert result["state"] == "refused" and result["worker_released"] is True


@pytest.mark.asyncio
async def test_actual_broker_identity_and_extra_token_do_not_change_delivery(elastic_context):
    allocate, remote = elastic_context
    allocate.return_value.json.return_value.update(
        worker_id="deadbeef", release_token="fixture-release"
    )
    payload = service.ElasticAssignmentRequest(
        operation_key="actual-broker-shape", agent_profile="developer", message="Exact task"
    )
    result = await service.assign("1234abcd", payload, owner="operator")
    assert result["state"] == "submitted" and result["worker_id"] == "deadbeef"
    assert "fixture-release" not in str(result)
    assert allocate.call_count == remote.call_count == 1
