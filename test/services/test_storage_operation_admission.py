"""Normal service transactions reuse exact physical ownership and drain safely."""

import asyncio
import contextvars
import importlib
from concurrent.futures import ThreadPoolExecutor
from test.services.test_delivery_admission import create, observe, reserve
from threading import Event

import pytest

from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    acquire_storage_connection,
    physical_storage_scope,
)
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

pytestmark = pytest.mark.offline_database


def port(owner=None, *, retired=lambda: True):
    module = importlib.import_module("cli_agent_orchestrator.services.storage_operation_admission")
    value = module.StorageOperationAdmission()
    if owner is not None:
        value.bind_admission(owner, clock=lambda: 10, retirement=retired)
        value.start_accepting()
    return value


def test_unknown_normal_operation_denies_without_a_lease_or_connection():
    owner = create()
    value = port(owner)
    with pytest.raises(StorageAdmissionUnavailable):
        with value.operation():
            pytest.fail("unknown data capacity admitted")
    assert owner.snapshot(now=10).inflight == 0


def test_normal_storage_only_operation_covers_each_sequential_connection():
    owner = create()
    assert observe(owner, 1)
    value = port(owner)
    with value.operation():
        status = owner.snapshot(now=10)
        assert status.inflight == status.storage_only_inflight == 1
        assert status.delivery_inflight == 0
        assert status.committed.memory_bytes == status.committed.cpu_millicores == 0
        assert status.committed.storage_slots == 1
        first = acquire_storage_connection()
        assert first is not None and first.finish()
        second = acquire_storage_connection()
        assert second is not None and second.finish()
    assert owner.snapshot(now=10).inflight == 0


def test_normal_parent_scope_is_reused_without_another_storage_reservation():
    owner = create()
    assert observe(owner, 2)
    value = port(owner)
    lease = reserve(owner, "normal-parent")
    assert lease.start()
    try:
        with physical_storage_scope(lease, clock=lambda: 10):
            with value.operation():
                state = owner.snapshot(now=10)
                assert state.inflight == state.delivery_inflight == 1
                assert state.storage_only_inflight == 0
                loan = acquire_storage_connection()
                assert loan is not None and loan.finish()
    finally:
        assert lease.finish()


def test_control_scope_cannot_authorize_normal_service_sql():
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    value = port(owner)
    with pytest.raises(StorageAdmissionUnavailable):
        with permit.operation(clock=lambda: 10):
            with pytest.raises(StorageAdmissionUnavailable):
                with value.operation():
                    pytest.fail("control credit escaped into data SQL")
    assert owner.snapshot(now=10).inflight == 0


def test_copied_physical_context_cannot_fall_back_to_another_normal_lease():
    owner = create()
    assert observe(owner, 2)
    value = port(owner)
    lease = reserve(owner, "normal-parent")
    assert lease.start()
    try:
        with physical_storage_scope(lease, clock=lambda: 10):
            copied = contextvars.copy_context()

            def attempt():
                with value.operation():
                    pytest.fail("copied scope minted a new lease")

            with ThreadPoolExecutor(max_workers=1) as pool:
                with pytest.raises(StorageAdmissionUnavailable):
                    pool.submit(copied.run, attempt).result(timeout=10)
            assert owner.snapshot(now=10).inflight == 1
    finally:
        assert lease.finish()


def test_closed_binding_never_reverts_to_legacy_before_retirement():
    owner = create()
    assert observe(owner, 1)
    ready = [False]
    value = port(owner, retired=lambda: ready[0])
    value.stop_accepting()
    with pytest.raises(StorageAdmissionUnavailable):
        with value.operation():
            pytest.fail("closed binding admitted")
    with pytest.raises(RuntimeError):
        value.bind_admission(None)
    ready[0] = True
    value.bind_admission(None)
    value.start_accepting()
    with value.operation():
        assert acquire_storage_connection() is None


@pytest.mark.asyncio
async def test_cancelled_drain_joins_the_actual_operation_before_returning():
    owner = create()
    assert observe(owner, 1)
    value = port(owner)
    entered, release = Event(), Event()

    def worker():
        with value.operation():
            entered.set()
            assert release.wait(10)

    future = asyncio.create_task(asyncio.to_thread(worker))
    assert await asyncio.to_thread(entered.wait, 10)
    value.stop_accepting()
    drain = asyncio.create_task(value.drain(timeout_s=10))
    await asyncio.sleep(0)
    drain.cancel()
    try:
        await asyncio.sleep(0)
        assert not drain.done()
        assert owner.snapshot(now=10).inflight == 1
    finally:
        release.set()
    await future
    with pytest.raises(asyncio.CancelledError):
        await drain
    assert owner.snapshot(now=10).inflight == 0


def test_foreign_port_rejected_before_inherited_lock(monkeypatch):
    value = port()

    class Forbidden:
        def __enter__(self):
            pytest.fail("foreign inherited lock touched")

    monkeypatch.setattr(value, "_pid", -1)
    monkeypatch.setattr(value, "_process_guard", Forbidden())
    with pytest.raises(StorageAdmissionUnavailable):
        with value.operation():
            pytest.fail("foreign service port admitted")


def test_legacy_port_cannot_borrow_an_active_control_credit():
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    value = port()
    with pytest.raises(StorageAdmissionUnavailable):
        with permit.operation(clock=lambda: 10):
            with pytest.raises(StorageAdmissionUnavailable):
                with value.operation():
                    pytest.fail("legacy service consumed protected control SQL")
    assert owner.snapshot(now=10).inflight == 0


def test_stopped_port_allows_only_exact_already_owned_normal_completion():
    owner = create()
    assert observe(owner, 1)
    value = port(owner)
    lease = reserve(owner, "closing-parent")
    assert lease.start()
    value.stop_accepting()
    owner.stop_accepting()
    try:
        with physical_storage_scope(lease, clock=lambda: 10):
            with value.operation():
                loan = acquire_storage_connection()
                assert loan is not None and loan.finish()
                assert owner.snapshot(now=10).inflight == 1
        with pytest.raises(StorageAdmissionUnavailable):
            with value.operation():
                pytest.fail("unowned operation entered after stop")
    finally:
        assert lease.finish()


def test_restart_refuses_live_lifetimes_and_fences_copied_old_epoch():
    owner = create()
    assert observe(owner, 1)
    value = port(owner)
    with value.lifetime():
        copied = contextvars.copy_context()
        value.stop_accepting()
        with pytest.raises(RuntimeError):
            value.start_accepting()
    value.start_accepting()

    def old_request():
        with value.operation():
            pytest.fail("old request crossed a service epoch")

    with pytest.raises(StorageAdmissionUnavailable):
        copied.run(old_request)
    assert owner.snapshot(now=10).inflight == 0


def test_caught_epoch_denial_stays_sticky_in_the_physical_parent():
    from cli_agent_orchestrator.clients.storage_admission import check_storage_admission

    owner = create()
    assert observe(owner, 2)
    value = port(owner)
    with value.lifetime():
        old_epoch = value._context.get()
    value.stop_accepting()
    value.start_accepting()
    lease = reserve(owner, "sticky-parent")
    assert lease.start()
    try:
        with physical_storage_scope(lease, clock=lambda: 10):
            token = value._context.set(old_epoch)
            try:
                with pytest.raises(StorageAdmissionUnavailable):
                    with value.operation():
                        pytest.fail("stale epoch entered")
                with pytest.raises(StorageAdmissionUnavailable):
                    check_storage_admission()
            finally:
                value._context.reset(token)
    finally:
        assert lease.finish()
