"""Real PostgreSQL/owned child checks for parent prepayment before identity ACK."""

import copy
import json
import os
from contextlib import contextmanager
from test.postgres.test_child_sql_opening import pending_child
from test.postgres.test_sql_intent_journal import api, marked, prepare, shared
from test.services.test_delivery_admission import observe
from uuid import uuid4

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

pytestmark = pytest.mark.postgres


@contextmanager
def parent(target, *, slots=1):
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    assert observe(owner, slots)
    reservation = owner.try_reserve_storage(now=10.0).reservation
    assert reservation is not None and reservation.start()
    reservation.hold_retirement()
    with permit.operation(clock=lambda: 10.0):
        with connect(target) as channel:
            try:
                yield owner, reservation, channel
            finally:
                reservation.finish_after_storage()


def test_parent_charge_precedes_commit_ack_and_actual_child_tcp(
    shared, postgres_service, tmp_path, monkeypatch
):
    module = api()
    target = shared[0]
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        request = str(uuid4())
        with parent(target) as (budget, reservation, channel):
            with postgres_service.connect_admin(target.database) as observer:
                with pending_child(target, tmp_path) as (supervisor, marker, _):
                    original = repository.record_child_opening

                    def checked(connection, **kwargs):
                        charge = supervisor.child_sql_session_charge
                        assert charge is not None
                        charge.check()
                        assert budget.snapshot(now=10.0).storage_connection_loans == 2
                        assert budget.snapshot(now=10.0).committed.storage_slots == 1
                        assert not marker.exists()
                        receipt = original(connection, **kwargs)
                        retained = observer.execute(
                            sql.SQL("SELECT count(*) FROM {} WHERE intent_id=%s").format(
                                sql.Identifier(target.schema, "_cao_sql_child_openings")
                            ),
                            (request,),
                        ).fetchone()[0]
                        assert retained == 1 and not marker.exists()
                        assert charge.subject is kwargs["subject"].binding
                        return receipt

                    with monkeypatch.context() as patch:
                        patch.setattr(repository, "record_child_opening", checked)
                        receipt = supervisor.receive_binding_with_child_intent(
                            channel=channel,
                            repository=repository,
                            journal_id=journal,
                            intent_id=request,
                            destination=target,
                            session_reservation=reservation,
                        )
                    assert supervisor.wait(timeout_s=30).returncode == 23
                    actual = json.loads(marker.read_text())
                    assert actual["label"] == receipt.subject_sql_application_name
                    assert not reservation.finish_after_storage()
        after = budget.snapshot(now=10.0)
        assert after.storage_connection_loans == after.committed.storage_slots == 1
        assert after.inflight == 1


@pytest.mark.parametrize("failure", ["capacity", "commit_ack", "postcommit", "base_exception"])
def test_registration_failure_retains_parent_debit_and_never_grants_child_tcp(
    shared, tmp_path, monkeypatch, failure
):
    module = api()
    target = shared[0]
    with marked(monkeypatch):
        repository, journal = prepare(shared, module, capacity=0 if failure == "capacity" else 4)
        with parent(target) as (budget, reservation, channel):
            with pending_child(target, tmp_path) as (supervisor, marker, _):
                original = repository.record_child_opening
                request = str(uuid4())
                if failure == "commit_ack":
                    transaction = channel.funded_transaction

                    @contextmanager
                    def lost_ack():
                        from cli_agent_orchestrator.clients.postgres.connection import (
                            FundedSqlCommitUncertain,
                        )

                        with transaction() as current:
                            yield current
                        raise FundedSqlCommitUncertain("controlled lost acknowledgement")

                    monkeypatch.setattr(channel, "funded_transaction", lost_ack)
                elif failure == "postcommit":

                    def committed_then_failed(connection, **kwargs):
                        receipt = original(connection, **kwargs)
                        raise module.SqlChildAckUnavailableAfterCommit(receipt)

                    monkeypatch.setattr(repository, "record_child_opening", committed_then_failed)
                elif failure == "base_exception":

                    def failed(*args, **kwargs):
                        raise BaseException("controlled interruption before SQL")

                    monkeypatch.setattr(repository, "record_child_opening", failed)
                expected = {
                    "capacity": module.SqlIntentCapacityUnavailable,
                    "commit_ack": module.SqlIntentCommitUncertain,
                    "postcommit": module.SqlChildAckUnavailableAfterCommit,
                    "base_exception": BaseException,
                }[failure]
                with pytest.raises(expected) as caught:
                    supervisor.receive_binding_with_child_intent(
                        channel=channel,
                        repository=repository,
                        journal_id=journal,
                        intent_id=request,
                        destination=target,
                        session_reservation=reservation,
                    )
                charge = supervisor.child_sql_session_charge
                assert charge is not None
                assert caught.value.child_sql_session_charge is charge
                assert budget.snapshot(now=10.0).storage_connection_loans == 2
                assert not marker.exists()
                assert not reservation.finish_after_storage()
        assert budget.snapshot(now=10.0).committed.storage_slots == 1
        assert budget.snapshot(now=10.0).storage_connection_loans == 1


