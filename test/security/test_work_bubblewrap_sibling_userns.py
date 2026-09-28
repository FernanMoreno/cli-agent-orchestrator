"""Characterize the same-host-UID Bubblewrap sibling boundary for T097.

This probes one narrow boundary under Yama ptrace_scope=1. The test runner is
the trusted ancestor that launches the target; only a sibling in a separate
user namespace is treated as adversarial. Parent access is expected and is not
a failure. Passing this test does not establish complete Bubblewrap proxy
acceptance.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import platform
import re
import selectors
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_T097_ROOT = Path("/tmp/caos-exec/T097")
_SCRATCH_BWRAP = _T097_ROOT / "bubblewrap-build" / "bwrap"
_VERSION_RE = re.compile(r"^bubblewrap\s+(\d+)\.(\d+)\.(\d+)(?:\s.*)?$")
_CANARY = b"T097 sibling-private canary\n"
_PERMISSION_ERRNOS = {errno.EACCES, errno.EPERM}
_PYTHON_EXECUTABLE = str(Path(sys.executable).resolve())


_TARGET_PROGRAM = r"""
import ctypes
import json
import os
import socket
import sys

libc = ctypes.CDLL(None, use_errno=True)
set_dumpable = libc.prctl(4, 0, 0, 0, 0)
dumpable = libc.prctl(3, 0, 0, 0, 0)
os.chdir("/mnt")
canary_fd = os.open("/mnt/canary", os.O_RDONLY)
canary = os.read(canary_fd, 128)
os.lseek(canary_fd, 0, os.SEEK_SET)
memory = ctypes.create_string_buffer(b"ORIG!!")
control = socket.socket(fileno=int(sys.argv[1]))
report = {
    "pid": os.getpid(),
    "ppid": os.getppid(),
    "uid": os.geteuid(),
    "uid_map": open("/proc/self/uid_map").read().strip(),
    "user_ns_inode": os.stat("/proc/self/ns/user").st_ino,
    "mount_ns_inode": os.stat("/proc/self/ns/mnt").st_ino,
    "dumpable_set_rc": set_dumpable,
    "dumpable": dumpable,
    "canary_fd": canary_fd,
    "canary": canary.decode(),
    "socket_fd": control.fileno(),
    "memory_address": ctypes.addressof(memory),
    "cwd": os.readlink("/proc/self/cwd"),
}
print(json.dumps(report), flush=True)
if control.recv(4) != b"PING":
    raise SystemExit("trusted parent sent an unexpected socket message")
control.sendall(b"PONG")
print("FINAL_MEMORY=" + memory.value.decode(), flush=True)
control.close()
os.close(canary_fd)
"""


_SIBLING_PROGRAM = r"""
import ctypes
import errno
import json
import os
import sys

libc = ctypes.CDLL(None, use_errno=True)
libc.ptrace.restype = ctypes.c_long
pid = int(sys.argv[1])
canary_fd = int(sys.argv[2])
socket_fd = int(sys.argv[3])
memory_address = int(sys.argv[4])
host_canary_source = sys.argv[5]

class IOVec(ctypes.Structure):
    _fields_ = [("iov_base", ctypes.c_void_p), ("iov_len", ctypes.c_size_t)]

results = {}
try:
    pidfd = os.pidfd_open(pid, 0)
    pidfd_open_result = {"allowed": True}
except OSError as exc:
    pidfd = None
    pidfd_open_result = {
        "allowed": False,
        "errno": exc.errno,
        "error": exc.strerror,
    }

def attempt(name, operation):
    try:
        results[name] = {"allowed": True, "value": operation()}
    except OSError as exc:
        results[name] = {
            "allowed": False,
            "errno": exc.errno,
            "error": exc.strerror,
        }

def read_path(path, size=128, offset=0):
    fd = os.open(path, os.O_RDONLY)
    try:
        return os.pread(fd, size, offset).decode(errors="replace")
    finally:
        os.close(fd)

def pidfd_getfd_read(target_fd):
    if pidfd is None:
        raise OSError(errno.EBADF, "pidfd_open prerequisite failed")
    ctypes.set_errno(0)
    duplicated_fd = libc.syscall(
        ctypes.c_long(438),
        ctypes.c_int(pidfd),
        ctypes.c_int(target_fd),
        ctypes.c_uint(0),
    )
    error = ctypes.get_errno()
    if duplicated_fd < 0:
        raise OSError(error, os.strerror(error))
    try:
        return os.read(duplicated_fd, 128).decode(errors="replace")
    finally:
        os.close(duplicated_fd)

