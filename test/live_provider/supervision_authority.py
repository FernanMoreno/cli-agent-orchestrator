"""Strict, content-free evidence for one native supervision authorization case."""

from __future__ import annotations

import copy
import json
import re


class AuthorityProbeError(RuntimeError):
    """Fixed categories only; never retain native output or credential material."""


def require(condition, code):
    if not condition:
        raise AuthorityProbeError(code)


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise AuthorityProbeError("invalid_json_value") from None


def mcp_payload(result):
    require(isinstance(result, dict), "invalid_mcp_result")
    require(
        result.get("isError") is False or result.get("isError") is None,
        "native_or_transport_refusal",
    )
    structured = result.get("structuredContent")
    content = result.get("content", [])
    require(type(content) is list, "invalid_mcp_content")
    texts = [b.get("text") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    require(len(texts) <= 1, "ambiguous_mcp_text")
    decoded = None
    if texts:
        require(type(texts[0]) is str and len(texts[0]) <= 65536, "invalid_mcp_text")
        try:
            decoded = json.loads(texts[0])
        except (TypeError, ValueError):
            raise AuthorityProbeError("invalid_mcp_json") from None
        require(isinstance(decoded, dict), "invalid_mcp_object")
    if structured is not None:
        require(isinstance(structured, dict), "invalid_structured_content")
        require(
            decoded is None or _canonical(decoded) == _canonical(structured),
            "conflicting_mcp_content",
        )
        _canonical(structured)
        return structured
    require(decoded is not None, "missing_structured_result")
    _canonical(decoded)
    return decoded


def _foreign_snapshot(observe):
    try:
        value = copy.deepcopy(observe())
    except Exception:
        raise AuthorityProbeError("foreign_observation_failed") from None
    require(
        isinstance(value, dict)
        and set(value) == {"turn", "receipt", "action_requests", "target_reads"},
        "invalid_foreign_observation",
    )
    require(
        all(type(value[k]) is int and value[k] >= 0 for k in ("action_requests", "target_reads")),
        "invalid_request_observation",
    )
    turn, receipt = value["turn"], value["receipt"]
    require(isinstance(turn, dict) and isinstance(receipt, dict), "missing_persisted_turn")
    generation = turn.get("generation")
    require(
        type(generation) is str and re.fullmatch(r"[a-f0-9]{32}", generation),
        "foreign_generation_invalid",
    )
    require(receipt.get("generation") == generation, "receipt_generation_mismatch")
    require(
        turn.get("state") == "pending"
        and type(turn.get("allowed_actions")) is list
        and "cancel" in turn["allowed_actions"],
        "foreign_not_cancellable",
    )
    return value


def exercise_supervision(
    *, call, offered, caller_id, child_id, foreign_id, inspect_expected, observe_foreign
):
    ids = [caller_id, child_id, foreign_id]
    require(
        all(type(v) is str and re.fullmatch(r"[a-f0-9]{8}", v) for v in ids) and len(set(ids)) == 3,
        "invalid_probe_identities",
    )
    require(
        {"inspect_terminal_turn", "cancel_terminal_turn"}.issubset(offered),
        "native_tools_not_offered",
    )
    before = _foreign_snapshot(observe_foreign)
    require(before["turn"].get("terminal_id") == foreign_id, "foreign_identity_mismatch")
    for target in (caller_id, child_id):
        expected = inspect_expected(target)
        require(
            isinstance(expected, dict) and expected.get("terminal_id") == target,
            "invalid_expected_inspection",
        )
        result = mcp_payload(call("inspect_terminal_turn", {"terminal_id": target}))
        require(result.get("success") is True, "inspection_success_not_boolean")
        require(
            _canonical(result) == _canonical({**expected, "success": True}),
            "inspection_contract_mismatch",
        )
    result = mcp_payload(
        call(
            "cancel_terminal_turn",
            {"terminal_id": foreign_id, "generation": before["turn"]["generation"]},
        )
    )
    require(result.get("success") is False, "denial_success_not_boolean")
    require(
        result == {"success": False, "error": "Cannot authorize terminal turn target"},
        "server_target_denial_missing",
    )
    after = _foreign_snapshot(observe_foreign)
    require(
        after["target_reads"] == before["target_reads"] + 1, "successful_target_lookup_not_observed"
    )
    require(after["action_requests"] == before["action_requests"], "recovery_action_dispatched")
    require(
        after["turn"] == before["turn"] and after["receipt"] == before["receipt"],
        "foreign_state_changed",
    )
    return {
        "self_inspection": True,
        "child_inspection": True,
        "server_target_denial": True,
        "successful_target_lookup": True,
        "foreign_turn_unchanged": True,
        "recovery_action_not_dispatched": True,
    }
