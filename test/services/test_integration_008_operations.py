"""Native retained-capacity and durable disconnected-node operational contracts."""

import asyncio
import time
from test.integration.test_work_dispatch import admit, context

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_projection import WorkQueries
from cli_agent_orchestrator.services.work_scheduler import WorkScheduler


def test_expired_hold_is_owner_actionable_without_release_or_writes(context):
    work = admit(context, "g04-expired-held")
    prepared = context.service._prepare_dispatch(registered_only=False)
    assert prepared is not None
    prepared[1].close()
    attempt = context.repo.get_work(work["id"])["attempts"][-1]
    with context.repo.transaction() as connection:
        connection.execute(
            "UPDATE work_attempts SET lease_expires_at=? WHERE id=?",
            (time.time() - 1, attempt["id"]),
        )
    before = context.repo.get_work(work["id"])
    view = WorkQueries(context.repo).operational_status(context.actor, work["id"])
    assert view["lease_expired"] is True
    assert view["capacity"]["state"] == "held"
    assert view["release_allowed"] is False
    assert view["required_action"] == "reconcile_stop_and_cleanup_proof"
    assert view["process_state"] == "unknown"
    assert context.repo.get_work(work["id"]) == before
    assert WorkScheduler(context.repo).get_by_attempt(attempt["id"]).state == "held"
    assert "checkout_root" not in str(view)


def test_operational_query_rejects_foreign_and_unverified_identity(context):
    work = admit(context, "g04-private")
    foreign = auth._verified_principal("issuer", "foreign", [auth.SCOPE_READ], "jwt")
    with pytest.raises(KeyError):
        WorkQueries(context.repo).operational_status(foreign, work["id"])
    forged = object.__new__(auth.Principal)
    for field in ("id", "issuer", "subject", "scopes", "kind"):
        object.__setattr__(forged, field, getattr(context.actor, field))
    with pytest.raises(PermissionError):
        WorkQueries(context.repo).operational_status(forged, work["id"])


def test_durable_node_survives_disconnect_and_restart_without_replay():
    from cli_agent_orchestrator.clients.runtime_channel_schema import RemoteBase
    from cli_agent_orchestrator.runtime_channel.registry import RuntimeRegistry
    from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore
    from cli_agent_orchestrator.services.work_operations import runtime_operations

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    RemoteBase.metadata.create_all(engine)
    store = RemoteOperationStore(engine)
    store.activate_runtime("g04-known-node", "incarnation-1")
    admin = auth._verified_principal("issuer", "operator", [auth.SCOPE_ADMIN], "jwt")
    registry = RuntimeRegistry()

    async def run():
        async def send(value):
            pass

        connection = registry.register("g04-known-node", send)
        registry.activate(connection)
        registry.unregister("g04-known-node", connection)

    asyncio.run(run())
    for current in (registry, RuntimeRegistry()):
        view = runtime_operations(admin, engine, current)
        assert view["nodes"][0]["runtime_id"] == "g04-known-node"
        assert view["nodes"][0]["connection_state"] == "disconnected"
        assert view["nodes"][0]["incarnation_id"] == "incarnation-1"
        assert view["nodes"][0]["automatic_replay_allowed"] is False
    reader = auth._verified_principal("issuer", "reader", [auth.SCOPE_READ], "jwt")
    with pytest.raises(PermissionError):
        runtime_operations(reader, engine, registry)
    engine.dispose()


def test_blocked_restore_is_audit_only_even_for_verified_owner(context):
    from cli_agent_orchestrator.clients.work_repository import SchemaMismatch

    work = admit(context, "g04-restore")
    with context.repo.transaction() as connection:
        context.repo._block_recovery_for_restore(
            connection, installation_uuid="f" * 32, bundle_digest="a" * 64, restore_receipt="b" * 64
        )
    view = WorkQueries(context.repo).operational_status(context.actor, work["id"])
    assert view["execution_state"] == "blocked_restore"
    assert view["execution_allowed"] is False
    assert view["reactivation_allowed"] is False
    assert view["required_action"] == "restore_remains_blocked"
    assert all(action["method"] == "GET" for action in view["actions"])
    with context.repo.connection() as connection:
        with pytest.raises(SchemaMismatch, match="blocks execution"):
            context.repo.assert_execution_allowed(connection)


