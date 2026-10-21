"""Validated v3 policy must reach private native-v1 children without broadening grants."""

import copy
import json
import sys
from test.providers.test_opencode_native_policy import ready_agent
from test.providers.test_opencode_profile_initialization import launch
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.providers import opencode_cli
from cli_agent_orchestrator.utils import opencode_config, opencode_v1
from cli_agent_orchestrator.utils.mcp_tool_policy import (
    MCP_TOOL_FILTER_ENV,
    MCP_TOOL_PROFILE_ENV,
    TOOL_PROFILES,
)
from cli_agent_orchestrator.utils.opencode_policy import render_native_agent

pytestmark = pytest.mark.offline_database


def prepare(monkeypatch, profile="collaboration", tools=None, permissions=None):
    config, path, agent = ready_agent(monkeypatch, profile)
    if permissions is not None:
        config["agent"]["reviewer"]["tools"] = {}
        agent.metadata["permission"] = permissions
    path.write_text(
        render_native_agent(
            agent,
            "reviewer",
            {"cao": profile},
            source_config=config,
            filters=None if tools is None else {"cao": tools},
            preserve_permission_order=True,
        )
    )
    return config, path, agent


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", list(TOOL_PROFILES))
@pytest.mark.parametrize("tools", [None, [], ["cao_ask_user"]])
async def test_managed_v3_native_v1_exact_private_policy(launch, monkeypatch, profile, tools):
    instance, backend, _ = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    config, path, agent = prepare(monkeypatch, profile, tools)
    # External client discovery is independently covered by the readiness module.
    monkeypatch.setattr(instance, "_wait_for_v1_cao_mcp_ready", AsyncMock(return_value=True))
    original = copy.deepcopy(config), path.read_bytes()
    assert await instance.initialize()
    private = instance._v1_config_dir
    generated = json.loads((private / "opencode.json").read_text())
    child = generated["mcp"]["cao"]
    assert child["environment"][MCP_TOOL_PROFILE_ENV] == profile
    if tools is None:
        assert MCP_TOOL_FILTER_ENV not in child["environment"]
    else:
        assert json.loads(child["environment"][MCP_TOOL_FILTER_ENV]) == tools
    assert "cao_tool_profile" not in child and "enabled_tools" not in child
    assert child["command"][1:] == ["-m", "cli_agent_orchestrator.mcp_server.server"]
    assert generated["agent"]["reviewer"]["prompt"] == agent.content
    assert generated["agent"]["reviewer"]["permission"] == {"cao*": "allow"}
    assert (config, path.read_bytes()) == original
    assert instance.interaction_capabilities["explicit_channel"] == "unsupported"
    backend.send_keys.assert_called_once()
    instance.cleanup()
    assert not private.exists() and path.read_bytes() == original[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permission",
    [
        {},
        {"*": "deny"},
        {"cao*": "ask"},
        {"cao_cao_ask_user": "allow"},
        {"cao*": "allow", "*": "deny"},
        {"cao*": {"other-resource": "allow"}},
    ],
)
async def test_v1_preflight_refuses_missing_partial_or_late_deny_before_backend(
    launch, monkeypatch, permission
):
    instance, backend, cao_home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    prepare(monkeypatch, permissions=permission)
    with pytest.raises(ValueError, match="no CAO MCP grant"):
        await instance.initialize()
    backend.send_keys.assert_not_called()
    assert instance._owned_v1_config_dir is None
    assert not list((cao_home / "opencode-v1").glob("terminal-*"))


@pytest.mark.asyncio
async def test_v1_binding_rechecked_after_shell_wait(launch, monkeypatch):
    instance, backend, _ = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    config, _, _ = prepare(monkeypatch)

    async def alter(*args, **kwargs):
        config["agent"]["reviewer"]["tools"] = {"cao*": False}
        return True

    monkeypatch.setattr(opencode_cli, "wait_for_shell", alter)
    with pytest.raises(ValueError, match="incomplete or stale"):
        await instance.initialize()
    backend.send_keys.assert_not_called()
    assert instance._owned_v1_config_dir is None


@pytest.mark.asyncio
async def test_v1_managed_interaction_requires_runtime_capability(launch, monkeypatch):
    instance, backend, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    prepare(monkeypatch)
    instance._interaction_source_requested = True
    assert instance.interaction_source_configured({})
    assert instance.interaction_capabilities["explicit_channel"] == "unsupported"
    with pytest.raises(ValueError, match="v1.*capability"):
        await instance.initialize()
    backend.send_keys.assert_not_called()


