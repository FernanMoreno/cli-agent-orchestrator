"""Atomic dispatch across real SQLite capacity, paths, authority and external intent.

The backend below is a test-only transport: no tmux process or provider CLI runs.
"""

from concurrent.futures import ThreadPoolExecutor
import asyncio
import hashlib
import multiprocessing
import sqlite3
from threading import Barrier, Event, Lock
import time
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.clients.work_repository import WorkRepository
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
from cli_agent_orchestrator.services.work_admission import WorkAdmission
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning
from cli_agent_orchestrator.services.work_scheduler import WorkScheduler
from cli_agent_orchestrator.services.work_service import (
    DeliveryObservation,
    DeliveryUncertain,
)


class AdmissionOnlyBackend(TmuxBackend):
    """Exercise protected-port ordering, not a claim of real process isolation."""

    def __init__(self):
        self.preflight_hook = lambda: None
        self.restrictions = []
        self.effects = []
        self.lock = Lock()

    def preflight_work(self, contract):
        with self.lock:
            self.restrictions.append(contract)
        self.preflight_hook()

    def send_keys(self, session_name, window_name, text, **kwargs):
        with self.lock:
            self.effects.append((session_name, window_name, text))

    def create_session(self, *args, **kwargs):
        raise AssertionError("dispatch fixture must never create a CLI process")


@pytest.fixture
def context(tmp_path):
    # Production stores Work attempts beside terminal metadata. Share the
    # per-test SQLite selected by isolated_memory_db so target guards exercise
    # the same durable registry instead of an incomplete second database.
    repository = WorkRepository(constants.DATABASE_FILE)
    repository.initialize()
    WorkScheduler(repository).configure(
        capacity=2, max_queue=100, aging_seconds=10, expected_policy_revision=0
    )
    actor = auth._verified_principal("issuer", "dispatcher-owner", [auth.SCOPE_ADMIN], "jwt")
    control = WorkAuthority(repository)
    jobs = []
    for name in ("first", "second"):
        job = repository.create_job(
            project_id="project",
            principal_id=actor.id,
            allowed_providers=["mock_cli"],
            grant_id=name,
            budget={"scheduler_units": 100},
        )
        grant = control.issue_root(
            actor,
            job_id=job["id"],
            providers={"mock_cli"},
            permissions=Permissions(tools={"knowledge.read"}, paths={str(tmp_path)}),
            expires_at=time.time() + 600,
        )
        jobs.append((job, grant))
    backend = AdmissionOnlyBackend()
    service = WorkAdmission(repository, backends={"test": backend})
    return SimpleNamespace(
        root=tmp_path,
        repo=repository,
        actor=actor,
        control=control,
        jobs=jobs,
        backend=backend,
        service=service,
        contracts={},
        launch_origins={},
    )


