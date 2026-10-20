"""Journal operations finish transactions and close only connections they own."""

from __future__ import annotations

from typing import Any, Callable, List

import pytest

from cli_agent_orchestrator.clients.postgres import Connection, DatabaseError
from cli_agent_orchestrator.services import workflow_journal as journal

_PROBE_RUN = "workflow-connection-ownership-probe"


@pytest.fixture
def journal_database():
    """Seed an event owner in the isolated PostgreSQL schema supplied by test/conftest.py."""
    journal.insert_run("owned-run", "workflow", "{}", "{}", "running", "now")
    journal.append_event("owned-run", 1, "started", event_schema_version=1, ts="now")
    yield


def _insert_run():
    return journal.insert_run("owned-insert-run", "workflow", "{}", "{}", "running", "now")


def _append_event():
    return journal.append_event("owned-run", 2, "step_started", event_schema_version=1, ts="now")


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
    journal_database,
    monkeypatch: pytest.MonkeyPatch,
    operation: Callable[[], Any],
    fail_query: bool,
):
    """An owned connection commits or rolls back, then closes on both paths."""
    real_connect = journal._connect
    connections: List["TrackedConnection"] = []

    class TrackedConnection:
        def __init__(self, real: Connection):
            self.real = real
            self.events: List[str] = []

        def __enter__(self):
            self.real.__enter__()
            # This marker is in the same PostgreSQL transaction as the production operation.
            # The division-by-zero below proves the server rolls it back on the failure path.
            self.real.execute(
                "INSERT INTO workflow_run "
                "(run_id, workflow_name, spec_snapshot, inputs_json, state, started_at, "
                "tier, generation) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (_PROBE_RUN, "workflow", "{}", "{}", "running", "now", "yaml", "1"),
            )
            return self

        def execute(self, statement, params=()):
            if fail_query:
                # Let PostgreSQL raise a real error in this owned transaction.
                return self.real.execute("SELECT 1 / 0")
            return self.real.execute(statement, params)

        def executemany(self, statement, params):
            return self.real.executemany(statement, params)

        def __exit__(self, exc_type, exc, traceback):
            self.events.append("rollback" if exc_type else "commit")
            return self.real.__exit__(exc_type, exc, traceback)

        def close(self):
            self.events.append("close")
            self.real.close()

        def __getattr__(self, name):
            return getattr(self.real, name)

    def tracked_connect(original):
        def factory(*args, **kwargs):
            tracked = TrackedConnection(original(*args, **kwargs))
            connections.append(tracked)
            return tracked

        return factory

    # _connect_event delegates to the module-global _connect at call time, so
    # wrapping both factories would nest trackers and execute the marker twice.
    # Tracking _connect alone observes both ordinary and event connections.
    monkeypatch.setattr(journal, "_connect", tracked_connect(real_connect))

    if fail_query:
        with pytest.raises(DatabaseError):
            operation()
    else:
        operation()

    assert len(connections) == 1
    owned = connections[0]
    assert owned.events == ["rollback" if fail_query else "commit", "close"]
    assert owned.real.raw.closed

    # Restore the normal factory before reading the marker. The owned marker committed only
    # after success and was rolled back by PostgreSQL when the operation's query failed.
    monkeypatch.setattr(journal, "_connect", real_connect)
    assert (journal.get_run(_PROBE_RUN) is None) is fail_query


def test_borrowed_connection_stays_open_and_uncommitted(journal_database):
    """A caller-owned connection retains its transaction and controls rollback."""
    borrowed = journal._connect()
    try:
        journal.insert_run(
            "borrowed-run",
            "workflow",
            "{}",
            "{}",
            "running",
            "now",
            connection=borrowed,
        )
        journal.delete_run_events("owned-run", conn=borrowed)

        assert borrowed.in_transaction
        assert (
            borrowed.execute(
                "SELECT run_id FROM workflow_run WHERE run_id=%s", ("borrowed-run",)
            ).fetchall()[0][0]
            == "borrowed-run"
        )
        assert (
            borrowed.execute(
                "SELECT count(*) FROM workflow_run_event WHERE run_id=%s", ("owned-run",)
            ).fetchone()[0]
            == 0
        )

        borrowed.rollback()

        assert (
            borrowed.execute(
                "SELECT run_id FROM workflow_run WHERE run_id=%s", ("borrowed-run",)
            ).fetchone()
            is None
        )
        assert (
            borrowed.execute(
                "SELECT count(*) FROM workflow_run_event WHERE run_id=%s", ("owned-run",)
            ).fetchone()[0]
            == 1
        )
        assert not borrowed.raw.closed
    finally:
        borrowed.close()
