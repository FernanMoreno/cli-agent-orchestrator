"""Bound native filters survive installation, private launch and MCP dispatch."""

import copy
import json
from test.cli.commands.test_install_opencode import _write_profile
from test.cli.commands.test_opencode_native_policy import (
    install_workspace,
    isolated_policy_store,
)
from test.providers.test_mcp_profile_preflight import _config, _validate
from test.providers.test_opencode_native_policy import ready_agent
from test.providers.test_opencode_profile_initialization import launch
from test.utils.test_opencode_policy import native, rewrite_envelope

import frontmatter
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from cli_agent_orchestrator.services import install_service
from cli_agent_orchestrator.utils.mcp_tool_policy import (
    MCP_TOOL_FILTER_ENV,
    MCP_TOOL_PROFILE_ENV,
    TOOL_PROFILES,
)
from cli_agent_orchestrator.utils.opencode_policy import (
    apply_native_profiles,
    parse_native_agent,
    render_native_agent,
    validate_native_binding,
)

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize("tools", [[], ["send_message"], ["send_message", "list_siblings"]])
def test_filter_envelope_round_trip_and_binding(native, tools):
    agent, config = native
    before = copy.deepcopy(config)
    rendered = render_native_agent(
        agent, "reviewer", {"cao": "collaboration"}, filters={"cao": tools}, source_config=config
    )
    bare, policy = parse_native_agent(rendered, "reviewer")
    assert policy.filters == {"cao": frozenset(tools)}
    validate_native_binding(bare, policy, config)
    private = apply_native_profiles(config, policy)
    assert private["mcp"]["cao"]["enabled_tools"] == sorted(tools)
    assert config == before
    with pytest.raises(TypeError):
        policy.filters["cao"] = frozenset()
    changed = rewrite_envelope(rendered, lambda d: d["filters"].update(cao=["cao_ask_user"]))
    altered, altered_policy = parse_native_agent(changed, "reviewer")
    with pytest.raises(ValueError):
        validate_native_binding(altered, altered_policy, config)


@pytest.mark.parametrize(
    "filters", [{"other": []}, {"cao": "send_message"}, {"cao": [1]}, {"cao": ["assign"]}, [], None]
)
def test_malformed_version_two_filter_is_rejected(native, filters):
    agent, config = native
    rendered = render_native_agent(
        agent, "reviewer", {"cao": "collaboration"}, filters={"cao": []}, source_config=config
    )
    changed = rewrite_envelope(rendered, lambda d: d.update(filters=filters))
    with pytest.raises(ValueError):
        parse_native_agent(changed, "reviewer")


def test_version_one_encoding_and_binding_are_preserved(native):
    agent, config = native
    original = render_native_agent(agent, "reviewer", {"cao": "interaction"}, source_config=config)
    assert (
        render_native_agent(
            agent, "reviewer", {"cao": "interaction"}, filters={}, source_config=config
        )
        == original
    )
    bare, policy = parse_native_agent(original, "reviewer")
    assert policy.filters == {}
    validate_native_binding(bare, policy, config)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("tools", [[], ["send_message"], ["unknown", "list_siblings"]])
def test_install_preserves_valid_filter_only_in_bound_agent(install_workspace, existing, tools):
    from click.testing import CliRunner

    from cli_agent_orchestrator.cli.commands.install import install

    source = install_workspace["local_store"] / "test-agent.md"
    if existing:
        _write_profile(
            source,
            mcp_servers="  cao:\n    command: cao-mcp-server\n"
            "    cao_tool_profile: interaction\n",
        )
        assert install_service.install_agent("test-agent", provider="opencode_cli").success
    _write_profile(
        source,
        mcp_servers="  cao:\n    command: cao-mcp-server\n"
        "    cao_tool_profile: collaboration\n"
        f"    enabled_tools: {json.dumps(tools)}\n",
    )
    result = CliRunner().invoke(install, ["test-agent", "--provider", "opencode_cli"])
    assert result.exit_code == 0, result.output
    config = json.loads(install_workspace["config_file"].read_text())
    bare, policy = parse_native_agent(
        (install_workspace["agents_dir"] / "test-agent.md").read_text(), "test-agent"
    )
    expected = frozenset(tools) & TOOL_PROFILES["collaboration"]
    assert policy.filters == {"cao": expected}
    validate_native_binding(bare, policy, config)
    assert "enabled_tools" not in config["mcp"]["cao"]
    assert MCP_TOOL_FILTER_ENV not in config["mcp"]["cao"].get("environment", {})