def admit(
    context,
    key,
    *,
    job_index=0,
    path=None,
    operation="inbox",
    delivery=None,
    snapshot_content=None,
    snapshot_max_content_bytes=65536,
):
    if delivery is not None and operation == "inbox" and delivery.operation_kind == "launch":
        operation = "launch"
    job, grant = context.jobs[job_index]
    snapshot = DelegationSnapshots(
        context.repo,
        policy=KnowledgePolicy(context.repo, job["id"], grant.id, 1),
        max_content_bytes=snapshot_max_content_bytes,
    ).freeze(
        principal=context.actor,
        job_id=job["id"],
        contract_id=f"contract-{key}",
        binding_key=f"snapshot-{key}",
        request_hash=hashlib.sha256(key.encode()).hexdigest(),
        scope="project",
        scope_id="project",
        resolver=lambda connection, principal: ResolvedSnapshot(
            f"frozen context {key}" if snapshot_content is None else snapshot_content
        ),
    )
    contract = EffectiveWorkContract(
        id=f"contract-{key}",
        operation_kind=operation,
        provider="mock_cli",
        backend="test",
        permissions=ContractPermissions(paths=(str(context.root),)),
        resources=ContractResources(
            checkout_root=str(context.root), write_paths=(str(path or context.root / key),), units=1
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )
    delivery_args = {} if delivery is None else {"delivery": delivery}
    origin_args = {}
    if operation == "launch":
        origin_key = (job_index, key)
        origin = context.launch_origins.get(origin_key)
        if origin is None:
            provisioner = WorkProvisioning(context.repo)
            reference = provisioner.provision_launch(
                context.actor,
                subject=context.actor,
                selector=hashlib.sha256(f"dispatch-launch:{job['id']}:{key}".encode()).hexdigest(),
                expected_revision=0,
                job_id=job["id"],
                grant_id=grant.id,
                grant_revision=1,
                contract=contract,
                adapter_version=delivery.adapter_version,
                lease_seconds=300,
            )
            with context.repo.read_snapshot() as connection:
                origin = provisioner._resolve_launch(
                    connection,
                    context.actor,
                    reference.selector,
                    expected_ref=reference,
                )
            context.launch_origins[origin_key] = origin
        origin_args = {
            "launch_origin": origin,
            "launch_fingerprint": origin.fingerprint(),
        }
    work = context.service.admit(
        principal=context.actor,
        job_id=job["id"],
        idempotency_key=key,
        request_hash=hashlib.sha256(("request-" + key).encode()).hexdigest(),
        grant_id=grant.id,
        expected_grant_revision=1,
        contract=contract,
        lease_seconds=300,
        **delivery_args,
        **origin_args,
    )
    context.contracts[work["id"]] = contract
    return work


def accounting(repository):
    with repository.connection() as connection:
        return {
            "held": connection.execute(
                "SELECT count(*) FROM work_scheduler_requests WHERE state='held'"
            ).fetchone()[0],
            "spent": connection.execute(
                "SELECT coalesce(sum(units),0) FROM work_scheduler_requests WHERE state IN ('held','released')"
            ).fetchone()[0],
            "paths": connection.execute(
                "SELECT count(*) FROM work_reservation_sets WHERE state='active'"
            ).fetchone()[0],
            "queued": connection.execute(
                "SELECT count(*) FROM work_scheduler_requests WHERE state='queued'"
            ).fetchone()[0],
            "withdrawn": connection.execute(
                "SELECT count(*) FROM work_scheduler_requests WHERE state='withdrawn'"
            ).fetchone()[0],
            "sequence": connection.execute(
                "SELECT dispatch_sequence FROM work_scheduler_policy"
            ).fetchone()[0],
        }


def deliver(binding, port):
    # These scheduler tests have no durable terminal delivery identity. The
    # callback records a successful abstract observation; it does not synthesize
    # a backend target from the work item ID.
    return DeliveryObservation(task_received=True, execution_started=True)


def test_ten_threads_across_two_jobs_never_send_a_third_item(context):
    for job_index in range(2):
        for number in range(5):
            admit(context, f"job{job_index}-item{number}", job_index=job_index)
    observed, lock, start = [], Lock(), Barrier(10)

    def send(binding, port):
        state = accounting(context.repo)
        assert 1 <= state["held"] <= 2
        assert state["paths"] == state["held"]
        assert binding.contract == context.contracts[binding.work_item_id]
        with lock:
            observed.append((binding.work_item_id, binding.job_id))
        return deliver(binding, port)

    def contender(_):
        start.wait(timeout=30)
        return context.service.dispatch_next(send)

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(contender, range(10)))
    assert sum(result is not None for result in results) == 2
    assert len(observed) == 2
    assert context.backend.effects == []
    assert len({job for _, job in observed}) == 2
    assert accounting(context.repo) == {
        "held": 2,
        "spent": 2,
        "paths": 2,
        "queued": 8,
        "withdrawn": 0,
        "sequence": 2,
    }


def test_conflicting_path_does_not_block_an_eligible_item_behind_it(context):
    shared = context.root / "shared"
    first = admit(context, "first", path=shared)
    blocked = admit(context, "blocked", path=shared)
    eligible = admit(context, "eligible", path=context.root / "independent")
    assert context.service.dispatch_next(deliver)["id"] == first["id"]
    assert context.service.dispatch_next(deliver)["id"] == eligible["id"]
    assert context.repo.get_work(blocked["id"])["attempts"][0]["state"] == "planned"
    assert context.backend.effects == []
    assert accounting(context.repo) == {
        "held": 2,
        "spent": 2,
        "paths": 2,
        "queued": 1,
        "withdrawn": 0,
        "sequence": 2,
    }


