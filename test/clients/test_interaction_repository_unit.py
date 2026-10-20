"""Offline unit tests for interaction repository write guards."""

import pytest

from cli_agent_orchestrator.clients.interaction_repository import (
    InteractionRepository,
    InteractionSchemaMismatch,
)


class _Cursor:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _ShortBatchConnection:
    in_transaction = True

    def __init__(self) -> None:
        self.rows = []

    def executemany(self, statement, rows):
        self.rows = list(rows)
        return _Cursor(rowcount=len(self.rows) - 1)


def test_append_events_rejects_short_write_batch_without_database():
    connection = _ShortBatchConnection()
    events = [
        {
            "event_type": "interaction.question_created",
            "actor_kind": "agent",
            "reason_code": "explicit_question_created",
        },
        {
            "event_type": "interaction.question_created",
            "actor_kind": "agent",
            "reason_code": "explicit_question_created",
        },
    ]

    with pytest.raises(
        InteractionSchemaMismatch,
        match="interaction event batch was not fully appended",
    ):
        InteractionRepository.append_events(connection, events)

    assert len(connection.rows) == 2
