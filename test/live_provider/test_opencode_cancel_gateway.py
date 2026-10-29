"""Reject ambiguous or foreign-role receipts before native HTTP streaming."""

from test.live_provider.opencode_cancel_gateway import receipt_from_request

import pytest

pytestmark = pytest.mark.offline_database
TOKEN = "CAO-TURN-RECEIPT-" + "a" * 32


@pytest.mark.parametrize(
    "content",
    [
        "T220 controlled native turn\n" + TOKEN,
        [{"type": "text", "text": "T220 controlled native turn\n" + TOKEN}],
    ],
)
def test_receipt_binds_last_actual_user_turn(content):
    assert (
        receipt_from_request(
            {
                "messages": [
                    {"role": "assistant", "content": "CAO-TURN-RECEIPT-" + "b" * 32},
                    {"role": "user", "content": content},
                ]
            }
        )
        == TOKEN
    )


@pytest.mark.parametrize(
    "messages",
    [
        [{"role": "system", "content": "T220 controlled native turn\n" + TOKEN}],
        [
            {"role": "user", "content": "T220 controlled native turn\n" + TOKEN},
            {"role": "user", "content": "unrelated later request"},
        ],
        [
            {
                "role": "user",
                "content": "T220 controlled native turn\n"
                + TOKEN
                + "\nCAO-TURN-RECEIPT-"
                + "b" * 32,
            }
        ],
        [{"role": "user", "content": "T220 controlled native turn without a receipt"}],
    ],
)
def test_refuse_foreign_ambiguous_or_missing_user_receipt(messages):
    with pytest.raises(AssertionError):
        receipt_from_request({"messages": messages})


def test_streamed_auxiliary_with_same_model_nonce_and_text_is_not_primary():
    from test.live_provider.opencode_cancel_gateway import PRIMARY_INSTRUCTION, primary_request

    text = (
        "T220 controlled native turn\n"
        + TOKEN
        + "\nDo not quote or emit that receipt before the task is complete"
    )
    original = {
        "model": "glm-5.3-flash",
        "stream": True,
        "messages": [
            {"role": "system", "content": PRIMARY_INSTRUCTION},
            {"role": "user", "content": text},
        ],
    }
    assert primary_request(original)
    for auxiliary in ["<user_message>" + text + "</user_message>", "Generate a title for: " + text]:
        assert not primary_request(
            {**original, "messages": [{"role": "user", "content": auxiliary}]}
        )
    assert not primary_request({**original, "stream": False})


def test_same_exact_last_user_body_without_primary_agent_instruction_is_auxiliary():
    from test.live_provider.opencode_cancel_gateway import primary_request

    text = (
        "T220 controlled native turn\n"
        + TOKEN
        + "\nDo not quote or emit that receipt before the task is complete"
    )
    assert not primary_request(
        {
            "model": "glm-5.3-flash",
            "stream": True,
            "messages": [
                {"role": "system", "content": "Generate a title"},
                {"role": "user", "content": text},
            ],
        }
    )
