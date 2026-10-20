"""Attempt contracts use the native PostgreSQL journal before task delivery."""

import importlib.util
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import AsyncMock

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import (
    Connection,
    DatabaseTarget,
    IntegrityError,
    connect,
)
from cli_agent_orchestrator.models.workflow_runtime import RunState
from cli_agent_orchestrator.services import (
    agent_step,
    script_runner,
    workflow_journal,
    workflow_service,
)


def _owner_connection() -> Connection:
    """Open the explicit migration role for deliberate catalog-damage tests."""
    return connect(DatabaseTarget.from_settings("main").migration_target())


def _reject_contract_insert(monkeypatch) -> None:
    """Cause a native CHECK violation without changing the verified catalog."""
    original_connect = workflow_journal._connect

    def connect_with_fault(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        original_execute = connection.execute

        def fail_contract_insert(statement, params=(), *, prepare: bool | None = None):
            normalized = " ".join(str(statement).split()).upper()
            if normalized.startswith("INSERT INTO WORK_STEP_CONTRACTS VALUES"):
                invalid = list(params)
                invalid[3] = 0
                params = tuple(invalid)
            if prepare is None:
                return original_execute(statement, params)
            return original_execute(statement, params, prepare=prepare)

        connection.execute = fail_contract_insert
        return connection

    monkeypatch.setattr(workflow_journal, "_connect", connect_with_fault)


def _drop_contract_immutability_trigger() -> None:
    with _owner_connection() as connection:
        connection.execute(
            sql.SQL(
                "DROP TRIGGER work_step_contracts_immutable_update ON {}.work_step_contracts"
            ).format(sql.Identifier(connection.target.schema))
        )


def _damage_work_migration_history() -> None:
    with _owner_connection() as connection:
        connection.execute(
            "UPDATE _cao_schema_migrations SET checksum='damaged' "
            "WHERE scope='main' AND component='work' AND version=2"
        )


def test_contract_schema_is_available_to_the_versioned_migrator():
    from cli_agent_orchestrator.clients.work_postgres_schema import WORK_POSTGRES_MIGRATIONS

    assert (
        importlib.util.find_spec("cli_agent_orchestrator.clients.work_postgres_schema") is not None
    )
    assert any("work_step_contracts" in migration.tables for migration in WORK_POSTGRES_MIGRATIONS)


def test_contract_verification_uses_native_connection_record_mapping():
    """The contract gate is backed by a verified native PostgreSQL connection."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    repository = WorkRepository(DatabaseTarget.from_settings("main"))
    with repository.connection() as connection:
        WorkRepository._verify(connection)
        row = connection.execute("SELECT 1 AS value").fetchone()
        assert row[0] == row["value"] == 1


@pytest.fixture
def journal(monkeypatch):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    repository = WorkRepository(DatabaseTarget.from_settings("main"))
    repository.initialize()
    workflow_journal.insert_run("run", "script", "{}", "{}", "running", "now", tier="script")
    record = script_runner.ScriptRunRecord(
        run_id="run",
        workflow_name="script",
        state=RunState.RUNNING,
        cancelled=False,
        current_step_id=None,
        step_states={},
        process=None,
        generation="1",
        started_at="now",
        finished_at=None,
    )
    monkeypatch.setattr(script_runner, "run_registry", {"run": record})
    return None


@pytest.fixture
def yaml_journal():
    """A verified per-test PostgreSQL store holding a live YAML run."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    WorkRepository(DatabaseTarget.from_settings("main")).initialize()
    workflow_journal.insert_run("yaml-run", "yaml", "{}", "{}", "running", "now", tier="yaml")
    return None


def contract():
    fields = {
        key: {"status": "unknown", "value": None, "provenance": "not_observed"}
        for key in (
            "provider",
            "profile",
            "tools",
            "engine",
            "working_directory",
            "model",
            "effort",
            "timeout_seconds",
            "readiness_timeout_seconds",
            "teardown",
            "prompt_redelivery",
            "worktree",
            "retry_policy",
            "redelivery_limit",
            "pickup_grace_seconds",
        )
    }
    fields["timeout_seconds"] = {"status": "known", "value": 12.0, "provenance": "step_argument"}
    return {
        "schema_version": 1,
        "terminal_id": "abc12345",
        "call_fingerprint": "v2:identity",
        "fields": fields,
    }


@pytest.mark.asyncio
async def test_yaml_contract_commits_before_terminal_allocation(yaml_journal, monkeypatch):
    """Removing the preflight hook must make terminal creation impossible to reach."""
    from cli_agent_orchestrator.services import workflow_service

    observed = []

    async def create(*args, **kwargs):
        persisted = workflow_journal.get_step_contract("yaml-run", "step", "1", 1)
        observed.append(persisted)
        assert persisted["fields"]["provider"] == {
            "status": "known",
            "value": "codex",
            "provenance": "step_argument",
        }
        for name in ("tools", "working_directory", "model", "effort"):
            assert persisted["fields"][name]["status"] == "unknown"
            assert persisted["fields"][name]["value"] is None
        raise RuntimeError("terminal allocation followed the durable contract")

    monkeypatch.setattr(agent_step.terminal_service, "create_terminal", create)
    recorder = workflow_service._yaml_attempt_recorder(
        "yaml-run", "step", "1", attempt_number=1, retry_count=0
    )

    with pytest.raises(RuntimeError, match="durable contract"):
        await agent_step.run_agent_step(
            "codex",
            "developer",
            "task",
            timeout=17,
            pre_delivery_recorder=recorder,
        )

    assert len(observed) == 1
    assert observed[0]["call_fingerprint"]


def test_yaml_current_contract_fence_uses_readonly_native_snapshot(yaml_journal, monkeypatch):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    fields = contract()["fields"]
    attempt = workflow_journal.begin_yaml_step_with_contract(
        "yaml-run", "step", "1", "now", "v2:stable", fields
    )
    real_read_snapshot = WorkRepository.read_snapshot
    observed_targets: list[tuple[str, int, str, str, str]] = []

    @contextmanager
    def checked_read_snapshot(repository: WorkRepository) -> Iterator[Connection]:
        with real_read_snapshot(repository) as connection:
            assert connection.read_only
            observed_targets.append(connection.target.identity)
            yield connection

    monkeypatch.setattr(WorkRepository, "read_snapshot", checked_read_snapshot)
    workflow_journal.assert_yaml_step_contract_current(
        "yaml-run", "step", "1", attempt, "v2:stable", fields
    )
    assert observed_targets == [DatabaseTarget.from_settings("main").identity]


@pytest.mark.parametrize("stale", (None, "generation", "fingerprint", "attempt"))
def test_yaml_result_is_retained_only_for_current_attempt(yaml_journal, stale):
    from cli_agent_orchestrator.models.terminal import AgentStepResult, TerminalStatus

    fields = contract()["fields"]
    attempt = workflow_journal.begin_yaml_step_with_contract(
        "yaml-run", "step", "1", "now", "v2:stable", fields
    )
    result = AgentStepResult(
        terminal_id="abc12345", last_message="frozen result π", status=TerminalStatus.COMPLETED
    )
    args = (
        "yaml-run",
        "step",
        "2" if stale == "generation" else "1",
        attempt + 1 if stale == "attempt" else attempt,
        "v2:changed" if stale == "fingerprint" else "v2:stable",
        result,
    )
    if stale:
        with pytest.raises(ValueError):
            workflow_journal.record_yaml_step_result(
                *args, result_json=workflow_service._legacy_result_envelope(result)
            )
        assert workflow_journal.get_step("yaml-run", "step").result_json is None
    else:
        workflow_journal.record_yaml_step_result(
            *args, result_json=workflow_service._legacy_result_envelope(result)
        )
        retained = workflow_journal.get_step("yaml-run", "step")
        assert retained.state == "running"  # output collection still owns completion
        assert json.loads(retained.result_json)["last_message"] == result.last_message
        assert retained.terminal_id == result.terminal_id


def test_yaml_retry_rejects_a_changed_first_contract(yaml_journal):
    """Changing the retry's effective identity cannot authorize another send."""
    fields = agent_step._effective_step_fields(
        provider="codex",
        agent="developer",
        allowed_tools=None,
        engine=None,
        model=None,
        working_directory=None,
        use_worktree=False,
        created_here=True,
        timeout=17,
        ready_timeout=agent_step.DEFAULT_READY_TIMEOUT,
        teardown=True,
        prompt_redelivery=True,
    )
    first = workflow_journal.begin_yaml_step_with_contract(
        "yaml-run", "step", "1", "now", "v2:stable", fields
    )
    workflow_journal.fail_yaml_step_attempt(
        "yaml-run", "step", "1", first, "v2:stable", "later", "failed", "error"
    )
    changed = {name: dict(value) for name, value in fields.items()}
    changed["model"] = {
        "status": "known",
        "value": "invented-model",
        "provenance": "step_argument",
    }

    with pytest.raises(ValueError, match="does not match first attempt"):
        workflow_journal.begin_yaml_step_with_contract(
            "yaml-run", "step", "1", "again", "v2:changed", changed
        )


@pytest.mark.parametrize(
    "payload",
    ["{}", '{"schema_version":null}', '{"schema_version":true}', '{"schema_version":"1"}'],
)
def test_database_rejects_missing_or_untyped_schema_version(journal, payload):
    with workflow_journal._connect() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                "INSERT INTO work_step_contracts VALUES ('run','step','1',1,%s,'now')", (payload,)
            )


