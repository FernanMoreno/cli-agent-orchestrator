"""Internal filter parsing is strict, bounded and immutable per MCP child."""

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from cli_agent_orchestrator.mcp_server import server
from cli_agent_orchestrator.utils.mcp_tool_policy import INTERACTION_TOOLS

PROFILE_ENV = "CAO_INTERNAL_MCP_TOOL_PROFILE"
FILTER_ENV = "CAO_INTERNAL_MCP_ENABLED_TOOLS"
pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize(
    "value",
    [
        "",
        "null",
        "{}",
        '"cao_ask_user"',
        "[true]",
        "[1]",
        '["assign"]',
        '["unknown"]',
        pytest.param("[" + " " * 5000 + "]", id="oversized"),
    ],
)
def test_invalid_internal_filter_aborts_startup(monkeypatch, value):
    monkeypatch.setenv(PROFILE_ENV, "interaction")
    monkeypatch.setenv(FILTER_ENV, value)
    with pytest.raises(ValueError, match="tool filter"):
        server._install_tool_profile_middleware_if_requested(FastMCP("invalid"))


def test_filter_without_explicit_profile_aborts_startup(monkeypatch):
    monkeypatch.delenv(PROFILE_ENV, raising=False)
    monkeypatch.setenv(FILTER_ENV, "[]")
    with pytest.raises(ValueError, match="tool filter"):
        server._install_tool_profile_middleware_if_requested(FastMCP("invalid"))


@pytest.mark.asyncio
async def test_filters_are_frozen_and_independent_and_keep_legacy_intersection(monkeypatch):
    from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV

    first, second = FastMCP("first"), FastMCP("second")
    for mcp in (first, second):
        for name in INTERACTION_TOOLS | {"list_siblings"}:

            async def tool() -> str:
                return "called"

            mcp.tool(name=name)(tool)
    monkeypatch.setenv(PROFILE_ENV, "collaboration")
    monkeypatch.setenv(FILTER_ENV, '["cao_ask_user", "list_siblings"]')
    server._install_tool_profile_middleware_if_requested(first)
    monkeypatch.setenv(FILTER_ENV, "[]")
    server._install_tool_profile_middleware_if_requested(second)
    monkeypatch.setenv(FILTER_ENV, '["assign"]')
    async with Client(first) as a, Client(second) as b:
        assert {t.name for t in await a.list_tools()} == {"cao_ask_user", "list_siblings"}
        assert await b.list_tools() == []
        with pytest.raises(ToolError):
            await b.call_tool("cao_ask_user")
    monkeypatch.setenv(_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV, "1")
    server._install_interaction_pull_flow_middleware_if_requested(first)
    async with Client(first) as a:
        assert {t.name for t in await a.list_tools()} == {"cao_ask_user"}
        with pytest.raises(ToolError):
            await a.call_tool("list_siblings")
