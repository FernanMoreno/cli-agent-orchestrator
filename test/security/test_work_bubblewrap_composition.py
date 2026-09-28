"""End-to-end composition proof for one staged static ELF and Bubblewrap."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import importlib
import os
import select
import shutil
import signal
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services.work_elf_identity import identify_static_executable

_T097_BWRAP = Path("/tmp/caos-exec/T097/bubblewrap-build/bwrap")
_TRUSTED_BWRAP_SHA256 = "2177c6adb34a871e8c7472ca33e7b1ea1161593a04a4d395c4923c298951cd23"
_WORKER_MARKER = b"CAO_STAGED_WORKER_RAN_AFTER_AUTHORIZATION\n"


def _minimal_static_worker() -> bytes:
    """Build a real x86_64 static ELF that writes one marker, then exits."""

    code = b"".join(
        (
            b"\xb8\x01\x00\x00\x00",  # mov eax, SYS_write
            b"\xbf\x01\x00\x00\x00",  # mov edi, stdout
            b"\x48\x8d\x35\x10\x00\x00\x00",  # lea rsi, [rip + marker]
            b"\xba" + struct.pack("<I", len(_WORKER_MARKER)),  # mov edx, length
            b"\x0f\x05",  # syscall
            b"\xb8\x3c\x00\x00\x00",  # mov eax, SYS_exit
            b"\x31\xff",  # xor edi, edi
            b"\x0f\x05",  # syscall
        )
    )
    assert len(code) == 33

    return _static_elf(code + _WORKER_MARKER)


def _static_elf(code: bytes) -> bytes:
    base_address = 0x400000
    entry_offset = 64 + 56
    ident = b"\x7fELF" + bytes((2, 1, 1, 0)) + bytes(8)
    header = ident + struct.pack(
        "<HHIQQQIHHHHHH",
        2,
        62,
        1,
        base_address + entry_offset,
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
    image_size = entry_offset + len(code)
    program_header = struct.pack(
        "<IIQQQQQQ",
        1,
        5,
        0,
        base_address,
        0,
        image_size,
        image_size,
        0x1000,
    )
    return header + program_header + code


def _silent_hanging_static_worker() -> bytes:
    """Close stdio, then remain alive long enough to force monitor cleanup."""

    code = b"".join(
        (
            b"\xb8\x03\x00\x00\x00\x31\xff\x0f\x05",  # close(0)
            b"\xb8\x03\x00\x00\x00\xbf\x01\x00\x00\x00\x0f\x05",  # close(1)
            b"\xb8\x03\x00\x00\x00\xbf\x02\x00\x00\x00\x0f\x05",  # close(2)
            b"\x68" + struct.pack("<I", 500_000_000),  # tv_nsec = 0.5s
            b"\x6a\x08",  # tv_sec = 8
            b"\x48\x89\xe7\x31\xf6",  # nanosleep(&timespec, NULL)
            b"\xb8\x23\x00\x00\x00\x0f\x05",
            b"\xb8\x3c\x00\x00\x00\x31\xff\x0f\x05",  # exit(0)
        )
    )
    return _static_elf(code)


def _detached_double_fork_worker() -> bytes:
    """Fork a new session, double-fork, then keep PID 1 and its grandchild alive."""

    code = bytearray()
    labels: dict[str, int] = {}
    branches: list[tuple[int, str]] = []

    def emit(instructions: bytes) -> None:
        code.extend(instructions)

    def branch(opcode: int, label: str) -> None:
        code.extend((opcode, 0))
        branches.append((len(code) - 1, label))

    emit(b"\xb8\x39\x00\x00\x00\x0f\x05\x85\xc0")  # fork; test eax
    branch(0x75, "init_loop")  # PID 1 remains alive
    emit(b"\xb8\x70\x00\x00\x00\x0f\x05")  # setsid
    emit(b"\xb8\x39\x00\x00\x00\x0f\x05\x85\xc0")  # second fork
    branch(0x75, "intermediate_exit")

    labels["worker_loop"] = len(code)
    emit(b"\xb8\x22\x00\x00\x00\x0f\x05")  # pause
    branch(0xEB, "worker_loop")

    labels["intermediate_exit"] = len(code)
    emit(b"\xb8\x3c\x00\x00\x00\x31\xff\x0f\x05")  # exit(0)

    labels["init_loop"] = len(code)
    emit(b"\xb8\x22\x00\x00\x00\x0f\x05")  # pause
    branch(0xEB, "init_loop")

    for displacement_at, label in branches:
        displacement = labels[label] - displacement_at - 1
        assert -128 <= displacement <= 127
        code[displacement_at] = displacement & 0xFF

    return _static_elf(bytes(code))


def _real_bubblewrap() -> Path:
    if not _T097_BWRAP.is_file():
        pytest.skip(f"accepted scratch Bubblewrap is absent at {_T097_BWRAP}")
    executable = _T097_BWRAP.resolve(strict=True)
    if not os.access(executable, os.X_OK):
        pytest.skip(f"accepted scratch Bubblewrap is not executable: {executable}")
    digest = hashlib.sha256()
    descriptor = os.open(executable, os.O_RDONLY | os.O_CLOEXEC)
    try:
        offset = 0
        while chunk := os.pread(descriptor, 1024 * 1024, offset):
            digest.update(chunk)
            offset += len(chunk)
    finally:
        os.close(descriptor)
    assert digest.hexdigest() == _TRUSTED_BWRAP_SHA256
    version = subprocess.run(
        [str(executable), "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
        env={"LC_ALL": "C"},
    )
    assert version.returncode == 0
    assert version.stdout.strip() == "bubblewrap 0.13.0"
    probe = subprocess.run(
        [
            str(executable),
            "--unshare-user",
            "--unshare-pid",
            "--unshare-net",
            "--unshare-ipc",
            "--unshare-uts",
            "--die-with-parent",
            "--new-session",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind",
            "/lib64",
            "/lib64",
            "--proc",
            "/proc",
            "--",
            "/usr/bin/true",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={"LC_ALL": "C"},
    )
    if probe.returncode != 0:
        pytest.skip(
            "host cannot create the required Bubblewrap namespaces: "
            f"{probe.stderr.strip() or probe.returncode}"
        )
    return executable


def test_wrong_bubblewrap_digest_rejects_before_version_spawn_or_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    bwrap = _real_bubblewrap()
    identity, executable = _worker_fixture()
    subprocess_calls = []
    callback_calls = []

    def unexpected_subprocess(*args, **kwargs):
        subprocess_calls.append((args, kwargs))
        raise AssertionError("wrong Bubblewrap digest reached subprocess execution")

    monkeypatch.setattr(api.subprocess, "run", unexpected_subprocess)
    monkeypatch.setattr(api.subprocess, "Popen", unexpected_subprocess)

    with pytest.raises(api.WorkBubblewrapSetupFailed, match="digest"):
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=lambda acknowledgement, _process_identity: callback_calls.append(
                acknowledgement
            )
            or True,
            bwrap_path=bwrap,
            bwrap_sha256_digest="0" * 64,
        )

    assert subprocess_calls == []
    assert callback_calls == []


def test_in_place_mutation_after_bubblewrap_copy_rejects_before_version_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    accepted = _real_bubblewrap()
    pinned_path = tmp_path / "bubblewrap"
    pinned_path.write_bytes(accepted.read_bytes())
    pinned_path.chmod(0o755)
    monkeypatch.setattr(api, "_DESIGNATED_BWRAP", pinned_path)
    original_copy = api._create_sealed_bwrap_copy
    version_calls = []

    def mutate_after_copy(descriptor: int, expected_digest: str) -> int:
        execution_descriptor = original_copy(descriptor, expected_digest)
        mutation_fd = os.open(pinned_path, os.O_RDWR | os.O_CLOEXEC)
        try:
            original_byte = os.pread(mutation_fd, 1, 0)
            replacement_byte = b"\x00" if original_byte != b"\x00" else b"\x01"
            assert os.pwrite(mutation_fd, replacement_byte, 0) == 1
        finally:
            os.close(mutation_fd)
        return execution_descriptor

    def unexpected_version_probe(*args, **kwargs):
        version_calls.append((args, kwargs))
        raise AssertionError("mutated Bubblewrap reached its version probe")

    monkeypatch.setattr(api, "_create_sealed_bwrap_copy", mutate_after_copy)
    monkeypatch.setattr(api.subprocess, "run", unexpected_version_probe)
    identity, executable = _worker_fixture()

    with pytest.raises(
        api.WorkBubblewrapSetupFailed,
        match="changed after its accepted identity was captured",
    ):
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=lambda _acknowledgement, _process_identity: True,
            bwrap_path=pinned_path,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        )

    assert version_calls == []


def test_source_mutation_after_final_revalidation_still_runs_sealed_bubblewrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    accepted = _real_bubblewrap()
    pinned_path = tmp_path / "bubblewrap"
    pinned_path.write_bytes(accepted.read_bytes())
    pinned_path.chmod(0o755)
    monkeypatch.setattr(api, "_DESIGNATED_BWRAP", pinned_path)
    original_revalidate = api._revalidate_pinned_bwrap
    validations = 0
    mutated = False

    def mutate_after_final_revalidation(pinned) -> None:
        nonlocal validations, mutated
        original_revalidate(pinned)
        validations += 1
        if validations == 3:
            mutation_fd = os.open(pinned_path, os.O_RDWR | os.O_CLOEXEC)
            try:
                assert os.pwrite(mutation_fd, b"\x00", 0) == 1
            finally:
                os.close(mutation_fd)
            mutated = True

    monkeypatch.setattr(api, "_revalidate_pinned_bwrap", mutate_after_final_revalidation)
    identity, executable = _worker_fixture()
    acknowledgements = []

    result = api.launch_staged_static_elf(
        identity,
        executable,
        persist_and_authorize=lambda acknowledgement, _process_identity: acknowledgements.append(
            acknowledgement
        )
        or True,
        bwrap_path=pinned_path,
        bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
    )

    assert validations == 3
    assert mutated is True
    assert pinned_path.read_bytes()[0:1] == b"\x00"
    assert len(acknowledgements) == 1
    assert result.returncode == 0
    assert result.stdout == _WORKER_MARKER


def test_bubblewrap_execution_descriptor_is_sealed_against_writes() -> None:
    api = _composition_api()
    bwrap = _real_bubblewrap()
    pinned = api._require_accepted_bwrap(bwrap, _TRUSTED_BWRAP_SHA256)
    source_descriptor = getattr(pinned, "source_descriptor", getattr(pinned, "descriptor", None))
    try:
        assert hasattr(pinned, "execution_descriptor")
        descriptor = pinned.execution_descriptor
        required_seals = (
            getattr(fcntl, "F_SEAL_WRITE", 0x0008)
            | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
            | getattr(fcntl, "F_SEAL_GROW", 0x0004)
            | getattr(fcntl, "F_SEAL_SEAL", 0x0001)
        )
        assert fcntl.fcntl(descriptor, getattr(fcntl, "F_GET_SEALS", 1034)) & required_seals == (
            required_seals
        )
        with pytest.raises(OSError) as raised:
            os.pwrite(descriptor, b"\x00", 0)
        assert raised.value.errno == errno.EPERM
    finally:
        if source_descriptor is not None:
            os.close(source_descriptor)
        if hasattr(pinned, "execution_descriptor"):
            os.close(pinned.execution_descriptor)


def test_system_bubblewrap_path_can_be_pinned_for_an_approved_host(monkeypatch):
    from types import SimpleNamespace

    api = _composition_api()
    executable = Path("/usr/bin/bwrap")
    if not executable.is_file():
        pytest.skip("canonical system Bubblewrap is unavailable")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    probes = []

    def report_accepted_version(arguments, **kwargs):
        probes.append((arguments, kwargs))
        return SimpleNamespace(returncode=0, stdout="bubblewrap 0.13.0\n", stderr="")

    monkeypatch.setattr(api.subprocess, "run", report_accepted_version)
    # This unit test isolates canonical-path pinning. The current installed
    # version/digest are not treated as T097/C08 host acceptance.
    pinned = api._require_accepted_bwrap(executable, digest)
    try:
        assert pinned.path == executable
        assert len(probes) == 1
        assert probes[0][1]["executable"] == pinned.proc_path
    finally:
        source_descriptor = getattr(
            pinned, "source_descriptor", getattr(pinned, "descriptor", None)
        )
        if source_descriptor is not None:
            os.close(source_descriptor)
        os.close(pinned.execution_descriptor)


def test_memfd_creation_failure_rejects_before_version_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    bwrap = _real_bubblewrap()
    identity, executable = _worker_fixture()
    subprocess_calls = []
    memfd_calls = 0
    actual_memfd_create = os.memfd_create

    def unavailable_memfd(*args, **kwargs):
        nonlocal memfd_calls
        memfd_calls += 1
        if memfd_calls == 2:
            raise OSError(errno.ENOSYS, "test-only missing memfd support")
        return actual_memfd_create(*args, **kwargs)

    def unexpected_version_probe(*args, **kwargs):
        subprocess_calls.append((args, kwargs))
        raise AssertionError("missing memfd support reached version probing")

    monkeypatch.setattr(api.os, "memfd_create", unavailable_memfd)
    monkeypatch.setattr(api.subprocess, "run", unexpected_version_probe)

    with pytest.raises(api.WorkBubblewrapSetupFailed, match="memfd"):
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=lambda _acknowledgement, _process_identity: True,
            bwrap_path=bwrap,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        )

    assert subprocess_calls == []
    assert memfd_calls == 2


def test_bubblewrap_seal_failure_rejects_before_version_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    bwrap = _real_bubblewrap()
    identity, executable = _worker_fixture()
    subprocess_calls = []
    actual_fcntl = fcntl.fcntl
    seal_calls = 0
    add_seals_command = getattr(fcntl, "F_ADD_SEALS", 1033)

    def reject_sealing(descriptor: int, command: int, *args):
        nonlocal seal_calls
        if command == add_seals_command:
            seal_calls += 1
            if seal_calls == 2:
                raise OSError(errno.EPERM, "test-only seal failure")
        return actual_fcntl(descriptor, command, *args)

    def unexpected_version_probe(*args, **kwargs):
        subprocess_calls.append((args, kwargs))
        raise AssertionError("failed Bubblewrap sealing reached version probing")

    monkeypatch.setattr(fcntl, "fcntl", reject_sealing)
    monkeypatch.setattr(api.subprocess, "run", unexpected_version_probe)

    with pytest.raises(api.WorkBubblewrapSetupFailed, match="seal"):
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=lambda _acknowledgement, _process_identity: True,
            bwrap_path=bwrap,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        )

    assert subprocess_calls == []
    assert seal_calls == 2


@pytest.mark.parametrize("digest", ["0" * 63, "A" * 64, "g" * 64, None])
def test_malformed_bubblewrap_digest_rejects_before_executable_probe(
    monkeypatch: pytest.MonkeyPatch,
    digest: str | None,
) -> None:
    api = _composition_api()
    identity, executable = _worker_fixture()
    subprocess_calls = []
    callback_calls = []

    def unexpected_subprocess(*args, **kwargs):
        subprocess_calls.append((args, kwargs))
        raise AssertionError("malformed Bubblewrap digest reached subprocess execution")

    monkeypatch.setattr(api.subprocess, "run", unexpected_subprocess)
    monkeypatch.setattr(api.subprocess, "Popen", unexpected_subprocess)

    with pytest.raises(api.WorkBubblewrapSetupFailed, match="digest"):
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=lambda acknowledgement, _process_identity: callback_calls.append(
                acknowledgement
            )
            or True,
            bwrap_path=_T097_BWRAP,
            bwrap_sha256_digest=digest,
        )

    assert subprocess_calls == []
    assert callback_calls == []


def test_missing_bubblewrap_digest_rejects_before_executable_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    identity, executable = _worker_fixture()
    subprocess_calls = []

    def unexpected_subprocess(*args, **kwargs):
        subprocess_calls.append((args, kwargs))
        raise AssertionError("missing Bubblewrap digest reached subprocess execution")

    monkeypatch.setattr(api.subprocess, "run", unexpected_subprocess)
    monkeypatch.setattr(api.subprocess, "Popen", unexpected_subprocess)

    with pytest.raises(TypeError, match="bwrap_sha256_digest"):
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=lambda _acknowledgement, _process_identity: True,
            bwrap_path=_T097_BWRAP,
        )

    assert subprocess_calls == []


def _composition_api():
    try:
        return importlib.import_module(
            "cli_agent_orchestrator.services.work_bubblewrap_composition"
        )
    except ModuleNotFoundError as exc:
        if exc.name == "cli_agent_orchestrator.services.work_bubblewrap_composition":
            pytest.fail("staged static ELF composition harness is missing")
        raise


def _worker_fixture():
    executable = _minimal_static_worker()
    identity = identify_static_executable("/worker", executable)
    return identity, executable


def test_setup_timeout_is_bounded_separately_from_worker_runtime(monkeypatch: pytest.MonkeyPatch):
    api = _composition_api()
    identity, executable = _worker_fixture()
    staged = object()
    observed = {}
    sentinel = object()

    monkeypatch.setattr(api, "stage_static_executable", lambda *_args: staged)

    def capture(_staged, _identity, **kwargs):
        observed.update(kwargs)
        return sentinel

    monkeypatch.setattr(api, "_launch_owned_stage", capture)
    result = api.launch_staged_static_elf(
        identity,
        executable,
        persist_and_authorize=lambda *_args: True,
        bwrap_path="/usr/bin/bwrap",
        bwrap_sha256_digest="a" * 64,
        timeout_seconds=3.0,
        setup_timeout_seconds=23.0,
    )

    assert result is sentinel
    assert observed["timeout_seconds"] == 3.0
    assert observed["setup_timeout_seconds"] == 23.0


def _runtime_snapshot_fixture(api, tmp_path: Path):
    from cli_agent_orchestrator.services.work_bubblewrap_runtime_snapshot import (
        prepare_runtime_snapshot,
    )

    runtime, source, deps = (tmp_path / name for name in ("runtime", "source", "deps"))
    (runtime / "bin").mkdir(parents=True)
    source.mkdir()
    deps.mkdir()
    interpreter = runtime / "bin" / Path(api.sys.executable).resolve().name
    interpreter.write_bytes(b"python fixture")
    interpreter.chmod(0o755)
    (source / "module.py").write_text("snapshot source")
    (deps / "dependency.py").write_text("snapshot dependency")
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    return parent, prepare_runtime_snapshot(runtime, source, deps, parent=parent)


def _open_fd_identities() -> dict[int, tuple[int, int, int, int]]:
    descriptors = {}
    for entry in os.listdir("/proc/self/fd"):
        descriptor = int(entry)
        try:
            item = os.fstat(descriptor)
        except OSError:
            continue  # The procfs directory descriptor may have closed after readdir.
        descriptors[descriptor] = (item.st_dev, item.st_ino, item.st_mode, item.st_rdev)
    return descriptors


def _assert_no_new_fds(before: dict[int, tuple[int, int, int, int]]) -> None:
    after = _open_fd_identities()
    assert {
        descriptor: identity
        for descriptor, identity in after.items()
        if before.get(descriptor) != identity
    } == {}


def _assert_monitor_gone(pid: int) -> None:
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def _pidfd_readable(pidfd: int) -> bool:
    poller = select.poll()
    poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
    return bool(poller.poll(0))


def _pidfd_target(pidfd: int) -> int | None:
    info = Path(f"/proc/self/fdinfo/{pidfd}").read_text(encoding="utf-8")
    line = next((line for line in info.splitlines() if line.startswith("Pid:")), None)
    if line is None:
        return None
    return int(line.split(":", 1)[1].strip())


def _proc_stat(pid: int) -> tuple[str, int, int, int, int]:
    stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    close = stat_text.rfind(")")
    fields = stat_text[close + 2 :].split()
    return fields[0], int(fields[1]), int(fields[2]), int(fields[3]), int(fields[19])


def _proc_children(pid: int) -> list[int]:
    try:
        text = Path(f"/proc/{pid}/task/{pid}/children").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return []
    return [int(child) for child in text.split()]


def _process_nspids(pid: int) -> tuple[int, ...]:
    status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    line = next(line for line in status.splitlines() if line.startswith("NSpid:"))
    return tuple(int(value) for value in line.split(":", 1)[1].split())


def _find_detached_grandchild(monitor_pid: int) -> tuple[int, int]:
    pending = [monitor_pid]
    visited: set[int] = set()
    init_pid = None
    init_session = None
    processes: list[tuple[int, tuple[int, ...], tuple[str, int, int, int, int]]] = []
    while pending:
        parent = pending.pop()
        if parent in visited:
            continue
        visited.add(parent)
        for child in _proc_children(parent):
            try:
                proc_stat = _proc_stat(child)
                nspids = _process_nspids(child)
            except (FileNotFoundError, ProcessLookupError, StopIteration, ValueError):
                continue
            if proc_stat[0] not in {"Z", "X", "x"}:
                pending.append(child)
            processes.append((child, nspids, proc_stat))
            if nspids[-1] == 1:
                init_pid = child
                init_session = proc_stat[3]

    if init_pid is None or init_session is None:
        raise LookupError("Bubblewrap PID-namespace init was not found below its monitor")
    for pid, nspids, proc_stat in processes:
        if (
            pid != init_pid
            and nspids[-1] >= 3
            and proc_stat[0] not in {"Z", "X", "x"}
            and proc_stat[3] != init_session
        ):
            return pid, init_pid
    raise LookupError("detached double-fork worker was not found below namespace PID 1")


def _write_version_spoofer(path: Path) -> Path:
    invoked = Path(f"{path}.invoked")
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        "  echo 'bubblewrap 0.13.0'\n"
        "  exit 0\n"
        "fi\n"
        ': > "$0.invoked"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return invoked


def test_real_static_worker_runs_only_after_ack_persistence_and_fresh_authorization() -> None:
    api = _composition_api()
    bwrap = _real_bubblewrap()
    identity, executable = _worker_fixture()
    assert Path("/etc/passwd").is_file()
    before_fds = _open_fd_identities()
    events: list[str] = []
    seen_acknowledgements = []

    def persist_then_authorize(acknowledgement, _process_identity):
        seen_acknowledgements.append(acknowledgement)
        events.append("persist")
        assert acknowledgement.destination == "/exec/worker"
        assert acknowledgement.sha256_digest == identity.sha256_digest
        assert acknowledgement.mode == 0o500
        assert acknowledgement.size == len(executable)
        assert acknowledgement.open_fds == (0, 1, 2)
        assert acknowledgement.landlock_abi >= 1
        assert acknowledgement.seccomp_mode == 2
        assert acknowledgement.unlisted_path_denied is True
        assert acknowledgement.unmounted_host_path_absent is True
        assert acknowledgement.memfd_create_denied_errno == errno.EPERM
        assert acknowledgement.connect_denied_errno == errno.EPERM
        events.append("fresh-authorization")
        return True

    result = api.launch_staged_static_elf(
        identity,
        executable,
        persist_and_authorize=persist_then_authorize,
        bwrap_path=bwrap,
        bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
    )

    assert events == ["persist", "fresh-authorization"]
    assert len(seen_acknowledgements) == 1
    assert result.returncode == 0
    assert result.stdout == _WORKER_MARKER
    _assert_no_new_fds(before_fds)


def test_late_socket_in_live_usr_bind_is_denied_by_composed_seccomp_after_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A socket created after ACK in a live ro-bind cannot cross the Work filter."""
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("static C compiler unavailable")
    source = tmp_path / "late-connect.c"
    worker_path = tmp_path / "late-connect-worker"
    source.write_text(
        r"""#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
int main(void) {
    struct sockaddr_un address = {0};
    address.sun_family = AF_UNIX;
    strcpy(address.sun_path, "/usr/t097-probe/late.sock");
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return 20;
    if (connect(fd, (struct sockaddr *)&address, sizeof(address)) == 0) {
        puts("CONNECTED");
        close(fd);
        return 21;
    }
    printf("DENIED:%d\n", errno);
    close(fd);
    return errno == EPERM ? 0 : 22;
}
""",
        encoding="utf-8",
    )
    build = subprocess.run(
        [
            compiler,
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(source),
            "-o",
            str(worker_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if build.returncode != 0:
        pytest.skip(f"compiler cannot produce the static socket worker: {build.stderr.strip()}")

    api = _composition_api()
    bwrap = _real_bubblewrap()
    executable = worker_path.read_bytes()
    identity = identify_static_executable("/bin/alpha", executable)
    live_usr = tmp_path / "live-usr"
    (live_usr / "bin").mkdir(parents=True)
    (live_usr / "t097-probe").mkdir()
    true_probe = live_usr / "bin" / "true"
    true_probe.write_bytes(b"read-only path probe")
    true_probe.chmod(0o500)
    late_socket_path = live_usr / "t097-probe" / "late.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.settimeout(0.1)

    original_build_argv = api._build_bwrap_argv

    def use_test_live_usr(*args, **kwargs):
        argv = original_build_argv(*args, **kwargs)
        mount_index = next(
            index
            for index in range(len(argv) - 2)
            if argv[index : index + 3] == ["--ro-bind", "/usr", "/usr"]
        )
        argv[mount_index + 1] = str(live_usr)
        return argv

    monkeypatch.setattr(api, "_build_bwrap_argv", use_test_live_usr)

    def persist_then_create_socket(acknowledgement, _process_identity):
        assert acknowledgement.connect_denied_errno == errno.EPERM
        listener.bind(str(late_socket_path))
        listener.listen()
        return True

    try:
        result = api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=persist_then_create_socket,
            bwrap_path=bwrap,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        )
        assert result.returncode == 0
        assert result.stdout == b"DENIED:1\n"
        with pytest.raises(socket.timeout):
            listener.accept()
    finally:
        listener.close()
        try:
            late_socket_path.unlink()
        except FileNotFoundError:
            pass


def test_source_mutation_after_ack_cannot_change_mounted_runtime_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = _composition_api()
    bwrap = _real_bubblewrap()
    identity, executable = _worker_fixture()
    original_prepare = api._prepare_runtime_snapshot
    prepared = {}

    def capture_snapshot():
        result = original_prepare()
        prepared["snapshot"] = result[1]
        return result

    monkeypatch.setattr(api, "_prepare_runtime_snapshot", capture_snapshot)
    package_root = Path(api.__file__).resolve().parents[1]
    source = package_root / "agent_store" / "workflow_scout.md"
    snapshot_relative = source.relative_to(package_root)
    original_source = source.read_bytes()

    def mutate_after_ack(_acknowledgement, _process_identity):
        snapshot = prepared["snapshot"]
        snapshot_copy = snapshot.package_path / snapshot_relative
        try:
            source.write_bytes(original_source + b"\nmutation after ACK\n")
            assert snapshot_copy.read_bytes() == original_source
        finally:
            source.write_bytes(original_source)
        return True

    result = api.launch_staged_static_elf(
        identity,
        executable,
        persist_and_authorize=mutate_after_ack,
        bwrap_path=bwrap,
        bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
    )
    assert result.stdout == _WORKER_MARKER
    assert source.read_bytes() == original_source
    assert not prepared["snapshot"].path.exists()


def test_authorization_denial_never_releases_worker_and_reaps_bubblewrap() -> None:
    api = _composition_api()
    bwrap = _real_bubblewrap()
    identity, executable = _worker_fixture()
    before_fds = _open_fd_identities()
    observed = []

    def deny(_acknowledgement, _process_identity):
        observed.append(_acknowledgement)
        return False

    with pytest.raises(api.WorkBubblewrapAuthorizationDenied) as raised:
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=deny,
            bwrap_path=bwrap,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        )

    assert len(observed) == 1
    assert raised.value.cleanup_confirmed is True
    assert _WORKER_MARKER not in raised.value.stdout
    _assert_monitor_gone(raised.value.monitor_pid)
    _assert_no_new_fds(before_fds)


def test_persistence_exception_never_releases_worker_and_reaps_bubblewrap() -> None:
    api = _composition_api()
    bwrap = _real_bubblewrap()
    identity, executable = _worker_fixture()
    before_fds = _open_fd_identities()
    observed = []

    def persist(_acknowledgement, _process_identity):
        observed.append(True)
        raise OSError("test-only local persistence failure")

    with pytest.raises(api.WorkBubblewrapParentGateFailed) as raised:
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=persist,
            bwrap_path=bwrap,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        )

    assert observed == [True]
    assert isinstance(raised.value.__cause__, OSError)
    assert raised.value.cleanup_confirmed is True
    assert _WORKER_MARKER not in raised.value.stdout
    _assert_monitor_gone(raised.value.monitor_pid)
    _assert_no_new_fds(before_fds)


