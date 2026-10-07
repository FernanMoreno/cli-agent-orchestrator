"""Store operations close owned SQLite handles and preserve their failure policies."""

import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import create_engine

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services import (
    approval_store,
    recovery_bundle,
    workflow_journal,
    workflow_service,
    workflow_spec_service,
)
from cli_agent_orchestrator.services.work_workflow_plans import WorkWorkflowPlans


@pytest.fixture
def stores(tmp_path, monkeypatch):
    path = tmp_path / "stores.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(constants, "DATABASE_FILE", path)
    monkeypatch.setattr(constants, "DB_DIR", path.parent)
    monkeypatch.setattr(database, "DB_DIR", path.parent)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(approval_store, "DATABASE_FILE", path)
    database.init_db()
    workflow_journal.insert_run("generation-run", "workflow", "{}", "{}", "running", "now")
    scan = tmp_path / "specs"
    scan.mkdir()
    source = tmp_path / "source.db"
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute("CREATE TABLE probe (value TEXT)")
    stage = tmp_path / "stage"
    stage.mkdir()
    repository = WorkRepository(path)
    repository.initialize()
    operations = {
        "grant": lambda: approval_store.grant("approval", "local"),
        "approval_state": lambda: approval_store.approval_state("missing"),
        "get_approval": lambda: approval_store.get_approval("missing"),
        "list_workflows": lambda: workflow_spec_service.list_workflows(str(scan)),
        "compact": lambda: recovery_bundle._compact_staging_database(source, stage),
        "generation": lambda: workflow_service.update_run_generation("generation-run", "2"),
        "plans": lambda: WorkWorkflowPlans(repository).initialize(),
    }
    try:
        yield operations
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "operation_name,fail_query",
    [
        (name, fail)
        for name in (
            "grant",
            "approval_state",
            "get_approval",
            "list_workflows",
            "compact",
            "generation",
            "plans",
        )
        for fail in (False, True)
        if name != "plans" or not fail
    ],
)
def test_store_connections_close_on_success_and_error(
    stores, monkeypatch, operation_name, fail_query
):
    connect = sqlite3.connect
    connections = []

    class TrackedConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.active = False
            self.events = []

        def __enter__(self):
            self.active = True
            return super().__enter__()

        def execute(self, sql, parameters=()):
            if self.active and fail_query:
                raise sqlite3.OperationalError("injected store query failure")
            return super().execute(sql, parameters)

        def __exit__(self, exception_type, exception, traceback):
            result = super().__exit__(exception_type, exception, traceback)
            self.active = False
            self.events.append("rollback" if exception_type else "commit")
            return result

        def close(self):
            self.events.append("close")
            super().close()

    def tracked_connect(*args, **kwargs):
        kwargs["factory"] = TrackedConnection
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    operation = stores[operation_name]
    if fail_query and operation_name not in {"approval_state", "get_approval"}:
        with pytest.raises(sqlite3.OperationalError, match="injected store query failure"):
            operation()
    else:
        result = operation()
        if operation_name == "approval_state":
            assert result == (approval_store.UNKNOWN if fail_query else approval_store.ABSENT)
        if operation_name == "get_approval":
            assert result is None
        if operation_name == "list_workflows":
            assert result == []
    assert connections
    for owned in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            owned.execute("SELECT 1")
        assert owned.events[-1] == "close"
    if operation_name == "generation" and not fail_query:
        assert workflow_journal.get_run("generation-run").generation == "2"
