"""Original funded owner -> atomic durable fee -> one real opening per request."""

import hashlib
import json
import os
import signal
import sys
from contextlib import contextmanager
from dataclasses import asdict
from test.postgres.test_sql_intent_journal import api, marked, prepare, shared
from test.services.test_delivery_admission import observe
from test.services.test_resource_process_supervisor import CHILD
from uuid import uuid4

import pytest
from psycopg import sql

from cli_agent_orchestrator.models.sql_startup_permission import (
    SQL_BROKER_FD_ENV,
    SQL_STARTUP_PERMISSION_ENV,
)
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.services.resource_process_supervisor import (
    BINDING_FD_ENV,
    ResourceProcessSupervisor,
)

pytestmark = pytest.mark.postgres
TAIL = r"""
from contextlib import contextmanager
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.postgres import DatabaseTarget,connect
from cli_agent_orchestrator.services.legacy_memory_audit_repository import LegacyMemoryAuditConnectionManager
from cli_agent_orchestrator.utils import process_sql_origin as origin
import psycopg
values=json.loads(os.environ['SQL_BROKER_TARGET'])
target=DatabaseTarget(**values)
DatabaseTarget.from_settings=classmethod(lambda cls,*a,**kw:target)
marker=Path(os.environ['SQL_BROKER_RESULT']);path=os.environ['SQL_BROKER_PATH']
count=int(os.environ['SQL_BROKER_COUNT']);exhaust=os.environ['SQL_BROKER_EXHAUST']=='1'
calls=[];original=psycopg.connect;driver_barrier=None
def actual(**kwargs):
    calls.append(kwargs.get('application_name'))
    if driver_barrier is not None:driver_barrier.wait(timeout=10)
    return original(**kwargs)
psycopg.connect=actual
@contextmanager
def opening():
    if path=='native':
        with connect(target) as c:yield c
    elif path=='orm':
        c=database._native_orm_connection()
        try:yield c
        finally:c.close()
    else:
        manager=LegacyMemoryAuditConnectionManager(max_sessions=1)
        try:
            with manager.checkout(target,readonly=True) as c:yield c
        finally:manager.close()
try:
    publish_resource_binding(binding,first_sql_grant=True)
    if os.environ.get('SQL_BROKER_RELEASE'):
        import time
        deadline=time.monotonic()+10
        while not Path(os.environ['SQL_BROKER_RELEASE']).exists():
            if time.monotonic()>=deadline:raise AssertionError('release barrier timeout')
            time.sleep(0.005)
    with opening() as first:
        assert first.execute('SELECT 42').fetchone()[0]==42
        if os.environ.get('SQL_BROKER_PARALLEL')=='1':
            from concurrent.futures import ThreadPoolExecutor
            import threading
            barrier=threading.Barrier(count)
            driver_barrier=threading.Barrier(count)
            def worker(index):
                with opening() as current:
                    backend=current.execute('SELECT pg_backend_pid()').fetchone()[0]
                    barrier.wait(timeout=10)
                    assert current.execute('SELECT pg_backend_pid()').fetchone()[0]==backend
            with ThreadPoolExecutor(max_workers=count) as pool:
                list(pool.map(worker,range(count)))
        else:
            for _ in range(count):
                with opening() as current:
                    assert current.execute('SELECT 42').fetchone()[0]==42
        denied=False
        if exhaust:
            try:
                with opening():raise AssertionError('unfunded extra driver')
            except origin.SqlStartupPermissionUnavailable:denied=True
        assert first.execute('SELECT 42').fetchone()[0]==42
        origin.close_sql_broker()
        marker.write_text(json.dumps(dict(calls=len(calls),labels=calls,denied=denied,
            spent=origin._STATE.startup_spent,existing=True)))
    os._exit(23)
except BaseException as failure:
    marker.write_text(json.dumps(dict(error=type(failure).__name__,calls=len(calls))))
    os._exit(24)
"""
compile(TAIL, "funded-broker-child", "exec")


