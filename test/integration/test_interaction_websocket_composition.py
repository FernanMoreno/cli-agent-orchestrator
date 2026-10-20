"""Real PostgreSQL interaction state composed with the terminal WebSocket route.

The attach backend is intentionally a small fixture: ``prepare_web_attach``
starts ``cat`` in the real PTY allocated by the production WebSocket handler.
This exercises the input fence and OS write boundary, not a provider CLI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import replace
from test.integration.test_interaction_explicit_flow import interaction_http_context  # noqa: F401
from urllib.parse import quote

import pytest
from starlette.websockets import WebSocketDisconnect

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.skipif(os.name != "posix", reason="PTY WebSocket composition requires POSIX"),
]


class _CatAttachBackend:
    """Fixture transport: the real PTY subprocess echoes bytes through cat."""

    def __init__(self, cat_path: str):
        self.cat_path = cat_path
        self.attach_calls: list[tuple[str, str]] = []

    def prepare_web_attach(self, session_name: str, window_name: str) -> list[str]:
        self.attach_calls.append((session_name, window_name))
        return [self.cat_path]


@pytest.fixture
def real_interaction_websocket(interaction_http_context, monkeypatch):
    """Use the fixture's actual ledger and the production FastAPI WS route."""
    from cli_agent_orchestrator.api import main as main_module
    from cli_agent_orchestrator.services import terminal_service

    cat_path = shutil.which("cat")
    if cat_path is None:
        pytest.skip("cat is required for the fixture PTY transport")

    context = interaction_http_context
    backend = _CatAttachBackend(cat_path)
    monkeypatch.setattr(terminal_service, "_interaction_service", lambda: context.service)
    monkeypatch.setattr(main_module, "get_backend", lambda: backend)

    # Make the intended local-only test posture explicit. The source owner is
    # the fixture's verified principal; the native PTY transport is restricted
    # to TestClient's loopback peer and the exact source incarnation.
    for name in ("AUTH0_DOMAIN", "CAO_AUTH_JWKS_URI"):
        monkeypatch.delenv(name, raising=False)

    master_fds: set[int] = set()
    original_openpty = main_module.pty.openpty
    original_write = main_module.os.write
    original_popen = main_module.subprocess.Popen
    pty_writes: list[bytes] = []
    cat_processes: list[subprocess.Popen] = []

    def openpty_and_record_master():
        master_fd, slave_fd = original_openpty()
        master_fds.add(master_fd)
        return master_fd, slave_fd

    def write_and_record_pty_bytes(fd: int, data: bytes) -> int:
        result = original_write(fd, data)
        if fd in master_fds:
            pty_writes.append(bytes(data[:result]))
        return result

    def popen_and_track_cat(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        if args and args[0] == [cat_path]:
            cat_processes.append(process)
        return process

    monkeypatch.setattr(main_module.pty, "openpty", openpty_and_record_master)
    monkeypatch.setattr(main_module.os, "write", write_and_record_pty_bytes)
    monkeypatch.setattr(main_module.subprocess, "Popen", popen_and_track_cat)

    def connect(*, expected_incarnation_id: str | None = None):
        query = (
            f"?expected_incarnation_id={quote(expected_incarnation_id, safe='')}"
            if expected_incarnation_id is not None
            else ""
        )
        return context.client.websocket_connect(
            f"ws://localhost/terminals/{context.terminal_id}/ws{query}",
            headers={"Origin": "http://localhost"},
        )

    return {
        "context": context,
        "backend": backend,
        "connect": connect,
        "pty_writes": pty_writes,
        "master_fds": master_fds,
        "cat_processes": cat_processes,
    }


def _new_group(context, *, operation_key: str, questions):
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        QuestionInput,
    )

    assert all(isinstance(question, QuestionInput) for question in questions)
    return context.service.create_group(
        context.source_token,
        CreateGroupRequest(questions=tuple(questions), wait_for_required=False),
        operation_key,
    )


def _credential_question(*, required: bool = True):
    from cli_agent_orchestrator.services.interactions.models import QuestionInput

    return QuestionInput(
        kind="text",
        prompt="Please provide your API key to continue.",
        required=required,
    )


def _answer_count(context, question_id: str | None = None) -> int:
    with context.service.repository.read_connection() as connection:
        if question_id is None:
            row = connection.execute("SELECT count(*) FROM interaction_answers").fetchone()
        else:
            row = connection.execute(
                "SELECT count(*) FROM interaction_answers WHERE question_id=%s",
                (question_id,),
            ).fetchone()
    return int(row[0])


def _question_row(context, question_id: str):
    with context.service.repository.read_connection() as connection:
        return connection.execute(
            "SELECT question_id,group_id,kind,lifecycle,turn_generation,prompt_content,options_json "
            "FROM interaction_questions WHERE question_id=%s",
            (question_id,),
        ).fetchone()


