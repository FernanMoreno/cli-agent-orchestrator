"""Native catalog variants use the closed production profile intersection."""

import json
import shlex
from test.live_provider import claude_cancel_gateway as gateway
from test.live_provider.native_cancel_adapters import (
    claude_adapter_for_manifest,
    native_profile_text,
)

import pytest
import yaml

from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.providers import claude_code
from cli_agent_orchestrator.utils.mcp_tool_policy import (
    MCP_TOOL_FILTER_ENV,
    MCP_TOOL_PROFILE_ENV,
    TOOL_PROFILES,
)

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize("profile", ["interaction", "collaboration", "coordination"])
def test_native_full_catalog_matches_actual_production_overlay(tmp_path, monkeypatch, profile):
    adapter = claude_adapter_for_manifest(
        {"profile_case": "mcp_catalog:" + profile, "case_kind": "preflight"}
    )
    parsed = AgentProfile(
        **yaml.safe_load(native_profile_text("catalog", adapter).split("---", 2)[1])
    )
    assert set(adapter.expected_mcp_tools) == {
        "mcp__cao-mcp-server__" + n for n in TOOL_PROFILES[profile]
    }
    assert adapter.allowed_tools == ("@cao-mcp-server",) and adapter.native_operation == ""
    assert "delete_terminal" in parsed.mcpServers["cao-mcp-server"]["enabled_tools"]
    monkeypatch.setattr(claude_code, "CAO_HOME_DIR", tmp_path)
    provider = claude_code.ClaudeCodeProvider(
        "1234abcd", "s", "w", agent_profile=parsed.name, allowed_tools=parsed.allowedTools
    )
    args = shlex.split(provider._build_claude_command(parsed))
    config = json.loads(
        __import__("pathlib").Path(args[args.index("--mcp-config") + 1]).read_text()
    )["mcpServers"]["cao-mcp-server"]
    assert config["env"][MCP_TOOL_PROFILE_ENV] == profile
    assert set(json.loads(config["env"][MCP_TOOL_FILTER_ENV])) == TOOL_PROFILES[profile]
    gateway.validate_native_launch(args, adapter.expected_mcp_tools, mcp_profile=profile)


def test_catalog_manifest_cannot_select_arbitrary_profile_or_concurrent_kind():
    for profile, kind in [("unknown", "preflight"), ("coordination", "concurrent")]:
        with pytest.raises((AssertionError, ValueError)):
            claude_adapter_for_manifest(
                {"profile_case": "mcp_catalog:" + profile, "case_kind": kind}
            )
