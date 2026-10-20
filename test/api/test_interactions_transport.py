"""HTTP transport auth and strict-wire boundary for Spec 016 interactions."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cli_agent_orchestrator.api import interactions as transport
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.interactions.models import (
    AnswerReceipt,
    CreatedGroup,
    InteractionAnswerPayload,
    InteractionPage,
    ReturnedAnswer,
    WaitResult,
)
from cli_agent_orchestrator.services.interactions.service import InteractionService

QUESTION_ID = "d97001ba-d4ed-4c78-b3b9-22fa8c77631d"
GROUP_ID = "2e1fc625-a391-4bfd-a62d-4dddbcbf70a3"


def _service_mock():
    # Keep the transport double within the production service contract, including
    # available methods and the app-scoped resolver's declared service type.
    return Mock(spec=InteractionService)


def _client(monkeypatch, service, *, scopes=None):
    principal = auth._verified_principal(
        "https://issuer.test/",
        "interaction-owner",
        scopes or [auth.SCOPE_READ, auth.SCOPE_WRITE],
        "jwt",
    )
    app = FastAPI()
    app.include_router(transport.router)
    app.state.interaction_service = service
    app.dependency_overrides[auth.get_current_principal] = lambda: principal
    monkeypatch.setattr(transport, "get_local_bearer", lambda: "machine-service-bearer")
    return TestClient(app)


def test_app_service_resolver_rejects_an_untyped_configured_object(monkeypatch):
    client = _client(monkeypatch, object())

    with pytest.raises(RuntimeError, match="must be an InteractionService"):
        client.get("/interactions")


def test_app_service_resolver_uses_the_default_when_no_service_is_configured(monkeypatch):
    from cli_agent_orchestrator.services.interactions import service as service_module

    service = _service_mock()
    service.list_metadata.return_value = InteractionPage(items=(), next_cursor=None)
    monkeypatch.setattr(service_module, "get_default_interaction_service", lambda: service)
    client = _client(monkeypatch, None)

    response = client.get("/interactions")

    assert response.status_code == 200
    service.list_metadata.assert_called_once()


def test_human_routes_reject_the_configured_machine_bearer(monkeypatch):
    service = _service_mock()
    client = _client(monkeypatch, service)

    response = client.get(
        "/interactions", headers={"Authorization": "Bearer machine-service-bearer"}
    )

    assert response.status_code == 403
    service.list_metadata.assert_not_called()


def test_human_routes_reject_agent_capability_as_identity(monkeypatch):
    service = _service_mock()
    client = _client(monkeypatch, service)

    response = client.get(
        "/interactions",
        headers={"X-CAO-Interaction-Capability": "per-terminal-secret"},
    )

    assert response.status_code == 403
    service.list_metadata.assert_not_called()


def test_source_creation_does_not_bind_agent_or_machine_credentials_as_owner(monkeypatch):
    owner = auth._verified_principal(
        "https://issuer.test/", "interaction-owner", [auth.SCOPE_WRITE], "jwt"
    )
    resolve = AsyncMock(return_value=owner)
    monkeypatch.setattr(transport, "get_current_principal", resolve)
    monkeypatch.setattr(transport, "get_local_bearer", lambda: "machine-service-bearer")

    machine_request = SimpleNamespace(headers={"Authorization": "Bearer machine-service-bearer"})
    assert asyncio.run(transport.optional_creation_principal(machine_request)) is None
    resolve.assert_awaited_once()

    resolve.reset_mock()
    agent_request = SimpleNamespace(headers={"X-CAO-Interaction-Capability": "source-capability"})
    assert asyncio.run(transport.optional_creation_principal(agent_request)) is None
    resolve.assert_not_awaited()

    resolve.reset_mock()
    human_request = SimpleNamespace(headers={})
    assert asyncio.run(transport.optional_creation_principal(human_request)) is owner
    resolve.assert_awaited_once()


def test_admin_principal_also_satisfies_read_scope(monkeypatch):
    service = _service_mock()
    service.list_metadata.return_value = InteractionPage(items=(), next_cursor=None)
    client = _client(monkeypatch, service, scopes=[auth.SCOPE_ADMIN])

    response = client.get("/interactions")

    assert response.status_code == 200
    service.list_metadata.assert_called_once()


def test_write_principal_can_read_metadata_without_gaining_content_authority(monkeypatch):
    service = _service_mock()
    service.list_metadata.return_value = InteractionPage(items=(), next_cursor=None)
    client = _client(monkeypatch, service, scopes=[auth.SCOPE_WRITE])

    response = client.get("/interactions")

    assert response.status_code == 200
    service.list_metadata.assert_called_once()


def test_agent_route_uses_only_the_interaction_capability(monkeypatch):
    service = _service_mock()
    service.agent_list.return_value = InteractionPage(items=(), next_cursor=None)
    client = _client(monkeypatch, service)

    missing = client.get("/agent/interactions")
    assert missing.status_code == 401
    service.agent_list.assert_not_called()

    response = client.get(
        "/agent/interactions",
        headers={"X-CAO-Interaction-Capability": "per-terminal-secret"},
    )

    assert response.status_code == 200
    service.agent_list.assert_called_once_with(
        "per-terminal-secret",
        filters={"status": None},
        cursor=None,
        limit=50,
    )


def test_interaction_status_filter_uses_the_lifecycle_enum(monkeypatch):
    service = _service_mock()
    client = _client(monkeypatch, service)

    response = client.get(
        "/agent/interactions?status=unknown",
        headers={"X-CAO-Interaction-Capability": "per-terminal-secret"},
    )

    assert response.status_code == 422
    service.agent_list.assert_not_called()


def test_ack_body_requires_canonical_uuid_question_ids(monkeypatch):
    service = _service_mock()
    client = _client(monkeypatch, service)

    response = client.post(
        f"/agent/interactions/groups/{GROUP_ID}/ack",
        headers={"X-CAO-Interaction-Capability": "per-terminal-secret"},
        json={"receipts": [{"question_id": "../terminals", "version": 1, "receipt": "r" * 48}]},
    )

    assert response.status_code == 422
    service.acknowledge_answers.assert_not_called()


@pytest.mark.offline_database
def test_ack_body_rejects_non_ascii_receipt_before_service_call(monkeypatch):
    service = _service_mock()
    service.acknowledge_answers.return_value = []
    client = _client(monkeypatch, service)

    response = client.post(
        f"/agent/interactions/groups/{GROUP_ID}/ack",
        headers={"X-CAO-Interaction-Capability": "per-terminal-secret"},
        json={
            "receipts": [
                {
                    "question_id": QUESTION_ID,
                    "version": 1,
                    "receipt": "r" * 42 + "ñ",
                }
            ]
        },
    )

    assert response.status_code == 422
    assert "r" * 42 + "ñ" not in response.text
    service.acknowledge_answers.assert_not_called()


@pytest.mark.parametrize(
    "receipts, opaque_receipt",
    [
        (
            [
                {
                    "question_id": QUESTION_ID,
                    "version": 1,
                    "receipt": "short-opaque-RECEIPT-SENTINEL",
                }
            ],
            "short-opaque-RECEIPT-SENTINEL",
        ),
        (
            [
                {
                    "question_id": QUESTION_ID,
                    "version": 1,
                    "receipt": "LONG_OPAQUE_RECEIPT_SENTINEL_" * 6,
                }
            ],
            "LONG_OPAQUE_RECEIPT_SENTINEL_" * 6,
        ),
        (
            [
                {
                    "question_id": QUESTION_ID,
                    "version": 1,
                    "receipt": "LIST_OPAQUE_RECEIPT_SENTINEL_000000000000",
                }
            ]
            * 21,
            "LIST_OPAQUE_RECEIPT_SENTINEL_000000000000",
        ),
        (
            [
                {
                    "version": 1,
                    "receipt": "MISSING_ID_OPAQUE_RECEIPT_SENTINEL_000000",
                }
            ],
            "MISSING_ID_OPAQUE_RECEIPT_SENTINEL_000000",
        ),
    ],
)
@pytest.mark.offline_database
def test_application_ack_validation_never_echoes_receipts(receipts, opaque_receipt):
    from cli_agent_orchestrator.api.main import app

    response = TestClient(app, base_url="http://localhost").post(
        f"/agent/interactions/groups/{GROUP_ID}/ack",
        headers={"X-CAO-Interaction-Capability": "per-terminal-secret"},
        json={"receipts": receipts},
    )

    assert response.status_code == 422
    assert opaque_receipt not in response.text
    assert response.json() == {
        "code": "invalid",
        "detail": "The interaction request is invalid.",
        "current_version": None,
    }


@pytest.mark.offline_database
def test_router_mount_redacts_pre_endpoint_ack_validation(monkeypatch):
    service = _service_mock()
    service.acknowledge_answers.return_value = []
    client = _client(monkeypatch, service)
    opaque_receipt = "short-ROUTER_RECEIPT_SENTINEL"

    response = client.post(
        f"/agent/interactions/groups/{GROUP_ID}/ack",
        headers={"X-CAO-Interaction-Capability": "per-terminal-secret"},
        json={"receipts": [{"question_id": QUESTION_ID, "version": 1, "receipt": opaque_receipt}]},
    )

    assert response.status_code == 422
    assert opaque_receipt not in response.text
    assert response.json() == {
        "code": "invalid",
        "detail": "The interaction request is invalid.",
        "current_version": None,
    }
    service.acknowledge_answers.assert_not_called()


@pytest.mark.offline_database
def test_other_application_validation_keeps_fastapi_error_shape():
    from cli_agent_orchestrator.api.main import app

    response = TestClient(app, base_url="http://localhost").get(
        "/agent/interactions?status=not-a-status",
        headers={"X-CAO-Interaction-Capability": "per-terminal-secret"},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["input"] == "not-a-status"


@pytest.mark.offline_database
def test_ack_body_forwards_ascii_receipt_to_service(monkeypatch):
    service = _service_mock()
    service.acknowledge_answers.return_value = []
    client = _client(monkeypatch, service)
    receipt = "r" * 48

    response = client.post(
        f"/agent/interactions/groups/{GROUP_ID}/ack",
        headers={"X-CAO-Interaction-Capability": "per-terminal-secret"},
        json={"receipts": [{"question_id": QUESTION_ID, "version": 1, "receipt": receipt}]},
    )

    assert response.status_code == 200
    assert service.acknowledge_answers.call_args.args[2][0].receipt == receipt


def test_agent_group_body_rejects_caller_selected_identity(monkeypatch):
    service = _service_mock()
    client = _client(monkeypatch, service)

    response = client.post(
        "/agent/interactions/groups",
        headers={
            "X-CAO-Interaction-Capability": "per-terminal-secret",
            "Idempotency-Key": "stable-operation-key-0001",
        },
        json={
            "questions": [{"kind": "text", "prompt": "Choose", "required": True}],
            "actor_id": "human-admin",
            "source_terminal_id": "another-terminal",
        },
    )

    assert response.status_code == 422
    service.create_group.assert_not_called()


def test_agent_group_rejects_unanswerable_choice_or_text(monkeypatch):
    service = _service_mock()
    client = _client(monkeypatch, service)

    response = client.post(
        "/agent/interactions/groups",
        headers={
            "X-CAO-Interaction-Capability": "per-terminal-secret",
            "Idempotency-Key": "stable-operation-key-0001",
        },
        json={
            "questions": [
                {
                    "kind": "choice_or_text",
                    "prompt": "Choose",
                    "required": True,
                    "allow_free_text": False,
                    "options": [],
                }
            ]
        },
    )

    assert response.status_code == 422
    service.create_group.assert_not_called()


def test_agent_group_maps_header_operation_key_to_service_only(monkeypatch):
    service = _service_mock()
    service.create_group.return_value = CreatedGroup(
        group_id=GROUP_ID,
        lifecycle="open",
        wait_required=True,
        questions=(
            {
                "question_id": QUESTION_ID,
                "version": 1,
                "required": True,
                "lifecycle": "open",
                "expires_at": "2026-10-10T00:30:00Z",
            },
        ),
    )
    client = _client(monkeypatch, service)

    response = client.post(
        "/agent/interactions/groups",
        headers={
            "X-CAO-Interaction-Capability": "per-terminal-secret",
            "Idempotency-Key": "stable-operation-key-0001",
        },
        json={"questions": [{"kind": "text", "prompt": "Choose", "required": True}]},
    )

    assert response.status_code == 201
    assert response.json() == {
        "group_id": GROUP_ID,
        "lifecycle": "open",
        "questions": [
            {
                "question_id": QUESTION_ID,
                "version": 1,
                "required": True,
                "lifecycle": "open",
                "expires_at": "2026-10-10T00:30:00Z",
            }
        ],
        "wait_required": True,
    }
    service.create_group.assert_called_once()
    assert service.create_group.call_args.kwargs == {"operation_key": "stable-operation-key-0001"}


def test_agent_group_retry_returns_ok_without_exposing_internal_replay_flag(monkeypatch):
    service = _service_mock()
    service.create_group.return_value = CreatedGroup(
        group_id=GROUP_ID,
        lifecycle="open",
        wait_required=True,
        questions=(
            {
                "question_id": QUESTION_ID,
                "version": 1,
                "required": True,
                "lifecycle": "open",
                "expires_at": "2026-10-10T00:30:00Z",
            },
        ),
        replayed=True,
    )
    client = _client(monkeypatch, service)

    response = client.post(
        "/agent/interactions/groups",
        headers={
            "X-CAO-Interaction-Capability": "per-terminal-secret",
            "Idempotency-Key": "stable-operation-key-0001",
        },
        json={"questions": [{"kind": "text", "prompt": "Choose", "required": True}]},
    )

    assert response.status_code == 200
    assert "replayed" not in response.json()


def test_answer_rejects_mismatched_if_match_before_service_call(monkeypatch):
    service = _service_mock()
    client = _client(monkeypatch, service)

    response = client.post(
        f"/interactions/{QUESTION_ID}/answers",
        headers={
            "If-Match": '"1"',
            "Idempotency-Key": "answer-operation-key-0001",
        },
        json={
            "expected_version": 2,
            "response": {"kind": "text", "text": "approved"},
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "version_header_mismatch"
    service.accept_answer.assert_not_called()


def test_answer_receipt_preserves_replay_flag_and_closed_wire_shape(monkeypatch):
    service = _service_mock()
    service.accept_answer.return_value = AnswerReceipt(
        question_id=QUESTION_ID,
        version=3,
        answer_id="answer-1",
        lifecycle="answered",
        delivery_state="accepted",
        accepted_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        replayed=True,
    )
    client = _client(monkeypatch, service)

    response = client.post(
        f"/interactions/{QUESTION_ID}/answers",
        headers={
            "If-Match": '"3"',
            "Idempotency-Key": "answer-operation-key-0001",
        },
        json={
            "expected_version": 3,
            "response": {"kind": "text", "text": "approved"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "question_id": QUESTION_ID,
        "version": 3,
        "answer_id": "answer-1",
        "lifecycle": "answered",
        "delivery_state": "accepted",
        "accepted_at": "2026-10-10T00:00:00Z",
        "replayed": True,
    }
    service.accept_answer.assert_called_once()


def test_unanswered_metadata_keeps_nullable_delivery_state(monkeypatch):
    service = _service_mock()
    service.list_metadata.return_value = InteractionPage(
        items=(
            {
                "question_id": QUESTION_ID,
                "version": 1,
                "group_id": GROUP_ID,
                "lifecycle": "open",
                "delivery_state": None,
                "source": {
                    "terminal_id": "term-1",
                    "incarnation_id": "inc-1",
                    "provider": "claude_code",
                    "provider_version": None,
                    "execution_mode": "local",
                    "private_capability": "must-not-escape",
                },
                "task": None,
                "kind": "text",
                "required": True,
                "recipient": {"kind": "human", "label": "owner", "reason": "source_owner"},
                "group_progress": None,
                "origin": "explicit",
                "can_answer": True,
                "manual_reason": None,
                "created_at": "2026-10-10T00:00:00Z",
                "expires_at": None,
                "prompt": "protected content",
            },
        ),
        next_cursor=None,
    )
    client = _client(monkeypatch, service)

    response = client.get("/interactions")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["delivery_state"] is None
    assert item["source"]["incarnation_id"] == "inc-1"
    assert "private_capability" not in item["source"]
    assert "prompt" not in item


def test_wait_wire_response_contains_only_fields_for_selected_answer_kind():
    result = WaitResult(
        group_id=GROUP_ID,
        state="ready",
        answers=(
            ReturnedAnswer(
                question_id=QUESTION_ID,
                version=2,
                receipt="r" * 48,
                response=InteractionAnswerPayload(kind="options", option_ids=("approve",)),
            ),
        ),
        next_event_sequence=8,
        next_poll_after_ms=0,
    )

    wire = transport._wait_result_wire(result)

    assert wire["answers"][0]["response"] == {
        "kind": "options",
        "option_ids": ["approve"],
    }


def test_wait_http_route_awaits_the_async_interaction_service(monkeypatch):
    service = _service_mock()
    expected = WaitResult(
        group_id=GROUP_ID,
        state="ready",
        answers=(
            ReturnedAnswer(
                question_id=QUESTION_ID,
                version=2,
                receipt="r" * 48,
                response=InteractionAnswerPayload(kind="text", text="eu-west"),
            ),
        ),
        next_event_sequence=8,
        next_poll_after_ms=0,
    )
    calls = []

    async def wait_for_answers(*args, **kwargs):
        calls.append((args, kwargs))
        return expected

    service.wait_for_answers = wait_for_answers
    client = _client(monkeypatch, service)

    response = client.post(
        f"/agent/interactions/groups/{GROUP_ID}/wait",
        headers={"X-CAO-Interaction-Capability": "per-terminal-secret"},
        json={"wait_seconds": 0},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "ready"
    assert response.json()["answers"][0]["response"] == {
        "kind": "text",
        "text": "eu-west",
    }
    assert calls == [
        (
            ("per-terminal-secret", GROUP_ID),
            {"timeout_seconds": 0, "after_event_sequence": None},
        )
    ]


def test_delegation_detail_uses_the_closed_public_projection():
    expires_at = "2099-10-10T00:30:00Z"
    active = transport._delegation_wire(
        {
            "delegation_id": "internal-id",
            "supervisor_terminal_id": "supervisor-1",
            "supervisor_incarnation_id": "private-incarnation",
            "assignment_id": "assignment-1",
            "expires_at": expires_at,
            "revoked_at": None,
        }
    )

    assert active == {
        "terminal_id": "supervisor-1",
        "assignment_id": "assignment-1",
        "expires_at": expires_at,
        "state": "active",
    }

    revoked = transport._delegation_wire(
        {
            "supervisor_terminal_id": "supervisor-1",
            "assignment_id": "assignment-1",
            "expires_at": expires_at,
            "revoked_at": "2099-10-09T23:00:00Z",
        }
    )
    assert revoked is not None
    assert revoked["state"] == "revoked"
    assert "supervisor_terminal_id" not in revoked
    assert "supervisor_incarnation_id" not in revoked
