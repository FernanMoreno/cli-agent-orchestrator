"""Paid parent -> committed intent -> authenticated permission -> one real TCP."""

import json
import os
import signal
import sys
from contextlib import contextmanager
from dataclasses import asdict
from test.postgres.test_child_sql_opening import pending_child
from test.postgres.test_child_sql_session_charge import parent
from test.postgres.test_sql_intent_journal import api, marked, prepare, shared
from test.services.test_resource_process_supervisor import CHILD
from uuid import uuid4

import pytest
from psycopg import sql

from cli_agent_orchestrator.models.sql_startup_permission import SQL_STARTUP_PERMISSION_ENV
from cli_agent_orchestrator.services.resource_process_supervisor import (
    BINDING_FD_ENV,
    ResourceProcessSupervisor,
    ResourceProcessUnproven,
)

pytestmark = pytest.mark.postgres

TAIL = r"""
from contextlib import contextmanager
from dataclasses import replace
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.postgres import DatabaseTarget,connect
from cli_agent_orchestrator.services.legacy_memory_audit_repository import LegacyMemoryAuditConnectionManager
from cli_agent_orchestrator.utils import process_sql_origin as origin
import psycopg
config=json.loads(os.environ['STARTUP_SQL_TARGET'])
target=DatabaseTarget(**config)
DatabaseTarget.from_settings=classmethod(lambda cls,*a,**kw:target)
result=Path(os.environ['STARTUP_SQL_RESULT'])
path=os.environ['STARTUP_SQL_PATH']
mode=os.environ.get('STARTUP_SQL_MODE','ok')
@contextmanager
def opening(selected,target=target):
    if selected=='native':
        with connect(target) as connection:yield connection
    elif selected=='orm':
        raw=database._native_orm_connection()
        try:yield raw
        finally:raw.close()
    else:
        manager=LegacyMemoryAuditConnectionManager(max_sessions=1)
        try:
            with manager.checkout(target,readonly=True) as connection:yield connection
        finally:manager.close()
pre=False
try:
    try:
        with opening(path):raise AssertionError('SQL before authenticated permission')
    except origin.SqlStartupPermissionUnavailable:pre=True
    publish_resource_binding(binding,first_sql_grant=True)
    if mode in ('driver_failure','wrong_target'):
        calls=[]
        def forbidden(**kwargs):
            calls.append(True)
            if mode=='driver_failure':raise psycopg.OperationalError('controlled driver entry')
            raise AssertionError('wrong target reached physical driver')
        psycopg.connect=forbidden
        if mode=='driver_failure':
            try:
                with opening('native'):raise AssertionError('driver failure lost')
            except psycopg.OperationalError:pass
        else:
            try:
                with opening('native',replace(target,user=target.user+'_foreign')):raise AssertionError('wrong target accepted')
            except origin.SqlStartupPermissionUnavailable:pass
        try:
            with opening('native'):raise AssertionError('permission refunded after failure')
        except origin.SqlStartupPermissionUnavailable:pass
        assert len(calls)==(1 if mode=='driver_failure' else 0)
        result.write_text(json.dumps(dict(pre=pre,first=False,denied=True,driver_calls=len(calls))))
    else:
        with opening(path) as connection:
            row=connection.execute("SELECT pg_backend_pid(),current_setting('application_name')").fetchone()
            def forbidden(**kwargs):raise AssertionError('second TCP reached driver')
            psycopg.connect=forbidden
            denied=[]
            for selected in ('native','orm','audit'):
                try:
                    with opening(selected):raise AssertionError('second physical opening accepted')
                except origin.SqlStartupPermissionUnavailable:denied.append(selected)
            assert connection.execute('SELECT 42').fetchone()[0]==42
            result.write_text(json.dumps(dict(pre=pre,first=True,pid=row[0],label=row[1],denied=denied)))
    os._exit(23)
except BaseException as failure:
    result.write_text(json.dumps(dict(pre=pre,first=False,error=type(failure).__name__)))
    os._exit(24)
"""
compile(TAIL, "sql-startup-physical-tail", "exec")


