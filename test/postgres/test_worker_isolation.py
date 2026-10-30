"""Verify the shared PostgreSQL worker namespace is reset and self-repaired."""

from test.postgres.fixtures import (
    _POSTGRES_LOCAL_MAX_CONNECTIONS,
    _POSTGRES_MIN_TMPFS_FREE_BYTES,
    _postgres_tmpfs_available_bytes,
    reset_postgres_worker_targets,
)

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import connect
from cli_agent_orchestrator.clients.postgres.migrations import verify_runtime_role
from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("damage", ["checksum", "manifest", "identity", "missing_version"])
def test_worker_reset_repairs_owned_metadata_rows(postgres_worker_targets, damage):
    targets = postgres_worker_targets
    target = targets.main
    reset_postgres_worker_targets(targets)
    with connect(target, readonly=True) as connection:
        expected = [
            tuple(row)
            for row in connection.execute(
                "SELECT scope,component,version,checksum,manifest_digest "
                "FROM _cao_schema_migrations ORDER BY scope,component,version"
            ).fetchall()
        ]
    with connect(target.migration_target()) as connection, connection.transaction():
        statements = {
            "checksum": "UPDATE _cao_schema_migrations SET checksum='altered' "
            "WHERE component='work' AND version=1",
            "manifest": "UPDATE _cao_schema_migrations SET manifest_digest='altered' "
            "WHERE component='work' AND version=1",
            "identity": "UPDATE _cao_store_identity SET store_id='altered' WHERE singleton=1",
            "missing_version": "DELETE FROM _cao_schema_migrations "
            "WHERE component='work' AND version=1",
        }
        connection.execute(statements[damage])
    reset_postgres_worker_targets(targets)
    with connect(target, readonly=True) as connection:
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT scope,component,version,checksum,manifest_digest "
                "FROM _cao_schema_migrations ORDER BY scope,component,version"
            ).fetchall()
        ] == expected
        assert connection.execute(
            "SELECT scope,store_id FROM _cao_store_identity WHERE singleton=1"
        ).fetchone() == (target.scope, target.store_id)


def test_postgres_capacity_guard_parses_only_the_expected_tmpfs_mount():
    output = (
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        "tmpfs 1048576 917504 131072 88% /var/lib/postgresql\n"
    )
    assert _postgres_tmpfs_available_bytes(output) == 128 * 1024 * 1024

    with pytest.raises(RuntimeError, match="Cannot verify"):
        _postgres_tmpfs_available_bytes(
            "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            "tmpfs 1048576 917504 131072 88% /var/lib/postgresql/data\n"
        )


def test_local_postgres_service_has_v3_capacity_and_lock_budget(postgres_service):
    metadata = postgres_service.metadata
    assert metadata["service_profile"] == "cao-spec021-local-postgres-v3"
    assert metadata["storage_limit_bytes"] == 3 * 1024 * 1024 * 1024
    assert metadata["memory_limit_bytes"] == 4 * 1024 * 1024 * 1024
    assert metadata["memory_swap_bytes"] == 4 * 1024 * 1024 * 1024
    assert _POSTGRES_MIN_TMPFS_FREE_BYTES == 128 * 1024 * 1024

    with postgres_service.connect_admin() as connection:
        settings = connection.execute(
            "SELECT current_setting('max_connections'), "
            "current_setting('max_locks_per_transaction'), "
            "current_setting('max_wal_size'), current_setting('min_wal_size'), "
            "current_setting('checkpoint_timeout')"
        ).fetchone()
    assert settings == (str(_POSTGRES_LOCAL_MAX_CONNECTIONS), "256", "256MB", "64MB", "1min")


def test_worker_reset_clears_rows_and_repairs_only_its_schema(postgres_worker_targets):
    targets = postgres_worker_targets
    target = targets.main
    reset_postgres_worker_targets(targets)
    with connect(target, readonly=True) as connection:
        server_version = connection.execute(
            "SELECT current_setting('server_version_num')::integer"
        ).fetchone()
    assert server_version is not None and int(server_version[0]) // 10000 == 18

    store = RemoteOperationStore(target)
    store.activate_runtime("isolation-runtime", "before-reset")
    assert store.get_runtime("isolation-runtime") is not None

    reset_postgres_worker_targets(targets)
    assert RemoteOperationStore(target).get_runtime("isolation-runtime") is None

    with connect(target.migration_target()) as connection, connection.transaction():
        connection.execute("DROP TABLE remote_observation_revisions")

    reset_postgres_worker_targets(targets)
    assert RemoteOperationStore(target).get_runtime("isolation-runtime") is None
    assert RemoteOperationStore(target).activate_runtime("isolation-runtime", "after-repair") == 1
    reset_postgres_worker_targets(targets)


