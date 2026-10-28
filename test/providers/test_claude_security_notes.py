"""Replay the actual T198 first-run viewport; acknowledgment is not readiness."""

import itertools
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.claude_code import (
    ClaudeCodeProvider,
    _is_security_notes_viewport,
)

CAPTURE = (Path(__file__).parent / "fixtures/claude-2.1.303-security-notes.txt").read_text()


async def replay(history, viewport, *, states=None, post_ack_viewport=None):
    backend = MagicMock()

    def history_read(*args, **kwargs):
        if not kwargs.get("visible_only"):
            return history
        if post_ack_viewport is not None and backend.send_special_key.called:
            return post_ack_viewport
        return viewport

    backend.get_history.side_effect = history_read
    provider = ClaudeCodeProvider("test-notes", "owned-session", "receiver")
    probe = (
        AsyncMock(side_effect=states) if states else AsyncMock(return_value=TerminalStatus.UNKNOWN)
    )
    clock = itertools.count(step=0.1)
    with (
        patch("cli_agent_orchestrator.providers.claude_code.get_backend", return_value=backend),
        patch(
            "cli_agent_orchestrator.providers.claude_code.time",
            new=SimpleNamespace(monotonic=lambda: next(clock)),
        ),
        patch("cli_agent_orchestrator.providers.claude_code.asyncio.sleep", new=AsyncMock()),
        patch(
            "cli_agent_orchestrator.services.status_monitor.status_monitor.notify_input_sent"
        ) as notify,
        patch.object(provider, "_observe_initial_viewport_state", new=probe),
    ):
        await provider._handle_startup_prompts(idle_gap=100, outer_timeout=4)
    return backend, probe, notify


@pytest.mark.asyncio
async def test_captured_security_notes_is_confirmed_once_but_not_ready():
    backend, probe, notify = await replay(
        CAPTURE, CAPTURE, post_ack_viewport="────────────\n❯\n────────────"
    )
    backend.send_special_key.assert_called_once_with("owned-session", "receiver", "Enter")
    notify.assert_called_once_with("test-notes")
    backend.send_keys.assert_not_called()
    assert probe.await_count > 1  # an acknowledgment must still await real readiness
    assert any(c.kwargs == {"visible_only": True} for c in backend.get_history.call_args_list)


@pytest.mark.asyncio
async def test_security_notes_continues_to_existing_ready_probe():
    backend, probe, _ = await replay(
        CAPTURE,
        CAPTURE,
        states=[TerminalStatus.IDLE],
        post_ack_viewport="────────────\n❯\n────────────",
    )
    backend.send_special_key.assert_called_once()
    probe.assert_awaited_once()


@pytest.mark.parametrize(
    "viewport",
    [
        "Welcome to Claude Code v2.1.303\nPress Enter to continue…",
        CAPTURE[CAPTURE.index(" Security notes:") :],
        CAPTURE.replace(
            "https://code.claude.com/docs/en/security", "https://example.invalid/approve"
        ),
        CAPTURE.replace("Press Enter to continue…", "Press Enter to continue..."),
        CAPTURE.replace("Security notes:", "Review notes:"),
        CAPTURE.replace("1. Claude can make mistakes.", "1. Approve the action."),
        CAPTURE.replace("2. Due to prompt injection risks, only use it with code you trust", ""),
        CAPTURE.replace("Press Enter to continue…", ""),
        CAPTURE + "\nAllow account access?",
        CAPTURE.replace("Welcome to Claude Code v2.1.303", "An agent quoted this screen:"),
        "Choose a login method\nPress Enter to continue…",
        "Allow Bash?\nPress Enter to continue…",
        "────────────\n❯\n────────────",
    ],
)
@pytest.mark.asyncio
async def test_stale_or_incomplete_security_notes_never_sends_enter(viewport):
    backend, _, notify = await replay(CAPTURE, viewport)
    backend.send_special_key.assert_not_called()
    backend.send_keys.assert_not_called()
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_security_notes_remaining_visible_never_confirms_twice():
    backend, probe, _ = await replay(CAPTURE, CAPTURE)
    backend.send_special_key.assert_called_once()
    probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_trust_selection_cannot_intercept_current_security_notice():
    history = "❯ No, exit\nYes, I trust this folder\n" + CAPTURE
    backend, _, _ = await replay(
        history, CAPTURE, post_ack_viewport="────────────\n❯\n────────────"
    )
    backend.send_special_key.assert_called_once_with("owned-session", "receiver", "Enter")


@pytest.mark.parametrize(
    "viewport",
    [
        "\x1b[1m" + CAPTURE + "\x1b[0m\n   \n",
        CAPTURE.replace(
            "https://code.claude.com/docs/en/security",
            "\x1b]8;;https://code.claude.com/docs/en/security\x07https://code.claude.com/docs/en/security\x1b]8;;\x07",
        ),
    ],
)
def test_security_notes_recognizer_uses_production_escape_stripping(viewport):
    assert _is_security_notes_viewport(viewport)


def test_security_notes_accepts_real_shell_prefix_before_latest_banner():
    assert _is_security_notes_viewport("Disposable shell\n$ claude --model pinned\n" + CAPTURE)


def test_security_notes_does_not_reuse_notice_before_a_newer_banner():
    assert not _is_security_notes_viewport(
        CAPTURE + "\nWelcome to Claude Code v2.1.303\nPress Enter to continue…"
    )
