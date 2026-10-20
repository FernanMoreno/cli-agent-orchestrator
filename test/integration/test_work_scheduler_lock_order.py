"""Native PostgreSQL lock-order regressions for scheduler replacement.

The tests coordinate admission, retry, enqueue, claim and withdrawal through
their owned PostgreSQL transactions so lock ordering remains deterministic.
"""

from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from threading import Event, current_thread
from time import monotonic, sleep, time
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.postgres

from test.integration.test_work_dispatch import AdmissionOnlyBackend, admit

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_admission import WorkAdmission
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
from cli_agent_orchestrator.services.work_scheduler import (
    SchedulerBackpressure,
    StoppedWriter,
    WorkScheduler,
)
from cli_agent_orchestrator.services.work_service import WorkService


@pytest.fixture
def scheduler_lock_context(tmp_path, postgres_targets):
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    bootstrap_scope(postgres_targets.main)
    repository = WorkRepository(postgres_targets.main, filesystem_home=postgres_targets.home)
    repository.initialize()
    actor = auth._verified_principal("issuer", "scheduler-lock-owner", [auth.SCOPE_ADMIN], "jwt")
    job = repository.create_job(
        project_id="project",
        principal_id=actor.id,
        allowed_providers=["mock_cli"],
        grant_id="scheduler-lock-root",
        budget={"scheduler_units": 10},
    )
    grant = WorkAuthority(repository).issue_root(
        actor,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}, paths={str(tmp_path)}),
        expires_at=time() + 600,
    )
    scheduler = WorkScheduler(
        repository,
        stop_verifier=lambda owner: StoppedWriter(
            owner.attempt_id, owner.generation, owner.revision, "scheduler-lock-stop-proof"
        ),
    )
    scheduler.configure(capacity=2, max_queue=10, aging_seconds=10, expected_policy_revision=0)
    admission = WorkAdmission(repository, backends={"test": AdmissionOnlyBackend()})
    admission.scheduler = scheduler
    return SimpleNamespace(
        root=tmp_path,
        repo=repository,
        actor=actor,
        job=job,
        grant=grant,
        service=admission,
        scheduler=scheduler,
        contracts={},
        launch_origins={},
        jobs=[(job, grant)],
    )


def test_retry_release_and_admission_share_job_before_scheduler_lock_order(
    scheduler_lock_context, monkeypatch
):
    """A read-only preview stays outside the aggregate while retry locks its job."""
    context = scheduler_lock_context
    repository = context.repo
    scheduler = context.scheduler

    prior = admit(context, "scheduler-lock-prior")
    held = scheduler.claim_next(actor_id="scheduler-lock-test")
    assert held is not None and held.work_item_id == prior["id"]
    sent = repository.transition_attempt(
        attempt_id=prior["attempts"][-1]["id"],
        generation=1,
        expected_revision=prior["attempts"][-1]["revision"],
        expected_state="planned",
        target="sent",
        actor_id="scheduler-lock-test",
        event_id="scheduler-lock-sent",
        evidence=TransitionEvidence(
            generation=1,
            expected_generation=1,
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
    )
    reconciled = repository.transition_attempt(
        attempt_id=prior["attempts"][-1]["id"],
        generation=1,
        expected_revision=sent["attempts"][-1]["revision"],
        expected_state="sent",
        target="reconcile",
        actor_id="scheduler-lock-test",
        event_id="scheduler-lock-reconcile",
        evidence=TransitionEvidence(generation=1, expected_generation=1),
    )
    eligible = admit(context, "scheduler-lock-eligible")

    preview_after_qualification = Event()
    retry_finished = Event()
    original_peek = scheduler._peek_next

    def pause_before_pure_selection(connection, *, actor_id, eligible_attempts=None):
        if current_thread().name == "admission-preview":
            preview_after_qualification.set()
            assert retry_finished.wait(timeout=8), "retry waited on preview's scheduler lock"
        return original_peek(connection, actor_id=actor_id, eligible_attempts=eligible_attempts)

    monkeypatch.setattr(scheduler, "_peek_next", pause_before_pure_selection)

    def preview():
        current_thread().name = "admission-preview"
        return context.service._preview()

    def retry():
        current_thread().name = "retry-route"
        try:
            return WorkService(repository).retry_work(
                reconciled["id"],
                scheduler=scheduler,
                expected_revision=reconciled["revision"],
                provider="mock_cli",
                evidence=TransitionEvidence(
                    generation=1,
                    expected_generation=1,
                    reconciliation_authorized=True,
                ),
                actor_id="scheduler-lock-test",
                lease_seconds=300,
            )
        finally:
            retry_finished.set()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="work-lock-order") as pool:
        preview_future = pool.submit(preview)
        assert preview_after_qualification.wait(
            timeout=8
        ), "admission did not reach pure scheduler selection"
        retry_future = pool.submit(retry)
        replacement = retry_future.result(timeout=8)
        done, pending = wait((preview_future, retry_future), timeout=15)
        assert not pending, "scheduler lock-order participants did not finish"
        preview_result = preview_future.result()

    assert preview_result is not None and preview_result.work_item_id == eligible["id"]
    assert replacement["attempts"][-1]["generation"] == 2
    assert scheduler.get_by_attempt(held.attempt_id).state == "released"