def test_retargeted_path_is_rejected_without_poisoning_other_queued_work(context, tmp_path):
    bad_path = context.root / "retargeted"
    bad = admit(context, "bad-path", path=bad_path)
    good = admit(context, "good-path", path=context.root / "independent-good")
    outside = tmp_path / "outside-checkout"
    outside.mkdir()
    bad_path.symlink_to(outside, target_is_directory=True)
    result = context.service.dispatch_next(deliver)
    assert result["id"] == good["id"]
    assert context.repo.get_work(bad["id"])["state"] == "failed"
    assert context.backend.effects == []
    assert accounting(context.repo) == {
        "held": 1,
        "spent": 1,
        "paths": 1,
        "queued": 0,
        "withdrawn": 1,
        "sequence": 1,
    }


def test_revocation_in_dispatch_preflight_withdraws_without_debit_or_effect(context):
    work = admit(context, "revoked")

    def revoke():
        context.backend.preflight_hook = lambda: None
        context.control.revoke(
            context.actor,
            grant_id=context.jobs[0][1].id,
            expected_grant_revision=1,
            reason="dispatch preflight revocation",
        )

    context.backend.preflight_hook = revoke
    assert context.service.dispatch_next(deliver) is None
    assert context.backend.effects == []
    assert accounting(context.repo) == {
        "held": 0,
        "spent": 0,
        "paths": 0,
        "queued": 0,
        "withdrawn": 1,
        "sequence": 0,
    }
    assert context.repo.get_work(work["id"])["attempts"][0]["state"] != "sent"


def test_unregistered_bubblewrap_rejection_precedes_dispatch_adapter_guard_and_effect(context):
    from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement
    from cli_agent_orchestrator.backends.bubblewrap_backend import BubblewrapWorkBackend

    class RecordingClient:
        def __init__(self):
            self.effects = []

        def __getattr__(self, name):
            def record(*args, **kwargs):
                self.effects.append((name, args, kwargs))

            return record

    fake_bwrap = context.root / "bwrap"
    fake_bwrap.write_text("#!/bin/sh\nprintf 'bubblewrap 0.13.0\\n'\n", encoding="utf-8")
    fake_bwrap.chmod(0o700)
    client = RecordingClient()
    work = admit(context, "bubblewrap-preflight")
    before = accounting(context.repo)
    context.service = WorkAdmission(
        context.repo,
        backends={
            "test": BubblewrapWorkBackend(
                client=client,
                bwrap_executable=fake_bwrap,
            )
        },
    )
    adapter_calls = []
    guard_calls = []
    original_register = context.repo._register_writer_effect

    def record_guard(connection, *, writer_id):
        guard_calls.append(writer_id)
        return original_register(connection, writer_id=writer_id)

    context.repo._register_writer_effect = record_guard

    def adapter(binding, port):
        adapter_calls.append(binding.attempt_id)
        port.send_keys("session", binding.work_item_id, "unexpected")
        return DeliveryObservation(task_received=True, execution_started=True)

    with pytest.raises(UnsupportedWorkEnforcement, match="no command contract"):
        context.service.dispatch_next(adapter)

    assert adapter_calls == []
    assert guard_calls == []
    assert client.effects == []
    current = context.repo.get_work(work["id"])
    assert current["state"] == "queued"
    assert current["attempts"][0]["state"] == "planned"
    assert "attempt.sent" not in [
        event["event_type"] for event in context.repo.read_events(work["job_id"])["events"]
    ]
    assert accounting(context.repo) == before == {
        "held": 0,
        "spent": 0,
        "paths": 0,
        "queued": 1,
        "withdrawn": 0,
        "sequence": 0,
    }