def process_vm(which):
    if which == "read":
        local = ctypes.create_string_buffer(6)
        local_iov = IOVec(ctypes.addressof(local), 6)
        remote_iov = IOVec(memory_address, 6)
        operation = libc.process_vm_readv
    else:
        local = ctypes.create_string_buffer(b"SIBLNG")
        local_iov = IOVec(ctypes.addressof(local), 6)
        remote_iov = IOVec(memory_address, 6)
        operation = libc.process_vm_writev
    operation.argtypes = [
        ctypes.c_int,
        ctypes.POINTER(IOVec),
        ctypes.c_ulong,
        ctypes.POINTER(IOVec),
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    operation.restype = ctypes.c_ssize_t
    ctypes.set_errno(0)
    count = operation(
        pid,
        ctypes.byref(local_iov),
        1,
        ctypes.byref(remote_iov),
        1,
        0,
    )
    error = ctypes.get_errno()
    if count < 0:
        raise OSError(error, os.strerror(error))
    if which == "read":
        return {"count": int(count), "data": local.raw[:count].decode()}
    return {"count": int(count)}

def ptrace_attach():
    ctypes.set_errno(0)
    attached = libc.ptrace(16, pid, None, None)  # PTRACE_ATTACH
    error = ctypes.get_errno()
    if attached < 0:
        raise OSError(error, os.strerror(error))
    try:
        os.waitpid(pid, os.WUNTRACED)
    finally:
        ctypes.set_errno(0)
        detached = libc.ptrace(17, pid, None, None)  # PTRACE_DETACH
        detach_error = ctypes.get_errno()
    if detached < 0:
        raise OSError(detach_error, os.strerror(detach_error))
    return "attached and detached"

attempt("proc_fd_open_read", lambda: read_path(f"/proc/{pid}/fd/{canary_fd}"))
attempt("proc_fd_readlink", lambda: os.readlink(f"/proc/{pid}/fd/{canary_fd}"))
attempt(
    "proc_root_canary_open_read",
    lambda: read_path(f"/proc/{pid}/root/mnt/canary"),
)
attempt(
    "proc_mem_open_read",
    lambda: read_path(f"/proc/{pid}/mem", 6, memory_address),
)
attempt(
    "direct_host_canary_source",
    lambda: read_path(host_canary_source),
)
attempt("direct_sibling_mount_canary", lambda: read_path("/mnt/canary"))
attempt("pidfd_getfd_canary", lambda: pidfd_getfd_read(canary_fd))
attempt("pidfd_getfd_socket", lambda: pidfd_getfd_read(socket_fd))
attempt("process_vm_readv", lambda: process_vm("read"))
attempt("process_vm_writev", lambda: process_vm("write"))
attempt("PTRACE_ATTACH", ptrace_attach)
if pidfd is not None:
    os.close(pidfd)

print(json.dumps({
    "pid": os.getpid(),
    "uid": os.geteuid(),
    "uid_map": open("/proc/self/uid_map").read().strip(),
    "user_ns_inode": os.stat("/proc/self/ns/user").st_ino,
    "mount_ns_inode": os.stat("/proc/self/ns/mnt").st_ino,
    "target_uid_as_seen_here": (
        open(f"/proc/{pid}/status").read()
        .split("Uid:")[1]
        .splitlines()[0]
        .strip()
    ),
    "pidfd_open": pidfd_open_result,
    "results": results,
}), flush=True)
"""


def _scratch_bubblewrap() -> Path:
    """Resolve only the accepted T097 scratch build; never use PATH fallback."""
    if not _SCRATCH_BWRAP.is_file():
        pytest.skip(
            "T097 scratch Bubblewrap is absent at "
            f"{_SCRATCH_BWRAP}; installed /usr/bin/bwrap is intentionally not used"
        )
    try:
        executable = _SCRATCH_BWRAP.resolve(strict=True)
        scratch_root = _T097_ROOT.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"could not resolve the T097 scratch Bubblewrap: {exc}")
    if not executable.is_relative_to(scratch_root):
        pytest.skip(
            "T097 scratch Bubblewrap resolves outside its private scratch tree; "
            "installed or unrelated binaries are not accepted"
        )
    if executable == Path("/usr/bin/bwrap").resolve():
        pytest.skip("T097 scratch Bubblewrap resolves to installed /usr/bin/bwrap")
    if not os.access(executable, os.X_OK):
        pytest.skip(f"T097 scratch Bubblewrap is not executable: {executable}")

    try:
        version = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env={"LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"could not probe T097 scratch Bubblewrap version: {exc}")
    match = _VERSION_RE.fullmatch(version.stdout.strip())
    observed = version.stdout.strip() or f"exit {version.returncode}"
    if version.returncode != 0 or match is None or tuple(map(int, match.groups())) != (0, 13, 0):
        pytest.skip(
            "this characterization requires accepted T097 scratch Bubblewrap 0.13.0; "
            f"observed stdout={observed!r}, stderr={version.stderr.strip()!r}"
        )
    return executable


def _normalized_uid_map(contents: str) -> str:
    return "; ".join(" ".join(line.split()) for line in contents.splitlines() if line.strip())


def _require_bwrap_mount_setup(bwrap: Path, source_dir: str, role: str) -> None:
    mount_args = ["--tmpfs", "/mnt"]
    if role == "target":
        mount_args = ["--ro-bind", source_dir, "/mnt"]
    argv = [
        str(bwrap),
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
        *mount_args,
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--",
        _PYTHON_EXECUTABLE,
        "-c",
        'print(open("/proc/self/uid_map").read().strip())',
    ]
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={"LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"scratch Bubblewrap {role} namespace/mount probe failed: {exc}")
    if result.returncode != 0:
        pytest.skip(
            f"scratch Bubblewrap {role} namespace/mount setup is unavailable: "
            f"{result.stderr.strip() or result.returncode}"
        )
    uid_map = _normalized_uid_map(result.stdout)
    if uid_map != "0 1000 1":
        pytest.skip(
            f"scratch Bubblewrap {role} did not map inner UID 0 to host UID 1000; "
            f"observed uid_map={uid_map!r}"
        )


def _kill_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _run_sibling(argv: list[str], timeout: int = 15) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={"LC_ALL": "C"},
        start_new_session=True,
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(process)
        pytest.fail(f"sibling probe timed out; output so far: {process.stdout!r}")
    finally:
        if process.poll() is None:
            _kill_process_group(process)
    return subprocess.CompletedProcess(argv, process.returncode, stdout, "")


def _read_target_report(process: subprocess.Popen[str], timeout: int = 10) -> dict[str, object]:
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout):
            if process.poll() is not None:
                output, _ = process.communicate()
                pytest.fail(f"target exited before reporting its state: {output!r}")
            pytest.fail("target did not report setup state before the timeout")
        line = process.stdout.readline()
    finally:
        selector.close()
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        pytest.fail(f"target did not emit its JSON setup report: {line!r} ({exc})")


def _assert_permission_denied(results: dict[str, object], operation: str) -> None:
    result = results[operation]
    assert isinstance(result, dict)
    assert (
        result.get("allowed") is False
    ), f"same-host-UID sibling unexpectedly accessed {operation}: {result!r}"
    assert (
        result.get("errno") in _PERMISSION_ERRNOS
    ), f"{operation} failed for a reason other than permission denial: {result!r}"


def test_same_host_uid_userns_sibling_cannot_inspect_nondumpable_target():
    """Keep the trusted parent usable while denying the sibling's tested paths."""
    if os.geteuid() != 1000:
        pytest.skip(
            "this T097 characterization requires host UID 1000 for the exact "
            f"0 -> 1000 map; observed host UID {os.geteuid()}"
        )
    if platform.machine().lower() not in {"x86_64", "amd64", "aarch64", "arm64"}:
        pytest.skip(
            "pidfd_getfd syscall number 438 is only encoded here for x86_64/aarch64; "
            f"observed architecture {platform.machine()!r}"
        )
    if not hasattr(os, "pidfd_open"):
        pytest.skip("Python os.pidfd_open is unavailable for the sibling pidfd probe")
    if not hasattr(ctypes.CDLL(None), "process_vm_readv") or not hasattr(
        ctypes.CDLL(None), "process_vm_writev"
    ):
        pytest.skip("libc process_vm_readv/process_vm_writev wrappers are unavailable")

    try:
        ptrace_scope = Path("/proc/sys/kernel/yama/ptrace_scope").read_text().strip()
    except OSError as exc:
        pytest.skip(f"Yama ptrace_scope is unavailable: {exc}")
    if ptrace_scope != "1":
        pytest.skip(
            f"this characterization requires Yama ptrace_scope=1; observed {ptrace_scope!r}"
        )

    bwrap = _scratch_bubblewrap()
    try:
        temporary_source = tempfile.TemporaryDirectory(prefix="t097-sibling-userns-", dir="/tmp")
    except OSError as exc:
        pytest.skip(f"temporary canary directory under /tmp is unavailable: {exc}")

    try:
        with temporary_source as source_dir:
            _require_bwrap_mount_setup(bwrap, source_dir, "target")
            _require_bwrap_mount_setup(bwrap, source_dir, "sibling")
            canary_source = Path(source_dir) / "canary"
            canary_source.write_bytes(_CANARY)

            trusted_parent_socket, target_socket = socket.socketpair()
            target_socket.set_inheritable(True)
            target: subprocess.Popen[str] | None = None
            try:
                target_argv = [
                    str(bwrap),
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
                    "--ro-bind",
                    source_dir,
                    "/mnt",
                    "--proc",
                    "/proc",
                    "--dev",
                    "/dev",
                    "--",
                    _PYTHON_EXECUTABLE,
                    "-c",
                    _TARGET_PROGRAM,
                    str(target_socket.fileno()),
                ]
                target = subprocess.Popen(
                    target_argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env={"LC_ALL": "C"},
                    pass_fds=(target_socket.fileno(),),
                    start_new_session=True,
                )
                target_socket.close()
                target_report = _read_target_report(target)
                target_pid = int(target_report["pid"])
                target_fd = int(target_report["canary_fd"])
                socket_fd = int(target_report["socket_fd"])
                memory_address = int(target_report["memory_address"])

                assert target_report["uid"] == 0
                assert _normalized_uid_map(str(target_report["uid_map"])) == "0 1000 1"
                assert target_report["dumpable_set_rc"] == 0
                assert target_report["dumpable"] == 0
                assert target_report["canary"] == _CANARY.decode()
                assert target_report["cwd"] == "/mnt"

                target_namespace_inode = os.stat(f"/proc/{target_pid}/ns/user").st_ino
                assert target_report["user_ns_inode"] == target_namespace_inode
                target_mount_inode = os.stat(f"/proc/{target_pid}/ns/mnt").st_ino
                assert target_report["mount_ns_inode"] == target_mount_inode

                # The target and actor are separate Bubblewrap children of this test
                # process. This test process is the trusted ancestor; only the sibling
                # actor's access attempts below are required to fail.
                sibling_argv = [
                    str(bwrap),
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
                    "--tmpfs",
                    "/mnt",
                    "--proc",
                    "/proc",
                    "--dev",
                    "/dev",
                    "--",
                    _PYTHON_EXECUTABLE,
                    "-c",
                    _SIBLING_PROGRAM,
                    str(target_pid),
                    str(target_fd),
                    str(socket_fd),
                    str(memory_address),
                    str(canary_source),
                ]
                sibling = _run_sibling(sibling_argv)
                assert sibling.returncode == 0, f"scratch sibling probe failed: {sibling.stdout!r}"
                try:
                    sibling_report = json.loads(sibling.stdout.strip().splitlines()[-1])
                except (IndexError, json.JSONDecodeError) as exc:
                    pytest.fail(
                        f"sibling did not emit a JSON probe report: {sibling.stdout!r} ({exc})"
                    )

                assert sibling_report["uid"] == 0
                assert _normalized_uid_map(str(sibling_report["uid_map"])) == "0 1000 1"
                assert sibling_report["user_ns_inode"] != target_namespace_inode
                assert sibling_report["mount_ns_inode"] != target_mount_inode
                assert sibling_report["target_uid_as_seen_here"].split()[0] == "0"
                pidfd_open_result = sibling_report["pidfd_open"]
                if not pidfd_open_result["allowed"]:
                    if pidfd_open_result.get("errno") == errno.ENOSYS:
                        pytest.skip("kernel pidfd_open is unavailable for this characterization")
                    pytest.fail(
                        "sibling could not establish pidfd_open prerequisite: "
                        f"{pidfd_open_result!r}"
                    )
                results = sibling_report["results"]
                assert isinstance(results, dict)

                # The actor's /tmp and /mnt are private tmpfs mounts, so it sees
                # neither the host source nor the target-only canary mount.
                for operation in (
                    "direct_host_canary_source",
                    "direct_sibling_mount_canary",
                ):
                    assert results[operation] == {
                        "allowed": False,
                        "errno": errno.ENOENT,
                        "error": os.strerror(errno.ENOENT),
                    }

                for operation in (
                    "proc_fd_open_read",
                    "proc_fd_readlink",
                    "proc_root_canary_open_read",
                    "proc_mem_open_read",
                    "pidfd_getfd_canary",
                    "pidfd_getfd_socket",
                    "process_vm_readv",
                    "process_vm_writev",
                    "PTRACE_ATTACH",
                ):
                    result = results[operation]
                    if isinstance(result, dict) and result.get("errno") == errno.ENOSYS:
                        pytest.skip(f"kernel syscall for {operation} is unavailable")
                    _assert_permission_denied(results, operation)

                # Keep the trusted parent-to-target socket live through every
                # adversarial attempt; the target's own FD must still answer.
                trusted_parent_socket.settimeout(5)
                trusted_parent_socket.sendall(b"PING")
                assert trusted_parent_socket.recv(4) == b"PONG"
                assert target.stdout is not None
                final_memory = target.stdout.readline().strip()
                assert final_memory == "FINAL_MEMORY=ORIG!!"
                target.wait(timeout=5)
                assert target.returncode == 0
            finally:
                target_socket.close()
                trusted_parent_socket.close()
                if target is not None:
                    _kill_process_group(target)
    finally:
        temporary_source.cleanup()
