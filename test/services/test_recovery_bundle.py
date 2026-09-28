"""Closed v24 SQLite inventory is the first recovery-bundle prerequisite."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import json
import shutil
import sqlite3
import threading
import time
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

EXPECTED_WORK_REPOSITORY_TABLES = (
    "work_attempts",
    "work_bubblewrap_process_identities",
    "work_child_origin_bindings",
    "work_delegation_snapshots",
    "work_delivery_content_refs",
    "work_delivery_orders",
    "work_dispatch_bindings",
    "work_dispatch_v2_evidence",
    "work_event_sequences",
    "work_events",
    "work_executable_contents",
    "work_grant_revocations",
    "work_grants",
    "work_human_decision_claims",
    "work_human_decision_revocations",
    "work_human_decisions",
    "work_inbox_bindings",
    "work_inbox_store_context",
    "work_inbox_store_identity",
    "work_items",
    "work_jobs",
    "work_knowledge_access_audit",
    "work_knowledge_cursors",
    "work_knowledge_decisions",
    "work_knowledge_events",
    "work_knowledge_records",
    "work_knowledge_revisions",
    "work_knowledge_tombstones",
    "work_launch_origin_bindings",
    "work_launch_provisions",
    "work_lineage_integrity",
    "work_lineage_projection_intents",
    "work_memory_access_audit",
    "work_migrations",
    "work_offline_cut_observations",
    "work_offline_cut_rejections",
    "work_offline_cuts",
    "work_origin_authorizations",
    "work_origin_subjects",
    "work_path_reservations",
    "work_principals",
    "work_process_identities",
    "work_recovery_context",
    "work_registered_writers",
    "work_reservation_sets",
    "work_results",
    "work_scheduler_dependencies",
    "work_scheduler_policy",
    "work_scheduler_requests",
    "work_snapshot_sources",
    "work_step_contracts",
    "work_task_received_receipts",
    "work_transition_receipts",
    "work_worktree_evidence",
)

EXPECTED_V28_TABLES = (
    "flows",
    "idempotency_keys",
    "inbox",
    "memory_metadata",
    "memory_relationships",
    "native_children",
    "project_aliases",
    "terminal_turn_receipts",
    "terminals",
    *EXPECTED_WORK_REPOSITORY_TABLES,
    "workflow_index",
    "workflow_outcomes",
    "workflow_plan_approval",
    "workflow_run",
    "workflow_run_event",
    "workflow_run_seq",
    "workflow_run_step",
)

EXPECTED_REFERENCE_FAMILIES = tuple(
    sorted(
        (
            (
                "delivery_content_store",
                "external_bytes",
                "work_delivery_content_refs",
                ("content_ref", "content_hash", "byte_length"),
            ),
            (
                "executable_content_store",
                "external_bytes",
                "work_executable_contents",
                ("content_hash", "immutable_location", "byte_length"),
            ),
            (
                "immutable_result_store",
                "external_bytes",
                "work_results",
                ("content_hash", "immutable_location", "byte_length"),
            ),
            (
                "immutable_result_store",
                "external_bytes",
                "work_worktree_evidence",
                ("content_hash", "immutable_location", "byte_length"),
            ),
            (
                "inbox_store_identity",
                "observational",
                "work_inbox_store_context",
                ("store_identity",),
            ),
            (
                "inbox_store_identity",
                "observational",
                "work_inbox_store_identity",
                ("store_identity",),
            ),
            (
                "knowledge_result_relation",
                "sqlite_relation",
                "work_knowledge_revisions",
                ("source_artifact_id",),
            ),
            (
                "legacy_workflow_relation",
                "sqlite_relation",
                "work_step_contracts",
                ("run_id",),
            ),
            (
                "legacy_flow_path",
                "requires_future_profile",
                "flows",
                ("file_path",),
            ),
            (
                "legacy_live_terminal",
                "observational",
                "idempotency_keys",
                ("terminal_id",),
            ),
            (
                "legacy_live_terminal",
                "observational",
                "native_children",
                ("parent_terminal_id", "terminal_id"),
            ),
            (
                "legacy_live_terminal",
                "observational",
                "terminal_turn_receipts",
                ("terminal_id",),
            ),
            (
                "legacy_live_terminal",
                "observational",
                "terminals",
                ("tmux_session", "tmux_window", "working_directory"),
            ),
            (
                "legacy_live_terminal",
                "observational",
                "workflow_outcomes",
                ("source_terminal_id",),
            ),
            (
                "legacy_live_terminal",
                "observational",
                "workflow_run_event",
                ("terminal_id",),
            ),
            (
                "legacy_workflow_path",
                "requires_future_profile",
                "workflow_index",
                ("source_path",),
            ),
            (
                "legacy_workflow_payload",
                "sqlite_content",
                "workflow_run",
                ("spec_snapshot", "inputs_json", "manifest_json"),
            ),
            (
                "legacy_workflow_payload",
                "sqlite_content",
                "workflow_run_step",
                ("output_json", "result_json"),
            ),
            (
                "legacy_workflow_run_relation",
                "sqlite_relation",
                "workflow_run_event",
                ("run_id",),
            ),
            (
                "legacy_workflow_run_relation",
                "sqlite_relation",
                "workflow_run_seq",
                ("run_id",),
            ),
            (
                "legacy_workflow_run_relation",
                "sqlite_relation",
                "workflow_run_step",
                ("run_id",),
            ),
            (
                "memory_content_store",
                "external_text",
                "memory_metadata",
                ("id", "file_path"),
            ),
            (
                "memory_metadata_relation",
                "sqlite_content",
                "memory_relationships",
                ("scope", "scope_id", "source_key", "target_key"),
            ),
            (
                "snapshot_revision_relation",
                "sqlite_relation",
                "work_snapshot_sources",
                ("record_id", "revision"),
            ),
        )
    )
)

EXPECTED_SQLITE_RELATION_MAPPINGS = (
    (
        "knowledge_result_relation",
        "work_knowledge_revisions",
        ("source_artifact_id",),
        "work_results",
        ("id",),
    ),
    (
        "legacy_workflow_relation",
        "work_step_contracts",
        ("run_id",),
        "workflow_run",
        ("run_id",),
    ),
    (
        "legacy_workflow_run_relation",
        "workflow_run_event",
        ("run_id",),
        "workflow_run",
        ("run_id",),
    ),
    (
        "legacy_workflow_run_relation",
        "workflow_run_seq",
        ("run_id",),
        "workflow_run",
        ("run_id",),
    ),
    (
        "legacy_workflow_run_relation",
        "workflow_run_step",
        ("run_id",),
        "workflow_run",
        ("run_id",),
    ),
    (
        "snapshot_revision_relation",
        "work_snapshot_sources",
        ("record_id", "revision"),
        "work_knowledge_revisions",
        ("record_id", "revision"),
    ),
)


def _table_names(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )


def _database_state(connection: sqlite3.Connection) -> tuple[object, ...]:
    """Capture every initialized v24 table and its rows before read-only inspection."""
    tables = _table_names(connection)
    schema = tuple(
        connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )
    )
    rows = tuple(
        (
            table,
            (
                ()
                if table
                in {
                    "work_offline_cut_observations",
                    "work_offline_cut_rejections",
                    "work_offline_cuts",
                    "work_principals",
                    "work_registered_writers",
                }
                else tuple(connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid'))
            ),
        )
        for table in tables
    )
    return schema, rows, connection.total_changes


def _replace_flows_with_an_extra_foreign_key(connection: sqlite3.Connection) -> None:
    """Keep the legacy v24 columns while introducing an unsupported physical FK."""
    connection.execute("DROP TABLE flows")
    connection.execute(
        "CREATE TABLE flows ("
        "name VARCHAR NOT NULL, file_path VARCHAR NOT NULL, schedule VARCHAR NOT NULL, "
        "agent_profile VARCHAR NOT NULL, provider VARCHAR NOT NULL, script VARCHAR, "
        "last_run DATETIME, next_run DATETIME, enabled BOOLEAN, PRIMARY KEY (name), "
        "FOREIGN KEY (agent_profile) REFERENCES terminals(id)"
        ")"
    )


def _assert_real_v28_fixture(connection: sqlite3.Connection) -> None:
    """Keep RED behavioural: assert the real fixture before asking for the missing API."""
    assert _table_names(connection) == EXPECTED_V28_TABLES
    assert {row[1] for row in connection.execute("PRAGMA table_info(work_results)")} >= {
        "content_hash",
        "immutable_location",
        "byte_length",
    }
    assert {
        row[1] for row in connection.execute("PRAGMA table_info(work_delivery_content_refs)")
    } >= {"content_ref", "content_hash", "byte_length"}
    assert any(
        row[2] == "workflow_run"
        for row in connection.execute("PRAGMA foreign_key_list(work_step_contracts)")
    )


def _inventory_module(connection: sqlite3.Connection):
    _assert_real_v28_fixture(connection)
    name = "cli_agent_orchestrator.services.recovery_inventory"
    if importlib.util.find_spec(name) is None:
        pytest.fail("closed recovery inventory is not implemented")
    return importlib.import_module(name)


@pytest.fixture
def v24_connection(initialized_cao_connection):
    return initialized_cao_connection


@pytest.fixture
def initialized_cao_connection(tmp_path, monkeypatch):
    """Exercise the application's real init_db composition on an isolated SQLite file."""
    from cli_agent_orchestrator import constants
    from cli_agent_orchestrator.clients import database

    database_path = tmp_path / "cao-v24.sqlite3"
    database_directory = database_path.parent
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(constants, "DATABASE_FILE", database_path)
    monkeypatch.setattr(database, "DB_DIR", database_directory)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    database.init_db()
    connection = sqlite3.connect(database_path)
    try:
        yield connection
    finally:
        connection.close()
        engine.dispose()


def test_real_init_db_store_has_an_explicit_closed_v28_inventory(initialized_cao_connection):
    """The normal CAO store includes the legacy tables alongside the verified Work profile."""
    before = _database_state(initialized_cao_connection)
    module = importlib.import_module("cli_agent_orchestrator.services.recovery_inventory")

    assert _table_names(initialized_cao_connection) == EXPECTED_V28_TABLES
    inventory = module.inspect_work_store(initialized_cao_connection)

    assert inventory.tables == EXPECTED_V28_TABLES
    assert _database_state(initialized_cao_connection) == before


