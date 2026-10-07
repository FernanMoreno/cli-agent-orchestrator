"""Unit tests for Copilot CLI provider."""

from __future__ import annotations

import asyncio
import json
import shlex
from unittest.mock import patch

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.copilot_cli import CopilotCliProvider


@pytest.mark.parametrize(
    ("body", "footer", "expected"),
    [
        (
            "",
            "← open sidebar · Autopilot · Allow All · / commands · tab next tab",
            TerminalStatus.IDLE,
        ),
        (
            "❯ task\n● Current answer\n",
            "← open sidebar · Autopilot · Allow All · / commands · tab next tab",
            TerminalStatus.COMPLETED,
        ),
        (
            "❯ task\n● Working (esc to cancel)\n",
            "← open sidebar · Autopilot · Allow All · / commands · tab next tab",
            TerminalStatus.PROCESSING,
        ),
        (
            "",
            "◎ Loading: 1 agent, 14 skills — still waiting on mcp: cao-mcp-server; ide",
            TerminalStatus.PROCESSING,
        ),
        ("", "unexpected content below composer", TerminalStatus.PROCESSING),
    ],
)
def test_native_sidebar_footer_preserves_readiness_boundaries(body, footer, expected):
    provider = CopilotCliProvider("test1234", "test-session", "window-0")
    output = body + "────────────────────\n❯\n────────────────────\n " + footer + "\n"
    assert provider.get_status(output) == expected


def test_native_sidebar_footer_is_removed_from_response():
    provider = CopilotCliProvider("test1234", "test-session", "window-0")
    output = "❯ task\n● Current answer\n────────────────────\n❯\n────────────────────\n ← open sidebar · Autopilot · Allow All · / commands · tab next tab\n"
    assert provider.extract_last_message_from_script(output) == "● Current answer"


def test_erased_permission_dialog_is_not_current_waiting_evidence():
    provider = CopilotCliProvider("test1234", "test-session", "window-0")
    question = "Confirm folder trust\n[y/n]\n"
    redraw = question + "\x1b[2J\x1b[H❯\x1b[2B← open sidebar · Autopilot · Allow All"
    assert provider.get_status(question) == TerminalStatus.WAITING_USER_ANSWER
    assert provider.get_status(redraw) == TerminalStatus.PROCESSING


def test_native_startup_repaint_requires_two_current_pane_observations(monkeypatch):
    from pathlib import Path

    from cli_agent_orchestrator.services import status_monitor as monitor_module

    provider = CopilotCliProvider("test1234", "test-session", "window-0")
    raw = (Path(__file__).parent / "fixtures/copilot-1.0.91-startup-repaint.txt").read_text()
    # Removing escapes cannot resolve cursor-addressed redraws. The genuine
    # settled stream still contains loading and old permission-dialog text.
    assert provider.get_status(raw) == TerminalStatus.PROCESSING
    monkeypatch.setattr(monitor_module.provider_manager, "get_provider", lambda _: provider)
    pane = (Path(__file__).parent / "fixtures/copilot-1.0.91-startup-pane.txt").read_text()
    assert provider.get_status(pane) == TerminalStatus.IDLE
    from unittest.mock import MagicMock

    backend = MagicMock()
    backend.get_history.return_value = pane
    backend.get_native_status.return_value = None
    monkeypatch.setattr("cli_agent_orchestrator.backends.registry.get_backend", lambda: backend)
    monitor = monitor_module.StatusMonitor()
    try:
        assert monitor._fresh_capture_pane_status("test1234", 0) is None
        monitor._last_stale_capture_check["test1234"] = None
        assert monitor._fresh_capture_pane_status("test1234", 0) == TerminalStatus.IDLE
        assert backend.get_history.call_count == 2
        assert backend.get_history.call_args.kwargs["visible_only"] is True
    finally:
        monitor.clear_terminal("test1234")


