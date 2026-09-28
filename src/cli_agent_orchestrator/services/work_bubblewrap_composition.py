"""Pinned Bubblewrap process composition for durable Work launches.

The recorded launch stages one bound static ELF, installs inherited Landlock
and seccomp policy in the setup child, persists its acknowledgement and exact
process identity before GO, then hands tree ownership to the Work supervisor.
The Work backend remains unregistered until its separate T097/C08 gate closes.
"""

from __future__ import annotations

import array
import errno
import fcntl
import hashlib
import json
import math
import os
import select
import signal
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from cli_agent_orchestrator.services.work_process_supervisor import (
    WorkProcessAttempt,
    WorkProcessState,
    WorkProcessSupervisor,
)

from cli_agent_orchestrator.models.work_contract import ExecutableIdentity
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services.work_contract import WorkContracts
from cli_agent_orchestrator.services.work_bubblewrap_setup_intent import (
    WorkBubblewrapReleaseUncertain,
    WorkBubblewrapSetupIntent,
)
from cli_agent_orchestrator.services.work_mcp_proxy import (
    WorkMcpEndpoint,
    WorkMcpProxy,
    WorkMcpProxyError,
    WorkMcpProxyUncertain,
)
from cli_agent_orchestrator.services.work_executable_content import WorkExecutableContent
from cli_agent_orchestrator.services.work_executable_staging import (
    StagedExecutable,
    bubblewrap_bind_data_arguments,
    stage_static_executable,
)
from cli_agent_orchestrator.services.work_bubblewrap_runtime_snapshot import (
    RuntimeSnapshot,
    RuntimeSnapshotError,
    prepare_runtime_snapshot,
)
from cli_agent_orchestrator.work_bubblewrap_policy import BUBBLEWRAP_VERSION_TEXT
from cli_agent_orchestrator.services.work_bubblewrap_isolation_proof import (
    WorkBubblewrapRuntimeIsolationProof,
    _issue_runtime_isolation_proof,
)

_BWRAP_VERSION = BUBBLEWRAP_VERSION_TEXT
_CANONICAL_SYSTEM_BWRAP = Path("/usr/bin/bwrap")
_TRUSTED_SYSTEM_BWRAP_PARENTS = (Path("/"), Path("/usr"), Path("/usr/bin"))
_BWRAP_HASH_CHUNK_BYTES = 1024 * 1024
_BWRAP_HASH_MAX_BYTES = 64 * 1024 * 1024
_F_ADD_SEALS = getattr(fcntl, "F_ADD_SEALS", 1033)
_F_GET_SEALS = getattr(fcntl, "F_GET_SEALS", 1034)
_F_SEAL_WRITE = getattr(fcntl, "F_SEAL_WRITE", 0x0008)
_F_SEAL_SHRINK = getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
_F_SEAL_GROW = getattr(fcntl, "F_SEAL_GROW", 0x0004)
_F_SEAL_SEAL = getattr(fcntl, "F_SEAL_SEAL", 0x0001)
_BWRAP_REQUIRED_SEALS = _F_SEAL_WRITE | _F_SEAL_SHRINK | _F_SEAL_GROW | _F_SEAL_SEAL
_DESIGNATED_BWRAP = Path("/tmp/caos-exec/T097/bubblewrap-build/bwrap")
_DESTINATION = "/exec/worker"
_ACK_PREFIX = b"CAO_WORKER_READY/1 "
_ACK_LIMIT = 8192
_BWRAP_INFO_LIMIT = 4096
_STDERR_LIMIT = 65536
_CLEANUP_TIMEOUT_SECONDS = 2.0
_PROXY_THREAD_JOIN_SECONDS = 6.0
_DEFAULT_SETUP_TIMEOUT_SECONDS = 20.0


class WorkBubblewrapCompositionError(RuntimeError):
    """A staged composition could not be completed with a known-safe outcome."""

    def __init__(
        self,
        message: str,
        *,
        monitor_pid: int | None = None,
        stdout: bytes = b"",
        stderr: bytes = b"",
        cleanup_confirmed: bool = True,
    ) -> None:
        super().__init__(message)
        self.monitor_pid = monitor_pid
        self.stdout = stdout
        self.stderr = stderr
        self.cleanup_confirmed = cleanup_confirmed


class WorkBubblewrapSetupFailed(WorkBubblewrapCompositionError):
    """Bubblewrap or the isolated bootstrap failed before a valid ACK."""


class WorkBubblewrapAuthorizationDenied(WorkBubblewrapCompositionError):
    """The parent declined to release the worker after its setup ACK."""


class WorkBubblewrapParentGateFailed(WorkBubblewrapCompositionError):
    """The parent persistence or fresh-authorization callback raised."""


class WorkBubblewrapExecutionUncertain(WorkBubblewrapCompositionError):
    """The released worker did not finish within the bounded execution window."""


class WorkBubblewrapCleanupUncertain(WorkBubblewrapCompositionError):
    """The monitor or its descendants could not be proven gone."""


@dataclass(frozen=True, slots=True)
class WorkBubblewrapAcknowledgement:
    """Child evidence emitted only after destination checks and both policies."""

    destination: str
    sha256_digest: str
    mode: int
    size: int
    destination_device: int
    destination_inode: int
    open_fds: tuple[int, ...]
    landlock_abi: int
    seccomp_mode: int
    unlisted_path_denied: bool
    unmounted_host_path_absent: bool
    memfd_create_denied_errno: int
    connect_denied_errno: int
    monitor_pid: int
    proxy_socket_device: int | None
    proxy_socket_inode: int | None


@dataclass(frozen=True, slots=True)
class WorkBubblewrapExecution:
    """Known worker exit status and its post-gate standard-stream output."""

    returncode: int
    stdout: bytes
    stderr: bytes
    acknowledgement: WorkBubblewrapAcknowledgement


class _AckFailure(RuntimeError):
    def __init__(self, message: str, stdout: bytes, stderr: bytes) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True, slots=True)
class _PinnedBubblewrap:
    path: Path
    source_descriptor: int
    execution_descriptor: int
    identity: tuple[int, ...]

    @property
    def proc_path(self) -> str:
        return f"/proc/self/fd/{self.execution_descriptor}"


@dataclass(frozen=True, slots=True)
class _NamespaceInitIdentity:
    pid: int
    parent_pid: int
    start_time: int
    namespace_inode: int
    namespace_pids: tuple[int, ...]