def test_v28_inventory_is_closed_canonical_and_read_only(v24_connection):
    """A v28 recovery profile names every table and external family without writes."""
    before = _database_state(v24_connection)
    module = _inventory_module(v24_connection)

    inventory = module.inspect_work_store(v24_connection)

    assert inventory.profile_version == 28
    assert inventory.tables == EXPECTED_V28_TABLES
    assert (
        tuple(
            (
                reference.family,
                reference.resolution,
                reference.source_table,
                reference.source_columns,
            )
            for reference in inventory.references
        )
        == EXPECTED_REFERENCE_FAMILIES
    )
    assert inventory.foreign_keys == tuple(sorted(inventory.foreign_keys))
    assert inventory.foreign_keys == module._V28_FOREIGN_KEYS
    assert len(inventory.foreign_keys) == 118
    assert sum(len(reference.source_columns) for reference in inventory.foreign_keys) == 182
    assert {
        (reference.on_update, reference.on_delete, reference.match_observed)
        for reference in inventory.foreign_keys
    } == {("NO ACTION", "NO ACTION", "NONE")}
    assert any(
        reference.source_table == "work_step_contracts" and reference.target_table == "workflow_run"
        for reference in inventory.foreign_keys
    )
    assert any(
        reference.source_table == "work_offline_cut_observations"
        and reference.source_columns == ("lease_id",)
        and reference.target_table == "work_offline_cuts"
        and reference.target_columns == ("id",)
        for reference in inventory.foreign_keys
    )
    with pytest.raises(FrozenInstanceError):
        inventory.profile_version = 23
    assert _database_state(v24_connection) == before


def test_sqlite_reference_families_have_closed_verified_column_mappings(v24_connection):
    """Logical relations must name both endpoints without treating memory edges as one FK."""
    before = _database_state(v24_connection)
    module = _inventory_module(v24_connection)

    inventory = module.inspect_work_store(v24_connection)
    sqlite_relations = tuple(
        (
            reference.family,
            reference.source_table,
            reference.source_columns,
            reference.target_table,
            reference.target_columns,
        )
        for reference in inventory.references
        if reference.resolution == "sqlite_relation"
    )

    assert sqlite_relations == EXPECTED_SQLITE_RELATION_MAPPINGS
    for reference in inventory.references:
        if reference.resolution == "sqlite_relation":
            assert reference.target_table in inventory.tables
            assert len(reference.source_columns) == len(reference.target_columns)
            source_columns = {
                row[1]
                for row in v24_connection.execute(f'PRAGMA table_info("{reference.source_table}")')
            }
            target_columns = {
                row[1]
                for row in v24_connection.execute(f'PRAGMA table_info("{reference.target_table}")')
            }
            assert set(reference.source_columns).issubset(source_columns)
            assert set(reference.target_columns).issubset(target_columns)
        if reference.family == "memory_metadata_relation":
            assert reference.resolution == "sqlite_content"
            assert reference.target_table is None
            assert reference.target_columns == ()
    assert _database_state(v24_connection) == before


def test_inventory_rejects_an_unclassified_table(v24_connection):
    """A future table cannot be silently adopted by the v24 profile."""
    module = _inventory_module(v24_connection)
    v24_connection.execute("CREATE TABLE work_unclassified_future_table (id TEXT PRIMARY KEY)")

    with pytest.raises(module.RecoveryInventoryError, match="recovery inventory incompatible"):
        module.inspect_work_store(v24_connection)


def test_inventory_rejects_a_missing_required_table(v24_connection):
    """Removing a required v24 table makes the profile unrepresentable."""
    module = _inventory_module(v24_connection)
    v24_connection.execute("DROP TABLE work_recovery_context")

    with pytest.raises(module.RecoveryInventoryError, match="recovery inventory incompatible"):
        module.inspect_work_store(v24_connection)


def test_inventory_rejects_a_missing_executable_content_table(v24_connection):
    """The V29 byte family cannot disappear from the closed recovery profile."""
    module = _inventory_module(v24_connection)
    v24_connection.execute("DROP TABLE work_executable_contents")

    with pytest.raises(module.RecoveryInventoryError, match="recovery inventory incompatible"):
        module.inspect_work_store(v24_connection)


def test_inventory_rejects_a_required_relation_without_adopting_it(v24_connection):
    """The inventory rejects a table whose required snapshot-source relation disappeared."""
    module = _inventory_module(v24_connection)
    v24_connection.execute("DROP TABLE work_snapshot_sources")
    v24_connection.execute(
        "CREATE TABLE work_snapshot_sources "
        "(snapshot_id TEXT NOT NULL, record_id TEXT NOT NULL, revision INTEGER NOT NULL, "
        "PRIMARY KEY(snapshot_id,record_id,revision))"
    )

    with pytest.raises(module.RecoveryInventoryError, match="recovery inventory incompatible"):
        module.inspect_work_store(v24_connection)


def test_inventory_rejects_an_extra_legacy_foreign_key(v24_connection):
    """A physical FK cannot be adopted merely because it matches a known logical link."""
    module = _inventory_module(v24_connection)
    _replace_flows_with_an_extra_foreign_key(v24_connection)

    with pytest.raises(module.RecoveryInventoryError, match="recovery inventory incompatible"):
        module.inspect_work_store(v24_connection)


def test_foreign_key_extraction_is_canonical_and_preserves_multiplicity():
    """Constraint names/declaration order are irrelevant, but duplicate constraints remain."""
    module = importlib.import_module("cli_agent_orchestrator.services.recovery_inventory")
    base_parent = """
        CREATE TABLE parent (
            parent_alpha TEXT,
            parent_beta TEXT,
            gamma TEXT,
            delta TEXT,
            PRIMARY KEY (parent_alpha, parent_beta),
            UNIQUE (gamma, delta)
        );
    """
    first = sqlite3.connect(":memory:")
    second = sqlite3.connect(":memory:")
    duplicate = sqlite3.connect(":memory:")
    try:
        first.executescript(base_parent + """
            CREATE TABLE child (
                alpha TEXT, beta TEXT, gamma TEXT, delta TEXT,
                CONSTRAINT alpha_link FOREIGN KEY (alpha, beta)
                    REFERENCES parent(parent_alpha, parent_beta)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT gamma_link FOREIGN KEY (gamma, delta)
                    REFERENCES parent(gamma, delta) ON DELETE SET NULL
            );
            """)
        second.executescript(base_parent + """
            CREATE TABLE child (
                alpha TEXT, beta TEXT, gamma TEXT, delta TEXT,
                CONSTRAINT renamed_gamma FOREIGN KEY (gamma, delta)
                    REFERENCES parent(gamma, delta) ON DELETE SET NULL,
                CONSTRAINT renamed_alpha FOREIGN KEY (alpha, beta)
                    REFERENCES parent(parent_alpha, parent_beta)
                    ON UPDATE CASCADE ON DELETE RESTRICT
            );
            """)
        duplicate.executescript("""
            CREATE TABLE parent (id TEXT PRIMARY KEY);
            CREATE TABLE child (
                parent_id TEXT,
                FOREIGN KEY (parent_id) REFERENCES parent(id),
                FOREIGN KEY (parent_id) REFERENCES parent(id)
            );
            """)

        expected = (
            module.ForeignKeyReference(
                "child",
                ("alpha", "beta"),
                "parent",
                ("parent_alpha", "parent_beta"),
                "CASCADE",
                "RESTRICT",
                "NONE",
            ),
            module.ForeignKeyReference(
                "child",
                ("gamma", "delta"),
                "parent",
                ("gamma", "delta"),
                "NO ACTION",
                "SET NULL",
                "NONE",
            ),
        )
        assert module._foreign_keys(first, ("child",)) == expected
        assert module._foreign_keys(second, ("child",)) == expected
        duplicate_references = module._foreign_keys(duplicate, ("child",))
        assert duplicate_references == (duplicate_references[0], duplicate_references[0])
    finally:
        first.close()
        second.close()
        duplicate.close()


def _recovery_bundle_module():
    name = "cli_agent_orchestrator.services.recovery_bundle"
    if importlib.util.find_spec(name) is None:
        pytest.fail("offline recovery capture is not implemented")
    return importlib.import_module(name)


def test_executable_capture_root_is_an_optional_private_path(tmp_path):
    """A caller must be able to name the private executable root, never a string path."""
    module = _recovery_bundle_module()
    database = tmp_path / "source.sqlite3"
    root = tmp_path / "source.sqlite3.executable-content"

    source = module.RecoveryCaptureSource(database_path=database, executable_content_root=root)
    assert source.executable_content_root == root
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.RecoveryCaptureSource(database_path=database, executable_content_root=str(root))


def _database_path(connection: sqlite3.Connection) -> Path:
    rows = connection.execute("PRAGMA database_list").fetchall()
    return Path(next(row[2] for row in rows if row[1] == "main"))


def _capture_with_work_authority(module, source, destination):
    """Use the real authenticated Work owner; no capture test injects a verifier."""
    from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.work_authority import WorkAuthority

    authority = WorkAuthority(WorkRepository(source.database_path))
    operator = auth._verified_principal(
        "https://recovery.test", "capture-operator", [auth.SCOPE_ADMIN], "jwt"
    )
    lease = authority.create_offline_cut(operator, ttl_seconds=60)
    return module.OfflineRecoveryCapture(authority, lease).capture(source, destination)


def _recomposed_v2_receipt(module, receipt, manifest):
    """Rebind a receipt to a deliberately modified canonical v2 manifest."""
    encoded = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    (receipt.bundle_path / "manifest.json").write_bytes(encoded)
    return module.RecoveryBundleReceipt(
        receipt.bundle_path,
        hashlib.sha256(encoded).hexdigest(),
        len(encoded),
        receipt.source_database,
        receipt.lease_id,
        receipt.publication_revision,
    )


def _historical_v25_sqlite(path: Path) -> None:
    """Build a real pre-v26 Work database through its historical migrations."""
    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    store = repository_module.WorkRepository(path)
    with store.transaction() as connection:
        for version in range(1, 26):
            for statement in repository_module._MIGRATIONS[version]:
                connection.execute(statement)
            if version == 16:
                store._initialize_inbox_store_context(connection)
            if version == 24:
                store._initialize_recovery_context(connection)
            connection.execute(
                "INSERT INTO work_migrations VALUES (?,?,?,?)",
                (version, repository_module._CHECKSUMS[version], time.time(), "verified"),
            )
            store._verify(connection, version=version)


