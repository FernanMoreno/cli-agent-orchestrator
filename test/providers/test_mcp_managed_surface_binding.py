"""Only an exact bound child may supersede an explicit legacy restriction."""

import copy
import json
from test.providers.test_opencode_profile_initialization import launch
from test.providers.test_opencode_v1_managed_policy import prepare
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV as MARKER
from cli_agent_orchestrator.providers import opencode_cli
from cli_agent_orchestrator.providers.mcp_interactions import overlay_interaction_capability_file

pytestmark = pytest.mark.offline_database


def test_bound_child_cannot_expand_neighbors_or_mismatched_profiles():
    source = {
        name: {
            "command": ["cao-mcp-server"],
            "cao_tool_profile": "interaction" if name == "interaction" else "collaboration",
            "environment": {MARKER: "forged"},
        }
        for name in ["selected", "unbound", "mismatch", "interaction"]
    }
    source["unprofiled"] = {"command": ["cao-mcp-server"]}
    source["foreign"] = {"command": ["other"], "environment": {MARKER: "forged"}}
    binding = {
        "selected": "collaboration",
        "mismatch": "knowledge",
        "interaction": "interaction",
        "foreign": "collaboration",
    }
    before = copy.deepcopy(source), copy.deepcopy(binding)
    result = overlay_interaction_capability_file(
        source,
        "/private/runtime.cap",
        interaction_pull_flow_only=True,
        managed_tool_profiles=binding,
    )
    assert MARKER not in result["selected"]["environment"]
    for name in ["unbound", "mismatch", "unprofiled", "interaction"]:
        assert result[name]["environment"][MARKER] == "1"
    assert MARKER not in result["foreign"]["environment"]
    assert "CAO_INTERACTION_CAPABILITY_FILE" not in result["foreign"]["environment"]
    assert (source, binding) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("major", [1, 2])
@pytest.mark.parametrize("selectors", [[], ["*"], ["@other-server"]])
async def test_capability_only_request_retains_legacy_surface(
    launch, monkeypatch, major, selectors
):
    instance, _, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: major)
    monkeypatch.setattr(instance, "_wait_for_v1_cao_mcp_ready", AsyncMock(return_value=True))
    prepare(monkeypatch, profile="collaboration")
    instance._allowed_tools = selectors
    instance._interaction_source_requested = True
    instance.set_interaction_capability_file(home / "runtime.cap")
    assert await instance.initialize()
    private = instance._v1_config_dir if major == 1 else instance._v2_config_dir
    config = json.loads((private / "opencode.json").read_text())
    children = config["mcp"] if major == 1 else config["mcp"]["servers"]
    assert children["cao"]["environment"][MARKER] == "1"
    assert instance._allowed_tools == selectors
    assert instance.cleanup() is True
