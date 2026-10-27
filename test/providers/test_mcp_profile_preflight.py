"""Profile policy must agree with pre-launch native grants and Codex TOML."""

import json
import shlex
import tomllib

import pytest

from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.providers.codex import CodexProvider
from cli_agent_orchestrator.utils.mcp_tool_policy import MCP_TOOL_PROFILE_ENV, TOOL_PROFILES
from cli_agent_orchestrator.utils.opencode_v2 import validate_v2_mcp_configuration

pytestmark = pytest.mark.offline_database


def _config(tmp_path, environment, tools, **server_extra):
    path = tmp_path / "opencode.json"
    path.write_text(
        json.dumps(
            {
                "mcp": {
                    "servers": {
                        "cao-mcp-server": {
                            "command": ["cao-mcp-server"],
                            "codemode": False,
                            "environment": environment,
                            **server_extra,
                        }
                    }
                },
                "agents": {
                    "reviewer": {
                        "permissions": [
                            {"action": "*", "resource": "*", "effect": "deny"},
                            *[
                                {
                                    "action": "cao-mcp-server_" + tool,
                                    "resource": "*",
                                    "effect": "allow",
                                }
                                for tool in tools
                            ],
                        ]
                    }
                },
            }
        )
    )
    return tmp_path


def _validate(path):
    validate_v2_mcp_configuration(path, agent_name="reviewer", server_name="cao-mcp-server")


@pytest.mark.parametrize("missing", sorted(TOOL_PROFILES["collaboration"]))
def test_preflight_requires_every_tool_in_collaboration_profile(tmp_path, missing):
    path = _config(
        tmp_path,
        {MCP_TOOL_PROFILE_ENV: "collaboration"},
        TOOL_PROFILES["collaboration"] - {missing},
    )
    with pytest.raises(ValueError, match="has no CAO MCP grant"):
        _validate(path)


@pytest.mark.parametrize("profile", ["interaction", "collaboration"])
@pytest.mark.parametrize("legacy", [False, True])
def test_preflight_matches_effective_profile_and_legacy_intersection(tmp_path, profile, legacy):
    env = {MCP_TOOL_PROFILE_ENV: profile}
    if legacy:
        env[_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV] = "1"
    effective = TOOL_PROFILES["interaction"] if legacy else TOOL_PROFILES[profile]
    _validate(_config(tmp_path, env, effective))


@pytest.mark.parametrize("value", ["", "*", "unknown", None, 1, []])
def test_preflight_rejects_invalid_selected_policy(tmp_path, value):
    with pytest.raises(ValueError, match="tool profile"):
        _validate(_config(tmp_path, {MCP_TOOL_PROFILE_ENV: value}, TOOL_PROFILES["collaboration"]))


@pytest.mark.parametrize("environment", [None, [], "ignored"])
def test_preflight_rejects_malformed_native_environment(tmp_path, environment):
    with pytest.raises(ValueError, match="environment"):
        _validate(_config(tmp_path, environment, TOOL_PROFILES["collaboration"]))


def test_preflight_rejects_policy_in_ignored_native_env_field(tmp_path):
    path = _config(
        tmp_path, {}, TOOL_PROFILES["collaboration"], env={MCP_TOOL_PROFILE_ENV: "collaboration"}
    )
    with pytest.raises(ValueError, match="environment"):
        _validate(path)


@pytest.mark.parametrize("profile", ["interaction", "collaboration"])
@pytest.mark.parametrize(
    "enabled",
    [
        "absent",
        None,
        [],
        ["cao_ask_user"],
        ["assign"],
        ["send_message", "cao_ask_user", 'bad"\\\nname'],
    ],
)
def test_codex_emitted_allowlist_is_policy_intersection(monkeypatch, profile, enabled):
    cfg = {"command": "cao-mcp-server", "cao_tool_profile": profile}
    if enabled != "absent":
        cfg["enabled_tools"] = enabled
    agent = AgentProfile(name="test", description="test", mcpServers={"cao": cfg})
    monkeypatch.setattr(
        "cli_agent_orchestrator.providers.codex.load_agent_profile", lambda _: agent
    )
    command = CodexProvider("test1234", "session", "window", "test")._build_codex_command()
    overrides = [
        arg for arg in shlex.split(command) if arg.startswith("mcp_servers.cao.enabled_tools=")
    ]
    assert len(overrides) == 1
    actual = tomllib.loads(overrides[0])["mcp_servers"]["cao"]["enabled_tools"]
    expected = (
        TOOL_PROFILES[profile]
        if enabled in ("absent", None)
        else TOOL_PROFILES[profile].intersection(enabled)
    )
    assert set(actual) == expected
    assert "bad" not in repr(actual)


