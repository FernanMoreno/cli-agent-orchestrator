"""Real PostgreSQL and local preparation ownership, not provider acceptance."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from test.postgres.test_admission_reservations import shared
from test.postgres.test_shared_admission_runtime import enroll
from test.services.test_delivery_admission import observe
from test.services.test_shared_admission_runtime import Monitor, principal
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    _current_storage_owner,
    physical_storage_scope,
)
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.delivery_admission import AdmissionAttempt
from cli_agent_orchestrator.services.inbox_admission_runtime import InboxAdmissionRuntime
from cli_agent_orchestrator.services.inbox_service import InboxService
from cli_agent_orchestrator.services.work_blocking import run_owned_blocking

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_terminal_seal_waits_for_shared_prepare_then_allows_admitted_body_to_drain(
    shared, monkeypatch
):
    storage, credit = await run_owned_blocking(enroll, shared)
    target, _, binding, _ = shared
    monitor = Monitor([])
    bootstrap = ResourceBootstrap(
        repository=SimpleNamespace(resolve_target=lambda: target),
        binding_supplier=lambda selected: binding,
        instance_supplier=lambda: binding.instance_id,
        terminal_generation=True,
    )
    repository = bootstrap.shared_repository()
    runtime = InboxAdmissionRuntime(
        InboxService(),
        monitor=monitor,
        bootstrap=bootstrap,
        clock=lambda: 10,
        drain_timeout_s=10,
        shared_repository=repository,
    )

    prepare_committed = threading.Event()
    allow_prepare_to_activate = threading.Event()
    active_committed = threading.Event()
    body_entered = threading.Event()
    release_body = threading.Event()
    body_finished = threading.Event()
    seal_authority_read = threading.Event()
    seal_control_attempted = threading.Event()
    seal_waiting_for_body = threading.Event()
    seal_sql_entered = threading.Event()
    prepare_operations = []
    prepare_control_leases = []
    reserve_calls = []
    reserve_attempts = []
    primary_future = None
    late_future = None
    seal_task = None
    executor = None
    primary_lease = None
    late_lease = None
    primary_started = False
    late_started = False
    runtime_started = False

    original_reserve = repository.reserve_for_cost
    original_transition = repository.transition
    original_seal = repository.seal_current_generation

    def hold_after_preparing_commit(operation_id, cost, *, require_metadata=True):
        reserve_attempts.append(operation_id)
        receipt = original_reserve(operation_id, cost, require_metadata=require_metadata)
        reserve_calls.append((operation_id, receipt))
        assert receipt.state == "preparing"
        prepare_operations.append(operation_id)
        prepare_control_leases.append(_current_storage_owner())
        assert prepare_control_leases[-1] is monitor.owner._control_lease
        prepare_committed.set()
        if not allow_prepare_to_activate.wait(30):
            raise AssertionError("preparation did not receive its activation release")
        return receipt

    def observe_transition(operation_id, *, expected_revision, state):
        current = _current_storage_owner()
        if state == "active":
            assert current is prepare_control_leases[0]
        receipt = original_transition(
            operation_id, expected_revision=expected_revision, state=state
        )
        if state == "active":
            assert receipt.state == "active"
            active_committed.set()
        return receipt

    def funded_seal(authority):
        assert _current_storage_owner() is monitor.owner._control_lease
        assert _current_storage_owner() is not prepare_control_leases[0]
        seal_waiting_for_body.set()
        if not body_entered.wait(30):
            raise AssertionError("seal acquired control before the admitted body entered")
        assert not body_finished.is_set()
        seal_sql_entered.set()
        return original_seal(authority)

    @contextmanager
    def observe_seal_authority_read():
        seal_authority_read.set()
        yield

    monkeypatch.setattr(repository, "reserve_for_cost", hold_after_preparing_commit)
    monkeypatch.setattr(repository, "transition", observe_transition)
    monkeypatch.setattr(repository, "seal_current_generation", funded_seal)

    try:
        await runtime.start()
        runtime_started = True
        assert observe(monitor.owner, 8)
        await runtime.activate_shared(principal=principal())
        assert monitor.owner.shared_ready

        attempt = monitor.owner.try_reserve("prepared-delivery", now=10, storage_slots=1)
        assert isinstance(attempt, AdmissionAttempt) and attempt.reservation is not None
        primary_lease = attempt.reservation
        primary_started = primary_lease.start()
        assert primary_started

        def run_primary_body():
            try:
                with physical_storage_scope(primary_lease, clock=lambda: 10):
                    assert active_committed.is_set(), "body entered before active acknowledgement"
                    body_entered.set()
                    if not release_body.wait(30):
                        raise AssertionError("admitted body did not receive its release signal")
                    body_finished.set()
            finally:
                primary_lease.finish_after_storage()

        executor = ThreadPoolExecutor(max_workers=3)
        primary_future = executor.submit(run_primary_body)
        assert await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(executor, prepare_committed.wait, 30),
            timeout=31,
        ), "shared preparation did not commit preparing"
        assert len(reserve_calls) == 1
        assert reserve_calls[0][1].revision == 1
        assert not active_committed.is_set()
        assert not body_entered.is_set()

        original_authority_read = monitor.authority_read
        original_control_operation = monitor.storage_control.operation

        @contextmanager
        def traced_authority_read():
            with original_authority_read():
                with observe_seal_authority_read():
                    yield

        @contextmanager
        def trace_seal_control_attempt(*, clock, retirement=None, wait=False):
            seal_control_attempted.set()
            with original_control_operation(clock=clock, retirement=retirement, wait=wait):
                yield

        with monkeypatch.context() as patch:
            patch.setattr(monitor, "authority_read", traced_authority_read)
            patch.setattr(monitor.storage_control, "operation", trace_seal_control_attempt)
            seal_task = asyncio.create_task(runtime.seal_terminal_generation())
            assert await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(executor, seal_authority_read.wait, 30),
                timeout=31,
            ), "terminal sealer did not reach its funded authority read"
            assert await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    executor, seal_control_attempted.wait, 30
                ),
                timeout=31,
            ), "terminal sealer did not reach the serialized control lane"
            # Preparation still owns the one control lane: seal SQL has not run,
            # active is not acknowledged, and its protected body has not begun.
            assert not seal_task.done()
            assert monitor.owner._control_lease is prepare_control_leases[0]
            assert not seal_waiting_for_body.is_set()
            assert not seal_sql_entered.is_set()
            assert not active_committed.is_set()
            assert not body_entered.is_set()

            allow_prepare_to_activate.set()
            assert await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(executor, active_committed.wait, 30),
                timeout=31,
            ), "preparation did not commit active before yielding its control lane"
            assert await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(executor, body_entered.wait, 30),
                timeout=31,
            ), "the already-admitted body did not start after active acknowledgement"
            assert not body_finished.is_set()

            seal_receipt = await asyncio.wait_for(asyncio.shield(seal_task), timeout=30)
            assert seal_receipt.instance_id == binding.instance_id
            assert seal_receipt.process_generation == binding.process_generation
            assert seal_sql_entered.is_set()
            assert not body_finished.is_set(), "seal waited for or retired the physical body"

            operation_id = prepare_operations[0]

            def funded_inspection():
                return (
                    repository.read_operation(operation_id),
                    repository.used(storage.spec.domain_id),
                )

            operation, usage_while_body_is_live = await run_owned_blocking(
                monitor.control_call, funded_inspection
            )
            assert operation is not None and operation.state == "active"
            assert usage_while_body_is_live == 2  # Credit plus the active mapped operation.

            # The local owner may still issue a lease after coordinator stop;
            # the physical scope must reject before its protected body executes.
            assert monitor.owner.shared_ready
            late_attempt = monitor.owner.try_reserve(
                "after-terminal-coordinator-stop", now=10, storage_slots=1
            )
            assert late_attempt.reservation is not None, late_attempt.reason
            late_lease = late_attempt.reservation
            late_started = late_lease.start()
            assert late_started
            late_body_entered = threading.Event()

            def run_late_body():
                try:
                    with physical_storage_scope(late_lease, clock=lambda: 10):
                        late_body_entered.set()
                finally:
                    late_lease.finish_after_storage()

            late_future = executor.submit(run_late_body)
            with pytest.raises(StorageAdmissionUnavailable):
                late_future.result(timeout=30)
            assert not late_body_entered.is_set()
            assert len(reserve_calls) == len(reserve_attempts) == 1  # No new SQL or body.

        release_body.set()
        primary_future.result(timeout=30)
        assert body_finished.is_set()
    finally:
        allow_prepare_to_activate.set()
        release_body.set()
        for future in (primary_future, late_future):
            if future is not None:
                try:
                    future.result(timeout=30)
                except BaseException:
                    pass  # The main assertions consume expected and unexpected outcomes.
        if primary_lease is not None and primary_future is None:
            if primary_started:
                primary_lease.finish_after_storage()
            else:
                primary_lease.cancel_before_start()
        if late_lease is not None and late_future is None:
            if late_started:
                late_lease.finish_after_storage()
            else:
                late_lease.cancel_before_start()
        if seal_task is not None:
            # Never detach an admitted SQL callback because a wrapper timed out.
            from cli_agent_orchestrator.services.work_blocking import run_owned_async

            async def join_seal():
                return await asyncio.shield(seal_task)

            try:
                await run_owned_async(join_seal())
            except BaseException:
                pass  # Physical completion is joined; the main path retains its error.
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        if runtime_started:
            await runtime.stop()

    assert runtime.retired and not runtime._shared.worker_alive
    assert monitor.owner.snapshot(now=10).inflight == 0
    released = await run_owned_blocking(repository.read_operation, prepare_operations[0])
    assert released is not None and released.state == "released"
    assert await run_owned_blocking(repository.metadata_credits_current) == (credit,)
    assert await run_owned_blocking(repository.used, storage.spec.domain_id) == 1
    assert await run_owned_blocking(repository.audit_usage) == ()
