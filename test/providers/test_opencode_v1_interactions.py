"""Explicit native-v1 interaction composes validated policy, source and discovery."""

import copy
import json
from test.providers.test_opencode_profile_initialization import launch
from test.providers.test_opencode_v1_managed_policy import prepare
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.constants import (
    _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV,
)
from cli_agent_orchestrator.providers import opencode_cli
from cli_agent_orchestrator.providers.mcp_interactions import CAO_INTERACTION_CAPABILITY_FILE
from cli_agent_orchestrator.utils.mcp_tool_policy import INTERACTION_TOOLS, MCP_TOOL_FILTER_ENV

pytestmark = [pytest.mark.offline_database, pytest.mark.asyncio]


@pytest.mark.parametrize("profile", ["interaction", "collaboration"])
@pytest.mark.parametrize("tools", [None, sorted(INTERACTION_TOOLS)])
async def test_v3_interaction_advertised_only_after_protected_preflight_and_discovery(
    launch, monkeypatch, profile, tools
):
    instance, backend, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    config, path, _ = prepare(monkeypatch, profile=profile, tools=tools)
    before = copy.deepcopy(config), path.read_bytes()
    instance._interaction_source_requested = True
    assert instance.interaction_source_configured({})
    cap = home / "runtime.cap"
    instance.set_interaction_capability_file(cap)
    assert instance.interaction_capabilities["explicit_channel"] == "unsupported"

    async def discover(*, timeout):
        assert timeout > 0
        assert instance.interaction_capabilities["explicit_channel"] == "unsupported"
        return True

    monkeypatch.setattr(instance, "_wait_for_v1_cao_mcp_ready", discover)
    assert await instance.initialize()
    native = json.loads((instance._v1_config_dir / "opencode.json").read_text())
    env = native["mcp"]["cao"]["environment"]
    assert env[CAO_INTERACTION_CAPABILITY_FILE] == str(cap)
    assert env[_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV] == "1"
    if tools is not None:
        assert json.loads(env[MCP_TOOL_FILTER_ENV]) == tools
    assert instance.interaction_capabilities["explicit_channel"] == "pull_explicit"
    assert (config, path.read_bytes()) == before
    backend.send_keys.assert_called_once()
    instance.cleanup()
    assert instance.interaction_capabilities["explicit_channel"] == "unsupported"


async def test_v1_requires_runtime_issued_source_before_launch(launch, monkeypatch):
    instance, backend, _ = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    prepare(monkeypatch)
    instance._interaction_source_requested = True
    with pytest.raises(ValueError, match="capability"):
        await instance.initialize()
    backend.send_keys.assert_not_called()
    assert instance._owned_v1_config_dir is None


@pytest.mark.parametrize(
    "tools", [[], ["cao_ask_user"], ["cao_ask_user", "cao_wait_for_user_answers"]]
)
async def test_partial_filter_cannot_advertise_interaction(launch, monkeypatch, tools):
    instance, backend, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    prepare(monkeypatch, tools=tools)
    instance._interaction_source_requested = True
    instance.set_interaction_capability_file(home / "runtime.cap")
    with pytest.raises(ValueError, match="three interaction tools"):
        await instance.initialize()
    backend.send_keys.assert_not_called()
    assert instance.interaction_capabilities["explicit_channel"] == "unsupported"
    assert instance._owned_v1_config_dir is None


async def test_discovery_failure_does_not_leave_advertised_channel(launch, monkeypatch):
    instance, backend, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    prepare(monkeypatch)
    instance._interaction_source_requested = True
    instance.set_interaction_capability_file(home / "runtime.cap")
    monkeypatch.setattr(instance, "_wait_for_v1_cao_mcp_ready", AsyncMock(return_value=False))
    with pytest.raises(TimeoutError, match="v1.*MCP"):
        await instance.initialize()
    backend.send_keys.assert_called_once()
    assert instance.interaction_capabilities["explicit_channel"] == "unsupported"
    assert instance._owned_v1_config_dir is None


async def test_unmanaged_v1_interaction_remains_refused(launch, monkeypatch):
    instance, backend, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    instance._interaction_source_requested = True
    instance.set_interaction_capability_file(home / "runtime.cap")
    assert not instance.interaction_source_configured({})
    with pytest.raises(ValueError, match="managed.*v3"):
        await instance.initialize()
    backend.send_keys.assert_not_called()


@pytest.mark.parametrize("permitted", [False, True])
async def test_v3_policy_without_surface_metadata_still_requires_all_native_grants(
    launch, monkeypatch, permitted
):
    import sys

    from cli_agent_orchestrator.utils.opencode_policy import render_native_agent

    instance, backend, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    config, path, agent = prepare(monkeypatch)
    config["mcp"]["servers"]["cao"]["command"] = [
        sys.executable,
        "-m",
        "cli_agent_orchestrator.mcp_server.server",
    ]
    config["agent"]["reviewer"]["tools"] = {}
    agent.metadata["permission"] = {"*": "allow" if permitted else "deny"}
    path.write_text(
        render_native_agent(
            agent, "reviewer", {}, source_config=config, preserve_permission_order=True
        )
    )
    instance._interaction_source_requested = True
    instance.set_interaction_capability_file(home / "runtime.cap")
    monkeypatch.setattr(instance, "_wait_for_v1_cao_mcp_ready", AsyncMock(return_value=True))
    if permitted:
        assert await instance.initialize()
        assert instance.interaction_capabilities["explicit_channel"] == "pull_explicit"
    else:
        with pytest.raises(ValueError, match="no CAO MCP grant"):
            await instance.initialize()
        backend.send_keys.assert_not_called()


async def test_v1_explicit_pull_flow_does_not_require_unoffered_collaboration_grants(
    launch, monkeypatch
):
    instance, _, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    prepare(
        monkeypatch,
        permissions={"*": "deny", **{"cao_" + tool: "allow" for tool in sorted(INTERACTION_TOOLS)}},
    )
    instance._interaction_source_requested = True
    instance.set_interaction_capability_file(home / "runtime.cap")
    monkeypatch.setattr(instance, "_wait_for_v1_cao_mcp_ready", AsyncMock(return_value=True))
    assert await instance.initialize()
    assert instance.interaction_capabilities["explicit_channel"] == "pull_explicit"
