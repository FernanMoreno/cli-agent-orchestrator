"""Native waits stay bound to the notice observed when the wait began."""

from __future__ import annotations

import asyncio
from test.integration.test_interaction_explicit_flow import interaction_http_context  # noqa: F401

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def _observe(context, screen_lines):
    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation

    source = context.service.source_status(context.terminal_id, context.incarnation_id)
    assert source.state == "current"
    assert type(source.current_generation) is int
    observation = NativeInteractionObservation.manual_signal(
        screen_lines=screen_lines,
        terminal_id=context.terminal_id,
        session_name=context.session_name,
        window_name="worker",
    )
    receipt = context.service.observe_native(
        context.terminal_id,
        context.incarnation_id,
        source.current_generation,
        observation,
    )
    assert receipt.question_id is not None
    return receipt, observation


def _expire_question(context, question_id):
    from cli_agent_orchestrator.services.interactions import service as service_module

    with context.service.repository.transaction() as connection:
        connection.execute(
            "UPDATE interaction_questions SET expires_at=%s WHERE question_id=%s",
            (service_module._now() - 1, question_id),
        )


def _replace_and_close_second_notice(context, first_generation, first_question_id, *, prefix):
    second, observation = _observe(
        context, [f"Synthetic replacement provider dialog for {prefix}."]
    )
    assert second.question_id != first_question_id
    assert second.turn_generation == first_generation

    next_generation = context.service.advance_turn(
        context.terminal_id,
        context.incarnation_id,
        reason="websocket_input",
        idempotency_key=f"native-wait-identity-{prefix}-websocket-0001",
    )
    assert next_generation == first_generation + 1
    closed = context.service.reconcile_native_observation(
        context.terminal_id,
        context.incarnation_id,
        first_generation,
        observation_id=second.observation_id,
        dialog_fingerprint=observation.dialog_fingerprint,
        provider_evidence_version=observation.dialog_version,
    )
    assert closed.lifecycle == "reconciled_external"
    return closed


@pytest.mark.parametrize("terminal_state", ["cancelled", "expired"])
@pytest.mark.asyncio
async def test_live_native_wait_never_switches_to_replacement_same_generation_notice(
    interaction_http_context, terminal_state
):
    """A live F1 waiter is terminal even if F2 closes on the same source turn."""
    context = interaction_http_context
    first, _first_observation = _observe(
        context, ["Synthetic first native notice for the original waiter."]
    )
    first_generation = first.turn_generation
    assert first_generation is not None

    waiter = asyncio.create_task(
        context.service.wait_for_native_manual(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=5,
            expected_generation=first_generation,
        )
    )
    # Let the wait take its initial F1 snapshot before changing the ledger.
    await asyncio.sleep(0.05)

    if terminal_state == "cancelled":
        context.service.cancel(
            context.owner,
            first.question_id,
            1,
            "Cancel this synthetic native interaction.",
        )
    else:
        _expire_question(context, first.question_id)
        expired = await context.service.wait_for_native_manual(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=0,
            expected_generation=first_generation,
        )
        assert expired.state == "expired"
        assert expired.question_id == first.question_id

    second = _replace_and_close_second_notice(
        context,
        first_generation,
        first.question_id,
        prefix=f"live-{terminal_state}",
    )
    assert second.lifecycle == "reconciled_external"

    waited = await asyncio.wait_for(waiter, timeout=1)
    assert waited.state == terminal_state
    assert waited.question_id == first.question_id
    assert waited.observation_id == first.observation_id
    assert waited.state != "released"


