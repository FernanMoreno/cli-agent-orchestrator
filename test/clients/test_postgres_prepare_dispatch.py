"""Focused contracts for explicit PostgreSQL statement preparation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import tuple_row

from cli_agent_orchestrator.clients.postgres import migrations
from cli_agent_orchestrator.clients.postgres.connection import (
    Connection,
    Record,
    record_factory,
)
from cli_agent_orchestrator.services.legacy_memory_audit_repository import (
    PooledAuditLeaseExpired,
    _GuardedCursor,
    _LeaseState,
    _PooledAuditConnection,
)

pytestmark = pytest.mark.offline_database


class _FakeCursor:
    description = (SimpleNamespace(name="id"), SimpleNamespace(name="label"))

    def __init__(self, row_factory):
        self._row_factory = row_factory(self) if row_factory is not None else None
        self.calls = []
        self.closed = False

    def execute(self, statement, params=None, **kwargs):
        self.calls.append((statement, params, kwargs))
        return self

    def fetchone(self):
        values = (7, "borrowed")
        return self._row_factory(values) if self._row_factory is not None else values


class _FakeRaw:
    def __init__(self, *, owner_row_factory=tuple_row):
        self.row_factory = owner_row_factory
        self.info = SimpleNamespace(
            dbname="test_db",
            user="test_user",
            host="localhost",
            port=5432,
            transaction_status=TransactionStatus.IDLE,
        )
        self.cursor_row_factories = []
        self.cursors = []

    def cursor(self, *, row_factory):
        self.cursor_row_factories.append(row_factory)
        cursor = _FakeCursor(row_factory)
        self.cursors.append(cursor)
        return cursor


class _RecordingLeaseState(_LeaseState):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def call(self, callback):
        self.calls += 1
        return super().call(callback)


def _target():
    return SimpleNamespace(database="test_db", user="test_user", host="localhost", port=5432)


def test_native_default_execute_keeps_the_original_cursor_call():
    raw = _FakeRaw()
    connection = Connection.__new__(Connection)
    connection.raw = raw

    cursor = connection.execute("SELECT 1")

    assert raw.cursor_row_factories == [record_factory]
    assert raw.cursors[0].calls == [("SELECT 1", None, {})]
    assert cursor is raw.cursors[0]
    assert raw.row_factory is tuple_row


@pytest.mark.parametrize("prepare", (True, False))
def test_native_execute_forwards_explicit_prepare_without_changing_owner_rows(prepare):
    raw = _FakeRaw(owner_row_factory=tuple_row)
    connection = Connection.borrow(raw, _target())

    row = connection.execute("SELECT %s, %s", (7, "borrowed"), prepare=prepare).fetchone()

    assert isinstance(row, Record)
    assert row["id"] == 7
    assert row["label"] == "borrowed"
    assert raw.cursor_row_factories == [record_factory]
    assert raw.cursors[0].calls == [("SELECT %s, %s", (7, "borrowed"), {"prepare": prepare})]
    assert raw.row_factory is tuple_row


def test_pooled_execute_forwards_prepare_under_one_lease_guard_and_expires():
    raw = _FakeRaw()
    state = _RecordingLeaseState()
    connection = _PooledAuditConnection(raw, _target(), state)

    cursor = connection.execute("SELECT 1", prepare=True)

    assert state.calls == 1
    assert isinstance(cursor, _GuardedCursor)
    assert raw.cursor_row_factories == [record_factory]
    assert raw.cursors[0].calls == [("SELECT 1", None, {"prepare": True})]
    assert cursor.fetchone()["id"] == 7

    state.expire()
    with pytest.raises(PooledAuditLeaseExpired, match="expired"):
        connection.execute("SELECT 1", prepare=True)
    with pytest.raises(PooledAuditLeaseExpired, match="expired"):
        cursor.fetchone()


def test_pooled_default_execute_keeps_the_original_cursor_call():
    raw = _FakeRaw()
    state = _RecordingLeaseState()
    connection = _PooledAuditConnection(raw, _target(), state)

    cursor = connection.execute("SELECT 1")

    assert state.calls == 1
    assert isinstance(cursor, _GuardedCursor)
    assert raw.cursor_row_factories == [record_factory]
    assert raw.cursors[0].calls == [("SELECT 1", None, {})]


def test_literal_catalog_sql_escapes_names_and_caches_only_bounded_sql():
    cached = getattr(migrations, "_cached_catalog_manifest_literal_statement", None)
    statement_for = getattr(migrations, "_catalog_manifest_literal_statement", None)
    assert cached is not None
    assert statement_for is not None
    cached.cache_clear()

    schema = "catalog_'東京\\scope"
    tables = ("table_'雪", "back\\slash")
    statement = statement_for(schema, tables)
    rendered = statement.as_string(None)

    assert type(statement) is sql.SQL
    assert f"{sql.Literal(schema).as_string(None)}::text" in rendered
    assert "ARRAY[" + ", ".join(sql.Literal(table).as_string(None) for table in tables) in rendered
    assert "target_namespace AS MATERIALIZED" in rendered
    assert "target_relations AS MATERIALIZED" in rendered
    assert "%s" not in rendered
    assert statement_for(schema, tables) is statement
    assert cached.cache_info().hits == 1
    assert cached.cache_info().maxsize == 128

    before_large = cached.cache_info()
    statement_for("large_catalog", tuple(f"relation_{index}" for index in range(257)))
    after_large = cached.cache_info()
    assert after_large.currsize == before_large.currsize
    assert after_large.hits == before_large.hits
    assert after_large.misses == before_large.misses

    before_long_schema = cached.cache_info()
    statement_for("s" * 65537, ("small_table",))
    after_long_schema = cached.cache_info()
    assert after_long_schema.currsize == before_long_schema.currsize
    assert after_long_schema.hits == before_long_schema.hits
    assert after_long_schema.misses == before_long_schema.misses

    for index in range(130):
        statement_for(f"bounded_{index}", ("small_table",))
    assert cached.cache_info().currsize == 128


def test_catalog_manifest_prepares_fresh_reads_without_caching_results(monkeypatch):
    families = tuple(migrations._CATALOG_MANIFEST_SHARED_QUERIES)
    returned_manifests = [
        {family: ([{"read": ordinal}] if family == "columns" else []) for family in families}
        for ordinal in (1, 2)
    ]

    class _Cursor:
        def __init__(self, manifest):
            self.manifest = manifest

        def fetchone(self):
            return (self.manifest,)

    class _Connection:
        target = SimpleNamespace(schema="fresh_catalog")

        def __init__(self):
            self.calls = []

        def execute(self, statement, params=(), *, prepare=None):
            self.calls.append((statement, params, prepare))
            return _Cursor(returned_manifests[len(self.calls) - 1])

    monkeypatch.setattr(
        migrations,
        "_normalize_catalog_manifest",
        lambda tables, manifest: manifest,
    )
    connection = _Connection()

    first = migrations.catalog_manifest(connection, ("fresh_table",))
    second = migrations.catalog_manifest(connection, ("fresh_table",))

    assert first is not second
    assert first["columns"] == [{"read": 1}]
    assert second["columns"] == [{"read": 2}]
    assert len(connection.calls) == 2
    assert connection.calls[0][0] is connection.calls[1][0]
    assert connection.calls[0][1:] == ((), True)
    assert connection.calls[1][1:] == ((), True)
