"""New native installs preserve order; old artifacts retain explicit compatibility."""

import copy
import json
from dataclasses import replace
from pathlib import Path
from test.cli.commands.test_install_opencode import _write_profile, install_workspace
from test.cli.commands.test_opencode_native_policy import isolated_policy_store
from test.providers.test_opencode_native_policy import ready_agent
from test.providers.test_opencode_profile_initialization import launch
from test.utils.test_opencode_policy import rewrite_envelope

import frontmatter
import pytest

from cli_agent_orchestrator.services import install_service
from cli_agent_orchestrator.utils.opencode_policy import (
    parse_native_agent,
    render_native_agent,
    validate_native_binding,
)
from cli_agent_orchestrator.utils.opencode_v2 import _effective_permission, _rules

pytestmark = pytest.mark.offline_database


@pytest.fixture
def ordered():
    agent = frontmatter.Post(
        "Legacy Unicode ñ, 中文.",
        description="Reviewer",
        permission={"cao*": "allow", "*": "deny"},
    )
    config = {
        "mcp": {"cao": {"type": "local", "command": ["/stable/cao-mcp-server"]}},
        "agent": {"reviewer": {"tools": {"cao*": True}}},
        "tools": {"cao*": False},
    }
    return agent, config


def render_current(agent, config, **kwargs):
    return render_native_agent(
        agent,
        "reviewer",
        {"cao": "collaboration"},
        source_config=config,
        preserve_permission_order=True,
        **kwargs,
    )


def test_ordered_render_preserves_effect_and_has_immutable_v3_identity(ordered):
    agent, config = ordered
    before = copy.deepcopy((agent.metadata, config))
    bare, policy = parse_native_agent(render_current(agent, config), "reviewer")
    assert policy.format_version == 3 and policy.filters == {}
    assert _effective_permission(_rules(bare.metadata["permission"]), "cao_sensitive") == "deny"
    validate_native_binding(bare, policy, config)
    assert (agent.metadata, config) == before
    with pytest.raises(AttributeError):
        policy.format_version = 1


@pytest.mark.parametrize("field", ["permission", "tools"])
def test_v3_rejects_native_policy_mapping_reordering(ordered, field):
    agent, config = ordered
    agent.metadata[field] = {"cao*": True, "*": False}
    bare, policy = parse_native_agent(render_current(agent, config), "reviewer")
    bare.metadata[field] = dict(reversed(list(bare.metadata[field].items())))
    with pytest.raises(ValueError, match="native MCP policy"):
        validate_native_binding(bare, policy, config)


def test_v3_rejects_nested_resource_reordering(ordered):
    agent, config = ordered
    agent.metadata["permission"] = {"bash": {"git *": "allow", "*": "deny"}}
    bare, policy = parse_native_agent(render_current(agent, config), "reviewer")
    bare.metadata["permission"]["bash"] = {"*": "deny", "git *": "allow"}
    with pytest.raises(ValueError):
        validate_native_binding(bare, policy, config)


@pytest.mark.parametrize(
    "scope", ["global_tools", "global_permission", "agent_tools", "agent_permission"]
)
def test_v3_binds_global_and_selected_agent_order(ordered, scope):
    agent, config = ordered
    target = config["agent"]["reviewer"] if scope.startswith("agent") else config
    key = "tools" if scope.endswith("tools") else "permission"
    target[key] = {"cao*": True, "*": False} if key == "tools" else {"cao*": "allow", "*": "deny"}
    bare, policy = parse_native_agent(render_current(agent, config), "reviewer")
    target[key] = dict(reversed(list(target[key].items())))
    with pytest.raises(ValueError):
        validate_native_binding(bare, policy, config)


@pytest.mark.parametrize("filters", [None, {"cao": []}, {"cao": ["send_message"]}])
def test_v3_filter_and_pending_compatibility(ordered, filters):
    agent, config = ordered
    text = render_current(agent, config, filters=filters)
    bare, policy = parse_native_agent(text, "reviewer")
    validate_native_binding(bare, policy, config)
    assert policy.filters == ({"cao": frozenset(filters["cao"])} if filters else {})
    pending = render_native_agent(
        agent, "reviewer", {"cao": "collaboration"}, preserve_permission_order=True, filters=filters
    )
    bare, policy = parse_native_agent(pending, "reviewer")
    assert policy.format_version == 3 and policy.state == "pending"
    with pytest.raises(ValueError):
        validate_native_binding(bare, policy, config)


