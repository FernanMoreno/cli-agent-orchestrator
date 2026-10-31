"""Only an explicitly bound physical worker can borrow an invocation's credit."""

import asyncio
import contextvars
import importlib
import inspect
import os
import select
import signal
from concurrent.futures import ThreadPoolExecutor
from test.services.test_delivery_admission import create, observe, reserve
from threading import Event, Thread

import pytest

pytestmark = pytest.mark.offline_database


def port():
    return importlib.import_module("cli_agent_orchestrator.clients.storage_admission")


def owner_and_lease(slots=1):
    owner = create()
    observe(owner, slots)
    lease = reserve(owner, "physical")
    assert lease.start()
    return owner, lease


def assert_retirement(module, result, *, closed, pending, process_current=True):
    assert isinstance(result, module.StorageCloseResult)
    assert result.closed == closed
    assert result.pending == pending
    assert result.process_current is process_current
    assert result.clean is (process_current and pending == 0)


def test_unbound_legacy_execution_does_not_invent_a_storage_owner():
    module = port()
    assert module.acquire_storage_connection() is None
    assert module.storage_admission_failed() is False
    module.check_storage_admission()


def test_bound_worker_retains_credit_until_its_physical_return():
    module = port()
    owner, lease = owner_and_lease()
    with module.physical_storage_scope(lease, clock=lambda: 10.0):
        loan = module.acquire_storage_connection()
        assert loan is not None
        assert owner.snapshot(now=10.0).storage_connection_loans == 1
    with pytest.raises(RuntimeError, match="storage"):
        lease.finish()
    assert loan.finish()
    assert owner.snapshot(now=10.0).storage_completion_credits == 1
    assert lease.finish()


def test_handled_denial_is_sticky_but_retained_credit_can_record_the_failure():
    module = port()
    owner, lease = owner_and_lease()
    with module.physical_storage_scope(lease, clock=lambda: 10.0):
        first = module.acquire_storage_connection()
        with pytest.raises(module.StorageAdmissionUnavailable):
            module.acquire_storage_connection()
        assert module.storage_admission_failed()
        with module.physical_storage_scope(lease, clock=lambda: 10.0):
            with pytest.raises(module.StorageAdmissionUnavailable):
                module.check_storage_admission()
        assert first.finish()
        owner.stop_accepting()
        closing = module.acquire_storage_connection()
        assert closing is not None and closing.finish()
        assert module.storage_admission_failed()
    assert not module.storage_admission_failed()
    assert lease.finish()


def test_copied_context_alone_does_not_authorize_another_thread():
    module = port()
    owner, lease = owner_and_lease()
    with module.physical_storage_scope(lease, clock=lambda: 10.0):
        context = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(context.run, module.acquire_storage_connection)
            with pytest.raises(module.StorageAdmissionUnavailable):
                future.result(timeout=5)
        assert module.storage_admission_failed()
        assert owner.snapshot(now=10.0).storage_connection_loans == 0
    assert lease.finish()


def test_trusted_handoff_explicitly_binds_the_next_physical_worker():
    module = port()
    owner, lease = owner_and_lease()

    def physical():
        with module.physical_storage_scope(lease, clock=lambda: 10.0):
            loan = module.acquire_storage_connection()
            assert loan is not None
            assert owner.snapshot(now=10.0).storage_connection_loans == 1
            assert loan.finish()

    with module.physical_storage_scope(lease, clock=lambda: 10.0):
        context = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=1) as workers:
            workers.submit(context.run, physical).result(timeout=5)
        assert module.storage_admission_failed() is False
    assert lease.finish()


def test_failed_clock_is_redacted_and_cannot_fall_back_to_unbound_execution():
    module = port()
    _, lease = owner_and_lease()

    def invalid_clock():
        raise RuntimeError("private clock diagnostic marker")

    with module.physical_storage_scope(lease, clock=invalid_clock):
        with pytest.raises(module.StorageAdmissionUnavailable) as error:
            module.acquire_storage_connection()
        assert "private clock" not in str(error.value)
        assert module.storage_admission_failed()
    assert lease.finish()


