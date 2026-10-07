import json
from unittest.mock import AsyncMock, patch

import pytest

from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider


def test_cao_config_binds_fresh_readiness_identity(tmp_path, monkeypatch):
    import cli_agent_orchestrator.providers.claude_code as module

    monkeypatch.setattr(module, "CAO_HOME_DIR", tmp_path)
    profile = AgentProfile(
        name="supervisor",
        description="readiness test",
        mcpServers={"cao-mcp-server": {"command": "cao-mcp-server"}},
    )
    provider = ClaudeCodeProvider("identity", "session", "window")
    provider._build_claude_command(profile)
    first = json.loads((tmp_path / "tmp/identity.mcp.json").read_text())["mcpServers"][
        "cao-mcp-server"
    ]["env"]
    assert first["CAO_MCP_READY_FILE"]
    assert first["CAO_MCP_READY_TOKEN"]
    provider._build_claude_command(profile)
    second = json.loads((tmp_path / "tmp/identity.mcp.json").read_text())["mcpServers"][
        "cao-mcp-server"
    ]["env"]
    assert second["CAO_MCP_READY_TOKEN"] != first["CAO_MCP_READY_TOKEN"]
    assert second["CAO_MCP_READY_FILE"] != first["CAO_MCP_READY_FILE"]


@pytest.mark.asyncio
async def test_readiness_requires_current_token(tmp_path):
    provider = ClaudeCodeProvider("identity", "session", "window")
    provider._cao_mcp_ready_path = tmp_path / "signal.json"
    provider._cao_mcp_ready_token = "current"
    provider._cao_mcp_ready_path.write_text(json.dumps({"token": "old"}))
    with pytest.raises(TimeoutError, match="MCP.*discovery"):
        await provider._wait_for_cao_mcp_ready(timeout=0)
    provider._cao_mcp_ready_path.write_text(json.dumps({"token": "current"}))
    await provider._wait_for_cao_mcp_ready(timeout=0)


@pytest.mark.asyncio
async def test_initialization_does_not_succeed_before_discovery(monkeypatch):
    provider = ClaudeCodeProvider("identity", "session", "window")
    with (
        patch.object(provider, "_load_profile", return_value=None),
        patch.object(provider, "_build_claude_command", return_value="claude"),
        patch.object(provider, "_ensure_skip_bypass_prompt_setting"),
        patch.object(provider, "_handle_startup_prompts", new=AsyncMock()),
        patch.object(provider, "wait_until_input_ready", new=AsyncMock(return_value=True)),
        patch.object(
            provider,
            "_wait_for_cao_mcp_ready",
            new=AsyncMock(side_effect=TimeoutError("MCP discovery failed")),
        ) as ready,
        patch(
            "cli_agent_orchestrator.providers.claude_code.wait_for_shell",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "cli_agent_orchestrator.providers.claude_code.wait_until_status",
            new=AsyncMock(return_value=True),
        ),
        patch("cli_agent_orchestrator.providers.claude_code.get_backend"),
    ):
        with pytest.raises(TimeoutError, match="MCP discovery failed"):
            await provider.initialize()
        ready.assert_awaited_once()
        assert provider._initialized is False
