"""Same-UID sibling FD-theft regression after exec resets dumpability."""

from __future__ import annotations

import errno
import json
import subprocess
import sys
from pathlib import Path
from test.integration.t097.bounded_ready_line import read_newline_frame
from test.integration.t097.test_host_acceptance import accepted_linux_host

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.t097_host]

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_same_uid_sibling_cannot_steal_proxy_fd_after_exec_dumpability_reset(
    accepted_linux_host,
):
    """A sibling cannot widen ptracing or duplicate the filtered process's FD."""
    target = r"""
import ctypes, json, os, socket, time
libc = ctypes.CDLL(None, use_errno=True)
server, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
server.sendall(b"synthetic-private-proxy-fd")
ctypes.set_errno(0)
result = libc.prctl(0x59616d61, ctypes.c_ulong(-1).value, 0, 0, 0)
ptracer_errno = ctypes.get_errno()
dumpable = libc.prctl(3)
print(json.dumps({"pid": os.getpid(), "fd": worker.fileno(),
                  "ptracer_result": result, "ptracer_errno": ptracer_errno,
                  "dumpable_after_exec": dumpable}), flush=True)
time.sleep(30)
"""
    bootstrap = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
if sys.argv[3] == "filtered":
    from cli_agent_orchestrator.services.work_process_seccomp import install_work_process_seccomp_filter
    install_work_process_seccomp_filter()
os.execve(sys.executable, [sys.executable, "-c", sys.argv[2]], {})
"""
    attacker = r"""
import ctypes, errno, json, os, socket, sys
pid, fd = map(int, sys.argv[1:])
pidfd = os.pidfd_open(pid)
libc = ctypes.CDLL(None, use_errno=True)
duplicate = libc.syscall(438, pidfd, fd, 0)
if duplicate < 0:
    print(json.dumps({"errno": ctypes.get_errno()}))
else:
    stolen = socket.socket(fileno=duplicate)
    stolen.settimeout(2)
    payload = stolen.recv(128)
    print(json.dumps({"errno": 0, "payload_matches":
                      payload == b"synthetic-private-proxy-fd", "payload_size": len(payload)}))
    stolen.close()
os.close(pidfd)
"""

    def launch_target(mode: str):
        process = subprocess.Popen(
            [sys.executable, "-c", bootstrap, str(_REPO_ROOT), target, mode],
            cwd=_REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        try:
            assert process.stdout is not None
            ready_line = read_newline_frame(process.stdout, timeout=30)
            assert ready_line is not None, "target did not reach its post-exec ready state"
            assert ready_line.endswith(b"\n"), "target exited before publishing its FD identity"
            return process, json.loads(ready_line)
        except BaseException:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
            raise

    def steal_fd(ready: dict[str, int]):
        result = subprocess.run(
            [sys.executable, "-c", attacker, str(ready["pid"]), str(ready["fd"])],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        return json.loads(result.stdout)

    control = None
    worker = None
    try:
        control, control_ready = launch_target("unfiltered")
        assert control_ready["ptracer_result"] == 0
        assert control_ready["dumpable_after_exec"] == 1
        control_result = steal_fd(control_ready)
        assert control_result["errno"] == 0
        assert control_result["payload_matches"] is True
        control.kill()
        control.communicate(timeout=10)
        control = None

        worker, ready = launch_target("filtered")
        assert ready["ptracer_result"] == -1
        assert ready["ptracer_errno"] == errno.EPERM
        assert ready["dumpable_after_exec"] == 1
        assert steal_fd(ready) == {"errno": errno.EPERM}
    finally:
        for process in (worker, control):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
            elif process is not None:
                process.communicate(timeout=10)
