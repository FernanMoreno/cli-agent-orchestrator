"""PostgreSQL catalog proof must include row-security posture."""

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import (
    ComponentMigration,
    SchemaMismatch,
    catalog_manifest,
    migrate_component,
    verify_component,
    verify_runtime_role,
)


@pytest.mark.postgres
def test_component_verification_rejects_row_security_policy_drift(postgres_targets):
    target = postgres_targets.main
    migrations = (
        ComponentMigration(
            "row_security_posture",
            "main",
            1,
            ("CREATE TABLE rls_catalog_records " "(id BIGINT PRIMARY KEY, payload TEXT NOT NULL)",),
            ("rls_catalog_records",),
        ),
    )
    migrate_component(target, migrations)

    with connect(target.migration_target()) as owner:
        owner.execute(
            "INSERT INTO rls_catalog_records(id,payload) VALUES (1,%s)",
            ("migration-owner-visible",),
        )
        owner.execute("ALTER TABLE rls_catalog_records ENABLE ROW LEVEL SECURITY")
        owner.execute(
            sql.SQL(
                "CREATE POLICY deny_runtime_reads ON {} " "FOR SELECT TO {} USING (false)"
            ).format(
                sql.Identifier("rls_catalog_records"),
                sql.Identifier(target.user),
            )
        )
        # The non-FORCE policy leaves the owner-visible row visible to pg_dump
        # under the migration role while hiding it from runtime inventory scans.
        assert owner.execute("SELECT count(*) FROM rls_catalog_records").fetchone()[0] == 1

    with connect(target, readonly=True) as runtime:
        assert runtime.execute("SELECT count(*) FROM rls_catalog_records").fetchone()[0] == 0
        with pytest.raises(SchemaMismatch):
            verify_component(runtime, migrations)


@pytest.mark.postgres
def test_component_verification_rejects_policy_expression_drift(postgres_targets):
    target = postgres_targets.main
    migrations = (
        ComponentMigration(
            "row_security_policy_expression",
            "main",
            1,
            (
                "CREATE TABLE policy_catalog_records (payload TEXT)",
                "ALTER TABLE policy_catalog_records ENABLE ROW LEVEL SECURITY",
                "CREATE POLICY public_read ON policy_catalog_records "
                "FOR SELECT TO PUBLIC USING (false)",
            ),
            ("policy_catalog_records",),
        ),
    )
    migrate_component(target, migrations)

    with connect(target.migration_target()) as owner:
        owner.execute("INSERT INTO policy_catalog_records(payload) VALUES ('visible')")
        owner.execute("ALTER POLICY public_read ON policy_catalog_records USING (true)")

    with connect(target, readonly=True) as runtime:
        assert runtime.execute("SELECT count(*) FROM policy_catalog_records").fetchone()[0] == 1
        with pytest.raises(SchemaMismatch):
            verify_component(runtime, migrations)


@pytest.mark.postgres
def test_component_verification_rejects_unlogged_relation_drift(postgres_targets):
    target = postgres_targets.main
    migrations = (
        ComponentMigration(
            "logged_relation_posture",
            "main",
            1,
            ("CREATE TABLE durable_catalog_records (id BIGINT PRIMARY KEY)",),
            ("durable_catalog_records",),
        ),
    )
    migrate_component(target, migrations)

    with connect(target.migration_target()) as owner:
        owner.execute("ALTER TABLE durable_catalog_records SET UNLOGGED")

    with connect(target, readonly=True) as runtime:
        with pytest.raises(SchemaMismatch):
            verify_component(runtime, migrations)


@pytest.mark.postgres
def test_component_verification_rejects_relation_kind_drift(postgres_targets):
    target = postgres_targets.main
    migrations = (
        ComponentMigration(
            "relation_kind_posture",
            "main",
            1,
            ("CREATE TABLE typed_catalog_records (payload TEXT)",),
            ("typed_catalog_records",),
        ),
    )
    migrate_component(target, migrations)

    with connect(target.migration_target()) as owner:
        owner.execute(
            sql.SQL("DROP TABLE {}").format(sql.Identifier(target.schema, "typed_catalog_records"))
        )
        owner.execute(
            sql.SQL("CREATE VIEW {} AS SELECT 'replacement' AS payload").format(
                sql.Identifier(target.schema, "typed_catalog_records")
            )
        )

    with connect(target, readonly=True) as runtime:
        with pytest.raises(SchemaMismatch):
            verify_component(runtime, migrations)


@pytest.mark.postgres
def test_component_verification_rejects_check_function_body_drift(postgres_targets):
    target = postgres_targets.main
    migrations = (
        ComponentMigration(
            "check_function_body",
            "main",
            1,
            (
                "CREATE FUNCTION check_catalog_value(text) RETURNS boolean "
                "LANGUAGE sql IMMUTABLE AS $$ SELECT $1 <> 'blocked' $$",
                "CREATE TABLE check_catalog_records (payload TEXT NOT NULL "
                "CHECK (check_catalog_value(payload)))",
            ),
            ("check_catalog_records",),
        ),
    )
    migrate_component(target, migrations)

    with connect(target.migration_target()) as owner:
        owner.execute(
            sql.SQL(
                "CREATE OR REPLACE FUNCTION {}.check_catalog_value(text) RETURNS boolean "
                "LANGUAGE sql IMMUTABLE AS $$ SELECT true $$"
            ).format(sql.Identifier(target.schema))
        )

    with connect(target, readonly=True) as runtime:
        with pytest.raises(SchemaMismatch):
            verify_component(runtime, migrations)