def _publish_registered_result(
    connection: sqlite3.Connection,
    root: Path,
    *,
    content: bytes = b"offline recovery result bytes",
):
    """Use the real immutable producer and Work ledger to make a required byte object."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.services.step_output_store import ImmutableResultStore

    repository = WorkRepository(_database_path(connection))
    job = repository.create_job(
        project_id="recovery-project",
        principal_id="recovery-operator",
        allowed_providers=["mock_cli"],
        grant_id="recovery-grant",
    )
    work = repository.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key="recovery-result",
        request_hash="a" * 64,
        contract_id="recovery-contract",
        snapshot_id="recovery-snapshot",
        provider="mock_cli",
        actor_id="recovery-operator",
        lease_seconds=60,
    )
    attempt = work["attempts"][0]
    artifacts = ImmutableResultStore(root)

    def accept(ref):
        repository.register_result(
            attempt_id=attempt["id"],
            generation=attempt["generation"],
            content_hash=ref.content_hash,
            immutable_location=ref.immutable_location,
            byte_length=ref.byte_length,
            validator_id="recovery-fixture-validator",
            validation_evidence={"valid": True, "artifact_sha256": ref.content_hash},
            actor_id="recovery-operator",
        )
        return ref

    reference = artifacts.publish(content, accept)
    orphan = artifacts.publish(b"unreferenced bytes must not be captured", lambda ref: ref)
    return reference, orphan, root


def _register_executable_bytes(connection: sqlite3.Connection, root: Path) -> tuple[str, bytes]:
    """Seed the real V29 catalog with private binary bytes and one unreferenced file."""
    content = b"\x7fELF\x00\xff\x10 recovery executable bytes"
    digest = hashlib.sha256(content).hexdigest()
    root.mkdir(mode=0o700)
    target = root / digest
    target.write_bytes(content)
    target.chmod(0o600)
    orphan = root / hashlib.sha256(b"orphan executable").hexdigest()
    orphan.write_bytes(b"orphan executable")
    orphan.chmod(0o600)
    connection.execute(
        "INSERT INTO work_executable_contents(content_hash,immutable_location,byte_length,created_at) "
        "VALUES(?,?,?,?)",
        (digest, digest, len(content), time.time()),
    )
    connection.commit()
    return digest, content


def test_capture_and_restore_keep_exact_executable_bytes_in_bundle_only(
    initialized_cao_connection, tmp_path
):
    """A V29 row selects binary bytes, excludes orphans, and restores no live content root."""
    module = _recovery_bundle_module()
    source = _database_path(initialized_cao_connection)
    root = tmp_path / "source.sqlite3.executable-content"
    digest, content = _register_executable_bytes(initialized_cao_connection, root)
    receipt = _capture_with_work_authority(
        module,
        module.RecoveryCaptureSource(database_path=source, executable_content_root=root),
        tmp_path / "executable-bundle",
    )

    manifest = json.loads((receipt.bundle_path / "manifest.json").read_bytes())
    assert manifest["profile_version"] == 28
    assert (receipt.bundle_path / "objects" / digest).read_bytes() == content
    assert [
        (item["role"], item["identifier"], item["digest"], item["size"])
        for item in manifest["references"]
    ] == [("executable-content", digest, digest, len(content))]
    assert len(manifest["objects"]) == 2
    assert module.verify_recovery_bundle(receipt) == receipt

    destination = tmp_path / "restored.sqlite3"
    module.restore_recovery_bundle(receipt, destination)
    assert destination.exists()
    assert not destination.with_name(f"{destination.name}.executable-content").exists()
    assert (receipt.bundle_path / "objects" / digest).read_bytes() == content


@pytest.mark.parametrize(
    "fault", ["missing-root", "missing-file", "symlink", "wrong-size", "wrong-hash", "changed"]
)
def test_capture_rejects_broken_executable_reference_before_publication(
    initialized_cao_connection, tmp_path, monkeypatch, fault
):
    """Every required V29 byte object must remain private, present, and exact through capture."""
    module = _recovery_bundle_module()
    source = _database_path(initialized_cao_connection)
    root = tmp_path / "source.sqlite3.executable-content"
    digest, content = _register_executable_bytes(initialized_cao_connection, root)
    target = root / digest
    supplied_root = root
    if fault == "missing-root":
        supplied_root = None
    elif fault == "missing-file":
        target.unlink()
    elif fault == "symlink":
        target.unlink()
        target.symlink_to(root / hashlib.sha256(b"orphan executable").hexdigest())
    elif fault == "wrong-size":
        target.write_bytes(content + b"x")
    elif fault == "wrong-hash":
        target.write_bytes(b"X" + content[1:])
    else:
        original_copy = module._copy_open_file_as_object
        target_inode = target.stat().st_ino

        def copy_then_change(descriptor, objects, **kwargs):
            result = original_copy(descriptor, objects, **kwargs)
            if module.os.fstat(descriptor).st_ino == target_inode:
                target.write_bytes(content + b"late mutation")
            return result

        monkeypatch.setattr(module, "_copy_open_file_as_object", copy_then_change)

    destination = tmp_path / f"rejected-{fault}"
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        _capture_with_work_authority(
            module,
            module.RecoveryCaptureSource(
                database_path=source, executable_content_root=supplied_root
            ),
            destination,
        )
    assert not destination.exists()


@pytest.mark.parametrize("fault", ["oversized-on-open", "grows-during-read"])
def test_executable_capture_bounds_source_reads_before_rejection(
    initialized_cao_connection, tmp_path, monkeypatch, fault
):
    """A sparse or growing source cannot consume stage space beyond the 8 MiB cap."""
    module = _recovery_bundle_module()
    source = _database_path(initialized_cao_connection)
    root = tmp_path / "source.sqlite3.executable-content"
    digest, _content = _register_executable_bytes(initialized_cao_connection, root)
    target = root / digest
    if fault == "oversized-on-open":
        target.open("r+b").truncate(64 * 1024 * 1024)
    target_inode = target.stat().st_ino
    original_fdopen = module.os.fdopen
    bytes_read = [0]

    class MeteredReader:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            self.wrapped.__enter__()
            return self

        def __exit__(self, *args):
            return self.wrapped.__exit__(*args)

        def read(self, size=-1):
            if fault == "grows-during-read" and bytes_read[0] == 0:
                target.open("r+b").truncate(64 * 1024 * 1024)
            block = self.wrapped.read(size)
            bytes_read[0] += len(block)
            return block

    def metered_fdopen(descriptor, mode, *args, **kwargs):
        opened = original_fdopen(descriptor, mode, *args, **kwargs)
        if mode == "rb" and module.os.fstat(descriptor).st_ino == target_inode:
            return MeteredReader(opened)
        return opened

    monkeypatch.setattr(module.os, "fdopen", metered_fdopen)
    destination = tmp_path / f"rejected-{fault}"
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        _capture_with_work_authority(
            module,
            module.RecoveryCaptureSource(database_path=source, executable_content_root=root),
            destination,
        )
    assert bytes_read[0] <= 9 * 1024 * 1024
    assert not destination.exists()


def test_executable_capture_rejects_symlink_in_private_root_ancestor(
    initialized_cao_connection, tmp_path
):
    """Every path component to the executable root must be opened without symlink traversal."""
    module = _recovery_bundle_module()
    source = _database_path(initialized_cao_connection)
    root = tmp_path / "private-executables"
    _register_executable_bytes(initialized_cao_connection, root)
    alias = tmp_path / "linked-parent"
    alias.symlink_to(tmp_path, target_is_directory=True)
    destination = tmp_path / "rejected-symlink-ancestor"

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        _capture_with_work_authority(
            module,
            module.RecoveryCaptureSource(
                database_path=source, executable_content_root=alias / root.name
            ),
            destination,
        )
    assert not destination.exists()


def test_executable_capture_rejects_swapped_public_inode_before_read(
    initialized_cao_connection, tmp_path, monkeypatch
):
    """An inode replaced after path inspection must fail private-mode validation on its FD."""
    module = _recovery_bundle_module()
    source = _database_path(initialized_cao_connection)
    root = tmp_path / "private-executables"
    digest, content = _register_executable_bytes(initialized_cao_connection, root)
    target = root / digest
    replacement = root / "replacement"
    replacement.write_bytes(content)
    replacement.chmod(0o644)
    replacement_inode = replacement.stat().st_ino
    original_open = module.os.open
    original_fdopen = module.os.fdopen
    swapped = [False]
    bytes_read = [0]

    def swapping_open(path, flags, *args, **kwargs):
        if not swapped[0] and (path == target or path == digest):
            module.os.replace(replacement, target)
            swapped[0] = True
        return original_open(path, flags, *args, **kwargs)

    class MeteredReader:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            self.wrapped.__enter__()
            return self

        def __exit__(self, *args):
            return self.wrapped.__exit__(*args)

        def read(self, size=-1):
            block = self.wrapped.read(size)
            bytes_read[0] += len(block)
            return block

    def metered_fdopen(descriptor, mode, *args, **kwargs):
        opened = original_fdopen(descriptor, mode, *args, **kwargs)
        if mode == "rb" and module.os.fstat(descriptor).st_ino == replacement_inode:
            return MeteredReader(opened)
        return opened

    monkeypatch.setattr(module.os, "open", swapping_open)
    monkeypatch.setattr(module.os, "fdopen", metered_fdopen)
    destination = tmp_path / "rejected-swapped-inode"
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        _capture_with_work_authority(
            module,
            module.RecoveryCaptureSource(database_path=source, executable_content_root=root),
            destination,
        )
    assert swapped == [True]
    assert bytes_read == [0]
    assert not destination.exists()


def test_capture_verifies_a_real_v28_store_and_publishes_only_declared_objects(
    initialized_cao_connection, tmp_path
):
    """Would fail if capture copied an orphan, skipped a cut check, or leaked source paths."""
    source = _database_path(initialized_cao_connection)
    result, orphan, artifact_root = _publish_registered_result(
        initialized_cao_connection, tmp_path / "result-store"
    )
    before = _database_state(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / "private-recovery-bundle"

    receipt = _capture_with_work_authority(
        module,
        module.RecoveryCaptureSource(
            database_path=source,
            immutable_result_root=artifact_root,
        ),
        destination,
    )

    assert module.verify_recovery_bundle(receipt) == receipt
    manifest_bytes = (destination / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    assert (
        manifest_bytes
        == json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    )
    assert str(source) not in manifest_bytes.decode()
    assert manifest["profile_version"] == 28
    sqlite_roles = {
        role
        for object_ in manifest["objects"]
        for role in object_["roles"]
        if role.startswith("sqlite-v")
    }
    assert sqlite_roles == {"sqlite-v28"}
    assert {entry["digest"] for entry in manifest["objects"]} >= {result.content_hash}
    assert all(
        entry["path"] == f"objects/{entry['digest']}"
        and entry["size"] >= 0
        and len(entry["digest"]) == 64
        for entry in manifest["objects"]
    )
    assert (
        destination / "objects" / result.content_hash
    ).read_bytes() == b"offline recovery result bytes"
    assert orphan.content_hash not in {entry["digest"] for entry in manifest["objects"]}
    sqlite_object = next(
        object_ for object_ in manifest["objects"] if "sqlite-v28" in object_["roles"]
    )
    sqlite_object["roles"] = ["sqlite-v25"]
    inconsistent_manifest = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    (destination / "manifest.json").write_bytes(inconsistent_manifest)
    inconsistent_receipt = module.RecoveryBundleReceipt(
        destination,
        hashlib.sha256(inconsistent_manifest).hexdigest(),
        len(inconsistent_manifest),
    )
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.verify_recovery_bundle(inconsistent_receipt)
    manifest["format"] = "recovery-bundle-v1"
    manifest.pop("cut_evidence")
    manifest["profile_version"] = 25
    sqlite_object["roles"] = ["sqlite-v25"]
    legacy_manifest = json.dumps(
        manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    (destination / "manifest.json").write_bytes(legacy_manifest)
    legacy_receipt = module.RecoveryBundleReceipt(
        destination,
        hashlib.sha256(legacy_manifest).hexdigest(),
        len(legacy_manifest),
    )
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.verify_recovery_bundle(legacy_receipt)
    assert _database_state(initialized_cao_connection) == before


def test_capture_of_a_real_work_store_requires_v28_cut_evidence_before_publication(
    initialized_cao_connection, tmp_path
):
    """A real offline capture is not a v1 integrity-only bundle after T069."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.work_authority import WorkAuthority

    source = _database_path(initialized_cao_connection)
    module = _recovery_bundle_module()
    authority = WorkAuthority(WorkRepository(source))
    operator = auth._verified_principal(
        "https://recovery.test", "offline-cut-operator", [auth.SCOPE_ADMIN], "jwt"
    )
    lease = authority.create_offline_cut(operator, ttl_seconds=60)
    receipt = module.OfflineRecoveryCapture(authority, lease).capture(
        module.RecoveryCaptureSource(database_path=source),
        tmp_path / "v28-cut-evidence",
    )

    manifest = json.loads((receipt.bundle_path / "manifest.json").read_bytes())
    assert manifest["format"] == "recovery-bundle-v2"
    assert manifest["profile_version"] == 28
    assert set(manifest["cut_evidence"]) == {
        "capture_id",
        "coverage",
        "epoch",
        "expires_at",
        "fence",
        "lease_id",
        "operator_principal_id",
        "owner",
        "phases",
        "revision",
        "scope",
        "store_identity",
        "version",
    }
    evidence = manifest["cut_evidence"]
    assert evidence["owner"] == "work"
    assert evidence["store_identity"] != str(source.resolve())
    assert len(evidence["store_identity"]) == 64
    assert all(
        phase["lease"]["store_identity"] == evidence["store_identity"]
        for phase in evidence["phases"].values()
    )


