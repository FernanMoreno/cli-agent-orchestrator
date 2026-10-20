"""Real PostgreSQL contracts for the raw workflow journal DAL."""

from __future__ import annotations

from uuid import uuid4

import pytest

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import migrate_component, verify_component
from cli_agent_orchestrator.clients.workflows_postgres_schema import WORKFLOW_MIGRATIONS
from cli_agent_orchestrator.services import workflow_journal

pytestmark = pytest.mark.postgres


def _install(target):
    # The first migration owns only the workflow index and journal tables. Later
    # migrations may add continuation tables whose foreign keys depend on Work.
    migrations = WORKFLOW_MIGRATIONS[:1]
    migrate_component(target, migrations)
    with connect(target, readonly=True) as connection:
        verify_component(connection, migrations)


def _use_target(monkeypatch, target):
    from cli_agent_orchestrator.clients.postgres.config import DatabaseTarget

    monkeypatch.setattr(
        DatabaseTarget,
        "from_settings",
        classmethod(lambda cls, scope="main", home=None: target),
    )


def test_workflow_journal_round_trips_native_records_and_event_order(postgres_targets, monkeypatch):
    target = postgres_targets["main"]
    _install(target)
    _use_target(monkeypatch, target)
    run_id = "pg-" + uuid4().hex

    workflow_journal.insert_run_with_steps(
        run_id,
        "contract",
        "spec snapshot",
        '{"input":"value"}',
        "running",
        "2026-10-11T10:00:00Z",
        [("first", "pending"), ("second", "pending")],
        "2026-10-11T10:00:00Z",
    )
    run = workflow_journal.get_run(run_id)
    assert run is not None
    assert (run.workflow_name, run.state, run.generation) == ("contract", "running", "1")
    assert [step.step_id for step in workflow_journal.get_steps(run_id)] == ["first", "second"]

    wide = 2**63 - 1
    workflow_journal.persist_high_water(run_id, 9)
    workflow_journal.persist_high_water(run_id, wide)
    workflow_journal.persist_high_water(run_id, 4)
    workflow_journal.update_step(run_id, "first", "pending", 2**40, "2026-10-11T10:00:00Z")
    workflow_journal.append_event(
        run_id,
        8,
        "step.started",
        event_schema_version=1,
        ts="2026-10-11T10:00:01Z",
        step_id="first",
    )
    workflow_journal.append_event(
        run_id,
        wide,
        "step.completed",
        event_schema_version=1,
        ts="2026-10-11T10:00:02Z",
        step_id="first",
    )
    assert [event.seq for event in workflow_journal.read_events(run_id)] == [8, wide]
    assert workflow_journal.get_steps(run_id)[0].attempts == 2**40
    assert workflow_journal.persisted_high_water(run_id) == wide
    assert workflow_journal.max_event_seq(run_id) == wide


def test_workflow_run_and_seed_steps_commit_atomically_on_postgres(postgres_targets, monkeypatch):
    target = postgres_targets["main"]
    _install(target)
    _use_target(monkeypatch, target)
    run_id = "pg-atomic-" + uuid4().hex

    with pytest.raises(ValueError, match="not enough values to unpack"):
        workflow_journal.insert_run_with_steps(
            run_id,
            "contract",
            "spec snapshot",
            "{}",
            "running",
            "2026-10-11T10:00:00Z",
            [("valid", "pending"), ("bad arity",)],
            "2026-10-11T10:00:00Z",
        )

    assert workflow_journal.get_run(run_id) is None
    assert workflow_journal.get_steps(run_id) == []
