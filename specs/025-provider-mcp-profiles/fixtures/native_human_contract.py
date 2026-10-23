"""Pure fail-closed gates for the bounded native-v1 human Q&A journey."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from uuid import UUID

TOOLS = (
    "cao_ask_user",
    "cao_wait_for_user_answers",
    "cao_acknowledge_user_answers",
)

MAX_VERSION = 2**63 - 1
_QUESTION_FIELDS = frozenset(
    {"client_key", "kind", "prompt", "required", "options", "allow_free_text"}
)
_QUESTION_REQUIRED_FIELDS = frozenset({"client_key", "kind", "prompt", "required"})
_IDENTITY_FIELDS = frozenset({"group_id", "version", "terminal_id", "incarnation_id"})
_LIFECYCLES = frozenset(
    {"open", "answered", "expired", "cancelled", "stale", "manual_only", "reconciled_external"}
)
_CALL_FIELDS = frozenset({"server", "tool", "status", "error_present", "result_present"})

# The runner supplies these checks from the real production objects. Each must
# be the literal bool True before this diagnostic can report success.
FINAL_TRUE = (
    "diagnostic_only",
    "generation_gate_bound",
    "profile_configuration_exact",
    "runtime_capability_exact",
    "mcp_ready_exact",
    "native_permissions_exact",
    "native_model_ready",
    "questions_exact",
    "pending_ledger_before_restart_exact",
    "agui_pending_before_restart_exact",
    "pending_ledger_after_restart_exact",
    "agui_pending_after_restart_exact",
    "pending_snapshot_redacted_three",
    "old_api_worker_dead",
    "new_api_worker_identity_exact",
    "native_provider_mcp_identities_stable",
    "database_config_identity_stable",
    "restart_before_wait_exact",
    "create_response_released",
    "human_text_answer_exact",
    "human_choice_answer_exact",
    "exact_ack",
    "ack_audit_exact",
    "protocol_exact",
    "every_catalog_exact",
    "wait_history_exact",
    "agui_final_snapshot_exact",
    "unicode_response_exact",
    "last_active_receipt_exact",
    "deadline_published_once",
    "native_turn_completed_within_75_seconds",
    "native_model_argv_before_restart_exact",
    "native_model_argv_after_restart_exact",
    "native_provider_final_state_exact",
    "journey_within_180_seconds",
    "observer_graceful_seal",
    "private_http_boundary_exact",
    "shared_postgres_untouched",
    "api_cleanup",
    "api_launches_confirmed_dead_before_postgres_teardown",
    "tmux_cleanup",
    "tmux_processes_confirmed_dead_before_postgres_teardown",
    "postgres_cleanup",
    "private_state_removed",
    "operator_auth_unchanged",
    "source_unchanged",
    "binary_unchanged",
    "runner_unchanged",
    "source_head_pinned_at_end",
    "source_runtime_tree_clean_at_end",
)
FINAL_FALSE = ("acceptance_claimed", "global_acceptance_claimed")
FINAL_EXACT_VALUES = {
    "cleanup_session_delete_http_status": 200,
    "private_roots_after_session_delete": 0,
}
FINAL_EXACT_STRINGS = {"cleanup_status": "complete"}
FINAL_EMPTY_LISTS = ("cleanup_failure_codes",)
FINAL_MAX_SECONDS = {"model_seconds": 75.0, "journey_seconds": 180.0}
FINAL_MINIMUM_INTS = {"minimum_admission_mib": 6144}


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return value
    # SQLAlchemy Row exposes its columns through `_mapping` but is not itself
    # registered as a Mapping on every supported SQLAlchemy version.
    mapping = getattr(value, "_mapping", None)
    return mapping if isinstance(mapping, Mapping) else None


def _canonical_uuid(value: object) -> bool:
    if type(value) is not str or not value:
        return False
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _bounded_text(value: object, *, maximum: int = 128) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= maximum
        and all(ord(character) >= 0x20 and ord(character) != 0x7F for character in value)
    )


def _version(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_VERSION


def _json_object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_option_key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid_json_constant:{value}")


def _strict_json(raw: str) -> object:
    return json.loads(
        raw,
        object_pairs_hook=_json_object_without_duplicates,
        parse_constant=_reject_json_constant,
    )


def _json_exact(actual: object, expected: object) -> bool:
    """Compare JSON values without Python's bool/int equality coercion."""
    if type(actual) is not type(expected):
        return False
    if type(actual) is dict:
        if set(actual) != set(expected):
            return False
        return all(_json_exact(actual[key], expected[key]) for key in actual)
    if type(actual) is list:
        return len(actual) == len(expected) and all(
            _json_exact(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _question_specs(questions: object) -> dict[str, Mapping[str, object]] | None:
    if type(questions) is not list or len(questions) != 2:
        return None
    by_key: dict[str, Mapping[str, object]] = {}
    kinds: set[str] = set()
    for raw in questions:
        question = _as_mapping(raw)
        if question is None or set(question) - _QUESTION_FIELDS:
            return None
        if not _QUESTION_REQUIRED_FIELDS.issubset(question):
            return None
        client_key = question.get("client_key")
        kind = question.get("kind")
        prompt = question.get("prompt")
        options = question.get("options", [])
        allow_free_text = question.get("allow_free_text", False)
        if (
            not _bounded_text(client_key)
            or client_key in by_key
            or type(kind) is not str
            or kind not in {"text", "single_choice"}
            or type(prompt) is not str
            or not prompt
            or type(question.get("required")) is not bool
            or question["required"] is not True
            or type(allow_free_text) is not bool
            or allow_free_text is not False
            or type(options) is not list
        ):
            return None
        option_ids: set[str] = set()
        for option in options:
            if type(option) is not dict or set(option) != {"id", "label"}:
                return None
            option_id = option.get("id")
            label = option.get("label")
            if (
                not _bounded_text(option_id)
                or option_id in option_ids
                or not _bounded_text(label, maximum=1024)
            ):
                return None
            option_ids.add(option_id)
        if kind == "text" and options:
            return None
        if kind == "single_choice" and not options:
            return None
        by_key[client_key] = question
        kinds.add(kind)
    return by_key if kinds == {"text", "single_choice"} else None


def _identity_rows(rows: object) -> tuple[list[Mapping[str, object]], dict[str, dict[str, object]]]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or len(rows) != 2:
        raise ValueError("interaction_identity_requires_two_rows")
    row_maps: list[Mapping[str, object]] = []
    identities: dict[str, dict[str, object]] = {}
    shared_group: str | None = None
    shared_terminal: str | None = None
    shared_incarnation: str | None = None
    for raw in rows:
        row = _as_mapping(raw)
        if row is None:
            raise ValueError("interaction_row_invalid")
        question_id = row.get("question_id")
        group_id = row.get("group_id")
        version = row.get("version")
        terminal_id = row.get("terminal_id")
        incarnation_id = row.get("incarnation_id")
        if (
            not _canonical_uuid(question_id)
            or question_id in identities
            or not _canonical_uuid(group_id)
            or not _version(version)
            or not _bounded_text(terminal_id)
            or not _bounded_text(incarnation_id)
        ):
            raise ValueError("interaction_identity_invalid")
        if shared_group is None:
            shared_group = group_id
            shared_terminal = terminal_id
            shared_incarnation = incarnation_id
        elif (
            group_id != shared_group
            or terminal_id != shared_terminal
            or incarnation_id != shared_incarnation
        ):
            raise ValueError("interaction_identity_mismatch")
        identities[question_id] = {
            "group_id": group_id,
            "version": version,
            "terminal_id": terminal_id,
            "incarnation_id": incarnation_id,
        }
        row_maps.append(row)
    return row_maps, identities


def identity(rows: object) -> dict[str, dict[str, object]]:
    """Return the exact two-question group/source identity or raise ValueError."""
    return _identity_rows(rows)[1]


def _expected_identity(value: object) -> bool:
    if type(value) is not dict or len(value) != 2:
        return False
    group_id: str | None = None
    terminal_id: str | None = None
    incarnation_id: str | None = None
    for question_id, raw in value.items():
        if (
            not _canonical_uuid(question_id)
            or type(raw) is not dict
            or set(raw) != _IDENTITY_FIELDS
        ):
            return False
        candidate_group = raw.get("group_id")
        candidate_terminal = raw.get("terminal_id")
        candidate_incarnation = raw.get("incarnation_id")
        if (
            not _canonical_uuid(candidate_group)
            or not _version(raw.get("version"))
            or not _bounded_text(candidate_terminal)
            or not _bounded_text(candidate_incarnation)
        ):
            return False
        if group_id is None:
            group_id = candidate_group
            terminal_id = candidate_terminal
            incarnation_id = candidate_incarnation
        elif (
            candidate_group != group_id
            or candidate_terminal != terminal_id
            or candidate_incarnation != incarnation_id
        ):
            return False
    return True


def _identity_exact(actual: dict[str, dict[str, object]], expected: object) -> bool:
    if not _expected_identity(expected) or set(actual) != set(expected):
        return False
    for question_id, actual_identity in actual.items():
        expected_identity = expected[question_id]
        if set(actual_identity) != _IDENTITY_FIELDS or set(expected_identity) != _IDENTITY_FIELDS:
            return False
        if any(
            type(actual_identity[field]) is not type(expected_identity[field])
            or actual_identity[field] != expected_identity[field]
            for field in _IDENTITY_FIELDS
        ):
            return False
    return True


def questions_exact(
    rows: object,
    questions: object,
    terminal_id: object,
    incarnation_id: object,
) -> bool:
    """Match exactly two typed question rows to the case and current source."""
    specs = _question_specs(questions)
    if specs is None or not _bounded_text(terminal_id) or not _bounded_text(incarnation_id):
        return False
    try:
        row_maps, _identities = _identity_rows(rows)
    except (TypeError, ValueError):
        return False
    actual_by_key: dict[str, Mapping[str, object]] = {}
    for row in row_maps:
        client_key = row.get("client_key")
        if not _bounded_text(client_key) or client_key in actual_by_key:
            return False
        actual_by_key[client_key] = row
        if row.get("terminal_id") != terminal_id or row.get("incarnation_id") != incarnation_id:
            return False
    if set(actual_by_key) != set(specs):
        return False
    for client_key, question in specs.items():
        row = actual_by_key[client_key]
        if (
            type(row.get("kind")) is not str
            or row["kind"] != question["kind"]
            or type(row.get("required")) is not bool
            or row["required"] is not question["required"]
            or type(row.get("prompt_content")) is not str
            or row["prompt_content"] != question["prompt"]
            or type(row.get("options_json")) is not str
        ):
            return False
        try:
            actual_options = _strict_json(row["options_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        # Production InteractionOption persists its explicit available=True
        # default; compare that canonical representation without dropping it.
        expected_options = [{**option, "available": True} for option in question.get("options", [])]
        if not _json_exact(actual_options, expected_options):
            return False
    return True


def ledger_exact(
    rows: object,
    expected: object,
    lifecycle: object,
    delivery_state: object,
) -> bool:
    """Require exact identities and requested lifecycle/delivery state."""
    if (
        not _expected_identity(expected)
        or type(lifecycle) is not str
        or lifecycle not in _LIFECYCLES
        or (delivery_state is not None and type(delivery_state) is not str)
    ):
        return False
    try:
        row_maps, actual = _identity_rows(rows)
    except (TypeError, ValueError):
        return False
    if not _identity_exact(actual, expected):
        return False
    for row in row_maps:
        state_keys = {key for key in ("state", "delivery_state") if key in row}
        if len(state_keys) != 1 or type(row.get("lifecycle")) is not str:
            return False
        if row["lifecycle"] != lifecycle:
            return False
        current_state = row[next(iter(state_keys))]
        if delivery_state is None:
            if current_state is not None:
                return False
        elif type(current_state) is not str or current_state != delivery_state:
            return False
    return True


def audit_exact(diagnostics: object) -> bool:
    """Reconcile actual create, ready-wait, and exact receipt-ACK evidence."""
    if type(diagnostics) is not dict:
        return False
    for field in (
        "create_group_seen",
        "created_group_matches_database",
        "created_questions_match_database",
        "ready_wait_matches_database",
    ):
        if diagnostics.get(field) is not True:
            return False
    ready_wait_count = diagnostics.get("ready_wait_count")
    wait_call_count = diagnostics.get("wait_call_count")
    ack_call_count = diagnostics.get("ack_call_count")
    if type(ready_wait_count) is not int or ready_wait_count < 1:
        return False
    if type(wait_call_count) is not int or wait_call_count < ready_wait_count:
        return False
    if type(ack_call_count) is not int or ack_call_count != 1:
        return False
    checks = diagnostics.get("ack_checks")
    if type(checks) is not list or len(checks) != ack_call_count:
        return False
    required_ack_fields = (
        "group_matches_created",
        "group_matches_waited",
        "question_versions_match_created",
        "question_versions_match_returned",
        "receipt_hashes_match_returned",
        "ack_succeeded",
    )
    expected_ack_fields = {*required_ack_fields, "ack_count"}
    for check in checks:
        if type(check) is not dict or set(check) != expected_ack_fields:
            return False
        if any(check.get(field) is not True for field in required_ack_fields):
            return False
        if type(check.get("ack_count")) is not int or check["ack_count"] != 2:
            return False
    return True


def protocol_exact(observations: object) -> bool:
    """Require the restricted catalog and one real ask/wait+/ack call sequence."""
    if type(observations) is not dict:
        return False
    if (
        observations.get("observer_status") != "observed"
        or observations.get("mcp_catalog_observation_status") != "observed"
        or observations.get("mcp_tool_observation_status") != "observed"
        or observations.get("mcp_observer_shutdown_observed") is not True
    ):
        return False
    catalog_names = observations.get("mcp_catalog_tool_names")
    if type(catalog_names) is not list or catalog_names != sorted(TOOLS):
        return False
    if any(type(name) is not str for name in catalog_names):
        return False
    if type(observations.get("mcp_catalog_observation_count")) is not int:
        return False
    if observations["mcp_catalog_observation_count"] < 1:
        return False
    for field in ("mcp_catalog_unexpected_tool_count", "mcp_tool_unexpected_call_count"):
        if type(observations.get(field)) is not int or observations[field] != 0:
            return False
    calls = observations.get("mcp_tool_calls")
    if type(calls) is not list or len(calls) < 3:
        return False
    calls_by_tool: list[str] = []
    for call in calls:
        if type(call) is not dict or set(call) != _CALL_FIELDS:
            return False
        if (
            call.get("server") != "cao-mcp-server"
            or type(call.get("tool")) is not str
            or call.get("status") != "completed"
            or call.get("error_present") is not False
            or call.get("result_present") is not True
        ):
            return False
        if any(
            type(call.get(field)) is not expected_type
            for field, expected_type in (
                ("server", str),
                ("tool", str),
                ("status", str),
                ("error_present", bool),
                ("result_present", bool),
            )
        ):
            return False
        calls_by_tool.append(call["tool"])
    if (
        calls_by_tool[0] != TOOLS[0]
        or calls_by_tool[-1] != TOOLS[2]
        or any(tool != TOOLS[1] for tool in calls_by_tool[1:-1])
    ):
        return False
    if observations.get("observed_interaction_tools") != sorted(TOOLS):
        return False
    return True


def catalogs_exact(rows: object) -> bool:
    if type(rows) is not list:
        return False
    catalogs = [row for row in rows if type(row) is dict and row.get("event") == "tools_list"]
    return bool(catalogs) and all(
        type(row.get("names")) is list
        and row["names"] == sorted(TOOLS)
        and type(row.get("unexpected_count")) is int
        and row["unexpected_count"] == 0
        and type(row.get("tool_count")) is int
        and row["tool_count"] == len(TOOLS)
        for row in catalogs
    )


def wait_history_exact(events: object, group: object, expected: object) -> bool:
    if (
        not isinstance(events, (list, tuple))
        or not _expected_identity(expected)
        or not _canonical_uuid(group)
    ):
        return False
    if group != next(iter(expected.values()))["group_id"]:
        return False
    waits = [row for row in events if type(row) is dict and row.get("event") == "wait_returned"]
    if not waits:
        return False
    ready = False
    versions = sorted((key, value["version"]) for key, value in expected.items())
    for row in waits:
        if row.get("group_id") != group:
            return False
        if row.get("state") == "pending" and not ready:
            if row.get("question_versions") != []:
                return False
        elif row.get("state") == "ready":
            ready = True
            actual = row.get("question_versions")
            if (
                type(actual) is not list
                or len(actual) != 2
                or any(
                    type(item) is not dict
                    or set(item) != {"question_id", "version"}
                    or type(item["question_id"]) is not str
                    or type(item["version"]) is not int
                    for item in actual
                )
                or sorted((item["question_id"], item["version"]) for item in actual) != versions
            ):
                return False
        else:
            return False
    return ready


def final_exact(receipt: object) -> bool:
    """Enforce every positive, negative, bounded, and cleanup receipt predicate."""
    if type(receipt) is not dict:
        return False
    if any(receipt.get(field) is not True for field in FINAL_TRUE):
        return False
    if any(
        type(receipt.get(field)) is not bool or receipt[field] is not False for field in FINAL_FALSE
    ):
        return False
    for field, expected in FINAL_EXACT_VALUES.items():
        if type(receipt.get(field)) is not int or receipt[field] != expected:
            return False
    for field, expected in FINAL_EXACT_STRINGS.items():
        if type(receipt.get(field)) is not str or receipt[field] != expected:
            return False
    for field in FINAL_EMPTY_LISTS:
        if type(receipt.get(field)) is not list or receipt[field] != []:
            return False
    for field, limit in FINAL_MAX_SECONDS.items():
        value = receipt.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= limit:
            return False
    for field, minimum in FINAL_MINIMUM_INTS.items():
        value = receipt.get(field)
        if type(value) is not int or value < minimum:
            return False
    return True
