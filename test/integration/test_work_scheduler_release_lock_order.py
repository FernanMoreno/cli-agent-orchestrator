"""Native PostgreSQL lock-order regressions for scheduler release paths."""

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
from cli_agent_orchestrator.services.work_scheduler import StoppedWriter, WorkScheduler
from cli_agent_orchestrator.services.work_service import WorkService


@pytest.fixture
def release_lock_context(tmp_path, postgres_targets):
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    bootstrap_scope(postgres_targets.main)
    repository = WorkRepository(postgres_targets.main, filesystem_home=postgres_targets.home)
    repository.initialize()
    actor = auth._verified_principal("issuer", "scheduler-release-owner", [auth.SCOPE_ADMIN], "jwt")
    job = repository.create_job(
        project_id="project",
        principal_id=actor.id,
        allowed_providers=["mock_cli"],
        grant_id="scheduler-release-root",
        budget={"scheduler_units": 10},
    )
    authority = WorkAuthority(repository)
    grant = authority.issue_root(
        actor,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}, paths={str(tmp_path)}),
        expires_at=time() + 600,
    )
    scheduler = WorkScheduler(
        repository,
        stop_verifier=lambda owner: StoppedWriter(
            owner.attempt_id, owner.generation, owner.revision, "scheduler-release-stop-proof"
        ),
    )
    scheduler.configure(capacity=2, max_queue=10, aging_seconds=10, expected_policy_revision=0)
    admission = WorkAdmission(repository, backends={"test": AdmissionOnlyBackend()})
    admission.scheduler = scheduler
    return SimpleNamespace(
        root=tmp_path,
        repo=repository,
        actor=actor,
        authority=authority,
        job=job,
        grant=grant,
        service=admission,
        scheduler=scheduler,
        contracts={},
        launch_origins={},
        jobs=[(job, grant)],
    )


def _wait_until_blocked_by(target, waiting_pid, blocker_pid, *, timeout=8.0):
    deadline = monotonic() + timeout
    last_state = None
    with connect(target) as monitor:
        while monotonic() < deadline:
            row = monitor.execute(
                "SELECT wait_event_type,wait_event,state,pg_blocking_pids(pid) AS blockers "
                "FROM pg_stat_activity WHERE pid=%s",
                (waiting_pid,),
            ).fetchone()
            if row is not None:
                last_state = (row["wait_event_type"], row["wait_event"], tuple(row["blockers"]))
                if row["wait_event_type"] == "Lock" and blocker_pid in row["blockers"]:
                    return last_state
            sleep(0.01)
    raise AssertionError(
        f"backend {waiting_pid} never waited on {blocker_pid}; last state={last_state!r}"
    )


def _capture_outcome(call):
    try:
        return ("ok", call())
    except Exception as error:  # return the database failure for the assertion below
        return ("error", error)


def _cancel_planned(context, work):
    attempt = work["attempts"][-1]
    return context.repo.transition_attempt(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_revision=attempt["revision"],
        expected_state="planned",
        target="cancelled",
        actor_id="scheduler-release-test",
        event_id=f"cancel-{attempt['id']}",
        evidence=TransitionEvidence(generation=1, expected_generation=1),
    )


