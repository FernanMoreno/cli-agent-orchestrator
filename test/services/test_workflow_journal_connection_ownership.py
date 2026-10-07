"""Journal operations finish transactions and close only their own connections."""

import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import create_engine

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.services import workflow_journal as journal


@pytest.fixture
def journal_database(tmp_path, monkeypatch):
    path = tmp_path / "journal.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(constants, "DATABASE_FILE", path)
    monkeypatch.setattr(constants, "DB_DIR", path.parent)
    monkeypatch.setattr(database, "DB_DIR", path.parent)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(journal, "_MIGRATED_PATHS", set())
    monkeypatch.setattr(journal, "_event_migrated_paths", set())
    database.init_db()
    with closing(journal._connect()), closing(journal._connect_event()):
        pass
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE transaction_probe (value TEXT NOT NULL)")
    try:
        yield path
    finally:
        engine.dispose()


def _insert_run(**kwargs):
    return journal.insert_run("owned-run", "workflow", "{}", "{}", "running", "now", **kwargs)


def _append_event():
    return journal.append_event("owned-run", 1, "started", event_schema_version=1, ts="now")


@pytest.mark.parametrize(
    "operation",
    (
        lambda: journal.get_run("missing"),
        lambda: journal.read_events("missing"),
        _insert_run,
        _append_event,
    ),
)
@pytest.mark.parametrize("fail_query", (False, True))
def test_owned_operation_closes_after_transaction(
    journal_database, monkeypatch, operation, fail_query
):
    connect = sqlite3.connect
    connections = []

    class TrackedConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.events = []
            self.active = False

        def __enter__(self):
            super().__enter__()
            self.active = True
            super().execute("INSERT INTO transaction_probe VALUES ('pending')")
            return self

        def execute(self, sql, parameters=()):
            if self.active and fail_query:
                raise sqlite3.OperationalError("injected journal query failure")
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
    if fail_query:
        with pytest.raises(sqlite3.OperationalError, match="injected journal query failure"):
            operation()
    else:
        operation()
    assert len(connections) == 1
    owned = connections[0]
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        owned.execute("SELECT 1")
    assert owned.events == ["rollback" if fail_query else "commit", "close"]
    with closing(connect(journal_database)) as verification:
        rows = verification.execute("SELECT value FROM transaction_probe").fetchall()
    assert rows == ([] if fail_query else [("pending",)])


def test_borrowed_run_and_event_connection_stays_open_and_uncommitted(journal_database):
    with closing(sqlite3.connect(journal_database)) as borrowed:
        borrowed.execute("INSERT INTO transaction_probe VALUES ('borrowed')")
        _insert_run(connection=borrowed)
        journal.delete_run_events("owned-run", conn=borrowed)
        assert borrowed.in_transaction
        assert borrowed.execute("SELECT run_id FROM workflow_run").fetchall() == [("owned-run",)]
        borrowed.rollback()
        assert borrowed.execute("SELECT run_id FROM workflow_run").fetchall() == []
        assert borrowed.execute("SELECT value FROM transaction_probe").fetchall() == []
