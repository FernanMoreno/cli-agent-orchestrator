"""Pane replacement uses a real, privately named tmux server."""

import shlex
import shutil
import time

import libtmux
import pytest

from cli_agent_orchestrator.clients.tmux import TERMINAL_ID_OPTION, TmuxClient


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux unavailable")
def test_reset_replaces_old_execution_preserving_window_and_environment(tmp_path):
    # A private socket ensures this test cannot discover or change operator panes.
    server = libtmux.Server(socket_path=str(tmp_path / "recovery.sock"))
    client = TmuxClient()
    client.server = server
    session = server.new_session(
        session_name="recovery",
        window_name="supervisor",
        attach=False,
        environment={"CAO_TERMINAL_ID": "super001", "CAO_API_URL": "http://test.invalid"},
    )
    try:
        worker = session.new_window(
            window_name="worker",
            window_shell="sleep 300",
            environment={"CAO_TERMINAL_ID": "abcd1234"},
        )
        original = worker.active_pane
        # Production create_window stamps ownership before cancellation is admitted.
        worker.set_option(TERMINAL_ID_OPTION, "abcd1234")
        pane_id = original.pane_id
        old_pid = original.pane_pid
        client.reset_window("recovery", "worker", terminal_id="abcd1234")
        fresh = (
            server.sessions.get(session_name="recovery")
            .windows.get(window_name="worker")
            .active_pane
        )
        assert fresh.pane_id == pane_id
        assert fresh.pane_pid != old_pid
        # Session show-environment would report the supervisor and cannot prove
        # the worker shell's effective identity. Read only this variable there.
        identity = tmp_path / "worker-identity"
        fresh.send_keys('printf "%s" "$CAO_TERMINAL_ID" > ' + shlex.quote(str(identity)))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not identity.exists():
            time.sleep(0.02)
        assert identity.read_text() == "abcd1234"
        assert session.cmd("show-environment", "CAO_API_URL").stdout == [
            "CAO_API_URL=http://test.invalid"
        ]
        result = fresh.cmd("display-message", "-p", "#{pane_current_command}")
        assert result.stdout[0] != "sleep"
    finally:
        server.kill()
