"""Independent assembled Beads/approved Work review; no external bd or providers."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from test.services.test_integration_008_beads_work import beads_work
from test.services.test_integration_008_coordinator import (
    accept_iteration,
    admit_iteration,
    context,
    coordinator_context,
    plan_context,
)
from threading import Barrier
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services import approval_store
from cli_agent_orchestrator.services import beads_service as beads
from cli_agent_orchestrator.services.beads_assignment_service import BeadsAssignments


def prepared(values, key="review-key-1"):
    f, service, adapter = values
    return service.prepare(
        f.subject,
        "repo",
        "task-1",
        operation_key=key,
        expected_hash=beads.task_dto(adapter.task)["material_hash"],
        workflow_name="wf",
        criteria=[{"kind": "output_equals", "path": ["answer"], "value": 42}],
        binding_selections={"repo": {"developer": "controller-seed"}},
        scan_dir=str(f.directory),
    )


def completed(values):
    f, service, _ = values
    p = prepared(values)
    approval_store.grant(p["plan_id"], "fixture-admin")
    service.start(
        f.subject, p["binding_id"], p["plan_id"], "controller-run", scan_dir=str(f.directory)
    )
    epoch = f.driver.claim(f.subject, "controller-run")
    admit_iteration(f, 1)
    accept_iteration(f, 42)
    f.driver.release("controller-run", epoch)
    assert f.projector.project_pending_for_run("controller-run")[0].status == "projected"
    with f.context.repo.read_snapshot() as connection:
        event = connection.execute("SELECT * FROM workflow_continuation_outbox").fetchone()
    f.service.checkpoint(f.subject, "controller-run", event)
    return p


def test_concurrent_task_start_atomic_one_origin_run_and_no_second_worker(beads_work):
    f, service, _ = beads_work
    one = prepared(beads_work, "review-first")
    two = prepared(beads_work, "review-second")
    for p in (one, two):
        approval_store.grant(p["plan_id"], "fixture-admin")
    barrier = Barrier(2)

    def start(item):
        p, rid = item
        barrier.wait()
        try:
            return service.start(
                f.subject, p["binding_id"], p["plan_id"], rid, scan_dir=str(f.directory)
            )
        except ValueError as error:
            return error

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(start, [(one, "first-run"), (two, "second-run")]))
    assert sum(isinstance(r, dict) for r in results) == 1
    winner = next(r for r in results if isinstance(r, dict))
    with f.context.repo.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM workflow_coordinator").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT count(*) FROM workflow_run WHERE run_id IN ('first-run','second-run')"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM beads_work_bindings WHERE state='assigned'"
            ).fetchone()[0]
            == 1
        )
    fresh = BeadsAssignments(f.service)
    retry = fresh.start(
        f.subject,
        winner["binding_id"],
        winner["plan_id"],
        winner["run_id"],
        scan_dir=str(f.directory),
    )
    assert retry["coordinator_id"] == winner["coordinator_id"]
    assert "_prepared" not in retry


def test_actual_accepted_result_bytes_drift_revokes_status_verified(beads_work):
    f, service, adapter = beads_work
    p = completed(beads_work)
    assert service.status(f.subject, p["binding_id"])["work_verified_completed"] is True
    with f.context.repo.read_snapshot() as connection:
        result = connection.execute("SELECT immutable_location FROM work_results").fetchone()
    artifact = f.projector.work_service.artifacts.root / result[0]
    artifact.write_bytes(b"{}")
    status = service.status(f.subject, p["binding_id"])
    assert status["work_verified_completed"] is False
    assert status["coordinator"]["work_verified_completed"] is False
    assert adapter.task.status == "open"


def test_current_dependency_and_task_cas_checked_before_any_run(beads_work):
    from cli_agent_orchestrator.clients.beads import Task

    f, service, adapter = beads_work
    p = prepared(beads_work)
    approval_store.grant(p["plan_id"], "fixture-admin")
    adapter.task = replace(adapter.task, blocked_by=["dependency"])
    original = adapter.get
    adapter.get = lambda identity: (
        Task("dependency", "Open prerequisite", status="open")
        if identity == "dependency"
        else original(identity)
    )
    with pytest.raises(beads.BeadsConflict):
        service.start(
            f.subject, p["binding_id"], p["plan_id"], "refused-run", scan_dir=str(f.directory)
        )
    with f.context.repo.read_snapshot() as connection:
        assert (
            connection.execute("SELECT 1 FROM workflow_run WHERE run_id='refused-run'").fetchone()
            is None
        )
        assert connection.execute("SELECT count(*) FROM workflow_coordinator").fetchone()[0] == 0


def test_configured_auth_scopes_and_binding_owner_fail_before_effect(
    beads_work, auth_enabled_env, rsa_keys, monkeypatch
):
    import time

    import jwt
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cli_agent_orchestrator.api.beads_routes import router
    from cli_agent_orchestrator.security import auth

    f, service, _ = beads_work
    p = prepared(beads_work)
    app = FastAPI()
    app.include_router(router)
    for name, value in vars(f.app.state).items():
        setattr(app.state, name, value)
    app.state.workflow_coordinator = f.service
    public = load_pem_private_key(rsa_keys[0], password=None).public_key()
    monkeypatch.setattr(
        auth.get_jwks_cache(),
        "get_client",
        lambda uri: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=public)
        ),
    )

    def header(scope):
        now = int(time.time())
        token = jwt.encode(
            {
                "iss": "https://test.local/",
                "aud": "cao://test",
                "sub": "other",
                "iat": now,
                "exp": now + 300,
                "scope": scope,
            },
            rsa_keys[0],
            algorithm="RS256",
            headers={"kid": "test-kid"},
        )
        return {"Authorization": "Bearer " + token}

    path = "/beads/assignments/" + p["binding_id"]
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        assert (
            client.post(
                path + "/start",
                json={"expected_plan_id": p["plan_id"], "run_id": "not-owned"},
                headers=header("cao:read"),
            ).status_code
            == 403
        )
        assert client.get(path, headers=header("cao:read")).status_code == 404
        assert (
            client.post(
                path + "/start",
                json={"expected_plan_id": p["plan_id"], "run_id": "not-owned"},
                headers=header("cao:write"),
            ).status_code
            == 404
        )
    with f.context.repo.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM workflow_coordinator").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_cancelled_start_after_commit_recovers_one_owned_work_without_replay(
    beads_work, monkeypatch
):
    from threading import Event

    from cli_agent_orchestrator.api import beads_routes as routes
    from cli_agent_orchestrator.services import workflow_spec_service

    f, service, _ = beads_work
    p = prepared(beads_work)
    approval_store.grant(p["plan_id"], "fixture-admin")
    monkeypatch.setattr(workflow_spec_service, "WORKFLOW_SPEC_DIR", f.directory)
    monkeypatch.setattr(routes, "assignments_for_request", lambda request: service)
    committed, release = Event(), Event()
    original = service.start

    def delayed(*args, **kwargs):
        value = original(*args, **kwargs)
        committed.set()
        assert release.wait(4)
        return value

    monkeypatch.setattr(service, "start", delayed)
    task = asyncio.create_task(
        routes.start_work(
            p["binding_id"],
            routes.WorkStart(expected_plan_id=p["plan_id"], run_id="controller-run"),
            SimpleNamespace(),
            principal=f.subject,
        )
    )
    try:
        assert await asyncio.to_thread(committed.wait, 3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    finally:
        release.set()
    with f.context.repo.read_snapshot() as connection:
        assert (
            connection.execute(
                "SELECT state FROM workflow_driver WHERE run_id='controller-run'"
            ).fetchone()[0]
            == "ready"
        )
        assert connection.execute("SELECT count(*) FROM workflow_coordinator").fetchone()[0] == 1
    fresh = BeadsAssignments(f.service)
    replay = fresh.start(
        f.subject, p["binding_id"], p["plan_id"], "controller-run", scan_dir=str(f.directory)
    )
    assert "_prepared" not in replay
    admitted = []

    async def admitted_drive(principal, run_id, **kwargs):
        f.driver.assert_current(run_id, kwargs["epoch"])
        admitted.append(await asyncio.to_thread(admit_iteration, f, 1))

    monkeypatch.setattr(f.driver, "drive", admitted_drive)
    await f.driver.tick()
    await f.driver.tasks["controller-run"]
    await f.driver.tick()
    fresh.start(
        f.subject, p["binding_id"], p["plan_id"], "controller-run", scan_dir=str(f.directory)
    )
    assert len(admitted) == 1
    with f.context.repo.read_snapshot() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_workflow_step_bindings WHERE run_id='controller-run'"
            ).fetchone()[0]
            == 1
        )
        assert connection.execute("SELECT count(*) FROM workflow_coordinator").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_public_close_requires_actual_accepted_criteria_before_bd_effect(
    beads_work, monkeypatch
):
    from fastapi import HTTPException

    from cli_agent_orchestrator.api import beads_routes as routes

    f, service, _ = beads_work
    p = prepared(beads_work)
    monkeypatch.setattr(routes, "assignments_for_request", lambda request: service)
    effects = []
    monkeypatch.setattr(
        beads, "metadata_operation", lambda *args, **kwargs: effects.append((args, kwargs))
    )
    with pytest.raises(HTTPException) as refusal:
        await routes.close_verified_task(
            p["binding_id"],
            routes.VerifiedClose(operation_key="close-key-1", expected_hash="a" * 64),
            SimpleNamespace(),
            principal=f.subject,
        )
    assert refusal.value.status_code == 409 and not effects


def test_public_owner_read_scope_can_inspect_assigned_binding(beads_work):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cli_agent_orchestrator.api.beads_routes import router
    from cli_agent_orchestrator.security import auth

    f, service, _ = beads_work
    p = prepared(beads_work)
    approval_store.grant(p["plan_id"], "fixture-admin")
    service.start(
        f.subject, p["binding_id"], p["plan_id"], "controller-run", scan_dir=str(f.directory)
    )
    read_owner = auth._verified_principal(
        f.subject.issuer, f.subject.subject, [auth.SCOPE_READ], f.subject.kind
    )
    assert read_owner.id == f.subject.id
    app = FastAPI()
    app.include_router(router)
    for name, value in vars(f.app.state).items():
        setattr(app.state, name, value)
    app.state.workflow_coordinator = f.service
    # The trusted test factory supplies the authentic sealed owner identity;
    # scope dependencies, coordinator/Beads owners and real persistence remain live.
    app.dependency_overrides[auth.get_current_principal] = lambda: read_owner
    with TestClient(app) as client:
        response = client.get("/beads/assignments/" + p["binding_id"])
    assert response.status_code == 200, response.text
    assert response.json()["run_id"] == "controller-run"
    assert response.json()["work_verified_completed"] is False


def test_progress_read_hides_authentic_foreign_owner_and_does_not_write_binding(beads_work):
    from cli_agent_orchestrator.security import auth

    f, service, adapter = beads_work
    p = completed(beads_work)
    task = beads.task_dto(adapter.task)
    reader = auth._verified_principal(
        f.subject.issuer, f.subject.subject, [auth.SCOPE_READ], f.subject.kind
    )
    foreign = auth._verified_principal(
        f.subject.issuer, "another-real-reader", [auth.SCOPE_READ], f.subject.kind
    )
    with f.context.repo.read_snapshot() as connection:
        before = dict(connection.execute("SELECT * FROM beads_work_bindings").fetchone())
    owner = service.describe_tasks(reader, "repo", [task])[0]
    assert (
        owner["work_verified_completed"] is True
        and owner["work_assignment"]["run_id"] == "controller-run"
    )
    assert len(owner["work_attempts"]) == 1 and owner["work_attempts"][0]["state"] == "finished"
    assert owner["material_hash"] == task["material_hash"]
    denied = service.describe_tasks(foreign, "repo", [task])[0]
    assert (
        denied["work_assignment"] is None
        and denied["work_attempts"] == []
        and denied["work_verified_completed"] is False
    )
    with f.context.repo.read_snapshot() as connection:
        after = dict(connection.execute("SELECT * FROM beads_work_bindings").fetchone())
    assert before == after
    changed = beads.task_dto(replace(adapter.task, title="Actually changed task requirements"))
    assert not service.describe_tasks(reader, "repo", [changed])[0]["work_verified_completed"]


def test_actual_verified_external_close_preserves_accepted_work_progress(beads_work, monkeypatch):
    from cli_agent_orchestrator.api import beads_routes as routes

    f, service, adapter = beads_work
    p = completed(beads_work)
    monkeypatch.setattr(routes, "assignments_for_request", lambda request: service)

    def close(identity, reason=None):
        assert identity == adapter.task.id
        adapter.task = replace(adapter.task, status="closed")
        return adapter.task

    adapter.close = close
    response = asyncio.run(
        routes.close_verified_task(
            p["binding_id"],
            routes.VerifiedClose(
                operation_key="review-verified-close",
                expected_hash=beads.task_dto(adapter.task)["material_hash"],
            ),
            SimpleNamespace(),
            principal=f.subject,
        )
    )
    assert response["state"] == "applied", response
    task = beads.task_dto(adapter.task)
    assert task["external_status"] == "closed"
    projected = service.describe_tasks(f.subject, "repo", [task])[0]
    assert projected["work_verified_completed"] is True
    assert projected["material_hash"] == task["material_hash"]
    assert projected["work_assignment"]["run_id"] == "controller-run"


def test_public_metadata_list_above_projection_chunk_preserves_supported_tasks(beads_work):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cli_agent_orchestrator.api import beads_routes as routes
    from cli_agent_orchestrator.clients.beads import Task
    from cli_agent_orchestrator.security import auth

    f, service, adapter = beads_work
    adapter.list = lambda status=None, priority=None: [
        Task("task-" + str(i), "Task " + str(i)) for i in range(257)
    ]
    app = FastAPI()
    app.include_router(routes.router)
    for name, value in vars(f.app.state).items():
        setattr(app.state, name, value)
    app.state.workflow_coordinator = f.service
    app.dependency_overrides[routes.optional_principal] = lambda: f.subject
    app.dependency_overrides[auth.get_current_scopes] = lambda: [auth.SCOPE_READ]
    with TestClient(app) as client:
        response = client.get("/beads/workspaces/repo/tasks")
    assert response.status_code == 200, response.text
    assert len(response.json()["tasks"]) == 257


def test_same_workspace_alias_rebind_cannot_credit_old_task_scope(
    beads_work, monkeypatch, tmp_path
):
    f, service, adapter = beads_work
    completed(beads_work)
    original = beads.task_dto(adapter.task)
    assert service.describe_tasks(f.subject, "repo", [original])[0]["work_verified_completed"]
    root = tmp_path / "different-beads-repository"
    root.mkdir()
    info = root.stat()
    changed_workspace = beads.BeadsWorkspace(
        "repo", root, "modern", (info.st_dev, info.st_ino), "b" * 64
    )
    adapter.working_dir = root
    monkeypatch.setattr(beads, "client_for", lambda _: (changed_workspace, adapter))
    # Identical task IDs/bodies in a newly configured workspace are not proof
    # of the old private checkout's Work completion.
    value = service.describe_tasks(f.subject, "repo", [original])[0]
    assert value["work_verified_completed"] is False
    assert value["work_assignment"] is None
    assert value["work_attempts"] == []


def test_verified_close_refuses_rebound_workspace_alias_before_external_effect(
    beads_work, monkeypatch, tmp_path
):
    from fastapi import HTTPException

    from cli_agent_orchestrator.api import beads_routes as routes

    f, service, adapter = beads_work
    p = completed(beads_work)
    root = tmp_path / "new-task-repository"
    root.mkdir()
    info = root.stat()
    workspace = beads.BeadsWorkspace("repo", root, "modern", (info.st_dev, info.st_ino), "b" * 64)
    adapter.working_dir = root
    monkeypatch.setattr(beads, "client_for", lambda _: (workspace, adapter))
    monkeypatch.setattr(routes, "assignments_for_request", lambda request: service)
    effects = []

    def close(identity, reason=None):
        effects.append(identity)
        adapter.task = replace(adapter.task, status="closed")
        return adapter.task

    adapter.close = close
    with pytest.raises(HTTPException) as refusal:
        asyncio.run(
            routes.close_verified_task(
                p["binding_id"],
                routes.VerifiedClose(
                    operation_key="rebound-close-operation",
                    expected_hash=beads.task_dto(adapter.task)["material_hash"],
                ),
                SimpleNamespace(),
                principal=f.subject,
            )
        )
    assert refusal.value.status_code == 409
    assert effects == []


def test_verified_close_refuses_edited_task_even_with_fresh_current_hash(beads_work, monkeypatch):
    from fastapi import HTTPException

    from cli_agent_orchestrator.api import beads_routes as routes

    f, service, adapter = beads_work
    p = completed(beads_work)
    adapter.task = replace(adapter.task, title="New unfulfilled requirements")
    monkeypatch.setattr(routes, "assignments_for_request", lambda request: service)
    effects = []

    def close(identity, reason=None):
        effects.append(identity)
        adapter.task = replace(adapter.task, status="closed")
        return adapter.task

    adapter.close = close
    with pytest.raises(HTTPException) as refusal:
        asyncio.run(
            routes.close_verified_task(
                p["binding_id"],
                routes.VerifiedClose(
                    operation_key="edited-task-close-operation",
                    expected_hash=beads.task_dto(adapter.task)["material_hash"],
                ),
                SimpleNamespace(),
                principal=f.subject,
            )
        )
    assert refusal.value.status_code == 409
    assert effects == [] and adapter.task.status == "open"
