"""Real journey isolation must retain the protected managed child policy."""

import copy
import json
from test.live_provider import opencode_interaction_journey as journey
from test.live_provider.test_opencode_frozen_writer_contract import configuration, installed_adapter

import pytest

from cli_agent_orchestrator.utils import opencode_v2
from cli_agent_orchestrator.utils.mcp_tool_policy import MCP_TOOL_FILTER_ENV, MCP_TOOL_PROFILE_ENV

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize("boundary", ["helper", "installed_adapter"])
@pytest.mark.parametrize("tools", [None, [], ["cao_ask_user"]])
def test_real_writer_preserves_managed_policy_in_isolated_child(tmp_path, boundary, tools):
    agents = tmp_path / "agents"
    agents.mkdir()
    source = configuration(tmp_path)
    environment = source["mcp"]["servers"]["cao-mcp-server"]["environment"]
    environment[MCP_TOOL_PROFILE_ENV] = "interaction"
    if tools is not None:
        environment[MCP_TOOL_FILTER_ENV] = json.dumps(tools)
    before = copy.deepcopy(source)
    writer = (
        journey._write_v2_configuration_with_isolated_native_mcp
        if boundary == "helper"
        else installed_adapter(opencode_v2.write_v2_configuration)
    )
    kwargs = dict(
        source_agents=agents,
        source_config=source,
        session_id="probe1234",
        selected_agent_source=("reviewer", "---\ndescription: Reviewer\n---\nFrozen ñ, 中文."),
    )
    if boundary == "helper":
        result = writer(opencode_v2.write_v2_configuration, tmp_path / "native", **kwargs)
    else:
        result = writer(tmp_path / "native", **kwargs)
    generated = json.loads((result / "opencode.json").read_text())
    child = generated["mcp"]["servers"]["cao-mcp-server"]
    command = child["command"]
    assert command[1] == "-i"
    assert MCP_TOOL_PROFILE_ENV + "=interaction" in command
    if tools is None:
        assert not any(part.startswith(MCP_TOOL_FILTER_ENV + "=") for part in command)
    else:
        values = [
            part.split("=", 1)[1] for part in command if part.startswith(MCP_TOOL_FILTER_ENV + "=")
        ]
        assert len(values) == 1 and json.loads(values[0]) == tools
    assert child.get("environment", {}) == {}
    assert generated["agents"]["reviewer"]["system"] == "Frozen ñ, 中文."
    assert source == before


@pytest.mark.parametrize("name", ["UNREVIEWED_VARIABLE", "ANTHROPIC_API_KEY"])
def test_isolation_still_refuses_unknown_and_provider_credential_names(tmp_path, name):
    source = configuration(tmp_path)
    source["mcp"]["servers"]["cao-mcp-server"]["environment"][name] = "synthetic"
    before = copy.deepcopy(source)
    with pytest.raises(ValueError, match="mcp_environment_not_allowlisted"):
        journey._isolate_native_mcp_for_v2_writer(source)
    assert source == before
