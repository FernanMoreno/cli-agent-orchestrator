"""Route contracts for managed workflow authority and pending Work projection."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.models.workflow import WorkflowSpec, WorkflowStep
from cli_agent_orchestrator.models.workflow_runtime import RunState, WorkflowRunResult
from cli_agent_orchestrator.services import script_runner, workflow_journal


@pytest.fixture(autouse=True)
def _isolated_workflow_state(monkeypatch, isolated_memory_db):
    """Keep the real route journal and mutable app state scoped to this test."""
    monkeypatch.setattr(main.app.state, "work_workflow_origins", None, raising=False)
    monkeypatch.setattr(main.app.state, "workflow_step_projector", None, raising=False)
    monkeypatch.setattr(main.app.state, "workflow_coordinator", None, raising=False)
    return isolated_memory_db


@pytest.fixture(autouse=True)
def _legacy_script_gate(monkeypatch):
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.settings_service.is_workflow_approval_required",
        lambda: False,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.approval_gate.is_workflow_approval_required",
        lambda: False,
    )
    monkeypatch.setattr(main.approval_gate, "ensure_plan_approved", lambda **_kwargs: None)


def _yaml_spec(name: str) -> WorkflowSpec:
    return WorkflowSpec(
        name=name,
        steps=[
            WorkflowStep(
                id="step-a",
                provider="kiro_cli",
                agent="developer",
                prompt="run the reviewed step",
            )
        ],
    )


def _use_spec(monkeypatch, spec) -> None:
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.workflow_spec_service.get_workflow",
        lambda _name_or_path, scan_dir=None: spec,
    )


def _seed_script_run(
    run_id: str,
    snapshot: str,
    *,
    state: str = "failed",
    manifest_json: str | None = None,
) -> None:
    workflow_journal.insert_run(
        run_id=run_id,
        workflow_name="managed-script",
        spec_snapshot=snapshot,
        inputs_json='{"issue":"resume safely"}',
        state=state,
        started_at="2026-10-14T00:00:00Z",
        tier="script",
        generation="1",
        manifest_json=manifest_json,
    )


def _seed_pending_script(run_id: str, snapshot: str, *, manifest_json: str | None = None) -> None:
    started_at = "2026-10-14T00:00:00Z"
    _seed_script_run(
        run_id,
        snapshot,
        state="running",
        manifest_json=manifest_json,
    )
    workflow_journal.insert_steps(run_id, [("step-a", "pending")], started_at)
    fingerprint = "v2:" + "a" * 64
    workflow_journal.begin_step(run_id, "step-a", started_at, fingerprint)
    workflow_journal.mark_work_pending(
        run_id=run_id,
        step_id="step-a",
        generation="1",
        step_attempt=1,
        tier="script",
        updated_at=started_at,
        call_fingerprint=fingerprint,
    )


def test_script_resume_recovers_exact_pending_step_then_issues_bound_run_capability(
    client, monkeypatch
):
    """The real resume route uses only the frozen source's provision and identity."""
    run_id = f"managed-resume-{uuid.uuid4().hex}"
    source = "def main():\n    return 'frozen'\n"
    _seed_pending_script(run_id, json.dumps({"source": source, "path": "frozen.py"}))
    events = []

    async def recover(**kwargs):
        events.append(("recover", kwargs))
        return {"id": "work-recovered", "attempts": [{"generation": 1}]}

    class Origins:
        def resolve_step_admitter(self, principal, workflow_id, spec_hash, step_id):
            events.append(("resolve", principal, workflow_id, spec_hash, step_id))
            return recover

        def requires_run_capability(self, principal, workflow_id, spec_hash):
            events.append(("select", principal, workflow_id, spec_hash))
            return True

        def create_run_capability(self, principal, **kwargs):
            events.append(("issue", principal, kwargs))
            return "opaque-run-capability"

    projector_calls = []

    class Projector:
        def project_pending_for_run(self, projected_run_id):
            projector_calls.append(projected_run_id)
            if len(projector_calls) == 2:
                # Model the real projector applying the accepted Work result before
                # the route re-reads the step journal.
                workflow_journal.update_step(
                    run_id,
                    "step-a",
                    "completed",
                    1,
                    "2026-10-14T00:00:02Z",
                    output_json='{"recovered":true}',
                )

    monkeypatch.setattr(main.app.state, "work_workflow_origins", Origins(), raising=False)
    monkeypatch.setattr(main.app.state, "workflow_step_projector", Projector(), raising=False)
    resumed = AsyncMock(
        return_value=WorkflowRunResult(
            run_id=run_id,
            workflow_name="managed-script",
            state=RunState.COMPLETED,
            started_at="2026-10-14T00:00:00Z",
        )
    )
    monkeypatch.setattr(script_runner, "resume_script_run", resumed)

    response = client.post(f"/workflows/runs/{run_id}/resume")

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "completed"
    assert projector_calls == [run_id, run_id]
    resolved = next(event for event in events if event[0] == "resolve")
    assert resolved[2:] == (
        "managed-script",
        hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "step-a",
    )
    recovered = next(event for event in events if event[0] == "recover")[1]
    assert recovered == {
        "tier": "script",
        "run_id": run_id,
        "run_generation": 1,
        "step_id": "step-a",
        "step_attempt": 1,
        "workflow_step_attempt": 1,
        "recover": True,
        "inputs": {"issue": "resume safely"},
    }
    factory = resumed.await_args.kwargs["run_credential_factory"]
    assert factory(run_id, "1") == "opaque-run-capability"
    issued = next(event for event in events if event[0] == "issue")[2]
    assert issued == {
        "run_id": run_id,
        "workflow_id": "managed-script",
        "tier": "script",
        "run_generation": 1,
        "spec_hash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "ttl_seconds": 3600,
    }
    assert workflow_journal.get_steps(run_id)[0].state == "completed"