def test_version_spoofing_wrapper_is_rejected_before_launch_or_callback(
    tmp_path: Path,
) -> None:
    api = _composition_api()
    bwrap = _real_bubblewrap()
    identity, executable = _worker_fixture()
    wrapper = tmp_path / "bwrap-version-spoofer"
    invoked = _write_version_spoofer(wrapper)
    calls = []

    with pytest.raises(api.WorkBubblewrapSetupFailed) as raised:
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=lambda acknowledgement, _process_identity: calls.append(
                acknowledgement
            )
            or True,
            bwrap_path=wrapper,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        )

    assert calls == []
    assert not invoked.exists()
    assert raised.value.monitor_pid is None
    assert raised.value.cleanup_confirmed is True
    assert _WORKER_MARKER not in raised.value.stdout


def test_non_elf_designated_bubblewrap_is_rejected_before_launch_or_callback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    wrapper = tmp_path / "designated-bwrap"
    invoked = _write_version_spoofer(wrapper)
    monkeypatch.setattr(api, "_DESIGNATED_BWRAP", wrapper)
    identity, executable = _worker_fixture()
    calls = []

    with pytest.raises(api.WorkBubblewrapSetupFailed) as raised:
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=lambda acknowledgement, _process_identity: calls.append(
                acknowledgement
            )
            or True,
            bwrap_path=wrapper,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        )

    assert calls == []
    assert not invoked.exists()
    assert raised.value.monitor_pid is None
    assert raised.value.cleanup_confirmed is True


