"""Fail closed when a native PostgreSQL cursor unexpectedly has no row."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget
from cli_agent_orchestrator.clients.postgres.migrations import (
    ComponentMigration,
    SchemaMismatch,
    migrate_component,
)
from cli_agent_orchestrator.services import workflow_journal

pytestmark = pytest.mark.postgres


class _MissingRowCursor:
    """Delegate to an executed native cursor but report a missing fetched row."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def fetchone(self) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


def _make_fetchone_missing(monkeypatch, sql_fragment: str) -> tuple[list[str], list[str]]:
    """Run the real SQL, then fault only its cursor's fetchone result."""
    matches: list[str] = []
    writes: list[str] = []
    original_execute = Connection.execute

    def execute(
        self: Connection,
        statement: Any,
        params: Any = (),
        *,
        prepare: bool | None = None,
    ) -> Any:
        normalized = " ".join(str(statement).split())
        if normalized.lstrip().split(maxsplit=1)[0].casefold() in {
            "insert",
            "update",
            "delete",
            "truncate",
        }:
            writes.append(normalized)
        if prepare is None:
            cursor = original_execute(self, statement, params)
        else:
            cursor = original_execute(self, statement, params, prepare=prepare)
        if sql_fragment.casefold() in normalized.casefold():
            matches.append(normalized)
            return _MissingRowCursor(cursor)
        return cursor

    monkeypatch.setattr(Connection, "execute", execute)
    return matches, writes


def _initialize_run(target, run_id: str, tier: str) -> None:
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    bootstrap_scope(target)
    WorkRepository(target).initialize()
    workflow_journal.insert_run(
        run_id, "cursor-none-guard", "{}", "{}", "running", "now", tier=tier
    )


def _script_contract() -> dict[str, Any]:
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
    fields["timeout_seconds"] = {
        "status": "known",
        "value": 12.0,
        "provenance": "step_argument",
    }
    return {
        "schema_version": 1,
        "terminal_id": "cursor-none-terminal",
        "call_fingerprint": "v2:cursor-none-guard",
        "fields": fields,
    }


def _yaml_fields() -> dict[str, dict[str, Any]]:
    return _script_contract()["fields"]


def test_script_attempt_aggregate_missing_row_stops_before_inserts(postgres_targets, monkeypatch):
    target = postgres_targets.main
    _initialize_run(target, "script-aggregate", "script")
    matches, writes = _make_fetchone_missing(
        monkeypatch, "SELECT COALESCE(MAX(attempt_number),0)+1 FROM work_step_contracts"
    )

    with pytest.raises(ValueError, match="step contract attempt number aggregate returned no row"):
        workflow_journal.begin_step_with_contract(
            "script-aggregate", "step", "1", "later", _script_contract()
        )

    assert len(matches) == 1
    assert writes == []
    assert workflow_journal.get_step("script-aggregate", "step") is None
    assert workflow_journal.get_step_contract("script-aggregate", "step", "1", 1) is None


def test_yaml_attempt_aggregate_missing_row_stops_before_inserts(postgres_targets, monkeypatch):
    target = postgres_targets.main
    _initialize_run(target, "yaml-aggregate", "yaml")
    matches, writes = _make_fetchone_missing(
        monkeypatch, "SELECT COALESCE(MAX(attempt_number),0) FROM work_step_contracts"
    )

    with pytest.raises(ValueError, match="YAML step contract attempt aggregate returned no row"):
        workflow_journal.begin_yaml_step_with_contract(
            "yaml-aggregate", "step", "1", "now", "v2:stable", _yaml_fields()
        )

    assert len(matches) == 1
    assert writes == []
    assert workflow_journal.get_step("yaml-aggregate", "step") is None
    assert workflow_journal.get_step_contract("yaml-aggregate", "step", "1", 1) is None


def test_yaml_result_aggregate_missing_row_stops_before_update(postgres_targets, monkeypatch):
    target = postgres_targets.main
    _initialize_run(target, "yaml-result-aggregate", "yaml")
    attempt = workflow_journal.begin_yaml_step_with_contract(
        "yaml-result-aggregate", "step", "1", "now", "v2:stable", _yaml_fields()
    )
    matches, writes = _make_fetchone_missing(
        monkeypatch, "SELECT MAX(attempt_number) FROM work_step_contracts"
    )

    with pytest.raises(ValueError, match="YAML result attempt is no longer current"):
        workflow_journal.record_yaml_step_result(
            "yaml-result-aggregate",
            "step",
            "1",
            attempt,
            "v2:stable",
            SimpleNamespace(terminal_id="terminal"),
            result_json="{}",
        )

    assert len(matches) == 1
    assert writes == []
    assert workflow_journal.get_step("yaml-result-aggregate", "step").result_json is None


