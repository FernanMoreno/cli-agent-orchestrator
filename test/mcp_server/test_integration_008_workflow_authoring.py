"""Source authoring tools retain caller policy and authenticated HTTP boundary."""

from unittest.mock import Mock, patch

import pytest

from cli_agent_orchestrator.mcp_server import server


@pytest.mark.asyncio
async def test_create_source_has_policy_guard_before_http():
    with (
        patch.object(server, "_tool_denied_reason", return_value="tool denied"),
        patch.object(server.requests, "request") as request,
    ):
        result = await server.workflow_create("new", "INPUTS = {}\n")
    assert not result["ok"]
    request.assert_not_called()


@pytest.mark.asyncio
async def test_update_keeps_exact_cas_and_credentials():
    response = Mock(status_code=200, ok=True)
    response.json.return_value = {
        "name": "new",
        "content": "INPUTS = {}\n",
        "source_hash": "b" * 64,
    }
    with (
        patch.object(server, "_tool_denied_reason", return_value=None),
        patch.object(server, "_auth_headers", return_value={"Authorization": "Bearer local"}),
        patch.object(server.requests, "request", return_value=response) as request,
    ):
        result = await server.workflow_update("new", "INPUTS = {}\n", "a" * 64)
    assert result["ok"]
    assert request.call_args.kwargs["json"]["expected_source_hash"] == "a" * 64
    assert request.call_args.kwargs["headers"] == {"Authorization": "Bearer local"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda: server.workflow_prepare("new", {"repo": "/safe"}, {"repo": {"developer": "seed"}}),
        lambda: server.workflow_review("a" * 32),
    ],
)
async def test_prepared_tools_enforce_policy_before_transport(call):
    with (
        patch.object(server, "_tool_denied_reason", return_value="denied"),
        patch.object(server.requests, "request") as request,
    ):
        result = await call()
    assert result["kind"] == "tool_denied"
    request.assert_not_called()
