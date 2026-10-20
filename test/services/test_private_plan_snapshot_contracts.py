"""Executable bytes remain private, verified and transactionally reference-counted."""

import dataclasses
import os
from contextlib import closing

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.postgres import DatabaseError, connect
from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
from cli_agent_orchestrator.clients.private_plan_postgres_schema import PRIVATE_PLAN_MIGRATIONS
from cli_agent_orchestrator.clients.workflows_postgres_schema import WORKFLOW_MIGRATIONS
from cli_agent_orchestrator.services import plan_identifier as pi
from cli_agent_orchestrator.services import private_plan_snapshot as snapshots

pytestmark = pytest.mark.postgres


def _material_set(source=b"#!/bin/sh\nprintf 'private'\n", memory=None):
    materials = {
        "artifact_hash": source,
        "declaration": snapshots.structured_material_bytes({"name": "private-workflow"}),
        "targets": snapshots.structured_material_bytes(["worker"]),
        "limits": snapshots.structured_material_bytes({"steps": 2}),
        "retry_policy": snapshots.structured_material_bytes({"attempts": 1}),
        "policy": snapshots.structured_material_bytes({"write": False}),
        "memory": snapshots.structured_material_bytes(memory or {"content": "private memory"}),
    }
    key = pi.canonical_key_bytes("private-input")
    value = pi.canonical_component_bytes({"credential": "private-value", "count": 3})
    key_digest = pi.digest_bytes(key)
    materials[f"input-key:{key_digest}"] = key
    materials[f"input-value:{key_digest}"] = value
    components = pi.PlanV2Components(
        tier="script",
        inputs=(pi.InputDigest(key_digest, pi.digest_bytes(value)),),
        **{name: pi.digest_bytes(materials[name]) for name in snapshots.COMPONENT_MATERIAL_KINDS},
    )
    return components, materials


@pytest.fixture
def private_store(postgres_targets):
    target = postgres_targets.main
    migrate_component(target, WORKFLOW_MIGRATIONS)
    migrate_component(target, PRIVATE_PLAN_MIGRATIONS)
    with connect(target) as conn:
        conn.executemany(
            "INSERT INTO workflow_run(run_id,workflow_name,spec_snapshot,inputs_json,state,started_at,tier) VALUES (%s,'private','{}','{}','running','2026-01-01','script')",
            [("run-a",), ("run-b",)],
        )
    return target


def _count(target, table):
    with connect(target) as conn:
        return conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]


def _database_digest(target):
    import hashlib

    from psycopg import sql

    with connect(target, readonly=True) as conn:
        tables = conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind='r' ORDER BY c.relname",
            (target.schema,),
        ).fetchall()
        values = []
        for table in tables:
            rows = conn.execute(
                sql.SQL("SELECT * FROM {}.{}").format(
                    sql.Identifier(target.schema), sql.Identifier(table[0])
                )
            ).fetchall()
            values.append((table[0], sorted(repr(tuple(row)) for row in rows)))
        return hashlib.sha256(repr(values).encode()).digest()


def test_exact_bytes_roundtrip_identity_reconstruction_and_last_reference_collection(private_store):
    components, materials = _material_set()
    plan_id = snapshots.freeze_and_attach("run-a", components, materials)
    assert plan_id == pi.compute_v2(components)
    assert snapshots.freeze_and_attach("run-a", components, materials) == plan_id
    assert snapshots.freeze_and_attach("run-b", components, materials) == plan_id
    assert _count(private_store, "workflow_plan_snapshot") == 1
    assert _count(private_store, "workflow_plan_snapshot_component") == len(materials)
    assert _count(private_store, "workflow_run_plan_snapshot") == 2
    assert snapshots.stored_plan_components_for_run("run-a") == (plan_id, components)
    verified = snapshots.private_snapshot_for_run("run-a", components)
    assert dict(verified.components) == materials
    assert verified.decode_material("artifact_hash") == materials["artifact_hash"]
    assert verified.decode_material("declaration") == {"name": "private-workflow"}
    input_key = next(name for name in materials if name.startswith("input-key:"))
    input_value = next(name for name in materials if name.startswith("input-value:"))
    assert verified.decode_material(input_key) == "private-input"
    assert verified.decode_material(input_value) == {"credential": "private-value", "count": 3}
    with pytest.raises(snapshots.PrivateSnapshotIntegrityError, match="is missing"):
        verified.material("absent")
    snapshots.release_run_reference("run-a")
    assert snapshots.private_snapshot_for_run("run-b", components) == verified
    snapshots.release_run_reference("run-b")
    assert _count(private_store, "workflow_plan_snapshot_component") == 0
    assert _count(private_store, "workflow_plan_snapshot") == 0
    snapshots.release_run_reference("already-absent")