@pytest.mark.parametrize(
    "global_rules,agent_rules,accepted",
    [
        ({"*": "deny"}, {"cao*": "allow"}, True),
        ({"cao*": "allow"}, {"cao*": {"other": "deny"}}, True),
        ({"cao*": "allow"}, {"*": "deny"}, False),
        ({"cao*": "allow"}, {"cao*": {"*": "deny", "other": "allow"}}, False),
        ({"*": "deny"}, {"cao*": {"other": "deny", "*": "allow"}}, True),
    ],
)
def test_native_preflight_uses_global_agent_and_resource_order(
    tmp_path, global_rules, agent_rules, accepted
):
    (tmp_path / "opencode.json").write_text(
        json.dumps(
            {
                "permission": global_rules,
                "agent": {"reviewer": {"permission": agent_rules}},
                "mcp": {
                    "cao": {
                        "type": "local",
                        "command": [
                            sys.executable,
                            "-m",
                            "cli_agent_orchestrator.mcp_server.server",
                        ],
                        "environment": {MCP_TOOL_PROFILE_ENV: "interaction"},
                    }
                },
            }
        )
    )
    call = lambda: opencode_v1.validate_v1_mcp_configuration(
        tmp_path, agent_name="reviewer", server_name="cao"
    )
    if accepted:
        call()
    else:
        with pytest.raises(ValueError, match="no CAO MCP grant"):
            call()


def test_v1_writer_consumes_profile_and_strips_forged_policy_without_mutation(tmp_path):
    source = {
        "mcp": {
            "cao": {
                "type": "local",
                "command": ["cao-mcp-server"],
                "cao_tool_profile": "collaboration",
                "enabled_tools": [],
                "environment": {
                    MCP_TOOL_PROFILE_ENV: "knowledge_writer",
                    MCP_TOOL_FILTER_ENV: '["assign"]',
                },
            }
        }
    }
    before = copy.deepcopy(source)
    opencode_v1.write_v1_configuration(
        tmp_path,
        source_config=source,
        selected_agent_source=("reviewer", "Review."),
        session_id="probe1234",
    )
    child = json.loads((tmp_path / "opencode.json").read_text())["mcp"]["cao"]
    assert child["environment"][MCP_TOOL_PROFILE_ENV] == "collaboration"
    assert json.loads(child["environment"][MCP_TOOL_FILTER_ENV]) == []
    assert "enabled_tools" not in child and "cao_tool_profile" not in child
    assert source == before


@pytest.mark.parametrize("server_name", ["cao.mcp", "cao mcp", "cao:mcp"])
def test_native_v1_writer_normalizes_mcp_selector_names(tmp_path, server_name):
    source = {
        "mcp": {
            server_name: {
                "type": "local",
                "command": ["cao-mcp-server"],
                "cao_tool_profile": "interaction",
            }
        },
        "permission": {"*": "deny"},
    }
    before = copy.deepcopy(source)
    text = "---\ndescription: Reviewer\npermission:\n  '" + server_name + "*': allow\n---\nReview."
    opencode_v1.write_v1_configuration(
        tmp_path,
        source_config=source,
        selected_agent_source=("reviewer", text),
        session_id="probe1234",
    )
    generated = json.loads((tmp_path / "opencode.json").read_text())
    assert generated["agent"]["reviewer"]["permission"] == {"cao_mcp_*": "allow"}
    opencode_v1.validate_v1_mcp_configuration(
        tmp_path, agent_name="reviewer", server_name=server_name
    )
    assert source == before


def test_native_v1_writer_refuses_colliding_normalized_permission_actions(tmp_path):
    source = {"mcp": {"cao.mcp": {"type": "local", "command": ["cao-mcp-server"]}}}
    with pytest.raises(ValueError, match="ambiguous"):
        opencode_v1.write_v1_configuration(
            tmp_path,
            source_config=source,
            selected_agent_source=(
                "reviewer",
                "---\npermission:\n  'cao.mcp*': allow\n  'cao_mcp_*': deny\n---\nReview.",
            ),
            session_id="probe1234",
        )
    assert not (tmp_path / "opencode.json").exists()


@pytest.mark.parametrize("mutation", ["empty-command", "other-module", "ignored-env", "bad-type"])
def test_native_v1_preflight_refuses_malformed_or_noncanonical_child(tmp_path, mutation):
    child = {
        "type": "local",
        "command": [sys.executable, "-m", "cli_agent_orchestrator.mcp_server.server"],
        "environment": {MCP_TOOL_PROFILE_ENV: "interaction"},
    }
    if mutation == "empty-command":
        child["command"] = []
    elif mutation == "other-module":
        child["command"] = ["python", "-m", "untrusted.module"]
    elif mutation == "ignored-env":
        child["env"] = {MCP_TOOL_PROFILE_ENV: "knowledge_writer"}
    else:
        child["type"] = "remote"
    (tmp_path / "opencode.json").write_text(
        json.dumps(
            {"mcp": {"cao": child}, "agent": {"reviewer": {"permission": {"cao*": "allow"}}}}
        )
    )
    with pytest.raises(ValueError):
        opencode_v1.validate_v1_mcp_configuration(
            tmp_path, agent_name="reviewer", server_name="cao"
        )
