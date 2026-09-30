"""A durably bound AG-UI approval claims before a single terminal delivery."""

from __future__ import annotations

import asyncio
from test.services.test_work_decisions import EFFECT, EVIDENCE, _decision_context

import pytest

from cli_agent_orchestrator.services.agui.base import RecordingUiEmitter
from cli_agent_orchestrator.services.agui.handoff_approval import (
    AgentHandoffWithApproval,
    ApprovalDecision,
    DeliveryUncertain,
)
from cli_agent_orchestrator.services.work_decisions import WorkDecisions


class Delivery:
    def __init__(self, *, key_result=True, wait=None):
        self.key_result = key_result
        self.wait = wait
        self.calls = []

    def send_input(self, terminal_id, text, **_kwargs):
        self.calls.append(("text", terminal_id, text))
        return True

    def send_special_key(self, terminal_id, key):
        self.calls.append(("key", terminal_id, key))
        if self.wait is not None:
            self.wait.wait()
        return self.key_result


def _bound_construct(context, delivery):
    construct = AgentHandoffWithApproval(
        emitter=RecordingUiEmitter(),
        answer_delivery=delivery,
        decisions=WorkDecisions(context.repository),
    )
    interrupt = construct.on_provider_waiting("terminal-1", "claude_code", "Approve?")
    construct.register_durable_binding(
        interrupt_id=interrupt.id,
        work_item_id=context.original.work_item_id,
        attempt_id=context.original.attempt_id,
        generation=context.original.generation,
        job_id=context.job["id"],
        grant_id=context.grant.id,
        grant_revision=context.grant.revision,
        contract_hash=context.original.contract_hash,
        evidence_refs=EVIDENCE,
        idempotency_key="server-chosen-approval-key",
        effect=EFFECT,
    )
    return construct, interrupt


def _counts(context):
    with context.repository.read_snapshot() as connection:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("work_human_decisions", "work_human_decision_claims")
        )


@pytest.mark.asyncio
async def test_unbound_or_forged_metadata_stays_terminal_only(tmp_path):
    context = _decision_context(tmp_path)
    delivery = Delivery()
    construct = AgentHandoffWithApproval(emitter=RecordingUiEmitter(), answer_delivery=delivery)
    interrupt = construct.on_provider_waiting("terminal-1", "claude_code", "Approve?")
    interrupt.metadata.update(
        {
            "work_item_id": context.original.work_item_id,
            "attempt_id": context.original.attempt_id,
            "generation": context.original.generation,
            "grant_id": context.grant.id,
        }
    )

    result = await construct.resume(interrupt.id, ApprovalDecision.APPROVE)
    assert result.resolved and result.outcome == "approve"
    assert delivery.calls == [("key", "terminal-1", "Enter")]
    assert _counts(context) == (0, 0)


@pytest.mark.asyncio
async def test_bound_approval_records_and_claims_before_the_one_delivery(tmp_path):
    context = _decision_context(tmp_path)
    delivery = Delivery()
    construct, interrupt = _bound_construct(context, delivery)

    result = await construct.resume(interrupt.id, ApprovalDecision.APPROVE, principal=context.actor)
    assert result.resolved and result.outcome == "approve"
    assert _counts(context) == (1, 1)
    assert delivery.calls == [("key", "terminal-1", "Enter")]


@pytest.mark.asyncio
async def test_bound_false_delivery_ack_is_uncertain_and_never_retries(tmp_path):
    context = _decision_context(tmp_path)
    delivery = Delivery(key_result=False)
    construct, interrupt = _bound_construct(context, delivery)

    with pytest.raises(DeliveryUncertain):
        await construct.resume(interrupt.id, ApprovalDecision.APPROVE, principal=context.actor)
    with pytest.raises(DeliveryUncertain):
        await construct.resume(interrupt.id, ApprovalDecision.APPROVE, principal=context.actor)

    assert _counts(context) == (1, 1)
    assert delivery.calls == [("key", "terminal-1", "Enter")]


@pytest.mark.asyncio
async def test_bound_delivery_exception_is_uncertain_and_never_retries(tmp_path):
    """A post-claim backend exception cannot escape as retryable generic failure."""
    context = _decision_context(tmp_path)

    class ExplodingDelivery(Delivery):
        def send_special_key(self, terminal_id, key):
            self.calls.append(("key", terminal_id, key))
            raise RuntimeError("backend disconnected after claim")

    delivery = ExplodingDelivery()
    construct, interrupt = _bound_construct(context, delivery)

    with pytest.raises(DeliveryUncertain):
        await construct.resume(interrupt.id, ApprovalDecision.APPROVE, principal=context.actor)
    with pytest.raises(DeliveryUncertain):
        await construct.resume(interrupt.id, ApprovalDecision.APPROVE, principal=context.actor)

    assert _counts(context) == (1, 1)
    assert delivery.calls == [("key", "terminal-1", "Enter")]


@pytest.mark.asyncio
async def test_evicting_a_resolved_bound_interrupt_cleans_auxiliary_state(tmp_path, monkeypatch):
    """TTL/cap eviction cannot retain a durable binding for an obsolete ID."""
    context = _decision_context(tmp_path)
    construct, interrupt = _bound_construct(context, Delivery())
    await construct.resume(interrupt.id, ApprovalDecision.APPROVE, principal=context.actor)

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.agui.handoff_approval._RESOLVED_TTL_SECONDS", 0.0
    )
    construct._evict_if_needed()

    assert construct.get_interrupt(interrupt.id) is None
    assert not construct.has_durable_binding(interrupt.id)
    assert interrupt.id not in construct._durable_requests
    assert interrupt.id not in construct._durable_uncertain


@pytest.mark.asyncio
async def test_evicting_an_expired_bound_interrupt_cleans_auxiliary_state(tmp_path, monkeypatch):
    """An expired entry must not retain authority after terminal eviction."""
    context = _decision_context(tmp_path)
    construct, interrupt = _bound_construct(context, Delivery())
    construct.expire("terminal-1")

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.agui.handoff_approval._RESOLVED_TTL_SECONDS", 0.0
    )
    construct._evict_if_needed()

    assert construct.get_interrupt(interrupt.id) is None
    assert not construct.has_durable_binding(interrupt.id)
    assert interrupt.id not in construct._durable_requests
    assert interrupt.id not in construct._durable_uncertain


@pytest.mark.asyncio
async def test_bound_concurrent_same_request_joins_but_divergence_conflicts(tmp_path):
    import threading

    context = _decision_context(tmp_path)
    started, release = threading.Event(), threading.Event()

    class SlowDelivery(Delivery):
        def send_special_key(self, terminal_id, key):
            self.calls.append(("key", terminal_id, key))
            started.set()
            release.wait()
            return True

    delivery = SlowDelivery()
    construct, interrupt = _bound_construct(context, delivery)
    first = asyncio.create_task(
        construct.resume(interrupt.id, ApprovalDecision.APPROVE, principal=context.actor)
    )
    await asyncio.to_thread(started.wait)
    joined = asyncio.create_task(
        construct.resume(interrupt.id, ApprovalDecision.APPROVE, principal=context.actor)
    )
    with pytest.raises(Exception, match="bound approval request conflicts"):
        await construct.resume(interrupt.id, ApprovalDecision.DENY, principal=context.actor)
    release.set()
    one, two = await asyncio.gather(first, joined)

    assert one.outcome == two.outcome == "approve"
    assert _counts(context) == (1, 1)
    assert delivery.calls == [("key", "terminal-1", "Enter")]
