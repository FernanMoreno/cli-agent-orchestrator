"""Prove a same-host-UID sibling cannot inspect a gated Work process."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from cli_agent_orchestrator.services.work_bubblewrap_setup_intent import WorkBubblewrapSetupIntent
from test.integration.t098.test_work_launch_dispatch import _minimal_static_worker, _setup
from test.integration.t097.test_host_acceptance import _host_backend_factory, accepted_linux_host

pytestmark = [pytest.mark.integration, pytest.mark.t097_host]

_SIBLING_PROBE = r"""
import ctypes, errno, json, os, sys
pid = int(sys.argv[1])
monitor = int(sys.argv[2])
target_user_ns_inode = int(sys.argv[3])
expected_host_uid = int(sys.argv[4])
libc = ctypes.CDLL(None, use_errno=True)
results = {}
def attempt(name, operation):
    try:
        operation()
        results[name] = {"allowed": True}
    except OSError as exc:
        results[name] = {"allowed": False, "errno": exc.errno}
def read_fd():
    fd = os.open(f"/proc/{pid}/fd/0", os.O_RDONLY)
    os.close(fd)
def pidfd_getfd():
    pidfd = os.pidfd_open(pid, 0)
    try:
        ctypes.set_errno(0)
        duplicate = libc.syscall(438, pidfd, 0, 0)
        error = ctypes.get_errno()
        if duplicate < 0: raise OSError(error, os.strerror(error))
        os.close(duplicate)
    finally: os.close(pidfd)
def ptrace_attach():
    ctypes.set_errno(0)
    result = libc.ptrace(16, pid, None, None)
    error = ctypes.get_errno()
    if result < 0: raise OSError(error, os.strerror(error))
    try:
        waited, _status = os.waitpid(pid, 0)
        if waited != pid: raise OSError(errno.ECHILD, "ptrace target was not reaped")
    finally:
        ctypes.set_errno(0)
        detached = libc.ptrace(17, pid, None, None)
        detach_error = ctypes.get_errno()
    if detached < 0: raise OSError(detach_error, os.strerror(detach_error))
def maps_host_uid(host_uid, mapping):
    for entry in mapping.splitlines():
        inside, outside, count = (int(part) for part in entry.split())
        if outside <= host_uid < outside + count:
            return inside + host_uid - outside
    return None
attempt("proc_fd_open", read_fd)
attempt("proc_fd_readlink", lambda: os.readlink(f"/proc/{pid}/fd/0"))
attempt(
    "proc_root_read",
    lambda: open(f"/proc/{pid}/root/runtime/bin/python3.12", "rb").read(1),
)
attempt("proc_mem", lambda: open(f"/proc/{pid}/mem", "rb").read(1))
attempt("pidfd_getfd", pidfd_getfd)
attempt("ptrace", ptrace_attach)
target_status = open(f"/proc/{pid}/status").read()
monitor_status = open(f"/proc/{monitor}/status").read()
actor_status = open("/proc/self/status").read()
actor_uid_map = open("/proc/self/uid_map").read().strip()
target_uid_map = open(f"/proc/{pid}/uid_map").read().strip()
target_uid_view = [
    int(value)
    for value in next(
        line for line in target_status.splitlines() if line.startswith("Uid:")
    ).split()[1:]
]
try:
    target_exe = {"allowed": True, "path": os.readlink(f"/proc/{pid}/exe")}
except OSError as exc:
    target_exe = {"allowed": False, "errno": exc.errno}
