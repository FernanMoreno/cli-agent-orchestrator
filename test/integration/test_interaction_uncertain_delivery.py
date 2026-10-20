"""A durable native-effect claim becomes uncertain and is never auto-replayed."""

from __future__ import annotations

import time
from test.integration.test_interaction_explicit_flow import (  # noqa: F401
    _plain,
    interaction_http_context,
)

import pytest

pytestmark = pytest.mark.postgres


def test_restart_recovers_claimed_native_effect_as_uncertain_without_reclaiming(
    interaction_http_context,
):
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        InteractionAnswerPayload,
        QuestionInput,
    )
    from cli_agent_orchestrator.services.interactions.service import (
        InteractionError,
        InteractionService,
    )

    context = interaction_http_context
    group = _plain(
        context.service.create_group(
            context.source_token,
            CreateGroupRequest(
                questions=(
                    QuestionInput(
                        kind="text",
                        prompt="Confirm the native dialog response.",
                        required=True,
                    ),
                )
            ),
            "uncertain-effect-create-operation-0001",
        )
    )
    question = group["questions"][0]
    question_id = question["question_id"]
    accepted = _plain(
        context.service.accept_answer(
            context.owner,
            question_id,
            question.get("version", 1),
            InteractionAnswerPayload(kind="text", text="Proceed once."),
            "uncertain-effect-answer-operation-0001",
        )
    )

    # Fault injection at the durable boundary: model a crash after the claim
    # transaction committed, when the adapter may have sent none, some, or all
    # of its input and no provider confirmation reached CAO.
    repository = context.repository
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE interaction_deliveries SET mode='provider_native',state='claimed',"
            "claim_nonce=%s,claimed_at=%s,updated_at=%s WHERE answer_id=%s",
            (
                "claim-that-lost-its-process",
                time.time(),
                time.time(),
                accepted["answer_id"],
            ),
        )
    with repository.read_connection() as connection:
        claimed = connection.execute(
            "SELECT state,claim_nonce FROM interaction_deliveries WHERE answer_id=%s",
            (accepted["answer_id"],),
        ).fetchone()
    assert tuple(claimed) == ("claimed", "claim-that-lost-its-process")

    restarted = InteractionService(repository)
    restarted.initialize()
    with repository.read_connection() as connection:
        recovered = connection.execute(
            "SELECT state,claim_nonce FROM interaction_deliveries WHERE answer_id=%s",
            (accepted["answer_id"],),
        ).fetchone()
    assert recovered["state"] == "uncertain"

    # A second startup/reconciliation pass does not create another claim or
    # reset uncertainty to a sendable state. Providers in the initial matrix
    # do not advertise native response support, so the explicit claim route is
    # also rejected for this terminal.
    reopened = InteractionService(repository)
    reopened.initialize()
    with pytest.raises(InteractionError):
        reopened.claim_native_delivery(
            context.source_token,
            accepted["answer_id"],
            observation_id="uncertain-observation-after-restart",
            source_fingerprint="dialog-fingerprint-after-restart",
            provider_evidence_version="provider-version-1",
        )
    with repository.read_connection() as connection:
        state = connection.execute(
            "SELECT state FROM interaction_deliveries WHERE answer_id=%s",
            (accepted["answer_id"],),
        ).fetchone()[0]
        count = connection.execute(
            "SELECT COUNT(*) FROM interaction_deliveries WHERE answer_id=%s",
            (accepted["answer_id"],),
        ).fetchone()[0]
        uncertain_events = connection.execute(
            "SELECT COUNT(*) FROM interaction_events WHERE question_id=%s "
            "AND event_type='interaction.delivery_uncertain'",
            (question_id,),
        ).fetchone()[0]
    assert state == "uncertain"
    assert count == 1
    assert uncertain_events == 1
