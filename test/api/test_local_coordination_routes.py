"""Public local coordination preserves durable task outcomes."""

from test.services.test_local_peer_recovery import cancel_peer_task, cancellable_peer_task
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cli_agent_orchestrator.api import local_coordination_routes as routes


def test_public_task_inspection_returns_persisted_cancelled_receipt(
    cancellable_peer_task, monkeypatch
):
    task = cancellable_peer_task
    receipt = cancel_peer_task(task)

    async def authorize(*args, **kwargs):
        return task["source_instance_id"]

    monkeypatch.setattr(routes, "_active_peer_headers", authorize)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.get(
            f'/local-coordination/tasks/{task["task_id"]}',
            params={
                "project_id": task["project_id"],
                "requester_terminal_id": task["requester_terminal_id"],
            },
        )
    assert response.status_code == 200
    assert response.json() == receipt
    assert response.json()["result"]["state"] == "cancelled"


def test_unavailable_peer_task_is_distinct_from_missing_task(monkeypatch):
    async def authorize(*args, **kwargs):
        return "source-profile"

    monkeypatch.setattr(routes, "_active_peer_headers", authorize)
    monkeypatch.setattr(routes, "_require_loopback", lambda request: None)
    monkeypatch.setattr(routes, "_require_agent_terminal", lambda terminal: {})

    def unavailable(*args, **kwargs):
        raise routes.local_peer_service.LocalPeerUnavailable("peer stopped")

    monkeypatch.setattr(routes.local_peer_service, "task_from_peer", unavailable)
    monkeypatch.setattr(routes, "_require_task_principal", lambda *args: None)
    app = FastAPI()
    app.include_router(routes.router)
    from cli_agent_orchestrator.security.auth import (
        SCOPE_READ,
        _verified_principal,
        get_current_principal,
    )

    app.dependency_overrides[get_current_principal] = lambda: _verified_principal(
        "test", "alice", [SCOPE_READ], "jwt"
    )
    with TestClient(app) as client:
        response = client.get(
            "/local-coordination/agent/tasks/example", params={"requester_terminal_id": "deadbeef"}
        )
    assert response.status_code == 503
    assert response.json()["detail"]["kind"] == "peer_unavailable"


@pytest.mark.parametrize("action", ["read", "cancel"])
def test_another_principal_cannot_use_same_requester_terminal(
    cancellable_peer_task, monkeypatch, action
):
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.security.auth import (
        SCOPE_WRITE,
        _verified_principal,
        get_current_principal,
    )

    task = cancellable_peer_task
    with database.SessionLocal() as session:
        row = session.get(database.LocalPeerTaskModel, task["task_id"])
        row.requester_principal_id = "alice-principal"
        session.commit()
    monkeypatch.setattr(routes, "_require_loopback", lambda request: None)
    monkeypatch.setattr(routes, "_require_agent_terminal", lambda terminal: {})
    calls = []
    monkeypatch.setattr(
        routes.local_peer_service,
        "task_from_peer",
        lambda *args, **kwargs: calls.append("read") or task,
    )
    monkeypatch.setattr(
        routes.local_peer_service,
        "cancel_task_at_peer",
        lambda *args: calls.append("cancel") or task,
    )
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_principal] = lambda: _verified_principal(
        "test", "bob", [SCOPE_WRITE], "jwt"
    )
    with TestClient(app) as client:
        url = f'/local-coordination/agent/tasks/{task["task_id"]}'
        response = (
            client.get(url, params={"requester_terminal_id": "deadbeef"})
            if action == "read"
            else client.post(url + "/cancel", json={"requester_terminal_id": "deadbeef"})
        )
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    "scenario,expected", [("empty", "empty"), ("offline", "unavailable"), ("disappeared", "empty")]
)
def test_project_session_snapshot_distinguishes_empty_offline_and_disappearance(
    monkeypatch, scenario, expected, tmp_path
):
    from cli_agent_orchestrator.services import session_service

    async def authorize(*args, **kwargs):
        assert kwargs["required_scope"] == "session:read"
        return "source-profile"

    monkeypatch.setattr(routes, "_active_peer_headers", authorize)
    monkeypatch.setattr(
        routes.local_peer_service,
        "_load_project",
        lambda project: {
            "canonical_root": str(tmp_path),
            "project_id": project,
            "git_common_dir": None,
        },
    )

    def listing():
        if scenario == "offline":
            raise OSError("backend stopped")
        return [{"id": "cao-gone"}] if scenario == "disappeared" else []

    monkeypatch.setattr(
        session_service, "get_backend", lambda: SimpleNamespace(list_sessions=listing)
    )

    def vanished(name):
        raise ValueError("session disappeared")

    monkeypatch.setattr(session_service, "get_session", vanished)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.get("/local-coordination/sessions", params={"project_id": "b" * 64})
    assert response.status_code == 200
    assert response.json()["state"] == expected
    assert response.json()["sessions"] == []
    assert response.json()["disappeared_count"] == int(scenario == "disappeared")


