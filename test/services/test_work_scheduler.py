"""Durable admission accounting, fairness and stopped-writer capacity checks."""

import importlib
import multiprocessing
import sqlite3
import time

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence


def setup(tmp_path, *, capacity=2, max_queue=20, budgets=(10, 10), priorities=(0, 0)):
    module = importlib.import_module("cli_agent_orchestrator.services.work_scheduler")
    repo = WorkRepository(tmp_path / "scheduler.sqlite3")
    repo.initialize()
    scheduler = module.WorkScheduler(repo)
    scheduler.configure(
        capacity=capacity, max_queue=max_queue, aging_seconds=10, expected_policy_revision=0
    )
    jobs = [
        repo.create_job(
            project_id="p",
            principal_id="owner",
            allowed_providers=["mock_cli"],
            grant_id="g",
            budget={"scheduler_units": budget},
            priority=priority,
        )
        for budget, priority in zip(budgets, priorities)
    ]
    return module, repo, scheduler, jobs


def work(repo, job, key):
    return repo.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key=key,
        request_hash="a" * 64,
        contract_id="c",
        snapshot_id=None,
        provider="mock_cli",
        actor_id="owner",
        lease_seconds=3600,
    )


def enqueue(scheduler, item, *, units=1, dependencies=()):
    attempt = item["attempts"][-1]
    return scheduler.enqueue(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_attempt_revision=attempt["revision"],
        units=units,
        dependencies=dependencies,
        actor_id="owner",
    )


def test_enqueue_helper_revalidates_request_and_composes_claim_rollback(tmp_path):
    module, repo, scheduler, jobs = setup(tmp_path)
    item = work(repo, jobs[0], "a")
    arguments = dict(
        attempt_id=item["attempts"][0]["id"],
        generation=1,
        expected_attempt_revision=1,
        units=1,
        actor_id="owner",
    )
    before = repo.read_events(jobs[0]["id"])["high_water"]
    with repo.connection() as connection:
        with pytest.raises(ValueError):
            scheduler._enqueue(connection, **arguments)
    with pytest.raises(RuntimeError, match="rollback caller"):
        with repo.transaction() as connection:
            repo._verify(connection)
            with pytest.raises(module.SchedulerConflict):
                scheduler._enqueue(connection, **dict(arguments, units=True))
            queued = scheduler._enqueue(connection, **arguments)
            assert scheduler._claim_next(connection, actor_id="owner").id == queued.id
            assert connection.in_transaction
            raise RuntimeError("rollback caller")
    assert repo.read_events(jobs[0]["id"])["high_water"] == before
    with pytest.raises(module.SchedulerConflict):
        scheduler.get_by_attempt(item["attempts"][0]["id"])


def test_withdraw_helper_composes_failed_attempt_and_events_atomically(tmp_path):
    module, repo, scheduler, jobs = setup(tmp_path)
    item = work(repo, jobs[0], "denied")
    queued = enqueue(scheduler, item)
    attempt_id = item["attempts"][0]["id"]
    arguments = dict(
        generation=1, expected_revision=1, expected_attempt_revision=2, actor_id="owner"
    )
    before = repo.read_events(jobs[0]["id"])["high_water"]
    with repo.connection() as connection:
        with pytest.raises(ValueError):
            scheduler._withdraw_queued(connection, queued.id, **arguments)
    for rollback in (True, False):
        try:
            with repo.transaction() as connection:
                repo._verify(connection)
                repo._transition_attempt(
                    connection,
                    attempt_id=attempt_id,
                    generation=1,
                    expected_revision=1,
                    expected_state="planned",
                    target="failed",
                    actor_id="owner",
                    event_id="denied",
                    evidence=TransitionEvidence(generation=1, expected_generation=1),
                )
                with pytest.raises(module.SchedulerConflict):
                    scheduler._withdraw_queued(
                        connection, queued.id, **dict(arguments, generation=True)
                    )
                withdrawn = scheduler._withdraw_queued(connection, queued.id, **arguments)
                assert withdrawn.state == "withdrawn"
                assert connection.in_transaction
                if rollback:
                    raise RuntimeError("rollback caller")
        except RuntimeError as error:
            assert rollback and str(error) == "rollback caller"
        current = repo.get_work(item["id"])
        if rollback:
            assert current["state"] == "queued"
            assert current["attempts"][0]["state"] == "planned"
            assert scheduler.get_by_attempt(attempt_id).state == "queued"
            assert repo.read_events(jobs[0]["id"])["high_water"] == before
        else:
            assert current["state"] == "failed"
            assert current["attempts"][0]["state"] == "failed"
            assert scheduler.get_by_attempt(attempt_id).state == "withdrawn"
            assert (
                repo.read_events(jobs[0]["id"])["events"][-1]["event_type"] == "scheduler.withdrawn"
            )


