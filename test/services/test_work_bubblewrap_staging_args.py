from __future__ import annotations

import fcntl
import os
import struct

import pytest

from cli_agent_orchestrator.services import work_executable_staging as staging
from cli_agent_orchestrator.services.work_elf_identity import identify_static_executable


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


def _stage_or_skip():
    executable_bytes = _minimal_static_elf()
    identity = identify_static_executable("/usr/bin/example", executable_bytes)
    try:
        return staging.stage_static_executable(identity, executable_bytes)
    except staging.WorkExecutableStagingUnavailable as exc:
        pytest.skip(
            "kernel memfd executable staging is unavailable " f"(errno={exc.errno}: {exc.strerror})"
        )


def _bubblewrap_arguments(*args, **kwargs) -> tuple[str, ...]:
    helper = getattr(staging, "bubblewrap_bind_data_arguments", None)
    assert callable(helper), "public bubblewrap staging arguments API is missing"
    return helper(*args, **kwargs)


def test_bubblewrap_arguments_reset_source_offset_and_return_exact_argv():
    staged = _stage_or_skip()
    try:
        os.lseek(staged.memfd_fd, 7, os.SEEK_SET)

        arguments = _bubblewrap_arguments(staged)

        assert arguments == (
            "--perms",
            "0500",
            "--ro-bind-data",
            str(staged.memfd_fd),
            "/exec",
        )
        assert os.lseek(staged.memfd_fd, 0, os.SEEK_CUR) == 0
    finally:
        staged.close()


def test_bubblewrap_arguments_reject_identity_changed_after_staging():
    staged = _stage_or_skip()
    try:
        other_bytes = _minimal_static_elf()[:-1] + b"\x91"
        staged.identity = identify_static_executable("/usr/bin/example", other_bytes)
        os.lseek(staged.memfd_fd, 7, os.SEEK_SET)

        with pytest.raises(ValueError, match="identity"):
            _bubblewrap_arguments(staged)

        assert os.lseek(staged.memfd_fd, 0, os.SEEK_CUR) == 7
    finally:
        staged.close()


def test_bubblewrap_arguments_reject_oversized_stage_before_read(monkeypatch):
    with _stage_or_skip() as valid_stage:
        identity = valid_stage.identity
    flags = os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC | getattr(os, "MFD_EXEC", 0x0010)
    memfd_fd = os.memfd_create("oversized-stage", flags)
    opath_fd = -1
    try:
        os.ftruncate(memfd_fd, 8 * 1024 * 1024 + 1)
        os.fchmod(memfd_fd, 0o500)
        seals = (
            getattr(fcntl, "F_SEAL_WRITE", 0x0008)
            | getattr(fcntl, "F_SEAL_GROW", 0x0004)
            | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
            | getattr(fcntl, "F_SEAL_SEAL", 0x0001)
        )
        fcntl.fcntl(memfd_fd, getattr(fcntl, "F_ADD_SEALS", 1033), seals)
        exec_path = f"/proc/self/fd/{memfd_fd}"
        opath_fd = os.open(exec_path, os.O_PATH | os.O_CLOEXEC)
        oversized = staging.StagedExecutable(memfd_fd, opath_fd, exec_path, identity)

        def forbidden_pread(*_args):
            pytest.fail("oversized stage reached content read")

        monkeypatch.setattr(staging.os, "pread", forbidden_pread)
        with pytest.raises(ValueError, match="size"):
            _bubblewrap_arguments(oversized)
    finally:
        if opath_fd >= 0:
            os.close(opath_fd)
        os.close(memfd_fd)


@pytest.mark.parametrize(
    "destination",
    ("", "exec", "/", "//exec", "/exec/", "/exec//child", "/exec/./child", "/exec/../child"),
)
def test_bubblewrap_arguments_reject_noncanonical_destinations(destination: str):
    staged = _stage_or_skip()
    try:
        with pytest.raises(ValueError, match="destination"):
            _bubblewrap_arguments(staged, destination)
    finally:
        staged.close()


def test_bubblewrap_arguments_reject_closed_stage():
    staged = _stage_or_skip()
    staged.close()

    with pytest.raises(ValueError, match="closed|malformed"):
        _bubblewrap_arguments(staged)


def test_bubblewrap_arguments_reject_stage_with_mismatched_exec_path():
    staged = _stage_or_skip()
    try:
        staged.exec_path = "/proc/self/fd/999999"

        with pytest.raises(ValueError, match="malformed"):
            _bubblewrap_arguments(staged)
    finally:
        staged.close()


def test_bubblewrap_arguments_reject_stage_without_executable_mode():
    staged = _stage_or_skip()
    try:
        os.fchmod(staged.memfd_fd, 0o700)

        with pytest.raises(ValueError, match="mode"):
            _bubblewrap_arguments(staged)
    finally:
        staged.close()


def test_bubblewrap_arguments_reject_stage_missing_a_required_seal(monkeypatch):
    staged = _stage_or_skip()
    get_seals = getattr(fcntl, "F_GET_SEALS", 1034)
    required_seals = (
        getattr(fcntl, "F_SEAL_WRITE", 0x0008)
        | getattr(fcntl, "F_SEAL_GROW", 0x0004)
        | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
        | getattr(fcntl, "F_SEAL_SEAL", 0x0001)
    )
    real_fcntl = staging.fcntl.fcntl

    def report_missing_write_seal(fd: int, command: int, *args):
        if fd == staged.memfd_fd and command == get_seals:
            return required_seals & ~getattr(fcntl, "F_SEAL_WRITE", 0x0008)
        return real_fcntl(fd, command, *args)

    try:
        monkeypatch.setattr(staging.fcntl, "fcntl", report_missing_write_seal)

        with pytest.raises(ValueError, match="seal"):
            _bubblewrap_arguments(staged)
    finally:
        staged.close()