def test_yaml_failure_aggregate_missing_row_stops_before_update(postgres_targets, monkeypatch):
    target = postgres_targets.main
    _initialize_run(target, "yaml-failure-aggregate", "yaml")
    attempt = workflow_journal.begin_yaml_step_with_contract(
        "yaml-failure-aggregate", "step", "1", "now", "v2:stable", _yaml_fields()
    )
    matches, writes = _make_fetchone_missing(
        monkeypatch, "SELECT MAX(attempt_number) FROM work_step_contracts"
    )

    with pytest.raises(ValueError, match="YAML attempt is no longer current"):
        workflow_journal.fail_yaml_step_attempt(
            "yaml-failure-aggregate",
            "step",
            "1",
            attempt,
            "v2:stable",
            "later",
            "failure",
            "error",
        )

    assert len(matches) == 1
    assert writes == []
    assert workflow_journal.get_step("yaml-failure-aggregate", "step").state == "running"


def test_yaml_sendable_aggregate_missing_row_fails_closed(postgres_targets, monkeypatch):
    target = postgres_targets.main
    _initialize_run(target, "yaml-sendable-aggregate", "yaml")
    attempt = workflow_journal.begin_yaml_step_with_contract(
        "yaml-sendable-aggregate", "step", "1", "now", "v2:stable", _yaml_fields()
    )
    matches, writes = _make_fetchone_missing(
        monkeypatch, "SELECT MAX(attempt_number) FROM work_step_contracts"
    )

    with pytest.raises(
        ValueError, match="YAML contract is no longer the current deliverable attempt"
    ):
        workflow_journal.assert_yaml_step_contract_current(
            "yaml-sendable-aggregate", "step", "1", attempt, "v2:stable", _yaml_fields()
        )

    assert len(matches) == 1
    assert writes == []
    assert workflow_journal.get_step("yaml-sendable-aggregate", "step").state == "running"
    assert (
        workflow_journal.get_step_contract("yaml-sendable-aggregate", "step", "1", attempt)
        is not None
    )


def test_script_sendable_aggregate_missing_row_fails_closed(postgres_targets, monkeypatch):
    target = postgres_targets.main
    _initialize_run(target, "script-sendable-aggregate", "script")
    contract = _script_contract()
    attempt = workflow_journal.begin_step_with_contract(
        "script-sendable-aggregate", "step", "1", "now", contract
    )
    matches, writes = _make_fetchone_missing(
        monkeypatch, "SELECT max(attempt_number) FROM work_step_contracts"
    )

    with pytest.raises(
        ValueError, match="step contract is no longer the current deliverable attempt"
    ):
        workflow_journal.assert_step_contract_current(
            "script-sendable-aggregate",
            "step",
            "1",
            attempt,
            contract["terminal_id"],
            contract["call_fingerprint"],
        )

    assert len(matches) == 1
    assert writes == []
    assert workflow_journal.get_step("script-sendable-aggregate", "step").state == "running"


def test_registry_aggregate_missing_row_stops_before_bootstrap_progress(
    postgres_targets, monkeypatch
):
    from cli_agent_orchestrator.clients.postgres import registry

    target = postgres_targets.main
    migrate_component(
        target,
        (ComponentMigration("cursor_none_seed", "main", 1, (), ()),),
    )
    prefix = (ComponentMigration("cursor_none_target", "main", 1, (), ()),)
    complete = prefix + (ComponentMigration("cursor_none_target", "main", 2, (), ()),)
    monkeypatch.setattr(registry, "component_migrations", lambda _scope: (prefix, complete))
    applied: list[tuple[ComponentMigration, ...]] = []
    monkeypatch.setattr(
        registry, "migrate_component", lambda _target, migrations: applied.append(migrations)
    )
    matches, writes = _make_fetchone_missing(
        monkeypatch, "SELECT max(version) FROM _cao_schema_migrations"
    )

    with pytest.raises(SchemaMismatch, match="did not report installed migration version"):
        registry.bootstrap_scope(target, seed_work=False)

    assert len(matches) == 1
    assert writes == []
    assert applied == []