@pytest.mark.parametrize("failure", ["recovery", "projection"])
def test_pending_script_resume_remains_blocked_until_work_result_is_projected(
    client, monkeypatch, failure
):
    run_id = f"managed-resume-pending-{uuid.uuid4().hex}"
    source = "def main():\n    return 'frozen'\n"
    _seed_pending_script(run_id, json.dumps({"source": source, "path": "frozen.py"}))
    recover_calls = []
    projection_calls = []

    async def recover(**kwargs):
        recover_calls.append(kwargs)
        if failure == "recovery":
            raise RuntimeError("private recovery diagnostic")
        return {"id": "work-still-running", "attempts": [{"generation": 1}]}

    class Origins:
        def resolve_step_admitter(self, *_args):
            return recover

    class Projector:
        def project_pending_for_run(self, projected_run_id):
            projection_calls.append(projected_run_id)

    resumed = AsyncMock()
    monkeypatch.setattr(main.app.state, "work_workflow_origins", Origins(), raising=False)
    monkeypatch.setattr(main.app.state, "workflow_step_projector", Projector(), raising=False)
    monkeypatch.setattr(script_runner, "resume_script_run", resumed)

    response = client.post(f"/workflows/runs/{run_id}/resume")

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "managed Work attempt remains pending"
    assert "private recovery diagnostic" not in response.text
    assert recover_calls == [
        {
            "tier": "script",
            "run_id": run_id,
            "run_generation": 1,
            "step_id": "step-a",
            "step_attempt": 1,
            "workflow_step_attempt": 1,
            "recover": True,
            "inputs": {"issue": "resume safely"},
        }
    ]
    assert projection_calls == ([run_id] if failure == "recovery" else [run_id, run_id])
    assert workflow_journal.get_steps(run_id)[0].state == "work_pending"
    resumed.assert_not_awaited()


def test_script_resume_refuses_multiple_pending_work_steps_before_recovery(client, monkeypatch):
    run_id = f"managed-resume-multiple-pending-{uuid.uuid4().hex}"
    source = "def main():\n    return 'frozen'\n"
    _seed_pending_script(run_id, json.dumps({"source": source, "path": "frozen.py"}))
    started_at = "2026-10-14T00:00:01Z"
    workflow_journal.insert_steps(run_id, [("step-b", "pending")], started_at)
    fingerprint = "v2:" + "b" * 64
    workflow_journal.begin_step(run_id, "step-b", started_at, fingerprint)
    # Preserve the journal's normal current-step guard while representing a
    # legacy/corrupt snapshot with two unresolved managed rows. A real run can
    # only have one current step, so the duplicate pending row is seeded after
    # clearing that pointer.
    workflow_journal.update_run_current_step(run_id, None)
    workflow_journal.mark_work_pending(
        run_id=run_id,
        step_id="step-b",
        generation="1",
        step_attempt=1,
        tier="script",
        updated_at=started_at,
        call_fingerprint=fingerprint,
    )
    resolved = []

    class Origins:
        def resolve_step_admitter(self, *_args):
            resolved.append(_args)
            raise AssertionError("an ambiguous recovery set must fail before admission")

    class Projector:
        def project_pending_for_run(self, _run_id):
            return None

    resumed = AsyncMock()
    monkeypatch.setattr(main.app.state, "work_workflow_origins", Origins(), raising=False)
    monkeypatch.setattr(main.app.state, "workflow_step_projector", Projector(), raising=False)
    monkeypatch.setattr(script_runner, "resume_script_run", resumed)

    response = client.post(f"/workflows/runs/{run_id}/resume")

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "script run has multiple unresolved managed Work steps"
    assert resolved == []
    assert [step.state for step in workflow_journal.get_steps(run_id)] == [
        "work_pending",
        "work_pending",
    ]
    resumed.assert_not_awaited()