@pytest.mark.parametrize("enabled", ["all", [1], {}])
def test_codex_profile_preserves_fail_closed_client_filter_validation(monkeypatch, enabled):
    agent = AgentProfile(
        name="test",
        description="test",
        mcpServers={
            "cao": {
                "command": "cao-mcp-server",
                "cao_tool_profile": "interaction",
                "enabled_tools": enabled,
            }
        },
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.providers.codex.load_agent_profile", lambda _: agent
    )
    with pytest.raises(ValueError, match="enabled_tools"):
        CodexProvider("test1234", "session", "window", "test")._build_codex_command()


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", list(TOOL_PROFILES))
@pytest.mark.parametrize("enabled", [None, [], ["cao_ask_user"], ["assign"]])
async def test_codex_text_filter_reaches_server_catalog_and_direct_dispatch(
    monkeypatch, profile, enabled
):
    from fastmcp import Client, FastMCP
    from fastmcp.exceptions import ToolError

    from cli_agent_orchestrator.mcp_server import server
    from cli_agent_orchestrator.utils.mcp_tool_policy import MCP_TOOL_FILTER_ENV

    agent = AgentProfile(
        name="test",
        description="test",
        mcpServers={
            "cao": {
                "command": "cao-mcp-server",
                "cao_tool_profile": profile,
                "enabled_tools": enabled,
            }
        },
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.providers.codex.load_agent_profile", lambda _: agent
    )
    command = CodexProvider("test1234", "session", "window", "test")._build_codex_command()
    overrides = [v for v in shlex.split(command) if v.startswith("mcp_servers.cao.env=")]
    assert len(overrides) == 1
    env = tomllib.loads(overrides[0])["mcp_servers"]["cao"]["env"]
    monkeypatch.delenv(MCP_TOOL_FILTER_ENV, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    expected = (
        TOOL_PROFILES[profile] if enabled is None else TOOL_PROFILES[profile].intersection(enabled)
    )
    dispatched = []
    mcp = FastMCP("codex-text-filter")
    for name in TOOL_PROFILES[profile]:

        def make_tool(operation):
            async def call() -> str:
                dispatched.append(operation)
                return "called"

            return call

        mcp.tool(name=name)(make_tool(name))
    assert server._install_tool_profile_middleware_if_requested(mcp)
    async with Client(mcp) as client:
        for name in sorted(TOOL_PROFILES[profile] - expected):
            with pytest.raises(ToolError):
                await client.call_tool(name)
        assert {t.name for t in await client.list_tools()} == expected
        for name in expected:
            assert (await client.call_tool(name)).data == "called"
    assert set(dispatched) == expected


def test_codex_legacy_marker_narrows_advertised_collaboration_profile(monkeypatch):
    import sys

    agent = AgentProfile(name="test", description="test")
    monkeypatch.setattr(
        "cli_agent_orchestrator.providers.codex.load_agent_profile", lambda _: agent
    )
    provider = CodexProvider("test1234", "session", "window", "test")
    monkeypatch.setattr(
        provider,
        "interaction_mcp_servers",
        lambda _: {
            "cao": {
                "command": sys.executable,
                "args": ["-m", "cli_agent_orchestrator.mcp_server.server"],
                "env": {
                    MCP_TOOL_PROFILE_ENV: "collaboration",
                    _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV: "1",
                },
            }
        },
    )
    args = shlex.split(provider._build_codex_command())
    override = next(arg for arg in args if arg.startswith("mcp_servers.cao.enabled_tools="))
    assert (
        set(tomllib.loads(override)["mcp_servers"]["cao"]["enabled_tools"])
        == TOOL_PROFILES["interaction"]
    )
