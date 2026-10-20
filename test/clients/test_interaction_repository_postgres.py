"""Real PostgreSQL contracts for the shared interaction/Work repository."""

from __future__ import annotations

import pytest

from cli_agent_orchestrator.clients.interaction_repository import InteractionRepository
from cli_agent_orchestrator.clients.postgres import IntegrityError, connect
from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch
from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope
from cli_agent_orchestrator.clients.work_repository import WorkRepository

pytestmark = pytest.mark.postgres


def _repository(postgres_targets):
    target = postgres_targets["main"]
    bootstrap_scope(target)
    work_repository = WorkRepository(target)
    repository = InteractionRepository(target, work_repository=work_repository)
    return target, repository


def test_interaction_repository_uses_shared_native_work_transactions(postgres_targets):
    target, repository = _repository(postgres_targets)

    with repository.transaction() as connection:
        assert connection.target.identity == target.identity
        assert connection.in_transaction
        repository.append_event(
            connection,
            event_type="interaction.postgres_contract",
            source_terminal_id=None,
            source_incarnation_id=None,
            actor_kind="system",
            actor_id=None,
            reason_code="contract_test",
        )

    with repository.read_connection() as connection:
        assert connection.target.identity == target.identity
        event = connection.execute(
            "SELECT event_type FROM interaction_events WHERE event_type=%s",
            ("interaction.postgres_contract",),
        ).fetchone()
    assert event["event_type"] == "interaction.postgres_contract"


def test_component_schema_verifies_and_rejects_runtime_drift(postgres_targets):
    target, repository = _repository(postgres_targets)
    repository.initialize()

    with connect(target.migration_target()) as connection:
        connection.execute("ALTER TABLE interaction_groups ADD COLUMN test_untracked_drift TEXT")
    with connect(target) as connection:
        with pytest.raises(SchemaMismatch):
            repository.verify(connection)


def test_question_order_column_preserves_explicit_imported_sequence(postgres_targets):
    target, repository = _repository(postgres_targets)
    repository.initialize()

    with connect(target, readonly=True) as connection:
        question_columns = {
            row["column_name"]: row
            for row in connection.execute(
                "SELECT column_name,data_type,is_identity,identity_generation "
                "FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name='interaction_questions'",
                (target.schema,),
            ).fetchall()
        }
    assert question_columns["question_sequence"]["is_identity"] == "YES"
    assert question_columns["question_sequence"]["identity_generation"] == "BY DEFAULT"
    assert question_columns["created_at"]["data_type"] == "double precision"


def test_append_only_interaction_history_is_enforced_by_postgres(postgres_targets):
    _, repository = _repository(postgres_targets)
    repository.initialize()

    with repository.transaction() as connection:
        sequence = repository.append_event(
            connection,
            event_type="interaction.append_only_contract",
            source_terminal_id=None,
            source_incarnation_id=None,
            actor_kind="system",
            actor_id=None,
            reason_code="contract_test",
        )
        assert sequence > 0

    try:
        with repository.transaction() as connection:
            connection.execute(
                "UPDATE interaction_events SET reason_code=%s WHERE sequence=%s",
                ("changed", sequence),
            )
    except IntegrityError:
        pass
    else:
        raise AssertionError("PostgreSQL allowed an append-only history update")


