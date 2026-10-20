"""Real-ledger HTTP/MCP transport fixtures validated against Spec 016 OpenAPI."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from test.integration.test_interaction_delegation import delegation_context  # noqa: F401
from test.integration.test_interaction_explicit_flow import interaction_http_context  # noqa: F401
from uuid import UUID

import jsonschema
import pytest
import yaml
from fastapi.encoders import jsonable_encoder

_CONTRACT = yaml.safe_load(
    (
        Path(__file__).resolve().parents[2] / "specs/016-agent-interactions/contracts/http-api.yaml"
    ).read_text(encoding="utf-8")
)

pytestmark = pytest.mark.postgres


def _validate(schema_name: str, payload) -> None:
    jsonschema.Draft202012Validator(_CONTRACT, format_checker=jsonschema.FormatChecker()).evolve(
        schema={"$ref": f"#/components/schemas/{schema_name}"}
    ).validate(jsonable_encoder(payload))


def _plain(value):
    return jsonable_encoder(value, exclude_none=False)


def test_real_http_idempotency_and_mcp_answer_shapes_match_contract(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.mcp_server import server as mcp_server
    from cli_agent_orchestrator.services import terminal_service

    context = interaction_http_context
    send_input_calls = []
    monkeypatch.setattr(
        terminal_service,
        "send_input",
        lambda *args, **kwargs: send_input_calls.append((args, kwargs)),
    )

    group_body = {
        "questions": [
            {
                "client_key": "text",
                "kind": "text",
                "prompt": "What should the summary emphasize?",
                "required": True,
            },
            {
                "client_key": "single",
                "kind": "single_choice",
                "prompt": "Choose a release channel.",
                "required": True,
                "options": [
                    {"id": "stable", "label": "Stable"},
                    {"id": "preview", "label": "Preview"},
                ],
            },
            {
                "client_key": "multi",
                "kind": "multi_choice",
                "prompt": "Which checks should run?",
                "required": True,
                "options": [
                    {"id": "unit", "label": "Unit"},
                    {"id": "integration", "label": "Integration"},
                    {"id": "lint", "label": "Lint"},
                ],
            },
            {
                "client_key": "choice-or-text",
                "kind": "choice_or_text",
                "prompt": "Choose a format or describe another.",
                "required": True,
                "allow_free_text": True,
                "options": [{"id": "brief", "label": "Brief"}],
            },
            {
                "client_key": "approval",
                "kind": "approval",
                "prompt": "Record the preference for this proposed action.",
                "required": True,
                "options": [
                    {"id": "yes", "label": "Yes"},
                    {"id": "no", "label": "No"},
                ],
            },
        ]
    }
    operation_key = "transport-contract-group-0001"
    source_headers = {
        "X-CAO-Interaction-Capability": context.source_token,
        "Idempotency-Key": operation_key,
    }
    created = context.client.post(
        "/agent/interactions/groups", headers=source_headers, json=group_body
    )
    assert created.status_code == 201, created.text
    _validate("CreatedGroup", created.json())

    replay = context.client.post(
        "/agent/interactions/groups", headers=source_headers, json=group_body
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == created.json()
    assert "replayed" not in replay.json()

    questions = created.json()["questions"]
    responses = [
        {"kind": "text", "text": "Focus on compatibility."},
        {"kind": "options", "option_ids": ["stable"]},
        {"kind": "options", "option_ids": ["unit", "integration"]},
        {"kind": "free_text", "text": "Use the existing changelog structure."},
        {"kind": "options", "option_ids": ["yes"]},
    ]
    for index, (question, answer) in enumerate(zip(questions, responses, strict=True)):
        version = question["version"]
        idempotency_key = f"transport-contract-answer-{index:04d}"
        answer_headers = {
            "If-Match": f'"{version}"',
            "Idempotency-Key": idempotency_key,
        }
        accepted = context.client.post(
            f"/interactions/{question['question_id']}/answers",
            headers=answer_headers,
            json={"expected_version": version, "response": answer},
        )
        assert accepted.status_code == 201, accepted.text
        _validate("AnswerReceipt", accepted.json())
        assert accepted.json()["replayed"] is False

        if index == 0:
            retried = context.client.post(
                f"/interactions/{question['question_id']}/answers",
                headers=answer_headers,
                json={"expected_version": version, "response": answer},
            )
            assert retried.status_code == 200, retried.text
            _validate("AnswerReceipt", retried.json())
            assert retried.json()["replayed"] is True
            assert retried.json()["answer_id"] == accepted.json()["answer_id"]

            divergent = context.client.post(
                f"/interactions/{question['question_id']}/answers",
                headers=answer_headers,
                json={
                    "expected_version": version,
                    "response": {"kind": "text", "text": "A different answer."},
                },
            )
            assert divergent.status_code == 409, divergent.text

    ready = _plain(
        asyncio.run(
            mcp_server.cao_wait_for_user_answers(
                group_id=UUID(created.json()["group_id"]), wait_seconds=0
            )
        )
    )
    _validate("WaitResult", ready)
    assert ready["state"] == "ready"
    actual_responses = {answer["question_id"]: answer["response"] for answer in ready["answers"]}
    assert actual_responses[questions[0]["question_id"]] == {
        "kind": "text",
        "text": "Focus on compatibility.",
    }
    assert actual_responses[questions[1]["question_id"]] == {
        "kind": "options",
        "option_ids": ["stable"],
    }
    assert actual_responses[questions[2]["question_id"]] == {
        "kind": "options",
        "option_ids": ["unit", "integration"],
    }
    assert actual_responses[questions[3]["question_id"]] == {
        "kind": "free_text",
        "text": "Use the existing changelog structure.",
    }
    assert actual_responses[questions[4]["question_id"]] == {
        "kind": "options",
        "option_ids": ["yes"],
    }

    ack_inputs = [
        mcp_server._InteractionAckInput(
            question_id=answer["question_id"],
            version=answer["version"],
            receipt=answer["receipt"],
        )
        for answer in ready["answers"]
    ]
    acknowledged = _plain(
        asyncio.run(
            mcp_server.cao_acknowledge_user_answers(
                group_id=UUID(created.json()["group_id"]), receipts=ack_inputs
            )
        )
    )
    assert acknowledged == {"success": True, "acknowledged": len(questions)}
    assert send_input_calls == []

    for question in questions:
        detail = context.client.get(f"/interactions/{question['question_id']}")
        assert detail.status_code == 200, detail.text
        _validate("InteractionDetail", detail.json())
        assert detail.json()["delivery_state"] == "agent_acknowledged"

    metadata = context.client.get("/interactions")
    assert metadata.status_code == 200, metadata.text
    for item in metadata.json()["items"]:
        _validate("InteractionMetadata", item)

    requests = [
        item
        for item in context.requests_seen
        if str(item["path"]).startswith("/agent/interactions")
    ]
    assert requests
    for request in requests:
        assert request["headers"]["X-CAO-Interaction-Capability"] == context.source_token
        assert context.source_token not in json.dumps(request["json"])
        assert context.source_token not in str(request["params"])


def test_native_unknown_and_credential_metadata_match_public_contract(interaction_http_context):
    """Native notices expose only the bounded public kind and safe manual reason."""
    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation

    context = interaction_http_context
    observed_at = datetime.now(timezone.utc).isoformat()
    notices = {}
    from cli_agent_orchestrator.clients import database

    sources = [
        (
            "unknown",
            "A private provider dialog",
            context.terminal_id,
            context.incarnation_id,
            context.session_name,
        )
    ]
    second_terminal = "ac010002"
    second_incarnation = "interaction-credential-incarnation"
    second_session = "interaction-credential-session"
    database.create_terminal(
        second_terminal,
        second_session,
        "worker",
        "codex",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id=second_incarnation,
        new_session_incarnation=True,
    )
    context.service.register_source(
        terminal_id=second_terminal,
        incarnation_id=second_incarnation,
        session_name=second_session,
        provider="codex",
        principal=context.owner,
    )
    sources.append(
        (
            "credential",
            "Enter the one-time code",
            second_terminal,
            second_incarnation,
            second_session,
        )
    )
    for dialog_kind, screen, terminal_id, incarnation_id, session_name in sources:
        observation = NativeInteractionObservation.manual_signal(
            screen_lines=[screen],
            terminal_id=terminal_id,
            session_name=session_name,
            window_name="worker",
        )
        assert observation is not None
        observation = replace(observation, dialog_kind=dialog_kind, observed_at=observed_at)
        receipt = context.service.observe_native(
            terminal_id,
            incarnation_id,
            1,
            observation,
        )
        notices[dialog_kind] = receipt.question_id

    page = context.client.get("/interactions?limit=100")
    assert page.status_code == 200, page.text
    by_id = {item["question_id"]: item for item in page.json()["items"]}
    expected = {"unknown": "native_unknown", "credential": "credential"}
    for internal_kind, question_id in notices.items():
        metadata = by_id[question_id]
        _validate("InteractionMetadata", metadata)
        assert metadata["kind"] == expected[internal_kind]
        assert metadata["origin"] == "native"
        assert metadata["lifecycle"] == "manual_only"
        assert metadata["can_answer"] is False
        assert "private provider dialog" not in json.dumps(metadata).lower()

        detail = context.client.get(f"/interactions/{question_id}")
        assert detail.status_code == 200, detail.text
        _validate("InteractionDetail", detail.json())
        assert detail.json()["kind"] == expected[internal_kind]
        assert detail.json()["prompt"] == (
            "A native provider dialog requires manual action in its terminal."
        )


def test_explicit_interactions_remain_available_with_agui_disabled(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        QuestionInput,
    )

    context = interaction_http_context
    monkeypatch.delenv("CAO_AGUI_ENABLED", raising=False)
    monkeypatch.delenv("CAO_MCP_APPS_ENABLED", raising=False)

    created = context.service.create_group(
        context.source_token,
        CreateGroupRequest(
            questions=[
                QuestionInput(
                    kind="text",
                    prompt="Inbox remains available while AG-UI is disabled.",
                    required=True,
                )
            ]
        ),
        "agui-disabled-inbox-operation-0001",
    )

    assert context.client.get("/agui/v1/stream").status_code == 404
    response = context.client.get("/interactions")
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["question_id"] == created.questions[0]["question_id"]


def test_agui_stream_snapshots_and_polls_real_interaction_metadata_when_idle(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        InteractionAnswerPayload,
        QuestionInput,
    )

    context = interaction_http_context
    monkeypatch.setenv("CAO_AGUI_ENABLED", "true")
    monkeypatch.delenv("CAO_MCP_APPS_ENABLED", raising=False)
    secret_prompt = "this prompt must not enter the fleet state"
    secret_answer = "this answer must not enter the fleet state"
    created = context.service.create_group(
        context.source_token,
        CreateGroupRequest(
            questions=[QuestionInput(kind="text", prompt=secret_prompt, required=True)]
        ),
        "agui-idle-projection-operation-0001",
    )
    question_id = created.questions[0]["question_id"]

    class _IdleBus:
        def __init__(self):
            self.updated = False

        def register(self, overflow_close=False):
            return object()

        def unregister(self, subscription):
            pass

        async def drain(self, subscription):
            # The stream emits STATE_SNAPSHOT before it starts draining. Make
            # the ledger transition at that exact boundary, then remain idle
            # until the one-second heartbeat projects it.
            if not self.updated:
                self.updated = True
                try:
                    context.service.accept_answer(
                        context.owner,
                        question_id,
                        1,
                        InteractionAnswerPayload(kind="text", text=secret_answer),
                        "agui-idle-projection-answer-0001",
                    )
                except BaseException as error:  # surfaced in the assertion below
                    answer_errors.append(error)
            await asyncio.sleep(1.25)
            if False:
                yield None

    bus = _IdleBus()
    monkeypatch.setattr("cli_agent_orchestrator.services.sse_bus.get_bus", lambda: bus)
    answer_errors: list[BaseException] = []
    with context.client.stream("GET", "/agui/v1/stream") as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert answer_errors == []
    assert "STATE_SNAPSHOT" in body
    assert "STATE_DELTA" in body
    delta_match = re.search(r"event: STATE_DELTA\ndata: ([^\n]+)", body)
    assert delta_match is not None
    delta_ops = json.loads(delta_match.group(1))["delta"]
    assert delta_ops
    assert all(operation["path"].startswith("/interactions") for operation in delta_ops)
    snapshot_match = re.search(r"event: STATE_SNAPSHOT\ndata: ([^\n]+)", body)
    assert snapshot_match is not None
    initial_state = json.loads(snapshot_match.group(1))["snapshot"]
    interactions = initial_state["interactions"]
    assert interactions["available"] is True
    assert interactions["truncated"] is False
    assert interactions["items"][0]["question_id"] == question_id
    assert interactions["items"][0]["delivery_state"] is None
    assert interactions["items"][0]["can_answer"] is False
    assert interactions["items"][0]["actions"] == []
    assert '"delivery_state": "accepted"' in body
    assert secret_prompt not in body
    assert secret_answer not in body
    assert re.search(r"(?:^|\n)id: ", body) is None

    ready = asyncio.run(context.service.wait_for_answers(context.source_token, created.group_id, 0))
    assert ready.state == "ready"

    class _ClosedBus:
        def register(self, overflow_close=False):
            return object()

        def unregister(self, subscription):
            pass

        async def drain(self, subscription):
            if False:
                yield None

    monkeypatch.setattr("cli_agent_orchestrator.services.sse_bus.get_bus", lambda: _ClosedBus())
    with context.client.stream("GET", "/agui/v1/stream") as response:
        assert response.status_code == 200
        reconnect_body = "".join(response.iter_text())
    reconnect_match = re.search(r"event: STATE_SNAPSHOT\ndata: ([^\n]+)", reconnect_body)
    assert reconnect_match is not None
    reconnect_state = json.loads(reconnect_match.group(1))["snapshot"]
    reconnect_item = reconnect_state["interactions"]["items"][0]
    assert reconnect_item["question_id"] == question_id
    assert reconnect_item["delivery_state"] == "returned_to_mcp"
    assert secret_prompt not in reconnect_body
    assert secret_answer not in reconnect_body


def test_agui_stream_revalidates_scope_after_projection_read_before_snapshot(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.api import main as api_main

    context = interaction_http_context
    monkeypatch.setenv("CAO_AGUI_ENABLED", "true")
    monkeypatch.delenv("CAO_MCP_APPS_ENABLED", raising=False)
    scopes = {"value": [api_main.SCOPE_READ]}
    monkeypatch.setattr(api_main, "is_auth_enabled", lambda: True)

    async def current_scopes(authorization, request):
        return list(scopes["value"])

    monkeypatch.setattr(api_main, "get_current_scopes", current_scopes)
    original_projection = context.service.projection_snapshot
    projection_reads = 0

    def revoke_during_projection_read(*, cursor=None, limit=100):
        nonlocal projection_reads
        projection_reads += 1
        page = original_projection(cursor=cursor, limit=limit)
        scopes["value"] = []
        return page

    monkeypatch.setattr(context.service, "projection_snapshot", revoke_during_projection_read)

    class _IdleBus:
        def __init__(self):
            self.unregistered = False

        def register(self, overflow_close=False):
            return object()

        def unregister(self, subscription):
            self.unregistered = True

        async def drain(self, subscription):
            await asyncio.sleep(1.25)
            if False:
                yield None

    bus = _IdleBus()
    monkeypatch.setattr("cli_agent_orchestrator.services.sse_bus.get_bus", lambda: bus)
    with context.client.stream(
        "GET",
        "/agui/v1/stream",
        headers={"Authorization": "Bearer synthetic-test-transport-token"},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert "event: STATE_SNAPSHOT" not in body
    assert "event: " not in body
    assert "event: STATE_DELTA" not in body
    assert ": keep-alive" not in body
    assert bus.unregistered is True
    assert projection_reads == 1


def test_agui_snapshot_uses_scopes_returned_after_projection_read(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.api import main as api_main

    context = interaction_http_context
    monkeypatch.setenv("CAO_AGUI_ENABLED", "true")
    monkeypatch.delenv("CAO_MCP_APPS_ENABLED", raising=False)
    scopes = {"value": [api_main.SCOPE_READ, api_main.SCOPE_WRITE]}
    monkeypatch.setattr(api_main, "is_auth_enabled", lambda: True)

    async def current_scopes(authorization, request):
        return list(scopes["value"])

    monkeypatch.setattr(api_main, "get_current_scopes", current_scopes)
    original_projection = context.service.projection_snapshot

    def downgrade_after_projection(*, cursor=None, limit=100):
        page = original_projection(cursor=cursor, limit=limit)
        scopes["value"] = [api_main.SCOPE_READ]
        return page

    monkeypatch.setattr(context.service, "projection_snapshot", downgrade_after_projection)

    class _ClosedBus:
        def __init__(self):
            self.unregistered = False

        def register(self, overflow_close=False):
            return object()

        def unregister(self, subscription):
            self.unregistered = True

        async def drain(self, subscription):
            if False:
                yield None

    bus = _ClosedBus()
    monkeypatch.setattr("cli_agent_orchestrator.services.sse_bus.get_bus", lambda: bus)
    with context.client.stream(
        "GET",
        "/agui/v1/stream",
        headers={"Authorization": "Bearer synthetic-test-transport-token"},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    snapshot_match = re.search(r"event: STATE_SNAPSHOT\ndata: ([^\n]+)", body)
    assert snapshot_match is not None
    state = json.loads(snapshot_match.group(1))["snapshot"]
    assert state["scopes"] == [api_main.SCOPE_READ]
    assert bus.unregistered is True


def test_agui_idle_deltas_track_scope_downgrade_and_upgrade(interaction_http_context, monkeypatch):
    from cli_agent_orchestrator.api import main as api_main

    context = interaction_http_context
    monkeypatch.setenv("CAO_AGUI_ENABLED", "true")
    monkeypatch.delenv("CAO_MCP_APPS_ENABLED", raising=False)
    scopes = {"value": [api_main.SCOPE_READ, api_main.SCOPE_WRITE]}
    monkeypatch.setattr(api_main, "is_auth_enabled", lambda: True)

    async def current_scopes(authorization, request):
        return list(scopes["value"])

    monkeypatch.setattr(api_main, "get_current_scopes", current_scopes)
    original_projection = context.service.projection_snapshot
    projection_reads = 0
    upgrade_delta_built = asyncio.Event()

    def change_scopes_after_projection(*, cursor=None, limit=100):
        nonlocal projection_reads
        page = original_projection(cursor=cursor, limit=limit)
        projection_reads += 1
        if projection_reads == 2:
            # Model a slow ledger read while keeping the stream's 5s request
            # bound. The fake bus below must wait for the second refresh rather
            # than assume it finishes inside a fixed sleep interval.
            time.sleep(0.35)
            scopes["value"] = [api_main.SCOPE_READ]
        elif projection_reads == 3:
            scopes["value"] = [api_main.SCOPE_READ, api_main.SCOPE_WRITE]
        return page

    monkeypatch.setattr(context.service, "projection_snapshot", change_scopes_after_projection)

    from cli_agent_orchestrator.services import agui_stream

    original_state_delta_frame = agui_stream.state_delta_frame

    def signal_after_upgrade_delta(previous, current):
        frame = original_state_delta_frame(previous, current)
        if (
            frame is not None
            and previous.get("scopes") == [api_main.SCOPE_READ]
            and current.get("scopes") == [api_main.SCOPE_READ, api_main.SCOPE_WRITE]
            and any(
                operation.get("path") == "/scopes"
                and operation.get("value") == [api_main.SCOPE_READ, api_main.SCOPE_WRITE]
                for operation in frame[1].get("delta", [])
            )
        ):
            upgrade_delta_built.set()
        return frame

    monkeypatch.setattr(agui_stream, "state_delta_frame", signal_after_upgrade_delta)

    class _IdleBus:
        def __init__(self):
            self.unregistered = False

        def register(self, overflow_close=False):
            return object()

        def unregister(self, subscription):
            self.unregistered = True

        async def drain(self, subscription):
            # No fleet event arrives; close only after the route builds the real
            # upgrade delta, with a watchdog below the client's 5s timeout.
            try:
                await asyncio.wait_for(upgrade_delta_built.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                return
            if False:
                yield None

    bus = _IdleBus()
    monkeypatch.setattr("cli_agent_orchestrator.services.sse_bus.get_bus", lambda: bus)
    with context.client.stream(
        "GET",
        "/agui/v1/stream",
        headers={"Authorization": "Bearer synthetic-test-transport-token"},
        timeout=5,
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    snapshot_match = re.search(r"event: STATE_SNAPSHOT\ndata: ([^\n]+)", body)
    assert snapshot_match is not None
    initial = json.loads(snapshot_match.group(1))["snapshot"]
    assert initial["scopes"] == [api_main.SCOPE_READ, api_main.SCOPE_WRITE]
    delta_payloads = [
        json.loads(match.group(1))["delta"]
        for match in re.finditer(r"event: STATE_DELTA\ndata: ([^\n]+)", body)
    ]
    scope_values = [
        operation["value"]
        for delta in delta_payloads
        for operation in delta
        if operation["path"] == "/scopes"
    ]
    assert scope_values == [
        [api_main.SCOPE_READ],
        [api_main.SCOPE_READ, api_main.SCOPE_WRITE],
    ]
    assert bus.unregistered is True


def test_agui_stream_keeps_fleet_state_when_interaction_projection_is_unavailable(
    interaction_http_context, monkeypatch, caplog
):
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        QuestionInput,
    )

    context = interaction_http_context
    monkeypatch.setenv("CAO_AGUI_ENABLED", "true")
    monkeypatch.delenv("CAO_MCP_APPS_ENABLED", raising=False)
    created = context.service.create_group(
        context.source_token,
        CreateGroupRequest(
            questions=[QuestionInput(kind="text", prompt="Still available in REST.", required=True)]
        ),
        "agui-unavailable-projection-operation-0001",
    )

    def unavailable_projection(*, cursor=None, limit=100):
        raise RuntimeError("private database diagnostic must not be logged")

    monkeypatch.setattr(context.service, "projection_snapshot", unavailable_projection)

    class _ClosedBus:
        def register(self, overflow_close=False):
            return object()

        def unregister(self, subscription):
            pass

        async def drain(self, subscription):
            if False:
                yield None

    monkeypatch.setattr("cli_agent_orchestrator.services.sse_bus.get_bus", lambda: _ClosedBus())
    with context.client.stream("GET", "/agui/v1/stream") as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    snapshot_match = re.search(r"event: STATE_SNAPSHOT\ndata: ([^\n]+)", body)
    assert snapshot_match is not None
    state = json.loads(snapshot_match.group(1))["snapshot"]
    assert {"sessions", "terminals", "counts", "scopes"}.issubset(state)
    assert state["interactions"] == {
        "available": False,
        "items": [],
        "next_cursor": None,
        "truncated": False,
        "event_sequence": None,
    }
    assert "private database diagnostic must not be logged" not in body
    assert "private database diagnostic must not be logged" not in caplog.text

    inbox = context.client.get("/interactions")
    assert inbox.status_code == 200, inbox.text
    assert inbox.json()["items"][0]["question_id"] == created.questions[0]["question_id"]


@pytest.mark.postgres
def test_agui_stream_stops_after_persisted_browser_session_revocation(
    interaction_http_context, monkeypatch, postgres_targets
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cli_agent_orchestrator.api import browser_auth_routes
    from cli_agent_orchestrator.api import main as api_main
    from cli_agent_orchestrator.clients.browser_auth_repository import BROWSER_AUTH_MIGRATIONS
    from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
    from cli_agent_orchestrator.models.browser_auth import BrowserAuthError
    from cli_agent_orchestrator.services.browser_auth import BrowserAuthService
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        InteractionAnswerPayload,
        QuestionInput,
    )

    context = interaction_http_context
    monkeypatch.setenv("CAO_AGUI_ENABLED", "true")
    monkeypatch.delenv("CAO_MCP_APPS_ENABLED", raising=False)
    browser_config = {
        "enabled": True,
        "installation_id": "interaction-stream-test",
        "canonical_origin": "http://127.0.0.1:9889",
        "transport_policy": "loopback_http",
    }
    binding = {
        "id": context.owner.id,
        "issuer": context.owner.issuer,
        "subject": context.owner.subject,
        "kind": context.owner.kind,
        "scopes": sorted(context.owner.scopes),
    }
    migrate_component(postgres_targets.auth, BROWSER_AUTH_MIGRATIONS)
    browser_service = BrowserAuthService(postgres_targets.auth, binding, browser_config)
    browser_service.initialize_store()
    browser_service.create_account("stream-operator", "A private passphrase 649!")
    stream_app = FastAPI()
    stream_app.state.interaction_service = context.service
    stream_app.state.browser_auth = browser_service
    stream_app.state.browser_auth_config = browser_config
    stream_app.state.browser_auth_unavailable = False
    stream_app.include_router(browser_auth_routes.router)
    stream_app.add_api_route("/agui/v1/stream", api_main.agui_stream, methods=["GET"])

    origin = "http://127.0.0.1:9889"
    browser_client = TestClient(
        stream_app,
        base_url=origin,
        client=("127.0.0.1", 50000),
        headers={"Origin": origin, "X-CAO-Browser": "1"},
    )
    login = browser_client.post(
        "/auth/login",
        json={
            "username": "stream-operator",
            "password": "A private passphrase 649!",
            "remember": True,
        },
    )
    assert login.status_code == 200, login.text
    secret = browser_client.cookies.get("cao_browser_interaction-stream-test")
    assert secret
    secret_holder = {"value": secret}

    created = context.service.create_group(
        context.source_token,
        CreateGroupRequest(
            questions=[
                QuestionInput(
                    kind="text",
                    prompt="This protected prompt is never sent after revocation.",
                    required=True,
                )
            ]
        ),
        "agui-browser-revocation-operation-0001",
    )
    revocation_codes = []
    projection_reads = 0
    revoke_on_projection_read = {"value": 1}
    original_projection = context.service.projection_snapshot

    def projection_snapshot(*, cursor=None, limit=100):
        nonlocal projection_reads
        page = original_projection(cursor=cursor, limit=limit)
        projection_reads += 1
        if projection_reads == revoke_on_projection_read["value"]:
            browser_service.logout(secret_holder["value"])
            try:
                browser_service.identity(secret_holder["value"])
            except BrowserAuthError as error:
                revocation_codes.append(error.code)
        return page

    monkeypatch.setattr(context.service, "projection_snapshot", projection_snapshot)

    class _IdleBus:
        def __init__(self, answer_before_idle=False):
            self.unregistered = False
            self.answer_before_idle = answer_before_idle

        def register(self, overflow_close=False):
            return object()

        def unregister(self, subscription):
            self.unregistered = True

        async def drain(self, subscription):
            # drain begins after the initial snapshot. For the second attach,
            # create a durable ledger delta while the cookie session is valid.
            if self.answer_before_idle:
                context.service.accept_answer(
                    context.owner,
                    created.questions[0]["question_id"],
                    1,
                    InteractionAnswerPayload(kind="text", text="safe answer"),
                    "agui-browser-revocation-answer-operation-0001",
                )
            await asyncio.sleep(1.25)
            if False:
                yield None

    # First reproduce the in-flight initial snapshot race: a real PostgreSQL
    # browser session is revoked after the projection read returns but before
    # the route emits its STATE_SNAPSHOT.
    empty_bus = _IdleBus()
    monkeypatch.setattr("cli_agent_orchestrator.services.sse_bus.get_bus", lambda: empty_bus)
    with browser_client.stream("GET", "/agui/v1/stream", timeout=5) as response:
        assert response.status_code == 200
        initial_race_body = "".join(response.iter_text())
    assert revocation_codes == ["session_revoked"]
    assert empty_bus.unregistered is True
    assert "event: STATE_SNAPSHOT" not in initial_race_body

    # Log in again for the idle-poll race. The ledger changes while the real
    # session remains valid; the projection read itself then revokes that
    # persisted session before its STATE_DELTA can be emitted.
    login = browser_client.post(
        "/auth/login",
        json={
            "username": "stream-operator",
            "password": "A private passphrase 649!",
            "remember": True,
        },
    )
    assert login.status_code == 200, login.text
    secret_holder["value"] = browser_client.cookies.get("cao_browser_interaction-stream-test")
    assert secret_holder["value"]
    projection_reads = 0
    revoke_on_projection_read["value"] = 2
    idle_bus = _IdleBus(answer_before_idle=True)
    monkeypatch.setattr("cli_agent_orchestrator.services.sse_bus.get_bus", lambda: idle_bus)
    with browser_client.stream("GET", "/agui/v1/stream", timeout=5) as response:
        assert response.status_code == 200
        idle_race_body = "".join(response.iter_text())

    assert revocation_codes == ["session_revoked", "session_revoked"]
    assert idle_bus.unregistered is True
    assert idle_race_body.count("event: STATE_SNAPSHOT") == 1
    assert idle_race_body.count("event: ") == 1
    assert "event: STATE_DELTA" not in idle_race_body
    assert ": keep-alive" not in idle_race_body
    assert "This protected prompt is never sent after revocation." not in idle_race_body


def test_agui_interaction_cursor_is_validated_before_streaming_response(
    interaction_http_context, monkeypatch
):
    context = interaction_http_context
    monkeypatch.setenv("CAO_AGUI_ENABLED", "true")
    monkeypatch.delenv("CAO_MCP_APPS_ENABLED", raising=False)

    class _EmptyBus:
        def register(self, overflow_close=False):
            return object()

        def unregister(self, subscription):
            pass

        async def drain(self, subscription):
            if False:
                yield None

    monkeypatch.setattr("cli_agent_orchestrator.services.sse_bus.get_bus", lambda: _EmptyBus())
    malformed = [
        "not-an-interaction-cursor",
        base64.urlsafe_b64encode(b"nan\n00000000-0000-0000-0000-000000000001")
        .rstrip(b"=")
        .decode("ascii"),
        base64.urlsafe_b64encode(b"123.000000\nnot-a-question-id").rstrip(b"=").decode("ascii"),
    ]
    for cursor in malformed:
        response = context.client.get("/agui/v1/stream", params={"interaction_cursor": cursor})
        assert response.status_code == 400, cursor


def test_projection_normalizes_a_delegated_question_and_native_manual_notice(
    delegation_context,
):
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation
    from cli_agent_orchestrator.services.agui.interaction_projection import (
        project_interaction_snapshot,
    )

    context = delegation_context
    context.service.delegate_question(
        context.owner,
        context.question_id,
        1,
        expected_assignment_id=context.assignment_id,
        reason="owner-reviewed clarification",
        expires_in_seconds=60,
    )
    with database.SessionLocal() as connection:
        parent = connection.get(database.TerminalModel, "ac040001")
        window_name = parent.tmux_window
    observation = NativeInteractionObservation.manual_signal(
        screen_lines=["private provider dialog text is not projected"],
        terminal_id="ac040001",
        session_name="delegation-parent",
        window_name=window_name,
    )
    assert observation is not None
    native = context.service.observe_native("ac040001", "delegation-parent-inc", 1, observation)

    snapshot = project_interaction_snapshot(context.service.projection_snapshot(limit=100))
    by_id = {item["question_id"]: item for item in snapshot["items"]}
    delegated = by_id[context.question_id]
    native_item = by_id[native.question_id]
    assert delegated["recipient"]["kind"] == "supervisor"
    assert delegated["can_answer"] is False
    assert delegated["actions"] == []
    assert native_item["kind"] == "native_unknown"
    assert native_item["lifecycle"] == "manual_only"
    assert native_item["can_answer"] is False
    assert native_item["actions"] == []
    assert "private provider dialog text is not projected" not in json.dumps(snapshot)


def test_reconcile_rejects_free_form_evidence_narrative(interaction_http_context):
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        QuestionInput,
    )

    context = interaction_http_context
    created = context.service.create_group(
        context.source_token,
        CreateGroupRequest(
            questions=[QuestionInput(kind="text", prompt="Confirm current state.", required=True)]
        ),
        "reconcile-evidence-code-operation",
    )
    question_id = created.questions[0]["question_id"]
    response = context.client.post(
        f"/interactions/{question_id}/reconcile",
        headers={"If-Match": '"1"'},
        json={
            "outcome": "remains_uncertain",
            "evidence_code": "The provider still shows this private prompt",
        },
    )
    assert response.status_code == 422


def test_all_explicit_mcp_tool_success_and_failure_shapes_are_validated(
    interaction_http_context,
):
    from cli_agent_orchestrator.mcp_server import server as mcp_server

    context = interaction_http_context
    question = mcp_server._InteractionQuestionInput(
        kind="text", prompt="What should the report emphasize?", required=True
    )
    created = _plain(
        asyncio.run(
            mcp_server.cao_ask_user(
                operation_key="typed-mcp-success-operation-01",
                questions=[question],
            )
        )
    )
    assert set(created) == {"group_id", "lifecycle", "questions", "wait_required"}
    question_id = created["questions"][0]["question_id"]
    version = created["questions"][0]["version"]

    listed = _plain(asyncio.run(mcp_server.cao_list_interactions()))
    assert set(listed) == {"items", "next_cursor"}
    assert question_id in {item["question_id"] for item in listed["items"]}

    detail = _plain(asyncio.run(mcp_server.cao_get_interaction(question_id)))
    assert detail["question_id"] == question_id
    assert detail["prompt"] == "What should the report emphasize?"

    rejected = _plain(
        asyncio.run(
            mcp_server.cao_answer_interaction(
                question_id,
                expected_version=version,
                idempotency_key="typed-mcp-delegated-answer-01",
                response=mcp_server._InteractionTextAnswer(kind="text", text="not delegated"),
            )
        )
    )
    assert rejected["success"] is False
    assert rejected["error"]["code"] == "forbidden"

    escalated = _plain(
        asyncio.run(
            mcp_server.cao_escalate_interaction(
                question_id, reason_code="human_decision", detail="A human must decide."
            )
        )
    )
    assert escalated["question_id"] == question_id
    assert escalated["recipient"]["kind"] == "human"


def test_legacy_answer_user_prompt_cannot_bypass_a_required_interaction(
    interaction_http_context, monkeypatch
):
    """The old terminal-input route is fenced by the exact source ledger edge."""
    from unittest.mock import MagicMock

    import requests

    from cli_agent_orchestrator.mcp_server import server as mcp_server
    from cli_agent_orchestrator.services import terminal_service
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        QuestionInput,
    )

    context = interaction_http_context
    group = context.service.create_group(
        context.source_token,
        CreateGroupRequest(
            questions=[QuestionInput(kind="text", prompt="Confirm the intended setting.")]
        ),
        "legacy-answer-prompt-fence-operation",
    )
    question_id = group.questions[0]["question_id"]

    class _StatusResponse:
        def raise_for_status(self):
            return None

        @staticmethod
        def json():
            return {"status": "waiting_user_answer", "provider": "codex"}

    class _InputResponse:
        def __init__(self, response):
            self.status_code = response.status_code
            self.text = response.text
            self._response = response

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code), response=self)

        def json(self):
            return self._response.json()

    attempted_inputs = []

    def legacy_get(url, **kwargs):
        assert url.endswith(f"/terminals/{context.terminal_id}")
        return _StatusResponse()

    def legacy_post(url, *, params, **kwargs):
        attempted_inputs.append((url, params))
        response = context.client.post(f"/terminals/{context.terminal_id}/input", params=params)
        return _InputResponse(response)

    monkeypatch.setattr(mcp_server.requests, "get", legacy_get)
    monkeypatch.setattr(mcp_server.requests, "post", legacy_post)
    backend = MagicMock()
    monkeypatch.setattr(terminal_service, "get_backend", lambda: backend)

    result = asyncio.run(mcp_server.answer_user_prompt(context.terminal_id, "legacy text answer"))

    assert result["success"] is False
    assert attempted_inputs
    assert "409" in result["error"]
    assert context.service.get_detail(context.owner, question_id)["lifecycle"] == "open"
    backend.send_keys.assert_not_called()