def _reconcile_planned(context, work):
    attempt = work["attempts"][-1]
    sent = context.repo.transition_attempt(
        attempt_id=attempt["id"],
        generation=1,
        expected_revision=attempt["revision"],
        expected_state="planned",
        target="sent",
        actor_id="scheduler-release-test",
        event_id=f"sent-{attempt['id']}",
        evidence=TransitionEvidence(
            generation=1,
            expected_generation=1,
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
    )
    sent_attempt = sent["attempts"][-1]
    return context.repo.transition_attempt(
        attempt_id=sent_attempt["id"],
        generation=1,
        expected_revision=sent_attempt["revision"],
        expected_state="sent",
        target="reconcile",
        actor_id="scheduler-release-test",
        event_id=f"reconcile-{sent_attempt['id']}",
        evidence=TransitionEvidence(generation=1, expected_generation=1),
    )


def test_release_does_not_hold_aggregate_while_admission_owns_job_event(
    release_lock_context, monkeypatch
):
    context = release_lock_context
    repository = context.repo
    scheduler = context.scheduler
    held_work = admit(context, "release-held")
    held = scheduler.claim_next(actor_id="scheduler-release-test")
    assert held is not None and held.work_item_id == held_work["id"]
    cancelled = _cancel_planned(context, held_work)

    admission_at_aggregate = Event()
    release_transaction_open = Event()
    continue_admission = Event()
    backend_pids = {}
    original_enqueue = scheduler._enqueue
    original_transaction = repository.transaction

    @contextmanager
    def trace_transaction():
        with original_transaction() as connection:
            if (
                current_thread().name == "scheduler-release"
                and "scheduler-release" not in backend_pids
            ):
                backend_pids["scheduler-release"] = connection.execute(
                    "SELECT pg_backend_pid()"
                ).fetchone()[0]
                release_transaction_open.set()
            yield connection

    def pause_admission(connection, **arguments):
        if current_thread().name == "scheduler-admission":
            backend_pids["scheduler-admission"] = connection.execute(
                "SELECT pg_backend_pid()"
            ).fetchone()[0]
            admission_at_aggregate.set()
            assert continue_admission.wait(8), "release did not reach its PostgreSQL lock wait"
        return original_enqueue(connection, **arguments)

    monkeypatch.setattr(repository, "transaction", trace_transaction)
    monkeypatch.setattr(scheduler, "_enqueue", pause_admission)

    def do_admission():
        current_thread().name = "scheduler-admission"
        return _capture_outcome(lambda: admit(context, "release-competing-admission"))

    def do_release():
        current_thread().name = "scheduler-release"
        return _capture_outcome(
            lambda: scheduler.release(
                held.id,
                generation=held.generation,
                expected_revision=held.revision,
                expected_attempt_revision=cancelled["attempts"][-1]["revision"],
                actor_id="scheduler-release-test",
            )
        )

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="scheduler-release-lock") as pool:
        admission_future = pool.submit(do_admission)
        assert admission_at_aggregate.wait(8), "admission did not reach scheduler enqueue"
        release_future = pool.submit(do_release)
        try:
            assert release_transaction_open.wait(8), "release did not open its write transaction"
            _wait_until_blocked_by(
                repository.target,
                backend_pids["scheduler-release"],
                backend_pids["scheduler-admission"],
            )
        finally:
            continue_admission.set()
        done, pending = wait((admission_future, release_future), timeout=15)
        assert not pending, "release/admission lock-order participants did not finish"
        admission_outcome, release_outcome = admission_future.result(), release_future.result()

    failures = [
        outcome[1] for outcome in (admission_outcome, release_outcome) if outcome[0] == "error"
    ]
    assert not failures, f"concurrent admission/release failed: {failures!r}"
    assert admission_outcome[1]["id"] != held_work["id"]
    assert release_outcome[1].state == "released"