@contextmanager
def parent(target, units):
    owner, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
    assert observe(owner, units)
    reservation = owner.try_reserve_storage(now=10.0, storage_slots=units).reservation
    assert reservation is not None and reservation.start()
    reservation.hold_retirement()
    from cli_agent_orchestrator.clients.postgres import connect

    with permit.operation(clock=lambda: 10.0):
        with connect(target) as channel:
            try:
                yield owner, reservation, channel
            finally:
                reservation.finish_after_storage()


@contextmanager
def child(
    target, tmp_path, count, path="native", exhaust=False, parallel=False, wait_for_release=False
):
    digest = hashlib.sha256(json.dumps(target.identity, separators=(",", ":")).encode()).hexdigest()
    source = CHILD[: CHILD.index("mode=os.environ")]
    source = source.replace(
        "'d18bcbac-9c97-4408-baf0-24f7e2331486'", repr(target.store_id)
    ).replace("'a'*64", repr(digest))
    source = source.replace("'395141ce-9910-43f6-b4c4-9fe21790309e'", repr(str(uuid4()))) + TAIL
    values = {
        k: v
        for k, v in asdict(target).items()
        if k
        in {
            "host",
            "port",
            "database",
            "user",
            "schema",
            "store_id",
            "secret_file",
            "sslmode",
            "scope",
        }
    }
    values["secret_file"] = str(values["secret_file"])
    marker = tmp_path / "result.json"
    env = dict(os.environ)
    for key in (BINDING_FD_ENV, SQL_STARTUP_PERMISSION_ENV, SQL_BROKER_FD_ENV):
        env.pop(key, None)
    env.update(
        SQL_BROKER_TARGET=json.dumps(values),
        SQL_BROKER_RESULT=str(marker),
        SQL_BROKER_PATH=path,
        SQL_BROKER_COUNT=str(count),
        SQL_BROKER_EXHAUST="1" if exhaust else "0",
        SQL_BROKER_PARALLEL="1" if parallel else "0",
        SQL_BROKER_RELEASE=str(tmp_path / "release") if wait_for_release else "",
    )
    owner = ResourceProcessSupervisor.launch(
        [sys.executable, "-c", source],
        env=env,
        expected_store_id=target.store_id,
        expected_target_identity_sha256=digest,
        first_sql_grant=True,
        sql_broker=True,
    )
    try:
        yield owner, marker
    finally:
        if not owner._exited:
            owner.send_signal(signal.SIGTERM)
        owner.wait(timeout_s=30)
        owner.close()


@pytest.mark.parametrize(
    "path,count,exhaust,parallel",
    [
        ("native", 5, False, False),
        ("orm", 5, False, False),
        ("audit", 5, False, False),
        ("native", 20, True, False),
        ("native", 5, False, True),
    ],
)
def test_each_actual_opening_has_distinct_prepaid_unit_and_committed_mapping(
    shared, postgres_service, tmp_path, monkeypatch, path, count, exhaust, parallel
):
    m = api()
    target = shared[0]
    with marked(monkeypatch):
        repository, journal = prepare(shared, m, capacity=count + 2)
        with parent(target, count + 1) as (budget, reservation, channel):
            with postgres_service.connect_admin(target.database) as observer:
                with child(target, tmp_path, count, path, exhaust, parallel) as (owner, marker):
                    startup = owner.receive_binding_with_child_grant(
                        channel=channel,
                        repository=repository,
                        journal_id=journal,
                        intent_id=str(uuid4()),
                        destination=target,
                        session_reservation=reservation,
                        path=path,
                    )

                    def visible(intent):
                        row = observer.execute(
                            sql.SQL("SELECT session_id FROM {} WHERE intent_id=%s").format(
                                sql.Identifier(target.schema, "_cao_sql_child_session_fundings")
                            ),
                            (intent,),
                        ).fetchone()
                        assert row is not None
                        return str(row[0])

                    assert visible(startup.intent_id) == owner.child_sql_session_charge.session_id
                    original = repository.record_child_followup

                    def confirmed(*args, **kwargs):
                        result = original(*args, **kwargs)
                        assert visible(result.intent_id) == kwargs["session_charge"].session_id
                        assert not marker.exists()
                        return result

                    with monkeypatch.context() as patch:
                        patch.setattr(repository, "record_child_followup", confirmed)
                        for sequence in range(1, count + 1):
                            result = owner.serve_child_sql_request(
                                channel=channel,
                                repository=repository,
                                destination=target,
                                session_reservation=reservation,
                            )
                            assert result.sequence == sequence and result.ordinal == sequence + 1
                            assert (
                                budget.snapshot(now=10.0).storage_connection_loans == sequence + 2
                            )
                        if exhaust:
                            from cli_agent_orchestrator.clients.storage_admission import (
                                StorageAdmissionUnavailable,
                            )

                            with pytest.raises(StorageAdmissionUnavailable):
                                owner.serve_child_sql_request(
                                    channel=channel,
                                    repository=repository,
                                    destination=target,
                                    session_reservation=reservation,
                                )
                        else:
                            assert (
                                owner.serve_child_sql_request(
                                    channel=channel,
                                    repository=repository,
                                    destination=target,
                                    session_reservation=reservation,
                                )
                                is None
                            )
                    observation = owner.wait(timeout_s=30)
                    assert (
                        observation is not None and observation.returncode == 23
                    ), marker.read_text()
                    result = json.loads(marker.read_text())
                    assert result["calls"] == count + 1 and result["existing"] and result["spent"]
                    assert result["denied"] == exhaust
                    assert set(result["labels"]) == {startup.subject_sql_application_name}
                    charges = owner.child_sql_session_charges
                    assert len(charges) == len({c.session_id for c in charges}) == count + 1
                    assert reservation.finish() is False
        assert budget.snapshot(now=10.0).storage_connection_loans == count + 1
        assert budget.snapshot(now=10.0).committed.storage_slots == count + 1