def test_agent_cannot_list_peers_for_another_project(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "_require_loopback", lambda request: None)
    monkeypatch.setattr(
        routes,
        "_require_agent_terminal",
        lambda terminal: {"working_directory": str(tmp_path / "own")},
    )
    monkeypatch.setattr(
        routes.local_peer_service,
        "project_binding",
        lambda path: {"project_id": "own" if path.endswith("own") else "other"},
    )
    calls = []
    monkeypatch.setattr(
        routes.local_peer_service, "list_local_peers", lambda **kwargs: calls.append(kwargs) or []
    )
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.get(
            "/local-coordination/agent/peers",
            params={"requester_terminal_id": "deadbeef", "project_path": str(tmp_path / "other")},
        )
    assert response.status_code == 403
    assert calls == []


def test_session_snapshot_excludes_other_projects(monkeypatch, tmp_path):
    from cli_agent_orchestrator.services import session_service

    project = "b" * 64
    monkeypatch.setattr(routes.local_peer_service, "_load_project", lambda _: {})
    monkeypatch.setattr(
        session_service,
        "get_backend",
        lambda: SimpleNamespace(list_sessions=lambda: [{"id": "cao-mixed"}]),
    )
    monkeypatch.setattr(
        session_service,
        "get_session",
        lambda _: {
            "session": {"id": "mixed"},
            "terminals": [
                {"id": "one", "working_directory": "own"},
                {"id": "secret", "working_directory": "other"},
            ],
        },
    )
    monkeypatch.setattr(
        routes.local_peer_service,
        "project_binding",
        lambda cwd: {"project_id": project if cwd == "own" else "other"},
    )
    snapshot = routes.local_peer_service.project_session_snapshot(project)
    assert snapshot["state"] == "available"
    assert [t["id"] for s in snapshot["sessions"] for t in s["terminals"]] == ["one"]


def test_agent_session_query_distinguishes_unavailable_target(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "_require_loopback", lambda _: None)
    monkeypatch.setattr(
        routes, "_require_agent_terminal", lambda _: {"working_directory": str(tmp_path)}
    )
    monkeypatch.setattr(
        routes.local_peer_service, "project_binding", lambda _: {"project_id": "b" * 64}
    )
    monkeypatch.setattr(
        routes.local_peer_service,
        "sessions_from_peer",
        lambda *args, **kwargs: {"state": "unavailable", "sessions": [], "disappeared_count": 0},
        raising=False,
    )
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.get(
            "/local-coordination/agent/peers/peer/sessions",
            params={"requester_terminal_id": "deadbeef", "project_path": str(tmp_path)},
        )
    assert response.status_code == 200
    assert response.json()["state"] == "unavailable"


def test_agent_session_query_rejects_missing_action_before_contacting_peer(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "_require_loopback", lambda _: None)
    monkeypatch.setattr(
        routes, "_require_agent_terminal", lambda _: {"working_directory": str(tmp_path)}
    )
    monkeypatch.setattr(
        routes.local_peer_service, "project_binding", lambda _: {"project_id": "b" * 64}
    )

    def denied(*args):
        raise routes.local_peer_auth.PeerScopeDeniedError("session scope not granted")

    monkeypatch.setattr(routes.local_peer_service, "_outbound_peer", denied)
    calls = []
    monkeypatch.setattr(
        routes.local_peer_service, "_loopback_request", lambda *args, **kwargs: calls.append(args)
    )
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.get(
            "/local-coordination/agent/peers/peer/sessions",
            params={"requester_terminal_id": "deadbeef", "project_path": str(tmp_path)},
        )
    assert response.status_code == 403
    assert calls == []
