"""Bypass the native client and check the generated text filter over real MCP stdio."""

import asyncio
import shlex
import sys
import tomllib
from test.live_provider import interaction_cli_journey as journey

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.providers.codex import CodexProvider

pytestmark = pytest.mark.offline_database


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [[], ["cao_ask_user", "assign"]])
async def test_codex_text_subset_blocks_direct_stdio_calls(tmp_path, monkeypatch, enabled):
    child_env = journey._mcp_child_environment(tmp_path, 1, "abcdef01")
    child_env["CAO_MCP_APPS_ENABLED"] = "0"
    profile = AgentProfile(
        name="test",
        description="test",
        mcpServers={
            "cao": {
                "command": "cao-mcp-server",
                "cao_tool_profile": "interaction",
                "enabled_tools": enabled,
                "env": child_env,
            }
        },
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.providers.codex.load_agent_profile", lambda _: profile
    )
    command = CodexProvider("abcdef01", "private", "worker", "test")._build_codex_command()
    overrides = [v for v in shlex.split(command) if v.startswith("mcp_servers.cao.env=")]
    assert len(overrides) == 1
    actual_env = tomllib.loads(overrides[0])["mcp_servers"]["cao"]["env"]

    async def exercise():
        async with stdio_client(
            StdioServerParameters(
                command=sys.executable,
                args=["-m", "cli_agent_orchestrator.mcp_server.server"],
                env=actual_env,
            )
        ) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                expected = {"cao_ask_user"} if enabled else set()
                assert {tool.name for tool in (await session.list_tools()).tools} == expected
                denied = await session.call_tool(
                    "cao_wait_for_user_answers",
                    {"group_id": "2e1fc625-a391-4bfd-a62d-4dddbcbf70a3", "wait_seconds": 0},
                )
                assert denied.isError is True
                assert any(
                    "unavailable in selected CAO tool-profile mode" in block.text
                    for block in denied.content
                    if block.type == "text"
                )
                ask = await session.call_tool(
                    "cao_ask_user",
                    {
                        "operation_key": "raw-text-filter-operation-0001",
                        "questions": [
                            {
                                "kind": "text",
                                "prompt": "Synthetic stdio filter test",
                                "required": True,
                            }
                        ],
                        "wait_for_required": False,
                    },
                )
                if enabled:
                    assert ask.isError is not True
                    assert (
                        journey._safe_mcp_result_error_code(ask.model_dump(by_alias=True))
                        == "capability_unavailable"
                    )
                else:
                    assert ask.isError is True
                assert not (await session.list_resources()).resources
                assert not (await session.list_resource_templates()).resourceTemplates
                assert not (await session.list_prompts()).prompts
                with pytest.raises(McpError):
                    await session.read_resource("cao://widget/topology")
                with pytest.raises(McpError):
                    await session.get_prompt("catalog_forbidden_prompt")

    await asyncio.wait_for(exercise(), timeout=60)
