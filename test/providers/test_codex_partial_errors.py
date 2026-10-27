"""Native partial-output error boundaries and quoted/stale negative controls."""

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
    result = p.get_status_from_screen(text.splitlines()) if screen else p.get_status(text)
    assert before == p.pending_turn_receipt_state()
    return result


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("screen", [False, True])
def test_captured_native_failure_after_partial_text_is_error(monkeypatch, active, screen):
    p = agent(monkeypatch, active)
    text = (FIXTURES / "codex_0164_partial_auth_error.txt").read_text()
    assert classify(p, text, screen) == TerminalStatus.ERROR


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("screen", [False, True])
def test_captured_successful_native_quote_is_not_a_live_error(monkeypatch, active, screen):
    p = agent(monkeypatch, active)
    text = (FIXTURES / "codex_0164_successful_error_quote.txt").read_text()
    assert classify(p, text, screen) == (
        TerminalStatus.PROCESSING if active else TerminalStatus.COMPLETED
    )


@pytest.mark.parametrize("screen", [False, True])
@pytest.mark.parametrize(
    "variant", ["adjacent", "indented", "later_reply", "finished", "new_user", "notice"]
)
def test_synthetic_ambiguous_quoted_stale_or_non_error_cells_stay_negative(
    monkeypatch, screen, variant
):
    p = agent(monkeypatch, True)
    error = "■ unexpected status 401 Unauthorized: example"
    separator = "\n\n"
    after = ""
    if variant == "adjacent":
        separator = "\n"
    elif variant == "indented":
        error = "  " + error
    elif variant == "later_reply":
        after = "\n• Further assistant content"
    elif variant == "finished":
        after = "\n  Worked for 1s"
    elif variant == "new_user":
        after = "\n› Next request"
    elif variant == "notice":
        error = "■ Conversation interrupted - tell the model what to do differently."
    text = (
        "› Original task\n• Partial answer"
        + separator
        + error
        + after
        + "\n\n› \n? for shortcuts\n"
    )
    assert classify(p, text, screen) not in {TerminalStatus.ERROR, TerminalStatus.WAITING_QUOTA}


@pytest.mark.parametrize("active", [False, True])
def test_screen_blank_cell_boundaries_do_not_hide_a_trust_dialog(monkeypatch, active):
    p = agent(monkeypatch, active)
    # Keep the screen adapter's established nonblank-row dialog windows.
    text = (
        "Do you trust the contents of this directory?"
        + "\n" * 18
        + "Press enter to continue\n› \n? for shortcuts"
    )
    assert p.get_status_from_screen(text.splitlines()) == TerminalStatus.WAITING_USER_ANSWER


@pytest.mark.parametrize("screen", [False, True])
def test_indented_multiline_user_bullet_is_not_a_partial_assistant_reply(monkeypatch, screen):
    p = agent(monkeypatch, True)
    text = "› Explain this input:\n  • a user bullet\n\n■ unexpected status 401 Unauthorized: example\n\n› \n? for shortcuts\n"
    assert classify(p, text, screen) != TerminalStatus.ERROR