@pytest.mark.postgres
def test_component_routine_manifest_uses_repeatable_read_catalog_rows(postgres_targets):
    target = postgres_targets.main
    migrations = (
        ComponentMigration(
            "routine_snapshot_manifest",
            "main",
            1,
            (
                "CREATE FUNCTION routine_snapshot_check(text) RETURNS boolean "
                "LANGUAGE sql IMMUTABLE AS $$ SELECT $1 <> 'snapshot-original' $$",
                "CREATE TABLE routine_snapshot_records (payload TEXT NOT NULL "
                "CHECK (routine_snapshot_check(payload)))",
            ),
            ("routine_snapshot_records",),
        ),
    )
    migrate_component(target, migrations)

    with connect(target, readonly=True) as snapshot:
        source = snapshot.execute(
            "SELECT p.prosrc FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname=%s AND p.proname='routine_snapshot_check'",
            (target.schema,),
        ).fetchone()[0]
        assert "snapshot-original" in source

        with connect(target.migration_target()) as owner:
            owner.execute(
                sql.SQL(
                    "CREATE OR REPLACE FUNCTION {}.routine_snapshot_check(text) "
                    "RETURNS boolean LANGUAGE sql IMMUTABLE "
                    "AS $$ SELECT $1 <> 'snapshot-replaced' $$"
                ).format(sql.Identifier(target.schema))
            )

        snapshot_manifest = catalog_manifest(snapshot, ("routine_snapshot_records",))
        routine = snapshot_manifest["routines"][0]
        assert "snapshot-original" in routine["definition"]["source"]
        assert "snapshot-replaced" not in routine["definition"]["source"]
        verify_component(snapshot, migrations)

    with connect(target, readonly=True) as current:
        with pytest.raises(SchemaMismatch):
            verify_component(current, migrations)


@pytest.mark.postgres
def test_runtime_role_cannot_own_schema_routines(postgres_targets, postgres_service):
    target = postgres_targets.main
    migrations = (
        ComponentMigration(
            "runtime_routine_ownership",
            "main",
            1,
            (
                "CREATE FUNCTION runtime_owned_check(text) RETURNS boolean "
                "LANGUAGE sql IMMUTABLE AS $$ SELECT $1 <> 'blocked' $$",
                "CREATE TABLE runtime_owned_records (payload TEXT NOT NULL "
                "CHECK (runtime_owned_check(payload)))",
            ),
            ("runtime_owned_records",),
        ),
    )
    migrate_component(target, migrations)

    with postgres_service.connect_admin(target.database) as admin:
        admin.execute(
            sql.SQL("GRANT CREATE ON SCHEMA {} TO {}").format(
                sql.Identifier(target.schema), sql.Identifier(target.user)
            )
        )
        admin.execute(
            sql.SQL("ALTER FUNCTION {}.runtime_owned_check(text) OWNER TO {}").format(
                sql.Identifier(target.schema), sql.Identifier(target.user)
            )
        )
        admin.execute(
            sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(
                sql.Identifier(target.schema), sql.Identifier(target.user)
            )
        )

    with connect(target, readonly=True) as runtime:
        with pytest.raises(SchemaMismatch):
            verify_runtime_role(runtime)


@pytest.mark.postgres
def test_component_verification_rejects_unreviewed_table_rule(postgres_targets):
    target = postgres_targets.main
    migrations = (
        ComponentMigration(
            "table_rule_posture",
            "main",
            1,
            ("CREATE TABLE rule_catalog_records (id BIGINT PRIMARY KEY)",),
            ("rule_catalog_records",),
        ),
    )
    migrate_component(target, migrations)

    with connect(target.migration_target()) as owner:
        owner.execute(
            sql.SQL("CREATE RULE unreviewed_noop AS ON DELETE TO {} DO ALSO NOTHING").format(
                sql.Identifier(target.schema, "rule_catalog_records")
            )
        )

    with connect(target, readonly=True) as runtime:
        with pytest.raises(SchemaMismatch):
            verify_component(runtime, migrations)


@pytest.mark.postgres
def test_component_verification_rejects_generated_column_semantic_drift(postgres_targets):
    target = postgres_targets.main
    migrations = (
        ComponentMigration(
            "generated_column_posture",
            "main",
            1,
            ("CREATE TABLE generated_catalog_records (value INTEGER NOT NULL DEFAULT 5)",),
            ("generated_catalog_records",),
        ),
    )
    migrate_component(target, migrations)

    with connect(target.migration_target()) as owner:
        owner.execute(
            sql.SQL("DROP TABLE {}").format(
                sql.Identifier(target.schema, "generated_catalog_records")
            )
        )
        owner.execute(
            sql.SQL(
                "CREATE TABLE {} (value INTEGER GENERATED ALWAYS AS (5) STORED NOT NULL)"
            ).format(sql.Identifier(target.schema, "generated_catalog_records"))
        )

    with connect(target, readonly=True) as runtime:
        with pytest.raises(SchemaMismatch):
            verify_component(runtime, migrations)