class TestCopilotCliProviderCommand:
    @patch("cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._supports_flag")
    @patch(
        "cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._build_runtime_mcp_config"
    )
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    @patch.dict("os.environ", {}, clear=True)
    def test_command_builds_default(self, mock_tmux, mock_build_mcp, mock_supports_flag):
        mock_supports_flag.return_value = True
        mock_build_mcp.return_value = '{"mcpServers":{"cao-mcp-server":{"command":"x"}}}'
        mock_tmux.return_value.get_pane_working_directory.return_value = "/tmp/project"

        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        command = provider._command()
        parts = shlex.split(command)

        assert parts[0] == "copilot"
        assert "--allow-all" in parts
        assert "--model" not in parts
        assert "--config-dir" in parts
        assert "--add-dir" in parts
        assert parts[parts.index("--add-dir") + 1] == "/tmp/project"
        assert "--additional-mcp-config" in parts
        assert "--autopilot" in parts
        mock_build_mcp.assert_called_once_with()

    @patch("cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._supports_flag")
    @patch(
        "cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._build_runtime_mcp_config"
    )
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    @patch.dict("os.environ", {}, clear=True)
    def test_empty_allowlist_emits_deny_tool(self, mock_tmux, mock_build_mcp, mock_supports_flag):
        """allowed_tools=[] must pass --deny-tool, not skip restrictions."""
        mock_supports_flag.return_value = True
        mock_build_mcp.return_value = '{"mcpServers":{"cao-mcp-server":{"command":"x"}}}'
        mock_tmux.return_value.get_pane_working_directory.return_value = "/tmp/project"

        provider = CopilotCliProvider("test1234", "test-session", "window-0", allowed_tools=[])
        parts = shlex.split(provider._command())

        assert "--deny-tool" in parts
        assert "shell" in parts

    @patch("cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._supports_flag")
    @patch(
        "cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._build_runtime_mcp_config"
    )
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    @patch.dict("os.environ", {}, clear=True)
    def test_command_does_not_use_model_env(self, mock_tmux, mock_build_mcp, mock_supports_flag):
        mock_supports_flag.return_value = True
        mock_build_mcp.return_value = '{"mcpServers":{"cao-mcp-server":{"command":"x"}}}'
        mock_tmux.return_value.get_pane_working_directory.return_value = "/tmp/project"

        with patch.dict("os.environ", {"CAO_COPILOT_MODEL": "gpt-4.1"}, clear=False):
            provider = CopilotCliProvider("test1234", "test-session", "window-0")
            parts = shlex.split(provider._command())

        assert "--model" not in parts
        assert "--allow-all" in parts
        assert "--autopilot" in parts
        assert "--config-dir" in parts
        assert "--add-dir" in parts
        assert parts[parts.index("--add-dir") + 1] == "/tmp/project"
        assert "--additional-mcp-config" in parts
        mock_build_mcp.assert_called_once_with()

    @patch("cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._supports_flag")
    @patch(
        "cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._build_runtime_mcp_config"
    )
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    @patch.dict("os.environ", {}, clear=True)
    def test_command_passes_agent_name_directly(
        self,
        mock_tmux,
        mock_build_mcp,
        mock_supports_flag,
    ):
        mock_supports_flag.return_value = True
        mock_build_mcp.return_value = '{"mcpServers":{"cao-mcp-server":{"command":"x"}}}'
        mock_tmux.return_value.get_pane_working_directory.return_value = "/tmp/project"

        provider = CopilotCliProvider(
            "test1234", "test-session", "window-0", agent_profile="repo-agent"
        )
        parts = shlex.split(provider._command())
        assert parts[parts.index("--agent") + 1] == "repo-agent"

    @patch("cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._supports_flag")
    @patch(
        "cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._build_runtime_mcp_config"
    )
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    @patch.dict("os.environ", {}, clear=True)
    def test_command_skips_mcp_flag_if_unsupported(
        self,
        mock_tmux,
        mock_build_mcp,
        mock_supports_flag,
    ):
        mock_supports_flag.return_value = False
        mock_tmux.return_value.get_pane_working_directory.return_value = "/tmp/project"
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        command = provider._command()
        assert "--additional-mcp-config" not in command
        mock_build_mcp.assert_not_called()

    @patch("cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._supports_flag")
    @patch(
        "cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._build_runtime_mcp_config"
    )
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    @patch.dict("os.environ", {}, clear=True)
    def test_command_falls_back_to_process_cwd_when_pane_dir_missing(
        self,
        mock_tmux,
        mock_build_mcp,
        mock_supports_flag,
    ):
        mock_supports_flag.return_value = True
        mock_build_mcp.return_value = '{"mcpServers":{"cao-mcp-server":{"command":"x"}}}'
        mock_tmux.return_value.get_pane_working_directory.return_value = None

        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        parts = shlex.split(provider._command())

        assert "--add-dir" in parts
        assert parts[parts.index("--add-dir") + 1]


