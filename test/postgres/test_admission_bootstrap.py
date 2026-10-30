"""Resource authority must remain unreadjustable by the ordinary runtime role."""

import importlib
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import (
    ComponentMigration,
    SchemaMismatch,
    migrate_component,
    verify_component,
    verify_runtime_role,
)
from cli_agent_orchestrator.clients.postgres.registry import component_migrations

pytestmark = pytest.mark.postgres


def schema():
    return importlib.import_module("cli_agent_orchestrator.clients.admission_bootstrap_schema")


@pytest.fixture
def resource_target(postgres_targets):
    target = postgres_targets.main
    migrate_component(target, schema().RESOURCE_ENVELOPE_MIGRATIONS)
    return target


def record(target, **overrides):
    fields = {
        "instance_id": str(uuid4()),
        "revision": 1,
        "protocol_version": 1,
        "store_id": target.store_id,
        "target_identity_sha256": "a" * 64,
        "process_generation": str(uuid4()),
        "process_pid": 123,
        "process_started_at": 1.0,
        "mount_ns_device": 1,
        "mount_ns_inode": 2,
        "cgroup_ns_device": 1,
        "cgroup_ns_inode": 3,
        "resource_view_sha256": "b" * 64,
        "memory_budget_bytes": 1024 * 1024 * 1024,
        "cpu_budget_millicores": 1000,
        "storage_budget_slots": 10,
        "complete_hierarchy": True,
        "root_is_host": False,
        "active": True,
        "actor_principal_id": "principal-" + "c" * 64,
    }
    fields.update(overrides)
    return fields


def insert_record(connection, fields):
    connection.execute(
        sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(schema().RESOURCE_ENVELOPE_TABLE),
            sql.SQL(",").join(map(sql.Identifier, fields)),
            sql.SQL(",").join(sql.Placeholder() for _ in fields),
        ),
        tuple(fields.values()),
    )


def test_resource_authority_is_registered_in_main_only():
    migrations = schema().RESOURCE_ENVELOPE_MIGRATIONS
    assert migrations[0].scope == "main"
    assert migrations[0].version == 1
    assert migrations[0].tables == (schema().RESOURCE_ENVELOPE_TABLE,)
    assert migrations in component_migrations("main")
    for scope in ("auth", "peer"):
        assert not any(
            group[0].component == migrations[0].component for group in component_migrations(scope)
        )


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO {} DEFAULT VALUES",
        "UPDATE {} SET active=false",
        "DELETE FROM {}",
        "TRUNCATE {}",
    ],
    ids=["insert", "update", "delete", "truncate"],
)
def test_runtime_can_verify_and_read_but_cannot_change_authority(resource_target, statement):
    target = resource_target
    with connect(target) as runtime:
        verify_runtime_role(runtime)
        verify_component(runtime, schema().RESOURCE_ENVELOPE_MIGRATIONS)
        table = sql.Identifier(schema().RESOURCE_ENVELOPE_TABLE)
        assert runtime.execute(sql.SQL("SELECT count(*) FROM {}").format(table)).fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute(sql.SQL(statement).format(table))


def test_explicit_owner_can_store_history_visible_to_readonly_runtime(resource_target):
    target = resource_target
    first = record(target)
    with connect(target.migration_target()) as owner:
        insert_record(owner, first)
        insert_record(owner, {**first, "revision": 2, "active": False})
    with connect(target, readonly=True) as runtime:
        rows = runtime.execute(
            sql.SQL("SELECT revision,active FROM {} WHERE instance_id=%s ORDER BY revision").format(
                sql.Identifier(schema().RESOURCE_ENVELOPE_TABLE)
            ),
            (first["instance_id"],),
        ).fetchall()
        assert [tuple(row) for row in rows] == [(1, True), (2, False)]


def unrelated_migration():
    return (
        ComponentMigration(
            component="resource_acl_regression",
            scope="main",
            version=1,
            statements=("SELECT 1",),
            tables=(),
        ),
    )