@pytest.mark.asyncio
async def test_api_contract_rejection_does_not_settle_a_competing_attempt(
    journal, monkeypatch, tmp_path
):
    from fastapi import BackgroundTasks, HTTPException, Request

    from cli_agent_orchestrator.api import main
    from cli_agent_orchestrator.services import workflow_service

    monkeypatch.setattr(workflow_service, "run_registry", script_runner.run_registry)

    async def create(*args, **kwargs):
        # A contender commits after this request passed the replay read, but
        # before its newly allocated terminal reaches the contract gate.
        contender = script_runner.make_step_terminal_recorder(
            {"CAO_WORKFLOW_RUN_ID": "run", "CAO_WORKFLOW_STEP_ID": "step"}
        )
        contender.record_contract("abc12345", "v2:identity", contract())
        return SimpleNamespace(id="def67890")

    monkeypatch.setattr(agent_step.terminal_service, "create_terminal", create)
    monkeypatch.setattr(agent_step.terminal_service, "get_terminal_metadata", lambda _: {})
    monkeypatch.setattr(main, "get_plugin_registry", lambda request: None)
    monkeypatch.setattr(main.app.state, "work_workflow_origins", None, raising=False)
    body = main.RunStepRequest(
        provider="codex",
        agent="developer",
        prompt="do it",
        working_directory=str(tmp_path),
        env_vars={
            "CAO_WORKFLOW_RUN_ID": "run",
            "CAO_WORKFLOW_STEP_ID": "step",
            "CAO_WORKFLOW_GENERATION": "1",
        },
    )
    with pytest.raises(HTTPException) as failure:
        await main.run_step(
            Request({"type": "http", "app": main.app}),
            BackgroundTasks(),
            body,
            workflow_run_credential=None,
        )
    assert failure.value.status_code == 409
    assert failure.value.detail["kind"] == "contract_rejected"
    assert failure.value.detail["terminal_id"] == "def67890"
    assert script_runner.run_registry["run"].step_states["step"].terminal_id == "abc12345"
    row = workflow_journal.get_step("run", "step")
    assert (row.state, row.attempts, row.terminal_id, row.error_kind) == (
        "running",
        0,
        "abc12345",
        None,
    )


