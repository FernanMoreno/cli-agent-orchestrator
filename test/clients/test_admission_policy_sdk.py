"""Offline HTTP contract tests for the admission-policy SDK."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from cli_agent_orchestrator.clients.admission_policy_sdk import (
    AdmissionPolicyAuthorizationError,
    AdmissionPolicyClient,
    AdmissionPolicyConflictError,
    AdmissionPolicyCredentialError,
    AdmissionPolicyNotFoundError,
    AdmissionPolicyProtocolError,
    AdmissionPolicyTransportError,
    AdmissionPolicyUnavailableError,
    AdmissionPolicyValidationError,
)
from cli_agent_orchestrator.models.admission_policy_control import (
    AdmissionPolicyHistory,
    AdmissionPolicyRecord,
    AdmissionPolicyRevision,
    AdmissionPolicyState,
    AdmissionPolicyUpdate,
    AdmissionPolicyValues,
)


def _record() -> AdmissionPolicyRecord:
    return AdmissionPolicyRecord(
        profile_id=str(uuid4()),
        revision=3,
        active=True,
        policy=AdmissionPolicyValues(),
        actor_principal_id="principal-" + "b" * 64,
        recorded_at=datetime(2026, 11, 4, 12, 30, tzinfo=timezone.utc),
    )


def _http_client(handler) -> httpx.Client:
    return httpx.Client(base_url="https://control.example/", transport=httpx.MockTransport(handler))


def test_sdk_uses_shared_dtos_fixed_paths_and_fresh_bearer_per_operation() -> None:
    record = _record()
    state_json = AdmissionPolicyState(persisted=record, applied_revision=2).model_dump_json()
    record_json = record.model_dump_json()
    history_json = AdmissionPolicyHistory(
        entries=(record,), next_before_revision=2
    ).model_dump_json()
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == f"Bearer token-{len(calls)}"
        if request.url.path == "/admission/policy" and request.method == "GET":
            return httpx.Response(200, content=state_json)
        if request.url.path == "/admission/policy" and request.method == "PUT":
            return httpx.Response(200, content=record_json)
        if request.url.path == "/admission/policy/revoke" and request.method == "POST":
            return httpx.Response(200, content=record_json)
        if request.url.path == "/admission/policy/seed" and request.method == "POST":
            return httpx.Response(200, content=record_json)
        if request.url.path == "/admission/policy/history" and request.method == "GET":
            return httpx.Response(200, content=history_json)
        return httpx.Response(404)

    http_client = _http_client(handle)
    token_calls = 0

    def token_supplier() -> str:
        nonlocal token_calls
        token_calls += 1
        return f"token-{token_calls}"

    sdk = AdmissionPolicyClient(http_client, token_supplier)
    update = AdmissionPolicyUpdate(expected_revision=2, policy=AdmissionPolicyValues())

    assert sdk.get().persisted == record
    assert sdk.update(update) == record
    assert sdk.revoke(AdmissionPolicyRevision(expected_revision=3)) == record
    assert sdk.seed(AdmissionPolicyRevision(expected_revision=0)) == record
    assert sdk.history(limit=17, before_revision=8).entries == (record,)

    assert [(request.method, request.url.path) for request in calls] == [
        ("GET", "/admission/policy"),
        ("PUT", "/admission/policy"),
        ("POST", "/admission/policy/revoke"),
        ("POST", "/admission/policy/seed"),
        ("GET", "/admission/policy/history"),
    ]
    assert calls[1].read() == update.model_dump_json().encode()
    assert (
        calls[2].read() == AdmissionPolicyRevision(expected_revision=3).model_dump_json().encode()
    )
    assert (
        calls[3].read() == AdmissionPolicyRevision(expected_revision=0).model_dump_json().encode()
    )
    assert dict(calls[4].url.params) == {"limit": "17", "before_revision": "8"}
    assert token_calls == len(calls) == 5
    assert not http_client.is_closed
    http_client.close()


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, AdmissionPolicyAuthorizationError),
        (403, AdmissionPolicyAuthorizationError),
        (404, AdmissionPolicyNotFoundError),
        (409, AdmissionPolicyConflictError),
        (422, AdmissionPolicyValidationError),
        (503, AdmissionPolicyUnavailableError),
    ],
)
def test_http_errors_are_typed_without_echoing_response_body(
    status: int, error_type: type[Exception]
) -> None:
    secret_body = "do-not-echo-response-secret"
    client = _http_client(lambda _request: httpx.Response(status, text=secret_body))
    sdk = AdmissionPolicyClient(client, lambda: "do-not-echo-token")

    with pytest.raises(error_type) as raised:
        sdk.get()

    assert secret_body not in str(raised.value)
    assert "do-not-echo-token" not in repr(raised.value)
    if hasattr(raised.value, "status_code"):
        assert raised.value.status_code == status
    client.close()


def test_conflict_mutation_is_sent_once_and_response_body_is_not_exposed() -> None:
    calls = 0
    secret_body = "credential-from-server-body"

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(409, text=secret_body)

    client = _http_client(handle)
    sdk = AdmissionPolicyClient(
        client,
        lambda: "opaque-token",
    )

    with pytest.raises(AdmissionPolicyConflictError) as raised:
        sdk.update(AdmissionPolicyUpdate(expected_revision=2, policy=AdmissionPolicyValues()))

    assert calls == 1
    assert secret_body not in str(raised.value)
    client.close()


def test_transport_and_malformed_response_errors_are_fixed_and_do_not_retry() -> None:
    calls = 0
    request_secret = "network-error-secret"

    def fail_transport(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError(request_secret, request=request)

    transport_client = _http_client(fail_transport)
    transport_sdk = AdmissionPolicyClient(transport_client, lambda: "token")
    with pytest.raises(AdmissionPolicyTransportError) as transport_error:
        transport_sdk.get()
    assert calls == 1
    assert request_secret not in str(transport_error.value)
    transport_client.close()

    malformed_client = _http_client(lambda _request: httpx.Response(200, text="private bad body"))
    malformed_sdk = AdmissionPolicyClient(malformed_client, lambda: "token")
    with pytest.raises(AdmissionPolicyProtocolError) as protocol_error:
        malformed_sdk.get()
    assert "private bad body" not in str(protocol_error.value)
    malformed_client.close()


def test_redirects_are_not_followed_even_when_the_injected_client_allows_them() -> None:
    calls: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(307, headers={"Location": "https://other.example/admission/policy"})

    client = httpx.Client(
        base_url="https://control.example/",
        transport=httpx.MockTransport(redirect),
        follow_redirects=True,
    )
    sdk = AdmissionPolicyClient(client, lambda: "bearer-secret")

    with pytest.raises(AdmissionPolicyProtocolError):
        sdk.update(AdmissionPolicyUpdate(expected_revision=2, policy=AdmissionPolicyValues()))

    assert len(calls) == 1
    assert calls[0].headers["Authorization"] == "Bearer bearer-secret"
    client.close()


def test_bad_credentials_and_history_arguments_are_rejected_before_http() -> None:
    calls = 0

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = _http_client(handle)
    sdk = AdmissionPolicyClient(client, lambda: "secret\r\nInjected: header")
    with pytest.raises(AdmissionPolicyCredentialError):
        sdk.get()
    assert calls == 0

    sdk = AdmissionPolicyClient(client, lambda: "token")
    for values in (
        {"limit": True},
        {"limit": 0},
        {"limit": 101},
        {"before_revision": False},
        {"before_revision": 0},
    ):
        with pytest.raises(ValueError):
            sdk.history(**values)
    assert calls == 0
    with pytest.raises(ValueError):
        sdk.seed(AdmissionPolicyRevision(expected_revision=1))
    assert calls == 0
    client.close()