def terminal(repo, item):
    attempt = repo.get_work(item["id"])["attempts"][-1]
    return repo.transition_attempt(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_revision=attempt["revision"],
        expected_state=attempt["state"],
        target="cancelled",
        actor_id="owner",
        event_id="cancel-" + attempt["id"],
        evidence=TransitionEvidence(generation=1, expected_generation=1),
    )


def test_capacity_budget_and_accounting_survive_new_instances(tmp_path):
    module, repo, scheduler, jobs = setup(tmp_path, budgets=(2, 10))
    items = [work(repo, jobs[0], str(i)) for i in range(3)]
    for item in items:
        enqueue(scheduler, item)
    assert scheduler.claim_next(actor_id="owner") is not None
    assert module.WorkScheduler(repo).claim_next(actor_id="owner") is not None
    assert scheduler.claim_next(actor_id="owner") is None
    first = scheduler.get_by_attempt(items[0]["attempts"][0]["id"])
    terminal(repo, items[0])
    stopped = module.WorkScheduler(
        repo,
        stop_verifier=lambda owner: module.StoppedWriter(
            owner.attempt_id, owner.generation, owner.revision, "backend-exit"
        ),
    )
    stopped.release(
        first.id, generation=1, expected_revision=2, expected_attempt_revision=2, actor_id="owner"
    )
    # Free capacity does not refund already admitted declared units.
    assert scheduler.claim_next(actor_id="owner") is None


def test_global_queue_bound_and_frozen_request_idempotency(tmp_path):
    module, repo, scheduler, jobs = setup(tmp_path, max_queue=1)
    item = work(repo, jobs[0], "a")
    first = enqueue(scheduler, item)
    assert enqueue(scheduler, item).id == first.id
    with pytest.raises(module.SchedulerConflict):
        enqueue(scheduler, item, units=2)
    with pytest.raises(module.SchedulerBackpressure):
        enqueue(scheduler, work(repo, jobs[1], "b"))


@pytest.mark.parametrize("budget", [None, True, -1, "2", 1.5])
def test_missing_or_noninteger_budget_never_authorizes(tmp_path, budget):
    module, repo, scheduler, jobs = setup(tmp_path, budgets=(budget,), priorities=(0,))
    with pytest.raises(module.SchedulerConflict):
        enqueue(scheduler, work(repo, jobs[0], "a"))


def test_round_robin_equal_priority_jobs_not_fifo_across_jobs(tmp_path):
    _, repo, scheduler, jobs = setup(tmp_path, capacity=4)
    for j, count in [(0, 3), (1, 2)]:
        for i in range(count):
            enqueue(scheduler, work(repo, jobs[j], str(i)))
    actual = [scheduler.claim_next(actor_id="owner").job_id for _ in range(4)]
    assert actual == [jobs[0]["id"], jobs[1]["id"], jobs[0]["id"], jobs[1]["id"]]


def test_aging_can_promote_old_lower_priority_work(tmp_path, monkeypatch):
    module, repo, scheduler, jobs = setup(tmp_path, priorities=(0, 5))
    old = enqueue(scheduler, work(repo, jobs[0], "old"))
    monkeypatch.setattr(module.time, "time", lambda: old.queued_at + 61)
    enqueue(scheduler, work(repo, jobs[1], "new"))
    assert scheduler.claim_next(actor_id="owner").job_id == jobs[0]["id"]


