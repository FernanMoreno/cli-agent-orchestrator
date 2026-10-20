"""Native setup lifetime and atomic PostgreSQL migration failure contracts."""

from test.postgres.fixtures import make_orm_engine
from uuid import uuid4

import pytest

from cli_agent_orchestrator.clients import database, work_repository
from cli_agent_orchestrator.clients.postgres import Connection, DatabaseError, connect
from cli_agent_orchestrator.clients.postgres.migrations import (
    ComponentMigration,
    SchemaMismatch,
    migrate_component,
)
from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

pytestmark = pytest.mark.postgres


@pytest.fixture
def captured_connections(monkeypatch):
    connections = []
    original = Connection.__init__

    def capture(self, *args, **kwargs):
        original(self, *args, **kwargs)
        connections.append(self)

    monkeypatch.setattr(Connection, "__init__", capture)
    return connections


def assert_closed(connections):
    assert connections
    assert all(connection.raw.closed for connection in connections)


@pytest.mark.parametrize("operation", ["work", "terminals"])
def test_damaged_schema_setup_closes_owned_connection(
    postgres_targets, monkeypatch, captured_connections, operation
):
    target = postgres_targets.main
    bootstrap_scope(target)
    with connect(target.migration_target()) as owner:
        owner.execute(
            "UPDATE _cao_schema_migrations SET checksum=%s WHERE component=%s",
            ("0" * 64, "work" if operation == "work" else "core"),
        )
    captured_connections.clear()
    if operation == "work":
        with pytest.raises(work_repository.SchemaMismatch):
            with work_repository.WorkRepository(target).connection():
                pytest.fail("damaged schema must fail before handoff")
    else:
        engine = make_orm_engine(target)
        monkeypatch.setattr(database, "engine", engine)
        try:
            with pytest.raises(SchemaMismatch):
                database.init_db()
        finally:
            engine.dispose()
    assert_closed(captured_connections)


@pytest.mark.parametrize("fail_body", [False, True])
def test_successful_handoff_preserves_commit_and_rollback(
    postgres_targets, captured_connections, fail_body
):
    target = postgres_targets.main
    bootstrap_scope(target)
    repository = work_repository.WorkRepository(target)
    captured_connections.clear()

    def write():
        with repository.transaction() as connection:
            connection.execute(
                "INSERT INTO flows(name,file_path,schedule,agent_profile,provider,enabled) VALUES (%s,%s,%s,%s,%s,%s)",
                ("probe", "/probe", "* * * * *", "developer", "mock_cli", False),
            )
            if fail_body:
                raise RuntimeError("body failed")

    if fail_body:
        with pytest.raises(RuntimeError, match="body failed"):
            write()
    else:
        write()
    assert_closed(captured_connections)
    with connect(target) as observer:
        assert observer.execute("SELECT count(*) FROM flows WHERE name='probe'").fetchone()[0] == (
            0 if fail_body else 1
        )


@pytest.mark.parametrize("fail_later_statement", [False, True])
def test_owner_migration_is_atomic_and_closes_handles(
    postgres_targets, captured_connections, fail_later_statement
):
    target = postgres_targets.main
    baseline_table = f"atomic_probe_baseline_{uuid4().hex[:12]}"
    baseline = ComponentMigration(
        component=f"atomic_probe_baseline_{uuid4().hex}",
        scope="main",
        version=1,
        statements=(f"CREATE TABLE {baseline_table} (value INTEGER)",),
        tables=(baseline_table,),
    )
    migrate_component(target, (baseline,))
    statements = ["CREATE TABLE atomic_probe(id BIGINT PRIMARY KEY)"]
    statements.append(
        "ALTER TABLE missing_atomic_probe ADD COLUMN value TEXT"
        if fail_later_statement
        else "ALTER TABLE atomic_probe ADD COLUMN value TEXT"
    )
    migrations = (
        ComponentMigration("atomic_probe", "main", 1, tuple(statements), ("atomic_probe",)),
    )
    captured_connections.clear()
    if fail_later_statement:
        with pytest.raises(DatabaseError):
            migrate_component(target, migrations)
    else:
        migrate_component(target, migrations)
    assert_closed(captured_connections)
    with connect(target.migration_target()) as owner:
        exists = owner.execute(
            "SELECT to_regclass(%s)", (f"{target.schema}.atomic_probe",)
        ).fetchone()[0]
        assert (exists is None) == fail_later_statement
        if fail_later_statement:
            assert (
                owner.execute(
                    "SELECT count(*) FROM _cao_schema_migrations "
                    "WHERE scope=%s AND component='atomic_probe'",
                    (target.scope,),
                ).fetchone()[0]
                == 0
            )
        else:
            assert (
                owner.execute(
                    "SELECT count(*) FROM _cao_schema_migrations WHERE component='atomic_probe'"
                ).fetchone()[0]
                == 1
            )
