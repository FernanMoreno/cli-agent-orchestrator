"""Discovery consumes real storage, without fabricating a receiver or delivery."""

from copy import copy
from dataclasses import replace

import pytest

from cli_agent_orchestrator.clients.storage_admission import physical_storage_scope
from cli_agent_orchestrator.services.admission_policy import (
    AdmissionPolicy,
    CapacityObservation,
    ResourceAmounts,
)
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

pytestmark = pytest.mark.offline_database
MIB = 1024 * 1024


def observed(deliveries, storage_slots, *, cpu=16000, budget=None, ceiling=None):
    owner = DeliveryAdmission(AdmissionPolicy(), budget=budget)
    memory = 80 * MIB * deliveries
    assert owner.observe(
        CapacityObservation(10.0, memory, max(memory, MIB), cpu, 16000, storage_slots),
        now=10.0,
        generation=owner.generation,
        ceiling=ceiling,
    )
    return owner


def storage_lease(owner, slots=1):
    attempt = owner.try_reserve_storage(now=10.0, storage_slots=slots)
    assert attempt.reservation is not None, attempt.reason
    assert attempt.reservation.start()
    return attempt.reservation


def test_storage_only_can_progress_without_delivery_memory_or_receiver():
    owner = observed(0, 1)
    assert owner.snapshot(now=10.0).available_slots == 0
    lease = storage_lease(owner)
    try:
        snapshot = owner.snapshot(now=10.0)
        assert lease.footprint == ResourceAmounts(storage_slots=1)
        assert snapshot.committed == lease.footprint
        assert snapshot.inflight == snapshot.started == 1
        assert snapshot.storage_only_inflight == snapshot.storage_only_started == 1
        assert snapshot.delivery_inflight == snapshot.delivery_started == 0
        assert owner._receivers == {}
        assert owner.try_reserve("actual-receiver", now=10.0).reservation is None
    finally:
        lease.finish_after_storage()
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.parametrize("deliveries", [1, 5, 20])
def test_scanner_and_variable_delivery_counts_share_one_quantity_budget(deliveries):
    owner = observed(deliveries, 1 + 2 * deliveries)
    scanner = storage_lease(owner)
    leases = []
    try:
        for number in range(deliveries):
            attempt = owner.try_reserve(str(number), now=10.0, storage_slots=2)
            assert attempt.reservation is not None, attempt.reason
            assert attempt.reservation.start()
            leases.append(attempt.reservation)
        snapshot = owner.snapshot(now=10.0)
        assert snapshot.inflight == snapshot.started == 1 + deliveries
        assert snapshot.delivery_inflight == snapshot.delivery_started == deliveries
        assert snapshot.storage_only_inflight == snapshot.storage_only_started == 1
        assert snapshot.committed.storage_slots == 1 + 2 * deliveries
        assert len(owner._receivers) == deliveries
        assert owner.try_reserve_storage(now=10.0).reservation is None
        assert owner.try_reserve("overflow", now=10.0, storage_slots=2).reservation is None
    finally:
        for lease in leases:
            lease.finish_after_storage()
        scanner.finish_after_storage()
    snapshot = owner.snapshot(now=10.0)
    assert snapshot.inflight == snapshot.storage_only_inflight == 0
    assert snapshot.committed == ResourceAmounts()


def test_scanner_credit_competes_with_the_complete_delivery_footprint():
    owner = observed(1, 2)
    scanner = storage_lease(owner)
    try:
        assert owner.try_reserve("receiver", now=10.0, storage_slots=2).reservation is None
    finally:
        scanner.finish_after_storage()
    delivery = owner.try_reserve("receiver", now=10.0, storage_slots=2).reservation
    assert delivery is not None
    assert delivery.cancel_before_start()


def test_scanner_retains_control_operation_until_its_exact_physical_loan_finishes():
    owner = observed(1, 2)
    scanner = storage_lease(owner)
    with physical_storage_scope(scanner, clock=lambda: 10.0):
        loan = scanner.try_acquire_storage_connection(now=10.0)
        assert loan is not None
        assert not copy(loan).finish()
        assert not scanner.finish_after_storage()
    owner.stop_accepting()
    snapshot = owner.snapshot(now=10.0)
    assert snapshot.inflight == snapshot.storage_only_inflight == 1
    assert snapshot.storage_retirement_pending == snapshot.storage_connection_loans == 1
    assert loan.finish()
    assert not loan.finish()
    snapshot = owner.snapshot(now=10.0)
    assert snapshot.inflight == snapshot.storage_only_inflight == 0
    assert snapshot.storage_retirement_pending == snapshot.storage_connection_loans == 0