@pytest.mark.parametrize(
    "mutation", ["missing_filters", "unknown_version", "extra", "invalid_filter"]
)
def test_v3_schema_remains_closed(ordered, mutation):
    agent, config = ordered
    text = render_current(agent, config)

    def alter(data):
        if mutation == "missing_filters":
            data.pop("filters")
        elif mutation == "unknown_version":
            data["version"] = 4
        elif mutation == "extra":
            data["extra"] = True
        else:
            data["filters"] = {"cao": ["delete_terminal"]}

    with pytest.raises(ValueError):
        parse_native_agent(rewrite_envelope(text, alter), "reviewer")


@pytest.mark.parametrize("version,filters", [(1, None), (2, {"cao": ["send_message"]})])
def test_legacy_golden_bytes_and_bindings_remain_supported(version, filters):
    agent = frontmatter.Post(
        "Legacy Unicode ñ, 中文.", description="Reviewer", permission={"*": False, "cao*": True}
    )
    config = {
        "mcp": {"cao": {"type": "local", "command": ["/stable/cao-mcp-server"]}},
        "agent": {"reviewer": {"tools": {"cao*": True}}},
        "tools": {"cao*": False},
    }
    expected = (
        Path(__file__).parent / f"fixtures/opencode-native-v{version}-legacy-golden.md"
    ).read_text()
    assert (
        render_native_agent(
            agent, "reviewer", {"cao": "collaboration"}, source_config=config, filters=filters
        )
        == expected
    )
    bare, policy = parse_native_agent(expected, "reviewer")
    validate_native_binding(bare, policy, config)
    assert policy.format_version == version


def test_legacy_metadata_order_drift_requires_reinstall(ordered):
    agent, config = ordered
    bare, policy = parse_native_agent(
        render_native_agent(agent, "reviewer", {"cao": "collaboration"}, source_config=config),
        "reviewer",
    )
    bare.metadata["permission"] = dict(reversed(list(bare.metadata["permission"].items())))
    with pytest.raises(ValueError, match="native MCP policy"):
        validate_native_binding(bare, policy, config)


def test_legacy_mixed_wildcard_gate_requires_reinstall_but_v3_accepts_it(ordered):
    agent, config = ordered
    config["agent"]["reviewer"]["tools"] = {"cao*": True, "*": False}
    bare, policy = parse_native_agent(
        render_native_agent(agent, "reviewer", {"cao": "collaboration"}, source_config=config),
        "reviewer",
    )
    with pytest.raises(ValueError):
        validate_native_binding(bare, policy, config)
    bare, policy = parse_native_agent(render_current(agent, config), "reviewer")
    validate_native_binding(bare, policy, config)


def test_actual_installer_publishes_v3_in_both_barriers(install_workspace, monkeypatch):
    _write_profile(
        install_workspace["local_store"] / "test-agent.md",
        mcp_servers="  cao:\n    command: cao-mcp-server\n    cao_tool_profile: collaboration\n",
    )
    original = install_service.locked_atomic_write
    observed = []

    def capture(path, text, **kwargs):
        if Path(path).name == "test-agent.md":
            _, policy = parse_native_agent(text, "test-agent")
            if policy is not None:
                observed.append((policy.state, policy.format_version))
        return original(path, text, **kwargs)

    monkeypatch.setattr(install_service, "locked_atomic_write", capture)
    result = install_service.install_agent("test-agent", provider="opencode_cli")
    assert result.success, result.message
    assert observed == [("pending", 3), ("ready", 3)]
    text = (install_workspace["agents_dir"] / "test-agent.md").read_text()
    bare, policy = parse_native_agent(text, "test-agent")
    validate_native_binding(bare, policy, json.loads(install_workspace["config_file"].read_text()))


