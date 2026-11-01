"""Prefunded SQL control channels and durable pre-TCP intent contracts."""

import asyncio
import os
import select
from concurrent.futures import ThreadPoolExecutor
from copy import copy
from dataclasses import replace

import pytest

from cli_agent_orchestrator.clients.postgres import Connection, connect
from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

pytestmark = pytest.mark.postgres


def funded_api():
    assert callable(
        getattr(Connection, "funded_transaction", None)
    ), "missing prefunded SQL channel"


@pytest.fixture
def control_connection(postgres_targets):
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    with permit.operation(clock=lambda: 10.0):
        with connect(postgres_targets.main) as connection:
            yield owner, connection
    assert owner.snapshot(now=10.0).storage_connection_loans == 0


def test_funded_transaction_uses_only_the_existing_raw_connection(control_connection, monkeypatch):
    import psycopg

    funded_api()
    owner, connection = control_connection
    original_pid = connection.execute("SELECT pg_backend_pid()").fetchone()[0]

    def forbidden(**kwargs):
        pytest.fail("prefunded channel opened another TCP connection")

    with monkeypatch.context() as patch:
        patch.setattr(psycopg, "connect", forbidden)
        with connection.funded_transaction() as session:
            assert session is connection
            assert session.execute("SELECT pg_backend_pid(),42").fetchone() == (original_pid, 42)
        assert not connection.in_transaction
        assert owner.snapshot(now=10.0).control_connection_loans == 1


@pytest.mark.parametrize("facade", ["copy", "borrow"])
def test_a_facade_cannot_adopt_another_connections_funding(control_connection, facade):
    funded_api()
    _, connection = control_connection
    other = (
        copy(connection)
        if facade == "copy"
        else Connection.borrow(connection.raw, connection.target)
    )
    with pytest.raises(StorageAdmissionUnavailable):
        with other.funded_transaction():
            pytest.fail("facade adopted an original connection's funding")
    assert connection.execute("SELECT 42").fetchone()[0] == 42


def test_an_unfunded_legacy_connection_cannot_act_as_the_control_channel(postgres_targets):
    funded_api()
    with connect(postgres_targets.main) as connection:
        with pytest.raises(StorageAdmissionUnavailable):
            with connection.funded_transaction():
                pytest.fail("legacy connection was accepted as prefunded control")


def test_a_funded_channel_cannot_escape_to_another_thread(control_connection):
    funded_api()
    _, connection = control_connection

    def foreign():
        with pytest.raises(StorageAdmissionUnavailable):
            with connection.funded_transaction():
                pytest.fail("funded channel escaped its physical worker")

    with ThreadPoolExecutor(max_workers=1) as worker:
        worker.submit(foreign).result(timeout=15)


def test_a_funded_channel_does_not_acknowledge_inside_an_existing_transaction(control_connection):
    funded_api()
    _, connection = control_connection
    with connection.transaction():
        with pytest.raises(StorageAdmissionUnavailable):
            with connection.funded_transaction():
                pytest.fail("a caller-owned transaction could still roll back this acknowledgement")
        assert connection.in_transaction


def test_a_released_loan_cannot_fund_subsequent_sql(control_connection):
    funded_api()
    _, connection = control_connection
    assert connection._storage_loan.finish()
    with pytest.raises(StorageAdmissionUnavailable):
        with connection.funded_transaction():
            pytest.fail("released physical loan still authorized SQL")


def test_funding_factory_cannot_reassign_an_existing_loan_to_a_copy(control_connection):
    from cli_agent_orchestrator.clients.storage_admission import capture_control_sql_funding

    funded_api()
    _, connection = control_connection
    facade = copy(connection)
    facade._control_sql_funding = capture_control_sql_funding(facade, connection._storage_loan)
    with pytest.raises(StorageAdmissionUnavailable):
        with facade.funded_transaction():
            pytest.fail("funding factory reassigned another connection's paid loan")


def test_funding_cannot_follow_a_changed_connection_target(control_connection):
    funded_api()
    _, connection = control_connection
    target = connection.target
    try:
        connection.target = replace(target, user="other_runtime")
        with pytest.raises(StorageAdmissionUnavailable):
            with connection.funded_transaction():
                pytest.fail("funding followed a retargeted connection")
    finally:
        connection.target = target


def test_a_driver_suppressed_rollback_is_not_a_funded_commit_acknowledgement(control_connection):
    from psycopg import Rollback

    funded_api()
    _, connection = control_connection
    with pytest.raises(StorageAdmissionUnavailable):
        with connection.funded_transaction():
            connection.execute("SELECT 42")
            raise Rollback()
    assert not connection.in_transaction


def test_a_different_task_cannot_adopt_the_control_channel(control_connection):
    _, connection = control_connection

    async def foreign():
        with pytest.raises(StorageAdmissionUnavailable):
            with connection.funded_transaction():
                pytest.fail("another task adopted the paid channel")

    asyncio.run(foreign())


def test_expired_worker_scope_keeps_its_loan_but_cannot_issue_sql(postgres_targets):
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    connection = None
    try:
        with permit.operation(clock=lambda: 10.0):
            connection = connect(postgres_targets.main)
        assert owner.snapshot(now=10.0).control_connection_loans == 1
        with pytest.raises(StorageAdmissionUnavailable):
            with connection.funded_transaction():
                pytest.fail("expired worker scope issued a SQL acknowledgement")
    finally:
        if connection is not None:
            connection.close()
    assert owner.snapshot(now=10.0).control_inflight == 0


def test_fork_cannot_adopt_the_parent_control_connection(control_connection):
    _, connection = control_connection
    reader, writer = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(reader)
        try:
            with pytest.raises(StorageAdmissionUnavailable):
                with connection.funded_transaction():
                    os._exit(23)
            os.write(writer, b"denied")
            os._exit(0)
        except BaseException:
            os._exit(24)
    os.close(writer)
    pin = os.pidfd_open(child)
    ready = []
    try:
        ready, _, _ = select.select([reader], [], [], 15)
        assert ready and os.read(reader, 16) == b"denied"
    finally:
        os.close(reader)
        if not ready:
            import signal

            signal.pidfd_send_signal(pin, signal.SIGKILL)
        os.close(pin)
        assert os.waitpid(child, 0) == (child, 0)
    assert connection.execute("SELECT 42").fetchone()[0] == 42