def test_append_events_preserves_order_ids_and_transaction_boundary(postgres_targets):
    import json
    from uuid import uuid4

    target, repository = _repository(postgres_targets)
    repository.initialize()
    group_id = str(uuid4())
    question_ids = [str(uuid4()) for _ in range(3)]
    event_ids = [str(uuid4()) for _ in question_ids]
    with repository.transaction() as connection:
        repository.append_events(
            connection,
            [
                {
                    "event_id": event_id,
                    "event_type": "interaction.question_created",
                    "group_id": group_id,
                    "question_id": question_id,
                    "source_terminal_id": "batch-terminal",
                    "source_incarnation_id": "batch-incarnation",
                    "actor_kind": "agent",
                    "actor_id": "batch-terminal",
                    "reason_code": "explicit_question_created",
                    "metadata": {"question_id": question_id, "question_version": 1},
                }
                for event_id, question_id in zip(event_ids, question_ids, strict=True)
            ],
        )
        group_sequence = repository.append_event(
            connection,
            event_type="interaction.group_created",
            source_terminal_id="batch-terminal",
            source_incarnation_id="batch-incarnation",
            actor_kind="agent",
            actor_id="batch-terminal",
            reason_code="explicit_group_created",
            metadata={"group_id": group_id},
            group_id=group_id,
        )

    with repository.read_connection() as connection:
        rows = connection.execute(
            "SELECT event_id,event_type,question_id,metadata_json,sequence FROM interaction_events "
            "WHERE group_id=%s ORDER BY sequence",
            (group_id,),
        ).fetchall()
    assert [row["event_type"] for row in rows] == [
        "interaction.question_created",
        "interaction.question_created",
        "interaction.question_created",
        "interaction.group_created",
    ]
    assert [row["event_id"] for row in rows[:3]] == event_ids
    assert [row["question_id"] for row in rows[:3]] == question_ids
    assert all(
        json.loads(row["metadata_json"]) == {"question_id": question_id, "question_version": 1}
        for row, question_id in zip(rows[:3], question_ids, strict=True)
    )
    assert rows[2]["sequence"] < group_sequence == rows[3]["sequence"]


def test_append_events_validates_all_metadata_before_writing_and_rolls_back_mid_batch(
    postgres_targets,
):
    from uuid import uuid4

    target, repository = _repository(postgres_targets)
    repository.initialize()
    group_id = str(uuid4())
    safe_event_id = str(uuid4())
    with repository.transaction() as connection:
        with pytest.raises(ValueError):
            repository.append_events(
                connection,
                [
                    {
                        "event_id": safe_event_id,
                        "event_type": "interaction.batch_validation",
                        "actor_kind": "system",
                        "reason_code": "safe_before_reject",
                    },
                    {
                        "event_type": "interaction.batch_validation",
                        "actor_kind": "system",
                        "reason_code": "reject_prompt_field",
                        "metadata": {"note": "synthetic secret marker"},
                    },
                ],
            )
        assert (
            connection.execute(
                "SELECT count(*) FROM interaction_events WHERE event_id=%s", (safe_event_id,)
            ).fetchone()[0]
            == 0
        )

    duplicate_event_id = str(uuid4())
    duplicate_rows = [
        {
            "event_id": duplicate_event_id,
            "event_type": "interaction.batch_rollback",
            "group_id": group_id,
            "actor_kind": "system",
            "reason_code": "duplicate_constraint",
        },
        {
            "event_id": duplicate_event_id,
            "event_type": "interaction.batch_rollback",
            "group_id": group_id,
            "actor_kind": "system",
            "reason_code": "duplicate_constraint",
        },
    ]
    with pytest.raises(IntegrityError):
        with repository.transaction() as connection:
            repository.append_events(connection, duplicate_rows)
    with repository.read_connection() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM interaction_events WHERE event_id=%s", (duplicate_event_id,)
            ).fetchone()[0]
            == 0
        )


def test_append_events_requires_a_writable_caller_transaction(postgres_targets):
    from cli_agent_orchestrator.clients.postgres import DatabaseError

    target, repository = _repository(postgres_targets)
    repository.initialize()
    event = {
        "event_type": "interaction.batch_transaction_guard",
        "actor_kind": "system",
        "reason_code": "caller_owned_only",
    }
    with connect(target) as connection:
        with pytest.raises(ValueError, match="caller-owned transaction"):
            repository.append_events(connection, [event])
    with connect(target, readonly=True) as connection:
        with pytest.raises(DatabaseError):
            repository.append_events(connection, [event])
        connection.rollback()