@pytest.mark.parametrize("agents", [[1], {"reviewer": [1]}])
def test_legacy_malformed_agent_mapping_fails_with_policy_error(ordered, agents):
    agent, config = ordered
    bare, policy = parse_native_agent(
        render_native_agent(agent, "reviewer", {"cao": "collaboration"}, source_config=config),
        "reviewer",
    )
    config["agent"] = agents
    with pytest.raises(ValueError, match="native MCP policy"):
        validate_native_binding(bare, policy, config)


def test_boolean_format_is_not_a_valid_legacy_version(ordered):
    agent, config = ordered
    bare, policy = parse_native_agent(
        render_native_agent(agent, "reviewer", {"cao": "collaboration"}, source_config=config),
        "reviewer",
    )
    with pytest.raises(ValueError):
        validate_native_binding(bare, replace(policy, format_version=True), config)


def test_uniform_legacy_tool_gate_keeps_order_independent_compatibility(ordered):
    agent, config = ordered
    config["agent"]["reviewer"]["tools"] = {"cao_z": True, "cao_a": True}
    bare, policy = parse_native_agent(
        render_native_agent(agent, "reviewer", {"cao": "collaboration"}, source_config=config),
        "reviewer",
    )
    config["agent"]["reviewer"]["tools"] = {"cao_a": True, "cao_z": True}
    validate_native_binding(bare, policy, config)


@pytest.mark.parametrize("version", [1, 2])
def test_ordered_digest_cannot_be_downgraded_to_legacy(ordered, version):
    agent, config = ordered
    text = render_current(agent, config, filters={"cao": ["send_message"]})

    def downgrade(data):
        data["version"] = version
        if version == 1:
            data.pop("filters")

    bare, policy = parse_native_agent(rewrite_envelope(text, downgrade), "reviewer")
    with pytest.raises(ValueError):
        validate_native_binding(bare, policy, config)


def test_ordered_binding_does_not_include_unrelated_agents_or_servers(ordered):
    agent, config = ordered
    bare, policy = parse_native_agent(render_current(agent, config), "reviewer")
    config["agent"]["other"] = {"tools": {"*": True}}
    config["mcp"]["other"] = {"type": "local", "command": ["other-server"]}
    validate_native_binding(bare, policy, config)


@pytest.mark.parametrize("field", ["tools", "permission"])
def test_ordered_digest_distinguishes_empty_mapping_from_sequence(ordered, field):
    agent, config = ordered
    config[field] = {}
    bare, policy = parse_native_agent(render_current(agent, config), "reviewer")
    config[field] = []
    with pytest.raises(ValueError):
        validate_native_binding(bare, policy, config)


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", ["nested", "flat"])
@pytest.mark.parametrize("tools", [None, [], ["send_message"]])
async def test_v3_reaches_production_private_launch_with_exact_filter(
    launch, monkeypatch, layout, tools
):
    from cli_agent_orchestrator.providers import opencode_cli
    from cli_agent_orchestrator.utils.mcp_tool_policy import (
        MCP_TOOL_FILTER_ENV,
        MCP_TOOL_PROFILE_ENV,
    )

    instance, backend, _ = launch
    config, path, agent = ready_agent(monkeypatch, "collaboration", layout)
    path.write_text(
        render_native_agent(
            agent,
            "reviewer",
            {"cao": "collaboration"},
            source_config=config,
            filters=None if tools is None else {"cao": tools},
            preserve_permission_order=True,
        )
    )
    snapshot = opencode_cli.OpenCodeCliProvider.load_native_profile("reviewer")
    assert snapshot.policy.format_version == 3
    instance.prepare_native_profile(snapshot)
    assert await instance.initialize()
    generated = json.loads((instance._v2_config_dir / "opencode.json").read_text())
    environment = generated["mcp"]["servers"]["cao"]["environment"]
    assert environment[MCP_TOOL_PROFILE_ENV] == "collaboration"
    if tools is None:
        assert MCP_TOOL_FILTER_ENV not in environment
    else:
        assert json.loads(environment[MCP_TOOL_FILTER_ENV]) == tools
    assert instance._native_tool_policy.format_version == 3
    backend.send_keys.assert_called_once()