def test_transaction_local_eligibility_skips_ineligible_high_priority(tmp_path):
    _, repo, scheduler, jobs = setup(tmp_path, priorities=(0, 100))
    eligible = work(repo, jobs[0], "eligible")
    excluded = work(repo, jobs[1], "excluded")
    enqueue(scheduler, eligible)
    enqueue(scheduler, excluded)
    with repo.transaction() as connection:
        selected = scheduler._claim_next(
            connection,
            actor_id="owner",
            eligible_attempts=frozenset({eligible["attempts"][0]["id"]}),
        )
    assert selected.work_item_id == eligible["id"]
    assert scheduler.get_by_attempt(excluded["attempts"][0]["id"]).state == "queued"


def test_empty_eligibility_consumes_no_slot_turn_budget_or_event(tmp_path):
    _, repo, scheduler, jobs = setup(tmp_path)
    item = work(repo, jobs[0], "queued")
    enqueue(scheduler, item)
    events = repo.read_events(jobs[0]["id"])
    with repo.transaction() as connection:
        assert (
            scheduler._claim_next(connection, actor_id="owner", eligible_attempts=frozenset())
            is None
        )
        assert (
            connection.execute("SELECT dispatch_sequence FROM work_scheduler_policy").fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM work_scheduler_requests WHERE state IN ('held','released')"
            ).fetchone()[0]
            == 0
        )
    assert repo.read_events(jobs[0]["id"]) == events
    assert scheduler.get_by_attempt(item["attempts"][0]["id"]).state == "queued"


def test_transaction_local_eligibility_preserves_round_robin(tmp_path):
    _, repo, scheduler, jobs = setup(tmp_path, capacity=4)
    eligible = []
    for job in jobs:
        enqueue(scheduler, work(repo, job, "excluded"))
        for number in range(2):
            item = work(repo, job, f"eligible-{number}")
            enqueue(scheduler, item)
            eligible.append(item["attempts"][0]["id"])
    with repo.transaction() as connection:
        actual = [
            scheduler._claim_next(
                connection, actor_id="owner", eligible_attempts=frozenset(eligible)
            ).job_id
            for _ in range(4)
        ]
    assert actual == [jobs[0]["id"], jobs[1]["id"], jobs[0]["id"], jobs[1]["id"]]


@pytest.mark.parametrize(
    "eligibility",
    [
        set(),
        [],
        "attempt",
        True,
        frozenset({True}),
        frozenset({""}),
        frozenset({" "}),
        frozenset({"a" * 513}),
    ],
)
def test_eligibility_requires_frozen_set_of_bounded_string_ids(tmp_path, eligibility):
    module, repo, scheduler, jobs = setup(tmp_path)
    with repo.transaction() as connection:
        with pytest.raises(module.SchedulerConflict):
            scheduler._claim_next(connection, actor_id="owner", eligible_attempts=eligibility)


def test_internal_claim_requires_active_transaction(tmp_path):
    module, repo, scheduler, jobs = setup(tmp_path)
    enqueue(scheduler, work(repo, jobs[0], "one"))
    with repo.connection() as connection:
        with pytest.raises(module.SchedulerConflict, match="transaction"):
            scheduler._claim_next(connection, actor_id="owner")


def test_continuous_new_jobs_cannot_starve_previously_served_waiting_job(tmp_path):
    module, repo, scheduler, jobs = setup(
        tmp_path, capacity=1, max_queue=2, budgets=(20,), priorities=(0,)
    )
    stopped = module.WorkScheduler(
        repo,
        stop_verifier=lambda owner: module.StoppedWriter(
            owner.attempt_id, owner.generation, owner.revision, "backend-exit"
        ),
    )
    first = work(repo, jobs[0], "first")
    enqueue(scheduler, first)
    held = scheduler.claim_next(actor_id="owner")
    terminal(repo, first)
    stopped.release(
        held.id, generation=1, expected_revision=2, expected_attempt_revision=2, actor_id="owner"
    )
    waiting = work(repo, jobs[0], "waiting")
    enqueue(scheduler, waiting)
    for i in range(8):
        newcomer = repo.create_job(
            project_id="p",
            principal_id="owner",
            allowed_providers=["mock_cli"],
            grant_id="g",
            budget={"scheduler_units": 1},
            priority=0,
        )
        new_work = work(repo, newcomer, str(i))
        enqueue(scheduler, new_work)
        # Re-instantiation proves fairness uses durable state, not process memory.
        selected = module.WorkScheduler(repo).claim_next(actor_id="owner")
        if selected.work_item_id == waiting["id"]:
            break
        terminal(repo, new_work)
        stopped.release(
            selected.id,
            generation=1,
            expected_revision=2,
            expected_attempt_revision=2,
            actor_id="owner",
        )
    assert selected.work_item_id == waiting["id"], "new arrivals indefinitely overtake waiting job"


