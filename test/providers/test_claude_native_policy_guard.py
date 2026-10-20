"""Native routing must not silently discard an explicitly requested CAO policy."""

import asyncio
import copy
import json
import shlex
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cli_agent_orchestrator.models.agent_profile import AgentProfile, McpServer
from cli_agent_orchestrator.providers import claude_code
from cli_agent_orchestrator.utils.mcp_tool_policy import MCP_TOOL_FILTER_ENV, MCP_TOOL_PROFILE_ENV

pytestmark = pytest.mark.offline_database


class PolicyServer(McpServer):
    cao_tool_profile: Any = None


@pytest.fixture
def provider(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_code, "CAO_HOME_DIR", tmp_path)
    return claude_code.ClaudeCodeProvider("test1234", "session", "window", "wrapper")


def native_profile(servers):
    return AgentProfile(
        name="wrapper",
        description="test",
        native_agent="native-guard",
        mcpServers=servers,
        system_prompt="秘密 prompt",
        model="profile-model",
    )


@pytest.mark.parametrize(
    "selection", ["interaction", "collaboration", None, "秘密 unknown", [], {}]
)
@pytest.mark.parametrize("model_entry", [False, True])
@pytest.mark.parametrize("server_name", ["cao-mcp-server", "other"])
def test_native_agent_refuses_explicit_policy_presence_without_artifacts(
    provider, tmp_path, selection, model_entry, server_name
):
    data = {
        "command": "cao-mcp-server" if server_name == "cao-mcp-server" else "untrusted",
        "cao_tool_profile": selection,
    }
    entry = PolicyServer(**data) if model_entry else data
    profile = native_profile({server_name: entry})
    before = copy.deepcopy(profile.model_dump())
    with pytest.raises(ValueError, match="native_agent") as caught:
        provider._build_claude_command(profile)
    assert "秘密" not in str(caught.value)
    assert profile.model_dump() == before
    assert not list(tmp_path.rglob("*"))


@pytest.mark.parametrize("tools", [None, [], ["*"], ["@cao-mcp-server"]])
def test_native_agent_policy_refusal_is_independent_of_native_grants(monkeypatch, tmp_path, tools):
    monkeypatch.setattr(claude_code, "CAO_HOME_DIR", tmp_path)
    provider = claude_code.ClaudeCodeProvider(
        "test1234", "session", "window", "wrapper", allowed_tools=tools
    )
    with pytest.raises(ValueError, match="native_agent"):
        provider._build_claude_command(
            native_profile(
                {"cao": {"command": "cao-mcp-server", "cao_tool_profile": "interaction"}}
            )
        )


@pytest.mark.parametrize("selection", ["interaction", "collaboration"])
def test_initialize_rejects_native_agent_policy_before_sending_backend_input(
    provider, monkeypatch, selection
):
    from cli_agent_orchestrator.services.status_monitor import status_monitor

    profile = native_profile({"cao": {"command": "cao-mcp-server", "cao_tool_profile": selection}})
    loader = MagicMock(return_value=profile)
    monkeypatch.setattr(provider, "_load_profile", loader)
    monkeypatch.setattr(claude_code, "wait_for_shell", AsyncMock(return_value=True))
    monkeypatch.setattr(provider, "_ensure_skip_bypass_prompt_setting", MagicMock())
    backend = MagicMock()
    backend.send_keys.side_effect = AssertionError("unsafe native command sent")
    monkeypatch.setattr(claude_code, "get_backend", lambda: backend)
    had_own_notification = "notify_input_sent" in vars(status_monitor)
    with patch.object(status_monitor, "notify_input_sent") as notification:
        with pytest.raises(ValueError, match="native_agent"):
            asyncio.run(provider.initialize())
        notification.assert_not_called()
    assert ("notify_input_sent" in vars(status_monitor)) is had_own_notification
    loader.assert_called_once_with()
    backend.send_keys.assert_not_called()


@pytest.mark.parametrize(
    "servers",
    [
        None,
        {},
        {"legacy": {"command": "untrusted", "enabled_tools": []}},
        {"legacy": McpServer(command="untrusted")},
    ],
)
def test_native_agent_legacy_routing_without_policy_is_preserved(provider, servers):
    args = shlex.split(provider._build_claude_command(native_profile(servers)))
    assert args[args.index("--agent") + 1] == "native-guard"
    assert "--mcp-config" not in args
    assert "--model" not in args
    assert "--append-system-prompt-file" not in args


def test_missing_cao_profile_still_routes_to_native_agent(provider):
    args = shlex.split(provider._build_claude_command(None))
    assert args[args.index("--agent") + 1] == "wrapper"
    assert "--mcp-config" not in args


def test_full_cao_profile_still_materializes_its_explicit_policy(provider):
    profile = AgentProfile(
        name="full",
        description="test",
        mcpServers={
            "cao": {
                "command": "cao-mcp-server",
                "cao_tool_profile": "collaboration",
                "enabled_tools": ["list_siblings"],
            }
        },
    )
    args = shlex.split(provider._build_claude_command(profile))
    assert "--agent" not in args
    path = Path(args[args.index("--mcp-config") + 1])
    config = json.loads(path.read_text())["mcpServers"]["cao"]
    assert config["env"][MCP_TOOL_PROFILE_ENV] == "collaboration"
    assert json.loads(config["env"][MCP_TOOL_FILTER_ENV]) == ["list_siblings"]
