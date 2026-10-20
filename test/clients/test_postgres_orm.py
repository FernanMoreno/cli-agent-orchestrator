"""Core ORM native PostgreSQL ordering and DDL contracts."""

from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable

from cli_agent_orchestrator.clients.database import Base, TerminalModel


def test_terminal_order_is_explicit_and_generated():
    order = TerminalModel.__table__.c.insertion_sequence
    assert order.identity is not None
    assert order.unique and not order.nullable


def test_native_boolean_defaults_and_partial_index():
    ddl = str(CreateTable(TerminalModel.__table__).compile(dialect=dialect()))
    assert "BOOLEAN DEFAULT false" in ddl
    indexes = [i for table in Base.metadata.tables.values() for i in table.indexes]
    partial = [i for i in indexes if i.name == "uq_memory_key_scope_null"]
    assert len(partial) == 1
    assert all(i.dialect_options["postgresql"]["where"] is not None for i in partial)


from datetime import datetime, timedelta, timezone

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.postgres import connect


@pytest.mark.postgres
def test_real_native_terminal_fifo_defaults_and_timezone(postgres_core):
    target = postgres_core
    with connect(target) as connection:
        connection.execute(
            "INSERT INTO terminals(id,tmux_session,tmux_window,provider,last_active) VALUES (%s,%s,%s,%s,%s)",
            ("older", "session", "a", "codex", datetime(2026, 1, 2)),
        )
        connection.execute(
            "INSERT INTO terminals(id,tmux_session,tmux_window,provider,last_active) VALUES (%s,%s,%s,%s,%s)",
            ("newer", "session", "b", "codex", datetime(2020, 1, 2)),
        )
        row = connection.execute(
            "SELECT deferred_init_external_owner,deferred_init_runtime_reclaimed FROM terminals WHERE id=%s",
            ("older",),
        ).fetchone()
        assert tuple(row) == (False, False)
        connection.execute(
            "UPDATE terminals SET last_active=%s WHERE id=%s", (datetime(2030, 1, 2), "older")
        )
        aware = datetime(2026, 1, 1, 9, tzinfo=timezone(timedelta(hours=2)))
        dates = connection.execute(
            "SELECT %s::timestamp without time zone AS naive, %s::timestamp with time zone AS aware",
            (aware, aware),
        ).fetchone()
        assert dates["naive"] == datetime(2026, 1, 1, 7)
        assert dates["aware"] == datetime(2026, 1, 1, 7, tzinfo=timezone.utc)
    database.init_db()
    assert [row["id"] for row in database.list_terminals_by_session("session")] == [
        "older",
        "newer",
    ]


@pytest.mark.postgres
@pytest.mark.parametrize(
    "column", ["caller_id", "working_directory", "group", "session_incarnation_id"]
)
def test_runtime_refuses_missing_terminal_metadata_without_repair(
    postgres_core, monkeypatch, column
):
    from test.postgres.fixtures import make_orm_engine

    from psycopg import sql

    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch

    with connect(postgres_core.migration_target()) as owner:
        owner.raw.execute(
            sql.SQL("ALTER TABLE terminals DROP COLUMN {}").format(sql.Identifier(column))
        )
    engine = make_orm_engine(postgres_core)
    monkeypatch.setattr(database, "engine", engine)
    try:
        with pytest.raises(SchemaMismatch):
            database.init_db()
        with connect(postgres_core) as runtime:
            assert (
                runtime.execute(
                    "SELECT 1 FROM information_schema.columns WHERE table_schema=%s AND table_name='terminals' AND column_name=%s",
                    (postgres_core.schema, column),
                ).fetchone()
                is None
            )
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_project_alias_primary_key_is_global_and_memory_null_scope_is_unique(postgres_core):
    from test.postgres.fixtures import make_orm_engine

    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    from cli_agent_orchestrator.clients.database import MemoryMetadataModel, ProjectAliasModel

    engine = make_orm_engine(postgres_core)
    try:
        with Session(engine) as session:
            session.add(
                MemoryMetadataModel(
                    key="shared",
                    memory_type="project",
                    scope="global",
                    scope_id=None,
                    file_path="/a.md",
                    tags="",
                )
            )
            session.commit()
            session.add(
                MemoryMetadataModel(
                    key="shared",
                    memory_type="project",
                    scope="global",
                    scope_id=None,
                    file_path="/b.md",
                    tags="",
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
            session.add(
                MemoryMetadataModel(
                    key="shared",
                    memory_type="project",
                    scope="session",
                    scope_id="t1",
                    file_path="/c.md",
                    tags="",
                )
            )
            session.commit()
        assert tuple(ProjectAliasModel.__table__.primary_key.columns.keys()) == ("alias",)
    finally:
        engine.dispose()


@pytest.mark.offline_database
def test_orm_connection_configuration_keeps_its_creation_target(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import psycopg

    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.clients.postgres import DatabaseTarget

    target = SimpleNamespace(
        schema="owned_schema",
        identity=("host", 5432, "db", "owned_schema", "store"),
        connection_parameters=lambda: {},
    )
    monkeypatch.setattr(DatabaseTarget, "from_settings", classmethod(lambda cls: target))
    native = MagicMock()
    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: native)
    assert database._native_orm_connection() is native

    def changed_configuration(cls):
        raise AssertionError("connection setup must not reread changed configuration")

    monkeypatch.setattr(DatabaseTarget, "from_settings", classmethod(changed_configuration))
    record = SimpleNamespace(info={})
    database._configure_native_orm_connection(native, record)
    assert record.info["cao_target_identity"] == target.identity


@pytest.mark.postgres
def test_native_orm_creator_and_pool_configuration_share_target(postgres_core):
    from types import SimpleNamespace

    native = database._native_orm_connection()
    try:
        record = SimpleNamespace(info={})
        database._configure_native_orm_connection(native, record)
        assert record.info["cao_target_identity"] == postgres_core.identity
        assert native.execute("SELECT count(*) FROM terminals").fetchone()[0] == 0
    finally:
        native.close()