def test_changed_designated_identity_is_rejected_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    accepted = _real_bubblewrap()
    pinned_path = tmp_path / "bubblewrap"
    pinned_path.write_bytes(accepted.read_bytes())
    pinned_path.chmod(0o755)
    monkeypatch.setattr(api, "_DESIGNATED_BWRAP", pinned_path)
    identity, executable = _worker_fixture()
    original_builder = api._build_bwrap_argv
    invoked = _write_version_spoofer(tmp_path / "replacement")

    def replace_during_argv_build(*args, **kwargs):
        command = original_builder(*args, **kwargs)
        os.replace(invoked.parent / "replacement", pinned_path)
        pinned_path.chmod(0o700)
        return command

    monkeypatch.setattr(api, "_build_bwrap_argv", replace_during_argv_build)
    calls = []

    with pytest.raises(api.WorkBubblewrapSetupFailed) as raised:
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=lambda acknowledgement, _process_identity: calls.append(
                acknowledgement
            )
            or True,
            bwrap_path=pinned_path,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        )

    assert calls == []
    assert raised.value.monitor_pid is None
    assert raised.value.cleanup_confirmed is True
    assert not Path(f"{pinned_path}.invoked").exists()


def test_pinned_descriptor_close_failure_after_spawn_reaps_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    bwrap = _real_bubblewrap()
    identity, executable = _worker_fixture()
    launched: list[subprocess.Popen[bytes]] = []
    original_popen = api.subprocess.Popen
    original_close = api._close_pinned_bwrap

    def capture_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, (list, tuple)) and "--version" not in command:
            launched.append(process)
        return process

    def close_then_fail(pinned) -> None:
        original_close(pinned)
        raise api.WorkBubblewrapCleanupUncertain(
            "injected pinned Bubblewrap descriptor close failure",
            cleanup_confirmed=False,
        )

    monkeypatch.setattr(api.subprocess, "Popen", capture_popen)
    monkeypatch.setattr(api, "_close_pinned_bwrap", close_then_fail)
    callback_calls = []

    try:
        with pytest.raises(api.WorkBubblewrapCleanupUncertain) as raised:
            api.launch_staged_static_elf(
                identity,
                executable,
                persist_and_authorize=lambda acknowledgement, _process_identity: callback_calls.append(
                    acknowledgement
                )
                or True,
                bwrap_path=bwrap,
                bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
            )

        assert len(launched) == 1
        process = launched[0]
        assert process.poll() is not None
        assert raised.value.monitor_pid == process.pid
        assert raised.value.cleanup_confirmed is False
        assert callback_calls == []
        _assert_monitor_gone(process.pid)
    finally:
        for process in launched:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)


