"""Exact physical-retirement holds; no shared PostgreSQL acceptance here."""

import copy
from test.services.test_delivery_admission import observe, reserve

import pytest

from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    acquire_storage_connection,
)
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

pytestmark = pytest.mark.offline_database


def started():
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    assert observe(owner, 8)
    attempt = owner.try_reserve("receiver", now=10, storage_slots=2)
    assert attempt.reservation is not None
    lease = attempt.reservation
    assert lease.start()
    return owner, permit, lease


@pytest.mark.parametrize("finish", ["finish", "finish_after_storage"])
def test_hold_preserves_receiver_and_remaining_costs_until_confirmation(finish):
    owner, _, lease = started()
    hold = lease.hold_retirement()
    before = owner.snapshot(now=10)
    assert not getattr(lease, finish)()
    assert hold.physical_ready
    after = owner.snapshot(now=10)
    assert after.committed == before.committed
    assert after.inflight == after.started == after.storage_retirement_pending == 1
    assert owner.try_reserve("receiver", now=10).reason == "receiver_busy"
    assert hold.finish()
    assert not hold.finish()
    assert owner.snapshot(now=10).inflight == 0
    assert reserve(owner, "receiver").cancel_before_start()


def test_last_exact_loan_sets_ready_and_stop_does_not_erase_hold():
    owner, _, lease = started()
    hold = lease.hold_retirement()
    one = lease.try_acquire_storage_connection(now=10)
    two = lease.try_acquire_storage_connection(now=10)
    assert one is not None and two is not None
    assert not lease.finish_after_storage()
    owner.stop_accepting()
    assert not hold.physical_ready and not hold.finish()
    assert lease.try_acquire_storage_connection(now=10) is None
    assert one.finish() and not hold.physical_ready
    assert not copy.copy(two).finish()
    assert two.finish() and hold.physical_ready
    with pytest.raises(RuntimeError):
        owner.start_accepting()
    assert owner.snapshot(now=10).inflight == 1
    assert hold.finish()
    owner.start_accepting()


def test_last_storage_stage_converges_on_same_hold():
    owner, _, lease = started()
    hold = lease.hold_retirement()
    stage = lease.take_storage_stage(now=10)
    assert stage is not None
    assert not lease.finish_after_storage()
    assert not hold.physical_ready
    assert stage.finish()
    assert hold.physical_ready and lease.state == "started"
    assert owner.try_reserve("receiver", now=10).reason == "receiver_busy"
    assert hold.finish()
    assert owner.snapshot(now=10).inflight == 0


def test_registration_requires_exact_started_nonretiring_data_lease():
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    assert observe(owner, 8)
    pending = reserve(owner, "pending")
    with pytest.raises(RuntimeError):
        pending.hold_retirement()
    assert pending.start()
    with pytest.raises(RuntimeError):
        copy.copy(pending).hold_retirement()
    hold = pending.hold_retirement()
    with pytest.raises(RuntimeError):
        pending.hold_retirement()
    assert not pending.finish_after_storage()
    with pytest.raises(RuntimeError):
        pending.hold_retirement()
    assert hold.finish()
    with pytest.raises(RuntimeError):
        pending.hold_retirement()
    with permit.operation(clock=lambda: 10):
        loan = acquire_storage_connection()
        assert loan is not None
        with pytest.raises(RuntimeError):
            loan._lease.hold_retirement()
        assert loan.finish()


def test_copy_or_signalled_event_cannot_manufacture_physical_completion():
    owner, _, lease = started()
    hold = lease.hold_retirement()
    hold._signal.set()  # A wakeup is not the owner's physical witness.
    assert not hold.physical_ready and not hold.finish()
    assert not lease.finish_after_storage()
    assert hold.physical_ready
    assert not copy.copy(hold).finish()
    assert owner.snapshot(now=10).inflight == 1
    assert hold.finish()


def test_foreign_process_fails_before_inherited_lock(monkeypatch):
    owner, _, lease = started()
    hold = lease.hold_retirement()

    class Forbidden:
        def __enter__(self):
            pytest.fail("foreign process entered parent lock")

    monkeypatch.setattr(owner, "_process_guard", Forbidden())
    monkeypatch.setattr(owner, "_pid", -1)
    with pytest.raises(StorageAdmissionUnavailable):
        hold.finish()
    with pytest.raises(StorageAdmissionUnavailable):
        _ = hold.physical_ready