results["__meta"] = {
    "actor_pid": os.getpid(),
    "actor_ppid": os.getppid(),
    "actor_uid": os.getuid(),
    "actor_caps": next(line for line in actor_status.splitlines() if line.startswith("CapEff:")),
    "target_pid": pid,
    "target_status": [line for line in target_status.splitlines() if line.startswith(("Uid:", "Gid:", "PPid:", "NSpid:", "NoNewPrivs:", "Seccomp:"))],
    "monitor_pid": monitor,
    "monitor_status": [line for line in monitor_status.splitlines() if line.startswith(("Uid:", "Gid:", "PPid:", "NSpid:", "NoNewPrivs:", "Seccomp:"))],
    "target_exe": target_exe,
    "target_uid_map": open(f"/proc/{pid}/uid_map").read().strip(),
    "actor_uid_map": actor_uid_map,
    "actor_user_ns_inode": os.stat("/proc/self/ns/user").st_ino,
    "target_user_ns_inode": target_user_ns_inode,
    "actor_maps_to_expected_host_uid": maps_host_uid(expected_host_uid, actor_uid_map)
    is not None,
    "target_uid_view": target_uid_view,
    "target_effective_uid_matches_actor": target_uid_view[1] == os.geteuid(),
    "ptrace_scope": open("/proc/sys/kernel/yama/ptrace_scope").read().strip(),
}
print(json.dumps(results, sort_keys=True))
"""


@pytest.mark.asyncio
async def test_same_uid_sibling_cannot_read_proc_or_extract_target_fds(
    tmp_path, monkeypatch, accepted_linux_host
):
    supervisors = []
    executions = []
    backend_factory = _host_backend_factory(supervisors, executions)
    repository = None

    def create_backend(marker, work_repository):
        nonlocal repository
        repository = work_repository
        return backend_factory(marker, work_repository)

    repository, principal, gateway, _backend, _marker, request = _setup(
        tmp_path,
        backend_factory=create_backend,
        worker_binary=_minimal_static_worker(),
    )
    original_release = WorkBubblewrapSetupIntent.release_if_current
    sibling_results = []

    def probe_sibling(self, evidence, *, expected_attempt_revision, release, authorize=None):
        identity = repository.read_bubblewrap_process_identity(
            evidence.attempt_id, evidence.generation
        )
        assert identity is not None
        probe = subprocess.run(
            [
                "/usr/bin/bwrap",
                "--unshare-user",
                "--uid",
                "0",
                "--gid",
                "0",
                "--die-with-parent",
                "--ro-bind",
                "/",
                "/",
                "--tmpfs",
                "/tmp",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--",
                sys.executable,
                "-I",
                "-c",
                _SIBLING_PROBE,
                str(identity["init_pid"]),
                str(identity["monitor_pid"]),
                str(os.stat(f"/proc/{identity['init_pid']}/ns/user").st_ino),
                str(os.getuid()),
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            env={"LC_ALL": "C"},
        )
        assert probe.returncode == 0, probe.stderr
        sibling_results.append(json.loads(probe.stdout))
        return original_release(
            self,
            evidence,
            expected_attempt_revision=expected_attempt_revision,
            release=release,
            authorize=authorize,
        )

    monkeypatch.setattr(WorkBubblewrapSetupIntent, "release_if_current", probe_sibling)
    receipt = gateway.admit(principal, request)
    try:
        result = await gateway.dispatch_registered_next()
    except Exception as error:
        raise AssertionError(f"dispatch failed after sibling probe: {sibling_results!r}") from error

    assert result["id"] == receipt.work_item_id
    assert len(sibling_results) == 1
    metadata = sibling_results[0]["__meta"]
    assert set(sibling_results[0]) == {
        "__meta",
        "proc_fd_open",
        "proc_fd_readlink",
        "proc_root_read",
        "proc_mem",
        "pidfd_getfd",
        "ptrace",
    }
    violations = []
    for operation, outcome in sibling_results[0].items():
        if operation == "__meta":
            continue
        if outcome["allowed"] or outcome.get("errno") not in {13, 1}:
            violations.append((operation, outcome))
    assert (
        not violations
    ), f"sibling boundary failed: {violations!r}; {sibling_results[0]['__meta']!r}"
    assert metadata["actor_user_ns_inode"] != metadata["target_user_ns_inode"]
    assert metadata["actor_maps_to_expected_host_uid"] is True
    assert metadata["target_effective_uid_matches_actor"] is True
    assert metadata["ptrace_scope"] == "1"
    assert len(executions) == len(supervisors) == 1
    assert supervisors[0].attempt.state.value == "terminated"