def test_atomic_attempt_contract_is_immutable_and_identical_retry_does_not_overwrite(journal):
    first = workflow_journal.begin_step_with_contract("run", "step", "1", "now", contract())
    assert first == 1
    assert (
        workflow_journal.begin_step_with_contract(
            "run", "step", "1", "later", contract(), attempt_number=1
        )
        == 1
    )
    changed = contract()
    changed["fields"]["timeout_seconds"]["value"] = 99
    with pytest.raises(ValueError):
        workflow_journal.begin_step_with_contract(
            "run", "step", "1", "later", changed, attempt_number=1
        )


def test_reexecution_contract_compares_the_gate_snapshot_atomically(journal):
    workflow_journal.begin_step("run", "step", "before-gate", "v2:prior")
    expected_prior = workflow_journal.get_step("run", "step").attempt_identity

    workflow_journal.begin_step("run", "step", "after-gate", "v2:contender")
    with pytest.raises(ValueError, match="changed after replay decision"):
        workflow_journal.begin_step_with_contract(
            "run", "step", "1", "delivery", contract(), expected_prior=expected_prior
        )


def test_reexecution_contract_accepts_the_unchanged_gate_snapshot(journal):
    workflow_journal.begin_step("run", "step", "before-gate", "v2:prior")
    expected_prior = workflow_journal.get_step("run", "step").attempt_identity
    number = workflow_journal.begin_step_with_contract(
        "run", "step", "1", "delivery", contract(), expected_prior=expected_prior
    )
    assert number == 1
    with workflow_journal._connect() as connection:
        stored = connection.execute("SELECT contract_json FROM work_step_contracts").fetchone()[0]
        assert json.loads(stored)["fields"]["timeout_seconds"]["value"] == 12
        with pytest.raises(IntegrityError):
            with connection.transaction():
                connection.execute("DELETE FROM work_step_contracts")
    assert workflow_journal.get_step("run", "step").state == "running"