def _add_job(context, *, index):
    job = context.repo.create_job(
        project_id="project",
        principal_id=context.actor.id,
        allowed_providers=["mock_cli"],
        grant_id=f"scheduler-lock-root-{index}",
        budget={"scheduler_units": 10},
    )
    grant = WorkAuthority(context.repo).issue_root(
        context.actor,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}, paths={str(context.root)}),
        expires_at=time() + 600,
    )
    context.jobs.append((job, grant))
    return job, grant


def _cancel_queued_work(context, work, *, event_id):
    attempt = work["attempts"][-1]
    return context.repo.transition_attempt(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_revision=attempt["revision"],
        expected_state="planned",
        target="cancelled",
        actor_id="scheduler-lock-test",
        event_id=event_id,
        evidence=TransitionEvidence(
            generation=attempt["generation"], expected_generation=attempt["generation"]
        ),
    )


def _wait_until_retry_finishes_or_waits_on_preview(
    target, retry_pid, preview_pid, retry_done, timeout=8.0
):
    deadline = monotonic() + timeout
    last_state = None
    with connect(target) as monitor:
        while monotonic() < deadline:
            if retry_done.is_set():
                return "finished"
            row = monitor.execute(
                "SELECT wait_event_type,wait_event,pg_blocking_pids(pid) AS blockers "
                "FROM pg_stat_activity WHERE pid=%s",
                (retry_pid,),
            ).fetchone()
            if row is None:
                if retry_done.is_set():
                    return "finished"
            else:
                last_state = (row["wait_event_type"], row["wait_event"], tuple(row["blockers"]))
                if row["wait_event_type"] == "Lock" and preview_pid in row["blockers"]:
                    return "blocked"
            sleep(0.01)
    raise AssertionError(f"retry neither finished nor waited on preview; state={last_state!r}")


