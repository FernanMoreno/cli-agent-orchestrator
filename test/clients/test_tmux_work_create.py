"""Work creation must preserve a possibly created tmux session for reconciliation."""

from unittest.mock import MagicMock, patch

import pytest

from cli_agent_orchestrator.backends.base import ProcessRestrictionContract
from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.clients.tmux import TmuxClient


@pytest.fixture
def guarded_backend():
    events = []

    class GuardedTmuxBackend(TmuxBackend):
        def preflight_work(self, contract):
            events.append("preflight")

    with patch("cli_agent_orchestrator.clients.tmux.libtmux") as mock_libtmux:
        server = MagicMock()
        mock_libtmux.Server.return_value = server
        client = TmuxClient()
        client.server = server
        server.cmd.return_value.returncode = 0
        client._kill_via_cli = MagicMock()
        backend = GuardedTmuxBackend(client=client)
        contract = ProcessRestrictionContract(paths=(), commands=(), network=())
        yield backend, client, server, contract, events


def _create_work(guarded_backend, tmp_path):
    backend, _client, _server, contract, events = guarded_backend

    def authorize():
        events.append("guard")

    return backend.create_work_session(
        contract, "ses", "win", "tid", str(tmp_path), before_effect=authorize
    )


def _assert_uncertain(exc):
    assert getattr(exc.value, "error_kind", None) == "work_session_create_uncertain"
    assert "may remain" in str(exc.value)
    assert "reconcil" in str(exc.value)


def test_work_create_hydration_failure_preserves_possible_session(guarded_backend, tmp_path):
    _backend, client, server, _contract, events = guarded_backend

    def ambiguous_create(**_kwargs):
        events.append("new_session")
        raise ValueError("tmux listing failed after create")

    server.new_session.side_effect = ambiguous_create

    with pytest.raises(Exception) as exc:
        _create_work(guarded_backend, tmp_path)

    assert events == ["preflight", "guard", "new_session"]
    client._kill_via_cli.assert_not_called()
    _assert_uncertain(exc)


def test_work_create_window_listing_failure_preserves_session(guarded_backend, tmp_path):
    _backend, client, server, _contract, events = guarded_backend
    session = MagicMock()
    type(session).windows = property(
        lambda self: (_ for _ in ()).throw(ValueError("tmux window listing failed"))
    )

    def create(**_kwargs):
        events.append("new_session")
        return session

    server.new_session.side_effect = create

    with pytest.raises(Exception) as exc:
        _create_work(guarded_backend, tmp_path)

    assert events == ["preflight", "guard", "new_session"]
    client._kill_via_cli.assert_not_called()
    session.kill.assert_not_called()
    _assert_uncertain(exc)


def test_work_create_unexpected_window_failure_preserves_session(guarded_backend, tmp_path):
    _backend, client, server, _contract, _events = guarded_backend
    session = MagicMock()
    window = MagicMock()
    window.name = None
    session.windows = [window]
    server.new_session.return_value = session

    with pytest.raises(Exception) as exc:
        _create_work(guarded_backend, tmp_path)

    client._kill_via_cli.assert_not_called()
    session.kill.assert_not_called()
    _assert_uncertain(exc)