def test_crash_after_committed_intent_keeps_reservations_without_restart_resend(context):
    work = admit(context, "crash")

    def crash(_binding, _port):
        raise SystemExit("simulated dispatcher death")

    with pytest.raises(SystemExit):
        context.service.dispatch_next(crash)
    assert context.repo.get_work(work["id"])["attempts"][0]["state"] == "sent"
    state = accounting(context.repo)
    assert (state["held"], state["paths"], state["spent"]) == (1, 1, 1)
    restarted = WorkAdmission(WorkRepository(context.repo.path), backends={"test": context.backend})
    assert restarted.dispatch_next(deliver) is None
    assert context.backend.effects == []


def test_send_callback_can_commit_a_separate_sqlite_writer(context):
    work = admit(context, "writer")

    def send(binding, port):
        with sqlite3.connect(context.repo.path, timeout=0.2) as connection:
            connection.execute("BEGIN IMMEDIATE")
            assert (
                connection.execute(
                    "SELECT state FROM work_attempts WHERE id=?", (binding.attempt_id,)
                ).fetchone()[0]
                == "sent"
            )
            connection.execute(
                "UPDATE work_jobs SET priority=priority WHERE id=?", (binding.job_id,)
            )
        return deliver(binding, port)

    result = context.service.dispatch_next(send)
    assert result["attempts"][0]["state"] == "sent"
    assert "attempt.acknowledged" not in [
        event["event_type"] for event in context.repo.read_events(work["job_id"])["events"]
    ]
    assert context.service.dispatch_next(deliver) is None
    assert context.backend.effects == []


def test_send_exception_reconciles_and_never_frees_uncertain_ownership(context):
    work = admit(context, "timeout")

    def uncertain(binding, port):
        raise TimeoutError("acknowledgement unavailable")

    with pytest.raises(DeliveryUncertain):
        context.service.dispatch_next(uncertain)
    assert context.repo.get_work(work["id"])["state"] == "reconcile"
    state = accounting(context.repo)
    assert (state["held"], state["paths"], state["spent"]) == (1, 1, 1)
    assert context.service.dispatch_next(deliver) is None
    assert context.backend.effects == []


def test_repeated_dispatch_does_not_duplicate_active_work(context):
    work = admit(context, "once")
    first = context.service.dispatch_next(deliver)
    assert first["id"] == work["id"]
    assert first["attempts"][0]["state"] == "sent"
    assert context.service.dispatch_next(deliver) is None
    assert context.service.dispatch_next(deliver) is None
    assert context.backend.effects == []


def test_captured_effect_port_cannot_send_after_delivery_callback_returns(context):
    admit(context, "captured-port")
    ports = []

    def send(binding, port):
        ports.append(port)
        return DeliveryObservation()  # Still sent, not safe to repeat.

    context.service.dispatch_next(send)
    with pytest.raises(ValueError):
        ports[0].send_keys("session", "late", "duplicate delivery")
    assert context.backend.effects == []


def test_intent_persistence_failure_rolls_back_claim_paths_and_events(context):
    work = admit(context, "rollback")
    before = context.repo.read_events(work["job_id"])
    with context.repo.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER reject_sent BEFORE UPDATE OF state ON work_attempts "
            "WHEN NEW.state='sent' BEGIN SELECT RAISE(ABORT,'intent unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="intent unavailable"):
        context.service.dispatch_next(deliver)
    assert context.backend.effects == []
    assert context.repo.get_work(work["id"])["state"] == "queued"
    assert accounting(context.repo) == {
        "held": 0,
        "spent": 0,
        "paths": 0,
        "queued": 1,
        "withdrawn": 0,
        "sequence": 0,
    }
    assert context.repo.read_events(work["job_id"]) == before