def test_real_jwt_routes_enforce_owner_and_admin_boundaries(context, monkeypatch):
    from types import SimpleNamespace

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cli_agent_orchestrator.api import work_routes
    from cli_agent_orchestrator.clients.runtime_channel_schema import RemoteBase
    from cli_agent_orchestrator.runtime_channel import server
    from cli_agent_orchestrator.runtime_channel.registry import RuntimeRegistry
    from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("CAO_AUTH_JWKS_URI", "https://offline.example/jwks")
    monkeypatch.setenv("CAO_AUTH_ISSUER", context.actor.issuer)
    monkeypatch.setenv("CAO_AUTH_AUDIENCE", "operations-test")
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    monkeypatch.setattr(
        auth.get_jwks_cache(),
        "get_client",
        lambda _: SimpleNamespace(
            get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private_key.public_key())
        ),
    )
    monkeypatch.setattr(work_routes, "queries", lambda: WorkQueries(context.repo))
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    RemoteBase.metadata.create_all(engine)
    RemoteOperationStore(engine).activate_runtime("private-node", "private-incarnation")
    monkeypatch.setattr(server.database, "engine", engine)
    monkeypatch.setattr(server, "runtime_registry", RuntimeRegistry())
    application = FastAPI()
    application.include_router(work_routes.router)
    application.include_router(server.router)
    work = admit(context, "g04-jwt")
    url = f"/work-items/{work['id']}/operations"

    def headers(subject, scopes):
        token = jwt.encode(
            {
                "iss": context.actor.issuer,
                "sub": subject,
                "aud": "operations-test",
                "exp": time.time() + 60,
                "scope": " ".join(scopes),
            },
            private_key,
            algorithm="RS256",
        )
        return {"Authorization": "Bearer " + token}

    with TestClient(application) as client:
        assert client.get(url).status_code == 401
        reader = headers(context.actor.subject, [auth.SCOPE_READ])
        assert client.get(url, headers=reader).status_code == 200
        foreign = client.get(url, headers=headers("foreign", [auth.SCOPE_READ]))
        assert foreign.status_code == 404
        assert "g04-jwt" not in foreign.text
        assert client.get(url, headers=headers(context.actor.subject, [])).status_code == 403
        assert client.get("/runtimes/operations", headers=reader).status_code == 403
        assert client.get("/runtimes/operations").status_code == 401
        admin = headers(context.actor.subject, [auth.SCOPE_ADMIN])
        response = client.get("/runtimes/operations", headers=admin)
        assert response.status_code == 200, response.text
        assert response.json()["nodes"][0]["runtime_id"] == "private-node"
        assert client.get("/runtimes/operations?limit=101", headers=admin).status_code == 422
    engine.dispose()


from test.services.test_integration_008_coordinator import coordinator_context, plan_context


def test_foreign_scoped_controller_is_private_and_existing_resume_stays_fenced(coordinator_context):
    from test.services.test_integration_008_coordinator import admit_iteration, start_controller

    from cli_agent_orchestrator.services import workflow_journal

    f = coordinator_context
    start_controller(f)
    f.driver.claim(f.subject, "controller-run")
    work = admit_iteration(f, 1)
    before = workflow_journal.get_run("controller-run")
    view = WorkQueries(f.context.repo).operational_status(f.context.actor, work["id"])
    assert view["controller"] is None
    assert all(action["method"] == "GET" for action in view["actions"])
    with pytest.raises(KeyError):
        WorkQueries(f.context.repo).operational_status(f.subject, work["id"])
    with pytest.raises(ValueError, match="coordinator_work_pending"):
        asyncio.run(f.service.resume(f.subject, "controller-run"))
    assert workflow_journal.get_run("controller-run").generation == before.generation


def test_known_current_incarnation_observation_preserves_old_reconcile_operation():
    from cli_agent_orchestrator.clients.runtime_channel_schema import RemoteBase
    from cli_agent_orchestrator.runtime_channel.durable_connection import DurableRuntimeConnection
    from cli_agent_orchestrator.runtime_channel.protocol import CommandType
    from cli_agent_orchestrator.runtime_channel.registry import RuntimeRegistry
    from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore
    from cli_agent_orchestrator.services.work_operations import runtime_operations

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    RemoteBase.metadata.create_all(engine)
    store = RemoteOperationStore(engine)
    store.activate_runtime("node-a", "old")
    store.prepare(
        "old-operation", "node-a", "old", CommandType.LAUNCH, {"private": "secret-not-diagnostic"}
    )
    for index in range(6):
        store.prepare(
            f"old-operation-{index}",
            "node-a",
            "old",
            CommandType.LAUNCH,
            {"private": "secret-not-diagnostic"},
        )
    epoch = store.activate_runtime("node-a", "current")
    store.activate_runtime("node-b", "other")
    registry = RuntimeRegistry()
    admin = auth._verified_principal("issuer", "operator", [auth.SCOPE_ADMIN], "jwt")

    async def run():
        async def send(value):
            raise AssertionError("diagnostics must never send a command")

        connection = DurableRuntimeConnection("node-a", "current", epoch, store, send)
        registry.register("node-a", send, connection=connection)
        registry.activate(connection)
        view = runtime_operations(admin, engine, registry, limit=1)
        assert view["nodes"][0]["connection_state"] == "connected"
        assert view["nodes"][0]["unresolved_operation_count"] == 7
        assert view["nodes"][0]["unresolved_operations"][:1] == [
            {
                "op_id": "old-operation",
                "incarnation_id": "old",
                "state": "reconcile",
                "inspect_path": "/runtimes/node-a/operations/old-operation",
            }
        ]
        assert len(view["nodes"][0]["unresolved_operations"]) == 5
        assert view["nodes"][0]["operations_truncated"] is True
        assert view["next_cursor"] == "node-a"
        assert "secret-not-diagnostic" not in str(view)
        next_page = runtime_operations(admin, engine, registry, limit=1, after=view["next_cursor"])
        assert next_page["nodes"][0]["runtime_id"] == "node-b"
        store.activate_runtime("node-a", "replacement")
        assert (
            runtime_operations(admin, engine, registry)["nodes"][0]["connection_state"]
            == "identity_mismatch"
        )
        registry.unregister("node-a", connection)

    asyncio.run(run())
    assert store.get("old-operation")["state"] == "reconcile"
    engine.dispose()
