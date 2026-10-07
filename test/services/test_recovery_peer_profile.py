"""Peer authority is closed inventory, never a portable restored capability."""

import hashlib
import json
import sqlite3
import time
from test.services import test_recovery_bundle
from test.services.test_recovery_bundle import (
    _capture_with_work_authority,
    _database_path,
    _rebind_published_manifest,
    _recomposed_v2_receipt,
    _recovery_bundle_module,
)

import pytest

initialized_cao_connection = test_recovery_bundle.initialized_cao_connection

PEER_TABLES = (
    "local_peer_grants",
    "local_peer_pairing_challenges",
    "local_peer_projects",
    "local_peer_request_nonces",
    "local_peer_tasks",
)


def _seed_peer_state(connection, table):
    now = "2026-10-07T00:00:00Z"
    rows = {
        "local_peer_projects": {
            "project_id": "project",
            "canonical_root": "/private/project",
            "created_at": now,
        },
        "local_peer_grants": {
            "grant_id": "grant",
            "peer_instance_id": "peer",
            "project_id": "project",
            "peer_display_name": "Peer",
            "peer_public_key": "A" * 43,
            "scopes_json": '["read"]',
            "created_at": now,
        },
        "local_peer_request_nonces": {
            "peer_instance_id": "peer",
            "nonce": "b" * 32,
            "expires_at": time.time() + 600,
        },
        "local_peer_tasks": {
            "task_id": "task",
            "source_instance_id": "source",
            "target_instance_id": "target",
            "project_id": "project",
            "operation_key": "operation",
            "request_hash": "c" * 64,
            "state": "accepted",
            "created_at": now,
            "updated_at": now,
        },
        "local_peer_pairing_challenges": {
            "challenge_id": "challenge",
            "role": "initiator",
            "code_hash": "c" * 64,
            "initiator_instance_id": "initiator",
            "initiator_process_generation": "d" * 32,
            "initiator_display_name": "Initiator",
            "initiator_public_key": "A" * 43,
            "initiator_loopback_port": 12345,
            "candidate_instance_id": "candidate",
            "candidate_process_generation": "e" * 32,
            "candidate_display_name": "Candidate",
            "candidate_public_key": "B" * 43,
            "project_id": "project",
            "canonical_root": "/private/project",
            "requested_scopes_json": '["read"]',
            "expires_at": time.time() + 600,
            "created_at": now,
        },
    }
    row = rows[table]
    connection.execute(
        f'INSERT INTO "{table}" ({",".join(row)}) VALUES ({",".join("?" for _ in row)})',
        tuple(row.values()),
    )
    connection.commit()


def test_current_peer_profile_is_closed_and_nonportable(initialized_cao_connection):
    from cli_agent_orchestrator.services import recovery_inventory

    inventory = recovery_inventory.inspect_work_store(initialized_cao_connection)
    assert inventory.profile_version == 39
    assert set(PEER_TABLES).issubset(inventory.tables)
    assert {
        reference.source_table
        for reference in inventory.references
        if reference.resolution == "requires_future_profile"
        and reference.source_table in PEER_TABLES
    } == set(PEER_TABLES)


def test_frozen_v38_catalog_rejects_current_peer_objects(initialized_cao_connection):
    from cli_agent_orchestrator.services import recovery_inventory

    assert not set(PEER_TABLES) & set(recovery_inventory._V38_TABLES)
    with pytest.raises(recovery_inventory.RecoveryInventoryError):
        recovery_inventory.inspect_work_store(initialized_cao_connection, profile_version=38)
    for table in PEER_TABLES:
        initialized_cao_connection.execute(f'DROP TABLE "{table}"')
    initialized_cao_connection.commit()
    before = initialized_cao_connection.total_changes
    assert (
        recovery_inventory.inspect_work_store(initialized_cao_connection, profile_version=38).tables
        == recovery_inventory._V38_TABLES
    )
    assert initialized_cao_connection.total_changes == before


def test_historical_v38_bundle_remains_verify_only(
    initialized_cao_connection, monkeypatch, tmp_path
):
    from cli_agent_orchestrator.services import recovery_inventory

    for table in PEER_TABLES:
        initialized_cao_connection.execute(f'DROP TABLE "{table}"')
    initialized_cao_connection.commit()
    source = _database_path(initialized_cao_connection)
    module = _recovery_bundle_module()
    with monkeypatch.context() as historical:
        historical.setattr(module, "WORK_SQLITE_PROFILE_VERSION", 38)
        historical.setattr(
            module,
            "inspect_work_store",
            lambda connection: recovery_inventory.inspect_work_store(
                connection, profile_version=38
            ),
        )
        historical.setattr(
            module,
            "verified_inbox_store_identity",
            lambda connection: recovery_inventory.verified_inbox_store_identity(
                connection, profile_version=38
            ),
        )
        historical.setattr(
            module,
            "inspect_portable_work_store",
            lambda connection, *, expected_source_identity: recovery_inventory.inspect_portable_work_store(
                connection, expected_source_identity=expected_source_identity, profile_version=38
            ),
        )
        receipt = _capture_with_work_authority(
            module, module.RecoveryCaptureSource(source), tmp_path / "historical-v38"
        )
    assert module.verify_recovery_bundle(receipt) == receipt
    destination = tmp_path / "historical-v38-restored.sqlite3"
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.restore_recovery_bundle(receipt, destination)
    assert not destination.exists()


