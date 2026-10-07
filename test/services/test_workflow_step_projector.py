from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.models.work_origin import WorkflowStepResultV1
from cli_agent_orchestrator.services import workflow_journal
from cli_agent_orchestrator.services.work_origin import OriginDenied
from cli_agent_orchestrator.services.work_service import DeliveryUncertain
from cli_agent_orchestrator.services.workflow_step_projector import (
    WorkflowProjectionError,
    WorkflowStepProjector,
)


def _identity(**overrides):
    values = {
        "tier": "yaml",
        "run_id": "run-1",
        "run_generation": 4,
        "step_id": "build",
        "workflow_step_attempt": 2,
        "binding_id": "binding-1",
        "provision_fingerprint": "provision-hash",
        "work_item_id": "work-1",
        "work_attempt_id": "attempt-1",
        "work_generation": 3,
        "delivery_id": "delivery-1",
        "delivery_hash": "delivery-hash",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _accepted(**overrides):
    envelope = WorkflowStepResultV1.from_payload(
        {"schema_version": 1, "status": "completed", "output": {"answer": 42}}
    )
    identity = _identity()
    values = {
        **vars(identity),
        "binding_fingerprint": identity.provision_fingerprint,
        "accepted_result_id": "result-1",
        "content_hash": hashlib.sha256(envelope.canonical_bytes()).hexdigest(),
        "byte_length": len(envelope.canonical_bytes()),
        "canonical_bytes": envelope.canonical_bytes(),
        "result": envelope,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _work_state(
    binding,
    *,
    work_state="running",
    attempt_state="running",
    current_attempt_id=None,
    current_generation=None,
    current_attempt_state=None,
    accepted_result_id=None,
):
    return SimpleNamespace(
        binding_id=binding.binding_id,
        work_item_id=binding.work_item_id,
        work_attempt_id=binding.work_attempt_id,
        work_generation=binding.work_generation,
        work_state=work_state,
        work_revision=4,
        attempt_state=attempt_state,
        attempt_revision=5,
        current_attempt_id=current_attempt_id or binding.work_attempt_id,
        current_generation=current_generation or binding.work_generation,
        current_attempt_state=current_attempt_state or attempt_state,
        accepted_result_id=accepted_result_id,
        cleanup_state="not_requested",
    )


def _projector(tmp_path, *, binding, accepted, work_state=None):
    repository = SimpleNamespace(path=tmp_path / "work.sqlite")

    class Origins:
        def __init__(self):
            self.repository = repository
            self.reads = []

        def read_step_binding(self, **identity):
            self.reads.append(identity)
            return binding

    class Service:
        def __init__(self):
            self.repository = repository
            self.reads = []
            self.state_reads = []
            if work_state is None and binding is not None:
                if accepted is None:
                    self.work_state = _work_state(binding)
                else:
                    self.work_state = _work_state(
                        binding,
                        work_state="succeeded",
                        attempt_state="finished",
                        current_attempt_state="finished",
                        accepted_result_id=accepted.accepted_result_id,
                    )
            else:
                self.work_state = work_state

        def read_workflow_step_state(self, exact_binding):
            self.state_reads.append(exact_binding)
            return self.work_state

        def read_accepted_workflow_result(self, exact_binding):
            self.reads.append(exact_binding)
            return accepted

    origins = Origins()
    service = Service()
    return WorkflowStepProjector(repository, origins, service), origins, service


def _journal_rows(monkeypatch, *, state="work_pending", attempts=2):
    run = SimpleNamespace(
        run_id="run-1",
        state="running",
        tier="yaml",
        generation="4",
        current_step_id="build",
    )
    step = SimpleNamespace(
        run_id="run-1",
        step_id="build",
        state=state,
        attempts=attempts,
        updated_at="2026-09-29T10:00:00Z",
    )
    monkeypatch.setattr(workflow_journal, "get_run", lambda _run_id: run)
    monkeypatch.setattr(workflow_journal, "get_steps", lambda _run_id: [step])


def test_projector_reads_exact_durable_binding_and_projects_accepted_result(tmp_path, monkeypatch):
    _journal_rows(monkeypatch)
    binding = _identity()
    accepted = _accepted()
    projector, origins, service = _projector(tmp_path, binding=binding, accepted=accepted)
    projected = []
    monkeypatch.setattr(
        workflow_journal,
        "project_work_result",
        lambda **kwargs: projected.append(kwargs) or "projected",
        raising=False,
    )

    outcomes = projector.project_pending_for_run("run-1")

    assert origins.reads == [
        {
            "tier": "yaml",
            "run_id": "run-1",
            "run_generation": 4,
            "step_id": "build",
            "workflow_step_attempt": 2,
            "historical": True,
        }
    ]
    assert service.reads == [binding]
    assert service.state_reads == [binding]
    assert len(projected) == 1
    assert projected[0]["binding"] is binding
    assert projected[0]["accepted_result"] is accepted
    assert projected[0]["run_id"] == "run-1"
    assert outcomes[0].status == "projected"
    assert outcomes[0].step_id == "build"


def test_projector_leaves_missing_binding_pending_without_reading_work_result(
    tmp_path, monkeypatch
):
    _journal_rows(monkeypatch)
    projector, origins, service = _projector(tmp_path, binding=None, accepted=None)

    outcomes = projector.project_pending_for_run("run-1")

    assert len(origins.reads) == 1
    assert service.reads == []
    assert service.state_reads == []
    assert outcomes[0].status == "binding_missing"
    assert outcomes[0].step_id == "build"


def test_projector_keeps_step_pending_when_work_has_no_accepted_result(tmp_path, monkeypatch):
    _journal_rows(monkeypatch)
    projector, _origins, service = _projector(tmp_path, binding=_identity(), accepted=None)

    outcomes = projector.project_pending_for_run("run-1")

    assert len(service.reads) == 1
    assert outcomes[0].status == "result_pending"


def test_projector_rejects_result_proof_for_another_binding(tmp_path, monkeypatch):
    _journal_rows(monkeypatch)
    projector, _origins, _service = _projector(
        tmp_path,
        binding=_identity(),
        accepted=_accepted(work_attempt_id="different-attempt"),
    )
    writes = []
    monkeypatch.setattr(
        workflow_journal,
        "project_work_result",
        lambda **kwargs: writes.append(kwargs),
        raising=False,
    )

    with pytest.raises(WorkflowProjectionError, match="binding"):
        projector.project_pending_for_run("run-1")

    assert writes == []


def test_projector_ignores_non_pending_steps(tmp_path, monkeypatch):
    _journal_rows(monkeypatch, state="completed")
    projector, origins, service = _projector(tmp_path, binding=None, accepted=None)

    outcomes = projector.project_pending_for_run("run-1")

    assert outcomes == ()
    assert origins.reads == []
    assert service.reads == []


def test_projector_rejects_dependencies_from_different_work_stores(tmp_path):
    repository = SimpleNamespace(path=tmp_path / "work.sqlite")
    origins = SimpleNamespace(
        repository=SimpleNamespace(path=tmp_path / "other.sqlite"),
        read_step_binding=lambda **_identity: None,
    )
    service = SimpleNamespace(
        repository=repository,
        read_accepted_workflow_result=lambda _binding: None,
    )

    with pytest.raises(ValueError, match="same Work repository"):
        WorkflowStepProjector(repository, origins, service)


def test_projector_scans_pending_runs_at_startup(tmp_path, monkeypatch):
    projector, _origins, _service = _projector(tmp_path, binding=None, accepted=None)
    runs = []
    monkeypatch.setattr(workflow_journal, "list_work_pending_run_ids", lambda: ["run-a", "run-b"])
    monkeypatch.setattr(
        projector,
        "project_pending_for_run",
        lambda run_id: runs.append(run_id) or (),
    )

    assert projector.project_pending_at_startup() == ()
    assert runs == ["run-a", "run-b"]


def test_startup_projection_keeps_serving_when_one_run_authority_is_revoked(
    tmp_path, monkeypatch, caplog
):
    projector, _origins, _service = _projector(tmp_path, binding=None, accepted=None)
    runs = []
    monkeypatch.setattr(
        workflow_journal,
        "list_work_pending_run_ids",
        lambda: ["run-revoked", "run-healthy"],
    )

    def project_run(run_id):
        runs.append(run_id)
        if run_id == "run-revoked":
            raise OriginDenied("workflow receiver authorization was revoked")
        return ()

    monkeypatch.setattr(projector, "project_pending_for_run", project_run)

    assert projector.project_pending_at_startup() == ()
    assert runs == ["run-revoked", "run-healthy"]
    assert "run-revoked" in caplog.text


@pytest.mark.parametrize(
    "failure",
    [
        DeliveryUncertain("work-1"),
        OriginDenied("workflow receiver authorization was revoked"),
    ],
    ids=["uncertain-work", "revoked-workflow-authority"],
)
def test_projector_does_not_project_uncertain_or_revoked_work(tmp_path, monkeypatch, failure):
    _journal_rows(monkeypatch)
    projector, _origins, service = _projector(tmp_path, binding=_identity(), accepted=None)

    def reject(_binding):
        raise failure

    service.read_accepted_workflow_result = reject
    monkeypatch.setattr(
        workflow_journal,
        "project_work_result",
        lambda **_kwargs: pytest.fail("untrusted Work state must not reach journal CAS"),
    )

    with pytest.raises(type(failure)):
        projector.project_pending_for_run("run-1")


def test_projector_projects_only_an_exact_durable_work_failure(tmp_path, monkeypatch):
    _journal_rows(monkeypatch)
    binding = _identity()
    work_state = _work_state(
        binding,
        work_state="failed",
        attempt_state="failed",
        current_attempt_state="failed",
    )
    projector, origins, service = _projector(
        tmp_path, binding=binding, accepted=None, work_state=work_state
    )
    projected = []
    monkeypatch.setattr(
        workflow_journal,
        "project_work_failure",
        lambda **kwargs: projected.append(kwargs) or "failed",
        raising=False,
    )

    outcomes = projector.project_pending_for_run("run-1")

    assert origins.reads[0]["workflow_step_attempt"] == 2
    assert service.state_reads == [binding]
    assert service.reads == []
    assert outcomes[0].status == "failed"
    assert projected[0]["binding"] is binding
    assert projected[0]["work_state"] is work_state


def test_projector_leaves_reconcile_distinct_and_does_not_read_result_or_project(
    tmp_path, monkeypatch
):
    _journal_rows(monkeypatch)
    binding = _identity()
    work_state = _work_state(
        binding,
        work_state="reconcile",
        attempt_state="reconcile",
        current_attempt_state="reconcile",
    )
    projector, _origins, service = _projector(
        tmp_path, binding=binding, accepted=None, work_state=work_state
    )
    monkeypatch.setattr(
        workflow_journal,
        "project_work_failure",
        lambda **_kwargs: pytest.fail("reconcile is not a durable failed result"),
        raising=False,
    )
    monkeypatch.setattr(
        workflow_journal,
        "project_work_result",
        lambda **_kwargs: pytest.fail("reconcile is not a durable success"),
        raising=False,
    )

    outcomes = projector.project_pending_for_run("run-1")

    assert outcomes[0].status == "work_reconcile"
    assert service.state_reads == [binding]
    assert service.reads == []


def test_projector_leaves_cancellation_distinct_and_does_not_project(tmp_path, monkeypatch):
    _journal_rows(monkeypatch)
    binding = _identity()
    work_state = _work_state(
        binding,
        work_state="cancelled",
        attempt_state="cancelled",
        current_attempt_state="cancelled",
    )
    projector, _origins, service = _projector(
        tmp_path, binding=binding, accepted=None, work_state=work_state
    )
    monkeypatch.setattr(
        workflow_journal,
        "project_work_failure",
        lambda **_kwargs: pytest.fail("cancellation is not a failed result"),
        raising=False,
    )
    monkeypatch.setattr(
        workflow_journal,
        "project_work_result",
        lambda **_kwargs: pytest.fail("cancellation is not a successful result"),
        raising=False,
    )

    outcomes = projector.project_pending_for_run("run-1")

    assert outcomes[0].status == "work_cancelled"
    assert service.state_reads == [binding]
    assert service.reads == []


def test_projector_blocks_stale_failed_binding_without_failure_projection(tmp_path, monkeypatch):
    _journal_rows(monkeypatch)
    binding = _identity()
    work_state = _work_state(
        binding,
        work_state="failed",
        attempt_state="failed",
        current_attempt_id="attempt-2",
        current_generation=4,
        current_attempt_state="running",
    )
    projector, _origins, service = _projector(
        tmp_path, binding=binding, accepted=None, work_state=work_state
    )
    monkeypatch.setattr(
        workflow_journal,
        "project_work_failure",
        lambda **_kwargs: pytest.fail("stale binding cannot project failure"),
        raising=False,
    )

    outcomes = projector.project_pending_for_run("run-1")

    assert outcomes[0].status == "work_stale"
    assert service.reads == []


def test_projector_requires_success_state_to_match_accepted_result_reference(tmp_path, monkeypatch):
    _journal_rows(monkeypatch)
    binding = _identity()
    accepted = _accepted()
    projector, _origins, _service = _projector(
        tmp_path,
        binding=binding,
        accepted=accepted,
        work_state=_work_state(
            binding,
            work_state="succeeded",
            attempt_state="finished",
            current_attempt_state="finished",
            accepted_result_id="different-result",
        ),
    )
    monkeypatch.setattr(
        workflow_journal,
        "project_work_result",
        lambda **_kwargs: pytest.fail("mismatched result reference cannot project"),
        raising=False,
    )

    with pytest.raises(WorkflowProjectionError, match="accepted result reference"):
        projector.project_pending_for_run("run-1")


def test_projector_does_not_turn_stale_success_into_projection(tmp_path, monkeypatch):
    _journal_rows(monkeypatch)
    binding = _identity()
    accepted = _accepted()
    projector, _origins, service = _projector(
        tmp_path,
        binding=binding,
        accepted=accepted,
        work_state=_work_state(
            binding,
            work_state="succeeded",
            attempt_state="finished",
            current_attempt_id="attempt-2",
            current_generation=4,
            current_attempt_state="running",
            accepted_result_id=accepted.accepted_result_id,
        ),
    )
    monkeypatch.setattr(
        workflow_journal,
        "project_work_result",
        lambda **_kwargs: pytest.fail("stale binding cannot project success"),
        raising=False,
    )

    outcomes = projector.project_pending_for_run("run-1")

    assert outcomes[0].status == "work_stale"
    assert service.reads == []