_BOOTSTRAP = r"""
import array
import ctypes
import errno
import json
import os
import socket
import stat
import sys

from cli_agent_orchestrator.services.work_elf_identity import identify_static_executable
from cli_agent_orchestrator.services.work_process_landlock import (
    PathPolicyAccess,
    PathPolicyRule,
    _query_abi_version,
    install_path_policy,
)
from cli_agent_orchestrator.services.work_process_seccomp import (
    _prctl,
    install_work_process_seccomp_filter,
)
source_fd = int(sys.argv[1])
expected = json.loads(sys.argv[2])
bwrap_executable_fd = int(sys.argv[3])
proxy_requested = int(sys.argv[4]) == 1
proxy_fd = -1
destination = "/exec/worker"

for inherited_descriptor in (source_fd, bwrap_executable_fd):
    try:
        os.close(inherited_descriptor)
    except OSError as exc:
        if exc.errno != errno.EBADF:
            raise
if proxy_requested:
    control = socket.socket(fileno=0)
    try:
        message, ancillary, flags, _ = control.recvmsg(
            1, socket.CMSG_SPACE(array.array("i").itemsize), socket.MSG_CMSG_CLOEXEC
        )
    finally:
        control.detach()
    received_fds = []
    for level, kind, payload in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            descriptors = array.array("i")
            descriptors.frombytes(payload[:len(payload) - (len(payload) % descriptors.itemsize)])
            received_fds.extend(descriptors)
    if message != b"P" or flags & socket.MSG_CTRUNC or len(received_fds) != 1:
        for descriptor in received_fds:
            os.close(descriptor)
        raise RuntimeError("parent did not pass exactly one proxy socket")
    proxy_fd = received_fds[0]
    if proxy_fd != 3:
        os.dup2(proxy_fd, 3, inheritable=False)
        os.close(proxy_fd)
        proxy_fd = 3
    if not stat.S_ISSOCK(os.fstat(proxy_fd).st_mode):
        raise RuntimeError("parent passed a non-socket proxy descriptor")

read_fd = os.open(destination, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
opath_fd = -1
inventory_fd = -1
unmounted_host_path_absent = False
try:
    before = os.fstat(read_fd)
    if not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o500:
        raise RuntimeError("materialized executable has the wrong file type or mode")
    chunks = []
    while True:
        chunk = os.read(read_fd, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    content = b"".join(chunks)
    observed = identify_static_executable(expected["command_token"], content)
    if observed.model_dump(mode="json") != expected:
        raise RuntimeError("materialized executable identity differs from its sealed stage")
    if len(content) != before.st_size:
        raise RuntimeError("materialized executable size changed while validating it")

    try:
        os.stat("/etc/passwd")
    except OSError as exc:
        if exc.errno != errno.ENOENT:
            raise
        unmounted_host_path_absent = True
    else:
        raise RuntimeError("unmounted host path remained visible in the child root")

    # Landlock will deny opening unlisted /proc paths. Keep this directory
    # description only long enough to count descriptors after policy install.
    inventory_fd = os.open(
        "/proc/self/fd",
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
    )
    opath_fd = os.open(
        destination,
        os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    anchored = os.fstat(opath_fd)
    if (
        not stat.S_ISREG(anchored.st_mode)
        or (anchored.st_dev, anchored.st_ino) != (before.st_dev, before.st_ino)
    ):
        raise RuntimeError("Landlock anchor does not identify the validated destination")

    landlock_abi = _query_abi_version()
    if landlock_abi < 1:
        raise OSError(errno.EOPNOTSUPP, "Landlock ABI 1 is required")
    install_path_policy(
        [
            PathPolicyRule(
                opath_fd,
                PathPolicyAccess.READ | PathPolicyAccess.EXECUTE,
            )
        ]
    )
finally:
    os.close(read_fd)
    if opath_fd >= 0:
        os.close(opath_fd)

unlisted_path_denied = False
try:
    os.open("/usr/bin/true", os.O_RDONLY | os.O_CLOEXEC)
except OSError as exc:
    if exc.errno not in (errno.EACCES, errno.EPERM):
        raise
    unlisted_path_denied = True
else:
    raise RuntimeError("Landlock allowed a read from an unlisted path")

libc = ctypes.CDLL(None, use_errno=True)
syscall = libc.syscall
syscall.restype = ctypes.c_long
syscall.argtypes = [ctypes.c_long, ctypes.c_char_p, ctypes.c_uint]
seccomp_mode = _prctl(21)
if seccomp_mode == 1:
    raise RuntimeError("strict seccomp mode cannot install the Work filter")
install_work_process_seccomp_filter()
seccomp_mode = _prctl(21)
if seccomp_mode != 2:
    raise RuntimeError("Work seccomp filter did not enter filter mode")
ctypes.set_errno(0)
memfd_result = syscall(319, b"cao-composition-policy-probe", 1)
memfd_errno = ctypes.get_errno()
if memfd_result >= 0:
    os.close(memfd_result)
    raise RuntimeError("Work seccomp policy allowed memfd_create")
if memfd_errno != errno.EPERM:
    raise OSError(memfd_errno, "Work seccomp policy returned an unexpected errno")

connect_probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    try:
        connect_probe.connect("/run/cao-seccomp-connect-probe.sock")
    except OSError as exc:
        connect_errno = exc.errno
    else:
        raise RuntimeError("Work seccomp policy allowed connect")
finally:
    connect_probe.close()
if connect_errno != errno.EPERM:
    raise OSError(connect_errno, "Work seccomp policy returned an unexpected connect errno")

listed_fds = tuple(sorted(int(name) for name in os.listdir(inventory_fd)))
if inventory_fd not in listed_fds:
    raise RuntimeError("descriptor inventory lost its own directory descriptor")
live_fds = []
for descriptor in listed_fds:
    try:
        os.fstat(descriptor)
    except OSError as exc:
        if exc.errno != errno.EBADF:
            raise
        continue
    live_fds.append(descriptor)
fd_inventory = tuple(fd for fd in live_fds if fd != inventory_fd)
os.close(inventory_fd)
inventory_fd = -1
expected_fds = (0, 1, 2) if proxy_fd < 0 else (0, 1, 2, proxy_fd)
if fd_inventory != expected_fds:
    raise RuntimeError(
        "bootstrap retained an unexpected file descriptor: " + repr(fd_inventory)
    )

acknowledgement = {
    "destination": destination,
    "sha256_digest": expected["sha256_digest"],
    "mode": stat.S_IMODE(before.st_mode),
    "size": before.st_size,
    "destination_device": before.st_dev,
    "destination_inode": before.st_ino,
    "open_fds": fd_inventory,
    "proxy_socket_device": None if proxy_fd < 0 else os.fstat(proxy_fd).st_dev,
    "proxy_socket_inode": None if proxy_fd < 0 else os.fstat(proxy_fd).st_ino,
    "landlock_abi": landlock_abi,
    "seccomp_mode": seccomp_mode,
    "unlisted_path_denied": unlisted_path_denied,
    "unmounted_host_path_absent": unmounted_host_path_absent,
    "memfd_create_denied_errno": memfd_errno,
    "connect_denied_errno": connect_errno,
}
os.write(1, b"CAO_WORKER_READY/1 " + json.dumps(acknowledgement, sort_keys=True).encode() + b"\n")
if os.read(0, 3) != b"GO\n":
    raise RuntimeError("parent did not release the worker")
if proxy_fd >= 0:
    os.set_inheritable(proxy_fd, True)
    environment = {"LC_ALL": "C", "CAO_WORK_MCP_FD": str(proxy_fd)}
else:
    environment = {"LC_ALL": "C"}
os.execve(destination, [expected["command_token"]], environment)
"""


def launch_staged_static_elf(
    identity: ExecutableIdentity,
    executable_bytes: bytes,
    *,
    persist_and_authorize: Callable[[WorkBubblewrapAcknowledgement, dict[str, object]], object],
    bwrap_path: str | os.PathLike[str],
    bwrap_sha256_digest: str,
    timeout_seconds: float = 10.0,
    setup_timeout_seconds: float = _DEFAULT_SETUP_TIMEOUT_SECONDS,
) -> WorkBubblewrapExecution:
    """Run one staged static ELF after child policy ACK and parent approval.

    ``persist_and_authorize`` is an injected local callback. It must persist the
    ACK and process identity, then return a fresh authorization result. It runs
    synchronously before any worker code, and only the literal ``True`` releases
    the child. ``timeout_seconds`` bounds worker execution and
    ``setup_timeout_seconds`` bounds the namespace and policy ACK. Neither can
    cancel or bound a blocked local callback.
    """

    if not isinstance(identity, ExecutableIdentity):
        raise TypeError("identity must be an ExecutableIdentity")
    if not isinstance(executable_bytes, (bytes, bytearray, memoryview)):
        raise TypeError("executable_bytes must be bytes-like")
    if not callable(persist_and_authorize):
        raise TypeError("persist_and_authorize must be callable")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be a finite positive number")
    if (
        isinstance(setup_timeout_seconds, bool)
        or not isinstance(setup_timeout_seconds, (int, float))
        or not math.isfinite(setup_timeout_seconds)
        or setup_timeout_seconds <= 0
    ):
        raise ValueError("setup_timeout_seconds must be a finite positive number")

    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise WorkBubblewrapCleanupUncertain(
            "host Python or kernel does not provide pidfd cleanup support",
            cleanup_confirmed=False,
        )

    staged = stage_static_executable(identity, executable_bytes)
    return _launch_owned_stage(
        staged,
        identity,
        expected_size=memoryview(executable_bytes).nbytes,
        persist_and_authorize=persist_and_authorize,
        bwrap_path=bwrap_path,
        bwrap_sha256_digest=bwrap_sha256_digest,
        timeout_seconds=timeout_seconds,
        setup_timeout_seconds=setup_timeout_seconds,
    )


def launch_bound_work_static_elf(
    content: WorkExecutableContent,
    attempt_id: str,
    generation: int,
    command_token: str,
    *,
    persist_and_authorize: Callable[[WorkBubblewrapAcknowledgement, dict[str, object]], object],
    bwrap_path: str | os.PathLike[str],
    bwrap_sha256_digest: str,
    timeout_seconds: float = 10.0,
    setup_timeout_seconds: float = _DEFAULT_SETUP_TIMEOUT_SECONDS,
) -> WorkBubblewrapExecution:
    """Launch only the sealed content selected by a bound V2 attempt command."""
    if not isinstance(content, WorkExecutableContent):
        raise TypeError("content must be a WorkExecutableContent")
    staged = content.resolve_and_stage(attempt_id, generation, command_token)
    return _launch_owned_stage(
        staged,
        None,
        expected_command_token=command_token,
        persist_and_authorize=persist_and_authorize,
        bwrap_path=bwrap_path,
        bwrap_sha256_digest=bwrap_sha256_digest,
        timeout_seconds=timeout_seconds,
        setup_timeout_seconds=setup_timeout_seconds,
    )