@contextmanager
def sql_child(target, tmp_path, *, path="native", mode="ok"):
    import hashlib

    digest = hashlib.sha256(json.dumps(target.identity, separators=(",", ":")).encode()).hexdigest()
    source = CHILD[: CHILD.index("mode=os.environ")]
    source = source.replace(
        "'d18bcbac-9c97-4408-baf0-24f7e2331486'", repr(target.store_id)
    ).replace("'a'*64", repr(digest))
    source = source.replace("'395141ce-9910-43f6-b4c4-9fe21790309e'", repr(str(uuid4()))) + TAIL
    compile(source, "sql-startup-owned-child", "exec")
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
    marker = tmp_path / "startup-sql.json"
    environment = dict(os.environ)
    environment.pop(BINDING_FD_ENV, None)
    environment.pop(SQL_STARTUP_PERMISSION_ENV, None)
    environment.update(
        STARTUP_SQL_TARGET=json.dumps(values),
        STARTUP_SQL_RESULT=str(marker),
        STARTUP_SQL_PATH=path,
        STARTUP_SQL_MODE=mode,
    )
    owner = ResourceProcessSupervisor.launch(
        [sys.executable, "-c", source],
        expected_store_id=target.store_id,
        expected_target_identity_sha256=digest,
        env=environment,
        first_sql_grant=True,
    )
    try:
        yield owner, marker
    finally:
        if not owner._exited:
            owner.send_signal(signal.SIGTERM)
        owner.wait(timeout_s=30)
        owner.close()


@pytest.mark.parametrize("path", ["native", "orm", "audit"])
def test_actual_adapter_opens_one_backend_only_after_debit_and_independent_commit_observation(
    shared, postgres_service, tmp_path, monkeypatch, path
):
    module = api()
    target = shared[0]
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        request = str(uuid4())
        with parent(target) as (budget, reservation, channel):
            with postgres_service.connect_admin(target.database) as observer:
                with sql_child(target, tmp_path, path=path) as (owner, marker):
                    original = repository.record_child_opening

                    def checked(connection, **kwargs):
                        charge = owner.child_sql_session_charge
                        charge.check()
                        assert budget.snapshot(now=10.0).storage_connection_loans == 2
                        assert not marker.exists()
                        receipt = original(connection, **kwargs)
                        row = observer.execute(
                            sql.SQL(
                                "SELECT subject_process_pid,path FROM {} WHERE intent_id=%s"
                            ).format(sql.Identifier(target.schema, "_cao_sql_child_openings")),
                            (request,),
                        ).fetchone()
                        assert row == (owner._process.pid, path) and not marker.exists()
                        return receipt

                    with monkeypatch.context() as patch:
                        patch.setattr(repository, "record_child_opening", checked)
                        receipt = owner.receive_binding_with_child_grant(
                            channel=channel,
                            repository=repository,
                            journal_id=journal,
                            intent_id=request,
                            destination=target,
                            session_reservation=reservation,
                            path=path,
                        )
                    observation = owner.wait(timeout_s=30)
                    assert observation is not None and observation.returncode == 23, (
                        marker.read_text() if marker.exists() else "no result"
                    )
                    result = json.loads(marker.read_text())
                    assert result["pre"] and result["first"]
                    assert result["denied"] == ["native", "orm", "audit"]
                    assert result["label"] == receipt.subject_sql_application_name
                    assert budget.snapshot(now=10.0).committed.storage_slots == 1
                    assert reservation.finish() is False
        assert budget.snapshot(now=10.0).committed.storage_slots == 1
        assert budget.snapshot(now=10.0).storage_connection_loans == 1


@pytest.mark.parametrize("mode", ["driver_failure", "wrong_target"])
def test_failed_or_wrong_first_opening_never_refunds_the_original_permission(
    shared, tmp_path, monkeypatch, mode
):
    module = api()
    target = shared[0]
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        with parent(target) as (budget, reservation, channel):
            with sql_child(target, tmp_path, mode=mode) as (owner, marker):
                owner.receive_binding_with_child_grant(
                    channel=channel,
                    repository=repository,
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    destination=target,
                    session_reservation=reservation,
                )
                assert owner.wait(timeout_s=30).returncode == 23, marker.read_text()
                result = json.loads(marker.read_text())
                assert result["pre"] and not result["first"] and result["denied"]
                assert result["driver_calls"] == (1 if mode == "driver_failure" else 0)
        assert budget.snapshot(now=10.0).committed.storage_slots == 1


