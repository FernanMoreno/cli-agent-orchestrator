"""A paid control channel cannot silently prepare shared child data SQL."""

from test.services.test_child_sql_session_charge import Channel
from test.services.test_resource_process_supervisor import child
from test.services.test_shared_delivery_admission import (
    FakeRepo,
    authorize,
    clock,
    make_owner,
    registry,
    shutdown,
    until,
)

import pytest

from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    acquire_storage_connection,
    capture_control_sql_funding,
)

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize("prepared", [False, True])
def test_only_exact_already_prepared_shared_reservation_can_be_debited_without_sql(
    registry, prepared
):
    owner, permit = make_owner()
    repo = FakeRepo()
    owner.require_shared_admission()
    coordinator = authorize(owner, permit, repo)
    owner.bind_shared_admission(coordinator)
    reservation = owner.try_reserve_storage(now=10.0).reservation
    assert reservation is not None and reservation.start()
    if prepared:
        coordinator.prepare(reservation)
    else:
        reservation.hold_retirement()
    assert coordinator.owns_prepared_storage(reservation) is prepared
    assert coordinator.owns_prepared_storage(object()) is False
    retained = []
    try:
        with permit.operation(clock=clock):
            loan = acquire_storage_connection()
            channel = Channel()
            funding = capture_control_sql_funding(channel, loan)
            assert funding is not None
            try:
                with child() as supervisor:

                    def register(subject):
                        before = list(repo.events)
                        if prepared:
                            charge = funding.reserve_child_sql_session(
                                channel, loan, reservation=reservation, subject=subject
                            )
                            retained.append(charge)
                            charge.check()
                        else:
                            with pytest.raises(StorageAdmissionUnavailable):
                                funding.reserve_child_sql_session(
                                    channel, loan, reservation=reservation, subject=subject
                                )
                            assert owner.snapshot(now=10.0).storage_connection_loans == 1
                        assert repo.events == before

                    supervisor._receive_binding(timeout_s=30, before_ack=register)
            finally:
                assert loan.finish()
        if prepared:
            assert not reservation.finish_after_storage()
            assert coordinator.owns_prepared_storage(reservation) is False
            assert owner.snapshot(now=10.0).committed.storage_slots == 1
    finally:
        # This publisher-only fixture never opens SQL. Retire the unused fake
        # backend obligation after its actual child has joined, solely to stop
        # this isolated coordinator. No production charge exposes this API.
        reservation.finish_after_storage()
        for charge in retained:
            assert charge._loan.finish()
        if not prepared:
            assert reservation._retirement_hold.physical_ready
            assert reservation._retirement_hold.finish()
        assert until(lambda: coordinator.pending_count == 0)
        shutdown(coordinator)
