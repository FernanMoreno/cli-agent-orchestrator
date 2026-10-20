"""Managed native artifacts cannot silently fall back to an unmarked launch."""

import base64
import copy
import json
from test.providers.test_opencode_profile_initialization import launch

import frontmatter
import pytest

from cli_agent_orchestrator.utils import opencode_config

pytestmark = [pytest.mark.offline_database, pytest.mark.asyncio]


async def test_pending_native_agent_is_rejected_before_backend_launch(launch, monkeypatch):
    from cli_agent_orchestrator.providers import opencode_cli

    instance, backend, _ = launch
    monkeypatch.setattr(opencode_config, "read_config", lambda: {})
    payload = {
        "version": 1,
        "agent_id": "reviewer",
        "state": "pending",
        "profiles": {"cao": "collaboration"},
        "binding": None,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    (opencode_cli.OPENCODE_AGENTS_DIR / "reviewer.md").write_text(
        "---\ndescription: Reviewer\n---\n" f"<!-- cao-native-mcp-policy:{encoded} -->\nReview.\n"
    )
    with pytest.raises(ValueError, match="native.*policy|native.*profile"):
        await instance.initialize()
    backend.send_keys.assert_not_called()


async def test_bound_stable_launcher_is_available_for_interaction_registration(launch, monkeypatch):
    instance, backend, _ = launch
    ready_agent(monkeypatch)
    assert instance.interaction_source_configured({}) is True
    backend.send_keys.assert_not_called()


async def test_managed_interaction_opt_in_retains_three_tool_intersection(launch, monkeypatch):
    from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV
    from cli_agent_orchestrator.utils.mcp_tool_policy import MCP_TOOL_PROFILE_ENV

    instance, _, cao_home = launch
    ready_agent(monkeypatch)
    instance._interaction_source_requested = True
    instance._allowed_tools = ["@cao-mcp-server"]
    assert instance.interaction_source_configured({}) is True
    instance.set_interaction_capability_file(cao_home / "capability.json")
    assert await instance.initialize() is True
    generated = json.loads((instance._v2_config_dir / "opencode.json").read_text())
    environment = generated["mcp"]["servers"]["cao"]["environment"]
    assert environment[MCP_TOOL_PROFILE_ENV] == "collaboration"
    assert environment[_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV] == "1"
    assert instance._interaction_preflight_ok is True


def ready_agent(monkeypatch, profile="collaboration", layout="nested"):
    from cli_agent_orchestrator.providers import opencode_cli
    from cli_agent_orchestrator.utils.opencode_policy import render_native_agent

    servers = {"cao": {"type": "local", "command": ["/stable/cao-mcp-server"], "enabled": True}}
    config = {
        "mcp": {"servers": servers} if layout == "nested" else servers,
        "agent": {"reviewer": {"tools": {"cao*": True}}},
    }
    profiles = {} if profile is None else {"cao": profile}
    agent = frontmatter.Post("Revisión ñ, 中文 y 🐾.", description="Reviewer")
    path = opencode_cli.OPENCODE_AGENTS_DIR / "reviewer.md"
    path.write_text(render_native_agent(agent, "reviewer", profiles, source_config=config))
    monkeypatch.setattr(opencode_config, "read_config", lambda: config)
    return config, path, agent


@pytest.mark.parametrize("layout", ["nested", "flat"])
@pytest.mark.parametrize("profile", ["interaction", "collaboration"])
async def test_ready_native_policy_is_applied_only_to_private_child(
    launch, monkeypatch, layout, profile
):
    from cli_agent_orchestrator.utils.mcp_tool_policy import MCP_TOOL_PROFILE_ENV
    from cli_agent_orchestrator.utils.opencode_policy import NATIVE_POLICY_PREFIX

    instance, backend, _ = launch
    config, path, agent = ready_agent(monkeypatch, profile, layout)
    original_config = copy.deepcopy(config)
    original_agent = path.read_bytes()
    assert await instance.initialize() is True
    generated = json.loads((instance._v2_config_dir / "opencode.json").read_text())
    assert generated["mcp"]["servers"]["cao"]["environment"][MCP_TOOL_PROFILE_ENV] == profile
    assert generated["agents"]["reviewer"]["system"] == agent.content
    assert NATIVE_POLICY_PREFIX not in generated["agents"]["reviewer"]["system"]
    assert generated["mcp"]["servers"]["cao"]["command"][1:] == [
        "-m",
        "cli_agent_orchestrator.mcp_server.server",
    ]
    assert config == original_config and path.read_bytes() == original_agent
    assert instance.interaction_capabilities["explicit_channel"] == "unsupported"
    backend.send_keys.assert_called_once()


@pytest.mark.parametrize("profile", [None, "interaction", "collaboration"])
async def test_v1_rejects_any_managed_native_envelope(launch, monkeypatch, profile):
    from cli_agent_orchestrator.providers import opencode_cli

    instance, backend, _ = launch
    ready_agent(monkeypatch, profile)
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    with pytest.raises(ValueError, match="v1 does not support managed native"):
        await instance.initialize()
    backend.send_keys.assert_not_called()


@pytest.mark.parametrize("change", ["prompt", "server", "agent-grant", "disabled", "missing"])
async def test_stale_native_binding_refuses_launch_and_cleans_owned_state(
    launch, monkeypatch, change
):
    instance, backend, cao_home = launch
    config, path, _ = ready_agent(monkeypatch)
    if change == "prompt":
        path.write_text(path.read_text().replace("Revisión", "Changed"))
    elif change == "server":
        config["mcp"]["servers"]["cao"]["command"] = ["other-server"]
    elif change == "agent-grant":
        config["agent"]["reviewer"]["tools"] = {}
    elif change == "disabled":
        config["mcp"]["servers"]["cao"]["enabled"] = False
    else:
        config["mcp"]["servers"].clear()
    with pytest.raises(ValueError, match="native MCP policy"):
        await instance.initialize()
    backend.send_keys.assert_not_called()
    assert instance._owned_v2_config_dir is None
    assert not list((cao_home / "opencode-v2").glob("terminal-*"))


async def test_native_payload_is_frozen_before_runtime_preparation(launch, monkeypatch):
    instance, _, _ = launch
    _, path, original = ready_agent(monkeypatch)
    prepare = instance._prepare_runtime

    def change_after_validation():
        prepare()
        path.write_text("---\ndescription: Changed\n---\nDifferent prompt.")

    monkeypatch.setattr(instance, "_prepare_runtime", change_after_validation)
    assert await instance.initialize() is True
    generated = json.loads((instance._v2_config_dir / "opencode.json").read_text())
    assert generated["agents"]["reviewer"]["system"] == original.content
    assert generated["agents"]["reviewer"]["description"] == original.metadata["description"]


@pytest.mark.parametrize("pending", [False, True])
async def test_private_catalog_omits_other_managed_agents(launch, monkeypatch, pending):
    from cli_agent_orchestrator.providers import opencode_cli
    from cli_agent_orchestrator.utils.opencode_policy import render_native_agent

    instance, _, _ = launch
    config, _, _ = ready_agent(monkeypatch)
    other = frontmatter.Post("Another managed prompt.", description="Other")
    (opencode_cli.OPENCODE_AGENTS_DIR / "other.md").write_text(
        render_native_agent(
            other, "other", {"cao": "interaction"}, source_config=None if pending else config
        )
    )
    (opencode_cli.OPENCODE_AGENTS_DIR / "legacy.md").write_text(
        "---\ndescription: Legacy\n---\nOrdinary prompt."
    )
    assert await instance.initialize() is True
    generated = json.loads((instance._v2_config_dir / "opencode.json").read_text())
    assert "reviewer" in generated["agents"] and "legacy" in generated["agents"]
    assert "other" not in generated["agents"]
