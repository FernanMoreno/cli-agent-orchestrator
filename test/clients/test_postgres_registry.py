"""The complete registry can provision and verify an existing native store."""

import psycopg
import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.import_receipt_schema import IMPORT_RECEIPT_TABLE
from cli_agent_orchestrator.clients.postgres.migrations import (
    ComponentMigration,
    SchemaMismatch,
    migrate_component,
    verify_runtime_role,
)
from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope, component_migrations

pytestmark = pytest.mark.postgres


def test_full_bootstrap_is_idempotent_and_preserves_business_data(postgres_targets):
    target = postgres_targets.main
    bootstrap_scope(target)
    with connect(target) as connection:
        connection.execute(
            "INSERT INTO flows(name,file_path,schedule,agent_profile,provider,enabled) VALUES (%s,%s,%s,%s,%s,%s)",
            ("preserved", "/preserved", "* * * * *", "developer", "mock_cli", False),
        )
        before = [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM _cao_schema_migrations ORDER BY component,version"
            )
        ]
    bootstrap_scope(target)
    with connect(target) as connection:
        after = [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM _cao_schema_migrations ORDER BY component,version"
            )
        ]
        assert after == before
        assert connection.execute("SELECT name FROM flows").fetchone()[0] == "preserved"


def test_import_receipt_is_registered_and_runtime_read_only_for_every_scope(postgres_targets):
    for scope in ("main", "auth", "peer"):
        target = getattr(postgres_targets, scope)
        groups = component_migrations(scope)
        receipt_migrations = [
            group for group in groups if group and group[0].component == "import_receipts"
        ]
        assert len(receipt_migrations) == 1
        assert receipt_migrations[0][0].scope == scope
        assert receipt_migrations[0][0].tables == (IMPORT_RECEIPT_TABLE,)
        assert groups[-1] == receipt_migrations[0]

        bootstrap_scope(target, seed_work=False)

        receipt_table = sql.Identifier(target.schema, IMPORT_RECEIPT_TABLE)
        with connect(target) as runtime:
            receipt_oid = runtime.execute(
                """SELECT c.oid FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=%s AND c.relname=%s AND c.relkind IN ('r','p')""",
                (target.schema, IMPORT_RECEIPT_TABLE),
            ).fetchone()
            assert receipt_oid is not None
            effective = runtime.execute(
                """SELECT has_table_privilege(current_user,%s,'SELECT'),
                has_table_privilege(current_user,%s,'INSERT'),
                has_table_privilege(current_user,%s,'UPDATE'),
                has_table_privilege(current_user,%s,'DELETE'),
                has_table_privilege(current_user,%s,'TRUNCATE'),
                has_table_privilege(current_user,%s,'REFERENCES'),
                has_table_privilege(current_user,%s,'TRIGGER'),
                has_table_privilege(current_user,%s,'MAINTAIN')""",
                (receipt_oid[0],) * 8,
            ).fetchone()
            assert effective is not None
            assert tuple(effective) == (True, False, False, False, False, False, False, False)
            assert (
                runtime.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(receipt_table)
                ).fetchone()[0]
                == 0
            )

            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(
                    sql.SQL("INSERT INTO {} (receipt_id) VALUES (%s)").format(receipt_table),
                    ("c92e63f7-e032-46a3-8b0f-f887f8bd7d30",),
                )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(sql.SQL("UPDATE {} SET state='verified'").format(receipt_table))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                runtime.execute(sql.SQL("DELETE FROM {}").format(receipt_table))

        # Any subsequent migration reapplies the generic table grant. Receipt
        # protection must be restored by migrate_component every time.
        migrate_component(
            target,
            (
                ComponentMigration(
                    component="receipt_acl_regression",
                    scope=scope,
                    version=1,
                    statements=("SELECT 1",),
                    tables=(),
                ),
            ),
        )
        with connect(target) as runtime:
            effective = runtime.execute(
                """SELECT has_table_privilege(current_user,%s,'SELECT'),
                has_table_privilege(current_user,%s,'INSERT'),
                has_table_privilege(current_user,%s,'UPDATE'),
                has_table_privilege(current_user,%s,'DELETE')""",
                (f"{target.schema}.{IMPORT_RECEIPT_TABLE}",) * 4,
            ).fetchone()
            assert effective is not None
            assert tuple(effective) == (True, False, False, False)


