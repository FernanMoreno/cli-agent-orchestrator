"""Synthetic Anthropic wire data and a strict, content-free client-denial proof.

This module never launches a CLI, reads a login or changes production policy.
The caller must own and retire the native process and audit every MCP request.
"""

import json
import re


def _require(condition, code):
    if not condition:
        raise ValueError(code)


def _name(value):
    return isinstance(value, str) and re.fullmatch(r"mcp__[A-Za-z0-9_-]+__[A-Za-z0-9_-]+", value)


def _blocks(event):
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    blocks = message.get("content")
    _require(isinstance(blocks, (str, list)), "native_message_content_invalid")
    if isinstance(blocks, str):
        return []
    _require(all(isinstance(block, dict) for block in blocks), "native_block_invalid")
    return blocks


def check_offer(request, *, excluded, permitted, nonce):
    """Require the primary synthetic prompt and an exact permitted MCP offer."""
    _require(_name(excluded) and isinstance(permitted, (set, frozenset)), "tool_scope_invalid")
    _require(
        permitted and all(_name(n) for n in permitted) and excluded not in permitted,
        "tool_scope_invalid",
    )
    _require(isinstance(nonce, str) and bool(nonce), "primary_nonce_invalid")
    _require(isinstance(request, dict) and request.get("stream") is True, "primary_stream_invalid")
    tools = request.get("tools")
    _require(isinstance(tools, list), "offered_tools_invalid")
    names = []
    for tool in tools:
        _require(
            isinstance(tool, dict) and isinstance(tool.get("name"), str), "offered_tool_invalid"
        )
        names.append(tool["name"])
    _require(len(names) == len(set(names)), "offered_tools_duplicate")
    _require(excluded not in names, "excluded_tool_offered")
    _require({n for n in names if n.startswith("mcp__")} == permitted, "permitted_offer_mismatch")
    messages = request.get("messages")
    _require(isinstance(messages, list), "primary_messages_invalid")
    found = False
    for message in messages:
        _require(isinstance(message, dict), "primary_message_invalid")
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            found |= nonce in content
        elif isinstance(content, list):
            found |= any(
                isinstance(b, dict)
                and b.get("type") == "text"
                and isinstance(b.get("text"), str)
                and nonce in b["text"]
                for b in content
            )
    _require(found, "primary_nonce_missing")


def prove_denial(
    request,
    events,
    witness,
    *,
    excluded,
    permitted,
    call_id,
    nonce,
    server_catalog,
    client_policy,
    backend_state,
):
    """A missing call or EOF alone never establishes native client rejection."""
    check_offer(request, excluded=excluded, permitted=permitted, nonce=nonce)
    _require(
        isinstance(server_catalog, (set, frozenset)) and server_catalog == permitted | {excluded},
        "server_catalog_mismatch",
    )
    _require(
        isinstance(client_policy, dict)
        and client_policy.get("excluded_flags") == [excluded]
        and client_policy.get("server_filter") is None
        and "server_filter" in client_policy,
        "client_policy_unproven",
    )
    _require(
        isinstance(backend_state, dict)
        and backend_state.get("primary") is request
        and backend_state.get("followup_error") is True
        and backend_state.get("errors") == [],
        "backend_exchange_unproven",
    )
    _require(isinstance(call_id, str) and bool(call_id), "call_identity_invalid")
    _require(
        isinstance(witness, dict)
        and set(witness) == {"initialize", "tools/list", "tools/call", "closed_cleanly"},
        "mcp_witness_invalid",
    )
    _require(
        all(
            type(witness[k]) is int and witness[k] >= 0
            for k in ("initialize", "tools/list", "tools/call")
        )
        and witness["closed_cleanly"] is True,
        "mcp_witness_invalid",
    )
    _require(witness["initialize"] > 0 and witness["tools/list"] > 0, "mcp_witness_not_connected")
    _require(witness["tools/call"] == 0, "mcp_tool_dispatched")
    _require(isinstance(events, list) and bool(events), "native_events_invalid")
    uses, results, finishes = [], [], []
    for ordinal, event in enumerate(events):
        _require(isinstance(event, dict), "native_event_invalid")
        _require(event.get("type") != "error", "native_error_event")
        kind = event.get("type")
        if kind in {"assistant", "user"}:
            for block in _blocks(event):
                if block.get("type") == "tool_use":
                    _require(
                        kind == "assistant"
                        and block.get("id") == call_id
                        and block.get("name") == excluded,
                        "native_tool_identity_mismatch",
                    )
                    uses.append(ordinal)
                if block.get("type") == "tool_result":
                    _require(
                        kind == "user"
                        and block.get("tool_use_id") == call_id
                        and block.get("is_error") is True,
                        "native_tool_error_missing",
                    )
                    results.append(ordinal)
        if kind == "result":
            _require(
                event.get("subtype") == "success" and event.get("is_error") is False,
                "native_completion_failed",
            )
            finishes.append(ordinal)
    _require(len(uses) == len(results) == len(finishes) == 1, "native_evidence_cardinality")
    _require(uses[0] < results[0] < finishes[0] == len(events) - 1, "native_evidence_order")
    return {
        "offered_tool_excluded": True,
        "permitted_tool_offered": True,
        "native_error_correlated": True,
        "mcp_connected_witness": True,
        "mcp_tool_calls": 0,
        "provider_backend": "synthetic_local",
        "real_provider_inference": False,
    }


def _stream(block, delta, *, reason, model):
    message = {
        "id": "msg_caos_synthetic",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 0},
    }
    frames = [
        {"type": "message_start", "message": message},
        {"type": "content_block_start", "index": 0, "content_block": block},
        {"type": "content_block_delta", "index": 0, "delta": delta},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": reason, "stop_sequence": None},
            "usage": {"output_tokens": 1},
        },
        {"type": "message_stop"},
    ]
    return "".join(f"event: {f['type']}\ndata: {json.dumps(f)}\n\n" for f in frames).encode()


def tool_stream(name, call_id, arguments, *, model):
    _require(_name(name) and isinstance(call_id, str) and bool(call_id), "synthetic_tool_invalid")
    _require(isinstance(arguments, dict), "synthetic_arguments_invalid")
    return _stream(
        {"type": "tool_use", "id": call_id, "name": name, "input": {}},
        {"type": "input_json_delta", "partial_json": json.dumps(arguments)},
        reason="tool_use",
        model=model,
    )


def text_stream(text, *, model):
    return _stream(
        {"type": "text", "text": ""},
        {"type": "text_delta", "text": text},
        reason="end_turn",
        model=model,
    )
