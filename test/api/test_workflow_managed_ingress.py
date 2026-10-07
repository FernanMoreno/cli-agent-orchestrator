"""API boundaries for server-owned managed workflow admission."""

from test.services.test_work_launch_runtime import ProtectedFakeBackend, trusted_setup
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.clients.database import (
    _migrate_workflow_run,
    _migrate_workflow_run_step,
)
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.constants import TERMINALS_RUN_STEP_ROUTE
from cli_agent_orchestrator.models.terminal import AgentStepResult, TerminalStatus
from cli_agent_orchestrator.services import workflow_journal, workflow_service


@pytest.fixture(autouse=True)
def _isolated_journal(tmp_path, monkeypatch):
    database = tmp_path / "workflow-managed.db"
    monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", database)
    _migrate_workflow_run()
    _migrate_workflow_run_step()
    WorkRepository(database).initialize()
    return database


def test_work_pending_script_step_stays_fenced_after_process_restart(client):
    """A durable Work marker must fence the terminal path without a process cache."""
    workflow_journal.insert_run(
        run_id="managed-after-restart",
        workflow_name="workflow",
        spec_snapshot='{"source":"# frozen script\\n","path":"workflow.py"}',
        inputs_json="{}",
        state="running",
        started_at="2026-09-29T00:00:00Z",
        tier="script",
        generation="1",
    )
    workflow_journal.begin_step(
        "managed-after-restart", "step-a", "2026-09-29T00:00:01Z", "v2:" + "a" * 64
    )
    workflow_journal.mark_work_pending(
        run_id="managed-after-restart",
        step_id="step-a",
        generation="1",
        step_attempt=1,
        tier="script",
        updated_at="2026-09-29T00:00:02Z",
    )
    workflow_service.run_registry.pop("managed-after-restart", None)

    with patch(
        "cli_agent_orchestrator.api.main.run_agent_step",
        new=AsyncMock(
            return_value=AgentStepResult(
                terminal_id="abcdef12",
                last_message="legacy result",
                status=TerminalStatus.COMPLETED,
            )
        ),
    ) as run_agent_step:
        response = client.post(
            TERMINALS_RUN_STEP_ROUTE,
            json={
                "provider": "kiro_cli",
                "agent": "developer",
                "prompt": "do not execute",
                "env_vars": {
                    "CAO_WORKFLOW_RUN_ID": "managed-after-restart",
                    "CAO_WORKFLOW_STEP_ID": "step-a",
                    "CAO_WORKFLOW_GENERATION": "1",
                },
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["kind"] == "work_pending"
    run_agent_step.assert_not_awaited()


def test_yaml_start_resolves_callbacks_from_verified_identity_and_frozen_snapshot(
    client, monkeypatch
):
    """Only the server resolver can attach a managed-step callback to a run."""
    from cli_agent_orchestrator.models.workflow import WorkflowSpec, WorkflowStep
    from cli_agent_orchestrator.models.workflow_runtime import (
        RunState,
        WorkflowRunResult,
    )
    from cli_agent_orchestrator.services import workflow_spec_service

    spec = WorkflowSpec(
        name="managed-yaml",
        steps=[WorkflowStep(id="step-a", provider="kiro_cli", agent="dev", prompt="hello")],
    )
    callback = AsyncMock(return_value={"id": "work-1", "attempts": [{"id": "attempt-1"}]})

    class Origins:
        def __init__(self):
            self.calls = []

        def resolve_step_admitter(self, principal, workflow_id, spec_hash, step_id):
            self.calls.append((principal, workflow_id, spec_hash, step_id))
            return callback

    origins = Origins()
    previous_origins = getattr(main.app.state, "work_workflow_origins", None)
    main.app.state.work_workflow_origins = origins
    start = AsyncMock(
        return_value=WorkflowRunResult(
            run_id="managed-run",
            workflow_name=spec.name,
            state=RunState.RUNNING,
            steps=[],
            started_at="2026-09-29T00:00:00Z",
            finished_at=None,
        )
    )
    monkeypatch.setattr(workflow_spec_service, "get_workflow", lambda _name: spec)
    monkeypatch.setattr(workflow_service, "start_run", start)
    try:
        response = client.post(
            "/workflows/runs",
            json={"name_or_path": spec.name, "run_id": "managed-run", "inputs": {}},
        )
    finally:
        if previous_origins is None:
            del main.app.state.work_workflow_origins
        else:
            main.app.state.work_workflow_origins = previous_origins

    assert response.status_code == 200
    assert len(origins.calls) == 1
    principal, workflow_id, spec_hash, step_id = origins.calls[0]
    assert principal.id.startswith("principal-")
    assert workflow_id == spec.name
    assert step_id == "step-a"
    assert start.await_args.kwargs["managed_step_admitters"] == {"step-a": callback}


def test_managed_script_step_marks_pending_before_admission_and_never_runs_terminal(
    client, monkeypatch
):
    """A run-scoped capability selects the frozen provision and fences terminal execution."""
    from cli_agent_orchestrator.security import auth

    workflow_journal.insert_run(
        run_id="managed-script",
        workflow_name="script-workflow",
        spec_snapshot='{"source":"# frozen source\\n","path":"workflow.py"}',
        inputs_json="{}",
        state="running",
        started_at="2026-09-29T00:00:00Z",
        tier="script",
        generation="1",
    )
    principal = auth._verified_principal(
        "urn:test:issuer", "script-owner", [auth.SCOPE_WRITE], "jwt"
    )
    calls = []

    class Origins:
        def authenticate_run_capability(self, run_id, run_generation, token):
            calls.append(("authenticate", run_id, run_generation, token))
            return principal

        def resolve_step_admitter(self, actor, workflow_id, spec_hash, step_id):
            calls.append(("resolve", actor.id, workflow_id, spec_hash, step_id))

            async def admit(**kwargs):
                assert set(kwargs) <= {
                    "tier",
                    "run_id",
                    "run_generation",
                    "step_id",
                    "step_attempt",
                    "workflow_step_attempt",
                    "workflow_id",
                    "spec_hash",
                    "recover",
                    "prompt",
                    "inputs",
                    "call_fingerprint",
                }
                step = workflow_journal.get_step(kwargs["run_id"], kwargs["step_id"])
                calls.append(("admit", kwargs, step.state, step.attempts))
                return {"id": "work-1", "attempts": [{"id": "attempt-1", "generation": 1}]}

            return admit

    origins = Origins()
    monkeypatch.setattr(workflow_journal, "get_work_step_projection", lambda *_: None)
    previous_origins = getattr(main.app.state, "work_workflow_origins", None)
    main.app.state.work_workflow_origins = origins
    workflow_service.run_registry.pop("managed-script", None)
    legacy = AsyncMock(
        return_value=AgentStepResult(
            terminal_id="abcdef12",
            last_message="legacy terminal result",
            status=TerminalStatus.COMPLETED,
        )
    )
    monkeypatch.setattr(main, "run_agent_step", legacy)
    try:
        response = client.post(
            TERMINALS_RUN_STEP_ROUTE,
            headers={"X-CAO-Workflow-Run-Credential": "opaque-run-capability"},
            json={
                "provider": "kiro_cli",
                "agent": "caller-chosen-agent",
                "prompt": "managed task",
                "env_vars": {
                    "CAO_WORKFLOW_RUN_ID": "managed-script",
                    "CAO_WORKFLOW_GENERATION": "1",
                    "CAO_WORKFLOW_STEP_ID": "step-a",
                },
            },
        )
    finally:
        if previous_origins is None:
            del main.app.state.work_workflow_origins
        else:
            main.app.state.work_workflow_origins = previous_origins

    assert response.status_code == 409
    assert response.json()["detail"]["kind"] == "work_pending"
    step = workflow_journal.get_step("managed-script", "step-a")
    assert step.state == "work_pending"
    assert step.attempts == 1
    admission = next(call for call in calls if call[0] == "admit")
    assert admission[2:] == ("work_pending", 1)
    assert admission[1]["tier"] == "script"
    assert admission[1]["run_generation"] == 1
    assert admission[1]["step_attempt"] == 1
    assert admission[1]["recover"] is False
    legacy.assert_not_awaited()


def test_managed_script_step_without_run_capability_never_falls_back_to_terminal(
    client, monkeypatch
):
    """Run ID/env identify a step but cannot authenticate its provisioned path."""
    from cli_agent_orchestrator.models.workflow_runtime import RunState
    from cli_agent_orchestrator.services.script_runner import ScriptRunRecord

    workflow_journal.insert_run(
        run_id="managed-script-no-cap",
        workflow_name="script-workflow",
        spec_snapshot='{"source":"# frozen source\\n","path":"workflow.py"}',
        inputs_json="{}",
        state="running",
        started_at="2026-09-29T00:00:00Z",
        tier="script",
        generation="1",
    )
    callback = AsyncMock()
    workflow_service.run_registry["managed-script-no-cap"] = ScriptRunRecord(
        run_id="managed-script-no-cap",
        workflow_name="script-workflow",
        state=RunState.RUNNING,
        cancelled=False,
        current_step_id=None,
        step_states={},
        process=None,
        generation="1",
        started_at="2026-09-29T00:00:00Z",
        finished_at=None,
        managed_step_admitters={"step-a": callback},
        run_capability_required=True,
    )
    legacy = AsyncMock(
        return_value=AgentStepResult(
            terminal_id="abcdef12",
            last_message="legacy terminal result",
            status=TerminalStatus.COMPLETED,
        )
    )
    monkeypatch.setattr(main, "run_agent_step", legacy)

    response = client.post(
        TERMINALS_RUN_STEP_ROUTE,
        json={
            "provider": "kiro_cli",
            "agent": "developer",
            "prompt": "must not execute",
            "env_vars": {
                "CAO_WORKFLOW_RUN_ID": "managed-script-no-cap",
                "CAO_WORKFLOW_GENERATION": "1",
                "CAO_WORKFLOW_STEP_ID": "step-a",
            },
        },
    )

    assert response.status_code == 401
    callback.assert_not_awaited()
    legacy.assert_not_awaited()


def test_managed_script_after_restart_requires_capability_even_without_pending_marker(
    client, monkeypatch
):
    """Run ID and env data cannot recover a managed process after server restart."""
    workflow_journal.insert_run(
        run_id="managed-script-restart-no-cap",
        workflow_name="script-workflow",
        spec_snapshot='{"source":"# frozen source\\n","path":"workflow.py"}',
        inputs_json="{}",
        state="running",
        started_at="2026-09-29T00:00:00Z",
        tier="script",
        generation="1",
    )
    workflow_service.run_registry.pop("managed-script-restart-no-cap", None)

    class Origins:
        def authenticate_run_capability(self, *_args):
            raise AssertionError("missing capability must be rejected before authentication")

        def resolve_step_admitter(self, *_args):
            raise AssertionError("missing capability must not resolve a provision")

    previous = getattr(main.app.state, "work_workflow_origins", None)
    main.app.state.work_workflow_origins = Origins()
    legacy = AsyncMock()
    monkeypatch.setattr(main, "run_agent_step", legacy)
    try:
        response = client.post(
            TERMINALS_RUN_STEP_ROUTE,
            json={
                "provider": "kiro_cli",
                "agent": "developer",
                "prompt": "must not execute after restart",
                "env_vars": {
                    "CAO_WORKFLOW_RUN_ID": "managed-script-restart-no-cap",
                    "CAO_WORKFLOW_GENERATION": "1",
                    "CAO_WORKFLOW_STEP_ID": "step-a",
                },
            },
        )
    finally:
        if previous is None:
            del main.app.state.work_workflow_origins
        else:
            main.app.state.work_workflow_origins = previous

    assert response.status_code == 401
    legacy.assert_not_awaited()


def test_managed_script_replay_returns_typed_projected_work_result(client, monkeypatch):
    """Only an authenticated capability plus journal projection can replay output."""
    import hashlib
    from types import SimpleNamespace

    from cli_agent_orchestrator.models.workflow_runtime import RunState
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services import script_runner, step_fingerprint

    source = "# frozen managed source\\n"
    workflow_journal.insert_run(
        run_id="managed-script-result",
        workflow_name="script-workflow",
        spec_snapshot=__import__("json").dumps(
            {"source": source, "path": "workflow.py", "content_hash": None}
        ),
        inputs_json="{}",
        state="running",
        started_at="2026-09-29T00:00:00Z",
        tier="script",
        generation="2",
    )
    workflow_journal.insert_steps(
        "managed-script-result", [("step-a", "pending")], "2026-09-29T00:00:00Z"
    )
    workflow_journal.begin_managed_work_step(
        "managed-script-result",
        "step-a",
        "2",
        "call-fingerprint-v2",
        "2026-09-29T00:00:01Z",
    )
    # T121's projection is the authoritative, typed source for replay. This
    # route-level test pins its identity checks and wire shape without building
    # the Work repository tables a second time.
    workflow_journal.update_step(
        "managed-script-result",
        "step-a",
        "completed",
        1,
        "2026-09-29T00:00:02Z",
        output_json='{"answer":"42"}',
    )
    projected_result = '{"output":{"answer":"42"},"schema_version":1,"status":"completed"}'
    projection = SimpleNamespace(
        run_id="managed-script-result",
        step_id="step-a",
        tier="script",
        run_generation=1,
        step_attempt=1,
        result_json=projected_result,
    )
    monkeypatch.setattr(
        workflow_journal,
        "get_work_step_projection",
        lambda run_id, step_id: projection,
    )
    monkeypatch.setattr(step_fingerprint, "compute", lambda _fields: "call-fingerprint-v2")
    principal = auth._verified_principal(
        "urn:test:issuer", "script-owner", [auth.SCOPE_WRITE], "jwt"
    )
    seen = []

    class Origins:
        def authenticate_run_capability(self, run_id, run_generation, token):
            seen.append(("authenticate", run_id, run_generation, token))
            return principal

        def resolve_step_admitter(self, actor, workflow_id, spec_hash, step_id):
            seen.append(("resolve", actor.id, workflow_id, spec_hash, step_id))
            return AsyncMock()

    origins = Origins()
    previous_origins = getattr(main.app.state, "work_workflow_origins", None)
    main.app.state.work_workflow_origins = origins
    workflow_service.run_registry["managed-script-result"] = script_runner.ScriptRunRecord(
        run_id="managed-script-result",
        workflow_name="script-workflow",
        state=RunState.RUNNING,
        cancelled=False,
        current_step_id="step-a",
        step_states={},
        process=None,
        generation="2",
        started_at="2026-09-29T00:00:00Z",
        finished_at=None,
        run_capability_required=True,
    )
    legacy = AsyncMock()
    monkeypatch.setattr(main, "run_agent_step", legacy)
    try:
        response = client.post(
            TERMINALS_RUN_STEP_ROUTE,
            headers={"X-CAO-Workflow-Run-Credential": "opaque-run-capability"},
            json={
                "provider": "kiro_cli",
                "agent": "developer",
                "prompt": "call fingerprint source",
                "env_vars": {
                    "CAO_WORKFLOW_RUN_ID": "managed-script-result",
                    "CAO_WORKFLOW_GENERATION": "2",
                    "CAO_WORKFLOW_STEP_ID": "step-a",
                },
            },
        )
    finally:
        if previous_origins is None:
            del main.app.state.work_workflow_origins
        else:
            main.app.state.work_workflow_origins = previous_origins

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "completed",
        "replayed": True,
        "work_result": {
            "schema_version": 1,
            "status": "completed",
            "output": {"answer": "42"},
        },
    }
    assert seen[0] == (
        "authenticate",
        "managed-script-result",
        2,
        "opaque-run-capability",
    )
    assert [event for event in seen if event[0] == "resolve"] == [
        (
            "resolve",
            principal.id,
            "script-workflow",
            hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "step-a",
        )
    ]
    assert [event for event in seen if event[0] == "authenticate"] == [seen[0], seen[0]]
    legacy.assert_not_awaited()


@pytest.mark.parametrize("tier", ["script", "yaml"])
def test_managed_retry_route_consumes_exact_authorization_before_admission(
    client, monkeypatch, tier
):
    """Operator retry uses only exact journal/Work fences and one durable claim."""
    import hashlib
    import json
    from types import SimpleNamespace

    from cli_agent_orchestrator.models.workflow import WorkflowSpec, WorkflowStep

    events = []
    if tier == "script":
        workflow_id = "script-workflow"
        frozen_source = "# frozen retry source\n"
        spec_snapshot = json.dumps({"source": frozen_source, "path": "flow.py"})
        spec_hash = hashlib.sha256(frozen_source.encode("utf-8")).hexdigest()
    else:
        workflow_id = "yaml-workflow"
        frozen_spec = WorkflowSpec(
            name=workflow_id,
            steps=[
                WorkflowStep(
                    id="step-a",
                    provider="kiro_cli",
                    agent="developer",
                    prompt="frozen retry prompt",
                    retries=2,
                )
            ],
        )
        spec_snapshot = frozen_spec.model_dump_json()
        spec_hash = hashlib.sha256(spec_snapshot.encode("utf-8")).hexdigest()
        monkeypatch.setattr(
            workflow_service,
            "_rebuild_record_from_journal",
            lambda _run_id: SimpleNamespace(inputs={}, step_states={}),
        )

    run = SimpleNamespace(
        run_id="managed-retry",
        workflow_name=workflow_id,
        spec_snapshot=spec_snapshot,
        inputs_json="{}",
        state="running",
        tier=tier,
        generation="1",
        current_step_id="step-a",
    )
    step = SimpleNamespace(
        run_id="managed-retry",
        step_id="step-a",
        state="failed",
        attempts=1,
        error_kind="managed_work_failed",
        call_fingerprint="prior-call-fingerprint",
    )
    monkeypatch.setattr(workflow_journal, "get_run", lambda _run_id: run)
    monkeypatch.setattr(workflow_journal, "get_step", lambda *_args: step)
    authorization = SimpleNamespace(
        authorization_id="retry-auth-1",
        authorization_fingerprint="a" * 64,
        run_id="managed-retry",
        run_generation=1,
        workflow_id=workflow_id,
        spec_hash=spec_hash,
        tier=tier,
        step_id="step-a",
        workflow_step_attempt=1,
        work_attempt_id="prior-work-attempt",
        work_generation=7,
    )

    class Origins:
        def authorize_step_retry(self, principal, **kwargs):
            events.append(("authorize", principal, kwargs))
            return authorization

        def resolve_step_admitter(self, principal, workflow_id, spec_hash, step_id):
            events.append(("resolve", principal, workflow_id, spec_hash, step_id))

            async def admit(**kwargs):
                events.append(("admit", kwargs))
                return {
                    "id": "work-item-retry",
                    "attempts": [{"id": "next-work-attempt", "generation": 8}],
                }

            return admit

    class Projector:
        def project_pending_for_run(self, run_id):
            events.append(("project", run_id))
            return ()

    def begin(*args, **kwargs):
        events.append(("begin", args, kwargs))
        assert kwargs["retry_authorization"] is authorization
        assert kwargs["principal"] is events[0][1]
        assert kwargs["workflow_origins"] is origins
        assert kwargs["work_service"] is result_service
        step.state = "work_pending"
        step.attempts = 2
        step.error_kind = None
        return 2

    monkeypatch.setattr(workflow_journal, "begin_managed_work_step", begin)
    origins = Origins()
    result_service = object()
    previous = {
        name: getattr(main.app.state, name, None)
        for name in (
            "work_workflow_origins",
            "work_workflow_result_service",
            "workflow_step_projector",
        )
    }
    main.app.state.work_workflow_origins = origins
    main.app.state.work_workflow_result_service = result_service
    main.app.state.workflow_step_projector = Projector()
    try:
        response = client.post(
            "/workflows/runs/managed-retry/steps/step-a/retry",
            json={
                "run_generation": 1,
                "workflow_step_attempt": 1,
                "work_attempt_id": "prior-work-attempt",
                "work_generation": 7,
            },
        )
        replayed = client.post(
            "/workflows/runs/managed-retry/steps/step-a/retry",
            json={
                "run_generation": 1,
                "workflow_step_attempt": 1,
                "work_attempt_id": "prior-work-attempt",
                "work_generation": 7,
            },
        )
        stale = client.post(
            "/workflows/runs/managed-retry/steps/step-a/retry",
            json={
                "run_generation": 2,
                "workflow_step_attempt": 1,
                "work_attempt_id": "prior-work-attempt",
                "work_generation": 7,
            },
        )
    finally:
        for name, value in previous.items():
            if value is None:
                if hasattr(main.app.state, name):
                    delattr(main.app.state, name)
            else:
                setattr(main.app.state, name, value)

    assert response.status_code == 202, response.text
    assert response.json() == {
        "run_id": "managed-retry",
        "step_id": "step-a",
        "state": "retry_admitted",
        "workflow_step_attempt": 2,
        "work_attempt_id": "next-work-attempt",
        "work_generation": 8,
        "authorization_id": "retry-auth-1",
    }
    assert replayed.status_code == 202, replayed.text
    assert replayed.json() == response.json()
    assert stale.status_code == 409
    assert [event[0] for event in events] == [
        "authorize",
        "resolve",
        "begin",
        "admit",
        "project",
        "authorize",
        "resolve",
        "admit",
        "project",
    ]
    assert events[2][2]["retry_authorization"] is authorization
    assert events[3][1]["step_attempt"] == 2
    assert events[3][1]["recover"] is False
    assert events[7][1]["step_attempt"] == 2
    assert events[7][1]["recover"] is True
    assert events[3][1]["tier"] == tier
    if tier == "yaml":
        assert events[3][1]["prompt"] == "frozen retry prompt"
        assert events[3][1]["step"].id == "step-a"


@pytest.mark.asyncio
async def test_managed_workflow_runtime_composition_binds_startup_projector(
    trusted_setup, monkeypatch
):
    """Lifespan composes result replay on the gateway's exact Work runtime."""
    from cli_agent_orchestrator.services import work_launch_gateway, workflow_step_projector

    repository, *_rest = trusted_setup
    monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", repository.path)
    gateway = work_launch_gateway.build_durable_launch_gateway(
        repository, backends={"test": ProtectedFakeBackend()}
    )
    called = []
    original = workflow_step_projector.WorkflowStepProjector.project_pending_at_startup

    def record_startup_projection(projector):
        called.append(projector)
        return original(projector)

    monkeypatch.setattr(
        workflow_step_projector.WorkflowStepProjector,
        "project_pending_at_startup",
        record_startup_projection,
    )
    previous = {
        name: getattr(main.app.state, name, None)
        for name in (
            "work_workflow_origins",
            "work_workflow_result_service",
            "workflow_step_projector",
        )
    }
    try:
        projector = await main._initialize_managed_workflow_runtime(main.app, repository, gateway)
        runtime = gateway._launch_runtime_provider._runtime
        assert runtime._admission.workflow_origins is main.app.state.work_workflow_origins
        assert projector is main.app.state.workflow_step_projector
        assert projector.workflow_origins is runtime._admission.workflow_origins
        assert projector.work_service is main.app.state.work_workflow_result_service
        assert projector.work_service.origins is runtime.origins
        assert projector.work_service.artifacts.root == repository.path.with_name(
            repository.path.name + ".result-content"
        )
        assert called == [projector]
    finally:
        for name, value in previous.items():
            if value is None:
                try:
                    delattr(main.app.state, name)
                except AttributeError:
                    pass
            else:
                setattr(main.app.state, name, value)
