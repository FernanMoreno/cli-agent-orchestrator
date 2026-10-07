"""App snapshots and gestures retain receipt identity and typed failures."""

from unittest.mock import Mock, patch

import pytest
import requests

from cli_agent_orchestrator.mcp_server import app_tools

TURN = {
    "terminal_id": "abcd1234",
    "generation": "a" * 32,
    "state": "reconcile",
    "allowed_actions": ["verify", "cancel"],
    "attempts": 3,
}


def test_agent_snapshot_preserves_turn_and_reports_output_capture_failure():
    response = Mock(status_code=500)
    response.json.return_value = {"detail": "capture failed"}
    error = requests.HTTPError(response=response)
    with patch.object(
        app_tools,
        "_get_json",
        side_effect=[{"id": "abcd1234", "status": "processing", "turn": TURN}, error],
    ):
        result = app_tools._render_agent_view_impl("abcd1234")
    assert result["turn"] == TURN
    assert result["output_error"]["kind"] == "internal_error"
    assert result["output_error"]["http_status"] == 500
    assert result["output_tail"] == ""


@pytest.mark.parametrize("kind,action", [("verify_turn", "verify"), ("cancel_turn", "cancel")])
def test_selected_generation_routes_only_to_recovery(kind, action):
    response = Mock(status_code=200)
    response.json.return_value = {
        **TURN,
        "state": "verified" if action == "verify" else "cancelled",
    }
    with (
        patch.object(app_tools, "get_scopes_for_local_token", return_value=[app_tools.SCOPE_WRITE]),
        patch.object(app_tools, "local_auth_misconfig_error", return_value=None),
        patch.object(app_tools.requests, "post", return_value=response) as post,
    ):
        result = app_tools._submit_command_impl(
            kind, {"terminal_id": "abcd1234", "generation": TURN["generation"]}
        )
    assert result["success"]
    post.assert_called_once()
    assert post.call_args.args[0].endswith(f"/terminals/abcd1234/turn/{action}")
    assert post.call_args.kwargs["params"] == {"generation": TURN["generation"]}


def test_scope_denial_happens_before_recovery_request():
    with (
        patch.object(app_tools, "get_scopes_for_local_token", return_value=[app_tools.SCOPE_READ]),
        patch.object(app_tools.requests, "post") as post,
    ):
        result = app_tools._submit_command_impl(
            "cancel_turn", {"terminal_id": "abcd1234", "generation": TURN["generation"]}
        )
    assert result["success"] is False
    post.assert_not_called()


def test_stale_recovery_keeps_current_generation_and_diagnostic():
    response = Mock(status_code=409)
    response.json.return_value = {
        "detail": {**TURN, "generation": "b" * 32, "reason": "stale_generation"}
    }
    response.raise_for_status.side_effect = requests.HTTPError(response=response)
    with (
        patch.object(app_tools, "get_scopes_for_local_token", return_value=[app_tools.SCOPE_WRITE]),
        patch.object(app_tools, "local_auth_misconfig_error", return_value=None),
        patch.object(app_tools.requests, "post", return_value=response),
    ):
        result = app_tools._submit_command_impl(
            "verify_turn", {"terminal_id": "abcd1234", "generation": TURN["generation"]}
        )
    assert result["success"] is False
    assert result["turn"]["generation"] == "b" * 32
    assert result["turn"]["reason"] == "stale_generation"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"terminal_id": "abcd1234"},
        {"terminal_id": "abcd1234", "generation": "invalid"},
        {"terminal_id": "../input", "generation": "a" * 32},
    ],
)
def test_invalid_recovery_payload_cannot_send_input(payload):
    with (
        patch.object(app_tools, "get_scopes_for_local_token", return_value=[app_tools.SCOPE_WRITE]),
        patch.object(app_tools, "local_auth_misconfig_error", return_value=None),
        patch.object(app_tools.requests, "post") as post,
    ):
        assert app_tools._submit_command_impl("verify_turn", payload)["success"] is False
    post.assert_not_called()