def test_batch_terminal_withdrawals_do_not_hold_aggregate_against_retry(
    scheduler_lock_context, monkeypatch
):
    """Many cleanup decrements in one admission tx must not invert across jobs."""
    context = scheduler_lock_context
    repository = context.repo
    scheduler = context.scheduler
    _add_job(context, index=1)
    _add_job(context, index=2)

    retry_work = admit(context, "withdraw-batch-retry", job_index=1)
    held = scheduler.claim_next(actor_id="scheduler-lock-test")
    assert held is not None and held.work_item_id == retry_work["id"]
    prior = retry_work["attempts"][-1]
    sent = repository.transition_attempt(
        attempt_id=prior["id"],
        generation=prior["generation"],
        expected_revision=prior["revision"],
        expected_state="planned",
        target="sent",
        actor_id="scheduler-lock-test",
        event_id="scheduler-lock-batch-sent",
        evidence=TransitionEvidence(
            generation=prior["generation"],
            expected_generation=prior["generation"],
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
    )
    sent_attempt = sent["attempts"][-1]
    reconciled = repository.transition_attempt(
        attempt_id=sent_attempt["id"],
        generation=sent_attempt["generation"],
        expected_revision=sent_attempt["revision"],
        expected_state="sent",
        target="reconcile",
        actor_id="scheduler-lock-test",
        event_id="scheduler-lock-batch-reconcile",
        evidence=TransitionEvidence(
            generation=sent_attempt["generation"],
            expected_generation=sent_attempt["generation"],
        ),
    )

    terminal_first = _cancel_queued_work(
        context, admit(context, "withdraw-batch-first", job_index=0), event_id="cancel-first"
    )
    terminal_second = _cancel_queued_work(
        context, admit(context, "withdraw-batch-second", job_index=1), event_id="cancel-second"
    )
    first_request = scheduler.get_by_attempt(terminal_first["attempts"][-1]["id"])
    second_request = scheduler.get_by_attempt(terminal_second["attempts"][-1]["id"])
    for grant in (context.jobs[0][1], context.jobs[1][1]):
        WorkAuthority(repository).revoke(
            context.actor,
            grant_id=grant.id,
            expected_grant_revision=1,
            reason="terminal queued request withdrawal lock-order regression",
        )
    eligible = admit(context, "withdraw-batch-eligible", job_index=2)

    retry_at_aggregate = Event()
    allow_retry_aggregate = Event()
    first_withdrawal_complete = Event()
    allow_preview_continue = Event()
    retry_done = Event()
    backend_pids = {}
    original_transaction = repository.transaction
    original_lock_aggregate = scheduler._lock_aggregate
    original_withdraw = scheduler._withdraw_queued

    @contextmanager
    def trace_transaction():
        with original_transaction() as connection:
            name = current_thread().name
            if name in {"batch-retry", "batch-preview"} and name not in backend_pids:
                backend_pids[name] = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
            yield connection

    def pause_retry_before_aggregate(connection):
        if current_thread().name == "batch-retry":
            retry_at_aggregate.set()
            assert allow_retry_aggregate.wait(8), "preview never completed its first withdrawal"
        return original_lock_aggregate(connection)

    def pause_preview_after_first_withdrawal(connection, identifier, **arguments):
        result = original_withdraw(connection, identifier, **arguments)
        if current_thread().name == "batch-preview" and identifier == first_request.id:
            first_withdrawal_complete.set()
            assert allow_preview_continue.wait(8), "retry never reached the aggregate boundary"
        return result

    monkeypatch.setattr(repository, "transaction", trace_transaction)
    monkeypatch.setattr(scheduler, "_lock_aggregate", pause_retry_before_aggregate)
    monkeypatch.setattr(scheduler, "_withdraw_queued", pause_preview_after_first_withdrawal)

    def do_retry():
        current_thread().name = "batch-retry"
        try:
            return WorkService(repository).retry_work(
                reconciled["id"],
                scheduler=scheduler,
                expected_revision=reconciled["revision"],
                provider="mock_cli",
                evidence=TransitionEvidence(
                    generation=1,
                    expected_generation=1,
                    reconciliation_authorized=True,
                ),
                actor_id="scheduler-lock-test",
                lease_seconds=300,
            )
        finally:
            retry_done.set()

    def do_preview():
        current_thread().name = "batch-preview"
        return context.service._preview()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="scheduler-withdraw-batch") as pool:
        retry_future = pool.submit(do_retry)
        assert retry_at_aggregate.wait(8), "retry did not take its parent job lock"
        preview_future = pool.submit(do_preview)
        state = None
        try:
            assert first_withdrawal_complete.wait(
                8
            ), "preview did not withdraw the first terminal request"
            allow_retry_aggregate.set()
            state = _wait_until_retry_finishes_or_waits_on_preview(
                repository.target,
                backend_pids["batch-retry"],
                backend_pids["batch-preview"],
                retry_done,
            )
        finally:
            allow_retry_aggregate.set()
            allow_preview_continue.set()
        done, pending = wait((retry_future, preview_future), timeout=15)
        assert not pending, "batch withdrawal/retry lock-order participants did not finish"
        retry_result, preview_result = retry_future.result(), preview_future.result()

    assert state == "finished", "withdrawal held the aggregate while locking another job"
    assert retry_result["attempts"][-1]["generation"] == 2
    assert preview_result is not None and preview_result.work_item_id == eligible["id"]
    assert scheduler.get_by_attempt(first_request.attempt_id).state == "withdrawn"
    assert scheduler.get_by_attempt(second_request.attempt_id).state == "withdrawn"

    scheduler.configure(capacity=1, max_queue=2, aging_seconds=10, expected_policy_revision=1)
    admit(context, "withdraw-batch-capacity-one", job_index=2)
    with pytest.raises(SchedulerBackpressure):
        admit(context, "withdraw-batch-capacity-two-overflow", job_index=2)
    claimed = scheduler.claim_next(actor_id="scheduler-lock-test")
    assert claimed is not None
    assert scheduler.claim_next(actor_id="scheduler-lock-test") is None