def test_revocation_in_protected_backend_preflight_blocks_the_final_effect(context):
    work = admit(context, "last-guard")
    calls = 0

    def preflight():
        nonlocal calls
        calls += 1
        if calls == 2:  # After committed intent, inside protected send_keys.
            context.control.revoke(
                context.actor,
                grant_id=context.jobs[0][1].id,
                expected_grant_revision=1,
                reason="last preflight revocation",
            )

    context.backend.preflight_hook = preflight

    def send(binding, port):
        port.send_keys("session", binding.work_item_id, "task")
        return DeliveryObservation(task_received=True)

    with pytest.raises(DeliveryUncertain):
        context.service.dispatch_next(send)
    assert calls == 2 and context.backend.effects == []
    assert context.repo.get_work(work["id"])["state"] == "reconcile"
    # The already-committed intent does not silently return ownership to the pool.
    assert accounting(context.repo)["held"] == accounting(context.repo)["paths"] == 1


def _process_dispatch(database, start, results):
    backend = AdmissionOnlyBackend()
    repository = WorkRepository(database)
    service = WorkAdmission(repository, backends={"test": backend})
    held_at_send = []

    def send(binding, port):
        held_at_send.append(accounting(repository)["held"])
        return deliver(binding, port)

    try:
        if not start.wait(timeout=30):
            raise TimeoutError("process start barrier expired")
        result = service.dispatch_next(send)
        results.put(("ok", result["id"] if result else None, len(backend.effects), held_at_send))
    except BaseException as error:
        results.put(("error", type(error).__name__, str(error)))


def test_ten_processes_share_two_durable_slots_without_duplicate_send(context):
    for job_index in range(2):
        for number in range(5):
            admit(context, f"process-job{job_index}-item{number}", job_index=job_index)
    fork = multiprocessing.get_context("fork")
    start, results = fork.Event(), fork.Queue()
    workers = [
        fork.Process(target=_process_dispatch, args=(context.repo.path, start, results))
        for _ in range(10)
    ]
    try:
        for worker in workers:
            worker.start()
        start.set()
        outcomes = [results.get(timeout=45) for _ in workers]
        for worker in workers:
            worker.join(timeout=10)
        assert all(row[0] == "ok" for row in outcomes), outcomes
        assert all(worker.exitcode == 0 for worker in workers)
        assert sum(row[1] is not None for row in outcomes) == 2
        assert sum(row[2] for row in outcomes) == 0
        assert all(1 <= held <= 2 for row in outcomes for held in row[3])
        assert accounting(context.repo) == {
            "held": 2,
            "spent": 2,
            "paths": 2,
            "queued": 8,
            "withdrawn": 0,
            "sequence": 2,
        }
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            if worker.pid is not None:
                worker.join(timeout=5)
        results.close()
        results.join_thread()


@pytest.mark.asyncio
async def test_async_dispatch_runs_delivery_on_callers_loop_after_durable_intent(context):
    work = admit(context, "async-launch")
    loop = asyncio.get_running_loop()

    async def send(binding, port):
        assert asyncio.get_running_loop() is loop
        await asyncio.sleep(0)
        with sqlite3.connect(context.repo.path, timeout=0.2) as connection:
            connection.execute("BEGIN IMMEDIATE")
            assert (
                connection.execute(
                    "SELECT state FROM work_attempts WHERE id=?", (binding.attempt_id,)
                ).fetchone()[0]
                == "sent"
            )
        assert binding.contract.snapshot == context.contracts[work["id"]].snapshot
        return deliver(binding, port)

    result = await context.service.dispatch_next_async(send)
    assert result["id"] == work["id"]
    assert result["attempts"][0]["state"] == "sent"
    assert "attempt.acknowledged" not in [
        event["event_type"] for event in context.repo.read_events(work["job_id"])["events"]
    ]
    assert await context.service.dispatch_next_async(send) is None
    assert context.backend.effects == []


@pytest.mark.asyncio
async def test_async_revocation_during_readiness_blocks_effect(context):
    work = admit(context, "async-revoked")

    async def send(binding, port):
        context.control.revoke(
            context.actor,
            grant_id=context.jobs[0][1].id,
            expected_grant_revision=1,
            reason="revoked during readiness",
        )
        port.send_keys("session", binding.work_item_id, "task")
        return deliver(binding, port)

    with pytest.raises(DeliveryUncertain):
        await context.service.dispatch_next_async(send)
    assert context.backend.effects == []
    assert context.repo.get_work(work["id"])["state"] == "reconcile"