def test_script_resume_refuses_a_pending_step_that_disappears_after_projection_scan(
    client, monkeypatch
):
    run_id = f"managed-resume-stale-step-{uuid.uuid4().hex}"
    source = "def main():\n    return 'frozen'\n"
    _seed_pending_script(run_id, json.dumps({"source": source, "path": "frozen.py"}))
    original_get_steps = workflow_journal.get_steps
    assert original_get_steps(run_id)[0].state == "work_pending"
    monkeypatch.setattr(workflow_journal, "get_step", lambda *_args: None)
    resolved = []

    class Origins:
        def resolve_step_admitter(self, *_args):
            resolved.append(_args)
            raise AssertionError("a missing durable step has no recovery authority")

    class Projector:
        def project_pending_for_run(self, _run_id):
            return None

    resumed = AsyncMock()
    monkeypatch.setattr(main.app.state, "work_workflow_origins", Origins(), raising=False)
    monkeypatch.setattr(main.app.state, "workflow_step_projector", Projector(), raising=False)
    monkeypatch.setattr(script_runner, "resume_script_run", resumed)

    response = client.post(f"/workflows/runs/{run_id}/resume")

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "managed script step identity is unavailable"
    assert resolved == []
    assert workflow_journal.get_steps(run_id)[0].state == "work_pending"
    resumed.assert_not_awaited()


def test_script_resume_rejects_a_non_string_source_in_the_frozen_snapshot(client, monkeypatch):
    run_id = f"managed-resume-corrupt-source-{uuid.uuid4().hex}"
    _seed_script_run(run_id, json.dumps({"source": None, "path": "frozen.py"}))
    resumed = AsyncMock()
    monkeypatch.setattr(script_runner, "resume_script_run", resumed)

    response = client.post(f"/workflows/runs/{run_id}/resume")

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == f"run '{run_id}' snapshot is corrupt"
    assert workflow_journal.get_run(run_id).state == "failed"
    resumed.assert_not_awaited()


def _public_script_manifest() -> str:
    from cli_agent_orchestrator.services import execution_manifest, plan_identifier

    components = plan_identifier.PlanV2Components(
        tier="script",
        artifact_hash=plan_identifier.digest_bytes(b"frozen source"),
        declaration=plan_identifier.digest_bytes(b"declaration"),
        targets=plan_identifier.digest_bytes(b"targets"),
        inputs=plan_identifier.digest_inputs({}),
        limits=plan_identifier.digest_bytes(b"limits"),
        retry_policy=plan_identifier.digest_bytes(b"retry policy"),
        policy=plan_identifier.digest_bytes(b"policy"),
        memory=plan_identifier.digest_bytes(b"memory"),
    )
    return execution_manifest.serialise_v2_public(execution_manifest.build_v2_public(components))


