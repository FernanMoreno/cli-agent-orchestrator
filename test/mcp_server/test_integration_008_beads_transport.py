from unittest.mock import Mock, patch

import pytest

from cli_agent_orchestrator.mcp_server import server


@pytest.mark.asyncio
async def test_beads_write_denial_precedes_http_and_empty_policy_cannot_mutate():
    with (
        patch.object(server, "_tool_denied_reason", return_value="denied"),
        patch.object(server.requests, "request") as transport,
    ):
        value = await server.mutate_bead("repo", "stable-key", "create", {"title": "x"})
        assert value["kind"] == "tool_denied"
        transport.assert_not_called()


@pytest.mark.asyncio
async def test_exact_beads_body_auth_and_uncertain_response():
    response = Mock(ok=True)
    response.json.return_value = {"operation_id": "b1", "state": "uncertain"}
    with (
        patch.object(server, "_tool_denied_reason", return_value=None),
        patch.object(server, "_auth_headers", return_value={"Authorization": "Bearer operator"}),
        patch.object(server.requests, "request", return_value=response) as transport,
    ):
        value = await server.mutate_bead("repo", "stable-key", "create", {"title": "x"})
        assert value["state"] == "uncertain"
        assert transport.call_args.kwargs["headers"] == {"Authorization": "Bearer operator"}
        assert transport.call_args.args[1].endswith("/beads/workspaces/repo/mutations")
        assert transport.call_args.kwargs["json"]["operation_key"] == "stable-key"
