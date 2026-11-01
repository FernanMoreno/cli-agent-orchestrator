"""Strict recovery control, separate from activation and Work cancellation."""

from test.api.test_admission_control import control, headers
from uuid import uuid4

import pytest

from cli_agent_orchestrator.clients.admission_repository import (
    SharedActionCommitUncertain,
    SharedAdmissionDenied,
    SharedClosureUnproven,
    SharedRevisionConflict,
)
from cli_agent_orchestrator.models.admission_domains import SharedReleaseReceipt
from cli_agent_orchestrator.security.auth import is_verified_principal
from cli_agent_orchestrator.services.inbox_admission_runtime import InboxAdmissionRuntime

pytestmark = pytest.mark.offline_database
PATH = "/admission/shared/release-witnessed"


def body():
    return {
        "version": 1,
        "action_id": str(uuid4()),
        "operation_id": str(uuid4()),
        "expected_revision": 3,
    }


def test_verified_admin_receives_exact_typed_release_ack(control, monkeypatch):
    client, _, key = control
    calls = []

    async def aborted(self, *, principal, **kwargs):
        assert is_verified_principal(principal) and principal.kind == "jwt"
        calls.append(kwargs)
        return SharedReleaseReceipt(kwargs["action_id"], kwargs["operation_id"], 4)

    monkeypatch.setattr(InboxAdmissionRuntime, "release_witnessed", aborted, raising=False)
    request = body()
    response = client.post(PATH, json=request, headers=headers(key))
    assert response.status_code == 200
    assert response.json() == {
        "version": 1,
        "action_id": request["action_id"],
        "operation_id": request["operation_id"],
        "revision": 4,
        "state": "released",
    }
    assert calls == [{k: v for k, v in request.items() if k != "version"}]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("scopes", ["cao:read", "cao:write"])
def test_nonadmin_is_refused_before_abort_callback(control, monkeypatch, scopes):
    client, _, key = control

    async def forbidden(*args, **kwargs):
        pytest.fail("nonadmin reached abort")

    monkeypatch.setattr(InboxAdmissionRuntime, "release_witnessed", forbidden, raising=False)
    assert client.post(PATH, json=body(), headers=headers(key, scopes)).status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"version": 2},
        {"action_id": "private-input-canary"},
        {"operation_id": 7},
        {"expected_revision": True},
        {"expected_revision": 2},
        {"expected_revision": 2**63 - 1},
        {"expected_revision": "3"},
        {"actor_principal_id": "private-input-canary"},
        {"binding": "private-input-canary"},
    ],
)
def test_invalid_abort_body_is_fixed_and_does_not_invoke_control(control, monkeypatch, change):
    client, _, key = control

    async def forbidden(*args, **kwargs):
        pytest.fail("invalid abort reached callback")

    monkeypatch.setattr(InboxAdmissionRuntime, "release_witnessed", forbidden, raising=False)
    response = client.post(PATH, json={**body(), **change}, headers=headers(key))
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_resource_envelope"}}
    assert "private-input-canary" not in response.text


def test_abort_conflict_is_409_without_changing_activation_errors(control, monkeypatch):
    client, _, key = control

    async def conflict(*args, **kwargs):
        raise SharedRevisionConflict("private-state-canary")

    monkeypatch.setattr(InboxAdmissionRuntime, "release_witnessed", conflict, raising=False)
    monkeypatch.setattr(InboxAdmissionRuntime, "activate_shared", conflict)
    response = client.post(PATH, json=body(), headers=headers(key))
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "resource_operation_conflict"}}
    activation = client.post(
        "/admission/shared/activate", json={"version": 1}, headers=headers(key)
    )
    assert activation.status_code == 503
    assert "private-state-canary" not in response.text + activation.text


@pytest.mark.parametrize("uncertain", [False, True])
def test_unavailable_or_uncertain_abort_is_fixed_503(control, monkeypatch, uncertain):
    client, _, key = control

    async def fail(*args, **kwargs):
        if uncertain:
            raise SharedActionCommitUncertain(kwargs["action_id"], kwargs["operation_id"])
        raise SharedAdmissionDenied("private-ledger-canary")

    monkeypatch.setattr(InboxAdmissionRuntime, "release_witnessed", fail, raising=False)
    response = client.post(PATH, json=body(), headers=headers(key))
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "private-ledger-canary" not in response.text


def test_local_or_anonymous_request_cannot_implicitly_enable_recovery(control):
    client, _, key = control
    assert client.post(PATH, json=body(), headers=headers(key)).status_code == 503
    assert client.post(PATH, json=body()).status_code == 401


def test_missing_physical_witness_is_explicit_409(control, monkeypatch):
    client, _, key = control

    async def unproven(*args, **kwargs):
        raise SharedClosureUnproven("private-native-canary")

    monkeypatch.setattr(InboxAdmissionRuntime, "release_witnessed", unproven, raising=False)
    response = client.post(PATH, json=body(), headers=headers(key))
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "resource_closure_unproven"}}
    assert response.headers["cache-control"] == "no-store"
