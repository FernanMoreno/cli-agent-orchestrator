"""Actual Claude initialization retains old receipt until common recovery settles it."""

from unittest.mock import AsyncMock, Mock

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers import claude_code as module
from cli_agent_orchestrator.providers.base import cancelled_turn_initialization

pytestmark = pytest.mark.offline_database
RAIL = "─" * 80
CLEAN = "Claude Code v2.1.303\n" + RAIL + "\n❯ \n" + RAIL + "\n"


@pytest.mark.asyncio
async def test_actual_claude_initialize_observes_clean_replacement_without_releasing_old_receipt(
    monkeypatch,
):
    provider = module.ClaudeCodeProvider("1234abcd", "session", "window")
    provider._resolve_native_status = lambda _: None
    provider._resolve_buffer = lambda value: value
    provider.prepare_input("Original task must never be resent")
    pending = provider.pending_turn_receipt_state()
    backend = Mock()
    monkeypatch.setattr(module, "get_backend", lambda: backend)
    monkeypatch.setattr(module, "wait_for_shell", AsyncMock(return_value=True))
    monkeypatch.setattr(provider, "_load_profile", lambda: None)
    monkeypatch.setattr(provider, "_build_claude_command", lambda _: "claude")
    monkeypatch.setattr(provider, "_ensure_skip_bypass_prompt_setting", Mock())
    monkeypatch.setattr(provider, "_handle_startup_prompts", AsyncMock())
    monkeypatch.setattr(provider, "wait_until_input_ready", AsyncMock(return_value=True))
    # No MCP config in this isolated control; production discovery still runs.
    assert provider._cao_mcp_ready_path is None

    async def wait(tid, targets, **kwargs):
        assert tid == provider.terminal_id
        assert provider.pending_turn_receipt_state() == pending
        assert provider.get_status(CLEAN) == TerminalStatus.IDLE
        return provider.get_status(CLEAN) in targets

    monkeypatch.setattr(module, "wait_until_status", wait)
    with cancelled_turn_initialization(
        provider, "1234abcd", "session", "window", pending["generation"]
    ):
        assert await provider.initialize() is True
        assert provider.pending_turn_receipt_state() == pending
    assert provider.pending_turn_receipt_state() == pending
    backend.send_keys.assert_called_once_with("session", "window", "claude")
