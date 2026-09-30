"""Adversarial evidence for the unregistered Bubblewrap Work backend.

These tests reproduce narrow Landlock EXECUTE-policy counterexamples with the
official scratch Bubblewrap build at ``/tmp/caos-exec/T097``. They do not run
the installed ``/usr/bin/bwrap`` and never fall back to it. The denied controls
cover only those exact kernel entrypoints; they do not prove that an executable
allowlist is complete.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

_T097_ROOT = Path("/tmp/caos-exec/T097")
_SCRATCH_BWRAP = _T097_ROOT / "bubblewrap-build" / "bwrap"
_BWRAP_VERSION = re.compile(r"^bubblewrap\s+(\d+)\.(\d+)\.(\d+)(?:\s.*)?$")
_MARKER_CONTENT = "landlock-adversarial-payload-ran\n"
_PROBE_SOURCE = Path(__file__).resolve().parents[1] / "fixtures" / ("work_bubblewrap_adversarial.c")


@dataclass(frozen=True)
class ScratchBubblewrap:
    executable: Path
    version: tuple[int, int, int]


@dataclass(frozen=True)
class ProbeResult:
    completed: subprocess.CompletedProcess[str]
    payload: Path
    marker: Path


@pytest.fixture(scope="session")
def scratch_bubblewrap() -> ScratchBubblewrap:
    """Use only the explicitly built T097 scratch binary, never a PATH fallback."""
    if not _SCRATCH_BWRAP.is_file():
        pytest.skip(
            "T097 scratch Bubblewrap binary is absent at "
            f"{_SCRATCH_BWRAP}; installed /usr/bin/bwrap is intentionally not used"
        )

    executable = _SCRATCH_BWRAP.resolve()
    scratch_root = _T097_ROOT.resolve()
    if not executable.is_relative_to(scratch_root):
        pytest.skip(
            "T097 Bubblewrap path resolves outside the scratch tree; refusing to run "
            "an installed or unrelated bwrap binary"
        )
    if executable == Path("/usr/bin/bwrap").resolve():
        pytest.skip("T097 Bubblewrap resolves to installed /usr/bin/bwrap; refusing fallback")
    if not os.access(executable, os.X_OK):
        pytest.skip(f"T097 scratch Bubblewrap is not executable: {executable}")

    try:
        version_probe = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env={"LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"could not probe T097 scratch Bubblewrap: {exc}")
    match = _BWRAP_VERSION.fullmatch(version_probe.stdout.strip())
    if version_probe.returncode != 0 or match is None:
        pytest.skip(
            "T097 scratch Bubblewrap did not report a parseable version >= 0.12.0: "
            f"stdout={version_probe.stdout!r} stderr={version_probe.stderr!r}"
        )
    version = tuple(int(part) for part in match.groups())
    if version < (0, 12, 0):
        pytest.skip(
            f"T097 scratch Bubblewrap is {version_probe.stdout.strip()}, below 0.12.0; "
            "installed /usr/bin/bwrap is never substituted"
        )

    # Detect unavailable user namespaces before trying to run any adversarial payload.
    namespace_probe = subprocess.run(
        [
            str(executable),
            "--unshare-all",
            "--die-with-parent",
            "--ro-bind",
            "/",
            "/",
            "--",
            "/bin/true",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={"LC_ALL": "C"},
    )
    if namespace_probe.returncode != 0:
        pytest.skip(
            "T097 scratch Bubblewrap cannot create the namespaces needed for this "
            f"reproduction: {namespace_probe.stderr.strip() or namespace_probe.returncode}"
        )

    return ScratchBubblewrap(executable=executable, version=version)


@pytest.fixture(scope="session")
def probe_binary(scratch_bubblewrap: ScratchBubblewrap, tmp_path_factory) -> Path:
    del scratch_bubblewrap  # Ensure the scratch-bwrap guard runs before compiling.
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("C compiler 'cc' is unavailable for the self-contained ELF payload")

    binary = tmp_path_factory.mktemp("work-bubblewrap-adversarial") / "landlock-probe"
    build = subprocess.run(
        [
            compiler,
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(_PROBE_SOURCE),
            "-o",
            str(binary),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, f"could not compile adversarial fixture:\n{build.stderr}"
    return binary


@pytest.fixture(scope="session")
def pt_interp(probe_binary: Path) -> tuple[str, Path]:
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
    assert headers.returncode == 0, f"readelf could not inspect fixture ELF: {headers.stderr}"
    match = re.search(r"Requesting program interpreter:\s*([^\]]+)\]", headers.stdout)
    if match is None:
        pytest.skip("C compiler produced no PT_INTERP segment; direct-loader case is unavailable")
    interp = match.group(1).strip()
    loader = Path(interp)
    if not loader.is_absolute() or not loader.is_file():
        pytest.skip(f"fixture PT_INTERP loader is unavailable: {interp}")
    return interp, loader.resolve()


@pytest.fixture(scope="session")
def executable_mountpoint() -> Path:
    """Pick a regular ELF file that Bubblewrap can safely overlay in its mount namespace."""
    installed_bwrap = Path("/usr/bin/bwrap").resolve()
    for directory in (Path("/usr/bin"), Path("/bin"), Path("/usr/local/bin")):
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.iterdir()):
            try:
                info = candidate.lstat()
                if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
                    continue
                if candidate.resolve() == installed_bwrap or candidate.name in {
                    "bwrap",
                    "bubblewrap",
                }:
                    continue
                with candidate.open("rb") as stream:
                    if stream.read(4) == b"\x7fELF":
                        return candidate
            except OSError:
                continue
    pytest.skip("no regular ELF mountpoint is available for the scratch-only namespace")


def _run_probe(
    scratch_bubblewrap: ScratchBubblewrap,
    probe_binary: Path,
    pt_interp: tuple[str, Path],
    executable_mountpoint: Path,
    tmp_path: Path,
    mode: str,
) -> ProbeResult:
    write_path = tmp_path / "write-path"
    write_path.mkdir()
    payload = write_path / "payload"
    shutil.copyfile(probe_binary, payload)
    payload.chmod(0o700)
    marker = write_path / f"{mode}.marker"

    completed = subprocess.run(
        [
            str(scratch_bubblewrap.executable),
            "--unshare-all",
            "--die-with-parent",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            str(write_path),
            "/tmp",
            "--proc",
            "/proc",
            "--chdir",
            "/tmp",
            "--ro-bind",
            str(probe_binary),
            str(executable_mountpoint),
            "--",
            str(executable_mountpoint),
            mode,
            "/tmp/payload",
            f"/tmp/{marker.name}",
            str(pt_interp[1]),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env={"LC_ALL": "C"},
    )
    if completed.returncode == 78 and (
        "probe-unavailable:" in completed.stderr or "landlock-unavailable:" in completed.stderr
    ):
        pytest.skip(
            f"kernel does not support the requested {mode} probe: {completed.stderr.strip()}"
        )
    return ProbeResult(completed=completed, payload=payload, marker=marker)


def _assert_payload_ran(result: ProbeResult) -> None:
    assert result.completed.returncode == 0, (
        f"expected payload execution; status={result.completed.returncode}; "
        f"stdout={result.completed.stdout!r}; stderr={result.completed.stderr!r}"
    )
    assert result.marker.read_text(encoding="utf-8") == _MARKER_CONTENT


def _assert_kernel_exec_denied(result: ProbeResult) -> None:
    assert result.completed.returncode == 77, (
        f"expected Landlock denial; status={result.completed.returncode}; "
        f"stdout={result.completed.stdout!r}; stderr={result.completed.stderr!r}"
    )
    assert "denied:" in result.completed.stderr
    assert not result.marker.exists()


def test_pt_interp_elf_under_write_path_is_denied_by_kernel_execve(
    scratch_bubblewrap,
    probe_binary,
    pt_interp,
    executable_mountpoint,
    tmp_path,
):
    interp, _loader = pt_interp
    assert (
        "Requesting program interpreter:"
        in subprocess.run(
            [shutil.which("readelf"), "-l", str(probe_binary)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    assert interp.startswith("/")

    result = _run_probe(
        scratch_bubblewrap,
        probe_binary,
        pt_interp,
        executable_mountpoint,
        tmp_path,
        "kernel-execve",
    )
    assert result.payload.parent.name == "write-path"
    _assert_kernel_exec_denied(result)


def test_pt_interp_bootstrap_is_denied_when_only_the_write_path_elf_is_allowed(
    scratch_bubblewrap,
    probe_binary,
    pt_interp,
    executable_mountpoint,
    tmp_path,
):
    # Landlock also checks the PT_INTERP executable on this kernel; permitting
    # only the ELF path does not let the kernel bootstrap its denied loader.
    result = _run_probe(
        scratch_bubblewrap,
        probe_binary,
        pt_interp,
        executable_mountpoint,
        tmp_path,
        "kernel-execve-payload-only",
    )
    _assert_kernel_exec_denied(result)
    assert "PT_INTERP bootstrap with loader denied" in result.completed.stderr


def test_direct_loader_is_denied_when_only_the_write_path_elf_is_allowed(
    scratch_bubblewrap,
    probe_binary,
    pt_interp,
    executable_mountpoint,
    tmp_path,
):
    result = _run_probe(
        scratch_bubblewrap,
        probe_binary,
        pt_interp,
        executable_mountpoint,
        tmp_path,
        "direct-loader-payload-only",
    )
    _assert_kernel_exec_denied(result)
    assert "direct loader not in allowlist" in result.completed.stderr


def test_allowed_direct_loader_runs_the_same_write_path_elf(
    scratch_bubblewrap,
    probe_binary,
    pt_interp,
    executable_mountpoint,
    tmp_path,
):
    result = _run_probe(
        scratch_bubblewrap,
        probe_binary,
        pt_interp,
        executable_mountpoint,
        tmp_path,
        "allowlisted-loader",
    )
    assert result.payload.parent.name == "write-path"
    _assert_payload_ran(result)


def test_proc_self_fd_to_write_path_elf_preserves_the_kernel_denial(
    scratch_bubblewrap,
    probe_binary,
    pt_interp,
    executable_mountpoint,
    tmp_path,
):
    result = _run_probe(
        scratch_bubblewrap,
        probe_binary,
        pt_interp,
        executable_mountpoint,
        tmp_path,
        "proc-self-fd-file",
    )
    _assert_kernel_exec_denied(result)


def test_memfd_execve_through_proc_self_fd_runs_the_payload(
    scratch_bubblewrap,
    probe_binary,
    pt_interp,
    executable_mountpoint,
    tmp_path,
):
    result = _run_probe(
        scratch_bubblewrap,
        probe_binary,
        pt_interp,
        executable_mountpoint,
        tmp_path,
        "memfd-execve",
    )
    _assert_payload_ran(result)


def test_memfd_execveat_empty_path_runs_the_payload(
    scratch_bubblewrap,
    probe_binary,
    pt_interp,
    executable_mountpoint,
    tmp_path,
):
    result = _run_probe(
        scratch_bubblewrap,
        probe_binary,
        pt_interp,
        executable_mountpoint,
        tmp_path,
        "memfd-execveat",
    )
    _assert_payload_ran(result)


def test_allowed_loader_runs_memfd_payload_through_proc_self_fd(
    scratch_bubblewrap,
    probe_binary,
    pt_interp,
    executable_mountpoint,
    tmp_path,
):
    result = _run_probe(
        scratch_bubblewrap,
        probe_binary,
        pt_interp,
        executable_mountpoint,
        tmp_path,
        "allowlisted-loader-memfd",
    )
    _assert_payload_ran(result)


def test_process_preflight_does_not_enable_legacy_terminal_effects(
    scratch_bubblewrap, tmp_path, monkeypatch
):
    from cli_agent_orchestrator.backends import bubblewrap_backend as bubblewrap_module
    from cli_agent_orchestrator.backends.base import (
        ProcessRestrictionContract,
        UnsupportedWorkEnforcement,
    )
    from cli_agent_orchestrator.backends.bubblewrap_backend import BubblewrapWorkBackend
    from cli_agent_orchestrator.backends.work_backend import WorkBackendView
    from cli_agent_orchestrator.models.work_contract import (
        ContractPermissions,
        ContractResources,
        ContractSnapshot,
        EffectiveWorkContractV2,
        ExecutableIdentity,
    )
    from cli_agent_orchestrator.services.work_admission import WorkAdmission

    monkeypatch.setattr(bubblewrap_module, "_require_work_broker_identity", lambda _name: None)
    monkeypatch.setattr(bubblewrap_module, "_query_abi_version", lambda: 9)
    monkeypatch.setattr(bubblewrap_module, "_probe_bwrap_version", lambda _: (0, 13, 0))

    class RecordingTmuxClient:
        def __init__(self):
            self.effects = []

        def __getattr__(self, name):
            def record(*args, **kwargs):
                self.effects.append((name, args, kwargs))

            return record

    class NoWorkRepositoryEffects:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected Work repository effect: {name}")

    root = tmp_path.resolve()
    digest = "d" * 64
    identity = ExecutableIdentity(
        command_token="/usr/bin/python3",
        content_reference=f"sha256:{digest}",
        sha256_digest=digest,
        elf_machine="x86_64",
        elf_class="ELF64",
        endianness="little",
        static=True,
    )
    contract = ProcessRestrictionContract(
        paths=(str(root),),
        commands=("/usr/bin/python3",),
        network=(),
        read_paths=(str(root),),
        checkout_root=str(root),
        executable_identities=(identity,),
    )
    client = RecordingTmuxClient()
    backend = BubblewrapWorkBackend(
        client=client,
        bwrap_executable=scratch_bubblewrap.executable,
    )
    work_effects = []
    view = WorkBackendView(
        backend,
        contract,
        lambda *_args: work_effects.append("authorized"),
        terminal_id="shell-id",
        expected_target=("s", "w"),
    )
    view._authorized_targets.add(("s", "w"))

    # The service-level preflight runs before admission touches its repository.
    admission = object.__new__(WorkAdmission)
    admission.backends = {"bubblewrap": backend}
    admission.repository = NoWorkRepositoryEffects()
    effective_contract = EffectiveWorkContractV2(
        id="t098-preflight-contract",
        operation_kind="launch",
        provider="scratch",
        backend="bubblewrap",
        permissions=ContractPermissions(paths=(str(root),), commands=("/usr/bin/python3",)),
        resources=ContractResources(checkout_root=str(root), write_paths=(str(root),), units=1),
        snapshot=ContractSnapshot(state="absent", absence_reason="legacy_parent_has_no_snapshot"),
        executable_identities=(identity,),
    )
    assert admission._preflight(effective_contract) is None

    for effect in (
        lambda: view.create_session("s", "w", "shell-id", str(root)),
        lambda: view.send_keys("s", "w", "payload"),
    ):
        with pytest.raises(UnsupportedWorkEnforcement, match="direct unguarded"):
            effect()

    from cli_agent_orchestrator.backends.base import WorkEffectAuthorizationRequired

    with pytest.raises(WorkEffectAuthorizationRequired, match="durable Work window reservation"):
        view.create_window("s", "w", "shell-id", str(root))

    assert client.effects == []
    assert work_effects == ["authorized", "authorized"]
