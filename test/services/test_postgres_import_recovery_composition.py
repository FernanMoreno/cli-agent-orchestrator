"""Native composition of historical PostgreSQL receipt provenance and recovery."""

from __future__ import annotations

import hashlib
import json
import uuid
from test.services.test_postgres_recovery_bundle import (
    _capture_context,
    _make_destination,
    _OwnedDockerArchiveRunner,
    native_store,
)

import pytest
from psycopg import sql
from psycopg.errors import InsufficientPrivilege

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.import_receipt_schema import (
    IMPORT_RECEIPT_TABLE,
    import_receipt_migrations,
)
from cli_agent_orchestrator.clients.postgres.migrations import verify_component
from cli_agent_orchestrator.clients.postgres.registry import component_migrations
from cli_agent_orchestrator.services.recovery_bundle import (
    _owned_tables,
    _verify_closed_relations,
    restore_recovery_bundle,
)

pytestmark = pytest.mark.postgres


def _historical_target_identity(target):
    """Build the opaque identity hash stored in historical receipt rows."""
    encoded = json.dumps(
        list(target.identity), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _seed_historical_receipt(target):
    receipt_id = str(uuid.uuid4())
    with connect(target.migration_target()) as owner:
        with owner.transaction():
            owner.execute(
                """INSERT INTO _cao_import_receipts
                (receipt_id, scope, profile_id, source_path, source_digest,
                 source_schema_fingerprint, source_identity, target_store_id,
                 target_schema, target_identity_hash, state, table_manifest, verified_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'verified',%s,CURRENT_TIMESTAMP)""",
                (
                    receipt_id,
                    target.scope,
                    "legacy-main-v1",
                    "offline-main-v1",
                    "a" * 64,
                    "b" * 64,
                    json.dumps({"kind": "stopped-legacy-source"}),
                    target.store_id,
                    target.schema,
                    _historical_target_identity(target),
                    json.dumps({"fixture": {"count": 0}}),
                ),
            )
    return receipt_id


def _receipt_row(target, receipt_id):
    with connect(target, readonly=True) as reader:
        return reader.execute(
            """SELECT receipt_id::text, scope, profile_id, source_path, source_digest,
            source_schema_fingerprint, source_identity, target_store_id::text,
            target_schema, target_identity_hash, state, table_manifest, created_at, verified_at
            FROM _cao_import_receipts WHERE receipt_id=%s""",
            (receipt_id,),
        ).fetchone()


def test_historical_receipt_component_remains_in_the_native_catalog(native_store):
    target, _repository, _home, _digest = native_store
    for scope in ("main", "auth", "peer"):
        receipt_migrations = import_receipt_migrations(scope)
        assert receipt_migrations in component_migrations(scope)
        assert len(receipt_migrations) == 1
        assert receipt_migrations[0].tables == (IMPORT_RECEIPT_TABLE,)
        assert receipt_migrations[0].version == 1
    receipt_migrations = import_receipt_migrations(target.scope)
    with connect(target, readonly=True) as reader:
        verify_component(reader, receipt_migrations)
        _verify_closed_relations(reader, target, _owned_tables(target.scope))


def test_historical_receipt_survives_native_capture_and_blocked_restore(
    native_store, postgres_service, tmp_path
):
    target, _repository, _home, _digest = native_store
    receipt_id = _seed_historical_receipt(target)
    expected_receipt = _receipt_row(target, receipt_id)
    _authority, _operator, _lease, source, capture, _digest = _capture_context(
        native_store, postgres_service, tmp_path
    )
    receipt = capture.capture(source, tmp_path / "historical-receipt-bundle")
    manifest = json.loads((receipt.bundle_path / "manifest.json").read_text())
    assert manifest["inventory"]["rows"]["_cao_import_receipts"]["count"] == 1

    destination, database_name, destination_home = _make_destination(
        postgres_service, source, tmp_path
    )
    try:
        restored = restore_recovery_bundle(
            receipt,
            destination,
            filesystem_home=destination_home,
            runner=_OwnedDockerArchiveRunner(postgres_service, tmp_path),
        )
        assert restored.execution_state == "blocked_restore"
        restored_receipt = _receipt_row(destination, receipt_id)
        assert restored_receipt == expected_receipt
        assert restored_receipt[9] == _historical_target_identity(target)
        assert restored_receipt[9] != _historical_target_identity(destination)
    finally:
        with postgres_service.connect_admin() as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (database_name,),
            )
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


def test_runtime_can_read_historical_receipts_but_cannot_rewrite_provenance(native_store):
    target, _repository, _home, _digest = native_store
    receipt_id = _seed_historical_receipt(target)
    with connect(target, readonly=True) as reader:
        assert reader.execute(
            "SELECT state FROM _cao_import_receipts WHERE receipt_id=%s", (receipt_id,)
        ).fetchone() == ("verified",)
    with connect(target) as runtime:
        with pytest.raises(InsufficientPrivilege):
            with runtime.transaction():
                runtime.execute(
                    "UPDATE _cao_import_receipts SET source_digest=%s WHERE receipt_id=%s",
                    ("c" * 64, receipt_id),
                )
