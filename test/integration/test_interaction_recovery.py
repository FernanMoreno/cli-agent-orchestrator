"""Durable source, question, and answer state across service restarts."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from test.integration.test_interaction_explicit_flow import (  # noqa: F401
    _plain,
    interaction_http_context,
)

import pytest

pytestmark = pytest.mark.postgres


def _one_question(service, capability, operation_key, *, required=True):
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        QuestionInput,
    )

    return service.create_group(
        capability,
        CreateGroupRequest(
            questions=(
                QuestionInput(
                    kind="text",
                    prompt=f"Question for {operation_key}",
                    required=required,
                ),
            )
        ),
        operation_key,
    )


def test_group_and_answer_receipts_replay_after_reopening_the_same_postgres_store(
    interaction_http_context,
):
    from cli_agent_orchestrator.services.interactions.models import (
        AnswerAck,
        InteractionAnswerPayload,
    )
    from cli_agent_orchestrator.services.interactions.service import InteractionService

    context = interaction_http_context
    operation_key = "restart-question-operation-0001"
    group = _one_question(context.service, context.source_token, operation_key)
    group_value = _plain(group)
    question = group_value["questions"][0]
    question_id = question["question_id"]

    accepted = context.service.accept_answer(
        context.owner,
        question_id,
        question.get("version", 1),
        InteractionAnswerPayload(kind="text", text="Durable answer from the operator."),
        "restart-answer-idempotency-0001",
    )
    assert _plain(accepted)["delivery_state"] == "accepted"

    # A fresh service instance over the same file is the process-restart seam.
    restarted = InteractionService(context.repository)
    restarted.initialize()
    assert restarted.validate_agent(context.source_token).terminal_id == context.terminal_id

    replayed_group = _plain(
        _one_question(
            restarted,
            context.source_token,
            operation_key,
        )
    )
    assert replayed_group["group_id"] == group_value["group_id"]
    assert replayed_group["questions"][0]["question_id"] == question_id
    with restarted.repository.read_connection() as connection:
        group_count = connection.execute(
            "SELECT COUNT(*) FROM interaction_groups WHERE operation_key=%s",
            (operation_key,),
        ).fetchone()[0]
    assert group_count == 1

    pulled = _plain(
        asyncio.run(
            restarted.wait_for_answers(
                context.source_token,
                group_value["group_id"],
                timeout_seconds=0,
            )
        )
    )
    assert pulled["state"] == "ready"
    returned = pulled["answers"][0]
    assert returned["response"]["kind"] == "text"
    assert returned["response"]["text"] == "Durable answer from the operator."
    receipt = returned["receipt"]
    with context.repository.read_connection() as connection:
        answer_return_events_before_replay = connection.execute(
            "SELECT count(*) FROM interaction_events " "WHERE event_type=%s AND question_id=%s",
            ("interaction.answer_returned", question_id),
        ).fetchone()[0]
    assert answer_return_events_before_replay == 1

    detail = context.client.get(f"/interactions/{question_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["delivery_state"] == "returned_to_mcp"

    # Reopening after the pull preserves the same answer and its receipt. It
    # must not recreate the initial task or mint a second response identity.
    reopened = InteractionService(context.repository)
    reopened.initialize()
    pulled_again = _plain(
        asyncio.run(
            reopened.wait_for_answers(
                context.source_token,
                group_value["group_id"],
                timeout_seconds=0,
            )
        )
    )
    assert pulled_again["answers"][0]["question_id"] == returned["question_id"]
    assert pulled_again["answers"][0]["version"] == returned["version"]
    assert pulled_again["answers"][0]["receipt"] == receipt
    assert pulled_again["answers"][0]["response"] == returned["response"]
    with reopened.repository.read_connection() as connection:
        answer_return_events_after_replay = connection.execute(
            "SELECT count(*) FROM interaction_events " "WHERE event_type=%s AND question_id=%s",
            ("interaction.answer_returned", question_id),
        ).fetchone()[0]
    assert answer_return_events_after_replay == answer_return_events_before_replay

    ack = reopened.acknowledge_answers(
        context.source_token,
        group_value["group_id"],
        [AnswerAck(question_id=question_id, version=returned["version"], receipt=receipt)],
    )
    assert _plain(ack)[0]["state"] == "agent_acknowledged"


def test_late_human_answer_is_stale_after_server_advances_source_turn(interaction_http_context):
    from cli_agent_orchestrator.services.interactions.models import (
        InteractionAnswerPayload,
    )
    from cli_agent_orchestrator.services.interactions.service import InteractionStale

    context = interaction_http_context
    group = _plain(
        _one_question(
            context.service,
            context.source_token,
            "stale-turn-question-operation-0001",
            required=False,
        )
    )
    question = group["questions"][0]

    generation = context.service.advance_turn(
        context.terminal_id,
        context.incarnation_id,
        reason="new_task_delivery",
        idempotency_key="stale-turn-advance-operation-0001",
    )
    assert generation > 1

    with pytest.raises(InteractionStale):
        context.service.accept_answer(
            context.owner,
            question["question_id"],
            question.get("version", 1),
            InteractionAnswerPayload(kind="text", text="This belongs to the old task."),
            "stale-turn-answer-idempotency-0001",
        )


def test_runtime_wait_pins_source_generation_and_returns_stale_on_turn_advance(
    interaction_http_context,
):
    from cli_agent_orchestrator.services.interactions.models import InteractionAnswerPayload

    context = interaction_http_context
    group = _one_question(
        context.service,
        context.source_token,
        "wait-generation-id-operation-0001",
    )
    group_value = _plain(group)
    assert group_value["lifecycle"] == "open"
    source = context.service.source_status(context.terminal_id, context.incarnation_id)
    generation = source.current_generation
    assert generation is not None
    question = _plain(group)["questions"][0]
    context.service.accept_answer(
        context.owner,
        question["question_id"],
        question["version"],
        InteractionAnswerPayload(kind="text", text="Resolve before the next turn."),
        "wait-generation-answer-operation-0001",
    )
    assert context.service.has_required_wait(context.terminal_id, context.incarnation_id)
    graph_while_accepted = context.service.wait_graph(context.owner)
    assert any(
        edge["from"] == f"task:{context.terminal_id}"
        and edge["to"] == f"question_group:{group_value['group_id']}"
        for edge in graph_while_accepted["edges"]
    )
    still_waiting = asyncio.run(
        context.service.wait_for_required(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=0,
            expected_generation=generation,
        )
    )
    assert _plain(still_waiting)["state"] == "waiting"
    returned = asyncio.run(
        context.service.wait_for_answers(
            context.source_token,
            group_value["group_id"],
            timeout_seconds=0,
        )
    )
    assert _plain(returned)["state"] == "ready"
    assert _plain(returned)["answers"][0]["response"]["text"] == "Resolve before the next turn."
    assert context.service.has_required_wait(context.terminal_id, context.incarnation_id) is False
    next_generation = context.service.advance_turn(
        context.terminal_id,
        context.incarnation_id,
        reason="new_task_delivery",
        idempotency_key="wait-generation-advance-operation-0001",
    )
    assert next_generation > generation
    result = asyncio.run(
        context.service.wait_for_required(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=0,
            expected_generation=generation,
        )
    )
    assert _plain(result)["state"] == "stale"


def test_simultaneous_identical_answers_converge_and_divergent_answers_conflict(
    interaction_http_context,
):
    from cli_agent_orchestrator.services.interactions.models import InteractionAnswerPayload
    from cli_agent_orchestrator.services.interactions.service import (
        InteractionConflict,
        InteractionService,
    )

    context = interaction_http_context
    group = _plain(
        _one_question(
            context.service,
            context.source_token,
            "answer-race-identical-operation-0001",
        )
    )
    question_id = group["questions"][0]["question_id"]
    payload = InteractionAnswerPayload(kind="text", text="Same canonical answer.")

    def accept_same(index):
        return context.service.accept_answer(
            context.owner,
            question_id,
            1,
            payload,
            f"identical-answer-race-key-{index:04d}",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = [
            future.result() for future in [pool.submit(accept_same, 1), pool.submit(accept_same, 2)]
        ]
    assert _plain(first)["answer_id"] == _plain(second)["answer_id"]

    divergent_group = _plain(
        _one_question(
            context.service,
            context.source_token,
            "answer-race-divergent-operation-0001",
        )
    )
    divergent_id = divergent_group["questions"][0]["question_id"]

    def accept_divergent(text, key):
        try:
            receipt = context.service.accept_answer(
                context.owner,
                divergent_id,
                1,
                InteractionAnswerPayload(kind="text", text=text),
                key,
            )
            return ("accepted", _plain(receipt))
        except InteractionConflict:
            return ("conflict", None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda values: accept_divergent(*values),
                [
                    ("First competing answer.", "divergent-answer-race-key-0001"),
                    ("Different competing answer.", "divergent-answer-race-key-0002"),
                ],
            )
        )
    assert sorted(outcome[0] for outcome in outcomes) == ["accepted", "conflict"]

    repository = context.repository
    with repository.read_connection() as connection:
        rows = connection.execute(
            "SELECT COUNT(*) FROM interaction_answers WHERE question_id=%s", (divergent_id,)
        ).fetchone()[0]
    assert rows == 1
