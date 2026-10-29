"""Protocol/identity controls; these do not establish native acceptance."""

import json
from test.live_provider import claude_cancel_gateway as gateway
from test.live_provider.native_cancel_adapters import CLAUDE

import pytest

pytestmark = pytest.mark.offline_database
TOKEN = "CAO-TURN-RECEIPT-" + "a" * 32


def body():
    return {
        "system": [{"type": "text", "text": CLAUDE.primary_instruction}],
        "model": CLAUDE.model,
        "stream": True,
        "tools": [],
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": CLAUDE.prompt_prefix
                        + "\nDo not quote or emit that receipt before the task is complete\n"
                        + TOKEN,
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize("blocks", [False, True])
def test_primary_identity_is_protocol_specific_but_receipt_is_shared(blocks):
    value = body()
    if not blocks:
        value["system"] = CLAUDE.primary_instruction
        value["messages"][0]["content"] = value["messages"][0]["content"][0]["text"]
    assert gateway.primary_request(value)
    assert gateway.primary_receipt(value) == TOKEN


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x.update(system="auxiliary"),
        lambda x: x["messages"].append({"role": "user", "content": "later unrelated request"}),
        lambda x: x["messages"][0].update(role="assistant"),
        lambda x: x["messages"][0]["content"][0].update(text="Generate a title for: " + TOKEN),
    ],
)
def test_auxiliary_cannot_satisfy_actual_user_primary(change):
    value = body()
    change(value)
    assert not gateway.primary_request(value)


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x.update(stream=False),
        lambda x: x.update(tools=[{"name": "Bash"}]),
        lambda x: x.update(model="unexpected-model"),
        lambda x: x["messages"][0]["content"][0].update(
            text=x["messages"][0]["content"][0]["text"].replace(TOKEN, "missing")
        ),
        lambda x: x["messages"][0]["content"][0].update(
            text=x["messages"][0]["content"][0]["text"] + "\nCAO-TURN-RECEIPT-" + "b" * 32
        ),
    ],
)
def test_identified_primary_invalid_offer_or_receipt_fails_instead_of_becoming_auxiliary(change):
    value = body()
    change(value)
    assert gateway.primary_request(value)
    with pytest.raises(AssertionError):
        gateway.primary_receipt(value)


def test_anthropic_partial_stream_stays_open_until_explicit_end():
    first = gateway.stream_start(CLAUDE.model) + gateway.stream_delta("live progress\n\n")
    assert b"message_stop" not in first and b"end_turn" not in first
    wire = first + gateway.stream_delta(TOKEN) + gateway.stream_end()
    events = [json.loads(part.split(b"data: ", 1)[1]) for part in wire.split(b"\n\n") if part]
    assert [x["type"] for x in events] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert events[2]["delta"] == {"type": "text_delta", "text": "live progress\n\n"}
    assert events[3]["delta"]["text"] == TOKEN
    assert events[-2]["delta"]["stop_reason"] == "end_turn"