def test_unresolved_attempt_blocks_second_admission_and_terminal_retry_increments(journal):
    def begin():
        try:
            return workflow_journal.begin_step_with_contract("run", "step", "1", "now", contract())
        except ValueError:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: begin(), range(2)))
    assert sorted(results, key=str) == [1, "blocked"]
    workflow_journal.settle_step(
        "run", "step", "failed", "later", None, None, "failed", error_kind="error"
    )
    assert begin() == 2


@pytest.mark.parametrize("tier", ["script", "yaml"])
def test_concurrent_same_step_attempt_allocation_has_one_durable_winner(
    journal, yaml_journal, monkeypatch, tier
):
    """Two real PostgreSQL transactions cannot allocate the same run-step attempt."""
    run_id = "run" if tier == "script" else "yaml-run"
    before_parent_read = Barrier(2)
    original_connect = workflow_journal._connect

    def connect_racing_attempts():
        connection = original_connect()
        original_execute = connection.execute
        paused_parent_read = False

        def execute_with_rendezvous(statement, params=(), *, prepare: bool | None = None):
            nonlocal paused_parent_read
            normalized = " ".join(str(statement).split())
            if not paused_parent_read and normalized.startswith(
                "SELECT generation,state,tier FROM workflow_run WHERE run_id=%s"
            ):
                paused_parent_read = True
                before_parent_read.wait(timeout=10)
                # Keep both PostgreSQL statement snapshots open together so the
                # old unlocked allocator deterministically reads the same parent
                # state. The rendezvous is before the parent lock; no thread waits
                # after a row lock that the other contender needs.
                statement = normalized.replace(
                    " WHERE run_id=%s",
                    " WHERE run_id=%s AND (SELECT 1 FROM (SELECT pg_sleep(0.25)) "
                    "AS _race_pause)=1",
                    1,
                )
            if prepare is None:
                return original_execute(statement, params)
            return original_execute(statement, params, prepare=prepare)

        connection.execute = execute_with_rendezvous
        return connection

    monkeypatch.setattr(workflow_journal, "_connect", connect_racing_attempts)

    def begin():
        try:
            if tier == "script":
                return workflow_journal.begin_step_with_contract(
                    run_id, "race-step", "1", "now", contract()
                )
            value = contract()
            return workflow_journal.begin_yaml_step_with_contract(
                run_id,
                "race-step",
                "1",
                "now",
                value["call_fingerprint"],
                value["fields"],
            )
        except ValueError:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(begin) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]

    assert sorted(results, key=str) == [1, "blocked"]
    with workflow_journal._connect() as connection:
        rows = connection.execute(
            "SELECT attempt_number FROM work_step_contracts "
            "WHERE run_id=%s AND step_id='race-step' AND generation='1'",
            (run_id,),
        ).fetchall()
    assert [row[0] for row in rows] == [1]


def test_stale_generation_and_cancelled_run_cannot_create_contract(journal):
    with pytest.raises(ValueError):
        workflow_journal.begin_step_with_contract("run", "step", "2", "now", contract())
    with workflow_journal._connect() as connection:
        connection.execute("UPDATE workflow_run SET state='cancelled' WHERE run_id='run'")
    with pytest.raises(ValueError):
        workflow_journal.begin_step_with_contract("run", "step", "1", "now", contract())
    assert workflow_journal.get_step("run", "step") is None


def test_callback_cannot_adopt_a_new_generation_after_it_was_created(journal):
    callback = script_runner.make_step_terminal_recorder(
        {"CAO_WORKFLOW_RUN_ID": "run", "CAO_WORKFLOW_STEP_ID": "step"}
    )
    script_runner.run_registry["run"].generation = "2"
    with workflow_journal._connect() as connection:
        connection.execute("UPDATE workflow_run SET generation='2' WHERE run_id='run'")
    with pytest.raises(ValueError):
        callback.record_contract("abc12345", "v2:identity", contract())
    assert workflow_journal.get_step("run", "step") is None


def test_contract_insert_failure_rolls_back_running_row(journal, monkeypatch):
    _reject_contract_insert(monkeypatch)
    with pytest.raises(IntegrityError):
        workflow_journal.begin_step_with_contract("run", "step", "1", "now", contract())
    assert workflow_journal.get_step("run", "step") is None
    with workflow_journal._connect() as connection:
        assert connection.execute("SELECT count(*) FROM work_step_contracts").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["timeout", "reconcile", None])
