"""The additive core migration restores legacy PostgreSQL query indexes."""

from dataclasses import replace

import pytest
from psycopg.errors import InsufficientPrivilege

from cli_agent_orchestrator.clients.postgres import DatabaseError, connect
from cli_agent_orchestrator.clients.postgres.migrations import (
    migrate_component,
    verify_component,
)

pytestmark = pytest.mark.postgres


def test_core_v2_adds_legacy_indexes_without_changing_v1_or_rows(postgres_targets):
    from cli_agent_orchestrator.clients.database import CORE_MIGRATIONS

    target = postgres_targets.main
    assert [migration.version for migration in CORE_MIGRATIONS] == [1, 2, 3]
    assert CORE_MIGRATIONS[0].checksum == (
        "cb4d6f0973a20961854be52f2ac90fd93bd5fbdf6eb06e5b9b2f2fcb3038aae1"
    )
    migrate_component(target, CORE_MIGRATIONS[:1])

    with connect(target) as connection:
        with connection.transaction():
            connection.execute(
                "INSERT INTO memory_metadata "
                "(id,key,memory_type,scope,source_kind,file_path,tags) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                ("core-v1-row", "core-v1-key", "project", "global", "native", "/note.md", ""),
            )

    migrate_component(target, CORE_MIGRATIONS)
    with connect(target, readonly=True) as connection:
        verify_component(connection, CORE_MIGRATIONS)
        indexes = {
            row["indexname"]
            for row in connection.execute(
                "SELECT indexname FROM pg_indexes " "WHERE schemaname=%s AND tablename=ANY(%s)",
                (target.schema, ["memory_metadata", "memory_relationships"]),
            ).fetchall()
        }
        row = connection.execute(
            "SELECT id,key,source_kind FROM memory_metadata WHERE id=%s", ("core-v1-row",)
        ).fetchone()

    assert {
        "idx_memory_scope",
        "idx_memory_updated",
        "idx_memory_type",
        "idx_memory_rel_lookup",
    } <= indexes
    assert tuple(row) == ("core-v1-row", "core-v1-key", "native")

    with connect(target) as runtime:
        with pytest.raises(InsufficientPrivilege):
            runtime.execute(
                "CREATE INDEX test_runtime_cannot_create_core_index ON memory_metadata (key)"
            )


def test_core_v2_index_failure_rolls_back_only_the_new_migration(postgres_targets):
    from cli_agent_orchestrator.clients.database import CORE_MIGRATIONS

    target = postgres_targets.main
    migrate_component(target, CORE_MIGRATIONS[:1])
    broken_v2 = replace(
        CORE_MIGRATIONS[1],
        statements=CORE_MIGRATIONS[1].statements + ("SELECT 1 / 0",),
    )

    with pytest.raises(DatabaseError):
        migrate_component(target, (CORE_MIGRATIONS[0], broken_v2))

    with connect(target, readonly=True) as connection:
        history = connection.execute(
            "SELECT version,checksum FROM _cao_schema_migrations "
            "WHERE component='core' ORDER BY version"
        ).fetchall()
        indexes = {
            row["indexname"]
            for row in connection.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname=%s",
                (target.schema,),
            ).fetchall()
        }
    assert [(row["version"], row["checksum"]) for row in history] == [
        (1, CORE_MIGRATIONS[0].checksum)
    ]
    assert "idx_memory_scope" not in indexes


def test_core_v3_indexes_unreclaimed_window_labels_without_changing_rows(postgres_targets):
    from cli_agent_orchestrator.clients.database import CORE_MIGRATIONS

    assert [m.version for m in CORE_MIGRATIONS] == [1, 2, 3]
    target = postgres_targets.main
    migrate_component(target, CORE_MIGRATIONS[:2])
    with connect(target) as connection:
        with connection.transaction():
            connection.execute(
                "INSERT INTO terminals (id,tmux_session,tmux_window,provider) "
                "VALUES (%s,%s,%s,%s)",
                ("window-old", "cao-s", "worker", "mock_cli"),
            )
        history_before = connection.execute(
            "SELECT version,checksum FROM _cao_schema_migrations WHERE component='core' ORDER BY version"
        ).fetchall()

    migrate_component(target, CORE_MIGRATIONS)
    migrate_component(target, CORE_MIGRATIONS)
    with connect(target, readonly=True) as connection:
        verify_component(connection, CORE_MIGRATIONS)
        definition = connection.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname=%s AND indexname=%s",
            (target.schema, "idx_terminal_live_window"),
        ).fetchone()["indexdef"]
        assert "(tmux_session, tmux_window)" in definition
        assert "deferred_init_runtime_reclaimed IS FALSE" in definition
        assert connection.execute("SELECT id,tmux_window FROM terminals").fetchall() == [
            ("window-old", "worker")
        ]
        history_after = connection.execute(
            "SELECT version,checksum FROM _cao_schema_migrations WHERE component='core' ORDER BY version"
        ).fetchall()
    assert history_after[:2] == history_before
    assert len(history_after) == 3


def test_core_v3_failure_keeps_v2_history_and_rolls_back_index(postgres_targets):
    from cli_agent_orchestrator.clients.database import CORE_MIGRATIONS

    assert len(CORE_MIGRATIONS) == 3
    target = postgres_targets.main
    migrate_component(target, CORE_MIGRATIONS[:2])
    broken = replace(
        CORE_MIGRATIONS[2], statements=CORE_MIGRATIONS[2].statements + ("SELECT 1 / 0",)
    )
    with pytest.raises(DatabaseError):
        migrate_component(target, (*CORE_MIGRATIONS[:2], broken))
    with connect(target, readonly=True) as connection:
        assert connection.execute(
            "SELECT version FROM _cao_schema_migrations WHERE component='core' ORDER BY version"
        ).fetchall() == [(1,), (2,)]
        assert (
            connection.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname=%s AND indexname=%s",
                (target.schema, "idx_terminal_live_window"),
            ).fetchone()
            is None
        )