@pytest.mark.parametrize(
    "fault",
    [
        "commit_ack",
        "before_insert",
        "after_commit",
        "reused_unit",
        "destination",
        "mapping_missing",
        "wrong_receipt",
    ],
)
def test_broker_failure_keeps_prepaid_debt_and_existing_session_usable(
    shared, postgres_service, tmp_path, monkeypatch, fault
):
    from dataclasses import replace

    from cli_agent_orchestrator.clients.postgres.connection import FundedSqlCommitUncertain
    from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
    from cli_agent_orchestrator.services import resource_process_supervisor as supervisor_module

    m = api()
    target = shared[0]
    with marked(monkeypatch):
        repository, journal = prepare(shared, m, capacity=4)
        with parent(target, 2) as (budget, reservation, channel):
            with child(target, tmp_path, 0, exhaust=True) as (owner, marker):
                owner.receive_binding_with_child_grant(
                    channel=channel,
                    repository=repository,
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    destination=target,
                    session_reservation=reservation,
                )
                transaction = channel.funded_transaction
                record = repository.record_child_followup
                query = repository._q

                @contextmanager
                def uncertain():
                    with transaction() as c:
                        yield c
                    raise FundedSqlCommitUncertain("controlled lost funding commit ACK")

                def refused(*a, **kw):
                    raise RuntimeError("controlled before INSERT")

                def reuse(*a, **kw):
                    kw["session_charge"] = owner.child_sql_session_charge
                    return record(*a, **kw)

                def missing_mapping(c, statement, values=()):
                    from types import SimpleNamespace

                    if "INSERT INTO {fundings}" in statement:
                        return SimpleNamespace(fetchone=lambda: None)
                    return query(c, statement, values)

                def wrong_receipt(*a, **kw):
                    value = record(*a, **kw)
                    return replace(value, sequence=value.sequence + 1)

                def send_failed(*a, **kw):
                    raise OSError("controlled reply loss after commit")

                with monkeypatch.context() as patch:
                    if fault == "commit_ack":
                        patch.setattr(channel, "funded_transaction", uncertain)
                    elif fault == "before_insert":
                        patch.setattr(repository, "record_child_followup", refused)
                    elif fault == "reused_unit":
                        patch.setattr(repository, "record_child_followup", reuse)
                    elif fault == "after_commit":
                        patch.setattr(supervisor_module, "_broker_send", send_failed)
                    elif fault == "mapping_missing":
                        patch.setattr(repository, "_q", missing_mapping)
                    elif fault == "wrong_receipt":
                        patch.setattr(repository, "record_child_followup", wrong_receipt)
                    expected = {
                        "commit_ack": m.SqlIntentCommitUncertain,
                        "before_insert": RuntimeError,
                        "reused_unit": StorageAdmissionUnavailable,
                        "after_commit": m.SqlFollowupAckUnavailableAfterCommit,
                        "destination": supervisor_module.ResourceProcessUnproven,
                        "mapping_missing": m.SchemaMismatch,
                        "wrong_receipt": m.SqlIntentCommitUncertain,
                    }[fault]
                    with pytest.raises(expected) as error:
                        owner.serve_child_sql_request(
                            channel=channel,
                            repository=repository,
                            destination=(
                                replace(target, user=target.user + "_foreign")
                                if fault == "destination"
                                else target
                            ),
                            session_reservation=reservation,
                        )
                if fault != "destination":
                    assert (
                        error.value.child_sql_session_charge is owner.child_sql_session_charges[-1]
                    )
                    assert len(owner.child_sql_session_charges) == 2
                else:
                    assert len(owner.child_sql_session_charges) == 1
                assert owner.wait(timeout_s=30).returncode == 23, marker.read_text()
                result = json.loads(marker.read_text())
                assert result["calls"] == 1 and result["denied"] and result["existing"]
                with postgres_service.connect_admin(target.database) as observer:
                    counts = observer.execute(
                        sql.SQL(
                            "SELECT (SELECT count(*) FROM {}),(SELECT count(*) FROM {})"
                        ).format(
                            sql.Identifier(target.schema, "_cao_sql_child_followups"),
                            sql.Identifier(target.schema, "_cao_sql_child_session_fundings"),
                        )
                    ).fetchone()
                    assert counts == (
                        (1, 2)
                        if fault in {"commit_ack", "after_commit", "wrong_receipt"}
                        else (0, 1)
                    )
                assert reservation.finish() is False
        assert budget.snapshot(now=10.0).storage_connection_loans == (
            1 if fault == "destination" else 2
        )


