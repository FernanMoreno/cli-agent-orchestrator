"""Profiled Claude client filters must reach actual MCP catalog and dispatch."""

import copy
import json
import shlex
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from cli_agent_orchestrator.mcp_server import server
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.providers import claude_code
from cli_agent_orchestrator.providers.mcp_interactions import overlay_interaction_capability_file
from cli_agent_orchestrator.utils.mcp_tool_policy import TOOL_PROFILES

PROFILE_ENV = "CAO_INTERNAL_MCP_TOOL_PROFILE"
FILTER_ENV = "CAO_INTERNAL_MCP_ENABLED_TOOLS"
pytestmark = pytest.mark.offline_database


@pytest.fixture
def build(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_code, "CAO_HOME_DIR", tmp_path)
    monkeypatch.delenv(FILTER_ENV, raising=False)

    def config(entries):
        profile = AgentProfile(name="test", description="test", mcpServers=entries)
        monkeypatch.setattr(claude_code, "load_agent_profile", lambda _: profile)
        provider = claude_code.ClaudeCodeProvider("test1234", "session", "window", "test")
        args = shlex.split(provider._build_claude_command())
        assert "--strict-mcp-config" in args
        path = Path(args[args.index("--mcp-config") + 1])
        return json.loads(path.read_text())["mcpServers"]

    return config


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tools",
    [
        [],
        ["list_siblings"],
        ["assign", "unknown"],
        ["send_message", "list_siblings", "send_message"],
    ],
)
async def test_claude_filter_restricts_real_catalog_and_dispatch(build, monkeypatch, tools):
    raw = {
        "cao": {
            "command": "cao-mcp-server",
            "cao_tool_profile": "collaboration",
            "enabled_tools": tools,
        }
    }
    before = copy.deepcopy(raw)
    entry = build(raw)["cao"]
    assert raw == before
    for key in (PROFILE_ENV, FILTER_ENV):
        if key in entry["env"]:
            monkeypatch.setenv(key, entry["env"][key])
        else:
            monkeypatch.delenv(key, raising=False)
    mcp = FastMCP("claude-filter")
    dispatched = []
    for name in TOOL_PROFILES["collaboration"] | {"assign"}:

        def make_tool(label):
            async def tool() -> str:
                dispatched.append(label)
                return "called"

            return tool

        mcp.tool(name=name)(make_tool(name))
    server._install_tool_profile_middleware_if_requested(mcp)
    expected = set(tools) & TOOL_PROFILES["collaboration"]
    async with Client(mcp) as client:
        assert {tool.name for tool in await client.list_tools()} == expected
        for name in expected:
            assert (await client.call_tool(name)).data == "called"
        for name in (TOOL_PROFILES["collaboration"] | {"assign", "unknown"}) - expected:
            with pytest.raises(ToolError):
                await client.call_tool(name)
    assert set(dispatched) == expected
    assert "enabled_tools" not in entry


@pytest.mark.parametrize("tools", [False, "cao_ask_user", {}, [1], [None]])
def test_profiled_malformed_filter_is_refused_before_mcp_file(build, tmp_path, tools):
    with pytest.raises(ValueError, match="enabled_tools"):
        build(
            {
                "cao": {
                    "command": "cao-mcp-server",
                    "cao_tool_profile": "interaction",
                    "enabled_tools": tools,
                }
            }
        )
    assert not list(tmp_path.rglob("*.mcp.json"))


@pytest.mark.parametrize("extra", [{}, {"enabled_tools": None}])
def test_unspecified_filter_keeps_selected_profile_and_is_not_sent_to_claude(build, extra):
    entry = build(
        {"cao": {"command": "cao-mcp-server", "cao_tool_profile": "collaboration", **extra}}
    )["cao"]
    assert FILTER_ENV not in entry["env"]
    assert entry["env"][PROFILE_ENV] == "collaboration"
    assert "enabled_tools" not in entry


def test_unprofiled_filter_serialization_remains_legacy(build):
    entry = build({"cao": {"command": "cao-mcp-server", "enabled_tools": []}})["cao"]
    assert entry["enabled_tools"] == []
    assert FILTER_ENV not in entry["env"]
    assert PROFILE_ENV not in entry["env"]


@pytest.mark.parametrize("profile", [None, "interaction"])
def test_protected_overlay_strips_forged_filter_on_every_entry(profile):
    raw = {
        name: {
            "command": command,
            "env": {FILTER_ENV: '["assign"]'},
            "environment": {FILTER_ENV: '["assign"]'},
        }
        for name, command in [("cao", "cao-mcp-server"), ("other", "untrusted")]
    }
    if profile is not None:
        # A text command with environment is independently invalid when profiled.
        raw["cao"].pop("environment")
        raw["cao"]["cao_tool_profile"] = profile
    before = copy.deepcopy(raw)
    result = overlay_interaction_capability_file(raw, None)
    assert raw == before
    for entry in result.values():
        assert FILTER_ENV not in entry.get("env", {})
        assert FILTER_ENV not in entry.get("environment", {})


@pytest.mark.parametrize("field", ["env", "environment"])
def test_native_shared_translation_refuses_internal_filter(field):
    from cli_agent_orchestrator.utils.opencode_config import translate_mcp_server_config

    with pytest.raises(ValueError, match="tool profile"):
        translate_mcp_server_config({"command": "cao-mcp-server", field: {FILTER_ENV: "[]"}})


@pytest.mark.asyncio
@pytest.mark.parametrize("tools", [[], ["list_siblings"]])
async def test_generated_claude_entry_controls_actual_stdio_child(build, tmp_path, tools):
    import asyncio
    import os
    import sys

    from fastmcp.client.transports import StdioTransport

    entry = build(
        {
            "cao": {
                "command": "cao-mcp-server",
                "cao_tool_profile": "collaboration",
                "enabled_tools": tools,
            }
        }
    )["cao"]
    home = tmp_path / "child-home"
    home.mkdir(mode=0o700)
    repo = Path(__file__).resolve().parents[2]
    assert entry["command"] == sys.executable
    assert entry["args"] == ["-m", "cli_agent_orchestrator.mcp_server.server"]
    environment = {
        **entry["env"],
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(home),
        "PYTHONPATH": str(repo / "src") + os.pathsep + str(repo),
        "CAO_MCP_APPS_ENABLED": "0",
    }
    assert environment["HOME"] == str(home)
    assert environment["TMPDIR"] == str(home)
    transport = StdioTransport(
        command=entry["command"],
        args=entry["args"],
        env=environment,
        cwd=str(home),
        keep_alive=False,
        log_file=home / "stderr.private.log",
    )
    async with asyncio.timeout(45):
        async with Client(transport) as client:
            assert {tool.name for tool in await client.list_tools()} == set(tools)
            assert await client.list_resources() == []
            assert await client.list_resource_templates() == []
            assert await client.list_prompts() == []
            # Rejections must occur before production handlers or argument checks.
            for name in ("cao_ask_user", "assign", "unknown"):
                with pytest.raises(ToolError, match="selected CAO tool-profile mode"):
                    await client.call_tool(name, {})