@pytest.mark.parametrize("failure", ["capacity", "commit_ack"])
def test_parent_refusal_or_uncertain_commit_never_issues_the_permission(
    shared, tmp_path, monkeypatch, failure
):
    module = api()
    target = shared[0]
    with marked(monkeypatch):
        repository, journal = prepare(shared, module, capacity=0 if failure == "capacity" else 4)
        with parent(target) as (budget, reservation, channel):
            transaction = channel.funded_transaction

            @contextmanager
            def uncertain():
                from cli_agent_orchestrator.clients.postgres.connection import (
                    FundedSqlCommitUncertain,
                )

                with transaction() as current:
                    yield current
                raise FundedSqlCommitUncertain("controlled lost commit ACK")

            with sql_child(target, tmp_path) as (owner, marker):
                with monkeypatch.context() as patch:
                    if failure == "commit_ack":
                        patch.setattr(channel, "funded_transaction", uncertain)
                    with pytest.raises(
                        module.SqlIntentCapacityUnavailable
                        if failure == "capacity"
                        else module.SqlIntentCommitUncertain
                    ) as caught:
                        owner.receive_binding_with_child_grant(
                            channel=channel,
                            repository=repository,
                            journal_id=journal,
                            intent_id=str(uuid4()),
                            destination=target,
                            session_reservation=reservation,
                        )
                assert owner.wait(timeout_s=30) is None
                assert owner._wait_status == (os.CLD_EXITED, 24)
                assert caught.value.child_sql_session_charge is owner.child_sql_session_charge
                assert not json.loads(marker.read_text())["first"]
        assert budget.snapshot(now=10.0).storage_connection_loans == 1


def test_actual_bridge_does_not_silently_enable_a_partially_funded_startup(
    shared, tmp_path, monkeypatch
):
    target = shared[0]
    original = ResourceProcessSupervisor.launch

    def strict(argv, **kwargs):
        return original(argv, **kwargs, first_sql_grant=True)

    with monkeypatch.context() as patch:
        patch.setattr(ResourceProcessSupervisor, "launch", staticmethod(strict))
        with pending_child(target, tmp_path) as (owner, marker, denied):
            with pytest.raises(ResourceProcessUnproven):
                owner.receive_binding(timeout_s=30)
            assert owner.wait(timeout_s=30) is None
            assert owner._wait_status == (os.CLD_EXITED, 24)
            assert denied.exists() and not marker.exists()
            assert owner.child_sql_session_charge is None


def test_emitting_a_paid_packet_is_not_evidence_that_the_child_accepted_or_opened_sql(
    shared, tmp_path, monkeypatch
):
    import socket

    module = api()
    target = shared[0]
    with marked(monkeypatch):
        repository, journal = prepare(shared, module)
        with parent(target) as (budget, reservation, channel):
            with sql_child(target, tmp_path) as (owner, marker):
                original = socket.socket.send

                def corrupted(sock, payload, *args, **kwargs):
                    if sock is not owner._channel:
                        return original(sock, payload, *args, **kwargs)
                    packet = json.loads(payload)
                    label = packet["sql_application_name"]
                    packet["sql_application_name"] = label[:-1] + ("0" if label[-1] != "0" else "1")
                    replaced = json.dumps(packet, separators=(",", ":")).encode()
                    assert len(replaced) == len(payload)
                    return original(sock, replaced, *args, **kwargs)

                with monkeypatch.context() as patch:
                    patch.setattr(socket.socket, "send", corrupted)
                    receipt = owner.receive_binding_with_child_grant(
                        channel=channel,
                        repository=repository,
                        journal_id=journal,
                        intent_id=str(uuid4()),
                        destination=target,
                        session_reservation=reservation,
                    )
                assert owner.wait(timeout_s=30).returncode == 24
                assert receipt.subject is owner.child_sql_session_charge.subject
                assert not json.loads(marker.read_text())["first"]
                assert budget.snapshot(now=10.0).storage_connection_loans == 2
                assert reservation.finish() is False
        assert budget.snapshot(now=10.0).storage_connection_loans == 1
