"""Characterize a private noexec bind remount of a writable workspace.

This records behavior for this host's workspace filesystem, the pinned T097
Bubblewrap 0.13.0 build, and the inert ELF fixture. All mount operations happen
after entering a private user and mount namespace; no backend is registered.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_T097_ROOT = Path("/tmp/caos-exec/T097")
_SCRATCH_BWRAP = _T097_ROOT / "bubblewrap-build" / "bwrap"
_VERSION_RE = re.compile(r"^bubblewrap\s+(\d+)\.(\d+)\.(\d+)(?:\s.*)?$")
_FIXTURE_SOURCE = _PROJECT_ROOT / "test" / "fixtures" / "work_bubblewrap_fd_probe.c"
_REPORT_PREFIX = "T097_NOEXEC_BIND_REPORT="


_NAMESPACE_HELPER = r"""
import ctypes
import errno
import json
import os
import pathlib
import subprocess
import sys

workspace = pathlib.Path(sys.argv[1])
bwrap = sys.argv[2]
work = pathlib.Path(sys.argv[3])
loader = sys.argv[4]
libc = ctypes.CDLL(None, use_errno=True)
libc.mount.argtypes = [
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_ulong,
    ctypes.c_char_p,
]
libc.umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]

MS_BIND = 4096
MS_REMOUNT = 32
MS_NOEXEC = 8
MS_REC = 1 << 14
MS_PRIVATE = 1 << 18
MNT_DETACH = 2


def mount(source, target, filesystem, flags, data):
    result = libc.mount(
        source.encode() if source else None,
        str(target).encode(),
        filesystem.encode() if filesystem else None,
        flags,
        data.encode() if data else None,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def mountinfo_entries(contents, mountpoint):
    entries = []
    for line in contents.splitlines():
        before_separator, separator, after_separator = line.partition(" - ")
        if not separator:
            continue
        left = before_separator.split()
        right = after_separator.split()
        if len(left) >= 6 and len(right) >= 2 and left[4] == mountpoint:
            entries.append((left, right))
    return entries


def direct_exec_errno(payload, marker):
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        try:
            os.execve(
                str(payload),
                [str(payload), "--write-marker", str(marker)],
                {"LC_ALL": "C"},
            )
        except OSError as exc:
            os.write(write_fd, int(exc.errno or 0).to_bytes(4, "little"))
            os._exit(111)
    os.close(write_fd)
    raw_errno = os.read(read_fd, 4)
    os.close(read_fd)
    _, status = os.waitpid(child, 0)
    if not raw_errno and not os.WIFEXITED(status):
        raise RuntimeError("direct-exec child did not exit normally")
    return int.from_bytes(raw_errno, "little") if raw_errno else None


report = {}
bind_mounted = False
mount(None, "/", None, MS_REC | MS_PRIVATE, None)
try:
    mount(str(workspace), workspace, None, MS_BIND, None)
    bind_mounted = True
    try:
        mount(None, workspace, None, MS_BIND | MS_REMOUNT | MS_NOEXEC, None)
    except OSError as exc:
        report["remount_error"] = {"errno": exc.errno, "message": str(exc)}
    else:
        source_entries = mountinfo_entries(
            pathlib.Path("/proc/self/mountinfo").read_text(), str(workspace)
        )
        if not source_entries:
            raise RuntimeError("workspace bind mount is absent from private mountinfo")
        source_mount, source_filesystem = source_entries[-1]
        report["source_mount_root"] = source_mount[3]
        report["source_mount_fstype"] = source_filesystem[0]
        report["source_mount_options"] = source_mount[5]

        direct_errno = direct_exec_errno(
            workspace / "direct-control", workspace / "direct-exec.marker"
        )
        command = [
            bwrap,
            "--unshare-all",
            "--die-with-parent",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            str(work),
            "/tmp",
            "--bind",
            str(workspace),
            "/tmp/workspace",
            "--proc",
            "/proc",
            "--chdir",
            "/tmp",
            "--",
            "/bin/sh",
            "-c",
            "cat /proc/self/mountinfo; exec /tmp/fd-probe --shm-loader "
            "/tmp/fd-probe /tmp/workspace/loader-payload "
            "/tmp/workspace/loader.marker \"$1\"",
            "t097-noexec-bind-loader",
            loader,
        ]
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            env={"LC_ALL": "C"},
        )
        report.update(
            {
                "bwrap_returncode": result.returncode,
                "bwrap_stdout": result.stdout,
                "bwrap_stderr": result.stderr,
                "direct_exec_errno": direct_errno,
                "direct_exec_marker_exists": (workspace / "direct-exec.marker").exists(),
                "loader_marker_exists": (workspace / "loader.marker").exists(),
            }
        )
