"""Pure witnesses for a native generic-message diagnostic, never Work authority.

The caller must supply operation-local raw observations from verified services.
Passing these checks alone establishes neither their provenance nor a real run.
"""

from __future__ import annotations

import copy
import re
from test.live_provider.message_api_restart import validate_agui_notification

ROW_FIELDS = {"id", "sender_id", "receiver_id", "message", "created_at", "status"}
RESULT_FIELDS = {"success", "message_id", "sender_id", "receiver_id", "created_at"}
EVENT_FIELDS = {"type", "message_id", "role", "delta", "metadata"}
EVENT_ENVELOPE = {"event_id", "terminal_id", "session_name", "timestamp", "traceparent"}


def _redacted_event(event, body, sender, receiver):
    if (
        not isinstance(event, dict)
        or not EVENT_FIELDS <= set(event) <= EVENT_FIELDS | EVENT_ENVELOPE
    ):
        return False
    if any(
        event[k] is not None and not isinstance(event[k], str)
        for k in EVENT_ENVELOPE - {"event_id"}
        if k in event
    ):
        return False
    if "terminal_id" in event and event["terminal_id"] != receiver:
        return False
    if (
        "event_id" in event
        and event["event_id"] is not None
        and not (
            type(event["event_id"]) is int
            and event["event_id"] > 0
            or isinstance(event["event_id"], str)
            and event["event_id"]
        )
    ):
        return False
    if not validate_agui_notification(event, sender_id=sender, receiver_id=receiver):
        return False
    # Known envelope fields are public metadata too: never allow the exact
    # operation body through an unchecked field. All values are scalar here.
    values = [v for k, v in event.items() if k != "metadata"] + list(event["metadata"].values())
    return not any(isinstance(value, str) and body in value for value in values)


def _delivered_row(row):
    return (
        isinstance(row, dict)
        and set(row) == ROW_FIELDS
        and type(row["id"]) is int
        and row["id"] > 0
        and all(isinstance(row[k], str) and row[k] for k in ROW_FIELDS - {"id"})
        and row["status"] == "delivered"
    )


def validate_effect(
    *, result, before_rows, after_rows, before_events, after_events, sender, receiver, expected_body
):
    """Require one exact new delivered row and one pair-scoped redacted event.

    Baselines must be empty for these freshly owned identities/capture. An
    existing row or event is not credited to this operation. The event's public
    message_id is the receiver terminal, not the PostgreSQL inbox row ID.
    """
    if (
        not all(isinstance(x, str) and re.fullmatch(r"[a-f0-9]{8}", x) for x in (sender, receiver))
        or sender == receiver
        or not isinstance(expected_body, str)
        or not expected_body
    ):
        raise ValueError("invalid_effect_expectation")
    if any(type(x) is not list for x in (before_rows, after_rows, before_events, after_events)):
        raise ValueError("invalid_effect_observations")
    if before_rows or before_events or len(after_rows) != 1 or len(after_events) != 1:
        raise ValueError("effect_not_one_new_operation")
    row = after_rows[0]
    if not _delivered_row(row) or (row["sender_id"], row["receiver_id"], row["message"]) != (
        sender,
        receiver,
        expected_body,
    ):
        raise ValueError("effect_row_mismatch")
    if not isinstance(result, dict) or result.get("isError", False) is not False:
        raise ValueError("native_effect_error")
    structured = result.get("structuredContent")
    if not isinstance(structured, dict) or set(structured) != RESULT_FIELDS:
        raise ValueError("native_effect_structured_result_required")
    if structured["success"] is not True or type(structured["message_id"]) is not int:
        raise ValueError("native_effect_not_success")
    if any(
        structured[k] != row[r]
        for k, r in (
            ("message_id", "id"),
            ("sender_id", "sender_id"),
            ("receiver_id", "receiver_id"),
            ("created_at", "created_at"),
        )
    ):
        raise ValueError("native_effect_identity_mismatch")
    if not _redacted_event(after_events[0], expected_body, sender, receiver):
        raise ValueError("effect_event_mismatch")
    return copy.deepcopy(row)


def validate_restart(before, after):
    """A read-only restart witness cannot change any accepted row field."""
    if not _delivered_row(before) or not _delivered_row(after) or before != after:
        raise ValueError("effect_restart_identity_mismatch")
