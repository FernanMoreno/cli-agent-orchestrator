"""Privacy and lifecycle tests for the AG-UI interaction state projection."""

from __future__ import annotations

import asyncio
import json
import uuid
from test.postgres.interactions import postgres_interaction_store, register_interaction_source

import pytest

from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.interactions.models import (
    CreateGroupRequest,
    InteractionAnswerPayload,
    QuestionInput,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def interaction_projection_context(postgres_interaction_store):
    owner = auth._verified_principal(
        "https://agui.test", "projection-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    registered = register_interaction_source(postgres_interaction_store, owner=owner)
    return {
        "owner": owner,
        "service": postgres_interaction_store.service,
        "source": registered.source,
        "target": postgres_interaction_store.target,
    }


def test_projection_adapter_whitelists_safe_metadata_and_disables_all_actions():
    from cli_agent_orchestrator.services.agui.interaction_projection import (
        project_interaction_snapshot,
    )

    question_id = str(uuid.uuid4())
    secret_prompt = "private prompt must never enter the shared state"
    secret_answer = "private answer must never enter the shared state"
    delegated = {
        "question_id": question_id,
        "version": 2,
        "group_id": str(uuid.uuid4()),
        "lifecycle": "answered",
        "delivery_state": "returned_to_mcp",
        "source": {
            "terminal_id": "term-safe",
            "incarnation_id": "inc-safe",
            "provider": "codex",
            "provider_version": "1.0",
            "execution_mode": "local",
            "capability_hash": "must-be-dropped",
        },
        "task": None,
        "kind": "text",
        "required": True,
        "recipient": {
            "kind": "agent",
            "label": "Supervisor agent",
            "reason": "active_delegation",
        },
        "group_progress": {"required_total": 1, "required_answered": 1},
        "origin": "explicit",
        "can_answer": True,
        "manual_reason": None,
        "created_at": "2026-10-10T10:00:00Z",
        "expires_at": "2026-10-10T11:00:00Z",
        "prompt": secret_prompt,
        "options": [{"id": "private", "label": secret_answer}],
        "response": {"text": secret_answer},
        "delegation": {"terminal_id": "private-supervisor"},
        "answer_receipt": {
            "question_id": question_id,
            "version": 2,
            "answer_id": "safe-answer-id",
            "lifecycle": "answered",
            "delivery_state": "returned_to_mcp",
            "accepted_at": "2026-10-10T10:01:00Z",
            "replayed": False,
            "receipt": "private-source-bound-token",
        },
    }
    native_question_id = str(uuid.uuid4())
    native_manual = {
        **delegated,
        "question_id": native_question_id,
        "group_id": None,
        "lifecycle": "manual_only",
        "delivery_state": None,
        "kind": "unknown",
        "origin": "native",
        "required": False,
        "recipient": {"kind": "unassigned", "label": "Unassigned", "reason": "owner_missing"},
        "answer_receipt": None,
        "prompt": "native dialog text that is protected",
        "options": [{"id": "native", "label": secret_answer}],
    }
    projected = project_interaction_snapshot(
        {
            "items": [
                delegated,
                native_manual,
            ],
            "next_cursor": "opaque-cursor",
            "event_sequence": 42,
        }
    )

    item = projected["items"][0]
    assert projected["available"] is True
    assert len(projected["items"]) == 2
    assert projected["next_cursor"] == "opaque-cursor"
    assert projected["truncated"] is True
    assert projected["event_sequence"] == 42
    assert item["can_answer"] is False
    assert item["actions"] == []
    assert item["recipient"]["kind"] == "supervisor"
    assert item["answer_receipt"] == {
        "question_id": question_id,
        "version": 2,
        "answer_id": "safe-answer-id",
        "lifecycle": "answered",
        "delivery_state": "returned_to_mcp",
        "accepted_at": "2026-10-10T10:01:00Z",
    }
    native_item = projected["items"][1]
    assert native_item["question_id"] == native_question_id
    assert native_item["kind"] == "native_unknown"
    assert native_item["lifecycle"] == "manual_only"
    assert native_item["delivery_state"] is None
    assert native_item["can_answer"] is False
    assert native_item["actions"] == []
    encoded = json.dumps(projected)
    for protected in (
        secret_prompt,
        secret_answer,
        "capability_hash",
        "private-supervisor",
        "private-source-bound-token",
    ):
        assert protected not in encoded


def test_projection_snapshot_tracks_safe_receipt_state_without_answer_content(
    interaction_projection_context,
):
    context = interaction_projection_context
    service = context["service"]
    owner = context["owner"]
    token = context["source"].token
    secret_prompt = "the private prompt is not part of an AG-UI snapshot"
    secret_answer = "the private answer is not part of an AG-UI snapshot"
    created = service.create_group(
        token,
        CreateGroupRequest(
            questions=[QuestionInput(kind="text", prompt=secret_prompt, required=True)]
        ),
        "agui-projection-lifecycle-operation-01",
    )
    question_id = created.questions[0]["question_id"]

    def projected():
        from cli_agent_orchestrator.services.agui.interaction_projection import (
            project_interaction_snapshot,
        )

        return project_interaction_snapshot(service.projection_snapshot(limit=100))

    open_item = projected()["items"][0]
    assert open_item["question_id"] == question_id
    assert open_item["version"] == 1
    assert open_item["lifecycle"] == "open"
    assert open_item["delivery_state"] is None
    assert open_item["answer_receipt"] is None

    accepted = service.accept_answer(
        owner,
        question_id,
        1,
        InteractionAnswerPayload(kind="text", text=secret_answer),
        "agui-projection-answer-operation-01",
    )
    accepted_item = projected()["items"][0]
    assert accepted_item["question_id"] == accepted.question_id
    assert accepted_item["version"] == accepted.version
    assert accepted_item["delivery_state"] == "accepted"
    assert accepted_item["answer_receipt"]["answer_id"] == accepted.answer_id
    assert accepted_item["answer_receipt"]["delivery_state"] == "accepted"

    ready = asyncio.run(service.wait_for_answers(token, created.group_id, 0))
    assert ready.state == "ready"
    returned_item = projected()["items"][0]
    assert returned_item["question_id"] == question_id
    assert returned_item["version"] == accepted.version
    assert returned_item["delivery_state"] == "returned_to_mcp"
    assert returned_item["answer_receipt"]["delivery_state"] == "returned_to_mcp"

    service.acknowledge_answers(
        token,
        created.group_id,
        [
            {
                "question_id": question_id,
                "version": accepted.version,
                "receipt": ready.answers[0].receipt,
            }
        ],
    )
    acknowledged_item = projected()["items"][0]
    assert acknowledged_item["question_id"] == question_id
    assert acknowledged_item["version"] == accepted.version
    assert acknowledged_item["delivery_state"] == "agent_acknowledged"
    assert acknowledged_item["answer_receipt"]["delivery_state"] == "agent_acknowledged"
    encoded = json.dumps(acknowledged_item)
    assert secret_prompt not in encoded
    assert secret_answer not in encoded
