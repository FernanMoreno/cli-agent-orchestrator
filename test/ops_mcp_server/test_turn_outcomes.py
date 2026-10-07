"""Ops output and recovery must preserve the active durable turn."""

from unittest.mock import Mock, patch

import pytest

from cli_agent_orchestrator.ops_mcp_server import server

TURN = {
    "terminal_id": "abcd1234",
    "generation": "a" * 32,
    "state": "pending",
    "allowed_actions": ["verify", "cancel"],
    "attempts": 0,
}


@pytest.mark.parametrize("code,state", [(202, "pending"), (409, "reconcile"), (409, "cancelled")])
def test_output_preserves_unresolved_turn_instead_of_empty_or_historical_answer(code, state):
    turn = {**TURN, "state": state}
    response = Mock(status_code=code, text="")
    response.json.return_value = {"detail": turn}
    with patch.object(server.requests, "request", return_value=response):
        result = server._read_session_output_impl("abcd1234", None, "last", None)
    assert result["success"] is False
    assert result["state"] == state
    assert result["generation"] == TURN["generation"]
    assert result["allowed_actions"] == TURN["allowed_actions"]
    assert result["http_status"] == code
    assert "output" not in result


@pytest.mark.parametrize("action", ["verify", "cancel"])
def test_recovery_uses_selected_generation_and_never_pastes_or_interrupts(action):
    response = Mock(status_code=409, text="")
    response.json.return_value = {
        "detail": {**TURN, "generation": "b" * 32, "reason": "stale_generation"}
    }
    with patch.object(server.requests, "request", return_value=response) as request:
        result = server._recover_turn_impl("abcd1234", TURN["generation"], action)
    assert result["success"] is False
    assert result["generation"] == "b" * 32
    request.assert_called_once()
    assert request.call_args.args[0] == "post"
    assert request.call_args.args[1].endswith(f"/terminals/abcd1234/turn/{action}")
    assert request.call_args.kwargs["params"] == {"generation": TURN["generation"]}


@pytest.mark.parametrize(
    "generation,action", [("invalid", "verify"), ("a" * 32, "interrupt"), ("a" * 32, "../../input")]
)
def test_invalid_recovery_never_calls_api(generation, action):
    with patch.object(server.requests, "request") as request:
        result = server._recover_turn_impl("abcd1234", generation, action)
    assert result["success"] is False
    request.assert_not_called()


def test_internal_capture_failure_remains_distinct_from_pending_reconciliation():
    response = Mock(status_code=500, text="capture failed")
    response.json.return_value = {"detail": "capture failed"}
    with patch.object(server.requests, "request", return_value=response):
        result = server._read_session_output_impl("abcd1234", None, "last", None)
    assert result["success"] is False
    assert result["kind"] == "internal_error"
    assert result["http_status"] == 500
    assert "generation" not in result
    assert "output" not in result
