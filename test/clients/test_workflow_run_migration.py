"""Native PostgreSQL contracts for workflow run journal DDL."""

from uuid import uuid4

import pytest

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import migrate_component, verify_component
from cli_agent_orchestrator.clients.workflows_postgres_schema import WORKFLOW_MIGRATIONS

pytestmark = pytest.mark.postgres


def _install(target):
    migrate_component(target, WORKFLOW_MIGRATIONS)
    with connect(target, readonly=True) as connection:
        verify_component(connection, WORKFLOW_MIGRATIONS)


def _columns(connection, target, table):
    rows = connection.execute(
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
        (target.schema, table),
    ).fetchall()
    return {
        row[0]: {"type": row[1], "nullable": row[2] == "YES", "default": row[3]} for row in rows
    }


def test_workflow_run_and_step_columns_are_installed(postgres_targets):
    target = postgres_targets["main"]
    _install(target)

    with connect(target, readonly=True) as connection:
        run = _columns(connection, target, "workflow_run")
        assert set(run) == {
            "run_id",
            "workflow_name",
            "spec_snapshot",
            "inputs_json",
            "state",
            "current_step_id",
            "started_at",
            "finished_at",
            "tier",
            "generation",
            "manifest_json",
            "error",
        }
        assert run["run_id"]["type"] == "text"
        assert run["workflow_name"]["nullable"] is False
        assert run["current_step_id"]["nullable"] is True
        assert "yaml" in (run["tier"]["default"] or "")
        assert "1" in (run["generation"]["default"] or "")
        assert run["manifest_json"]["nullable"] is True
        assert not {"iteration_counter", "iterations_run"} & set(run)

        step = _columns(connection, target, "workflow_run_step")
        assert set(step) == {
            "run_id",
            "step_id",
            "state",
            "attempts",
            "output_json",
            "error",
            "updated_at",
            "call_fingerprint",
            "terminal_id",
            "reprompted",
            "error_kind",
            "result_json",
        }
        assert step["attempts"]["type"] == "bigint"
        assert step["result_json"]["nullable"] is True


def test_event_and_high_water_columns_are_installed(postgres_targets):
    target = postgres_targets["main"]
    _install(target)

    with connect(target, readonly=True) as connection:
        event = _columns(connection, target, "workflow_run_event")
        assert set(event) == {
            "run_id",
            "seq",
            "event_type",
            "event_schema_version",
            "ts",
            "step_id",
            "attempt",
            "state",
            "elapsed_ms",
            "provider",
            "agent_profile",
            "engine",
            "terminal_id",
            "terminal_offset_start",
            "terminal_offset_len",
            "error_kind",
            "reason",
            "validation_result",
            "output_ref",
            "iteration",
            "which_guard_fired",
        }
        for name in ("run_id", "seq", "event_type", "event_schema_version", "ts"):
            assert event[name]["nullable"] is False
        assert event["seq"]["type"] == "bigint"
        assert event["iteration"]["nullable"] is True

        sequence = _columns(connection, target, "workflow_run_seq")
        assert set(sequence) == {"run_id", "high_water"}
        assert sequence["high_water"]["type"] == "bigint"
        assert sequence["high_water"]["nullable"] is False


def test_migration_is_idempotent_and_preserves_rows(postgres_targets):
    target = postgres_targets["main"]
    _install(target)
    run_id = "migration-" + uuid4().hex
    with connect(target) as connection:
        with connection.transaction():
            connection.execute(
                "INSERT INTO workflow_run "
                "(run_id,workflow_name,spec_snapshot,inputs_json,state,started_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (run_id, "wf", "{}", "{}", "running", "2026-01-01T00:00:00Z"),
            )
            connection.execute(
                "INSERT INTO workflow_run_event "
                "(run_id,seq,event_type,event_schema_version,ts) VALUES (%s,%s,%s,%s,%s)",
                (run_id, 1, "run.started", 1, "2026-01-01T00:00:00Z"),
            )
            connection.execute(
                "INSERT INTO workflow_run_seq (run_id,high_water) VALUES (%s,%s)",
                (run_id, 1),
            )

    _install(target)
    with connect(target, readonly=True) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM workflow_run WHERE run_id=%s", (run_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM workflow_run_event WHERE run_id=%s", (run_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT high_water FROM workflow_run_seq WHERE run_id=%s", (run_id,)
            ).fetchone()[0]
            == 1
        )
