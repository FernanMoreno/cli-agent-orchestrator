"""Strict operation-local effect witnesses; no native or PostgreSQL credit."""

import copy
from test.live_provider import codex_message_effect as effect

import pytest

pytestmark = pytest.mark.offline_database
SENDER = "aabbcc01"
RECEIVER = "aabbcc02"
BODY = "café 日本語 e\u0301"


@pytest.fixture
def witness():
    row = {
        "id": 1,
        "sender_id": SENDER,
        "receiver_id": RECEIVER,
        "message": BODY,
        "created_at": "2026-10-28T10:00:00+00:00",
        "status": "delivered",
    }
    result = {
        "structuredContent": {
            "success": True,
            "message_id": 1,
            "sender_id": SENDER,
            "receiver_id": RECEIVER,
            "created_at": row["created_at"],
        }
    }
    event = {
        "type": "TEXT_MESSAGE_CONTENT",
        "message_id": RECEIVER,
        "role": "assistant",
        "delta": "",
        "metadata": {"sender": SENDER, "receiver": RECEIVER, "orchestration_type": "send_message"},
    }
    return dict(
        result=result,
        before_rows=[],
        after_rows=[row],
        before_events=[],
        after_events=[event],
        sender=SENDER,
        receiver=RECEIVER,
        expected_body=BODY,
    )


def test_exact_effect_and_restart_identity(witness):
    row = effect.validate_effect(**witness)
    assert row == witness["after_rows"][0]
    effect.validate_restart(row, copy.deepcopy(row))
    row["message"] = "changed outside gate"
    assert witness["after_rows"][0]["message"] == BODY


@pytest.mark.parametrize(
    "field,value",
    [
        ("success", 1),
        ("success", False),
        ("message_id", True),
        ("message_id", 2),
        ("sender_id", RECEIVER),
        ("receiver_id", SENDER),
        ("created_at", "other"),
    ],
)
def test_structured_success_must_match_persisted_identity(witness, field, value):
    witness["result"]["structuredContent"][field] = value
    with pytest.raises(ValueError):
        effect.validate_effect(**witness)


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"content": [{"type": "text", "text": '{"success":true}'}]},
        {"structured_content": {"success": True}},
        {"structuredContent": []},
    ],
)
def test_no_unstructured_or_dialect_fallback(witness, result):
    witness["result"] = result
    with pytest.raises(ValueError):
        effect.validate_effect(**witness)


@pytest.mark.parametrize("part", ["before_rows", "before_events", "after_rows", "after_events"])
def test_stale_or_duplicate_observations_cannot_supply_new_effect(witness, part):
    source = witness[part.replace("before", "after")][0]
    witness[part].append(copy.deepcopy(source))
    with pytest.raises(ValueError):
        effect.validate_effect(**witness)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", True),
        ("message", "café 日本語 é"),
        ("status", "pending"),
        ("status", "failed"),
        ("sender_id", RECEIVER),
        ("receiver_id", SENDER),
    ],
)
def test_row_must_be_delivered_exact_unicode_and_pair(witness, field, value):
    witness["after_rows"][0][field] = value
    with pytest.raises(ValueError):
        effect.validate_effect(**witness)


@pytest.mark.parametrize("field,value", [("delta", BODY), ("message_id", 1), ("type", "other")])
def test_redacted_event_does_not_use_inbox_id_as_terminal_id(witness, field, value):
    witness["after_events"][0][field] = value
    with pytest.raises(ValueError):
        effect.validate_effect(**witness)


@pytest.mark.parametrize("leak", [{"text": BODY}, {"extra": {"t": BODY}}, {"session_name": BODY}])
def test_message_body_cannot_leak_through_unchecked_event_fields(witness, leak):
    witness["after_events"][0].update(leak)
    with pytest.raises(ValueError, match="effect_event_mismatch"):
        effect.validate_effect(**witness)


def test_source_defined_event_envelope_is_preserved(witness):
    witness["after_events"][0].update(
        event_id=4,
        terminal_id=RECEIVER,
        session_name="owned-session",
        timestamp="2026-10-28T10:00:00Z",
        traceparent=None,
    )
    assert effect.validate_effect(**witness)["id"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", 2),
        ("created_at", "changed"),
        ("message", "other"),
        ("sender_id", RECEIVER),
        ("status", "pending"),
    ],
)
def test_restart_is_read_only_identity_not_just_same_body(witness, field, value):
    before = witness["after_rows"][0]
    after = dict(before, **{field: value})
    with pytest.raises(ValueError):
        effect.validate_restart(before, after)
