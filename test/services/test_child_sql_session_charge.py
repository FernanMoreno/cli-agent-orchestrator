"""Parent prepayment stays on its original lease; no child TCP authority."""

import copy
import os
import pickle
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from test.services.test_delivery_admission import observe
from test.services.test_resource_process_supervisor import child

import pytest

from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    acquire_storage_connection,
    capture_control_sql_funding,
)
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

pytestmark = pytest.mark.offline_database


class Channel:
    pass


@contextmanager
def funded(*, slots=1, reserved=1, held=True):
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    assert observe(owner, slots)
    reservation = owner.try_reserve_storage(now=10.0, storage_slots=reserved).reservation
    assert reservation is not None and reservation.start()
    if held:
        reservation.hold_retirement()
    with permit.operation(clock=lambda: 10.0):
        loan = acquire_storage_connection()
        channel = Channel()
        funding = capture_control_sql_funding(channel, loan)
        assert funding is not None
        try:
            yield owner, reservation, channel, loan, funding
        finally:
            assert loan.finish()
    reservation.finish_after_storage()


def require_api(funding):
    method = getattr(funding, "reserve_child_sql_session", None)
    assert callable(method), "missing parent-paid child SQL session reservation"
    return method


@pytest.mark.parametrize("slots", [1, 2, 7, 20])
def test_child_charge_borrows_prepaid_slot_without_increasing_original_footprint(slots):
    with funded(slots=slots) as (owner, reservation, channel, loan, funding):
        reserve = require_api(funding)
        with child() as supervisor:
            charges = []

            def register(subject):
                before = owner.diagnostic(now=10.0)
                charge = reserve(channel, loan, reservation=reservation, subject=subject)
                charge.check()
                after = owner.diagnostic(now=10.0)
                assert charge.subject is subject.binding
                assert after.snapshot.committed == before.snapshot.committed
                assert (
                    after.snapshot.storage_connection_loans
                    == before.snapshot.storage_connection_loans + 1
                )
                assert owner._additional_storage_connections == 0
                with pytest.raises((StorageAdmissionUnavailable, ValueError)):
                    reserve(channel, loan, reservation=reservation, subject=subject)
                charges.append(charge)

            supervisor._receive_binding(timeout_s=30, before_ack=register)
            assert not reservation.finish_after_storage()
            assert owner.snapshot(now=10.0).committed.storage_slots == 1
            assert not hasattr(charges[0], "finish")
        assert owner.snapshot(now=10.0).committed.storage_slots == 1
    assert owner.snapshot(now=10.0).storage_connection_loans == 1
    assert owner.snapshot(now=10.0).committed.storage_slots == 1
    with pytest.raises(StorageAdmissionUnavailable):
        charges[0].check()


@pytest.mark.parametrize("copying", [copy.copy, copy.deepcopy, pickle.dumps])
def test_charge_cannot_be_copied_or_transferred_to_another_worker(copying):
    with funded() as (_, reservation, channel, loan, funding):
        reserve = require_api(funding)
        with child() as supervisor:

            def register(subject):
                charge = reserve(channel, loan, reservation=reservation, subject=subject)
                with pytest.raises(TypeError):
                    copying(charge)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    with pytest.raises(StorageAdmissionUnavailable):
                        pool.submit(charge.check).result(timeout=10)
                charge.check()

            supervisor._receive_binding(timeout_s=30, before_ack=register)


@pytest.mark.parametrize(
    "invalid", ["other_owner", "reserved", "control", "loan_used", "stopping", "stale"]
)
def test_charge_refuses_wrong_or_unavailable_reservation_before_consuming_capacity(invalid):
    with funded(slots=7) as (owner, reservation, channel, loan, funding):
        reserve = require_api(funding)
        other = DeliveryAdmission(AdmissionPolicy())
        assert observe(other, 2)
        candidate = reservation
        held = None
        if invalid == "other_owner":
            candidate = other.try_reserve_storage(now=10.0).reservation
            assert candidate.start()
        elif invalid == "reserved":
            assert reservation.finish_after_storage() is False
            assert reservation._retirement_hold.physical_ready
            assert reservation._retirement_hold.finish()
            candidate = owner.try_reserve_storage(now=10.0).reservation
        elif invalid == "control":
            candidate = loan._lease
        elif invalid == "loan_used":
            held = reservation.try_acquire_storage_connection(now=10.0)
            assert held is not None
        elif invalid == "stopping":
            owner.stop_accepting()
        elif invalid == "stale":
            assert owner.invalidate(expected_generation=owner.generation)
        with child() as supervisor:

            def register(subject):
                before = owner.snapshot(now=10.0)
                with pytest.raises(StorageAdmissionUnavailable):
                    reserve(channel, loan, reservation=candidate, subject=subject)
                assert (
                    owner.snapshot(now=10.0).storage_connection_loans
                    == before.storage_connection_loans
                )

            supervisor._receive_binding(timeout_s=30, before_ack=register)
        if held is not None:
            assert held.finish()
        if candidate is not reservation and invalid in {"reserved", "other_owner"}:
            if invalid == "reserved":
                assert candidate.cancel_before_start()
            else:
                assert candidate.finish_after_storage()


