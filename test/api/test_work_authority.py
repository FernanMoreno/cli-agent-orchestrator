"""The durable launch ingress requires a bearer even on a loopback socket."""

import pytest
from fastapi.testclient import TestClient

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_launch_gateway import DurableLaunchGateway
from cli_agent_orchestrator.services.work_launch_runtime import LaunchReceipt

_MISSING = object()
_IDENTITY_REQUIRED_DETAIL = {
    "code": "launch_identity_required",
    "message": "Verified launch identity is required.",
    "retryable": False,
    "required_action": "authenticate",
}


class RecordingGateway(DurableLaunchGateway):
    """Typed gateway double used to observe the HTTP authority boundary."""

    def __init__(self):
        self.calls = []

    def admit(self, principal, request):
        self.calls.append((principal, request))
        return LaunchReceipt("work-1", "attempt-1", 1, "queued")


def launch_body(**extra):
    body = {
        "selection": "trusted-selection",
        "agent_profile": "developer",
        "session_name": "durable-session",
        "message": "review this change",
        "allowed_tools": ["tool.read"],
    }
    body.update(extra)
    return body


@pytest.fixture
def loopback_launch_client(monkeypatch):
    """Install only a typed gateway while global bearer verification is off."""
    for name in ("AUTH0_DOMAIN", "CAO_AUTH_JWKS_URI", "CAO_AUTH_ISSUER", "CAO_AUTH_AUDIENCE"):
        monkeypatch.delenv(name, raising=False)

    gateway = RecordingGateway()
    previous_gateway = getattr(main.app.state, "durable_launch_gateway", _MISSING)
    main.app.state.durable_launch_gateway = gateway
    client = TestClient(
        main.app,
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    )
    try:
        yield client, gateway
    finally:
        client.close()
        if previous_gateway is _MISSING:
            del main.app.state.durable_launch_gateway
        else:
            main.app.state.durable_launch_gateway = previous_gateway


@pytest.mark.parametrize(
    "body",
    [
        launch_body(),
        launch_body(
            actor="operator",
            job="operator-job",
            grant="operator-grant",
            caller_id="operator",
        ),
    ],
)
def test_loopback_work_launch_requires_bearer_before_gateway_or_body_authority(
    loopback_launch_client, body
):
    """Breaks if loopback or request data becomes launch identity without a bearer."""
    client, gateway = loopback_launch_client

    response = client.post(
        "/work-launches",
        json=body,
        headers={
            "Host": "localhost",
            "X-Forwarded-For": "198.51.100.7",
            "X-Forwarded-Host": "operator.example.test",
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": _IDENTITY_REQUIRED_DETAIL}
    assert response.headers["www-authenticate"] == "Bearer"
    assert gateway.calls == []


def test_verified_bearer_principal_reaches_work_launch_gateway(loopback_launch_client, monkeypatch):
    """Breaks if the local bearer-verification seam is bypassed before admission."""
    client, gateway = loopback_launch_client
    principal = auth._verified_principal(
        "https://issuer.test/", "launch-subject", [auth.SCOPE_ADMIN], "jwt"
    )
    monkeypatch.setattr(main, "principal_from_token", lambda token: principal, raising=False)

    response = client.post(
        "/work-launches",
        json=launch_body(),
        headers={"Authorization": "Bearer verified-launch-subject"},
    )

    assert response.status_code == 202
    assert gateway.calls[0][0] == principal
