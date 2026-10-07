"""Response loss crosses the actual HTTP admission boundary without a second worker."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.terminal import Terminal


@pytest.fixture
def assignment_store(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'assign.sqlite'}", connect_args={"check_same_thread": False}
    )
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine))
    db.create_terminal("1234abcd", "cao-parent", "0", "codex", "developer")
    yield
    engine.dispose()


def test_lost_http_response_retry_replays_one_admitted_message(client, assignment_store):
    body = {
        "operation_key": "response-loss-1",
        "agent_profile": "developer",
        "message": "one exact task",
    }
    terminal = Terminal(
        id="abcd1234", name="developer", provider="codex", session_name="cao-parent"
    )
    with (
        patch(
            "cli_agent_orchestrator.services.assignment_service.resolve_provider",
            return_value="codex",
        ),
        patch(
            "cli_agent_orchestrator.services.assignment_service.terminal_service.ensure_terminal_is_not_work_owned"
        ),
        patch(
            "cli_agent_orchestrator.services.assignment_service.terminal_service.create_terminal",
            new=AsyncMock(return_value=terminal),
        ) as create,
    ):
        lost = client.post("/terminals/1234abcd/assignments", json=body)
        assert lost.status_code == 202
        receipt = lost.json()
        replay = client.post("/terminals/1234abcd/assignments", json=body)
        assert replay.status_code == 202 and replay.json() == receipt
        assert client.get("/assignments/" + receipt["assignment_id"]).json() == receipt
        conflict = client.post(
            "/terminals/1234abcd/assignments", json={**body, "message": "different"}
        )
        assert (
            conflict.status_code == 409
            and conflict.json()["detail"]["kind"] == "assignment_conflict"
        )
    assert create.await_count == 1
    assert create.call_args.kwargs["initial_message"] == "one exact task"
    assert create.call_args.kwargs["defer_init"] is True