def test_worker_reset_restores_runtime_acl_baseline(postgres_worker_targets):
    targets = postgres_worker_targets
    target = targets.main
    reset_postgres_worker_targets(targets)
    relation = f"{target.schema}.work_memory_access_audit"

    def privileges():
        with connect(target, readonly=True) as connection:
            return connection.execute(
                "SELECT has_table_privilege(%s,%s,'INSERT'), "
                "has_table_privilege(%s,%s,'SELECT'), "
                "has_table_privilege(%s,%s,'TRUNCATE'), "
                "has_schema_privilege(%s,%s,'CREATE'), "
                "has_table_privilege(%s,%s,'SELECT'), "
                "has_table_privilege(%s,%s,'INSERT'), "
                "has_table_privilege(%s,%s,'UPDATE')",
                (
                    target.user,
                    relation,
                    target.user,
                    relation,
                    target.user,
                    relation,
                    target.user,
                    target.schema,
                    target.user,
                    f"{target.schema}._cao_schema_migrations",
                    target.user,
                    f"{target.schema}._cao_schema_migrations",
                    target.user,
                    f"{target.schema}._cao_schema_migrations",
                ),
            ).fetchone()

    def object_access(kind: str):
        catalog = "pg_class" if kind == "sequence" else "pg_proc"
        namespace_column = "relnamespace" if kind == "sequence" else "pronamespace"
        predicate = "object.relkind='S'" if kind == "sequence" else "TRUE"
        object_privilege = (
            "has_sequence_privilege(%s, object.oid, 'USAGE') AND "
            "has_sequence_privilege(%s, object.oid, 'SELECT')"
            if kind == "sequence"
            else "has_function_privilege(%s, object.oid, 'EXECUTE')"
        )
        role_parameters = (target.user, target.user) if kind == "sequence" else (target.user,)
        with connect(target, readonly=True) as connection:
            return connection.execute(
                f"SELECT count(*), count(*) FILTER (WHERE {object_privilege}) "
                f"FROM pg_catalog.{catalog} AS object "
                f"JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid=object.{namespace_column} "
                f"WHERE namespace.nspname=%s AND {predicate}",
                (*role_parameters, target.schema),
            ).fetchone()

    assert privileges() == (True, True, False, False, True, False, False)
    sequence_count, sequence_access_count = object_access("sequence")
    function_count, function_access_count = object_access("function")
    assert sequence_count > 0 and sequence_access_count == sequence_count
    assert function_count > 0 and function_access_count == function_count
    with connect(target.migration_target()) as connection, connection.transaction():
        connection.execute(
            sql.SQL("REVOKE INSERT ON {}.{} FROM {}").format(
                sql.Identifier(target.schema),
                sql.Identifier("work_memory_access_audit"),
                sql.Identifier(target.user),
            )
        )
        connection.execute(
            sql.SQL("GRANT TRUNCATE ON {}.{} TO {}").format(
                sql.Identifier(target.schema),
                sql.Identifier("work_memory_access_audit"),
                sql.Identifier(target.user),
            )
        )
        connection.execute(
            sql.SQL("GRANT CREATE ON SCHEMA {} TO {}").format(
                sql.Identifier(target.schema), sql.Identifier(target.user)
            )
        )
        connection.execute(
            sql.SQL("GRANT ALL ON {}.{} TO {}").format(
                sql.Identifier(target.schema),
                sql.Identifier("_cao_schema_migrations"),
                sql.Identifier(target.user),
            )
        )
        connection.execute(
            sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM {}").format(
                sql.Identifier(target.schema), sql.Identifier(target.user)
            )
        )
        connection.execute(
            sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {} FROM {}").format(
                sql.Identifier(target.schema), sql.Identifier(target.user)
            )
        )
    assert privileges() == (False, True, True, True, True, True, True)
    assert object_access("sequence")[1] == 0
    assert object_access("function")[1] == 0

    reset_postgres_worker_targets(targets)

    assert privileges() == (True, True, False, False, True, False, False)
    assert object_access("sequence") == (sequence_count, sequence_count)
    assert object_access("function") == (function_count, function_count)


def test_worker_reset_keeps_import_receipts_runtime_read_only(postgres_worker_targets):
    targets = postgres_worker_targets
    reset_postgres_worker_targets(targets)

    for target in (targets.main, targets.auth, targets.peer):
        receipt = f"{target.schema}._cao_import_receipts"
        with connect(target, readonly=True) as connection:
            privileges = connection.execute(
                """SELECT has_table_privilege(%s,%s,'SELECT'),
                has_table_privilege(%s,%s,'INSERT'),
                has_table_privilege(%s,%s,'UPDATE'),
                has_table_privilege(%s,%s,'DELETE'),
                has_table_privilege(%s,%s,'TRUNCATE'),
                has_table_privilege(%s,%s,'REFERENCES'),
                has_table_privilege(%s,%s,'TRIGGER'),
                has_table_privilege(%s,%s,'MAINTAIN')""",
                tuple(value for _ in range(8) for value in (target.user, receipt)),
            ).fetchone()
        assert privileges is not None
        assert tuple(privileges) == (True, False, False, False, False, False, False, False)


@pytest.mark.parametrize("public", [False, True], ids=["runtime", "public"])
def test_worker_reset_keeps_resource_authority_and_columns_read_only(
    postgres_worker_targets, public
):
    from cli_agent_orchestrator.clients.admission_bootstrap_schema import RESOURCE_ENVELOPE_TABLE

    targets = postgres_worker_targets
    target = targets.main
    reset_postgres_worker_targets(targets)
    relation = f"{target.schema}.{RESOURCE_ENVELOPE_TABLE}"
    with connect(target.migration_target()) as owner:
        owner.execute(
            sql.SQL("GRANT UPDATE(active) ON {} TO {}").format(
                sql.Identifier(target.schema, RESOURCE_ENVELOPE_TABLE),
                sql.SQL("PUBLIC") if public else sql.Identifier(target.user),
            )
        )
    reset_postgres_worker_targets(targets)
    with connect(target, readonly=True) as runtime:
        verify_runtime_role(runtime)
        privileges = runtime.execute(
            "SELECT has_table_privilege(current_user,%s,'SELECT'),"
            "has_any_column_privilege(current_user,%s,'INSERT'),"
            "has_any_column_privilege(current_user,%s,'UPDATE'),"
            "has_table_privilege(current_user,%s,'DELETE')",
            (relation,) * 4,
        ).fetchone()
        assert tuple(privileges) == (True, False, False, False)
