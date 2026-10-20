"""Operator-only API surface for reconciling uncertain MCP proxy state."""

import time

import pytest

from cli_agent_orchestrator.api.main import app
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_mcp_proxy import WorkMcpProxy

pytestmark = pytest.mark.postgres

_MISSING = object()


@pytest.fixture
def effect_operator_client(client, postgres_targets, tmp_path, monkeypatch):
    """Use the normal API dependency and a real, isolated PostgreSQL Work store."""
    from test.services.test_work_mcp_proxy import _bound_worker

    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    bootstrap_scope(postgres_targets.main)
    repository, binding, _, _ = _bound_worker(tmp_path)
    effect_id = "e3000000000000000000000000000000"
    now = time.time()
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO work_mcp_proxy_issues "
            "(attempt_id,generation,attempt_revision,contract_hash,expires_at,issued_at) "
            "VALUES (%s,1,1,%s,%s,%s)",
            (binding.attempt_id, binding.contract_hash, now + 30, now),
        )
        connection.execute(
            "INSERT INTO work_mcp_proxy_effects "
            "(effect_id,attempt_id,generation,request_sha256,created_at) "
            "VALUES (%s,%s,1,%s,%s)",
            (effect_id, binding.attempt_id, "a" * 64, now),
        )
        connection.execute(
            "INSERT INTO work_mcp_proxy_effect_events "
            "(effect_id,sequence,state,occurred_at) VALUES (%s,1,'intent',%s)",
            (effect_id, now),
        )
    assert WorkMcpProxy(repository).recover_incomplete_effects(binding.attempt_id, 1) == 1

    monkeypatch.setenv("AUTH0_DOMAIN", "issuer.test")
    scopes = [auth.SCOPE_READ]
    previous = app.dependency_overrides.get(auth.get_current_scopes, _MISSING)
    app.dependency_overrides[auth.get_current_scopes] = lambda: scopes
    try:
        yield client, repository, effect_id, binding.attempt_id, scopes
    finally:
        if previous is _MISSING:
            app.dependency_overrides.pop(auth.get_current_scopes, None)
        else:
            app.dependency_overrides[auth.get_current_scopes] = previous


def test_only_admin_can_inspect_and_reconcile_uncertain_effects(effect_operator_client):
    client, repository, effect_id, attempt_id, scopes = effect_operator_client

    assert client.get("/work-proxy/effects").status_code == 403

    scopes[:] = [auth.SCOPE_ADMIN]
    listed = client.get("/work-proxy/effects")
    assert listed.status_code == 200
    effect = listed.json()["effects"][0]
    assert effect == {
        "effect_id": effect_id,
        "attempt_id": attempt_id,
        "generation": 1,
        "request_sha256": "a" * 64,
        "created_at": effect["created_at"],
        "state": "uncertain",
        "updated_at": effect["updated_at"],
    }
    assert set(effect) == {
        "effect_id",
        "attempt_id",
        "generation",
        "request_sha256",
        "created_at",
        "state",
        "updated_at",
    }

    reconciled = client.post(
        f"/work-proxy/effects/{effect_id}/reconcile",
        json={"resolution": "operator checked provider state; no replay"},
    )
    assert reconciled.status_code == 200
    assert reconciled.json() == {"effect_id": effect_id, "state": "reconciled"}
    assert client.get("/work-proxy/effects").json() == {"effects": []}
    with repository.read_snapshot() as connection:
        states = [
            row[0]
            for row in connection.execute(
                "SELECT state FROM work_mcp_proxy_effect_events "
                "WHERE effect_id=%s ORDER BY sequence",
                (effect_id,),
            )
        ]
    assert states == ["intent", "uncertain", "reconciled"]


def test_admin_can_reconcile_an_abandoned_proxy_issue_without_effects(
    client, postgres_targets, tmp_path, monkeypatch
):
    from test.services.test_work_mcp_proxy import _bound_worker

    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    bootstrap_scope(postgres_targets.main)
    repository, binding, _, _ = _bound_worker(tmp_path)
    now = time.time()
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO work_mcp_proxy_issues "
            "(attempt_id,generation,attempt_revision,contract_hash,expires_at,issued_at) "
            "VALUES (%s,1,1,%s,%s,%s)",
            (binding.attempt_id, binding.contract_hash, now + 30, now),
        )
        connection.execute(
            "INSERT INTO work_mcp_proxy_issue_events "
            "(attempt_id,generation,sequence,state,occurred_at) "
            "VALUES (%s,1,1,'issued',%s)",
            (binding.attempt_id, now),
        )
    proxy = WorkMcpProxy(repository)
    assert proxy.recover_incomplete_issues(binding.attempt_id, 1) == 1

    monkeypatch.setenv("AUTH0_DOMAIN", "issuer.test")
    previous = app.dependency_overrides.get(auth.get_current_scopes, _MISSING)
    app.dependency_overrides[auth.get_current_scopes] = lambda: [auth.SCOPE_ADMIN]
    try:
        listed = client.get("/work-proxy/issues")
        assert listed.status_code == 200
        issue = listed.json()["issues"][0]
        assert issue["attempt_id"] == binding.attempt_id
        assert issue["generation"] == 1
        assert issue["state"] == "abandoned"
        assert "contract_hash" not in issue

        reconciled = client.post(
            f"/work-proxy/issues/{binding.attempt_id}/1/reconcile",
            json={"resolution": "confirmed the endpoint had no requests"},
        )
        assert reconciled.status_code == 200
        assert reconciled.json() == {
            "attempt_id": binding.attempt_id,
            "generation": 1,
            "state": "reconciled",
        }
        assert client.get("/work-proxy/issues").json() == {"issues": []}
    finally:
        if previous is _MISSING:
            app.dependency_overrides.pop(auth.get_current_scopes, None)
        else:
            app.dependency_overrides[auth.get_current_scopes] = previous