def test_capture_rejects_an_injected_cut_verifier_instead_of_emitting_a_v1_bundle(
    initialized_cao_connection, tmp_path
):
    """Only the productive Work authority may start a capture."""
    module = _recovery_bundle_module()

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.OfflineRecoveryCapture(object(), object()).capture(
            module.RecoveryCaptureSource(database_path=_database_path(initialized_cao_connection)),
            tmp_path / "untrusted-verifier",
        )


def test_stage_failure_leaves_no_bundle_and_records_only_safe_rejection_evidence(
    initialized_cao_connection, tmp_path, monkeypatch
):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.work_authority import WorkAuthority

    source = _database_path(initialized_cao_connection)
    module = _recovery_bundle_module()
    authority = WorkAuthority(WorkRepository(source))
    operator = auth._verified_principal("https://cut.test", "operator", [auth.SCOPE_ADMIN], "jwt")
    lease = authority.create_offline_cut(operator, ttl_seconds=60)
    monkeypatch.setattr(
        module, "_write_manifest", lambda *args: (_ for _ in ()).throw(OSError("stage"))
    )
    destination = tmp_path / "failed-stage"
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.OfflineRecoveryCapture(authority, lease).capture(
            module.RecoveryCaptureSource(database_path=source), destination
        )
    assert not destination.exists()
    assert initialized_cao_connection.execute(
        "SELECT phase,reason FROM work_offline_cut_rejections"
    ).fetchall() == [("promote", "capture_rejected")]


@pytest.mark.parametrize("change", ["expiry", "revocation", "fence", "registered_writer"])
def test_capture_aborts_when_the_live_cut_changes_after_before_phase(
    initialized_cao_connection, tmp_path, monkeypatch, change
):
    """A durable cut change after preflight never returns a receipt or destination."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.work_authority import WorkAuthority

    source = _database_path(initialized_cao_connection)
    module = _recovery_bundle_module()
    store = WorkRepository(source)
    authority = WorkAuthority(store)
    operator = auth._verified_principal("https://cut.test", "operator", [auth.SCOPE_ADMIN], "jwt")
    writer_id = None
    if change == "registered_writer":
        job = store.create_job(
            project_id="cut-project",
            principal_id=operator.id,
            allowed_providers=["mock_cli"],
            grant_id="root",
        )
        writer_id = store.admit_work(
            job_id=job["id"],
            operation_kind="launch",
            idempotency_key="competing-writer",
            request_hash="b" * 64,
            contract_id="contract",
            snapshot_id="snapshot",
            provider="mock_cli",
            actor_id=operator.id,
        )["attempts"][0]["id"]
    lease = authority.create_offline_cut(operator, ttl_seconds=60)

    capture = module.OfflineRecoveryCapture(authority, lease)
    original_verify = capture._verify_cut

    def verify_then_change(*, database, profile_version, phase):
        verified = original_verify(database=database, profile_version=profile_version, phase=phase)
        if phase == "before":
            if change == "expiry":
                with sqlite3.connect(source) as connection:
                    connection.execute(
                        "UPDATE work_offline_cuts SET expires_at=0 WHERE id=?", (lease.id,)
                    )
            elif change == "revocation":
                authority.revoke_offline_cut(operator, lease)
            elif change == "fence":
                with sqlite3.connect(source) as connection:
                    connection.execute(
                        "UPDATE work_offline_cuts SET fence=fence+1 WHERE id=?", (lease.id,)
                    )
            else:
                with store.transaction() as connection:
                    connection.execute(
                        "INSERT INTO work_registered_writers "
                        "(writer_id,owner,scope,state,revision,registered_at) VALUES (?,?,?,?,?,?)",
                        (writer_id, "work", "registered-work-writers", "active", 1, time.time()),
                    )
        return verified

    monkeypatch.setattr(capture, "_verify_cut", verify_then_change)
    destination = tmp_path / f"cut-{change}-rejected"
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(module.RecoveryCaptureSource(database_path=source), destination)
    assert not destination.exists()
    assert initialized_cao_connection.execute(
        "SELECT lease_id,phase,reason FROM work_offline_cut_rejections"
    ).fetchall() == [(lease.id, "stage", "capture_rejected")]


def test_promotion_failure_after_replace_returns_no_receipt_and_records_rejection(
    initialized_cao_connection, tmp_path, monkeypatch
):
    """A post-replace orphan is never returned as a receipt and is durably rejected."""
    source = _database_path(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / "partial-promotion"
    replace = module.os.replace

    def replace_then_fail(stage, final):
        replace(stage, final)
        raise OSError("simulated fsync failure after promotion")

    monkeypatch.setattr(module.os, "replace", replace_then_fail)
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        _capture_with_work_authority(
            module, module.RecoveryCaptureSource(database_path=source), destination
        )
    assert destination.exists()
    assert initialized_cao_connection.execute(
        "SELECT phase,reason FROM work_offline_cut_rejections"
    ).fetchall() == [("promote", "capture_rejected")]
    manifest_bytes = (destination / "manifest.json").read_bytes()
    orphan_receipt = module.RecoveryBundleReceipt(
        destination, hashlib.sha256(manifest_bytes).hexdigest(), len(manifest_bytes)
    )
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.verify_recovery_bundle(orphan_receipt)


@pytest.mark.parametrize("mutation", ("revocation", "expiry"))
def test_promotion_holds_the_fence_through_replace_and_directory_fsync(
    initialized_cao_connection, tmp_path, monkeypatch, mutation
):
    """A competing revoke/expiry update cannot land after promote but before publication."""
    source = _database_path(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / f"serialized-{mutation}"
    replace = module.os.replace

    def attempt_mutation(stage, final):
        with sqlite3.connect(source, timeout=0) as contender:
            if mutation == "revocation":
                statement = "UPDATE work_offline_cuts SET state='revoked',revision=revision+1 WHERE state='live'"
            else:
                statement = "UPDATE work_offline_cuts SET expires_at=0 WHERE state='live'"
            with pytest.raises(sqlite3.OperationalError):
                contender.execute(statement)
        replace(stage, final)

    monkeypatch.setattr(module.os, "replace", attempt_mutation)
    receipt = _capture_with_work_authority(
        module, module.RecoveryCaptureSource(database_path=source), destination
    )
    assert module.verify_recovery_bundle(receipt) == receipt


def test_publish_blocks_real_revoke_until_publication_and_then_revoke_is_rejected(
    initialized_cao_connection, tmp_path, monkeypatch
):
    """A second authority cannot revoke a live cut between promotion and its publication CAS."""
    from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.work_authority import WorkAuthority

    source = _database_path(initialized_cao_connection)
    module = _recovery_bundle_module()
    operator = auth._verified_principal("https://cut.test", "race", [auth.SCOPE_ADMIN], "jwt")
    authority = WorkAuthority(WorkRepository(source))
    lease = authority.create_offline_cut(operator, ttl_seconds=60)
    revoker = WorkAuthority(WorkRepository(source))
    destination = tmp_path / "publish-revoke-race"
    entered_publish = threading.Event()
    release_publish = threading.Event()
    revoke_started = threading.Event()
    revoke_finished = threading.Event()
    capture_result = {}
    revoke_result = {}
    observed = []
    replace = module.os.replace

    def pause_inside_publish(stage, final):
        observed.append("publish-lock")
        entered_publish.set()
        if not release_publish.wait(timeout=5):
            raise TimeoutError("test did not release publication")
        observed.append("replace")
        replace(stage, final)

    def capture_bundle():
        try:
            capture_result["receipt"] = module.OfflineRecoveryCapture(authority, lease).capture(
                module.RecoveryCaptureSource(database_path=source), destination
            )
        except BaseException as error:  # thread boundary must report every failure to the test
            capture_result["error"] = error

    def revoke_from_second_owner():
        observed.append("revoke-request")
        revoke_started.set()
        try:
            revoker.revoke_offline_cut(operator, lease)
        except BaseException as error:  # the public API must report its published-state rejection
            revoke_result["error"] = error
        finally:
            observed.append("revoke-finished")
            revoke_finished.set()

    monkeypatch.setattr(module.os, "replace", pause_inside_publish)
    capture_thread = threading.Thread(target=capture_bundle, name="capture-publish")
    revoke_thread = threading.Thread(target=revoke_from_second_owner, name="revoke-published")
    try:
        capture_thread.start()
        assert entered_publish.wait(timeout=5)
        revoke_thread.start()
        assert revoke_started.wait(timeout=5)
        assert observed == ["publish-lock", "revoke-request"]
        assert not revoke_finished.wait(timeout=0.2)
    finally:
        release_publish.set()
        capture_thread.join(timeout=5)
        revoke_thread.join(timeout=5)

    assert not capture_thread.is_alive()
    assert not revoke_thread.is_alive()
    assert "error" not in capture_result
    assert isinstance(revoke_result.get("error"), WorkConflict)
    assert observed.index("replace") < observed.index("revoke-finished")
    assert module.verify_recovery_bundle(capture_result["receipt"]) == capture_result["receipt"]


def test_unregistered_repository_mutation_during_stage_is_integrity_only_not_quiescence(
    initialized_cao_connection, tmp_path, monkeypatch
):
    """An allowed unregistered Work mutation cannot upgrade a copy into a quiescence claim."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.work_authority import WorkAuthority

    source = _database_path(initialized_cao_connection)
    module = _recovery_bundle_module()
    store = WorkRepository(source)
    operator = auth._verified_principal(
        "https://cut.test", "unregistered-mutation", [auth.SCOPE_ADMIN], "jwt"
    )
    authority = WorkAuthority(store)
    lease = authority.create_offline_cut(operator, ttl_seconds=60)
    capture = module.OfflineRecoveryCapture(authority, lease)
    copied = module._copy_file_as_object
    changed = []

    def copy_then_mutate(*args, **kwargs):
        result = copied(*args, **kwargs)
        if not changed:
            changed.append(
                store.create_job(
                    project_id="cut-stage-project",
                    principal_id=operator.id,
                    allowed_providers=["mock_cli"],
                    grant_id="cut-stage-grant",
                )
            )
        return result

    monkeypatch.setattr(module, "_copy_file_as_object", copy_then_mutate)
    destination = tmp_path / "unregistered-stage-mutation"
    receipt = capture.capture(module.RecoveryCaptureSource(database_path=source), destination)
    assert changed and changed[0]["id"]
    manifest = json.loads((receipt.bundle_path / "manifest.json").read_bytes())
    coverage = manifest["cut_evidence"]["coverage"]
    assert coverage
    assert {entry["classification"] for entry in coverage} == {"integrity-only"}
    for entry in coverage:
        entry["classification"] = "consistent"
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.verify_recovery_bundle(_recomposed_v2_receipt(module, receipt, manifest))