def test_failed_but_unresolved_delivery_requires_explicit_recovery(journal, kind):
    workflow_journal.begin_step_with_contract("run", "step", "1", "now", contract())
    workflow_journal.settle_step(
        "run", "step", "failed", "later", None, None, "uncertain", error_kind=kind
    )
    with pytest.raises(ValueError):
        workflow_journal.begin_step_with_contract("run", "step", "1", "again", contract())


def test_cold_process_reads_frozen_contract_without_resolving_a_profile(journal):
    workflow_journal.begin_step_with_contract("run", "step", "1", "now", contract())
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; "
            "from cli_agent_orchestrator.services import workflow_journal as j; "
            "print(json.dumps(j.get_step_contract('run','step','1',1)))",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(result.stdout) == contract()


def test_cold_recovery_rejects_an_unverified_contract_store(journal):
    """Removing immutable schema evidence must block a fresh recovery read.

    This fails if ``get_step_contract`` stops using the verified work-store
    reader and simply returns JSON from a modified database.
    """
    workflow_journal.begin_step_with_contract("run", "step", "1", "now", contract())
    _drop_contract_immutability_trigger()

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; "
            "from cli_agent_orchestrator.services import workflow_journal as j; "
            "print(json.dumps(j.get_step_contract('run','step','1',1)))",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "SchemaMismatch" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("mutation", ["extra", "unknown_value", "nonfinite"])
def test_contract_validation_rejects_unstructured_or_sensitive_payload(journal, mutation):
    value = contract()
    if mutation == "extra":
        value["prompt"] = "not permitted"
    elif mutation == "unknown_value":
        value["fields"]["model"]["value"] = "invented"
    else:
        value["fields"]["timeout_seconds"]["value"] = float("inf")
    with pytest.raises(ValueError):
        workflow_journal.begin_step_with_contract("run", "step", "1", "now", value)
    assert workflow_journal.get_step("run", "step") is None


def test_effective_capture_redacts_sensitive_direct_fields_before_persistence(journal, monkeypatch):
    monkeypatch.setattr(agent_step.terminal_service, "get_terminal_metadata", lambda _: None)
    value = agent_step._effective_step_contract(
        "abc12345",
        "v2:identity",
        agent_step._effective_step_fields(
            provider="codex",
            agent="AKIAIOSFODNN7EXAMPLE",
            allowed_tools=None,
            engine=None,
            model=None,
            working_directory=None,
            use_worktree=False,
            created_here=True,
            timeout=12,
            ready_timeout=100,
            teardown=True,
            prompt_redelivery=False,
        ),
    )
    workflow_journal.begin_step_with_contract("run", "step", "1", "now", value)
    stored = workflow_journal.get_step_contract("run", "step", "1", 1)
    assert stored["fields"]["profile"] == {
        "status": "unknown",
        "value": None,
        "provenance": "redacted",
    }
    assert stored["fields"]["readiness_timeout_seconds"]["value"] == 100
    assert stored["fields"]["teardown"]["value"] is True
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(stored)


def test_contract_captures_applied_redelivery_bounds(monkeypatch):
    enabled = agent_step._effective_step_fields(
        provider="codex",
        agent="developer",
        allowed_tools=None,
        engine=None,
        model=None,
        working_directory=None,
        use_worktree=False,
        created_here=True,
        timeout=12,
        ready_timeout=100,
        teardown=True,
        prompt_redelivery=True,
    )
    disabled = agent_step._effective_step_fields(
        provider="codex",
        agent="developer",
        allowed_tools=None,
        engine=None,
        model=None,
        working_directory=None,
        use_worktree=False,
        created_here=False,
        timeout=12,
        ready_timeout=100,
        teardown=True,
        prompt_redelivery=False,
    )
    assert enabled["redelivery_limit"]["value"] == 3
    assert enabled["pickup_grace_seconds"]["value"] == 8
    assert disabled["redelivery_limit"]["value"] == 0
    assert disabled["pickup_grace_seconds"]["status"] == "not_applicable"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at_creation", [None, "cancel", "storage"])