@pytest.mark.asyncio
async def test_agent_step_pins_native_identity_across_bounded_wait_calls(
    interaction_http_context, monkeypatch
):
    """The runtime helper carries F1 identity from its zero read to later polls."""
    import time

    from cli_agent_orchestrator.services.agent_step import (
        StepExecutionError,
        _pause_timeout_for_native_wait,
    )

    context = interaction_http_context
    first, _first_observation = _observe(
        context, ["Synthetic first notice before bounded wait polling."]
    )
    first_generation = first.turn_generation
    assert first_generation is not None
    assert first.question_id is not None

    original_wait = context.service.wait_for_native_manual
    wait_calls = []

    async def record_wait(*args, **kwargs):
        result = await original_wait(*args, **kwargs)
        wait_calls.append(
            (kwargs.get("expected_question_id"), kwargs.get("expected_observation_id"), result)
        )
        return result

    monkeypatch.setattr(context.service, "wait_for_native_manual", record_wait)
    wait_task = asyncio.create_task(
        _pause_timeout_for_native_wait(
            context.terminal_id,
            source_snapshot=(context.service, context.incarnation_id, first_generation),
            deadline=time.monotonic() + 10,
            cancel_event=None,
        )
    )
    await asyncio.sleep(0.05)
    assert wait_calls
    initial_call = wait_calls[0]
    assert initial_call[0] is None
    assert initial_call[1] is None
    assert initial_call[2].state == "waiting"
    assert initial_call[2].question_id == first.question_id
    assert initial_call[2].observation_id == first.observation_id

    context.service.cancel(
        context.owner,
        first.question_id,
        1,
        "Cancel the original bounded native wait.",
    )
    second = _replace_and_close_second_notice(
        context,
        first_generation,
        first.question_id,
        prefix="bounded-helper",
    )
    assert second.lifecycle == "reconciled_external"

    with pytest.raises(StepExecutionError) as error:
        await asyncio.wait_for(wait_task, timeout=1)
    assert error.value.kind == "reconcile"
    pinned_calls = [call for call in wait_calls if call[0] == first.question_id]
    assert pinned_calls
    assert all(call[1] == first.observation_id for call in pinned_calls)
    assert all(call[2].question_id == first.question_id for call in pinned_calls)
    assert all(call[2].state != "released" for call in wait_calls)


@pytest.mark.asyncio
async def test_live_waiter_stays_expired_when_exact_notice_closes_after_ttl(
    interaction_http_context, monkeypatch
):
    """Exact post-TTL closure clears the dialog gate without releasing its old wait."""
    from cli_agent_orchestrator.services.interactions import service as service_module

    context = interaction_http_context
    first, observation = _observe(
        context, ["Synthetic native notice whose live wait expires after WS input."]
    )
    first_generation = first.turn_generation
    assert first_generation is not None
    assert first.question_id is not None

    with context.service.repository.transaction() as connection:
        row = connection.execute(
            "SELECT expires_at FROM interaction_questions WHERE question_id=%s",
            (first.question_id,),
        ).fetchone()
    assert row is not None
    expires_at = float(row["expires_at"])
    assert expires_at > service_module._now()

    waiter = asyncio.create_task(
        context.service.wait_for_native_manual(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=5,
            expected_generation=first_generation,
        )
    )
    # The live task must have captured F1 before the source turn advances.
    await asyncio.sleep(0.05)
    assert not waiter.done()

    next_generation = context.service.advance_turn(
        context.terminal_id,
        context.incarnation_id,
        reason="websocket_input",
        idempotency_key="native-wait-identity-live-ttl-websocket-0001",
    )
    assert next_generation == first_generation + 1
    with context.service.repository.transaction() as connection:
        row = connection.execute(
            "SELECT lifecycle FROM interaction_questions WHERE question_id=%s",
            (first.question_id,),
        ).fetchone()
    assert row is not None and row["lifecycle"] == "stale"

    # Reconciliation itself must observe expiry; this avoids a prior gate read
    # masking a stale-row/TTL ordering bug.
    monkeypatch.setattr(service_module, "_now", lambda: expires_at + 1)
    closed = context.service.reconcile_native_observation(
        context.terminal_id,
        context.incarnation_id,
        first_generation,
        observation_id=first.observation_id,
        dialog_fingerprint=observation.dialog_fingerprint,
        provider_evidence_version=observation.dialog_version,
    )

    assert closed.lifecycle == "expired"
    assert closed.question_id == first.question_id
    assert not context.service.has_unresolved_native_notice(
        context.terminal_id, context.incarnation_id
    )

    waited = await asyncio.wait_for(waiter, timeout=1)
    assert waited.state == "expired"
    assert waited.lifecycle == "expired"
    assert waited.question_id == first.question_id
    assert waited.observation_id == first.observation_id
    assert waited.state != "released"

    with context.service.repository.transaction() as connection:
        question = connection.execute(
            "SELECT lifecycle FROM interaction_questions WHERE question_id=%s",
            (first.question_id,),
        ).fetchone()
        answers = connection.execute(
            "SELECT COUNT(*) FROM interaction_answers WHERE question_id=%s",
            (first.question_id,),
        ).fetchone()[0]
        events = connection.execute(
            "SELECT event_type,metadata_json FROM interaction_events WHERE question_id=%s "
            "ORDER BY sequence",
            (first.question_id,),
        ).fetchall()

    assert question is not None and question["lifecycle"] == "expired"
    assert answers == 0
    closure_events = [
        event for event in events if event["event_type"] == "interaction.native_closed"
    ]
    assert len(closure_events) == 1
    assert '"outcome":"closed_after_terminal_lifecycle"' in closure_events[0]["metadata_json"]
    assert not any(event["event_type"] == "interaction.reconciled" for event in events)
