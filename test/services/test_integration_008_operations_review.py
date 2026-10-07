"""Independent real SQLite operational diagnostic boundaries; no effects outside fixtures."""

import asyncio
import hashlib
import time
from test.integration.test_work_dispatch import admit, context

import pytest
from sqlalchemy import create_engine, text

from cli_agent_orchestrator.clients.runtime_channel_schema import RemoteBase
from cli_agent_orchestrator.models.work_operations import RuntimeOperations, WorkOperations
from cli_agent_orchestrator.runtime_channel.durable_connection import DurableRuntimeConnection
from cli_agent_orchestrator.runtime_channel.protocol import CommandType
from cli_agent_orchestrator.runtime_channel.registry import RuntimeRegistry
from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_operations import runtime_operations
from cli_agent_orchestrator.services.work_projection import WorkQueries


def digest(repo):
    with repo.read_snapshot() as connection:
        return hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()


def test_actual_expired_capacity_and_paths_remain_held_for_read_owner_then_restore(context):
    work = admit(context, "independent-g04-expired")
    dispatched = context.service._prepare_dispatch(registered_only=False)
    assert dispatched
    dispatched[1].close()
    attempt = context.repo.get_work(work["id"])["attempts"][-1]
    with context.repo.transaction() as connection:
        connection.execute(
            "UPDATE work_attempts SET lease_expires_at=? WHERE id=?",
            (time.time() - 30, attempt["id"]),
        )
    reader = auth._verified_principal(
        context.actor.issuer, context.actor.subject, [auth.SCOPE_READ], context.actor.kind
    )
    before = digest(context.repo)
    view = WorkOperations.model_validate(
        WorkQueries(context.repo).operational_status(reader, work["id"])
    )
    assert view.attempt_id == attempt["id"] and view.generation == attempt["generation"]
    assert (
        view.capacity.state == "held"
        and view.reservations.state == "active"
        and view.reservations.path_count > 0
    )
    assert view.lease_expired and view.process_state == "unknown"
    assert not view.release_allowed and not view.reactivation_allowed
    assert view.required_action == "reconcile_stop_and_cleanup_proof"
    assert all(a.kind == "inspect_work" for a in view.actions)
    assert digest(context.repo) == before
    with context.repo.transaction() as connection:
        context.repo._block_recovery_for_restore(
            connection, installation_uuid="e" * 32, bundle_digest="c" * 64, restore_receipt="d" * 64
        )
    before = digest(context.repo)
    blocked = WorkOperations.model_validate(
        WorkQueries(context.repo).operational_status(reader, work["id"])
    )
    assert blocked.execution_state == "blocked_restore" and not blocked.execution_allowed
    assert blocked.capacity.state == "held" and blocked.reservations.state == "active"
    assert blocked.required_action == "restore_remains_blocked" and all(
        a.method == "GET" for a in blocked.actions
    )
    assert digest(context.repo) == before


def test_exact_same_incarnation_new_epoch_inactive_closed_and_restart_inventory(
    tmp_path, monkeypatch
):
    from cli_agent_orchestrator.runtime_channel import server

    engine = create_engine("sqlite:///" + str(tmp_path / "operations.sqlite"))
    RemoteBase.metadata.create_all(engine)
    store = RemoteOperationStore(engine)
    epoch = store.activate_runtime("node-review", "inc-review")
    store.prepare(
        "old-review-operation",
        "node-review",
        "inc-review",
        CommandType.INPUT,
        {"message": "private-diagnostic-secret"},
    )
    registry = RuntimeRegistry()
    admin = auth._verified_principal("issuer", "operator", [auth.SCOPE_ADMIN], "jwt")

    async def probe():
        async def send(value):
            raise AssertionError("observation must not send")

        conn = DurableRuntimeConnection("node-review", "inc-review", epoch, store, send)
        registry.register("node-review", send, connection=conn)
        assert (
            runtime_operations(admin, engine, registry)["nodes"][0]["connection_state"]
            == "disconnected"
        )
        registry.activate(conn)
        assert (
            runtime_operations(admin, engine, registry)["nodes"][0]["connection_state"]
            == "connected"
        )
        replacement_epoch = store.activate_runtime("node-review", "inc-review")
        assert replacement_epoch > epoch
        view = RuntimeOperations.model_validate(runtime_operations(admin, engine, registry))
        assert view.nodes[0].connection_state == "identity_mismatch"
        assert view.nodes[0].connection_epoch == replacement_epoch
        assert view.nodes[0].unresolved_operation_count == 1
        assert (
            not view.nodes[0].automatic_replay_allowed
            and not view.nodes[0].protected_work_supported
        )
        assert "private-diagnostic-secret" not in view.model_dump_json()
        conn.close("local observation closed")
        assert (
            runtime_operations(admin, engine, registry)["nodes"][0]["connection_state"]
            == "disconnected"
        )
        monkeypatch.setattr(server.database, "engine", engine)
        inspected = await server.get_operation("node-review", "old-review-operation")
        assert inspected["op_id"] == "old-review-operation" and inspected["state"] == "prepared"
        assert "private-diagnostic-secret" not in str(inspected)
        registry.unregister("node-review", conn)

    asyncio.run(probe())
    before = store.get("old-review-operation")
    engine.dispose()
    restarted = create_engine("sqlite:///" + str(tmp_path / "operations.sqlite"))
    actual = RuntimeOperations.model_validate(
        runtime_operations(admin, restarted, RuntimeRegistry())
    )
    assert actual.nodes[0].connection_state == "disconnected"
    assert actual.nodes[0].unresolved_operation_count == 1
    assert RemoteOperationStore(restarted).get("old-review-operation") == before
    restarted.dispose()


