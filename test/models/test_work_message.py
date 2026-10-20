"""Closed request/result contracts for managed Work agent messages."""

import json

import pytest
from pydantic import ValidationError

from cli_agent_orchestrator.models.work_message import (
    WorkMessageAckRequest,
    WorkMessageAckResult,
    WorkMessageReceiveRequest,
    WorkMessageReceiveResult,
    WorkMessageResultItem,
    WorkMessageSendRequest,
    WorkMessageSendResult,
)


def _send_request(**overrides):
    return {
        "operation": "send",
        "receiver_work_item_id": "receiver-1",
        "idempotency_key": "turn:1.message-1_key",
        "body": "coordinate ☃",
        **overrides,
    }


def _result_item(**overrides):
    return {
        "message_id": "message-1",
        "sender_work_item_id": "sender-1",
        "body": "coordinate ☃",
        "created_at": 1792195200.0,
        **overrides,
    }


@pytest.mark.parametrize(
    ("model", "payload", "expected"),
    [
        (
            WorkMessageSendRequest,
            _send_request(),
            {
                "operation": "send",
                "receiver_work_item_id": "receiver-1",
                "idempotency_key": "turn:1.message-1_key",
                "body": "coordinate ☃",
            },
        ),
        (
            WorkMessageReceiveRequest,
            {"operation": "receive"},
            {"operation": "receive"},
        ),
        (
            WorkMessageReceiveRequest,
            {"operation": "receive", "limit": 2},
            {"operation": "receive", "limit": 2},
        ),
        (
            WorkMessageAckRequest,
            {"operation": "ack", "message_id": "message-1"},
            {"operation": "ack", "message_id": "message-1"},
        ),
    ],
)
def test_valid_message_requests_have_only_the_operation_fields(model, payload, expected):
    request = model.model_validate(payload)

    assert request.model_dump(exclude_unset=True) == expected
    if model is WorkMessageReceiveRequest and "limit" not in payload:
        assert request.limit == 1


@pytest.mark.parametrize(
    ("model", "payload", "extra_field"),
    [
        (WorkMessageSendRequest, _send_request(), "limit"),
        (WorkMessageReceiveRequest, {"operation": "receive"}, "body"),
        (WorkMessageAckRequest, {"operation": "ack", "message_id": "message-1"}, "limit"),
    ],
)
def test_message_requests_reject_fields_outside_their_closed_shape(model, payload, extra_field):
    with pytest.raises(ValidationError):
        model.model_validate({**payload, extra_field: "unexpected"})


@pytest.mark.parametrize(
    "identity_field",
    [
        "sender_work_item_id",
        "job_id",
        "attempt_id",
        "generation",
        "principal_id",
        "credential",
        "receiver_attempt_id",
        "receiver_generation",
    ],
)
@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (WorkMessageSendRequest, _send_request()),
        (WorkMessageReceiveRequest, {"operation": "receive"}),
        (WorkMessageAckRequest, {"operation": "ack", "message_id": "message-1"}),
    ],
)
def test_message_requests_never_accept_caller_supplied_authority(model, payload, identity_field):
    value = 1 if identity_field in {"generation", "receiver_generation"} else "forged"

    with pytest.raises(ValidationError):
        model.model_validate({**payload, identity_field: value})


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (WorkMessageReceiveRequest, {"operation": "receive"}),
        (WorkMessageAckRequest, {"operation": "ack", "message_id": "message-1"}),
    ],
)
def test_receive_and_ack_never_accept_a_caller_supplied_receiver(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "receiver_work_item_id": "forged-receiver"})


@pytest.mark.parametrize(
    ("model", "payload", "wrong_operation"),
    [
        (WorkMessageSendRequest, _send_request(), "receive"),
        (WorkMessageReceiveRequest, {"operation": "receive"}, "ack"),
        (WorkMessageAckRequest, {"operation": "ack", "message_id": "message-1"}, "send"),
    ],
)
def test_message_request_operation_is_exact_and_lowercase(model, payload, wrong_operation):
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "operation": wrong_operation})


@pytest.mark.parametrize(
    "key",
    [
        "",
        "a" * 129,
        "contains space",
        "slash/key",
        "ümlaut",
        "line\nbreak",
    ],
)
def test_send_rejects_non_ascii_or_out_of_grammar_idempotency_keys(key):
    with pytest.raises(ValidationError):
        WorkMessageSendRequest.model_validate(_send_request(idempotency_key=key))


def test_send_accepts_the_longest_ascii_idempotency_key():
    key = "Az09._:-" * 16

    request = WorkMessageSendRequest.model_validate(_send_request(idempotency_key=key))

    assert request.idempotency_key == key
    assert len(key) == 128


