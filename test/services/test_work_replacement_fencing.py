"""Replacement fencing through the durable scheduler/work seams."""

import sqlite3

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
from cli_agent_orchestrator.services.work_scheduler import (
    SchedulerConflict,
    StoppedWriter,
    WorkScheduler,
)


def _reconciled_held_work(tmp_path, *, budget=2):
    repository = WorkRepository(tmp_path / "replacement.sqlite3")
    repository.initialize()
    scheduler = WorkScheduler(repository)
    scheduler.configure(capacity=1, max_queue=2, aging_seconds=10, expected_policy_revision=0)
    job = repository.create_job(
        project_id="project",
        principal_id="owner",
        allowed_providers=["mock_cli"],
        grant_id="grant",
        budget={"scheduler_units": budget},
    )
    work = repository.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key="replacement",
        request_hash="a" * 64,
        contract_id="contract",
        snapshot_id=None,
        provider="mock_cli",
        actor_id="owner",
        lease_seconds=60,
    )
    attempt = work["attempts"][-1]
    queued = scheduler.enqueue(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_attempt_revision=attempt["revision"],
        units=1,
        actor_id="owner",
    )
    held = scheduler.claim_next(actor_id="owner")
    sent = repository.transition_attempt(
        attempt_id=attempt["id"],
        generation=1,
        expected_revision=1,
        expected_state="planned",
        target="sent",
        actor_id="owner",
        event_id="replacement-sent",
        evidence=TransitionEvidence(
            generation=1,
            expected_generation=1,
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
    )
    reconcile = repository.transition_attempt(
        attempt_id=attempt["id"],
        generation=1,
        expected_revision=sent["attempts"][-1]["revision"],
        expected_state="sent",
        target="reconcile",
        actor_id="owner",
        event_id="replacement-reconcile",
        evidence=TransitionEvidence(generation=1, expected_generation=1),
    )
    assert queued.id == held.id
    return repository, reconcile, held


def _replacement_evidence():
    return TransitionEvidence(
        generation=1,
        expected_generation=1,
        reconciliation_authorized=True,
    )


def _retry(service, reconcile, scheduler):
    return service.retry_work(
        reconcile["id"],
        scheduler=scheduler,
        expected_revision=reconcile["revision"],
        provider="mock_cli",
        evidence=_replacement_evidence(),
        actor_id="owner",
        lease_seconds=60,
    )


def test_exact_stop_proof_releases_once_then_creates_one_generation(tmp_path):
    """Removing the release/retry fencing must leave generation one durable."""
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, reconcile, held = _reconciled_held_work(tmp_path)
    prior = reconcile["attempts"][-1]
    scheduler = WorkScheduler(
        repository,
        stop_verifier=lambda owner: StoppedWriter(
            owner.attempt_id, owner.generation, owner.revision, "irreversible-exit"
        ),
    )
    replacement = _retry(WorkService(repository), reconcile, scheduler)

    assert replacement["state"] == "queued"
    assert replacement["attempts"][-1]["generation"] == 2
    assert replacement["attempts"][-1]["state"] == "planned"
    assert scheduler.get_by_attempt(prior["id"]).state == "released"
    with pytest.raises(SchedulerConflict):
        scheduler.get_by_attempt(replacement["attempts"][-1]["id"])
    events = repository.read_events(reconcile["job_id"])["events"]
    assert [event["event_type"] for event in events[-2:]] == [
        "scheduler.released",
        "attempt.replanned",
    ]

    with pytest.raises(WorkConflict):
        _retry(WorkService(repository), reconcile, scheduler)
    assert repository.get_work(reconcile["id"])["attempts"] == replacement["attempts"]
    assert repository.read_events(reconcile["job_id"])["events"] == events


@pytest.mark.parametrize("proof", [None, False, "wrong proof"])
def test_quota_reconcile_without_stop_proof_keeps_generation_and_slot(tmp_path, proof):
    """Replacing ``StoppedWriter`` with quota/reconcile state must not free capacity."""
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, reconcile, held = _reconciled_held_work(tmp_path)
    waiting = repository.admit_work(
        job_id=reconcile["job_id"],
        operation_kind="launch",
        idempotency_key="waiting",
        request_hash="b" * 64,
        contract_id="contract",
        snapshot_id=None,
        provider="mock_cli",
        actor_id="owner",
        lease_seconds=60,
    )
    waiting_attempt = waiting["attempts"][-1]
    scheduler = WorkScheduler(repository, stop_verifier=lambda owner: proof)
    scheduler.enqueue(
        attempt_id=waiting_attempt["id"],
        generation=1,
        expected_attempt_revision=1,
        units=1,
        actor_id="owner",
    )
    before = repository.read_events(reconcile["job_id"])["events"]

    with pytest.raises(SchedulerConflict):
        _retry(WorkService(repository), reconcile, scheduler)

    current = repository.get_work(reconcile["id"])
    assert current["attempts"][-1]["generation"] == 1
    assert scheduler.get_by_attempt(current["attempts"][-1]["id"]).state == "held"
    assert scheduler.claim_next(actor_id="owner") is None
    assert repository.read_events(reconcile["job_id"])["events"] == before


def test_stop_verifier_runs_without_sqlite_transaction(tmp_path):
    """Holding SQLite while asking the external verifier would deadlock this probe."""
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, reconcile, held = _reconciled_held_work(tmp_path)
    observed = []

    def verify(owner):
        with sqlite3.connect(repository.path, timeout=0.1, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        observed.append(owner.attempt_id)
        return StoppedWriter(owner.attempt_id, owner.generation, owner.revision, "exit")

    replacement = _retry(WorkService(repository), reconcile, WorkScheduler(repository, stop_verifier=verify))

    assert observed == [held.attempt_id]
    assert replacement["attempts"][-1]["generation"] == 2


def test_scheduler_release_for_replacement_persists_exact_stop_evidence(tmp_path):
    """The scheduler's reconcile-only release is the durable cessation boundary."""
    repository, reconcile, held = _reconciled_held_work(tmp_path)
    scheduler = WorkScheduler(
        repository,
        stop_verifier=lambda owner: StoppedWriter(
            owner.attempt_id, owner.generation, owner.revision, "scheduler-stop-proof"
        ),
    )

    released = scheduler.release_for_replacement(
        held.id,
        generation=1,
        expected_revision=held.revision,
        expected_attempt_revision=reconcile["attempts"][-1]["revision"],
        expected_work_revision=reconcile["revision"],
        actor_id="owner",
    )

    assert released["state"] == "reconcile"
    assert released["revision"] == reconcile["revision"] + 1
    with repository.connection() as connection:
        row = connection.execute(
            "SELECT state,stop_evidence_ref FROM work_scheduler_requests WHERE id=?", (held.id,)
        ).fetchone()
    assert tuple(row) == ("released", "scheduler-stop-proof")


def test_revision_changed_during_verifier_keeps_slot_and_generation(tmp_path):
    """A work revision fence is distinct from the attempt revision fence."""
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, reconcile, held = _reconciled_held_work(tmp_path)

    def verify(owner):
        with repository.transaction() as connection:
            connection.execute("UPDATE work_items SET revision=revision+1 WHERE id=?", (reconcile["id"],))
        return StoppedWriter(owner.attempt_id, owner.generation, owner.revision, "exit")

    scheduler = WorkScheduler(repository, stop_verifier=verify)
    with pytest.raises(SchedulerConflict):
        _retry(WorkService(repository), reconcile, scheduler)

    assert repository.get_work(reconcile["id"])["attempts"][-1]["generation"] == 1
    assert scheduler.get_by_attempt(held.attempt_id).state == "held"


def test_late_result_during_verifier_invalidates_work_fence(tmp_path):
    """Late result recording changes work, even when reconcile keeps attempt revision."""
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, reconcile, held = _reconciled_held_work(tmp_path)

    def verify(owner):
        repository.register_result(
            attempt_id=owner.attempt_id,
            generation=owner.generation,
            content_hash="c" * 64,
            immutable_location="c" * 64,
            byte_length=1,
            validator_id="validator",
            validation_evidence={"valid": True},
            actor_id="owner",
        )
        return StoppedWriter(owner.attempt_id, owner.generation, owner.revision, "exit")

    scheduler = WorkScheduler(repository, stop_verifier=verify)
    with pytest.raises(SchedulerConflict):
        _retry(WorkService(repository), reconcile, scheduler)

    assert repository.get_work(reconcile["id"])["accepted_result_id"] is None
    assert scheduler.get_by_attempt(held.attempt_id).state == "held"


def test_cancellation_during_verifier_wins_without_replacement(tmp_path):
    """A cancellation racing cessation verification retains its terminal winner."""
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, reconcile, held = _reconciled_held_work(tmp_path)

    def verify(owner):
        repository.transition_attempt(
            attempt_id=owner.attempt_id,
            generation=owner.generation,
            expected_revision=owner.revision,
            expected_state="reconcile",
            target="cancelled",
            actor_id="owner",
            event_id="cancel-during-stop-verification",
            evidence=TransitionEvidence(generation=1, expected_generation=1),
        )
        return StoppedWriter(owner.attempt_id, owner.generation, owner.revision, "exit")

    scheduler = WorkScheduler(repository, stop_verifier=verify)
    with pytest.raises(SchedulerConflict):
        _retry(WorkService(repository), reconcile, scheduler)

    assert repository.get_work(reconcile["id"])["state"] == "cancelled"
    assert scheduler.get_by_attempt(held.attempt_id).state == "held"


def test_result_between_release_and_retry_blocks_replacement(tmp_path, monkeypatch):
    """The retry must consume the release snapshot, never a freshly read revision."""
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, reconcile, held = _reconciled_held_work(tmp_path)
    scheduler = WorkScheduler(
        repository,
        stop_verifier=lambda owner: StoppedWriter(
            owner.attempt_id, owner.generation, owner.revision, "exit"
        ),
    )
    original_release = scheduler.release_for_replacement

    def release_then_record(*args, **kwargs):
        snapshot = original_release(*args, **kwargs)
        prior = snapshot["attempts"][-1]
        repository.register_result(
            attempt_id=prior["id"],
            generation=prior["generation"],
            content_hash="d" * 64,
            immutable_location="d" * 64,
            byte_length=1,
            validator_id="validator",
            validation_evidence={"valid": True},
            actor_id="owner",
        )
        return snapshot

    monkeypatch.setattr(scheduler, "release_for_replacement", release_then_record)
    with pytest.raises(WorkConflict):
        _retry(WorkService(repository), reconcile, scheduler)

    assert scheduler.get_by_attempt(held.attempt_id).state == "released"
    assert repository.get_work(reconcile["id"])["attempts"][-1]["generation"] == 1


def test_retry_failure_keeps_durable_stop_evidence_without_duplicate_effects(tmp_path, monkeypatch):
    """A retry failure is not compensated by reviving capacity or retrying automatically."""
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, reconcile, held = _reconciled_held_work(tmp_path)
    scheduler = WorkScheduler(
        repository,
        stop_verifier=lambda owner: StoppedWriter(
            owner.attempt_id, owner.generation, owner.revision, "exit-proof"
        ),
    )

    def fail_retry(*args, **kwargs):
        raise WorkConflict("controlled retry failure")

    monkeypatch.setattr(repository, "retry_work", fail_retry)
    with pytest.raises(WorkConflict, match="controlled"):
        _retry(WorkService(repository), reconcile, scheduler)

    with repository.connection() as connection:
        row = connection.execute(
            "SELECT state,stop_evidence_ref FROM work_scheduler_requests WHERE id=?", (held.id,)
        ).fetchone()
    assert tuple(row) == ("released", "exit-proof")
    assert repository.get_work(reconcile["id"])["attempts"][-1]["generation"] == 1


def test_replacement_rejects_mismatched_scheduler_store_without_effects(tmp_path):
    """A scheduler on another SQLite store cannot release this work's writer."""
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, reconcile, held = _reconciled_held_work(tmp_path)
    foreign_repository = WorkRepository(tmp_path / "foreign.sqlite3")
    foreign_repository.initialize()
    foreign_scheduler = WorkScheduler(foreign_repository)

    with pytest.raises(WorkConflict):
        _retry(WorkService(repository), reconcile, foreign_scheduler)

    assert repository.get_work(reconcile["id"])["attempts"][-1]["generation"] == 1
    assert WorkScheduler(repository).get_by_attempt(held.attempt_id).state == "held"


@pytest.mark.parametrize("cleanup_state", ["pending", "complete"])
def test_cleanup_state_without_stop_proof_keeps_held_generation_and_events(tmp_path, cleanup_state):
    """Cleanup accounting is not irreversible writer-cessation evidence."""
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, reconcile, held = _reconciled_held_work(tmp_path)
    prior = reconcile["attempts"][-1]
    reconciled_with_cleanup = repository.record_cleanup(
        prior["id"],
        generation=prior["generation"],
        expected_revision=prior["revision"],
        state="pending",
        actor_id="owner",
    )
    if cleanup_state == "complete":
        pending = reconciled_with_cleanup["attempts"][-1]
        reconciled_with_cleanup = repository.record_cleanup(
            pending["id"],
            generation=pending["generation"],
            expected_revision=pending["revision"],
            state="complete",
            actor_id="owner",
        )
    scheduler = WorkScheduler(repository, stop_verifier=lambda owner: None)
    before = repository.read_events(reconcile["job_id"])["events"]

    with pytest.raises(SchedulerConflict):
        _retry(WorkService(repository), reconciled_with_cleanup, scheduler)

    current = repository.get_work(reconcile["id"])
    assert current["attempts"][-1]["cleanup_state"] == cleanup_state
    assert current["attempts"][-1]["generation"] == 1
    assert scheduler.get_by_attempt(held.attempt_id).state == "held"
    assert repository.read_events(reconcile["job_id"])["events"] == before


def test_attempt_revision_changed_during_replacement_verifier_keeps_slot_and_generation(tmp_path):
    """The replacement release must recheck the exact writer revision, not just work."""
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, reconcile, held = _reconciled_held_work(tmp_path)
    before = repository.read_events(reconcile["job_id"])["events"]

    def verify(owner):
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE work_attempts SET revision=revision+1 WHERE id=?", (owner.attempt_id,)
            )
        return StoppedWriter(owner.attempt_id, owner.generation, owner.revision, "stale-proof")

    scheduler = WorkScheduler(repository, stop_verifier=verify)
    with pytest.raises(SchedulerConflict):
        _retry(WorkService(repository), reconcile, scheduler)

    current = repository.get_work(reconcile["id"])
    assert current["attempts"][-1]["revision"] == reconcile["attempts"][-1]["revision"] + 1
    assert current["attempts"][-1]["generation"] == 1
    assert scheduler.get_by_attempt(held.attempt_id).state == "held"
    assert repository.read_events(reconcile["job_id"])["events"] == before


def test_cancellation_between_release_and_retry_blocks_replacement(tmp_path, monkeypatch):
    """A terminal cancellation after durable cessation wins over the retry CAS."""
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, reconcile, held = _reconciled_held_work(tmp_path)
    scheduler = WorkScheduler(
        repository,
        stop_verifier=lambda owner: StoppedWriter(
            owner.attempt_id, owner.generation, owner.revision, "irreversible-exit"
        ),
    )
    release = scheduler.release_for_replacement

    def release_then_cancel(*args, **kwargs):
        snapshot = release(*args, **kwargs)
        prior = snapshot["attempts"][-1]
        repository.transition_attempt(
            attempt_id=prior["id"],
            generation=prior["generation"],
            expected_revision=prior["revision"],
            expected_state="reconcile",
            target="cancelled",
            actor_id="owner",
            event_id="cancel-after-stop-release",
            evidence=TransitionEvidence(generation=1, expected_generation=1),
        )
        return snapshot

    monkeypatch.setattr(scheduler, "release_for_replacement", release_then_cancel)
    with pytest.raises(WorkConflict):
        _retry(WorkService(repository), reconcile, scheduler)

    current = repository.get_work(reconcile["id"])
    events = repository.read_events(reconcile["job_id"])["events"]
    assert current["state"] == "cancelled"
    assert current["attempts"][-1]["generation"] == 1
    assert scheduler.get_by_attempt(held.attempt_id).state == "released"
    assert [event["event_type"] for event in events].count("scheduler.released") == 1
    assert [event["event_type"] for event in events].count("attempt.replanned") == 0


def test_retry_failure_replay_does_not_duplicate_release_or_replan(tmp_path, monkeypatch):
    """Replaying stale fences after a retry failure cannot repeat either durable effect."""
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, reconcile, held = _reconciled_held_work(tmp_path)
    scheduler = WorkScheduler(
        repository,
        stop_verifier=lambda owner: StoppedWriter(
            owner.attempt_id, owner.generation, owner.revision, "irreversible-exit"
        ),
    )
    original_retry = repository.retry_work

    def fail_retry(*args, **kwargs):
        raise WorkConflict("controlled retry failure")

    monkeypatch.setattr(repository, "retry_work", fail_retry)
    with pytest.raises(WorkConflict, match="controlled"):
        _retry(WorkService(repository), reconcile, scheduler)
    monkeypatch.setattr(repository, "retry_work", original_retry)
    after_failure = repository.read_events(reconcile["job_id"])["events"]

    with pytest.raises(WorkConflict):
        _retry(WorkService(repository), reconcile, scheduler)

    events = repository.read_events(reconcile["job_id"])["events"]
    assert scheduler.get_by_attempt(held.attempt_id).state == "released"
    assert [event["event_type"] for event in events].count("scheduler.released") == 1
    assert [event["event_type"] for event in events].count("attempt.replanned") == 0
    assert events == after_failure


def test_replacement_preserves_consumed_units_and_requires_readmission(tmp_path):
    """A stopped writer can be replanned but cannot bypass scheduler budget admission."""
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, reconcile, held = _reconciled_held_work(tmp_path, budget=1)
    scheduler = WorkScheduler(
        repository,
        stop_verifier=lambda owner: StoppedWriter(
            owner.attempt_id, owner.generation, owner.revision, "irreversible-exit"
        ),
    )

    replacement = _retry(WorkService(repository), reconcile, scheduler)
    current = replacement["attempts"][-1]
    with pytest.raises(SchedulerConflict):
        scheduler.get_by_attempt(current["id"])
    queued = scheduler.enqueue(
        attempt_id=current["id"],
        generation=current["generation"],
        expected_attempt_revision=current["revision"],
        units=1,
        actor_id="owner",
    )

    assert scheduler.get_by_attempt(held.attempt_id).state == "released"
    assert queued.state == "queued"
    assert scheduler.claim_next(actor_id="owner") is None
    with repository.connection() as connection:
        consumed = connection.execute(
            "SELECT sum(units) FROM work_scheduler_requests "
            "WHERE work_item_id=? AND state='released'",
            (reconcile["id"],),
        ).fetchone()[0]
    assert consumed == 1
    assert repository.get_work(reconcile["id"])["attempts"][-1]["state"] == "planned"


def test_other_work_reservation_in_same_store_cannot_release_or_replan(tmp_path, monkeypatch):
    """A same-store reservation swap is rejected before it can cross work ownership."""
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, reconcile, held = _reconciled_held_work(tmp_path)
    scheduler = WorkScheduler(repository, stop_verifier=lambda owner: None)
    other = repository.admit_work(
        job_id=reconcile["job_id"],
        operation_kind="launch",
        idempotency_key="other-work",
        request_hash="e" * 64,
        contract_id="contract",
        snapshot_id=None,
        provider="mock_cli",
        actor_id="owner",
        lease_seconds=60,
    )
    other_attempt = other["attempts"][-1]
    other_reservation = scheduler.enqueue(
        attempt_id=other_attempt["id"],
        generation=other_attempt["generation"],
        expected_attempt_revision=other_attempt["revision"],
        units=1,
        actor_id="owner",
    )
    before = repository.read_events(reconcile["job_id"])["events"]
    original_lookup = scheduler.get_by_attempt
    monkeypatch.setattr(scheduler, "get_by_attempt", lambda attempt_id: other_reservation)

    with pytest.raises(WorkConflict, match="does not own"):
        _retry(WorkService(repository), reconcile, scheduler)

    monkeypatch.setattr(scheduler, "get_by_attempt", original_lookup)
    assert scheduler.get_by_attempt(held.attempt_id).state == "held"
    assert scheduler.get_by_attempt(other_attempt["id"]).state == "queued"
    assert repository.get_work(reconcile["id"])["attempts"][-1]["generation"] == 1
    assert repository.get_work(other["id"])["attempts"][-1]["generation"] == 1
    assert repository.read_events(reconcile["job_id"])["events"] == before
