"""Native PostgreSQL contract for the catalog fingerprint read path."""

import json

import psycopg
import pytest
from psycopg.errors import DivisionByZero

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.connection import Connection
from cli_agent_orchestrator.clients.postgres.migrations import (
    ComponentMigration,
    SchemaMismatch,
    _catalog_manifest_queries,
    _digest,
    _normalize_catalog_manifest,
    _records,
    catalog_manifest,
    migrate_component,
    verify_component,
)


def _legacy_manifest(connection, tables):
    """Execute the frozen nine-query path used before catalog batching."""
    parameters = (connection.target.schema, list(tables))
    result = {
        name: _records(connection, query, parameters)
        for name, query in _catalog_manifest_queries().items()
    }
    return _normalize_catalog_manifest(tables, result)


def _parity_migrations(scope):
    return (
        ComponentMigration(
            "catalog_batch_parity",
            scope,
            1,
            (
                "CREATE FUNCTION catalog_batch_allows(text) RETURNS boolean "
                "LANGUAGE sql IMMUTABLE AS $$ SELECT $1 <> 'blocked' $$",
                "CREATE FUNCTION catalog_batch_before_insert() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
                "CREATE TABLE catalog_batch_probe ("
                "id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
                "payload TEXT NOT NULL, "
                "CONSTRAINT catalog_batch_payload_check "
                "CHECK (catalog_batch_allows(payload)))",
                "CREATE INDEX catalog_batch_probe_payload_idx " "ON catalog_batch_probe(payload)",
                "CREATE TRIGGER catalog_batch_before_insert_trigger "
                "BEFORE INSERT ON catalog_batch_probe FOR EACH ROW "
                "EXECUTE FUNCTION catalog_batch_before_insert()",
                "CREATE RULE catalog_batch_no_update AS ON UPDATE "
                "TO catalog_batch_probe DO INSTEAD NOTHING",
                "ALTER TABLE catalog_batch_probe ENABLE ROW LEVEL SECURITY",
                "CREATE POLICY catalog_batch_policy ON catalog_batch_probe TO PUBLIC "
                "USING (catalog_batch_allows(payload)) "
                "WITH CHECK (catalog_batch_allows(payload))",
                "CREATE TABLE catalog_batch_empty (payload TEXT)",
            ),
            ("catalog_batch_probe", "catalog_batch_empty"),
        ),
    )


@pytest.mark.postgres
@pytest.mark.parametrize("scope", ("main", "auth", "peer"))
def test_catalog_manifest_is_one_statement_and_matches_legacy_digest(
    postgres_targets, monkeypatch, scope
):
    target = postgres_targets[scope]
    migrations = _parity_migrations(scope)
    migrate_component(target, migrations)
    tables = migrations[0].tables

    with connect(target, readonly=True) as connection:
        legacy = _legacy_manifest(connection, tables)
        statements = []
        original_execute = connection.execute

        def capture_execute(statement, params=(), *, prepare=None):
            statements.append(statement)
            if prepare is None:
                return original_execute(statement, params)
            return original_execute(statement, params, prepare=prepare)

        with monkeypatch.context() as counted:
            counted.setattr(connection, "execute", capture_execute)
            actual = catalog_manifest(connection, tables)
            assert len(statements) == 1
        prepared = connection.execute(
            "SELECT cardinality(parameter_types) AS parameter_count, generic_plans, "
            "custom_plans FROM pg_prepared_statements "
            "WHERE statement LIKE 'WITH target_namespace AS MATERIALIZED%'"
        ).fetchone()
        assert prepared is not None
        assert prepared["parameter_count"] == 0
        assert prepared["generic_plans"] >= 1
        assert prepared["custom_plans"] == 0
        assert list(actual) == list(_catalog_manifest_queries())
        assert actual == legacy
        assert json.dumps(actual, separators=(",", ":")) == json.dumps(
            legacy, separators=(",", ":")
        )
        assert _digest(actual) == _digest(legacy)
        assert connection.in_transaction
        assert connection.read_only

        legacy_empty_categories = _legacy_manifest(connection, ("catalog_batch_empty",))
        statements.clear()
        with monkeypatch.context() as counted:
            counted.setattr(connection, "execute", capture_execute)
            empty_categories = catalog_manifest(connection, ("catalog_batch_empty",))
            assert len(statements) == 1
        assert empty_categories == legacy_empty_categories
        for category in (
            "constraints",
            "indexes",
            "triggers",
            "rules",
            "sequences",
            "policies",
            "routines",
        ):
            assert empty_categories[category] == []

    legacy_digest = _digest(legacy)
    with connect(target.migration_target()) as owner, owner.transaction():
        owner.execute(
            "UPDATE _cao_schema_migrations SET manifest_digest=%s "
            "WHERE scope=%s AND component=%s AND version=%s",
            (legacy_digest, scope, "catalog_batch_parity", 1),
        )

    # The verifier accepts the digest recorded by the old nine-query reader.
    with connect(target, readonly=True) as connection:
        verify_component(connection, migrations)


@pytest.mark.postgres
def test_catalog_manifest_preserves_borrowed_repeatable_read_transaction(postgres_targets):
    target = postgres_targets.main
    migrations = _parity_migrations(target.scope)
    migrate_component(target, migrations)

    raw = psycopg.connect(**target.connection_parameters())
    try:
        raw.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        snapshot_before = raw.execute("SELECT pg_current_snapshot()::text").fetchone()[0]
        borrowed = Connection.borrow(raw, target)

        manifest = catalog_manifest(borrowed, migrations[0].tables)

        snapshot_after = raw.execute("SELECT pg_current_snapshot()::text").fetchone()[0]
        assert manifest["columns"]
        assert snapshot_after == snapshot_before
        assert borrowed.in_transaction
        assert borrowed.read_only
        assert raw.execute("SELECT 1").fetchone()[0] == 1
    finally:
        raw.rollback()
        raw.close()


@pytest.mark.postgres
def test_catalog_manifest_query_error_leaves_rollback_to_caller(postgres_targets, monkeypatch):
    target = postgres_targets.main
    with connect(target) as connection:
        connection.begin()
        execute = connection.execute
        statements = []

        def fail_catalog_read(statement, parameters=(), *, prepare=None):
            statements.append(statement)
            return execute("SELECT 1 / 0")

        with monkeypatch.context() as failing:
            failing.setattr(connection, "execute", fail_catalog_read)
            with pytest.raises(DivisionByZero):
                catalog_manifest(connection, ("catalog_batch_error_probe",))
        assert len(statements) == 1
        assert connection.in_transaction
        connection.rollback()
        assert not connection.in_transaction
        assert connection.execute("SELECT 1").fetchone()[0] == 1


@pytest.mark.postgres
def test_catalog_manifest_rejects_missing_tables_after_single_read(postgres_targets, monkeypatch):
    target = postgres_targets.main
    with connect(target, readonly=True) as connection:
        statements = []
        original_execute = connection.execute

        def capture_execute(statement, params=(), *, prepare=None):
            statements.append(statement)
            if prepare is None:
                return original_execute(statement, params)
            return original_execute(statement, params, prepare=prepare)

        monkeypatch.setattr(connection, "execute", capture_execute)
        with pytest.raises(SchemaMismatch, match="component tables are missing"):
            catalog_manifest(connection, ("catalog_batch_missing",))
        assert len(statements) == 1
        assert connection.in_transaction
