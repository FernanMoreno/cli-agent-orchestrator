"""Generic API failures must not masquerade as recovery projections."""

from unittest.mock import Mock, patch

import pytest
import requests

from cli_agent_orchestrator.mcp_server import app_tools


@pytest.mark.parametrize("code", [500, 503])
def test_generic_structured_recovery_failure_does_not_replace_turn(code):
    response = Mock(status_code=code)
    response.json.return_value = {
        "detail": {"kind": "internal_error", "message": "capture unavailable"}
    }
    response.raise_for_status.side_effect = requests.HTTPError(response=response)
    with (
        patch.object(app_tools, "get_scopes_for_local_token", return_value=[app_tools.SCOPE_WRITE]),
        patch.object(app_tools, "local_auth_misconfig_error", return_value=None),
        patch.object(app_tools.requests, "post", return_value=response) as post,
    ):
        result = app_tools._submit_command_impl(
            "verify_turn", {"terminal_id": "abcd1234", "generation": "a" * 32}
        )
    assert result["success"] is False
    assert "turn" not in result
    assert "capture unavailable" in result["error"]
    post.assert_called_once()