def _withdraw_request(scheduler, request, *, attempt_revision):
    return scheduler.withdraw_queued(
        request.id,
        generation=request.generation,
        expected_revision=request.revision,
        expected_attempt_revision=attempt_revision,
        actor_id="scheduler-lock-test",
    )


def _scheduler_counts(repository):
    with repository.read_snapshot() as connection:
        return dict(
            connection.execute(
                "SELECT state,count(*) FROM work_scheduler_requests GROUP BY state"
            ).fetchall()
        )


def test_enqueue_racing_withdrawal_never_exceeds_queue_limit(scheduler_lock_context, monkeypatch):
    """An uncommitted queue decrement can only cause conservative backpressure."""
    context = scheduler_lock_context
    repository = context.repo
    scheduler = context.scheduler
    _add_job(context, index=1)
    scheduler.configure(capacity=2, max_queue=1, aging_seconds=10, expected_policy_revision=1)

    terminal = _cancel_queued_work(
        context, admit(context, "enqueue-withdraw-original"), event_id="cancel-enqueue-withdraw"
    )
    terminal_attempt = terminal["attempts"][-1]
    request = scheduler.get_by_attempt(terminal_attempt["id"])

    withdrawal_updated = Event()
    allow_withdrawal_commit = Event()
    original_withdraw = scheduler._withdraw_queued

    def pause_before_withdrawal_commit(connection, identifier, **arguments):
        result = original_withdraw(connection, identifier, **arguments)
        if current_thread().name == "queue-withdrawal":
            withdrawal_updated.set()
            assert allow_withdrawal_commit.wait(8), "enqueue never reached the withdrawal race"
        return result

    monkeypatch.setattr(scheduler, "_withdraw_queued", pause_before_withdrawal_commit)

    def do_withdrawal():
        current_thread().name = "queue-withdrawal"
        return _withdraw_request(scheduler, request, attempt_revision=terminal_attempt["revision"])

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="queue-withdrawal") as pool:
        withdrawal = pool.submit(do_withdrawal)
        assert withdrawal_updated.wait(8), "withdrawal did not update the queue row"
        enqueue_future = pool.submit(
            admit, context, "enqueue-during-uncommitted-withdrawal", job_index=1
        )
        try:
            try:
                enqueue_result = enqueue_future.result(timeout=8)
            except SchedulerBackpressure:
                enqueue_result = None
        finally:
            allow_withdrawal_commit.set()
        withdrawn = withdrawal.result(timeout=8)

    assert withdrawn.state == "withdrawn"
    if enqueue_result is None:
        # READ COMMITTED still sees the old queued row until withdrawal commits.
        # Retrying after commit must use the freed slot.
        admitted = admit(context, "enqueue-after-withdrawal-commit", job_index=1)
        assert admitted["state"] == "queued"
    else:
        assert enqueue_result["state"] == "queued"
    counts = _scheduler_counts(repository)
    assert counts.get("queued", 0) == 1
    assert counts.get("withdrawn", 0) == 1
    events = repository.read_events(context.job["id"])["events"]
    assert sum(event["event_type"] == "scheduler.withdrawn" for event in events) == 1


