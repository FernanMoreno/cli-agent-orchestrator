"""Startup closes its own SQLite handles while preserving borrowed authority."""

import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import create_engine

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import beads_work_schema, database, work_repository

# Owners identified by allocation traces from two real Python 3.13 startups.
_MIGRATORS = (
    "_migrate_add_handoff_results",
    "_migrate_project_aliases_schema",
    "_migrate_memory_indexes",
    "_migrate_memory_source_kind",
    "_migrate_add_access_count",
    "_migrate_add_last_compiled_at",
    "_migrate_add_related_keys",
    "_migrate_memory_relationships",
    "_migrate_workflow_plan_approval",
    "_migrate_memory_scope_null_uniqueness",
    "_migrate_vault_exclusions",
    "_migrate_vault_migration_receipts",
    "_migrate_vault_key_provenance",
    "_migrate_workflow_run",
    "_migrate_workflow_run_step",
    "_migrate_workflow_outcome_indexes",
    "_migrate_workflow_run_event",
    "_migrate_workflow_run_seq",
    "_migrate_workflow_run_indexes",
    "_migrate_workflow_plan_snapshot",
)


@pytest.fixture
def startup_database(tmp_path, monkeypatch):
    path = tmp_path / "startup.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(constants, "DATABASE_FILE", path)
    monkeypatch.setattr(constants, "DB_DIR", path.parent)
    monkeypatch.setattr(database, "DB_DIR", path.parent)
    monkeypatch.setattr(database, "engine", engine)
    database.init_db()
    try:
        yield path
    finally:
        engine.dispose()


def _track_owned_connections(monkeypatch, *, fail_query=False):
    connect = sqlite3.connect
    connections = []

    class TrackedConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.events = []

        def execute(self, sql, parameters=()):
            if fail_query and not self.events:
                self.events.append("write")
                super().execute("INSERT INTO transaction_probe VALUES ('pending')")
                raise sqlite3.OperationalError("injected migration query failure")
            return super().execute(sql, parameters)

        def __exit__(self, exception_type, exception, traceback):
            result = super().__exit__(exception_type, exception, traceback)
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
    return connect, connections


@pytest.mark.parametrize("name", _MIGRATORS)
@pytest.mark.parametrize("fail_query", (False, True))
def test_migration_closes_after_transaction(startup_database, monkeypatch, name, fail_query):
    with closing(sqlite3.connect(startup_database)) as connection, connection:
        connection.execute("CREATE TABLE transaction_probe (value TEXT NOT NULL)")
        if not fail_query:
            # This connection is distinct from the pending write below.
            connection.execute("INSERT INTO transaction_probe VALUES ('baseline')")
    connect, connections = _track_owned_connections(monkeypatch, fail_query=fail_query)
    if not fail_query:
        # Queue a real transaction on the owned handle before its first query.
        tracked_connect = sqlite3.connect

        def with_pending_write(*args, **kwargs):
            connection = tracked_connect(*args, **kwargs)
            sqlite3.Connection.execute(
                connection, "INSERT INTO transaction_probe VALUES ('pending')"
            )
            connection.events.append("write")
            return connection

        monkeypatch.setattr(sqlite3, "connect", with_pending_write)
    fail_hard = {
        "_migrate_memory_source_kind",
        "_migrate_vault_exclusions",
        "_migrate_vault_migration_receipts",
        "_migrate_vault_key_provenance",
    }
    if fail_query and name in fail_hard:
        with pytest.raises(sqlite3.OperationalError, match="injected migration query failure"):
            getattr(database, name)()
    else:
        getattr(database, name)()
    with closing(connect(startup_database)) as verification:
        rows = verification.execute("SELECT value FROM transaction_probe ORDER BY rowid").fetchall()
    assert rows == ([] if fail_query else [("baseline",), ("pending",)])
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    assert connections[0].events == ["write", "rollback" if fail_query else "commit", "close"]


def test_expected_schema_closes_and_preserves_catalog(monkeypatch):
    _, connections = _track_owned_connections(monkeypatch)
    expected = work_repository._expected_schemas()
    assert expected == work_repository._EXPECTED_SCHEMAS
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    assert connections[0].events == ["commit", "close"]


@pytest.mark.parametrize("invalid", (False, True))
def test_beads_comparison_closes_only_its_owned_connection(monkeypatch, invalid):
    connect = sqlite3.connect
    with closing(connect(":memory:")) as borrowed:
        borrowed.executescript(beads_work_schema.DDL)
        if invalid:
            borrowed.execute("DROP INDEX idx_beads_one_active_assignment")
        borrowed.execute("CREATE TABLE borrowed_probe (value TEXT)")
        borrowed.execute("INSERT INTO borrowed_probe VALUES ('pending')")
        _, connections = _track_owned_connections(monkeypatch)
        if invalid:
            with pytest.raises(ValueError, match="beads_binding_schema_integrity"):
                beads_work_schema.verify(borrowed)
        else:
            beads_work_schema.verify(borrowed)
        assert borrowed.in_transaction
        assert borrowed.execute("SELECT value FROM borrowed_probe").fetchall() == [("pending",)]
        borrowed.rollback()
        assert borrowed.execute("SELECT value FROM borrowed_probe").fetchall() == []
        assert len(connections) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connections[0].execute("SELECT 1")
        assert connections[0].events == ["rollback" if invalid else "commit", "close"]