def test_inventory_pagination_and_sample_bounds_no_mutation(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "many.sqlite"))
    RemoteBase.metadata.create_all(engine)
    store = RemoteOperationStore(engine)
    for index in range(102):
        store.activate_runtime("runtime-" + str(index).zfill(3), "inc")
    for index in range(7):
        store.prepare(
            "pending-" + str(index),
            "runtime-000",
            "inc",
            CommandType.LAUNCH,
            {"private": "omitted"},
        )
    admin = auth._verified_principal("issuer", "operator", [auth.SCOPE_ADMIN], "jwt")
    first = RuntimeOperations.model_validate(
        runtime_operations(admin, engine, RuntimeRegistry(), limit=100)
    )
    assert len(first.nodes) == 100 and first.next_cursor == "runtime-099"
    assert len(first.nodes[0].unresolved_operations) == 5 and first.nodes[0].operations_truncated
    assert first.nodes[0].unresolved_operation_count == 7
    second = RuntimeOperations.model_validate(
        runtime_operations(admin, engine, RuntimeRegistry(), limit=100, after=first.next_cursor)
    )
    assert [n.runtime_id for n in second.nodes] == [
        "runtime-100",
        "runtime-101",
    ] and second.next_cursor is None
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM remote_operations WHERE state='prepared'")
            ).scalar()
            == 7
        )
    for bad in (True, 0, 101):
        with pytest.raises(ValueError):
            runtime_operations(admin, engine, RuntimeRegistry(), limit=bad)
    reader = auth._verified_principal("issuer", "operator", [auth.SCOPE_READ], "jwt")
    with pytest.raises(PermissionError):
        runtime_operations(reader, engine, RuntimeRegistry())
    engine.dispose()


from test.services.test_integration_008_coordinator import coordinator_context, plan_context


def test_actual_work_owner_cannot_observe_foreign_scoped_controller_or_gain_action(
    coordinator_context,
):
    from test.services.test_integration_008_coordinator import admit_iteration, start_controller

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cli_agent_orchestrator.api import work_routes

    f = coordinator_context
    start_controller(f)
    f.driver.claim(f.subject, "controller-run")
    work = admit_iteration(f, 1)
    before = digest(f.context.repo)
    view = WorkOperations.model_validate(
        WorkQueries(f.context.repo).operational_status(f.context.actor, work["id"])
    )
    assert view.controller is None
    assert len(view.actions) == 1 and view.actions[0].kind == "inspect_work"
    app = FastAPI()
    app.include_router(work_routes.router)
    app.dependency_overrides[auth.get_current_principal] = lambda: f.context.actor
    app.dependency_overrides[auth.get_current_scopes] = lambda: [auth.SCOPE_ADMIN]
    original = work_routes.queries
    try:
        work_routes.queries = lambda: WorkQueries(f.context.repo)
        with TestClient(app) as client:
            result = client.get(view.actions[0].path)
            assert result.status_code == 200, result.text
            assert result.json()["work_item_id"] == work["id"]
    finally:
        work_routes.queries = original
    assert digest(f.context.repo) == before
    with pytest.raises(KeyError):
        WorkQueries(f.context.repo).operational_status(f.subject, work["id"])
