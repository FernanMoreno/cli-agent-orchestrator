"""The shared task contract distinguishes native identity and receipt transport."""

import pytest

from cli_agent_orchestrator.providers.catalog import registered_provider_descriptors


@pytest.mark.parametrize(
    "descriptor",
    [
        d
        for d in registered_provider_descriptors()
        if d.name in {"claude_code", "codex", "opencode_cli", "gemini_cli"}
    ],
    ids=lambda d: d.name,
)
def test_receipt_contract_binds_own_identity_and_visible_final_reply(descriptor):
    provider = descriptor.adapter_class("own-worker", "session", "window")
    prepared = provider.prepare_input(
        "Assigned by terminal supervisor-parent. Send results to the supervisor."
    )
    assert "Your own CAO terminal ID is own-worker" in prepared
    assert "final visible terminal reply" in prepared
    assert "callback" in prepared
    state = provider.pending_turn_receipt_state()
    assert provider.prepared_input_for_redelivery() == prepared
    assert provider.pending_turn_receipt_state() == state


@pytest.mark.parametrize("name", ["claude_code", "codex", "opencode_cli"])
def test_async_dispatch_receipt_closes_phase_without_claiming_worker_success(name):
    descriptor = next(d for d in registered_provider_descriptors() if d.name == name)
    provider = descriptor.adapter_class("coordinator", "session", "window")
    prepared = provider.prepare_input(
        "Assign API and frontend reviews, then synthesize their callbacks."
    )
    assert "dispatch phase" in prepared
    assert "pending" in prepared
    assert "does not prove that workers or the overall task have finished" in prepared
    assert "later callbacks are separate turns" in prepared
    # Clarifying phase completion must not bypass the active durable fence.
    assert provider.blocks_new_task_input_for_reconciliation
    with pytest.raises(Exception, match="active task receipt"):
        provider.prepare_input("unexpected replacement task")