def test_scoped_script_resume_refuses_a_run_owned_by_another_principal_before_projection(
    client, monkeypatch
):
    run_id = f"managed-resume-owner-refused-{uuid.uuid4().hex}"
    source = "def main():\n    return 'frozen'\n"
    _seed_pending_script(
        run_id,
        json.dumps({"source": source, "path": "frozen.py"}),
        manifest_json=_public_script_manifest(),
    )
    owner_calls = []

    class Owner:
        def validate_resume(self, principal, candidate_run_id):
            owner_calls.append((principal, candidate_run_id))
            from cli_agent_orchestrator.services.work_workflow_plans import WorkflowPlanRefused

            raise WorkflowPlanRefused("run_plan_owner_mismatch", 403)

    class Origins:
        plans = Owner()

    projector_calls = []

    class Projector:
        def project_pending_for_run(self, projected_run_id):
            projector_calls.append(projected_run_id)

    resumed = AsyncMock()
    monkeypatch.setattr(main.app.state, "work_workflow_origins", Origins(), raising=False)
    monkeypatch.setattr(main.app.state, "workflow_step_projector", Projector(), raising=False)
    monkeypatch.setattr(script_runner, "resume_script_run", resumed)

    response = client.post(f"/workflows/runs/{run_id}/resume")

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == {
        "kind": "run_plan_owner_mismatch",
        "retryable": False,
    }
    assert len(owner_calls) == 1
    assert owner_calls[0][1] == run_id
    assert projector_calls == []
    assert workflow_journal.get_steps(run_id)[0].state == "work_pending"
    resumed.assert_not_awaited()


@pytest.mark.parametrize(
    ("failure", "status_code", "detail"),
    [
        ("permission", 403, "managed workflow authority is unavailable"),
        ("stale", 409, "managed workflow provision changed"),
        ("unavailable", 503, "managed workflow selection failed"),
        ("invalid-selection", 503, "invalid managed workflow selection"),
        ("missing-issuer", 503, "managed workflow run capability is unavailable"),
    ],
)
def test_script_resume_fails_closed_when_run_capability_cannot_be_selected(
    client, monkeypatch, failure, status_code, detail
):
    run_id = f"managed-capability-refused-{uuid.uuid4().hex}"
    source = "def main():\n    return 'frozen'\n"
    _seed_script_run(run_id, json.dumps({"source": source, "path": "frozen.py"}))
    resumed = AsyncMock()

    class Origins:
        def requires_run_capability(self, *_args):
            if failure == "permission":
                raise PermissionError("private provision detail")
            if failure == "stale":
                raise LookupError("private stale detail")
            if failure == "unavailable":
                raise RuntimeError("private selector diagnostic")
            if failure == "invalid-selection":
                return None
            return True

        def create_run_capability(self, _principal, **_kwargs):
            if failure == "missing-issuer":
                raise AssertionError("a missing issuer must fail before invocation")
            return "unreachable"

    origins = Origins()
    if failure == "missing-issuer":
        monkeypatch.setattr(origins, "create_run_capability", None, raising=False)

    monkeypatch.setattr(main.app.state, "work_workflow_origins", origins, raising=False)
    monkeypatch.setattr(script_runner, "resume_script_run", resumed)

    response = client.post(f"/workflows/runs/{run_id}/resume")

    assert response.status_code == status_code, response.text
    assert response.json()["detail"] == detail
    assert "private" not in response.text
    assert workflow_journal.get_run(run_id).generation == "1"
    resumed.assert_not_awaited()


def test_legacy_resume_does_not_receive_a_run_capability_factory(client, monkeypatch):
    run_id = f"legacy-resume-without-capability-{uuid.uuid4().hex}"
    source = "def main():\n    return 'frozen'\n"
    _seed_script_run(run_id, json.dumps({"source": source, "path": "frozen.py"}))

    class Origins:
        def requires_run_capability(self, *_args):
            return False

        def create_run_capability(self, *_args, **_kwargs):
            raise AssertionError("legacy source must not issue a managed credential")

    resumed = AsyncMock(
        return_value=WorkflowRunResult(
            run_id=run_id,
            workflow_name="managed-script",
            state=RunState.COMPLETED,
            started_at="2026-10-14T00:00:00Z",
        )
    )
    monkeypatch.setattr(main.app.state, "work_workflow_origins", Origins(), raising=False)
    monkeypatch.setattr(script_runner, "resume_script_run", resumed)

    response = client.post(f"/workflows/runs/{run_id}/resume")

    assert response.status_code == 200, response.text
    assert "run_credential_factory" not in resumed.await_args.kwargs


def test_actual_resume_refuses_a_malformed_run_credential_before_process_spawn(client, monkeypatch):
    run_id = f"badcred-{uuid.uuid4().hex}"
    source = "def main():\n    return 'frozen'\n"
    _seed_script_run(run_id, json.dumps({"source": source, "path": "frozen.py"}))

    class Origins:
        def requires_run_capability(self, *_args):
            return True

        def create_run_capability(self, _principal, **_kwargs):
            return object()

    process = AsyncMock()
    monkeypatch.setattr(main.app.state, "work_workflow_origins", Origins(), raising=False)
    monkeypatch.setattr(script_runner, "_drive_process", process)

    response = client.post(f"/workflows/runs/{run_id}/resume")

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "managed script run credential could not be issued"
    row = workflow_journal.get_run(run_id)
    assert row.state == "failed"
    assert row.generation == "2"
    assert run_id not in script_runner.run_registry
    process.assert_not_awaited()


