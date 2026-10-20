"""Native Work migrations preserve knowledge heads and cursor constraints."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.postgres


def test_native_work_migration_preserves_knowledge_and_installs_cursor_constraints(
    postgres_targets,
):
    from cli_agent_orchestrator.clients.database import CORE_MIGRATIONS
    from cli_agent_orchestrator.clients.postgres.connection import connect
    from cli_agent_orchestrator.clients.postgres.migrations import (
        migrate_component,
        verify_component,
    )
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope
    from cli_agent_orchestrator.clients.work_postgres_schema import WORK_POSTGRES_MIGRATIONS

    target = postgres_targets.main
    migrate_component(target, CORE_MIGRATIONS)
    migrate_component(target, WORK_POSTGRES_MIGRATIONS[:1])

    with connect(target) as connection, connection.transaction():
        connection.execute(
            "INSERT INTO work_knowledge_records (id,scope,scope_id,head_revision,version) "
            "VALUES (%s,%s,%s,%s,%s)",
            ("historic", "project", "project", 1, 1),
        )

    bootstrap_scope(target, seed_work=False)

    with connect(target, readonly=True) as connection:
        verify_component(connection, WORK_POSTGRES_MIGRATIONS)
        row = connection.execute(
            "SELECT id,scope,scope_id,head_revision,version "
            "FROM work_knowledge_records WHERE id=%s",
            ("historic",),
        ).fetchone()
        cursor = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
            (target.schema, "work_knowledge_cursors"),
        ).fetchall()
        foreign_keys = connection.execute(
            "SELECT count(*) FROM pg_constraint c "
            "JOIN pg_class t ON t.oid=c.conrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE n.nspname=%s AND t.relname=%s AND c.contype='f'",
            (target.schema, "work_knowledge_cursors"),
        ).fetchone()[0]

    assert tuple(row) == ("historic", "project", "project", 1, 1)
    assert {item[0] for item in cursor} >= {
        "token_hash",
        "principal_id",
        "scope",
        "scope_id",
        "job_id",
        "grant_id",
        "grant_revision",
        "checkpoint",
        "last_record_id",
        "last_revision",
        "limit_value",
        "expires_at",
        "created_at",
    }
    assert foreign_keys == 3