@pytest.mark.parametrize("body", ["", "has\x00nul", "é" * 2049, "\ud800", 1, None])
def test_send_rejects_empty_nul_oversized_or_non_utf8_bodies(body):
    with pytest.raises(ValidationError):
        WorkMessageSendRequest.model_validate(_send_request(body=body))


def test_message_body_limit_counts_strict_utf8_bytes():
    body = "é" * 2048

    request = WorkMessageSendRequest.model_validate(_send_request(body=body))

    assert len(request.body.encode("utf-8", errors="strict")) == 4096


@pytest.mark.parametrize("receiver_work_item_id", ["", "r" * 129, 1, None])
def test_send_rejects_empty_or_oversized_receiver_work_item_ids(receiver_work_item_id):
    with pytest.raises(ValidationError):
        WorkMessageSendRequest.model_validate(
            _send_request(receiver_work_item_id=receiver_work_item_id)
        )


@pytest.mark.parametrize("limit", [0, 3, -1, True, 1.0, "1"])
def test_receive_limit_is_an_integer_from_one_through_two(limit):
    with pytest.raises(ValidationError):
        WorkMessageReceiveRequest.model_validate({"operation": "receive", "limit": limit})


@pytest.mark.parametrize(
    ("model", "payload", "valid_statuses", "expected_fields"),
    [
        (
            WorkMessageSendResult,
            {"message_id": "message-1", "status": "sent"},
            {"sent", "replayed"},
            {"message_id", "status"},
        ),
        (
            WorkMessageAckResult,
            {"message_id": "message-1", "status": "acknowledged"},
            {"acknowledged", "already_acknowledged"},
            {"message_id", "status"},
        ),
    ],
)
def test_send_and_ack_results_have_exact_closed_shapes(
    model, payload, valid_statuses, expected_fields
):
    result = model.model_validate(payload)

    assert set(result.model_dump(mode="json")) == expected_fields
    for status in valid_statuses:
        model.model_validate({**payload, "status": status})
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "status": "pending"})
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "sender_work_item_id": "sender-1"})


def test_receive_result_has_at_most_two_exact_message_items():
    payload = {"messages": [_result_item(), _result_item(message_id="message-2")]}

    result = WorkMessageReceiveResult.model_validate(payload)

    assert set(result.model_dump(mode="json")) == {"messages"}
    assert len(result.messages) == 2
    assert WorkMessageReceiveResult.model_validate({"messages": []}).messages == []
    assert set(result.messages[0].model_dump(mode="json")) == {
        "message_id",
        "sender_work_item_id",
        "body",
        "created_at",
    }
    with pytest.raises(ValidationError):
        WorkMessageReceiveResult.model_validate(
            {"messages": [*payload["messages"], _result_item(message_id="message-3")]}
        )
    with pytest.raises(ValidationError):
        WorkMessageReceiveResult.model_validate({**payload, "next_cursor": "cursor"})
    with pytest.raises(ValidationError):
        WorkMessageResultItem.model_validate({**_result_item(), "acknowledged": False})


def test_two_maximum_utf8_bodies_fit_the_response_frame_with_worst_case_escaping():
    worst_case_body = "\x01" * 4096
    result = WorkMessageReceiveResult.model_validate(
        {
            "messages": [
                _result_item(
                    message_id="m" * 128,
                    sender_work_item_id="s" * 128,
                    body=worst_case_body,
                ),
                _result_item(
                    message_id="n" * 128,
                    sender_work_item_id="t" * 128,
                    body=worst_case_body,
                ),
            ]
        }
    )
    response_frame = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "r" * 128,
                "result": {"content": [{"type": "text", "text": result.model_dump_json()}]},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )

    assert len(worst_case_body.encode("utf-8", errors="strict")) == 4096
    assert len(response_frame) <= 65536


@pytest.mark.parametrize("body", ["", "has\x00nul", "é" * 2049, "\ud800", 1, None])
def test_received_message_body_uses_the_same_strict_utf8_bound(body):
    with pytest.raises(ValidationError):
        WorkMessageResultItem.model_validate(_result_item(body=body))


@pytest.mark.parametrize("message_id", ["", "m" * 129, 1, None])
@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (WorkMessageAckRequest, {"operation": "ack", "message_id": "message-1"}),
        (WorkMessageSendResult, {"message_id": "message-1", "status": "sent"}),
        (WorkMessageAckResult, {"message_id": "message-1", "status": "acknowledged"}),
        (WorkMessageResultItem, _result_item()),
    ],
)
def test_message_ids_are_nonempty_strings_of_at_most_128_characters(model, payload, message_id):
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "message_id": message_id})
