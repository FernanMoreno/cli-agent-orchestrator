from unittest.mock import patch

from click.testing import CliRunner

from cli_agent_orchestrator.cli.commands.launch import launch


def _restricted_launch(provider: str):
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
        return runner.invoke(
            launch,
            [
                "--agents",
                "test-agent",
                "--provider",
                provider,
                "--allowed-tools",
                "fs_read",
                "--headless",
            ],
            input="y\n",
        )


def test_launch_gate_names_native_enforcement():
    result = _restricted_launch("claude_code")
    assert result.exit_code == 0
    assert "Enforcement: native" in result.output
    assert "WARNING: this provider does not enforce" not in result.output


def test_launch_gate_warns_when_the_provider_cannot_enforce():
    """A restricted profile on cursor_cli runs unrestricted; the gate must say so
    instead of showing a Blocked list that looks real."""
    result = _restricted_launch("cursor_cli")
    assert result.exit_code == 0
    assert "Enforcement: none" in result.output
    assert "WARNING: this provider does not enforce the Blocked list" in result.output
    # No TOOL_MAPPING entry means an empty deny list; it must not read as "(none)".
    assert "Blocked:  (none)" not in result.output
    assert "not translated for this provider" in result.output


def test_launch_gate_qualifies_opencode_install_time_enforcement():
    """opencode enforces the permission block `cao install` wrote and ignores the
    list resolved at launch, and it has no TOOL_MAPPING, so the deny list is
    always empty. The gate must not attach the native promise to the requested
    list or print an empty Blocked list as `(none)`."""
    result = _restricted_launch("opencode_cli")
    assert result.exit_code == 0
    assert "Enforcement: native at install time" in result.output
    assert "launch overrides do not change it" in result.output
    assert "Blocked:  (none)" not in result.output
    assert "set at install time from the installed agent's policy" in result.output
    assert "WARNING: this provider does not enforce" not in result.output


def test_launch_gate_on_unrestricted_opencode_still_names_the_installed_policy():
    """An unrestricted request does not loosen opencode either: what runs is the
    installed agent's permission block, so the gate says so for ["*"] too."""
    runner = CliRunner()
    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
    ):
        mock_post.return_value.json.return_value = {"session_name": "s", "name": "t"}
        mock_post.return_value.raise_for_status.return_value = None
        result = runner.invoke(
            launch,
            ["--agents", "a", "--provider", "opencode_cli", "--allowed-tools", "*", "--headless"],
            input="y\n",
        )
    assert result.exit_code == 0, result.output
    assert "Enforcement: native at install time" in result.output
    assert "set at install time from the installed agent's policy" in result.output


def test_describe_enforcement_distinguishes_install_time_from_runtime_native():
    from cli_agent_orchestrator.utils.enforcement import describe_enforcement, is_install_time

    assert is_install_time("opencode_cli") is True
    assert is_install_time("claude_code") is False
    assert describe_enforcement("opencode_cli", ["fs_read"]).startswith("native at install time")
    assert (
        describe_enforcement("claude_code", ["fs_read"])
        == "native (the provider refuses blocked tools)"
    )
    # An unrestricted request on opencode still describes where the policy lives.
    assert describe_enforcement("opencode_cli", ["*"]).startswith("native at install time")


def test_launch_gate_marks_prompt_only_enforcement():
    result = _restricted_launch("codex")
    assert result.exit_code == 0
    assert "Enforcement: prompt-only" in result.output
    assert "WARNING: this provider does not enforce" in result.output


def test_auto_approve_help_does_not_claim_enforcement():
    runner = CliRunner()
    result = runner.invoke(launch, ["--help"])
    flat = " ".join(result.output.split())  # click re-wraps help text
    assert "restrictions still enforced" not in flat
    assert "Does not change the tool policy" in flat


def test_launch_gate_names_kiro_install_time_enforcement():
    """Kiro enforces the installed agent JSON's `tools`, written by `cao install`
    from the profile; the list resolved at launch does not change it. The gate
    says so and prints no "does not enforce" warning for a restricted profile."""
    with patch(
        "cli_agent_orchestrator.cli.commands.launch.kiro_install_predates_native_enforcement",
        return_value=False,
    ):
        result = _restricted_launch("kiro_cli")
    assert result.exit_code == 0
    assert "Enforcement: native at install time" in result.output
    assert "set at install time from the installed agent's policy" in result.output
    assert "WARNING: this provider does not enforce" not in result.output
    assert "WARNING: the installed Kiro agent" not in result.output


def test_launch_gate_warns_when_the_installed_kiro_agent_predates_native_enforcement():
    """An agent JSON with tools ["*"] was written before CAO put the policy into
    `tools`; the restriction the gate prints is not what the agent runs with, so
    the gate must say so and name the reinstall command."""
    with patch(
        "cli_agent_orchestrator.cli.commands.launch.kiro_install_predates_native_enforcement",
        return_value=True,
    ) as stale:
        result = _restricted_launch("kiro_cli")
    assert result.exit_code == 0
    stale.assert_called_once_with("test-agent", ["fs_read"])
    assert "WARNING: the installed Kiro agent 'test-agent' has tools" in result.output
    assert "cao install test-agent --provider kiro_cli" in result.output


def test_launch_yolo_on_kiro_says_it_does_not_widen_the_tool_set():
    runner = CliRunner()
    with (
        patch("cli_agent_orchestrator.cli.commands.launch.requests.post") as mock_post,
        patch("cli_agent_orchestrator.cli.commands.launch.get_backend"),
    ):
        mock_post.return_value.json.return_value = {"session_name": "s", "name": "t"}
        mock_post.return_value.raise_for_status.return_value = None
        result = runner.invoke(
            launch,
            ["--agents", "a", "--provider", "kiro_cli", "--yolo", "--headless"],
        )
    assert result.exit_code == 0, result.output
    assert "--yolo does not widen kiro_cli's tool set" in result.output
    assert "re-run 'cao install'" in result.output
