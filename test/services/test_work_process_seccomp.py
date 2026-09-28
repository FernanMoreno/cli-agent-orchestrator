"""Kernel-level proofs for the standalone Work process seccomp layer.

The filter permanently changes the installing process, so every install below
runs in a disposable interpreter subprocess.
"""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Linux x86_64 syscall numbers. Keep this table independent of the production
# filter so an accidentally omitted production rule remains visible here.
_DENIED_SYSCALLS = {
    "connect": (42, (0, 0, 0), errno.EPERM),
    "unshare": (272, (0,), errno.EPERM),
    "setns": (308, (-1, 0), errno.EPERM),
    # ENOSYS makes clone3 unavailable while allowing libc to fall back to the
    # checked legacy clone syscall on runtimes that use clone3 opportunistically.
    "clone3": (435, (0, 0), errno.ENOSYS),
    "clone_newtime": (
        56,
        (0x00000080 | 0x00010000, 0, 0, 0, 0),
        errno.EPERM,
    ),
    "clone_newns": (
        56,
        (0x00020000 | 0x00010000, 0, 0, 0, 0),
        errno.EPERM,
    ),
    "clone_newcgroup": (
        56,
        (0x02000000 | 0x00010000, 0, 0, 0, 0),
        errno.EPERM,
    ),
    "clone_newuts": (
        56,
        (0x04000000 | 0x00010000, 0, 0, 0, 0),
        errno.EPERM,
    ),
    "clone_newipc": (
        56,
        (0x08000000 | 0x00010000, 0, 0, 0, 0),
        errno.EPERM,
    ),
    "clone_newuser": (
        56,
        (0x10000000 | 0x00010000, 0, 0, 0, 0),
        errno.EPERM,
    ),
    "clone_newpid": (
        56,
        (0x20000000 | 0x00010000, 0, 0, 0, 0),
        errno.EPERM,
    ),
    "clone_newnet": (
        56,
        (0x40000000 | 0x00010000, 0, 0, 0, 0),
        errno.EPERM,
    ),
    "mount": (165, (0, 0, 0, 0, 0), errno.EPERM),
    "umount2": (166, (0, 0), errno.EPERM),
    "pivot_root": (155, (0, 0), errno.EPERM),
    "open_tree": (428, (-100, 0, 0), errno.EPERM),
    "move_mount": (429, (-1, 0, -1, 0, 0), errno.EPERM),
    "fsopen": (430, (0, 0), errno.EPERM),
    "fsconfig": (431, (-1, 0, 0, 0, 0), errno.EPERM),
    "fsmount": (432, (-1, 0, 0), errno.EPERM),
    "fspick": (433, (-100, 0, 0), errno.EPERM),
    "mount_setattr": (442, (-100, 0, 0, 0, 0), errno.EPERM),
    "ptrace": (101, (16, 0, 0, 0), errno.EPERM),
    "process_vm_readv": (310, (0, 0, 0, 0, 0, 0), errno.EPERM),
    "process_vm_writev": (311, (0, 0, 0, 0, 0, 0), errno.EPERM),
    "pidfd_getfd": (438, (-1, -1, 0), errno.EPERM),
    "memfd_create": (319, (0, 0), errno.EPERM),
    "execveat": (322, (-100, 0, 0, 0, 0), errno.EPERM),
    "io_uring_setup": (425, (0, 0), errno.EPERM),
    "bpf": (321, (0, 0, 0), errno.EPERM),
}


