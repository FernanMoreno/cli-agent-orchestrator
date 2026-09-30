from __future__ import annotations

import errno
import fcntl
import os
import shutil
import stat
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.models.work_contract import ExecutableIdentity
from cli_agent_orchestrator.services import work_executable_staging as staging
from cli_agent_orchestrator.services.work_elf_identity import identify_static_executable
from cli_agent_orchestrator.services.work_executable_staging import (
    WorkExecutableStagingUnavailable,
    stage_static_executable,
)


def _minimal_static_elf() -> bytes:
    ident = b"\x7fELF" + bytes((2, 1, 1)) + b"\0" * 9
    header = struct.pack(
        "<HHIQQQIHHHHHH",
        2,
        62,
        1,
        0x1000,
        64,
        0,
        0,
        64,
        56,
        1,
        0,
        0,
        0,
    )
    program_header = struct.pack(
        "<IIQQQQQQ",
        1,
        1,
        0,
        0x1000,
        0x1000,
        120,
        120,
        0x1000,
    )
    return ident + header + program_header + b"\x90" * 8


def test_staging_returns_an_object_for_the_exact_static_snapshot():
    executable_bytes = _minimal_static_elf()
    identity = identify_static_executable("/usr/bin/example", executable_bytes)

    with _stage_or_skip(identity, executable_bytes) as staged:
        assert staged is not None
        assert staged.exec_path == f"/proc/self/fd/{staged.memfd_fd}"


def _stage_or_skip(identity: ExecutableIdentity, executable_bytes: bytes):
    try:
        return stage_static_executable(identity, executable_bytes)
    except WorkExecutableStagingUnavailable as exc:
        pytest.skip(
            "kernel memfd executable staging is unavailable " f"(errno={exc.errno}: {exc.strerror})"
        )


def test_staging_seals_bytes_and_landlock_fd_names_the_exec_object():
    executable_bytes = _minimal_static_elf()
    identity = identify_static_executable("/usr/bin/example", executable_bytes)

    with _stage_or_skip(identity, executable_bytes) as staged:
        memfd_info = os.fstat(staged.memfd_fd)
        opath_info = os.fstat(staged.opath_fd)

        assert staged.exec_path == f"/proc/self/fd/{staged.memfd_fd}"
        assert stat.S_ISREG(memfd_info.st_mode)
        assert stat.S_IMODE(memfd_info.st_mode) == 0o500
        assert memfd_info.st_size == len(executable_bytes)
        assert (memfd_info.st_dev, memfd_info.st_ino) == (
            opath_info.st_dev,
            opath_info.st_ino,
        )
        assert fcntl.fcntl(staged.opath_fd, fcntl.F_GETFL) & os.O_PATH == os.O_PATH
        required_seals = (
            getattr(fcntl, "F_SEAL_WRITE", 0x0008)
            | getattr(fcntl, "F_SEAL_GROW", 0x0004)
            | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
            | getattr(fcntl, "F_SEAL_SEAL", 0x0001)
        )
        installed_seals = fcntl.fcntl(staged.memfd_fd, getattr(fcntl, "F_GET_SEALS", 1034))
        assert installed_seals & required_seals == required_seals
        assert os.pread(staged.memfd_fd, len(executable_bytes), 0) == executable_bytes


def test_staging_rejects_digest_mismatch_before_allocating_memfd(monkeypatch):
    executable_bytes = _minimal_static_elf()
    identity = identify_static_executable("/usr/bin/example", executable_bytes)
    different_bytes = executable_bytes[:-1] + b"\x91"
    create_calls = []

    def unexpected_memfd_create(*args, **kwargs):
        create_calls.append((args, kwargs))
        raise AssertionError("digest mismatch must be rejected before memfd creation")

    monkeypatch.setattr(staging.os, "memfd_create", unexpected_memfd_create)

    with pytest.raises(ValueError, match="identity does not match executable bytes"):
        stage_static_executable(identity, different_bytes)

    assert create_calls == []


def test_staging_rejects_invalid_elf_before_allocating_memfd(monkeypatch):
    executable_bytes = _minimal_static_elf()
    identity = identify_static_executable("/usr/bin/example", executable_bytes)
    create_calls = []

    def unexpected_memfd_create(*args, **kwargs):
        create_calls.append((args, kwargs))
        raise AssertionError("invalid ELF must be rejected before memfd creation")

    monkeypatch.setattr(staging.os, "memfd_create", unexpected_memfd_create)

    with pytest.raises(ValueError, match="invalid or truncated ELF"):
        stage_static_executable(identity, b"not an ELF")

    assert create_calls == []


