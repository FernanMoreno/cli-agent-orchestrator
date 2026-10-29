"""Actual configured production stdio catalogs, dispatch filters and authority."""

import asyncio
import json
import os
import shlex
from pathlib import Path
from test.live_provider.native_cancel_adapters import (
    claude_adapter_for_manifest,
    native_profile_text,
)

import pytest
import yaml
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import McpError

from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.providers import claude_code
from cli_agent_orchestrator.utils.mcp_tool_policy import TOOL_PROFILES

pytestmark = pytest.mark.offline_database


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["interaction", "collaboration", "coordination"])
@pytest.mark.parametrize("selection", ["full", "partial", "empty"])
async def test_real_configured_profile_stdio_catalog_and_direct_authority(
    tmp_path, monkeypatch, profile, selection
):
    adapter = claude_adapter_for_manifest(
        {"case_kind": "preflight", "profile_case": "mcp_catalog:" + profile}
    )
    parsed = AgentProfile(
        **yaml.safe_load(native_profile_text("stdio-catalog", adapter).split("---", 2)[1])
    )
    selected = (
        set(TOOL_PROFILES[profile])
        if selection == "full"
        else {"cao_wait_for_user_answers"} if selection == "partial" else set()
    )
    parsed.mcpServers["cao-mcp-server"]["enabled_tools"] = sorted(selected) + ["delete_terminal"]
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setattr(claude_code, "CAO_HOME_DIR", home)
    provider = claude_code.ClaudeCodeProvider(
        "1234abcd", "s", "w", agent_profile=parsed.name, allowed_tools=parsed.allowedTools
    )
    args = shlex.split(provider._build_claude_command(parsed))
    entry = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())["mcpServers"][
        "cao-mcp-server"
    ]
    repo = Path(__file__).resolve().parents[2]
    environment = {
        **entry["env"],
        "HOME": str(home),
        "TMPDIR": str(home),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONPATH": str(repo / "src") + os.pathsep + str(repo),
        "PYTHONPYCACHEPREFIX": os.environ["PYTHONPYCACHEPREFIX"],
        "CAO_MCP_APPS_ENABLED": "0",
        "CAO_TERMINAL_ID": "",
        "CAO_AUTH_TOKEN": "",
    }
    # No interaction capability or operator source is available to this child.
    environment.pop("CAO_INTERACTION_CAPABILITY_FILE", None)
    transport = StdioTransport(
        command=entry["command"],
        args=entry["args"],
        env=environment,
        cwd=str(home),
        keep_alive=False,
        log_file=home / "stderr.private.log",
    )
    async with asyncio.timeout(45):
        async with Client(transport) as client:
            assert {t.name for t in await client.list_tools()} == selected
            assert await client.list_resources() == []
            assert await client.list_resource_templates() == []
            assert await client.list_prompts() == []
            for name in {"delete_terminal", "unknown", "cao.work.message"} | (
                set(TOOL_PROFILES["coordination"]) - selected
            ):
                with pytest.raises(ToolError, match="selected CAO tool-profile mode"):
                    await client.call_tool(name, {})
            with pytest.raises(McpError):
                await client.read_resource("test://unavailable")
            with pytest.raises(McpError):
                await client.get_prompt("unavailable")
            if "cao_wait_for_user_answers" in selected:
                result = await client.call_tool(
                    "cao_wait_for_user_answers",
                    {"group_id": "11111111-1111-4111-8111-111111111111", "wait_seconds": 0},
                )
                data = result.structured_content
                assert isinstance(data, dict)
                if set(data) == {"result"}:
                    data = data["result"]
                assert (
                    data["success"] is False and data["error"]["code"] == "capability_unavailable"
                )
            if "list_siblings" in selected:
                result = await client.call_tool("list_siblings", {})
                assert (
                    result.data["success"] is False
                    and "CAO_TERMINAL_ID not set" in result.data["error"]
                )
            if "list_local_cao_peers" in selected:
                result = await client.call_tool("list_local_cao_peers", {})
                assert (
                    result.data["success"] is False
                    and "calling CAO terminal" in result.data["error"]
                )
