"""The recovery cursor table is an additive, verified work-store migration."""

import sqlite3
from test.clients.test_work_migrations import repository_module
from test.fixtures.work_store import work_store_paths  # noqa: F401

import pytest


def test_recovery_cursor_migration_preserves_v16_history_and_scope_immutability(work_store_paths):
    """Would fail if installing operational cursors rewrote durable knowledge heads."""
    module = repository_module()
    path = work_store_paths.database
    store = module.WorkRepository(path)
    with sqlite3.connect(path) as connection:
        for version in range(1, 17):
            for statement in module._MIGRATIONS[version]:
                connection.execute(statement)
            if version == 16:
                store._initialize_inbox_store_context(connection)
            connection.execute(
                "INSERT INTO work_migrations VALUES (?,?,?,?)",
                (version, module._CHECKSUMS[version], float(version), "verified"),
            )
        connection.execute(
            "INSERT INTO work_knowledge_records VALUES ('historic','project','project',1,1)"
        )
        connection.execute(
            "INSERT INTO work_principals VALUES ('owner','issuer','subject','jwt',1)"
        )
        connection.execute(
            "INSERT INTO work_knowledge_revisions VALUES "
            "('historic',1,'owner',NULL,NULL,NULL,?,?,'[]',0.8,300,'historic',0,0,0,NULL,1)",
            ("a" * 64, "b" * 64),
        )
        before = {
            "records": connection.execute("SELECT * FROM work_knowledge_records").fetchall(),
            "revisions": connection.execute("SELECT * FROM work_knowledge_revisions").fetchall(),
        }
    store.initialize()
    store.verify_schema()
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT * FROM work_knowledge_records").fetchall()
            == before["records"]
        )
        assert (
            connection.execute("SELECT * FROM work_knowledge_revisions").fetchall()
            == before["revisions"]
        )
        assert connection.execute(
            "SELECT version FROM work_migrations WHERE version=17"
        ).fetchone() == (17,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='work_knowledge_cursors'"
        ).fetchone() == ("work_knowledge_cursors",)
        with pytest.raises(sqlite3.IntegrityError, match="scope is immutable"):
            connection.execute(
                "UPDATE work_knowledge_records SET scope_id='other' WHERE id='historic'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="history is immutable"):
            connection.execute("UPDATE work_knowledge_revisions SET content='changed'")
