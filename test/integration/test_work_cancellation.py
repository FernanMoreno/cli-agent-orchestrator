"""PostgreSQL cancellation trees keep logical winners and cleanup separate."""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.postgres

from test.services.test_work_lineage_origin import (
    _delivery_adapter,
    _managed_handoff,
    _modules,
    _NoEffectBackend,
)

from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
from cli_agent_orchestrator.services.work_service import WorkService


@pytest.fixture
def work_store(tmp_path, postgres_targets):
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope
    from cli_agent_orchestrator.models.work_contract import (
        ContractPermissions,
        ContractResources,
        ContractSnapshot,
        EffectiveWorkContract,
    )
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.delegation_snapshot import (
        DelegationSnapshots,
        ResolvedSnapshot,
    )
    from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
    from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
    from cli_agent_orchestrator.services.work_scheduler import WorkScheduler

    target = postgres_targets.main
    bootstrap_scope(target)
    repository = WorkRepository(target, filesystem_home=postgres_targets.home)
    repository.initialize()
    WorkScheduler(repository).configure(
        capacity=4, max_queue=100, aging_seconds=10, expected_policy_revision=0
    )
    models, admission_module, origin_module = _modules()
    owner = auth._verified_principal(
        "https://issuer.test", "cancellation-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    child = auth._verified_principal(
        "https://issuer.test", "cancellation-child", [auth.SCOPE_WRITE], "jwt"
    )
    receiver = auth._verified_principal(
        "https://issuer.test", "cancellation-receiver", [auth.SCOPE_WRITE], "jwt"
    )
    job = repository.create_job(
        project_id="project",
        principal_id=owner.id,
        allowed_providers=["mock_cli"],
        grant_id="cancellation-root",
        budget={"scheduler_units": 100},
    )
    authority = WorkAuthority(repository)
    root = authority.issue_root(
        owner,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}, paths={str(tmp_path)}),
        expires_at=time.time() + 600,
    )
    child_grant = authority.delegate(
        owner,
        parent_grant_id=root.id,
        expected_parent_revision=root.revision,
        child_principal=child,
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}, paths={str(tmp_path)}),
        expires_at=time.time() + 300,
    )
    receiver_grant = authority.delegate(
        owner,
        parent_grant_id=root.id,
        expected_parent_revision=root.revision,
        child_principal=receiver,
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}, paths={str(tmp_path)}),
        expires_at=time.time() + 300,
    )
    snapshot = DelegationSnapshots(
        repository,
        policy=KnowledgePolicy(repository, job["id"], root.id, root.revision),
    ).freeze(
        principal=owner,
        job_id=job["id"],
        contract_id="cancellation-parent-contract",
        binding_key="cancellation-parent-snapshot",
        request_hash="a" * 64,
        scope="project",
        scope_id="project",
        resolver=lambda _connection, _principal: ResolvedSnapshot("cancellation parent"),
    )
    parent_contract = EffectiveWorkContract(
        id="cancellation-parent-contract",
        operation_kind="inbox",
        provider="mock_cli",
        backend="test",
        permissions=ContractPermissions(paths=(str(tmp_path),)),
        resources=ContractResources(
            checkout_root=str(tmp_path), write_paths=(str(tmp_path / "parent"),), units=1
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )
    admission = admission_module.WorkAdmission(
        repository,
        backends={"test": _NoEffectBackend()},
        delivery_adapters={("inbox", 1): _delivery_adapter()},
    )
    parent = admission.admit(
        principal=owner,
        job_id=job["id"],
        idempotency_key="cancellation-parent",
        request_hash="b" * 64,
        grant_id=root.id,
        expected_grant_revision=root.revision,
        contract=parent_contract,
        lease_seconds=300,
    )
    origin_authority = origin_module.WorkOriginAuthority(repository)
    child_subject = origin_authority.register_subject(
        owner,
        verified_subject=child,
        kind="child",
        issuer_id=owner.id,
        expected_revision=0,
    )
    child_authorization = origin_authority.authorize(
        owner,
        subject=child,
        origin_kind="child",
        grant_id=child_grant.id,
        grant_revision=child_grant.revision,
        actions={"admit_child", "execute"},
        expires_at=time.time() + 120,
        expected_revision=0,
    )
    receiver_subject = origin_authority.register_subject(
        owner,
        verified_subject=receiver,
        kind="receiver",
        issuer_id=owner.id,
        expected_revision=0,
    )
    receiver_authorization = origin_authority.authorize(
        owner,
        subject=receiver,
        origin_kind="receiver",
        grant_id=receiver_grant.id,
        grant_revision=receiver_grant.revision,
        actions={"task_received"},
        expires_at=time.time() + 120,
        expected_revision=0,
    )
    child_contract = parent_contract.model_copy(
        update={
            "id": "cancellation-child-contract",
            "resources": parent_contract.resources.model_copy(
                update={"write_paths": (str(tmp_path / "child"),)}
            ),
        }
    )
    context = SimpleNamespace(
        models=models,
        admission=admission,
        origin_module=origin_module,
        repository=repository,
        owner=owner,
        job=job,
        parent=parent,
        child=child,
        child_grant=child_grant,
        child_subject=child_subject,
        child_authorization=child_authorization,
        receiver=receiver,
        receiver_grant=receiver_grant,
        receiver_subject=receiver_subject,
        receiver_authorization=receiver_authorization,
        child_contract=child_contract,
        root_used=False,
    )
    repository._cancellation_context = context
    return repository, job


def _admit(repository, job, key, *, parent_work_item_id=None):
    context = repository._cancellation_context
    if parent_work_item_id is None and not context.root_used:
        context.root_used = True
        return context.parent
    # Cancellation tests exercise repair of durable ancestry imported before
    # managed-lineage admission. Seed this explicitly legacy tree through the
    # repository's trusted boundary; public admission must still reject it.
    with repository.transaction() as connection:
        work = repository._admit_work(
            connection,
            job_id=job["id"],
            operation_kind="launch",
            idempotency_key=key,
            request_hash=hashlib.sha256(f"request:{key}".encode()).hexdigest(),
            contract_id=f"contract-{key}",
            snapshot_id=None,
            provider="mock_cli",
            actor_id=context.owner.id,
            lease_seconds=60,
            parent_work_item_id=parent_work_item_id,
        )
    assert work["lineage_protocol"] == "legacy"
    return work


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


def _root_only_cancelled_tree(repository, job, monkeypatch):
    """Model a pre-cascade cancellation using the current native schema."""
    parent = _running(repository, _admit(repository, job, "root-only-parent"))
    child = _running(
        repository, _admit(repository, job, "root-only-child", parent_work_item_id=parent["id"])
    )
    grandchild = _running(
        repository,
        _admit(repository, job, "root-only-grandchild", parent_work_item_id=child["id"]),
    )
    succeeded_child, result = _succeeded(
        repository,
        _running(
            repository,
            _admit(
                repository,
                job,
                "root-only-succeeded-child",
                parent_work_item_id=parent["id"],
            ),
        ),
    )
    before_cancel = repository.get_work(parent["id"])
    monkeypatch.setattr(repository, "_cancel_descendants", lambda *args, **kwargs: None)
    _cancel(repository, parent, event_id="root-only-parent-cancel")
    return dict(
        parent=parent,
        child=child,
        grandchild=grandchild,
        succeeded_child=succeeded_child,
        result=result,
        before_cancel=before_cancel,
        event_id="root-only-parent-cancel",
    )


def _tree_snapshot(repository, job_id, *work_item_ids):
    return (
        tuple(repository.get_work(work_item_id) for work_item_id in work_item_ids),
        repository.read_events(job_id),
    )


def test_parent_cancellation_cascades_live_descendants_and_preserves_succeeded_child(work_store):
    repository, job = work_store
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
                    WHERE receipt.work_item_id=%s AND event.event_type='attempt.cancelled'""",
                    (work_item_id,),
                ).fetchone()[0]
                == 1
            )


def test_cancelled_child_cleanup_stays_pending_until_external_confirmation(work_store):
    repository, job = work_store
    parent = _running(repository, _admit(repository, job, "parent"))
    child = _running(repository, _admit(repository, job, "child", parent_work_item_id=parent["id"]))
    _cancel(repository, parent, event_id="parent-cancel")
    child_after_cancel = repository.get_work(child["id"])
    assert child_after_cancel["state"] == "cancelled"

    cleanup_started, allow_confirmation = Event(), Event()

    def cleanup():
        cleanup_started.set()
        assert allow_confirmation.wait(timeout=5), "test cleanup confirmation was not released"
        return True

    restarted_service = WorkService(WorkRepository(repository.target))
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
            pending = WorkRepository(repository.target).get_work(child["id"])
            assert pending["state"] == "cancelled"
            assert pending["accepted_result_id"] is None
            assert pending["attempts"][-1]["cleanup_state"] == "pending"
        finally:
            allow_confirmation.set()
        completed = future.result(timeout=5)

    assert completed["state"] == "cancelled"
    assert completed["accepted_result_id"] is None
    assert completed["attempts"][-1]["cleanup_state"] == "complete"


def test_cancel_and_result_finish_race_obeys_postgres_commit_winner(work_store):
    repository, job = work_store
    parent = _running(repository, _admit(repository, job, "parent"))
    child = _running(repository, _admit(repository, job, "child", parent_work_item_id=parent["id"]))
    result = _record_result(repository, child)
    stale_child = repository.get_work(child["id"])
    start, cancellation_committed = Barrier(2), Event()

    def cancel_parent():
        start.wait(timeout=5)
        try:
            return _cancel(WorkRepository(repository.target), parent, event_id="parent-cancel")
        finally:
            cancellation_committed.set()

    def finish_child():
        start.wait(timeout=5)
        assert cancellation_committed.wait(
            timeout=5
        ), "cancellation commit did not release finisher"
        try:
            return _transition(
                WorkRepository(repository.target),
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


def test_restart_replay_repairs_root_only_cancelled_tree_without_rewriting_root(
    work_store, monkeypatch
):
    repository, job = work_store
    legacy = _root_only_cancelled_tree(repository, job, monkeypatch)
    reopened = WorkRepository(repository.target)
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
    work_store, monkeypatch
):
    repository, job = work_store
    legacy = _root_only_cancelled_tree(repository, job, monkeypatch)
    parent_attempt = legacy["parent"]["attempts"][-1]
    reopened = WorkRepository(repository.target)
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
        WorkRepository(repository.target).reconcile_cancelled_descendants(
            work_item_id=legacy["parent"]["id"],
            attempt_id=parent_attempt["id"],
            generation=parent_attempt["generation"],
            actor_id="operator",
        )
        == recovered
    )
    assert (
        _tree_snapshot(
            WorkRepository(repository.target),
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


def test_recovery_commit_fences_historical_result_finish(work_store, monkeypatch):
    repository, job = work_store
    legacy = _root_only_cancelled_tree(repository, job, monkeypatch)
    result = _record_result(repository, repository.get_work(legacy["child"]["id"]))
    stale_child = repository.get_work(legacy["child"]["id"])
    parent_attempt = legacy["parent"]["attempts"][-1]
    start, recovery_committed = Barrier(2), Event()

    def recover():
        start.wait(timeout=5)
        try:
            return WorkRepository(repository.target).reconcile_cancelled_descendants(
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
                WorkRepository(repository.target),
                stale_child,
                "finished",
                event_id="root-only-child-finish",
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
