"""HTTP boundaries must retain launch validation and session incarnation fences."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from cli_agent_orchestrator.api.main import CreateSessionBody


@pytest.mark.parametrize(
    "key", ["HOME", "PATH", "LD_PRELOAD", "BASH_ENV", "NODE_OPTIONS", "PYTHONPATH"]
)
def test_forwarded_startup_variables_rejected(key):
    with pytest.raises(ValidationError):
        CreateSessionBody(env_vars={key: "secret-value"})


def test_delete_alias_stays_inside_cao_namespace(client):
    with patch(
        "cli_agent_orchestrator.api.main.session_service.delete_session",
        return_value={"deleted": ["cao-dev"], "errors": []},
    ) as delete:
        response = client.delete("/sessions/dev")
    assert response.status_code == 200
    assert delete.call_args.args == ("cao-dev",)


def test_terminal_listing_uses_current_incarnation(client):
    current = [{"id": "12345678", "session_name": "cao-dev"}]
    with patch(
        "cli_agent_orchestrator.services.session_service.list_current_session_terminals",
        return_value=current,
    ) as listing:
        response = client.get("/sessions/cao-dev/terminals")
    assert response.status_code == 200
    assert response.json() == current
    listing.assert_called_once_with("cao-dev")


def test_input_ack_preserves_boolean_success_and_causal_sequence(client):
    with patch(
        "cli_agent_orchestrator.api.main.terminal_service.send_input", return_value=7
    ) as send:
        response = client.post("/terminals/abcd1234/input", params={"message": "hello"})
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["turn_sequence"] == 7
    send.assert_called_once()