def test_copy_of_native_parent_channel_cannot_charge_data_reservation(
    shared, tmp_path, monkeypatch
):
    module = api()
    target = shared[0]
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        with parent(target) as (budget, reservation, channel):
            with pending_child(target, tmp_path) as (supervisor, marker, _):
                with pytest.raises(StorageAdmissionUnavailable):
                    supervisor.receive_binding_with_child_intent(
                        channel=copy.copy(channel),
                        repository=repository,
                        journal_id=journal,
                        intent_id=str(uuid4()),
                        destination=target,
                        session_reservation=reservation,
                    )
                assert supervisor.child_sql_session_charge is None and not marker.exists()
                assert budget.snapshot(now=10.0).storage_connection_loans == 1
                assert reservation.finish() is False
                assert reservation._retirement_hold.physical_ready
                assert reservation._retirement_hold.finish()
        assert budget.snapshot(now=10.0).inflight == 0


def test_failed_identity_ack_retains_both_confirmed_row_and_original_parent_charge(
    shared, tmp_path, monkeypatch
):
    import socket

    module = api()
    target = shared[0]
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        with parent(target) as (budget, reservation, channel):
            with pending_child(target, tmp_path) as (supervisor, marker, _):
                original_send = socket.socket.send

                def failed_send(sock, payload, *args, **kwargs):
                    if sock is supervisor._channel and payload == b"1":
                        raise OSError("controlled failure before ACK send")
                    return original_send(sock, payload, *args, **kwargs)

                with monkeypatch.context() as patch:
                    patch.setattr(socket.socket, "send", failed_send)
                    with pytest.raises(module.SqlChildAckUnavailableAfterCommit) as caught:
                        supervisor.receive_binding_with_child_intent(
                            channel=channel,
                            repository=repository,
                            journal_id=journal,
                            intent_id=str(uuid4()),
                            destination=target,
                            session_reservation=reservation,
                        )
                charge = supervisor.child_sql_session_charge
                assert caught.value.child_sql_session_charge is charge
                assert caught.value.receipt.subject is charge.subject
                assert not marker.exists()
                assert budget.snapshot(now=10.0).storage_connection_loans == 2
        assert budget.snapshot(now=10.0).storage_connection_loans == 1
        assert budget.snapshot(now=10.0).committed.storage_slots == 1


def test_real_shared_operation_retains_its_charge_when_child_registration_is_refused(
    shared, postgres_service, tmp_path, monkeypatch
):
    from test.services.test_shared_delivery_admission import principal, until

    from cli_agent_orchestrator.services import shared_delivery_admission as coordination

    module = api()
    target, resources, _, _ = shared
    isolated = coordination._NodeRegistry()
    monkeypatch.setattr(coordination, "_REGISTRY", isolated)
    with marked(monkeypatch):
        repository, journal = prepare(shared, module, capacity=0)
        owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
        assert observe(owner, 7)
        owner.require_shared_admission()
        with permit.operation(clock=lambda: 10.0):
            coordinator = coordination.authorize_shared_delivery(
                principal=principal(),
                repository=resources,
                owner=owner,
                control=permit,
                retirement=lambda: False,
                clock=lambda: 10.0,
                retry_interval_s=0.01,
            )
        owner.bind_shared_admission(coordinator)
        reservation = owner.try_reserve_storage(now=10.0).reservation
        assert reservation is not None and reservation.start()
        coordinator.prepare(reservation)
        domain = coordinator.coverage.storage_domain_ids[0]
        with postgres_service.connect_admin(target.database) as observer:

            def usage():
                return observer.execute(
                    sql.SQL("SELECT committed FROM {} WHERE domain_id=%s").format(
                        sql.Identifier(target.schema, "_cao_resource_usage")
                    ),
                    (domain,),
                ).fetchone()[0]

            assert usage() == 2  # Parent metadata1 and original data operation1.
            charge = None
            try:
                with permit.operation(clock=lambda: 10.0):
                    with connect(target) as channel:
                        with pending_child(target, tmp_path) as (supervisor, marker, denied):
                            with pytest.raises(module.SqlIntentCapacityUnavailable):
                                supervisor.receive_binding_with_child_intent(
                                    channel=channel,
                                    repository=repository,
                                    journal_id=journal,
                                    intent_id=str(uuid4()),
                                    destination=target,
                                    session_reservation=reservation,
                                )
                            charge = supervisor.child_sql_session_charge
                            assert charge is not None
                            assert supervisor.wait(timeout_s=30) is None
                            assert supervisor._wait_status == (os.CLD_EXITED, 24)
                            assert denied.exists() and not marker.exists()
                            assert reservation.finish() is False
                            assert usage() == 2
                            assert not reservation._retirement_hold.physical_ready
            finally:
                # This exact source-bound child rejected the identity gate
                # before its only SQL callback. No TCP effect exists here.
                # Fixture cleanup retires its unused loan, not a production
                # absence/custody path or arbitrary historical obligation.
                reservation.finish_after_storage()
                if charge is not None:
                    assert charge._loan.finish()
                assert until(lambda: coordinator.pending_count == 0)
                coordinator.drain(5.0)
                assert not coordinator.worker_alive
            assert usage() == 1