finally:
    if bind_mounted and libc.umount2(str(workspace).encode(), MNT_DETACH) != 0:
        error = ctypes.get_errno()
        report["cleanup_error"] = {"errno": error, "message": os.strerror(error)}

print("T097_NOEXEC_BIND_REPORT=" + json.dumps(report), flush=True)
"""


def _scratch_bubblewrap() -> Path:
    """Resolve only the pinned scratch build; never fall back to PATH."""
    if not _SCRATCH_BWRAP.is_file():
        pytest.skip(
            f"T097 scratch Bubblewrap is absent at {_SCRATCH_BWRAP}; "
            "installed /usr/bin/bwrap is intentionally not used"
        )
    try:
        executable = _SCRATCH_BWRAP.resolve(strict=True)
        scratch_root = _T097_ROOT.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"could not resolve T097 scratch Bubblewrap: {exc}")
    if not executable.is_relative_to(scratch_root) or not os.access(executable, os.X_OK):
        pytest.skip("T097 scratch Bubblewrap is outside its scratch tree or not executable")
    version = subprocess.run(
        [str(executable), "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
        env={"LC_ALL": "C"},
    )
    match = _VERSION_RE.fullmatch(version.stdout.strip())
    if version.returncode != 0 or match is None or tuple(map(int, match.groups())) != (0, 13, 0):
        pytest.skip(
            "this characterization requires scratch Bubblewrap 0.13.0; "
            f"observed stdout={version.stdout.strip()!r}, stderr={version.stderr.strip()!r}"
        )
    return executable


def _parse_report(output: str) -> dict[str, object]:
    reports = [
        line[len(_REPORT_PREFIX) :]
        for line in output.splitlines()
        if line.startswith(_REPORT_PREFIX)
    ]
    assert len(reports) == 1, f"namespace helper did not return one result: {output!r}"
    return json.loads(reports[0])


def _mountinfo_entries(stdout: str, mountpoint: str) -> list[tuple[list[str], list[str]]]:
    entries = []
    for line in stdout.splitlines():
        before_separator, separator, after_separator = line.partition(" - ")
        if not separator:
            continue
        left = before_separator.split()
        right = after_separator.split()
        if len(left) >= 6 and len(right) >= 2 and left[4] == mountpoint:
            entries.append((left, right))
    return entries


def _run_process_group(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"LC_ALL": "C"},
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        pytest.fail(
            "private namespace probe timed out; process group was killed; "
            f"stdout={stdout!r}, stderr={stderr!r}"
        )
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def test_allowed_loader_cannot_run_elf_from_private_noexec_workspace_bind(tmp_path: Path):
    """Probe a noexec bind remount of this checkout's writable filesystem path."""
    bwrap = _scratch_bubblewrap()
    compiler = shutil.which("cc")
    readelf = shutil.which("readelf")
    unshare = shutil.which("unshare")
    if compiler is None:
        pytest.skip("C compiler 'cc' is unavailable for the inert ELF probe")
    if readelf is None:
        pytest.skip("readelf is unavailable to inspect the fixture's PT_INTERP segment")
    if unshare is None:
        pytest.skip("unshare is unavailable for a private user and mount namespace")

    namespace_check = subprocess.run(
        [unshare, "--user", "--map-root-user", "--mount", "--fork", "--", "/bin/true"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={"LC_ALL": "C"},
    )
    if namespace_check.returncode != 0:
        pytest.skip(
            "private unprivileged user/mount namespace is unavailable: "
            f"{namespace_check.stderr.strip() or namespace_check.returncode}"
        )

    build = subprocess.run(
        [
            compiler,
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(_FIXTURE_SOURCE),
            "-o",
            str(tmp_path / "fd-probe"),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, f"could not compile inert probe:\n{build.stderr}"
    headers = subprocess.run(
        [readelf, "-l", str(tmp_path / "fd-probe")],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert headers.returncode == 0, f"readelf could not inspect probe ELF:\n{headers.stderr}"
    interpreter = re.search(r"Requesting program interpreter:\s*([^\]]+)\]", headers.stdout)
    if interpreter is None:
        pytest.skip("compiler produced no PT_INTERP segment for the allowed-loader probe")
    loader = Path(interpreter.group(1).strip())
    if not loader.is_absolute() or not loader.is_file():
        pytest.skip(f"probe ELF's dynamic loader is unavailable: {loader}")

    work = tmp_path / "runner"
    work.mkdir(mode=0o700)
    shutil.copyfile(tmp_path / "fd-probe", work / "fd-probe")
    (work / "fd-probe").chmod(0o700)

    # Keep the host filesystem under test identical to the checkout's mount.
    # The child remounts only this new directory, inside its private namespace.
    with tempfile.TemporaryDirectory(
        prefix="t097-noexec-bind-workspace-", dir=_PROJECT_ROOT
    ) as workspace_name:
        workspace = Path(workspace_name)
        shutil.copyfile(tmp_path / "fd-probe", workspace / "direct-control")
        (workspace / "direct-control").chmod(0o700)
        namespace = _run_process_group(
            [
                unshare,
                "--user",
                "--map-root-user",
                "--mount",
                "--fork",
                "--",
                sys.executable,
                "-c",
                _NAMESPACE_HELPER,
                str(workspace),
                str(bwrap),
                str(work),
                str(loader),
            ],
            timeout=40,
        )
        assert namespace.returncode == 0, (
            "private user/mount namespace probe failed; "
            f"stdout={namespace.stdout!r}, stderr={namespace.stderr!r}"
        )
        report = _parse_report(namespace.stdout)
        if "remount_error" in report:
            pytest.skip(
                "workspace filesystem/kernel cannot bind-remount the writable directory "
                f"noexec: {report['remount_error']}"
            )
        assert "cleanup_error" not in report, f"private bind mount cleanup failed: {report!r}"

        entries = _mountinfo_entries(report["bwrap_stdout"], "/tmp/workspace")
        assert entries, (
            "sandbox mountinfo has no workspace entry at /tmp/workspace: "
            f"{report['bwrap_stdout']!r}"
        )
        topmost_mount, topmost_filesystem = entries[-1]
        assert topmost_mount[3] == report["source_mount_root"], (
            "Bwrap exposed a different mount root than the exact remounted workspace: "
            f"source={report['source_mount_root']!r}, sandbox={topmost_mount!r}"
        )
        assert topmost_filesystem[0] == report["source_mount_fstype"], (
            "Bwrap workspace filesystem differs from the remounted source: "
            f"source={report['source_mount_fstype']!r}, sandbox={topmost_filesystem!r}"
        )
        assert "noexec" in topmost_mount[5].split(","), (
            "topmost sandbox workspace mount is not noexec: " f"{topmost_mount!r}"
        )

        if report.get("bwrap_returncode") == 78 and "landlock-unavailable:" in report.get(
            "bwrap_stderr", ""
        ):
            pytest.skip(f"kernel Landlock EXECUTE probe is unavailable: {report['bwrap_stderr']}")
        assert report["direct_exec_errno"] == errno.EACCES, (
            "direct exec from the remounted workspace should return EACCES; "
            f"observed errno={report['direct_exec_errno']!r}"
        )
        assert not report["direct_exec_marker_exists"], "noexec direct-exec control ran its ELF"
        assert report["bwrap_returncode"] == 127, (
            "measured allowed-loader outcome changed; "
            f"status={report['bwrap_returncode']}, stderr={report['bwrap_stderr']!r}"
        )
        assert "direct-exec-denied=1" in report["bwrap_stdout"]
        assert "failed to map segment from shared object" in report["bwrap_stderr"]
        assert not report["loader_marker_exists"], "allowed loader ran ELF from noexec workspace"