@pytest.mark.parametrize("table", PEER_TABLES)
def test_capture_rejects_populated_peer_authority(initialized_cao_connection, tmp_path, table):
    from cli_agent_orchestrator.services import recovery_inventory

    _seed_peer_state(initialized_cao_connection, table)
    # The catalog must accept the schema before content policy rejects these rows.
    assert recovery_inventory.inspect_work_store(initialized_cao_connection).profile_version == 39
    module = _recovery_bundle_module()
    source = _database_path(initialized_cao_connection)
    destination = tmp_path / "peer-capture"
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        _capture_with_work_authority(module, module.RecoveryCaptureSource(source), destination)
    assert not destination.exists()
    assert initialized_cao_connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] == 1


@pytest.mark.parametrize("table", PEER_TABLES)
def test_restore_rejects_published_peer_authority(initialized_cao_connection, tmp_path, table):
    """Even re-bound integrity/publication evidence cannot revive native peer state."""
    module = _recovery_bundle_module()
    source = _database_path(initialized_cao_connection)
    receipt = _capture_with_work_authority(
        module, module.RecoveryCaptureSource(source), tmp_path / "peer-restore-bundle"
    )
    manifest = json.loads((receipt.bundle_path / "manifest.json").read_bytes())
    sqlite_object = next(
        item
        for item in manifest["objects"]
        if f"sqlite-v{module.WORK_SQLITE_PROFILE_VERSION}" in item["roles"]
    )
    sqlite_path = receipt.bundle_path / sqlite_object["path"]
    with sqlite3.connect(sqlite_path) as copied:
        _seed_peer_state(copied, table)
    digest = hashlib.sha256(sqlite_path.read_bytes()).hexdigest()
    sqlite_object.update(digest=digest, path=f"objects/{digest}", size=sqlite_path.stat().st_size)
    sqlite_path.rename(receipt.bundle_path / sqlite_object["path"])
    manifest["objects"].sort(key=lambda item: item["digest"])
    manifest["cut_evidence"]["coverage"] = [
        {"digest": item["digest"], "classification": "integrity-only", "roles": item["roles"]}
        for item in manifest["objects"]
    ]
    receipt = _rebind_published_manifest(source, receipt, manifest, module)
    source_before = source.read_bytes()
    destination = tmp_path / "peer-restored.sqlite3"
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.restore_recovery_bundle(receipt, destination)
    assert not destination.exists()
    assert source.read_bytes() == source_before


def _add_unreviewed_schema_object(connection, kind):
    if kind == "trigger":
        connection.execute(
            "CREATE TRIGGER unreviewed_native_trigger AFTER INSERT ON memory_metadata "
            "BEGIN SELECT 1; END"
        )
    else:
        connection.execute("CREATE VIEW unreviewed_native_view AS SELECT 1 AS value")
    connection.commit()


@pytest.mark.parametrize("kind", ["trigger", "view"])
def test_capture_rejects_unreviewed_executable_schema(initialized_cao_connection, tmp_path, kind):
    """Even inert undeclared executable objects lie outside the closed catalog."""
    _add_unreviewed_schema_object(initialized_cao_connection, kind)
    module = _recovery_bundle_module()
    source = _database_path(initialized_cao_connection)
    destination = tmp_path / "unreviewed-schema-capture"
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        _capture_with_work_authority(module, module.RecoveryCaptureSource(source), destination)
    assert not destination.exists()


@pytest.mark.parametrize("kind", ["trigger", "view"])
def test_restore_rejects_unreviewed_schema_before_writing(
    initialized_cao_connection, tmp_path, kind
):
    module = _recovery_bundle_module()
    source = _database_path(initialized_cao_connection)
    receipt = _capture_with_work_authority(
        module, module.RecoveryCaptureSource(source), tmp_path / "unreviewed-schema-bundle"
    )
    manifest = json.loads((receipt.bundle_path / "manifest.json").read_bytes())
    sqlite_object = next(
        item
        for item in manifest["objects"]
        if f"sqlite-v{module.WORK_SQLITE_PROFILE_VERSION}" in item["roles"]
    )
    sqlite_path = receipt.bundle_path / sqlite_object["path"]
    with sqlite3.connect(sqlite_path) as copied:
        _add_unreviewed_schema_object(copied, kind)
    digest = hashlib.sha256(sqlite_path.read_bytes()).hexdigest()
    sqlite_object.update(digest=digest, path=f"objects/{digest}", size=sqlite_path.stat().st_size)
    sqlite_path.rename(receipt.bundle_path / sqlite_object["path"])
    manifest["objects"].sort(key=lambda item: item["digest"])
    manifest["cut_evidence"]["coverage"] = [
        {"digest": item["digest"], "classification": "integrity-only", "roles": item["roles"]}
        for item in manifest["objects"]
    ]
    receipt = _recomposed_v2_receipt(module, receipt, manifest)
    with sqlite3.connect(source) as original:
        original.execute(
            "UPDATE work_offline_cuts SET published_manifest_digest=?,published_manifest_size=? "
            "WHERE id=?",
            (receipt.manifest_digest, receipt.manifest_size, receipt.lease_id),
        )
    source_before = source.read_bytes()
    destination = tmp_path / "unreviewed-schema-restored.sqlite3"
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.restore_recovery_bundle(receipt, destination)
    assert not destination.exists()
    assert not tuple(tmp_path.glob(".recovery-restore-*"))
    assert source.read_bytes() == source_before
