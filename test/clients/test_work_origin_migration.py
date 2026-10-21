"""Native PostgreSQL Work origin migration contracts."""

import pytest


@pytest.mark.postgres
def test_native_work_origin_migration_is_installed_and_verified(postgres_targets):
    """The active Work runtime uses the current native migration ledger and DDL."""
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.postgres.migrations import verify_component
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope
    from cli_agent_orchestrator.clients.work_postgres_schema import WORK_POSTGRES_MIGRATIONS
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    target = postgres_targets.main
    bootstrap_scope(target)
    WorkRepository(target, filesystem_home=postgres_targets.home).initialize()

    with connect(target, readonly=True) as connection:
        verify_component(connection, WORK_POSTGRES_MIGRATIONS)
        installed = connection.execute(
            "SELECT version,checksum FROM _cao_schema_migrations "
            "WHERE scope='main' AND component='work' ORDER BY version"
        ).fetchall()
        assert [(row["version"], row["checksum"]) for row in installed] == [
            (migration.version, migration.checksum) for migration in WORK_POSTGRES_MIGRATIONS
        ]

        tables = connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name=ANY(%s)",
            (
                target.schema,
                ["work_launch_provisions", "work_launch_origin_bindings"],
            ),
        ).fetchall()
        assert {row["table_name"] for row in tables} == {
            "work_launch_provisions",
            "work_launch_origin_bindings",
        }

        triggers = connection.execute(
            "SELECT trigger.tgname FROM pg_trigger AS trigger "
            "JOIN pg_class AS relation ON relation.oid=trigger.tgrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
            "WHERE namespace.nspname=%s AND NOT trigger.tgisinternal",
            (target.schema,),
        ).fetchall()
        assert {
            row["tgname"]
            for row in triggers
            if row["tgname"]
            in {
                "work_launch_origin_bindings_immutable_delete",
                "work_items_origin_protocol_monotonic",
            }
        } == {
            "work_launch_origin_bindings_immutable_delete",
            "work_items_origin_protocol_monotonic",
        }


@pytest.mark.postgres
def test_native_work_retry_origin_migration_is_installed_and_verified(postgres_targets):
    """Replacement provenance is additive, current, and immutable at the DB boundary."""
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.postgres.migrations import verify_component
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope
    from cli_agent_orchestrator.clients.work_postgres_schema import WORK_POSTGRES_MIGRATIONS
    from cli_agent_orchestrator.clients.work_repository import SCHEMA_VERSION, WorkRepository

    target = postgres_targets.main
    bootstrap_scope(target)
    WorkRepository(target, filesystem_home=postgres_targets.home).initialize()
    assert SCHEMA_VERSION == 4
    assert [migration.version for migration in WORK_POSTGRES_MIGRATIONS] == [1, 2, 3, 4]

    with connect(target, readonly=True) as connection:
        verify_component(connection, WORK_POSTGRES_MIGRATIONS)
        installed = connection.execute(
            "SELECT version,checksum FROM _cao_schema_migrations "
            "WHERE scope='main' AND component='work' ORDER BY version"
        ).fetchall()
        assert [(row["version"], row["checksum"]) for row in installed] == [
            (migration.version, migration.checksum) for migration in WORK_POSTGRES_MIGRATIONS
        ]

        columns = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name='work_launch_retry_bindings'",
            (target.schema,),
        ).fetchall()
        assert {
            "attempt_id",
            "generation",
            "work_item_id",
            "root_attempt_id",
            "prior_attempt_id",
            "prior_scheduler_request_id",
            "stop_evidence_ref",
            "release_event_id",
            "retry_event_id",
            "principal_id",
            "provision_revision",
            "contract_hash",
            "snapshot_hash",
            "delivery_hash",
            "retry_admission_key",
            "current_scheduler_request_id",
        }.issubset({row["column_name"] for row in columns})

        triggers = connection.execute(
            "SELECT trigger.tgname FROM pg_trigger AS trigger "
            "JOIN pg_class AS relation ON relation.oid=trigger.tgrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
            "WHERE namespace.nspname=%s AND relation.relname='work_launch_retry_bindings' "
            "AND NOT trigger.tgisinternal",
            (target.schema,),
        ).fetchall()
        assert {row["tgname"] for row in triggers} == {
            "work_launch_retry_bindings_immutable_update",
            "work_launch_retry_bindings_immutable_delete",
        }
        foreign_keys = connection.execute(
            "SELECT pg_get_constraintdef(constraint_row.oid) AS definition "
            "FROM pg_constraint AS constraint_row "
            "JOIN pg_class AS relation ON relation.oid=constraint_row.conrelid "
            "JOIN pg_namespace AS namespace ON namespace.oid=relation.relnamespace "
            "WHERE namespace.nspname=%s AND relation.relname='work_launch_retry_bindings' "
            "AND constraint_row.contype='f'",
            (target.schema,),
        ).fetchall()
        assert any(
            "current_scheduler_request_id" in row["definition"]
            and "work_scheduler_requests" in row["definition"]
            for row in foreign_keys
        )


