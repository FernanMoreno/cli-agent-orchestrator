"""Migration failure must roll back its DDL and ledger row atomically."""

from uuid import uuid4

import pytest
from psycopg import Error as PsycopgError

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import ComponentMigration, migrate_component

pytestmark = pytest.mark.postgres


def test_failed_workflow_component_migration_rolls_back_ddl_and_history(postgres_targets):
    target = postgres_targets["main"]
    component = "workflow_failure_" + uuid4().hex
    table = "workflow_probe_" + uuid4().hex[:12]
    migration = (
        ComponentMigration(
            component=component,
            scope="main",
            version=1,
            statements=(
                f"CREATE TABLE {table} (value TEXT NOT NULL)",
                "SELECT 1 / 0",
            ),
            tables=(table,),
        ),
    )

    ledger_seed_id = uuid4().hex
    ledger_seed_table = f"workflow_history_seed_{ledger_seed_id[:12]}"
    ledger_seed = ComponentMigration(
        component="workflow_history_seed_" + ledger_seed_id,
        scope="main",
        version=1,
        statements=(f"CREATE TABLE {ledger_seed_table} (value INTEGER)",),
        tables=(ledger_seed_table,),
    )
    migrate_component(target, (ledger_seed,))

    with pytest.raises(PsycopgError):
        migrate_component(target, migration)

    with connect(target, readonly=True) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema=%s AND table_name=%s",
                (target.schema, table),
            ).fetchone()
            is None
        )
        assert (
            connection.execute(
                "SELECT 1 FROM _cao_schema_migrations WHERE scope=%s AND component=%s",
                (target.scope, component),
            ).fetchone()
            is None
        )
