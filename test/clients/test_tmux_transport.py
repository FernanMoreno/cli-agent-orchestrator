"""Bounded tmux transport must preserve uncertainty and original errors."""

import os
import shutil
import signal
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from cli_agent_orchestrator.clients.tmux import TmuxClient


def _client():
    client = TmuxClient.__new__(TmuxClient)
    client.server = MagicMock(socket_path=None, socket_name=None, config_file=None, colors=None)
    client.pane_mode = False
    return client


def test_direct_paste_commands_are_bounded():
    client = _client()
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with (
        patch("cli_agent_orchestrator.clients.tmux.subprocess.run", side_effect=run),
        patch("cli_agent_orchestrator.clients.tmux.time.sleep"),
    ):
        client.send_keys("cao-session", "worker", "private task", plain_shell=True)
    assert calls
    assert all(0 < kw.get("timeout", 0) <= 5 for _, kw in calls)


def test_cleanup_does_not_replace_paste_failure():
    client = _client()
    original = RuntimeError("paste outcome uncertain")

    def run(argv, **kwargs):
        if "paste-buffer" in argv:
            raise original
        if "delete-buffer" in argv:
            raise RuntimeError("cleanup unavailable")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with (
        patch("cli_agent_orchestrator.clients.tmux.subprocess.run", side_effect=run),
        patch("cli_agent_orchestrator.clients.tmux.time.sleep"),
    ):
        with pytest.raises(RuntimeError) as error:
            client.send_keys("cao-session", "worker", "private task", plain_shell=True)
    assert error.value is original


def test_libtmux_result_shape_and_server_selectors():
    from cli_agent_orchestrator.clients.tmux_transport import BoundedTmuxServer

    server = BoundedTmuxServer(
        socket_name="private",
        socket_path="/tmp/private.sock",
        config_file="/tmp/private.conf",
        colors=256,
    )
    with patch(
        "cli_agent_orchestrator.clients.tmux_transport.subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "one\n\n", "warning\n"),
    ) as run:
        result = server.cmd("display-message", "-p", "hello", target="%9")
    argv = run.call_args.args[0]
    assert "-S/tmp/private.sock" in argv
    # libtmux normalizes away socket_name when socket_path is supplied.
    assert not any(a.startswith("-L") for a in argv)
    named = BoundedTmuxServer(socket_name="private")
    with patch(
        "cli_agent_orchestrator.clients.tmux_transport.subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, "", ""),
    ) as named_run:
        named.cmd("list-sessions")
    assert "-Lprivate" in named_run.call_args.args[0]
    assert "-f/tmp/private.conf" in argv and "-2" in argv
    assert argv[-5:] == ["display-message", "-t", "%9", "-p", "hello"]
    assert result.stdout == ["one"] and result.stderr == ["warning"] and result.returncode == 0
    assert run.call_args.kwargs["timeout"] == 5


def test_timeout_error_does_not_expose_command_payload():
    from cli_agent_orchestrator.clients.tmux_transport import BoundedTmuxServer, TmuxCommandTimeout

    with patch(
        "cli_agent_orchestrator.clients.tmux_transport.subprocess.run",
        side_effect=subprocess.TimeoutExpired(["tmux", "set-buffer", "secret-token"], 5),
    ):
        with pytest.raises(TmuxCommandTimeout) as error:
            BoundedTmuxServer().cmd("set-buffer", "secret-token")
    assert "secret-token" not in str(error.value)
    assert "set-buffer" in str(error.value)


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("tmux") is None, reason="requires Unix tmux"
)
def test_real_suspended_server_is_bounded_and_recovers(tmp_path, monkeypatch):
    from cli_agent_orchestrator.clients.tmux_transport import BoundedTmuxServer, TmuxCommandTimeout

    socket = str(tmp_path / "tmux.sock")
    subprocess.run(
        ["tmux", "-S", socket, "new-session", "-d", "-s", "cao-bounded", "-n", "worker"],
        check=True,
        timeout=5,
    )
    pid = int(
        subprocess.check_output(
            ["tmux", "-S", socket, "display-message", "-p", "#{pid}"], timeout=5
        )
    )
    client = _client()
    client.server = BoundedTmuxServer(socket_path=socket)
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.tmux_transport.COMMAND_TIMEOUT_SECONDS", 0.2
    )
    try:
        assert isinstance(client.get_history("cao-bounded", "worker"), str)
        os.kill(pid, signal.SIGSTOP)
        started = time.monotonic()
        with pytest.raises(TmuxCommandTimeout):
            client.get_history("cao-bounded", "worker")
        assert time.monotonic() - started < 2
        os.kill(pid, signal.SIGCONT)
        assert isinstance(client.get_history("cao-bounded", "worker"), str)
    finally:
        os.kill(pid, signal.SIGCONT)
        subprocess.run(["tmux", "-S", socket, "kill-server"], check=False, timeout=5)