def test_staging_closes_memfd_if_proc_fd_open_fails(monkeypatch):
    executable_bytes = _minimal_static_elf()
    identity = identify_static_executable("/usr/bin/example", executable_bytes)
    # Establish that the host supports the preconditions for reaching the
    # injected proc-FD failure before asserting partial-FD cleanup.
    with _stage_or_skip(identity, executable_bytes):
        pass
    real_memfd_create = os.memfd_create
    real_open = os.open
    created_fds = []

    def capture_memfd_create(*args, **kwargs):
        fd = real_memfd_create(*args, **kwargs)
        created_fds.append(fd)
        return fd

    def fail_proc_fd_open(path, flags, *args, **kwargs):
        if isinstance(path, str) and path.startswith("/proc/self/fd/"):
            raise OSError(errno.ENOENT, "injected proc fd failure")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(staging.os, "memfd_create", capture_memfd_create)
    monkeypatch.setattr(staging.os, "open", fail_proc_fd_open)

    with pytest.raises(OSError, match="injected proc fd failure"):
        stage_static_executable(identity, executable_bytes)

    assert len(created_fds) == 1
    with pytest.raises(OSError) as closed:
        os.fstat(created_fds[0])
    assert closed.value.errno == errno.EBADF


def test_staging_closes_both_fds_if_opath_validation_fails(monkeypatch):
    executable_bytes = _minimal_static_elf()
    identity = identify_static_executable("/usr/bin/example", executable_bytes)
    with _stage_or_skip(identity, executable_bytes):
        pass

    real_memfd_create = os.memfd_create
    real_open = os.open
    real_fstat = os.fstat
    memfd_fds = []
    opath_fds = []

    def capture_memfd_create(*args, **kwargs):
        fd = real_memfd_create(*args, **kwargs)
        memfd_fds.append(fd)
        return fd

    def capture_proc_fd_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if isinstance(path, str) and path.startswith("/proc/self/fd/"):
            opath_fds.append(fd)
        return fd

    def fail_opath_fstat(fd):
        if fd in opath_fds:
            raise OSError(errno.EIO, "injected O_PATH verification failure")
        return real_fstat(fd)

    monkeypatch.setattr(staging.os, "memfd_create", capture_memfd_create)
    monkeypatch.setattr(staging.os, "open", capture_proc_fd_open)
    monkeypatch.setattr(staging.os, "fstat", fail_opath_fstat)

    with pytest.raises(OSError, match="injected O_PATH verification failure"):
        stage_static_executable(identity, executable_bytes)

    assert len(memfd_fds) == 1
    assert len(opath_fds) == 1
    for fd in (memfd_fds[0], opath_fds[0]):
        with pytest.raises(OSError) as closed:
            real_fstat(fd)
        assert closed.value.errno == errno.EBADF


def test_staged_executable_close_is_idempotent():
    executable_bytes = _minimal_static_elf()
    identity = identify_static_executable("/usr/bin/example", executable_bytes)
    staged = _stage_or_skip(identity, executable_bytes)
    memfd_fd, opath_fd = staged.memfd_fd, staged.opath_fd

    staged.close()
    staged.close()

    for fd in (memfd_fd, opath_fd):
        with pytest.raises(OSError) as closed:
            os.fstat(fd)
        assert closed.value.errno == errno.EBADF


