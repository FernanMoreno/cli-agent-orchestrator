"""HTTP consumers distinguish missing proof from a broken reader."""

from unittest.mock import patch

import pytest

from cli_agent_orchestrator.providers.base import OutputExtractionError, TurnResultUnavailableError


@pytest.mark.parametrize(
    ("state", "code"), [("pending", 202), ("reconcile", 409), ("cancelled", 409)]
)
def test_last_unavailable_result_is_typed(client, state, code):
    turn = {
        "terminal_id": "abcd1234",
        "provider": "claude_code",
        "generation": "1" * 32,
        "state": state,
        "attempts": 3 if state == "reconcile" else 0,
        "reason": "receipt_result_unverifiable",
        "allowed_actions": ["verify", "cancel"],
    }
    with (
        patch(
            "cli_agent_orchestrator.api.main.terminal_service.get_output",
            side_effect=TurnResultUnavailableError("no proof"),
        ),
        patch("cli_agent_orchestrator.services.turn_recovery_service.get_turn", return_value=turn),
    ):
        response = client.get("/terminals/abcd1234/output?mode=last")
    assert response.status_code == code
    assert response.json()["detail"]["generation"] == "1" * 32
    assert response.json()["detail"]["state"] == state


def test_real_extraction_failure_remains_internal(client):
    with patch(
        "cli_agent_orchestrator.api.main.terminal_service.get_output",
        side_effect=OutputExtractionError("reader failed"),
    ):
        response = client.get("/terminals/abcd1234/output?mode=last")
    assert response.status_code == 500


def test_turn_inspection_exposes_same_public_contract(client):
    turn = {"terminal_id": "abcd1234", "state": "reconcile", "generation": "1" * 32}
    with patch("cli_agent_orchestrator.services.turn_recovery_service.get_turn", return_value=turn):
        response = client.get("/terminals/abcd1234/turn")
    assert response.status_code == 200
    assert response.json() == turn


def test_verify_requires_well_formed_generation(client):
    response = client.post("/terminals/abcd1234/turn/verify?generation=old")
    assert response.status_code == 422
