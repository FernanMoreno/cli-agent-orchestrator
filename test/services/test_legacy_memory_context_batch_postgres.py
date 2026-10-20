from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch
from uuid import UUID

import psycopg
import pytest
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import tuple_row

from cli_agent_orchestrator.clients.postgres import Connection, connect
from cli_agent_orchestrator.clients.postgres.connection import Record
from cli_agent_orchestrator.clients.work_repository import (
    SchemaMismatch,
    WorkRepository,
)
from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
    _CONTEXT_BATCH_FALLBACK,
    LegacyMemoryAuditConnectionManager,
    _LegacyMemoryAuditWorkRepository,
)

pytest_plugins = ("test.postgres.fixtures",)
pytestmark = pytest.mark.postgres


INBOX_SQL = "SELECT singleton,store_identity,store_uuid FROM work_inbox_store_context"
CONTROL_SQL = "SELECT singleton,store_identity,store_uuid FROM work_store_control"
RECOVERY_SQL = (
    "SELECT singleton,context_version,execution_state,installation_uuid,"
    "source_installation_uuid,bundle_digest,restore_receipt,created_at "
    "FROM work_recovery_context"
)
CONTEXT_TABLES = (
    "work_inbox_store_context",
    "work_store_control",
    "work_recovery_context",
)


def _reset_targets(targets):
    from test.postgres.fixtures import reset_postgres_worker_targets

    reset_postgres_worker_targets(targets)


@pytest.fixture
def native_targets(postgres_worker_targets):
    _reset_targets(postgres_worker_targets)
    yield postgres_worker_targets
    _reset_targets(postgres_worker_targets)


@pytest.fixture
def legacy_repository(native_targets):
    target = native_targets.main
    manager = LegacyMemoryAuditConnectionManager(max_sessions=1, checkout_timeout=2.0)
    repository = _LegacyMemoryAuditWorkRepository(
        target,
        manager=manager,
        resolver=lambda: target,
    )
    try:
        yield native_targets, target, manager, repository
    finally:
        manager.close()


@contextmanager
def _observe_tail(connection):
    statements = []
    batch_results = []
    original_execute = connection.execute
    original_batch = connection._batch_verify_work_contexts

    def execute(statement, *args, **kwargs):
        statements.append(statement)
        return original_execute(statement, *args, **kwargs)

    with patch.object(connection, "execute", new=execute):

        def batch():
            result = original_batch()
            batch_results.append(result)
            return result

        with patch.object(connection, "_batch_verify_work_contexts", new=batch):
            yield statements, batch_results


def _verify_tail(repository, connection, identity):
    expected_uuid = UUID(identity).hex
    return repository._verify_store_context(connection, identity, expected_uuid)


def _create_temp_shadow(manager, target, table, updates):
    with manager.checkout(target, resolver=lambda: target) as connection:
        connection.execute(
            sql.SQL(
                "CREATE TEMP TABLE {} ON COMMIT PRESERVE ROWS " "AS SELECT * FROM {}.{}"
            ).format(sql.Identifier(table), sql.Identifier(target.schema), sql.Identifier(table))
        )
        for column, value in updates.items():
            connection.execute(
                sql.SQL("UPDATE pg_temp.{} SET {}=%s").format(
                    sql.Identifier(table), sql.Identifier(column)
                ),
                (value,),
            )


def _assert_inbox_precedence(repository, target, expected_message):
    with repository._audit_manager.checkout(
        target,
        resolver=lambda: target,
        readonly=True,
    ) as connection:
        connection.execute("SET LOCAL lock_timeout TO '500ms'")
        with _observe_tail(connection) as (statements, batch_results):
            with pytest.raises(SchemaMismatch, match=expected_message) as raised:
                _verify_tail(repository, connection, target.store_id)
        assert type(raised.value) is SchemaMismatch
        assert len(batch_results) == 1
        assert batch_results[0] is _CONTEXT_BATCH_FALLBACK
        assert statements == [INBOX_SQL]
        assert connection.in_transaction
        assert connection.raw.info.transaction_status is TransactionStatus.INTRANS
        assert not connection._lease_state.tainted


def _admin(target):
    return connect(target.migration_target())


def test_native_batch_matches_ordered_reads_and_preserves_snapshot_and_audit_transactions(
    legacy_repository,
):
    _targets, target, manager, repository = legacy_repository
    identity = target.store_id
    expected_uuid = UUID(identity).hex

    with repository.read_snapshot() as connection:
        assert connection.in_transaction
        assert connection.read_only
        assert connection.raw.info.transaction_status is TransactionStatus.INTRANS
        assert connection.execute("SHOW transaction_isolation").fetchone()[0] == "repeatable read"

        batch_rows = connection._batch_verify_work_contexts()
        assert len(batch_rows) == 3
        assert all(isinstance(row, Record) for row in batch_rows)
        by_kind = {row["context_kind"]: row for row in batch_rows}
        assert set(by_kind) == {"inbox", "control", "recovery"}
        WorkRepository._verify_batched_store_context(batch_rows, identity, expected_uuid)
        inbox = connection.execute(INBOX_SQL).fetchone()
        control = connection.execute(CONTROL_SQL).fetchone()
        recovery = connection.execute(RECOVERY_SQL).fetchone()
        assert isinstance(inbox, Record)
        assert isinstance(control, Record)
        assert isinstance(recovery, Record)
        for field in ("singleton", "store_identity", "store_uuid"):
            assert by_kind["inbox"][field] == inbox[field]
            assert by_kind["control"][field] == control[field]
        for field in (
            "singleton",
            "context_version",
            "execution_state",
            "installation_uuid",
            "source_installation_uuid",
            "bundle_digest",
            "restore_receipt",
            "created_at",
        ):
            assert by_kind["recovery"][field] == recovery[field]

        locks = connection.execute(
            "SELECT c.relname,l.mode,l.granted "
            "FROM pg_catalog.pg_locks AS l "
            "JOIN pg_catalog.pg_class AS c ON c.oid=l.relation "
            "JOIN pg_catalog.pg_namespace AS n ON n.oid=c.relnamespace "
            "WHERE l.pid=pg_backend_pid() AND l.mode='AccessShareLock' "
            "AND l.granted AND c.relname::text=ANY(%s::text[]) ORDER BY c.relname",
            (list(CONTEXT_TABLES),),
        ).fetchall()
        assert set(row[0] for row in locks) == set(CONTEXT_TABLES)
        assert not connection._lease_state.tainted

    with repository.audited_write_transaction() as connection:
        assert connection.in_transaction
        assert not connection.read_only
        assert connection.raw.info.transaction_status is TransactionStatus.INTRANS
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    assert manager.session_count_for(target) == 1


