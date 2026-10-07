"""External task assignments use actual approved scoped Work and accepted bytes."""

import asyncio
from dataclasses import replace
from test.services.test_integration_008_coordinator import (  # noqa:F401
    accept_iteration,
    admit_iteration,
    context,
    coordinator_context,
    plan_context,
)

import pytest

from cli_agent_orchestrator.clients.beads import Task
from cli_agent_orchestrator.services import approval_store, beads_service
from cli_agent_orchestrator.services.beads_assignment_service import BeadsAssignments


@pytest.fixture
def beads_work(coordinator_context, monkeypatch):
    f = coordinator_context
    from cli_agent_orchestrator.clients.beads_work_schema import initialize

    with f.context.repo.transaction() as connection:
        initialize(connection)
    info = f.context.root.stat()
    workspace = beads_service.BeadsWorkspace(
        "repo", f.context.root, "modern", (info.st_dev, info.st_ino), "a" * 64
    )

    class Adapter:
        working_dir = f.context.root
        task = Task("task-1", "Return answer 42", "Explicit task criteria")

        def get(self, identity):
            return self.task if identity == self.task.id else None

    adapter = Adapter()
    monkeypatch.setattr(beads_service, "client_for", lambda _: (workspace, adapter))
    return f, BeadsAssignments(f.service), adapter


def prepare(values, key="prepare-key-1"):
    f, service, adapter = values
    return service.prepare(
        f.subject,
        "repo",
        "task-1",
        operation_key=key,
        expected_hash=beads_service.task_dto(adapter.task)["material_hash"],
        workflow_name="wf",
        criteria=[{"kind": "output_equals", "path": ["answer"], "value": 42}],
        binding_selections={"repo": {"developer": "controller-seed"}},
        scan_dir=str(f.directory),
    )


def test_prepare_replay_retains_reviewed_plan_and_changed_material_refuses(beads_work):
    f, service, adapter = beads_work
    first = prepare(beads_work)
    assert prepare(beads_work) == first
    assert first["state"] == "prepared" and not first["work_verified_completed"]
    adapter.task = replace(adapter.task, title="Changed task")
    with pytest.raises(beads_service.BeadsConflict):
        prepare(beads_work)
    with f.context.repo.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM beads_work_bindings").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM workflow_coordinator").fetchone()[0] == 0


def test_start_requires_approval_and_actual_material_no_coordinator_on_refusal(beads_work):
    f, service, adapter = beads_work
    p = prepare(beads_work)
    with pytest.raises(ValueError):
        service.start(
            f.subject, p["binding_id"], p["plan_id"], "controller-run", scan_dir=str(f.directory)
        )
    approval_store.grant(p["plan_id"], "fixture-admin")
    adapter.task = replace(adapter.task, description="Changed after approval")
    with pytest.raises(beads_service.BeadsConflict):
        service.start(
            f.subject, p["binding_id"], p["plan_id"], "controller-run", scan_dir=str(f.directory)
        )
    with f.context.repo.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM workflow_coordinator").fetchone()[0] == 0


def test_one_run_exact_retry_actual_checkpoint_and_external_close_is_separate(beads_work):
    f, service, adapter = beads_work
    p = prepare(beads_work)
    approval_store.grant(p["plan_id"], "fixture-admin")
    result = service.start(
        f.subject, p["binding_id"], p["plan_id"], "controller-run", scan_dir=str(f.directory)
    )
    assert not result["work_verified_completed"]
    replay = service.start(
        f.subject, p["binding_id"], p["plan_id"], "controller-run", scan_dir=str(f.directory)
    )
    assert result["coordinator_id"] == replay["coordinator_id"]
    with pytest.raises(beads_service.BeadsConflict):
        service.start(
            f.subject, p["binding_id"], p["plan_id"], "different-run", scan_dir=str(f.directory)
        )
    epoch = f.driver.claim(f.subject, "controller-run")
    admit_iteration(f, 1)
    accept_iteration(f, 42)
    f.driver.release("controller-run", epoch)
    assert f.projector.project_pending_for_run("controller-run")[0].status == "projected"
    with f.context.repo.read_snapshot() as connection:
        event = connection.execute("SELECT * FROM workflow_continuation_outbox").fetchone()
    f.service.checkpoint(f.subject, "controller-run", event)
    completed = service.status(f.subject, p["binding_id"])
    assert completed["work_verified_completed"] and completed["state"] == "completed"
    assert adapter.task.status == "open"
    with f.context.repo.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM workflow_coordinator").fetchone()[0] == 1
        assert (
            connection.execute("SELECT state FROM beads_work_bindings").fetchone()[0] == "completed"
        )


