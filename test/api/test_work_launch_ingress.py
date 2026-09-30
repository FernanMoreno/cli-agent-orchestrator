"""The durable launch HTTP ingress is closed around its injected gateway."""

import pytest
from fastapi.testclient import TestClient

from cli_agent_orchestrator.api.main import app, get_work_launch_principal
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_launch_gateway import (
    DurableLaunchGateway,
    DurableLaunchGatewayError,
    DurableLaunchRequest,
)
from cli_agent_orchestrator.services.work_launch_runtime import LaunchReceipt

_MISSING = object()
_UNAVAILABLE_DETAIL = {
    "code": "launch_runtime_unavailable",
    "message": "Trusted launch runtime is unavailable.",
    "retryable": False,
    "required_action": "inspect_server_configuration",
}
_INTERNAL_DETAIL = {
    "code": "launch_internal_error",
    "message": "Unable to process the launch request.",
    "retryable": False,
    "required_action": "inspect_server_configuration",
}
_IDENTITY_REQUIRED_DETAIL = {
    "code": "launch_identity_required",
    "message": "Verified launch identity is required.",
    "retryable": False,
    "required_action": "authenticate",
}


class RecordingGateway(DurableLaunchGateway):
    """A typed application-state fake that exposes the HTTP boundary inputs."""

    def __init__(self, *, receipt=None, error=None):
        self.receipt = receipt or LaunchReceipt("work-1", "attempt-1", 1, "queued")
        self.error = error
        self.calls = []

    def admit(self, principal, request):
        self.calls.append((principal, request))
        if self.error is not None:
            raise self.error
        return self.receipt


@pytest.fixture
def verified_principal():
    return auth._verified_principal(
        "https://issuer.test/", "verified-owner", [auth.SCOPE_ADMIN], "jwt"
    )


@pytest.fixture
def launch_client(client, verified_principal, monkeypatch):
    previous_gateway = getattr(app.state, "durable_launch_gateway", _MISSING)
    monkeypatch.setenv("CAO_ENABLE_PUBLIC_WORK_INGRESS", "true")
    if previous_gateway is not _MISSING:
        del app.state.durable_launch_gateway
    app.dependency_overrides[get_work_launch_principal] = lambda: verified_principal
    yield client
    app.dependency_overrides.pop(get_work_launch_principal, None)
    if hasattr(app.state, "durable_launch_gateway"):
        del app.state.durable_launch_gateway
    if previous_gateway is not _MISSING:
        app.state.durable_launch_gateway = previous_gateway


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


def test_work_launch_ingress_sanitizes_unverified_identity_before_gateway():
    """Breaks if the shared plain-text 401 or a gateway call reaches this transport."""
    gateway = RecordingGateway()
    previous_gateway = getattr(app.state, "durable_launch_gateway", _MISSING)
    app.state.durable_launch_gateway = gateway
    remote_client = TestClient(
        app,
        base_url="http://localhost",
        client=("192.0.2.1", 50000),
    )
    try:
        response = remote_client.post(
            "/work-launches",
            json=launch_body(),
            headers={"Host": "localhost"},
        )
    finally:
        remote_client.close()
        if previous_gateway is _MISSING:
            del app.state.durable_launch_gateway
        else:
            app.state.durable_launch_gateway = previous_gateway

    assert response.status_code == 401
    assert response.json() == {"detail": _IDENTITY_REQUIRED_DETAIL}
    assert response.headers["www-authenticate"] == "Bearer"
    assert gateway.calls == []
    assert "verified work identity required" not in response.text


def test_work_launch_ingress_uses_only_injected_gateway_and_verified_principal(
    launch_client, verified_principal
):
    """Breaks if ingress derives authority from a body/caller value or returns a terminal."""
    gateway = RecordingGateway(
        receipt=LaunchReceipt("work-7", "attempt-9", 3, "queued"),
    )
    app.state.durable_launch_gateway = gateway

    response = launch_client.post("/work-launches", json=launch_body())

    assert response.status_code == 202
    assert response.json() == {
        "work_item_id": "work-7",
        "attempt_id": "attempt-9",
        "generation": 3,
        "state": "queued",
    }
    assert gateway.calls == [
        (
            verified_principal,
            DurableLaunchRequest(
                selection="trusted-selection",
                agent_profile="developer",
                session_name="durable-session",
                message="review this change",
                allowed_tools=("tool.read",),
            ),
        )
    ]