@pytest.mark.parametrize(
    "mutate",
    (
        lambda evidence: evidence.__setitem__("coverage", []),
        lambda evidence: evidence["coverage"].append(
            {"digest": "f" * 64, "classification": "integrity-only", "roles": []}
        ),
        lambda evidence: evidence.__setitem__("fence", evidence["fence"] + 1),
    ),
)
def test_v2_verifier_rejects_recomputed_receipt_with_unobserved_cut_evidence(
    initialized_cao_connection, tmp_path, mutate
):
    """A self-consistent manifest digest cannot bless absent, foreign, or contradictory evidence."""
    source = _database_path(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / "unobserved-evidence"
    receipt = _capture_with_work_authority(
        module, module.RecoveryCaptureSource(database_path=source), destination
    )
    manifest = json.loads((destination / "manifest.json").read_bytes())
    mutate(manifest["cut_evidence"])
    encoded = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    (destination / "manifest.json").write_bytes(encoded)
    forged = module.RecoveryBundleReceipt(
        destination,
        hashlib.sha256(encoded).hexdigest(),
        len(encoded),
        receipt.source_database,
        receipt.lease_id,
        receipt.publication_revision,
    )
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.verify_recovery_bundle(forged)


@pytest.mark.parametrize("tamper", ("phase_timestamp", "capture_id", "inventory"))
def test_v2_verifier_rejects_recomposed_receipt_with_durable_evidence_tamper(
    initialized_cao_connection, tmp_path, tamper
):
    """Receipt recomputation cannot bless timestamps, captures, or inventories absent from the ledger."""
    source = _database_path(initialized_cao_connection)
    module = _recovery_bundle_module()
    receipt = _capture_with_work_authority(
        module,
        module.RecoveryCaptureSource(database_path=source),
        tmp_path / f"durable-evidence-{tamper}",
    )
    manifest = json.loads((receipt.bundle_path / "manifest.json").read_bytes())
    evidence = manifest["cut_evidence"]
    if tamper == "phase_timestamp":
        evidence["phases"]["before"]["observed_at"] += 1
    elif tamper == "capture_id":
        evidence["capture_id"] = "0" * 32
    else:
        for phase in evidence["phases"].values():
            phase["inventory"]["fingerprint"] = "0" * 64
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.verify_recovery_bundle(_recomposed_v2_receipt(module, receipt, manifest))


def test_v2_verifier_rejects_recomposed_receipt_when_durable_ledger_contradicts_manifest(
    initialized_cao_connection, tmp_path
):
    """A receipt cannot make a tampered server observation equal to its manifest phase."""
    source = _database_path(initialized_cao_connection)
    module = _recovery_bundle_module()
    receipt = _capture_with_work_authority(
        module, module.RecoveryCaptureSource(database_path=source), tmp_path / "ledger-tamper"
    )
    with sqlite3.connect(source) as connection:
        raw = connection.execute(
            "SELECT observation FROM work_offline_cut_observations "
            "WHERE lease_id=? AND phase='after'",
            (receipt.lease_id,),
        ).fetchone()[0]
        contradictory = json.loads(raw)
        contradictory["observed_at"] += 1
        connection.execute(
            "UPDATE work_offline_cut_observations SET observation=? "
            "WHERE lease_id=? AND phase='after'",
            (json.dumps(contradictory, separators=(",", ":"), sort_keys=True), receipt.lease_id),
        )
    manifest = json.loads((receipt.bundle_path / "manifest.json").read_bytes())
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.verify_recovery_bundle(_recomposed_v2_receipt(module, receipt, manifest))


def test_v2_capture_records_phase_observations_and_inventory_before_returning_receipt(
    initialized_cao_connection, tmp_path
):
    """Would fail if v2 named phases instead of recording the observed fenced state."""
    source = _database_path(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / "observed-evidence"
    _capture_with_work_authority(
        module, module.RecoveryCaptureSource(database_path=source), destination
    )
    evidence = json.loads((destination / "manifest.json").read_bytes())["cut_evidence"]
    assert set(evidence["phases"]) == {"before", "after", "promote"}
    for phase in evidence["phases"].values():
        assert phase["writer_count"] == 0
        assert phase["inventory"]["profile_version"] == 28
        assert phase["lease"]["store_identity"] != str(source.resolve())
        assert phase["lease"]["store_identity"] == evidence["store_identity"]


def test_restart_revalidates_the_durable_cut_before_capture(initialized_cao_connection, tmp_path):
    """A new server owner reads the persisted lease rather than a process-local grant."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.work_authority import WorkAuthority

    source = _database_path(initialized_cao_connection)
    operator = auth._verified_principal("https://cut.test", "restart", [auth.SCOPE_ADMIN], "jwt")
    lease = WorkAuthority(WorkRepository(source)).create_offline_cut(operator, ttl_seconds=60)
    restarted = WorkAuthority(WorkRepository(source))
    assert restarted.verify_offline_cut(lease, source_database=source) == lease
    restarted.revoke_offline_cut(operator, lease)

    destination = tmp_path / "restart-revoked"
    with pytest.raises(
        importlib.import_module(
            "cli_agent_orchestrator.services.recovery_bundle"
        ).RecoveryBundleError
    ):
        _recovery_bundle_module().OfflineRecoveryCapture(restarted, lease).capture(
            _recovery_bundle_module().RecoveryCaptureSource(database_path=source), destination
        )
    assert not destination.exists()


def test_historic_v25_bundle_verifies_bytes_and_references_without_publication_semantics(tmp_path):
    """A genuine pre-v26 SQLite profile remains a v1 integrity-only verification path."""
    module = _recovery_bundle_module()
    historical = tmp_path / "historic-v25.sqlite3"
    _historical_v25_sqlite(historical)
    with sqlite3.connect(historical) as connection:
        assert connection.execute("SELECT max(version) FROM work_migrations").fetchone() == (25,)
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_offline_cuts'"
            ).fetchone()
            is None
        )

    bundle = tmp_path / "historic-v1-bundle"
    objects = bundle / "objects"
    bundle.mkdir(mode=0o700)
    objects.mkdir(mode=0o700)
    payload = historical.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    (objects / digest).write_bytes(payload)
    (objects / digest).chmod(0o600)
    manifest_digest, manifest_size = module._write_manifest(
        bundle,
        [
            {
                "digest": digest,
                "path": f"objects/{digest}",
                "roles": ["sqlite-v25"],
                "size": len(payload),
            }
        ],
        [],
        profile_version=25,
    )
    receipt = module.RecoveryBundleReceipt(bundle, manifest_digest, manifest_size)

    assert module.verify_recovery_bundle(receipt) == receipt
    assert receipt.source_database is receipt.lease_id is receipt.publication_revision is None


def test_historic_v1_is_verify_only_and_malformed_or_mixed_v2_fails_closed(
    initialized_cao_connection, tmp_path
):
    """Only historic v1 profiles verify; v2 requires exactly the current fenced profile."""
    source = _database_path(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / "version-boundary"
    receipt = _capture_with_work_authority(
        module, module.RecoveryCaptureSource(database_path=source), destination
    )
    manifest = json.loads((destination / "manifest.json").read_bytes())

    historic = dict(manifest)
    historic.pop("cut_evidence")
    historic["format"] = "recovery-bundle-v1"
    historic["profile_version"] = 24
    historic["objects"] = [dict(item) for item in manifest["objects"]]
    next(item for item in historic["objects"] if "sqlite-v28" in item["roles"])["roles"] = [
        "sqlite-v24"
    ]
    historic_bytes = json.dumps(historic, separators=(",", ":"), sort_keys=True).encode()
    (destination / "manifest.json").write_bytes(historic_bytes)
    historic_receipt = module.RecoveryBundleReceipt(
        destination, hashlib.sha256(historic_bytes).hexdigest(), len(historic_bytes)
    )
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.verify_recovery_bundle(historic_receipt)

    for mutation in (
        lambda value: value.__setitem__("profile_version", 25),
        lambda value: value.__setitem__("profile_version", 29),
        lambda value: value["cut_evidence"].pop("fence"),
    ):
        invalid = json.loads(json.dumps(manifest))
        mutation(invalid)
        invalid_bytes = json.dumps(invalid, separators=(",", ":"), sort_keys=True).encode()
        (destination / "manifest.json").write_bytes(invalid_bytes)
        invalid_receipt = module.RecoveryBundleReceipt(
            destination, hashlib.sha256(invalid_bytes).hexdigest(), len(invalid_bytes)
        )
        with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
            module.verify_recovery_bundle(invalid_receipt)


@pytest.mark.parametrize("tamper", ["object", "manifest"])
def test_verify_rejects_any_tampered_bundle_member(initialized_cao_connection, tmp_path, tamper):
    """Would fail if verification trusted manifest claims rather than bytes on disk."""
    source = _database_path(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / f"tampered-{tamper}"
    receipt = _capture_with_work_authority(
        module, module.RecoveryCaptureSource(database_path=source), destination
    )
    if tamper == "object":
        object_path = next((destination / "objects").iterdir())
        object_path.write_bytes(b"tampered SQLite copy")
    else:
        (destination / "manifest.json").write_bytes(b'{"format":"tampered"}')

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.verify_recovery_bundle(receipt)


def test_capture_rejects_missing_declared_artifact_without_publishing_or_mutating_source(
    initialized_cao_connection, tmp_path
):
    """Would fail if a hash reference authorized a missing byte object or partial output."""
    source = _database_path(initialized_cao_connection)
    result, _orphan, artifact_root = _publish_registered_result(
        initialized_cao_connection, tmp_path / "missing-result-store"
    )
    (artifact_root / result.content_hash).unlink()
    before = _database_state(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / "must-not-publish"

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        _capture_with_work_authority(
            module,
            module.RecoveryCaptureSource(
                database_path=source,
                immutable_result_root=artifact_root,
            ),
            destination,
        )

    assert not destination.exists()
    assert _database_state(initialized_cao_connection) == before


def test_capture_rejects_credential_material_without_echo_or_publication(
    initialized_cao_connection, tmp_path
):
    """Would fail if opaque SQLite text bypassed the existing credential policy."""
    source = _database_path(initialized_cao_connection)
    secret = "api_key=0123456789abcdef"
    initialized_cao_connection.execute(
        "INSERT INTO flows VALUES (?,?,?,?,?,?,?,?,?)",
        ("credential-flow", "relative-flow.yaml", "", "safe", "safe", secret, None, None, False),
    )
    initialized_cao_connection.commit()
    before = _database_state(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / "credential-rejection"

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected") as error:
        _capture_with_work_authority(
            module, module.RecoveryCaptureSource(database_path=source), destination
        )

    assert secret not in str(error.value)
    assert not destination.exists()
    assert _database_state(initialized_cao_connection) == before


def test_capture_rejects_secret_in_a_real_referenced_result_without_publication(
    initialized_cao_connection, tmp_path
):
    """Would fail if content-addressing admitted credentials into published objects."""
    source = _database_path(initialized_cao_connection)
    secret = "api_key=0123456789abcdef"
    _result, _orphan, artifact_root = _publish_registered_result(
        initialized_cao_connection,
        tmp_path / "secret-result-store",
        content=secret.encode("utf-8"),
    )
    before = _database_state(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / "secret-result-rejection"

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected") as error:
        _capture_with_work_authority(
            module,
            module.RecoveryCaptureSource(
                database_path=source,
                immutable_result_root=artifact_root,
            ),
            destination,
        )

    assert secret not in str(error.value)
    assert not destination.exists()
    assert _database_state(initialized_cao_connection) == before


def test_capture_rejects_utf8_control_bytes_in_a_real_referenced_result_without_publication(
    initialized_cao_connection, tmp_path
):
    """Would fail if UTF-8-decodable binary objects crossed the capture boundary."""
    source = _database_path(initialized_cao_connection)
    _result, _orphan, artifact_root = _publish_registered_result(
        initialized_cao_connection,
        tmp_path / "binary-result-store",
        content=b"\x00\x01\x02\x03",
    )
    before = _database_state(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / "binary-result-rejection"

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        _capture_with_work_authority(
            module,
            module.RecoveryCaptureSource(
                database_path=source,
                immutable_result_root=artifact_root,
            ),
            destination,
        )

    assert not destination.exists()
    assert _database_state(initialized_cao_connection) == before


def _freeze_real_snapshot(connection: sqlite3.Connection, root: Path, content: str):
    """Freeze text through the production snapshot authority and SQLite producer."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.delegation_snapshot import (
        DelegationSnapshots,
        ResolvedSnapshot,
    )
    from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
    from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority

    repository = WorkRepository(_database_path(connection))
    principal = auth._verified_principal(
        "https://issuer.test",
        "recovery-snapshot-owner",
        [auth.SCOPE_ADMIN],
        "jwt",
    )
    job = repository.create_job(
        project_id="recovery-snapshot-project",
        principal_id=principal.id,
        allowed_providers=["mock_cli"],
        grant_id="recovery-snapshot-grant",
    )
    grant = WorkAuthority(repository).issue_root(
        principal,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}, paths={str(root)}),
        expires_at=time.time() + 60,
    )
    return DelegationSnapshots(
        repository,
        policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision),
    ).freeze(
        principal=principal,
        job_id=job["id"],
        contract_id="recovery-snapshot-contract",
        binding_key="recovery-snapshot-binding",
        request_hash="b" * 64,
        scope="project",
        scope_id=job["project_id"],
        resolver=lambda _connection, _principal: ResolvedSnapshot(content),
    )


