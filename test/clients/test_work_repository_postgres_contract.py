"""WorkRepository target identity is PostgreSQL configuration, never a DB path."""

from pathlib import Path
from uuid import uuid4

import pytest

from cli_agent_orchestrator.clients.postgres import DatabaseTarget
from cli_agent_orchestrator.clients.work_repository import WorkRepository


def _target(secret_file: Path, *, store_id: str | None = None) -> DatabaseTarget:
    return DatabaseTarget(
        host="127.0.0.1",
        port=59820,
        database="caos_test",
        user="caos_runtime",
        schema="main",
        store_id=store_id or str(uuid4()),
        secret_file=secret_file,
        sslmode="disable",
    )


def test_repository_accepts_native_target_and_keeps_filesystem_home_separate(tmp_path):
    target = _target(tmp_path / "runtime-secret.json")
    filesystem_home = tmp_path / "profile-home"

    repository = WorkRepository(target, filesystem_home=filesystem_home)

    assert repository.target is target
    assert repository.target.identity == target.identity
    assert repository.filesystem_home == filesystem_home
    assert repository.delivery_content_root.parent == filesystem_home
    assert repository.executable_content_root.parent == filesystem_home


def test_repository_store_comparison_uses_postgres_target_identity(tmp_path):
    store_id = str(uuid4())
    first = WorkRepository(_target(tmp_path / "runtime-secret-a.json", store_id=store_id))
    same = WorkRepository(_target(tmp_path / "runtime-secret-b.json", store_id=store_id))
    other = WorkRepository(_target(tmp_path / "runtime-secret-c.json"))

    assert first.same_store(same)
    assert not first.same_store(other)


@pytest.mark.postgres
def test_repository_transaction_and_read_snapshot_use_native_target(postgres_targets):
    """Runtime entrypoints verify the native Work schema without bootstrapping it."""
    from cli_agent_orchestrator.clients.database import core_migrations
    from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
    from cli_agent_orchestrator.clients.private_plan_postgres_schema import PRIVATE_PLAN_MIGRATIONS
    from cli_agent_orchestrator.clients.work_continuation_postgres_schema import (
        WORKFLOW_CONTINUATION_MIGRATIONS,
    )
    from cli_agent_orchestrator.clients.work_postgres_schema import WORK_POSTGRES_MIGRATIONS
    from cli_agent_orchestrator.clients.work_repository import bootstrap_work_store
    from cli_agent_orchestrator.clients.workflows_postgres_schema import WORKFLOW_MIGRATIONS

    target = postgres_targets.main
    migrate_component(target, core_migrations())
    migrate_component(target, WORK_POSTGRES_MIGRATIONS[:1])
    migrate_component(target, WORKFLOW_MIGRATIONS)
    migrate_component(target, PRIVATE_PLAN_MIGRATIONS)
    migrate_component(target, WORKFLOW_CONTINUATION_MIGRATIONS)
    migrate_component(target, WORK_POSTGRES_MIGRATIONS)
    bootstrap_work_store(target)

    repository = WorkRepository(target, filesystem_home=postgres_targets.home)
    repository.initialize()
    with repository.transaction() as connection:
        assert connection.target.identity == target.identity
        assert connection.in_transaction
        connection.execute(
            "INSERT INTO work_scheduler_policy "
            "(singleton,revision,capacity,max_queue,aging_seconds,dispatch_sequence) "
            "VALUES (1,1,1,1,1,0)"
        )
    with repository.read_snapshot() as connection:
        assert connection.target.identity == target.identity
        assert connection.in_transaction
        assert connection.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        assert connection.execute("SHOW transaction_isolation").fetchone()[0] == "repeatable read"