def test_withdrawal_does_not_hold_aggregate_while_retry_owns_job(release_lock_context, monkeypatch):
    context = release_lock_context
    repository = context.repo
    scheduler = context.scheduler
    retry_work = admit(context, "withdraw-retry")
    held = scheduler.claim_next(actor_id="scheduler-release-test")
    assert held is not None and held.work_item_id == retry_work["id"]
    reconciled = _reconcile_planned(context, retry_work)

    denied_work = admit(context, "withdraw-revoked-queued")
    queued = scheduler.get_by_attempt(denied_work["attempts"][-1]["id"])
    cancelled = _cancel_planned(context, denied_work)
    context.authority.revoke(
        context.actor,
        grant_id=context.grant.id,
        expected_grant_revision=1,
        reason="withdrawal lock-order regression",
    )

    retry_at_aggregate = Event()
    retirement_lock_started = Event()
    continue_retry = Event()
    backend_pids = {}
    original_lock_aggregate = scheduler._lock_aggregate
    original_lock_attempt = repository._lock_attempt_for_write
    original_transaction = repository.transaction

    @contextmanager
    def trace_transaction():
        with original_transaction() as connection:
            name = current_thread().name
            if name in {"scheduler-replacement", "scheduler-preview"} and name not in backend_pids:
                backend_pids[name] = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
            yield connection

    def pause_retry_before_aggregate(connection):
        if current_thread().name == "scheduler-replacement":
            retry_at_aggregate.set()
            assert continue_retry.wait(8), "preview did not reach its PostgreSQL lock wait"
        return original_lock_aggregate(connection)

    def observe_retirement_lock(connection, attempt_id):
        if (
            current_thread().name == "scheduler-preview"
            and attempt_id == denied_work["attempts"][-1]["id"]
        ):
            retirement_lock_started.set()
        return original_lock_attempt(connection, attempt_id)

    monkeypatch.setattr(repository, "transaction", trace_transaction)
    monkeypatch.setattr(repository, "_lock_attempt_for_write", observe_retirement_lock)
    monkeypatch.setattr(scheduler, "_lock_aggregate", pause_retry_before_aggregate)

    def do_retry():
        current_thread().name = "scheduler-replacement"
        return _capture_outcome(
            lambda: WorkService(repository).retry_work(
                reconciled["id"],
                scheduler=scheduler,
                expected_revision=reconciled["revision"],
                provider="mock_cli",
                evidence=TransitionEvidence(
                    generation=1,
                    expected_generation=1,
                    reconciliation_authorized=True,
                ),
                actor_id="scheduler-release-test",
                lease_seconds=300,
            )
        )

    def do_preview():
        current_thread().name = "scheduler-preview"
        return _capture_outcome(context.service._preview)

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="scheduler-withdraw-lock") as pool:
        retry_future = pool.submit(do_retry)
        assert retry_at_aggregate.wait(8), "retry did not reach scheduler aggregate lock"
        preview_future = pool.submit(do_preview)
        try:
            assert retirement_lock_started.wait(8), "preview did not lock the retired attempt"
            _wait_until_blocked_by(
                repository.target,
                backend_pids["scheduler-preview"],
                backend_pids["scheduler-replacement"],
            )
        finally:
            continue_retry.set()
        done, pending = wait((retry_future, preview_future), timeout=15)
        assert not pending, "retry/withdrawal lock-order participants did not finish"
        retry_outcome, preview_outcome = retry_future.result(), preview_future.result()

    failures = [outcome[1] for outcome in (retry_outcome, preview_outcome) if outcome[0] == "error"]
    assert not failures, f"concurrent retry/withdrawal failed: {failures!r}"
    assert retry_outcome[1]["attempts"][-1]["generation"] == 2
    assert preview_outcome[1] is None
    assert scheduler.get_by_attempt(queued.attempt_id).state == "withdrawn"
    assert repository.get_work(denied_work["id"])["attempts"][-1]["state"] == "cancelled"


