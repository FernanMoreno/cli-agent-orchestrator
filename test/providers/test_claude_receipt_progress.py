"""Native tool/commentary redraws and post-turn surveys cannot replace a result."""

import hashlib
from pathlib import Path

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider

RECEIPT = "CAO-TURN-RECEIPT-" + "a" * 32
RAIL = "─" * 80


def provider():
    p = ClaudeCodeProvider("worker", "offline", "offline")
    p._resolve_native_status = lambda output: None
    p.restore_turn_receipt_state(
        dict(
            generation="c" * 32,
            receipt_sha256=hashlib.sha256(RECEIPT.encode()).hexdigest(),
            phase="sent",
        )
    )
    return p


def test_authentic_optional_survey_does_not_replace_final_receipt():
    p = provider()
    text = (Path(__file__).parent / "fixtures/claude-post-turn-feedback.txt").read_text()
    result = p.extract_last_message_from_script(text)
    assert "Pedí la confirmación a ambos" in result
    assert p._result_has_active_receipt(result)
    assert "How is Claude doing" not in result
    assert p.receipt_result_terminal_status(text, result) == TerminalStatus.COMPLETED


@pytest.mark.parametrize("viewport", [False, True])
@pytest.mark.parametrize(
    "case, expected",
    [
        ("commentary", TerminalStatus.PROCESSING),
        ("tool", TerminalStatus.PROCESSING),
        ("interim_then_tool", TerminalStatus.PROCESSING),
        ("old", TerminalStatus.PROCESSING),
        ("missing_receipt_final", TerminalStatus.COMPLETED),
        ("missing_receipt_final_with_feedback", TerminalStatus.COMPLETED),
        ("receipt_final", TerminalStatus.COMPLETED),
    ],
)
def test_current_final_evidence_admits_verification(viewport, case, expected):
    p = provider()
    if case == "old":
        text = "❯ previous task\n● previous answer\n✻ Baked for 25s\n"
    else:
        text = "❯ current task\n exactly this receipt: " + RECEIPT + "\n"
        text += (
            "● Bash(python3 -m unittest test_api)\n"
            if case == "tool"
            else "● Voy a comprobar la API.\n"
        )
        if case == "interim_then_tool":
            text += "✻ Pondered for 8s\n● Calling cao-mcp-server…\n"
        if case == "receipt_final":
            text += "  " + RECEIPT + "\n"
        if case in {
            "missing_receipt_final",
            "receipt_final",
            "missing_receipt_final_with_feedback",
        }:
            text += "✻ Baked for 25s\n"
    if case == "missing_receipt_final_with_feedback":
        text += "● How is Claude doing this session? (optional)\n  1: Bad    2: Fine   3: Good   0: Dismiss\n"
    text += RAIL + "\n❯ \n" + RAIL + "\n  ⏵⏵ bypass permissions on"
    status = p.get_status_from_screen(text.splitlines()) if viewport else p.get_status(text)
    assert status == expected


def test_a_real_survey_shaped_answer_after_an_old_turn_is_preserved():
    p = ClaudeCodeProvider("worker", "offline", "offline")
    text = (
        "● Previous answer\n✻ Baked for 25s\n❯ New question\n"
        "● How is Claude doing this session? (optional)\n"
        "  1: Bad    2: Fine   3: Good   0: Dismiss\n" + RAIL + "\n❯ \n" + RAIL
    )
    assert p.extract_last_message_from_script(text).startswith("How is Claude doing")


def test_two_native_surveys_in_history_preserve_the_current_receipt():
    p = provider()
    text = (Path(__file__).parent / "fixtures/claude-post-turn-feedback.txt").read_text()
    result = p.extract_last_message_from_script(
        text.replace(RECEIPT, "CAO-TURN-RECEIPT-" + "b" * 32) + "\n" + text
    )
    assert p._result_has_active_receipt(result)
    assert "How is Claude doing" not in result
