"""CAO-only policy metadata must never leak into another MCP launch."""

import copy
import json
import sys

import pytest

from cli_agent_orchestrator.providers.mcp_interactions import overlay_interaction_capability_file

PROFILE_ENV = "CAO_INTERNAL_MCP_TOOL_PROFILE"
FILTER_ENV = "CAO_INTERNAL_MCP_ENABLED_TOOLS"

pytestmark = pytest.mark.offline_database


def test_selected_profile_is_pinned_and_client_filters_are_preserved():
    source = {
        "cao-mcp-server": {
            "command": "cao-mcp-server",
            "cao_tool_profile": "collaboration",
            "enabled_tools": [],
            "env": {PROFILE_ENV: "unrestricted"},
        },
        "other": {
            "command": "other",
            "env": {PROFILE_ENV: "collaboration"},
            "environment": {PROFILE_ENV: "collaboration"},
        },
    }
    before = copy.deepcopy(source)
    result = overlay_interaction_capability_file(source, None)
    assert source == before
    cao = result["cao-mcp-server"]
    assert cao["command"] == sys.executable
    assert cao["args"] == ["-m", "cli_agent_orchestrator.mcp_server.server"]
    assert cao["env"][PROFILE_ENV] == "collaboration"
    assert cao["enabled_tools"] == []
    assert "cao_tool_profile" not in cao
    assert "cao_tool_profile" not in result["other"]
    assert PROFILE_ENV not in result["other"]["env"]
    assert PROFILE_ENV not in result["other"]["environment"]


@pytest.mark.parametrize("value", [None, "", "*", "coordinator", [], {}, 1])
def test_invalid_explicit_profile_is_rejected(value):
    with pytest.raises(ValueError, match="tool profile"):
        overlay_interaction_capability_file(
            {
                "cao": {
                    "command": "cao-mcp-server",
                    "cao_tool_profile": value,
                }
            },
            None,
        )


def test_list_command_and_partial_filter_and_terminal_isolation():
    source = {
        "cao": {
            "command": ["cao-mcp-server"],
            "cao_tool_profile": "interaction",
            "enabled_tools": ["cao_ask_user"],
            "environment": {PROFILE_ENV: "collaboration"},
        }
    }
    before = copy.deepcopy(source)
    first = overlay_interaction_capability_file(source, "/private/first")
    assert source == before
    assert first["cao"]["environment"][PROFILE_ENV] == "interaction"
    assert json.loads(first["cao"]["environment"][FILTER_ENV]) == ["cao_ask_user"]
    assert "enabled_tools" not in first["cao"]
    source["cao"]["cao_tool_profile"] = "collaboration"
    source["cao"]["enabled_tools"] = []
    second = overlay_interaction_capability_file(source, "/private/second")
    assert second["cao"]["environment"][PROFILE_ENV] == "collaboration"
    assert json.loads(second["cao"]["environment"][FILTER_ENV]) == []
    assert "enabled_tools" not in second["cao"]
    assert first["cao"]["environment"][PROFILE_ENV] == "interaction"
    assert json.loads(first["cao"]["environment"][FILTER_ENV]) == ["cao_ask_user"]
    assert first["cao"]["environment"]["CAO_INTERACTION_CAPABILITY_FILE"] == "/private/first"
    assert second["cao"]["environment"]["CAO_INTERACTION_CAPABILITY_FILE"] == "/private/second"


def test_forged_env_without_metadata_cannot_select_profile():
    result = overlay_interaction_capability_file(
        {
            "cao": {
                "command": "cao-mcp-server",
                "env": {PROFILE_ENV: "collaboration"},
            }
        },
        None,
    )
    assert PROFILE_ENV not in result["cao"]["env"]


def test_codex_override_cannot_remove_selected_policy_without_interaction_capability(monkeypatch):
    from cli_agent_orchestrator.models.agent_profile import AgentProfile
    from cli_agent_orchestrator.providers.codex import CodexProvider

    profile = AgentProfile(
        name="test",
        description="test",
        mcpServers={
            "cao": {"command": "cao-mcp-server", "cao_tool_profile": "interaction"},
        },
        codexConfig={"mcp_servers.cao.command": "unrestricted-server"},
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.providers.codex.load_agent_profile", lambda _: profile
    )
    provider = CodexProvider("test1234", "session", "window", "test")
    with pytest.raises(ValueError, match="cannot override MCP configuration"):
        provider._build_codex_command()