@pytest.mark.parametrize(
    ("mode", "status_code", "detail"),
    [
        (
            "projection-failure",
            409,
            {
                "message": "managed Work result could not be verified for resume",
                "kind": "work_pending",
            },
        ),
        ("missing-projector", 503, "managed workflow result projector is unavailable"),
        ("corrupt-snapshot", 422, None),
        (
            "no-recovery-admitter",
            409,
            "managed Work step has no active provision-bound recovery adapter",
        ),
    ],
)
def test_pending_script_resume_fails_closed_when_projection_or_authority_is_unavailable(
    client, monkeypatch, mode, status_code, detail
):
    run_id = f"managed-resume-refused-{uuid.uuid4().hex}"
    source = "def main():\n    return 'frozen'\n"
    snapshot = (
        "not-json"
        if mode == "corrupt-snapshot"
        else json.dumps({"source": source, "path": "frozen.py"})
    )
    _seed_pending_script(run_id, snapshot)
    resumed = AsyncMock()
    monkeypatch.setattr(script_runner, "resume_script_run", resumed)

    if mode == "projection-failure":

        class Projector:
            def project_pending_for_run(self, _run_id):
                raise RuntimeError("private projection detail")

        monkeypatch.setattr(main.app.state, "workflow_step_projector", Projector(), raising=False)
    elif mode in {"corrupt-snapshot", "no-recovery-admitter"}:

        class Projector:
            def project_pending_for_run(self, _run_id):
                return None

        monkeypatch.setattr(main.app.state, "workflow_step_projector", Projector(), raising=False)
        if mode == "no-recovery-admitter":

            class Origins:
                def resolve_step_admitter(self, *_args):
                    return None

            monkeypatch.setattr(main.app.state, "work_workflow_origins", Origins(), raising=False)

    response = client.post(f"/workflows/runs/{run_id}/resume")

    assert response.status_code == status_code, response.text
    if detail is None:
        assert response.json()["detail"] == f"run '{run_id}' snapshot is corrupt"
    else:
        assert response.json()["detail"] == detail
    if mode == "projection-failure":
        assert "private projection detail" not in response.text
    assert workflow_journal.get_steps(run_id)[0].state == "work_pending"
    resumed.assert_not_awaited()


def test_pending_script_resume_fails_closed_when_projector_has_no_callable_method(
    client, monkeypatch
):
    run_id = f"managed-resume-invalid-projector-{uuid.uuid4().hex}"
    source = "def main():\n    return 'frozen'\n"
    _seed_pending_script(run_id, json.dumps({"source": source, "path": "frozen.py"}))
    resumed = AsyncMock()
    monkeypatch.setattr(main.app.state, "workflow_step_projector", object(), raising=False)
    monkeypatch.setattr(script_runner, "resume_script_run", resumed)

    response = client.post(f"/workflows/runs/{run_id}/resume")

    assert response.status_code == 503, response.text
    assert response.json()["detail"] == "managed workflow result projector is unavailable"
    assert workflow_journal.get_steps(run_id)[0].state == "work_pending"
    resumed.assert_not_awaited()


def test_pending_script_resume_hides_a_workflow_journal_read_failure(client, monkeypatch):
    run_id = f"managed-resume-journal-unavailable-{uuid.uuid4().hex}"
    source = "def main():\n    return 'frozen'\n"
    _seed_pending_script(run_id, json.dumps({"source": source, "path": "frozen.py"}))
    original_get_steps = workflow_journal.get_steps

    def fail_journal_read(_run_id):
        raise RuntimeError("private journal diagnostic")

    monkeypatch.setattr(workflow_journal, "get_steps", fail_journal_read)
    resumed = AsyncMock()
    monkeypatch.setattr(script_runner, "resume_script_run", resumed)

    response = client.post(f"/workflows/runs/{run_id}/resume")

    assert response.status_code == 503, response.text
    assert response.json()["detail"] == "workflow journal is unavailable"
    assert "private journal diagnostic" not in response.text
    assert original_get_steps(run_id)[0].state == "work_pending"
    resumed.assert_not_awaited()


