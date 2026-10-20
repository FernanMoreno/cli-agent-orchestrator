"""Schema proof survives PostgreSQL's own CHECK deparse/reparse."""

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import (
    ComponentMigration,
    catalog_manifest,
    migrate_component,
    verify_component,
)


@pytest.mark.postgres
def test_varchar_enum_check_roundtrip_preserves_component_proof(postgres_targets):
    target = postgres_targets.main
    migrations = (
        ComponentMigration(
            "enum_roundtrip",
            "main",
            1,
            (
                "CREATE TABLE enum_state (state VARCHAR NOT NULL "
                "CHECK(state IN ('admitted','submitted','reconcile')))",
            ),
            ("enum_state",),
        ),
    )
    migrate_component(target, migrations)
    with connect(target.migration_target()) as connection, connection.transaction():
        before = catalog_manifest(connection, ("enum_state",))
        definition = connection.execute(
            "SELECT pg_get_constraintdef(c.oid, false) FROM pg_constraint c "
            "JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname=%s AND t.relname='enum_state' AND c.contype='c'",
            (target.schema,),
        ).fetchone()[0]
        connection.execute("ALTER TABLE enum_state DROP CONSTRAINT enum_state_state_check")
        connection.execute(
            sql.SQL("ALTER TABLE enum_state ADD CONSTRAINT enum_state_state_check {}").format(
                sql.SQL(definition)
            )
        )
        assert catalog_manifest(connection, ("enum_state",)) == before
    with connect(target, readonly=True) as connection:
        verify_component(connection, migrations)


@pytest.mark.offline_database
@pytest.mark.parametrize("value", ["admitted", "it''s admitted"])
def test_enum_array_canonicalization_preserves_literal_bytes(value):
    from cli_agent_orchestrator.clients.postgres.migrations import _canonical_constraint_definition

    source = f"CHECK (state::text = ANY (ARRAY['{value}'::character varying]::text[]))"
    restored = f"CHECK (state::text = ANY (ARRAY['{value}'::character varying::text]))"
    assert _canonical_constraint_definition(source) == _canonical_constraint_definition(restored)
    assert value in _canonical_constraint_definition(source)


@pytest.mark.offline_database
@pytest.mark.parametrize(
    "definition",
    [
        "CHECK (label = 'ARRAY[''value''::character varying]::text[]')",
        'CHECK ("ARRAY[value::character varying]::text[]" IS NOT NULL)',
        "CHECK (state = ANY (ARRAY['value'::character varying]))",
        "CHECK (state = ANY (ARRAY['value'::character varying(5)]::text[]))",
        "CHECK (state = ANY (ARRAY[other_column::character varying]::text[]))",
    ],
)
def test_enum_array_canonicalization_does_not_rewrite_other_sql(definition):
    from cli_agent_orchestrator.clients.postgres.migrations import _canonical_constraint_definition

    assert _canonical_constraint_definition(definition) == definition


@pytest.mark.offline_database
@pytest.mark.parametrize(
    "changed",
    [
        "CHECK (state::text = ANY (ARRAY['tampered'::character varying]::text[]))",
        "CHECK (other_state::text = ANY (ARRAY['admitted'::character varying]::text[]))",
        "CHECK (state::text <> ALL (ARRAY['admitted'::character varying]::text[]))",
    ],
)
def test_enum_array_canonicalization_retains_semantic_changes(changed):
    from cli_agent_orchestrator.clients.postgres.migrations import _canonical_constraint_definition

    source = "CHECK (state::text = ANY (ARRAY['admitted'::character varying]::text[]))"
    assert _canonical_constraint_definition(source) != _canonical_constraint_definition(changed)
