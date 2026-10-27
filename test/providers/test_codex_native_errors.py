"""Controlled Codex 0.164 native error frames; no CLI, credentials or database."""

from pathlib import Path

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.codex import CodexProvider

pytestmark = pytest.mark.offline_database
FIXTURES = Path(__file__).parent / "fixtures"


def provider(monkeypatch, active):
    result = CodexProvider("test1234", "private", "worker")
    monkeypatch.setattr(result, "_resolve_native_status", lambda output: None)
    monkeypatch.setattr(result, "_resolve_buffer", lambda output: output)
    if active:
        result.prepare_input("Synthetic active task")
    return result


@pytest.mark.parametrize(
    "name,expected",
    [
        ("auth", TerminalStatus.ERROR),
        ("throttle", TerminalStatus.ERROR),
        ("quota", TerminalStatus.WAITING_QUOTA),
    ],
)
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("screen", [False, True])
@pytest.mark.parametrize("user_context", [False, True])
def test_native_terminal_error_is_not_an_idle_or_processing_turn(
    monkeypatch, name, expected, active, screen, user_context
):
    agent = provider(monkeypatch, active)
    text = (FIXTURES / f"codex_0164_{name}_error.txt").read_text()
    if not user_context:
        text = text.replace("› Reply briefly to this synthetic diagnostic.\n", "")
    status = agent.get_status_from_screen(text.splitlines()) if screen else agent.get_status(text)
    assert status == expected


@pytest.mark.parametrize("name", ["auth", "throttle", "quota"])
@pytest.mark.parametrize("active", [False, True])
def test_assistant_quoting_a_native_error_cell_is_not_a_live_error(monkeypatch, name, active):
    agent = provider(monkeypatch, active)
    native = (FIXTURES / f"codex_0164_{name}_error.txt").read_text()
    error = next(line for line in native.splitlines() if line.startswith("■ "))
    text = (
        "› Explain a provider error\n• Here is a quoted example:\n"
        + error
        + "\n› \n? for shortcuts\n"
    )
    assert agent.get_status(text) not in {TerminalStatus.ERROR, TerminalStatus.WAITING_QUOTA}


@pytest.mark.parametrize("label", ["■ You’ve", "Error: You've"])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_old_quota_cell_does_not_park_a_new_active_turn(monkeypatch, label, newline):
    agent = provider(monkeypatch, True)
    text = (
        "› Previous request\n" + label + " hit your usage limit. Try again later.\n"
        "› New active request\n› \n? for shortcuts\n"
    )
    assert agent.get_status(text.replace("\n", newline)) == TerminalStatus.PROCESSING


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize(
    "notice",
    [
        "Conversation interrupted - tell the model what to do differently.",
        "Synthetic informational notice",
    ],
)
def test_a_square_notice_alone_does_not_become_a_terminal_error(monkeypatch, active, notice):
    # Defensive synthetic notices, not evidence of native cancellation acceptance.
    agent = provider(monkeypatch, active)
    text = "› Synthetic request\n■ " + notice + "\n› \n? for shortcuts\n"
    assert agent.get_status(text) == (TerminalStatus.PROCESSING if active else TerminalStatus.IDLE)