def test_claim_racing_with_terminal_withdrawal_has_one_queue_event_winner(
    scheduler_lock_context, monkeypatch
):
    """A claim of an ineligible terminal row leaves one durable withdraw event."""
    context = scheduler_lock_context
    repository = context.repo
    scheduler = context.scheduler
    terminal = _cancel_queued_work(
        context, admit(context, "claim-withdraw-original"), event_id="cancel-claim-withdraw"
    )
    terminal_attempt = terminal["attempts"][-1]
    request = scheduler.get_by_attempt(terminal_attempt["id"])

    claim_holds_aggregate = Event()
    allow_claim_to_read_queue = Event()
    withdrawal_transaction_open = Event()
    withdrawal_done = Event()
    backend_pids = {}
    original_transaction = repository.transaction
    original_lock = scheduler._lock_aggregate
    original_claim = scheduler._claim_next

    @contextmanager
    def trace_transaction():
        with original_transaction() as connection:
            name = current_thread().name
            if name in {"racing-claim", "racing-withdrawal"}:
                backend_pids[name] = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
                if name == "racing-withdrawal":
                    withdrawal_transaction_open.set()
            yield connection

    def pause_claim_after_aggregate(connection, *, actor_id, eligible_attempts=None):
        original_lock(connection)
        if current_thread().name == "racing-claim":
            claim_holds_aggregate.set()
            assert allow_claim_to_read_queue.wait(8), "withdrawal never reached the parent-job wait"
        return original_claim(connection, actor_id=actor_id, eligible_attempts=eligible_attempts)

    monkeypatch.setattr(repository, "transaction", trace_transaction)
    monkeypatch.setattr(scheduler, "_claim_next", pause_claim_after_aggregate)

    def do_claim():
        current_thread().name = "racing-claim"
        return scheduler.claim_next(actor_id="scheduler-lock-test")

    def do_withdrawal():
        current_thread().name = "racing-withdrawal"
        try:
            return _withdraw_request(
                scheduler, request, attempt_revision=terminal_attempt["revision"]
            )
        finally:
            withdrawal_done.set()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="claim-withdrawal") as pool:
        claim_future = pool.submit(do_claim)
        assert claim_holds_aggregate.wait(8), "claim did not hold the aggregate lock"
        withdraw_future = pool.submit(do_withdrawal)
        try:
            assert withdrawal_transaction_open.wait(8), "withdrawal did not open its transaction"
            state = _wait_until_retry_finishes_or_waits_on_preview(
                repository.target,
                backend_pids["racing-withdrawal"],
                backend_pids["racing-claim"],
                withdrawal_done,
            )
        finally:
            allow_claim_to_read_queue.set()
        withdrawn = withdraw_future.result(timeout=8)
        claimed = claim_future.result(timeout=8)

    assert state == "blocked"
    assert withdrawn.state == "withdrawn"
    assert claimed is None
    counts = _scheduler_counts(repository)
    assert counts.get("queued", 0) == 0
    assert counts.get("held", 0) == 0
    events = repository.read_events(context.job["id"])["events"]
    scheduler_events = [
        event["event_type"]
        for event in events
        if event["event_type"] in {"scheduler.withdrawn", "scheduler.claimed"}
    ]
    assert scheduler_events == ["scheduler.withdrawn"]