def test_scope_restores_the_previous_owner_without_reusing_its_credit():
    module = port()
    first_owner, first = owner_and_lease()
    second_owner, second = owner_and_lease()
    with module.physical_storage_scope(first, clock=lambda: 10.0):
        with module.physical_storage_scope(second, clock=lambda: 10.0):
            loan = module.acquire_storage_connection()
            assert second_owner.snapshot(now=10.0).storage_connection_loans == 1
            assert first_owner.snapshot(now=10.0).storage_connection_loans == 0
            assert loan.finish()
        loan = module.acquire_storage_connection()
        assert first_owner.snapshot(now=10.0).storage_connection_loans == 1
        assert second_owner.snapshot(now=10.0).storage_connection_loans == 0
        assert loan.finish()
    assert first.finish() and second.finish()


def test_uncertain_close_keeps_physical_owner_until_a_verified_retirement():
    module = port()
    registry = module.StorageCloseRegistry()
    owner, lease = owner_and_lease()
    loan = lease.try_acquire_storage_connection(now=10.0)
    closed, allow_close = [False], [False]
    identity = object()

    def close():
        if not allow_close[0]:
            raise RuntimeError("private close diagnostic marker")
        closed[0] = True

    registry.retain(identity, close=close, closed=lambda: closed[0], loan=loan)
    registry.retain(identity, close=close, closed=lambda: closed[0], loan=loan)
    assert registry.pending_count == 1
    assert lease.finish_after_storage() is False
    assert_retirement(module, registry.retry_owned(), closed=0, pending=1)
    assert owner.snapshot(now=10.0).storage_connection_loans == 1
    assert owner.snapshot(now=10.0).inflight == 1
    allow_close[0] = True
    assert_retirement(module, registry.retry_owned(), closed=1, pending=0)
    assert owner.snapshot(now=10.0).inflight == 0
    assert_retirement(module, registry.retry_owned(), closed=0, pending=0)


def test_unknown_open_without_a_handle_cannot_be_cleared_by_time_or_retry():
    module = port()
    registry = module.StorageCloseRegistry()
    owner, lease = owner_and_lease()
    loan = lease.try_acquire_storage_connection(now=10.0)
    registry.retain(loan, close=None, closed=lambda: False, loan=loan)
    assert lease.finish_after_storage() is False
    assert registry.has_pending()
    assert_retirement(module, registry.retry_owned(), closed=0, pending=1)
    assert owner.snapshot(now=100.0).inflight == 1
    assert registry.has_pending()
    # Isolated fake registry: no live handle/process and no global reset.
    assert module.storage_close_uncertainty() is False


def test_parallel_retirement_has_one_physical_closer():
    module = port()
    registry = module.StorageCloseRegistry()
    owner, lease = owner_and_lease()
    loan = lease.try_acquire_storage_connection(now=10.0)
    entered, release = Event(), Event()
    closed, calls = [False], []

    def close():
        calls.append("close")
        entered.set()
        assert release.wait(10)
        closed[0] = True

    registry.retain(object(), close=close, closed=lambda: closed[0], loan=loan)
    assert lease.finish_after_storage() is False
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(registry.retry_owned)
        try:
            assert entered.wait(5)
            assert_retirement(
                module, workers.submit(registry.retry_owned).result(timeout=5), closed=0, pending=1
            )
            assert owner.snapshot(now=10.0).inflight == 1
        finally:
            release.set()
        assert_retirement(module, first.result(timeout=5), closed=1, pending=0)
    assert calls == ["close"] and owner.snapshot(now=10.0).inflight == 0


@pytest.mark.asyncio
async def test_async_child_cannot_inherit_its_parent_tasks_physical_scope():
    module = port()
    owner, lease = owner_and_lease()

    async def child():
        loan = module.acquire_storage_connection()
        if loan is not None:
            loan.finish()

    try:
        with module.physical_storage_scope(lease, clock=lambda: 10.0):
            with pytest.raises(module.StorageAdmissionUnavailable):
                await asyncio.create_task(child())
            assert module.storage_admission_failed()
            assert owner.snapshot(now=10.0).storage_connection_loans == 0
    finally:
        lease.finish_after_storage()


