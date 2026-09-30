"""MCP work readers are bounded authenticated HTTP clients, not a second store."""

from unittest.mock import Mock

import pytest
import requests

from cli_agent_orchestrator.mcp_server import server, utils


def tool_function(name):
    tool = getattr(server, name, None)
    assert tool is not None, f"missing MCP tool {name}"
    return getattr(tool, "fn", tool)


@pytest.mark.asyncio
async def test_work_reader_forwards_auth_and_preserves_api_dto(monkeypatch):
    body = {"schema_version": 1, "work_item_id": "work-1", "work_state": "reconcile"}
    response = Mock()
    response.json.return_value = body
    get = Mock(return_value=response)
    monkeypatch.setattr(utils.requests, "get", get)
    monkeypatch.setattr(utils, "get_local_bearer", lambda: "test-token")
    result = await tool_function("get_work_item")("work-1")
    assert result == {"ok": True, "work": body}
    assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer test-token"}
    assert get.call_args.kwargs["timeout"] > 0
    assert get.call_args.args[0].endswith("/work-items/work-1")


@pytest.mark.asyncio
async def test_events_forward_cursor_and_explicit_gaps(monkeypatch):
    body = {
        "schema_version": 1,
        "events": [],
        "next_cursor": 5,
        "high_water": 5,
        "gaps": [{"from_sequence": 1, "through_sequence": 5}],
    }
    get = Mock(return_value=body)
    monkeypatch.setattr(utils, "get_json", get)
    result = await tool_function("get_work_events")("job-1", after_sequence=0, limit=30)
    assert result == {"ok": True, "page": body}
    get.assert_called_once_with("/jobs/job-1/events", after_sequence=0, limit=30)


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["../settings", "a/b", "a?admin=true", ""])
async def test_identity_cannot_change_http_target(monkeypatch, identity):
    get = Mock()
    monkeypatch.setattr(utils, "get_json", get)
    assert (await tool_function("get_work_item")(identity))["ok"] is False
    get.assert_not_called()


@pytest.mark.asyncio
async def test_unavailable_is_not_empty_success_and_diagnostic_is_redacted(monkeypatch):
    monkeypatch.setattr(
        utils, "get_json", Mock(side_effect=requests.ConnectionError("secret-token"))
    )
    result = await tool_function("get_work_item")("work-1")
    assert result["ok"] is False and result["code"] == "work_store_unavailable"
    assert "secret-token" not in str(result)
