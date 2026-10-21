"""Human capability must preserve an explicitly selected mixed MCP surface."""

import copy
import json
from test.providers.test_opencode_profile_initialization import launch
from test.providers.test_opencode_v1_managed_policy import prepare
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV
from cli_agent_orchestrator.providers import opencode_cli
from cli_agent_orchestrator.utils.mcp_tool_policy import (
    INTERACTION_TOOLS,
    MCP_TOOL_FILTER_ENV,
    MCP_TOOL_PROFILE_ENV,
    TOOL_PROFILES,
)

pytestmark = [pytest.mark.offline_database, pytest.mark.asyncio]
BROAD_PROFILES = [name for name in TOOL_PROFILES if name != "interaction"]


async def initialize_human(launch, monkeypatch, major, profile, tools, prepared):
    instance, backend, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: major)
    monkeypatch.setattr(instance, "_wait_for_v1_cao_mcp_ready", AsyncMock(return_value=True))
    config, path, _ = prepare(monkeypatch, profile=profile, tools=tools)
    before = copy.deepcopy(config), path.read_bytes()
    instance._allowed_tools = ["@cao-mcp-server"]
    instance._interaction_source_requested = True
    if prepared:
        instance.prepare_native_profile(instance.load_native_profile("reviewer"))
    assert instance.interaction_source_configured({}) is True
    capability = home / "runtime-issued.cap"
    instance.set_interaction_capability_file(capability)
    assert await instance.initialize()
    private = instance._v1_config_dir if major == 1 else instance._v2_config_dir
    generated = json.loads((private / "opencode.json").read_text())
    servers = generated["mcp"] if major == 1 else generated["mcp"]["servers"]
    environment = servers["cao"]["environment"]
    assert environment[MCP_TOOL_PROFILE_ENV] == profile
    assert environment["CAO_INTERACTION_CAPABILITY_FILE"] == str(capability)
    expected = TOOL_PROFILES[profile] if tools is None else frozenset(tools)
    if tools is None:
        assert MCP_TOOL_FILTER_ENV not in environment
    else:
        assert frozenset(json.loads(environment[MCP_TOOL_FILTER_ENV])) == expected
    offered = expected
    if environment.get(_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV) == "1":
        offered = offered & INTERACTION_TOOLS
    assert offered == expected, "legacy pull-flow marker silently truncates managed profile"
    assert (config, path.read_bytes()) == before
    assert instance._allowed_tools == ["@cao-mcp-server"]
    backend.send_keys.assert_called_once()
    instance.cleanup()
    assert not private.exists()


@pytest.mark.parametrize("major", [1, 2])
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("profile", list(TOOL_PROFILES))
async def test_full_managed_profile_survives_human_capability(
    launch, monkeypatch, major, prepared, profile
):
    await initialize_human(launch, monkeypatch, major, profile, None, prepared)


@pytest.mark.parametrize("major", [1, 2])
@pytest.mark.parametrize("profile", BROAD_PROFILES)
async def test_complete_human_protocol_plus_message_retains_exact_subset(
    launch, monkeypatch, major, profile
):
    tools = sorted(INTERACTION_TOOLS | {"send_message"})
    await initialize_human(launch, monkeypatch, major, profile, tools, False)


@pytest.mark.parametrize("major", [1, 2])
async def test_mixed_profile_preflight_requires_nonhuman_native_grants(launch, monkeypatch, major):
    instance, backend, _ = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: major)
    monkeypatch.setattr(instance, "_wait_for_v1_cao_mcp_ready", AsyncMock(return_value=True))
    permissions = {"cao_" + tool: "allow" for tool in INTERACTION_TOOLS}
    prepare(monkeypatch, profile="collaboration", permissions=permissions)
    instance._allowed_tools = ["@cao-mcp-server"]
    instance._interaction_source_requested = True
    instance.set_interaction_capability_file(launch[2] / "runtime-issued.cap")
    with pytest.raises(ValueError, match="no CAO MCP grant"):
        await instance.initialize()
    backend.send_keys.assert_not_called()
