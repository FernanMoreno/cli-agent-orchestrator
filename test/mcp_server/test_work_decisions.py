"""MCP mutations remain thin authenticated HTTP clients over decision routes."""

from __future__ import annotations

from test.services.test_work_decisions import EFFECT, EVIDENCE, _decision_context

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cli_agent_orchestrator.mcp_server import server
from cli_agent_orchestrator.mcp_server import utils as mcp_utils
from cli_agent_orchestrator.security import auth


def _tool(name):
    tool = getattr(server, name, None)
    assert tool is not None, f"missing MCP tool {name}"
    return getattr(tool, "fn", tool)


@pytest.fixture
def decision_mcp(tmp_path, monkeypatch):
    from cli_agent_orchestrator.api import work_routes

    context = _decision_context(tmp_path)
    app = FastAPI()
    app.include_router(work_routes.router)
    app.dependency_overrides[work_routes.repository] = lambda: context.repository
    app.dependency_overrides[auth.get_current_principal] = lambda: context.actor
    client = TestClient(app, base_url="http://testserver")

    def post(path, body, **_kwargs):
        response = client.post(path, json=body)
        if response.status_code >= 400:
            error = requests.HTTPError(f"status {response.status_code}")
            error.response = response
            raise error
        return response.json()

    monkeypatch.setattr(mcp_utils, "post_body_json", post)
    return context, client


def _kwargs(context, **changes):
    payload = {
        "work_item_id": context.original.work_item_id,
        "attempt_id": context.original.attempt_id,
        "generation": context.original.generation,
        "idempotency_key": "shared-api-mcp-key",
        "evidence_refs": list(EVIDENCE),
        "action": "resume",
        "reason": "same canonical human evidence",
        "authorized_effects": [EFFECT],
    }
    payload.update(changes)
    return payload


@pytest.mark.asyncio
async def test_api_and_mcp_converge_through_the_same_http_store(decision_mcp):
    context, client = decision_mcp
    body = _kwargs(context)
    api = client.post(
        f"/work-items/{context.original.work_item_id}/decisions",
        json={key: value for key, value in body.items() if key != "work_item_id"},
    )
    mcp = await _tool("decide_work")(**body)

    assert api.status_code == 200, api.text
    assert mcp["ok"] is True
    assert mcp["decision"]["id"] == api.json()["id"]
    with context.repository.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM work_human_decisions").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT count(*) FROM work_events WHERE event_type='decision.recorded'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.asyncio
async def test_mcp_rejects_path_spoofing_without_an_http_write(decision_mcp, monkeypatch):
    _context, _client = decision_mcp
    post = pytest.MonkeyPatch()
    calls = []
    monkeypatch.setattr(
        mcp_utils, "post_body_json", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    result = await _tool("decide_work")(**_kwargs(_context, work_item_id="../operator-db"))
    assert result["ok"] is False
    assert result["code"] == "work_decision_invalid"
    assert calls == []


@pytest.mark.asyncio
async def test_mcp_preserves_machine_readable_conflict(decision_mcp):
    context, _client = decision_mcp
    first = await _tool("decide_work")(**_kwargs(context))
    divergent = await _tool("decide_work")(**_kwargs(context, reason="different evidence"))
    assert first["ok"] is True
    assert divergent == {
        "ok": False,
        "code": "work_decision_conflict",
        "message": "Decision evidence conflicts with durable state.",
        "retryable": False,
        "required_action": "read_current_decision",
    }
