"""A client refusal or failed target lookup is not server authorization proof."""

import copy
import json
from test.live_provider import supervision_authority as probe

import pytest

pytestmark = pytest.mark.offline_database
CALLER, CHILD, FOREIGN = "aaaabbbb", "ccccdddd", "eeeeffff"
GEN = "a" * 32


def setup():
    turns = {
        key: {
            "terminal_id": key,
            "state": "pending",
            "generation": GEN,
            "allowed_actions": ["verify", "cancel"],
        }
        for key in (CALLER, CHILD, FOREIGN)
    }
    state = {
        "turn": turns[FOREIGN],
        "receipt": {"generation": GEN, "phase": "sent"},
        "action_requests": 1,
        "target_reads": 0,
    }
    calls = []

    def call(name, args):
        calls.append((name, args))
        if name == "inspect_terminal_turn":
            payload = {**turns[args["terminal_id"]], "success": True}
        else:
            state["target_reads"] += 1
            payload = {"success": False, "error": "Cannot authorize terminal turn target"}
        return {
            "isError": False,
            "structuredContent": payload,
            "content": [{"type": "text", "text": json.dumps(payload)}],
        }

    def exercise(**overrides):
        return probe.exercise_supervision(
            call=overrides.get("call", call),
            offered=overrides.get("offered", {"inspect_terminal_turn", "cancel_terminal_turn"}),
            caller_id=overrides.get("caller_id", CALLER),
            child_id=overrides.get("child_id", CHILD),
            foreign_id=overrides.get("foreign_id", FOREIGN),
            inspect_expected=lambda key: copy.deepcopy(turns[key]),
            observe_foreign=overrides.get("observe_foreign", lambda: copy.deepcopy(state)),
        )

    return exercise, call, state, calls


def test_three_calls_require_real_target_lookup_and_no_action_or_state_change():
    exercise, _, _, calls = setup()
    result = exercise()
    assert result == {
        "self_inspection": True,
        "child_inspection": True,
        "server_target_denial": True,
        "successful_target_lookup": True,
        "foreign_turn_unchanged": True,
        "recovery_action_not_dispatched": True,
    }
    assert calls == [
        ("inspect_terminal_turn", {"terminal_id": CALLER}),
        ("inspect_terminal_turn", {"terminal_id": CHILD}),
        ("cancel_terminal_turn", {"terminal_id": FOREIGN, "generation": GEN}),
    ]
    assert GEN not in json.dumps(result) and FOREIGN not in json.dumps(result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("offered", {"inspect_terminal_turn"}),
        ("child_id", CALLER),
        ("foreign_id", CHILD),
        ("caller_id", "bad"),
    ],
)
def test_invalid_admission_has_no_native_calls(field, value):
    exercise, _, _, calls = setup()
    with pytest.raises(probe.AuthorityProbeError):
        exercise(**{field: value})
    assert calls == []


@pytest.mark.parametrize(
    "kind",
    [
        "client_denied",
        "missing_lookup",
        "http_failed_lookup",
        "dispatched",
        "changed_turn",
        "changed_receipt",
        "bad_generation",
        "not_cancellable",
    ],
)
def test_negative_false_positives_are_rejected(kind):
    exercise, real_call, state, _ = setup()
    if kind == "bad_generation":
        state["turn"]["generation"] = "old"
    if kind == "not_cancellable":
        state["turn"]["allowed_actions"] = []

    def call(name, args):
        result = real_call(name, args)
        if name == "cancel_terminal_turn":
            if kind == "client_denied":
                return {"isError": True, "content": []}
            if kind in {"missing_lookup", "http_failed_lookup"}:
                state["target_reads"] -= 1
            if kind == "dispatched":
                state["action_requests"] += 1
            if kind == "changed_turn":
                state["turn"]["state"] = "cancelled"
            if kind == "changed_receipt":
                state["receipt"]["phase"] = "cancelled"
        return result

    with pytest.raises(probe.AuthorityProbeError):
        exercise(call=call)


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"isError": True},
        {"structuredContent": []},
        {"content": [{"type": "text", "text": "not-json"}]},
        {
            "structuredContent": {"success": True},
            "content": [{"type": "text", "text": '{"success":false}'}],
        },
    ],
)
def test_ambiguous_or_invalid_mcp_payloads_fail_closed(result):
    with pytest.raises(probe.AuthorityProbeError):
        probe.mcp_payload(result)


def test_single_json_text_fallback_is_structured():
    assert probe.mcp_payload({"content": [{"type": "text", "text": '{"success":true}'}]}) == {
        "success": True
    }


@pytest.mark.parametrize(
    "tool,numeric", [("inspect_terminal_turn", 1), ("cancel_terminal_turn", 0)]
)
def test_numeric_boolean_is_not_operation_success_or_denial(tool, numeric):
    exercise, real_call, _, _ = setup()

    def call(name, args):
        result = real_call(name, args)
        if name == tool:
            result["structuredContent"]["success"] = numeric
            result["content"][0]["text"] = json.dumps(result["structuredContent"])
        return result

    with pytest.raises(probe.AuthorityProbeError):
        exercise(call=call)


def test_conflicting_boolean_and_integer_content_rejected():
    with pytest.raises(probe.AuthorityProbeError):
        probe.mcp_payload(
            {
                "structuredContent": {"success": True},
                "content": [{"type": "text", "text": '{"success":1}'}],
            }
        )


def test_invalid_content_array_is_fixed_failure():
    with pytest.raises(probe.AuthorityProbeError):
        probe.mcp_payload({"content": None})


def test_native_schema_nullable_is_error_is_supported():
    assert probe.mcp_payload({"isError": None, "structuredContent": {"success": True}}) == {
        "success": True
    }
    with pytest.raises(probe.AuthorityProbeError):
        probe.mcp_payload({"isError": 0, "structuredContent": {"success": True}})
