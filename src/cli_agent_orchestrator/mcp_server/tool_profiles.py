"""Exact catalog and dispatch policy for an explicitly scoped MCP child."""

from cli_agent_orchestrator.mcp_server.interaction_pull_flow_only import (
    InteractionPullFlowOnlyMiddleware,
)
from cli_agent_orchestrator.utils.mcp_tool_policy import resolve_tool_profile


class ToolProfileMiddleware(InteractionPullFlowOnlyMiddleware):
    """Restrict tools and non-tool surfaces without replacing handler authority."""

    mode_label = "selected CAO tool-profile mode"

    def __init__(self, profile: str, *, allowed_tools: frozenset[str] | None = None) -> None:
        super().__init__()
        profile_tools = resolve_tool_profile(profile)
        self.tool_names = profile_tools if allowed_tools is None else profile_tools & allowed_tools