def test_enqueue_frontier_is_frozen_in_durable_request(tmp_path):
    _, repo, scheduler, jobs = setup(tmp_path)
    item = enqueue(scheduler, work(repo, jobs[0], "one"))
    with repo.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="frontier"):
            connection.execute(
                "UPDATE work_scheduler_requests SET enqueue_frontier=99 WHERE id=?", (item.id,)
            )


def test_dependencies_same_job_cycle_detection_and_readiness(tmp_path):
    module, repo, scheduler, jobs = setup(tmp_path)
    a, b = work(repo, jobs[0], "a"), work(repo, jobs[0], "b")
    enqueue(scheduler, a, dependencies=(b["id"],))
    with pytest.raises(module.DependencyCycle) as error:
        enqueue(scheduler, b, dependencies=(a["id"],))
    assert set(error.value.participants) == {a["id"], b["id"]}
    with pytest.raises(module.SchedulerConflict):
        enqueue(scheduler, work(repo, jobs[1], "foreign"), dependencies=(b["id"],))
    assert scheduler.claim_next(actor_id="owner") is None
    # Fixture models an already accepted prerequisite; scheduler never authors this state.
    with repo.transaction() as connection:
        connection.execute("UPDATE work_items SET state='succeeded' WHERE id=?", (b["id"],))
    assert scheduler.claim_next(actor_id="owner").work_item_id == a["id"]


def test_policy_compare_swap_and_reduction_checks(tmp_path):
    module, repo, scheduler, jobs = setup(tmp_path)
    for i in range(3):
        enqueue(scheduler, work(repo, jobs[0], str(i)))
    for _ in range(2):
        scheduler.claim_next(actor_id="owner")
    for overrides in ({"expected_policy_revision": 0}, {"capacity": 1}, {"max_queue": 0}):
        args = dict(capacity=2, max_queue=20, aging_seconds=10, expected_policy_revision=1)
        args.update(overrides)
        with pytest.raises(module.SchedulerConflict):
            scheduler.configure(**args)


def test_expired_or_terminal_attempt_keeps_slot_until_server_proof(tmp_path, monkeypatch):
    module, repo, scheduler, jobs = setup(tmp_path, capacity=1)
    item = work(repo, jobs[0], "a")
    enqueue(scheduler, item)
    held = scheduler.claim_next(actor_id="owner")
    enqueue(scheduler, work(repo, jobs[1], "b"))
    terminal(repo, item)
    with pytest.raises(module.SchedulerConflict):
        scheduler.release(
            held.id,
            generation=1,
            expected_revision=2,
            expected_attempt_revision=2,
            actor_id="owner",
        )
    monkeypatch.setattr(module.time, "time", lambda: held.expires_at + 1)
    assert scheduler.claim_next(actor_id="owner") is None
    assert scheduler.get_by_attempt(item["attempts"][0]["id"]).state == "held"


def test_failed_claim_event_rolls_back_capacity_and_budget(tmp_path):
    _, repo, scheduler, jobs = setup(tmp_path)
    item = work(repo, jobs[0], "a")
    enqueue(scheduler, item)
    with repo.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER reject_claim BEFORE INSERT ON work_events "
            "WHEN NEW.event_type='scheduler.claimed' BEGIN SELECT RAISE(ABORT,'test'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        scheduler.claim_next(actor_id="owner")
    assert scheduler.get_by_attempt(item["attempts"][0]["id"]).state == "queued"


