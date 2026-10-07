"""Local workflow lifecycle calls carry the shared API credential."""

from unittest.mock import Mock, patch

import pytest

from cli_agent_orchestrator.mcp_server import server


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["start", "status", "events"])
async def test_workflow_lifecycle_uses_local_api_bearer(operation):
    response = Mock(status_code=202 if operation == "start" else 200)
    response.json.return_value = {
        "run_id": "auth-test",
        "state": "completed",
        "steps": [],
        "links": {},
    }
    response.iter_lines.return_value = iter(())
    credential = {"Authorization": "Bearer synthetic-local-token"}
    with (
        patch.object(server, "_auth_headers", return_value=credential),
        patch.object(server.requests, "post", return_value=response) as post,
        patch.object(server.requests, "get", return_value=response) as get,
    ):
        if operation == "start":
            await server.workflow_start("auth-workflow", inputs={}, run_id="auth-test")
            request = post
        elif operation == "status":
            await server.workflow_status("auth-test")
            request = get
        else:
            await server.workflow_events("auth-test", after_seq=7, max_events=1)
            request = get
    request.assert_called_once()
    assert request.call_args.args[0].startswith(server.API_BASE_URL + "/workflows/")
    headers = request.call_args.kwargs.get("headers", {})
    assert headers.get("Authorization") == credential["Authorization"]
    if operation == "events":
        assert headers["Accept"] == "text/event-stream"
        assert request.call_args.kwargs["params"]["after_seq"] == 7


@pytest.mark.asyncio
async def test_foreign_worker_completion_uses_release_token_without_local_bearer(monkeypatch):
    monkeypatch.setenv("CAO_ELASTIC_WORKER_ID", "synthetic-worker")
    monkeypatch.setenv("CAO_ELASTIC_BROKER_URL", "https://foreign.example.invalid")
    monkeypatch.setenv("CAO_ELASTIC_RELEASE_TOKEN", "synthetic-release-token")
    response = Mock(status_code=200)
    response.json.return_value = {"success": True}
    with (
        patch.object(
            server, "_auth_headers", return_value={"Authorization": "Bearer synthetic-local-token"}
        ),
        patch.object(server, "_send_message_impl", return_value={"success": True}),
        patch.object(server.requests, "post", return_value=response) as post,
    ):
        await server.complete_assignment("synthetic result")
    post.assert_called_once()
    assert (
        post.call_args.args[0]
        == "https://foreign.example.invalid/workers/synthetic-worker/complete"
    )
    assert post.call_args.kwargs["headers"] == {"X-CAO-Release-Token": "synthetic-release-token"}
