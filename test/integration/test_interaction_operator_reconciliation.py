"""Native PostgreSQL/API contracts for operator interaction reconciliation."""

from __future__ import annotations

import asyncio
import time
from test.integration.test_interaction_explicit_flow import interaction_http_context  # noqa: F401
from test.integration.test_interaction_native_guard_contracts import (
    _install_native_runtime,
    _observe_native,
)
from uuid import uuid4

import pytest

pytestmark = pytest.mark.postgres


def _accepted_answer(context, prompt: str):
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        InteractionAnswerPayload,
        QuestionInput,
    )

    created = context.service.create_group(
        context.source_token,
        CreateGroupRequest(questions=(QuestionInput(kind="text", prompt=prompt, required=True),)),
        f"reconcile-create-{uuid4().hex}",
    )
    question = created.questions[0]
    accepted = context.service.accept_answer(
        context.owner,
        question["question_id"],
        question["version"],
        InteractionAnswerPayload(kind="text", text=f"answer-{uuid4().hex}"),
        f"reconcile-answer-{uuid4().hex}",
    )
    return created, question, accepted


def _set_delivery_state(context, answer_id: str, state: str) -> None:
    """Seed a durable provider-effect boundary that cannot be produced today."""
    claim_nonce = f"reconcile-claim-{uuid4().hex}"
    now = time.time()
    with context.repository.transaction() as connection:
        connection.execute(
            "UPDATE interaction_deliveries SET mode='provider_native',state=%s,claim_nonce=%s,"
            "claimed_at=%s,updated_at=%s WHERE answer_id=%s",
            (state, claim_nonce, now, now, answer_id),
        )


def _reconcile(context, question_id: str, outcome: str):
    return context.client.post(
        f"/interactions/{question_id}/reconcile",
        headers={"If-Match": '"1"'},
        json={"outcome": outcome, "evidence_code": "operator_checked_state"},
    )


def _state(context, question_id: str, answer_id: str):
    with context.repository.read_connection() as connection:
        question = connection.execute(
            "SELECT lifecycle FROM interaction_questions WHERE question_id=%s", (question_id,)
        ).fetchone()
        delivery = connection.execute(
            "SELECT state,mode,claim_nonce FROM interaction_deliveries WHERE answer_id=%s",
            (answer_id,),
        ).fetchone()
        attempts = connection.execute(
            "SELECT state FROM interaction_delivery_attempts WHERE delivery_id=("
            "SELECT delivery_id FROM interaction_deliveries WHERE answer_id=%s) ORDER BY sequence",
            (answer_id,),
        ).fetchall()
        reconciliation_events = connection.execute(
            "SELECT count(*) FROM interaction_events WHERE question_id=%s "
            "AND event_type='interaction.reconciled'",
            (question_id,),
        ).fetchone()[0]
        answers = connection.execute(
            "SELECT count(*) FROM interaction_answers WHERE question_id=%s", (question_id,)
        ).fetchone()[0]
        deliveries = connection.execute(
            "SELECT count(*) FROM interaction_deliveries WHERE answer_id=%s", (answer_id,)
        ).fetchone()[0]
    return (
        question,
        delivery,
        [row["state"] for row in attempts],
        reconciliation_events,
        answers,
        deliveries,
    )


def test_no_effect_proven_is_rejected_after_answer_was_only_accepted(interaction_http_context):
    context = interaction_http_context
    _created, question, answer = _accepted_answer(
        context, "private prompt for accepted-delivery reconciliation"
    )

    response = _reconcile(context, question["question_id"], "no_effect_proven")

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "conflict"
    assert "private prompt for accepted-delivery reconciliation" not in response.text
    state = _state(context, question["question_id"], answer.answer_id)
    assert state[0]["lifecycle"] == "answered"
    assert state[1]["state"] == "accepted"
    assert state[2] == []
    assert state[3:] == (0, 1, 1)


@pytest.mark.parametrize("delivery_state", ["claimed", "uncertain"])
def test_no_effect_proven_only_resolves_claimed_or_uncertain_effects(
    interaction_http_context, delivery_state
):
    context = interaction_http_context
    _created, question, answer = _accepted_answer(
        context, f"private prompt for {delivery_state} no-effect reconciliation"
    )
    _set_delivery_state(context, answer.answer_id, delivery_state)

    response = _reconcile(context, question["question_id"], "no_effect_proven")

    assert response.status_code == 200, response.text
    assert response.json()["delivery_state"] == "no_effect"
    state = _state(context, question["question_id"], answer.answer_id)
    assert state[0]["lifecycle"] == "answered"
    assert state[1]["state"] == "no_effect"
    assert state[2] == ["no_effect"]
    assert state[3:] == (1, 1, 1)


def test_remains_uncertain_keeps_one_delivery_uncertain_without_claiming_again(
    interaction_http_context,
):
    context = interaction_http_context
    _created, question, answer = _accepted_answer(
        context, "private prompt for remains-uncertain reconciliation"
    )
    _set_delivery_state(context, answer.answer_id, "uncertain")

    first = _reconcile(context, question["question_id"], "remains_uncertain")
    second = _reconcile(context, question["question_id"], "remains_uncertain")

    assert first.status_code == second.status_code == 200
    assert first.json()["delivery_state"] == second.json()["delivery_state"] == "uncertain"
    state = _state(context, question["question_id"], answer.answer_id)
    assert state[0]["lifecycle"] == "answered"
    assert state[1]["state"] == "uncertain"
    assert state[2] == ["uncertain", "uncertain"]
    # Reconciliation records the repeated operator evidence; it does not
    # create another answer/delivery or a new claimed provider effect.
    assert state[3:] == (2, 1, 1)


