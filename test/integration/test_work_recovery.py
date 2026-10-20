"""PostgreSQL recovery capture, restore, and execution-fencing contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from test.services.test_postgres_recovery_bundle import native_store

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.work_repository import SchemaMismatch, WorkRepository
from cli_agent_orchestrator.services.recovery_bundle import (
    PostgresArchiveRunner,
    RecoveryBundleError,
    restore_recovery_bundle,
    verify_recovery_bundle,
)


@pytest.mark.postgres
def test_v2_capture_restores_only_a_blocked_distinct_portable_store(
    native_store, postgres_service, tmp_path
):
    """Restore preserves the old test's no-source-mutation and no-replay contract."""
    from test.services.test_postgres_recovery_bundle import (
        _capture,
        _make_destination,
        _OwnedDockerArchiveRunner,
    )

    source_target, source_repository, _source_home, _digest = native_store
    receipt, source, _digest = _capture(native_store, postgres_service, tmp_path)
    assert verify_recovery_bundle(receipt) == receipt
    destination, database_name, destination_home = _make_destination(
        postgres_service, source, tmp_path
    )
    try:
        source_before = _attempt_snapshot(source_target)
        restored = restore_recovery_bundle(
            receipt,
            destination,
            filesystem_home=destination_home,
            runner=_OwnedDockerArchiveRunner(postgres_service, tmp_path),
        )

        assert restored.execution_state == "blocked_restore"
        assert restored.source_store_id == source_target.store_id
        assert _attempt_snapshot(source_target) == source_before
        with connect(destination, readonly=True) as connection:
            restored_attempt = connection.execute(
                "SELECT state FROM work_attempts WHERE work_item_id=("
                "SELECT id FROM work_items WHERE idempotency_key=%s)",
                ("offline-recovery-attempt",),
            ).fetchone()
        assert restored_attempt == ("reconcile",)
        with source_repository.read_snapshot() as connection:
            source_repository.assert_execution_allowed(connection)
        restored_repository = WorkRepository(
            destination,
            filesystem_home=destination_home,
        )
        with restored_repository.read_snapshot() as connection:
            with pytest.raises(SchemaMismatch, match="recovery context blocks execution"):
                restored_repository.assert_execution_allowed(connection)
    finally:
        _drop_test_database(postgres_service, database_name)


@pytest.mark.postgres
def test_corrupt_publication_lease_and_interrupted_copy_fail_without_destination(
    native_store, postgres_service, tmp_path
):
    """Cut receipt corruption and dump interruption cannot create a usable bundle."""
    from test.services.test_postgres_recovery_bundle import (
        _capture,
        _capture_context,
    )

    class InterruptedDumpRunner(PostgresArchiveRunner):
        def dump(self, target, destination, *, snapshot_id):
            raise OSError("fixture dump interruption")

        def restore(self, target, archive):
            raise AssertionError("restore must not run in this test")

    authority, _operator, lease, source, capture, _digest = _capture_context(
        native_store,
        postgres_service,
        tmp_path,
        runner=InterruptedDumpRunner(),
    )
    failed_destination = tmp_path / "interrupted-capture"
    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        capture.capture(source, failed_destination)
    assert not failed_destination.exists()
    assert not tuple(tmp_path.glob(".postgres-recovery-*"))
    assert authority.verify_offline_cut(lease, source_database=source.database)
    # The failed capture leaves the verified cut intact for inspection. Revoke
    # that exact lease before asking the same source to acquire a new one.
    authority.revoke_offline_cut(_operator, lease)

    receipt, _source, _digest = _capture(native_store, postgres_service, tmp_path)
    manifest_path = receipt.bundle_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["cut"]["lease"]["fence"] += 1
    payload = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    manifest_path.write_bytes(payload)
    manifest_path.chmod(0o600)
    forged = replace(
        receipt,
        manifest_digest=hashlib.sha256(payload).hexdigest(),
        manifest_size=len(payload),
    )
    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        verify_recovery_bundle(forged)


def _attempt_snapshot(target):
    with connect(target, readonly=True) as connection:
        return connection.execute(
            "SELECT attempt.id, attempt.state, attempt.generation "
            "FROM work_attempts AS attempt JOIN work_items AS item "
            "ON item.id=attempt.work_item_id WHERE item.idempotency_key=%s",
            ("offline-recovery-attempt",),
        ).fetchall()


def _drop_test_database(postgres_service, database_name: str) -> None:
    with postgres_service.connect_admin() as admin:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=%s AND pid<>pg_backend_pid()",
            (database_name,),
        )
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name)))
