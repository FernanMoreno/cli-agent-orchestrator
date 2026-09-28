"""Knowledge tools cross HTTP, never infer an identity or fall back to memory."""

import importlib
import importlib.util
from unittest.mock import Mock

import pytest
import requests

from cli_agent_orchestrator.mcp_server import utils


def module():
    name = "cli_agent_orchestrator.mcp_server.knowledge_tools"
    assert importlib.util.find_spec(name), "knowledge HTTP tools missing"
    return importlib.import_module(name)


@pytest.mark.asyncio
async def test_readers_registered_with_running_mcp_server():
    from cli_agent_orchestrator.mcp_server.server import mcp

    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert {"knowledge_read", "knowledge_instructions"} <= names


@pytest.mark.asyncio
async def test_read_forwards_selectors_and_existing_bearer(monkeypatch):
    response = Mock()
    response.json.return_value = {"schema_version": 1, "record_id": "record", "revision": 2}
    get = Mock(return_value=response)
    monkeypatch.setattr(utils.requests, "get", get)
    monkeypatch.setattr(utils, "get_local_bearer", lambda: "verified-bearer")
    result = await module().knowledge_read("record", "job", "grant", 3, revision=2)
    assert result == {"ok": True, "knowledge": response.json.return_value}
    assert get.call_args.args[0].endswith("/v1/knowledge/records/record/revisions/2")
    assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer verified-bearer"}
    assert get.call_args.kwargs["params"] == {
        "job_id": "job",
        "grant_id": "grant",
        "grant_revision": 3,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("record", ["../settings", "..", "a/b", "a?admin=true"])
async def test_invalid_record_never_changes_http_target(monkeypatch, record):
    get = Mock()
    monkeypatch.setattr(utils, "get_json", get)
    result = await module().knowledge_read(record, "job", "grant", 1)
    assert result["ok"] is False and result["retryable"] is False
    get.assert_not_called()


@pytest.mark.asyncio
async def test_instruction_read_is_separate_from_evidence(monkeypatch):
    get = Mock(return_value=[])
    monkeypatch.setattr(utils, "get_json", get)
    result = await module().knowledge_instructions("project", "project-1", "job", "grant", 1)
    assert result == {"ok": True, "instructions": []}
    get.assert_called_once_with(
        "/v1/knowledge/instructions",
        job_id="job",
        grant_id="grant",
        grant_revision=1,
        scope="project",
        scope_id="project-1",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 409, 503])
async def test_authority_errors_are_redacted_and_not_success(monkeypatch, status):
    response = requests.Response()
    response.status_code = status
    monkeypatch.setattr(
        utils,
        "get_json",
        Mock(
            side_effect=requests.HTTPError(
                "credential-in-server-diagnostic",
                response=response,
            )
        ),
    )
    result = await module().knowledge_read("record", "job", "grant", 1)
    assert result["ok"] is False
    assert result["retryable"] is (status >= 500)
    assert "credential" not in str(result)


@pytest.mark.asyncio
async def test_remote_failure_does_not_return_empty_local_knowledge(monkeypatch):
    get = Mock(side_effect=requests.ConnectionError("secret-token"))
    monkeypatch.setattr(utils, "get_json", get)
    result = await module().knowledge_instructions("job", "job", "job", "grant", 1)
    assert result["ok"] is False and result["code"] == "knowledge_authority_unavailable"
    assert "secret-token" not in str(result)
    assert get.call_count == 1
