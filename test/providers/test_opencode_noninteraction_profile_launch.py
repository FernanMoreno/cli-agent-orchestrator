"""A server selector must not force human tools onto managed nonhuman profiles."""

import copy
import json
from test.providers.test_opencode_profile_initialization import launch
from test.providers.test_opencode_v1_managed_policy import prepare
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.providers import opencode_cli
from cli_agent_orchestrator.utils.mcp_tool_policy import MCP_TOOL_FILTER_ENV, MCP_TOOL_PROFILE_ENV

pytestmark = [pytest.mark.offline_database, pytest.mark.asyncio]


@pytest.mark.parametrize("major", [1, 2])
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize(
    "profile,tools",
    [
        ("collaboration", ["list_siblings", "send_message"]),
        ("collaboration", []),
        ("coordination", ["list_local_cao_peers"]),
        ("supervision", ["inspect_terminal_turn"]),
        ("knowledge", ["memory_recall"]),
        ("knowledge_writer", ["memory_store"]),
        ("elastic_worker", ["complete_assignment"]),
    ],
)
async def test_nonhuman_surface_launches_without_human_source(
    launch, monkeypatch, major, prepared, profile, tools
):
    instance, backend, _ = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: major)
    monkeypatch.setattr(instance, "_wait_for_v1_cao_mcp_ready", AsyncMock(return_value=True))
    config, path, _ = prepare(monkeypatch, profile=profile, tools=tools)
    before = copy.deepcopy(config), path.read_bytes()
    instance._allowed_tools = ["@cao-mcp-server", "discovery"]
    instance._interaction_source_requested = True
    if prepared:
        instance.prepare_native_profile(instance.load_native_profile("reviewer"))
    assert instance.interaction_source_configured({}) is False
    assert instance.interaction_capability_file is None
    assert await instance.initialize()
    private = instance._v1_config_dir if major == 1 else instance._v2_config_dir
    generated = json.loads((private / "opencode.json").read_text())
    servers = generated["mcp"] if major == 1 else generated["mcp"]["servers"]
    child = servers["cao"]
    environment = child["environment"]
    assert environment[MCP_TOOL_PROFILE_ENV] == profile
    assert json.loads(environment[MCP_TOOL_FILTER_ENV]) == tools
    assert "CAO_INTERACTION_CAPABILITY_FILE" not in environment
    assert "CAO_INTERNAL_INTERACTION_PULL_FLOW_ONLY" not in environment
    assert instance.interaction_capabilities["explicit_channel"] == "unsupported"
    assert instance._allowed_tools == ["@cao-mcp-server", "discovery"]
    assert (config, path.read_bytes()) == before
    backend.send_keys.assert_called_once()
    instance.cleanup()
    assert not private.exists()


@pytest.mark.parametrize("major", [1, 2])
@pytest.mark.parametrize(
    "profile,tools",
    [
        ("interaction", []),
        ("collaboration", ["cao_ask_user"]),
        ("collaboration", ["cao_list_interactions"]),
        ("collaboration", ["cao_get_interaction"]),
        ("collaboration", ["cao_escalate_interaction"]),
    ],
)
async def test_declared_human_surface_retains_complete_protocol_refusal(
    launch, monkeypatch, major, profile, tools
):
    instance, backend, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: major)
    prepare(monkeypatch, profile=profile, tools=tools)
    instance._allowed_tools = ["@cao-mcp-server"]
    instance._interaction_source_requested = True
    assert instance.interaction_source_configured({}) is True
    instance.set_interaction_capability_file(home / "explicit.cap")
    with pytest.raises(ValueError):
        await instance.initialize()
    backend.send_keys.assert_not_called()
    assert instance._owned_v1_config_dir is None
    assert instance._owned_v2_config_dir is None


@pytest.mark.parametrize("major", [1, 2])
async def test_explicit_runtime_capability_cannot_downgrade_to_nonhuman(launch, monkeypatch, major):
    instance, backend, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: major)
    prepare(monkeypatch, tools=["send_message"])
    instance._allowed_tools = ["@cao-mcp-server"]
    instance._interaction_source_requested = True
    instance.set_interaction_capability_file(home / "explicit.cap")
    with pytest.raises(ValueError):
        await instance.initialize()
    backend.send_keys.assert_not_called()
    assert instance._owned_v1_config_dir is None
    assert instance._owned_v2_config_dir is None


@pytest.mark.parametrize("major", [1, 2])
async def test_later_explicit_capability_restores_strict_refusal(launch, monkeypatch, major):
    instance, backend, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: major)
    monkeypatch.setattr(instance, "_wait_for_v1_cao_mcp_ready", AsyncMock(return_value=True))
    prepare(monkeypatch, tools=["send_message"])
    instance._allowed_tools = ["@cao-mcp-server"]
    instance._interaction_source_requested = True
    assert await instance.initialize()
    private = instance._v1_config_dir if major == 1 else instance._v2_config_dir
    instance.set_interaction_capability_file(home / "later-explicit.cap")
    with pytest.raises(ValueError):
        await instance.initialize()
    assert backend.send_keys.call_count == 1
    assert not private.exists()
    assert instance._owned_v1_config_dir is None
    assert instance._owned_v2_config_dir is None


@pytest.mark.parametrize("major", [1, 2])
@pytest.mark.parametrize("with_capability", [False, True])
async def test_direct_nonhuman_to_human_reinitialization_recomputes_intent(
    launch, monkeypatch, major, with_capability
):
    instance, backend, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: major)
    monkeypatch.setattr(instance, "_wait_for_v1_cao_mcp_ready", AsyncMock(return_value=True))
    prepare(monkeypatch, tools=["send_message"])
    instance._allowed_tools = ["@cao-mcp-server"]
    instance._interaction_source_requested = True
    assert await instance.initialize()
    assert instance.interaction_capabilities["explicit_channel"] == "unsupported"
    prepare(monkeypatch, profile="interaction")
    assert instance.interaction_source_configured({}) is True
    if with_capability:
        instance.set_interaction_capability_file(home / "human.cap")
        assert await instance.initialize()
        assert instance.interaction_capabilities["explicit_channel"] == "pull_explicit"
        assert backend.send_keys.call_count == 2
    else:
        with pytest.raises(ValueError, match="capability"):
            await instance.initialize()
        assert backend.send_keys.call_count == 1