@pytest.mark.parametrize(
    ("table_name", "privilege"),
    (
        ("_cao_schema_migrations", "INSERT"),
        ("_cao_store_identity", "UPDATE"),
        (IMPORT_RECEIPT_TABLE, "DELETE"),
    ),
)
def test_runtime_role_rejects_grants_on_protected_registry_tables(
    postgres_targets, table_name, privilege
):
    target = postgres_targets.main
    migrate_component(
        target,
        (
            ComponentMigration(
                component="runtime_acl_guard",
                scope=target.scope,
                version=1,
                statements=(f"CREATE TABLE {IMPORT_RECEIPT_TABLE} (receipt_id TEXT PRIMARY KEY)",),
                tables=(IMPORT_RECEIPT_TABLE,),
            ),
        ),
    )
    table = sql.Identifier(target.schema, table_name)
    runtime_role = sql.Identifier(target.user)

    with connect(target) as runtime:
        verify_runtime_role(runtime)

    with connect(target.migration_target()) as owner:
        owner.execute(
            sql.SQL("GRANT {} ON TABLE {} TO {}").format(sql.SQL(privilege), table, runtime_role)
        )

    try:
        with connect(target) as runtime:
            with pytest.raises(
                SchemaMismatch,
                match="read-only access to protected registry tables",
            ):
                verify_runtime_role(runtime)
    finally:
        with connect(target.migration_target()) as owner:
            owner.execute(
                sql.SQL("REVOKE {} ON TABLE {} FROM {}").format(
                    sql.SQL(privilege), table, runtime_role
                )
            )

    with connect(target) as runtime:
        verify_runtime_role(runtime)


@pytest.mark.parametrize(
    ("table_name", "privilege", "column_name"),
    (
        ("_cao_schema_migrations", "UPDATE", "checksum"),
        ("_cao_store_identity", "UPDATE", "store_id"),
        (IMPORT_RECEIPT_TABLE, "INSERT", "receipt_id"),
    ),
)
def test_runtime_role_rejects_column_grants_on_protected_registry_tables(
    postgres_targets, table_name, privilege, column_name
):
    target = postgres_targets.main
    migrate_component(
        target,
        (
            ComponentMigration(
                component="runtime_acl_column_guard",
                scope=target.scope,
                version=1,
                statements=(f"CREATE TABLE {IMPORT_RECEIPT_TABLE} (receipt_id TEXT PRIMARY KEY)",),
                tables=(IMPORT_RECEIPT_TABLE,),
            ),
        ),
    )
    table = sql.Identifier(target.schema, table_name)
    column = sql.Identifier(column_name)
    runtime_role = sql.Identifier(target.user)

    with connect(target) as runtime:
        verify_runtime_role(runtime)

    with connect(target.migration_target()) as owner:
        owner.execute(
            sql.SQL("GRANT {} ({}) ON TABLE {} TO {}").format(
                sql.SQL(privilege), column, table, runtime_role
            )
        )

    try:
        with connect(target) as runtime:
            with pytest.raises(
                SchemaMismatch,
                match="read-only access to protected registry tables",
            ):
                verify_runtime_role(runtime)
    finally:
        with connect(target.migration_target()) as owner:
            owner.execute(
                sql.SQL("REVOKE {} ({}) ON TABLE {} FROM {}").format(
                    sql.SQL(privilege), column, table, runtime_role
                )
            )

    with connect(target) as runtime:
        verify_runtime_role(runtime)