def _assert_cat_processes_stopped(processes: list[subprocess.Popen], *, count: int) -> None:
    assert len(processes) == count
    for process in processes:
        process.wait(timeout=4.0)
        assert process.poll() is not None


def _assert_blocked_before_write(
    websocket,
    pty_writes: list[bytes],
    cat_processes: list[subprocess.Popen],
) -> None:
    with pytest.raises(WebSocketDisconnect) as disconnected:
        websocket.receive_text()
    assert disconnected.value.code == 4409
    assert pty_writes == []
    _assert_cat_processes_stopped(cat_processes, count=1)


def test_required_explicit_credential_closes_real_websocket_before_pty_write(
    real_interaction_websocket,
):
    ws_context = real_interaction_websocket
    context = ws_context["context"]
    group = _new_group(
        context,
        operation_key="ws-explicit-credential-required-001",
        questions=[_credential_question()],
    )
    question = group.questions[0]
    stored = _question_row(context, question["question_id"])
    assert stored["kind"] == "credential"
    assert stored["lifecycle"] == "manual_only"
    assert context.service.has_required_wait(context.terminal_id, context.incarnation_id)

    safe_probe = "SYNTHETIC_SAFE_INPUT_MUST_NOT_REACH_PTY\n"
    with ws_context["connect"](expected_incarnation_id=context.incarnation_id) as websocket:
        websocket.send_json({"type": "input", "data": safe_probe})
        _assert_blocked_before_write(
            websocket, ws_context["pty_writes"], ws_context["cat_processes"]
        )

    assert ws_context["backend"].attach_calls == [(context.session_name, "worker")]
    _assert_cat_processes_stopped(ws_context["cat_processes"], count=1)
    assert _answer_count(context, question["question_id"]) == 0
    assert not any(safe_probe.encode() in chunk for chunk in ws_context["pty_writes"])


def test_native_credential_manual_notice_allows_exact_incarnation_terminal_input(
    real_interaction_websocket,
):
    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation

    ws_context = real_interaction_websocket
    context = ws_context["context"]
    safe_screen_marker = "SAFE_SYNTHETIC_CREDENTIAL_SCREEN_MARKER_NOT_A_SECRET"
    observation = NativeInteractionObservation.manual_signal(
        screen_lines=[f"Provider credential dialog fixture: {safe_screen_marker}"],
        terminal_id=context.terminal_id,
        session_name=context.session_name,
        window_name="worker",
    )
    assert observation is not None
    observation = replace(
        observation,
        dialog_kind="credential",
        prompt=safe_screen_marker,
        options=({"id": "fixture", "label": safe_screen_marker},),
        answer_capability="none",
    )
    before = context.service.source_status(context.terminal_id, context.incarnation_id)
    receipt = context.service.observe_native(
        context.terminal_id,
        context.incarnation_id,
        before.current_generation,
        observation,
    )
    assert receipt.lifecycle == "manual_only"
    assert receipt.response_capability == "manual_only"
    row = _question_row(context, receipt.question_id)
    assert row["group_id"] is None
    assert row["kind"] == "credential"
    assert row["lifecycle"] == "manual_only"
    assert (
        row["prompt_content"] == "A native provider dialog requires manual action in its terminal."
    )
    assert json.loads(row["options_json"]) == []

    safe_input = "SAFE_NATIVE_MANUAL_INPUT_NOT_A_SECRET\n"
    with ws_context["connect"](expected_incarnation_id=context.incarnation_id) as websocket:
        try:
            websocket.send_json({"type": "input", "data": safe_input})
            # The fixture backend runs the real cat subprocess in the real PTY;
            # the wrapping spy records only writes to that PTY's master fd.
            deadline = time.monotonic() + 3.0
            while not ws_context["pty_writes"] and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            websocket.close()
            _assert_cat_processes_stopped(ws_context["cat_processes"], count=1)

    assert ws_context["backend"].attach_calls == [(context.session_name, "worker")]
    _assert_cat_processes_stopped(ws_context["cat_processes"], count=1)
    assert b"".join(ws_context["pty_writes"]) == safe_input.encode()
    after = context.service.source_status(context.terminal_id, context.incarnation_id)
    assert after.current_generation == before.current_generation + 1
    assert _answer_count(context, receipt.question_id) == 0
    assert _question_row(context, receipt.question_id)["lifecycle"] == "stale"

    with context.service.repository.read_connection() as connection:
        persisted_text = json.dumps(
            {
                table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                for table in (
                    "interaction_questions",
                    "interaction_question_versions",
                    "interaction_events",
                    "interaction_answers",
                )
            },
            default=str,
        )
    assert safe_screen_marker not in persisted_text
    assert safe_input.strip() not in persisted_text


