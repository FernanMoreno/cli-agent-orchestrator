"""Native PostgreSQL schema checks for the Work component."""

import pytest

from cli_agent_orchestrator.clients.postgres import DatabaseError, connect
from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
from cli_agent_orchestrator.clients.work_postgres_schema import WORK_POSTGRES_MIGRATIONS


@pytest.mark.postgres
def test_work_baseline_v1_migrates_and_verifies_on_native_postgres(postgres_targets):
    """The internal Work schema is valid before cross-component FKs are added."""
    from cli_agent_orchestrator.clients.database import CORE_MIGRATIONS

    migration = WORK_POSTGRES_MIGRATIONS[:1]
    migrate_component(postgres_targets.main, CORE_MIGRATIONS)
    migrate_component(postgres_targets.main, migration)

    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.postgres.migrations import verify_component

    with connect(postgres_targets.main, readonly=True) as connection:
        verify_component(connection, migration)

    maximum = 2**63 - 1
    with connect(postgres_targets.main) as connection:
        with connection.transaction():
            column_type = connection.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name='work_scheduler_policy' "
                "AND column_name='dispatch_sequence'",
                (postgres_targets.main.schema,),
            ).fetchone()
            assert column_type is not None and column_type[0] == "bigint"
            queue_column = connection.execute(
                "SELECT data_type,is_identity,is_nullable "
                "FROM information_schema.columns WHERE table_schema=%s "
                "AND table_name='work_scheduler_requests' AND column_name='queue_sequence'",
                (postgres_targets.main.schema,),
            ).fetchone()
            assert queue_column == ("bigint", "YES", "NO")
            connection.execute(
                "INSERT INTO work_scheduler_policy "
                "(singleton,revision,capacity,max_queue,aging_seconds,dispatch_sequence) "
                "VALUES (1,1,1,1,1,%s)",
                (maximum,),
            )
            stored = connection.execute(
                "SELECT dispatch_sequence FROM work_scheduler_policy WHERE singleton=1"
            ).fetchone()
            assert stored is not None and stored[0] == maximum


@pytest.mark.postgres
def test_only_legacy_cyclic_work_foreign_keys_are_deferred(postgres_targets):
    """Importer cycles can be deferred without weakening normal Work writes."""
    import time

    from cli_agent_orchestrator.clients.database import CORE_MIGRATIONS

    target = postgres_targets.main
    migrate_component(target, CORE_MIGRATIONS)
    migrate_component(target, WORK_POSTGRES_MIGRATIONS[:1])
    cyclic_constraints = {
        "fk_attempts_b08af97cd0be",
        "fk_attempts_32702b971058",
        "fk_grants_b5ac3f7ae1a6",
        "fk_items_716e44d2dad0",
        "fk_items_a324a6bce182",
        "fk_knowledge_revision_353137de328c",
        "fk_results_e417885a43f2",
    }

    with connect(target) as connection:
        with connection.transaction():
            rows = connection.execute(
                "SELECT constraint_row.conname, constraint_row.condeferrable, "
                "constraint_row.condeferred "
                "FROM pg_constraint AS constraint_row "
                "JOIN pg_class AS child ON child.oid=constraint_row.conrelid "
                "JOIN pg_namespace AS child_schema ON child_schema.oid=child.relnamespace "
                "WHERE child_schema.nspname=%s AND left(child.relname,5)='work_' "
                "AND constraint_row.contype='f'",
                (target.schema,),
            ).fetchall()
            assert {row[0] for row in rows if row[1]} == cyclic_constraints
            assert all(not row[2] for row in rows if row[0] in cyclic_constraints)

        now = time.time()
        with connection.transaction():
            connection.execute(
                "SET CONSTRAINTS fk_attempts_b08af97cd0be, fk_attempts_32702b971058, "
                "fk_items_716e44d2dad0, fk_results_e417885a43f2 DEFERRED"
            )
            connection.execute(
                "INSERT INTO work_jobs "
                "(id,project_id,principal_id,allowed_providers,grant_id,created_at) "
                "VALUES ('cycle-job','project','principal','[]','grant',%s)",
                (now,),
            )
            connection.execute(
                "INSERT INTO work_items "
                "(id,job_id,operation_kind,idempotency_key,request_hash,contract_id,"
                "accepted_result_id,created_at) "
                "VALUES ('cycle-item','cycle-job','launch','cycle-key','hash','contract',"
                "'cycle-result',%s)",
                (now,),
            )
            connection.execute(
                "INSERT INTO work_attempts "
                "(id,work_item_id,attempt_number,generation,provider,lease_expires_at,created_at) "
                "VALUES ('cycle-attempt','cycle-item',1,1,'mock_cli',%s,%s)",
                (now + 60, now),
            )
            connection.execute(
                "INSERT INTO work_results "
                "(id,attempt_id,content_hash,immutable_location,byte_length,validation_state,"
                "validator_id,validation_evidence,created_at) "
                "VALUES ('cycle-result','cycle-attempt','hash','location',0,'verified',"
                "'test','{}',%s)",
                (now,),
            )

        with connection.transaction():
            with pytest.raises(DatabaseError):
                with connection.transaction():
                    connection.execute(
                        "INSERT INTO work_attempts "
                        "(id,work_item_id,attempt_number,generation,provider,lease_expires_at,created_at) "
                        "VALUES ('invalid-attempt','missing-item',1,1,'mock_cli',%s,%s)",
                        (now + 60, now),
                    )

        with pytest.raises(DatabaseError):
            with connection.transaction():
                connection.execute("SET CONSTRAINTS fk_attempts_b08af97cd0be DEFERRED")
                connection.execute(
                    "INSERT INTO work_attempts "
                    "(id,work_item_id,attempt_number,generation,provider,lease_expires_at,created_at) "
                    "VALUES ('deferred-invalid-attempt','missing-item',1,1,'mock_cli',%s,%s)",
                    (now + 60, now),
                )
        with connection.transaction():
            stored = connection.execute(
                "SELECT count(*) FROM work_attempts WHERE id='deferred-invalid-attempt'"
            ).fetchone()
            assert stored is not None and stored[0] == 0