def test_other_assignment_same_task_refuses_in_same_start_transaction(beads_work):
    f, service, _ = beads_work
    one = prepare(beads_work, "first-key-1")
    two = prepare(beads_work, "second-key-1")
    for p in (one, two):
        approval_store.grant(p["plan_id"], "fixture-admin")
    service.start(
        f.subject, one["binding_id"], one["plan_id"], "first-run", scan_dir=str(f.directory)
    )
    with pytest.raises(ValueError):
        service.start(
            f.subject, two["binding_id"], two["plan_id"], "second-run", scan_dir=str(f.directory)
        )
    with f.context.repo.read_snapshot() as connection:
        assert (
            connection.execute("SELECT 1 FROM workflow_run WHERE run_id='second-run'").fetchone()
            is None
        )
        assert connection.execute("SELECT count(*) FROM workflow_coordinator").fetchone()[0] == 1


def test_unassign_queued_work_retains_active_slot_until_actual_cleanup(beads_work):
    f, service, _ = beads_work
    p = prepare(beads_work)
    approval_store.grant(p["plan_id"], "fixture-admin")
    service.start(
        f.subject, p["binding_id"], p["plan_id"], "controller-run", scan_dir=str(f.directory)
    )
    f.driver.claim(f.subject, "controller-run")
    admit_iteration(f, 1)
    result = asyncio.run(service.unassign(f.subject, p["binding_id"]))
    assert result["state"] == "stopping" and not result["work_verified_completed"]
    with f.context.repo.read_snapshot() as connection:
        assert (
            connection.execute("SELECT state FROM beads_work_bindings").fetchone()[0] == "stopping"
        )


def test_task_progress_uses_actual_accepted_work_and_preserves_material_hash(beads_work):
    f, service, adapter = beads_work
    task = beads_service.task_dto(adapter.task)
    p = prepare(beads_work)
    approval_store.grant(p["plan_id"], "fixture-admin")
    service.start(
        f.subject, p["binding_id"], p["plan_id"], "controller-run", scan_dir=str(f.directory)
    )
    before = service.describe_tasks(f.subject, "repo", [task])[0]
    assert not before["work_verified_completed"]
    assert before["work_assignment"]["run_id"] == "controller-run"
    epoch = f.driver.claim(f.subject, "controller-run")
    admit_iteration(f, 1)
    accept_iteration(f, 42)
    f.driver.release("controller-run", epoch)
    assert f.projector.project_pending_for_run("controller-run")[0].status == "projected"
    with f.context.repo.read_snapshot() as connection:
        event = connection.execute("SELECT * FROM workflow_continuation_outbox").fetchone()
    f.service.checkpoint(f.subject, "controller-run", event)
    after = service.describe_tasks(f.subject, "repo", [task])[0]
    assert after["work_verified_completed"]
    assert after["material_hash"] == task["material_hash"]
    assert len(after["work_attempts"]) == 1
    assert after["work_attempts"][0]["state"] == "finished"
    changed = beads_service.task_dto(replace(adapter.task, title="Different work"))
    assert not service.describe_tasks(f.subject, "repo", [changed])[0]["work_verified_completed"]
    from types import SimpleNamespace

    foreign = SimpleNamespace(id="foreign-owner", scopes=f.subject.scopes)
    with pytest.raises(PermissionError):
        service.describe_tasks(foreign, "repo", [task])


def test_api_task_and_epic_project_verified_work_separately_from_external_close(
    beads_work, monkeypatch
):
    from test.api.test_integration_008_beads_work_review import completed
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cli_agent_orchestrator.api import beads_routes
    from cli_agent_orchestrator.security import auth

    f, service, adapter = beads_work
    completed(beads_work)
    original = adapter.get
    parent = Task("epic-1", "Parent epic", type="epic")
    pending = Task("pending-1", "Closed externally", status="closed")
    adapter.get = lambda identity: parent if identity == parent.id else original(identity)
    adapter.get_children = lambda _: [adapter.task, pending]
    adapter.ready = lambda _: [adapter.task]
    app = FastAPI()
    app.include_router(beads_routes.router)
    app.state.work_workflow_origins = SimpleNamespace()
    app.state.workflow_step_projector = f.projector
    app.state.workflow_coordinator = f.service
    app.dependency_overrides[beads_routes.optional_principal] = lambda: f.subject
    app.dependency_overrides[auth.get_current_scopes] = lambda: [auth.SCOPE_READ]
    with TestClient(app) as client:
        task = client.get("/beads/workspaces/repo/tasks/task-1").json()
        epic = client.get("/beads/workspaces/repo/epics/epic-1").json()
    assert task["external_status"] == "open" and task["work_verified_completed"]
    assert task["work_assignment"]["run_id"] == "controller-run"
    assert epic["external_closed_count"] == 1
    assert epic["work_verified_completed_count"] == 1
    assert not epic["children"][1]["work_verified_completed"]
