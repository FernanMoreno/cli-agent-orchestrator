"""Tests for launch command."""

import os
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from cli_agent_orchestrator.cli.commands.launch import _parse_env_pairs, launch

# ── Backend auto-detection (issue #308) ──────────────────────────────


def test_normal_launch_attaches_local_bearer_only_in_header():
    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            return_value="private-launch-token",
        ),
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as post,
    ):
        post.return_value.json.return_value = {"session_name": "test", "id": "id", "name": "worker"}
        result = CliRunner().invoke(launch, ["--agents", "test-agent", "--yolo", "--headless"])
        assert result.exit_code == 0, result.output
        assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer private-launch-token"}
        assert "private-launch-token" not in str(post.call_args.kwargs["params"])
        assert "private-launch-token" not in result.output


def test_launch_syncs_backend_from_server_before_attach():
    """Non-headless launch calls sync_backend_from_server() before get_backend().

    Regression guard for #308: when ``cao-server --terminal herdr`` is used
    without config.json, the CLI must auto-detect the server's backend via
    /health rather than defaulting to tmux.
    """
    runner = CliRunner()
    call_order = []

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend") as mock_get_backend,
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
        patch("cli_agent_orchestrator.cli.commands.launch.sync_backend_from_server") as mock_sync,
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None
        mock_wait.return_value = True

        def record_sync():
            call_order.append("sync")

        def record_attach(*a, **kw):
            call_order.append("attach")

        mock_sync.side_effect = record_sync
        mock_get_backend.return_value.attach_session.side_effect = record_attach

        result = runner.invoke(launch, ["--agents", "test-agent", "--yolo"])

        assert result.exit_code == 0
        mock_sync.assert_called_once()
        assert call_order == ["sync", "attach"]


def test_launch_headless_does_not_sync_backend():
    """Headless launch skips sync_backend_from_server (no attach needed)."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.sync_backend_from_server") as mock_sync,
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None

        result = runner.invoke(launch, ["--agents", "test-agent", "--headless", "--yolo"])

        assert result.exit_code == 0
        mock_sync.assert_not_called()


def test_launch_passes_cwd_by_default():
    """Test that launch command sends current working directory when not explicitly provided."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend") as mock_get_backend,
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
    ):
        mock_get_backend.return_value.attach_session.return_value = None

        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None
        mock_wait.return_value = True

        result = runner.invoke(launch, ["--agents", "test-agent", "--yolo"])

        assert result.exit_code == 0
        mock_post.assert_called_once()
        params = mock_post.call_args.kwargs["params"]
        assert "working_directory" in params
        assert params["working_directory"] == os.path.realpath(os.getcwd())


def test_launch_passes_explicit_working_directory():
    """Test that --working-directory is passed to the API when provided."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend") as mock_get_backend,
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
    ):
        mock_get_backend.return_value.attach_session.return_value = None

        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None
        mock_wait.return_value = True

        result = runner.invoke(
            launch,
            [
                "--agents",
                "test-agent",
                "--yolo",
                "--working-directory",
                "/remote/path",
            ],
        )

        assert result.exit_code == 0
        params = mock_post.call_args.kwargs["params"]
        assert params["working_directory"] == "/remote/path"


def test_launch_passes_explicit_kiro_engine():
    """The direct CLI surface forwards engine selection without changing provider selection."""
    runner = CliRunner()

    with patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post:
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None

        result = runner.invoke(
            launch, ["--agents", "test-agent", "--engine", "kas", "--headless", "--yolo"]
        )

    assert result.exit_code == 0
    assert mock_post.call_args.kwargs["params"]["engine"] == "kas"


def test_launch_headless_message_sends_to_terminal():
    """Test headless mode with message waits for IDLE then sends and polls for output."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.get") as mock_get,
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
        patch("cli_agent_orchestrator.cli.commands.launch.time.sleep"),
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None
        mock_wait.return_value = True

        poll_resp = MagicMock()
        poll_resp.raise_for_status.return_value = None
        poll_resp.json.return_value = {"status": "completed"}

        output_resp = MagicMock()
        output_resp.raise_for_status.return_value = None
        output_resp.json.return_value = {"output": "task done"}

        mock_get.side_effect = [poll_resp, output_resp]

        result = runner.invoke(
            launch,
            [
                "--agents",
                "test-agent",
                "--headless",
                "--yolo",
                "do something",
            ],
        )

        assert result.exit_code == 0
        assert "task done" in result.output
        mock_wait.assert_called_once()
        # Two POST calls: create session + send message
        assert mock_post.call_count == 2


def test_launch_invalid_provider():
    """Test launch with invalid provider."""
    runner = CliRunner()

    result = runner.invoke(launch, ["--agents", "test-agent", "--provider", "invalid-provider"])

    assert result.exit_code != 0
    assert "Invalid provider" in result.output