def test_only_preregistered_ready_hold_allows_control_after_stop():
    owner, permit, lease = started()
    hold = lease.hold_retirement()
    with pytest.raises(StorageAdmissionUnavailable):
        with permit.operation(clock=lambda: 10, retirement=hold):
            pytest.fail("nonready hold entered cleanup")
    assert not lease.finish_after_storage()
    owner.stop_accepting()
    with pytest.raises(StorageAdmissionUnavailable):
        with permit.operation(clock=lambda: 10):
            pytest.fail("new control work entered after stop")
    with pytest.raises(StorageAdmissionUnavailable):
        with permit.operation(clock=lambda: 10, retirement=copy.copy(hold)):
            pytest.fail("copied hold entered cleanup")
    with permit.operation(clock=lambda: 10, retirement=hold):
        assert not hold.finish()  # The metadata body has not retired yet.
        loan = acquire_storage_connection()
        assert loan is not None and loan.finish()
        assert not hold.finish()
        with pytest.raises(StorageAdmissionUnavailable):
            with permit.operation(clock=lambda: 10, retirement=hold):
                pytest.fail("cleanup created a second control credit")
    assert hold.finish()
    with pytest.raises(StorageAdmissionUnavailable):
        with permit.operation(clock=lambda: 10, retirement=hold):
            pytest.fail("finished hold admitted another cleanup")


def test_uncertain_metadata_close_retains_hold_until_last_exact_loan():
    owner, permit, lease = started()
    hold = lease.hold_retirement()
    assert not lease.finish_after_storage()
    owner.stop_accepting()
    with permit.operation(clock=lambda: 10, retirement=hold):
        loan = acquire_storage_connection()
        assert loan is not None
    assert owner.snapshot(now=10).inflight == 2
    assert not hold.finish()
    with pytest.raises(StorageAdmissionUnavailable):
        with permit.operation(clock=lambda: 10, retirement=hold):
            pytest.fail("uncertain metadata credit reused")
    assert loan.finish()
    assert owner.snapshot(now=10).inflight == 1
    assert hold.finish()
    assert owner.snapshot(now=10).inflight == 0


def test_mapped_hold_cannot_open_more_sessions_than_original_footprint():
    owner, _, lease = started()
    hold = lease.hold_retirement()
    loans = [
        lease.try_acquire_storage_connection(now=10) for _ in range(lease.footprint.storage_slots)
    ]
    assert all(loan is not None for loan in loans)
    assert lease.try_acquire_storage_connection(now=10) is None
    assert not lease.finish_after_storage()
    for loan in loans:
        assert loan.finish()
    assert hold.finish()


@pytest.mark.parametrize("kind", ["loan", "stage"])
def test_legacy_finish_with_hold_waits_for_live_physical_storage(kind):
    owner, _, lease = started()
    hold = lease.hold_retirement()
    token = (
        lease.try_acquire_storage_connection(now=10)
        if kind == "loan"
        else lease.take_storage_stage(now=10)
    )
    assert token is not None
    assert not lease.finish()
    assert not hold.physical_ready and not hold.finish()
    assert token.finish()
    assert hold.physical_ready
    assert hold.finish()
    assert owner.snapshot(now=10).inflight == 0


@pytest.mark.parametrize("kind", ["loan", "stage"])
def test_hold_registration_must_precede_live_physical_storage(kind):
    _, _, lease = started()
    token = (
        lease.try_acquire_storage_connection(now=10)
        if kind == "loan"
        else lease.take_storage_stage(now=10)
    )
    assert token is not None
    with pytest.raises(RuntimeError):
        lease.hold_retirement()
    assert token.finish()
    assert lease.finish()


def test_waiting_control_serializes_without_holding_the_ledger_guard():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    owner, permit, _ = started()
    entered, release, attempted = (threading.Event() for _ in range(3))

    def first():
        with permit.operation(clock=lambda: 10):
            entered.set()
            assert release.wait(10)

    def second():
        attempted.set()
        with permit.operation(clock=lambda: 10, wait=True):
            loan = acquire_storage_connection()
            assert loan is not None and loan.finish()
            return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(first)
        try:
            assert entered.wait(5)
            two = pool.submit(second)
            assert attempted.wait(5)
            # If waiting held owner._guard, this observation would deadlock.
            assert owner.snapshot(now=10).control_inflight == 1
            with pytest.raises(StorageAdmissionUnavailable):
                with permit.operation(clock=lambda: 10):
                    pytest.fail("nonblocking probe bypassed occupied lane")
        finally:
            release.set()
        one.result(timeout=10)
        assert two.result(timeout=10)


def test_nested_waiting_control_is_denied_instead_of_waiting_on_itself():
    _, permit, _ = started()
    with permit.operation(clock=lambda: 10):
        with pytest.raises(StorageAdmissionUnavailable):
            with permit.operation(clock=lambda: 10, wait=True):
                pytest.fail("nested blocking lane reentered")


def test_waiting_control_rechecks_stop_before_running_callback():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    owner, permit, lease = started()
    release, entered, attempted = (threading.Event() for _ in range(3))

    def first():
        with permit.operation(clock=lambda: 10):
            entered.set()
            assert release.wait(10)

    def second():
        attempted.set()
        with pytest.raises(StorageAdmissionUnavailable):
            with permit.operation(clock=lambda: 10, wait=True):
                pytest.fail("queued fresh control survived stop")

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(first)
        try:
            assert entered.wait(5)
            two = pool.submit(second)
            assert attempted.wait(5)
            owner.stop_accepting()
        finally:
            release.set()
        one.result(timeout=10)
        two.result(timeout=10)
    assert lease.finish()