def _run_in_disposable_process(code: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_blocked_syscalls_return_expected_errno_in_fork_exec_descendant() -> None:
    code = r'''
import ctypes, errno, json, os, sys
from cli_agent_orchestrator.services.work_process_seccomp import install_work_process_seccomp_filter

install_work_process_seccomp_filter()
calls = json.loads(sys.argv[1])
descendant = r"""
import ctypes, errno, json, sys
libc = ctypes.CDLL(None, use_errno=True)
calls = json.loads(sys.argv[1])
results = []
for name, number, args, expected_errno in calls:
    ctypes.set_errno(0)
    result = libc.syscall(ctypes.c_long(number), *args)
    results.append((name, result, ctypes.get_errno(), expected_errno))
print(json.dumps(results), flush=True)
if any(result != -1 or error != expected for _, result, error, expected in results):
    raise SystemExit(23)
"""
pid = os.fork()
if pid == 0:
    os.execve(sys.executable, [sys.executable, "-c", descendant, json.dumps(calls)], os.environ.copy())
_, status = os.waitpid(pid, 0)
if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
    raise SystemExit(24)
'''
    serialized_calls = json.dumps(
        [
            (name, number, args, expected_errno)
            for name, (number, args, expected_errno) in _DENIED_SYSCALLS.items()
        ]
    )
    result = _run_in_disposable_process(code, serialized_calls)

    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize("create_socket_before_filter", [True, False])
def test_filter_denies_existing_and_late_pathname_unix_socket_connect(
    create_socket_before_filter: bool,
) -> None:
    code = r"""
import errno, os, socket, sys, tempfile
from cli_agent_orchestrator.services.work_process_seccomp import install_work_process_seccomp_filter

directory = tempfile.mkdtemp(prefix="seccomp-connect-")
path = os.path.join(directory, "endpoint.sock")
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
def bind_server():
    server.bind(path)
    server.listen(1)
if sys.argv[1] == "before":
    bind_server()
install_work_process_seccomp_filter()
if sys.argv[1] == "after":
    bind_server()
client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    try:
        client.connect(path)
    except OSError as error:
        if error.errno != errno.EPERM:
            raise
    else:
        raise SystemExit("connect to pathname socket was allowed")
finally:
    client.close()
    server.close()
    os.unlink(path)
    os.rmdir(directory)
"""
    timing = "before" if create_socket_before_filter else "after"
    result = _run_in_disposable_process(code, timing)

    assert result.returncode == 0, result.stderr or result.stdout


def test_execve_remains_allowed_after_filter_installation() -> None:
    code = r"""
import os, sys
from cli_agent_orchestrator.services.work_process_seccomp import install_work_process_seccomp_filter

install_work_process_seccomp_filter()
pid = os.fork()
if pid == 0:
    os.execve(sys.executable, [sys.executable, "-c", "print('execve-allowed')"], os.environ.copy())
_, status = os.waitpid(pid, 0)
if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
    raise SystemExit(24)
"""
    result = _run_in_disposable_process(code)

    assert result.returncode == 0, result.stderr or result.stdout
    assert "execve-allowed" in result.stdout


def test_filter_locks_pre_exec_dumpability_and_ptracer_without_blocking_other_prctl() -> None:
    code = r"""
import ctypes, errno, json
from cli_agent_orchestrator.services.work_process_seccomp import (
    _prctl,
    install_work_process_seccomp_filter,
)

set_dumpable_before = _prctl(4, 1)  # PR_SET_DUMPABLE
dumpable_before = _prctl(3)  # PR_GET_DUMPABLE
install_work_process_seccomp_filter()
dumpable = _prctl(3)  # PR_GET_DUMPABLE
set_dumpable_zero = _prctl(4, 0)  # PR_SET_DUMPABLE
dumpable_after_set_zero = _prctl(3)  # PR_GET_DUMPABLE
clear_ptracer = _prctl(0x59616D61, 0)  # PR_SET_PTRACER(0)
try:
    _prctl(4, 1)
except OSError as error:
    relax_dumpable = (None, error.errno)
else:
    relax_dumpable = (0, 0)
try:
    _prctl(0x59616D61, ctypes.c_ulong(-1).value)  # PR_SET_PTRACER_ANY
except OSError as error:
    widen_ptracer = (None, error.errno)
else:
    widen_ptracer = (0, 0)

name = ctypes.create_string_buffer(b"seccomp-prctl")
set_other_prctl = _prctl(15, ctypes.addressof(name))  # PR_SET_NAME
observed_name = ctypes.create_string_buffer(16)
get_other_prctl = _prctl(16, ctypes.addressof(observed_name))  # PR_GET_NAME
print(json.dumps({
    "set_dumpable_before": set_dumpable_before,
    "dumpable_before": dumpable_before,
    "set_dumpable_zero": set_dumpable_zero,
    "dumpable": dumpable,
    "relax_dumpable": relax_dumpable,
    "widen_ptracer": widen_ptracer,
    "dumpable_after_set_zero": dumpable_after_set_zero,
    "clear_ptracer": clear_ptracer,
    "other_prctl": (set_other_prctl, get_other_prctl, observed_name.value.decode()),
}))
"""
    result = _run_in_disposable_process(code)

    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout) == {
        "set_dumpable_before": 0,
        "dumpable_before": 1,
        "set_dumpable_zero": 0,
        "dumpable": 0,
        "relax_dumpable": [None, errno.EPERM],
        "widen_ptracer": [None, errno.EPERM],
        "dumpable_after_set_zero": 0,
        "clear_ptracer": 0,
        "other_prctl": [0, 0, "seccomp-prctl"],
    }