def test_copied_context_is_stale_after_its_physical_scope_exits():
    module = port()
    _, lease = owner_and_lease()
    loans = []

    def acquire():
        loan = module.acquire_storage_connection()
        loans.append(loan)
        return loan

    with module.physical_storage_scope(lease, clock=lambda: 10.0):
        copied = contextvars.copy_context()
    try:
        with pytest.raises(module.StorageAdmissionUnavailable):
            copied.run(acquire)
    finally:
        for loan in loans:
            if loan is not None:
                loan.finish()
        lease.finish_after_storage()


def test_uncertain_close_requires_a_non_null_physical_identity():
    module = port()
    registry = module.StorageCloseRegistry()
    _, lease = owner_and_lease()
    loan = lease.try_acquire_storage_connection(now=10.0)
    try:
        with pytest.raises(ValueError, match="identity"):
            registry.retain(None, close=lambda: None, closed=lambda: True, loan=loan)
        assert registry.pending_count == 0
    finally:
        loan.finish()
        lease.finish_after_storage()


def test_production_retainer_cannot_bypass_process_uncertainty_with_a_custom_registry():
    assert "registry" not in inspect.signature(port().retain_storage_close).parameters


def fork_with_held_locks(callback, locks):
    if not hasattr(os, "fork"):
        pytest.skip("Requires an actual POSIX fork ownership boundary")
    held, release = Event(), Event()

    def holder():
        for lock in locks:
            lock.acquire()
        try:
            held.set()
            assert release.wait(15)
        finally:
            for lock in reversed(locks):
                lock.release()

    holder_thread = Thread(target=holder)
    holder_thread.start()
    read_fd, write_fd = os.pipe()
    child = None
    try:
        assert held.wait(5)
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            try:
                callback()
                os.write(write_fd, b"ok")
                os._exit(0)
            except BaseException:
                os.write(write_fd, b"failed")
                os._exit(1)
        os.close(write_fd)
        write_fd = None
        ready, _, _ = select.select([read_fd], [], [], 5)
        assert ready, "fork child touched an inherited lock"
        assert os.read(read_fd, 32) == b"ok"
        waited, status = os.waitpid(child, 0)
        assert waited == child and os.waitstatus_to_exitcode(status) == 0
        child = None
    finally:
        if child is not None and child != 0:
            # This PID is our unreaped direct child and cannot have been reused.
            waited, _ = os.waitpid(child, os.WNOHANG)
            if waited == 0:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
        release.set()
        holder_thread.join(5)
        assert not holder_thread.is_alive()
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)


def test_forked_registry_never_touches_parent_locks_or_closes_parent_handles():
    module = port()
    registry = module.StorageCloseRegistry()
    owner, lease = owner_and_lease()
    loan = lease.try_acquire_storage_connection(now=10.0)
    closed, calls = [False], []

    def close():
        calls.append("close")
        closed[0] = True

    registry.retain(object(), close=close, closed=lambda: closed[0], loan=loan)

    def child():
        assert registry.has_pending()
        assert registry.pending_count is None
        assert registry.has_loan(loan)
        assert_retirement(
            module, registry.retry_owned(), closed=0, pending=None, process_current=False
        )
        with pytest.raises(module.StorageAdmissionUnavailable):
            registry.retain(object(), close=close, closed=lambda: False, loan=loan)

    try:
        fork_with_held_locks(child, [registry._guard, registry._retry_guard])
        assert calls == [] and owner.snapshot(now=10.0).storage_connection_loans == 1
    finally:
        closed[0] = True
        lease.finish_after_storage()
        registry.retry_owned()


def test_forked_scope_refuses_before_touching_its_inherited_lock():
    module = port()
    _, lease = owner_and_lease()

    def child():
        with pytest.raises(module.StorageAdmissionUnavailable):
            module.acquire_storage_connection()
        assert module.storage_admission_failed()

    try:
        with module.physical_storage_scope(lease, clock=lambda: 10.0):
            scope = module._CURRENT.get()
            fork_with_held_locks(child, [scope.guard])
    finally:
        lease.finish_after_storage()
