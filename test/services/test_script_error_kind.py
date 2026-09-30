"""Structured script failure kinds survive settlement and cold journal reads."""

import json
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.models.workflow_runtime import RunState
from cli_agent_orchestrator.services import script_runner, workflow_journal, workflow_service
from cli_agent_orchestrator.services.agent_step import StepExecutionError


@pytest.fixture
def script_journal(tmp_path, monkeypatch):
    database = tmp_path / "journal.db"
    monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", database)
    monkeypatch.setattr(workflow_journal, "_MIGRATED_PATHS", set())
    monkeypatch.setattr(workflow_service, "run_registry", {})
    monkeypatch.setattr(script_runner, "run_registry", workflow_service.run_registry)
    workflow_journal.insert_run(
        run_id="typed-error",
        workflow_name="script",
        spec_snapshot="{}",
        inputs_json="{}",
        state="running",
        started_at="2026-09-22T00:00:00Z",
        tier="script",
    )
    record = script_runner.ScriptRunRecord(
        run_id="typed-error",
        workflow_name="script",
        state=RunState.RUNNING,
        cancelled=False,
        current_step_id=None,
        step_states={},
        process=None,
        generation="1",
        started_at="2026-09-22T00:00:00Z",
        finished_at=None,
    )
    workflow_service.run_registry[record.run_id] = record
    callback = script_runner.record_step_completion(
        {"CAO_WORKFLOW_RUN_ID": record.run_id, "CAO_WORKFLOW_STEP_ID": "step"}
    )
    assert callback is not None
    return database, callback


def test_structured_kind_survives_script_settlement_and_process_restart(script_journal):
    database, complete = script_journal
    # The misleading word would classify this as timeout if structure were lost.
    error = StepExecutionError("timeout is irrelevant; delivery needs inspection", kind="reconcile")
    complete("terminal", str(error), None, error_kind=error.kind)
    row = workflow_journal.get_step("typed-error", "step")
    assert row.state == "failed" and row.error_kind == "reconcile"
    code = (
        "import json,sys; from pathlib import Path; "
        "from cli_agent_orchestrator import constants; "
        "constants.DATABASE_FILE=Path(sys.argv[1]); "
        "from cli_agent_orchestrator.services import workflow_journal as j; "
        "r=j.get_step('typed-error','step'); "
        "print(json.dumps([r.state,r.error_kind,r.attempts]))"
    )
    reopened = subprocess.run(
        [sys.executable, "-c", code, str(database)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(reopened.stdout) == ["failed", "reconcile", 1]


def test_success_clears_previous_durable_kind(script_journal):
    _, complete = script_journal
    complete("terminal", "provider failed", None, error_kind="error")
    complete("terminal", None, "recovered")
    row = workflow_journal.get_step("typed-error", "step")
    assert (row.state, row.error, row.error_kind, row.attempts) == ("completed", None, None, 2)


@pytest.mark.parametrize("state", ["completed", "completed_unvalidated"])
def test_successful_journal_settlement_cannot_keep_stale_failure_type(script_journal, state):
    workflow_journal.settle_step(
        "typed-error", "step", state, "now", "{}", None, None, error_kind="timeout"
    )
    assert workflow_journal.get_step("typed-error", "step").error_kind is None


def test_failed_settlement_rolls_back_kind_with_other_fields(script_journal):
    database, _ = script_journal
    workflow_journal.begin_step("typed-error", "step", "now", "fingerprint")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER reject_settlement BEFORE UPDATE ON workflow_run_step "
            "WHEN NEW.state='failed' BEGIN SELECT RAISE(ABORT,'reject'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        workflow_journal.settle_step(
            "typed-error",
            "step",
            "failed",
            "later",
            "{}",
            None,
            "failure",
            error_kind="reconcile",
        )
    row = workflow_journal.get_step("typed-error", "step")
    assert (row.state, row.error_kind, row.attempts, row.result_json) == ("running", None, 0, None)


def test_projection_uses_durable_type_before_legacy_text_inference(script_journal):
    from cli_agent_orchestrator.api.main import _resolve_error_kind

    _, complete = script_journal
    complete("terminal", "timeout wording", None, error_kind="reconcile")
    rows = workflow_journal.get_steps("typed-error")
    assert _resolve_error_kind(SimpleNamespace(state="failed"), rows) == "reconcile"
    # Old callers have no typed input. Keep NULL, so only those rows use fallback.
    complete("terminal", "legacy timeout wording", None)
    rows = workflow_journal.get_steps("typed-error")
    assert rows[0].error_kind is None
    assert _resolve_error_kind(SimpleNamespace(state="failed"), rows) == "timeout"


def test_retry_begin_clears_kind_on_current_step_projection(script_journal):
    _, complete = script_journal
    complete("terminal", "old attempt timed out", None, error_kind="timeout")
    workflow_journal.begin_step("typed-error", "step", "later", "new-fingerprint")
    row = workflow_journal.get_step("typed-error", "step")
    assert (row.state, row.error_kind, row.attempts) == ("running", None, 1)
    assert row.call_fingerprint == "new-fingerprint"


@pytest.mark.parametrize("state", ["running", "completed"])
def test_run_projection_does_not_report_old_step_failure_as_current_run_kind(script_journal, state):
    from cli_agent_orchestrator.api.main import _resolve_error_kind

    _, complete = script_journal
    complete("terminal", "a failed call the script can catch", None, error_kind="reconcile")
    rows = workflow_journal.get_steps("typed-error")
    assert _resolve_error_kind(SimpleNamespace(state=state), rows) is None
    # This read must retain the underlying step failure as evidence.
    assert workflow_journal.get_step("typed-error", "step").error_kind == "reconcile"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,http_status",
    [("error", 502), ("timeout", 504), ("reconcile", 409), ("quota_wait", 409), (None, 504)],
)
async def test_api_producer_forwards_structured_kind_to_script_journal(
    script_journal, monkeypatch, tmp_path, kind, http_status
):
    from fastapi import BackgroundTasks, HTTPException, Request

    from cli_agent_orchestrator.api import main

    async def fail_execution(**kwargs):
        if kind is None:
            raise TimeoutError("opaque diagnostic")
        raise StepExecutionError("opaque diagnostic", kind=kind, terminal_id="abc12345")

    monkeypatch.setattr(main, "run_agent_step", fail_execution)
    monkeypatch.setattr(main, "get_plugin_registry", lambda request: None)
    monkeypatch.setattr(main.app.state, "work_workflow_origins", None, raising=False)
    body = main.RunStepRequest(
        provider="kiro_cli",
        agent="developer",
        prompt="do it",
        working_directory=str(tmp_path),
        env_vars={
            "CAO_WORKFLOW_RUN_ID": "typed-error",
            "CAO_WORKFLOW_STEP_ID": "step",
            "CAO_WORKFLOW_GENERATION": "1",
        },
    )
    with pytest.raises(HTTPException) as failure:
        await main.run_step(Request({"type": "http", "app": main.app}), BackgroundTasks(), body)
    assert failure.value.status_code == http_status
    assert failure.value.detail["kind"] == (kind or "timeout")
    row = workflow_journal.get_step("typed-error", "step")
    if kind == "quota_wait":
        assert row is None  # A resumable pause is not a terminal failure.
    else:
        assert row.state == "failed" and row.error_kind == (kind or "timeout")