async def test_real_step_hook_captures_authorized_values_before_delivery(
    journal, monkeypatch, failure_at_creation
):
    metadata = {
        "provider": "codex",
        "agent_profile": "effective-profile",
        "allowed_tools": ["Read"],
        "engine": None,
        "working_directory": "/effective/worktree",
    }

    async def create(*args, **kwargs):
        if failure_at_creation == "cancel":
            with workflow_journal._connect() as connection:
                connection.execute("UPDATE workflow_run SET state='cancelled' WHERE run_id='run'")
        elif failure_at_creation == "storage":
            _reject_contract_insert(monkeypatch)
        return SimpleNamespace(id="abc12345")

    delivered = []

    def send(*args, **kwargs):
        with workflow_journal._connect() as connection:
            raw = connection.execute("SELECT contract_json FROM work_step_contracts").fetchone()[0]
        delivered.append(json.loads(raw))
        raise RuntimeError("stop after delivery boundary")

    monkeypatch.setattr(agent_step.terminal_service, "create_terminal", create)
    monkeypatch.setattr(agent_step.terminal_service, "get_terminal_metadata", lambda _: metadata)
    monkeypatch.setattr(agent_step.terminal_service, "send_input", send)
    monkeypatch.setattr(agent_step, "wait_until_status", AsyncMock(return_value=True))
    monkeypatch.setattr(agent_step.frozen_run_memory, "frozen_memory_for", lambda *args: None)
    callback = script_runner.make_step_terminal_recorder(
        {"CAO_WORKFLOW_RUN_ID": "run", "CAO_WORKFLOW_STEP_ID": "step"}
    )
    with pytest.raises(Exception) as failure:
        await agent_step.run_agent_step(
            "codex",
            "declared-profile",
            "AKIAIOSFODNN7EXAMPLE",
            working_directory="/declared",
            allowed_tools=["Write"],
            model="requested-model",
            timeout=17,
            on_step_terminal_ready=callback,
        )
    if failure_at_creation:
        assert not delivered
        assert getattr(failure.value, "terminal_id", None) == "abc12345", repr(failure.value)
        assert failure.value.kind == "contract_rejected"
        assert failure.value.delivery_may_have_occurred is False
        if failure_at_creation == "storage":
            assert isinstance(failure.value.__cause__, IntegrityError)
    else:
        assert delivered, repr(failure.value)
        captured = delivered[0]
        assert captured["fields"]["tools"]["value"] == ["Write"]
        assert captured["fields"]["working_directory"]["value"] == "/declared"
        assert captured["fields"]["timeout_seconds"]["value"] == 17
        assert captured["fields"]["model"] == {
            "status": "known",
            "value": "requested-model",
            "provenance": "step_argument",
        }
        assert captured["fields"]["engine"]["status"] == "not_applicable"
        assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(captured)


@pytest.mark.asyncio
async def test_pre_assignment_contract_uses_authorized_arguments_not_terminal_registry(
    journal, monkeypatch
):
    """A later terminal-registry mutation cannot become step launch evidence."""
    terminal_registry = {
        "provider": "mutated-provider",
        "agent_profile": "mutated-profile",
        "allowed_tools": ["Admin"],
        "engine": "mutated-engine",
        "working_directory": "/mutated/after-assignment",
    }

    async def create(*args, **kwargs):
        return SimpleNamespace(id="abc12345")

    captured = []

    def send(*args, **kwargs):
        with workflow_journal._connect() as connection:
            captured.append(
                json.loads(
                    connection.execute("SELECT contract_json FROM work_step_contracts").fetchone()[
                        0
                    ]
                )
            )
        raise RuntimeError("stop after the delivery boundary")

    monkeypatch.setattr(agent_step.terminal_service, "create_terminal", create)
    monkeypatch.setattr(
        agent_step.terminal_service, "get_terminal_metadata", lambda _: terminal_registry
    )
    monkeypatch.setattr(agent_step.terminal_service, "send_input", send)
    monkeypatch.setattr(agent_step, "wait_until_status", AsyncMock(return_value=True))
    monkeypatch.setattr(agent_step.frozen_run_memory, "frozen_memory_for", lambda *args: None)
    callback = script_runner.make_step_terminal_recorder(
        {"CAO_WORKFLOW_RUN_ID": "run", "CAO_WORKFLOW_STEP_ID": "step"}
    )

    with pytest.raises(RuntimeError, match="delivery boundary"):
        await agent_step.run_agent_step(
            "codex",
            "declared-profile",
            "task",
            working_directory="/declared/before-assignment",
            allowed_tools=["Read"],
            model="requested-model",
            timeout=17,
            on_step_terminal_ready=callback,
        )

    assert len(captured) == 1
    contract_value = captured[0]
    assert contract_value["fields"]["provider"] == {
        "status": "known",
        "value": "codex",
        "provenance": "step_argument",
    }
    assert contract_value["fields"]["profile"] == {
        "status": "known",
        "value": "declared-profile",
        "provenance": "step_argument",
    }
    assert contract_value["fields"]["tools"] == {
        "status": "known",
        "value": ["Read"],
        "provenance": "step_argument",
    }
    assert contract_value["fields"]["working_directory"] == {
        "status": "known",
        "value": "/declared/before-assignment",
        "provenance": "step_argument",
    }
    assert contract_value["fields"]["model"] == {
        "status": "known",
        "value": "requested-model",
        "provenance": "step_argument",
    }
    assert contract_value["fields"]["effort"] == {
        "status": "unknown",
        "value": None,
        "provenance": "not_recorded_at_launch",
    }
    assert contract_value["fields"]["retry_policy"] == {
        "status": "unknown",
        "value": None,
        "provenance": "caller_owned",
    }
    assert "mutated-provider" not in json.dumps(contract_value)
    assert "mutated-profile" not in json.dumps(contract_value)
    assert "/mutated/after-assignment" not in json.dumps(contract_value)