def test_spawn_uses_pinned_bubblewrap_fd_if_path_changes_after_revalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    accepted = _real_bubblewrap()
    pinned_path = tmp_path / "bubblewrap"
    pinned_path.write_bytes(accepted.read_bytes())
    pinned_path.chmod(0o755)
    monkeypatch.setattr(api, "_DESIGNATED_BWRAP", pinned_path)
    identity, executable = _worker_fixture()
    original_builder = api._build_bwrap_argv
    original_revalidate = api._revalidate_pinned_bwrap
    replacement = tmp_path / "replacement"
    _write_version_spoofer(replacement)
    validations = 0

    def swap_path_after_final_revalidation(pinned):
        nonlocal validations
        original_revalidate(pinned)
        validations += 1
        if validations == 3:
            os.replace(replacement, pinned_path)

    def invalid_arguments(*args, **kwargs):
        command = original_builder(*args, **kwargs)
        return [command[0], "--invalid-t097-test-option", *command[1:]]

    monkeypatch.setattr(api, "_revalidate_pinned_bwrap", swap_path_after_final_revalidation)
    monkeypatch.setattr(api, "_build_bwrap_argv", invalid_arguments)
    calls = []

    with pytest.raises(api.WorkBubblewrapCleanupUncertain) as raised:
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=lambda acknowledgement, _process_identity: calls.append(
                acknowledgement
            )
            or True,
            bwrap_path=pinned_path,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        )

    assert validations == 3
    assert calls == []
    assert not Path(f"{pinned_path}.invoked").exists()
    assert raised.value.monitor_pid is not None
    assert raised.value.cleanup_confirmed is False
    _assert_monitor_gone(raised.value.monitor_pid)


