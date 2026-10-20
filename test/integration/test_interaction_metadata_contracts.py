"""Native PostgreSQL cursor, filter, and privacy contracts for metadata pages."""

from __future__ import annotations

import json
import time
from test.integration.test_interaction_explicit_flow import interaction_http_context  # noqa: F401
from uuid import uuid4

import pytest

pytestmark = pytest.mark.postgres


def _created_group(context, count: int):
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        QuestionInput,
    )

    prompt_markers = [f"metadata-private-prompt-{index}-{uuid4().hex}" for index in range(count)]
    created = context.service.create_group(
        context.source_token,
        CreateGroupRequest(
            questions=tuple(
                QuestionInput(kind="text", prompt=prompt, required=True)
                for prompt in prompt_markers
            )
        ),
        f"metadata-page-create-{uuid4().hex}",
    )
    return created, prompt_markers


def _answer_one(context, question_id: str) -> str:
    from cli_agent_orchestrator.services.interactions.models import InteractionAnswerPayload

    answer_marker = f"metadata-private-answer-{uuid4().hex}"
    context.service.accept_answer(
        context.owner,
        question_id,
        1,
        InteractionAnswerPayload(kind="text", text=answer_marker),
        f"metadata-answer-{uuid4().hex}",
    )
    return answer_marker


def _filtered_metadata_params(context, limit: int):
    return [
        ("status", "open"),
        ("status", "answered"),
        ("terminal_id", context.terminal_id),
        ("limit", str(limit)),
    ]


def test_filtered_metadata_cursor_pages_have_no_tie_gaps_duplicates_or_content(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.services.interactions import service as interaction_service_module

    context = interaction_http_context
    # Equal timestamps force cursor traversal to use the question ID tiebreaker
    # and preserve the full timestamp precision across encode/decode.
    fixed_timestamp = time.time()
    monkeypatch.setattr(interaction_service_module, "_now", lambda: fixed_timestamp)
    created, prompt_markers = _created_group(context, count=8)
    question_ids = [question["question_id"] for question in created.questions]
    answer_marker = _answer_one(context, question_ids[0])

    with context.repository.read_connection() as connection:
        timestamps = connection.execute(
            "SELECT count(DISTINCT created_at) FROM interaction_questions WHERE group_id=%s",
            (created.group_id,),
        ).fetchone()[0]
    assert timestamps == 1

    expected_response = context.client.get(
        "/interactions", params=_filtered_metadata_params(context, limit=100)
    )
    assert expected_response.status_code == 200, expected_response.text
    expected_items = expected_response.json()["items"]
    expected_ids = [item["question_id"] for item in expected_items]
    assert set(expected_ids) == set(question_ids)
    assert len(expected_ids) == len(question_ids)

    actual_items = []
    cursors = set()
    cursor = None
    while True:
        params = _filtered_metadata_params(context, limit=2)
        if cursor is not None:
            params.append(("cursor", cursor))
        page_response = context.client.get("/interactions", params=params)
        assert page_response.status_code == 200, page_response.text
        page = page_response.json()
        actual_items.extend(page["items"])
        next_cursor = page["next_cursor"]
        if next_cursor is None:
            break
        assert next_cursor not in cursors
        cursors.add(next_cursor)
        cursor = next_cursor

    actual_ids = [item["question_id"] for item in actual_items]
    assert actual_ids == expected_ids
    assert len(actual_ids) == len(set(actual_ids))
    serialized = json.dumps(actual_items, sort_keys=True)
    assert all(marker not in serialized for marker in prompt_markers)
    assert answer_marker not in serialized
    assert all("prompt" not in item and "answer" not in item for item in actual_items)


@pytest.mark.parametrize("limit", [0, 101])
def test_invalid_metadata_limit_is_rejected_without_returning_question_content(
    interaction_http_context, limit
):
    from cli_agent_orchestrator.services.interactions.models import InteractionAnswerPayload

    context = interaction_http_context
    created, prompt_markers = _created_group(context, count=1)
    answer_marker = f"metadata-private-answer-{uuid4().hex}"
    context.service.accept_answer(
        context.owner,
        created.questions[0]["question_id"],
        1,
        InteractionAnswerPayload(kind="text", text=answer_marker),
        f"metadata-answer-{uuid4().hex}",
    )

    response = context.client.get("/interactions", params={"limit": limit})

    assert response.status_code == 422
    assert prompt_markers[0] not in response.text
    assert answer_marker not in response.text


def test_invalid_metadata_cursor_is_rejected_without_returning_question_content(
    interaction_http_context,
):
    from cli_agent_orchestrator.services.interactions.models import InteractionAnswerPayload

    context = interaction_http_context
    created, prompt_markers = _created_group(context, count=1)
    answer_marker = f"metadata-private-answer-{uuid4().hex}"
    context.service.accept_answer(
        context.owner,
        created.questions[0]["question_id"],
        1,
        InteractionAnswerPayload(kind="text", text=answer_marker),
        f"metadata-answer-{uuid4().hex}",
    )

    response = context.client.get(
        "/interactions", params={"limit": 1, "cursor": "not-a-valid-interaction-cursor"}
    )

    assert response.status_code == 422
    assert prompt_markers[0] not in response.text
    assert answer_marker not in response.text