@pytest.mark.parametrize("slots", [True, 0, -1, 1.5, None, "1"])
def test_storage_quantity_is_strict_and_invalid_requests_do_not_mutate(slots):
    owner = observed(1, 4)
    before = owner.snapshot(now=10.0)
    with pytest.raises(ValueError):
        owner.try_reserve_storage(now=10.0, storage_slots=slots)
    assert owner.snapshot(now=10.0) == before


@pytest.mark.parametrize("state", ["unknown", "stale", "stopped"])
def test_storage_only_respects_authority_freshness_and_stop(state):
    owner = DeliveryAdmission(AdmissionPolicy()) if state == "unknown" else observed(1, 4)
    if state == "stopped":
        owner.stop_accepting()
    now = 14.0 if state == "stale" else 10.0
    assert owner.try_reserve_storage(now=now).reservation is None
    assert owner.snapshot(now=now).inflight == 0


@pytest.mark.parametrize("operation", ["cancel", "stop", "invalidate", "reconfigure"])
def test_unstarted_scanner_retires_exactly_once_and_cannot_start_late(operation):
    owner = observed(1, 2)
    lease = owner.try_reserve_storage(now=10.0).reservation
    assert lease is not None
    assert owner.snapshot(now=10.0).storage_only_inflight == 1
    if operation == "cancel":
        assert lease.cancel_before_start()
    elif operation == "stop":
        owner.stop_accepting()
    elif operation == "invalidate":
        assert owner.invalidate(expected_generation=owner.generation)
    else:
        assert (
            owner.reconfigure(
                replace(AdmissionPolicy(), cpu_per_delivery_millicores=251),
                expected_generation=owner.generation,
            )
            is not None
        )
    assert not lease.start()
    assert not lease.cancel_before_start()
    snapshot = owner.snapshot(now=10.0)
    assert snapshot.inflight == snapshot.storage_only_inflight == 0
    assert snapshot.started == snapshot.storage_only_started == 0
    assert snapshot.committed == ResourceAmounts()


def test_pressure_retires_pending_scanner_but_preserves_started_physical_debt():
    owner = observed(1, 2)
    started = storage_lease(owner)
    loan = started.try_acquire_storage_connection(now=10.0)
    assert loan is not None
    pending = owner.try_reserve_storage(now=10.0).reservation
    assert pending is not None
    try:
        assert owner.observe(
            CapacityObservation(11.0, 80 * MIB, 80 * MIB, 16000, 16000, 0),
            now=11.0,
            generation=owner.generation,
        )
        assert not pending.start()
        snapshot = owner.snapshot(now=11.0)
        assert snapshot.inflight == snapshot.storage_only_inflight == 1
        assert snapshot.started == snapshot.storage_only_started == 1
        assert snapshot.resource_debt.storage_slots == 1
        assert not started.finish_after_storage()
        assert owner.snapshot(now=11.0).inflight == 1
    finally:
        loan.finish()
        started.finish_after_storage()
    assert owner.snapshot(now=11.0).inflight == 0


@pytest.mark.parametrize("constraint", ["budget", "ceiling"])
def test_storage_only_cannot_bypass_zero_storage_budget_or_ceiling(constraint):
    owner = observed(1, 4, **{constraint: ResourceAmounts(80 * MIB, 16000, 0)})
    assert owner.try_reserve_storage(now=10.0).reservation is None
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.parametrize("with_loan", [False, True])
def test_storage_reservation_copy_cannot_change_retirement_counters(with_loan):
    owner = observed(1, 2)
    scanner = storage_lease(owner)
    loan = scanner.try_acquire_storage_connection(now=10.0) if with_loan else None
    try:
        before = owner.snapshot(now=10.0)
        assert copy(scanner).finish_after_storage() is False
        assert owner.snapshot(now=10.0) == before
    finally:
        if loan is not None:
            loan.finish()
        scanner.finish_after_storage()
    assert owner.snapshot(now=10.0).storage_only_inflight == 0


def test_storage_only_can_progress_when_cpu_has_no_delivery_headroom():
    owner = observed(1, 1, cpu=0)
    assert owner.snapshot(now=10.0).available_slots == 0
    scanner = storage_lease(owner)
    assert owner.snapshot(now=10.0).committed == ResourceAmounts(storage_slots=1)
    assert scanner.finish_after_storage()
