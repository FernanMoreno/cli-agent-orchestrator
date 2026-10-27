"""Actual narrow Codex cells and conservative continuation boundary controls."""

from pathlib import Path

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.codex import CodexProvider

pytestmark = pytest.mark.offline_database
FIXTURES = Path(__file__).parent / "fixtures"


def agent(monkeypatch, active):
    p = CodexProvider("test1234", "private", "worker")
    monkeypatch.setattr(p, "_resolve_native_status", lambda output: None)
    monkeypatch.setattr(p, "_resolve_buffer", lambda output: output)
    if active:
        p.prepare_input("Synthetic active task")
    return p


def classify(p, text, screen):
    before = p.pending_turn_receipt_state()
    status = p.get_status_from_screen(text.splitlines()) if screen else p.get_status(text)
    assert p.pending_turn_receipt_state() == before
    return status


@pytest.mark.parametrize("case", ["auth", "quota", "quote"])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("screen", [False, True])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_captured_wrapped_native_cell_keeps_its_kind(monkeypatch, case, active, screen, newline):
    text = (FIXTURES / f"codex_0164_wrapped_{case}.txt").read_text().replace("\n", newline)
    expected = {"auth": TerminalStatus.ERROR, "quota": TerminalStatus.WAITING_QUOTA}.get(case)
    if case == "quote":
        expected = TerminalStatus.PROCESSING if active else TerminalStatus.COMPLETED
    assert classify(agent(monkeypatch, active), text, screen) == expected


@pytest.mark.parametrize("case", ["auth", "quota"])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("screen", [False, True])
@pytest.mark.parametrize(
    "variant",
    [
        "adjacent",
        "quoted_head",
        "indented_tail",
        "assistant_tail",
        "user_tail",
        "notice",
        "finished",
        "progress",
        "separate_paragraph",
    ],
)
def test_synthetic_new_cells_and_ambiguous_continuations_are_not_native_errors(
    monkeypatch, case, active, screen, variant
):
    head = (
        "■ unexpected status 401 Unauthorized: example"
        if case == "auth"
        else "■ You’ve hit your usage limit."
    )
    tail = (
        "http://127.0.0.1:1/v1/responses"
        if case == "auth"
        else "visit the usage settings or try again later."
    )
    separator = "\n\n"
    if variant == "adjacent":
        separator = "\n"
    elif variant == "quoted_head":
        head = "  " + head
    elif variant == "indented_tail":
        tail = "  " + tail
    elif variant == "assistant_tail":
        tail = "• Further assistant content"
    elif variant == "user_tail":
        tail = "› New request"
    elif variant == "notice":
        head = "■ Conversation interrupted - tell the model what to do differently."
    elif variant == "finished":
        tail = "Worked for 1s"
    elif variant == "progress":
        tail = "◦ Working (1s • esc to interrupt)"
    elif variant == "separate_paragraph":
        tail = "\n" + tail
    text = (
        "› Original task\n• Partial answer"
        + separator
        + head
        + "\n"
        + tail
        + "\n\n› \n? for shortcuts\n"
    )
    assert classify(agent(monkeypatch, active), text, screen) not in {
        TerminalStatus.ERROR,
        TerminalStatus.WAITING_QUOTA,
    }


@pytest.mark.parametrize("case", ["auth", "quota"])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("screen", [False, True])
def test_actual_wrapped_history_does_not_settle_a_new_request(monkeypatch, case, active, screen):
    text = (FIXTURES / f"codex_0164_wrapped_{case}.txt").read_text()
    text += "\n› Next request\n› \n? for shortcuts\n"
    assert classify(agent(monkeypatch, active), text, screen) == (
        TerminalStatus.PROCESSING if active else TerminalStatus.IDLE
    )


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("screen", [False, True])
def test_actual_wrapped_quote_does_not_need_a_finished_marker(monkeypatch, active, screen):
    text = (FIXTURES / "codex_0164_wrapped_quote.txt").read_text()
    text = "\n".join(line for line in text.splitlines() if "Worked for" not in line)
    assert classify(agent(monkeypatch, active), text, screen) == (
        TerminalStatus.PROCESSING if active else TerminalStatus.COMPLETED
    )


@pytest.mark.parametrize("case", ["auth", "quota"])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("screen", [False, True])
@pytest.mark.parametrize("variant", ["frame_side", "frame_top", "notice_phrase", "second_square"])
def test_new_structural_cells_and_notice_bodies_do_not_extend_an_error(
    monkeypatch, case, active, screen, variant
):
    head = (
        "■ unexpected status 401 Unauthorized: example"
        if case == "auth"
        else "■ You’ve hit your usage limit."
    )
    if variant == "frame_side":
        tail = "│ Choose an option"
    elif variant == "frame_top":
        tail = "╭── Dialog"
    elif variant == "notice_phrase":
        head = "■ Conversation interrupted - tell the model what to do differently."
        tail = (
            "unexpected status 401 Unauthorized"
            if case == "auth"
            else "You’ve hit your usage limit."
        )
    else:
        tail = head
    text = (
        "› Original task\n• Partial answer\n\n" + head + "\n" + tail + "\n\n› \n? for shortcuts\n"
    )
    assert classify(agent(monkeypatch, active), text, screen) not in {
        TerminalStatus.ERROR,
        TerminalStatus.WAITING_QUOTA,
    }


@pytest.mark.parametrize("case", ["auth", "quota"])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("screen", [False, True])
def test_wrapped_error_without_commentary_keeps_the_existing_path(
    monkeypatch, case, active, screen
):
    text = (FIXTURES / f"codex_0164_wrapped_{case}.txt").read_text()
    text = "\n".join(line for line in text.splitlines() if not line.startswith("• CAO-"))
    assert classify(agent(monkeypatch, active), text, screen) == (
        TerminalStatus.ERROR if case == "auth" else TerminalStatus.WAITING_QUOTA
    )


@pytest.mark.parametrize("case", ["auth", "quota"])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("screen", [False, True])
def test_rate_limit_dialog_after_an_error_is_still_an_input_gate(monkeypatch, case, active, screen):
    head = (
        "■ unexpected status 401 Unauthorized: example"
        if case == "auth"
        else "■ You’ve hit your usage limit."
    )
    text = (
        "› Original task\n• Partial answer\n\n" + head + "\n"
        "  Approaching rate limits\n"
        "› 1. Switch to gpt-5.6-luna                 Fast and affordable model.\n"
        "  2. Keep current model\n"
        "  3. Keep current model (never show again)\n\n"
        "Press enter to confirm or esc to go back\n"
    )
    assert classify(agent(monkeypatch, active), text, screen) == TerminalStatus.WAITING_USER_ANSWER