def test_launch_with_session_name():
    """Test launch with custom session name."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend") as mock_get_backend,
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
    ):
        mock_get_backend.return_value.attach_session.return_value = None
        mock_post.return_value.json.return_value = {
            "session_name": "custom-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None
        mock_wait.return_value = True

        result = runner.invoke(
            launch, ["--agents", "test-agent", "--session-name", "custom-session", "--yolo"]
        )

        assert result.exit_code == 0

        call_args = mock_post.call_args
        params = call_args.kwargs["params"]
        assert params["session_name"] == "custom-session"


def test_launch_request_exception():
    """Test launch handles RequestException."""
    runner = CliRunner()

    with patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post:
        import requests

        mock_post.side_effect = requests.exceptions.RequestException("Connection refused")

        result = runner.invoke(launch, ["--agents", "test-agent", "--yolo"])

        assert result.exit_code != 0
        assert "Failed to connect to cao-server" in result.output


def test_launch_generic_exception():
    """Test launch handles generic exception."""
    runner = CliRunner()

    with patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post:
        mock_post.side_effect = Exception("Unexpected error")

        result = runner.invoke(launch, ["--agents", "test-agent", "--yolo"])

        assert result.exit_code != 0
        assert "Unexpected error" in result.output


def test_launch_headless_mode():
    """Test launch in headless mode doesn't attach to the backend."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend") as mock_get_backend,
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None

        result = runner.invoke(launch, ["--agents", "test-agent", "--headless", "--yolo"])

        assert result.exit_code == 0
        # In headless mode, attach_session should not be called
        mock_get_backend.return_value.attach_session.assert_not_called()


def test_launch_non_headless_waits_for_idle_before_attach():
    """Non-headless launch must wait for IDLE/COMPLETED before attaching.

    Regression guard for #220: attaching before the TUI finishes initializing
    races with input-handler wiring and silently drops keystrokes. The wait
    must be called with the terminal id before attach_session.
    """
    runner = CliRunner()

    call_order = []

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend") as mock_get_backend,
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None

        def record_wait(*a, **kw):
            call_order.append("wait")
            return True

        def record_attach(*a, **kw):
            call_order.append("attach")

        mock_wait.side_effect = record_wait
        mock_get_backend.return_value.attach_session.side_effect = record_attach

        result = runner.invoke(launch, ["--agents", "test-agent", "--yolo"])

        assert result.exit_code == 0
        mock_wait.assert_called_once()
        wait_args = mock_wait.call_args
        assert wait_args.args[0] == "test-terminal-id"
        assert call_order == ["wait", "attach"]


def test_launch_non_headless_attaches_even_if_wait_times_out():
    """Non-headless launch warns but still attaches if the idle wait times out.

    The wait is advisory: orphaning the session (by refusing to attach)
    would be worse than letting the user inspect a slow-initializing session.
    """
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend") as mock_get_backend,
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None
        mock_wait.return_value = False
        mock_get_backend.return_value.attach_session.return_value = None

        result = runner.invoke(launch, ["--agents", "test-agent", "--yolo"])

        assert result.exit_code == 0
        assert "did not reach idle within 120s" in result.output
        mock_get_backend.return_value.attach_session.assert_called_once_with("test-session")


def test_launch_workspace_confirmation_accepted():
    """Test workspace confirmation is shown for claude_code provider and accepted."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None

        # Provide 'y' input to accept the confirmation prompt
        result = runner.invoke(
            launch,
            ["--agents", "test-agent", "--provider", "claude_code", "--headless"],
            input="y\n",
        )

        assert result.exit_code == 0
        # New prompt format shows tool summary
        assert "launching on claude_code" in result.output
        assert "Allowed:" in result.output
        assert "Proceed?" in result.output
        mock_post.assert_called_once()


def test_launch_workspace_confirmation_declined():
    """Test workspace confirmation declined cancels launch."""
    runner = CliRunner()

    # Provide 'n' input to decline the confirmation prompt
    result = runner.invoke(
        launch, ["--agents", "test-agent", "--provider", "claude_code"], input="n\n"
    )

    assert result.exit_code != 0
    assert "Launch cancelled by user" in result.output


def test_launch_workspace_confirmation_skipped_with_yolo_flag():
    """Test --yolo flag skips workspace confirmation."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None

        result = runner.invoke(
            launch, ["--agents", "test-agent", "--provider", "claude_code", "--headless", "--yolo"]
        )

        assert result.exit_code == 0
        # --yolo shows warning but no confirmation prompt
        assert "Proceed?" not in result.output
        assert "WARNING" in result.output
        mock_post.assert_called_once()


def test_launch_workspace_confirmation_for_default_provider():
    """Test that default provider (kiro_cli) also triggers workspace confirmation."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None

        # Default provider is kiro_cli, which requires workspace confirmation
        result = runner.invoke(launch, ["--agents", "test-agent", "--headless"], input="y\n")

        assert result.exit_code == 0
        assert "launching on kiro_cli" in result.output
        assert "Proceed?" in result.output


def test_launch_yolo_sets_unrestricted_allowed_tools():
    """Test --yolo flag passes allowed_tools=* to the API."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None
        mock_wait.return_value = True

        result = runner.invoke(launch, ["--agents", "test-agent", "--yolo"])

        assert result.exit_code == 0
        call_args = mock_post.call_args
        params = call_args.kwargs["params"]
        assert params["allowed_tools"] == "*"


def test_launch_allowed_tools_override():
    """Test --allowed-tools CLI flag overrides profile defaults."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None

        result = runner.invoke(
            launch,
            [
                "--agents",
                "test-agent",
                "--allowed-tools",
                "@cao-mcp-server",
                "--allowed-tools",
                "fs_read",
                "--headless",
            ],
            input="y\n",
        )

        assert result.exit_code == 0
        call_args = mock_post.call_args
        params = call_args.kwargs["params"]
        assert params["allowed_tools"] == "@cao-mcp-server,fs_read"


def test_launch_builtin_profile_resolves_role_defaults():
    """Test that launching a built-in profile resolves role-based allowedTools."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None

        # code_supervisor is a built-in profile with role=supervisor
        result = runner.invoke(
            launch,
            ["--agents", "code_supervisor", "--headless"],
            input="y\n",
        )

        assert result.exit_code == 0
        call_args = mock_post.call_args
        params = call_args.kwargs["params"]
        # Supervisor should only have MCP server tools
        assert "@cao-mcp-server" in params["allowed_tools"]