@pytest.mark.parametrize(
    ("failure", "status_code", "detail"),
    [
        ("authority", 403, "managed workflow authority is unavailable"),
        ("stale", 409, "managed workflow source is stale or unavailable"),
        ("invalid-capability", 503, "managed workflow runtime returned an invalid step capability"),
        ("unavailable", 503, "managed workflow runtime is unavailable"),
    ],
)
def test_managed_yaml_submit_rejects_bad_step_authority_before_journaling(
    client, monkeypatch, failure, status_code, detail
):
    run_id = f"managed-yaml-refused-{uuid.uuid4().hex}"
    spec = _yaml_spec(f"workflow-{uuid.uuid4().hex}")
    _use_spec(monkeypatch, spec)
    scheduled = []
    monkeypatch.setattr(
        main, "_schedule_background_drive", lambda *args, **kwargs: scheduled.append((args, kwargs))
    )

    class Origins:
        def resolve_step_admitter(self, *_args):
            if failure == "authority":
                raise PermissionError("private authority detail")
            if failure == "stale":
                raise LookupError("private stale detail")
            if failure == "unavailable":
                raise RuntimeError("private resolver diagnostic")
            return object()

    monkeypatch.setattr(main.app.state, "work_workflow_origins", Origins(), raising=False)

    response = client.post(
        "/workflows/runs:submit",
        json={"name_or_path": spec.name, "run_id": run_id, "inputs": {}},
    )

    assert response.status_code == status_code, response.text
    assert response.json()["detail"] == detail
    assert "private" not in response.text
    assert workflow_journal.get_run(run_id) is None
    assert scheduled == []


@pytest.mark.parametrize("has_decisions", [False, True])
def test_coordinator_owned_resume_uses_its_service_and_refuses_legacy_decisions(
    client, monkeypatch, has_decisions
):
    run_id = f"coordinator-resume-{uuid.uuid4().hex}"
    resume_calls = []

    class Connection:
        def execute(self, query, params):
            assert query == "SELECT 1 FROM workflow_driver WHERE run_id=%s"
            assert params == (run_id,)
            return self

        def fetchone(self):
            return (1,)

    class Repository:
        @contextmanager
        def read_snapshot(self):
            yield Connection()

    class Coordinator:
        repository = Repository()

        async def resume(self, principal, candidate_run_id):
            resume_calls.append((principal, candidate_run_id))
            return {"run_id": candidate_run_id, "state": "completed"}

    monkeypatch.setattr(main.app.state, "workflow_coordinator", Coordinator(), raising=False)
    url = f"/workflows/runs/{run_id}/resume"
    if has_decisions:
        response = client.post(url, json={"decisions": {"step-a": "skip"}})
    else:
        response = client.post(url)

    if has_decisions:
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == {"kind": "coordinator_decisions_refused"}
        assert resume_calls == []
    else:
        assert response.status_code == 200, response.text
        assert response.json() == {"run_id": run_id, "state": "completed"}
        assert len(resume_calls) == 1
        assert resume_calls[0][1] == run_id


@pytest.mark.parametrize(
    ("failure", "status_code", "kind"),
    [
        ("owner", 403, "run belongs to another principal"),
        ("missing", 404, "run was deleted"),
        ("changed", 409, "coordinator state changed"),
    ],
)
def test_coordinator_owned_resume_maps_owner_and_stale_run_errors(
    client, monkeypatch, failure, status_code, kind
):
    run_id = f"coordinator-resume-refused-{uuid.uuid4().hex}"

    class Connection:
        def execute(self, _query, _params):
            return self

        def fetchone(self):
            return (1,)

    class Repository:
        @contextmanager
        def read_snapshot(self):
            yield Connection()

    class Coordinator:
        repository = Repository()

        async def resume(self, _principal, _candidate_run_id):
            if failure == "owner":
                raise PermissionError(kind)
            if failure == "missing":
                raise LookupError(kind)
            raise ValueError(kind)

    monkeypatch.setattr(main.app.state, "workflow_coordinator", Coordinator(), raising=False)

    response = client.post(f"/workflows/runs/{run_id}/resume")

    assert response.status_code == status_code, response.text
    assert response.json()["detail"] == {"kind": kind, "retryable": False}