def test_capture_accepts_a_real_utf8_delegation_snapshot(initialized_cao_connection, tmp_path):
    """Would fail if recovery rejected the producer's textual snapshot BLOB."""
    source = _database_path(initialized_cao_connection)
    snapshot = _freeze_real_snapshot(
        initialized_cao_connection,
        tmp_path,
        "contexto durable limpio: π\nsegunda línea\r\ttercera columna",
    )
    before = _database_state(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / "snapshot-capture"

    receipt = _capture_with_work_authority(
        module,
        module.RecoveryCaptureSource(database_path=source),
        destination,
    )

    assert module.verify_recovery_bundle(receipt) == receipt
    database_object = _captured_sqlite_object(destination)
    with sqlite3.connect(database_object.as_uri() + "?mode=ro", uri=True) as captured:
        assert (
            captured.execute(
                "SELECT content FROM work_delegation_snapshots WHERE id=?", (snapshot.id,)
            ).fetchone()[0]
            == snapshot.content
        )
    assert _database_state(initialized_cao_connection) == before


def _captured_sqlite_object(destination: Path) -> Path:
    """Find the one captured database object without trusting its manifest position."""
    objects = [
        entry
        for entry in (destination / "objects").iterdir()
        if entry.read_bytes().startswith(b"SQLite format 3")
    ]
    assert len(objects) == 1
    return objects[0]


def test_capture_includes_a_real_memory_content_object_with_a_private_reference(
    initialized_cao_connection, tmp_path
):
    """Would fail if a live memory producer remained an unrepresented file path."""
    from cli_agent_orchestrator.services.memory_service import MemoryService

    source = _database_path(initialized_cao_connection)
    memory_root = tmp_path / "memory-root"
    memory_engine = create_engine(f"sqlite:///{source}")
    try:
        asyncio.run(
            MemoryService(base_dir=memory_root, db_engine=memory_engine).store(
                "memoria durable recuperable",
                scope="global",
                memory_type="project",
                key="recovery-memory",
            )
        )
    finally:
        memory_engine.dispose()
    memory_id, memory_path = initialized_cao_connection.execute(
        "SELECT id,file_path FROM memory_metadata WHERE key='recovery-memory'"
    ).fetchone()
    before = _database_state(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / "memory-capture"

    receipt = _capture_with_work_authority(
        module,
        module.RecoveryCaptureSource(database_path=source, memory_root=memory_root),
        destination,
    )

    assert module.verify_recovery_bundle(receipt) == receipt
    manifest = json.loads((destination / "manifest.json").read_bytes())
    reference = next(
        item
        for item in manifest["references"]
        if item["role"] == "memory-content" and item["identifier"] == memory_id
    )
    assert (destination / reference["path"]).read_bytes() == Path(memory_path).read_bytes()
    assert str(memory_root) not in (destination / "manifest.json").read_text()
    assert _database_state(initialized_cao_connection) == before


def test_capture_rejects_real_memory_content_through_an_intermediate_symlink(
    initialized_cao_connection, tmp_path
):
    """A lexical memory path must not escape through an in-root directory link."""
    from cli_agent_orchestrator.services.memory_service import MemoryService

    source = _database_path(initialized_cao_connection)
    memory_root = tmp_path / "memory-root"
    memory_engine = create_engine(f"sqlite:///{source}")
    try:
        asyncio.run(
            MemoryService(base_dir=memory_root, db_engine=memory_engine).store(
                "memoria durable recuperable",
                scope="global",
                memory_type="project",
                key="recovery-memory-symlink",
            )
        )
    finally:
        memory_engine.dispose()
    memory_path = Path(
        initialized_cao_connection.execute(
            "SELECT file_path FROM memory_metadata WHERE key='recovery-memory-symlink'"
        ).fetchone()[0]
    )
    relative_path = memory_path.relative_to(memory_root)
    intermediate = memory_root / relative_path.parts[0]
    outside_intermediate = tmp_path / "outside-memory-root"
    outside_path = outside_intermediate.joinpath(*relative_path.parts[1:])
    outside_path.parent.mkdir(parents=True)
    outside_path.write_bytes(memory_path.read_bytes())
    shutil.rmtree(intermediate)
    intermediate.symlink_to(outside_intermediate, target_is_directory=True)
    before = _database_state(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / "memory-symlink-capture"

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        _capture_with_work_authority(
            module,
            module.RecoveryCaptureSource(database_path=source, memory_root=memory_root),
            destination,
        )

    assert not destination.exists()
    assert _database_state(initialized_cao_connection) == before


def test_capture_compacts_deleted_sqlite_secret_before_hashing(
    initialized_cao_connection, tmp_path
):
    """Would fail if the published SQLite object retained deleted credential bytes."""
    source = _database_path(initialized_cao_connection)
    secret = "api_key=" + "a" * 100_000
    deleted_bytes = ("api_key=" + "a" * 1_000).encode("utf-8")
    initialized_cao_connection.execute(
        "INSERT INTO flows VALUES (?,?,?,?,?,?,?,?,?)",
        ("deleted-secret", "flow.yaml", "", "safe", "safe", secret, None, None, False),
    )
    initialized_cao_connection.commit()
    if deleted_bytes not in source.read_bytes():
        pytest.fail("fixture did not retain its deleted credential bytes")
    initialized_cao_connection.execute("DELETE FROM flows WHERE name='deleted-secret'")
    initialized_cao_connection.commit()
    if deleted_bytes not in source.read_bytes():
        pytest.fail("fixture did not retain deleted credential bytes")
    before = _database_state(initialized_cao_connection)
    inventory_module = _inventory_module(initialized_cao_connection)
    source_inventory = inventory_module.inspect_work_store(initialized_cao_connection)
    source_identity = inventory_module.verified_inbox_store_identity(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / "compacted-capture"

    receipt = _capture_with_work_authority(
        module,
        module.RecoveryCaptureSource(database_path=source),
        destination,
    )

    assert module.verify_recovery_bundle(receipt) == receipt
    database_object = _captured_sqlite_object(destination)
    if deleted_bytes in database_object.read_bytes():
        pytest.fail("published SQLite object retained deleted credential bytes")
    with sqlite3.connect(database_object.as_uri() + "?mode=ro", uri=True) as captured:
        assert (
            inventory_module.inspect_portable_work_store(
                captured,
                expected_source_identity=source_identity,
            )
            == source_inventory
        )
    assert _database_state(initialized_cao_connection) == before


def test_capture_rejects_non_utf8_delegation_snapshot_blob(initialized_cao_connection, tmp_path):
    """Would fail if accepting textual snapshots widened capture to arbitrary BLOBs."""
    source = _database_path(initialized_cao_connection)
    snapshot = _freeze_real_snapshot(initialized_cao_connection, tmp_path, "texto inicial limpio")
    trigger = initialized_cao_connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='work_delegation_snapshots_immutable_update'"
    ).fetchone()[0]
    initialized_cao_connection.execute("DROP TRIGGER work_delegation_snapshots_immutable_update")
    initialized_cao_connection.execute(
        "UPDATE work_delegation_snapshots SET content=? WHERE id=?", (b"\xff", snapshot.id)
    )
    initialized_cao_connection.execute(trigger)
    initialized_cao_connection.commit()
    before = _database_state(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / "non-utf8-snapshot-rejection"

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        _capture_with_work_authority(
            module,
            module.RecoveryCaptureSource(database_path=source),
            destination,
        )

    assert not destination.exists()
    assert _database_state(initialized_cao_connection) == before


@pytest.mark.parametrize("content", [b"\x00", b"\x01", b"\x7f"])
def test_capture_rejects_utf8_control_delegation_snapshot_blobs(
    initialized_cao_connection, tmp_path, content
):
    """Would fail if textual snapshot admission accepted control-byte binary BLOBs."""
    source = _database_path(initialized_cao_connection)
    snapshot = _freeze_real_snapshot(initialized_cao_connection, tmp_path, "texto inicial limpio")
    trigger = initialized_cao_connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='work_delegation_snapshots_immutable_update'"
    ).fetchone()[0]
    initialized_cao_connection.execute("DROP TRIGGER work_delegation_snapshots_immutable_update")
    initialized_cao_connection.execute(
        "UPDATE work_delegation_snapshots SET content=? WHERE id=?", (content, snapshot.id)
    )
    initialized_cao_connection.execute(trigger)
    initialized_cao_connection.commit()
    before = _database_state(initialized_cao_connection)
    module = _recovery_bundle_module()
    destination = tmp_path / "control-snapshot-rejection"

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        _capture_with_work_authority(
            module, module.RecoveryCaptureSource(database_path=source), destination
        )

    assert not destination.exists()
    assert _database_state(initialized_cao_connection) == before


def _portable_copy(connection: sqlite3.Connection, tmp_path: Path) -> Path:
    """Use SQLite's real backup API; the copied inbox identity intentionally names source."""
    copy = tmp_path / "portable-v24-copy.sqlite3"
    with sqlite3.connect(copy) as destination:
        connection.backup(destination)
    return copy


def test_portable_inventory_accepts_a_closed_copy_without_making_it_executable(
    v24_connection, tmp_path
):
    """Would fail if portable capture adopted the staging path or weakened execution fencing."""
    module = _inventory_module(v24_connection)
    before = _database_state(v24_connection)
    source_inventory = module.inspect_work_store(v24_connection)
    source_identity = module.verified_inbox_store_identity(v24_connection)
    copy = _portable_copy(v24_connection, tmp_path)
    copied_connection = sqlite3.connect(copy)
    try:
        assert (
            module.inspect_portable_work_store(
                copied_connection, expected_source_identity=source_identity
            )
            == source_inventory
        )
        from cli_agent_orchestrator.clients.work_repository import SchemaMismatch, WorkRepository

        with pytest.raises(SchemaMismatch, match="managed inbox store context names another file"):
            WorkRepository._verify(copied_connection, version=module._WORK_SCHEMA_VERSION)
    finally:
        copied_connection.close()
    assert _database_state(v24_connection) == before


def test_portable_inventory_rejects_a_copy_with_required_work_index_removed(
    v24_connection, tmp_path
):
    """Would fail if a staging scan checked rows but not the exact v24 DDL profile."""
    module = _inventory_module(v24_connection)
    source_identity = module.verified_inbox_store_identity(v24_connection)
    copy = _portable_copy(v24_connection, tmp_path)
    copied_connection = sqlite3.connect(copy)
    try:
        copied_connection.execute("DROP INDEX work_items_idempotency")
        with pytest.raises(module.RecoveryInventoryError, match="recovery inventory incompatible"):
            module.inspect_portable_work_store(
                copied_connection, expected_source_identity=source_identity
            )
    finally:
        copied_connection.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("store_identity", "different-copy.sqlite3"),
        ("store_uuid", "a" * 32),
    ],
)
def test_portable_inventory_rejects_any_inbox_identity_pair_not_verified_at_source(
    v24_connection, tmp_path, column, value
):
    """Would fail if an arbitrary persisted path or UUID could authorize the copied profile."""
    module = _inventory_module(v24_connection)
    source_identity = module.verified_inbox_store_identity(v24_connection)
    copy = _portable_copy(v24_connection, tmp_path)
    copied_connection = sqlite3.connect(copy)
    try:
        trigger = copied_connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='work_inbox_store_context_immutable_update'"
        ).fetchone()[0]
        copied_connection.execute("DROP TRIGGER work_inbox_store_context_immutable_update")
        copied_connection.execute(f"UPDATE work_inbox_store_context SET {column}=?", (value,))
        copied_connection.execute(trigger)
        with pytest.raises(module.RecoveryInventoryError, match="recovery inventory incompatible"):
            module.inspect_portable_work_store(
                copied_connection, expected_source_identity=source_identity
            )
    finally:
        copied_connection.close()