def test_idle_poll_does_not_close_or_charge_the_persistent_channel(shared, tmp_path, monkeypatch):
    m = api()
    target = shared[0]
    with marked(monkeypatch):
        repository, journal = prepare(shared, m, capacity=3)
        with parent(target, 2) as (budget, reservation, channel):
            with child(target, tmp_path, 1, wait_for_release=True) as (owner, marker):
                owner.receive_binding_with_child_grant(
                    channel=channel,
                    repository=repository,
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    destination=target,
                    session_reservation=reservation,
                )
                for timeout in (0, 0.01):
                    result = owner.serve_child_sql_request(
                        channel=channel,
                        repository=repository,
                        destination=target,
                        session_reservation=reservation,
                        timeout_s=timeout,
                    )
                    assert result is not None and result.value == "no_request"
                    assert not owner._broker_failed and owner._broker_sequence == 0
                    assert budget.snapshot(now=10.0).storage_connection_loans == 2
                (tmp_path / "release").write_text("go")
                result = owner.serve_child_sql_request(
                    channel=channel,
                    repository=repository,
                    destination=target,
                    session_reservation=reservation,
                )
                assert result.sequence == 1
                assert (
                    owner.serve_child_sql_request(
                        channel=channel,
                        repository=repository,
                        destination=target,
                        session_reservation=reservation,
                    )
                    is None
                )
                assert owner.wait(timeout_s=30).returncode == 23, marker.read_text()


def test_foreign_task_cannot_poll_or_consume_original_funded_broker(shared, tmp_path, monkeypatch):
    import asyncio

    from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable

    m = api()
    target = shared[0]
    with marked(monkeypatch):
        repository, journal = prepare(shared, m, capacity=3)
        with parent(target, 2) as (_, reservation, channel):
            with child(target, tmp_path, 1, wait_for_release=True) as (owner, marker):
                owner.receive_binding_with_child_grant(
                    channel=channel,
                    repository=repository,
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    destination=target,
                    session_reservation=reservation,
                )

                async def foreign():
                    with pytest.raises(StorageAdmissionUnavailable):
                        owner.serve_child_sql_request(
                            channel=channel,
                            repository=repository,
                            destination=target,
                            session_reservation=reservation,
                            timeout_s=0,
                        )

                asyncio.run(foreign())
                assert not owner._broker_failed and owner._broker_sequence == 0
                (tmp_path / "release").write_text("go")
                assert (
                    owner.serve_child_sql_request(
                        channel=channel,
                        repository=repository,
                        destination=target,
                        session_reservation=reservation,
                    ).sequence
                    == 1
                )
                assert (
                    owner.serve_child_sql_request(
                        channel=channel,
                        repository=repository,
                        destination=target,
                        session_reservation=reservation,
                    )
                    is None
                )
                assert owner.wait(timeout_s=30).returncode == 23, marker.read_text()


