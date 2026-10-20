"""Native PostgreSQL Work origin migration contracts."""

import pytest


@pytest.mark.postgres
def test_native_work_origin_migration_is_installed_and_verified(postgres_targets):
    """The active Work runtime uses the current native migration ledger and DDL."""
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.postgres.migrations import verify_component
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope
    from cli_agent_orchestrator.clients.work_postgres_schema import WORK_POSTGRES_MIGRATIONS
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    target = postgres_targets.main
    bootstrap_scope(target)
    WorkRepository(target, filesystem_home=postgres_targets.home).initialize()

    with connect(target, readonly=True) as connection:
        verify_component(connection, WORK_POSTGRES_MIGRATIONS)
        installed = connection.execute(
            "SELECT version,checksum FROM _cao_schema_migrations "
            "WHERE scope='main' AND component='work' ORDER BY version"
        ).fetchall()
        assert [(row["version"], row["checksum"]) for row in installed] == [
            (migration.version, migration.checksum) for migration in WORK_POSTGRES_MIGRATIONS
        ]

        tables = connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name=ANY(%s)",
            (
                target.schema,
                ["work_launch_provisions", "work_launch_origin_bindings"],
            ),
        ).fetchall()
        assert {row["table_name"] for row in tables} == {
            "work_launch_provisions",
            "work_launch_origin_bindings",
        }

        triggers = connection.execute(
            "SELECT trigger.tgname FROM pg_trigger AS trigger "
            "JOIN pg_class AS relation ON relation.oid=trigger.tgrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
            "WHERE namespace.nspname=%s AND NOT trigger.tgisinternal",
            (target.schema,),
        ).fetchall()
        assert {
            row["tgname"]
            for row in triggers
            if row["tgname"]
            in {
                "work_launch_origin_bindings_immutable_delete",
                "work_items_origin_protocol_monotonic",
            }
        } == {
            "work_launch_origin_bindings_immutable_delete",
            "work_items_origin_protocol_monotonic",
        }
