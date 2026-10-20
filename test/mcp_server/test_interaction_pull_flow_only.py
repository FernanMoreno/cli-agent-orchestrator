"""Contracts for the private OpenCode interaction MCP catalog scope."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError

from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV
from cli_agent_orchestrator.mcp_server import server
from cli_agent_orchestrator.mcp_server.interaction_pull_flow_only import (
    INTERACTION_PULL_FLOW_TOOL_NAMES,
    InteractionPullFlowOnlyMiddleware,
)


def _server_with_tools() -> FastMCP:
    mcp = FastMCP("interaction-pull-flow-only-test")
    for name in (
        *INTERACTION_PULL_FLOW_TOOL_NAMES,
        "cao_list_interactions",
        "handoff",
        "evil___cao_ask_user",
    ):

        async def tool_stub() -> str:
            return "called"

        mcp.tool(name=name)(tool_stub)

    @mcp.resource("ui://hidden-resource")
    async def hidden_resource() -> str:
        return "must not be exposed"

    @mcp.prompt(name="hidden-prompt")
    async def hidden_prompt() -> str:
        return "must not be exposed"

    mcp.add_middleware(InteractionPullFlowOnlyMiddleware())
    return mcp


@pytest.mark.asyncio
async def test_lists_and_allows_exactly_the_three_pull_flow_tools() -> None:
    mcp = _server_with_tools()

    async with Client(mcp) as client:
        listed = {tool.name for tool in await client.list_tools()}
        assert listed == INTERACTION_PULL_FLOW_TOOL_NAMES
        for name in INTERACTION_PULL_FLOW_TOOL_NAMES:
            assert (await client.call_tool(name)).data == "called"
        for name in ("cao_list_interactions", "handoff", "evil___cao_ask_user"):
            with pytest.raises(ToolError, match="explicit-interaction mode"):
                await client.call_tool(name)
        assert await client.list_resources() == []
        assert await client.list_resource_templates() == []
        assert await client.list_prompts() == []
        with pytest.raises(McpError, match="Resource access is unavailable"):
            await client.read_resource("ui://hidden-resource")
        with pytest.raises(McpError, match="Prompt access is unavailable"):
            await client.get_prompt("hidden-prompt")


@pytest.mark.parametrize("marked", [False, True])
def test_private_launch_marker_is_the_only_middleware_activation_source(
    monkeypatch: pytest.MonkeyPatch, marked: bool
) -> None:
    installed: list[Any] = []

    class MiddlewareSink:
        def add_middleware(self, middleware: Any) -> None:
            installed.append(middleware)

    if marked:
        monkeypatch.setenv(_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV, "1")
    else:
        monkeypatch.delenv(_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV, raising=False)

    activated = server._install_interaction_pull_flow_middleware_if_requested(MiddlewareSink())

    assert activated is marked
    assert len(installed) == (1 if marked else 0)
    if marked:
        assert isinstance(installed[0], InteractionPullFlowOnlyMiddleware)


@pytest.mark.asyncio
async def test_unmarked_generic_server_catalog_remains_unrestricted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV, raising=False)
    baseline = {tool.name for tool in await server.mcp.local_provider.list_tools()}

    assert server._install_interaction_pull_flow_middleware_if_requested(server.mcp) is False
    after = {tool.name for tool in await server.mcp.local_provider.list_tools()}

    assert after == baseline
    assert {"cao_list_interactions", "handoff", *INTERACTION_PULL_FLOW_TOOL_NAMES} <= after
