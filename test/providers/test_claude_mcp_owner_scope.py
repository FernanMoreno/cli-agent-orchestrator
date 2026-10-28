"""The API owner scope is explicit and never inherited by unrelated MCPs."""

import json

import pytest

from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider
from cli_agent_orchestrator.utils.mcp_owner_stop import OWNER_DIR_ENV, SIGNAL_STOP_ENV

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize("enabled", [False, True])
def test_owner_scope_is_bound_by_api_only_for_opted_in_cao(tmp_path, monkeypatch, enabled):
    from cli_agent_orchestrator.providers import claude_code as module

    monkeypatch.setattr(module, "CAO_HOME_DIR", tmp_path)
    monkeypatch.setenv(OWNER_DIR_ENV, "/irrelevant/inherited-owner-dir")
    monkeypatch.setenv(SIGNAL_STOP_ENV, "1")
    explicit = (
        {SIGNAL_STOP_ENV: "1", OWNER_DIR_ENV: "/irrelevant/profile-owner-dir"} if enabled else {}
    )
    profile = AgentProfile(
        name="owner-control",
        description="owned MCP scope",
        mcpServers={
            "cao-mcp-server": {"command": "cao-mcp-server", "env": explicit},
            "unrelated": {"command": "unrelated-mcp"},
        },
    )
    provider = ClaudeCodeProvider("identity", "session", "window")
    provider._build_claude_command(profile)
    servers = json.loads((tmp_path / "tmp/identity.mcp.json").read_text())["mcpServers"]
    cao = servers["cao-mcp-server"]["env"]
    if enabled:
        assert cao[OWNER_DIR_ENV] == str(tmp_path / "tmp/identity.mcp-owners")
        assert cao[SIGNAL_STOP_ENV] == "1"
    else:
        assert OWNER_DIR_ENV not in cao
        assert SIGNAL_STOP_ENV not in cao
    assert OWNER_DIR_ENV not in servers["unrelated"]["env"]
    assert SIGNAL_STOP_ENV not in servers["unrelated"]["env"]
