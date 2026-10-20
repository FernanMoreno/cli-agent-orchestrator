"""Bubblewrap process identity is private, immutable, and restart-readable."""

import hashlib
import json

import pytest
from psycopg import IntegrityError

from cli_agent_orchestrator.clients.postgres import DatabaseTarget, connect
from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository


def _identity(**changes):
    payload = {
        "version": 1,
        "kind": "bubblewrap",
        "boot_id": "boot-123",
        "monitor_pid": 101,
        "monitor_start_time_ticks": 2001,
        "init_pid": 102,
        "init_start_time_ticks": 2002,
        "init_parent_pid": 101,
        "pid_namespace": [1, 301],
        "net_namespace": [1, 302],
        "ipc_namespace": [1, 303],
        "monitor_argv_sha256": "a" * 64,
        "monitor_executable_sha256": "b" * 64,
    }
    payload.update(changes)
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return {**payload, "identity_sha256": digest}


def _sent_attempt(store):
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO work_jobs (id,project_id,principal_id,allowed_providers,grant_id,created_at) "
            "VALUES ('j','p','operator','[\"mock_cli\"]','g',1)"
        )
        connection.execute(
            "INSERT INTO work_items "
            "(id,job_id,operation_kind,idempotency_key,request_hash,contract_id,created_at) "
            "VALUES ('w','j','launch','k',%s,'c',1)",
            ("c" * 64,),
        )
        connection.execute(
            "INSERT INTO work_attempts "
            "(id,work_item_id,attempt_number,generation,provider,state,lease_expires_at,created_at) "
            "VALUES ('a','w',1,1,'mock_cli','sent',999,1)"
        )


@pytest.mark.postgres
def test_native_work_baseline_contains_private_bubblewrap_identity_table(postgres_targets):
    from cli_agent_orchestrator.clients.postgres.migrations import (
        migrate_component,
        verify_component,
    )
    from cli_agent_orchestrator.clients.work_postgres_schema import (
        WORK_POSTGRES_MIGRATIONS,
        WORK_TABLES,
    )
    from cli_agent_orchestrator.clients.workflows_postgres_schema import WORKFLOW_MIGRATIONS

    target = postgres_targets.main
    migrate_component(target, WORK_POSTGRES_MIGRATIONS[:1])
    migrate_component(target, WORKFLOW_MIGRATIONS)
    migrate_component(target, WORK_POSTGRES_MIGRATIONS)
    with connect(target, readonly=True) as connection:
        verify_component(connection, WORK_POSTGRES_MIGRATIONS)
        columns = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name='work_bubblewrap_process_identities' "
            "ORDER BY ordinal_position",
            (target.schema,),
        ).fetchall()
        assert tuple(row[0] for row in columns) == (
            "attempt_id",
            "generation",
            "protocol_version",
            "identity_json",
            "identity_sha256",
            "created_at",
        )
        unvalidated_foreign_keys = connection.execute(
            "SELECT count(*) FROM pg_catalog.pg_constraint AS constraint_row "
            "JOIN pg_catalog.pg_class AS relation ON relation.oid=constraint_row.conrelid "
            "JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
            "WHERE namespace.nspname=%s AND constraint_row.contype='f' "
            "AND NOT constraint_row.convalidated",
            (target.schema,),
        ).fetchone()[0]
        assert unvalidated_foreign_keys == 0
        table_names = connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=%s AND table_type='BASE TABLE'",
            (target.schema,),
        ).fetchall()
    assert {
        *WORK_TABLES,
        "_cao_schema_migrations",
        "_cao_store_identity",
    } <= {row[0] for row in table_names}


def test_bubblewrap_identity_survives_restart_and_rejects_replacement(tmp_path):
    target = DatabaseTarget.from_settings(scope="main")
    store = WorkRepository(target, filesystem_home=tmp_path)
    store.initialize()
    _sent_attempt(store)
    identity = _identity()
    store.persist_bubblewrap_process_identity("a", 1, identity)
    store.persist_bubblewrap_process_identity("a", 1, identity)

    reopened = WorkRepository(store.target, filesystem_home=store.filesystem_home)
    assert reopened.read_bubblewrap_process_identity("a", 1) == identity
    with reopened.connection() as connection:
        row = connection.execute(
            "SELECT identity_json FROM work_bubblewrap_process_identities WHERE attempt_id='a'"
        ).fetchone()
    assert "argv" not in json.loads(row["identity_json"])
    with pytest.raises(WorkConflict):
        reopened.persist_bubblewrap_process_identity(
            "a", 1, _identity(monitor_pid=201, init_pid=202, init_parent_pid=201)
        )


def test_bubblewrap_identity_rejects_raw_argv_and_bad_relationship(tmp_path):
    store = WorkRepository(DatabaseTarget.from_settings(scope="main"), filesystem_home=tmp_path)
    store.initialize()
    _sent_attempt(store)
    with pytest.raises(ValueError):
        store.persist_bubblewrap_process_identity("a", 1, _identity(monitor_argv=["secret"]))
    with pytest.raises(ValueError):
        store.persist_bubblewrap_process_identity("a", 1, _identity(init_parent_pid=999))
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_bubblewrap_process_identities"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("first", ["bubblewrap", "unshare"])
def test_attempt_cannot_hold_two_process_identity_kinds(tmp_path, first):
    store = WorkRepository(DatabaseTarget.from_settings(scope="main"), filesystem_home=tmp_path)
    store.initialize()
    _sent_attempt(store)

    def insert_unshare():
        with store.transaction() as connection:
            connection.execute(
                "INSERT INTO work_process_identities "
                "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
                "VALUES ('a',1,3,'{}',%s,1)",
                ("a" * 64,),
            )

    if first == "bubblewrap":
        store.persist_bubblewrap_process_identity("a", 1, _identity())
        with pytest.raises(IntegrityError):
            insert_unshare()
    else:
        insert_unshare()
        with pytest.raises(IntegrityError):
            store.persist_bubblewrap_process_identity("a", 1, _identity())