@pytest.mark.parametrize("tools", [[], ["send_message"]])
def test_preflight_accepts_only_effective_filtered_grants(tmp_path, tools):
    environment = {MCP_TOOL_PROFILE_ENV: "collaboration", MCP_TOOL_FILTER_ENV: json.dumps(tools)}
    _validate(_config(tmp_path, environment, tools))
    if tools:
        with pytest.raises(ValueError, match="grant"):
            _validate(_config(tmp_path, environment, []))


@pytest.mark.parametrize("ignored", [False, True])
def test_preflight_rejects_internal_filter_without_effective_profile(tmp_path, ignored):
    extra = {"env": {MCP_TOOL_FILTER_ENV: "[]"}} if ignored else {}
    environment = {} if ignored else {MCP_TOOL_FILTER_ENV: "[]"}
    with pytest.raises(ValueError):
        _validate(_config(tmp_path, environment, [], **extra))


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", ["nested", "flat"])
@pytest.mark.parametrize("tools", [[], ["send_message"]])
async def test_actual_native_launch_carries_filter_to_server(launch, monkeypatch, layout, tools):
    from cli_agent_orchestrator.mcp_server import server

    instance, backend, _ = launch
    config, path, agent = ready_agent(monkeypatch, "collaboration", layout)
    path.write_text(
        render_native_agent(
            agent,
            "reviewer",
            {"cao": "collaboration"},
            filters={"cao": tools},
            source_config=config,
        )
    )
    original = copy.deepcopy(config)
    assert await instance.initialize() is True
    generated = json.loads((instance._v2_config_dir / "opencode.json").read_text())
    child = generated["mcp"]["servers"]["cao"]
    assert "enabled_tools" not in child
    env = child["environment"]
    assert json.loads(env[MCP_TOOL_FILTER_ENV]) == tools
    assert config == original
    for key in (MCP_TOOL_PROFILE_ENV, MCP_TOOL_FILTER_ENV):
        monkeypatch.setenv(key, env[key])
    mcp = FastMCP("native-filter")
    dispatched = []
    for name in (*TOOL_PROFILES["collaboration"], "assign"):

        def make_tool(tool_name):
            async def tool() -> str:
                dispatched.append(tool_name)
                return "called"

            return tool

        mcp.tool(name=name)(make_tool(name))
    server._install_tool_profile_middleware_if_requested(mcp)
    async with Client(mcp) as client:
        assert {tool.name for tool in await client.list_tools()} == set(tools)
        for name in tools:
            assert (await client.call_tool(name)).data == "called"
        for name in (TOOL_PROFILES["collaboration"] | {"assign"}) - set(tools):
            with pytest.raises(ToolError):
                await client.call_tool(name)
    assert set(dispatched) == set(tools)
    backend.send_keys.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("tools", [[], ["cao_ask_user"], ["send_message"]])
async def test_filtered_interaction_opt_in_refuses_incomplete_protocol(launch, monkeypatch, tools):
    instance, backend, cao_home = launch
    config, path, agent = ready_agent(monkeypatch)
    path.write_text(
        render_native_agent(
            agent,
            "reviewer",
            {"cao": "collaboration"},
            filters={"cao": tools},
            source_config=config,
        )
    )
    instance._interaction_source_requested = True
    instance._allowed_tools = ["@cao-mcp-server"]
    instance.set_interaction_capability_file(cao_home / "capability.json")
    with pytest.raises(ValueError, match="interaction"):
        await instance.initialize()
    backend.send_keys.assert_not_called()
    assert instance._owned_v2_config_dir is None


