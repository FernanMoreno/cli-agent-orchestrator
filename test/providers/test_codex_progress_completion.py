"""A redraw of commentary is not the end of a receipt-backed Codex turn."""

import re

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.codex import CodexProvider


@pytest.mark.parametrize("viewport", [False, True])
@pytest.mark.parametrize(
    "case, expected",
    [
        ("commentary", TerminalStatus.PROCESSING),
        ("empty", TerminalStatus.PROCESSING),
        ("wrapped_echo", TerminalStatus.PROCESSING),
        ("old_finished", TerminalStatus.PROCESSING),
        ("current_finished_missing_receipt", TerminalStatus.COMPLETED),
        ("current_receipt", TerminalStatus.COMPLETED),
        ("current_receipt_working", TerminalStatus.PROCESSING),
    ],
)
def test_only_current_final_evidence_starts_receipt_verification(viewport, case, expected):
    provider = CodexProvider("worker", "session", "window")
    provider._resolve_native_status = lambda output: None
    task = provider.prepare_input("Review the API without changes.")
    receipt = re.search(r"CAO-TURN-RECEIPT-[0-9a-f]{32}", task).group()
    if case == "old_finished":
        text = "› Old task\n• Old answer\n─ Worked for 2m 21s ─\n"
    else:
        text = "› " + task + "\n• Revisaré API y comprobaré resultados.\n"
        if case == "current_finished_missing_receipt":
            text += "─ Worked for 2m 21s • 14:46 ─\n"
        if case.startswith("current_receipt"):
            text += "  Review complete.\n  " + receipt + "\n"
        if case == "current_receipt_working":
            text += "• Working (15s • esc to interrupt)\n"
    if case == "empty":
        text = ""
    elif case == "wrapped_echo":
        text = "› " + task.replace(receipt, "\n" + receipt + "\n") + "\n"
    text += "› Explain this codebase\n  ? for shortcuts    90% context left"
    status = (
        provider.get_status_from_screen(text.splitlines())
        if viewport
        else provider.get_status(text)
    )
    assert status == expected
