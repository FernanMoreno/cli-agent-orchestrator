"""Fail-closed HTTP contracts for legacy turn recovery around managed Work."""

from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.parametrize(
    ("action", "failure", "status_code", "detail"),
    [
        ("verify", "owned", 403, "managed Work owns this terminal"),
        ("cancel", "owned", 403, "managed Work owns this terminal"),
        (
            "verify",
            "store",
            503,
            "Unable to verify durable Work terminal ownership",
        ),
        (
            "cancel",
            "store",
            503,
            "Unable to verify durable Work terminal ownership",
        ),
    ],
)
def test_turn_recovery_route_fails_closed_when_work_ownership_is_unknown(
    client, monkeypatch, action, failure, status_code, detail
):
    from cli_agent_orchestrator.services import turn_recovery_service
    from cli_agent_orchestrator.services.terminal_service import (
        WorkOwnedTerminalError,
        WorkOwnershipStoreUnavailableError,
    )

    if failure == "owned":
        error = WorkOwnedTerminalError("managed Work owns this terminal")
    else:
        error = WorkOwnershipStoreUnavailableError("private database diagnostic")

    operation = Mock(side_effect=error)
    if action == "verify":
        monkeypatch.setattr(turn_recovery_service, "verify_turn", operation)
    else:
        monkeypatch.setattr(turn_recovery_service, "cancel_turn", AsyncMock(side_effect=error))

    response = client.post(f"/terminals/abcd1234/turn/{action}?generation={'a' * 32}")

    assert response.status_code == status_code
    assert response.json()["detail"] == detail
    if failure == "store":
        assert "private database diagnostic" not in response.text
    if action == "verify":
        operation.assert_called_once_with("abcd1234", "a" * 32)


@pytest.mark.parametrize("action", ["verify", "cancel"])
def test_turn_recovery_conflict_returns_the_current_public_turn(client, monkeypatch, action):
    from cli_agent_orchestrator.services import turn_recovery_service

    current_turn = {
        "terminal_id": "abcd1234",
        "provider": "claude_code",
        "generation": "b" * 32,
        "state": "reconcile",
        "allowed_actions": ["cancel"],
    }
    conflict = turn_recovery_service.TurnRecoveryConflict("turn generation changed")
    operation = Mock(side_effect=conflict)
    if action == "verify":
        monkeypatch.setattr(turn_recovery_service, "verify_turn", operation)
    else:
        monkeypatch.setattr(turn_recovery_service, "cancel_turn", AsyncMock(side_effect=conflict))
    monkeypatch.setattr(turn_recovery_service, "get_turn", Mock(return_value=current_turn))

    response = client.post(f"/terminals/abcd1234/turn/{action}?generation={'a' * 32}")

    assert response.status_code == 409
    assert response.json()["detail"] == current_turn
    if action == "verify":
        operation.assert_called_once_with("abcd1234", "a" * 32)