def test_claim_and_terminal_withdrawal_for_same_job_do_not_deadlock(
    release_lock_context, monkeypatch
):
    """Public claim and withdrawal serialize event writes without a job/FK cycle."""
    context = release_lock_context
    repository = context.repo
    scheduler = context.scheduler

    claim_work = admit(context, "claim-withdraw-claim")
    withdraw_work = admit(context, "claim-withdraw-terminal")
    cancelled = _cancel_planned(context, withdraw_work)
    queued = scheduler.get_by_attempt(withdraw_work["attempts"][-1]["id"])

    claim_holds_sequence = Event()
    withdrawal_transaction_open = Event()
    continue_claim = Event()
    backend_pids = {}
    original_append_event = repository._append_event
    original_transaction = repository.transaction

    @contextmanager
    def trace_transaction():
        with original_transaction() as connection:
            name = current_thread().name
            if name in {"scheduler-claim", "scheduler-withdraw"}:
                backend_pids[name] = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
                if name == "scheduler-withdraw":
                    withdrawal_transaction_open.set()
            yield connection

    def pause_claim_after_sequence_lock(
        connection,
        *,
        job_id,
        event_type,
        **arguments,
    ):
        if current_thread().name == "scheduler-claim" and event_type == "scheduler.claimed":
            sequence = connection.execute(
                "SELECT high_water FROM work_event_sequences WHERE job_id=%s FOR UPDATE",
                (job_id,),
            ).fetchone()
            assert sequence is not None
            claim_holds_sequence.set()
            assert continue_claim.wait(8), "withdrawal did not reach its event-sequence lock wait"
        return original_append_event(connection, job_id=job_id, event_type=event_type, **arguments)

    def wait_for_withdrawal_event_lock():
        deadline = monotonic() + 8.0
        last_state = None
        with connect(repository.target) as monitor:
            while monotonic() < deadline:
                row = monitor.execute(
                    "SELECT wait_event_type,wait_event,state,pg_blocking_pids(pid) AS blockers,query "
                    "FROM pg_stat_activity WHERE pid=%s",
                    (backend_pids["scheduler-withdraw"],),
                ).fetchone()
                if row is not None:
                    last_state = (
                        row["wait_event_type"],
                        row["wait_event"],
                        tuple(row["blockers"]),
                        row["query"],
                    )
                    query = row["query"].lower()
                    if (
                        row["wait_event_type"] == "Lock"
                        and backend_pids["scheduler-claim"] in row["blockers"]
                        and "work_jobs" in query
                    ):
                        return last_state
                sleep(0.01)
        raise AssertionError(
            "withdrawal did not wait on the claim's parent-job lock; "
            f"last PostgreSQL activity={last_state!r}"
        )

    monkeypatch.setattr(repository, "transaction", trace_transaction)
    monkeypatch.setattr(repository, "_append_event", pause_claim_after_sequence_lock)

    def do_claim():
        current_thread().name = "scheduler-claim"
        return _capture_outcome(lambda: scheduler.claim_next(actor_id="scheduler-release-test"))

    def do_withdraw():
        current_thread().name = "scheduler-withdraw"
        return _capture_outcome(
            lambda: scheduler.withdraw_queued(
                queued.id,
                generation=queued.generation,
                expected_revision=queued.revision,
                expected_attempt_revision=cancelled["attempts"][-1]["revision"],
                actor_id="scheduler-release-test",
            )
        )

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="scheduler-claim-withdraw") as pool:
        claim_future = pool.submit(do_claim)
        assert claim_holds_sequence.wait(8), "public claim did not lock the job event sequence"
        withdrawal_future = pool.submit(do_withdraw)
        try:
            assert withdrawal_transaction_open.wait(
                8
            ), "public withdrawal did not open its transaction"
            wait_for_withdrawal_event_lock()
        finally:
            continue_claim.set()
        done, pending = wait((claim_future, withdrawal_future), timeout=15)
        assert not pending, "claim/withdraw lock-order participants did not finish"
        claim_outcome = claim_future.result()
        withdrawal_outcome = withdrawal_future.result()

    failures = [
        outcome[1] for outcome in (claim_outcome, withdrawal_outcome) if outcome[0] == "error"
    ]
    assert not failures, f"concurrent public claim/withdraw failed: {failures!r}"
    assert claim_outcome[1] is not None and claim_outcome[1].work_item_id == claim_work["id"]
    assert claim_outcome[1].state == "held"
    assert withdrawal_outcome[1].state == "withdrawn"
    events = repository.read_events(context.job["id"])["events"]
    event_types = [event["event_type"] for event in events]
    assert event_types.count("scheduler.claimed") == 1
    assert event_types.count("scheduler.withdrawn") == 1


