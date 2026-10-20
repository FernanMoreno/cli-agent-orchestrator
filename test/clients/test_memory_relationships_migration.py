"""U1 persistence tests (issue #511): schema shape, migrator registry placement,
dedup for global scope, idempotent re-run, and loss-aware legacy backfill."""

import uuid
from test.postgres.orm import configured_orm_engine

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database as db_mod
from cli_agent_orchestrator.clients.database import Base, MemoryMetadataModel
from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope


@pytest.fixture
def fresh_db(isolated_memory_db):
    engine = configured_orm_engine()
    try:
        yield isolated_memory_db, engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def close_native_observers(monkeypatch):
    owned = []
    original = connect

    def observer(target):
        connection = original(target)
        owned.append(connection)
        return connection

    monkeypatch.setattr(__import__(__name__, fromlist=["connect"]), "connect", observer)
    yield
    for connection in owned:
        connection.close()


def backfill(target):
    with connect(target) as connection:
        db_mod._backfill_legacy_related_keys(connection.raw)


def _seed(engine, key, related_keys=None, scope="global", scope_id=None):
    s = sessionmaker(bind=engine)()
    try:
        s.add(
            MemoryMetadataModel(
                id=str(uuid.uuid4()),
                key=key,
                memory_type="project",
                scope=scope,
                scope_id=scope_id,
                file_path=f"/{key}.md",
                tags="t",
                related_keys=related_keys,
            )
        )
        s.commit()
    finally:
        s.close()


def test_table_shape_14_columns(fresh_db):
    db_path, _ = fresh_db
    backfill(db_path)
    conn = connect(db_path)
    cols = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT column_name, is_nullable='NO' FROM information_schema.columns WHERE table_schema=%s AND table_name='memory_relationships'",
            (db_path.schema,),
        )
    }
    expected = {
        "id",
        "scope",
        "scope_id",
        "source_key",
        "target_key",
        "type",
        "origin",
        "status",
        "confidence",
        "rank",
        "attributes_json",
        "source_updated_at",
        "created_at",
        "updated_at",
    }
    assert set(cols) == expected
    # NOT NULL on the dedup-tuple columns incl scope_id (the sentinel column).
    for c in ("scope", "scope_id", "source_key", "target_key", "type", "origin", "status"):
        assert cols[c] == 1, f"{c} must be NOT NULL"
    for c in ("confidence", "rank", "attributes_json", "source_updated_at"):
        assert cols[c] == 0, f"{c} must be nullable"


def test_indexes_created(fresh_db):
    db_path, _ = fresh_db
    backfill(db_path)
    conn = connect(db_path)
    idx = {
        r[0]
        for r in conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname=%s AND tablename='memory_relationships'",
            (db_path.schema,),
        )
    }
    assert "uq_memory_rel" in idx
    assert "idx_memory_rel_lookup" in idx


def test_native_registry_declares_memory_relationships_without_runtime_ddl():
    import inspect

    assert "memory_relationships" in db_mod.CORE_MIGRATIONS[0].tables
    assert "_backfill_legacy_related_keys" not in inspect.getsource(db_mod.init_db)
    source = inspect.getsource(db_mod._backfill_legacy_related_keys)
    assert "workflow_run" not in source
    assert "memory_metadata" in source and "memory_relationships" in source


def test_idempotent_rerun(fresh_db):
    db_path, _ = fresh_db
    backfill(db_path)
    backfill(db_path)  # no error, no duplicate table
    conn = connect(db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname='memory_relationships'",
        (db_path.schema,),
    ).fetchone()[0]
    assert n == 1


def test_backfill_happy_path_preserves_rank_null_confidence(fresh_db):
    db_path, engine = fresh_db
    for k in ("src", "a", "b", "c"):
        _seed(engine, k)
    _seed_src = sessionmaker(bind=engine)()
    # give src a related_keys list a,b,c
    row = _seed_src.query(MemoryMetadataModel).filter(MemoryMetadataModel.key == "src").first()
    row.related_keys = "a,b,c"
    _seed_src.commit()
    _seed_src.close()

    backfill(db_path)
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT target_key, rank, confidence, type, origin, status FROM memory_relationships "
        "WHERE source_key='src' ORDER BY rank"
    ).fetchall()
    assert [r[0] for r in rows] == ["a", "b", "c"]
    assert [r[1] for r in rows] == [0, 1, 2]  # rank preserved
    assert all(r[2] is None for r in rows)  # confidence NULL, never fabricated
    assert all(
        r[3] == "relates_to" and r[4] == "legacy_related_keys" and r[5] == "active" for r in rows
    )


def test_backfill_null_and_empty_yield_zero_rows(fresh_db):
    db_path, engine = fresh_db
    _seed(engine, "never", related_keys=None)  # never computed
    _seed(engine, "empty", related_keys="")  # computed-empty
    backfill(db_path)
    conn = connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM memory_relationships").fetchone()[0]
    assert n == 0


def test_backfill_dangling_reported_not_activated(fresh_db):
    db_path, engine = fresh_db
    _seed(engine, "src", related_keys="ghost")  # ghost does not exist
    backfill(db_path)
    conn = connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM memory_relationships WHERE source_key='src'").fetchone()[
        0
    ]
    assert n == 0  # dangling link NOT activated (reported instead)


def test_backfill_idempotent_per_source(fresh_db):
    db_path, engine = fresh_db
    _seed(engine, "src", related_keys="a")
    _seed(engine, "a")
    backfill(db_path)
    backfill(db_path)  # re-run
    conn = connect(db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM memory_relationships WHERE source_key='src' AND target_key='a'"
    ).fetchone()[0]
    assert n == 1  # not double-inserted


def test_related_keys_column_untouched(fresh_db):
    """The migrator must NOT modify memory_metadata.related_keys (retirement is a
    separate later change)."""
    db_path, engine = fresh_db
    _seed(engine, "src", related_keys="a")
    _seed(engine, "a")
    backfill(db_path)
    conn = connect(db_path)
    val = conn.execute("SELECT related_keys FROM memory_metadata WHERE key='src'").fetchone()[0]
    assert val == "a"  # unchanged
