"""A retained closing credit cannot be minted or retired by an awaiter."""

from dataclasses import replace
from test.services.test_delivery_admission import create, observe, reserve

import pytest

pytestmark = pytest.mark.offline_database


def running(owner, receiver="receiver"):
    lease = reserve(owner, receiver)
    assert lease.start()
    return lease


def test_connection_returns_closing_credit_without_releasing_invocation_budget():
    owner = create()
    observe(owner, 1)
    lease = running(owner)
    first = lease.try_acquire_storage_connection(now=10.0)
    assert first is not None
    assert owner.snapshot(now=10.0).storage_connection_loans == 1
    assert lease.try_acquire_storage_connection(now=10.0) is None
    assert lease.release_storage() is False
    with pytest.raises(RuntimeError, match="storage"):
        lease.finish()
    assert first.finish() and not first.finish()
    assert owner.snapshot(now=10.0).committed == lease.footprint
    assert owner.snapshot(now=10.0).storage_connection_loans == 0
    assert owner.snapshot(now=10.0).storage_completion_credits == 1
    assert owner.try_reserve("other", now=10.0).reservation is None
    second = lease.try_acquire_storage_connection(now=10.0)
    assert second is not None and second is not first
    assert not first.finish() and not replace(second).finish()
    assert second.finish() and lease.finish()
    assert owner.snapshot(now=10.0).inflight == 0
    assert owner.snapshot(now=10.0).storage_completion_credits == 0


def test_concurrent_connections_charge_only_additional_units_and_keep_closing_credit():
    owner = create()
    observe(owner, 3)
    lease = running(owner)
    first, second, third = [lease.try_acquire_storage_connection(now=10.0) for _ in range(3)]
    assert first is not None and second is not None and third is not None
    assert owner.snapshot(now=10.0).committed.storage_slots == 3
    assert owner.snapshot(now=10.0).storage_connection_loans == 3
    assert owner.snapshot(now=10.0).storage_completion_credits == 1
    assert lease.try_acquire_storage_connection(now=10.0) is None
    assert first.finish()
    replacement = lease.try_acquire_storage_connection(now=10.0)
    assert replacement is not None
    assert owner.snapshot(now=10.0).committed.storage_slots == 3
    assert third.finish() and second.finish() and replacement.finish()
    assert owner.snapshot(now=10.0).committed == lease.footprint
    assert lease.finish()


@pytest.mark.parametrize("transition", ["stop", "invalidate", "stale"])
def test_closing_credit_survives_pressure_without_admitting_extra_connections(transition):
    owner = create()
    observe(owner, 3)
    lease = running(owner)
    first = lease.try_acquire_storage_connection(now=10.0)
    assert first is not None and first.finish()
    now = 10.0
    if transition == "stop":
        owner.stop_accepting()
    elif transition == "invalidate":
        owner.invalidate(expected_generation=owner.generation)
    else:
        now = 20.0
    closing = lease.try_acquire_storage_connection(now=now)
    assert closing is not None
    assert lease.try_acquire_storage_connection(now=now) is None
    assert owner.snapshot(now=now).committed == lease.footprint
    assert closing.finish() and lease.finish()


def test_coarse_stage_and_connection_loans_cannot_release_each_other():
    owner = create()
    observe(owner, 3)
    lease = running(owner)
    connection = lease.try_acquire_storage_connection(now=10.0)
    assert connection is not None
    assert lease.take_storage_stage(now=10.0) is None
    assert connection.finish()
    coarse = lease.take_storage_stage(now=10.0)
    assert coarse is not None
    assert lease.try_acquire_storage_connection(now=10.0) is None
    assert coarse.finish() and lease.finish()


def test_no_connection_loan_before_start_or_after_release():
    owner = create()
    observe(owner, 1)
    lease = reserve(owner, "receiver")
    assert lease.try_acquire_storage_connection(now=10.0) is None
    assert lease.cancel_before_start()
    assert lease.try_acquire_storage_connection(now=10.0) is None


@pytest.mark.parametrize("deliveries", [5, 20])
def test_retained_credits_do_not_impose_a_four_callback_limit(deliveries):
    owner = create()
    observe(owner, deliveries)
    leases = [running(owner, f"receiver-{index}") for index in range(deliveries)]
    connections = [lease.try_acquire_storage_connection(now=10.0) for lease in leases]
    assert all(connection is not None for connection in connections)
    assert owner.snapshot(now=10.0).started == deliveries
    assert owner.snapshot(now=10.0).committed.storage_slots == deliveries
    for lease, connection in zip(leases, connections):
        assert connection.finish() and lease.finish()
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.parametrize("connections", [1, 3])
def test_finished_body_waits_for_every_exact_physical_connection(connections):
    owner = create()
    observe(owner, connections)
    lease = running(owner)
    loans = [lease.try_acquire_storage_connection(now=10.0) for _ in range(connections)]
    assert all(loan is not None for loan in loans)
    assert lease.finish_after_storage() is False
    assert lease.finish_after_storage() is False
    assert owner.snapshot(now=10.0).storage_retirement_pending == 1
    assert lease.try_acquire_storage_connection(now=10.0) is None
    for loan in loans[:-1]:
        assert loan.finish()
        assert owner.snapshot(now=10.0).started == 1
    assert loans[-1].finish()
    snapshot = owner.snapshot(now=10.0)
    assert snapshot.inflight == snapshot.storage_connection_loans == 0
    assert snapshot.storage_completion_credits == snapshot.storage_retirement_pending == 0
    assert lease.finish_after_storage() is False


def test_finished_body_cannot_reopen_a_coarse_stage_and_retires_after_its_return():
    owner = create()
    observe(owner, 1)
    lease = running(owner)
    stage = lease.take_storage_stage(now=10.0)
    assert stage is not None
    assert lease.finish_after_storage() is False
    assert lease.take_storage_stage(now=10.0) is None
    assert stage.finish()
    assert owner.snapshot(now=10.0).inflight == 0


def test_declared_storage_footprint_is_reserved_atomically_before_worker_start():
    owner = create()
    assert observe(owner, 1, at=9.0)
    attempt = owner.try_reserve("two-session-input", now=9.0, storage_slots=2)
    assert attempt.reservation is None and attempt.reason == "resource_exhausted"
    assert owner.snapshot(now=10.0).inflight == 0
    assert observe(owner, 2)
    lease = owner.try_reserve("two-session-input", now=10.0, storage_slots=2).reservation
    assert lease is not None and lease.footprint.storage_slots == 2
    assert owner.snapshot(now=10.0).committed.storage_slots == 2
    assert lease.start()
    owner.stop_accepting()
    lock = lease.try_acquire_storage_connection(now=10.0)
    closing = lease.try_acquire_storage_connection(now=10.0)
    assert lock is not None and closing is not None
    assert lease.try_acquire_storage_connection(now=10.0) is None
    assert not lease.finish_after_storage()
    assert lock.finish() and owner.snapshot(now=10.0).inflight == 1
    assert closing.finish() and owner.snapshot(now=10.0).inflight == 0


@pytest.mark.parametrize("slots", [0, -1, True, 1.5])
def test_invalid_declared_storage_footprint_cannot_create_reservation(slots):
    owner = create()
    observe(owner, 20)
    with pytest.raises(ValueError):
        owner.try_reserve("invalid-storage", now=10.0, storage_slots=slots)
    assert owner.snapshot(now=10.0).inflight == 0