def test_later_migration_cannot_regrant_runtime_authority(resource_target):
    target = resource_target
    migrate_component(target, unrelated_migration())
    with connect(target, readonly=True) as runtime:
        verify_runtime_role(runtime)
        privileges = runtime.execute(
            "SELECT has_table_privilege(current_user,%s,'SELECT'),"
            "has_table_privilege(current_user,%s,'INSERT'),"
            "has_table_privilege(current_user,%s,'UPDATE'),"
            "has_table_privilege(current_user,%s,'DELETE')",
            (f"{target.schema}.{schema().RESOURCE_ENVELOPE_TABLE}",) * 4,
        ).fetchone()
        assert tuple(privileges) == (True, False, False, False)


@pytest.mark.parametrize("public", [False, True], ids=["runtime", "public"])
def test_later_migration_retires_explicit_column_authority(resource_target, public):
    target = resource_target
    with connect(target.migration_target()) as owner:
        owner.execute(
            sql.SQL("GRANT UPDATE(active) ON {} TO {}").format(
                sql.Identifier(schema().RESOURCE_ENVELOPE_TABLE),
                sql.SQL("PUBLIC") if public else sql.Identifier(target.user),
            )
        )
    migrate_component(target, unrelated_migration())
    with connect(target, readonly=True) as runtime:
        verify_runtime_role(runtime)
        writable = runtime.execute(
            "SELECT has_any_column_privilege(current_user,%s,'UPDATE')",
            (f"{target.schema}.{schema().RESOURCE_ENVELOPE_TABLE}",),
        ).fetchone()[0]
        assert writable is False


@pytest.mark.parametrize(
    ("privilege", "public"),
    [
        ("INSERT", False),
        ("UPDATE(active)", False),
        ("DELETE", True),
        ("TRUNCATE", False),
        ("REFERENCES", True),
        ("TRIGGER", False),
        ("MAINTAIN", False),
    ],
)
def test_effective_authority_grants_are_detected(resource_target, privilege, public):
    target = resource_target
    with connect(target.migration_target()) as owner:
        owner.execute(
            sql.SQL("GRANT {} ON {} TO {}").format(
                sql.SQL(privilege),
                sql.Identifier(schema().RESOURCE_ENVELOPE_TABLE),
                sql.SQL("PUBLIC") if public else sql.Identifier(target.user),
            )
        )
    with connect(target, readonly=True) as runtime:
        with pytest.raises(SchemaMismatch, match="read-only access"):
            verify_runtime_role(runtime)


def test_migration_rejects_an_authority_view_in_place_of_a_table(resource_target):
    target = resource_target
    table = sql.Identifier(target.schema, schema().RESOURCE_ENVELOPE_TABLE)
    with connect(target.migration_target()) as owner:
        owner.execute(sql.SQL("DROP TABLE {}").format(table))
        owner.execute(sql.SQL("CREATE VIEW {} AS SELECT 1 AS value").format(table))
    with pytest.raises(SchemaMismatch):
        migrate_component(target, unrelated_migration())


def test_runtime_role_rejects_an_authority_view_even_with_read_permission(resource_target):
    target = resource_target
    table = sql.Identifier(target.schema, schema().RESOURCE_ENVELOPE_TABLE)
    with connect(target.migration_target()) as owner:
        owner.execute(sql.SQL("DROP TABLE {}").format(table))
        owner.execute(sql.SQL("CREATE VIEW {} AS SELECT 1 AS value").format(table))
        owner.execute(
            sql.SQL("GRANT SELECT ON {} TO {}").format(table, sql.Identifier(target.user))
        )
    with connect(target, readonly=True) as runtime:
        with pytest.raises(SchemaMismatch, match="read-only access"):
            verify_runtime_role(runtime)


@pytest.mark.parametrize(
    "invalid",
    [
        {"revision": 0},
        {"protocol_version": 2},
        {"memory_budget_bytes": 0},
        {"cpu_budget_millicores": -1},
        {"storage_budget_slots": 0},
        {"process_started_at": float("nan")},
        {"process_started_at": float("inf")},
        {"mount_ns_inode": 0},
        {"cgroup_ns_device": -1},
        {"resource_view_sha256": "unknown"},
        {"target_identity_sha256": "z" * 64},
        {"root_is_host": True, "complete_hierarchy": False},
        {"actor_principal_id": "body-supplied-identity"},
    ],
)
def test_invalid_authority_data_is_rejected_by_postgres(resource_target, invalid):
    target = resource_target
    with connect(target.migration_target()) as owner:
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_record(owner, record(target, **invalid))