@pytest.mark.parametrize("damage", ["ledger", "recovery_context", "foreign_key"])
def test_portable_inventory_rejects_durable_profile_corruption(v24_connection, tmp_path, damage):
    """Would fail if portable validation omitted a v24 verifier boundary beyond DDL."""
    module = _inventory_module(v24_connection)
    source_identity = module.verified_inbox_store_identity(v24_connection)
    copy = _portable_copy(v24_connection, tmp_path)
    copied_connection = sqlite3.connect(copy)
    try:
        if damage == "ledger":
            copied_connection.execute(
                "UPDATE work_migrations SET checksum='broken' WHERE version=24"
            )
        elif damage == "recovery_context":
            trigger = copied_connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='work_recovery_context_transition'"
            ).fetchone()[0]
            copied_connection.execute("DROP TRIGGER work_recovery_context_transition")
            copied_connection.execute("PRAGMA ignore_check_constraints=ON")
            copied_connection.execute("UPDATE work_recovery_context SET context_version=2")
            copied_connection.execute("PRAGMA ignore_check_constraints=OFF")
            copied_connection.execute(trigger)
        else:
            copied_connection.execute("PRAGMA foreign_keys=OFF")
            copied_connection.execute(
                "INSERT INTO work_results "
                "(id,attempt_id,content_hash,immutable_location,byte_length,validation_state,"
                "validator_id,validation_evidence,created_at) "
                "VALUES ('bad-result','missing-attempt','a','a',0,'verified','validator','{}',0)"
            )
            copied_connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(module.RecoveryInventoryError, match="recovery inventory incompatible"):
            module.inspect_portable_work_store(
                copied_connection, expected_source_identity=source_identity
            )
    finally:
        copied_connection.close()