def test_claim_helper_rolls_back_with_its_callers_transaction(tmp_path):
    _, repo, scheduler, jobs = setup(tmp_path)
    item = work(repo, jobs[0], "a")
    enqueue(scheduler, item)
    before = repo.read_events(jobs[0]["id"])["high_water"]
    with pytest.raises(RuntimeError):
        with repo.transaction() as connection:
            repo._verify(connection)
            assert scheduler._claim_next(connection, actor_id="owner") is not None
            raise RuntimeError("later path reservation failed")
    assert scheduler.get_by_attempt(item["attempts"][0]["id"]).state == "queued"
    assert repo.read_events(jobs[0]["id"])["high_water"] == before


def test_current_attempt_and_reservation_fences_are_both_required(tmp_path):
    module, repo, scheduler, jobs = setup(tmp_path)
    item = work(repo, jobs[0], "a")
    enqueue(scheduler, item)
    held = scheduler.claim_next(actor_id="owner")
    assert (
        scheduler.assert_held(
            held.id, generation=1, expected_revision=2, expected_attempt_revision=1
        ).state
        == "held"
    )
    for change in ({"generation": 2}, {"expected_revision": 1}, {"expected_attempt_revision": 2}):
        arguments = dict(generation=1, expected_revision=2, expected_attempt_revision=1)
        arguments.update(change)
        with pytest.raises(module.SchedulerConflict):
            scheduler.assert_held(held.id, **arguments)
    terminal(repo, item)
    with pytest.raises(module.SchedulerConflict):
        scheduler.assert_held(
            held.id, generation=1, expected_revision=2, expected_attempt_revision=2
        )


def test_release_rechecks_revision_after_stop_verification(tmp_path):
    module, repo, scheduler, jobs = setup(tmp_path)
    item = work(repo, jobs[0], "a")
    enqueue(scheduler, item)
    held = scheduler.claim_next(actor_id="owner")
    terminal(repo, item)

    def verify(owner):
        with repo.transaction() as connection:
            connection.execute(
                "UPDATE work_attempts SET revision=revision+1 WHERE id=?", (owner.attempt_id,)
            )
        return module.StoppedWriter(owner.attempt_id, owner.generation, owner.revision, "exit")

    with pytest.raises(module.SchedulerConflict):
        module.WorkScheduler(repo, stop_verifier=verify).release(
            held.id,
            generation=1,
            expected_revision=2,
            expected_attempt_revision=2,
            actor_id="owner",
        )
    assert scheduler.get_by_attempt(item["attempts"][0]["id"]).state == "held"


def test_existing_request_cannot_change_its_dependency_set(tmp_path):
    module, repo, scheduler, jobs = setup(tmp_path)
    a, b = work(repo, jobs[0], "a"), work(repo, jobs[0], "b")
    enqueue(scheduler, a, dependencies=(b["id"],))
    with pytest.raises(module.SchedulerConflict):
        enqueue(scheduler, a)


def test_lowering_nonzero_queue_limit_below_occupancy_is_rejected(tmp_path):
    module, repo, scheduler, jobs = setup(tmp_path)
    for i in range(2):
        enqueue(scheduler, work(repo, jobs[0], str(i)))
    with pytest.raises(module.SchedulerConflict):
        scheduler.configure(capacity=2, max_queue=1, aging_seconds=10, expected_policy_revision=1)


def test_self_dependency_reports_cycle_without_writing_request(tmp_path):
    module, repo, scheduler, jobs = setup(tmp_path)
    item = work(repo, jobs[0], "a")
    with pytest.raises(module.DependencyCycle) as error:
        enqueue(scheduler, item, dependencies=(item["id"],))
    assert error.value.participants == (item["id"],)
    with pytest.raises(module.SchedulerConflict):
        scheduler.get_by_attempt(item["attempts"][0]["id"])


