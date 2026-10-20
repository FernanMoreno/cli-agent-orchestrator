"""Contracts for the PostgreSQL verification hot path."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres import migrations as migration_module
from cli_agent_orchestrator.clients.postgres.migrations import (
    ComponentMigration,
    SchemaMismatch,
    _digest,
    _normalize_catalog_manifest,
    catalog_manifest,
    migrate_component,
    verify_component,
    verify_runtime_role,
)

_CATALOG_FAMILIES = (
    "columns",
    "constraints",
    "indexes",
    "triggers",
    "rules",
    "sequences",
    "relations",
    "policies",
    "routines",
)


def _catalog_component(scope: str = "main") -> tuple[ComponentMigration, ...]:
    return (
        ComponentMigration(
            "verification_optimization",
            scope,
            1,
            (
                "CREATE FUNCTION verification_allows(text) RETURNS boolean "
                "LANGUAGE sql IMMUTABLE AS $$ SELECT $1 <> 'blocked' $$",
                "CREATE FUNCTION verification_before_insert() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
                "CREATE TABLE verification_records ("
                "id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
                'payload TEXT NOT NULL, "café_名" TEXT, '
                "CONSTRAINT verification_payload_check CHECK (verification_allows(payload)))",
                "CREATE INDEX verification_records_payload_idx " "ON verification_records(payload)",
                "CREATE TRIGGER verification_before_insert_trigger "
                "BEFORE INSERT ON verification_records FOR EACH ROW "
                "EXECUTE FUNCTION verification_before_insert()",
                "CREATE RULE verification_no_update AS ON UPDATE "
                "TO verification_records DO INSTEAD NOTHING",
                "ALTER TABLE verification_records ENABLE ROW LEVEL SECURITY",
                "CREATE POLICY verification_policy ON verification_records TO PUBLIC "
                "USING (verification_allows(payload)) "
                "WITH CHECK (verification_allows(payload))",
            ),
            ("verification_records",),
        ),
    )


def _legacy_manifest(connection, tables: tuple[str, ...]) -> dict:
    parameters = (connection.target.schema, list(tables))
    raw = {
        name: migration_module._records(connection, query, parameters)
        for name, query in migration_module._catalog_manifest_queries().items()
    }
    return _normalize_catalog_manifest(tables, raw)


@pytest.mark.offline_database
def test_canonical_definition_cache_is_bounded_unicode_exact_and_skips_long_text():
    cached = getattr(migration_module, "_canonical_constraint_definition_cache", None)
    assert cached is not None
    cached.cache_clear()

    source = "CHECK (state::text = ANY (ARRAY['café 東京 🧠'::character varying]::text[]))"
    expected = "CHECK (state::text = ANY (ARRAY['café 東京 🧠'::text]))"
    first = migration_module._canonical_constraint_definition(source)
    assert first.encode("utf-8") == expected.encode("utf-8")
    assert migration_module._canonical_constraint_definition(source) == first
    assert cached.cache_info().hits == 1
    assert cached.cache_info().maxsize == 512
    assert migration_module._CANONICAL_CONSTRAINT_CACHE_MAX_CHARS == 4096

    non_enum = "CHECK (label = 'Málaga 東京 🧠')"
    assert migration_module._canonical_constraint_definition(non_enum) == non_enum

    before_long = cached.cache_info()
    long_definition = "x" * 4097
    assert migration_module._canonical_constraint_definition(long_definition) == long_definition
    after_long = cached.cache_info()
    assert after_long.hits == before_long.hits
    assert after_long.misses == before_long.misses
    assert after_long.currsize == before_long.currsize

    for index in range(513):
        migration_module._canonical_constraint_definition(f"CHECK (field = {index})")
    assert cached.cache_info().currsize == 512


@pytest.mark.offline_database
def test_shared_catalog_templates_are_fixed_and_cover_every_family():
    queries = getattr(migration_module, "_CATALOG_MANIFEST_SHARED_QUERIES", None)
    statement = getattr(migration_module, "_CATALOG_MANIFEST_SHARED_STATEMENT", None)
    assert queries is not None
    assert statement is not None
    assert tuple(queries) == _CATALOG_FAMILIES
    for query in queries.values():
        assert "%s" not in query
        assert "target_relations" in query
    assert "target_namespace AS MATERIALIZED" in statement.as_string(None)
    assert "target_relations AS MATERIALIZED" in statement.as_string(None)


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _RoleConnection:
    target = SimpleNamespace(schema="verification_schema")

    def __init__(self, row):
        self.row = row
        self.statements = []

    def execute(self, statement, parameters=(), *, prepare=None):
        self.statements.append((statement, parameters, prepare))
        return _Cursor(self.row)


@pytest.mark.offline_database
@pytest.mark.parametrize(
    ("row", "error"),
    [
        (None, "runtime role has unsafe privileges"),
        ((True, True, True, True, True, True), "runtime role has unsafe privileges"),
        ((True, False, True, True, None, False), "namespace allows untrusted DDL"),
        ((True, False, False, True, None, False), "must not own schema objects"),
        ((True, False, False, False, None, False), "target namespace is missing"),
        ((True, False, False, False, True, False), "must not create schema objects"),
    ],
)
def test_role_batch_preserves_original_error_precedence(row, error):
    connection = _RoleConnection(row)
    with pytest.raises(SchemaMismatch, match=error):
        verify_runtime_role(connection)
    assert len(connection.statements) == 1


@pytest.mark.offline_database
def test_role_batch_rejects_protected_table_acl_drift_after_existing_checks():
    connection = _RoleConnection((True, False, False, False, False, True))
    with pytest.raises(SchemaMismatch, match="read-only access to protected registry tables"):
        verify_runtime_role(connection)
    assert len(connection.statements) == 1


@pytest.mark.offline_database
def test_role_batch_sql_gates_later_checks_in_original_order():
    connection = _RoleConnection((True, False, False, False, False, False))
    verify_runtime_role(connection)
    query = connection.statements[0][0]
    assert "role_safety AS MATERIALIZED" in query
    assert "namespace_acl AS MATERIALIZED" in query
    assert "object_ownership AS MATERIALIZED" in query
    assert "namespace_privilege AS MATERIALIZED" in query
    assert "CASE WHEN role_safety.role_exists AND NOT role_safety.unsafe THEN EXISTS" in query
    assert "AND NOT namespace_acl.untrusted_create THEN EXISTS" in query
    assert "AND NOT object_ownership.owns_objects" in query
    assert "protected_table_acl AS MATERIALIZED" in query
    assert "AND namespace_privilege.can_create IS FALSE" in query
    assert "has_any_column_privilege(current_user,c.oid,'INSERT')" in query
    assert "has_any_column_privilege(current_user,c.oid,'UPDATE')" in query
    assert "has_any_column_privilege(current_user,c.oid,'REFERENCES')" in query
    assert "has_table_privilege(current_user,c.oid,'MAINTAIN')" in query


@pytest.mark.offline_database
def test_component_identity_mismatch_stops_before_history_or_catalog_reads():
    target = SimpleNamespace(scope="main", store_id="expected-store")

    class Connection:
        def __init__(self):
            self.target = target
            self.statements = []

        def execute(self, statement, parameters=()):
            self.statements.append((statement, parameters))
            return _Cursor(("main", "different-store"))

    connection = Connection()
    with pytest.raises(SchemaMismatch, match="logical store identity mismatch"):
        verify_component(connection, (ComponentMigration("probe", "main", 1, (), ()),))
    assert len(connection.statements) == 1


@pytest.mark.postgres
@pytest.mark.parametrize("scope", ("main", "auth", "peer"))
def test_shared_catalog_manifest_matches_legacy_digest_with_native_json_types(
    postgres_targets, monkeypatch, scope
):
    target = postgres_targets[scope]
    migrations = _catalog_component(scope)
    migrate_component(target, migrations)

    with connect(target, readonly=True) as connection:
        expected = _legacy_manifest(connection, migrations[0].tables)
        calls = []
        execute = connection.execute

        def capture(statement, parameters=(), *, prepare=None):
            calls.append((statement, parameters, prepare))
            if prepare is None:
                return execute(statement, parameters)
            return execute(statement, parameters, prepare=prepare)

        with monkeypatch.context() as counted:
            counted.setattr(connection, "execute", capture)
            actual = catalog_manifest(connection, migrations[0].tables)

        assert len(calls) == 1
        statement, parameters, prepare = calls[0]
        rendered = statement.as_string(connection.raw)
        assert "target_namespace AS MATERIALIZED" in rendered
        assert "target_relations AS MATERIALIZED" in rendered
        assert "%s" not in rendered
        assert parameters == ()
        assert prepare is True
        assert tuple(actual) == _CATALOG_FAMILIES
        assert actual == expected
        assert all(actual[family] is not None for family in _CATALOG_FAMILIES)

        table_columns = {
            row["name"]: row["position"]
            for row in actual["columns"]
            if row["table_name"] == "verification_records"
        }
        assert type(table_columns["id"]) is int
        assert "café_名" in table_columns
        serialized = json.dumps(actual, sort_keys=True, separators=(",", ":"))
        assert serialized.isascii()
        assert _digest(actual) == hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@pytest.mark.postgres
def test_manifest_and_store_identity_are_fresh_after_prior_success(postgres_targets):
    target = postgres_targets.main
    migrations = _catalog_component("main")
    migrate_component(target, migrations)

    with connect(target, readonly=True) as connection:
        verify_component(connection, migrations)
        before = catalog_manifest(connection, migrations[0].tables)

    wrong_identity = replace(target, store_id=str(uuid4()))
    with connect(wrong_identity, readonly=True) as connection:
        with pytest.raises(SchemaMismatch, match="logical store identity mismatch"):
            verify_component(connection, migrations)

    with connect(target.migration_target()) as owner:
        owner.execute('ALTER TABLE verification_records ADD COLUMN "fresh_列" INTEGER')
    with connect(target, readonly=True) as connection:
        after = catalog_manifest(connection, migrations[0].tables)
        assert before != after
        assert any(row["name"] == "fresh_列" for row in after["columns"])
        with pytest.raises(SchemaMismatch, match="schema fingerprint mismatch"):
            verify_component(connection, migrations)


@pytest.mark.postgres
@pytest.mark.parametrize("history_posture", ("missing", "unreadable"))
def test_wrong_store_identity_precedes_missing_or_unreadable_history(
    postgres_targets, history_posture
):
    target = postgres_targets.main
    migrations = _catalog_component("main")
    migrate_component(target, migrations)

    with connect(target.migration_target()) as owner:
        if history_posture == "missing":
            owner.execute("DROP TABLE _cao_schema_migrations")
        else:
            from psycopg import sql

            owner.execute(
                sql.SQL("REVOKE SELECT ON _cao_schema_migrations FROM {}").format(
                    sql.Identifier(target.user)
                )
            )

    wrong_identity = replace(target, store_id=str(uuid4()))
    with connect(wrong_identity, readonly=True) as reader:
        with pytest.raises(SchemaMismatch, match="logical store identity mismatch"):
            verify_component(reader, migrations)


@pytest.mark.postgres
def test_role_batch_preserves_native_multifault_error_order(postgres_targets, postgres_service):
    from psycopg import sql

    target = postgres_targets.main
    migrations = _catalog_component("main")
    migrate_component(target, migrations)
    schema = sql.Identifier(target.schema)
    runtime_role = sql.Identifier(target.user)
    table = sql.Identifier(target.schema, "verification_records")
    owner_role = sql.Identifier(target.migration_user)

    with postgres_service.connect_admin(target.database) as admin:
        admin.execute(sql.SQL("GRANT CREATE ON SCHEMA {} TO {}").format(schema, runtime_role))
        admin.execute(sql.SQL("ALTER TABLE {} OWNER TO {}").format(table, runtime_role))
        admin.execute(sql.SQL("ALTER ROLE {} CREATEDB").format(runtime_role))

    try:
        with connect(target, readonly=True) as runtime:
            with pytest.raises(SchemaMismatch, match="runtime role has unsafe privileges"):
                verify_runtime_role(runtime)

        with postgres_service.connect_admin(target.database) as admin:
            admin.execute(sql.SQL("ALTER ROLE {} NOCREATEDB").format(runtime_role))
        with connect(target, readonly=True) as runtime:
            with pytest.raises(SchemaMismatch, match="namespace allows untrusted DDL"):
                verify_runtime_role(runtime)

        with postgres_service.connect_admin(target.database) as admin:
            admin.execute(
                sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(schema, runtime_role)
            )
        with connect(target, readonly=True) as runtime:
            with pytest.raises(SchemaMismatch, match="must not own schema objects"):
                verify_runtime_role(runtime)
    finally:
        with postgres_service.connect_admin(target.database) as admin:
            admin.execute(sql.SQL("ALTER ROLE {} NOCREATEDB").format(runtime_role))
            admin.execute(
                sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(schema, runtime_role)
            )
            admin.execute(sql.SQL("ALTER TABLE {} OWNER TO {}").format(table, owner_role))