def test_installation_synchronizes_filter_to_preexisting_sibling_thread() -> None:
    code = r"""
import ctypes, errno, json, threading
from cli_agent_orchestrator.services.work_process_seccomp import install_work_process_seccomp_filter

ready = threading.Event()
probe = threading.Event()
result = {}
def sibling():
    libc = ctypes.CDLL(None, use_errno=True)
    ctypes.set_errno(0)
    baseline_result = libc.syscall(
        ctypes.c_long(319), ctypes.c_void_p(0), ctypes.c_ulong(0)
    )
    baseline_errno = ctypes.get_errno()
    result.update(
        baseline_syscall_result=baseline_result,
        baseline_errno=baseline_errno,
    )
    if baseline_result != -1 or baseline_errno != errno.EFAULT:
        result["unavailable"] = True
        ready.set()
        return
    ready.set()
    if not probe.wait(5):
        result["timeout"] = True
        return
    ctypes.set_errno(0)
    # NULL name safely yields EFAULT without seccomp, EPERM when the filter
    # has been synchronized to this already-existing thread.
    syscall_result = libc.syscall(
        ctypes.c_long(319), ctypes.c_void_p(0), ctypes.c_ulong(0)
    )
    result.update(syscall_result=syscall_result, errno=ctypes.get_errno())

thread = threading.Thread(target=sibling)
thread.start()
if not ready.wait(5):
    raise SystemExit("sibling did not start before filter installation")
if result.get("unavailable"):
    thread.join(5)
    print(json.dumps(result))
    raise SystemExit(0)
install_work_process_seccomp_filter()
probe.set()
thread.join(5)
if thread.is_alive():
    raise SystemExit("sibling did not finish its probe")
print(json.dumps(result))
"""
    result = _run_in_disposable_process(code)

    assert result.returncode == 0, result.stderr or result.stdout
    observed = json.loads(result.stdout)
    if observed.get("unavailable"):
        pytest.skip(
            "inherited seccomp policy prevents a clean pre-install memfd_create "
            f"baseline (return={observed['baseline_syscall_result']}, "
            f"errno={observed['baseline_errno']})"
        )
    assert observed == {
        "baseline_syscall_result": -1,
        "baseline_errno": errno.EFAULT,
        "syscall_result": -1,
        "errno": errno.EPERM,
    }


def test_tsync_failure_raises_isolation_unavailable() -> None:
    code = r"""
import errno
from cli_agent_orchestrator.services import work_process_seccomp as seccomp

def fail_tsync(_program_address):
    raise OSError(errno.EBUSY, "thread 123 could not be synchronized")
seccomp._seccomp_tsync = fail_tsync
try:
    seccomp.install_work_process_seccomp_filter()
except seccomp.WorkProcessSeccompUnavailable as error:
    print(str(error))
else:
    raise SystemExit("TSYNC failure was accepted")
"""
    result = _run_in_disposable_process(code)

    assert result.returncode == 0, result.stderr or result.stdout
    assert "TSYNC" in result.stdout