def launch_recorded_bound_work_static_elf(
    repository: WorkRepository,
    attempt_id: str,
    generation: int,
    *,
    expected_attempt_revision: int,
    command_token: str,
    bwrap_path: str | os.PathLike[str],
    bwrap_sha256_digest: str,
    timeout_seconds: float = 10.0,
    setup_timeout_seconds: float = _DEFAULT_SETUP_TIMEOUT_SECONDS,
    mcp_proxy: WorkMcpProxy | None = None,
    worker_input: bytes = b"",
    process_supervisor: WorkProcessSupervisor | None = None,
    before_start: Callable[[], None] | None = None,
    authorize_setup: Callable[[sqlite3.Connection], None] | None = None,
    authorize_go: Callable[[sqlite3.Connection], None] | None = None,
) -> WorkBubblewrapExecution:
    """Run the bound scratch worker only after durable setup and a fenced GO."""
    if not isinstance(repository, WorkRepository):
        raise TypeError("a server-owned Work repository is required")
    if (
        isinstance(setup_timeout_seconds, bool)
        or not isinstance(setup_timeout_seconds, (int, float))
        or not math.isfinite(setup_timeout_seconds)
        or setup_timeout_seconds <= 0
    ):
        raise ValueError("setup_timeout_seconds must be a finite positive number")
    content = WorkExecutableContent(repository)
    staged = content.resolve_and_stage(attempt_id, generation, command_token)
    setup = WorkBubblewrapSetupIntent(repository)
    endpoint: WorkMcpEndpoint | None = None
    serving_thread: threading.Thread | None = None
    proxy_errors: list[WorkMcpProxyError] = []
    proxy_proof: WorkBubblewrapRuntimeIsolationProof | None = None
    proof_context: tuple[WorkRepository, str, int, int, str] | None = None
    if mcp_proxy is not None:
        with repository.read_snapshot() as connection:
            repository._verify(connection)
            binding = WorkContracts(repository)._revalidate_order(
                connection, attempt_id, generation=generation
            )
            attempt = connection.execute(
                "SELECT state,revision,lease_expires_at FROM work_attempts "
                "WHERE id=? AND generation=?",
                (attempt_id, generation),
            ).fetchone()
            if (
                attempt is None
                or attempt["state"] != "sent"
                or attempt["revision"] != expected_attempt_revision
            ):
                staged.close()
                raise ValueError("proxy endpoint requires the current sent attempt")
            proxy_expiry = attempt["lease_expires_at"]
            contract_hash = binding.contract_hash
        try:
            endpoint = mcp_proxy.create_bound_attempt(
                attempt_id=attempt_id,
                generation=generation,
                expected_attempt_revision=expected_attempt_revision,
                contract_hash=contract_hash,
                expires_at=proxy_expiry,
            )
        except BaseException:
            staged.close()
            raise
        proof_context = (
            repository,
            attempt_id,
            generation,
            expected_attempt_revision,
            contract_hash,
        )

    def release_after_ack(acknowledgement, process_identity, release, proof_factory):
        nonlocal serving_thread, proxy_proof
        evidence = setup.record_pre_go(
            attempt_id,
            generation,
            expected_attempt_revision,
            command_token,
            acknowledgement,
            process_identity,
            process_identity["identity_sha256"],
            expected_proxy_fd=None if endpoint is None else 3,
            expected_proxy_socket_identity=(
                None if endpoint is None else endpoint.worker_socket_identity
            ),
            authorize=authorize_setup,
        )
        if mcp_proxy is not None and endpoint is not None:
            proxy_proof = proof_factory()
            mcp_proxy.activate_with_isolation_proof(endpoint, proxy_proof)

            def serve_proxy() -> None:
                try:
                    mcp_proxy.serve(attempt_id, generation)
                except WorkMcpProxyError as exc:
                    proxy_errors.append(exc)

            serving_thread = threading.Thread(target=serve_proxy, daemon=True)
            serving_thread.start()
        setup.release_if_current(
            evidence,
            expected_attempt_revision=expected_attempt_revision,
            release=release,
            authorize=authorize_go,
        )

    try:
        result = _launch_owned_stage(
            staged,
            None,
            expected_command_token=command_token,
            release_after_ack=release_after_ack,
            worker_input=worker_input,
            process_supervisor=process_supervisor,
            before_start=before_start,
            authorize_setup=authorize_setup,
            authorize_go=authorize_go,
            worker_socket_fd=None if endpoint is None else endpoint.worker_fd,
            proof_context=proof_context,
            bwrap_path=bwrap_path,
            bwrap_sha256_digest=bwrap_sha256_digest,
            timeout_seconds=timeout_seconds,
            setup_timeout_seconds=setup_timeout_seconds,
        )
        if serving_thread is not None:
            # The broker owns a copy of the worker endpoint. Close both ends
            # after the worker is reaped so the proxy reader observes EOF
            # before joining; joining first would wait forever on our own FD.
            assert mcp_proxy is not None
            mcp_proxy.revoke(attempt_id, generation)
            serving_thread.join(timeout=_PROXY_THREAD_JOIN_SECONDS)
            if serving_thread.is_alive():
                raise WorkBubblewrapExecutionUncertain(
                    "proxy worker thread did not stop after worker exit",
                    cleanup_confirmed=False,
                )
            if any(isinstance(error, WorkMcpProxyUncertain) for error in proxy_errors):
                raise WorkBubblewrapExecutionUncertain(
                    "MCP upstream effect is uncertain; reconcile before retry",
                    cleanup_confirmed=True,
                )
        return result
    finally:
        if mcp_proxy is not None:
            mcp_proxy.close()
        if proxy_proof is not None and (
            mcp_proxy is None or mcp_proxy._isolation_proof is not proxy_proof
        ):
            proxy_proof.close()