def test_public_claim_and_enqueue_do_not_invert_parent_and_aggregate_locks(
    release_lock_context, monkeypatch
):
    """A claim's parent lock must not cycle with public enqueue's aggregate lock."""
    context = release_lock_context
    repository = context.repo
    scheduler = context.scheduler

    claim_work = admit(context, "claim-enqueue-existing")
    unqueued_work = repository.admit_work(
        job_id=context.job["id"],
        operation_kind="launch",
        idempotency_key="claim-enqueue-new",
        request_hash="a" * 64,
        contract_id="claim-enqueue-contract",
        snapshot_id=None,
        provider="mock_cli",
        actor_id=context.actor.id,
        lease_seconds=600,
    )
    enqueue_attempt = unqueued_work["attempts"][-1]

    enqueue_holds_aggregate = Event()
    continue_enqueue = Event()
    enqueue_transaction_open = Event()
    claim_transaction_open = Event()
    backend_pids = {}
    original_lock_aggregate = scheduler._lock_aggregate
    original_transaction = repository.transaction

    @contextmanager
    def trace_transaction():
        with original_transaction() as connection:
            name = current_thread().name
            if name in {"scheduler-enqueue", "scheduler-claim"}:
                backend_pids[name] = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
                if name == "scheduler-enqueue":
                    enqueue_transaction_open.set()
                else:
                    claim_transaction_open.set()
            yield connection

    def pause_enqueue_after_aggregate(connection):
        original_lock_aggregate(connection)
        if current_thread().name == "scheduler-enqueue":
            enqueue_holds_aggregate.set()
            assert continue_enqueue.wait(8), "claim did not reach its PostgreSQL lock wait"

    def wait_until_claim_is_blocked_by_enqueue():
        deadline = monotonic() + 8.0
        last_state = None
        with connect(repository.target) as monitor:
            while monotonic() < deadline:
                row = monitor.execute(
                    "SELECT wait_event_type,wait_event,state,pg_blocking_pids(pid) AS blockers,query "
                    "FROM pg_stat_activity WHERE pid=%s",
                    (backend_pids["scheduler-claim"],),
                ).fetchone()
                if row is not None:
                    last_state = (
                        row["wait_event_type"],
                        row["wait_event"],
                        tuple(row["blockers"]),
                        row["query"],
                    )
                    if (
                        row["wait_event_type"] == "Lock"
                        and backend_pids["scheduler-enqueue"] in row["blockers"]
                    ):
                        return last_state
                sleep(0.01)
        raise AssertionError(
            "claim never waited on the enqueue transaction; "
            f"last PostgreSQL activity={last_state!r}"
        )

    def observe_enqueue_fk_wait(timeout=1.5):
        deadline = monotonic() + timeout
        with connect(repository.target) as monitor:
            while monotonic() < deadline:
                row = monitor.execute(
                    "SELECT wait_event_type,wait_event,pg_blocking_pids(pid) AS blockers,query "
                    "FROM pg_stat_activity WHERE pid=%s",
                    (backend_pids["scheduler-enqueue"],),
                ).fetchone()
                if row is None:
                    return None
                query = row["query"].lower()
                if (
                    row["wait_event_type"] == "Lock"
                    and backend_pids["scheduler-claim"] in row["blockers"]
                    and "insert into work_scheduler_requests" in query
                ):
                    return (
                        row["wait_event_type"],
                        row["wait_event"],
                        tuple(row["blockers"]),
                        query,
                    )
                sleep(0.005)
        return None

    monkeypatch.setattr(repository, "transaction", trace_transaction)
    monkeypatch.setattr(scheduler, "_lock_aggregate", pause_enqueue_after_aggregate)

    def do_enqueue():
        current_thread().name = "scheduler-enqueue"
        return _capture_outcome(
            lambda: scheduler.enqueue(
                attempt_id=enqueue_attempt["id"],
                generation=enqueue_attempt["generation"],
                expected_attempt_revision=enqueue_attempt["revision"],
                units=1,
                actor_id=context.actor.id,
            )
        )

    def do_claim():
        current_thread().name = "scheduler-claim"
        return _capture_outcome(lambda: scheduler.claim_next(actor_id=context.actor.id))

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="scheduler-enqueue-claim") as pool:
        enqueue_future = pool.submit(do_enqueue)
        assert enqueue_holds_aggregate.wait(8), "public enqueue did not acquire the aggregate lock"
        claim_future = pool.submit(do_claim)
        fk_wait = None
        try:
            assert enqueue_transaction_open.wait(8), "public enqueue did not open its transaction"
            assert claim_transaction_open.wait(8), "public claim did not open its transaction"
            claim_wait = wait_until_claim_is_blocked_by_enqueue()
            assert any(
                marker in claim_wait[3].lower() for marker in ("pg_advisory_xact_lock", "work_jobs")
            ), f"claim waited on an unexpected SQL boundary: {claim_wait!r}"
        finally:
            continue_enqueue.set()
        fk_wait = observe_enqueue_fk_wait()
        done, pending = wait((enqueue_future, claim_future), timeout=15)
        assert not pending, "public enqueue/claim lock-order participants did not finish"
        enqueue_outcome = enqueue_future.result()
        claim_outcome = claim_future.result()

    failures = [outcome[1] for outcome in (enqueue_outcome, claim_outcome) if outcome[0] == "error"]
    assert not failures, f"public enqueue/claim failed (FK wait={fk_wait!r}): {failures!r}"
    assert enqueue_outcome[1].state == "queued"
    assert claim_outcome[1] is not None and claim_outcome[1].work_item_id == claim_work["id"]
    assert claim_outcome[1].state == "held"
