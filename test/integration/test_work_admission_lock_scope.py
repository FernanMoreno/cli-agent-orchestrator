"""Native PostgreSQL regressions for bounded Work admission lock scope."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from psycopg.errors import LockNotAvailable

pytestmark = pytest.mark.postgres

from test.integration.test_work_dispatch import AdmissionOnlyBackend, admit

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_admission import WorkAdmission, _SelectionChanged
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_scheduler import (
    SchedulerSelectionChanged,
    WorkScheduler,
)


@pytest.fixture
def admission_lock_context(tmp_path, postgres_targets):
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    bootstrap_scope(postgres_targets.main)
    repository = WorkRepository(postgres_targets.main, filesystem_home=postgres_targets.home)
    repository.initialize()
    scheduler = WorkScheduler(repository)
    scheduler.configure(capacity=2, max_queue=10, aging_seconds=10, expected_policy_revision=0)
    actor = auth._verified_principal("issuer", "admission-lock-owner", [auth.SCOPE_ADMIN], "jwt")
    authority = WorkAuthority(repository)
    jobs = []
    for name in ("first", "second"):
        job = repository.create_job(
            project_id="project",
            principal_id=actor.id,
            allowed_providers=["mock_cli"],
            grant_id=f"admission-lock-{name}",
            budget={"scheduler_units": 10},
        )
        grant = authority.issue_root(
            actor,
            job_id=job["id"],
            providers={"mock_cli"},
            permissions=Permissions(tools={"knowledge.read"}, paths={str(tmp_path)}),
            expires_at=time.time() + 600,
        )
        jobs.append((job, grant))
    service = WorkAdmission(repository, backends={"test": AdmissionOnlyBackend()})
    service.scheduler = scheduler
    return SimpleNamespace(
        root=tmp_path,
        repo=repository,
        actor=actor,
        authority=authority,
        jobs=jobs,
        service=service,
        scheduler=scheduler,
        contracts={},
        launch_origins={},
    )


def _assert_row_lock_available(target, table, identifier):
    with connect(target) as peer:
        with peer.transaction():
            peer.execute(
                "SELECT id FROM " + table + " WHERE id=%s FOR UPDATE NOWAIT", (identifier,)
            )


def _assert_path_aggregate_lock_available(target):
    with connect(target) as peer:
        with peer.transaction():
            acquired = peer.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"cao:work-path-reservations:{target.store_id}",),
            ).fetchone()[0]
            assert acquired is True


def test_eligible_releases_each_candidate_authority_locks(admission_lock_context):
    context = admission_lock_context
    admitted = [
        admit(context, "lock-scope-first", job_index=0),
        admit(context, "lock-scope-second", job_index=1),
    ]

    with context.repo.transaction() as connection:
        eligible = context.service._eligible(connection)
        assert set(eligible) == {work["attempts"][-1]["id"] for work in admitted}

        for job, grant in context.jobs:
            _assert_row_lock_available(context.repo.target, "work_grants", grant.id)
            _assert_row_lock_available(context.repo.target, "work_jobs", job["id"])
        _assert_path_aggregate_lock_available(context.repo.target)


def test_preview_uses_selection_without_claim_or_event(admission_lock_context, monkeypatch):
    context = admission_lock_context
    work = admit(context, "preview-lock-scope", job_index=0)
    attempt_id = work["attempts"][-1]["id"]

    def forbidden(*args, **kwargs):
        raise AssertionError("preview must not claim, emit events, or lock the scheduler aggregate")

    monkeypatch.setattr(context.scheduler, "_claim_next", forbidden)
    monkeypatch.setattr(context.scheduler, "_lock_aggregate", forbidden)
    monkeypatch.setattr(context.scheduler, "_event", forbidden)

    preview = context.service._preview()
    assert preview is not None
    assert preview.attempt_id == attempt_id


def test_commit_retains_only_preview_authority_until_locked_claim(
    admission_lock_context, monkeypatch
):
    context = admission_lock_context
    admitted = [
        admit(context, "selected-lock-scope-first", job_index=0),
        admit(context, "selected-lock-scope-second", job_index=1),
    ]
    preview = context.service._preview()
    selected_index = next(
        index
        for index, work in enumerate(admitted)
        if work["attempts"][-1]["id"] == preview.attempt_id
    )
    other_index = 1 - selected_index

    def inspect_fence_then_change_selection(connection, **arguments):
        assert arguments["expected_selection"] == (preview.attempt_id, preview.generation)
        selected_job, selected_grant = context.jobs[selected_index]
        other_job, other_grant = context.jobs[other_index]
        for table, identifier in (
            ("work_grants", selected_grant.id),
            ("work_jobs", selected_job["id"]),
        ):
            with pytest.raises(LockNotAvailable):
                _assert_row_lock_available(context.repo.target, table, identifier)
        _assert_row_lock_available(context.repo.target, "work_grants", other_grant.id)
        _assert_row_lock_available(context.repo.target, "work_jobs", other_job["id"])
        raise SchedulerSelectionChanged("regression stops before any claim mutation")

    monkeypatch.setattr(context.scheduler, "_claim_next", inspect_fence_then_change_selection)
    with pytest.raises(_SelectionChanged):
        context.service._commit_dispatch(preview)


def test_claim_rejects_stale_preview_before_scheduler_writes(admission_lock_context):
    context = admission_lock_context
    work = admit(context, "stale-preview-claim", job_index=0)
    attempt_id = work["attempts"][-1]["id"]
    job = context.jobs[0][0]
    request = context.scheduler.get_by_attempt(attempt_id)

    with context.repo.transaction() as connection:
        policy_before = context.scheduler._policy(connection)["dispatch_sequence"]
        request_before = context.scheduler._row(connection, request.id)
        events_before = connection.execute(
            "SELECT count(*) FROM work_events WHERE job_id=%s", (job["id"],)
        ).fetchone()[0]

        with pytest.raises(SchedulerSelectionChanged):
            context.scheduler._claim_next(
                connection,
                actor_id="admission-lock-test",
                eligible_attempts=frozenset({attempt_id}),
                expected_selection=("another-attempt", 1),
            )

        assert context.scheduler._policy(connection)["dispatch_sequence"] == policy_before
        request_after = context.scheduler._row(connection, request_before["id"])
        assert (request_after["state"], request_after["revision"]) == (
            request_before["state"],
            request_before["revision"],
        )
        events_after = connection.execute(
            "SELECT count(*) FROM work_events WHERE job_id=%s", (job["id"],)
        ).fetchone()[0]
        assert events_after == events_before


def test_revoked_queued_work_is_retired_when_capacity_is_full(admission_lock_context):
    context = admission_lock_context
    context.scheduler.configure(
        capacity=1, max_queue=10, aging_seconds=10, expected_policy_revision=1
    )
    occupying = admit(context, "full-capacity-occupant", job_index=0)
    held = context.scheduler.claim_next(actor_id="admission-lock-test")
    assert held is not None and held.attempt_id == occupying["attempts"][-1]["id"]

    revoked_work = admit(context, "full-capacity-revoked", job_index=1)
    revoked_attempt = revoked_work["attempts"][-1]
    revoked_job, revoked_grant = context.jobs[1]
    context.authority.revoke(
        context.actor,
        grant_id=revoked_grant.id,
        expected_grant_revision=1,
        reason="full scheduler capacity retirement regression",
    )

    assert context.service._preview() is None
    assert context.repo.get_work(revoked_work["id"])["attempts"][-1]["state"] == "failed"
    assert context.scheduler.get_by_attempt(revoked_attempt["id"]).state == "withdrawn"


def test_concurrent_revoked_previews_retire_the_queued_order_once(
    admission_lock_context, monkeypatch
):
    context = admission_lock_context
    work = admit(context, "parallel-revoked-retirement", job_index=0)
    attempt_id = work["attempts"][-1]["id"]
    job, grant = context.jobs[0]
    context.authority.revoke(
        context.actor,
        grant_id=grant.id,
        expected_grant_revision=1,
        reason="parallel preview retirement regression",
    )

    both_candidates_probed = Barrier(2, timeout=15)
    original_retire = context.service._retire_unexecutable

    def synchronize_after_candidate_probe(connection, request):
        assert request["attempt_id"] == attempt_id
        both_candidates_probed.wait()
        return original_retire(connection, request)

    monkeypatch.setattr(context.service, "_retire_unexecutable", synchronize_after_candidate_probe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        previews = [pool.submit(context.service._preview) for _ in range(2)]
        outcomes = [preview.result(timeout=30) for preview in previews]

    assert outcomes == [None, None]
    assert context.repo.get_work(work["id"])["attempts"][-1]["state"] == "failed"
    assert context.scheduler.get_by_attempt(attempt_id).state == "withdrawn"
    with context.repo.connection() as connection:
        event_counts = dict(
            connection.execute(
                "SELECT event_type,count(*) FROM work_events WHERE job_id=%s "
                "AND attempt_id=%s AND event_type IN ('attempt.failed','scheduler.withdrawn') "
                "GROUP BY event_type",
                (job["id"], attempt_id),
            ).fetchall()
        )
    assert event_counts == {"attempt.failed": 1, "scheduler.withdrawn": 1}


def test_revoked_snapshot_does_not_retire_a_concurrently_held_request(
    admission_lock_context, monkeypatch
):
    context = admission_lock_context
    work = admit(context, "held-revoked-retirement", job_index=0)
    attempt_id = work["attempts"][-1]["id"]
    job, grant = context.jobs[0]
    context.authority.revoke(
        context.actor,
        grant_id=grant.id,
        expected_grant_revision=1,
        reason="held request retirement race regression",
    )

    original_retire = context.service._retire_unexecutable
    claimed = {}

    def claim_before_retire(connection, request):
        assert request["attempt_id"] == attempt_id
        held = context.scheduler.claim_next(actor_id="concurrent-claim-test")
        assert held is not None and held.attempt_id == attempt_id
        claimed["request"] = context.scheduler.get_by_attempt(attempt_id)
        return original_retire(connection, request)

    monkeypatch.setattr(context.service, "_retire_unexecutable", claim_before_retire)
    assert context.service._preview() is None

    assert claimed["request"].state == "held"
    assert context.repo.get_work(work["id"])["attempts"][-1]["state"] == "planned"
    with context.repo.connection() as connection:
        event_counts = dict(
            connection.execute(
                "SELECT event_type,count(*) FROM work_events WHERE job_id=%s "
                "AND attempt_id=%s AND event_type IN "
                "('scheduler.claimed','attempt.failed','scheduler.withdrawn') "
                "GROUP BY event_type",
                (job["id"], attempt_id),
            ).fetchall()
        )
    assert event_counts == {"scheduler.claimed": 1}


def test_dispatch_retirement_and_selected_job_lock_in_canonical_order(
    admission_lock_context, monkeypatch
):
    """A dispatcher must not hold a retired job and then lock a lower selected job."""
    from contextlib import contextmanager
    from test.integration.test_work_scheduler_lock_order import _wait_until_blocked_by
    from threading import Event, current_thread

    context = admission_lock_context
    repository = context.repo
    low, high = sorted(range(2), key=lambda index: context.jobs[index][0]["id"])
    job_a, job_b = context.jobs[low][0]["id"], context.jobs[high][0]["id"]
    selected = admit(context, "canonical-dispatch-selected", job_index=low)
    retired = admit(context, "canonical-dispatch-retired", job_index=high)
    selected_attempt = selected["attempts"][-1]["id"]
    retired_attempt = retired["attempts"][-1]["id"]
    preview = context.service._preview()
    assert preview is not None and preview.attempt_id == selected_attempt
    context.authority.revoke(
        context.actor,
        grant_id=context.jobs[high][1].id,
        expected_grant_revision=1,
        reason="canonical dispatch lock-order regression",
    )

    retired_event = Event()
    allow_dispatch = Event()
    backend_pids = {}
    original_transaction = repository.transaction
    original_retire = context.service._retire_unexecutable

    @contextmanager
    def trace_transaction():
        with original_transaction() as connection:
            name = current_thread().name
            if name in {"dispatch", "claim"} and name not in backend_pids:
                backend_pids[name] = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
            yield connection

    def pausing_retire(connection, request):
        result = original_retire(connection, request)
        if current_thread().name == "dispatch":
            retired_event.set()
            assert allow_dispatch.wait(8), "claimer never waited on the dispatcher"
        return result

    monkeypatch.setattr(repository, "transaction", trace_transaction)
    monkeypatch.setattr(context.service, "_retire_unexecutable", pausing_retire)

    def run(name, call):
        current_thread().name = name
        try:
            return ("ok", call())
        except BaseException as error:  # retained for exact assertion below
            return ("error", error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        dispatch = pool.submit(run, "dispatch", lambda: context.service._commit_dispatch(preview))
        claim = None
        try:
            assert retired_event.wait(8), "dispatcher did not retire the revoked request"
            claim = pool.submit(
                run, "claim", lambda: context.scheduler.claim_next(actor_id="admission-lock-test")
            )
            _wait_until_blocked_by(
                repository.target, _backend_pid(backend_pids, "claim"), backend_pids["dispatch"]
            )
        finally:
            allow_dispatch.set()
        dispatch_outcome = dispatch.result(timeout=15)
        claim_outcome = claim.result(timeout=15)

    errors = [value for kind, value in (dispatch_outcome, claim_outcome) if kind == "error"]
    assert not errors, f"dispatcher/claimer failed: {errors!r}"
    assert claim_outcome == ("ok", None)
    assert context.scheduler.get_by_attempt(selected_attempt).state == "held"
    assert context.scheduler.get_by_attempt(retired_attempt).state == "withdrawn"
    assert repository.get_work(retired["id"])["attempts"][-1]["state"] == "failed"
    with repository.connection() as connection:
        event_counts = dict(
            connection.execute(
                "SELECT event_type,count(*) FROM work_events WHERE job_id IN (%s,%s) "
                "AND event_type IN ('scheduler.claimed','attempt.failed','scheduler.withdrawn') "
                "GROUP BY event_type",
                (job_a, job_b),
            ).fetchall()
        )
    assert event_counts == {"scheduler.claimed": 1, "attempt.failed": 1, "scheduler.withdrawn": 1}


def _backend_pid(backend_pids, name, timeout=8.0):
    deadline = time.monotonic() + timeout
    while name not in backend_pids:
        assert time.monotonic() < deadline, f"{name} never opened its transaction"
        time.sleep(0.01)
    return backend_pids[name]