def test_tsync_positive_unsynchronized_tid_raises_ebusy() -> None:
    code = r"""
import ctypes, errno, json
from cli_agent_orchestrator.services import work_process_seccomp as seccomp

captured = []
class FakeSyscall:
    restype = None
    def __call__(self, *args):
        captured.extend(arg.value for arg in args)
        return 12345
class FakeLibc:
    syscall = FakeSyscall()
seccomp.ctypes.CDLL = lambda *_args, **_kwargs: FakeLibc()
try:
    seccomp._seccomp_tsync(0x1234)
except OSError as error:
    print(json.dumps({"errno": error.errno, "message": str(error), "args": captured}))
else:
    raise SystemExit("positive unsynchronized TID was accepted")
"""
    result = _run_in_disposable_process(code)

    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout) == {
        "errno": errno.EBUSY,
        "message": "[Errno 16] SECCOMP_FILTER_FLAG_TSYNC failed for thread 12345",
        "args": [317, 1, 1, 0x1234],
    }


def test_unsupported_architecture_fails_closed_with_clear_error() -> None:
    code = r"""
import platform
platform.machine = lambda: "aarch64"
from cli_agent_orchestrator.services.work_process_seccomp import (
    WorkProcessSeccompUnavailable,
    install_work_process_seccomp_filter,
)
try:
    install_work_process_seccomp_filter()
except WorkProcessSeccompUnavailable as error:
    print(str(error))
else:
    raise SystemExit("unsupported architecture was accepted")
"""
    result = _run_in_disposable_process(code)

    assert result.returncode == 0, result.stderr or result.stdout
    assert "Linux x86_64" in result.stdout


def test_unsupported_kernel_fails_closed_with_clear_error() -> None:
    code = r"""
import errno, json
from cli_agent_orchestrator.services import work_process_seccomp as seccomp

calls = []
def unavailable(option, *args):
    calls.append(option)
    raise OSError(errno.EINVAL, "seccomp is unavailable")
seccomp._prctl = unavailable
try:
    seccomp.install_work_process_seccomp_filter()
except seccomp.WorkProcessSeccompUnavailable as error:
    print(str(error))
    print(json.dumps(calls))
else:
    raise SystemExit("unsupported kernel was accepted")
"""
    result = _run_in_disposable_process(code)

    assert result.returncode == 0, result.stderr or result.stdout
    assert "seccomp" in result.stdout.lower()
    assert json.loads(result.stdout.splitlines()[-1]) == [21]  # PR_GET_SECCOMP


def test_filter_program_checks_architecture_before_syscall_number() -> None:
    code = r"""
import json
from cli_agent_orchestrator.services import work_process_seccomp as seccomp

program = seccomp._build_filter_instructions()
print(json.dumps([[item.code, item.jt, item.jf, item.k] for item in program[:6]]))
"""
    result = _run_in_disposable_process(code)

    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout) == [
        [0x20, 0, 0, 4],  # load seccomp_data.arch
        [0x15, 1, 0, 0xC000003E],  # require AUDIT_ARCH_X86_64
        [0x06, 0, 0, 0x80000000],  # kill on a different audit architecture
        [0x20, 0, 0, 0],  # load seccomp_data.nr
        [0x45, 0, 1, 0x40000000],  # kill x32 ABI syscall numbers
        [0x06, 0, 0, 0x80000000],
    ]


def test_filter_requires_no_new_privs_and_installs_in_disposable_process() -> None:
    code = r"""
import ctypes, json
from cli_agent_orchestrator.services.work_process_seccomp import install_work_process_seccomp_filter

install_work_process_seccomp_filter()
libc = ctypes.CDLL(None, use_errno=True)
no_new_privs = libc.prctl(39, 0, 0, 0, 0)  # PR_GET_NO_NEW_PRIVS
seccomp_mode = libc.prctl(21, 0, 0, 0, 0)  # PR_GET_SECCOMP
print(json.dumps({"no_new_privs": no_new_privs, "seccomp_mode": seccomp_mode}))
"""
    result = _run_in_disposable_process(code)

    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(result.stdout) == {"no_new_privs": 1, "seccomp_mode": 2}