def test_borrowed_transaction_never_commits_or_rolls_back_caller_work(private_store):
    components, materials = _material_set()
    with closing(connect(private_store)) as conn:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO workflow_run(run_id,workflow_name,spec_snapshot,inputs_json,state,started_at,tier) VALUES ('new-run','private','{}','{}','running','2026-01-01','script')"
        )
        snapshots.freeze_and_attach("new-run", components, materials, conn)
        assert conn.in_transaction
        assert _count(private_store, "workflow_plan_snapshot") == 0
        conn.rollback()
        assert _count(private_store, "workflow_plan_snapshot") == 0
        assert conn.execute("SELECT 1 FROM workflow_run WHERE run_id='new-run'").fetchone() is None
        conn.execute("BEGIN")
        snapshots.freeze_and_attach("run-a", components, materials, conn)
        snapshots.release_run_reference("run-a", conn)
        assert conn.in_transaction
        conn.commit()
    assert _count(private_store, "workflow_plan_snapshot") == 0


def test_conflicting_attachment_rolls_back_new_snapshot_atomically(private_store):
    components, materials = _material_set()
    original = snapshots.freeze_and_attach("run-a", components, materials)
    other_components, other_materials = _material_set(b"different executable")
    with pytest.raises(snapshots.PrivateSnapshotIntegrityError, match="run reference"):
        snapshots.freeze_and_attach("run-a", other_components, other_materials)
    assert snapshots.stored_plan_components_for_run("run-a")[0] == original
    assert _count(private_store, "workflow_plan_snapshot") == 1
    assert _count(private_store, "workflow_plan_snapshot_component") == len(materials)


def test_prepared_approval_keeps_bytes_until_last_run_and_approval_are_released(private_store):
    components, materials = _material_set()
    plan_id = snapshots.freeze_and_attach("run-a", components, materials)
    with connect(private_store) as conn:
        conn.begin()
        conn.execute(
            "INSERT INTO workflow_prepared_plan VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            ("approval", plan_id, "workflow", "script", "source", "owner", 1, 2, "{}"),
        )
        assert conn.in_transaction
        conn.commit()
    snapshots.release_run_reference("run-a")
    assert _count(private_store, "workflow_plan_snapshot_component") == len(materials)
    snapshots.freeze_and_attach("run-b", components, materials)
    with connect(private_store) as conn:
        conn.begin()
        assert conn.in_transaction
        conn.execute("DELETE FROM workflow_prepared_plan")
        snapshots.release_run_reference("run-b", conn)
        assert conn.in_transaction
        conn.commit()
    assert _count(private_store, "workflow_plan_snapshot") == 0


@pytest.mark.parametrize("invalid", ["missing", "extra", "text", "digest", "not-dict"])
def test_invalid_material_never_persists_private_bytes(private_store, invalid):
    components, materials = _material_set()
    if invalid == "missing":
        del materials["memory"]
    elif invalid == "extra":
        materials["unknown"] = b"private"
    elif invalid == "text":
        materials["memory"] = "private plaintext"
    elif invalid == "digest":
        materials["memory"] = b"private altered bytes"
    else:
        materials = list(materials.values())
    with pytest.raises(snapshots.PrivateSnapshotIntegrityError) as error:
        snapshots.freeze_and_attach("run-a", components, materials)
    assert "private plaintext" not in str(error.value)
    assert "private altered bytes" not in str(error.value)
    assert _count(private_store, "workflow_plan_snapshot_component") == 0


