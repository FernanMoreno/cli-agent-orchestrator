"""The workflow index migrator owns its connection and transaction lifetime."""

import sqlite3
from contextlib import closing

import pytest

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import database


@pytest.mark.parametrize("fail_query", [False, True])
def test_workflow_index_migration_closes_after_transaction(tmp_path, monkeypatch, fail_query):
    path = tmp_path / "index.db"
    connect = sqlite3.connect
    with closing(connect(path)) as connection, connection:
        connection.execute("CREATE TABLE transaction_probe (value TEXT NOT NULL)")

    events = []
    connections = []

    class TrackedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if not events:
                # A real pending write proves that exit still commits or rolls back
                # before the connection closes, including the handled error path.
                super().execute("INSERT INTO transaction_probe VALUES ('pending')")
                events.append("write")
                if fail_query:
                    raise sqlite3.OperationalError("injected index migration failure")
            return super().execute(sql, parameters)

        def __exit__(self, exception_type, exception, traceback):
            result = super().__exit__(exception_type, exception, traceback)
            events.append("rollback" if exception_type else "commit")
            return result

        def close(self):
            events.append("close")
            super().close()

    def tracked_connect(*args, **kwargs):
        kwargs["factory"] = TrackedConnection
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(constants, "DATABASE_FILE", path)
    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    database._migrate_workflow_index()

    with closing(connect(path)) as verification:
        rows = verification.execute("SELECT value FROM transaction_probe").fetchall()
        index_exists = verification.execute(
            "SELECT name FROM sqlite_master WHERE name = 'workflow_index'"
        ).fetchone()
    assert rows == ([] if fail_query else [("pending",)])
    assert bool(index_exists) is not fail_query
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    assert events == ["write", "rollback" if fail_query else "commit", "close"]