@pytest.mark.postgres
def test_work_v3_store_upgrades_without_rewriting_history(postgres_targets, monkeypatch):
    """A populated V3 store requires explicit migration and retains its identity/history."""
    from cli_agent_orchestrator.clients import work_postgres_schema as schema
    from cli_agent_orchestrator.clients import work_repository as repository_module
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.postgres.migrations import verify_component
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    target = postgres_targets.main
    current = schema.WORK_POSTGRES_MIGRATIONS
    previous = current[:3]
    with monkeypatch.context() as legacy:
        legacy.setattr(schema, "WORK_POSTGRES_MIGRATIONS", previous)
        legacy.setattr(repository_module, "WORK_POSTGRES_MIGRATIONS", previous)
        legacy.setattr(repository_module, "SCHEMA_VERSION", 3)
        bootstrap_scope(target)
        store = repository_module.WorkRepository(target, filesystem_home=postgres_targets.home)
        store.initialize()
        job = store.create_job(
            project_id="upgrade-project",
            principal_id="upgrade-owner",
            allowed_providers=["mock_cli"],
            grant_id="upgrade-root",
            budget={"scheduler_units": 10},
        )
        with connect(target, readonly=True) as connection:
            before = [
                tuple(row)
                for row in connection.execute(
                    "SELECT version,checksum FROM _cao_schema_migrations "
                    "WHERE scope='main' AND component='work' ORDER BY version"
                ).fetchall()
            ]
            identity = tuple(
                connection.execute(
                    "SELECT store_identity,store_uuid FROM work_store_control WHERE singleton=1"
                ).fetchone()
            )
    with pytest.raises(repository_module.SchemaMismatch, match="migration history mismatch"):
        repository_module.WorkRepository(target, filesystem_home=postgres_targets.home).initialize()

    bootstrap_scope(target)
    upgraded = repository_module.WorkRepository(target, filesystem_home=postgres_targets.home)
    upgraded.initialize()
    assert upgraded.get_job(job["id"]) == job
    with connect(target, readonly=True) as connection:
        verify_component(connection, current)
        after = [
            tuple(row)
            for row in connection.execute(
                "SELECT version,checksum FROM _cao_schema_migrations "
                "WHERE scope='main' AND component='work' ORDER BY version"
            ).fetchall()
        ]
        assert after[:3] == before
        assert [row[0] for row in after] == [1, 2, 3, 4]
        assert (
            tuple(
                connection.execute(
                    "SELECT store_identity,store_uuid FROM work_store_control WHERE singleton=1"
                ).fetchone()
            )
            == identity
        )
        assert (
            connection.execute("SELECT id FROM work_jobs WHERE id=%s", (job["id"],)).fetchone()[0]
            == job["id"]
        )
        assert (
            connection.execute("SELECT count(*) FROM work_launch_retry_bindings").fetchone()[0] == 0
        )