def _compile_static_executable(tmp_path: Path, name: str, returncode: int) -> bytes:
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("static C toolchain unavailable: compiler cc was not found")
    source = tmp_path / f"{name}.c"
    executable = tmp_path / name
    source.write_text(f"int main(void) {{ return {returncode}; }}\n", encoding="utf-8")
    compiled = subprocess.run(
        [compiler, "-static", "-O2", "-o", str(executable), str(source)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if compiled.returncode != 0:
        pytest.skip(
            "static C toolchain unavailable: "
            f"compiler exited {compiled.returncode}: {compiled.stderr.strip()}"
        )
    executable.chmod(0o700)
    return executable.read_bytes()


def _run_restricted_exec(
    *,
    staged,
    argv0: str,
    extra_fds: tuple[int, ...] = (),
    target_path: str | None = None,
    landlock_opath_fd: int | None = None,
) -> subprocess.CompletedProcess[str]:
    source_root = Path(__file__).resolve().parents[2] / "src"
    child_code = r"""
import errno, os, sys
sys.path.insert(0, sys.argv[1])
from cli_agent_orchestrator.services.work_process_landlock import install_exec_policy
from cli_agent_orchestrator.services.work_process_seccomp import (
    WorkProcessSeccompUnavailable,
    install_work_process_seccomp_filter,
)

allowed_opath_fd = int(sys.argv[2])
allowed_memfd = int(sys.argv[3])
exec_path = sys.argv[4]
argv0 = sys.argv[5]
target_path = sys.argv[6] or exec_path
extra_fds = tuple(int(value) for value in sys.argv[7:])

try:
    install_exec_policy([allowed_opath_fd])
except OSError as exc:
    if exc.errno in (errno.ENOSYS, errno.EOPNOTSUPP):
        print(f"landlock-unavailable errno={exc.errno}: {exc.strerror}", flush=True)
        raise SystemExit(78)
    raise

try:
    install_work_process_seccomp_filter()
except WorkProcessSeccompUnavailable as exc:
    cause = exc.__cause__
    error_number = cause.errno if isinstance(cause, OSError) else None
    if error_number in (errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM):
        print(f"seccomp-unavailable errno={error_number}: {exc}", flush=True)
        raise SystemExit(78)
    raise

for fd in (allowed_opath_fd, allowed_memfd, *extra_fds):
    os.set_inheritable(fd, False)
try:
    os.execve(target_path, [argv0], {"LC_ALL": "C"})
except OSError as exc:
    if exc.errno in (errno.EACCES, errno.EPERM):
        print(f"execve-error-errno={exc.errno}", flush=True)
        raise SystemExit(89)
    raise
"""
    rule_fd = staged.opath_fd if landlock_opath_fd is None else landlock_opath_fd
    arguments = [
        sys.executable,
        "-I",
        "-c",
        child_code,
        str(source_root),
        str(rule_fd),
        str(staged.memfd_fd),
        staged.exec_path,
        argv0,
        target_path or "",
        *(str(fd) for fd in extra_fds),
    ]
    return subprocess.run(
        arguments,
        env={},
        pass_fds=(rule_fd, staged.memfd_fd, *extra_fds),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _skip_if_kernel_policy_unavailable(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode == 78:
        pytest.skip(result.stdout.strip() or "kernel Landlock/seccomp capability unavailable")


def _run_memfd_landlock_rule(opath_fd: int) -> subprocess.CompletedProcess[str]:
    source_root = Path(__file__).resolve().parents[2] / "src"
    child_code = r"""
import errno, sys
sys.path.insert(0, sys.argv[1])
from cli_agent_orchestrator.services.work_process_landlock import install_exec_policy
try:
    install_exec_policy([int(sys.argv[2])])
except OSError as exc:
    print(f"landlock-add-rule-errno={exc.errno}", flush=True)
    if exc.errno in (errno.ENOSYS, errno.EOPNOTSUPP):
        raise SystemExit(78)
    if exc.errno == errno.EBADFD:
        raise SystemExit(0)
    raise
raise SystemExit(91)
"""
    return subprocess.run(
        [sys.executable, "-I", "-c", child_code, str(source_root), str(opath_fd)],
        env={},
        pass_fds=(opath_fd,),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_landlock_rejects_memfd_opath_with_ebadfd(tmp_path: Path):
    """Record that this kernel cannot add an anonymous memfd as a path rule."""

    executable_bytes = _compile_static_executable(tmp_path, "sealed-static", 37)
    identity = identify_static_executable("/usr/bin/sealed-static", executable_bytes)

    with _stage_or_skip(identity, executable_bytes) as staged:
        result = _run_memfd_landlock_rule(staged.opath_fd)

    _skip_if_kernel_policy_unavailable(result)
    assert result.returncode == 0, f"stdout={result.stdout!r}; stderr={result.stderr!r}"
    assert result.stdout.strip() == f"landlock-add-rule-errno={errno.EBADFD}"


def test_regular_path_landlock_and_seccomp_allow_rule_target_and_deny_other_path(
    tmp_path: Path,
):
    allowed_bytes = _compile_static_executable(tmp_path, "allowed-static", 37)
    other_bytes = _compile_static_executable(tmp_path, "other-static", 41)
    allowed_path = tmp_path / "allowed-static"
    other_path = tmp_path / "other-static"
    allowed_path.write_bytes(allowed_bytes)
    allowed_path.chmod(0o700)
    other_path.write_bytes(other_bytes)
    other_path.chmod(0o700)
    identity = identify_static_executable("/usr/bin/allowed-static", allowed_bytes)
    allowed_fd = os.open(allowed_path, os.O_PATH | os.O_CLOEXEC)

    try:
        with _stage_or_skip(identity, allowed_bytes) as staged:
            allowed_result = _run_restricted_exec(
                staged=staged,
                argv0=identity.command_token,
                target_path=str(allowed_path),
                landlock_opath_fd=allowed_fd,
            )
            denied_result = _run_restricted_exec(
                staged=staged,
                argv0="/usr/bin/other-static",
                target_path=str(other_path),
                landlock_opath_fd=allowed_fd,
            )
    finally:
        os.close(allowed_fd)

    _skip_if_kernel_policy_unavailable(allowed_result)
    _skip_if_kernel_policy_unavailable(denied_result)
    assert (
        allowed_result.returncode == 37
    ), f"stdout={allowed_result.stdout!r}; stderr={allowed_result.stderr!r}"
    assert (
        denied_result.returncode == 89
    ), f"stdout={denied_result.stdout!r}; stderr={denied_result.stderr!r}"


def test_procfd_bypasses_regular_path_landlock_for_preopened_memfd_under_seccomp(
    tmp_path: Path,
):
    """Characterize the proc-FD bypass; this is not a safe execution chain."""

    allowed_bytes = _compile_static_executable(tmp_path, "allowed-static", 37)
    other_bytes = _compile_static_executable(tmp_path, "other-static", 43)
    allowed_path = tmp_path / "allowed-static"
    allowed_path.write_bytes(allowed_bytes)
    allowed_path.chmod(0o700)
    other_identity = identify_static_executable("/usr/bin/other-static", other_bytes)
    allowed_fd = os.open(allowed_path, os.O_PATH | os.O_CLOEXEC)

    try:
        with _stage_or_skip(other_identity, other_bytes) as other:
            result = _run_restricted_exec(
                staged=other,
                argv0=other_identity.command_token,
                target_path=other.exec_path,
                landlock_opath_fd=allowed_fd,
            )
    finally:
        os.close(allowed_fd)

    _skip_if_kernel_policy_unavailable(result)
    assert result.returncode == 43, (
        "expected the preopened, unlisted memfd to execute through procfd despite "
        f"the Landlock rule and seccomp filter; stdout={result.stdout!r}; "
        f"stderr={result.stderr!r}"
    )


def test_landlock_denies_unlisted_otmpfile_exec_via_procfd_under_seccomp(
    tmp_path: Path,
):
    """Characterize Landlock denying an unlisted O_TMPFILE proc-FD execution."""

    if not sys.platform.startswith("linux"):
        pytest.skip(f"O_TMPFILE is Linux-only (errno={errno.ENOSYS})")
    otmpfile_flag = getattr(os, "O_TMPFILE", None)
    if type(otmpfile_flag) is not int:
        pytest.skip(f"os.O_TMPFILE is unavailable (errno={errno.ENOSYS})")

    allowed_bytes = _compile_static_executable(tmp_path, "allowed-static", 37)
    unlisted_bytes = _compile_static_executable(tmp_path, "unlisted-otmpfile", 43)
    allowed_path = tmp_path / "allowed-static"
    allowed_path.write_bytes(allowed_bytes)
    allowed_path.chmod(0o700)
    allowed_fd = os.open(allowed_path, os.O_PATH | os.O_CLOEXEC)
    writable_fd = -1
    readonly_fd = -1

    try:
        try:
            writable_fd = os.open("/tmp", otmpfile_flag | os.O_RDWR | os.O_CLOEXEC, 0o700)
        except OSError as exc:
            if exc.errno in {
                errno.EINVAL,
                errno.EOPNOTSUPP,
                errno.ENOSYS,
                errno.EPERM,
            }:
                pytest.skip(
                    f"O_TMPFILE unavailable on /tmp " f"(errno={exc.errno}: {exc.strerror})"
                )
            raise

        offset = 0
        while offset < len(unlisted_bytes):
            written = os.write(writable_fd, unlisted_bytes[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short write to O_TMPFILE")
            offset += written
        os.fchmod(writable_fd, 0o500)
        try:
            readonly_fd = os.open(f"/proc/self/fd/{writable_fd}", os.O_RDONLY | os.O_CLOEXEC)
        except OSError as exc:
            if exc.errno in {
                errno.ENOENT,
                errno.EACCES,
                errno.EOPNOTSUPP,
                errno.ENOSYS,
                errno.EPERM,
            }:
                pytest.skip(
                    f"read-only proc-FD reopen unavailable " f"(errno={exc.errno}: {exc.strerror})"
                )
            raise

        os.close(writable_fd)
        writable_fd = -1
        metadata = os.fstat(readonly_fd)
        assert stat.S_IMODE(metadata.st_mode) == 0o500
        assert metadata.st_nlink == 0
        assert fcntl.fcntl(readonly_fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
        assert os.pread(readonly_fd, len(unlisted_bytes), 0) == unlisted_bytes

        unlisted = SimpleNamespace(
            memfd_fd=readonly_fd,
            exec_path=f"/proc/self/fd/{readonly_fd}",
        )
        result = _run_restricted_exec(
            staged=unlisted,
            argv0="/usr/bin/unlisted-otmpfile",
            target_path=unlisted.exec_path,
            landlock_opath_fd=allowed_fd,
        )
    finally:
        os.close(allowed_fd)
        for descriptor in (readonly_fd, writable_fd):
            if descriptor >= 0:
                os.close(descriptor)

    _skip_if_kernel_policy_unavailable(result)
    assert result.returncode == 89, f"stdout={result.stdout!r}; stderr={result.stderr!r}"
    assert result.stdout.strip() == f"execve-error-errno={errno.EACCES}"
