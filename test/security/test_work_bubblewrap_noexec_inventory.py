"""Inventory the writable mount surfaces exposed by pinned T097 Bubblewrap.

All mounts live in a private user/mount namespace. The inert C runner is bound
read-only at a trusted executable path; the workspace, /tmp, and /dev/shm
sources are separate writable noexec tmpfs mounts. The existing fixture has a
detached fork/exec loader probe; shell commands add the /proc/self/fd alias
probe without changing that fixture.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_T097_ROOT = Path("/tmp/caos-exec/T097")
_SCRATCH_BWRAP = _T097_ROOT / "bubblewrap-build" / "bwrap"
_VERSION_RE = re.compile(r"^bubblewrap\s+(\d+)\.(\d+)\.(\d+)(?:\s.*)?$")
_FIXTURE_SOURCE = _PROJECT_ROOT / "test" / "fixtures" / "work_bubblewrap_fd_probe.c"
_REPORT_PREFIX = "T097_INVENTORY_REPORT="
_TRUSTED_RUNNER = "/usr/bin/sed"


_NAMESPACE_HELPER = r'''
import ctypes
import errno
import json
import os
import pathlib
import re
import shlex
import signal
import shutil
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
bwrap = sys.argv[2]
runner_build = pathlib.Path(sys.argv[3])
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
MS_RDONLY = 1
MS_REMOUNT = 32
MNT_DETACH = 2
NO_MOUNT_CAPABILITY = {errno.EPERM, errno.EACCES, errno.ENOSYS, errno.EOPNOTSUPP}


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


def mountinfo_entries(contents):
    entries = []
    for line in contents.splitlines():
        before, separator, after = line.partition(" - ")
        if not separator:
            continue
        left = before.split()
        right = after.split()
        if len(left) < 6 or len(right) < 3:
            continue
        entries.append({
            "mount_id": int(left[0]),
            "root": left[3],
            "mountpoint": left[4],
            "options": left[5].split(","),
            "fstype": right[0],
            "source": right[1],
            "super_options": right[2].split(","),
        })
    return entries


def topmost(entries, path):
    matches = [entry for entry in entries if entry["mountpoint"] == path]
    if not matches:
        raise RuntimeError("mountinfo has no entry at " + path)
    return matches[-1]


mounts = {
    "workspace": (root / "workspace", "t097-workspace"),
    "tmp": (root / "tmp", "t097-tmp"),
    "shm": (root / "shm", "t097-shm"),
    "runner": (root / "runner", "t097-runner"),
}
for target, _source in mounts.values():
    target.mkdir(parents=True, exist_ok=True)
mounted = []
report = {}
mount(None, "/", None, MS_REC | MS_PRIVATE, None)
try:
    for name, (target, source) in mounts.items():
        flags = MS_NOSUID | MS_NODEV
        if name != "runner":
            flags |= MS_NOEXEC
        try:
            mount(source, target, "tmpfs", flags, "rw,size=16m,mode=700")
        except OSError as exc:
            if exc.errno in NO_MOUNT_CAPABILITY:
                report["capability_unavailable"] = {
                    "operation": "mount private tmpfs " + name,
                    "errno": exc.errno,
                    "message": str(exc),
                }
                break
            raise
        mounted.append(target)

    if "capability_unavailable" not in report:
        runner = mounts["runner"][0] / "fd-probe"
        shutil.copyfile(runner_build, runner)
        runner.chmod(0o500)
        try:
            mount(None, mounts["runner"][0], None, MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV, "mode=700")
        except OSError as exc:
            if exc.errno in NO_MOUNT_CAPABILITY:
                report["capability_unavailable"] = {
                    "operation": "make trusted runner source read-only",
                    "errno": exc.errno,
                    "message": str(exc),
                }
            else:
                raise

    if "capability_unavailable" not in report:
        outer_entries = mountinfo_entries(pathlib.Path("/proc/self/mountinfo").read_text())
        expected_outer = {
            "workspace": mounts["workspace"][0],
            "tmp": mounts["tmp"][0],
            "shm": mounts["shm"][0],
        }
        outer_inventory = {}
        for name, path in expected_outer.items():
            entry = topmost(outer_entries, str(path))
            outer_inventory[name] = entry
            if entry["fstype"] != "tmpfs" or "rw" not in entry["options"] or "noexec" not in entry["options"]:
                raise RuntimeError("outer private mount is not rw+noexec: " + repr(entry))
        runner_outer = topmost(outer_entries, str(mounts["runner"][0]))
        if runner_outer["fstype"] != "tmpfs" or "ro" not in runner_outer["options"] or "noexec" in runner_outer["options"]:
            raise RuntimeError("trusted runner source is not ro+exec: " + repr(runner_outer))

        commands = ["/bin/cat /proc/self/mountinfo", "set +e"]
        runner_path = shlex.quote("/usr/bin/sed")
        loader_path = shlex.quote(loader)
        for name, target in (("workspace", "/tmp/workspace"), ("tmp", "/tmp"), ("shm", "/dev/shm")):
            direct_payload = target + "/t097-loader-payload"
            direct_marker = target + "/t097-loader-marker"
            direct = " ".join([
                runner_path,
                "--shm-loader",
                runner_path,
                shlex.quote(direct_payload),
                shlex.quote(direct_marker),
                loader_path,
            ])
            commands.append(direct + "; status=$?; printf '\\nT097_PROBE_STATUS=" + name + ",loader,%s\\n' \"$status\"")

            descendant_payload = target + "/t097-descendant-payload"
            descendant_marker = target + "/t097-descendant-marker"
            descendant = " ".join([
                runner_path,
                "--shm-loader-descendant",
                runner_path,
                shlex.quote(descendant_payload),
                shlex.quote(descendant_marker),
                loader_path,
            ])
            commands.append(descendant + "; status=$?; printf '\\nT097_PROBE_STATUS=" + name + ",descendant,%s\\n' \"$status\"")

            alias_payload = target + "/t097-fd-alias-payload"
            alias_direct_marker = target + "/t097-fd-alias-direct.marker"
            alias_loader_marker = target + "/t097-fd-alias-loader.marker"
            alias_direct_stderr = target + "/t097-fd-alias-direct.stderr"
            alias_loader_stderr = target + "/t097-fd-alias-loader.stderr"
            commands.append(
                "cp " + runner_path + " " + shlex.quote(alias_payload)
                + " && chmod 700 " + shlex.quote(alias_payload)
            )
            direct_alias = (
                "( exec 3<" + shlex.quote(alias_payload) + "; "
                "/proc/self/fd/3 --write-marker " + shlex.quote(alias_direct_marker) + " )"
            )
            commands.append(
                direct_alias + " 2>" + shlex.quote(alias_direct_stderr)
                + "; status=$?; printf '\\nT097_ALIAS_STATUS="
                + name + ",direct,%s\\n' \"$status\""
            )
            loader_alias = (
                "( exec 3<" + shlex.quote(alias_payload) + "; "
                + loader_path + " /proc/self/fd/3 --write-marker "
                + shlex.quote(alias_loader_marker) + " )"
            )
            commands.append(
                loader_alias + " 2>" + shlex.quote(alias_loader_stderr)
                + "; status=$?; printf '\\nT097_ALIAS_STATUS="
                + name + ",loader,%s\\n' \"$status\""
            )
        commands.append("exit 0")
        sandbox_command = [
            bwrap,
            "--unshare-all",
            "--die-with-parent",
            "--ro-bind", "/", "/",
            "--ro-bind", str(mounts["runner"][0] / "fd-probe"), "/usr/bin/sed",
            "--bind", str(mounts["tmp"][0]), "/tmp",
            "--dir", "/tmp/workspace",
            "--bind", str(mounts["workspace"][0]), "/tmp/workspace",
            "--bind", str(mounts["shm"][0]), "/dev/shm",
            "--proc", "/proc",
            "--chdir", "/tmp/workspace",
            "--", "/bin/sh", "-c", "\n".join(commands),
        ]
        sandbox_process = subprocess.Popen(
            sandbox_command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={"LC_ALL": "C"},
            start_new_session=True,
        )
        sandbox_timed_out = False
        try:
            sandbox_stdout, stderr = sandbox_process.communicate(timeout=45)
        except subprocess.TimeoutExpired as timeout_output:
            sandbox_timed_out = True
            try:
                os.killpg(sandbox_process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                sandbox_stdout, stderr = sandbox_process.communicate(timeout=5)
            except subprocess.TimeoutExpired as pipe_timeout:
                sandbox_process.kill()
                sandbox_process.wait()
                sandbox_process.stdout.close()
                sandbox_process.stderr.close()
                sandbox_stdout = pipe_timeout.output or timeout_output.output or ""
                stderr = pipe_timeout.stderr or timeout_output.stderr or ""
                if isinstance(sandbox_stdout, bytes):
                    sandbox_stdout = sandbox_stdout.decode("utf-8", "replace")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", "replace")
        sandbox = subprocess.CompletedProcess(
            sandbox_command, sandbox_process.returncode, sandbox_stdout, stderr
        )

        if sandbox.returncode != 0 and re.search(
            r"Creating new namespace failed: (Operation not permitted|Permission denied|Function not implemented)",
            stderr,
        ):
            report["capability_unavailable"] = {
                "operation": "Bubblewrap requested namespaces",
                "returncode": sandbox.returncode,
                "stderr": stderr,
            }
        else:
            sandbox_entries = mountinfo_entries(sandbox.stdout)
            report.update({
                "bwrap_returncode": sandbox.returncode,
                "bwrap_timed_out": sandbox_timed_out,
                "bwrap_stdout": sandbox.stdout,
                "bwrap_stderr": stderr,
                "outer_inventory": outer_inventory,
                "sandbox_entries": sandbox_entries,
                "markers": {
                    "workspace": {
                        "loader": (mounts["workspace"][0] / "t097-loader-marker").exists(),
                        "descendant": (mounts["workspace"][0] / "t097-descendant-marker").exists(),
                    },
                    "tmp": {
                        "loader": (mounts["tmp"][0] / "t097-loader-marker").exists(),
                        "descendant": (mounts["tmp"][0] / "t097-descendant-marker").exists(),
                    },
                    "shm": {
                        "loader": (mounts["shm"][0] / "t097-loader-marker").exists(),
                        "descendant": (mounts["shm"][0] / "t097-descendant-marker").exists(),
                    },
                },
                "fd_alias_markers": {
                    "workspace": {
                        "direct": (mounts["workspace"][0] / "t097-fd-alias-direct.marker").exists(),
                        "loader": (mounts["workspace"][0] / "t097-fd-alias-loader.marker").exists(),
                    },
                    "tmp": {
                        "direct": (mounts["tmp"][0] / "t097-fd-alias-direct.marker").exists(),
                        "loader": (mounts["tmp"][0] / "t097-fd-alias-loader.marker").exists(),
                    },
                    "shm": {
                        "direct": (mounts["shm"][0] / "t097-fd-alias-direct.marker").exists(),
                        "loader": (mounts["shm"][0] / "t097-fd-alias-loader.marker").exists(),
                    },
                },
                "fd_alias_stderr": {
                    "workspace": {
                        "direct": (mounts["workspace"][0] / "t097-fd-alias-direct.stderr").read_text(errors="replace"),
                        "loader": (mounts["workspace"][0] / "t097-fd-alias-loader.stderr").read_text(errors="replace"),
                    },
                    "tmp": {
                        "direct": (mounts["tmp"][0] / "t097-fd-alias-direct.stderr").read_text(errors="replace"),
                        "loader": (mounts["tmp"][0] / "t097-fd-alias-loader.stderr").read_text(errors="replace"),
                    },
                    "shm": {
                        "direct": (mounts["shm"][0] / "t097-fd-alias-direct.stderr").read_text(errors="replace"),
                        "loader": (mounts["shm"][0] / "t097-fd-alias-loader.stderr").read_text(errors="replace"),
                    },
                },
            })
finally:
    cleanup_errors = []
    for target in reversed(mounted):
        if libc.umount2(str(target).encode(), MNT_DETACH) != 0:
            error = ctypes.get_errno()
            cleanup_errors.append({"mountpoint": str(target), "errno": error, "message": os.strerror(error)})
    report["cleanup_errors"] = cleanup_errors

print("T097_INVENTORY_REPORT=" + json.dumps(report), flush=True)
'''


def _scratch_bubblewrap() -> Path:
    """Resolve only the pinned scratch build; never fall back to PATH."""
    if not _SCRATCH_BWRAP.is_file():
        pytest.skip(f"T097 scratch Bubblewrap is absent at {_SCRATCH_BWRAP}; installed bwrap is not used")
    try:
        executable = _SCRATCH_BWRAP.resolve(strict=True)
        scratch_root = _T097_ROOT.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"could not resolve T097 scratch Bubblewrap: {exc}")
    if not executable.is_relative_to(scratch_root) or executable == Path("/usr/bin/bwrap").resolve():
        pytest.skip("T097 Bubblewrap does not resolve to its private scratch build")
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
    if version.returncode != 0 or match is None or tuple(map(int, match.groups())) != (0, 13, 0):
        pytest.skip(f"T097 scratch Bubblewrap 0.13.0 is required; observed {version.stdout.strip()!r}")
    return executable


def _parse_report(output: str) -> dict[str, object]:
    reports = [line[len(_REPORT_PREFIX):] for line in output.splitlines() if line.startswith(_REPORT_PREFIX)]
    assert len(reports) == 1, f"namespace helper did not return one inventory: {output!r}"
    return json.loads(reports[0])


def _topmost(entries: list[dict[str, object]], mountpoint: str) -> dict[str, object]:
    matches = [entry for entry in entries if entry["mountpoint"] == mountpoint]
    assert matches, f"sandbox mountinfo has no entry at {mountpoint}: {entries!r}"
    return matches[-1]


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
        pytest.fail(f"private namespace inventory timed out; stdout={stdout!r}, stderr={stderr!r}")
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def test_pinned_bubblewrap_inventory_keeps_all_writable_surfaces_noexec(tmp_path: Path):
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

    runner_build = tmp_path / "fd-probe-build"
    build = subprocess.run(
        [compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", str(_FIXTURE_SOURCE), "-o", str(runner_build)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, f"could not compile inert probe:\n{build.stderr}"
    headers = subprocess.run(
        [readelf, "-l", str(runner_build)],
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

    mounts_root = tmp_path / "private-mounts"
    mounts_root.mkdir(mode=0o700)
    namespace = _run_process_group(
        [
            unshare,
            "--user", "--map-root-user", "--mount", "--fork", "--",
            sys.executable,
            "-c", _NAMESPACE_HELPER,
            str(mounts_root), str(bwrap), str(runner_build), str(loader),
        ],
        timeout=75,
    )
    assert namespace.returncode == 0, (
        "private user/mount namespace helper failed; "
        f"stdout={namespace.stdout!r}, stderr={namespace.stderr!r}"
    )
    report = _parse_report(namespace.stdout)
    assert report.get("cleanup_errors") == [], f"private mount cleanup failed: {report!r}"
    if "capability_unavailable" in report:
        unavailable = report["capability_unavailable"]
        if unavailable.get("operation") == "Bubblewrap requested namespaces":
            pytest.skip(f"requested Bubblewrap namespaces are unavailable: {unavailable!r}")
        pytest.skip(f"private tmpfs mount capability is unavailable: {unavailable!r}")

    assert not report["bwrap_timed_out"], (
        "Bubblewrap probe timed out; its process group was killed before private mounts were unmounted: "
        f"stdout={report['bwrap_stdout']!r}, stderr={report['bwrap_stderr']!r}"
    )
    assert report["bwrap_returncode"] == 0, (
        f"pinned Bubblewrap failed: stderr={report['bwrap_stderr']!r}; "
        f"stdout={report['bwrap_stdout']!r}"
    )
    if "landlock-unavailable:" in report["bwrap_stderr"]:
        pytest.skip(f"kernel Landlock EXECUTE probe is unavailable: {report['bwrap_stderr']!r}")

    entries = report["sandbox_entries"]
    surfaces = {
        "workspace": _topmost(entries, "/tmp/workspace"),
        "tmp": _topmost(entries, "/tmp"),
        "shm": _topmost(entries, "/dev/shm"),
        "runner": _topmost(entries, _TRUSTED_RUNNER),
    }
    expected = {
        "workspace": ("/tmp/workspace", "t097-workspace"),
        "tmp": ("/tmp", "t097-tmp"),
        "shm": ("/dev/shm", "t097-shm"),
    }
    for name, (mountpoint, source) in expected.items():
        entry = surfaces[name]
        assert entry["mountpoint"] == mountpoint and entry["source"] == source and entry["fstype"] == "tmpfs", (
            f"Bwrap did not retain the exact {name} tmpfs as its topmost mount: {entry!r}"
        )
        assert "rw" in entry["options"] and "noexec" in entry["options"], (
            f"Bwrap {name} mount is not writable+noexec: {entry!r}"
        )
    runner_mount = surfaces["runner"]
    assert runner_mount["mountpoint"] == _TRUSTED_RUNNER and "ro" in runner_mount["options"], (
        f"trusted runner is not read-only at its fixed path: {runner_mount!r}"
    )
    assert "noexec" not in runner_mount["options"], (
        f"trusted runner path is unexpectedly noexec: {runner_mount!r}"
    )

    statuses = re.findall(r"^T097_PROBE_STATUS=(workspace|tmp|shm),(loader|descendant),(\d+)$", report["bwrap_stdout"], re.M)
    assert len(statuses) == 6, f"not all per-surface probes reported a status: {statuses!r}"
    if "landlock-unavailable:" in report["bwrap_stderr"] or any(int(code) == 78 for _, _, code in statuses):
        pytest.skip("kernel Landlock EXECUTE probe is unavailable")
    for surface, mode, code in statuses:
        assert int(code) == 127, (
            f"{mode} probe from {surface} changed its noexec outcome; status={code}; "
            f"stderr={report['bwrap_stderr']!r}"
        )
    assert report["bwrap_stdout"].count("direct-exec-denied=1") == 6, (
        "fixture did not confirm direct exec denial for both probes on all three surfaces"
    )
    for name, markers in report["markers"].items():
        assert not markers["loader"] and not markers["descendant"], (
            f"a noexec {name} surface ran its inert ELF marker: {markers!r}"
        )

    writable_exec = [
        {
            "mountpoint": entry["mountpoint"],
            "source": entry["source"],
            "fstype": entry["fstype"],
            "options": entry["options"],
        }
        for entry in entries
        if "rw" in entry["options"] and "noexec" not in entry["options"]
    ]
    assert writable_exec == [], f"sandbox exposes writable+exec mounts: {writable_exec!r}"
    print("T097_WRITABLE_EXEC_INVENTORY=" + json.dumps(writable_exec, sort_keys=True))

    alias_statuses = re.findall(
        r"^T097_ALIAS_STATUS=(workspace|tmp|shm),(direct|loader),(\d+)$",
        report["bwrap_stdout"],
        re.M,
    )
    assert len(alias_statuses) == 6, f"not all /proc/self/fd/3 probes reported a status: {alias_statuses!r}"
    assert {(surface, mode) for surface, mode, _ in alias_statuses} == {
        (surface, mode)
        for surface in ("workspace", "tmp", "shm")
        for mode in ("direct", "loader")
    }, f"/proc/self/fd/3 probe statuses are incomplete or duplicated: {alias_statuses!r}"
    alias_report = []
    for surface, mode, code in alias_statuses:
        marker = report["fd_alias_markers"][surface][mode]
        stderr = report["fd_alias_stderr"][surface][mode]
        expected_code = 126 if mode == "direct" else 127
        expected_error = "Permission denied" if mode == "direct" else "failed to map segment from shared object"
        alias_report.append({
            "surface": surface,
            "mode": mode,
            "status": int(code),
            "marker": marker,
            "stderr": stderr.strip(),
        })
        assert int(code) == expected_code and not marker and expected_error in stderr, (
            f"/proc/self/fd/3 {mode} exec residual on {surface}: "
            f"status={code}, marker={marker}, stderr={stderr!r}, "
            f"writable+exec inventory={writable_exec!r}"
        )
    print("T097_FD_ALIAS_RESULTS=" + json.dumps(alias_report, sort_keys=True))