class TestCopilotCliProviderModelFlag:
    """Tests that the model kwarg is forwarded to Copilot CLI via --model.

    The Copilot provider takes the model value directly from the constructor
    (populated by terminal_service from the already-loaded AgentProfile) to
    avoid re-loading the profile.
    """

    @patch("cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._supports_flag")
    @patch(
        "cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._build_runtime_mcp_config"
    )
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    @patch.dict("os.environ", {}, clear=True)
    def test_command_appends_model_when_set(self, mock_tmux, mock_build_mcp, mock_supports_flag):
        mock_supports_flag.return_value = True
        mock_build_mcp.return_value = '{"mcpServers":{"cao-mcp-server":{"command":"x"}}}'
        mock_tmux.return_value.get_pane_working_directory.return_value = "/tmp/project"

        provider = CopilotCliProvider(
            "test1234",
            "test-session",
            "window-0",
            agent_profile="repo-agent",
            model="claude-sonnet-4.5",
        )
        parts = shlex.split(provider._command())

        assert "--model" in parts
        assert parts[parts.index("--model") + 1] == "claude-sonnet-4.5"

    @patch("cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._supports_flag")
    @patch(
        "cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._build_runtime_mcp_config"
    )
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    @patch.dict("os.environ", {}, clear=True)
    def test_command_omits_model_when_unset(self, mock_tmux, mock_build_mcp, mock_supports_flag):
        mock_supports_flag.return_value = True
        mock_build_mcp.return_value = '{"mcpServers":{"cao-mcp-server":{"command":"x"}}}'
        mock_tmux.return_value.get_pane_working_directory.return_value = "/tmp/project"

        provider = CopilotCliProvider(
            "test1234", "test-session", "window-0", agent_profile="repo-agent"
        )
        parts = shlex.split(provider._command())

        assert "--model" not in parts

    @patch("cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._supports_flag")
    @patch(
        "cli_agent_orchestrator.providers.copilot_cli.CopilotCliProvider._build_runtime_mcp_config"
    )
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    @patch.dict("os.environ", {}, clear=True)
    def test_command_omits_model_when_no_agent_profile(
        self, mock_tmux, mock_build_mcp, mock_supports_flag
    ):
        # --model is only meaningful alongside --agent; without an agent
        # profile the flag is not emitted even if a model is passed.
        mock_supports_flag.return_value = True
        mock_build_mcp.return_value = '{"mcpServers":{"cao-mcp-server":{"command":"x"}}}'
        mock_tmux.return_value.get_pane_working_directory.return_value = "/tmp/project"

        provider = CopilotCliProvider(
            "test1234", "test-session", "window-0", model="claude-sonnet-4.5"
        )
        parts = shlex.split(provider._command())

        assert "--model" not in parts


