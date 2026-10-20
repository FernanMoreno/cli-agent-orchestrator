"""Contracts for prepared PostgreSQL runtime-role verification."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from psycopg import sql
from psycopg.pq import TransactionStatus

from cli_agent_orchestrator.clients.postgres import connect, migrations
from cli_agent_orchestrator.clients.postgres.migrations import (
    SchemaMismatch,
    verify_runtime_role,
)


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _RoleConnection:
    def __init__(self, schema, row=(True, False, False, False, False, False)):
        self.target = SimpleNamespace(schema=schema)
        self.row = row
        self.calls = []

    def execute(self, statement, parameters=(), *, prepare=None):
        self.calls.append((statement, parameters, prepare))
        return _Cursor(self.row)


@pytest.mark.offline_database
def test_runtime_role_statement_quotes_all_schema_positions_and_caches_only_sql():
    cached = migrations._cached_runtime_role_statement
    cached.cache_clear()
    schema = "role_'東京\\scope"
    connection = _RoleConnection(schema)

    verify_runtime_role(connection)
    first_statement, parameters, prepare = connection.calls[0]
    literal = sql.Literal(schema).as_string(None)

    assert type(first_statement) is str
    assert first_statement.count(literal) == 5
    assert "%s" not in first_statement
    assert parameters == ()
    assert prepare is True
    assert cached.cache_info().misses == 1

    verify_runtime_role(connection)
    assert connection.calls[1][0] is first_statement
    assert connection.calls[1][1:] == ((), True)
    assert cached.cache_info().hits == 1
    assert cached.cache_info().maxsize == 128

    for index in range(130):
        verify_runtime_role(_RoleConnection(f"bounded_schema_{index}"))
    assert cached.cache_info().currsize == 128
    cached.cache_clear()


@pytest.mark.offline_database
def test_runtime_role_oversized_or_nonexact_schema_uses_original_bound_query():
    class SchemaSubclass(str):
        pass

    cached = migrations._cached_runtime_role_statement
    cached.cache_clear()
    for schema in ("s" * 4097, SchemaSubclass("schema_subclass")):
        connection = _RoleConnection(schema)

        verify_runtime_role(connection)

        statement, parameters, prepare = connection.calls[0]
        assert statement is migrations._RUNTIME_ROLE_QUERY
        assert parameters == (schema,) * 5
        assert prepare is None
        assert cached.cache_info().currsize == 0
        assert cached.cache_info().misses == 0


class _ConfigCursor:
    def __init__(self, row, *, fail_nextset_at=None):
        self.row = row
        self.fail_nextset_at = fail_nextset_at
        self.nextset_calls = 0
        self.advances = 0
        self.fetched = False

    def nextset(self):
        self.nextset_calls += 1
        if self.fail_nextset_at == self.nextset_calls:
            raise RuntimeError("synthetic command result failure")
        if self.advances < 7:
            self.advances += 1
            return self
        return None

    def fetchone(self):
        self.fetched = True
        assert self.nextset_calls == 8
        return self.row


class _ConfigRaw:
    def __init__(self, cursor):
        self.autocommit = True
        self.info = SimpleNamespace(
            transaction_status=TransactionStatus.IDLE,
            user="runtime_user",
            dbname="runtime_database",
        )
        self.cursor = cursor
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return self.cursor


@pytest.mark.offline_database
def test_pool_configuration_uses_one_command_sequence_and_drains_nextsets():
    from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
        LegacyMemoryAuditConnectionManager,
    )

    target = SimpleNamespace(
        schema="runtime_schema",
        user="runtime_user",
        database="runtime_database",
    )
    cursor = _ConfigCursor(
        (
            "runtime_user",
            "runtime_user",
            "pg_catalog, runtime_schema",
            "UTC",
            "30s",
            "10s",
            "force_custom_plan",
        )
    )
    raw = _ConfigRaw(cursor)

    LegacyMemoryAuditConnectionManager._configure_raw(raw, target)

    assert len(raw.statements) == 1
    statement = raw.statements[0].as_string(None)
    assert statement.startswith("RESET ROLE; RESET ALL;")
    assert statement.count(";") == 7
    assert statement.endswith("current_setting('plan_cache_mode')")
    assert cursor.advances == 7
    assert cursor.fetched is True


@pytest.mark.offline_database
def test_pool_configuration_propagates_result_sequence_faults():
    from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
        LegacyMemoryAuditConnectionManager,
    )

    target = SimpleNamespace(
        schema="runtime_schema",
        user="runtime_user",
        database="runtime_database",
    )
    cursor = _ConfigCursor(
        (
            "runtime_user",
            "runtime_user",
            "pg_catalog, runtime_schema",
            "UTC",
            "30s",
            "10s",
            "force_custom_plan",
        ),
        fail_nextset_at=3,
    )
    raw = _ConfigRaw(cursor)

    with pytest.raises(RuntimeError, match="synthetic command result failure"):
        LegacyMemoryAuditConnectionManager._configure_raw(raw, target)
    assert len(raw.statements) == 1
    assert cursor.fetched is False


@pytest.mark.postgres
def test_prepared_role_query_observes_acl_changes_after_preparation(
    postgres_targets, postgres_service
):
    target = postgres_targets.main
    schema = sql.Identifier(target.schema)
    runtime_role = sql.Identifier(target.user)

    def set_untrusted_create(granted: bool) -> None:
        verb = "GRANT" if granted else "REVOKE"
        suffix = " TO " if granted else " FROM "
        with postgres_service.connect_admin(target.database) as admin:
            admin.execute(
                sql.SQL(verb + " CREATE ON SCHEMA {}" + suffix + "{}").format(
                    schema,
                    runtime_role,
                )
            )

    set_untrusted_create(False)
    try:
        with connect(target) as connection:
            verify_runtime_role(connection)
            with postgres_service.connect_admin(target.database) as admin:
                admin.execute(
                    sql.SQL("GRANT CREATE ON SCHEMA {} TO {}").format(schema, runtime_role)
                )
            with pytest.raises(SchemaMismatch, match="namespace allows untrusted DDL"):
                verify_runtime_role(connection)

            set_untrusted_create(False)
            verify_runtime_role(connection)
            prepared = connection.execute(
                "SELECT cardinality(parameter_types) AS parameter_count, generic_plans, "
                "custom_plans FROM pg_prepared_statements "
                "WHERE statement LIKE 'WITH role_flags AS MATERIALIZED%'"
            ).fetchone()

        assert prepared is not None
        assert prepared["parameter_count"] == 0
        assert prepared["generic_plans"] == 3
        assert prepared["custom_plans"] == 0
    finally:
        set_untrusted_create(False)


@pytest.mark.postgres
@pytest.mark.parametrize("scope", ("main", "auth", "peer"))
def test_prepared_role_row_matches_bound_query_for_each_scope(postgres_targets, scope):
    target = getattr(postgres_targets, scope)

    with connect(target) as connection:
        bound_row = connection.execute(
            migrations._RUNTIME_ROLE_QUERY, (target.schema,) * 5
        ).fetchone()
        prepared_row = connection.execute(
            migrations._runtime_role_statement(target.schema), prepare=True
        ).fetchone()

    assert bound_row is not None
    assert prepared_row is not None
    assert len(bound_row) == len(prepared_row) == 6
    assert tuple(bound_row) == tuple(prepared_row)
    assert tuple(prepared_row) == (True, False, False, False, False, False)


@pytest.mark.postgres
def test_prepared_role_query_observes_role_flag_changes_on_same_session(
    postgres_targets, postgres_service
):
    target = postgres_targets.main
    runtime_role = sql.Identifier(target.user)

    def set_createdb(granted: bool) -> None:
        flag = "CREATEDB" if granted else "NOCREATEDB"
        with postgres_service.connect_admin(target.database) as admin:
            admin.execute(sql.SQL("ALTER ROLE {} " + flag).format(runtime_role))

    set_createdb(False)
    try:
        with connect(target) as connection:
            backend_pid = connection.raw.info.backend_pid
            verify_runtime_role(connection)

            set_createdb(True)
            with pytest.raises(SchemaMismatch, match="runtime role has unsafe privileges"):
                verify_runtime_role(connection)

            set_createdb(False)
            verify_runtime_role(connection)

            assert connection.raw.info.backend_pid == backend_pid
            prepared = connection.execute(
                "SELECT cardinality(parameter_types) AS parameter_count, generic_plans, "
                "custom_plans FROM pg_prepared_statements "
                "WHERE statement LIKE 'WITH role_flags AS MATERIALIZED%'"
            ).fetchone()

        assert prepared is not None
        assert prepared["parameter_count"] == 0
        assert prepared["generic_plans"] == 3
        assert prepared["custom_plans"] == 0
    finally:
        set_createdb(False)