def test_fork_refuses_charge_before_touching_inherited_funding_locks():
    with funded() as (_, reservation, channel, loan, funding):
        reserve = require_api(funding)
        with child() as supervisor:

            def register(subject):
                charge = reserve(channel, loan, reservation=reservation, subject=subject)
                pid = os.fork()
                if pid == 0:
                    try:
                        charge.check()
                    except StorageAdmissionUnavailable:
                        os._exit(0)
                    os._exit(1)
                assert os.waitpid(pid, 0)[1] == 0
                charge.check()

            supervisor._receive_binding(timeout_s=30, before_ack=register)


def test_charge_constructor_cannot_create_a_prepaid_session():
    from cli_agent_orchestrator.clients.storage_admission import ChildSqlSessionCharge

    with pytest.raises(TypeError):
        ChildSqlSessionCharge()


@pytest.mark.parametrize("slots", [1, 2, 7, 20])
def test_one_prepaid_reservation_can_account_for_all_its_units_without_fixed_four(slots):
    with funded(slots=slots, reserved=slots) as (owner, reservation, _, _, funding):
        require_api(funding)
        loans = [
            funding.scope.owner.reserve_child_sql_session(reservation, now=10.0)
            for _ in range(slots)
        ]
        assert len(set(id(loan) for loan in loans)) == slots
        assert owner.snapshot(now=10.0).storage_connection_loans == slots + 1
        assert owner.snapshot(now=10.0).committed.storage_slots == slots
        with pytest.raises(StorageAdmissionUnavailable):
            funding.scope.owner.reserve_child_sql_session(reservation, now=10.0)
        owner.stop_accepting()
        assert not reservation.finish_after_storage()
        with pytest.raises(RuntimeError):
            owner.start_accepting()
        assert owner.snapshot(now=10.0).committed.storage_slots == slots


def test_expired_subject_and_plain_binding_cannot_consume_a_prepaid_unit():
    with funded() as (owner, reservation, channel, loan, funding):
        reserve = require_api(funding)
        captured = []
        with child() as supervisor:

            def capture(subject):
                with pytest.raises(StorageAdmissionUnavailable):
                    reserve(channel, loan, reservation=reservation, subject=subject.binding)
                captured.append(subject)

            supervisor._receive_binding(timeout_s=30, before_ack=capture)
            with pytest.raises(ValueError):
                reserve(channel, loan, reservation=reservation, subject=captured[0])
            assert owner.snapshot(now=10.0).storage_connection_loans == 1
            assert reservation.finish_after_storage() is False
            assert reservation._retirement_hold.physical_ready


def test_another_async_task_cannot_spend_the_original_sync_funding():
    import asyncio

    with funded() as (owner, reservation, channel, loan, funding):
        reserve = require_api(funding)
        with child() as supervisor:

            def register(subject):
                async def escaped():
                    with pytest.raises(StorageAdmissionUnavailable):
                        reserve(channel, loan, reservation=reservation, subject=subject)

                asyncio.run(escaped())
                assert owner.snapshot(now=10.0).storage_connection_loans == 1
                reserve(channel, loan, reservation=reservation, subject=subject).check()

            supervisor._receive_binding(timeout_s=30, before_ack=register)


def test_unheld_child_debit_must_not_break_the_normal_worker_finalizer():
    with funded(held=False) as (_, reservation, channel, loan, funding):
        reserve = require_api(funding)
        assert reservation._retirement_hold is None
        with child() as supervisor:

            def register(subject):
                try:
                    reserve(channel, loan, reservation=reservation, subject=subject)
                except StorageAdmissionUnavailable:
                    # The narrower safe contract refuses an unheld lease.
                    assert reservation.finish()
                else:
                    # Accepting it would require ordinary finish to hand off.
                    assert reservation.finish() is False

            supervisor._receive_binding(timeout_s=30, before_ack=register)


def test_child_occupies_the_original_closing_credit_without_allowing_a_second_data_loan():
    with funded(slots=7) as (owner, reservation, channel, loan, funding):
        with child() as supervisor:

            def register(subject):
                charge = require_api(funding)(
                    channel, loan, reservation=reservation, subject=subject
                )
                charge.check()
                assert reservation.try_acquire_storage_connection(now=10.0) is None
                assert reservation.release_storage() is False
                assert owner.snapshot(now=10.0).committed.storage_slots == 1
                assert reservation.finish() is False
                assert not reservation._retirement_hold.physical_ready

            supervisor._receive_binding(timeout_s=30, before_ack=register)
        assert owner.snapshot(now=10.0).storage_connection_loans == 2


def test_released_prepaid_storage_cannot_be_borrowed_again_as_a_child_session():
    with funded(slots=7) as (owner, reservation, channel, loan, funding):
        assert reservation.release_storage()
        assert owner.snapshot(now=10.0).committed.storage_slots == 0
        with child() as supervisor:

            def register(subject):
                with pytest.raises(StorageAdmissionUnavailable):
                    require_api(funding)(channel, loan, reservation=reservation, subject=subject)
                assert owner.snapshot(now=10.0).storage_connection_loans == 1
                assert reservation.finish() is False
                assert reservation._retirement_hold.physical_ready

            supervisor._receive_binding(timeout_s=30, before_ack=register)
