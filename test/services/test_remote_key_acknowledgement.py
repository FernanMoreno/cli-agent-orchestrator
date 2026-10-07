"""Remote key replies must preserve the boolean acknowledgement contract."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services import remote_terminal_service, terminal_service, work_terminal
from cli_agent_orchestrator.services.agui.handoff_approval import (
    AgentHandoffWithApproval,
    RecordingUiEmitter,
    TerminalServiceAnswerDelivery,
)


@pytest.fixture
def remote_key(monkeypatch):
    monkeypatch.setattr(work_terminal, "terminal_dispatch_lock", lambda *args: nullcontext())
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _: {"runtime_id": "remote"})

    def reply(value):
        monkeypatch.setattr(remote_terminal_service, "call", lambda *args: {"success": value})

    return reply


@pytest.mark.parametrize("acknowledgement", [None, 0, 1, "true", [], {}])
def test_remote_key_rejects_non_boolean_acknowledgement(remote_key, acknowledgement):
    remote_key(acknowledgement)
    with pytest.raises(RuntimeError, match="malformed.*acknowledgement"):
        terminal_service.send_special_key("remote-key", "Enter")


@pytest.mark.parametrize("acknowledgement", [True, False])
def test_remote_key_preserves_boolean_acknowledgement(remote_key, acknowledgement):
    remote_key(acknowledgement)
    assert terminal_service.send_special_key("remote-key", "Enter") is acknowledgement


@pytest.mark.asyncio
async def test_bound_key_delivery_rejects_malformed_remote_acknowledgement(remote_key):
    remote_key(None)
    construct = AgentHandoffWithApproval(
        RecordingUiEmitter(), answer_delivery=TerminalServiceAnswerDelivery()
    )
    with pytest.raises(RuntimeError, match="malformed.*acknowledgement"):
        await construct._bound_delivery(
            SimpleNamespace(terminal_id="remote-key"), {"type": "key", "value": "Enter"}
        )


def test_remote_key_missing_acknowledgement_remains_false(remote_key, monkeypatch):
    remote_key(False)
    monkeypatch.setattr(remote_terminal_service, "call", lambda *args: {})
    assert terminal_service.send_special_key("remote-key", "Enter") is False