def test_bubblewrap_setup_failure_before_ack_never_calls_parent_gate_or_executes_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    bwrap = _real_bubblewrap()
    identity, executable = _worker_fixture()
    original_builder = api._build_bwrap_argv

    def invalid_arguments(*args, **kwargs):
        command = original_builder(*args, **kwargs)
        return [command[0], "--invalid-t097-test-option", *command[1:]]

    monkeypatch.setattr(api, "_build_bwrap_argv", invalid_arguments)
    calls = []

    with pytest.raises(api.WorkBubblewrapCleanupUncertain) as raised:
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=lambda acknowledgement, _process_identity: calls.append(
                acknowledgement
            )
            or True,
            bwrap_path=bwrap,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        )

    assert calls == []
    assert raised.value.cleanup_confirmed is False
    assert _WORKER_MARKER not in raised.value.stdout
    _assert_monitor_gone(raised.value.monitor_pid)


def test_pre_ack_timeout_reports_confirmed_namespace_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    bwrap = _real_bubblewrap()
    identity, executable = _worker_fixture()
    monkeypatch.setattr(api, "_BOOTSTRAP", "import time\ntime.sleep(3)\n" + api._BOOTSTRAP)
    calls = []

    with pytest.raises(api.WorkBubblewrapSetupFailed) as raised:
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=lambda acknowledgement, _process_identity: calls.append(
                acknowledgement
            )
            or True,
            bwrap_path=bwrap,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
            timeout_seconds=1.0,
            setup_timeout_seconds=1.0,
        )

    assert calls == []
    assert raised.value.cleanup_confirmed is True
    _assert_monitor_gone(raised.value.monitor_pid)


