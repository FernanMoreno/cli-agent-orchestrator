"""Captured native quota after commentary, with synthetic ambiguity controls."""

from pathlib import Path

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.codex import CodexProvider

pytestmark = pytest.mark.offline_database
FIXTURE = Path(__file__).parent / "fixtures/codex_0164_partial_quota.txt"


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


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("screen", [False, True])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_native_quota_after_partial_reply_remains_quota(monkeypatch, active, screen, newline):
    p = agent(monkeypatch, active)
    assert (
        classify(p, FIXTURE.read_text().replace("\n", newline), screen)
        == TerminalStatus.WAITING_QUOTA
    )


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("screen", [False, True])
@pytest.mark.parametrize(
    "variant",
    [
        "adjacent",
        "indented",
        "finished",
        "new_user",
        "user_bullet",
        "bare",
        "legacy_label",
        "http_429",
    ],
)
def test_synthetic_quotes_stale_and_nonquota_cells_do_not_park_a_turn(
    monkeypatch, active, screen, variant
):
    p = agent(monkeypatch, active)
    error = "■ You’ve hit your usage limit. Try again later."
    reply = "• Partial answer"
    separator = "\n\n"
    after = ""
    if variant == "adjacent":
        separator = "\n"
    elif variant == "indented":
        error = "  " + error
    elif variant == "finished":
        after = "\n  Worked for 1s"
    elif variant == "new_user":
        after = "\n› New request"
    elif variant == "user_bullet":
        reply = "  • Echoed user bullet"
    elif variant == "bare":
        error = error[2:]
    elif variant == "legacy_label":
        error = "Error: " + error[2:]
    elif variant == "http_429":
        error = "■ exceeded retry limit, last status: 429 Too Many Requests: synthetic throttle"
    text = "› Original request\n" + reply + separator + error + after + "\n\n› \n? for shortcuts\n"
    assert classify(p, text, screen) != TerminalStatus.WAITING_QUOTA