@pytest.mark.asyncio
async def test_reused_terminal_contract_keeps_creation_only_fields_not_applicable(
    journal, monkeypatch
):
    """A reused terminal cannot inherit creation policy from a new caller."""
    metadata = {
        "provider": "codex",
        "agent_profile": "old-profile",
        "allowed_tools": ["Admin"],
        "working_directory": "/old/terminal",
    }
    captured = []

    def send(*args, **kwargs):
        with workflow_journal._connect() as connection:
            captured.append(
                json.loads(
                    connection.execute("SELECT contract_json FROM work_step_contracts").fetchone()[
                        0
                    ]
                )
            )
        raise RuntimeError("stop after the delivery boundary")

    monkeypatch.setattr(agent_step.terminal_service, "get_terminal_metadata", lambda _: metadata)
    monkeypatch.setattr(agent_step.terminal_service, "send_input", send)
    monkeypatch.setattr(agent_step.frozen_run_memory, "frozen_memory_for", lambda *args: None)
    callback = script_runner.make_step_terminal_recorder(
        {"CAO_WORKFLOW_RUN_ID": "run", "CAO_WORKFLOW_STEP_ID": "step"}
    )

    with pytest.raises(RuntimeError, match="delivery boundary"):
        await agent_step.run_agent_step(
            "codex",
            "new-profile",
            "task",
            reuse_terminal_id="abc12345",
            working_directory="/new/request",
            allowed_tools=["Read"],
            model="requested-model",
            use_worktree=True,
            on_step_terminal_ready=callback,
        )

    assert len(captured) == 1
    fields = captured[0]["fields"]
    assert fields["provider"] == {
        "status": "known",
        "value": "codex",
        "provenance": "step_argument",
    }
    for name in ("profile", "tools", "working_directory", "model", "worktree"):
        assert fields[name]["status"] == "not_applicable"
        assert fields[name]["value"] is None
        assert fields[name]["provenance"] == "reuse_not_applicable"
    assert fields["effort"]["status"] == "unknown"
    assert fields["retry_policy"]["status"] == "unknown"
    assert "old-profile" not in json.dumps(captured[0])
    assert "/old/terminal" not in json.dumps(captured[0])


@pytest.mark.parametrize("damage", ["trigger", "ledger"])
def test_contract_admission_rejects_damaged_verified_work_schema(journal, damage):
    from cli_agent_orchestrator.clients.work_repository import SchemaMismatch

    if damage == "trigger":
        _drop_contract_immutability_trigger()
    else:
        _damage_work_migration_history()
    with pytest.raises(SchemaMismatch):
        workflow_journal.begin_step_with_contract("run", "step", "1", "now", contract())
    assert workflow_journal.get_step("run", "step") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cancel", "generation", "trigger", "ledger"])
async def test_change_during_readiness_wait_blocks_initial_delivery(journal, monkeypatch, change):
    from cli_agent_orchestrator.models.terminal import TerminalStatus

    monkeypatch.setattr(
        agent_step.terminal_service,
        "create_terminal",
        AsyncMock(return_value=SimpleNamespace(id="abc12345")),
    )
    monkeypatch.setattr(agent_step.terminal_service, "get_terminal_metadata", lambda _: {})
    monkeypatch.setattr(agent_step.frozen_run_memory, "frozen_memory_for", lambda *args: None)
    sent = []
    monkeypatch.setattr(
        agent_step.terminal_service, "send_input", lambda *args, **kwargs: sent.append(args)
    )
    monkeypatch.setattr(
        agent_step.status_monitor, "notify_input_sent", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        agent_step, "_wait_for_completion", AsyncMock(side_effect=RuntimeError("stop if sent"))
    )

    async def ready(*args, **kwargs):
        if change in {"cancel", "generation"}:
            with workflow_journal._connect() as connection:
                if change == "cancel":
                    connection.execute(
                        "UPDATE workflow_run SET state='cancelled' WHERE run_id='run'"
                    )
                else:
                    connection.execute("UPDATE workflow_run SET generation='2' WHERE run_id='run'")
        elif change == "trigger":
            _drop_contract_immutability_trigger()
        else:
            _damage_work_migration_history()
        return True

    monkeypatch.setattr(agent_step, "wait_until_status", ready)
    recorder = script_runner.make_step_terminal_recorder(
        {"CAO_WORKFLOW_RUN_ID": "run", "CAO_WORKFLOW_STEP_ID": "step"}
    )
    with pytest.raises(agent_step.StepExecutionError) as error:
        await agent_step.run_agent_step(
            "codex", "developer", "task", on_step_terminal_ready=recorder
        )
    assert error.value.kind == "contract_rejected"
    assert error.value.delivery_may_have_occurred is False
    assert sent == []


