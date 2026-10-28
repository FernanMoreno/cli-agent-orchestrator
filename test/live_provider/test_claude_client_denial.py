"""Synthetic denial proof controls; no native CLI, credentials or database."""

import copy
import json
from test.live_provider import claude_client_denial as denial

import pytest

pytestmark = pytest.mark.offline_database
TOOL = "mcp__cao-mcp-server__cao_ask_user"
ALLOWED = "mcp__cao-mcp-server__cao_wait_for_user_answers"
CALL = "toolu_caos_denied"


def request():
    return {
        "stream": True,
        "tools": [{"name": ALLOWED}],
        "messages": [{"role": "user", "content": "caos-denial-nonce"}],
    }


def events():
    return [
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": CALL, "name": TOOL, "input": {}}]},
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": CALL,
                        "is_error": True,
                        "content": "denied",
                    }
                ]
            },
        },
        {"type": "result", "subtype": "success", "is_error": False},
    ]


def proof(native=None, offer=None, witness=None, **overrides):
    primary = request() if offer is None else offer
    context = {
        "server_catalog": {TOOL, ALLOWED},
        "client_policy": {"excluded_flags": [TOOL], "server_filter": None},
        "backend_state": {"primary": primary, "followup_error": True, "errors": []},
    }
    context.update(overrides)
    return denial.prove_denial(
        primary,
        events() if native is None else native,
        (
            {"initialize": 1, "tools/list": 1, "tools/call": 0, "closed_cleanly": True}
            if witness is None
            else witness
        ),
        excluded=TOOL,
        permitted={ALLOWED},
        call_id=CALL,
        nonce="caos-denial-nonce",
        **context,
    )


def test_correlated_native_error_and_live_zero_dispatch_prove_only_client_scope():
    assert proof() == {
        "offered_tool_excluded": True,
        "permitted_tool_offered": True,
        "native_error_correlated": True,
        "mcp_connected_witness": True,
        "mcp_tool_calls": 0,
        "provider_backend": "synthetic_local",
        "real_provider_inference": False,
    }


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v.pop(0),
        lambda v: v.pop(1),
        lambda v: v.pop(2),
        lambda v: v.append(copy.deepcopy(v[0])),
        lambda v: v.append(copy.deepcopy(v[1])),
        lambda v: v.reverse(),
        lambda v: v[1]["message"]["content"][0].update(tool_use_id="foreign"),
        lambda v: v[0]["message"]["content"][0].update(name=ALLOWED),
        lambda v: v[1]["message"]["content"][0].update(is_error=1),
        lambda v: v[1]["message"]["content"][0].update(is_error=False),
        lambda v: v[2].update(is_error=0),
        lambda v: v[2].update(subtype="error"),
        lambda v: v.append({"type": "error", "error": "PRIVATE"}),
    ],
)
def test_native_false_positive_controls(change):
    native = events()
    change(native)
    with pytest.raises(ValueError) as error:
        proof(native=native)
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v["tools"].append({"name": TOOL}),
        lambda v: v.update(tools=[]),
        lambda v: v["tools"].append({"name": ALLOWED}),
        lambda v: v.update(stream=1),
        lambda v: v.update(messages=[{"role": "user", "content": "unrelated"}]),
    ],
)
def test_offer_must_be_exact_primary_not_auxiliary(change):
    offer = request()
    change(offer)
    with pytest.raises(ValueError):
        proof(offer=offer)


@pytest.mark.parametrize(
    "witness",
    [
        {},
        {"initialize": 0, "tools/list": 1, "tools/call": 0},
        {"initialize": 1, "tools/list": 0, "tools/call": 0},
        {"initialize": 1, "tools/list": 1, "tools/call": 1},
        {"initialize": True, "tools/list": 1, "tools/call": 0},
        {"initialize": 1, "tools/list": 1, "tools/call": False},
    ],
)
def test_zero_without_typed_live_mcp_witness_is_not_denial(witness):
    with pytest.raises(ValueError):
        proof(witness=witness)


def test_anthropic_stream_injects_one_fixed_call_and_finishes_cleanly():
    wire = denial.tool_stream(TOOL, CALL, {"question": "control"}, model="synthetic")
    frames = [json.loads(p.split("\ndata: ", 1)[1]) for p in wire.decode().strip().split("\n\n")]
    assert [f["type"] for f in frames] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert frames[1]["content_block"] == {"type": "tool_use", "id": CALL, "name": TOOL, "input": {}}
    assert json.loads(frames[2]["delta"]["partial_json"]) == {"question": "control"}
    assert frames[4]["delta"]["stop_reason"] == "tool_use"


def test_text_stream_is_a_complete_anthropic_turn():
    wire = denial.text_stream("control complete", model="synthetic")
    assert b'"end_turn"' in wire and b'"text_delta"' in wire


@pytest.mark.parametrize(
    "context",
    [
        {"server_catalog": {ALLOWED}},
        {"client_policy": {"excluded_flags": [], "server_filter": None}},
        {"client_policy": {"excluded_flags": [TOOL], "server_filter": [ALLOWED]}},
        {"client_policy": {"excluded_flags": [TOOL]}},
        {"backend_state": {"primary": request(), "followup_error": True, "errors": []}},
    ],
)
def test_server_filter_or_unbound_backend_cannot_prove_client_denial(context):
    with pytest.raises(ValueError):
        proof(**context)


def test_intermediate_proxy_snapshot_cannot_prove_final_denial():
    with pytest.raises(ValueError):
        proof(witness={"initialize": 1, "tools/list": 1, "tools/call": 0, "closed_cleanly": False})


@pytest.mark.parametrize(
    "field,value", [("followup_error", 1), ("followup_error", False), ("errors", ["PRIVATE"])]
)
def test_backend_failure_never_qualifies(field, value):
    primary = request()
    state = {"primary": primary, "followup_error": True, "errors": []}
    state[field] = value
    with pytest.raises(ValueError, match="backend_exchange_unproven"):
        proof(offer=primary, backend_state=state)
