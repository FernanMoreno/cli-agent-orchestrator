"""Numeric-looking native IDs retain exact identity through MCP argument parsing."""

from unittest.mock import patch

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.tools import FunctionTool

from cli_agent_orchestrator.mcp_server import server


@pytest.mark.asyncio
@pytest.mark.parametrize("receiver_id", ["70942986", 70942986, "00942986", "ab942986", None])
async def test_send_message_normalizes_exact_numeric_terminal_id(receiver_id):
    tool = FunctionTool.from_function(server.send_message)
    with patch.object(server, "_send_message_impl", return_value={"success": True}) as send:
        await tool.run({"message": "PEER_ACK", "receiver_id": receiver_id})
    expected = str(receiver_id) if isinstance(receiver_id, int) else receiver_id
    send.assert_called_once_with(expected, "PEER_ACK")


@pytest.mark.asyncio
@pytest.mark.parametrize("receiver_id", [True, False, 70942986.0, 942986, -70942986, 170942986])
async def test_numeric_reference_never_guesses_missing_digits_or_accepts_other_scalars(receiver_id):
    tool = FunctionTool.from_function(server.send_message)
    with patch.object(server, "_send_message_impl") as send:
        with pytest.raises((ToolError, ValueError)):
            await tool.run({"message": "PEER_ACK", "receiver_id": receiver_id})
    send.assert_not_called()