def test_launch_headless_message_conductor_not_ready():
    """Test headless+message raises when conductor does not become ready."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None
        mock_wait.return_value = False

        result = runner.invoke(
            launch,
            [
                "--agents",
                "test-agent",
                "--headless",
                "--yolo",
                "do something",
            ],
        )

        assert result.exit_code != 0
        assert "did not become ready" in result.output


def test_launch_headless_message_poll_error_status():
    """Test headless+message raises when terminal reaches error status during poll."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.get") as mock_get,
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
        patch("cli_agent_orchestrator.cli.commands.launch.time.sleep"),
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None
        mock_wait.return_value = True

        poll_resp = MagicMock()
        poll_resp.raise_for_status.return_value = None
        poll_resp.json.return_value = {"status": "error"}
        mock_get.return_value = poll_resp

        result = runner.invoke(
            launch,
            [
                "--agents",
                "test-agent",
                "--headless",
                "--yolo",
                "do something",
            ],
        )

        assert result.exit_code != 0
        assert "ERROR" in result.output


def test_launch_headless_message_poll_processing_then_completed():
    """Test headless+message poll loop sleeps when status is processing before completing."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.get") as mock_get,
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
        patch("cli_agent_orchestrator.cli.commands.launch.time.sleep"),
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None
        mock_wait.return_value = True

        processing_resp = MagicMock()
        processing_resp.raise_for_status.return_value = None
        processing_resp.json.return_value = {"status": "processing"}

        completed_resp = MagicMock()
        completed_resp.raise_for_status.return_value = None
        completed_resp.json.return_value = {"status": "completed"}

        output_resp = MagicMock()
        output_resp.raise_for_status.return_value = None
        output_resp.json.return_value = {"output": "done"}

        mock_get.side_effect = [processing_resp, completed_resp, output_resp]

        result = runner.invoke(
            launch,
            [
                "--agents",
                "test-agent",
                "--headless",
                "--yolo",
                "do something",
            ],
        )

        assert result.exit_code == 0
        assert "done" in result.output


def test_launch_honors_profile_provider_when_flag_not_given():
    """When --provider is not passed, provider is omitted from POST params (server resolves it)."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
        patch(
            "cli_agent_orchestrator.utils.agent_profiles.resolve_provider",
            return_value="claude_code",
        ) as mock_resolve,
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None

        result = runner.invoke(
            launch,
            ["--agents", "code_supervisor", "--headless"],
            input="y\n",
        )

        assert result.exit_code == 0
        mock_resolve.assert_called_once()
        params = mock_post.call_args.kwargs["params"]
        # provider is NOT sent — server-side resolution handles it
        assert "provider" not in params


def test_launch_yolo_still_resolves_profile_provider():
    """--yolo must not swallow ``provider:`` in the agent profile frontmatter.

    Regression guard for #239: ``resolve_provider`` previously lived inside
    the ``else`` branch of the permission-resolution conditional and never
    fired when ``--yolo`` took the first branch, breaking heterogeneous-
    panelist workflows where each agent profile pins a specific CLI.
    """
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
        patch(
            "cli_agent_orchestrator.utils.agent_profiles.resolve_provider",
            return_value="claude_code",
        ) as mock_resolve,
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None
        mock_wait.return_value = True

        result = runner.invoke(launch, ["--agents", "codex_panelist", "--yolo"])

        assert result.exit_code == 0
        # Provider resolution must run even on the --yolo branch.
        mock_resolve.assert_called_once_with("codex_panelist", "kiro_cli")
        # The kiro_cli-specific yolo warning text must NOT appear, because
        # the profile's provider ("claude_code") was honoured.
        assert "consent dialog will be auto-answered" not in result.output


def test_launch_allowed_tools_still_resolves_profile_provider():
    """--allowed-tools must also not swallow ``provider:`` in the profile."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
        patch(
            "cli_agent_orchestrator.utils.agent_profiles.resolve_provider",
            return_value="copilot_cli",
        ) as mock_resolve,
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None

        result = runner.invoke(
            launch,
            [
                "--agents",
                "copilot_panelist",
                "--allowed-tools",
                "fs_read",
                "--headless",
            ],
            input="y\n",
        )

        assert result.exit_code == 0
        mock_resolve.assert_called_once_with("copilot_panelist", "kiro_cli")
        # Local prompts must reflect the resolved provider, not the default.
        assert "launching on copilot_cli" in result.output


def test_launch_explicit_provider_skips_profile_resolution():
    """An explicit --provider flag wins over the profile's provider field.

    ``resolve_provider`` should not be invoked at all when the operator names
    a provider on the command line.
    """
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
        patch("cli_agent_orchestrator.utils.agent_profiles.resolve_provider") as mock_resolve,
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None
        mock_wait.return_value = True

        result = runner.invoke(
            launch,
            ["--agents", "codex_panelist", "--yolo", "--provider", "claude_code"],
        )

        assert result.exit_code == 0
        mock_resolve.assert_not_called()
        # Explicit provider IS sent to the API in this path.
        params = mock_post.call_args.kwargs["params"]
        assert params["provider"] == "claude_code"


def test_launch_yolo_falls_back_to_default_when_profile_lacks_provider():
    """When the profile has no ``provider`` key, ``--yolo`` falls back to
    DEFAULT_PROVIDER. ``resolve_provider`` handles this by returning the
    fallback it was given, so the trailing local fallback is unnecessary."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
        patch(
            "cli_agent_orchestrator.utils.agent_profiles.resolve_provider",
            return_value="kiro_cli",  # fallback returned because profile has no provider
        ),
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None
        mock_wait.return_value = True

        result = runner.invoke(launch, ["--agents", "test-agent", "--yolo"])

        assert result.exit_code == 0
        # The kiro_cli-specific yolo warning IS expected here because the
        # profile didn't override and the fallback is kiro_cli.
        assert "consent dialog will be auto-answered" in result.output


