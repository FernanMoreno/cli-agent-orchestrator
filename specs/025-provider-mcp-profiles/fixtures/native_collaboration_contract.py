"""Fail-closed pure gates for native-v1 collaboration plus human Q&A."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import native_acceptance_contract as acceptance
import native_human_contract as human

from cli_agent_orchestrator.utils.mcp_tool_policy import TOOL_PROFILES

# Keep this expected catalog literal and sorted. A runtime product change must
# update the acceptance expectation deliberately instead of silently changing
# what this gate accepts.
TOOLS = (
    "cao_acknowledge_user_answers",
    "cao_ask_user",
    "cao_escalate_interaction",
    "cao_get_interaction",
    "cao_list_interactions",
    "cao_wait_for_user_answers",
    "list_siblings",
    "send_message",
)

if frozenset(TOOLS) != TOOL_PROFILES.get("collaboration"):
    raise RuntimeError("native_collaboration_source_profile_mismatch")

_CALL_FIELDS = frozenset({"server", "tool", "status", "error_present", "result_present"})
_MESSAGE_FIELDS = frozenset({"id", "sender_id", "receiver_id", "message", "created_at", "status"})
_TERMINAL_ID = re.compile(r"[0-9a-f]{8}\Z")

FINAL_TRUE = (
    "mixed_profile_configuration_exact",
    "receiver_profile_configuration_exact",
    "receiver_initialization_complete_exact",
    "message_pair_before_restart_exact",
    "message_pair_after_restart_exact",
    "message_identity_survives_restart",
    "message_agui_notification_exact",
    "receiver_one_turn_before_restart_exact",
    "receiver_no_new_turn_after_restart_exact",
    "receiver_ack_exact",
    "receiver_verified_receipt_exact",
    "receiver_native_identity_stable",
    "receiver_model_argv_exact",
)
_NO_BROAD_CLAIMS = (
    "acceptance_claimed",
    "global_acceptance_claimed",
    "original_model_acceptance_claimed",
)


def protocol_exact(observations: object) -> bool:
    """Require the exact eight-tool catalog and send→ask→wait+→ack call order."""
    if type(observations) is not dict:
        return False
    if (
        any(
            type(observations.get(field)) is not str or observations[field] != "observed"
            for field in (
                "observer_status",
                "mcp_catalog_observation_status",
                "mcp_tool_observation_status",
            )
        )
        or observations.get("mcp_observer_shutdown_observed") is not True
    ):
        return False

    catalog_names = observations.get("mcp_catalog_tool_names")
    if (
        type(catalog_names) is not list
        or any(type(name) is not str for name in catalog_names)
        or catalog_names != list(TOOLS)
    ):
        return False
    catalog_count = observations.get("mcp_catalog_observation_count")
    if type(catalog_count) is not int or catalog_count < 1:
        return False
    for field in ("mcp_catalog_unexpected_tool_count", "mcp_tool_unexpected_call_count"):
        if type(observations.get(field)) is not int or observations[field] != 0:
            return False

    calls = observations.get("mcp_tool_calls")
    if type(calls) is not list or len(calls) < 4:
        return False
    call_names: list[str] = []
    for call in calls:
        if type(call) is not dict or set(call) != _CALL_FIELDS:
            return False
        if (
            type(call.get("server")) is not str
            or call["server"] != "cao-mcp-server"
            or type(call.get("tool")) is not str
            or type(call.get("status")) is not str
            or call["status"] != "completed"
            or type(call.get("error_present")) is not bool
            or call["error_present"] is not False
            or type(call.get("result_present")) is not bool
            or call["result_present"] is not True
        ):
            return False
        call_names.append(call["tool"])

    return (
        call_names[0:2] == ["send_message", "cao_ask_user"]
        and call_names[-1] == "cao_acknowledge_user_answers"
        and all(name == "cao_wait_for_user_answers" for name in call_names[2:-1])
        and observations.get("observed_interaction_tools") == sorted(set(call_names))
    )


def catalogs_exact(rows: object) -> bool:
    """Require every observed native catalog response to contain exactly eight tools."""
    if type(rows) is not list or any(type(row) is not dict for row in rows):
        return False
    catalogs = [row for row in rows if row.get("event") == "tools_list"]
    if not catalogs:
        return False
    for row in catalogs:
        names = row.get("names")
        unexpected = row.get("unexpected_count")
        count = row.get("tool_count")
        if (
            type(names) is not list
            or any(type(name) is not str for name in names)
            or names != list(TOOLS)
            or type(unexpected) is not int
            or unexpected != 0
            or type(count) is not int
            or count != len(TOOLS)
        ):
            return False
    return True


def _as_mapping(value: object) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    # Psycopg/SQLAlchemy row types expose selected columns through `_mapping`.
    mapping = getattr(value, "_mapping", None)
    return mapping if isinstance(mapping, Mapping) else None


def _terminal_id(value: object) -> bool:
    return type(value) is str and _TERMINAL_ID.fullmatch(value) is not None


def _timestamp(value: object, *, allow_datetime: bool) -> str | None:
    if allow_datetime and type(value) is datetime:
        return value.isoformat()
    if type(value) is not str or not value:
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError, OverflowError):
        return None
    canonical = parsed.isoformat()
    if value != canonical:
        return None
    return canonical


def _normalized_message_row(value: object, *, allow_datetime: bool) -> dict[str, object] | None:
    row = _as_mapping(value)
    if row is None or set(row) != _MESSAGE_FIELDS:
        return None
    message_id = row.get("id")
    sender_id = row.get("sender_id")
    receiver_id = row.get("receiver_id")
    body = row.get("message")
    status = row.get("status")
    created_at = _timestamp(row.get("created_at"), allow_datetime=allow_datetime)
    if (
        type(message_id) is not int
        or message_id <= 0
        or not _terminal_id(sender_id)
        or not _terminal_id(receiver_id)
        or sender_id == receiver_id
        or type(body) is not str
        or not body
        or type(status) is not str
        or status != "delivered"
        or created_at is None
    ):
        return None
    return {
        "id": message_id,
        "sender_id": sender_id,
        "receiver_id": receiver_id,
        "message": body,
        "created_at": created_at,
        "status": status,
    }


def _single_normalized_row(rows: object, *, allow_datetime: bool) -> dict[str, object] | None:
    if type(rows) is not list or len(rows) != 1:
        return None
    return _normalized_message_row(rows[0], allow_datetime=allow_datetime)


def message_pair_exact(
    pg_rows: object,
    api_rows: object,
    sender_id: object,
    receiver_id: object,
    expected_persisted_message: object,
) -> bool:
    """Match one delivered inbox row across PostgreSQL and the production API."""
    if (
        not _terminal_id(sender_id)
        or not _terminal_id(receiver_id)
        or sender_id == receiver_id
        or type(expected_persisted_message) is not str
        or not expected_persisted_message
    ):
        return False
    pg = _single_normalized_row(pg_rows, allow_datetime=True)
    api = _single_normalized_row(api_rows, allow_datetime=False)
    if pg is None or api is None:
        return False
    if (
        pg["sender_id"] != sender_id
        or pg["receiver_id"] != receiver_id
        or pg["message"] != expected_persisted_message
    ):
        return False
    # Reuse the established six-column inbox contract after strict type,
    # timestamp, exact-row-count, and terminal-identity validation above.
    return acceptance.inbox_pair_exact(
        pg,
        api,
        sender_id,
        receiver_id,
        expected_persisted_message,
    )


def message_survives_restart(before_rows: object, after_rows: object) -> bool:
    """Require one normalized, delivered six-field PG row to remain identical."""
    before = _single_normalized_row(before_rows, allow_datetime=False)
    after = _single_normalized_row(after_rows, allow_datetime=False)
    return before is not None and after is not None and before == after


def final_exact(receipt: object) -> bool:
    """Keep all restricted human gates and require each T103 predicate literally."""
    if type(receipt) is not dict or human.final_exact(receipt) is not True:
        return False
    if type(receipt.get("mcp_profile")) is not str or receipt["mcp_profile"] != "collaboration":
        return False
    if any(
        type(receipt.get(field)) is not bool or receipt[field] is not False
        for field in _NO_BROAD_CLAIMS
    ):
        return False
    return all(type(receipt.get(field)) is bool and receipt[field] is True for field in FINAL_TRUE)
