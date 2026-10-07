"""Signal client discovery through FastMCP's middleware boundary."""

import asyncio
from pathlib import Path

from fastmcp.server.middleware import Middleware

from cli_agent_orchestrator.utils.mcp_readiness import publish_ready_signal


class StartupReadinessMiddleware(Middleware):
    def __init__(self, path: Path, token: str):
        self.path = path
        self.token = token

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        # Import and internal catalog scans are not a connected client.
        if context.source == "client":
            await asyncio.to_thread(publish_ready_signal, self.path, self.token)
        return tools
