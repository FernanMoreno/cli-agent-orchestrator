"""Shared activation is a strict, authenticated control operation, not enrollment."""

from test.api.test_admission_control import control, headers

import pytest

from cli_agent_orchestrator.clients.admission_repository import (
    SharedAdmissionDenied,
    SharedCapacityUnavailable,
    SharedCommitUncertain,
    SharedRevisionConflict,
)
from cli_agent_orchestrator.security.auth import is_verified_principal
from cli_agent_orchestrator.services.inbox_admission_runtime import InboxAdmissionRuntime
from cli_agent_orchestrator.services.shared_delivery_admission import SharedDeliveryUnavailable

pytestmark = pytest.mark.offline_database
PATH = "/admission/shared/activate"


def test_verified_admin_activates_without_exposing_identity(control, monkeypatch):
    client, _, key = control
    calls = []

    async def activate(self, *, principal):
        assert is_verified_principal(principal) and principal.kind == "jwt"
        calls.append(principal)

    monkeypatch.setattr(InboxAdmissionRuntime, "activate_shared", activate)
    response = client.post(PATH, json={"version": 1}, headers=headers(key))
    assert response.status_code == 200
    assert response.json() == {"version": 1, "activated": True}
    assert response.headers["cache-control"] == "no-store"
    assert len(calls) == 1


@pytest.mark.parametrize("scopes", ["cao:read", "cao:write"])
def test_nonadmin_is_denied_before_control_callback(control, monkeypatch, scopes):
    client, _, key = control

    async def forbidden(*args, **kwargs):
        pytest.fail("unauthorized shared activation")

    monkeypatch.setattr(InboxAdmissionRuntime, "activate_shared", forbidden)
    assert client.post(PATH, json={"version": 1}, headers=headers(key, scopes)).status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"version": True},
        {"version": "1"},
        {"version": 2},
        {"version": 1, "binding": "private-input-canary"},
    ],
)
def test_malformed_activation_is_fixed_and_has_no_effect(control, monkeypatch, body):
    client, _, key = control

    async def forbidden(*args, **kwargs):
        pytest.fail("malformed activation reached callback")

    monkeypatch.setattr(InboxAdmissionRuntime, "activate_shared", forbidden)
    response = client.post(PATH, json=body, headers=headers(key))
    assert response.status_code == 422
    assert "private-input-canary" not in response.text
    assert response.json() == {"detail": {"code": "invalid_resource_envelope"}}


@pytest.mark.parametrize(
    "error",
    [
        SharedAdmissionDenied,
        SharedCapacityUnavailable,
        SharedCommitUncertain,
        SharedRevisionConflict,
        SharedDeliveryUnavailable,
    ],
)
def test_shared_failures_have_fixed_unavailable_response(control, monkeypatch, error):
    client, _, key = control

    async def fail(*args, **kwargs):
        if error is SharedDeliveryUnavailable:
            raise error()
        raise error("private-ledger-canary")

    monkeypatch.setattr(InboxAdmissionRuntime, "activate_shared", fail)
    response = client.post(PATH, json={"version": 1}, headers=headers(key))
    assert response.status_code == 503 and "private-ledger-canary" not in response.text
    assert response.json() == {"detail": {"code": "resource_control_unavailable"}}
    assert response.headers["cache-control"] == "no-store"


def test_local_runtime_cannot_be_implicitly_enabled_by_admin(control):
    client, _, key = control
    response = client.post(PATH, json={"version": 1}, headers=headers(key))
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "resource_control_unavailable"}}


def test_anonymous_cannot_activate(control):
    client, _, _ = control
    assert client.post(PATH, json={"version": 1}).status_code == 401