@pytest.mark.asyncio
async def test_async_cancellation_after_adapter_callback_closes_port_and_never_replays(context):
    work = admit(context, "async-cancelled")
    started, ports = asyncio.Event(), []

    async def send(binding, port):
        ports.append(port)
        deliver(binding, port)
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(context.service.dispatch_next_async(send))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert context.repo.get_work(work["id"])["state"] == "reconcile"
    assert accounting(context.repo)["held"] == accounting(context.repo)["paths"] == 1
    with pytest.raises(ValueError):
        ports[0].send_keys("session", "late", "must not send")
    restarted = WorkAdmission(WorkRepository(context.repo.path), backends={"test": context.backend})
    assert await restarted.dispatch_next_async(send) is None
    assert context.backend.effects == []


@pytest.mark.asyncio
async def test_async_cancel_during_preflight_never_starts_delivery(context):
    work = admit(context, "async-preflight")
    entered, release = Event(), Event()

    def preflight():
        entered.set()
        assert release.wait(timeout=5)

    async def send(binding, port):
        pytest.fail("cancelled preparation must never deliver")

    context.backend.preflight_hook = preflight
    task = asyncio.create_task(context.service.dispatch_next_async(send))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert context.backend.effects == []
    assert context.repo.get_work(work["id"])["state"] == "reconcile"
    assert context.service.dispatch_next(deliver) is None


@pytest.mark.asyncio
async def test_async_restarted_dispatch_reads_exact_durable_snapshot(context):
    work = admit(context, "async-restart")
    repository = WorkRepository(context.repo.path)
    restarted = WorkAdmission(repository, backends={"test": context.backend})
    job, grant = context.jobs[0]
    snapshots = DelegationSnapshots(
        repository,
        policy=KnowledgePolicy(repository, job["id"], grant.id, 1),
    )

    async def send(binding, port):
        snapshot = snapshots.read(context.actor, binding.contract.snapshot.id)
        assert snapshot.content == b"frozen context async-restart"
        await asyncio.sleep(0)
        return DeliveryObservation(task_received=True)

    result = await restarted.dispatch_next_async(send)
    assert result["id"] == work["id"]
    assert result["attempts"][0]["state"] == "sent"
    assert "attempt.acknowledged" not in [
        event["event_type"] for event in repository.read_events(work["job_id"])["events"]
    ]
    assert await restarted.dispatch_next_async(send) is None
    assert context.backend.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "invalid_receipt"])
async def test_async_delivery_failure_preserves_uncertainty_and_closes_port(context, failure):
    work = admit(context, "async-failure")
    ports = []

    async def send(binding, port):
        ports.append(port)
        deliver(binding, port)
        await asyncio.sleep(0)
        if failure == "timeout":
            raise TimeoutError("receipt unavailable")
        return True  # A boolean is not durable delivery evidence.

    with pytest.raises(DeliveryUncertain):
        await context.service.dispatch_next_async(send)
    assert context.repo.get_work(work["id"])["state"] == "reconcile"
    assert accounting(context.repo)["held"] == accounting(context.repo)["paths"] == 1
    with pytest.raises(ValueError):
        ports[0].send_keys("session", "late", "must not send")
    assert await context.service.dispatch_next_async(send) is None
    assert context.backend.effects == []


@pytest.mark.asyncio
async def test_repeated_async_cancellation_keeps_preparation_cleanup_alive(context):
    work = admit(context, "async-repeated-cancel")
    entered, release = Event(), Event()

    def preflight():
        entered.set()
        assert release.wait(timeout=5)

    async def send(binding, port):
        pytest.fail("cancelled preparation must never deliver")

    context.backend.preflight_hook = preflight
    task = asyncio.create_task(context.service.dispatch_next_async(send))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        # Let the first cancellation enter its shielded cleanup await.
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()

    async def wait_for_reconciliation():
        while context.repo.get_work(work["id"])["state"] != "reconcile":
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait_for_reconciliation(), timeout=5)
    assert context.backend.effects == []
    assert context.service.dispatch_next(deliver) is None
