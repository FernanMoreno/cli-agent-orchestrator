"""Native PostgreSQL persistence invariants for the interaction component."""

from __future__ import annotations

from test.postgres.interactions import postgres_interaction_store

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.interaction_repository import (
    INTERACTION_MIGRATIONS,
    InteractionSchemaMismatch,
)
from cli_agent_orchestrator.clients.postgres import connect

pytestmark = pytest.mark.postgres


def test_interaction_schema_is_independently_versioned(postgres_interaction_store):
    store = postgres_interaction_store
    store.repository.initialize()
    store.repository.initialize()

    with connect(store.target, readonly=True) as connection:
        tables = {
            row["table_name"]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=%s AND table_type='BASE TABLE'",
                (store.target.schema,),
            ).fetchall()
        }
        assert {
            "interaction_sources",
            "interaction_questions",
            "interaction_answers",
            "interaction_deliveries",
            "interaction_events",
            "interaction_private_audit",
        } <= tables
        assert "work_migrations" not in tables
        versions = connection.execute(
            "SELECT version,checksum FROM _cao_schema_migrations "
            "WHERE scope='main' AND component='interactions' ORDER BY version"
        ).fetchall()
        assert [(row["version"], row["checksum"]) for row in versions] == [
            (migration.version, migration.checksum) for migration in INTERACTION_MIGRATIONS
        ]

        source_columns = {
            row["column_name"]: row
            for row in connection.execute(
                "SELECT column_name,data_type,is_nullable,column_default "
                "FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name='interaction_sources'",
                (store.target.schema,),
            ).fetchall()
        }
        assert source_columns["capability_available"]["data_type"] == "boolean"
        assert source_columns["capability_available"]["is_nullable"] == "NO"
        assert source_columns["capability_available"]["column_default"] == "true"


def test_interaction_startup_preserves_work_component_history(postgres_interaction_store):
    store = postgres_interaction_store
    with connect(store.target, readonly=True) as connection:
        work_before = connection.execute(
            "SELECT version,checksum FROM _cao_schema_migrations "
            "WHERE scope='main' AND component='work' ORDER BY version"
        ).fetchall()

    store.repository.initialize()

    with connect(store.target, readonly=True) as connection:
        work_after = connection.execute(
            "SELECT version,checksum FROM _cao_schema_migrations "
            "WHERE scope='main' AND component='work' ORDER BY version"
        ).fetchall()
    assert [tuple(row) for row in work_after] == [tuple(row) for row in work_before]


def test_event_append_requires_caller_owned_shared_transaction(postgres_interaction_store):
    store = postgres_interaction_store
    repository = store.repository
    with connect(store.target) as unrelated_connection:
        with pytest.raises(ValueError, match="caller-owned transaction"):
            repository.append_event(
                unrelated_connection,
                event_type="interaction.test",
                source_terminal_id=None,
                source_incarnation_id=None,
                actor_kind="system",
                actor_id=None,
                reason_code="test",
            )

    with repository.transaction() as connection:
        assert connection.target.identity == store.target.identity
        assert connection.in_transaction
        sequence = repository.append_event(
            connection,
            event_type="interaction.test",
            source_terminal_id=None,
            source_incarnation_id=None,
            actor_kind="system",
            actor_id=None,
            reason_code="test",
            metadata={"question_id": "opaque"},
        )
        assert sequence > 0

    with repository.read_connection() as connection:
        event = connection.execute(
            "SELECT event_type,metadata_json FROM interaction_events WHERE sequence=%s",
            (sequence,),
        ).fetchone()
        assert event["event_type"] == "interaction.test"
        assert "prompt" not in event["metadata_json"]


def test_event_metadata_is_allowlisted_and_bounded(postgres_interaction_store):
    repository = postgres_interaction_store.repository
    with repository.transaction() as connection:
        for metadata in (
            {"note": "private prompt text"},
            {"response": {"text": "private answer"}},
            {"safe_reason": "x" * 4096},
        ):
            with pytest.raises(ValueError):
                repository.append_event(
                    connection,
                    event_type="interaction.test",
                    source_terminal_id=None,
                    source_incarnation_id=None,
                    actor_kind="system",
                    actor_id=None,
                    reason_code="test",
                    metadata=metadata,
                )


@pytest.mark.parametrize(
    ("object_name", "object_kind", "table_name"),
    [
        ("interaction_events_no_update", "trigger", "interaction_events"),
        (
            "interaction_question_versions_no_delete",
            "trigger",
            "interaction_question_versions",
        ),
        ("interaction_delivery_attempt_history", "index", None),
    ],
)
def test_schema_verification_rejects_removed_append_only_guards_and_indexes(
    postgres_interaction_store, object_name, object_kind, table_name
):
    target = postgres_interaction_store.target
    repository = postgres_interaction_store.repository
    with connect(target.migration_target()) as connection:
        identifier = sql.Identifier(object_name)
        if object_kind == "trigger":
            statement = sql.SQL("DROP TRIGGER IF EXISTS {} ON {}").format(
                identifier, sql.Identifier(table_name)
            )
        else:
            statement = sql.SQL("DROP INDEX IF EXISTS {}").format(identifier)
        connection.execute(statement)

    with pytest.raises(InteractionSchemaMismatch):
        repository.initialize()
    with repository.transaction() as connection:
        with pytest.raises(InteractionSchemaMismatch):
            repository._verify(connection)