# ── --env forwarded env vars (issue #248) ────────────────────────────


class TestParseEnvPairs:
    """``_parse_env_pairs`` validates --env at the CLI boundary so a value
    that would be silently dropped server-side is rejected up front."""

    def test_valid_pairs_parsed(self):
        assert _parse_env_pairs(["FOO=bar", "AWS_REGION=us-west-2"]) == {
            "FOO": "bar",
            "AWS_REGION": "us-west-2",
        }

    def test_value_with_equals_preserved(self):
        # Split on first '=' only — values may legitimately contain '='.
        assert _parse_env_pairs(["URL=https://x?a=1&b=2"]) == {"URL": "https://x?a=1&b=2"}

    def test_empty_value_allowed(self):
        assert _parse_env_pairs(["EMPTY="]) == {"EMPTY": ""}

    @pytest.mark.parametrize("bad", ["NO_EQUALS", "", "=value_with_no_key"])
    def test_invalid_format_rejected(self, bad):
        import click as _click

        with pytest.raises(_click.ClickException):
            _parse_env_pairs([bad])

    @pytest.mark.parametrize("key", ["1FOO", "FÖÖ", "BAD-KEY", "WITH SPACE"])
    def test_bad_key_rejected(self, key):
        import click as _click

        with pytest.raises(_click.ClickException, match="--env key must match"):
            _parse_env_pairs([f"{key}=x"])

    @pytest.mark.parametrize("blocked", ["CLAUDE_SECRET=x", "CODEX_TOKEN=x", "__MISE_X=y"])
    def test_blocked_prefix_rejected(self, blocked):
        import click as _click

        with pytest.raises(_click.ClickException, match="blocked prefix"):
            _parse_env_pairs([blocked])

    def test_allowlisted_claude_auth_var_passes(self):
        # The 6 CLAUDE_CODE_USE_* / SKIP_* auth vars are explicitly allowed
        # through despite matching the CLAUDE prefix — same allowlist as
        # TmuxClient inherits at the server boundary.
        assert _parse_env_pairs(["CLAUDE_CODE_USE_BEDROCK=1"]) == {"CLAUDE_CODE_USE_BEDROCK": "1"}

    def test_value_at_cap_rejected(self):
        import click as _click

        with pytest.raises(_click.ClickException, match="exceeds 2048 bytes"):
            _parse_env_pairs(["BIG=" + ("x" * 2048)])

    def test_value_under_cap_accepted(self):
        assert _parse_env_pairs(["SMALL=" + ("x" * 2047)]) == {"SMALL": "x" * 2047}

    def test_nul_byte_value_rejected(self):
        # Shared-validator rule now protects --env too (no drift): a NUL value
        # is rejected at the CLI boundary as a --env error.
        import click as _click

        with pytest.raises(_click.ClickException, match="NUL byte"):
            _parse_env_pairs(["TOKEN=bad\x00value"])

    def test_non_utf8_value_rejected(self):
        import click as _click

        with pytest.raises(_click.ClickException, match="not valid UTF-8"):
            _parse_env_pairs(["X=\ud800"])

    def test_too_many_entries_rejected(self):
        import click as _click

        pairs = [f"K{i}=x" for i in range(257)]
        with pytest.raises(_click.ClickException, match="exceeds the limit"):
            _parse_env_pairs(pairs)


