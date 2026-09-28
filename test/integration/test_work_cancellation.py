"""Cancellation trees keep logical winners and cleanup observations separate."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
from cli_agent_orchestrator.services.work_service import WorkService
from test.clients.test_work_repository import (
    legacy_admit,
    legacy_v21_store,
    migrate_legacy_v21_store,
)


def _new_store(tmp_path, *, legacy=False):
    path = tmp_path / "work-cancellation.sqlite3"
    repository = legacy_v21_store(path) if legacy else WorkRepository(path)
    if not legacy:
        repository.initialize()
    job = repository.create_job(
        project_id="project",
        principal_id="operator",
        allowed_providers=["mock_cli"],
        grant_id="grant",
        budget={},
    )
    return repository, job


def _admit(repository, job, key, *, parent_work_item_id=None):
    request = dict(
        operation_kind="launch",
        idempotency_key=key,
        request_hash=hashlib.sha256(f"request:{key}".encode()).hexdigest(),
        contract_id=f"contract-{key}",
        snapshot_id=None,
        provider="mock_cli",
        actor_id="operator",
        lease_seconds=60,
        parent_work_item_id=parent_work_item_id,
    )
    if repository.__dict__.get("_legacy_v21"):
        return legacy_admit(repository, job, **request)
    return repository.admit_work(job_id=job["id"], **request)


def _transition(repository, work, target, *, event_id):
    attempt = work["attempts"][-1]
    if target == "sent":
        evidence = TransitionEvidence(
            generation=attempt["generation"],
            expected_generation=attempt["generation"],
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        )
    elif target == "running":
        evidence = TransitionEvidence(
            generation=attempt["generation"],
            expected_generation=attempt["generation"],
            task_received=True,
            execution_started=True,
        )
    elif target == "finished":
        evidence = TransitionEvidence(
            generation=attempt["generation"],
            expected_generation=attempt["generation"],
            result_durable=True,
            result_validated=True,
            children_settled=True,
        )
    else:
        evidence = TransitionEvidence(
            generation=attempt["generation"], expected_generation=attempt["generation"]
        )
    return repository.transition_attempt(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_revision=attempt["revision"],
        expected_state=attempt["state"],
        target=target,
        actor_id="operator",
        event_id=event_id,
        evidence=evidence,
    )


def _running(repository, work):
    sent = _transition(repository, work, "sent", event_id=f"sent-{work['id']}")
    return _transition(repository, sent, "running", event_id=f"running-{work['id']}")


def _record_result(repository, work):
    attempt = work["attempts"][-1]
    content = f"result:{work['id']}".encode()
    digest = hashlib.sha256(content).hexdigest()
    return repository.register_result(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        content_hash=digest,
        immutable_location=digest,
        byte_length=len(content),
        validator_id="contract-validator",
        validation_evidence={"contract_id": work["contract_id"]},
        actor_id="operator",
    )


def _succeeded(repository, work):
    result = _record_result(repository, work)
    finished = _transition(
        repository,
        repository.get_work(work["id"]),
        "finished",
        event_id=f"finished-{work['id']}",
    )
    return finished, result


def _cancel(repository, work, *, event_id):
    return _transition(repository, repository.get_work(work["id"]), "cancelled", event_id=event_id)


def _events_for(repository, job_id, work_item_id, event_type):
    return [
        event
        for event in repository.read_events(job_id)["events"]
        if event["work_item_id"] == work_item_id and event["event_type"] == event_type
    ]


def _replay_cancellation(repository, before_cancel, *, event_id):
    attempt = before_cancel["attempts"][-1]
    return repository.transition_attempt(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_revision=attempt["revision"],
        expected_state=attempt["state"],
        target="cancelled",
        actor_id="operator",
        event_id=event_id,
        evidence=TransitionEvidence(
            generation=attempt["generation"], expected_generation=attempt["generation"]
        ),
    )


def _legacy_cancelled_tree(repository, job, monkeypatch):
    """Persist a tree made by the pre-T054 root-only cancellation behavior."""
    parent = _running(repository, _admit(repository, job, "legacy-parent"))
    child = _running(
        repository, _admit(repository, job, "legacy-child", parent_work_item_id=parent["id"])
    )
    grandchild = _running(
        repository,
        _admit(repository, job, "legacy-grandchild", parent_work_item_id=child["id"]),
    )
    succeeded_child, result = _succeeded(
        repository,
        _running(
            repository,
            _admit(
                repository,
                job,
                "legacy-succeeded-child",
                parent_work_item_id=parent["id"],
            ),
        ),
    )
    before_cancel = repository.get_work(parent["id"])
    monkeypatch.setattr(repository, "_cancel_descendants", lambda *args, **kwargs: None)
    _cancel(repository, parent, event_id="legacy-parent-cancel")
    migrate_legacy_v21_store(repository)
    return dict(
        parent=parent,
        child=child,
        grandchild=grandchild,
        succeeded_child=succeeded_child,
        result=result,
        before_cancel=before_cancel,
        event_id="legacy-parent-cancel",
    )


def _tree_snapshot(repository, job_id, *work_item_ids):
    return (
        tuple(repository.get_work(work_item_id) for work_item_id in work_item_ids),
        repository.read_events(job_id),
    )


def test_parent_cancellation_cascades_live_descendants_and_preserves_succeeded_child(tmp_path):
    repository, job = _new_store(tmp_path, legacy=True)
    parent = _running(repository, _admit(repository, job, "parent"))
    live_child = _running(
        repository, _admit(repository, job, "live-child", parent_work_item_id=parent["id"])
    )
    grandchild = _running(
        repository, _admit(repository, job, "grandchild", parent_work_item_id=live_child["id"])
    )
    reconciled_grandchild = _transition(
        repository, grandchild, "reconcile", event_id="grandchild-reconcile"
    )
    succeeded_child, result = _succeeded(
        repository,
        _running(
            repository,
            _admit(repository, job, "succeeded-child", parent_work_item_id=parent["id"]),
        ),
    )
    migrate_legacy_v21_store(repository)

    cancelled_parent = _cancel(repository, parent, event_id="parent-cancel")

    assert cancelled_parent["state"] == "cancelled"
    assert repository.get_work(live_child["id"])["state"] == "cancelled"
    assert repository.get_work(reconciled_grandchild["id"])["state"] == "cancelled"
    preserved = repository.get_work(succeeded_child["id"])
    assert preserved["state"] == "succeeded"
    assert preserved["accepted_result_id"] == result["id"]
    assert len(_events_for(repository, job["id"], live_child["id"], "attempt.cancelled")) == 1
    assert (
        len(_events_for(repository, job["id"], reconciled_grandchild["id"], "attempt.cancelled"))
        == 1
    )
    with repository.connection() as connection:
        for work_item_id in (live_child["id"], reconciled_grandchild["id"]):
            assert (
                connection.execute(
                    """SELECT count(*)
                    FROM work_transition_receipts AS receipt
                    JOIN work_events AS event ON event.event_id=receipt.event_id
                    WHERE receipt.work_item_id=? AND event.event_type='attempt.cancelled'""",
                    (work_item_id,),
                ).fetchone()[0]
                == 1
            )


def test_cancelled_child_cleanup_stays_pending_until_external_confirmation(tmp_path):
    repository, job = _new_store(tmp_path, legacy=True)
    parent = _running(repository, _admit(repository, job, "parent"))
    child = _running(repository, _admit(repository, job, "child", parent_work_item_id=parent["id"]))
    migrate_legacy_v21_store(repository)
    _cancel(repository, parent, event_id="parent-cancel")
    child_after_cancel = repository.get_work(child["id"])
    assert child_after_cancel["state"] == "cancelled"

    cleanup_started, allow_confirmation = Event(), Event()

    def cleanup():
        cleanup_started.set()
        assert allow_confirmation.wait(timeout=5), "test cleanup confirmation was not released"
        return True

    restarted_service = WorkService(WorkRepository(repository.path))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            restarted_service.cleanup_attempt,
            child_after_cancel["attempts"][-1]["id"],
            cleanup,
            actor_id="operator",
        )
        try:
            assert cleanup_started.wait(
                timeout=5
            ), "cleanup callback did not observe durable pending"
            pending = WorkRepository(repository.path).get_work(child["id"])
            assert pending["state"] == "cancelled"
            assert pending["accepted_result_id"] is None
            assert pending["attempts"][-1]["cleanup_state"] == "pending"
        finally:
            allow_confirmation.set()
        completed = future.result(timeout=5)

    assert completed["state"] == "cancelled"
    assert completed["accepted_result_id"] is None
    assert completed["attempts"][-1]["cleanup_state"] == "complete"


def test_cancel_and_result_finish_race_obeys_sqlite_commit_winner(tmp_path):
    repository, job = _new_store(tmp_path, legacy=True)
    parent = _running(repository, _admit(repository, job, "parent"))
    child = _running(repository, _admit(repository, job, "child", parent_work_item_id=parent["id"]))
    migrate_legacy_v21_store(repository)
    result = _record_result(repository, child)
    stale_child = repository.get_work(child["id"])
    start, cancellation_committed = Barrier(2), Event()

    def cancel_parent():
        start.wait(timeout=5)
        try:
            return _cancel(WorkRepository(repository.path), parent, event_id="parent-cancel")
        finally:
            cancellation_committed.set()

    def finish_child():
        start.wait(timeout=5)
        assert cancellation_committed.wait(
            timeout=5
        ), "cancellation commit did not release finisher"
        try:
            return _transition(
                WorkRepository(repository.path),
                stale_child,
                "finished",
                event_id="child-finish",
            )
        except WorkConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        cancel_future = pool.submit(cancel_parent)
        finish_future = pool.submit(finish_child)
        cancelled = cancel_future.result(timeout=5)
        finished = finish_future.result(timeout=5)

    assert cancelled["state"] == "cancelled"
    parent_cancel = _events_for(repository, job["id"], parent["id"], "attempt.cancelled")
    child_finished = _events_for(repository, job["id"], child["id"], "attempt.finished")
    child_cancelled = _events_for(repository, job["id"], child["id"], "attempt.cancelled")
    assert len(parent_cancel) == 1
    current_child = repository.get_work(child["id"])
    assert isinstance(finished, WorkConflict)
    assert current_child["state"] == "cancelled"
    assert current_child["accepted_result_id"] is None
    assert current_child["attempts"][-1]["result_id"] == result["id"]
    assert not child_finished
    assert len(child_cancelled) == 1


def test_restart_replay_repairs_legacy_cancelled_tree_without_rewriting_root(tmp_path, monkeypatch):
    repository, job = _new_store(tmp_path, legacy=True)
    legacy = _legacy_cancelled_tree(repository, job, monkeypatch)
    reopened = WorkRepository(repository.path)
    root_before_recovery = reopened.get_work(legacy["parent"]["id"])
    root_events_before_recovery = _events_for(
        reopened, job["id"], legacy["parent"]["id"], "attempt.cancelled"
    )

    replayed = _replay_cancellation(reopened, legacy["before_cancel"], event_id=legacy["event_id"])

    assert replayed == root_before_recovery
    assert reopened.get_work(legacy["child"]["id"])["state"] == "cancelled"
    assert reopened.get_work(legacy["grandchild"]["id"])["state"] == "cancelled"
    assert reopened.get_work(legacy["parent"]["id"]) == root_before_recovery
    assert (
        _events_for(reopened, job["id"], legacy["parent"]["id"], "attempt.cancelled")
        == root_events_before_recovery
    )
    preserved = reopened.get_work(legacy["succeeded_child"]["id"])
    assert preserved["state"] == "succeeded"
    assert preserved["accepted_result_id"] == legacy["result"]["id"]


def test_explicit_recovery_is_idempotent_and_rejects_invalid_root_identity_or_generation(
    tmp_path, monkeypatch
):
    repository, job = _new_store(tmp_path, legacy=True)
    legacy = _legacy_cancelled_tree(repository, job, monkeypatch)
    parent_attempt = legacy["parent"]["attempts"][-1]
    reopened = WorkRepository(repository.path)
    root_before_recovery = reopened.get_work(legacy["parent"]["id"])

    recovered = reopened.reconcile_cancelled_descendants(
        work_item_id=legacy["parent"]["id"],
        attempt_id=parent_attempt["id"],
        generation=parent_attempt["generation"],
        actor_id="operator",
    )

    assert recovered == root_before_recovery
    assert reopened.get_work(legacy["child"]["id"])["state"] == "cancelled"
    assert reopened.get_work(legacy["grandchild"]["id"])["state"] == "cancelled"
    preserved = reopened.get_work(legacy["succeeded_child"]["id"])
    assert preserved["state"] == "succeeded"
    assert preserved["accepted_result_id"] == legacy["result"]["id"]
    stable = _tree_snapshot(
        reopened,
        job["id"],
        legacy["parent"]["id"],
        legacy["child"]["id"],
        legacy["grandchild"]["id"],
        legacy["succeeded_child"]["id"],
    )
    assert (
        WorkRepository(repository.path).reconcile_cancelled_descendants(
            work_item_id=legacy["parent"]["id"],
            attempt_id=parent_attempt["id"],
            generation=parent_attempt["generation"],
            actor_id="operator",
        )
        == recovered
    )
    assert (
        _tree_snapshot(
            WorkRepository(repository.path),
            job["id"],
            legacy["parent"]["id"],
            legacy["child"]["id"],
            legacy["grandchild"]["id"],
            legacy["succeeded_child"]["id"],
        )
        == stable
    )
    before_rejections = _tree_snapshot(
        reopened, job["id"], legacy["parent"]["id"], legacy["child"]["id"]
    )
    invalid_requests = (
        dict(
            work_item_id=legacy["parent"]["id"],
            attempt_id=legacy["child"]["attempts"][-1]["id"],
            generation=1,
        ),
        dict(
            work_item_id=legacy["parent"]["id"],
            attempt_id=parent_attempt["id"],
            generation=True,
        ),
        dict(
            work_item_id=legacy["parent"]["id"],
            attempt_id=parent_attempt["id"],
            generation=parent_attempt["generation"] + 1,
        ),
    )
    for request in invalid_requests:
        with pytest.raises(WorkConflict):
            reopened.reconcile_cancelled_descendants(actor_id="operator", **request)
        assert (
            _tree_snapshot(reopened, job["id"], legacy["parent"]["id"], legacy["child"]["id"])
            == before_rejections
        )
    active_parent = _running(repository, _admit(repository, job, "active-parent"))
    active_before = _tree_snapshot(reopened, job["id"], active_parent["id"])
    with pytest.raises(WorkConflict):
        reopened.reconcile_cancelled_descendants(
            work_item_id=active_parent["id"],
            attempt_id=active_parent["attempts"][-1]["id"],
            generation=1,
            actor_id="operator",
        )
    assert _tree_snapshot(reopened, job["id"], active_parent["id"]) == active_before


def test_recovery_commit_fences_historical_result_finish(tmp_path, monkeypatch):
    repository, job = _new_store(tmp_path, legacy=True)
    legacy = _legacy_cancelled_tree(repository, job, monkeypatch)
    result = _record_result(repository, repository.get_work(legacy["child"]["id"]))
    stale_child = repository.get_work(legacy["child"]["id"])
    parent_attempt = legacy["parent"]["attempts"][-1]
    start, recovery_committed = Barrier(2), Event()

    def recover():
        start.wait(timeout=5)
        try:
            return WorkRepository(repository.path).reconcile_cancelled_descendants(
                work_item_id=legacy["parent"]["id"],
                attempt_id=parent_attempt["id"],
                generation=parent_attempt["generation"],
                actor_id="operator",
            )
        finally:
            recovery_committed.set()

    def finish():
        start.wait(timeout=5)
        assert recovery_committed.wait(timeout=5), "recovery commit did not release finisher"
        try:
            return _transition(
                WorkRepository(repository.path),
                stale_child,
                "finished",
                event_id="legacy-child-finish",
            )
        except WorkConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        recover_future = pool.submit(recover)
        finish_future = pool.submit(finish)
        recovered = recover_future.result(timeout=5)
        finished = finish_future.result(timeout=5)

    child_after_recovery = repository.get_work(legacy["child"]["id"])
    assert recovered["state"] == "cancelled"
    assert isinstance(finished, WorkConflict)
    assert child_after_recovery["state"] == "cancelled"
    assert child_after_recovery["accepted_result_id"] is None
    assert child_after_recovery["attempts"][-1]["result_id"] == result["id"]
