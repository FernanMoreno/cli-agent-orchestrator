"""session_service.delete_session only ever tears down cao- sessions."""

from unittest.mock import patch

import pytest

from cli_agent_orchestrator.services.session_service import delete_session


@pytest.mark.offline_database
def test_delete_session_refuses_unregistered_unprefixed_name_before_teardown():
    with (
        patch(
            "cli_agent_orchestrator.services.remote_terminal_service.session_terminals",
            return_value=None,
        ) as remote,
        patch(
            "cli_agent_orchestrator.services.remote_terminal_service.delete_remote"
        ) as delete_remote,
        patch("cli_agent_orchestrator.services.session_service.list_terminals_by_session") as rows,
        patch("cli_agent_orchestrator.services.session_service.get_backend") as backend,
    ):
        with pytest.raises(ValueError, match="not a CAO session name"):
            delete_session("dev")
    remote.assert_called_once_with("dev")
    delete_remote.assert_not_called()
    rows.assert_not_called()
    backend.assert_not_called()