def test_sql4_funding_mapping_enforces_fk_exclusivity_unique_units_and_effective_acl(
    shared, postgres_service, tmp_path, monkeypatch
):
    import psycopg

    m = api()
    target = shared[0]
    with marked(monkeypatch):
        repository, journal = prepare(shared, m, capacity=4)
        with parent(target, 2) as (_, reservation, channel):
            with child(target, tmp_path, 1) as (owner, marker):
                startup = owner.receive_binding_with_child_grant(
                    channel=channel,
                    repository=repository,
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    destination=target,
                    session_reservation=reservation,
                )
                followup = owner.serve_child_sql_request(
                    channel=channel,
                    repository=repository,
                    destination=target,
                    session_reservation=reservation,
                )
                assert (
                    owner.serve_child_sql_request(
                        channel=channel,
                        repository=repository,
                        destination=target,
                        session_reservation=reservation,
                    )
                    is None
                )
                assert owner.wait(timeout_s=30).returncode == 23, marker.read_text()
                with postgres_service.connect_admin(target.database) as admin:
                    funding = sql.Identifier(target.schema, "_cao_sql_child_session_fundings")
                    insert = sql.SQL(
                        "INSERT INTO {}(intent_id,session_id,startup_intent_id,followup_intent_id) VALUES(%s,%s,%s,%s)"
                    ).format(funding)
                    for left, right in [(None, None), (startup.intent_id, followup.intent_id)]:
                        with pytest.raises(psycopg.errors.CheckViolation):
                            with admin.transaction():
                                admin.execute(insert, (str(uuid4()), str(uuid4()), left, right))
                    missing = str(uuid4())
                    with pytest.raises(psycopg.errors.ForeignKeyViolation):
                        with admin.transaction():
                            admin.execute(insert, (missing, str(uuid4()), None, missing))
                    # An owner-only extra descriptive row tests SQL constraints;
                    # it never finances or grants an opening.
                    another = str(uuid4())
                    admin.execute(
                        sql.SQL(
                            """INSERT INTO {}(intent_id,journal_id,startup_intent_id,ordinal,
                        sequence,path,destination_store_id,destination_scope,destination_sha256)
                        SELECT %s,journal_id,startup_intent_id,ordinal+1,sequence+1,path,
                        destination_store_id,destination_scope,destination_sha256 FROM {} WHERE intent_id=%s"""
                        ).format(
                            sql.Identifier(target.schema, "_cao_sql_child_followups"),
                            sql.Identifier(target.schema, "_cao_sql_child_followups"),
                        ),
                        (another, followup.intent_id),
                    )
                    with pytest.raises(psycopg.errors.UniqueViolation):
                        with admin.transaction():
                            admin.execute(
                                insert,
                                (another, owner.child_sql_session_charge.session_id, None, another),
                            )
                    acl = admin.execute(
                        """SELECT has_table_privilege(%s,c.oid,'SELECT'),has_table_privilege(%s,c.oid,'INSERT'),
                        has_table_privilege(%s,c.oid,'UPDATE'),has_table_privilege(%s,c.oid,'DELETE'),
                        has_table_privilege(%s,c.oid,'TRUNCATE'),has_any_column_privilege(%s,c.oid,'REFERENCES')
                        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                        WHERE n.nspname=%s AND c.relname='_cao_sql_child_session_fundings' """,
                        (target.user,) * 6 + (target.schema,),
                    ).fetchone()
                    assert acl == (True, True, False, False, False, False)
                    assert admin.execute(
                        sql.SQL("SELECT count(*) FROM {}").format(funding)
                    ).fetchone() == (2,)
