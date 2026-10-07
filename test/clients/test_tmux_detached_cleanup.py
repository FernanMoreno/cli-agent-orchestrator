"""Closing a terminal must also stop its inherited detached Linux children."""

import os
import shlex
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from cli_agent_orchestrator.clients.tmux import TmuxClient
from cli_agent_orchestrator.clients.tmux_transport import BoundedTmuxServer


def running(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux procfs ownership")
@pytest.mark.parametrize("operation", ["delete", "cancel", "foreign_cancel"])
def test_exact_close_stops_detached_child_and_preserves_other_terminal(tmp_path, operation):
    client = TmuxClient.__new__(TmuxClient)
    client.server = BoundedTmuxServer(socket_path=str(tmp_path / "tmux.sock"))
    client.pane_mode = False
    terminal = uuid.uuid4().hex
    session = "cao-detached-" + terminal[:8]
    pid_file = tmp_path / "child.pid"
    launcher = tmp_path / "launcher.py"
    launcher.write_text(
        "import subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],start_new_session=True)\nPath("
        + repr(str(pid_file))
        + ").write_text(str(p.pid))\ntime.sleep(120)\n"
    )
    client.create_session(session, "worker", terminal, str(tmp_path))
    foreign = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(120)"],
        env=dict(
            os.environ, CAO_TERMINAL_ID="other-terminal", TMUX=str(tmp_path / "tmux.sock") + ",0,0"
        ),
    )
    child = None
    try:
        client.server.cmd(
            "send-keys", "-t", session + ":0", shlex.join([sys.executable, str(launcher)]), "Enter"
        )
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert pid_file.exists()
        child = int(pid_file.read_text())
        assert running(child)
        if operation == "foreign_cancel":
            with pytest.raises(ValueError, match="identity"):
                client.reset_window(session, "worker", terminal_id="another-terminal")
            assert running(child)
            assert client.server.has_session(session)
            return
        if operation == "delete":
            client.cleanup_terminal_exact(terminal, session, "worker")
        else:
            client.reset_window(session, "worker", terminal_id=terminal)
            assert client.server.has_session(session)
        deadline = time.monotonic() + 3
        while running(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not running(child), "detached child survived exact terminal deletion"
        assert foreign.poll() is None
    finally:
        client.server.cmd("kill-server")
        if child and running(child):
            os.kill(child, signal.SIGKILL)
        foreign.kill()
        foreign.wait(timeout=5)
