"""Metadata-only AG-UI projection for durable Work message events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from cli_agent_orchestrator.services.agui_stream import AGUI_TEXT_MESSAGE_CONTENT


def _field(event: Any, name: str):
    if isinstance(event, Mapping):
        return event.get(name)
    return getattr(event, name, None)


def project_work_message_events(
    events: Sequence[Any], *, after_sequence: int
) -> tuple[list[tuple[int, str, dict[str, Any]]], int]:
    """Project send rows, suppress other metadata, and advance over every event."""
    if type(after_sequence) is not int or after_sequence < 0:
        raise ValueError("invalid Work message event cursor")

    frames: list[tuple[int, str, dict[str, Any]]] = []
    cursor = after_sequence
    for event in events:
        sequence = _field(event, "sequence")
        if type(sequence) is not int or sequence <= cursor:
            continue
        cursor = sequence
        if _field(event, "event_type") != "work.message.sent":
            continue

        metadata = _field(event, "metadata")
        if not isinstance(metadata, Mapping):
            continue
        message_id = metadata.get("message_id")
        sender_work_item_id = metadata.get("sender_work_item_id")
        receiver_work_item_id = metadata.get("receiver_work_item_id")
        if any(
            type(value) is not str or not value
            for value in (
                message_id,
                sender_work_item_id,
                receiver_work_item_id,
            )
        ):
            continue

        data = {
            "message_id": message_id,
            "role": "assistant",
            "delta": "",
            "metadata": {
                "sender_work_item_id": sender_work_item_id,
                "receiver_work_item_id": receiver_work_item_id,
            },
        }
        frames.append((sequence, AGUI_TEXT_MESSAGE_CONTENT, data))
    return frames, cursor
