"""Exact catalog and dispatch policy for an explicitly scoped MCP child."""

from cli_agent_orchestrator.mcp_server.interaction_pull_flow_only import (
    InteractionPullFlowOnlyMiddleware,
)
from cli_agent_orchestrator.utils.mcp_tool_policy import resolve_tool_profile


class ToolProfileMiddleware(InteractionPullFlowOnlyMiddleware):
    """Restrict tools and non-tool surfaces without replacing handler authority."""

    mode_label = "selected CAO tool-profile mode"

    def __init__(self, profile: str) -> None:
        super().__init__()
        self.tool_names = resolve_tool_profile(profile)