@pytest.mark.parametrize("delivery_state", ["accepted", "returned_to_mcp", "claimed"])
def test_answered_outside_cao_rejects_delivery_states_that_are_not_reconcilable(
    interaction_http_context, delivery_state
):
    context = interaction_http_context
    created, question, answer = _accepted_answer(
        context, f"private prompt for rejected {delivery_state} external reconciliation"
    )
    if delivery_state == "returned_to_mcp":
        pulled = asyncio.run(
            context.service.wait_for_answers(context.source_token, created.group_id, 0)
        )
        assert pulled.state == "ready"
        assert pulled.answers[0].question_id == question["question_id"]
    elif delivery_state != "accepted":
        _set_delivery_state(context, answer.answer_id, delivery_state)

    response = _reconcile(context, question["question_id"], "answered_outside_cao")

    assert response.status_code == 409, response.text
    state = _state(context, question["question_id"], answer.answer_id)
    assert state[0]["lifecycle"] == "answered"
    assert state[1]["state"] == delivery_state
    assert state[3:] == (0, 1, 1)
    if delivery_state == "returned_to_mcp":
        assert created.group_id


@pytest.mark.parametrize("delivery_state", ["uncertain", "no_effect", "reconciled"])
def test_answered_outside_cao_reconciles_only_permitted_prior_effect_states(
    interaction_http_context, delivery_state
):
    context = interaction_http_context
    _created, question, answer = _accepted_answer(
        context, f"private prompt for allowed {delivery_state} external reconciliation"
    )
    _set_delivery_state(context, answer.answer_id, delivery_state)

    response = _reconcile(context, question["question_id"], "answered_outside_cao")

    assert response.status_code == 200, response.text
    assert response.json()["lifecycle"] == "reconciled_external"
    assert response.json()["delivery_state"] == "reconciled"
    state = _state(context, question["question_id"], answer.answer_id)
    assert state[0]["lifecycle"] == "reconciled_external"
    assert state[1]["state"] == "reconciled"
    assert state[2] == ["reconciled"]
    assert state[3:] == (1, 1, 1)


def test_answered_outside_cao_can_reconcile_native_closure_without_an_answer(
    interaction_http_context, monkeypatch
):
    context = interaction_http_context
    _terminal, monitor, provider, _backend, _metadata = _install_native_runtime(
        monkeypatch, context
    )
    native = _observe_native(
        context, monitor, provider, ["Synthetic terminal reports the dialog has closed."]
    )

    response = _reconcile(context, native["question_id"], "answered_outside_cao")

    assert response.status_code == 200, response.text
    assert response.json()["lifecycle"] == "reconciled_external"
    assert response.json()["delivery_state"] is None
    assert "Synthetic terminal reports the dialog has closed." not in response.text
    with context.repository.read_connection() as connection:
        question = connection.execute(
            "SELECT lifecycle,source_kind,response_capability FROM interaction_questions "
            "WHERE question_id=%s",
            (native["question_id"],),
        ).fetchone()
        answers = connection.execute(
            "SELECT count(*) FROM interaction_answers WHERE question_id=%s",
            (native["question_id"],),
        ).fetchone()[0]
        deliveries = connection.execute(
            "SELECT count(*) FROM interaction_deliveries d JOIN interaction_answers a "
            "ON a.answer_id=d.answer_id WHERE a.question_id=%s",
            (native["question_id"],),
        ).fetchone()[0]
    assert tuple(question) == ("reconciled_external", "native", "manual_only")
    assert answers == 0
    assert deliveries == 0


def test_dialog_still_open_applies_only_to_native_manual_question_without_answer(
    interaction_http_context, monkeypatch
):
    context = interaction_http_context
    _terminal, monitor, provider, _backend, _metadata = _install_native_runtime(
        monkeypatch, context
    )
    native = _observe_native(context, monitor, provider, ["Synthetic native dialog is still open."])

    response = _reconcile(context, native["question_id"], "dialog_still_open")

    assert response.status_code == 200, response.text
    assert response.json()["lifecycle"] == "manual_only"
    assert response.json()["delivery_state"] is None
    assert "Synthetic native dialog is still open." not in response.text
    with context.repository.read_connection() as connection:
        question = connection.execute(
            "SELECT lifecycle,source_kind,response_capability FROM interaction_questions "
            "WHERE question_id=%s",
            (native["question_id"],),
        ).fetchone()
        answers = connection.execute(
            "SELECT count(*) FROM interaction_answers WHERE question_id=%s",
            (native["question_id"],),
        ).fetchone()[0]
    assert tuple(question) == ("manual_only", "native", "manual_only")
    assert answers == 0


def test_dialog_still_open_rejects_explicit_question(interaction_http_context):
    context = interaction_http_context
    _created, question, _answer = _accepted_answer(
        context, "private explicit prompt cannot be called a native open dialog"
    )

    response = _reconcile(context, question["question_id"], "dialog_still_open")

    assert response.status_code == 409, response.text
    assert "private explicit prompt cannot be called a native open dialog" not in response.text
