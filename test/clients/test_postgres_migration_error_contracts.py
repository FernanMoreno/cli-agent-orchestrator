"""Native PostgreSQL role, migration, and restore-provenance contracts."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from cli_agent_orchestrator.clients.postgres import ConfigurationError, connect
from cli_agent_orchestrator.clients.postgres.migrations import (
    ComponentMigration,
    SchemaMismatch,
    migrate_component,
)

pytestmark = pytest.mark.postgres


def test_runtime_and_migration_roles_remain_distinct(postgres_targets):
    target = postgres_targets.main
    migration_target = target.migration_target()

    assert migration_target.user == target.migration_user
    assert migration_target.user != target.user
    assert migration_target.migration_user is None
    with connect(target) as runtime, connect(migration_target) as owner:
        assert runtime.execute("SELECT current_user").fetchone()[0] == target.user
        assert owner.execute("SELECT current_user").fetchone()[0] == target.migration_user

    implicit_owner = replace(target, migration_user=None, migration_secret_file=None)
    with pytest.raises(ConfigurationError, match="Explicit PostgreSQL migration credentials"):
        implicit_owner.migration_target()


def test_migration_checksum_corruption_is_not_repaired_by_bootstrap(postgres_targets):
    target = postgres_targets.peer
    migration = (
        ComponentMigration(
            component="migration_error_contract",
            scope="peer",
            version=1,
            statements=("CREATE TABLE migration_guard_records (id BIGINT PRIMARY KEY)",),
            tables=("migration_guard_records",),
        ),
    )
    migrate_component(target, migration)

    with connect(target.migration_target()) as owner:
        owner.execute(
            "UPDATE _cao_schema_migrations SET checksum=%s "
            "WHERE scope='peer' AND component=%s AND version=1",
            ("f" * 64, migration[0].component),
        )

    with pytest.raises(SchemaMismatch, match="Unknown or modified PostgreSQL migration"):
        migrate_component(target, migration)

    with connect(target.migration_target(), readonly=True) as owner:
        assert (
            owner.execute(
                "SELECT checksum FROM _cao_schema_migrations "
                "WHERE scope='peer' AND component=%s AND version=1",
                (migration[0].component,),
            ).fetchone()[0]
            == "f" * 64
        )
        assert (
            owner.execute(
                "SELECT to_regclass(%s)", (f"{target.schema}.migration_guard_records",)
            ).fetchone()[0]
            is not None
        )


def test_restore_provenance_cannot_replace_already_committed_receipt(postgres_targets):
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope
    from cli_agent_orchestrator.clients.work_repository import (
        RECOVERY_STATE_BLOCKED_RESTORE,
    )
    from cli_agent_orchestrator.clients.work_repository import SchemaMismatch as WorkSchemaMismatch
    from cli_agent_orchestrator.clients.work_repository import (
        WorkRepository,
        bootstrap_work_store,
    )

    target = postgres_targets.main
    bootstrap_scope(target, seed_work=False)
    bootstrap_work_store(target)
    repository = WorkRepository(target, filesystem_home=postgres_targets.home)
    repository.initialize()

    first = {
        "source_store_uuid": UUID(target.store_id).hex,
        "installation_uuid": uuid4().hex,
        "bundle_digest": "a" * 64,
        "restore_receipt": "b" * 64,
    }
    restored = repository.block_imported_restore(**first)
    assert restored.execution_state == RECOVERY_STATE_BLOCKED_RESTORE

    second = {
        "source_store_uuid": UUID(target.store_id).hex,
        "installation_uuid": uuid4().hex,
        "bundle_digest": "c" * 64,
        "restore_receipt": "d" * 64,
    }
    with pytest.raises(WorkSchemaMismatch, match="not eligible for restore blocking"):
        repository.block_imported_restore(**second)

    with connect(target, readonly=True) as connection:
        context = connection.execute(
            "SELECT execution_state,installation_uuid,source_installation_uuid,"
            "bundle_digest,restore_receipt FROM work_recovery_context WHERE singleton=1"
        ).fetchone()
    assert context is not None
    assert context["execution_state"] == RECOVERY_STATE_BLOCKED_RESTORE
    assert context["installation_uuid"] == first["installation_uuid"]
    assert context["bundle_digest"] == first["bundle_digest"]
    assert context["restore_receipt"] == first["restore_receipt"]
