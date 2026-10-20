"""Runtime startup closes native owned handles and leaves borrowed transactions live."""

from test.postgres.fixtures import make_orm_engine

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.postgres import Connection, DatabaseError, connect
from cli_agent_orchestrator.clients.postgres.migrations import verify_component

pytestmark = pytest.mark.postgres


def test_startup_closes_its_native_connection(postgres_core, monkeypatch):
    allocated = []
    original = Connection.__init__

    def track(self, *args, **kwargs):
        original(self, *args, **kwargs)
        allocated.append(self)

    monkeypatch.setattr(Connection, "__init__", track)
    engine = make_orm_engine(postgres_core)
    monkeypatch.setattr(database, "engine", engine)
    try:
        database.init_db()
        assert allocated and all(connection.raw.closed for connection in allocated)
    finally:
        engine.dispose()


def test_component_verification_preserves_borrowed_transaction(postgres_core):
    from psycopg import Rollback

    with connect(postgres_core) as borrowed:
        with borrowed.transaction():
            borrowed.execute(
                "INSERT INTO flows (name,file_path,schedule,agent_profile,provider,enabled) VALUES (%s,%s,%s,%s,%s,%s)",
                ("pending", "/pending", "* * * * *", "developer", "mock_cli", False),
            )
            verify_component(borrowed, database.CORE_MIGRATIONS)
            assert borrowed.in_transaction
            assert (
                borrowed.execute("SELECT count(*) FROM flows WHERE name='pending'").fetchone()[0]
                == 1
            )
            raise Rollback()
        assert not borrowed.raw.closed
        assert (
            borrowed.execute("SELECT count(*) FROM flows WHERE name='pending'").fetchone()[0] == 0
        )