def test_launch_forwards_env_in_json_body_not_url():
    """``--env`` values travel in the request body so secrets do not leak
    into cao-server's HTTP access log. Issue #248."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None
        mock_wait.return_value = True

        result = runner.invoke(
            launch,
            [
                "--agents",
                "test-agent",
                "--yolo",
                "--env",
                "MNEMOSYNE_DIR=/root/mnemosyne",
                "--env",
                "ISAAC_CHANNEL=room:engineering",
            ],
        )

        assert result.exit_code == 0
        kwargs = mock_post.call_args.kwargs
        # env vars must NOT appear in query params
        assert "env_vars" not in kwargs["params"]
        assert "MNEMOSYNE_DIR" not in kwargs["params"]
        # env vars travel under the embedded ``env_vars`` body key
        assert kwargs["json"] == {
            "env_vars": {
                "MNEMOSYNE_DIR": "/root/mnemosyne",
                "ISAAC_CHANNEL": "room:engineering",
            }
        }


def test_launch_without_env_omits_request_body():
    """A launch with no --env must not send a JSON body — preserves
    backward compatibility with callers that ignore an unexpected body."""
    runner = CliRunner()

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "id": "test-terminal-id",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None
        mock_wait.return_value = True

        result = runner.invoke(launch, ["--agents", "test-agent", "--yolo"])

        assert result.exit_code == 0
        assert "json" not in mock_post.call_args.kwargs


def test_launch_rejects_blocked_env_prefix_before_calling_api():
    """A blocked --env prefix must fail at the CLI boundary, before any
    POST is issued — operator gets an actionable error instead of a
    silent server-side drop."""
    runner = CliRunner()

    with patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post:
        result = runner.invoke(
            launch,
            ["--agents", "test-agent", "--yolo", "--env", "CLAUDE_SESSION_ID=abc"],
        )

        assert result.exit_code != 0
        assert "blocked prefix" in result.output
        mock_post.assert_not_called()


def test_launch_omp_requires_workspace_confirmation():
    runner = CliRunner()
    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "test-session",
            "name": "test-terminal",
        }
        mock_post.return_value.raise_for_status.return_value = None

        result = runner.invoke(
            launch,
            ["--agents", "test-agent", "--provider", "omp", "--headless"],
            input="y\n",
        )

    assert result.exit_code == 0
    assert "launching on omp" in result.output
    assert "Proceed?" in result.output
    mock_post.assert_called_once()


def test_grok_cli_requires_workspace_access_confirmation():
    from cli_agent_orchestrator.cli.commands.launch import (
        PROVIDERS_REQUIRING_WORKSPACE_ACCESS,
    )

    assert "grok_cli" in PROVIDERS_REQUIRING_WORKSPACE_ACCESS


def test_minimax_code_requires_workspace_access_confirmation():
    from cli_agent_orchestrator.cli.commands.launch import (
        PROVIDERS_REQUIRING_WORKSPACE_ACCESS,
    )

    assert "mcode" in PROVIDERS_REQUIRING_WORKSPACE_ACCESS


# ── Explicit queued Work launch admission (T017 partial) ───────────────────


def _queued_work_args(*extra):
    return [
        "--agents",
        "test-agent",
        "--queue-work",
        "--work-selection",
        "opaque-selection",
        "--session-name",
        "queued-session",
        "--allowed-tools",
        "fs_read",
        "do queued work",
        *extra,
    ]


def test_required_launch_mode_routes_ordinary_cli_to_durable_admission(monkeypatch):
    monkeypatch.setenv("CAO_WORK_LAUNCH_MODE", "required")
    receipt = {
        "work_item_id": "work-required",
        "attempt_id": "attempt-required",
        "generation": 1,
        "state": "queued",
    }
    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            return_value="verified-local-token",
        ),
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_server_settings") as mock_settings,
    ):
        mock_settings.return_value = {"mcp_request_timeout": 9}
        mock_post.return_value.json.return_value = receipt
        result = CliRunner().invoke(
            launch,
            [
                "--agents",
                "test-agent",
                "--session-name",
                "managed-session",
                "--allowed-tools",
                "fs_read",
                "work text",
            ],
        )

    assert result.exit_code == 0, result.output
    assert mock_post.call_args.args[0].endswith("/work-launches")
    assert mock_post.call_args.kwargs["json"] == {
        "agent_profile": "test-agent",
        "session_name": "managed-session",
        "message": "work text",
        "allowed_tools": ["fs_read"],
    }
    assert "work-required" in result.output


def test_required_launch_mode_rejects_missing_bearer_before_network(monkeypatch):
    monkeypatch.setenv("CAO_WORK_LAUNCH_MODE", "required")
    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            return_value=None,
        ),
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
    ):
        result = CliRunner().invoke(
            launch,
            ["--agents", "test-agent", "--session-name", "managed-session", "work text"],
        )

    assert result.exit_code != 0
    assert "bearer token" in result.output
    mock_post.assert_not_called()


def test_required_launch_mode_omits_profile_default_tools(monkeypatch):
    monkeypatch.setenv("CAO_WORK_LAUNCH_MODE", "required")
    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            return_value="token",
        ),
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_server_settings") as mock_settings,
    ):
        mock_settings.return_value = {"mcp_request_timeout": 9}
        mock_post.return_value.json.return_value = {
            "work_item_id": "work-default",
            "attempt_id": "attempt-default",
            "generation": 1,
            "state": "queued",
        }
        result = CliRunner().invoke(
            launch,
            ["--agents", "test-agent", "--session-name", "managed-session", "work text"],
        )

    assert result.exit_code == 0, result.output
    assert mock_post.call_args.kwargs["json"]["allowed_tools"] == []


def test_unknown_launch_mode_rejects_explicit_queue_before_network(monkeypatch):
    monkeypatch.setenv("CAO_WORK_LAUNCH_MODE", "unrecognized")
    with patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post:
        result = CliRunner().invoke(launch, _queued_work_args())

    assert result.exit_code != 0
    assert "CAO_WORK_LAUNCH_MODE" in result.output
    mock_post.assert_not_called()


def test_queue_work_launch_posts_intent_and_displays_only_receipt():
    runner = CliRunner()
    receipt = {
        "work_item_id": "work-123",
        "attempt_id": "attempt-456",
        "generation": 1,
        "state": "queued",
    }

    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            create=True,
            return_value="verified-local-token",
        ) as mock_bearer,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.get") as mock_get,
        patch("cli_agent_orchestrator.cli.commands.launch.get_server_settings") as mock_settings,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend") as mock_backend,
        patch("cli_agent_orchestrator.cli.commands.launch.sync_backend_from_server") as mock_sync,
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
        patch("cli_agent_orchestrator.utils.agent_profiles.resolve_provider") as mock_provider,
    ):
        mock_settings.return_value = {"mcp_request_timeout": 9}
        mock_post.return_value.json.return_value = receipt
        mock_post.return_value.raise_for_status.return_value = None

        result = runner.invoke(launch, _queued_work_args())

    assert result.exit_code == 0, result.output
    mock_bearer.assert_called_once()
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0].endswith("/work-launches")
    assert mock_post.call_args.kwargs["headers"] == {"Authorization": "Bearer verified-local-token"}
    assert mock_post.call_args.kwargs["timeout"] == 9
    assert mock_post.call_args.kwargs["json"] == {
        "selection": "opaque-selection",
        "agent_profile": "test-agent",
        "session_name": "queued-session",
        "message": "do queued work",
        "allowed_tools": ["fs_read"],
    }
    for value in receipt.values():
        assert str(value) in result.output
    assert "Session created:" not in result.output
    assert "Terminal created:" not in result.output
    assert "ACK" not in result.output
    mock_get.assert_not_called()
    mock_backend.assert_not_called()
    mock_sync.assert_not_called()
    mock_wait.assert_not_called()
    mock_provider.assert_not_called()


def test_queue_work_launch_allows_omitted_selector_and_posts_only_intent():
    runner = CliRunner()
    receipt = {
        "work_item_id": "work-789",
        "attempt_id": "attempt-012",
        "generation": 1,
        "state": "queued",
    }
    args = [
        "--agents",
        "test-agent",
        "--queue-work",
        "--session-name",
        "queued-session",
        "--allowed-tools",
        "fs_read",
        "do queued work",
    ]

    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            create=True,
            return_value="verified-local-token",
        ),
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_server_settings") as mock_settings,
    ):
        mock_settings.return_value = {"mcp_request_timeout": 9}
        mock_post.return_value.json.return_value = receipt
        mock_post.return_value.raise_for_status.return_value = None

        result = runner.invoke(launch, args)

    assert result.exit_code == 0, result.output
    assert mock_post.call_args.args[0].endswith("/work-launches")
    assert mock_post.call_args.kwargs["json"] == {
        "agent_profile": "test-agent",
        "session_name": "queued-session",
        "message": "do queued work",
        "allowed_tools": ["fs_read"],
    }
    assert "selection" not in mock_post.call_args.kwargs["json"]
    assert "work-789" in result.output
    assert "attempt-012" in result.output
    assert "queued" in result.output


def test_queue_work_launch_requires_bearer_before_network():
    runner = CliRunner()

    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            create=True,
            return_value=None,
        ),
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
    ):
        result = runner.invoke(launch, _queued_work_args())

    assert result.exit_code != 0
    assert "bearer" in result.output.lower() or "auth" in result.output.lower()
    mock_post.assert_not_called()


def test_queue_work_launch_requires_session_name_before_bearer_or_network():
    runner = CliRunner()
    args = [
        "--agents",
        "test-agent",
        "--queue-work",
        "--work-selection",
        "opaque-selection",
        "do queued work",
    ]

    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            create=True,
        ) as mock_bearer,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
    ):
        result = runner.invoke(launch, args)

    assert result.exit_code != 0
    assert "--session-name" in result.output
    mock_bearer.assert_not_called()
    mock_post.assert_not_called()


def test_queue_work_launch_requires_message_before_bearer_or_network():
    runner = CliRunner()
    args = [
        "--agents",
        "test-agent",
        "--queue-work",
        "--work-selection",
        "opaque-selection",
        "--session-name",
        "queued-session",
    ]

    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            create=True,
        ) as mock_bearer,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
    ):
        result = runner.invoke(launch, args)

    assert result.exit_code != 0
    assert "message is required" in result.output.lower()
    mock_bearer.assert_not_called()
    mock_post.assert_not_called()


def test_queue_work_launch_never_falls_back_to_legacy_after_error():
    import requests

    runner = CliRunner()

    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            create=True,
            return_value="verified-local-token",
        ),
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend") as mock_backend,
        patch("cli_agent_orchestrator.cli.commands.launch.sync_backend_from_server") as mock_sync,
        patch("cli_agent_orchestrator.cli.commands.launch.wait_until_terminal_status") as mock_wait,
    ):
        mock_post.side_effect = requests.exceptions.ConnectionError("offline")

        result = runner.invoke(launch, _queued_work_args())

    assert result.exit_code != 0
    assert "Failed to connect to cao-server" in result.output
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0].endswith("/work-launches")
    mock_backend.assert_not_called()
    mock_sync.assert_not_called()
    mock_wait.assert_not_called()


@pytest.mark.parametrize(
    ("status_code", "code", "message", "required_action"),
    [
        (
            409,
            "launch_idempotency_conflict",
            "Launch operation conflicts with an existing server-owned identity.",
            "inspect_existing_launch",
        ),
        (
            503,
            "launch_runtime_unavailable",
            "Trusted launch runtime is unavailable.",
            "inspect_server_configuration",
        ),
    ],
)
def test_queue_work_http_error_displays_server_message_and_required_action(
    status_code, code, message, required_action
):
    import requests

    detail = {
        "code": code,
        "message": message,
        "retryable": False,
        "required_action": required_action,
    }
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {"detail": detail}
    http_error = requests.exceptions.HTTPError(f"HTTP {status_code}", response=response)

    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            create=True,
            return_value="verified-local-token",
        ),
        patch("cli_agent_orchestrator.cli.commands.launch.get_server_settings") as mock_settings,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend") as mock_backend,
    ):
        mock_settings.return_value = {"mcp_request_timeout": 9}
        mock_post.return_value.raise_for_status.side_effect = http_error

        result = CliRunner().invoke(launch, _queued_work_args())

    assert result.exit_code != 0
    assert message in result.output
    assert required_action in result.output
    assert "Failed to connect to cao-server" not in result.output
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0].endswith("/work-launches")
    mock_backend.assert_not_called()


@pytest.mark.parametrize(
    ("body", "invalid_json"),
    [
        ({"detail": "Work admission is unavailable."}, False),
        ({"error": "Work admission is unavailable."}, False),
        ({"detail": {"message": "Missing action"}}, False),
        (None, True),
    ],
    ids=["detail-is-text", "detail-missing", "action-missing", "invalid-json"],
)
def test_queue_work_unstructured_http_error_reports_rejection_status(body, invalid_json):
    import requests

    response = MagicMock()
    response.status_code = 503
    if invalid_json:
        response.json.side_effect = ValueError("invalid JSON")
    else:
        response.json.return_value = body
    http_error = requests.exceptions.HTTPError("HTTP 503", response=response)

    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            create=True,
            return_value="verified-local-token",
        ),
        patch("cli_agent_orchestrator.cli.commands.launch.get_server_settings") as mock_settings,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend") as mock_backend,
    ):
        mock_settings.return_value = {"mcp_request_timeout": 9}
        mock_post.return_value.raise_for_status.side_effect = http_error

        result = CliRunner().invoke(launch, _queued_work_args())

    assert result.exit_code != 0
    assert "cao-server rejected queued Work (HTTP 503)" in result.output
    assert "Failed to connect to cao-server" not in result.output
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0].endswith("/work-launches")
    mock_backend.assert_not_called()


@pytest.mark.parametrize(
    "detail",
    [
        {
            "code": "private_launch_failure",
            "message": "PRIVATE_LAUNCH_DETAIL_7F3A\x1b[31m",
            "retryable": False,
            "required_action": "leak_PRIVATE_LAUNCH_DETAIL_7F3A\x1b[0m",
        },
        {
            "code": "launch_runtime_unavailable",
            "message": "PRIVATE_LAUNCH_DETAIL_7F3A\x1b[31m",
            "retryable": False,
            "required_action": "leak_PRIVATE_LAUNCH_DETAIL_7F3A\x1b[0m",
        },
        {
            "code": "launch_runtime_unavailable",
            "message": "Trusted launch runtime is unavailable.",
            "retryable": True,
            "required_action": "inspect_server_configuration",
        },
        {
            "code": "launch_runtime_unavailable",
            "message": "Trusted launch runtime is unavailable.",
            "retryable": False,
            "required_action": "inspect_server_configuration",
            "debug": "PRIVATE_LAUNCH_DETAIL_7F3A\x1b[31m",
        },
    ],
    ids=["unknown-envelope", "known-code-private-message", "wrong-retryable", "extra-field"],
)
def test_queue_work_http_error_hides_details_outside_safe_envelopes(detail):
    import requests

    response = MagicMock()
    response.status_code = 503
    response.json.return_value = {"detail": detail}
    http_error = requests.exceptions.HTTPError("HTTP 503", response=response)

    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            create=True,
            return_value="verified-local-token",
        ),
        patch("cli_agent_orchestrator.cli.commands.launch.get_server_settings") as mock_settings,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
    ):
        mock_settings.return_value = {"mcp_request_timeout": 9}
        mock_post.return_value.raise_for_status.side_effect = http_error

        result = CliRunner().invoke(launch, _queued_work_args())

    assert result.exit_code != 0
    assert "cao-server rejected queued Work (HTTP 503)" in result.output
    assert "PRIVATE_LAUNCH_DETAIL_7F3A" not in result.output
    assert "\x1b" not in result.output
    assert "Trusted launch runtime is unavailable." not in result.output
    assert "inspect_server_configuration" not in result.output


def test_legacy_launch_http_error_keeps_connection_error_message():
    import requests

    response = MagicMock()
    response.status_code = 503
    response.json.return_value = {
        "detail": {
            "message": "Trusted launch runtime is unavailable.",
            "required_action": "inspect_server_configuration",
        }
    }
    http_error = requests.exceptions.HTTPError("HTTP 503", response=response)

    with (
        patch("cli_agent_orchestrator.cli.commands.launch.get_server_settings") as mock_settings,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
    ):
        mock_settings.return_value = {"mcp_request_timeout": 9}
        mock_post.return_value.raise_for_status.side_effect = http_error

        result = CliRunner().invoke(
            launch,
            ["--agents", "test-agent", "--provider", "claude_code", "--yolo"],
        )

    assert result.exit_code != 0
    assert "Failed to connect to cao-server" in result.output
    assert "inspect_server_configuration" not in result.output


@pytest.mark.parametrize("generation", [0, -1])
def test_queue_work_rejects_nonpositive_generation_receipt(generation):
    receipt = {
        "work_item_id": "work-123",
        "attempt_id": "attempt-456",
        "generation": generation,
        "state": "queued",
    }

    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            create=True,
            return_value="verified-local-token",
        ),
        patch("cli_agent_orchestrator.cli.commands.launch.get_server_settings") as mock_settings,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
    ):
        mock_settings.return_value = {"mcp_request_timeout": 9}
        mock_post.return_value.json.return_value = receipt
        mock_post.return_value.raise_for_status.return_value = None

        result = CliRunner().invoke(launch, _queued_work_args())

    assert result.exit_code != 0
    assert "invalid queued Work receipt" in result.output
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0].endswith("/work-launches")


def test_launch_without_queue_work_keeps_legacy_sessions_endpoint():
    runner = CliRunner()

    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            create=True,
        ) as mock_bearer,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
    ):
        mock_post.return_value.json.return_value = {
            "session_name": "legacy-session",
            "id": "terminal-id",
            "name": "terminal-name",
        }
        mock_post.return_value.raise_for_status.return_value = None

        result = runner.invoke(launch, ["--agents", "test-agent", "--headless", "--yolo"])

    assert result.exit_code == 0
    assert mock_post.call_args.args[0].endswith("/sessions")
    mock_bearer.assert_called_once()


def test_launch_help_describes_queued_work_as_admission_only():
    result = CliRunner().invoke(launch, ["--help"])

    assert result.exit_code == 0
    assert "--queue-work" in result.output
    assert "queued" in result.output.lower()
    assert "provider" in result.output.lower()


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--provider", "claude_code"),
        ("--engine", "v2"),
        ("--headless", None),
        ("--async", None),
        ("--auto-approve", None),
        ("--yolo", None),
        ("--working-directory", "/tmp/work"),
        ("--memory", None),
        ("--env", "KEY=value"),
        ("--resume-session-id", "resume-123"),
    ],
)
def test_queue_work_rejects_legacy_launch_options_before_auth_or_network(option, value):
    args = _queued_work_args()
    args.extend([option] if value is None else [option, value])

    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            create=True,
        ) as mock_bearer,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
    ):
        result = CliRunner().invoke(launch, args)

    assert result.exit_code != 0
    assert option in result.output
    assert "--queue-work" in result.output
    mock_bearer.assert_not_called()
    mock_post.assert_not_called()


def test_work_selection_without_queue_work_is_rejected_before_auth_or_network():
    args = [
        "--agents",
        "test-agent",
        "--work-selection",
        "opaque-selection",
        "--session-name",
        "queued-session",
        "do queued work",
    ]

    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            create=True,
        ) as mock_bearer,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
    ):
        result = CliRunner().invoke(launch, args)

    assert result.exit_code != 0
    assert "--work-selection requires --queue-work" in result.output
    mock_bearer.assert_not_called()
    mock_post.assert_not_called()


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("--agents", "invalid profile"),
        ("--agents", "a" * 129),
        ("--work-selection", "invalid selector"),
        ("--work-selection", "s" * 129),
        ("--session-name", "invalid session"),
        ("--session-name", "s" * 129),
        ("--allowed-tools", "invalid tool"),
        ("--allowed-tools", "t" * 129),
        ("--allowed-tools", "*"),
        ("MESSAGE", "m" * 32769),
    ],
    ids=[
        "profile-invalid-characters",
        "profile-too-long",
        "selection-invalid-characters",
        "selection-too-long",
        "session-invalid-characters",
        "session-too-long",
        "tool-invalid-characters",
        "tool-too-long",
        "wildcard-tool",
        "message-too-long",
    ],
)
def test_queue_work_rejects_invalid_identity_or_oversize_message_before_auth(field, invalid_value):
    args = _queued_work_args()
    if field == "MESSAGE":
        args[-1] = invalid_value
    elif field == "--allowed-tools":
        args[args.index(field) + 1] = invalid_value
    else:
        args[args.index(field) + 1] = invalid_value

    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            create=True,
        ) as mock_bearer,
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
    ):
        result = CliRunner().invoke(launch, args)

    assert result.exit_code != 0
    assert field.lower() in result.output.lower() or "invalid" in result.output.lower()
    mock_bearer.assert_not_called()
    mock_post.assert_not_called()


def test_queue_work_accepts_identity_and_message_maximum_lengths():
    receipt = {
        "work_item_id": "work-123",
        "attempt_id": "attempt-456",
        "generation": 1,
        "state": "queued",
    }
    args = [
        "--agents",
        "a" * 128,
        "--queue-work",
        "--work-selection",
        "s" * 128,
        "--session-name",
        "n" * 128,
        "--allowed-tools",
        "fs_read",
        "m" * 32768,
    ]

    with (
        patch(
            "cli_agent_orchestrator.cli.commands.launch.get_local_bearer",
            create=True,
            return_value="verified-local-token",
        ),
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_server_settings") as mock_settings,
    ):
        mock_settings.return_value = {"mcp_request_timeout": 9}
        mock_post.return_value.json.return_value = receipt
        mock_post.return_value.raise_for_status.return_value = None

        result = CliRunner().invoke(launch, args)

    assert result.exit_code == 0, result.output
    assert mock_post.call_args.kwargs["json"]["agent_profile"] == "a" * 128
    assert mock_post.call_args.kwargs["json"]["selection"] == "s" * 128
    assert mock_post.call_args.kwargs["json"]["session_name"] == "n" * 128
    assert mock_post.call_args.kwargs["json"]["message"] == "m" * 32768