def test_worker_timeout_reports_execution_uncertainty_after_confirmed_cleanup() -> None:
    api = _composition_api()
    bwrap = _real_bubblewrap()
    executable = _silent_hanging_static_worker()
    identity = identify_static_executable("/worker", executable)
    before_fds = _open_fd_identities()

    with pytest.raises(api.WorkBubblewrapExecutionUncertain) as raised:
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=lambda _acknowledgement, _process_identity: True,
            bwrap_path=bwrap,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
            timeout_seconds=5.0,
        )

    assert raised.value.cleanup_confirmed is True
    assert raised.value.stdout == b""
    _assert_monitor_gone(raised.value.monitor_pid)
    _assert_no_new_fds(before_fds)


def test_bubblewrap_argv_makes_command_pid1_and_requests_bounded_info(tmp_path: Path) -> None:
    api = _composition_api()
    identity, executable = _worker_fixture()
    staged = api.stage_static_executable(identity, executable)
    snapshot_parent, snapshot = _runtime_snapshot_fixture(api, tmp_path)
    try:
        argv = api._build_bwrap_argv(
            _T097_BWRAP,
            staged,
            identity,
            91,
            92,
            snapshot=snapshot,
        )
    finally:
        staged.close()
        snapshot.close(cleanup_confirmed=True)
        os.rmdir(snapshot_parent)

    assert "--as-pid-1" in argv
    info_index = argv.index("--info-fd")
    assert info_index + 1 < len(argv)
    assert argv[info_index + 1].isdecimal()


def test_setup_ack_requires_explicit_connect_denial_evidence() -> None:
    api = _composition_api()
    identity, executable = _worker_fixture()
    payload = {
        "destination": "/exec/worker",
        "sha256_digest": identity.sha256_digest,
        "mode": 0o500,
        "size": len(executable),
        "destination_device": 1,
        "destination_inode": 2,
        "open_fds": [0, 1, 2],
        "landlock_abi": 7,
        "seccomp_mode": 2,
        "unlisted_path_denied": True,
        "unmounted_host_path_absent": True,
        "memfd_create_denied_errno": errno.EPERM,
        "connect_denied_errno": errno.EPERM,
        "proxy_socket_device": None,
        "proxy_socket_inode": None,
    }
    acknowledgement = api._parse_ack(payload, identity, len(executable), 123)
    assert acknowledgement.connect_denied_errno == errno.EPERM


