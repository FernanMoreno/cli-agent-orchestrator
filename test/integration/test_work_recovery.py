"""End-to-end SQLite proof for the private v2 recovery boundary."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _recovery_module():
    from cli_agent_orchestrator.services import recovery_bundle

    return recovery_bundle


def _repository_module():
    from cli_agent_orchestrator.clients import work_repository

    return work_repository


@pytest.fixture
def recovery_source(tmp_path, monkeypatch):
    """Initialize an application store wholly under pytest-owned storage."""
    from cli_agent_orchestrator import constants
    from cli_agent_orchestrator.clients import database

    source = tmp_path / "source.sqlite3"
    engine = create_engine(f"sqlite:///{source}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(constants, "DATABASE_FILE", source)
    monkeypatch.setattr(database, "DB_DIR", source.parent)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    database.init_db()
    try:
        yield source
    finally:
        engine.dispose()


def _admit_uncertain_attempt(source: Path) -> str:
    """Create a durable sent intent without invoking a provider or backend."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.services.work_reducer import TransitionEvidence

    repository = WorkRepository(source)
    job = repository.create_job(
        project_id="recovery-project",
        principal_id="recovery-operator",
        allowed_providers=["mock_cli"],
        grant_id="recovery-grant",
    )
    work = repository.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key="recovery-integration",
        request_hash="a" * 64,
        contract_id="recovery-contract",
        snapshot_id="recovery-snapshot",
        provider="mock_cli",
        actor_id="recovery-operator",
        lease_seconds=60,
    )
    attempt = work["attempts"][0]
    repository.transition_attempt(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_revision=attempt["revision"],
        expected_state="planned",
        target="sent",
        actor_id="recovery-operator",
        event_id="recovery-integration-sent",
        evidence=TransitionEvidence(
            generation=attempt["generation"],
            expected_generation=attempt["generation"],
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
    )
    return attempt["id"]


def _capture_v2(source: Path, bundle_path: Path):
    """Capture through the real authority and receipt-publication protocol."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.work_authority import WorkAuthority

    module = _recovery_module()
    authority = WorkAuthority(WorkRepository(source))
    operator = auth._verified_principal(
        "https://recovery.integration",
        "capture-operator",
        [auth.SCOPE_ADMIN],
        "jwt",
    )
    lease = authority.create_offline_cut(operator, ttl_seconds=60)
    receipt = module.OfflineRecoveryCapture(authority, lease).capture(
        module.RecoveryCaptureSource(database_path=source), bundle_path
    )
    assert module.verify_recovery_bundle(receipt) == receipt
    return receipt


def _v25_store(path: Path):
    """Build a ledger-verified v25 store without invoking any newer migration."""
    module = _repository_module()
    store = module.WorkRepository(path)
    with store.transaction() as connection:
        for version in range(1, 26):
            for statement in module._MIGRATIONS[version]:
                connection.execute(statement)
            if version == 16:
                store._initialize_inbox_store_context(connection)
            if version == 24:
                store._initialize_recovery_context(connection)
            connection.execute(
                "INSERT INTO work_migrations VALUES (?,?,?,?)",
                (version, module._CHECKSUMS[version], time.time(), "verified"),
            )
        module.WorkRepository._verify(connection, version=25)
    return store


def _historical_v1_receipt(path: Path, bundle: Path):
    """Produce a verified legacy bundle only to prove it can never activate restore."""
    module = _recovery_module()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    size = path.stat().st_size
    objects = bundle / "objects"
    objects.mkdir(parents=True, mode=0o700)
    object_path = objects / digest
    object_path.write_bytes(path.read_bytes())
    object_path.chmod(0o600)
    bundle.chmod(0o700)
    manifest = {
        "format": "recovery-bundle-v1",
        "profile_version": 25,
        "objects": [
            {
                "digest": digest,
                "path": f"objects/{digest}",
                "roles": ["sqlite-v25"],
                "size": size,
            }
        ],
        "references": [],
    }
    encoded = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    manifest_path = bundle / "manifest.json"
    manifest_path.write_bytes(encoded)
    manifest_path.chmod(0o600)
    return module.RecoveryBundleReceipt(bundle, hashlib.sha256(encoded).hexdigest(), len(encoded))


def test_v2_capture_restores_only_a_blocked_distinct_portable_store(recovery_source, tmp_path):
    """Catches a restore that mutates source, relaunches sent work, or activates its copy."""
    module = _recovery_module()
    attempt_id = _admit_uncertain_attempt(recovery_source)
    receipt = _capture_v2(recovery_source, tmp_path / "bundle-v2")
    manifest = json.loads((receipt.bundle_path / "manifest.json").read_bytes())
    source_before = recovery_source.read_bytes()
    destination = tmp_path / "restored.sqlite3"

    module.restore_recovery_bundle(receipt, destination)

    assert manifest["format"] == "recovery-bundle-v2"
    assert manifest["profile_version"] == 31
    assert manifest["cut_evidence"]
    assert recovery_source.read_bytes() == source_before
    with sqlite3.connect(recovery_source) as source_connection:
        assert source_connection.execute(
            "SELECT state FROM work_attempts WHERE id=?", (attempt_id,)
        ).fetchone() == ("sent",)
    with sqlite3.connect(destination) as restored:
        assert restored.execute("SELECT execution_state FROM work_recovery_context").fetchone() == (
            "blocked_restore",
        )
        assert restored.execute("SELECT id,state FROM work_attempts ORDER BY id").fetchall() == [
            (attempt_id, "reconcile")
        ]
        assert restored.execute("SELECT count(*) FROM work_attempts").fetchone() == (1,)


def test_legacy_v1_integrity_never_activates_restore_and_mixed_profile_rejects(tmp_path):
    """Catches a downgrade path that treats historical verification as restore authority."""
    module = _recovery_module()
    legacy = tmp_path / "v25.sqlite3"
    _v25_store(legacy)
    receipt = _historical_v1_receipt(legacy, tmp_path / "legacy-bundle")
    destination = tmp_path / "legacy-restore.sqlite3"

    assert module.verify_recovery_bundle(receipt) == receipt
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.restore_recovery_bundle(receipt, destination)
    assert not destination.exists()

    manifest_path = receipt.bundle_path / "manifest.json"
    mixed = json.loads(manifest_path.read_bytes())
    mixed["profile_version"] = 26
    encoded = json.dumps(mixed, separators=(",", ":"), sort_keys=True).encode()
    manifest_path.write_bytes(encoded)
    mixed_receipt = module.RecoveryBundleReceipt(
        receipt.bundle_path, hashlib.sha256(encoded).hexdigest(), len(encoded)
    )
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.restore_recovery_bundle(mixed_receipt, tmp_path / "mixed-restore.sqlite3")
    assert not (tmp_path / "mixed-restore.sqlite3").exists()


def test_v25_to_v26_is_additive_and_ddl_interruption_keeps_the_v25_ledger(tmp_path, monkeypatch):
    """Catches a migration that drops history, downgrades a v26 store, or journals failed DDL."""
    module = _repository_module()
    upgraded = _v25_store(tmp_path / "upgraded.sqlite3")
    with sqlite3.connect(upgraded.path) as connection:
        v25_ledger = connection.execute(
            "SELECT version,checksum,verification_result FROM work_migrations ORDER BY version"
        ).fetchall()
        v25_tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    upgraded.initialize()
    upgraded.initialize()
    with upgraded.connection() as connection:
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT version,checksum,verification_result FROM work_migrations ORDER BY version"
            )
        ] == [
            (version, module._CHECKSUMS[version], "verified")
            for version in range(1, module.SCHEMA_VERSION + 1)
        ]
        assert v25_ledger == [
            (version, module._CHECKSUMS[version], "verified") for version in range(1, 26)
        ]
        assert v25_tables <= {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        with pytest.raises(module.SchemaMismatch):
            module.WorkRepository._verify(connection, version=25)

    interrupted = _v25_store(tmp_path / "interrupted.sqlite3")
    original_connection = interrupted.connection

    @contextmanager
    def deny_v26_cut_ddl():
        with original_connection() as connection:
            connection.set_authorizer(
                lambda action, name, *args: (
                    sqlite3.SQLITE_DENY
                    if action == sqlite3.SQLITE_CREATE_TABLE and name == "work_offline_cuts"
                    else sqlite3.SQLITE_OK
                )
            )
            yield connection

    monkeypatch.setattr(interrupted, "connection", deny_v26_cut_ddl)
    with pytest.raises(sqlite3.DatabaseError):
        interrupted.initialize()
    with sqlite3.connect(interrupted.path) as connection:
        assert connection.execute("SELECT max(version) FROM work_migrations").fetchone() == (25,)
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='work_offline_cuts'"
            ).fetchone()
            is None
        )


def test_corrupt_publication_lease_and_interrupted_copy_fail_without_destination(
    recovery_source, monkeypatch, tmp_path
):
    """Catches acceptance of corrupted publication evidence or cleanup after a copy interruption."""
    module = _recovery_module()
    receipt = _capture_v2(recovery_source, tmp_path / "lease-bundle")
    with sqlite3.connect(recovery_source) as connection:
        connection.execute(
            "UPDATE work_offline_cuts SET published_manifest_digest=? WHERE id=?",
            ("0" * 64, receipt.lease_id),
        )
    corrupt_destination = tmp_path / "corrupt.sqlite3"

    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.restore_recovery_bundle(receipt, corrupt_destination)
    assert not corrupt_destination.exists()

    valid_receipt = _capture_v2(recovery_source, tmp_path / "copy-bundle")
    source_before = recovery_source.read_bytes()
    destination = tmp_path / "interrupted-copy.sqlite3"

    def interrupt_copy(*_args, **_kwargs):
        raise OSError("interrupted private copy")

    monkeypatch.setattr(module, "_copy_restore_sqlite", interrupt_copy)
    with pytest.raises(module.RecoveryBundleError, match="recovery bundle rejected"):
        module.restore_recovery_bundle(valid_receipt, destination)
    assert recovery_source.read_bytes() == source_before
    assert not destination.exists()
    assert not list(tmp_path.glob(".recovery-restore-*"))