@pytest.mark.postgres
def test_native_contract_json_versions_use_postgres_number_semantics(postgres_targets):
    """Accept integer JSON versions while rejecting decimal encodings."""
    import time

    from cli_agent_orchestrator.clients.database import CORE_MIGRATIONS

    target = postgres_targets.main
    migrate_component(target, CORE_MIGRATIONS)
    migrate_component(target, WORK_POSTGRES_MIGRATIONS[:1])
    now = time.time()
    with connect(target) as connection:
        with connection.transaction():
            connection.execute(
                "INSERT INTO work_principals (id,issuer,subject,kind,created_at) "
                "VALUES ('principal','test','principal','local_operator',%s)",
                (now,),
            )
            connection.execute(
                "INSERT INTO work_jobs "
                "(id,project_id,principal_id,allowed_providers,grant_id,budget,priority,created_at) "
                "VALUES ('job','project','principal','[\"mock_cli\"]','grant','{}',0,%s)",
                (now,),
            )
            connection.execute(
                "INSERT INTO work_grants "
                "(id,revision,job_id,principal_id,allowed_providers,permissions,expires_at,created_at) "
                "VALUES ('grant',1,'job','principal','[\"mock_cli\"]','{}',%s,%s)",
                (now + 600, now),
            )
            connection.execute(
                "INSERT INTO work_delegation_snapshots "
                "(id,schema_version,job_id,contract_id,binding_key,request_hash,scope,scope_id,"
                "producer_principal_id,source_hash,delivered_hash,content,redacted,truncated,created_at) "
                "VALUES ('snapshot',1,'job','contract','binding',%s,'job','job','principal',%s,%s,"
                "%s,0,0,%s)",
                ("a" * 64, "b" * 64, "c" * 64, b"", now),
            )

            attempts = ("attempt-valid-v1", "attempt-invalid-v1", "attempt-valid-v2")
            for attempt_id in attempts:
                item_id = f"work-{attempt_id}"
                connection.execute(
                    "INSERT INTO work_items "
                    "(id,job_id,operation_kind,idempotency_key,request_hash,contract_id,snapshot_id,created_at) "
                    "VALUES (%s,'job','launch',%s,%s,'contract','snapshot',%s)",
                    (item_id, item_id, "d" * 64, now),
                )
                connection.execute(
                    "INSERT INTO work_attempts "
                    "(id,work_item_id,attempt_number,generation,provider,lease_expires_at,created_at) "
                    "VALUES (%s,%s,1,1,'mock_cli',%s,%s)",
                    (attempt_id, item_id, now + 600, now),
                )

            def insert_binding(attempt_id: str, contract_json: str) -> None:
                connection.execute(
                    "INSERT INTO work_dispatch_bindings "
                    "(attempt_id,generation,job_id,work_item_id,principal_id,grant_id,grant_revision,"
                    "contract_id,contract_json,contract_hash,snapshot_id,created_at) "
                    "VALUES (%s,1,'job',%s,'principal','grant',1,'contract',%s,%s,'snapshot',%s)",
                    (attempt_id, f"work-{attempt_id}", contract_json, "e" * 64, now),
                )

            insert_binding("attempt-valid-v1", '{"schema_version":1,"id":"contract"}')
            with pytest.raises(DatabaseError):
                with connection.transaction():
                    insert_binding("attempt-invalid-v1", '{"schema_version":1.0,"id":"contract"}')
            insert_binding("attempt-valid-v2", '{"schema_version":1,"id":"contract"}')
            connection.execute(
                "INSERT INTO work_dispatch_v2_evidence "
                "(attempt_id,generation,contract_json,contract_hash) VALUES (%s,1,%s,%s)",
                ("attempt-valid-v1", '{"schema_version":2}', "f" * 64),
            )
            with pytest.raises(DatabaseError):
                with connection.transaction():
                    connection.execute(
                        "INSERT INTO work_dispatch_v2_evidence "
                        "(attempt_id,generation,contract_json,contract_hash) VALUES (%s,1,%s,%s)",
                        ("attempt-valid-v2", '{"schema_version":2.0}', "f" * 64),
                    )

            assert (
                connection.execute("SELECT count(*) FROM work_dispatch_bindings").fetchone()[0] == 2
            )
            assert (
                connection.execute("SELECT count(*) FROM work_dispatch_v2_evidence").fetchone()[0]
                == 1
            )


