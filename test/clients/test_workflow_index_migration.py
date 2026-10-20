"""Native PostgreSQL contracts for the derived workflow index schema."""

import pytest

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import migrate_component, verify_component
from cli_agent_orchestrator.clients.workflows_postgres_schema import WORKFLOW_MIGRATIONS

pytestmark = pytest.mark.postgres


def test_workflow_index_shape_and_indexes_are_verified(postgres_targets):
    target = postgres_targets["main"]
    migrate_component(target, WORKFLOW_MIGRATIONS)

    with connect(target, readonly=True) as connection:
        verify_component(connection, WORKFLOW_MIGRATIONS)
        rows = connection.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name='workflow_index'",
            (target.schema,),
        ).fetchall()
        columns = {row[0]: row[1] for row in rows}
        assert set(columns) == {
            "name",
            "source_path",
            "mode",
            "step_count",
            "description",
            "indexed_at",
        }
        assert columns["name"] == "NO"
        assert columns["source_path"] == "NO"
        assert columns["mode"] == "NO"
        assert columns["step_count"] == "YES"

        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname=%s AND tablename='workflow_run'",
                (target.schema,),
            ).fetchall()
        }
        assert {"idx_workflow_run_started_at", "idx_workflow_run_state"} <= indexes


def test_installed_manifest_detects_missing_index(postgres_targets):
    target = postgres_targets["main"]
    migrate_component(target, WORKFLOW_MIGRATIONS)
    with connect(target.migration_target()) as connection:
        with connection.transaction():
            connection.execute("DROP INDEX idx_workflow_run_state")

    with connect(target, readonly=True) as connection:
        with pytest.raises(Exception, match="schema fingerprint mismatch"):
            verify_component(connection, WORKFLOW_MIGRATIONS)