@pytest.mark.parametrize("stage", ["pending", "mcp", "ready"])
def test_filtered_install_failure_stays_previous_or_pending_and_repairs(
    install_workspace, monkeypatch, stage
):
    from test.cli.commands.test_opencode_native_policy import policy_source

    policy_source(install_workspace)
    assert install_service.install_agent("test-agent", provider="opencode_cli").success
    path = install_workspace["agents_dir"] / "test-agent.md"
    previous = path.read_bytes()
    source = install_workspace["local_store"] / "test-agent.md"
    _write_profile(
        source,
        mcp_servers="  cao-mcp-server:\n    command: cao-mcp-server\n"
        "    cao_tool_profile: collaboration\n    enabled_tools: []\n",
    )
    writer = install_service.locked_atomic_write
    calls = 0

    def fail(*args, **kwargs):
        raise OSError("injected filtered publication failure")

    def controlled_write(*args, **kwargs):
        nonlocal calls
        calls += 1
        if (stage == "pending" and calls == 1) or (stage == "ready" and calls == 2):
            fail()
        return writer(*args, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(install_service, "locked_atomic_write", controlled_write)
        if stage == "mcp":
            fault.setattr(install_service, "_materialize_opencode_mcp", fail)
        result = install_service.install_agent("test-agent", provider="opencode_cli")
    assert not result.success
    if stage == "pending":
        assert path.read_bytes() == previous
    else:
        bare, policy = parse_native_agent(path.read_text(), "test-agent")
        assert policy.state == "pending"
        assert policy.filters == {"cao-mcp-server": frozenset()}
        with pytest.raises(ValueError):
            validate_native_binding(bare, policy, {})
    result = install_service.install_agent("test-agent", provider="opencode_cli")
    assert result.success, result.message
    bare, policy = parse_native_agent(path.read_text(), "test-agent")
    validate_native_binding(bare, policy, json.loads(install_workspace["config_file"].read_text()))
    assert policy.filters == {"cao-mcp-server": frozenset()}


def test_two_agents_keep_independent_filters_on_shared_server(install_workspace):
    expected = {"first": [], "second": ["send_message"]}
    for name, tools in expected.items():
        _write_profile(
            install_workspace["local_store"] / f"{name}.md",
            name=name,
            mcp_servers="  cao:\n    command: cao-mcp-server\n"
            "    cao_tool_profile: collaboration\n"
            f"    enabled_tools: {json.dumps(tools)}\n",
        )
        result = install_service.install_agent(name, provider="opencode_cli")
        assert result.success, result.message
    config = json.loads(install_workspace["config_file"].read_text())
    before = copy.deepcopy(config)
    for name, tools in expected.items():
        bare, policy = parse_native_agent(
            (install_workspace["agents_dir"] / f"{name}.md").read_text(), name
        )
        validate_native_binding(bare, policy, config)
        private = apply_native_profiles(config, policy)
        assert private["mcp"]["cao"]["enabled_tools"] == tools
    assert config == before


@pytest.mark.asyncio
async def test_filtered_legacy_opt_in_preserves_three_tool_server_intersection(launch, monkeypatch):
    from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV
    from cli_agent_orchestrator.mcp_server import server

    instance, _, cao_home = launch
    config, path, agent = ready_agent(monkeypatch)
    tools = sorted(TOOL_PROFILES["interaction"] | {"send_message"})
    path.write_text(
        render_native_agent(
            agent,
            "reviewer",
            {"cao": "collaboration"},
            filters={"cao": tools},
            source_config=config,
        )
    )
    instance._interaction_source_requested = True
    instance._allowed_tools = ["@cao-mcp-server"]
    instance.set_interaction_capability_file(cao_home / "capability.json")
    assert await instance.initialize() is True
    generated = json.loads((instance._v2_config_dir / "opencode.json").read_text())
    env = generated["mcp"]["servers"]["cao"]["environment"]
    for key in (MCP_TOOL_PROFILE_ENV, MCP_TOOL_FILTER_ENV, _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV):
        monkeypatch.setenv(key, env[key])
    mcp = FastMCP("filtered-legacy")
    for name in tools:

        def make_tool():
            async def tool() -> str:
                return "called"

            return tool

        mcp.tool(name=name)(make_tool())
    assert server._install_tool_profile_middleware_if_requested(mcp)
    assert server._install_interaction_pull_flow_middleware_if_requested(mcp)
    async with Client(mcp) as client:
        assert {tool.name for tool in await client.list_tools()} == TOOL_PROFILES["interaction"]
        with pytest.raises(ToolError):
            await client.call_tool("send_message")
