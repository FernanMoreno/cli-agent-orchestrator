"""Bounded T097 probes for still-open Bubblewrap execution boundaries.

These tests characterize the exact T097 scratch Bubblewrap 0.13.0 behavior.
They are not evidence to register or authorize Bubblewrap Work: its preflight
must continue rejecting before effects until the open execution boundaries are
closed and separately proven.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_T097_ROOT = Path("/tmp/caos-exec/T097")
_SCRATCH_BWRAP = _T097_ROOT / "bubblewrap-build" / "bwrap"
_VERSION_RE = re.compile(r"^bubblewrap\s+(\d+)\.(\d+)\.(\d+)(?:\s.*)?$")
_FIXTURE_SOURCE = Path(__file__).resolve().parents[1] / "fixtures" / "work_bubblewrap_fd_probe.c"
_FD_MARKER = b"cao-t097-inert-fd-probe\n"
_PAYLOAD_MARKER = "t097-writable-shm-elf-ran\n"


@dataclass(frozen=True)
class ScratchBubblewrap:
    executable: Path


@dataclass(frozen=True)
class ProbeResult:
    returncode: int
    stdout: str
    stderr: str


@pytest.fixture(scope="session")
def scratch_bubblewrap() -> ScratchBubblewrap:
    """Use the exact T097 scratch build; never search PATH or fall back."""
    if not _SCRATCH_BWRAP.is_file():
        pytest.skip(
            f"T097 scratch Bubblewrap is absent at {_SCRATCH_BWRAP}; "
            "installed /usr/bin/bwrap is intentionally not used"
        )
    try:
        executable = _SCRATCH_BWRAP.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"cannot resolve T097 scratch Bubblewrap {_SCRATCH_BWRAP}: {exc}")
    scratch_root = _T097_ROOT.resolve()
    if not executable.is_relative_to(scratch_root):
        pytest.skip(
            "T097 Bubblewrap resolves outside the scratch tree; refusing to run "
            "an installed or unrelated bwrap binary"
        )
    if executable == Path("/usr/bin/bwrap").resolve():
        pytest.skip("T097 Bubblewrap resolves to installed /usr/bin/bwrap; refusing fallback")
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
        pytest.skip(f"could not probe T097 scratch Bubblewrap: {exc}")
    match = _VERSION_RE.fullmatch(version.stdout.strip())
    if version.returncode != 0 or match is None:
        pytest.skip(
            "T097 scratch Bubblewrap did not report a parseable version: "
            f"stdout={version.stdout!r} stderr={version.stderr!r}"
        )
    rendered = tuple(int(part) for part in match.groups())
    if rendered != (0, 13, 0):
        pytest.skip(
            "these probes characterize Bubblewrap 0.13.0 only; "
            f"scratch binary reports {version.stdout.strip()}"
        )
    return ScratchBubblewrap(executable=executable)


@pytest.fixture(scope="session")
def sandbox_ready(scratch_bubblewrap: ScratchBubblewrap) -> ScratchBubblewrap:
    """Skip with the exact missing namespace or mount prerequisite."""
    try:
        result = subprocess.run(
            [
                str(scratch_bubblewrap.executable),
                "--unshare-all",
                "--die-with-parent",
                "--ro-bind",
                "/",
                "/",
                "--tmpfs",
                "/dev/shm",
                "--proc",
                "/proc",
                "--",
                "/bin/true",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={"LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"Bubblewrap namespace/mount prerequisite probe failed: {exc}")
    if result.returncode != 0:
        reason = result.stderr.strip() or f"exit status {result.returncode}"
        pytest.skip(
            "required Bwrap namespaces or writable /dev/shm tmpfs are unavailable: " f"{reason}"
        )
    return scratch_bubblewrap


@pytest.fixture(scope="session")
def probe_binary(scratch_bubblewrap: ScratchBubblewrap, tmp_path_factory) -> Path:
    del scratch_bubblewrap
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("C compiler 'cc' is unavailable for the self-contained ELF probe")
    binary = tmp_path_factory.mktemp("work-bubblewrap-open-questions") / "fd-probe"
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
            str(binary),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, f"could not compile T097 probe fixture:\n{build.stderr}"
    return binary


@pytest.fixture(scope="session")
def dynamic_loader(probe_binary: Path) -> tuple[Path, Path]:
    readelf = shutil.which("readelf")
    if readelf is None:
        pytest.skip("readelf is unavailable to inspect the fixture's PT_INTERP segment")
    headers = subprocess.run(
        [readelf, "-l", str(probe_binary)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert headers.returncode == 0, f"readelf could not inspect the probe ELF: {headers.stderr}"
    match = re.search(r"Requesting program interpreter:\s*([^\]]+)\]", headers.stdout)
    if match is None:
        pytest.skip("compiler produced no PT_INTERP segment; dynamic-loader probe is unavailable")
    loader_path = Path(match.group(1).strip())
    if not loader_path.is_absolute() or not loader_path.is_file():
        pytest.skip(f"fixture's dynamic loader is unavailable: {loader_path}")
    return loader_path, loader_path.resolve()


def _run_child(
    argv: list[str], *, pass_fds: tuple[int, ...] = (), timeout: int = 15
) -> ProbeResult:
    """Run only a private probe and reap/kill its whole process group."""
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={"LC_ALL": "C"},
            pass_fds=pass_fds,
            start_new_session=True,
        )
    except OSError as exc:
        pytest.fail(f"could not launch the verified T097 scratch probe: {exc}")

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        pytest.fail(
            f"scratch probe timed out and its process group was killed; "
            f"stdout={stdout!r}, stderr={stderr!r}"
        )
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    return ProbeResult(process.returncode, stdout, stderr)


def test_bubblewrap_0130_passes_explicit_memfd_to_exact_probe_launcher(
    sandbox_ready: ScratchBubblewrap, probe_binary: Path, tmp_path: Path
):
    """Record the FD risk; neither `--preserve-fds` nor this result authorizes Work."""
    if not hasattr(os, "memfd_create"):
        pytest.skip("Python/runtime lacks memfd_create for the inert inherited-FD probe")
    mounted_probe = tmp_path / "fd-probe"
    shutil.copyfile(probe_binary, mounted_probe)
    mounted_probe.chmod(0o700)
    fd = os.memfd_create("cao-t097-inert-fd-probe", os.MFD_CLOEXEC)
    try:
        os.write(fd, _FD_MARKER)
        os.lseek(fd, 0, os.SEEK_SET)
        os.set_inheritable(fd, True)

        preserve_flag = _run_child(
            [
                str(sandbox_ready.executable),
                "--preserve-fds",
                "1",
                "--",
                "/bin/true",
            ],
            pass_fds=(fd,),
        )
        assert preserve_flag.returncode != 0
        assert "Unknown option --preserve-fds" in preserve_flag.stderr

        launcher = _run_child(
            [
                str(sandbox_ready.executable),
                "--unshare-all",
                "--die-with-parent",
                "--ro-bind",
                "/",
                "/",
                "--bind",
                str(tmp_path),
                "/tmp",
                "--proc",
                "/proc",
                "--chdir",
                "/tmp",
                "--",
                "/tmp/fd-probe",
                "--fd-check",
                str(fd),
            ],
            pass_fds=(fd,),
        )
    finally:
        os.close(fd)

    assert launcher.returncode == 0, (
        f"explicitly passed memfd was expected to reach the sandbox process; "
        f"status={launcher.returncode}, stdout={launcher.stdout!r}, "
        f"stderr={launcher.stderr!r}"
    )
    assert "memfd-inherited=present" in launcher.stdout


def test_allowed_loader_runs_elf_copied_to_writable_dev_shm(
    sandbox_ready: ScratchBubblewrap,
    probe_binary: Path,
    dynamic_loader: tuple[Path, Path],
    tmp_path: Path,
):
    """Characterize loader execution of a writable ELF despite FS_EXECUTE policy."""
    loader_arg, _resolved_loader = dynamic_loader
    mounted_probe = tmp_path / "fd-probe"
    shutil.copyfile(probe_binary, mounted_probe)
    mounted_probe.chmod(0o700)
    marker = tmp_path / "shm-loader.marker"
    result = _run_child(
        [
            str(sandbox_ready.executable),
            "--unshare-all",
            "--die-with-parent",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            str(tmp_path),
            "/tmp",
            "--tmpfs",
            "/dev/shm",
            "--proc",
            "/proc",
            "--chdir",
            "/tmp",
            "--",
            "/tmp/fd-probe",
            "--shm-loader",
            "/tmp/fd-probe",
            "/dev/shm/t097-writable-payload",
            "/tmp/shm-loader.marker",
            str(loader_arg),
        ],
    )
    if result.returncode == 78 and "landlock-unavailable:" in result.stderr:
        pytest.skip(f"kernel Landlock EXECUTE probe unavailable: {result.stderr.strip()}")

    assert result.returncode == 0, (
        f"allowed-loader probe failed: status={result.returncode}, "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    assert "direct-exec-denied=1" in result.stdout
    assert marker.read_text(encoding="utf-8") == _PAYLOAD_MARKER


def test_allowed_loader_runs_writable_elf_from_setsid_double_fork_descendant(
    sandbox_ready: ScratchBubblewrap,
    probe_binary: Path,
    dynamic_loader: tuple[Path, Path],
    tmp_path: Path,
):
    """Characterize the loader bypass in a detached descendant; this does not authorize Work."""
    loader_arg, _resolved_loader = dynamic_loader
    mounted_probe = tmp_path / "fd-probe"
    shutil.copyfile(probe_binary, mounted_probe)
    mounted_probe.chmod(0o700)
    marker = tmp_path / "descendant-loader.marker"
    result = _run_child(
        [
            str(sandbox_ready.executable),
            "--unshare-all",
            "--die-with-parent",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            str(tmp_path),
            "/tmp",
            "--tmpfs",
            "/dev/shm",
            "--proc",
            "/proc",
            "--chdir",
            "/tmp",
            "--",
            "/tmp/fd-probe",
            "--shm-loader-descendant",
            "/tmp/fd-probe",
            "/dev/shm/t097-descendant-writable-payload",
            "/tmp/descendant-loader.marker",
            str(loader_arg),
        ],
    )
    if result.returncode == 78 and "landlock-unavailable:" in result.stderr:
        pytest.skip(f"kernel Landlock EXECUTE probe unavailable: {result.stderr.strip()}")

    assert result.returncode == 0, (
        f"detached-descendant loader probe failed: status={result.returncode}, "
        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    assert "direct-exec-denied=1" in result.stdout
    assert marker.read_text(encoding="utf-8") == _PAYLOAD_MARKER


def test_bubblewrap_backend_preflight_still_rejects_before_effects(
    scratch_bubblewrap: ScratchBubblewrap, tmp_path: Path, monkeypatch
):
    """Characterization evidence does not register or authorize the backend."""
    from cli_agent_orchestrator.backends import bubblewrap_backend as bubblewrap_module
    from cli_agent_orchestrator.backends.base import (
        ProcessRestrictionContract,
        UnsupportedWorkEnforcement,
    )
    from cli_agent_orchestrator.backends.bubblewrap_backend import BubblewrapWorkBackend

    monkeypatch.setattr(bubblewrap_module, "_require_work_broker_identity", lambda _name: None)
    monkeypatch.setattr(bubblewrap_module, "_probe_bwrap_version", lambda _: (0, 13, 0))

    class NoEffectsClient:
        def __init__(self):
            self.effects = []

        def __getattr__(self, name):
            if name.startswith(("create_", "send_", "kill_", "pipe_", "stop_")):
                self.effects.append(name)
            raise AssertionError(f"unexpected effect: {name}")

    client = NoEffectsClient()
    backend = BubblewrapWorkBackend(client=client, bwrap_executable=scratch_bubblewrap.executable)
    contract = ProcessRestrictionContract(
        paths=(str(tmp_path.resolve()),),
        commands=(),
        network=(),
        read_paths=(str(tmp_path.resolve()),),
        checkout_root=str(tmp_path.resolve()),
    )

    with pytest.raises(UnsupportedWorkEnforcement, match="no command contract"):
        backend.preflight_work(contract)
    assert client.effects == []
