"""Remote inventory can become stale before its projected terminal is read."""

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import remote_terminal_service as remote
from cli_agent_orchestrator.services.session_service import get_session
from cli_agent_orchestrator.services.status_monitor import StatusMonitor
from cli_agent_orchestrator.services.terminal_service import get_terminal


def test_session_retains_live_projection_when_another_terminal_disappears(monkeypatch):
    monkeypatch.setattr(remote, "session_terminals", lambda _: [{"id": "gone"}, {"id": "live"}])
    monkeypatch.setattr(
        remote,
        "terminal_projection",
        lambda terminal_id: None if terminal_id == "gone" else {"id": "live", "status": "idle"},
    )
    session = get_session("remote-test")
    assert session["session"]["status"] == "active"
    assert session["terminals"] == [{"id": "live", "status": "idle"}]


def test_disappeared_remote_projection_does_not_claim_completion(monkeypatch):
    monkeypatch.setattr(remote, "placement", lambda _: {"runtime_id": "runtime"})
    monkeypatch.setattr(remote, "terminal_projection", lambda _: None)
    assert StatusMonitor().get_status("gone") == TerminalStatus.UNKNOWN


def test_disappeared_remote_projection_has_no_observed_turn(monkeypatch):
    monkeypatch.setattr(remote, "placement", lambda _: {"runtime_id": "runtime"})
    monkeypatch.setattr(remote, "terminal_projection", lambda _: None)
    assert StatusMonitor().turn_state("gone") == (0, 0)


def test_disappeared_remote_terminal_read_reports_not_found(monkeypatch):
    monkeypatch.setattr(remote, "placement", lambda _: {"runtime_id": "runtime"})
    monkeypatch.setattr(remote, "terminal_projection", lambda _: None)
    with pytest.raises(ValueError, match="not found"):
        get_terminal("gone")


def test_disappeared_remote_turn_read_reports_not_found(monkeypatch):
    from cli_agent_orchestrator.services.turn_recovery_service import get_turn

    monkeypatch.setattr(remote, "placement", lambda _: {"runtime_id": "runtime"})
    monkeypatch.setattr(remote, "terminal_projection", lambda _: None)
    with pytest.raises(ValueError, match="not found"):
        get_turn("gone")


@pytest.fixture
def remote_session_inventory(monkeypatch):
    """A name snapshot can outlive the terminals that produced it."""
    from unittest.mock import MagicMock

    monkeypatch.setattr(remote, "inspect", lambda _: MagicMock(has_table=lambda _: True))
    connection = MagicMock()
    connection.execute.return_value.scalars.return_value.all.return_value = ["remote-test"]
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    monkeypatch.setattr(remote.database, "engine", engine)


def test_remote_sessions_omit_session_whose_terminal_inventory_disappeared(
    monkeypatch, remote_session_inventory
):
    monkeypatch.setattr(remote, "session_terminals", lambda _: None)
    assert remote.sessions() == []


def test_remote_sessions_omit_session_whose_projections_all_disappeared(
    monkeypatch, remote_session_inventory
):
    monkeypatch.setattr(remote, "session_terminals", lambda _: [{"id": "gone"}])
    monkeypatch.setattr(remote, "terminal_projection", lambda _: None)
    assert remote.sessions() == []


def test_remote_sessions_keep_live_projection_when_another_disappears(
    monkeypatch, remote_session_inventory
):
    monkeypatch.setattr(remote, "session_terminals", lambda _: [{"id": "gone"}, {"id": "live"}])
    monkeypatch.setattr(
        remote,
        "terminal_projection",
        lambda terminal_id: None if terminal_id == "gone" else {"id": "live", "status": "idle"},
    )
    assert remote.sessions() == [{"id": "remote-test", "name": "remote-test", "status": "active"}]