def _mark_recovery_attempt_sent(connection: sqlite3.Connection) -> str:
    """Create durable in-flight intent without invoking a delivery provider."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.services.work_reducer import TransitionEvidence

    attempt_id, generation, revision = connection.execute(
        "SELECT id,generation,revision FROM work_attempts"
    ).fetchone()
    work = WorkRepository(_database_path(connection)).transition_attempt(
        attempt_id=attempt_id,
        generation=generation,
        expected_revision=revision,
        expected_state="planned",
        target="sent",
        actor_id="recovery-operator",
        event_id="recovery-capture-sent-attempt",
        evidence=TransitionEvidence(
            generation=generation,
            expected_generation=generation,
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
    )

    assert work["attempts"][-1]["state"] == "sent"
    return attempt_id


def test_restore_stages_bundle_without_touching_operator_store_or_relaunching_active_attempt(
    initialized_cao_connection, monkeypatch, tmp_path
):
    """Would fail if restore used a live store or replayed an uncertain delivery intent."""
    from cli_agent_orchestrator import constants

    source = _database_path(initialized_cao_connection)
    _result, _orphan, artifact_root = _publish_registered_result(
        initialized_cao_connection, tmp_path / "restore-result-store"
    )
    active_attempt_id = _mark_recovery_attempt_sent(initialized_cao_connection)
    module = _recovery_bundle_module()
    bundle = _capture_with_work_authority(
        module,
        module.RecoveryCaptureSource(
            database_path=source,
            immutable_result_root=artifact_root,
        ),
        tmp_path / "restore-bundle",
    )
    source_before = _database_state(initialized_cao_connection)
    source_bytes_before = source.read_bytes()
    operator_database = tmp_path / "operator.sqlite3"
    with sqlite3.connect(operator_database) as operator_connection:
        operator_connection.execute("CREATE TABLE operator_guard (value TEXT NOT NULL)")
        operator_connection.execute("INSERT INTO operator_guard VALUES ('must remain untouched')")
    operator_bytes_before = operator_database.read_bytes()
    monkeypatch.setattr(constants, "DATABASE_FILE", operator_database)
    restored_database = tmp_path / "isolated-restored.sqlite3"

    module.restore_recovery_bundle(bundle, restored_database)

    with sqlite3.connect(restored_database.as_uri() + "?mode=ro", uri=True) as restored:
        assert restored.execute("SELECT id,state FROM work_attempts ORDER BY id").fetchall() == [
            (active_attempt_id, "reconcile")
        ]
    assert _database_state(initialized_cao_connection) == source_before
    assert source.read_bytes() == source_bytes_before
    assert operator_database.read_bytes() == operator_bytes_before


def test_restore_stages_a_v2_bundle_containing_memory_content(initialized_cao_connection, tmp_path):
    """Would fail if the exact memory-reference closure rejected a valid memory object."""
    from cli_agent_orchestrator.services.memory_service import MemoryService

    source = _database_path(initialized_cao_connection)
    memory_root = tmp_path / "restore-memory-root"
    memory_engine = create_engine(f"sqlite:///{source}")
    try:
        asyncio.run(
            MemoryService(base_dir=memory_root, db_engine=memory_engine).store(
                "memoria restaurable",
                scope="global",
                memory_type="project",
                key="restore-memory",
            )
        )
    finally:
        memory_engine.dispose()
    module = _recovery_bundle_module()
    receipt = _capture_with_work_authority(
        module,
        module.RecoveryCaptureSource(database_path=source, memory_root=memory_root),
        tmp_path / "restore-memory-bundle",
    )
    source_before = source.read_bytes()
    destination = tmp_path / "memory-restored.sqlite3"

    module.restore_recovery_bundle(receipt, destination)

    assert source.read_bytes() == source_before
    with sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True) as connection:
        assert connection.execute(
            "SELECT execution_state FROM work_recovery_context"
        ).fetchone() == ("blocked_restore",)


def test_restore_rejects_an_absent_configured_operator_database_destination(
    initialized_cao_connection, monkeypatch, tmp_path
):
    """Would fail if restore could create a portable copy at the configured operator path."""
    from cli_agent_orchestrator import constants

    module, source, receipt = _restore_v2_fixture(initialized_cao_connection, tmp_path)
    source_before = source.read_bytes()
    destination = tmp_path / "configured-operator.sqlite3"
    assert not destination.exists()
    monkeypatch.setattr(constants, "DATABASE_FILE", destination)

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.restore_recovery_bundle(receipt, destination)

    assert source.read_bytes() == source_before
    assert not destination.exists()


def _restore_v2_fixture(initialized_cao_connection, tmp_path):
    """Capture one real v2 bundle whose source and destination remain distinct."""
    source = _database_path(initialized_cao_connection)
    _result, _orphan, artifact_root = _publish_registered_result(
        initialized_cao_connection, tmp_path / "restore-fixture-results"
    )
    module = _recovery_bundle_module()
    receipt = _capture_with_work_authority(
        module,
        module.RecoveryCaptureSource(
            database_path=source,
            immutable_result_root=artifact_root,
        ),
        tmp_path / "restore-fixture-bundle",
    )
    return module, source, receipt


def _rebind_published_manifest(source: Path, receipt, manifest, module):
    """Prepare a validly published-but-logically-incomplete fixture before restore."""
    receipt = _recomposed_v2_receipt(module, receipt, manifest)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "UPDATE work_offline_cuts SET published_manifest_digest=?,published_manifest_size=? "
            "WHERE id=?",
            (receipt.manifest_digest, receipt.manifest_size, receipt.lease_id),
        )
    assert module.verify_recovery_bundle(receipt) == receipt
    return receipt


def test_restore_rejects_a_published_reference_not_backed_by_sqlite(
    initialized_cao_connection, tmp_path
):
    """Would fail if restore trusted manifest hashes without closing them against SQLite rows."""
    module, source, receipt = _restore_v2_fixture(initialized_cao_connection, tmp_path)
    manifest = json.loads((receipt.bundle_path / "manifest.json").read_bytes())
    reference = next(item for item in manifest["references"] if item["role"] == "result-content")
    manifest["references"].append({**reference, "identifier": "unexpected-result"})
    manifest["references"].sort(key=lambda item: (item["role"], item["identifier"], item["digest"]))
    receipt = _rebind_published_manifest(source, receipt, manifest, module)
    source_before = source.read_bytes()
    destination = tmp_path / "reference-closure.sqlite3"

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.restore_recovery_bundle(receipt, destination)

    assert source.read_bytes() == source_before
    assert not destination.exists()


@pytest.mark.parametrize("fault", ["missing", "extra", "wrong-role"])
def test_restore_rejects_executable_reference_mismatch_before_publication(
    initialized_cao_connection, tmp_path, fault
):
    """A recomposed manifest cannot omit, invent, or relabel a V29 SQLite reference."""
    module = _recovery_bundle_module()
    source = _database_path(initialized_cao_connection)
    root = tmp_path / "source.sqlite3.executable-content"
    digest, _content = _register_executable_bytes(initialized_cao_connection, root)
    receipt = _capture_with_work_authority(
        module,
        module.RecoveryCaptureSource(database_path=source, executable_content_root=root),
        tmp_path / "executable-reference-bundle",
    )
    manifest = json.loads((receipt.bundle_path / "manifest.json").read_bytes())
    reference = next(
        item for item in manifest["references"] if item["role"] == "executable-content"
    )
    if fault == "missing":
        manifest["references"].remove(reference)
    elif fault == "extra":
        manifest["references"].append({**reference, "identifier": "unexpected-executable"})
    else:
        reference["role"] = "result-content"
        object_ = next(item for item in manifest["objects"] if item["digest"] == digest)
        object_["roles"] = ["result-content"]
        coverage = next(
            item for item in manifest["cut_evidence"]["coverage"] if item["digest"] == digest
        )
        coverage["roles"] = ["result-content"]
    manifest["references"].sort(key=lambda item: (item["role"], item["identifier"], item["digest"]))
    receipt = _rebind_published_manifest(source, receipt, manifest, module)
    destination = tmp_path / "rejected-restore.sqlite3"

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.restore_recovery_bundle(receipt, destination)
    assert not destination.exists()


def test_restore_rejects_historical_v1_bundle_without_creating_destination(tmp_path):
    """Would fail if a restore silently accepted a profile only eligible for historical verification."""
    module = _recovery_bundle_module()
    database = tmp_path / "historical-v25.sqlite3"
    _historical_v25_sqlite(database)
    digest = hashlib.sha256(database.read_bytes()).hexdigest()
    size = database.stat().st_size
    bundle = tmp_path / "historical-v1-bundle"
    objects = bundle / "objects"
    objects.mkdir(parents=True, mode=0o700)
    shutil.copyfile(database, objects / digest)
    bundle.chmod(0o700)
    (objects / digest).chmod(0o600)
    manifest = {
        "format": "recovery-bundle-v1",
        "objects": [
            {
                "digest": digest,
                "path": f"objects/{digest}",
                "roles": ["sqlite-v25"],
                "size": size,
            }
        ],
        "profile_version": 25,
        "references": [],
    }
    encoded = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    manifest_path = bundle / "manifest.json"
    manifest_path.write_bytes(encoded)
    manifest_path.chmod(0o600)
    receipt = module.RecoveryBundleReceipt(
        bundle, hashlib.sha256(encoded).hexdigest(), len(encoded)
    )
    assert module.verify_recovery_bundle(receipt) == receipt
    destination = tmp_path / "historical-v1-restore.sqlite3"

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.restore_recovery_bundle(receipt, destination)

    assert not destination.exists()


def test_restore_does_not_clobber_a_destination_created_during_publication(
    initialized_cao_connection, monkeypatch, tmp_path
):
    """Would fail if publication replaced a competitor after the destination precheck."""
    module, source, receipt = _restore_v2_fixture(initialized_cao_connection, tmp_path)
    source_before = source.read_bytes()
    destination = tmp_path / "competing-destination.sqlite3"
    link = module.os.link

    def create_competitor_then_link(staging, target, *, follow_symlinks):
        destination.write_bytes(b"competitor bytes must survive")
        return link(staging, target, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(module.os, "link", create_competitor_then_link)

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.restore_recovery_bundle(receipt, destination)

    assert source.read_bytes() == source_before
    assert destination.read_bytes() == b"competitor bytes must survive"


def test_restore_keeps_a_blocked_destination_when_directory_fsync_fails_after_link(
    initialized_cao_connection, monkeypatch, tmp_path
):
    """Would fail if an uncertain post-link publication reported success or erased its only safe copy."""
    module, source, receipt = _restore_v2_fixture(initialized_cao_connection, tmp_path)
    source_before = source.read_bytes()
    destination = tmp_path / "post-link-fsync.sqlite3"
    fsync_directory = module._fsync_directory

    def fail_only_after_publication(path):
        if path == destination.parent and destination.exists():
            raise OSError("directory durability is uncertain")
        return fsync_directory(path)

    monkeypatch.setattr(module, "_fsync_directory", fail_only_after_publication)

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.restore_recovery_bundle(receipt, destination)

    assert source.read_bytes() == source_before
    with sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True) as connection:
        assert connection.execute(
            "SELECT execution_state FROM work_recovery_context"
        ).fetchone() == ("blocked_restore",)


def test_restore_discards_staging_when_t095_rejects(
    initialized_cao_connection, monkeypatch, tmp_path
):
    """Would fail if a failed staged block could publish a normal or partial copy."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    module, source, receipt = _restore_v2_fixture(initialized_cao_connection, tmp_path)
    source_before = source.read_bytes()
    destination = tmp_path / "t095-rejected.sqlite3"

    def reject_staged_block(*_args, **_kwargs):
        raise module.RecoveryBundleError("injected T095 rejection")

    monkeypatch.setattr(WorkRepository, "_block_portable_restore_copy", reject_staged_block)

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.restore_recovery_bundle(receipt, destination)

    assert source.read_bytes() == source_before
    assert not destination.exists()
    assert not list(tmp_path.glob(".recovery-restore-*"))
