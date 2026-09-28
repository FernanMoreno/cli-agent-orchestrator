"""Bubblewrap process identity is private, immutable, and restart-readable."""

import hashlib
import json
import sqlite3

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from test.clients.test_work_migrations import verified_store_at_version


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
            "VALUES ('w','j','launch','k',?,'c',1)",
            ("c" * 64,),
        )
        connection.execute(
            "INSERT INTO work_attempts "
            "(id,work_item_id,attempt_number,generation,provider,state,lease_expires_at,created_at) "
            "VALUES ('a','w',1,1,'mock_cli','sent',999,1)"
        )


def test_v29_to_v30_adds_only_private_bubblewrap_identity_table(tmp_path):
    import cli_agent_orchestrator.clients.work_repository as module

    store = verified_store_at_version(module, tmp_path / "work.db", 29)
    with store.connection() as connection:
        old_objects = module._schema_objects(connection)
    store.initialize()
    store.initialize()
    with store.connection() as connection:
        new_objects = module._schema_objects(connection)
        assert module.SCHEMA_VERSION >= 30
        assert set(old_objects.items()).issubset(set(new_objects.items()))
        assert "work_bubblewrap_process_identities" in new_objects
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_bubblewrap_identity_survives_restart_and_rejects_replacement(tmp_path):
    store = WorkRepository(tmp_path / "work.db")
    store.initialize()
    _sent_attempt(store)
    identity = _identity()
    store.persist_bubblewrap_process_identity("a", 1, identity)
    store.persist_bubblewrap_process_identity("a", 1, identity)

    reopened = WorkRepository(store.path)
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
    store = WorkRepository(tmp_path / "work.db")
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
    store = WorkRepository(tmp_path / "work.db")
    store.initialize()
    _sent_attempt(store)

    def insert_unshare():
        with store.transaction() as connection:
            connection.execute(
                "INSERT INTO work_process_identities "
                "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
                "VALUES ('a',1,3,'{}',?,1)",
                ("a" * 64,),
            )

    if first == "bubblewrap":
        store.persist_bubblewrap_process_identity("a", 1, _identity())
        with pytest.raises(sqlite3.IntegrityError):
            insert_unshare()
    else:
        insert_unshare()
        with pytest.raises(sqlite3.IntegrityError):
            store.persist_bubblewrap_process_identity("a", 1, _identity())
