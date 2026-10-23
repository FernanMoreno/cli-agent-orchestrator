"""Additional strict gates; never replace inherited native acceptance/cleanup."""

import re
from datetime import datetime

BINARY_SHA = "77b2cfe4b97df6f15c3673b22100b9f79c711f25ecb9bf513bb82526b15d24fa"
GENERATION_TRUE = (
    "native_generation_completed",
    "native_generation_completion_checked",
    "native_unicode_response_observed",
    "native_tool_authority_empty",
    "diagnostic_only",
    "request_identity_booleans_only",
    "owned_process_session_stopped",
    "ledger_valid",
    "operator_auth_unchanged",
    "source_unchanged",
    "binary_unchanged",
    "plugin_unchanged",
    "runner_unchanged",
    "source_head_pinned_at_end",
    "private_state_removed",
)


def ack_exact(value):
    return type(value) is str and value == "ACK"


def turn_exact(state, expected):
    return (
        type(expected) is int
        and expected > 0
        and type(state) is dict
        and type(state.get("http_status")) is int
        and state["http_status"] == 200
        and state.get("status") == "completed"
        and all(
            type(state.get(key)) is int and state[key] == expected
            for key in ("turn_sequence", "turn_completed")
        )
    )


def receiver_phases_exact(phases):
    if type(phases) is not list or len(phases) != 2:
        return False
    for index, phase in enumerate(phases):
        if type(phase) is not dict or any(
            phase.get(key) is not True
            for key in (
                "receiver_ack_exact",
                "receiver_ack_within_deadline",
                "receiver_turn_completed_once",
            )
        ):
            return False
        values = [
            phase.get(key)
            for key in (
                "receiver_sequence_before",
                "receiver_sequence_after",
                "receiver_completed_after",
            )
        ]
        if any(type(value) is not int or value < 0 for value in values):
            return False
        before, after, completed = values
        if after != before + 1 or completed != after:
            return False
        if index and phase.get("restart_durable_receipts_exact") is not True:
            return False
    return True


def settled_local_state(state):
    return (
        type(state) is dict
        and type(state.get("http_status")) is int
        and state["http_status"] == 200
        and type(state.get("status")) is str
        and state["status"] in {"idle", "completed"}
        and type(state.get("turn_sequence")) is int
        and 0 <= state["turn_sequence"] <= 2**63 - 1
        and type(state.get("turn_completed")) is int
        and state["turn_completed"] == state["turn_sequence"]
    )


def durable_turns_exact(before, after):
    if type(before) is not dict or type(after) is not dict:
        return False
    if set(before) != {"sender", "receiver"} or set(after) != set(before):
        return False
    for role in before:
        row = before[role]
        if (
            type(row) is not dict
            or set(row) != {"generation", "receipt_sha256", "result_sha256", "phase"}
            or row.get("phase") != "result_verified"
        ):
            return False
        for key, length in (("generation", 32), ("receipt_sha256", 64), ("result_sha256", 64)):
            if (
                type(row.get(key)) is not str
                or re.fullmatch("[0-9a-f]{" + str(length) + "}", row[key]) is None
            ):
                return False
        if type(after[role]) is not dict or after[role] != row:
            return False
    return True


def sender_phases_exact(phases):
    if type(phases) is not list or len(phases) != 2:
        return False
    for phase in phases:
        if type(phase) is not dict or any(
            phase.get(key) is not True
            for key in ("last_receipt_unicode_verified", "sender_completed")
        ):
            return False
        expected = phase.get("input_turn_sequence")
        if (
            type(expected) is not int
            or expected <= 0
            or any(
                type(phase.get(key)) is not int or phase[key] != expected
                for key in ("sender_sequence_after", "sender_completed_after")
            )
        ):
            return False
    return True


def inbox_pair_exact(pg, api, sender, receiver, message):
    fields = {"id", "sender_id", "receiver_id", "message", "created_at", "status"}
    if type(pg) is not dict or type(api) is not dict or set(pg) != fields or set(api) != fields:
        return False
    if type(pg.get("created_at")) is datetime:
        pg = {**pg, "created_at": pg["created_at"].isoformat()}
    if type(pg["id"]) is not int or pg["id"] <= 0 or type(api["id"]) is not int:
        return False
    if any(
        type(pg[key]) is not str or not pg[key] or type(api[key]) is not str
        for key in fields - {"id"}
    ):
        return False
    return (
        pg == api
        and pg["sender_id"] == sender
        and pg["receiver_id"] == receiver
        and pg["message"] == message
        and pg["status"] == "delivered"
    )


def generation_exact(report, head, bindings):
    return (
        type(report) is dict
        and type(bindings) is dict
        and set(bindings) == {"binary_sha256", "runner_sha256", "plugin_sha256"}
        and all(
            type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None
            for value in bindings.values()
        )
        and bindings.get("binary_sha256") == BINARY_SHA
        and all(report.get(key) == value for key, value in bindings.items())
        and "error_type" not in report
        and all(report.get(key) is True for key in GENERATION_TRUE)
        and all(
            report.get(key) is False
            for key in (
                "acceptance_claimed",
                "original_model_acceptance_claimed",
                "raw_stdout_persisted",
                "headers_or_bodies_recorded",
                "error_body_persisted",
                "mcp_enabled",
            )
        )
        and report.get("source_head") == head
        and report.get("model") == "opencode-go/glm-5.3-flash"
        and type(report.get("native_exit_code")) is int
        and report["native_exit_code"] == 0
        and report.get("outcome") == "native_process_exit"
        and type(report.get("model_deadline_seconds")) is int
        and report["model_deadline_seconds"] == 75
        and type(report.get("model_admission_mib")) is int
        and report["model_admission_mib"] >= 6144
        and type(report.get("events")) is dict
        and type(report["events"].get("step_finish")) is int
        and report["events"]["step_finish"] > 0
    )


def final_exact(receipt):
    return type(receipt) is dict and all(
        receipt.get(key) is True
        for key in (
            "inherited_acceptance_passed",
            "receiver_ack_acceptance_passed",
            "native_profile_bindings_exact",
            "native_profile_configuration_exact",
            "generation_gate_bound",
            "sender_unicode_acceptance_passed",
            "api_restart_identity_exact",
            "inbox_pairs_exact",
            "terminal_groups_exact",
            "durable_turn_identity_survives_restart",
        )
    )
