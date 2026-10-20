"""Composition test for the explicit MCP question and answer receipt path.

The MCP tools cross the real HTTP API into a real PostgreSQL InteractionService.
Only the network hop is looped back into TestClient; no terminal or provider
input is used by this source-pull protocol.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from test.postgres.interactions import postgres_interaction_store, register_interaction_source
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.postgres


def _plain(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


class _RequestsResponse:
    """Adapt TestClient responses to the requests surface used by MCP tools."""

    def __init__(self, response):
        self._response = response
        self.status_code = response.status_code
        self.text = response.text

    def json(self):
        return self._response.json()

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(str(self.status_code), response=self)


@pytest.fixture
def interaction_http_context(tmp_path, monkeypatch, postgres_interaction_store):
    """Bind the actual interaction ledger, HTTP routes, and MCP HTTP client."""
    from cli_agent_orchestrator.api.main import app
    from cli_agent_orchestrator.mcp_server import utils as mcp_utils
    from cli_agent_orchestrator.plugins import PluginRegistry
    from cli_agent_orchestrator.security import auth

    terminal_id = "ac010001"
    session_name = "interaction-explicit-flow"
    incarnation_id = "interaction-explicit-flow-incarnation"
    owner = auth._verified_principal(
        "https://interaction.integration",
        "explicit-flow-owner",
        [auth.SCOPE_READ, auth.SCOPE_WRITE, auth.SCOPE_ADMIN],
        "jwt",
    )
    registered = register_interaction_source(
        postgres_interaction_store,
        owner=owner,
        terminal_id=terminal_id,
        session_name=session_name,
        incarnation_id=incarnation_id,
    )
    service = postgres_interaction_store.service
    issued = registered.source

    from cli_agent_orchestrator.security.auth import get_current_principal

    monkeypatch.setitem(app.dependency_overrides, get_current_principal, lambda: owner)
    monkeypatch.setattr(app.state, "interaction_service", service, raising=False)
    monkeypatch.setattr(app.state, "plugin_registry", PluginRegistry(), raising=False)

    capability_file = tmp_path / "interaction-capability"
    capability_file.write_text(issued.token, encoding="ascii")
    capability_file.chmod(0o600)
    monkeypatch.setenv("CAO_TERMINAL_ID", terminal_id)
    monkeypatch.setenv("CAO_INTERACTION_CAPABILITY_FILE", str(capability_file))

    client = TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50116))
    requests_seen: list[dict[str, object]] = []

    def dispatch(method: str, url: str, **kwargs):
        parsed = urlsplit(str(url))
        path = parsed.path or "/"
        params = kwargs.get("params")
        body = kwargs.get("json")
        headers = kwargs.get("headers") or {}
        requests_seen.append(
            {
                "method": method,
                "path": path,
                "params": params,
                "json": body,
                "headers": dict(headers),
            }
        )
        response = client.request(
            method,
            path,
            params=params,
            json=body,
            headers=headers,
        )
        return _RequestsResponse(response)

    def dispatch_session_request(session, method, url, **kwargs):
        assert session.trust_env is False
        return dispatch(method, url, **kwargs)

    monkeypatch.setattr(mcp_utils.requests.Session, "request", dispatch_session_request)

    yield SimpleNamespace(
        app=app,
        client=client,
        owner=owner,
        service=service,
        repository=postgres_interaction_store.repository,
        target=postgres_interaction_store.target,
        source_token=issued.token,
        terminal_id=terminal_id,
        incarnation_id=incarnation_id,
        session_name=session_name,
        capability_file=Path(capability_file),
        requests_seen=requests_seen,
    )


def test_five_typed_questions_round_trip_through_same_mcp_source_without_terminal_input(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.mcp_server import server as mcp_server
    from cli_agent_orchestrator.services import terminal_service

    context = interaction_http_context
    send_input_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def record_send_input(*args, **kwargs):
        send_input_calls.append((args, kwargs))
        raise AssertionError(
            "explicit interaction answers must use source-pull, not terminal input"
        )

    monkeypatch.setattr(terminal_service, "send_input", record_send_input)

    question_model = mcp_server._InteractionQuestionInput
    option_model = mcp_server._InteractionOptionInput
    questions = [
        question_model(
            client_key="freeform",
            kind="text",
            prompt="¿Qué debe destacar el resumen sobre café y 日本語?",
            required=True,
        ),
        question_model(
            client_key="one-choice",
            kind="single_choice",
            prompt="Choose a release channel.",
            required=True,
            options=[
                option_model(id="stable", label="Stable"),
                option_model(id="preview", label="Preview"),
            ],
        ),
        question_model(
            client_key="many-choice",
            kind="multi_choice",
            prompt="Which checks should run?",
            required=True,
            options=[
                option_model(id="unit", label="Unit"),
                option_model(id="integration", label="Integration"),
            ],
        ),
        question_model(
            client_key="choice-or-text",
            kind="choice_or_text",
            prompt="Choose a format or describe another.",
            required=True,
            allow_free_text=True,
            options=[option_model(id="brief", label="Brief")],
        ),
        question_model(
            client_key="informational-approval",
            kind="approval",
            prompt="Record the operator's preference for this proposed action.",
            required=True,
            options=[option_model(id="yes", label="Yes"), option_model(id="no", label="No")],
        ),
    ]
    operation_key = "explicit-flow-operation-0001"
    ask_result = _plain(
        asyncio.run(
            mcp_server.cao_ask_user(
                operation_key=operation_key,
                questions=questions,
                expires_in_seconds=1800,
                wait_for_required=True,
            )
        )
    )
    assert ask_result.get("group_id")
    assert ask_result.get("success", True) is True
    group_id = ask_result["group_id"]
    created_questions = ask_result["questions"]
    assert len(created_questions) == 5
    assert all(item["required"] is True for item in created_questions)

    # A lost create response retried by the MCP caller keeps the same stable group.
    replay = _plain(
        asyncio.run(
            mcp_server.cao_ask_user(
                operation_key=operation_key,
                questions=questions,
                expires_in_seconds=1800,
                wait_for_required=True,
            )
        )
    )
    assert replay["group_id"] == group_id
    assert [item["question_id"] for item in replay["questions"]] == [
        item["question_id"] for item in created_questions
    ]

    responses = [
        {"kind": "text", "text": "Mantén el resumen en español; incluye café y 日本語."},
        {"kind": "options", "option_ids": ["stable"]},
        {"kind": "options", "option_ids": ["unit", "integration"]},
        {"kind": "free_text", "text": "Use the existing changelog structure."},
        {"kind": "options", "option_ids": ["yes"]},
    ]
    accepted = []
    for index, (question, response) in enumerate(zip(created_questions, responses, strict=True)):
        question_id = question["question_id"]
        version = question.get("version", 1)
        result = context.client.post(
            f"/interactions/{question_id}/answers",
            headers={
                "If-Match": f'"{version}"',
                "Idempotency-Key": f"explicit-flow-answer-{index:04d}",
            },
            json={"expected_version": version, "response": response},
        )
        assert result.status_code == 201, result.text
        accepted.append(result.json())
        assert accepted[-1]["delivery_state"] == "accepted"

    wait_result = _plain(
        asyncio.run(mcp_server.cao_wait_for_user_answers(group_id=UUID(group_id), wait_seconds=0))
    )
    assert wait_result["state"] == "ready"
    returned = wait_result["answers"]
    assert len(returned) == 5
    assert {item["question_id"] for item in returned} == {
        item["question_id"] for item in created_questions
    }
    responses = {item["question_id"]: item["response"] for item in returned}
    assert {response["kind"] for response in responses.values()} == {"text", "options", "free_text"}
    assert responses[created_questions[0]["question_id"]]["text"] == (
        "Mantén el resumen en español; incluye café y 日本語."
    )
    assert responses[created_questions[1]["question_id"]]["option_ids"] == ["stable"]
    assert responses[created_questions[2]["question_id"]]["option_ids"] == ["unit", "integration"]
    assert responses[created_questions[3]["question_id"]]["text"] == (
        "Use the existing changelog structure."
    )
    assert responses[created_questions[4]["question_id"]]["option_ids"] == ["yes"]
    for question in created_questions:
        detail = context.client.get(f"/interactions/{question['question_id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["delivery_state"] == "returned_to_mcp"

    # The same per-terminal capability is used for ask, wait, and ack. The MCP
    # client serves the identical operation-key retry from its local cache.
    # The capability is never copied into a request body or query string.
    interaction_requests = [
        request
        for request in context.requests_seen
        if str(request["path"]).startswith("/agent/interactions")
    ]
    assert len(interaction_requests) == 3
    for request in interaction_requests:
        headers = request["headers"]
        assert headers["X-CAO-Interaction-Capability"] == context.source_token
        assert "X-CAO-Interaction-Capability" not in json.dumps(request["json"])
        assert context.source_token not in str(request["params"])
    assert not any(
        "/terminals/" in str(request["path"]) and str(request["path"]).endswith("/input")
        for request in context.requests_seen
    )

    ack_inputs = [
        mcp_server._InteractionAckInput(
            question_id=item["question_id"],
            version=item["version"],
            receipt=item["receipt"],
        )
        for item in returned
    ]
    ack_result = _plain(
        asyncio.run(
            mcp_server.cao_acknowledge_user_answers(group_id=UUID(group_id), receipts=ack_inputs)
        )
    )
    assert ack_result == {"success": True, "acknowledged": 5}
    ack_requests = [
        request
        for request in context.requests_seen
        if str(request["path"]).endswith(f"/groups/{group_id}/ack")
    ]
    assert len(ack_requests) == 1
    assert ack_requests[0]["headers"]["X-CAO-Interaction-Capability"] == context.source_token
    assert "X-CAO-Interaction-Capability" not in json.dumps(ack_requests[0]["json"])
    for question in created_questions:
        detail = context.client.get(f"/interactions/{question['question_id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["delivery_state"] == "agent_acknowledged"
    assert send_input_calls == []