@pytest.mark.parametrize(
    ("table", "updates", "expected_message"),
    (
        (
            "work_inbox_store_context",
            {"store_identity": "different-store"},
            "managed inbox context is absent or names another PostgreSQL store",
        ),
        (
            "work_store_control",
            {"store_uuid": "different-store"},
            "Work store control identity is absent or contradictory",
        ),
        (
            "work_recovery_context",
            {"context_version": 77},
            "recovery context is corrupt or unsupported",
        ),
    ),
)
def test_native_tail_retains_each_contexts_original_failure(
    legacy_repository, table, updates, expected_message
):
    _targets, target, manager, repository = legacy_repository
    _create_temp_shadow(manager, target, table, updates)
    with manager.checkout(
        target,
        resolver=lambda: target,
        readonly=True,
    ) as connection:
        with pytest.raises(SchemaMismatch, match=expected_message) as raised:
            _verify_tail(repository, connection, target.store_id)
        assert type(raised.value) is SchemaMismatch
        assert connection.raw.info.transaction_status is TransactionStatus.INTRANS


def test_bad_inbox_precedes_missing_later_relation_after_pipeline_fallback(
    legacy_repository,
):
    targets, target, manager, repository = legacy_repository
    _create_temp_shadow(
        manager,
        target,
        "work_inbox_store_context",
        {"store_identity": "different-store"},
    )
    try:
        with _admin(target) as admin:
            admin.execute("DROP TABLE work_store_control")
        _assert_inbox_precedence(
            repository,
            target,
            "managed inbox context is absent or names another PostgreSQL store",
        )
    finally:
        manager.close()
        _reset_targets(targets)


def test_bad_inbox_precedes_later_select_denial_after_pipeline_fallback(legacy_repository):
    _targets, target, manager, repository = legacy_repository
    _create_temp_shadow(
        manager,
        target,
        "work_inbox_store_context",
        {"store_identity": "different-store"},
    )
    with _admin(target) as admin:
        admin.execute(
            sql.SQL("REVOKE SELECT ON TABLE work_store_control FROM {}").format(
                sql.Identifier(target.user)
            )
        )
    try:
        with _admin(target) as admin:
            assert not admin.execute(
                "SELECT has_table_privilege(%s,'work_store_control','SELECT')",
                (target.user,),
            ).fetchone()[0]
        _assert_inbox_precedence(
            repository,
            target,
            "managed inbox context is absent or names another PostgreSQL store",
        )
    finally:
        with _admin(target) as admin:
            admin.execute(
                sql.SQL("GRANT SELECT ON TABLE work_store_control TO {}").format(
                    sql.Identifier(target.user)
                )
            )


def test_bad_inbox_precedes_later_access_exclusive_lock_with_nowait_fallback(
    legacy_repository,
):
    _targets, target, manager, repository = legacy_repository
    _create_temp_shadow(
        manager,
        target,
        "work_inbox_store_context",
        {"store_identity": "different-store"},
    )
    with _admin(target) as locker:
        with locker.transaction():
            locker.execute("LOCK TABLE work_store_control IN ACCESS EXCLUSIVE MODE")
            _assert_inbox_precedence(
                repository,
                target,
                "managed inbox context is absent or names another PostgreSQL store",
            )


def test_borrowed_tuple_connection_keeps_generic_ordered_validation(legacy_repository):
    _targets, target, _manager, _repository = legacy_repository
    raw = psycopg.connect(
        **target.connection_parameters(),
        autocommit=True,
        row_factory=tuple_row,
    )
    try:
        raw.execute(
            sql.SQL("SET search_path TO pg_catalog, {}").format(sql.Identifier(target.schema))
        )
        raw.execute("SET TIME ZONE 'UTC'")
        raw.execute("SET statement_timeout TO '30s'")
        raw.execute("SET lock_timeout TO '10s'")
        borrowed = Connection.borrow(raw, target)
        original_execute = borrowed.execute
        context_queries = []

        def record_context_queries(statement, *args, **kwargs):
            if isinstance(statement, str) and statement in {INBOX_SQL, CONTROL_SQL, RECOVERY_SQL}:
                context_queries.append(statement)
            return original_execute(statement, *args, **kwargs)

        with patch.object(borrowed, "execute", new=record_context_queries):
            WorkRepository._verify(borrowed)

        assert context_queries == [INBOX_SQL, CONTROL_SQL, RECOVERY_SQL]
        assert raw.row_factory is tuple_row
        assert type(raw.execute("SELECT 1").fetchone()) is tuple
    finally:
        raw.close()