def test_delivery_guard_rejects_replaced_attempt_even_with_same_terminal(journal):
    recorder = script_runner.make_step_terminal_recorder(
        {"CAO_WORKFLOW_RUN_ID": "run", "CAO_WORKFLOW_STEP_ID": "step"}
    )
    recorder.record_contract("abc12345", "v2:identity", contract())
    workflow_journal.settle_step(
        "run", "step", "failed", "later", None, None, "failure", error_kind="error"
    )
    workflow_journal.begin_step_with_contract("run", "step", "1", "again", contract())
    with pytest.raises(ValueError):
        recorder.guard_delivery()


@pytest.mark.asyncio
async def test_redelivery_guard_is_not_swallowed_after_cancel(journal, monkeypatch):
    from cli_agent_orchestrator.models.terminal import TerminalStatus

    recorder = script_runner.make_step_terminal_recorder(
        {"CAO_WORKFLOW_RUN_ID": "run", "CAO_WORKFLOW_STEP_ID": "step"}
    )
    recorder.record_contract("abc12345", "v2:identity", contract())
    with workflow_journal._connect() as connection:
        connection.execute("UPDATE workflow_run SET state='cancelled' WHERE run_id='run'")
    redelivered = []
    monkeypatch.setattr(agent_step, "_PROMPT_PICKUP_GRACE", 0)
    monkeypatch.setattr(agent_step.status_monitor, "get_status", lambda _: TerminalStatus.IDLE)
    monkeypatch.setattr(
        agent_step.terminal_service,
        "redeliver_dropped_message",
        lambda *args, **kwargs: redelivered.append(args),
    )
    monkeypatch.setattr(
        agent_step.terminal_service, "probe_post_turn_receipt_result", lambda _: None
    )
    with pytest.raises(agent_step.StepExecutionError) as error:
        await agent_step._wait_for_completion(
            "abc12345", 0.5, prompt="task", delivery_guard=recorder.guard_delivery
        )
    assert error.value.kind == "contract_rejected"
    assert error.value.delivery_may_have_occurred is True
    assert redelivered == []


@pytest.mark.parametrize("seed_memory", [False, True])
def test_work_claim_between_legacy_preflight_and_settle_is_preserved(
    journal, monkeypatch, seed_memory
):
    from cli_agent_orchestrator.models.workflow import StepState

    workflow_journal.begin_step("run", "step", "now", "v2:identity")
    record = script_runner.run_registry["run"]
    if seed_memory:
        record.step_states["step"] = workflow_service.StepRunState(
            step_id="step", state=StepState.RUNNING, terminal_id="abc12345"
        )
    before = dict(record.step_states)
    callback = script_runner.record_step_completion(
        {"CAO_WORKFLOW_RUN_ID": "run", "CAO_WORKFLOW_STEP_ID": "step"}
    )
    real_get = workflow_journal.get_step

    def claim_after_read(run_id, step_id):
        observed = real_get(run_id, step_id)
        with workflow_journal._connect() as connection:
            connection.execute(
                "UPDATE workflow_run_step SET state='work_pending' WHERE run_id=%s AND step_id=%s",
                (run_id, step_id),
            )
        return observed

    monkeypatch.setattr(workflow_journal, "get_step", claim_after_read)
    callback("abc12345", None, "late legacy output")
    retained = real_get("run", "step")
    assert retained.state == "work_pending"
    assert retained.result_json is None
    assert retained.attempts == 0
    assert record.step_states == before
    if seed_memory:
        assert record.step_states["step"].state == StepState.RUNNING
        assert record.step_states["step"].attempts == 0