def _launch_owned_stage(
    staged: StagedExecutable,
    identity: ExecutableIdentity | None,
    *,
    expected_size: int | None = None,
    expected_command_token: str | None = None,
    persist_and_authorize: (
        Callable[[WorkBubblewrapAcknowledgement, dict[str, object]], object] | None
    ) = None,
    release_after_ack: (
        Callable[
            [
                WorkBubblewrapAcknowledgement,
                dict[str, object],
                Callable[[], None],
                Callable[[], WorkBubblewrapRuntimeIsolationProof],
            ],
            None,
        ]
        | None
    ) = None,
    worker_socket_fd: int | None = None,
    proof_context: tuple[WorkRepository, str, int, int, str] | None = None,
    worker_input: bytes = b"",
    process_supervisor: WorkProcessSupervisor | None = None,
    before_start: Callable[[], None] | None = None,
    authorize_setup: Callable[[sqlite3.Connection], None] | None = None,
    authorize_go: Callable[[sqlite3.Connection], None] | None = None,
    bwrap_path: str | os.PathLike[str],
    bwrap_sha256_digest: str,
    timeout_seconds: float,
    setup_timeout_seconds: float,
) -> WorkBubblewrapExecution:
    """Take ownership of exactly one sealed stage through cleanup on every exit."""
    process: subprocess.Popen[bytes] | None = None
    info_read_fd: int | None = None
    info_write_fd: int | None = None
    init_pidfd: int | None = None
    monitor_pidfd: int | None = None
    snapshot_parent: Path | None = None
    runtime_snapshot: RuntimeSnapshot | None = None
    control_socket: socket.socket | None = None
    child_control_socket: socket.socket | None = None
    snapshot_cleanup_confirmed = True
    supervised_attempt: WorkProcessAttempt | None = None
    if not isinstance(worker_input, bytes) or len(worker_input) > 32768:
        raise ValueError("worker input must be bytes within the 32 KiB bound")
    if process_supervisor is not None and not isinstance(process_supervisor, WorkProcessSupervisor):
        raise TypeError("process_supervisor must be a WorkProcessSupervisor")
    if before_start is not None and not callable(before_start):
        raise TypeError("before_start must be callable")
    if authorize_setup is not None and not callable(authorize_setup):
        raise TypeError("authorize_setup must be callable")
    if authorize_go is not None and not callable(authorize_go):
        raise TypeError("authorize_go must be callable")

    def cleanup_process_tree(*, init_resolution_attempted=False):
        assert process is not None
        return _kill_and_reap(
            process,
            init_pidfd,
            monitor_pidfd,
            init_resolution_attempted=init_resolution_attempted,
            process_supervisor=(process_supervisor if supervised_attempt is not None else None),
            supervised_attempt=supervised_attempt,
        )

    try:
        if not isinstance(staged, StagedExecutable):
            raise TypeError("resolver did not return a staged executable")
        if identity is None:
            identity = staged.identity
        if not isinstance(identity, ExecutableIdentity) or staged.identity != identity:
            raise ValueError("staged executable identity differs from the selected command")
        if expected_command_token is not None and identity.command_token != expected_command_token:
            raise ValueError("staged command token differs from the bound command")
        if worker_socket_fd is not None and (
            type(worker_socket_fd) is not int
            or not 3 <= worker_socket_fd <= 64
            or not stat.S_ISSOCK(os.fstat(worker_socket_fd).st_mode)
            or proof_context is None
        ):
            raise ValueError("proxy worker descriptor must be bounded and attempt-bound")
        stage_size = os.fstat(staged.memfd_fd).st_size
        if stage_size <= 0 or (expected_size is not None and stage_size != expected_size):
            raise ValueError("staged executable size differs from its accepted content")
        if (persist_and_authorize is None) == (release_after_ack is None) or not callable(
            persist_and_authorize if release_after_ack is None else release_after_ack
        ):
            raise TypeError("exactly one parent release gate is required")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a finite positive number")
        if (
            isinstance(setup_timeout_seconds, bool)
            or not isinstance(setup_timeout_seconds, (int, float))
            or not math.isfinite(setup_timeout_seconds)
            or setup_timeout_seconds <= 0
        ):
            raise ValueError("setup_timeout_seconds must be a finite positive number")
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise WorkBubblewrapCleanupUncertain(
                "host Python or kernel does not provide pidfd cleanup support",
                cleanup_confirmed=False,
            )
        accepted_bwrap = _require_accepted_bwrap(bwrap_path, bwrap_sha256_digest)
        try:
            snapshot_parent, runtime_snapshot = _prepare_runtime_snapshot()
            info_read_fd, info_write_fd = os.pipe2(os.O_CLOEXEC)
            argv = _build_bwrap_argv(
                accepted_bwrap.path,
                staged,
                identity,
                accepted_bwrap.execution_descriptor,
                info_write_fd,
                snapshot=runtime_snapshot,
                worker_socket_fd=worker_socket_fd,
            )
            _revalidate_pinned_bwrap(accepted_bwrap)
            runtime_snapshot.validate()
            if before_start is not None:
                before_start()
            try:
                if worker_socket_fd is None:
                    child_stdin = subprocess.PIPE
                else:
                    control_socket, child_control_socket = socket.socketpair(
                        socket.AF_UNIX,
                        socket.SOCK_STREAM | getattr(socket, "SOCK_CLOEXEC", 0),
                    )
                    control_socket.settimeout(setup_timeout_seconds)
                    child_stdin = child_control_socket
                process = subprocess.Popen(
                    argv,
                    executable=accepted_bwrap.proc_path,
                    stdin=child_stdin,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                    pass_fds=(
                        staged.memfd_fd,
                        accepted_bwrap.execution_descriptor,
                        info_write_fd,
                    ),
                    start_new_session=True,
                    env={"LC_ALL": "C"},
                )
                if child_control_socket is not None:
                    child_control_socket.close()
                    child_control_socket = None
                if control_socket is not None and worker_socket_fd is not None:
                    rights = array.array("i", [worker_socket_fd])
                    control_socket.sendmsg([b"P"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, rights)])
                monitor_pidfd = _open_monitor_pidfd(process)
            except OSError as exc:
                if process is not None:
                    stdout, stderr, confirmed = _kill_and_reap(
                        process, init_pidfd, monitor_pidfd, init_resolution_attempted=True
                    )
                    raise WorkBubblewrapSetupFailed(
                        "Bubblewrap spawn or proxy descriptor handoff failed",
                        monitor_pid=process.pid,
                        stdout=stdout,
                        stderr=stderr,
                        cleanup_confirmed=confirmed,
                    ) from exc
                raise WorkBubblewrapSetupFailed(
                    f"could not start accepted Bubblewrap: {exc}",
                    cleanup_confirmed=True,
                ) from exc
        finally:
            try:
                _close_pinned_bwrap(accepted_bwrap)
            except WorkBubblewrapCompositionError as exc:
                if process is None:
                    raise
                if monitor_pidfd is None:
                    try:
                        process.kill()
                    except OSError:
                        pass
                stdout, stderr, confirmed = _kill_and_reap(
                    process,
                    init_pidfd,
                    monitor_pidfd,
                    init_resolution_attempted=init_pidfd is None,
                )
                raise WorkBubblewrapCleanupUncertain(
                    "pinned Bubblewrap descriptors could not be closed after spawn",
                    monitor_pid=process.pid,
                    stdout=stdout,
                    stderr=stderr,
                    cleanup_confirmed=confirmed if init_pidfd is not None else False,
                ) from exc

        os.close(info_write_fd)
        info_write_fd = None
        try:
            info_payload = _parse_bwrap_info(_read_bwrap_info(info_read_fd, setup_timeout_seconds))
            init_pidfd = _open_namespace_init_pidfd(process, info_payload)
        except BaseException as exc:
            stdout, stderr, _ = _kill_and_reap(
                process,
                init_pidfd,
                monitor_pidfd,
                init_resolution_attempted=True,
            )
            raise WorkBubblewrapCleanupUncertain(
                f"Bubblewrap namespace identity was unavailable or invalid: {exc}",
                monitor_pid=process.pid,
                stdout=stdout,
                stderr=stderr,
                cleanup_confirmed=False,
            ) from exc
        finally:
            os.close(info_read_fd)
            info_read_fd = None

        try:
            raw_ack, setup_stdout, setup_stderr = _read_ack(process, setup_timeout_seconds)
            acknowledgement = _parse_ack(
                raw_ack,
                identity,
                stage_size,
                process.pid,
                proxy_fd=None if worker_socket_fd is None else 3,
                proxy_socket_identity=(
                    None if worker_socket_fd is None else _socket_file_identity(worker_socket_fd)
                ),
            )
        except _AckFailure as exc:
            stdout, stderr, confirmed = _kill_and_reap(process, init_pidfd, monitor_pidfd)
            raise _setup_failure(
                str(exc),
                process,
                exc.stdout + stdout,
                exc.stderr + stderr,
                confirmed,
            ) from exc
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            stdout, stderr, confirmed = _kill_and_reap(process, init_pidfd, monitor_pidfd)
            raise _setup_failure(
                f"child sent an invalid setup acknowledgement: {exc}",
                process,
                stdout,
                stderr,
                confirmed,
            ) from exc

        if process.poll() is not None:
            stdout, stderr, confirmed = _kill_and_reap(process, init_pidfd, monitor_pidfd)
            raise _setup_failure(
                "Bubblewrap exited before the parent release gate",
                process,
                setup_stdout + stdout,
                setup_stderr + stderr,
                confirmed,
            )

        try:
            process_identity = _capture_bubblewrap_process_identity(
                process,
                monitor_pidfd,
                init_pidfd,
                info_payload,
                argv,
                bwrap_sha256_digest,
            )
            if process_supervisor is not None:
                supervised_attempt = process_supervisor.adopt_bubblewrap_process_tree(
                    process, monitor_pidfd, init_pidfd, process_identity
                )
        except BaseException as exc:
            stdout, stderr, confirmed = _kill_and_reap(process, init_pidfd, monitor_pidfd)
            raise WorkBubblewrapCleanupUncertain(
                f"Bubblewrap process identity could not be captured before release: {exc}",
                monitor_pid=process.pid,
                stdout=setup_stdout + stdout,
                stderr=setup_stderr + stderr,
                cleanup_confirmed=confirmed,
            ) from exc

        if process.stdin is None and control_socket is None:
            stdout, stderr, confirmed = cleanup_process_tree()
            raise _setup_failure(
                "Bubblewrap stdin release pipe is unavailable",
                process,
                setup_stdout + stdout,
                setup_stderr + stderr,
                confirmed,
            )
        if release_after_ack is not None:

            def write_go() -> None:
                if control_socket is not None:
                    control_socket.sendall(b"GO\n")
                else:
                    assert process.stdin is not None
                    written = os.write(process.stdin.fileno(), b"GO\n")
                    if written != 3:
                        raise OSError(errno.EIO, "short write to Bubblewrap release pipe")

            if proof_context is None:

                def proof_factory() -> WorkBubblewrapRuntimeIsolationProof:
                    raise WorkBubblewrapCompositionError(
                        "attempt-bound isolation proof context is unavailable"
                    )

            else:
                proof_repository, proof_attempt, proof_generation, proof_revision, proof_hash = (
                    proof_context
                )

                def proof_factory() -> WorkBubblewrapRuntimeIsolationProof:
                    assert runtime_snapshot is not None
                    assert monitor_pidfd is not None and init_pidfd is not None
                    return _issue_runtime_isolation_proof(
                        repository=proof_repository,
                        snapshot=runtime_snapshot,
                        attempt_id=proof_attempt,
                        generation=proof_generation,
                        attempt_revision=proof_revision,
                        contract_hash=proof_hash,
                        process_identity=process_identity,
                        monitor_pidfd=monitor_pidfd,
                        init_pidfd=init_pidfd,
                        worker_socket_fd=worker_socket_fd,
                    )

            try:
                release_after_ack(acknowledgement, process_identity, write_go, proof_factory)
            except WorkBubblewrapReleaseUncertain as exc:
                stdout, stderr, confirmed = cleanup_process_tree()
                raise WorkBubblewrapExecutionUncertain(
                    "Bubblewrap GO was attempted; reconcile the pending setup before retry",
                    monitor_pid=process.pid,
                    stdout=setup_stdout + stdout,
                    stderr=setup_stderr + stderr,
                    cleanup_confirmed=confirmed,
                ) from exc
            except BaseException as exc:
                stdout, stderr, confirmed = cleanup_process_tree()
                raise WorkBubblewrapParentGateFailed(
                    "parent persistence or fresh authorization failed before GO",
                    monitor_pid=process.pid,
                    stdout=setup_stdout + stdout,
                    stderr=setup_stderr + stderr,
                    cleanup_confirmed=confirmed,
                ) from exc
        else:
            try:
                approved = persist_and_authorize(acknowledgement, process_identity)
            except BaseException as exc:
                stdout, stderr, confirmed = cleanup_process_tree()
                raise WorkBubblewrapParentGateFailed(
                    "parent persistence or fresh authorization callback failed",
                    monitor_pid=process.pid,
                    stdout=setup_stdout + stdout,
                    stderr=setup_stderr + stderr,
                    cleanup_confirmed=confirmed,
                ) from exc

            if approved is not True:
                stdout, stderr, confirmed = cleanup_process_tree()
                raise WorkBubblewrapAuthorizationDenied(
                    "parent did not authorize release of the staged worker",
                    monitor_pid=process.pid,
                    stdout=setup_stdout + stdout,
                    stderr=setup_stderr + stderr,
                    cleanup_confirmed=confirmed,
                )

        try:
            if release_after_ack is None:
                assert process.stdin is not None
                written = os.write(process.stdin.fileno(), b"GO\n")
                if written != 3:
                    raise OSError(errno.EIO, "short write to Bubblewrap release pipe")
        except OSError as exc:
            stdout, stderr, confirmed = cleanup_process_tree()
            raise _setup_failure(
                f"could not release the acknowledged worker: {exc}",
                process,
                setup_stdout + stdout,
                setup_stderr + stderr,
                confirmed,
            ) from exc

        try:
            stdout, stderr = process.communicate(input=worker_input, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            partial_stdout, partial_stderr, confirmed = cleanup_process_tree()
            raise WorkBubblewrapExecutionUncertain(
                "staged worker exceeded its execution or cleanup bound",
                monitor_pid=process.pid,
                stdout=setup_stdout + (exc.output or b"") + partial_stdout,
                stderr=setup_stderr + (exc.stderr or b"") + partial_stderr,
                cleanup_confirmed=confirmed,
            ) from exc

        if supervised_attempt is not None:
            state = process_supervisor.wait(supervised_attempt, timeout=0)
            if state is not WorkProcessState.TERMINATED:
                partial_stdout, partial_stderr, confirmed = cleanup_process_tree()
                raise WorkBubblewrapCleanupUncertain(
                    "supervisor could not confirm the complete Bubblewrap process tree exit",
                    monitor_pid=process.pid,
                    stdout=stdout + partial_stdout,
                    stderr=stderr + partial_stderr,
                    cleanup_confirmed=confirmed,
                )

        if not _wait_pidfd_readable(init_pidfd, _CLEANUP_TIMEOUT_SECONDS):
            partial_stdout, partial_stderr, confirmed = cleanup_process_tree()
            raise WorkBubblewrapCleanupUncertain(
                "Bubblewrap monitor exited without a readable namespace-init pidfd",
                monitor_pid=process.pid,
                stdout=setup_stdout + stdout + partial_stdout,
                stderr=setup_stderr + stderr + partial_stderr,
                cleanup_confirmed=False and confirmed,
            )
        monitor_reaped = process.poll() is not None
        if not monitor_reaped:
            partial_stdout, partial_stderr, _ = cleanup_process_tree()
            raise WorkBubblewrapCleanupUncertain(
                "Bubblewrap monitor could not be collected after namespace init exited",
                monitor_pid=process.pid,
                stdout=setup_stdout + stdout + partial_stdout,
                stderr=setup_stderr + stderr + partial_stderr,
                cleanup_confirmed=False,
            )

        snapshot_cleanup_confirmed = True
        return WorkBubblewrapExecution(
            returncode=process.returncode,
            stdout=stdout,
            stderr=setup_stderr + stderr,
            acknowledgement=acknowledgement,
        )
    except WorkBubblewrapCompositionError as exc:
        snapshot_cleanup_confirmed = exc.cleanup_confirmed
        if runtime_snapshot is not None and not snapshot_cleanup_confirmed:
            exc.snapshot_path = runtime_snapshot.path
        raise
    except BaseException as exc:
        if process is not None:
            stdout, stderr, confirmed = cleanup_process_tree()
            snapshot_cleanup_confirmed = confirmed
            raise WorkBubblewrapCleanupUncertain(
                "unexpected composition failure; worker release was withheld",
                monitor_pid=process.pid,
                stdout=stdout,
                stderr=stderr,
                cleanup_confirmed=confirmed,
            ) from exc
        snapshot_cleanup_confirmed = True
        raise
    finally:
        for descriptor in (init_pidfd, monitor_pidfd, info_read_fd, info_write_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        for control in (control_socket, child_control_socket):
            if control is not None:
                try:
                    control.close()
                except OSError:
                    pass
        try:
            staged.close()
        except OSError as exc:
            raise WorkBubblewrapCleanupUncertain(
                "parent could not close every sealed-stage descriptor",
                monitor_pid=process.pid if process is not None else None,
                cleanup_confirmed=False,
            ) from exc
        if runtime_snapshot is not None:
            try:
                runtime_snapshot.close(cleanup_confirmed=snapshot_cleanup_confirmed)
                if snapshot_cleanup_confirmed and snapshot_parent is not None:
                    os.rmdir(snapshot_parent)
            except (OSError, RuntimeSnapshotError) as exc:
                if snapshot_cleanup_confirmed:
                    raise WorkBubblewrapSetupFailed(
                        "worker cleanup was confirmed but its runtime snapshot could not be removed",
                        monitor_pid=process.pid if process is not None else None,
                        cleanup_confirmed=True,
                    ) from exc


def _require_accepted_bwrap(
    path: str | os.PathLike[str], bwrap_sha256_digest: str
) -> _PinnedBubblewrap:
    if (
        not isinstance(bwrap_sha256_digest, str)
        or len(bwrap_sha256_digest) != 64
        or any(character not in "0123456789abcdef" for character in bwrap_sha256_digest)
    ):
        raise WorkBubblewrapSetupFailed(
            "Bubblewrap SHA-256 digest must be 64 lowercase hexadecimal characters"
        )
    try:
        executable = Path(os.path.abspath(os.fspath(path)))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise WorkBubblewrapSetupFailed(f"Bubblewrap path is invalid: {exc}") from exc
    if executable not in {_CANONICAL_SYSTEM_BWRAP, _DESIGNATED_BWRAP}:
        raise WorkBubblewrapSetupFailed(
            "Bubblewrap must use canonical /usr/bin/bwrap or the designated scratch fixture at "
            f"{_DESIGNATED_BWRAP}"
        )
    try:
        source_descriptor = os.open(
            executable,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
    except OSError as exc:
        raise WorkBubblewrapSetupFailed(f"Bubblewrap path is unavailable: {exc}") from exc

    execution_descriptor: int | None = None
    try:
        opened_stat = os.fstat(source_descriptor)
        named_stat = os.stat(executable, follow_symlinks=False)
        identity = _bubblewrap_file_identity(opened_stat)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or not opened_stat.st_mode & 0o111
            or opened_stat.st_mode & 0o022
            or identity != _bubblewrap_file_identity(named_stat)
        ):
            raise WorkBubblewrapSetupFailed(
                "designated Bubblewrap must be a stable, non-writable executable file"
            )
        if executable == _CANONICAL_SYSTEM_BWRAP:
            for directory in _TRUSTED_SYSTEM_BWRAP_PARENTS:
                parent_identity = os.stat(directory, follow_symlinks=False)
                if (
                    not stat.S_ISDIR(parent_identity.st_mode)
                    or parent_identity.st_uid != 0
                    or stat.S_IMODE(parent_identity.st_mode) & 0o022
                ):
                    raise WorkBubblewrapSetupFailed(
                        "canonical Bubblewrap parent ownership or permissions are unsafe"
                    )
            if opened_stat.st_uid != 0:
                raise WorkBubblewrapSetupFailed(
                    "canonical Bubblewrap executable must be root-owned"
                )
        _validate_elf_executable(source_descriptor)
        execution_descriptor = _create_sealed_bwrap_copy(
            source_descriptor,
            bwrap_sha256_digest,
        )
        pinned = _PinnedBubblewrap(
            executable,
            source_descriptor,
            execution_descriptor,
            identity,
        )
        _revalidate_pinned_bwrap(pinned)
        version = subprocess.run(
            [str(executable), "--version"],
            executable=pinned.proc_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
            close_fds=True,
            pass_fds=(execution_descriptor,),
            env={"LC_ALL": "C"},
        )
        _revalidate_pinned_bwrap(pinned)
        if version.returncode != 0 or version.stdout.strip() != _BWRAP_VERSION:
            raise WorkBubblewrapSetupFailed(
                f"Bubblewrap 0.13.0 is required; observed {version.stdout.strip()!r}"
            )
        return pinned
    except WorkBubblewrapCompositionError:
        _close_bubblewrap_descriptors(source_descriptor, execution_descriptor)
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        _close_bubblewrap_descriptors(source_descriptor, execution_descriptor)
        raise WorkBubblewrapSetupFailed(f"could not verify Bubblewrap executable: {exc}") from exc
    except BaseException:
        _close_bubblewrap_descriptors(source_descriptor, execution_descriptor)
        raise


def _create_sealed_bwrap_copy(source_descriptor: int, expected_digest: str) -> int:
    memfd_create = getattr(os, "memfd_create", None)
    allow_sealing = getattr(os, "MFD_ALLOW_SEALING", None)
    if not sys.platform.startswith("linux") or not callable(memfd_create) or allow_sealing is None:
        raise WorkBubblewrapSetupFailed(
            "Linux sealable memfd support is required for Bubblewrap execution"
        )
    try:
        execution_descriptor = memfd_create(
            "cao-t097-bubblewrap",
            allow_sealing | getattr(os, "MFD_CLOEXEC", 0) | getattr(os, "MFD_EXEC", 0x0010),
        )
    except OSError as exc:
        raise WorkBubblewrapSetupFailed(
            f"could not create sealable Bubblewrap memfd: {exc}"
        ) from exc

    try:
        size = os.fstat(source_descriptor).st_size
        if size < 0 or size > _BWRAP_HASH_MAX_BYTES:
            raise WorkBubblewrapSetupFailed("designated Bubblewrap exceeds the bounded digest size")
        digest = hashlib.sha256()
        offset = 0
        while offset < size:
            chunk = os.pread(
                source_descriptor,
                min(_BWRAP_HASH_CHUNK_BYTES, size - offset),
                offset,
            )
            if not chunk:
                raise WorkBubblewrapSetupFailed(
                    "designated Bubblewrap changed size while copying to memfd"
                )
            copied = 0
            while copied < len(chunk):
                written = os.write(execution_descriptor, chunk[copied:])
                if written <= 0:
                    raise WorkBubblewrapSetupFailed(
                        "could not finish copying Bubblewrap into its memfd"
                    )
                digest.update(chunk[copied : copied + written])
                copied += written
            offset += len(chunk)
        if os.pread(source_descriptor, 1, size) or os.fstat(source_descriptor).st_size != size:
            raise WorkBubblewrapSetupFailed(
                "designated Bubblewrap changed size while copying to memfd"
            )
        if digest.hexdigest() != expected_digest:
            raise WorkBubblewrapSetupFailed(
                "designated Bubblewrap SHA-256 digest does not match the trusted digest"
            )

        try:
            fcntl.fcntl(execution_descriptor, _F_ADD_SEALS, _BWRAP_REQUIRED_SEALS)
            observed_seals = fcntl.fcntl(execution_descriptor, _F_GET_SEALS)
        except OSError as exc:
            raise WorkBubblewrapSetupFailed(
                f"could not apply or verify Bubblewrap memfd seals: {exc}"
            ) from exc
        if observed_seals & _BWRAP_REQUIRED_SEALS != _BWRAP_REQUIRED_SEALS:
            raise WorkBubblewrapSetupFailed(
                "Bubblewrap memfd does not have the complete required seal mask"
            )
        return execution_descriptor
    except BaseException:
        try:
            os.close(execution_descriptor)
        except OSError:
            pass
        raise


def _close_bubblewrap_descriptors(*descriptors: int | None) -> None:
    errors: list[OSError] = []
    for descriptor in descriptors:
        if descriptor is None:
            continue
        try:
            os.close(descriptor)
        except OSError as exc:
            if exc.errno != errno.EBADF:
                errors.append(exc)
    if errors:
        raise WorkBubblewrapCleanupUncertain(
            "parent could not close every pinned Bubblewrap descriptor",
            cleanup_confirmed=False,
        ) from errors[0]


def _close_pinned_bwrap(pinned: _PinnedBubblewrap) -> None:
    _close_bubblewrap_descriptors(
        pinned.source_descriptor,
        pinned.execution_descriptor,
    )


def _bubblewrap_file_identity(file_stat: os.stat_result) -> tuple[int, ...]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        file_stat.st_uid,
        file_stat.st_gid,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _validate_elf_executable(descriptor: int) -> None:
    header = os.pread(descriptor, 64, 0)
    if (
        len(header) < 52
        or header[:4] != b"\x7fELF"
        or header[4] not in (1, 2)
        or header[5] not in (1, 2)
        or header[6] != 1
    ):
        raise WorkBubblewrapSetupFailed("designated Bubblewrap is not a valid ELF executable")
    byte_order = "<" if header[5] == 1 else ">"
    elf_type = struct.unpack_from(byte_order + "H", header, 16)[0]
    if elf_type not in (2, 3):
        raise WorkBubblewrapSetupFailed("designated Bubblewrap ELF has an invalid executable type")


def _revalidate_pinned_bwrap(pinned: _PinnedBubblewrap) -> None:
    try:
        opened_stat = os.fstat(pinned.source_descriptor)
        named_stat = os.stat(pinned.path, follow_symlinks=False)
    except OSError as exc:
        raise WorkBubblewrapSetupFailed(
            f"designated Bubblewrap identity could not be revalidated: {exc}"
        ) from exc
    if (
        _bubblewrap_file_identity(opened_stat) != pinned.identity
        or _bubblewrap_file_identity(named_stat) != pinned.identity
    ):
        raise WorkBubblewrapSetupFailed(
            "designated Bubblewrap changed after its accepted identity was captured"
        )


def _prepare_runtime_snapshot() -> tuple[Path, RuntimeSnapshot]:
    """Copy mutable interpreter, package, and dependency trees to private storage."""

    try:
        runtime_root = Path(sys.base_prefix).resolve(strict=True)
        package_root = Path(__file__).resolve().parents[1]
        dependency_root = Path(sysconfig.get_paths()["purelib"]).resolve(strict=True)
        dependency_entries = _python_bootstrap_dependency_entries(dependency_root)
        private_parent = Path(tempfile.mkdtemp(prefix="cao-t097-runtime-"))
    except (OSError, KeyError, RuntimeError, ValueError) as exc:
        raise WorkBubblewrapSetupFailed(f"runtime snapshot inputs are unavailable: {exc}") from exc

    try:
        snapshot = prepare_runtime_snapshot(
            runtime_root,
            package_root,
            dependency_root,
            parent=private_parent,
            runtime_entries=("bin", "lib"),
            deps_entries=dependency_entries,
        )
    except RuntimeSnapshotError as exc:
        try:
            os.rmdir(private_parent)
        except OSError:
            pass
        raise WorkBubblewrapSetupFailed(f"runtime snapshot was rejected: {exc}") from exc
    return private_parent, snapshot


def _python_bootstrap_dependency_entries(dependency_root: Path) -> tuple[str, ...]:
    """Select only Pydantic's runtime closure used by the Python setup bootstrap."""

    required = {
        "annotated_types",
        "pydantic",
        "pydantic_core",
        "typing_extensions.py",
        "typing_inspection",
    }
    try:
        observed = {path.name for path in dependency_root.iterdir()}
    except OSError as exc:
        raise WorkBubblewrapSetupFailed("Python dependency directory is unavailable") from exc
    missing = required - observed
    if missing:
        raise WorkBubblewrapSetupFailed(
            "Python bootstrap dependency is unavailable: " + ", ".join(sorted(missing))
        )

    distributions = {name.replace("_", "-") for name in required if not name.endswith(".py")}
    metadata = {
        name
        for name in observed
        if name.endswith(".dist-info")
        and any(
            name[: -len(".dist-info")].rsplit("-", maxsplit=1)[0].replace("_", "-") == distribution
            for distribution in distributions
        )
    }
    return tuple(sorted(required | metadata))


def _build_bwrap_argv(
    bwrap: Path,
    staged: StagedExecutable,
    identity: ExecutableIdentity,
    bwrap_executable_fd: int,
    info_fd: int,
    *,
    snapshot: RuntimeSnapshot,
    worker_socket_fd: int | None = None,
) -> list[str]:
    if not isinstance(snapshot, RuntimeSnapshot):
        raise TypeError("snapshot must be a RuntimeSnapshot")
    python_binary = Path(sys.executable).resolve(strict=True).name
    python_path = snapshot.runtime_path / "bin" / python_binary
    if not python_path.is_file() or not os.access(python_path, os.X_OK):
        raise WorkBubblewrapSetupFailed("snapshot Python runtime is unavailable or not executable")

    if worker_socket_fd is not None and (
        type(worker_socket_fd) is not int or not 3 <= worker_socket_fd <= 64
    ):
        raise ValueError("proxy worker descriptor is outside the bounded handoff range")
    argv = [
        str(bwrap),
        "--unshare-user",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-ipc",
        "--unshare-uts",
        "--die-with-parent",
        "--new-session",
        "--as-pid-1",
        "--info-fd",
        str(info_fd),
        "--clearenv",
        "--tmpfs",
        "/",
        "--dir",
        "/usr",
        "--ro-bind",
        "/usr",
        "/usr",
        "--dir",
        "/lib",
        "--ro-bind",
        "/lib",
        "/lib",
        "--dir",
        "/lib64",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--dir",
        "/runtime",
        "--ro-bind",
        str(snapshot.runtime_path),
        "/runtime",
        "--dir",
        "/src",
        "--ro-bind",
        str(snapshot.package_path),
        "/src/cli_agent_orchestrator",
        "--dir",
        "/deps",
        "--ro-bind",
        str(snapshot.deps_path),
        "/deps",
        "--tmpfs",
        "/tmp",
        "--proc",
        "/proc",
        "--dir",
        "/exec",
        "--dir",
        "/dev",
        "--dev",
        "/dev",
        *bubblewrap_bind_data_arguments(staged, _DESTINATION),
        "--setenv",
        "PYTHONHOME",
        "/runtime",
        "--setenv",
        "PYTHONPATH",
        "/src:/deps",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "LC_ALL",
        "C",
        "--chdir",
        "/tmp",
        "--",
        "/runtime/bin/" + python_binary,
        "-c",
        _BOOTSTRAP,
        str(staged.memfd_fd),
        json.dumps(identity.model_dump(mode="json"), sort_keys=True),
        str(bwrap_executable_fd),
        str(1 if worker_socket_fd is not None else 0),
    ]
    return argv


def _read_bwrap_info(info_fd: int, timeout_seconds: float) -> bytes:
    payload = bytearray()
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WorkBubblewrapSetupFailed("Bubblewrap namespace info timed out")
        ready, _, _ = select.select((info_fd,), (), (), remaining)
        if not ready:
            raise WorkBubblewrapSetupFailed("Bubblewrap namespace info timed out")
        chunk = os.read(info_fd, min(1024, _BWRAP_INFO_LIMIT + 1 - len(payload)))
        if not chunk:
            if payload:
                return bytes(payload)
            raise WorkBubblewrapSetupFailed("Bubblewrap closed its namespace info pipe empty")
        payload.extend(chunk)
        if len(payload) > _BWRAP_INFO_LIMIT:
            raise WorkBubblewrapSetupFailed("Bubblewrap namespace info exceeded its size bound")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkBubblewrapSetupFailed(
                f"Bubblewrap namespace info is not UTF-8: {exc}"
            ) from exc
        leading = len(text) - len(text.lstrip())
        try:
            _, end = json.JSONDecoder().raw_decode(text, leading)
        except json.JSONDecodeError:
            continue
        if text[end:].strip():
            raise WorkBubblewrapSetupFailed("Bubblewrap wrote extra data after namespace info")
        return text[leading:end].encode("utf-8")


def _parse_bwrap_info(raw_info: bytes) -> dict[str, int]:
    if not isinstance(raw_info, bytes) or not raw_info or len(raw_info) > _BWRAP_INFO_LIMIT:
        raise WorkBubblewrapSetupFailed("Bubblewrap namespace info is empty or oversized")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        parsed: dict[str, object] = {}
        for key, value in pairs:
            if key in parsed:
                raise ValueError(f"duplicate namespace info field: {key}")
            parsed[key] = value
        return parsed

    try:
        payload = json.loads(raw_info.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise WorkBubblewrapSetupFailed(f"Bubblewrap namespace info is malformed: {exc}") from exc
    allowed_fields = {
        "child-pid",
        "ipc-namespace",
        "mnt-namespace",
        "net-namespace",
        "pid-namespace",
        "uts-namespace",
    }
    if (
        not isinstance(payload, dict)
        or not {"child-pid", "pid-namespace"}.issubset(payload)
        or not set(payload).issubset(allowed_fields)
    ):
        raise WorkBubblewrapSetupFailed("Bubblewrap namespace info has unknown or missing fields")
    child_pid = payload["child-pid"]
    namespace_inode = payload["pid-namespace"]
    if (
        type(child_pid) is not int
        or child_pid <= 0
        or type(namespace_inode) is not int
        or namespace_inode <= 0
    ):
        raise WorkBubblewrapSetupFailed("Bubblewrap namespace info has invalid identity values")
    if any(type(value) is not int or value <= 0 for value in payload.values()):
        raise WorkBubblewrapSetupFailed("Bubblewrap namespace info has invalid namespace values")
    return {"child-pid": child_pid, "pid-namespace": namespace_inode}


def _proc_start_time(pid: int) -> int:
    stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    close = stat_text.rfind(")")
    if close < 0:
        raise ValueError("process stat record has no command terminator")
    fields = stat_text[close + 2 :].split()
    if len(fields) <= 19:
        raise ValueError("process stat record is incomplete")
    return int(fields[19])


def _proc_children(parent_pid: int) -> set[int]:
    children_path = Path(f"/proc/{parent_pid}/task/{parent_pid}/children")
    return {int(child) for child in children_path.read_text(encoding="ascii").split()}


def _read_namespace_init_identity(pid: int, monitor_pid: int) -> _NamespaceInitIdentity:
    stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    close = stat_text.rfind(")")
    if close < 0:
        raise ValueError("namespace init stat record has no command terminator")
    fields = stat_text[close + 2 :].split()
    if len(fields) <= 19:
        raise ValueError("namespace init stat record is incomplete")
    state = fields[0]
    parent_pid = int(fields[1])
    start_time = int(fields[19])
    if state in {"Z", "X", "x"} or parent_pid != monitor_pid:
        raise ValueError("reported namespace init is not a live direct child of Bubblewrap")

    status_text = Path(f"/proc/{pid}/status").read_text(encoding="ascii")
    nspid_lines = [line for line in status_text.splitlines() if line.startswith("NSpid:")]
    if len(nspid_lines) != 1:
        raise ValueError("namespace init has no unambiguous NSpid identity")
    namespace_pids = tuple(int(value) for value in nspid_lines[0].split(":", 1)[1].split())
    if not namespace_pids or namespace_pids[0] != pid or namespace_pids[-1] != 1:
        raise ValueError("reported child is not PID 1 in its innermost PID namespace")
    if pid not in _proc_children(monitor_pid):
        raise ValueError("reported namespace init is no longer a direct Bubblewrap child")
    namespace_inode = os.stat(f"/proc/{pid}/ns/pid").st_ino
    return _NamespaceInitIdentity(
        pid=pid,
        parent_pid=parent_pid,
        start_time=start_time,
        namespace_inode=namespace_inode,
        namespace_pids=namespace_pids,
    )


def _capture_bubblewrap_process_identity(
    process: subprocess.Popen[bytes],
    monitor_pidfd: int | None,
    init_pidfd: int | None,
    info_payload: dict[str, int],
    argv: list[str],
    accepted_executable_sha256: str,
) -> dict[str, object]:
    """Capture a checked process pair while both pidfds remain live and pinned."""
    if monitor_pidfd is None or init_pidfd is None or process.poll() is not None:
        raise ValueError("Bubblewrap process pair is no longer pinned and live")

    monitor_before = _proc_start_time(process.pid)
    init_before = _read_namespace_init_identity(info_payload["child-pid"], process.pid)
    if init_before.namespace_inode != info_payload["pid-namespace"]:
        raise ValueError("Bubblewrap PID namespace changed before identity capture")
    if _pidfd_target(monitor_pidfd) != process.pid or _pidfd_target(init_pidfd) != init_before.pid:
        raise ValueError("Bubblewrap pidfd target changed before identity capture")

    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    if not boot_id:
        raise ValueError("host boot ID is unavailable")
    namespaces: dict[str, list[int]] = {}
    for name in ("pid", "net", "ipc"):
        namespace = os.stat(f"/proc/{init_before.pid}/ns/{name}")
        namespaces[name] = [namespace.st_dev, namespace.st_ino]
    if namespaces["pid"][1] != init_before.namespace_inode:
        raise ValueError("Bubblewrap PID namespace changed during identity capture")

    expected_cmdline = b"\0".join(os.fsencode(argument) for argument in argv) + b"\0"
    if len(expected_cmdline) > 1024 * 1024:
        raise ValueError("Bubblewrap command line exceeds the bounded identity size")
    with Path(f"/proc/{process.pid}/cmdline").open("rb") as command_line:
        observed_cmdline = command_line.read(len(expected_cmdline) + 1)
    if observed_cmdline != expected_cmdline:
        raise ValueError("Bubblewrap command line differs from the accepted spawn arguments")

    executable_path = f"/proc/{process.pid}/exe"
    executable_fd = os.open(executable_path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        executable_stat = os.fstat(executable_fd)
        if not stat.S_ISREG(executable_stat.st_mode) or not (
            0 < executable_stat.st_size <= _BWRAP_HASH_MAX_BYTES
        ):
            raise ValueError("Bubblewrap monitor executable is not a bounded regular file")
        executable_hash = hashlib.sha256()
        remaining = executable_stat.st_size
        while remaining:
            chunk = os.read(executable_fd, min(remaining, _BWRAP_HASH_CHUNK_BYTES))
            if not chunk:
                raise ValueError("Bubblewrap monitor executable ended during hashing")
            executable_hash.update(chunk)
            remaining -= len(chunk)
        current_executable = os.stat(executable_path)
        if os.fstat(executable_fd).st_size != executable_stat.st_size or (
            current_executable.st_dev,
            current_executable.st_ino,
        ) != (executable_stat.st_dev, executable_stat.st_ino):
            raise ValueError("Bubblewrap monitor executable changed during hashing")
        observed_executable_sha256 = executable_hash.hexdigest()
    finally:
        os.close(executable_fd)
    if observed_executable_sha256 != accepted_executable_sha256:
        raise ValueError("Bubblewrap monitor does not execute the accepted binary")

    init_after = _read_namespace_init_identity(init_before.pid, process.pid)
    monitor_after = _proc_start_time(process.pid)
    if (
        process.poll() is not None
        or monitor_before != monitor_after
        or init_before != init_after
        or _pidfd_target(monitor_pidfd) != process.pid
        or _pidfd_target(init_pidfd) != init_before.pid
    ):
        raise ValueError("Bubblewrap process pair changed during identity capture")
    payload: dict[str, object] = {
        "version": 1,
        "kind": "bubblewrap",
        "boot_id": boot_id,
        "monitor_pid": process.pid,
        "monitor_start_time_ticks": monitor_after,
        "init_pid": init_after.pid,
        "init_start_time_ticks": init_after.start_time,
        "init_parent_pid": init_after.parent_pid,
        "pid_namespace": namespaces["pid"],
        "net_namespace": namespaces["net"],
        "ipc_namespace": namespaces["ipc"],
        "monitor_argv_sha256": hashlib.sha256(observed_cmdline).hexdigest(),
        "monitor_executable_sha256": observed_executable_sha256,
    }
    payload["identity_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    return payload


def _pidfd_target(pidfd: int) -> int:
    info_text = Path(f"/proc/self/fdinfo/{pidfd}").read_text(encoding="ascii")
    pid_lines = [line for line in info_text.splitlines() if line.startswith("Pid:")]
    if len(pid_lines) != 1:
        raise ValueError("pidfd has no unambiguous target identity")
    target_pid = int(pid_lines[0].split(":", 1)[1].strip())
    if target_pid <= 0:
        raise ValueError("pidfd target has exited before identity validation")
    return target_pid


def _open_monitor_pidfd(process: subprocess.Popen[bytes]) -> int | None:
    monitor_pidfd: int | None = None
    try:
        if process.poll() is not None:
            return None
        before = _proc_start_time(process.pid)
        monitor_pidfd = os.pidfd_open(process.pid)
        after = _proc_start_time(process.pid)
        if (
            process.poll() is not None
            or before != after
            or _pidfd_target(monitor_pidfd) != process.pid
        ):
            os.close(monitor_pidfd)
            monitor_pidfd = None
            return None
        return monitor_pidfd
    except (OSError, ValueError):
        if monitor_pidfd is not None:
            try:
                os.close(monitor_pidfd)
            except OSError:
                pass
        return None


def _open_namespace_init_pidfd(
    process: subprocess.Popen[bytes], info_payload: dict[str, int]
) -> int:
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise OSError("pidfd support is unavailable")
    pid = info_payload["child-pid"]
    expected_namespace_inode = info_payload["pid-namespace"]
    if process.poll() is not None:
        raise ProcessLookupError("Bubblewrap exited before its namespace init was pinned")
    monitor_start_before = _proc_start_time(process.pid)
    before = _read_namespace_init_identity(pid, process.pid)
    if before.namespace_inode != expected_namespace_inode:
        raise ValueError("reported PID namespace inode does not match the child")
    pidfd = os.pidfd_open(pid)
    try:
        after = _read_namespace_init_identity(pid, process.pid)
        monitor_start_after = _proc_start_time(process.pid)
        if (
            process.poll() is not None
            or monitor_start_before != monitor_start_after
            or before != after
            or after.namespace_inode != expected_namespace_inode
            or _pidfd_target(pidfd) != pid
        ):
            raise ValueError("namespace init identity changed while opening its pidfd")
    except BaseException:
        os.close(pidfd)
        raise
    return pidfd


def _wait_pidfd_readable(pidfd: int, timeout_seconds: float) -> bool:
    poller = select.poll()
    try:
        poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            timeout_ms = 0 if remaining <= 0 else max(1, math.ceil(remaining * 1000))
            events = poller.poll(timeout_ms)
            if events:
                if any(flags & (select.POLLERR | select.POLLNVAL) for _, flags in events):
                    return False
                return any(flags & (select.POLLIN | select.POLLHUP) for _, flags in events)
            if remaining <= 0:
                return False
    except (OSError, ValueError):
        return False


def _read_ack(
    process: subprocess.Popen[bytes], timeout_seconds: float
) -> tuple[dict[str, object], bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        raise _AckFailure("Bubblewrap ACK pipes are unavailable", b"", b"")
    stdout = bytearray()
    stderr = bytearray()
    streams = {process.stdout.fileno(): "stdout", process.stderr.fileno(): "stderr"}
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _AckFailure("child setup acknowledgement timed out", bytes(stdout), bytes(stderr))
        ready, _, _ = select.select(tuple(streams), (), (), remaining)
        if not ready:
            raise _AckFailure("child setup acknowledgement timed out", bytes(stdout), bytes(stderr))
        for descriptor in ready:
            chunk = os.read(descriptor, 4096)
            stream_name = streams[descriptor]
            if not chunk:
                del streams[descriptor]
                if stream_name == "stdout":
                    raise _AckFailure(
                        "child exited without a setup acknowledgement",
                        bytes(stdout),
                        bytes(stderr),
                    )
                continue
            if stream_name == "stderr":
                stderr.extend(chunk)
                if len(stderr) > _STDERR_LIMIT:
                    raise _AckFailure(
                        "child exceeded the setup diagnostic bound",
                        bytes(stdout),
                        bytes(stderr[:_STDERR_LIMIT]),
                    )
                continue
            stdout.extend(chunk)
            if len(stdout) > _ACK_LIMIT:
                raise _AckFailure(
                    "child exceeded the setup acknowledgement bound",
                    bytes(stdout[:_ACK_LIMIT]),
                    bytes(stderr),
                )
            newline = stdout.find(b"\n")
            if newline < 0:
                continue
            if newline + 1 != len(stdout):
                raise _AckFailure(
                    "child wrote output before the parent release gate",
                    bytes(stdout),
                    bytes(stderr),
                )
            line = bytes(stdout[:newline])
            if not line.startswith(_ACK_PREFIX):
                raise _AckFailure(
                    "child output did not begin with the setup acknowledgement",
                    bytes(stdout),
                    bytes(stderr),
                )
            try:
                payload = json.loads(line[len(_ACK_PREFIX) :])
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise _AckFailure(
                    f"child setup acknowledgement is malformed: {exc}",
                    bytes(stdout),
                    bytes(stderr),
                ) from exc
            if not isinstance(payload, dict):
                raise _AckFailure(
                    "child setup acknowledgement must be a JSON object",
                    bytes(stdout),
                    bytes(stderr),
                )
            return payload, b"", bytes(stderr)


def _parse_ack(
    payload: dict[str, object],
    identity: ExecutableIdentity,
    expected_size: int,
    monitor_pid: int,
    *,
    proxy_fd: int | None = None,
    proxy_socket_identity: tuple[int, int] | None = None,
) -> WorkBubblewrapAcknowledgement:
    expected_keys = {
        "destination",
        "sha256_digest",
        "mode",
        "size",
        "destination_device",
        "destination_inode",
        "open_fds",
        "landlock_abi",
        "seccomp_mode",
        "unlisted_path_denied",
        "unmounted_host_path_absent",
        "memfd_create_denied_errno",
        "connect_denied_errno",
        "proxy_socket_device",
        "proxy_socket_inode",
    }
    if set(payload) != expected_keys:
        raise ValueError("setup acknowledgement has an incomplete or unknown field set")
    expected_digest = identity.sha256_digest
    if (
        type(payload["destination"]) is not str
        or payload["destination"] != _DESTINATION
        or type(payload["sha256_digest"]) is not str
        or payload["sha256_digest"] != expected_digest
    ):
        raise ValueError("setup acknowledgement names a different destination or executable")
    if (
        type(payload["mode"]) is not int
        or payload["mode"] != 0o500
        or type(payload["size"]) is not int
        or payload["size"] != expected_size
        or type(payload["destination_device"]) is not int
        or payload["destination_device"] < 0
        or type(payload["destination_inode"]) is not int
        or payload["destination_inode"] <= 0
        or type(payload["landlock_abi"]) is not int
        or payload["landlock_abi"] < 1
        or type(payload["seccomp_mode"]) is not int
        or payload["seccomp_mode"] != 2
        or payload["unlisted_path_denied"] is not True
        or payload["unmounted_host_path_absent"] is not True
        or type(payload["memfd_create_denied_errno"]) is not int
        or payload["memfd_create_denied_errno"] != errno.EPERM
        or type(payload["connect_denied_errno"]) is not int
        or payload["connect_denied_errno"] != errno.EPERM
        or (
            proxy_socket_identity is None
            and (
                payload["proxy_socket_device"] is not None
                or payload["proxy_socket_inode"] is not None
            )
        )
        or (
            proxy_socket_identity is not None
            and (
                type(payload["proxy_socket_device"]) is not int
                or type(payload["proxy_socket_inode"]) is not int
                or (payload["proxy_socket_device"], payload["proxy_socket_inode"])
                != proxy_socket_identity
            )
        )
        or type(payload["open_fds"]) is not list
        or any(type(descriptor) is not int for descriptor in payload["open_fds"])
        or payload["open_fds"] != ([0, 1, 2] if proxy_fd is None else [0, 1, 2, proxy_fd])
    ):
        raise ValueError("setup acknowledgement did not prove the required child policy")
    return WorkBubblewrapAcknowledgement(
        destination=str(payload["destination"]),
        sha256_digest=str(payload["sha256_digest"]),
        mode=int(payload["mode"]),
        size=int(payload["size"]),
        destination_device=int(payload["destination_device"]),
        destination_inode=int(payload["destination_inode"]),
        open_fds=(0, 1, 2) if proxy_fd is None else (0, 1, 2, proxy_fd),
        landlock_abi=int(payload["landlock_abi"]),
        seccomp_mode=int(payload["seccomp_mode"]),
        unlisted_path_denied=True,
        unmounted_host_path_absent=True,
        memfd_create_denied_errno=errno.EPERM,
        connect_denied_errno=errno.EPERM,
        monitor_pid=monitor_pid,
        proxy_socket_device=(None if proxy_socket_identity is None else proxy_socket_identity[0]),
        proxy_socket_inode=(None if proxy_socket_identity is None else proxy_socket_identity[1]),
    )


def _socket_file_identity(descriptor: int) -> tuple[int, int]:
    item = os.fstat(descriptor)
    if not stat.S_ISSOCK(item.st_mode):
        raise WorkBubblewrapSetupFailed("proxy handoff descriptor is not a socket")
    return item.st_dev, item.st_ino


def _kill_and_reap(
    process: subprocess.Popen[bytes],
    init_pidfd: int | None,
    monitor_pidfd: int | None,
    *,
    init_resolution_attempted: bool = False,
    process_supervisor: WorkProcessSupervisor | None = None,
    supervised_attempt: WorkProcessAttempt | None = None,
) -> tuple[bytes, bytes, bool]:
    if (process_supervisor is None) != (supervised_attempt is None):
        raise ValueError("supervisor and adopted process attempt must be supplied together")
    if process_supervisor is not None and supervised_attempt is not None:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        state = process_supervisor.terminate(supervised_attempt, timeout=_CLEANUP_TIMEOUT_SECONDS)
        stdout = _read_available_pipe(process.stdout, _STDERR_LIMIT)
        stderr = _read_available_pipe(process.stderr, _STDERR_LIMIT)
        _close_process_pipes(process)
        return stdout, stderr, state is WorkProcessState.TERMINATED

    init_exited = False
    init_signal_succeeded = True
    init_termination_attempted = init_resolution_attempted or init_pidfd is not None
    if init_pidfd is not None:
        if _wait_pidfd_readable(init_pidfd, 0):
            init_exited = True
        else:
            try:
                signal.pidfd_send_signal(init_pidfd, signal.SIGKILL)
            except OSError:
                init_signal_succeeded = False
            if init_signal_succeeded:
                init_exited = _wait_pidfd_readable(init_pidfd, _CLEANUP_TIMEOUT_SECONDS)

    if process.stdin is not None:
        try:
            process.stdin.close()
        except OSError:
            pass

    monitor_signal_succeeded = True
    monitor_signal_sent = False
    if init_pidfd is None and init_resolution_attempted and monitor_pidfd is not None:
        try:
            signal.pidfd_send_signal(monitor_pidfd, signal.SIGKILL)
            monitor_signal_sent = True
        except OSError:
            monitor_signal_succeeded = False

    try:
        process.wait(timeout=_CLEANUP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        if init_termination_attempted and monitor_pidfd is not None and not monitor_signal_sent:
            try:
                signal.pidfd_send_signal(monitor_pidfd, signal.SIGKILL)
                monitor_signal_sent = True
            except OSError:
                monitor_signal_succeeded = False
        try:
            process.wait(timeout=_CLEANUP_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            _close_process_pipes(process)
            return b"", b"", False
    except OSError:
        _close_process_pipes(process)
        return b"", b"", False

    stdout = _read_available_pipe(process.stdout, _STDERR_LIMIT)
    stderr = _read_available_pipe(process.stderr, _STDERR_LIMIT)
    _close_process_pipes(process)
    monitor_reaped = process.returncode is not None
    if init_pidfd is not None and not init_exited:
        init_exited = _wait_pidfd_readable(init_pidfd, 0)
    cleanup_confirmed = (
        init_pidfd is not None
        and init_exited
        and init_signal_succeeded
        and monitor_reaped
        and monitor_signal_succeeded
    )
    return stdout, stderr, cleanup_confirmed


def _read_available_pipe(stream, maximum: int) -> bytes:
    if stream is None:
        return b""
    descriptor = stream.fileno()
    chunks = bytearray()
    try:
        os.set_blocking(descriptor, False)
        while len(chunks) <= maximum:
            ready, _, _ = select.select((descriptor,), (), (), 0)
            if not ready:
                break
            chunk = os.read(descriptor, min(4096, maximum + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
    except (OSError, ValueError):
        pass
    return bytes(chunks[:maximum])


def _close_process_pipes(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


def _setup_failure(
    message: str,
    process: subprocess.Popen[bytes],
    stdout: bytes,
    stderr: bytes,
    cleanup_confirmed: bool,
) -> WorkBubblewrapCompositionError:
    error_type = WorkBubblewrapSetupFailed if cleanup_confirmed else WorkBubblewrapCleanupUncertain
    return error_type(
        message,
        monitor_pid=process.pid,
        stdout=stdout,
        stderr=stderr,
        cleanup_confirmed=cleanup_confirmed,
    )