def test_cancelled_queued_request_drains_backpressure_without_refunding_held(tmp_path):
    module, repo, scheduler, jobs = setup(tmp_path, capacity=1, max_queue=1)
    holder = work(repo, jobs[0], "holder")
    enqueue(scheduler, holder)
    held = scheduler.claim_next(actor_id="owner")
    item = work(repo, jobs[0], "queued")
    queued = enqueue(scheduler, item)
    with pytest.raises(module.SchedulerConflict):
        scheduler.withdraw_queued(
            queued.id,
            generation=1,
            expected_revision=1,
            expected_attempt_revision=1,
            actor_id="owner",
        )
    terminal(repo, item)
    withdrawn = scheduler.withdraw_queued(
        queued.id, generation=1, expected_revision=1, expected_attempt_revision=2, actor_id="owner"
    )
    assert withdrawn.state == "withdrawn" and withdrawn.revision == 2
    enqueue(scheduler, work(repo, jobs[1], "replacement"))
    assert scheduler.claim_next(actor_id="owner") is None
    assert scheduler.get_by_attempt(holder["attempts"][0]["id"]).state == "held"
    assert repo.read_events(jobs[0]["id"])["events"][-1]["event_type"] == "scheduler.withdrawn"
    with pytest.raises(sqlite3.IntegrityError):
        with repo.transaction() as connection:
            connection.execute(
                "UPDATE work_scheduler_requests SET revision=3 WHERE id=?", (queued.id,)
            )
    terminal(repo, holder)
    with pytest.raises(module.SchedulerConflict):
        scheduler.withdraw_queued(
            held.id,
            generation=1,
            expected_revision=2,
            expected_attempt_revision=2,
            actor_id="owner",
        )


def _claim_process(path, barrier, output):
    module = importlib.import_module("cli_agent_orchestrator.services.work_scheduler")
    try:
        barrier.wait(15)
        value = module.WorkScheduler(WorkRepository(path)).claim_next(actor_id="owner")
        output.put("held" if value else "waiting")
    except BaseException as error:
        output.put(repr(error))


def _wait_external(stop, ready):
    ready.set()
    stop.wait(20)


def test_live_external_process_prevents_slot_reassignment_after_terminal_state(tmp_path):
    module, repo, scheduler, jobs = setup(tmp_path, capacity=1)
    item = work(repo, jobs[0], "a")
    enqueue(scheduler, item)
    held = scheduler.claim_next(actor_id="owner")
    enqueue(scheduler, work(repo, jobs[1], "b"))
    context = multiprocessing.get_context("fork")
    stop, ready = context.Event(), context.Event()
    process = context.Process(target=_wait_external, args=(stop, ready))
    process.start()
    try:
        assert ready.wait(10)
        terminal(repo, item)

        def stopped(owner):
            if process.is_alive():
                return None
            return module.StoppedWriter(
                owner.attempt_id, owner.generation, owner.revision, "process-exited"
            )

        manager = module.WorkScheduler(repo, stop_verifier=stopped)
        args = dict(
            generation=1, expected_revision=2, expected_attempt_revision=2, actor_id="owner"
        )
        with pytest.raises(module.SchedulerConflict):
            manager.release(held.id, **args)
        assert manager.claim_next(actor_id="owner") is None
        stop.set()
        process.join(10)
        assert process.exitcode == 0
        assert manager.release(held.id, **args).state == "released"
        assert manager.claim_next(actor_id="owner").job_id == jobs[1]["id"]
    finally:
        stop.set()
        if process.is_alive():
            process.terminate()
            process.join(5)


def test_ten_processes_never_exceed_two_slots(tmp_path):
    _, repo, scheduler, jobs = setup(tmp_path)
    for i in range(10):
        enqueue(scheduler, work(repo, jobs[i % 2], str(i)))
    ctx = multiprocessing.get_context("fork")
    barrier, output = ctx.Barrier(10), ctx.Queue()
    processes = [
        ctx.Process(target=_claim_process, args=(repo.path, barrier, output)) for _ in range(10)
    ]
    for process in processes:
        process.start()
    try:
        outcomes = [output.get(timeout=25) for _ in processes]
        assert sorted(outcomes) == ["held"] * 2 + ["waiting"] * 8
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