@pytest.mark.parametrize(
    "config",
    [
        {"command": "cao-mcp-server", "args": ["--extra"]},
        {"command": "/untrusted/cao-mcp-server"},
        {"command": "uvx", "args": ["cao-mcp-server"]},
        {"command": "other"},
    ],
)
def test_profile_on_untrusted_command_fails_instead_of_launching_unrestricted(config):
    with pytest.raises(ValueError, match="tool profile"):
        overlay_interaction_capability_file(
            {"cao": {**config, "cao_tool_profile": "interaction"}}, None
        )


def test_claude_command_builder_rejects_invalid_profile_before_launch(monkeypatch):
    from cli_agent_orchestrator.models.agent_profile import AgentProfile
    from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider

    profile = AgentProfile(
        name="test",
        description="test",
        mcpServers={
            "cao": {"command": "cao-mcp-server", "cao_tool_profile": "unknown"},
        },
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.providers.claude_code.load_agent_profile", lambda _: profile
    )
    provider = ClaudeCodeProvider("test1234", "session", "window", "test")
    with pytest.raises(ValueError, match="tool profile"):
        provider._build_claude_command()


@pytest.mark.parametrize("tools", [[], ["assign"], ["send_message", "cao_ask_user"]])
def test_profile_never_broadens_explicit_client_allowlist(tools):
    result = overlay_interaction_capability_file(
        {
            "cao": {
                "command": "cao-mcp-server",
                "cao_tool_profile": "interaction",
                "enabled_tools": tools,
            }
        },
        None,
    )
    assert result["cao"]["enabled_tools"] == tools


@pytest.mark.parametrize("adapter", ["codex", "claude_code"])
def test_text_command_environment_cannot_drop_policy_in_provider_builder(monkeypatch, adapter):
    from cli_agent_orchestrator.models.agent_profile import AgentProfile
    from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider
    from cli_agent_orchestrator.providers.codex import CodexProvider

    profile = AgentProfile(
        name="test",
        description="test",
        mcpServers={
            "cao": {
                "command": "cao-mcp-server",
                "environment": {},
                "cao_tool_profile": "interaction",
            },
        },
    )
    monkeypatch.setattr(
        f"cli_agent_orchestrator.providers.{adapter}.load_agent_profile", lambda _: profile
    )
    provider_type = CodexProvider if adapter == "codex" else ClaudeCodeProvider
    provider = provider_type("test1234", "session", "window", "test")
    build = provider._build_codex_command if adapter == "codex" else provider._build_claude_command
    with pytest.raises(ValueError, match="requires env for a text command"):
        build()


@pytest.mark.parametrize("adapter", ["codex", "claude_code"])
def test_array_command_profile_cannot_be_lost_by_text_command_adapter(monkeypatch, adapter):
    from cli_agent_orchestrator.models.agent_profile import AgentProfile
    from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider
    from cli_agent_orchestrator.providers.codex import CodexProvider

    profile = AgentProfile(
        name="test",
        description="test",
        mcpServers={
            "cao": {
                "command": ["cao-mcp-server"],
                "environment": {},
                "cao_tool_profile": "interaction",
            },
        },
    )
    monkeypatch.setattr(
        f"cli_agent_orchestrator.providers.{adapter}.load_agent_profile", lambda _: profile
    )
    provider_type = CodexProvider if adapter == "codex" else ClaudeCodeProvider
    provider = provider_type("test1234", "session", "window", "test")
    build = provider._build_codex_command if adapter == "codex" else provider._build_claude_command
    with pytest.raises(ValueError, match="tool profile requires a text command"):
        build()


@pytest.mark.parametrize("adapter", ["codex", "claude_code"])
def test_valid_text_command_builder_serializes_selected_policy(monkeypatch, adapter):
    import json
    import shlex
    from pathlib import Path

    from cli_agent_orchestrator.models.agent_profile import AgentProfile
    from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider
    from cli_agent_orchestrator.providers.codex import CodexProvider

    profile = AgentProfile(
        name="test",
        description="test",
        mcpServers={
            "cao": {"command": "cao-mcp-server", "env": {}, "cao_tool_profile": "interaction"},
        },
    )
    monkeypatch.setattr(
        f"cli_agent_orchestrator.providers.{adapter}.load_agent_profile", lambda _: profile
    )
    provider_type = CodexProvider if adapter == "codex" else ClaudeCodeProvider
    provider = provider_type("test1234", "session", "window", "test")
    command = (
        provider._build_codex_command() if adapter == "codex" else provider._build_claude_command()
    )
    args = shlex.split(command)
    if adapter == "codex":
        assert any(PROFILE_ENV in arg and '"interaction"' in arg for arg in args)
    else:
        config_path = Path(args[args.index("--mcp-config") + 1])
        try:
            assert (
                json.loads(config_path.read_text())["mcpServers"]["cao"]["env"][PROFILE_ENV]
                == "interaction"
            )
        finally:
            config_path.unlink()