@pytest.mark.parametrize("combined", [False, True])
def test_exact_material_ceiling_refuses_oversize_without_truncation(private_store, combined):
    limit = snapshots.WORKFLOW_MANIFEST_MAX_BYTES
    components, materials = _material_set(b"x" * (limit // 2 if combined else limit + 1))
    if combined:
        materials["memory"] = snapshots.structured_material_bytes({"content": "y" * (limit // 2)})
        components = dataclasses.replace(components, memory=pi.digest_bytes(materials["memory"]))
    with pytest.raises(
        snapshots.PrivateSnapshotTooLargeError,
        match="total snapshot" if combined else "artifact_hash",
    ):
        snapshots.freeze_and_attach("run-a", components, materials)
    assert _count(private_store, "workflow_plan_snapshot") == 0


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("DELETE FROM workflow_plan_snapshot", "snapshot"),
        (
            "UPDATE workflow_plan_snapshot SET component_set_version='unknown'",
            "component_set_version",
        ),
        ("DELETE FROM workflow_plan_snapshot_component WHERE component_name='memory'", "memory"),
        (
            "UPDATE workflow_plan_snapshot_component SET component_name='unknown' WHERE component_name='memory'",
            "set",
        ),
        (
            "UPDATE workflow_plan_snapshot_component SET content_digest='wrong' WHERE component_name='memory'",
            "memory",
        ),
        (
            "UPDATE workflow_plan_snapshot_component SET content='private plaintext' WHERE component_name='memory'",
            "memory",
        ),
        (
            "UPDATE workflow_plan_snapshot_component SET content=decode('00','hex') WHERE component_name='memory'",
            "memory",
        ),
        ("UPDATE workflow_run_plan_snapshot SET plan_id='other-plan'", "plan_id"),
    ],
)
def test_read_refuses_corrupt_private_material_without_repair(private_store, mutation, message):
    components, materials = _material_set()
    snapshots.freeze_and_attach("run-a", components, materials)
    before_mutation = _database_digest(private_store)
    with connect(private_store) as conn:
        if mutation in {
            "DELETE FROM workflow_plan_snapshot",
            "UPDATE workflow_run_plan_snapshot SET plan_id='other-plan'",
        }:
            with pytest.raises(DatabaseError):
                conn.execute(mutation)
            assert _database_digest(private_store) == before_mutation
            assert (
                snapshots.private_snapshot_for_run("run-a", components).material("memory")
                == materials["memory"]
            )
            return
        conn.execute(mutation)
    before = _database_digest(private_store)
    with pytest.raises(snapshots.PrivateSnapshotIntegrityError, match=message):
        snapshots.private_snapshot_for_run("run-a", components)
    assert _database_digest(private_store) == before


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("DELETE FROM workflow_run_plan_snapshot", "run reference"),
        ("DELETE FROM workflow_run", "run reference"),
        (
            "UPDATE workflow_plan_snapshot SET component_set_version='unknown'",
            "component_set_version",
        ),
        (
            "DELETE FROM workflow_plan_snapshot_component WHERE component_name='limits'",
            "component set",
        ),
        (
            "DELETE FROM workflow_plan_snapshot_component WHERE component_name LIKE 'input-value:%'",
            "input set",
        ),
        (
            "UPDATE workflow_plan_snapshot_component SET content_digest='wrong' WHERE component_name LIKE 'input-key:%'",
            "input set",
        ),
        (
            "UPDATE workflow_plan_snapshot_component SET component_name='unknown' WHERE component_name='memory'",
            "component set",
        ),
        (
            "UPDATE workflow_plan_snapshot_component SET content_digest='"
            + "0" * 64
            + "' WHERE component_name='memory'",
            "plan_id",
        ),
    ],
)
def test_reconstruct_identity_refuses_corrupt_public_digest_rows(private_store, mutation, message):
    components, materials = _material_set()
    snapshots.freeze_and_attach("run-a", components, materials)
    before_mutation = _database_digest(private_store)
    with connect(private_store) as conn:
        if mutation in {
            "DELETE FROM workflow_plan_snapshot",
            "UPDATE workflow_run_plan_snapshot SET plan_id='other-plan'",
        }:
            with pytest.raises(DatabaseError):
                conn.execute(mutation)
            assert _database_digest(private_store) == before_mutation
            assert (
                snapshots.private_snapshot_for_run("run-a", components).material("memory")
                == materials["memory"]
            )
            return
        conn.execute(mutation)
    before = _database_digest(private_store)
    with pytest.raises(snapshots.PrivateSnapshotIntegrityError, match=message):
        snapshots.stored_plan_components_for_run("run-a")
    assert _database_digest(private_store) == before


def test_digest_identity_reconstruction_does_not_claim_private_byte_verification(private_store):
    components, materials = _material_set()
    plan_id = snapshots.freeze_and_attach("run-a", components, materials)
    with connect(private_store) as conn:
        conn.execute(
            "UPDATE workflow_plan_snapshot_component SET content=decode('00','hex') WHERE component_name='memory'"
        )
    assert snapshots.stored_plan_components_for_run("run-a") == (plan_id, components)
    with pytest.raises(snapshots.PrivateSnapshotIntegrityError, match="memory"):
        snapshots.private_snapshot_for_run("run-a", components)
    with pytest.raises(snapshots.PrivateSnapshotIntegrityError, match="memory"):
        snapshots.freeze_and_attach("run-b", components, materials)
    assert _count(private_store, "workflow_run_plan_snapshot") == 1


@pytest.mark.parametrize("suffix", ["", "-journal", "-wal", "-shm"])
def test_reads_refuse_exposed_database_or_sidecar_without_chmod(private_store, suffix):
    components, materials = _material_set()
    snapshots.freeze_and_attach("run-a", components, materials)
    exposed = private_store.secret_file
    exposed.chmod(0o644)
    with pytest.raises(snapshots.PrivateSnapshotStoreError):
        snapshots.private_snapshot_for_run("run-a", components)
    assert exposed.stat().st_mode & 0o777 == 0o644


def test_freezing_repairs_owner_only_database_and_existing_sidecar(private_store):
    components, materials = _material_set()
    private_store.secret_file.chmod(0o644)
    with pytest.raises(snapshots.PrivateSnapshotStoreError):
        snapshots.freeze_and_attach("run-a", components, materials)
    assert private_store.secret_file.stat().st_mode & 0o777 == 0o644
    private_store.secret_file.chmod(0o600)
    assert _count(private_store, "workflow_plan_snapshot") == 0


@pytest.mark.parametrize("invalid_run", [None, 123, "\ud800"])
def test_identity_read_rejects_invalid_run_without_opening_store(private_store, invalid_run):
    before = _database_digest(private_store)
    with pytest.raises(snapshots.PrivateSnapshotIntegrityError, match="run reference"):
        snapshots.stored_plan_components_for_run(invalid_run)
    assert _database_digest(private_store) == before


def test_missing_store_reads_and_release_never_create_database(private_store):
    config = private_store.home / "database-config.json"
    config.unlink()
    components, _ = _material_set()
    with pytest.raises(snapshots.PrivateSnapshotStoreError):
        snapshots.private_snapshot_for_run("run-a", components)
    with pytest.raises(snapshots.PrivateSnapshotStoreError):
        snapshots.stored_plan_components_for_run("run-a")
    with pytest.raises(snapshots.PrivateSnapshotStoreError):
        snapshots.release_run_reference("run-a")
    assert not config.exists()


@pytest.mark.parametrize(
    "table", ["workflow_run", "workflow_run_plan_snapshot", "workflow_plan_snapshot_component"]
)
def test_missing_schema_read_is_fail_closed_without_migrations(private_store, table):
    from psycopg import sql

    with connect(private_store.migration_target()) as conn:
        conn.execute(
            sql.SQL("DROP TABLE {}.{} CASCADE").format(
                sql.Identifier(private_store.schema), sql.Identifier(table)
            )
        )
    before = _database_digest(private_store)
    with pytest.raises(snapshots.PrivateSnapshotStoreError):
        snapshots.stored_plan_components_for_run("run-a")
    assert _database_digest(private_store) == before


def test_borrowed_connection_requires_active_file_backed_transaction(private_store):
    components, materials = _material_set()
    with connect(private_store) as conn:
        with pytest.raises(snapshots.PrivateSnapshotStoreError, match="transaction required"):
            snapshots.freeze_and_attach("run-a", components, materials, conn)
    with connect(private_store.migration_target()) as owner:
        owner.begin()
        with pytest.raises(snapshots.PrivateSnapshotStoreError):
            snapshots.freeze_and_attach("run-a", components, materials, owner)
        assert owner.in_transaction


def test_missing_run_cannot_freeze_private_material(private_store):
    components, materials = _material_set()
    with pytest.raises(snapshots.PrivateSnapshotIntegrityError, match="run reference"):
        snapshots.freeze_and_attach("missing", components, materials)
    assert _count(private_store, "workflow_plan_snapshot") == 0


def test_invalid_database_store_errors_are_sanitized_without_repair(private_store):
    private_store.secret_file.write_text("private corrupt credential contents")
    components, _ = _material_set()
    with pytest.raises(snapshots.PrivateSnapshotStoreError) as error:
        snapshots.private_snapshot_for_run("run-a", components)
    assert "private corrupt" not in str(error.value)
    assert private_store.secret_file.read_text() == "private corrupt credential contents"


@pytest.mark.parametrize(
    "name, material",
    [
        (None, b"x"),
        ("memory", "text"),
        ("unknown", b"x"),
        ("memory", b'{ "x":1}'),
        ("memory", b"\xff"),
        ("input-key:opaque", b"\xff"),
        ("input-value:opaque", b"invalid"),
    ],
)
def test_noncanonical_or_unknown_material_cannot_be_decoded(name, material):
    with pytest.raises(snapshots.PrivateSnapshotIntegrityError):
        snapshots.decode_material(name, material)


@pytest.mark.parametrize("value", [float("nan"), {"unserializable": object()}])
def test_structured_material_errors_do_not_disclose_rejected_values(value):
    with pytest.raises(snapshots.PrivateSnapshotIntegrityError, match="structured material"):
        snapshots.structured_material_bytes(value)


def test_unusable_database_path_fails_closed_without_creating_private_store(
    private_store, monkeypatch
):
    directory = private_store.home / "not-a-config"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv("CAO_DATABASE_CONFIG_FILE", str(directory))
    components, materials = _material_set()
    with pytest.raises(snapshots.PrivateSnapshotStoreError):
        snapshots.freeze_and_attach("run-a", components, materials)
    with pytest.raises(snapshots.PrivateSnapshotStoreError):
        snapshots.private_snapshot_for_run("run-a", components)
    assert not list(directory.iterdir())


def test_closed_borrowed_connection_errors_are_sanitized(private_store):
    components, materials = _material_set()
    conn = connect(private_store)
    conn.close()
    with pytest.raises(snapshots.PrivateSnapshotStoreError):
        snapshots.freeze_and_attach("run-a", components, materials, conn)
    with pytest.raises(snapshots.PrivateSnapshotStoreError):
        snapshots.release_run_reference("run-a", conn)
    assert _count(private_store, "workflow_plan_snapshot") == 0


def test_missing_run_table_and_aborting_storage_trigger_do_not_commit_material(private_store):
    from psycopg import sql

    components, materials = _material_set()
    with connect(private_store.migration_target()) as owner:
        owner.execute(
            sql.SQL("DROP TABLE {}.workflow_run CASCADE").format(
                sql.Identifier(private_store.schema)
            )
        )
    with pytest.raises(snapshots.PrivateSnapshotStoreError):
        snapshots.freeze_and_attach("run-a", components, materials)
    assert _count(private_store, "workflow_plan_snapshot_component") == 0


def test_read_missing_reference_or_broken_schema_never_attempts_repair(private_store):
    from psycopg import sql

    components, materials = _material_set()
    with pytest.raises(snapshots.PrivateSnapshotIntegrityError, match="run reference"):
        snapshots.private_snapshot_for_run("run-a", components)
    snapshots.freeze_and_attach("run-a", components, materials)
    with connect(private_store.migration_target()) as owner:
        owner.execute(
            sql.SQL("DROP TABLE {}.workflow_plan_snapshot_component").format(
                sql.Identifier(private_store.schema)
            )
        )
    before = _database_digest(private_store)
    with pytest.raises(snapshots.PrivateSnapshotStoreError):
        snapshots.private_snapshot_for_run("run-a", components)
    assert _database_digest(private_store) == before


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE workflow_run_plan_snapshot SET plan_id=decode('00','hex')",
        "UPDATE workflow_plan_snapshot_component SET content_digest=decode('00','hex') WHERE component_name='memory'",
    ],
)
def test_identity_reconstruction_refuses_binary_database_values(private_store, mutation):
    components, materials = _material_set()
    snapshots.freeze_and_attach("run-a", components, materials)
    before = _database_digest(private_store)
    with connect(private_store) as conn:
        if "SET plan_id=" in mutation:
            with pytest.raises(DatabaseError):
                conn.execute(mutation)
            assert _database_digest(private_store) == before
        else:
            conn.execute(mutation)
            with pytest.raises(snapshots.PrivateSnapshotIntegrityError):
                snapshots.stored_plan_components_for_run("run-a")