def test_work_launch_ingress_passes_omitted_selection_as_server_resolvable_none(
    launch_client, verified_principal
):
    gateway = RecordingGateway(
        receipt=LaunchReceipt("work-8", "attempt-10", 1, "queued"),
    )
    app.state.durable_launch_gateway = gateway
    body = launch_body()
    body.pop("selection")

    response = launch_client.post("/work-launches", json=body)

    assert response.status_code == 202
    assert response.json() == {
        "work_item_id": "work-8",
        "attempt_id": "attempt-10",
        "generation": 1,
        "state": "queued",
    }
    assert gateway.calls == [
        (
            verified_principal,
            DurableLaunchRequest(
                selection=None,
                agent_profile="developer",
                session_name="durable-session",
                message="review this change",
                allowed_tools=("tool.read",),
            ),
        )
    ]


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "caller_id",
        "job",
        "grant",
        "contract",
        "snapshot",
        "backend",
        "provider",
        "model",
        "cwd",
        "idempotency_key",
        "task_received",
        "receiver_id",
        "child_id",
        "continuation",
        "attempt_id",
        "generation",
        "receipt",
        "attempt_credential",
        "receiver_credential",
    ],
)
def test_work_launch_ingress_rejects_authority_and_effect_extras_before_gateway_lookup(
    launch_client, forbidden_field
):
    """Breaks if any request field can smuggle authority or execution configuration."""
    response = launch_client.post("/work-launches", json=launch_body(**{forbidden_field: "forged"}))

    assert response.status_code == 422
    assert any(
        error["loc"] == ["body", forbidden_field] and error["type"] == "extra_forbidden"
        for error in response.json()["detail"]
    )


@pytest.mark.parametrize("state_value", [_MISSING, object()])
def test_work_launch_ingress_fails_closed_when_gateway_is_missing_or_malformed(
    launch_client, state_value
):
    """Breaks if a missing server gateway falls back to legacy launch handling."""
    if state_value is not _MISSING:
        app.state.durable_launch_gateway = state_value

    response = launch_client.post("/work-launches", json=launch_body())

    assert response.status_code == 503
    assert response.json() == {"detail": _UNAVAILABLE_DETAIL}


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (
            DurableLaunchGatewayError(
                "launch_authority_denied",
                "Verified launch authority is required.",
                retryable=False,
                required_action="reauthorize",
            ),
            403,
        ),
        (
            DurableLaunchGatewayError(
                "launch_intent_invalid",
                "Launch intent is invalid.",
                retryable=False,
                required_action="correct_launch_intent",
            ),
            422,
        ),
        (
            DurableLaunchGatewayError(
                "launch_idempotency_conflict",
                "Launch operation conflicts with an existing server-owned identity.",
                retryable=False,
                required_action="inspect_existing_launch",
            ),
            409,
        ),
        (
            DurableLaunchGatewayError(
                "launch_context_unavailable",
                "Trusted launch context is unavailable.",
                retryable=False,
                required_action="provision_launch_context",
            ),
            503,
        ),
        (
            DurableLaunchGatewayError(
                "launch_runtime_unavailable",
                "Trusted launch runtime is unavailable.",
                retryable=False,
                required_action="inspect_server_configuration",
            ),
            503,
        ),
    ],
)
def test_work_launch_ingress_maps_sanitized_gateway_errors(launch_client, error, expected_status):
    """Breaks if the transport maps durable error classes to the wrong HTTP family."""
    app.state.durable_launch_gateway = RecordingGateway(error=error)

    response = launch_client.post("/work-launches", json=launch_body())

    assert response.status_code == expected_status
    assert response.json() == {"detail": error.as_dict()}


def test_work_launch_ingress_sanitizes_an_unexpected_gateway_failure(launch_client):
    """Breaks if private gateway failures leak through the public error envelope."""
    app.state.durable_launch_gateway = RecordingGateway(
        error=RuntimeError("private gateway detail")
    )

    response = launch_client.post("/work-launches", json=launch_body())

    assert response.status_code == 500
    assert response.json() == {"detail": _INTERNAL_DETAIL}
    assert "private gateway detail" not in response.text


def test_work_launch_ingress_rejects_malformed_gateway_error_details(launch_client):
    """Only exact public launch envelopes may cross the HTTP boundary."""
    app.state.durable_launch_gateway = RecordingGateway(
        error=DurableLaunchGatewayError(
            "launch_runtime_unavailable",
            "private gateway configuration: token=secret",
            retryable=True,
            required_action="retry_same_intent",
        )
    )

    response = launch_client.post("/work-launches", json=launch_body())

    assert response.status_code == 500
    assert response.json() == {"detail": _INTERNAL_DETAIL}
    assert "token=secret" not in response.text
