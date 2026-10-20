"""Regression coverage for Work job/attempt row-lock ordering."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from psycopg.errors import DeadlockDetected

pytestmark = pytest.mark.postgres

from test.clients.test_work_bubblewrap_setup_intent import _setup

from cli_agent_orchestrator.clients.postgres import Connection
from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope
from cli_agent_orchestrator.clients.work_repository import WorkConflict
from cli_agent_orchestrator.services.work_bubblewrap_setup_intent import (
    WorkBubblewrapSetupIntent,
)
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence


def test_transition_and_bubblewrap_release_use_one_job_attempt_lock_order(
    postgres_targets, tmp_path, monkeypatch
):
    """Reproduce the pre-fix attempt->event-FK-job / job->attempt cycle."""
    bootstrap_scope(postgres_targets.main)
    repository, _, setup = _setup(tmp_path)
    setup_intent = WorkBubblewrapSetupIntent(repository)
    setup_evidence = setup_intent.record_pre_go(**setup)

    job_lock_held = threading.Event()
    transition_reached_lock_or_event = threading.Event()
    release_attempt_lock_requested = threading.Event()
    role = threading.local()
    effects: list[str] = []
    release_revalidation_count = 0

    original_revalidate = setup_intent.contracts._revalidate_order

    def pause_release_after_authority(*args, **kwargs):
        nonlocal release_revalidation_count
        result = original_revalidate(*args, **kwargs)
        if getattr(role, "name", None) == "release":
            release_revalidation_count += 1
        if getattr(role, "name", None) == "release" and release_revalidation_count == 2:
            # _revalidate_order has locked the grant chain and its job row.
            job_lock_held.set()
            if not transition_reached_lock_or_event.wait(timeout=15):
                raise AssertionError("transition did not reach its lock boundary")
        return result

    monkeypatch.setattr(setup_intent.contracts, "_revalidate_order", pause_release_after_authority)

    original_execute = Connection.execute

    def observe_lock_requests(connection, statement, params=(), *, prepare: bool | None = None):
        normalized = " ".join(str(statement).lower().split())
        if (
            getattr(role, "name", None) == "transition"
            and "work_jobs" in normalized
            and "for update" in normalized
        ):
            # A job-first implementation blocks here until release commits.
            transition_reached_lock_or_event.set()
        if (
            getattr(role, "name", None) == "release"
            and normalized.startswith("select state,revision,lease_expires_at from work_attempts")
            and normalized.endswith("for update")
        ):
            release_attempt_lock_requested.set()
        if prepare is None:
            return original_execute(connection, statement, params)
        return original_execute(connection, statement, params, prepare=prepare)

    monkeypatch.setattr(Connection, "execute", observe_lock_requests)

    original_append_event = repository._append_event

    def observe_transition_event(connection, **kwargs):
        if getattr(role, "name", None) == "transition":
            # In the old implementation this is reached after UPDATE
            # work_attempts has locked the attempt row. In a job-first
            # implementation it is reached only after release commits.
            transition_reached_lock_or_event.set()
            if not release_attempt_lock_requested.wait(timeout=15):
                raise AssertionError("release did not reach its attempt lock")
        return original_append_event(connection, **kwargs)

    monkeypatch.setattr(repository, "_append_event", observe_transition_event)

    def run_with_role(name, operation):
        role.name = name
        try:
            return operation()
        except BaseException as error:  # Return worker errors for exact assertions below.
            return error

    transition_evidence = TransitionEvidence(
        generation=setup["generation"],
        expected_generation=setup["generation"],
        process_exit_code=1,
        process_stopped=True,
        container_removed=True,
        image_removed=True,
    )

    release_result = None
    transition_result = None
    transition_future = None
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="work-lock-order") as executor:
        release_future = executor.submit(
            run_with_role,
            "release",
            lambda: setup_intent.release_if_current(
                setup_evidence,
                expected_attempt_revision=setup["expected_attempt_revision"],
                release=lambda: effects.append("GO"),
            ),
        )
        try:
            assert job_lock_held.wait(timeout=15)
            transition_future = executor.submit(
                run_with_role,
                "transition",
                lambda: repository.transition_attempt(
                    attempt_id=setup["attempt_id"],
                    generation=setup["generation"],
                    expected_revision=setup["expected_attempt_revision"],
                    expected_state="sent",
                    target="failed",
                    actor_id="runtime",
                    event_id="lock-order-transition",
                    evidence=transition_evidence,
                ),
            )
            release_result = release_future.result(timeout=25)
            transition_result = transition_future.result(timeout=25)
        finally:
            # Let either worker unwind if an assertion or SQL error occurs.
            transition_reached_lock_or_event.set()
            release_attempt_lock_requested.set()
        if transition_future is not None:
            transition_result = transition_future.result(timeout=15)

    errors = [
        value for value in (release_result, transition_result) if isinstance(value, BaseException)
    ]
    assert not any(isinstance(error, DeadlockDetected) for error in errors), errors
    assert errors == []
    assert effects == ["GO"]
    assert transition_result["attempts"][-1]["state"] == "failed"
    with repository.connection() as connection:
        event_count = connection.execute(
            "SELECT count(*) FROM work_events WHERE event_id=%s",
            ("lock-order-transition",),
        ).fetchone()[0]
        receipt_count = connection.execute(
            "SELECT count(*) FROM work_transition_receipts WHERE event_id=%s",
            ("lock-order-transition",),
        ).fetchone()[0]
    assert (event_count, receipt_count) == (1, 1)


def _capture_job_lock_waits(monkeypatch, role, names):
    requested = {name: threading.Event() for name in names}
    backend_pids = {}
    original_execute = Connection.execute

    def observe_lock_request(connection, statement, params=(), *, prepare: bool | None = None):
        name = getattr(role, "name", None)
        normalized = " ".join(str(statement).lower().split())
        if name in requested and normalized == "select id from work_jobs where id=%s for update":
            backend_pids[name] = original_execute(connection, "SELECT pg_backend_pid()").fetchone()[
                0
            ]
            requested[name].set()
        if prepare is None:
            return original_execute(connection, statement, params)
        return original_execute(connection, statement, params, prepare=prepare)

    monkeypatch.setattr(Connection, "execute", observe_lock_request)
    return requested, backend_pids


def _wait_until_blocked(repository, *, worker_pid, holder_pid=None):
    deadline = time.monotonic() + 15
    with repository.connection() as connection:
        while time.monotonic() < deadline:
            blockers = connection.execute("SELECT pg_blocking_pids(%s)", (worker_pid,)).fetchone()[
                0
            ]
            if blockers and (holder_pid is None or holder_pid in blockers):
                return blockers
    raise AssertionError(f"backend {worker_pid} never blocked on holder {holder_pid}")


@pytest.mark.parametrize("stale_field", ["revision", "state"])
def test_transition_rejects_stale_expectation_after_waiting_for_job_lock(
    postgres_targets, tmp_path, monkeypatch, stale_field
):
    """A waiter validates the row version it sees after the parent lock is granted."""
    bootstrap_scope(postgres_targets.main)
    repository, _, setup = _setup(tmp_path)
    with repository.connection() as connection:
        identity = connection.execute(
            "SELECT work_items.id,work_items.job_id FROM work_attempts "
            "JOIN work_items ON work_items.id=work_attempts.work_item_id "
            "WHERE work_attempts.id=%s",
            (setup["attempt_id"],),
        ).fetchone()
    assert identity is not None

    role = threading.local()
    requested, backend_pids = _capture_job_lock_waits(monkeypatch, role, {"stale-transition"})
    holder_locked = threading.Event()
    release_holder = threading.Event()
    holder_pid = {}

    def hold_and_advance():
        role.name = "holder"
        with repository.transaction() as connection:
            repository._lock_job_for_write(connection, identity["job_id"])
            holder_pid["pid"] = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
            if stale_field == "revision":
                connection.execute(
                    "UPDATE work_attempts SET revision=revision+1 WHERE id=%s",
                    (setup["attempt_id"],),
                )
            else:
                connection.execute(
                    "UPDATE work_attempts SET state='running',revision=revision+1 WHERE id=%s",
                    (setup["attempt_id"],),
                )
                connection.execute(
                    "UPDATE work_items SET state='running',revision=revision+1 WHERE id=%s",
                    (identity["id"],),
                )
            holder_locked.set()
            if not release_holder.wait(timeout=15):
                raise AssertionError("test did not release the job-lock holder")

    expected_revision = setup["expected_attempt_revision"] + (1 if stale_field == "state" else 0)
    stale_event_id = f"stale-after-wait-{stale_field}"
    evidence = TransitionEvidence(
        generation=setup["generation"],
        expected_generation=setup["generation"],
        process_exit_code=1,
        process_stopped=True,
        container_removed=True,
        image_removed=True,
    )

    def stale_transition():
        role.name = "stale-transition"
        try:
            return repository.transition_attempt(
                attempt_id=setup["attempt_id"],
                generation=setup["generation"],
                expected_revision=expected_revision,
                expected_state="sent",
                target="failed",
                actor_id="runtime",
                event_id=stale_event_id,
                evidence=evidence,
            )
        except BaseException as error:
            return error

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="work-stale-lock") as executor:
        holder_future = executor.submit(hold_and_advance)
        assert holder_locked.wait(timeout=15)
        stale_future = executor.submit(stale_transition)
        try:
            assert requested["stale-transition"].wait(timeout=15)
            _wait_until_blocked(
                repository,
                worker_pid=backend_pids["stale-transition"],
                holder_pid=holder_pid["pid"],
            )
        finally:
            release_holder.set()
        holder_future.result(timeout=15)
        stale_result = stale_future.result(timeout=15)

    assert isinstance(stale_result, WorkConflict)
    with repository.connection() as connection:
        attempt = connection.execute(
            "SELECT state,revision FROM work_attempts WHERE id=%s", (setup["attempt_id"],)
        ).fetchone()
        event_count = connection.execute(
            "SELECT count(*) FROM work_events WHERE event_id=%s", (stale_event_id,)
        ).fetchone()[0]
        receipt_count = connection.execute(
            "SELECT count(*) FROM work_transition_receipts WHERE event_id=%s",
            (stale_event_id,),
        ).fetchone()[0]
    assert attempt["revision"] == 2
    assert attempt["state"] == ("sent" if stale_field == "revision" else "running")
    assert (event_count, receipt_count) == (0, 0)


def test_concurrent_same_transition_replays_one_receipt_after_job_lock_wait(
    postgres_targets, tmp_path, monkeypatch
):
    """A same-fingerprint waiter sees the winner's receipt after its job lock wait."""
    bootstrap_scope(postgres_targets.main)
    repository, _, setup = _setup(tmp_path)
    role = threading.local()
    requested, backend_pids = _capture_job_lock_waits(monkeypatch, role, {"transition-b"})
    winner_paused = threading.Event()
    release_winner = threading.Event()
    winner_pid = {}

    evidence = TransitionEvidence(
        generation=setup["generation"],
        expected_generation=setup["generation"],
        process_exit_code=1,
        process_stopped=True,
        container_removed=True,
        image_removed=True,
    )
    event_id = "same-transition-after-job-wait"

    original_append_event = repository._append_event

    def pause_winner_before_receipt(connection, **kwargs):
        if getattr(role, "name", None) == "transition-a" and kwargs.get("event_id") == event_id:
            winner_pid["pid"] = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
            winner_paused.set()
            if not release_winner.wait(timeout=15):
                raise AssertionError("test did not release the transition winner")
        return original_append_event(connection, **kwargs)

    monkeypatch.setattr(repository, "_append_event", pause_winner_before_receipt)

    def transition_as(name):
        role.name = name
        try:
            return repository.transition_attempt(
                attempt_id=setup["attempt_id"],
                generation=setup["generation"],
                expected_revision=setup["expected_attempt_revision"],
                expected_state="sent",
                target="failed",
                actor_id="runtime",
                event_id=event_id,
                evidence=evidence,
            )
        except BaseException as error:
            return error

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="work-receipt-lock") as executor:
        winner_future = executor.submit(transition_as, "transition-a")
        assert winner_paused.wait(timeout=15)
        replay_future = executor.submit(transition_as, "transition-b")
        try:
            assert requested["transition-b"].wait(timeout=15)
            _wait_until_blocked(
                repository,
                worker_pid=backend_pids["transition-b"],
                holder_pid=winner_pid["pid"],
            )
        finally:
            release_winner.set()
        winner_result = winner_future.result(timeout=20)
        replay_result = replay_future.result(timeout=20)

    assert not isinstance(winner_result, BaseException), winner_result
    assert not isinstance(replay_result, BaseException), replay_result
    assert winner_result["attempts"][-1]["state"] == "failed"
    assert replay_result["attempts"][-1]["state"] == "failed"
    assert winner_result["id"] == replay_result["id"]
    with repository.connection() as connection:
        event_count = connection.execute(
            "SELECT count(*) FROM work_events WHERE event_id=%s", (event_id,)
        ).fetchone()[0]
        receipt_count = connection.execute(
            "SELECT count(*) FROM work_transition_receipts WHERE event_id=%s", (event_id,)
        ).fetchone()[0]
    assert event_count == 1
    assert receipt_count == 1