def test_release_corrupt_database_reports_failure_without_disclosing_contents(private_store):
    private_store.secret_file.write_text("private corrupt credentials")
    with pytest.raises(snapshots.PrivateSnapshotStoreError) as error:
        snapshots.release_run_reference("run-a")
    assert "private corrupt" not in str(error.value)


@pytest.mark.parametrize("operation", ["freeze", "release"])
def test_borrowed_private_operations_verify_component_before_mutation(private_store, operation):
    components, materials = _material_set()
    plan_id = snapshots.freeze_and_attach("run-a", components, materials)
    with connect(private_store.migration_target()) as owner:
        owner.execute(
            "UPDATE _cao_schema_migrations SET checksum=%s WHERE component='private_plans'",
            ("0" * 64,),
        )
    with closing(connect(private_store)) as conn:
        conn.begin()
        with pytest.raises(snapshots.PrivateSnapshotStoreError):
            if operation == "freeze":
                snapshots.freeze_and_attach("run-b", components, materials, conn)
            else:
                snapshots.release_run_reference("run-a", conn)
        assert conn.in_transaction
        assert (
            conn.execute(
                "SELECT plan_id FROM workflow_run_plan_snapshot WHERE run_id='run-a'"
            ).fetchone()[0]
            == plan_id
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM workflow_run_plan_snapshot WHERE run_id='run-b'"
            ).fetchone()[0]
            == 0
        )
        conn.rollback()
