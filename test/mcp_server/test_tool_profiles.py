"""Real FastMCP transport contracts for opt-in CAO tool profiles."""

from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError

from cli_agent_orchestrator.mcp_server import server
from cli_agent_orchestrator.mcp_server.interaction_pull_flow_only import (
    INTERACTION_PULL_FLOW_TOOL_NAMES,
)

PROFILE_ENV = "CAO_INTERNAL_MCP_TOOL_PROFILE"
COLLABORATION = INTERACTION_PULL_FLOW_TOOL_NAMES | {
    "send_message",
    "list_siblings",
    "cao_list_interactions",
    "cao_get_interaction",
    "cao_escalate_interaction",
}

pytestmark = pytest.mark.offline_database


def install(monkeypatch, mcp, profile):
    monkeypatch.setenv(PROFILE_ENV, profile)
    return server._install_tool_profile_middleware_if_requested(mcp)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile,expected",
    [
        ("interaction", INTERACTION_PULL_FLOW_TOOL_NAMES),
        ("collaboration", COLLABORATION),
    ],
)
async def test_profile_catalog_and_direct_calls_match(monkeypatch, profile, expected):
    mcp = FastMCP("profile-test")
    dispatched = []
    for name in (
        *COLLABORATION,
        "assign",
        "cao_answer_interaction",
        "memory_store",
        "complete_assignment",
        "evil___cao_ask_user",
    ):

        def make_tool(tool_name):
            async def tool() -> str:
                dispatched.append(tool_name)
                return "called"

            return tool

        mcp.tool(name=name)(make_tool(name))

    @mcp.resource("test://private/{key}")
    def resource(key: str):
        return "private"

    @mcp.prompt()
    def private_prompt():
        return "private"

    install(monkeypatch, mcp, profile)
    async with Client(mcp) as client:
        assert {tool.name for tool in await client.list_tools()} == expected
        for name in expected:
            assert (await client.call_tool(name)).data == "called"
        for name in (
            COLLABORATION
            | {
                "assign",
                "cao_answer_interaction",
                "memory_store",
                "complete_assignment",
                "evil___cao_ask_user",
                "unknown",
            }
        ) - expected:
            with pytest.raises(ToolError):
                await client.call_tool(name)
        assert set(dispatched) == expected
        assert await client.list_resources() == []
        assert await client.list_resource_templates() == []
        assert await client.list_prompts() == []
        with pytest.raises(McpError):
            await client.read_resource("test://private/value")
        with pytest.raises(McpError):
            await client.get_prompt("private_prompt")


@pytest.mark.parametrize("profile", ["", "*", "coordinator", "Collaboration", " interaction"])
def test_invalid_internal_selection_fails_closed(monkeypatch, profile):
    with pytest.raises(ValueError, match="tool profile"):
        install(monkeypatch, FastMCP("invalid"), profile)


@pytest.mark.asyncio
async def test_instances_are_isolated_and_legacy_marker_intersects(monkeypatch):
    from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV

    first, second = FastMCP("first"), FastMCP("second")
    for mcp in (first, second):
        for name in COLLABORATION:

            async def tool() -> str:
                return "called"

            mcp.tool(name=name)(tool)
    install(monkeypatch, first, "collaboration")
    install(monkeypatch, second, "interaction")
    async with Client(first) as a, Client(second) as b:
        assert {t.name for t in await a.list_tools()} == COLLABORATION
        assert {t.name for t in await b.list_tools()} == INTERACTION_PULL_FLOW_TOOL_NAMES
    monkeypatch.setenv(_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV, "1")
    server._install_interaction_pull_flow_middleware_if_requested(first)
    async with Client(first) as a:
        assert {t.name for t in await a.list_tools()} == INTERACTION_PULL_FLOW_TOOL_NAMES
        with pytest.raises(ToolError):
            await a.call_tool("send_message")


@pytest.mark.asyncio
async def test_entire_real_catalog_is_classified_before_dispatch(monkeypatch):
    """Audit every currently registered operation, only currently registered surfaces."""
    from cli_agent_orchestrator.mcp_server.tool_profiles import ToolProfileMiddleware

    policy = ToolProfileMiddleware("collaboration")
    catalog = await server.mcp.local_provider.list_tools()
    names = {t.name for t in catalog}
    assert COLLABORATION <= names
    dispatched = []

    async def call_next(context):
        dispatched.append(context.message.name)
        return "dispatched"

    for name in names | {"unknown"}:
        ctx = SimpleNamespace(message=SimpleNamespace(name=name))
        if name in COLLABORATION:
            assert await policy.on_call_tool(ctx, call_next) == "dispatched"
        else:
            with pytest.raises(ToolError):
                await policy.on_call_tool(ctx, call_next)
    assert set(dispatched) == COLLABORATION


@pytest.mark.asyncio
async def test_real_catalog_through_transport_denies_every_excluded_operation(monkeypatch):
    audit = FastMCP("actual-catalog-audit")
    audit.mount(server.mcp)
    names = {t.name for t in await server.mcp.local_provider.list_tools()}
    install(monkeypatch, audit, "collaboration")
    async with Client(audit) as client:
        assert {t.name for t in await client.list_tools()} == COLLABORATION
        for name in names - COLLABORATION:
            with pytest.raises(ToolError, match="selected CAO tool-profile mode"):
                await client.call_tool(name, {})


@pytest.mark.parametrize("profile", ["unknown", "", "interaction", None])
def test_main_installs_policy_or_aborts_before_run(monkeypatch, profile):
    from unittest.mock import Mock

    from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV

    sink = SimpleNamespace(add_middleware=Mock(), run=Mock())
    monkeypatch.setattr(server, "mcp", sink)
    monkeypatch.setenv(_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV, "1")
    if profile is None:
        monkeypatch.delenv(PROFILE_ENV, raising=False)
    else:
        monkeypatch.setenv(PROFILE_ENV, profile)
    if profile in ("unknown", ""):
        with pytest.raises(ValueError, match="tool profile"):
            server.main()
        sink.run.assert_not_called()
    else:
        server.main()
        assert sink.add_middleware.call_count == (2 if profile else 1)
        sink.run.assert_called_once()
