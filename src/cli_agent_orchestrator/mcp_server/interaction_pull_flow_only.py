"""Restrict the explicit-interaction launch to its pull-flow tools."""

from typing import Sequence

from fastmcp.exceptions import PromptError, ResourceError, ToolError
from fastmcp.prompts import Prompt, PromptResult
from fastmcp.resources import Resource, ResourceResult, ResourceTemplate
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import Tool, ToolResult
from mcp import types as mt

INTERACTION_PULL_FLOW_TOOL_NAMES = frozenset(
    {
        "cao_ask_user",
        "cao_wait_for_user_answers",
        "cao_acknowledge_user_answers",
    }
)


class InteractionPullFlowOnlyMiddleware(Middleware):
    """Expose and accept only the three explicit CAO pull-flow tools.

    The marked child is limited to asking, waiting, and acknowledging
    interaction questions.  Resources, resource templates, and prompts are
    hidden and rejected too, so optional MCP Apps registration cannot expand
    the process surface without changing the marker contract.
    """

    tool_names = INTERACTION_PULL_FLOW_TOOL_NAMES
    mode_label = "explicit-interaction mode"

    async def on_list_tools(
        self,
        context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = await call_next(context)
        return [tool for tool in tools if tool.name in self.tool_names]

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool_name = context.message.name
        if tool_name not in self.tool_names:
            raise ToolError(f"Tool '{tool_name}' is unavailable in {self.mode_label}.")
        return await call_next(context)

    async def on_list_resources(
        self,
        context: MiddlewareContext[mt.ListResourcesRequest],
        call_next: CallNext[mt.ListResourcesRequest, Sequence[Resource]],
    ) -> Sequence[Resource]:
        return ()

    async def on_read_resource(
        self,
        context: MiddlewareContext[mt.ReadResourceRequestParams],
        call_next: CallNext[mt.ReadResourceRequestParams, ResourceResult],
    ) -> ResourceResult:
        raise ResourceError(f"Resource access is unavailable in {self.mode_label}.")

    async def on_list_resource_templates(
        self,
        context: MiddlewareContext[mt.ListResourceTemplatesRequest],
        call_next: CallNext[mt.ListResourceTemplatesRequest, Sequence[ResourceTemplate]],
    ) -> Sequence[ResourceTemplate]:
        return ()

    async def on_list_prompts(
        self,
        context: MiddlewareContext[mt.ListPromptsRequest],
        call_next: CallNext[mt.ListPromptsRequest, Sequence[Prompt]],
    ) -> Sequence[Prompt]:
        return ()

    async def on_get_prompt(
        self,
        context: MiddlewareContext[mt.GetPromptRequestParams],
        call_next: CallNext[mt.GetPromptRequestParams, PromptResult],
    ) -> PromptResult:
        raise PromptError(f"Prompt access is unavailable in {self.mode_label}.")
