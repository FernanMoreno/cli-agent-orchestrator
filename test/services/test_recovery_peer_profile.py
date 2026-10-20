"""Peer state is a separate, host-scoped store and never enters main recovery."""

from __future__ import annotations

from test.services.test_postgres_recovery_bundle import native_store

import pytest

from cli_agent_orchestrator.clients.postgres.import_receipt_schema import IMPORT_RECEIPT_TABLE
from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope, component_migrations

PEER_TABLES = ("local_peer_processes", "local_project_write_leases")


@pytest.mark.postgres
def test_current_peer_profile_is_closed_and_nonportable(
    native_store, postgres_targets, postgres_service, tmp_path
):
    from test.services.test_postgres_recovery_bundle import _capture

    peer_target = postgres_targets.peer
    bootstrap_scope(peer_target)
    peer_tables = {
        table
        for component in component_migrations("peer")
        for migration in component
        for table in migration.tables
    }
    main_tables = {
        table
        for component in component_migrations("main")
        for migration in component
        for table in migration.tables
    }
    # Import receipts are scoped infrastructure installed in every store. They
    # describe migration/import state, not peer authority, so compare the
    # scope-owned peer tables separately from this shared per-scope table.
    assert IMPORT_RECEIPT_TABLE in peer_tables
    peer_owned_tables = peer_tables - {IMPORT_RECEIPT_TABLE}
    main_owned_tables = main_tables - {IMPORT_RECEIPT_TABLE}
    assert peer_owned_tables == set(PEER_TABLES)
    assert peer_owned_tables.isdisjoint(main_owned_tables)

    receipt, _source, _digest = _capture(native_store, postgres_service, tmp_path)
    manifest = __import__("json").loads((receipt.bundle_path / "manifest.json").read_text())
    assert not set(PEER_TABLES).intersection(
        row["table_name"] for row in manifest["inventory"]["catalog"]["columns"]
    )


@pytest.mark.postgres
@pytest.mark.parametrize("peer_table", PEER_TABLES)
def test_capture_rejects_populated_peer_authority(
    native_store, postgres_targets, postgres_service, tmp_path, peer_table
):
    """Peer state is never included in a main-store recovery capture."""
    from test.services.test_postgres_recovery_bundle import _capture

    from cli_agent_orchestrator.clients.postgres import connect

    peer_target = postgres_targets.peer
    bootstrap_scope(peer_target)
    with connect(peer_target) as connection:
        if peer_table == "local_peer_processes":
            connection.execute(
                "INSERT INTO local_peer_processes "
                "(host_id,owner_uid,instance_id,process_generation,pid,process_started_at,"
                "display_name,loopback_host,loopback_port,started_at,last_seen) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    "peer-host",
                    1000,
                    "instance",
                    "generation",
                    12345,
                    1.0,
                    "Peer",
                    "127.0.0.1",
                    1234,
                    1.0,
                    1.0,
                ),
            )
        else:
            connection.execute(
                "INSERT INTO local_project_write_leases "
                "(host_id,owner_uid,project_id,task_id,owner_instance_id,state,heartbeat_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,'held',%s,%s)",
                ("peer-host", 1000, "project", "task", "instance", 1.0, 1.0),
            )

    receipt, _source, _digest = _capture(native_store, postgres_service, tmp_path)
    manifest = __import__("json").loads((receipt.bundle_path / "manifest.json").read_text())
    assert peer_table not in {
        row["table_name"] for row in manifest["inventory"]["catalog"]["columns"]
    }
    with connect(peer_target, readonly=True) as connection:
        if peer_table == "local_peer_processes":
            assert connection.execute(
                "SELECT display_name FROM local_peer_processes WHERE instance_id=%s", ("instance",)
            ).fetchone() == ("Peer",)
        else:
            assert connection.execute(
                "SELECT state FROM local_project_write_leases WHERE project_id=%s", ("project",)
            ).fetchone() == ("held",)


@pytest.mark.postgres
@pytest.mark.parametrize("peer_table", PEER_TABLES)
def test_restore_rejects_peer_store_as_a_main_restore_target(
    native_store, postgres_targets, postgres_service, tmp_path, peer_table
):
    from test.services.test_postgres_recovery_bundle import (
        _capture,
        _OwnedDockerArchiveRunner,
    )

    from cli_agent_orchestrator.services.recovery_bundle import (
        RecoveryBundleError,
        restore_recovery_bundle,
    )

    receipt, _source, _digest = _capture(native_store, postgres_service, tmp_path)
    with pytest.raises(RecoveryBundleError, match="recovery bundle rejected"):
        restore_recovery_bundle(
            receipt,
            postgres_targets.peer,
            filesystem_home=postgres_targets.peer.home,
            runner=_OwnedDockerArchiveRunner(postgres_service, tmp_path),
        )


@pytest.mark.postgres
@pytest.mark.parametrize("kind", ["trigger", "view"])
def test_capture_rejects_unreviewed_executable_schema(
    native_store, postgres_service, tmp_path, kind
):
    from test.services.test_postgres_recovery_bundle import (
        test_capture_rejects_unreviewed_executable_schema_objects,
    )

    test_capture_rejects_unreviewed_executable_schema_objects(
        native_store, postgres_service, tmp_path, kind
    )


@pytest.mark.postgres
@pytest.mark.parametrize("kind", ["trigger", "view"])
def test_restore_rejects_unreviewed_schema_before_writing(
    native_store, postgres_service, tmp_path, kind
):
    from test.services.test_postgres_recovery_bundle import (
        test_restore_rejects_unreviewed_relation_and_drops_only_its_schema,
    )

    test_restore_rejects_unreviewed_relation_and_drops_only_its_schema(
        native_store, postgres_service, tmp_path, kind
    )