def test_replaced_incarnation_is_rejected_before_backend_attach(
    real_interaction_websocket,
):
    from cli_agent_orchestrator.clients import database

    ws_context = real_interaction_websocket
    context = ws_context["context"]
    replacement_incarnation = "interaction-websocket-replacement-incarnation"

    with database.SessionLocal() as db:
        terminal = db.get(database.TerminalModel, context.terminal_id)
        assert terminal is not None
        terminal.session_incarnation_id = replacement_incarnation
        incarnation = db.get(database.SessionIncarnationModel, context.session_name)
        assert incarnation is not None
        incarnation.incarnation_id = replacement_incarnation
        db.commit()

    with ws_context["connect"](expected_incarnation_id=context.incarnation_id) as websocket:
        with pytest.raises(WebSocketDisconnect) as disconnected:
            websocket.receive_text()
    assert disconnected.value.code == 4409
    assert ws_context["backend"].attach_calls == []
    assert ws_context["master_fds"] == set()
    assert ws_context["pty_writes"] == []
    _assert_cat_processes_stopped(ws_context["cat_processes"], count=0)


def test_owner_cancellation_releases_required_credential_without_fabricating_answer(
    real_interaction_websocket,
):
    from cli_agent_orchestrator.services.interactions.models import QuestionInput

    ws_context = real_interaction_websocket
    context = ws_context["context"]
    group = _new_group(
        context,
        operation_key="ws-cancel-credential-group-001",
        questions=[
            _credential_question(),
            QuestionInput(
                kind="text",
                prompt="Optional safe preference while the private flow runs.",
                required=False,
            ),
        ],
    )
    credential, optional = group.questions
    assert context.service.has_required_wait(context.terminal_id, context.incarnation_id)

    response = context.client.post(
        f"/interactions/{credential['question_id']}/cancel",
        headers={"If-Match": f'"{credential["version"]}"'},
        json={"reason": "operator cancels the unsupported credential request"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["lifecycle"] == "cancelled"
    assert not context.service.has_required_wait(context.terminal_id, context.incarnation_id)
    assert _question_row(context, credential["question_id"])["lifecycle"] == "cancelled"
    assert _question_row(context, optional["question_id"])["lifecycle"] == "cancelled"
    assert _answer_count(context) == 0

    safe_input = "SAFE_INPUT_AFTER_OWNER_CANCELLATION\n"
    before = context.service.source_status(context.terminal_id, context.incarnation_id)
    with ws_context["connect"](expected_incarnation_id=context.incarnation_id) as websocket:
        websocket.send_json({"type": "input", "data": safe_input})
        try:
            deadline = time.monotonic() + 3.0
            while not ws_context["pty_writes"] and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            websocket.close()
            _assert_cat_processes_stopped(ws_context["cat_processes"], count=1)

    assert ws_context["backend"].attach_calls == [(context.session_name, "worker")]
    _assert_cat_processes_stopped(ws_context["cat_processes"], count=1)
    assert b"".join(ws_context["pty_writes"]) == safe_input.encode()
    after = context.service.source_status(context.terminal_id, context.incarnation_id)
    assert after.current_generation == before.current_generation + 1
    assert _answer_count(context) == 0


def test_cancelling_optional_sibling_does_not_clear_required_credential_fence(
    real_interaction_websocket,
):
    from cli_agent_orchestrator.services.interactions.models import QuestionInput

    ws_context = real_interaction_websocket
    context = ws_context["context"]
    group = _new_group(
        context,
        operation_key="ws-cancel-optional-sibling-001",
        questions=[
            _credential_question(),
            QuestionInput(
                kind="text",
                prompt="Optional preference that may be cancelled.",
                required=False,
            ),
        ],
    )
    credential, optional = group.questions

    response = context.client.post(
        f"/interactions/{optional['question_id']}/cancel",
        headers={"If-Match": f'"{optional["version"]}"'},
        json={"reason": "cancel optional fixture question"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["lifecycle"] == "cancelled"
    assert _question_row(context, optional["question_id"])["lifecycle"] == "cancelled"
    assert _question_row(context, credential["question_id"])["lifecycle"] == "manual_only"
    assert context.service.has_required_wait(context.terminal_id, context.incarnation_id)
    assert _answer_count(context) == 0

    with ws_context["connect"](expected_incarnation_id=context.incarnation_id) as websocket:
        websocket.send_json({"type": "input", "data": "SAFE_INPUT_STILL_BLOCKED_BY_CREDENTIAL\n"})
        _assert_blocked_before_write(
            websocket, ws_context["pty_writes"], ws_context["cat_processes"]
        )

    assert ws_context["backend"].attach_calls == [(context.session_name, "worker")]
    _assert_cat_processes_stopped(ws_context["cat_processes"], count=1)
    assert _answer_count(context) == 0