def test_bubblewrap_argv_mounts_snapshot_paths_not_mutable_host_trees(tmp_path: Path) -> None:
    api = _composition_api()
    snapshot_parent, snapshot = _runtime_snapshot_fixture(api, tmp_path)
    runtime, source, deps = (tmp_path / name for name in ("runtime", "source", "deps"))
    identity, executable = _worker_fixture()
    staged = api.stage_static_executable(identity, executable)
    late_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        argv = api._build_bwrap_argv(
            _T097_BWRAP,
            staged,
            identity,
            91,
            92,
            snapshot=snapshot,
        )
        assert str(snapshot.runtime_path) in argv
        assert str(snapshot.package_path) in argv
        assert str(snapshot.deps_path) in argv
        assert str(runtime) not in argv
        assert str(source) not in argv
        assert str(deps) not in argv
        late_socket.bind(str(runtime / "late.sock"))
        (source / "module.py").write_text("changed after snapshot")
        assert not (snapshot.runtime_path / "late.sock").exists()
        assert (snapshot.package_path / "module.py").read_text() == "snapshot source"
        assert (source / "module.py").read_text() == "changed after snapshot"
    finally:
        late_socket.close()
        staged.close()
        snapshot.close(cleanup_confirmed=True)


def test_malformed_bubblewrap_info_is_rejected_before_parent_gate_or_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    assert hasattr(api, "_parse_bwrap_info"), "bounded bwrap info parser is required"
    with pytest.raises(api.WorkBubblewrapSetupFailed):
        api._parse_bwrap_info(b'{"child-pid":')

    signal_targets: list[int | None] = []

    def unexpected_signal(pidfd: int, _sig: int, _info=None) -> None:
        signal_targets.append(_pidfd_target(pidfd))

    monkeypatch.setattr(
        api,
        "signal",
        SimpleNamespace(pidfd_send_signal=unexpected_signal, SIGKILL=signal.SIGKILL),
        raising=False,
    )
    assert signal_targets == []


def test_malformed_bubblewrap_info_never_signals_its_unvalidated_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    bwrap = _real_bubblewrap()
    identity, executable = _worker_fixture()
    original_read_info = api._read_bwrap_info
    original_open_monitor = api._open_monitor_pidfd
    monitor_processes = []

    def malformed_info(fd: int, timeout_seconds: float) -> bytes:
        original_read_info(fd, timeout_seconds)
        return b'{"child-pid":123,"pid-namespace":'

    def capture_monitor(process):
        monitor_processes.append(process)
        return original_open_monitor(process)

    signal_targets: list[int] = []

    def spy_pidfd_send_signal(pidfd: int, sig: int, info=None) -> None:
        target = _pidfd_target(pidfd)
        signal_targets.append(target)
        if target != 123:
            signal.pidfd_send_signal(pidfd, sig, info)

    monkeypatch.setattr(api, "_read_bwrap_info", malformed_info)
    monkeypatch.setattr(api, "_open_monitor_pidfd", capture_monitor)
    monkeypatch.setattr(
        api,
        "signal",
        SimpleNamespace(pidfd_send_signal=spy_pidfd_send_signal, SIGKILL=signal.SIGKILL),
        raising=False,
    )
    callback_calls: list[object] = []

    with pytest.raises(api.WorkBubblewrapCleanupUncertain) as raised:
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=lambda acknowledgement, _process_identity: callback_calls.append(
                acknowledgement
            )
            or True,
            bwrap_path=bwrap,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
            timeout_seconds=5.0,
        )

    assert callback_calls == []
    assert monitor_processes and all(
        process.returncode is not None for process in monitor_processes
    )
    assert 123 not in signal_targets
    assert signal_targets and all(
        target in {process.pid for process in monitor_processes} | {-1} for target in signal_targets
    )
    assert raised.value.cleanup_confirmed is False


def test_recycled_namespace_init_identity_is_never_signaled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    bwrap = _real_bubblewrap()
    identity, executable = _worker_fixture()
    original_reader = api._read_namespace_init_identity
    original_open_monitor = api._open_monitor_pidfd
    reads = 0
    reported_pid: int | None = None
    monitor_pidfds: list[int] = []
    monitor_processes = []

    def capture_monitor_pidfd(process):
        monitor_processes.append(process)
        pidfd = original_open_monitor(process)
        if pidfd is not None:
            monitor_pidfds.append(os.dup(pidfd))
        return pidfd

    def mismatched_second_snapshot(pid: int, monitor_pid: int):
        nonlocal reads, reported_pid
        reported_pid = pid
        snapshot = original_reader(pid, monitor_pid)
        reads += 1
        if reads == 2:
            return SimpleNamespace(
                pid=snapshot.pid,
                parent_pid=snapshot.parent_pid,
                start_time=snapshot.start_time + 1,
                namespace_inode=snapshot.namespace_inode,
                namespace_pids=snapshot.namespace_pids,
            )
        return snapshot

    signal_targets: list[int | None] = []

    def unexpected_signal(pidfd: int, _sig: int, _info=None) -> None:
        target = _pidfd_target(pidfd)
        signal_targets.append(target)
        if target != reported_pid:
            signal.pidfd_send_signal(pidfd, _sig, _info)

    monkeypatch.setattr(api, "_read_namespace_init_identity", mismatched_second_snapshot)
    monkeypatch.setattr(api, "_open_monitor_pidfd", capture_monitor_pidfd)
    monkeypatch.setattr(
        api,
        "signal",
        SimpleNamespace(pidfd_send_signal=unexpected_signal, SIGKILL=signal.SIGKILL),
        raising=False,
    )
    callback_calls: list[object] = []

    with pytest.raises(api.WorkBubblewrapCleanupUncertain) as raised:
        api.launch_staged_static_elf(
            identity,
            executable,
            persist_and_authorize=lambda acknowledgement, _process_identity: callback_calls.append(
                acknowledgement
            )
            or True,
            bwrap_path=bwrap,
            bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
            timeout_seconds=5.0,
        )

    assert reads == 2
    assert callback_calls == []
    assert reported_pid is not None
    assert all(target != reported_pid for target in signal_targets)
    assert raised.value.cleanup_confirmed is False
    assert monitor_processes and all(
        process.returncode is not None for process in monitor_processes
    )
    assert signal_targets and all(
        target in {process.pid for process in monitor_processes} | {-1} for target in signal_targets
    )
    for pidfd in monitor_pidfds:
        os.close(pidfd)


