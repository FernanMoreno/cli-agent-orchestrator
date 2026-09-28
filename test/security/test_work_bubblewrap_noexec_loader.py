"""Characterize the allowed-loader probe on a private noexec tmpfs.

With the pinned T097 Bubblewrap 0.13.0 build, this test asserts that the exact
private tmpfs appears as the topmost noexec mount inside the sandbox, direct
exec returns EACCES, and the fixture's allowed dynamic loader does not create
its marker. It does not authorize the Work backend or establish behavior for
other kernels, loaders, binaries, or mounts.
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


_T097_ROOT = Path("/tmp/caos-exec/T097")
_SCRATCH_BWRAP = _T097_ROOT / "bubblewrap-build" / "bwrap"
_VERSION_RE = re.compile(r"^bubblewrap\s+(\d+)\.(\d+)\.(\d+)(?:\s.*)?$")
_FIXTURE_SOURCE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "work_bubblewrap_fd_probe.c"
)
_REPORT_PREFIX = "T097_NOEXEC_REPORT="


_NAMESPACE_HELPER = r'''
import ctypes
import json
import os
import pathlib
import shutil
import subprocess
import sys

mountpoint = pathlib.Path(sys.argv[1])
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

MS_NOSUID = 2
MS_NODEV = 4
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


mount(None, "/", None, MS_REC | MS_PRIVATE, None)
mounted = False
try:
    mount(
        "t097-noexec",
        mountpoint,
        "tmpfs",
        MS_NOSUID | MS_NODEV | MS_NOEXEC,
        "size=16m,mode=700",
    )
    mounted = True
    shutil.copyfile(work / "fd-probe", mountpoint / "fd-probe")
    (mountpoint / "fd-probe").chmod(0o700)

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
        str(mountpoint),
        "/dev/shm",
        "--proc",
        "/proc",
        "--chdir",
        "/tmp",
        "--",
        "/bin/sh",
        "-c",
        "cat /proc/self/mountinfo; exec /tmp/fd-probe --shm-loader "
        "/dev/shm/fd-probe /dev/shm/payload /tmp/loader.marker \"$1\"",
        "t097-noexec-loader",
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

    payload = mountpoint / "payload"
    direct_errno = direct_exec_errno(payload, work / "direct-exec.marker")
    report = {
        "bwrap_returncode": result.returncode,
        "bwrap_stdout": result.stdout,
        "bwrap_stderr": result.stderr,
        "probe_marker_exists": (work / "loader.marker").exists(),
        "direct_exec_errno": direct_errno,
        "direct_exec_marker_exists": (work / "direct-exec.marker").exists(),
    }
finally:
    if mounted and libc.umount2(str(mountpoint).encode(), MNT_DETACH) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(mountpoint))

print("T097_NOEXEC_REPORT=" + json.dumps(report), flush=True)
'''


def _scratch_bubblewrap() -> Path:
    """Resolve and verify the exact scratch build; never use PATH fallback."""
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
    if not executable.is_relative_to(scratch_root):
        pytest.skip("T097 Bubblewrap resolves outside its scratch tree; refusing fallback")
    if executable == Path("/usr/bin/bwrap").resolve():
        pytest.skip("T097 Bubblewrap resolves to installed /usr/bin/bwrap; refusing fallback")
    if not os.access(executable, os.X_OK):
        pytest.skip(f"T097 scratch Bubblewrap is not executable: {executable}")

    version = subprocess.run(
        [str(executable), "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
        env={"LC_ALL": "C"},
    )
    match = _VERSION_RE.fullmatch(version.stdout.strip())
    if version.returncode != 0 or match is None:
        pytest.skip(
            "T097 scratch Bubblewrap did not report a parseable version: "
            f"stdout={version.stdout!r}, stderr={version.stderr!r}"
        )
    if tuple(int(part) for part in match.groups()) != (0, 13, 0):
        pytest.skip(
            "this test characterizes Bubblewrap 0.13.0 only; "
            f"scratch binary reports {version.stdout.strip()}"
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
            "private namespace probe timed out; its process group was killed; "
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


def test_allowed_loader_cannot_run_elf_from_private_noexec_tmpfs():
    """Record the measured noexec result for this fixture and loader only."""
    bwrap = _scratch_bubblewrap()
    compiler = shutil.which("cc")
    readelf = shutil.which("readelf")
    unshare = shutil.which("unshare")
    if compiler is None:
        pytest.skip("C compiler 'cc' is unavailable for the inert ELF probe")
    if readelf is None:
        pytest.skip("readelf is unavailable to inspect the fixture's PT_INTERP segment")
    if unshare is None:
        pytest.skip("unshare is unavailable for the private user and mount namespace")

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

    temporary = tempfile.TemporaryDirectory(prefix="t097-noexec-loader-", dir="/tmp")
    try:
        root = Path(temporary.name)
        probe = root / "fd-probe"
        work = root / "work"
        mountpoint = root / "noexec-tmpfs"
        work.mkdir(mode=0o700)
        mountpoint.mkdir(mode=0o700)

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
                str(probe),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert build.returncode == 0, f"could not compile inert probe:\n{build.stderr}"
        headers = subprocess.run(
            [readelf, "-l", str(probe)],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert headers.returncode == 0, f"readelf could not inspect probe ELF:\n{headers.stderr}"
        interpreter = re.search(
            r"Requesting program interpreter:\s*([^\]]+)\]", headers.stdout
        )
        if interpreter is None:
            pytest.skip("compiler produced no PT_INTERP segment for the allowed-loader probe")
        loader = Path(interpreter.group(1).strip())
        if not loader.is_absolute() or not loader.is_file():
            pytest.skip(f"probe ELF's dynamic loader is unavailable: {loader}")

        shutil.copyfile(probe, work / "fd-probe")
        (work / "fd-probe").chmod(0o700)
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
                str(mountpoint),
                str(bwrap),
                str(work),
                str(loader),
            ],
            timeout=35,
        )
        assert namespace.returncode == 0, (
            "private user/mount namespace probe failed; "
            f"stdout={namespace.stdout!r}, stderr={namespace.stderr!r}"
        )
        report = _parse_report(namespace.stdout)

        entries = _mountinfo_entries(report["bwrap_stdout"], "/dev/shm")
        passed_mounts = [
            (mountinfo, filesystem)
            for mountinfo, filesystem in entries
            if filesystem[0] == "tmpfs" and filesystem[1] == "t097-noexec"
        ]
        assert len(passed_mounts) == 1, (
            "sandbox mountinfo does not show the exact private tmpfs at /dev/shm: "
            f"{entries!r}"
        )
        mountinfo, _filesystem = passed_mounts[0]
        assert entries[-1] == passed_mounts[0], (
            "the passed noexec tmpfs is not the topmost /dev/shm mount: "
            f"{entries!r}"
        )
        mount_options = set(mountinfo[5].split(","))
        assert "noexec" in mount_options, f"sandbox /dev/shm is not noexec: {mountinfo!r}"

        if report["bwrap_returncode"] == 78 and "landlock-unavailable:" in report["bwrap_stderr"]:
            pytest.skip(f"kernel Landlock EXECUTE probe is unavailable: {report['bwrap_stderr']}")

        assert report["direct_exec_errno"] == errno.EACCES, (
            "direct exec from the noexec mount should return EACCES; "
            f"observed errno={report['direct_exec_errno']!r}"
        )
        assert not report["direct_exec_marker_exists"], "noexec direct-exec control ran its ELF"
        assert report["bwrap_returncode"] == 127, (
            "measured allowed-loader outcome changed; "
            f"status={report['bwrap_returncode']}, stderr={report['bwrap_stderr']!r}"
        )
        assert "direct-exec-denied=1" in report["bwrap_stdout"]
        assert "failed to map segment from shared object" in report["bwrap_stderr"]
        assert not report["probe_marker_exists"], "allowed loader ran the ELF from noexec tmpfs"
    finally:
        temporary.cleanup()
