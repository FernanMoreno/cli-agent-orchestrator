"""Native PostgreSQL row contracts for workflow journal defaults and nulls."""

from uuid import uuid4

import pytest

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import migrate_component, verify_component
from cli_agent_orchestrator.clients.workflows_postgres_schema import WORKFLOW_MIGRATIONS

pytestmark = pytest.mark.postgres


def _install(target) -> None:
    migrate_component(target, WORKFLOW_MIGRATIONS)
    with connect(target, readonly=True) as connection:
        verify_component(connection, WORKFLOW_MIGRATIONS)


def test_workflow_defaults_and_nullable_script_index(postgres_targets):
    target = postgres_targets.main
    _install(target)
    run_id = "workflow-semantics-" + uuid4().hex
    index_name = "script-semantics-" + uuid4().hex

    with connect(target) as connection, connection.transaction():
        connection.execute(
            "INSERT INTO workflow_run "
            "(run_id,workflow_name,spec_snapshot,inputs_json,state,started_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (run_id, "wf", "{}", "{}", "running", "2026-01-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO workflow_index "
            "(name,source_path,mode,step_count,indexed_at) "
            "VALUES (%s,%s,%s,%s,%s)",
            (index_name, "/workflow.py", "script", None, "2026-01-01T00:00:00Z"),
        )

    with connect(target, readonly=True) as connection:
        run = connection.execute(
            "SELECT tier,generation,manifest_json FROM workflow_run WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        index = connection.execute(
            "SELECT step_count,description FROM workflow_index WHERE name=%s",
            (index_name,),
        ).fetchone()

    assert run == ("yaml", "1", None)
    assert index == (None, "")


def test_workflow_event_optional_fields_and_result_envelope(postgres_targets):
    target = postgres_targets.main
    _install(target)
    run_id = "workflow-events-" + uuid4().hex

    with connect(target) as connection, connection.transaction():
        connection.execute(
            "INSERT INTO workflow_run_event "
            "(run_id,seq,event_type,event_schema_version,ts) "
            "VALUES (%s,%s,%s,%s,%s)",
            (run_id, 1, "run.started", 1, "2026-01-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO workflow_run_step "
            "(run_id,step_id,state,attempts,updated_at) VALUES (%s,%s,%s,%s,%s)",
            (run_id, "s1", "pending", 0, "2026-01-01T00:00:00Z"),
        )

    with connect(target, readonly=True) as connection:
        event = connection.execute(
            "SELECT iteration,which_guard_fired FROM workflow_run_event WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        step = connection.execute(
            "SELECT call_fingerprint,terminal_id,reprompted,error_kind,result_json "
            "FROM workflow_run_step WHERE run_id=%s AND step_id=%s",
            (run_id, "s1"),
        ).fetchone()

    assert event == (None, None)
    assert step == (None, None, None, None, None)