def test_timeout_kills_pid1_by_pidfd_and_kernel_removes_detached_descendant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _composition_api()
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        pytest.skip("host Python or kernel has no pidfd support")
    bwrap = _real_bubblewrap()
    executable = _detached_double_fork_worker()
    identity = identify_static_executable("/worker", executable)
    captured: dict[str, object] = {}
    observer_done = threading.Event()
    original_pidfd_send_signal = signal.pidfd_send_signal
    signal_targets: list[tuple[int | None, int]] = []

    def spy_pidfd_send_signal(pidfd: int, sig: int, info=None) -> None:
        target = _pidfd_target(pidfd)
        signal_targets.append((target, sig))
        original_pidfd_send_signal(pidfd, sig, info)

    monkeypatch.setattr(
        api,
        "signal",
        SimpleNamespace(
            pidfd_send_signal=spy_pidfd_send_signal,
            SIGKILL=signal.SIGKILL,
        ),
        raising=False,
    )

    def observe_descendant(monitor_pid: int) -> None:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                worker_pid, init_pid = _find_detached_grandchild(monitor_pid)
                init_pidfd = os.pidfd_open(init_pid)
                try:
                    worker_pidfd = os.pidfd_open(worker_pid)
                except BaseException:
                    os.close(init_pidfd)
                    raise
                captured.update(
                    worker_pid=worker_pid,
                    init_pid=init_pid,
                    init_pidfd=init_pidfd,
                    worker_pidfd=worker_pidfd,
                    worker_alive_before_cleanup=(
                        not _pidfd_readable(worker_pidfd) and Path(f"/proc/{worker_pid}").exists()
                    ),
                )
                return
            except (FileNotFoundError, LookupError, ProcessLookupError, PermissionError):
                time.sleep(0.02)
            except OSError:
                time.sleep(0.02)
        captured["observer_error"] = "detached worker was not observed before deadline"

    def persist_and_authorize(_acknowledgement, _process_identity):
        monitor_pid = _acknowledgement.monitor_pid

        def run_observer() -> None:
            try:
                observe_descendant(monitor_pid)
            finally:
                observer_done.set()

        threading.Thread(target=run_observer, daemon=True).start()
        return True

    try:
        with pytest.raises(api.WorkBubblewrapExecutionUncertain) as raised:
            api.launch_staged_static_elf(
                identity,
                executable,
                persist_and_authorize=persist_and_authorize,
                bwrap_path=bwrap,
                bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
                timeout_seconds=3.0,
            )
        assert observer_done.wait(1.0), "bounded pidfd observer did not finish"
        assert "observer_error" not in captured, captured.get("observer_error")
        assert captured["worker_alive_before_cleanup"] is True
        init_pid = int(captured["init_pid"])
        worker_pid = int(captured["worker_pid"])
        assert (init_pid, signal.SIGKILL) in signal_targets
        assert any(target == init_pid for target, _sig in signal_targets)
        assert _pidfd_readable(int(captured["init_pidfd"]))
        assert _pidfd_readable(int(captured["worker_pidfd"]))
        assert not Path(f"/proc/{worker_pid}").exists()
        assert raised.value.cleanup_confirmed is True
        _assert_monitor_gone(raised.value.monitor_pid)
    finally:
        observer_done.wait(1.0)
        init_pidfd = captured.get("init_pidfd")
        worker_pidfd = captured.get("worker_pidfd")
        for pidfd in (init_pidfd, worker_pidfd):
            if isinstance(pidfd, int):
                try:
                    if not _pidfd_readable(pidfd):
                        original_pidfd_send_signal(pidfd, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
        for pidfd in (init_pidfd, worker_pidfd):
            if isinstance(pidfd, int):
                os.close(pidfd)


@pytest.mark.parametrize("failure_stage", ["post_open_starttime", "pidfd_target"])
def test_open_monitor_pidfd_closes_descriptor_after_post_open_identity_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    api = _composition_api()
    opened_pidfd = 987_654
    closed_descriptors: list[int] = []
    starttime_reads = 0
    real_close = os.close

    def fake_starttime(_pid: int) -> int:
        nonlocal starttime_reads
        starttime_reads += 1
        if failure_stage == "post_open_starttime" and starttime_reads == 2:
            raise OSError("test-only proc stat read failure")
        return 1234

    def fake_pidfd_target(_pidfd: int) -> int:
        if failure_stage == "pidfd_target":
            raise ValueError("test-only fdinfo parse failure")
        return 42

    def fake_close(descriptor: int) -> None:
        if descriptor == opened_pidfd:
            closed_descriptors.append(descriptor)
            return
        real_close(descriptor)

    monkeypatch.setattr(api.os, "pidfd_open", lambda _pid: opened_pidfd)
    monkeypatch.setattr(api.os, "close", fake_close)
    monkeypatch.setattr(api, "_proc_start_time", fake_starttime)
    monkeypatch.setattr(api, "_pidfd_target", fake_pidfd_target)
    process = SimpleNamespace(pid=42, poll=lambda: None)

    assert api._open_monitor_pidfd(process) is None
    assert closed_descriptors == [opened_pidfd]


def test_wait_pidfd_readable_checks_ready_pidfd_with_zero_timeout() -> None:
    api = _composition_api()
    if not hasattr(os, "pidfd_open"):
        pytest.skip("host Python or kernel has no pidfd support")
    process = subprocess.Popen(["/bin/sleep", "0.05"])
    pidfd = os.pidfd_open(process.pid)
    try:
        process.wait(timeout=1.0)
        assert api._wait_pidfd_readable(pidfd, 0.0) is True
    finally:
        os.close(pidfd)


@pytest.mark.parametrize(
    "event",
    [
        select.POLLERR,
        select.POLLNVAL,
        select.POLLERR | select.POLLIN,
        select.POLLNVAL | select.POLLHUP,
    ],
)
def test_wait_pidfd_readable_does_not_treat_poll_error_as_process_exit(
    monkeypatch: pytest.MonkeyPatch, event: int
) -> None:
    api = _composition_api()

    class ErrorPoller:
        def register(self, _pidfd: int, _events: int) -> None:
            pass

        def poll(self, _timeout: int) -> list[tuple[int, int]]:
            return [(42, event)]

    monkeypatch.setattr(api.select, "poll", ErrorPoller)

    assert api._wait_pidfd_readable(42, 0.0) is False