class TestCopilotCliProviderInitialization:
    @pytest.fixture(autouse=True)
    def executable_capabilities(self):
        # Initialization unit tests simulate the native transport. Keep the
        # capability probe equally controlled instead of invoking host PATH.
        with patch.object(CopilotCliProvider, "_supports_flag", return_value=True):
            yield

    @pytest.mark.asyncio
    @patch("cli_agent_orchestrator.providers.copilot_cli.wait_for_shell")
    async def test_initialize_shell_timeout(self, mock_wait_shell):
        mock_wait_shell.return_value = False
        provider = CopilotCliProvider("test1234", "test-session", "window-0")

        with pytest.raises(TimeoutError, match="Shell initialization timed out"):
            await provider.initialize()

    @pytest.mark.asyncio
    @patch("cli_agent_orchestrator.services.status_monitor.status_monitor")
    @patch("cli_agent_orchestrator.providers.copilot_cli.asyncio.sleep")
    @patch("cli_agent_orchestrator.providers.copilot_cli.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    @patch.object(CopilotCliProvider, "_accept_trust_prompts")
    async def test_initialize_success(
        self,
        mock_accept,
        mock_tmux,
        mock_wait_shell,
        _mock_async_sleep,
        mock_status_monitor,
    ):
        """initialize() launches the CLI and returns once the terminal is IDLE."""
        mock_wait_shell.return_value = True
        mock_status_monitor.get_status.return_value = TerminalStatus.IDLE

        provider = CopilotCliProvider("test1234", "test-session", "window-0")

        result = await provider.initialize()

        assert result is True
        assert provider._initialized is True
        # The CLI command is typed into the pane after the shell is ready.
        mock_tmux.return_value.send_keys.assert_called_once()
        mock_accept.assert_called_once()

    @pytest.mark.asyncio
    @patch("cli_agent_orchestrator.services.status_monitor.status_monitor")
    @patch("cli_agent_orchestrator.providers.copilot_cli.asyncio.sleep")
    @patch("cli_agent_orchestrator.providers.copilot_cli.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    @patch.object(CopilotCliProvider, "_accept_trust_prompts")
    async def test_initialize_handles_trust_prompt_then_idle(
        self,
        mock_accept,
        mock_tmux,
        mock_wait_shell,
        _mock_async_sleep,
        mock_status_monitor,
    ):
        """A WAITING_USER_ANSWER trust prompt is handled before reaching IDLE."""
        mock_wait_shell.return_value = True
        mock_status_monitor.get_status.side_effect = [
            TerminalStatus.WAITING_USER_ANSWER,
            TerminalStatus.IDLE,
        ]

        provider = CopilotCliProvider("test1234", "test-session", "window-0")

        result = await provider.initialize()

        assert result is True
        # Initial trust handling + the in-loop WAITING_USER_ANSWER handling.
        assert mock_accept.call_count >= 2

    @pytest.mark.asyncio
    @patch("cli_agent_orchestrator.services.status_monitor.status_monitor")
    @patch("cli_agent_orchestrator.providers.copilot_cli.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    @patch.object(CopilotCliProvider, "_accept_trust_prompts")
    async def test_initialize_polls_get_status_via_to_thread(
        self,
        mock_accept,
        mock_tmux,
        mock_wait_shell,
        mock_status_monitor,
    ):
        """#558: status_monitor.get_status() is no longer in-memory only -- for a
        PROCESSING terminal it can fork a real tmux capture-pane subprocess (the
        stale-PROCESSING fallback), and copilot init is exactly the regime that trips it
        (cached PROCESSING, pane quiet during auth/MCP boot). Pin the asyncio.to_thread
        dispatch of the init poll -- the other init tests mock the status monitor and
        cannot see HOW it was called."""
        mock_wait_shell.return_value = True
        mock_status_monitor.get_status.return_value = TerminalStatus.IDLE

        provider = CopilotCliProvider("test1234", "test-session", "window-0")

        with patch(
            "cli_agent_orchestrator.providers.copilot_cli.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ) as mock_to_thread:
            result = await provider.initialize()
            # initialize() also offloads _command and send_keys -- count only the
            # get_status dispatches.
            get_status_calls = [
                c
                for c in mock_to_thread.call_args_list
                if c.args[0] == mock_status_monitor.get_status
            ]

        assert result is True
        assert get_status_calls, "status_monitor.get_status was never dispatched via to_thread"
        assert all(c.args[1] == "test1234" for c in get_status_calls)


class TestCopilotCliProviderTrustPrompts:
    @pytest.mark.asyncio
    @patch("cli_agent_orchestrator.providers.copilot_cli.asyncio.sleep")
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    async def test_accept_trust_prompts_answers_yes_then_returns(self, mock_tmux, _mock_sleep):
        provider = CopilotCliProvider("test1234", "test-session", "window-0")

        with (
            patch.object(
                provider,
                "_history",
                side_effect=[
                    "Do you trust all the actions in this folder? [y/N]",
                    "GitHub Copilot v0.0.415\n❯ Type @ to mention files",
                ],
            ),
            patch.object(provider, "_send_enter") as mock_enter,
        ):
            await provider._accept_trust_prompts(timeout=2.0)

        mock_tmux.return_value.send_special_key.assert_any_call("test-session", "window-0", "y")
        assert mock_enter.call_count >= 1

    @pytest.mark.asyncio
    @patch("cli_agent_orchestrator.providers.copilot_cli.logger")
    @patch("cli_agent_orchestrator.providers.copilot_cli.asyncio.sleep")
    @patch("cli_agent_orchestrator.providers.copilot_cli.time.time")
    async def test_accept_trust_prompts_logs_warning_on_timeout(
        self, mock_time, _mock_sleep, mock_logger
    ):
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        mock_time.side_effect = [0.0, 0.0, 3.0]

        with patch.object(provider, "_history", return_value="still waiting"):
            await provider._accept_trust_prompts(timeout=2.0)

        mock_logger.warning.assert_called_once_with(
            "Trust prompt handler timed out for %s:%s",
            "test-session",
            "window-0",
        )

    @pytest.mark.asyncio
    @patch("cli_agent_orchestrator.providers.copilot_cli.asyncio.sleep")
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    async def test_accept_trust_prompts_answers_yes_for_spaced_yes_no_prompt(
        self, mock_tmux, _mock_sleep
    ):
        provider = CopilotCliProvider("test1234", "test-session", "window-0")

        with (
            patch.object(
                provider,
                "_history",
                side_effect=[
                    "Do you trust all the actions in this folder? [y / N]",
                    "GitHub Copilot v0.0.415\n❯ Type @ to mention files",
                ],
            ),
            patch.object(provider, "_send_enter") as mock_enter,
        ):
            await provider._accept_trust_prompts(timeout=2.0)

        mock_tmux.return_value.send_special_key.assert_any_call("test-session", "window-0", "y")
        assert mock_enter.call_count >= 1


class TestCopilotCliProviderStatusDetection:
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_get_status_waiting_user_answer(self, mock_tmux):
        output = "confirm folder trust [y/n]"
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.get_status(output) == TerminalStatus.WAITING_USER_ANSWER

    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_get_status_idle_with_no_user_message(self, mock_tmux):
        output = "GitHub Copilot v0.0.415\n❯ Type @ to mention files"
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.get_status(output) == TerminalStatus.IDLE

    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_get_status_idle_with_wrapped_prompt_helper(self, mock_tmux):
        output = (
            "● Environment loaded: 2 MCP servers, 3 agents\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            "❯  Type @ to mention files, # for issues/PRs, / for commands, or ? for \n"
            "  shortcuts\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            " shift+tab switch mode                     Remaining reqs.: 99.66666666666667%\n"
        )
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.get_status(output) == TerminalStatus.IDLE

    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_get_status_processing_when_no_idle_prompt(self, mock_tmux):
        output = "Working on edits..."
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.get_status(output) == TerminalStatus.PROCESSING

    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_get_status_error_while_processing_if_error_after_user(self, mock_tmux):
        output = "❯ refactor this\nError: failed to parse"
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.get_status(output) == TerminalStatus.ERROR

    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_get_status_completed_when_response_present_and_idle(self, mock_tmux):
        output = "❯ refactor this\n● Edit file.py (+1 -1)\n❯ "
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.get_status(output) == TerminalStatus.COMPLETED

    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_get_status_processing_when_spinner_visible_with_idle_prompt(self, mock_tmux):
        output = (
            "❯ Analyze the dataset and calculate standard \n"
            "  deviation.\n"
            "∙ Thinking (Esc to cancel)\n"
            " ~/repo [⎇ branch*] gpt-5-mini (medium) (0x) data_analyst\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            "❯ Type @ to mention files, # for issues/PRs, / for commands, or ? for \n"
            "  shortcuts\n"
        )
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.get_status(output) == TerminalStatus.PROCESSING

    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_get_status_error_when_idle_and_error_without_assistant_marker(self, mock_tmux):
        output = "❯ refactor this\nError: failed to parse\n❯ "
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.get_status(output) == TerminalStatus.ERROR

    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_get_status_completed_when_idle_and_error_with_assistant_marker(self, mock_tmux):
        output = "❯ refactor this\nassistant: note\nError: sample\n❯ "
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.get_status(output) == TerminalStatus.COMPLETED

    # ------------------------------------------------------------------
    # Copilot v1.0.31+ layout: bare ❯ followed by the status bar line
    # ------------------------------------------------------------------

    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_get_status_idle_with_v1031_autopilot_status_bar(self, mock_tmux):
        """Bare ❯ + 'autopilot · / commands ...' status bar → IDLE (no prior user turn)."""
        output = (
            "● Selected custom agent: developer\n"
            "\n"
            "● Environment loaded: 3 custom instructions, 1 hook, 2 MCP servers\n"
            "\n"
            " ~/repo [⎇ main*%]\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            "❯\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            " autopilot · / commands \u200b                        Claude Sonnet 4.6 · (0%)\n"
        )
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.get_status(output) == TerminalStatus.IDLE

    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_get_status_idle_with_v1031_plan_status_bar(self, mock_tmux):
        """Bare ❯ + 'plan · / commands ...' status bar → IDLE."""
        output = (
            " ~/repo [⎇ main]\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            "❯\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            " plan · / commands \u200b                             Claude Sonnet 4.6 · (0%)\n"
        )
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.get_status(output) == TerminalStatus.IDLE

    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_get_status_idle_with_v1031_interactive_status_bar(self, mock_tmux):
        """Bare ❯ + 'interactive · / commands ...' status bar → IDLE."""
        output = (
            " ~/repo [⎇ main]\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            "❯\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            " interactive · / commands \u200b                      Claude Sonnet 4.6 · (0%)\n"
        )
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.get_status(output) == TerminalStatus.IDLE

    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_get_status_completed_with_v1031_status_bar_after_user_turn(self, mock_tmux):
        """User turn + agent response + bare ❯ + status bar → COMPLETED."""
        output = (
            "❯ fix the bug\n"
            "● Edit src/main.py (+3 -1)\n"
            " ~/repo [⎇ main*%]\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            "❯\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            " autopilot · / commands \u200b                        Claude Sonnet 4.6 · (0%)\n"
        )
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.get_status(output) == TerminalStatus.COMPLETED

    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_get_status_processing_with_spinner_and_v1031_status_bar(self, mock_tmux):
        """Spinner line present alongside status bar → still PROCESSING."""
        output = (
            "❯ refactor utils.py\n"
            "∙ Thinking (Esc to cancel)\n"
            " ~/repo [⎇ main*%]\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            "❯\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            " autopilot · / commands \u200b                        Claude Sonnet 4.6 · (0%)\n"
        )
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.get_status(output) == TerminalStatus.PROCESSING

    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_get_status_idle_with_v1031_absolute_path_breadcrumb(self, mock_tmux):
        """Bare ❯ + absolute-path breadcrumb (CWD outside $HOME) → IDLE."""
        output = (
            " /tmp/pr184-e2e [⎇ pr-184]\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            "❯\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            " autopilot · / commands \u200b                        Claude Sonnet 4.6 · (0%)\n"
        )
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.get_status(output) == TerminalStatus.IDLE


class TestCopilotCliProviderMessageExtraction:
    def test_extract_last_message_from_post_user_lines(self):
        output = "❯ refactor\n● Edit x.py (+2 -1)\nDONE\n❯ "
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(output)
        assert "Edit x.py" in message
        assert "DONE" in message

    def test_extract_last_message_from_assistant_fallback(self):
        output = "assistant: Completed task successfully."
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.extract_last_message_from_script(output) == "Completed task successfully."

    def test_extract_last_message_raises_when_not_found(self):
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        with pytest.raises(ValueError, match="No provider response content found"):
            provider.extract_last_message_from_script("just ui chrome")

    def test_extract_last_message_ignores_processing_spinner_tail(self):
        output = (
            "❯ Analyze the dataset and calculate standard \n"
            "  deviation.\n"
            "∙ Thinking (Esc to cancel)\n"
            " ~/repo [⎇ branch*] gpt-5-mini (medium) (0x) data_analyst\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            "❯ Type @ to mention files, # for issues/PRs, / for commands, or ? for \n"
            "  shortcuts\n"
        )
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        with pytest.raises(ValueError, match="No provider response content found"):
            provider.extract_last_message_from_script(output)


class TestCopilotCliProviderMisc:
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_backend")
    def test_send_enter_uses_tmux_client(self, mock_tmux):
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        provider._send_enter()
        mock_tmux.return_value.send_special_key.assert_called_once_with(
            "test-session", "window-0", "Enter"
        )

    def test_get_idle_pattern_for_log(self):
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert "type @ to mention files" in provider.get_idle_pattern_for_log().lower()

    def test_exit_cli(self):
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        assert provider.exit_cli() == "/exit"

    def test_build_runtime_mcp_config_includes_terminal_id(self):
        provider = CopilotCliProvider("abc12345", "test-session", "window-0")
        runtime_cfg = json.loads(provider._build_runtime_mcp_config())
        assert "cao-mcp-server" in runtime_cfg["mcpServers"]
        assert runtime_cfg["mcpServers"]["cao-mcp-server"]["env"]["CAO_TERMINAL_ID"] == "abc12345"

    def test_build_runtime_mcp_config_resolves_bundled_command(self):
        """The bundled cao-mcp-server is resolved to a PATH-independent
        invocation (wiring guard: a refactor that drops the
        resolve_cao_mcp_command call must fail this test)."""
        provider = CopilotCliProvider("abc12345", "test-session", "window-0")
        MOD = "cli_agent_orchestrator.utils.mcp_resolution"
        with (
            patch(f"{MOD}._sibling_script", return_value="/venv/bin/cao-mcp-server"),
            patch(f"{MOD}.shutil.which", return_value=None),
        ):
            runtime_cfg = json.loads(provider._build_runtime_mcp_config())
        assert runtime_cfg["mcpServers"]["cao-mcp-server"]["command"] == "/venv/bin/cao-mcp-server"

    def test_cleanup_resets_initialized_state(self):
        provider = CopilotCliProvider("test1234", "test-session", "window-0")
        provider._initialized = True
        provider.cleanup()
        assert provider._initialized is False


class TestCopilotCliServerSettings:
    @pytest.mark.asyncio
    @patch("cli_agent_orchestrator.providers.copilot_cli.get_server_settings")
    @patch("cli_agent_orchestrator.providers.copilot_cli.wait_for_shell")
    async def test_initialize_uses_provider_init_timeout(self, mock_wait_shell, mock_settings):
        """initialize() reads provider_init_timeout from server settings."""
        mock_settings.return_value = {
            "mcp_request_timeout": 120,
            "event_bus_max_queue_size": 8192,
            "provider_init_timeout": 45,
            "startup_prompt_handler_timeout": 5,
        }
        mock_wait_shell.return_value = False
        provider = CopilotCliProvider("test1234", "test-session", "window-0")

        with pytest.raises(TimeoutError):
            await provider.initialize()

        mock_wait_shell.assert_called_once_with("test1234", timeout=45)


def test_failed_executable_probe_reports_native_launch_error():
    """An executable shim existing on PATH is not proof it can run in WSL."""
    import subprocess

    provider = CopilotCliProvider("probe", "session", "worker")
    with patch(
        "cli_agent_orchestrator.providers.copilot_cli.subprocess.run",
        return_value=subprocess.CompletedProcess(
            ["copilot", "--help"],
            127,
            stdout="",
            stderr="exec: /c/Users/example/copilot.bat: not found",
        ),
    ):
        with pytest.raises(RuntimeError, match="Copilot executable.*not found"):
            provider._supports_flag("--autopilot")
