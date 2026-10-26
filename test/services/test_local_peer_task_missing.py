"""Remote missing-task observations preserve receipts and transport failure semantics."""

import json
from types import SimpleNamespace

import pytest
import requests

from cli_agent_orchestrator.services import local_peer_service as service

TASK_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SOURCE_INSTANCE_ID = "source-profile"
TARGET_INSTANCE_ID = "target-profile"
PROJECT_ID = "b" * 64
REQUESTER_TERMINAL_ID = "deadbeef"

REMOTE_TASK = {
    "task_id": TASK_ID,
    "source_instance_id": SOURCE_INSTANCE_ID,
    "target_instance_id": TARGET_INSTANCE_ID,
    "requester_terminal_id": REQUESTER_TERMINAL_ID,
    "project_id": PROJECT_ID,
    "operation_key": "remote-lookup-01",
    "assignment_id": "assignment-1",
    "terminal_id": "facecafe",
    "state": "running",
    "result": None,
}


@pytest.fixture
def remote_task_boundary(monkeypatch):
    """Keep service dispatch real while isolating lookup, transport, and storage."""
    calls = []
    updates = []
    outbound = []
    task = dict(REMOTE_TASK)
    identity = SimpleNamespace(instance_id=SOURCE_INSTANCE_ID)
    peer = SimpleNamespace(base_url="http://127.0.0.1:19891")

    def load_task(task_id):
        assert task_id == TASK_ID
        return dict(task), False

    def resolve_peer(peer_id, project_id, scope):
        outbound.append((peer_id, project_id, scope))
        return peer, identity

    def record_update(task_id, **changes):
        updates.append((task_id, dict(changes)))
        persisted = {**task, **changes}
        if "result_json" in changes:
            raw_result = changes["result_json"]
            persisted["result"] = json.loads(raw_result) if raw_result is not None else None
        return persisted

    monkeypatch.setattr(service, "_find_task", load_task)
    monkeypatch.setattr(service, "current_server_identity", lambda: identity)
    monkeypatch.setattr(service, "_outbound_peer", resolve_peer)
    monkeypatch.setattr(service, "_signed_headers", lambda **_kwargs: {})
    monkeypatch.setattr(service, "_update_task", record_update)

    return SimpleNamespace(
        task=task,
        calls=calls,
        updates=updates,
        outbound=outbound,
        peer=peer,
        identity=identity,
    )


def _response(status_code: int, body: object | bytes) -> requests.Response:
    """Build a genuine requests.Response so raise_for_status attaches it to HTTPError."""
    response = requests.Response()
    response.status_code = status_code
    response.url = "http://127.0.0.1:19891/local-coordination/tasks/" + TASK_ID
    response._content = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    return response


def _install_responder(monkeypatch, boundary, responder):
    def request(method, url, **kwargs):
        boundary.calls.append((method, url, kwargs))
        return responder()

    monkeypatch.setattr(service, "_loopback_request", request)


def _call(operation: str):
    if operation == "status":
        return service.task_from_peer(TASK_ID, requester_terminal_id=REQUESTER_TERMINAL_ID)
    return service.cancel_task_at_peer(TASK_ID)


def _expected_scope(operation: str) -> str:
    return "task:status" if operation == "status" else "task:cancel"


@pytest.mark.parametrize("operation", ["status", "cancel"])
def test_exact_remote_task_not_found_propagates_without_receipt_mutation_or_cache(
    monkeypatch, remote_task_boundary, operation
):
    boundary = remote_task_boundary
    _install_responder(
        monkeypatch,
        boundary,
        lambda: _response(404, {"detail": {"kind": "task_not_found"}}),
    )

    for _ in range(2):
        with pytest.raises(service.LocalPeerError):
            _call(operation)

    assert len(boundary.calls) == 2, "each retry should consult the remote task owner"
    assert boundary.updates == [], "typed absence must not rewrite the cached source receipt"
    assert boundary.outbound == [
        (TARGET_INSTANCE_ID, PROJECT_ID, _expected_scope(operation)),
        (TARGET_INSTANCE_ID, PROJECT_ID, _expected_scope(operation)),
    ]


_NON_MISSING_REMOTE_FAILURES = (
    "server_error",
    "forbidden",
    "other_not_found",
    "extended_task_not_found",
    "malformed_not_found",
    "timeout",
)


@pytest.mark.parametrize("operation", ["status", "cancel"])
@pytest.mark.parametrize("failure", _NON_MISSING_REMOTE_FAILURES)
def test_other_remote_failures_keep_their_retryable_or_reconcile_fallback(
    monkeypatch, remote_task_boundary, operation, failure
):
    boundary = remote_task_boundary

    def respond():
        if failure == "server_error":
            return _response(503, {"detail": {"kind": "peer_unavailable"}})
        if failure == "forbidden":
            return _response(403, {"detail": {"kind": "scope_denied"}})
        if failure == "other_not_found":
            return _response(404, {"detail": {"kind": "route_not_found"}})
        if failure == "extended_task_not_found":
            return _response(404, {"detail": {"kind": "task_not_found", "reason": "extra"}})
        if failure == "malformed_not_found":
            return _response(404, b"not json")
        raise requests.Timeout("remote peer timed out")

    _install_responder(monkeypatch, boundary, respond)

    result = _call(operation)

    if operation == "status":
        assert result == {
            **boundary.task,
            "peer_state": "unavailable",
            "error": "peer_unavailable",
        }
    else:
        assert result == {
            **boundary.task,
            "state": "reconcile",
            "error": "reconcile_required",
        }
    assert boundary.calls
    assert boundary.updates == []


@pytest.mark.parametrize("operation", ["status", "cancel"])
def test_healthy_remote_receipt_still_flows_to_local_receipt_update(
    monkeypatch, remote_task_boundary, operation
):
    boundary = remote_task_boundary
    receipt = {
        "assignment_id": "assignment-1",
        "terminal_id": "facecafe",
        "state": "succeeded",
        "result": {"state": "succeeded", "output": "done"},
    }
    _install_responder(monkeypatch, boundary, lambda: _response(200, receipt))

    result = _call(operation)

    if operation == "status":
        expected_changes = {
            "assignment_id": "assignment-1",
            "terminal_id": "facecafe",
            "state": "succeeded",
            "result_json": json.dumps(receipt["result"], sort_keys=True),
        }
    else:
        expected_changes = {
            "state": "succeeded",
            "result_json": json.dumps(receipt["result"], sort_keys=True),
        }
    assert boundary.updates == [(TASK_ID, expected_changes)]
    assert result["state"] == "succeeded"
    assert result["result"] == receipt["result"]
