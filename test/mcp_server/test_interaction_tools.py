"""Contract and trust-boundary tests for explicit interaction MCP tools."""

import asyncio
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from cli_agent_orchestrator.mcp_server import server

GROUP_ID = "2e1fc625-a391-4bfd-a62d-4dddbcbf70a3"
QUESTION_ID = "d97001ba-d4ed-4c78-b3b9-22fa8c77631d"


def _plain(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def test_ask_user_forwards_operation_key_only_as_idempotency_header(monkeypatch):
    request = MagicMock(
        return_value={
            "group_id": GROUP_ID,
            "lifecycle": "open",
            "questions": [],
            "wait_required": False,
        }
    )
    monkeypatch.setattr(server.mcp_utils, "interaction_post_json", request)
    question = server._InteractionQuestionInput(
        kind="text",
        prompt="Choose a region",
        required=True,
    )

    result = _plain(
        asyncio.run(
            server.cao_ask_user(
                operation_key="logical-operation-0001",
                questions=[question],
            )
        )
    )

    assert result["group_id"] == GROUP_ID
    args, kwargs = request.call_args
    assert args[0] == "/agent/interactions/groups"
    assert args[1] == {
        "questions": [
            {
                "client_key": None,
                "kind": "text",
                "prompt": "Choose a region",
                "required": True,
                "allow_free_text": False,
                "options": [],
            }
        ],
        "expires_in_seconds": 1800,
        "wait_for_required": True,
    }
    assert kwargs == {"idempotency_key": "logical-operation-0001"}


def test_wait_validates_typed_ready_responses_and_returns_receipt(monkeypatch):
    request = MagicMock(
        return_value={
            "group_id": GROUP_ID,
            "state": "ready",
            "answers": [
                {
                    "question_id": QUESTION_ID,
                    "version": 1,
                    "receipt": "r" * 48,
                    "response": {"kind": "text", "text": "eu-west"},
                }
            ],
            "next_poll_after_ms": 0,
            "next_event_sequence": 4,
        }
    )
    monkeypatch.setattr(server.mcp_utils, "interaction_post_json", request)

    result = _plain(
        asyncio.run(
            server.cao_wait_for_user_answers(GROUP_ID, wait_seconds=0, after_event_sequence=3)
        )
    )

    assert result["state"] == "ready"
    assert result["answers"][0]["response"] == {"kind": "text", "text": "eu-west"}
    assert result["answers"][0]["receipt"] == "r" * 48
    args, kwargs = request.call_args
    assert args[0] == f"/agent/interactions/groups/{GROUP_ID}/wait"
    assert args[1] == {"wait_seconds": 0, "after_event_sequence": 3}
    assert kwargs["timeout"] == 5


def test_wait_rejects_pending_response_that_contains_answer_body(monkeypatch):
    monkeypatch.setattr(server.mcp_utils, "_read_interaction_capability", lambda: "test-capability")
    request = MagicMock(
        return_value={
            "group_id": GROUP_ID,
            "state": "pending",
            "answers": [
                {
                    "question_id": QUESTION_ID,
                    "version": 1,
                    "receipt": "r" * 48,
                    "response": {"kind": "text", "text": "should not be present"},
                }
            ],
            "next_poll_after_ms": 1000,
        }
    )
    monkeypatch.setattr(server.mcp_utils, "interaction_post_json", request)

    result = _plain(asyncio.run(server.cao_wait_for_user_answers(GROUP_ID, wait_seconds=1)))

    assert result == {
        "success": False,
        "error": {
            "code": "invalid_response",
            "detail": "The interaction API returned an invalid wait result.",
        },
    }


def test_question_path_tools_reject_url_escape_ids_before_http(monkeypatch):
    get_request = MagicMock()
    post_request = MagicMock()
    monkeypatch.setattr(server.mcp_utils, "interaction_get_json", get_request)
    monkeypatch.setattr(server.mcp_utils, "interaction_post_json", post_request)
    hostile = "d97001ba-d4ed-4c78-b3b9-22fa8c77631d/../../terminals?token=secret#fragment"

    result = _plain(asyncio.run(server.cao_get_interaction(hostile)))

    assert result["error"]["code"] == "invalid_identifier"
    get_request.assert_not_called()
    post_request.assert_not_called()


def test_group_path_tools_reject_url_escape_ids_before_http(monkeypatch):
    request = MagicMock()
    monkeypatch.setattr(server.mcp_utils, "interaction_post_json", request)

    result = _plain(
        asyncio.run(server.cao_acknowledge_user_answers("../terminals?x=1", receipts=[]))
    )

    assert result["error"]["code"] == "invalid_identifier"
    request.assert_not_called()


@pytest.mark.offline_database
def test_ack_and_wait_receipt_models_reject_non_ascii_tokens():
    with pytest.raises(ValidationError):
        server._InteractionAckInput(
            question_id=QUESTION_ID,
            version=1,
            receipt="r" * 42 + "ñ",
        )

    with pytest.raises(ValidationError):
        server._ReturnedInteractionAnswer(
            question_id=QUESTION_ID,
            version=1,
            receipt="r" * 42 + "ñ",
            response={"kind": "text", "text": "café"},
        )

    accepted = server._InteractionAckInput(
        question_id=QUESTION_ID,
        version=1,
        receipt="r" * 43,
    )
    assert accepted.receipt == "r" * 43


@pytest.mark.offline_database
def test_wait_rejects_non_ascii_receipt_from_api_without_echoing_it(monkeypatch):
    bad_receipt = "r" * 42 + "ñ"
    request = MagicMock(
        return_value={
            "group_id": GROUP_ID,
            "state": "ready",
            "answers": [
                {
                    "question_id": QUESTION_ID,
                    "version": 1,
                    "receipt": bad_receipt,
                    "response": {"kind": "text", "text": "café"},
                }
            ],
            "next_poll_after_ms": 0,
        }
    )
    monkeypatch.setattr(server.mcp_utils, "interaction_post_json", request)

    result = _plain(asyncio.run(server.cao_wait_for_user_answers(GROUP_ID, wait_seconds=0)))

    assert result == {
        "success": False,
        "error": {
            "code": "invalid_response",
            "detail": "The interaction API returned an invalid wait result.",
        },
    }
    assert bad_receipt not in str(result)


def test_tools_do_not_accept_caller_selected_agent_identity():
    for tool in (
        server.cao_ask_user,
        server.cao_wait_for_user_answers,
        server.cao_acknowledge_user_answers,
        server.cao_list_interactions,
        server.cao_get_interaction,
        server.cao_answer_interaction,
        server.cao_escalate_interaction,
    ):
        assert "terminal_id" not in tool.__annotations__
        assert "actor_id" not in tool.__annotations__
        assert "source_id" not in tool.__annotations__


def test_question_input_rejects_unanswerable_choice_or_text():
    with pytest.raises(ValidationError):
        server._InteractionQuestionInput(
            kind="choice_or_text",
            prompt="Choose",
            required=True,
            allow_free_text=False,
            options=[],
        )


def test_wait_and_acknowledgement_tools_advertise_typed_output_schemas():
    tools = asyncio.run(server.mcp.list_tools())
    listed = {tool.name: tool.output_schema for tool in tools}

    wait_schema = listed["cao_wait_for_user_answers"]
    wait_description = str(wait_schema)
    assert "pending" in wait_description and "ready" in wait_description
    assert "question_id" in wait_description and "response" in wait_description
    assert "receipt" in wait_description and "option_ids" in wait_description

    ack_schema = listed["cao_acknowledge_user_answers"]
    ack_description = str(ack_schema)
    assert "acknowledged" in ack_description
    assert "invalid_response" in ack_description
    assert "maximum" in ack_description and "20" in ack_description

    success_schemas = {
        "cao_ask_user": ("group_id", "wait_required", "question_id"),
        "cao_list_interactions": ("items", "next_cursor", "can_answer"),
        "cao_get_interaction": ("prompt", "answer_receipt", "delegation_candidate"),
        "cao_answer_interaction": ("answer_id", "accepted_at", "replayed"),
        "cao_escalate_interaction": ("recipient", "manual_reason", "lifecycle"),
    }
    for name, expected_fields in success_schemas.items():
        schema = listed[name]
        description = str(schema)
        assert "anyOf" in description, name
        assert "invalid_response" in description, name
        assert all(field in description for field in expected_fields), name
