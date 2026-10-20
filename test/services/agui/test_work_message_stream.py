"""Pure projection tests for durable Work message events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from cli_agent_orchestrator.services.agui.work_messages import (
    project_work_message_events,
)
from cli_agent_orchestrator.services.agui_stream import AGUI_TEXT_MESSAGE_CONTENT


def _work_event(sequence: int, event_type: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Build one row shaped like a retained ``work_events`` record."""
    return {
        "event_id": f"event-{sequence}",
        "job_id": "job-1",
        "work_item_id": "sender-item",
        "attempt_id": "sender-attempt",
        "sequence": sequence,
        "event_type": event_type,
        "actor_id": "sender-principal",
        "occurred_at": "2026-10-17T12:00:00+00:00",
        "metadata": metadata,
    }


def _nested_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return {str(key) for key in value} | {
            key for child in value.values() for key in _nested_keys(child)
        }
    if isinstance(value, list):
        return {key for child in value for key in _nested_keys(child)}
    return set()


def test_each_sent_event_maps_to_one_metadata_only_text_frame() -> None:
    events = [
        _work_event(
            21,
            "work.message.sent",
            {
                "message_id": "message-21",
                "sender_work_item_id": "sender-item",
                "receiver_work_item_id": "receiver-item",
                # Projection must allowlist routing data even if extra metadata
                # is present in a malformed or newer event row.
                "body": "private message text",
                "body_digest": "private-message-digest",
                "unrelated": "private event detail",
            },
        ),
        _work_event(
            22,
            "work.message.sent",
            {
                "message_id": "message-22",
                "sender_work_item_id": "sender-item-2",
                "receiver_work_item_id": "receiver-item-2",
            },
        ),
    ]

    frames, next_sequence = project_work_message_events(events, after_sequence=20)

    assert len(frames) == 2
    assert next_sequence == 22
    assert [frame[0] for frame in frames] == [21, 22]
    assert [frame[1] for frame in frames] == [
        AGUI_TEXT_MESSAGE_CONTENT,
        AGUI_TEXT_MESSAGE_CONTENT,
    ]

    first_data = frames[0][2]
    assert first_data["role"] == "assistant"
    assert first_data["delta"] == ""
    assert first_data["message_id"] == "message-21"
    assert first_data["metadata"] == {
        "sender_work_item_id": "sender-item",
        "receiver_work_item_id": "receiver-item",
    }
    assert not {"body", "body_digest"} & _nested_keys(first_data)
    assert "private message text" not in json.dumps(first_data)
    assert "private-message-digest" not in json.dumps(first_data)
    assert "private event detail" not in json.dumps(first_data)

    second_data = frames[1][2]
    assert second_data["role"] == "assistant"
    assert second_data["delta"] == ""
    assert second_data["message_id"] == "message-22"
    assert second_data["metadata"] == {
        "sender_work_item_id": "sender-item-2",
        "receiver_work_item_id": "receiver-item-2",
    }


def test_acknowledgements_and_unrelated_events_only_advance_the_cursor() -> None:
    events = [
        _work_event(
            41,
            "work.message.acknowledged",
            {
                "message_id": "message-40",
                "sender_work_item_id": "sender-item",
                "receiver_work_item_id": "receiver-item",
            },
        ),
        _work_event(
            42,
            "work.item.updated",
            {"work_item_id": "other-item", "status": "running"},
        ),
    ]

    frames, next_sequence = project_work_message_events(events, after_sequence=40)

    assert frames == []
    assert next_sequence == 42
