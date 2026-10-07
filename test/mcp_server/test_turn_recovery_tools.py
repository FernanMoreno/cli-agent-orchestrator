from unittest.mock import patch

import pytest

from cli_agent_orchestrator.mcp_server import server


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,action", [("verify_terminal_turn", "verify"), ("cancel_terminal_turn", "cancel")]
)
async def test_recovery_tool_preserves_diagnosis_and_generation(name, action):
    tool = getattr(server, name)
    fn = getattr(tool, "fn", tool)
    diagnosis = {
        "success": False,
        "state": "reconcile",
        "generation": "b" * 32,
        "allowed_actions": ["verify"],
    }
    with (
        patch.object(server, "_tool_denied_reason", return_value=None),
        patch.object(server, "_turn_action_impl", return_value=diagnosis) as impl,
    ):
        result = await fn("term", "a" * 32)
    assert result == diagnosis
    impl.assert_called_once_with("term", "a" * 32, action)


@pytest.mark.asyncio
async def test_inspection_tool_fails_closed_on_tool_denial():
    tool = server.inspect_terminal_turn
    fn = getattr(tool, "fn", tool)
    with (
        patch.object(server, "_tool_denied_reason", return_value="denied"),
        patch.object(server, "_inspect_turn_impl") as impl,
    ):
        assert (await fn("term"))["success"] is False
    impl.assert_not_called()
