import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_signal_only_after_client_discovery_succeeds(tmp_path):
    from cli_agent_orchestrator.mcp_server.startup_readiness import StartupReadinessMiddleware

    path = tmp_path / "ready.json"
    middleware = StartupReadinessMiddleware(path, "current")
    context = SimpleNamespace(source="client")
    failure = AsyncMock(side_effect=RuntimeError("discovery failed"))
    with pytest.raises(RuntimeError):
        await middleware.on_list_tools(context, failure)
    assert not path.exists()
    tools = [SimpleNamespace(name="assign")]
    assert await middleware.on_list_tools(context, AsyncMock(return_value=tools)) == tools
    assert json.loads(path.read_text())["token"] == "current"
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_internal_server_listing_does_not_signal_client_readiness(tmp_path):
    from cli_agent_orchestrator.mcp_server.startup_readiness import StartupReadinessMiddleware

    path = tmp_path / "ready.json"
    middleware = StartupReadinessMiddleware(path, "current")
    await middleware.on_list_tools(SimpleNamespace(source="server"), AsyncMock(return_value=[]))
    assert not path.exists()


@pytest.mark.asyncio
async def test_real_stdio_discovery_signals_current_launch(tmp_path):
    import asyncio
    import os
    import sys
    from pathlib import Path

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    path = tmp_path / "real-ready.json"
    env = {key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ}
    env.update(
        HOME=str(tmp_path),
        CAO_HOME_DIR=str(tmp_path / "cao-home"),
        CAO_MCP_READY_FILE=str(path),
        CAO_MCP_READY_TOKEN="actual-launch",
    )

    async def discover():
        async with stdio_client(
            StdioServerParameters(
                command=sys.executable,
                args=["-c", "from cli_agent_orchestrator.mcp_server.server import main; main()"],
                env=env,
            )
        ) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert not path.exists(), "initialization alone is not tool discovery"
                tools = await session.list_tools()
                assert {"assign", "send_message"} <= {tool.name for tool in tools.tools}
                assert json.loads(path.read_text())["token"] == "actual-launch"

    await asyncio.wait_for(discover(), timeout=90)
