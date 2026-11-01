"""Real PostgreSQL presence and mutable names are not generation-close witnesses."""

import hashlib
import json
import os
import signal
import sys
import time
from dataclasses import asdict

import pytest

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.services.resource_process_supervisor import ResourceProcessSupervisor
from cli_agent_orchestrator.utils import process_sql_origin as origin

pytestmark = pytest.mark.postgres

CHILD = r"""
import json,os,psutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import psycopg
from cli_agent_orchestrator.clients.postgres import DatabaseTarget
from cli_agent_orchestrator.services.admission_bootstrap import capture_server_binding
from cli_agent_orchestrator.services.resource_process_supervisor import publish_resource_binding
target=DatabaseTarget(**json.loads(Path(os.environ['CAOS_SQL_ORIGIN_TARGET']).read_text()))
identity=SimpleNamespace(instance_id=str(uuid4()),process_generation=str(uuid4()),pid=os.getpid(),process_started_at=psutil.Process().create_time())
with psycopg.connect(**target.connection_parameters(),autocommit=True) as connection:
    assert connection.execute('SHOW client_connection_check_interval').fetchone()[0]=='0'
    publish_resource_binding(capture_server_binding(target,identity_supplier=lambda:identity),timeout_s=15)
    connection.execute('SELECT pg_advisory_lock(%s,%s)',(824733111,int(os.environ['CAOS_SQL_ORIGIN_LOCK'])))
"""


def wait_for(read, predicate, *, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if predicate(value):
            return value
        assert (
            time.monotonic() < deadline
        ), "owned PostgreSQL session did not reach its expected state"
        time.sleep(0.01)


def mark(monkeypatch):
    monkeypatch.setattr(origin, "_STATE", origin._State(os.getpid()))
    monkeypatch.setenv(origin.RESOURCE_BINDING_FD_ENV, "123")
    return origin.current_sql_application_name()


def test_native_orm_and_private_audit_connections_use_the_same_process_tag(
    postgres_targets, monkeypatch
):
    from sqlalchemy import create_engine

    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.clients.postgres.storage_pool import StorageQueuePool
    from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
        LegacyMemoryAuditConnectionManager,
    )

    target = postgres_targets.main
    tag = mark(monkeypatch)
    with connect(target) as connection:
        assert connection.execute("SELECT current_setting('application_name')").fetchone()[0] == tag
    engine = create_engine(
        "postgresql+psycopg://",
        creator=database._native_orm_connection,
        poolclass=StorageQueuePool,
        pool_size=1,
        max_overflow=0,
    )
    manager = LegacyMemoryAuditConnectionManager(max_sessions=1)
    try:
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT current_setting('application_name')"
                ).scalar_one()
                == tag
            )
        with manager.checkout(target, readonly=True) as connection:
            assert (
                connection.execute("SELECT current_setting('application_name')").fetchone()[0]
                == tag
            )
    finally:
        manager.close()
        engine.dispose()


def test_an_application_name_can_change_while_the_same_backend_remains_alive(
    postgres_targets, postgres_service, monkeypatch
):
    target = postgres_targets.main
    tag = mark(monkeypatch)
    with connect(target) as connection, postgres_service.connect_admin(target.database) as observer:
        connection.execute("SET application_name TO 'caos-test-mutated-label'")
        pid = connection.execute("SELECT pg_backend_pid()").fetchone()[0]
        row = observer.execute(
            "SELECT pid,backend_start,application_name FROM pg_stat_activity WHERE pid=%s", (pid,)
        ).fetchone()
        assert row[0] == pid and row[1] is not None and row[2] == "caos-test-mutated-label"
        assert (
            observer.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=%s AND application_name=%s",
                (target.database, tag),
            ).fetchone()[0]
            == 0
        )
        # A vacant tag result must not be promoted to backend/process closure.
        assert not connection.raw.closed


def test_reaped_child_can_still_have_a_server_backend_alive(
    postgres_targets, postgres_service, tmp_path
):
    target = postgres_targets.main
    source = tmp_path / "target.json"
    source.write_text(json.dumps(asdict(target), default=str))
    source.chmod(0o600)
    key = int.from_bytes(os.urandom(3), "big")
    digest = hashlib.sha256(json.dumps(target.identity, separators=(",", ":")).encode()).hexdigest()
    owner = None
    observed_backend = None
    with postgres_service.connect_admin(target.database) as observer:
        observer.execute("SELECT pg_advisory_lock(%s,%s)", (824733111, key))
        try:
            owner = ResourceProcessSupervisor.launch(
                [sys.executable, "-c", CHILD],
                expected_store_id=target.store_id,
                expected_target_identity_sha256=digest,
                env={
                    **os.environ,
                    "CAOS_SQL_ORIGIN_TARGET": str(source),
                    "CAOS_SQL_ORIGIN_LOCK": str(key),
                },
            )
            original = owner.receive_binding(timeout_s=15)
            tag = owner.sql_application_name
            assert tag is not None and tag != "caos"

            def read():
                return observer.execute(
                    "SELECT pid,backend_start,wait_event_type FROM pg_stat_activity WHERE datname=%s AND application_name=%s",
                    (target.database, tag),
                ).fetchone()

            observed_backend = wait_for(read, lambda row: row is not None and row[2] == "Lock")
            owner.send_signal(signal.SIGKILL)
            evidence = owner.wait(timeout_s=15)
            assert evidence.binding is original and evidence.sql_application_name == tag
            assert evidence.exit_kind == "signaled" and evidence.exit_status == signal.SIGKILL
            still_alive = observer.execute(
                "SELECT pid,backend_start FROM pg_stat_activity WHERE pid=%s AND backend_start=%s",
                observed_backend[:2],
            ).fetchone()
            assert (
                still_alive == observed_backend[:2]
            ), "kernel client exit must not be used as a PG-backend absence witness"
        finally:
            if owner is not None:
                owner.send_signal(signal.SIGKILL)
                owner.wait(timeout_s=15)
                owner.close()
            observer.execute("SELECT pg_advisory_unlock(%s,%s)", (824733111, key))
            if observed_backend is not None:
                wait_for(
                    lambda: observer.execute(
                        "SELECT count(*) FROM pg_stat_activity WHERE pid=%s AND backend_start=%s",
                        observed_backend[:2],
                    ).fetchone()[0],
                    lambda count: count == 0,
                )
