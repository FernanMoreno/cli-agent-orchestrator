"""Live-schema determinism proof for derived vault state."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from test.fixtures.vault_factory import build_vault_fixture
from test.postgres.orm import configured_orm_engine

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients.database import (
    MemoryMetadataModel,
    MemoryRelationshipModel,
    VaultNoteAliasModel,
    VaultNoteModel,
)
from cli_agent_orchestrator.services.vault.reconcile import rebuild, reconcile

BYTE_EQUAL_COLUMNS = {
    "vault_note": {
        "note_uid",
        "vault_id",
        "scope",
        "scope_id",
        "cao_key",
        "vault_relpath",
        "managed",
        "content_sha256",
        "frontmatter_sha256",
        "size_bytes",
        "mtime_ns",
        "status",
        "key_source",
        "key_source_reason",
    },
    "vault_finding": {"vault_id", "vault_relpath", "code", "severity", "detail"},
    "vault_note_alias": {
        "vault_id",
        "former_relpath",
        "cao_key",
        "scope",
        "scope_id",
        "content_sha256",
    },
    "memory_metadata": {
        "id",
        "key",
        "memory_type",
        "scope",
        "scope_id",
        "source_kind",
        "file_path",
        "tags",
        "source_provider",
        "source_terminal_id",
        "token_estimate",
        "created_at",
        "updated_at",
        "access_count",
        "last_accessed_at",
        "last_compiled_at",
        "related_keys",
    },
    "memory_relationships": {
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
    },
}

BYTE_EQUAL_ROW_KEYS = {
    "vault_note": ("note_uid",),
    "vault_finding": ("vault_id", "vault_relpath", "code", "severity"),
    "vault_note_alias": ("vault_id", "former_relpath", "cao_key"),
    "memory_metadata": ("id",),
    "memory_relationships": (
        "scope",
        "scope_id",
        "source_key",
        "target_key",
        "type",
        "origin",
    ),
}

STRUCTURAL_COLUMNS = {
    "vault_note": {"last_reconciled_at"},
    "vault_finding": {"id", "reconcile_run_id", "created_at"},
    "vault_note_alias": {"created_at"},
    "memory_metadata": set(),
    "memory_relationships": {"id", "created_at", "updated_at"},
}


@pytest.fixture
def deterministic_vaults(tmp_path):
    forward = build_vault_fixture(tmp_path / "forward", fixed_mtimes=True)
    reverse = build_vault_fixture(tmp_path / "reverse", creation_order="reverse", fixed_mtimes=True)
    for fixture in (forward, reverse):
        _assert_fixed_mtimes(fixture)
        linked = fixture.root / "Projects/CAO Design/Linked.md"
        linked.write_text("[[Design]]", encoding="utf-8")
        os.utime(linked, ns=(1_700_000_000_999_999_999,) * 2)
    return forward, reverse


def test_live_schema_columns_are_completely_classified(tmp_path, monkeypatch):
    from cli_agent_orchestrator.services import memory_relationship_service
    from cli_agent_orchestrator.services.vault import reconcile as reconcile_module

    engine = _database(
        tmp_path / "schema.db", monkeypatch, reconcile_module, memory_relationship_service
    )
    _assert_live_schema_is_completely_classified(engine)


def test_rename_alias_is_retained_incrementally_but_rebuild_derives_current_path(
    tmp_path, monkeypatch, deterministic_vaults
):
    from cli_agent_orchestrator.services import memory_relationship_service
    from cli_agent_orchestrator.services.vault import reconcile as reconcile_module

    forward, _reverse = deterministic_vaults
    engine = _database(
        tmp_path / "rename.db", monkeypatch, reconcile_module, memory_relationship_service
    )
    incremental_key = _stage_incremental_rename(engine, forward)
    with sessionmaker(bind=engine)() as db:
        alias = db.query(VaultNoteAliasModel).one()
    assert (alias.former_relpath, alias.cao_key, alias.created_at is not None) == (
        "Projects/CAO Design/Don't Panic.md",
        incremental_key,
        True,
    )

    # ADR-006 defines rebuild as alias-free: a path-derived key can therefore
    # differ from the key retained during an incremental rename reconciliation.
    rebuild(forward.vault, run_id="rebuild-one")
    with sessionmaker(bind=engine)() as db:
        assert db.query(VaultNoteAliasModel).count() == 0
        rebuilt_key = (
            db.query(VaultNoteModel)
            .filter_by(vault_relpath="Projects/CAO Design/Renamed.md")
            .one()
            .cao_key
        )
    assert rebuilt_key != incremental_key


def test_two_rebuilds_are_byte_deterministic_with_rename_in_place(
    tmp_path, monkeypatch, deterministic_vaults
):
    from cli_agent_orchestrator.services import memory_relationship_service
    from cli_agent_orchestrator.services.vault import reconcile as reconcile_module

    forward, _reverse = deterministic_vaults
    engine = _database(
        tmp_path / "rebuild.db", monkeypatch, reconcile_module, memory_relationship_service
    )
    _stage_incremental_rename(engine, forward)
    rebuild(forward.vault, run_id="rebuild-one")
    byte_equal = _dump(engine, BYTE_EQUAL_COLUMNS)
    rebuild(forward.vault, run_id="rebuild-two")
    assert _dump(engine, BYTE_EQUAL_COLUMNS) == byte_equal


def test_two_rebuilds_refresh_every_structural_column(tmp_path, monkeypatch, deterministic_vaults):
    from cli_agent_orchestrator.services import memory_relationship_service
    from cli_agent_orchestrator.services.vault import reconcile as reconcile_module

    forward, _reverse = deterministic_vaults
    engine = _database(
        tmp_path / "structural.db", monkeypatch, reconcile_module, memory_relationship_service
    )
    _stage_incremental_rename(engine, forward)
    rebuild(forward.vault, run_id="rebuild-one")
    structural_before = _dump(engine, STRUCTURAL_COLUMNS)
    rebuild(forward.vault, run_id="rebuild-two")
    # ADR-006 requires rebuild to delete aliases, so its absence is intentional
    # here and alias freshness is asserted by the incremental pairing below.
    _assert_structural_group(
        engine,
        structural_before,
        run_id="rebuild-two",
        allow_empty_tables={"vault_note_alias"},
    )


def test_unchanged_incremental_reconcile_is_byte_deterministic_and_refreshes_all_structural_columns(
    tmp_path, monkeypatch, deterministic_vaults
):
    from cli_agent_orchestrator.services import memory_relationship_service
    from cli_agent_orchestrator.services.vault import reconcile as reconcile_module

    forward, _reverse = deterministic_vaults
    engine = _database(
        tmp_path / "incremental.db", monkeypatch, reconcile_module, memory_relationship_service
    )
    incremental_key = _stage_incremental_rename(engine, forward)
    renamed_path = forward.root / "Projects/CAO Design/Renamed.md"
    expected_updated_at = datetime.fromtimestamp(
        renamed_path.stat().st_mtime_ns / 1_000_000_000, timezone.utc
    )
    with sessionmaker(bind=engine)() as db:
        renamed_metadata = (
            db.query(MemoryMetadataModel).filter_by(key=incremental_key, source_kind="vault").one()
        )
        assert renamed_metadata.updated_at == expected_updated_at
    byte_equal = _dump(engine, BYTE_EQUAL_COLUMNS)
    structural_before = _dump(engine, STRUCTURAL_COLUMNS)
    assert len(structural_before["vault_note_alias"]) == 1

    reconcile(forward.vault, apply=True, run_id="unchanged-run")

    actual = _dump(engine, BYTE_EQUAL_COLUMNS)
    assert actual == byte_equal, _byte_equal_diff(byte_equal, actual)
    _assert_structural_group(engine, structural_before, run_id="unchanged-run")


def test_reverse_creation_order_rebuild_matches_forward_byte_state(
    tmp_path, monkeypatch, deterministic_vaults
):
    from cli_agent_orchestrator.services import memory_relationship_service
    from cli_agent_orchestrator.services.vault import reconcile as reconcile_module

    forward, reverse = deterministic_vaults
    for fixture in (forward, reverse):
        old_path = fixture.root / "Projects/CAO Design/Don't Panic.md"
        old_path.rename(old_path.with_name("Renamed.md"))

    first = _database(
        tmp_path / "forward.db", monkeypatch, reconcile_module, memory_relationship_service
    )
    rebuild(forward.vault, run_id="forward-rebuild")
    forward_byte = _dump(first, BYTE_EQUAL_COLUMNS)

    from test.postgres.fixtures import isolated_live_main_target

    # Creation-order determinism must compare independent stores, so the
    # second engine cannot merely reconnect to the first PostgreSQL target.
    with isolated_live_main_target(tmp_path / "reverse-store") as target:
        monkeypatch.setenv("CAO_DATABASE_CONFIG_FILE", str(target.home / "database-config.json"))
        second = _database(
            tmp_path / "reverse.db", monkeypatch, reconcile_module, memory_relationship_service
        )
        try:
            rebuild(reverse.vault, run_id="reverse-rebuild")
            assert _dump(second, BYTE_EQUAL_COLUMNS) == forward_byte
            assert _dump(first, BYTE_EQUAL_COLUMNS) == forward_byte
        finally:
            second.dispose()
            first.dispose()


def test_rebuild_leaves_native_rows_untouched(tmp_path, monkeypatch, deterministic_vaults):
    from cli_agent_orchestrator.services import memory_relationship_service
    from cli_agent_orchestrator.services.vault import reconcile as reconcile_module

    forward, _reverse = deterministic_vaults
    engine = _database(
        tmp_path / "native.db", monkeypatch, reconcile_module, memory_relationship_service
    )
    _seed_native_rows(engine)
    native_before = _native_dump(engine)
    rebuild(forward.vault, run_id="rebuild-native")
    assert _native_dump(engine) == native_before


def _stage_incremental_rename(engine, fixture) -> str:
    reconcile(fixture.vault, apply=True, run_id="first-run")
    old_path = fixture.root / "Projects/CAO Design/Don't Panic.md"
    new_path = old_path.with_name("Renamed.md")
    old_path.rename(new_path)
    reconcile(fixture.vault, apply=True, run_id="rename-run")
    with sessionmaker(bind=engine)() as db:
        return (
            db.query(VaultNoteModel)
            .filter_by(vault_relpath="Projects/CAO Design/Renamed.md")
            .one()
            .cao_key
        )


def _assert_fixed_mtimes(fixture) -> None:
    expected_design_mtime = 1_700_000_000_000_000_009
    assert (fixture.root / "Projects/CAO Design/Design.md").stat().st_mtime_ns == (
        expected_design_mtime
    )


def _database(path, monkeypatch, reconcile_module, relationship_module):
    engine = configured_orm_engine()
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(reconcile_module, "SessionLocal", Session)
    monkeypatch.setattr(relationship_module, "SessionLocal", Session)
    monkeypatch.setattr(reconcile_module, "_emit_audit_events", lambda *_args: None)
    return engine


def _assert_live_schema_is_completely_classified(engine) -> None:
    from cli_agent_orchestrator.clients.postgres import DatabaseTarget

    target = DatabaseTarget.from_settings()
    with engine.connect() as connection:
        inspector = inspect(connection)
        for table in BYTE_EQUAL_COLUMNS:
            live = {column["name"] for column in inspector.get_columns(table, schema=target.schema)}
            byte_equal = BYTE_EQUAL_COLUMNS[table]
            structural = STRUCTURAL_COLUMNS[table]
            assert byte_equal.isdisjoint(structural)
            assert live == byte_equal | structural


def _dump(engine, columns_by_table):
    filters = {
        "vault_note": "vault_id = 'fixture'",
        "vault_finding": "vault_id = 'fixture'",
        "vault_note_alias": "vault_id = 'fixture'",
        "memory_metadata": "source_kind = 'vault'",
        "memory_relationships": "origin = 'vault'",
    }
    dump = {}
    with engine.connect() as connection:
        for table, columns in columns_by_table.items():
            if not columns:
                dump[table] = []
                continue
            selected = ", ".join(sorted(columns))
            rows = connection.exec_driver_sql(
                f"SELECT {selected} FROM {table} WHERE {filters[table]} ORDER BY {selected}"
            ).all()
            dump[table] = [tuple(_json_value(value) for value in row) for row in rows]
    return dump


def _byte_equal_diff(before, after):
    """Name row identities and changed columns when a byte-stable projection drifts."""
    changes = {}
    for table, columns_set in BYTE_EQUAL_COLUMNS.items():
        columns = tuple(sorted(columns_set))
        key_columns = BYTE_EQUAL_ROW_KEYS[table]
        column_index = {column: index for index, column in enumerate(columns)}
        key_indexes = tuple(column_index[column] for column in key_columns)
        before_by_key = {
            tuple(row[index] for index in key_indexes): dict(zip(columns, row))
            for row in before[table]
        }
        after_by_key = {
            tuple(row[index] for index in key_indexes): dict(zip(columns, row))
            for row in after[table]
        }
        before_keys = set(before_by_key)
        after_keys = set(after_by_key)
        changed = {
            key: tuple(
                column
                for column in columns
                if before_by_key[key][column] != after_by_key[key][column]
            )
            for key in sorted(before_keys & after_keys, key=repr)
            if before_by_key[key] != after_by_key[key]
        }
        if changed or before_keys != after_keys:
            changes[table] = {
                "added_rows": tuple(sorted(after_keys - before_keys, key=repr)),
                "removed_rows": tuple(sorted(before_keys - after_keys, key=repr)),
                "changed_columns_by_row": changed,
            }
    return f"byte-equal projection changed: {changes!r}"


def _assert_structural_group(
    engine, before, *, run_id: str, allow_empty_tables=frozenset()
) -> None:
    after = _dump(engine, STRUCTURAL_COLUMNS)
    assert {table: len(rows) for table, rows in after.items()} == {
        table: len(rows) for table, rows in before.items()
    }
    for table, columns in STRUCTURAL_COLUMNS.items():
        if columns and table not in allow_empty_tables:
            assert before[table], f"{table} must have rows to prove its structural columns change"
        ordered_columns = sorted(columns)
        for index, column in enumerate(ordered_columns):
            assert {row[index] for row in before[table]}.isdisjoint(
                {row[index] for row in after[table]}
            ), f"{table}.{column} is structural and must change across runs"
    with engine.connect() as connection:
        for (last_reconciled_at,) in connection.exec_driver_sql(
            "SELECT last_reconciled_at FROM vault_note WHERE vault_id = 'fixture'"
        ):
            assert last_reconciled_at is not None
        for (
            finding_id,
            finding_run_id,
            code,
            relpath,
            severity,
            created_at,
        ) in connection.exec_driver_sql(
            "SELECT id, reconcile_run_id, code, vault_relpath, severity, created_at "
            "FROM vault_finding WHERE vault_id = 'fixture'"
        ):
            assert finding_run_id == run_id
            assert (
                finding_id
                == hashlib.sha256(
                    f"finding\0{run_id}\0{code}\0{relpath}\0{severity}".encode("utf-8")
                ).hexdigest()
            )
            assert created_at is not None
        for relation_id, created_at, updated_at in connection.exec_driver_sql(
            "SELECT id, created_at, updated_at FROM memory_relationships WHERE origin = 'vault'"
        ):
            assert uuid.UUID(relation_id)
            assert created_at is not None
            assert updated_at is not None


def _seed_native_rows(engine) -> None:
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(
            MemoryMetadataModel(
                id="native-id",
                key="native",
                memory_type="reference",
                scope="global",
                scope_id=None,
                source_kind="native",
                file_path="native.md",
            )
        )
        db.add(
            MemoryRelationshipModel(
                id="native-edge",
                scope="global",
                scope_id="",
                source_key="native",
                target_key="other",
                type="relates_to",
                origin="human",
                status="active",
            )
        )
        db.commit()


def _native_dump(engine):
    with engine.connect() as connection:
        metadata = connection.exec_driver_sql(
            "SELECT id, key, source_kind, file_path FROM memory_metadata WHERE source_kind = 'native'"
        ).all()
        relationships = connection.exec_driver_sql(
            "SELECT id, origin, source_key, target_key FROM memory_relationships WHERE origin != 'vault'"
        ).all()
    return metadata, relationships


def _json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value