@pytest.mark.postgres
def test_work_v2_to_v3_upgrade_is_additive_and_preserves_legacy_checksums(postgres_targets):
    """V3 appends message tables while preserving the installed V1/V2 contract."""
    from cli_agent_orchestrator.clients.database import CORE_MIGRATIONS
    from cli_agent_orchestrator.clients.postgres.migrations import verify_component
    from cli_agent_orchestrator.clients.work_postgres_schema import WORK_TABLES
    from cli_agent_orchestrator.clients.workflows_postgres_schema import WORKFLOW_MIGRATIONS

    migrations = WORK_POSTGRES_MIGRATIONS[:3]
    assert [migration.version for migration in migrations] == [1, 2, 3]
    assert len(WORK_POSTGRES_MIGRATIONS) >= 3
    assert migrations[0].checksum == (
        "dc9b319182dda2e94b82df009ab2484aeaa2ce7d99408787703d7017c9732608"
    )
    assert migrations[1].checksum == (
        "2641e252c3b05534758b9c38adb038d3827ad44cac56f6962428d0176fecb921"
    )
    assert migrations[0].tables == WORK_TABLES
    assert migrations[2].tables
    assert set(migrations[2].tables).isdisjoint(WORK_TABLES)

    target = postgres_targets.main
    migrate_component(target, CORE_MIGRATIONS)
    migrate_component(target, WORKFLOW_MIGRATIONS)
    migrate_component(target, migrations[:2])

    with connect(target, readonly=True) as connection:
        verify_component(connection, migrations[:2])
        installed_v2 = connection.execute(
            "SELECT version,checksum FROM _cao_schema_migrations "
            "WHERE scope='main' AND component='work' ORDER BY version"
        ).fetchall()
        assert [(row["version"], row["checksum"]) for row in installed_v2] == [
            (migration.version, migration.checksum) for migration in migrations[:2]
        ]
        for table in migrations[2].tables:
            assert (
                connection.execute(
                    "SELECT to_regclass(%s)", (f"{target.schema}.{table}",)
                ).fetchone()[0]
                is None
            )

    migrate_component(target, migrations)

    with connect(target, readonly=True) as connection:
        verify_component(connection, migrations)
        installed_v3 = connection.execute(
            "SELECT version,checksum FROM _cao_schema_migrations "
            "WHERE scope='main' AND component='work' ORDER BY version"
        ).fetchall()
        assert [(row["version"], row["checksum"]) for row in installed_v3] == [
            (migration.version, migration.checksum) for migration in migrations
        ]
        for table in migrations[2].tables:
            assert (
                connection.execute(
                    "SELECT to_regclass(%s)", (f"{target.schema}.{table}",)
                ).fetchone()[0]
                is not None
            )


@pytest.mark.postgres
def test_failed_work_v3_forward_ddl_rolls_back_tables_and_version_receipt(postgres_targets):
    """A failed V3 transaction leaves the V2 schema and ledger usable for retry."""
    from dataclasses import replace

    from cli_agent_orchestrator.clients.database import CORE_MIGRATIONS
    from cli_agent_orchestrator.clients.postgres.migrations import verify_component
    from cli_agent_orchestrator.clients.workflows_postgres_schema import WORKFLOW_MIGRATIONS

    migrations = WORK_POSTGRES_MIGRATIONS[:3]
    assert [migration.version for migration in migrations] == [1, 2, 3]
    target = postgres_targets.main
    migrate_component(target, CORE_MIGRATIONS)
    migrate_component(target, WORKFLOW_MIGRATIONS)
    migrate_component(target, migrations[:2])

    failure_probe = "work_message_v3_rollback_probe"
    failing_v3 = replace(
        migrations[2],
        statements=(
            *migrations[2].statements,
            f"CREATE TABLE {failure_probe} (id INTEGER PRIMARY KEY)",
            "CREATE TABLE work_message_v3_invalid_ddl (id INTEGER, id INTEGER)",
        ),
    )
    failing_migrations = (*migrations[:2], failing_v3)

    with pytest.raises(DatabaseError):
        migrate_component(target, failing_migrations)

    with connect(target, readonly=True) as connection:
        verify_component(connection, migrations[:2])
        installed = connection.execute(
            "SELECT version,checksum FROM _cao_schema_migrations "
            "WHERE scope='main' AND component='work' ORDER BY version"
        ).fetchall()
        assert [(row["version"], row["checksum"]) for row in installed] == [
            (migration.version, migration.checksum) for migration in migrations[:2]
        ]
        for table in (*migrations[2].tables, failure_probe):
            assert (
                connection.execute(
                    "SELECT to_regclass(%s)", (f"{target.schema}.{table}",)
                ).fetchone()[0]
                is None
            )

    migrate_component(target, migrations)
    with connect(target, readonly=True) as connection:
        verify_component(connection, migrations)
