"""Own one Work command inside isolated Linux namespaces.

Process groups are not a cleanup boundary: a worker can call ``setsid()`` and
double-fork.  This supervisor makes the command PID 1 of a fresh PID namespace.
The kernel forbids moving back to an ancestor PID namespace; when its PID 1 exits,
the kernel SIGKILLs and waits for the rest of that namespace to disappear.  The
``unshare --kill-child`` monitor waits for PID 1, so its exit is the cleanup
proof used by :meth:`wait`.

This provides process-lifecycle containment and per-attempt network and IPC
namespaces and a narrow seccomp kernel-surface layer. Seccomp permits ``execve``
and does not enforce executable-path allowlisting or restrict loader behavior.
This does not isolate filesystem, credentials, filesystem-backed Unix sockets,
or effects outside this process tree. An UNCERTAIN result must be retained by
the caller's durable attempt/admission lifecycle, including the serialized
process identity needed to reattach after controller restart. Identity protocol
v1 does not support cleanup-only reattachment and is rejected; draining or
otherwise cleaning up v1 attempts remains an open T097 compatibility gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Sequence, TypeGuard, cast


class WorkProcessState(str, Enum):
    """Observed state of a single attempt's process tree."""

    STARTING = "starting"
    RUNNING = "running"
    TERMINATED = "terminated"
    UNCERTAIN = "uncertain"


class WorkProcessIsolationUnavailable(RuntimeError):
    """The host could not prove the required isolated launch before running work."""


class WorkProcessIdentityProtocolIncompatible(WorkProcessIsolationUnavailable):
    """A persisted identity uses a bootstrap protocol this supervisor cannot reattach."""


class WorkProcessSupervisorBlocked(RuntimeError):
    """This supervisor owns an attempt that cannot safely be started again."""


class WorkProcessStartUncertain(WorkProcessIsolationUnavailable):
    """Bootstrap failed and its cleanup could not be confirmed."""

    def __init__(self, message: str, attempt: WorkProcessAttempt) -> None:
        super().__init__(message)
        self.attempt = attempt


_WORK_PROCESS_PROTOCOL_VERSION = 2
_WORK_PROCESS_DURABLE_VERSION = 3


def _monitor_argv_prefix_sha256(argv: tuple[str, ...]) -> str:
    """Hash only fixed supervisor arguments, excluding the worker command tail."""

    try:
        separator = argv.index("--")
    except ValueError as exc:
        raise ValueError("process identity monitor argv has no command separator") from exc
    prefix = argv[: separator + 7]
    canonical = json.dumps(list(prefix), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _canonical_identity_sha256(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class WorkProcessIdentity:
    """Serializable identity for reattaching to one unshare monitor and its PID 1."""

    boot_id: str
    monitor_pid: int
    monitor_start_time_ticks: int
    init_pid: int
    init_start_time_ticks: int
    init_parent_pid: int
    pid_namespace: tuple[int, int]
    net_namespace: tuple[int, int]
    ipc_namespace: tuple[int, int]
    monitor_argv: tuple[str, ...]
    version: int = _WORK_PROCESS_PROTOCOL_VERSION
    monitor_argv_prefix_sha256: str | None = None
    identity_sha256: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-compatible shape represented by this identity version."""

        result: dict[str, object] = {
            "version": self.version,
            "boot_id": self.boot_id,
            "monitor_pid": self.monitor_pid,
            "monitor_start_time_ticks": self.monitor_start_time_ticks,
            "init_pid": self.init_pid,
            "init_start_time_ticks": self.init_start_time_ticks,
            "init_parent_pid": self.init_parent_pid,
            "pid_namespace": list(self.pid_namespace),
            "net_namespace": list(self.net_namespace),
            "ipc_namespace": list(self.ipc_namespace),
        }
        if self.version == _WORK_PROCESS_DURABLE_VERSION:
            result["monitor_argv_prefix_sha256"] = self.monitor_argv_prefix_sha256
            result["identity_sha256"] = self.identity_sha256
        else:
            result["monitor_argv"] = list(self.monitor_argv)
        return result

    def to_durable_dict(self) -> dict[str, object]:
        """Return the privacy-preserving v3 shape for durable storage."""

        if self.version == _WORK_PROCESS_DURABLE_VERSION:
            result = self.to_dict()
            digest = result.pop("identity_sha256")
            if not _is_sha256_hex(digest) or digest != _canonical_identity_sha256(result):
                raise ValueError("process identity digest does not match its fields")
            return {**result, "identity_sha256": digest}
        prefix_digest = _monitor_argv_prefix_sha256(self.monitor_argv)
        result = {
            "version": _WORK_PROCESS_DURABLE_VERSION,
            "boot_id": self.boot_id,
            "monitor_pid": self.monitor_pid,
            "monitor_start_time_ticks": self.monitor_start_time_ticks,
            "init_pid": self.init_pid,
            "init_start_time_ticks": self.init_start_time_ticks,
            "init_parent_pid": self.init_parent_pid,
            "pid_namespace": list(self.pid_namespace),
            "net_namespace": list(self.net_namespace),
            "ipc_namespace": list(self.ipc_namespace),
            "monitor_argv_prefix_sha256": prefix_digest,
        }
        return {**result, "identity_sha256": _canonical_identity_sha256(result)}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> WorkProcessIdentity:
        """Parse the exact versioned identity shape; malformed data fails closed."""

        common_keys = {
            "version",
            "boot_id",
            "monitor_pid",
            "monitor_start_time_ticks",
            "init_pid",
            "init_start_time_ticks",
            "init_parent_pid",
            "pid_namespace",
            "net_namespace",
            "ipc_namespace",
        }
        try:
            version = value["version"]
        except KeyError as exc:
            raise ValueError("process identity has an incomplete or unknown field set") from exc
        if type(version) is not int:
            raise ValueError("process identity numeric fields must be integers")
        if version == 1:
            raise WorkProcessIdentityProtocolIncompatible(
                "identity protocol v1 is incompatible with the seccomp ACK bootstrap; "
                "legacy v1 identities have no cleanup-only reattach path yet "
                "(open T097 compatibility gate)"
            )
        if version == _WORK_PROCESS_PROTOCOL_VERSION:
            expected_keys = common_keys | {"monitor_argv"}
        elif version == _WORK_PROCESS_DURABLE_VERSION:
            expected_keys = common_keys | {
                "monitor_argv_prefix_sha256",
                "identity_sha256",
            }
        else:
            raise WorkProcessIdentityProtocolIncompatible(
                f"identity protocol version {version} is unsupported; "
                f"expected v{_WORK_PROCESS_PROTOCOL_VERSION} or "
                f"v{_WORK_PROCESS_DURABLE_VERSION}"
            )
        if set(value) != expected_keys:
            raise ValueError("process identity has an incomplete or unknown field set")
        try:
            boot_id = value["boot_id"]
            monitor_pid = value["monitor_pid"]
            monitor_start = value["monitor_start_time_ticks"]
            init_pid = value["init_pid"]
            init_start = value["init_start_time_ticks"]
            init_parent = value["init_parent_pid"]
            monitor_argv = value.get("monitor_argv", ())
            monitor_argv_prefix_sha256 = value.get("monitor_argv_prefix_sha256")
            identity_sha256 = value.get("identity_sha256")
            namespaces = tuple(
                cls._parse_namespace(value[key])
                for key in ("pid_namespace", "net_namespace", "ipc_namespace")
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("process identity contains malformed fields") from exc

        if (
            type(monitor_pid) is not int
            or type(monitor_start) is not int
            or type(init_pid) is not int
            or type(init_start) is not int
            or type(init_parent) is not int
        ):
            raise ValueError("process identity numeric fields must be integers")
        if (
            not isinstance(boot_id, str)
            or not boot_id.strip()
            or (version == _WORK_PROCESS_DURABLE_VERSION and "\x00" in boot_id)
        ):
            raise ValueError("process identity boot ID is invalid")
        if monitor_pid <= 0 or init_pid <= 0 or monitor_pid == init_pid:
            raise ValueError("process identity PIDs are invalid")
        if monitor_start <= 0 or init_start <= 0 or init_parent != monitor_pid:
            raise ValueError("process identity process relationship is invalid")
        if version == _WORK_PROCESS_PROTOCOL_VERSION:
            if (
                not isinstance(monitor_argv, (list, tuple))
                or not monitor_argv
                or any(
                    not isinstance(argument, str) or "\x00" in argument for argument in monitor_argv
                )
            ):
                raise ValueError("process identity monitor argv is invalid")
        elif not _is_sha256_hex(monitor_argv_prefix_sha256):
            raise ValueError("process identity supervisor-prefix digest is invalid")
        elif not _is_sha256_hex(identity_sha256):
            raise ValueError("process identity digest is invalid")

        if version == _WORK_PROCESS_DURABLE_VERSION:
            canonical_payload = {
                "version": version,
                "boot_id": boot_id,
                "monitor_pid": monitor_pid,
                "monitor_start_time_ticks": monitor_start,
                "init_pid": init_pid,
                "init_start_time_ticks": init_start,
                "init_parent_pid": init_parent,
                "pid_namespace": list(namespaces[0]),
                "net_namespace": list(namespaces[1]),
                "ipc_namespace": list(namespaces[2]),
                "monitor_argv_prefix_sha256": monitor_argv_prefix_sha256,
            }
            if identity_sha256 != _canonical_identity_sha256(canonical_payload):
                raise ValueError("process identity digest does not match its fields")

        return cls(
            boot_id=boot_id,
            monitor_pid=monitor_pid,
            monitor_start_time_ticks=monitor_start,
            init_pid=init_pid,
            init_start_time_ticks=init_start,
            init_parent_pid=init_parent,
            pid_namespace=namespaces[0],
            net_namespace=namespaces[1],
            ipc_namespace=namespaces[2],
            monitor_argv=(
                tuple(cast(Sequence[str], monitor_argv))
                if version == _WORK_PROCESS_PROTOCOL_VERSION
                else ()
            ),
            version=version,
            monitor_argv_prefix_sha256=(
                cast(str, monitor_argv_prefix_sha256)
                if version == _WORK_PROCESS_DURABLE_VERSION
                else None
            ),
            identity_sha256=(
                cast(str, identity_sha256) if version == _WORK_PROCESS_DURABLE_VERSION else None
            ),
        )

    @staticmethod
    def _parse_namespace(value: object) -> tuple[int, int]:
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 2
            or any(type(component) is not int or component < 0 for component in value)
        ):
            raise ValueError("process identity namespace ID is invalid")
        return value[0], value[1]


def _is_sha256_hex(value: object) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _matches_live_identity(expected: WorkProcessIdentity, live: WorkProcessIdentity) -> bool:
    stable_fields = (
        "boot_id",
        "monitor_pid",
        "monitor_start_time_ticks",
        "init_pid",
        "init_start_time_ticks",
        "init_parent_pid",
        "pid_namespace",
        "net_namespace",
        "ipc_namespace",
    )
    if any(getattr(expected, field) != getattr(live, field) for field in stable_fields):
        return False
    if expected.version == _WORK_PROCESS_PROTOCOL_VERSION:
        return expected.monitor_argv == live.monitor_argv
    if expected.version == _WORK_PROCESS_DURABLE_VERSION:
        return _monitor_argv_prefix_sha256(live.monitor_argv) == (
            expected.monitor_argv_prefix_sha256
        )
    return False


@dataclass
class WorkProcessAttempt:
    """Live handle returned by ``start`` or ``reattach``; keep until exit is proven."""

    command: tuple[str, ...]
    monitor_pid: int
    _state: WorkProcessState
    _monitor: subprocess.Popen[bytes] | None = field(repr=False)
    _pidfd: int | None = field(default=None, repr=False)
    _monitor_pidfd: int | None = field(default=None, repr=False)
    _gate_fd: int | None = field(default=None, repr=False)
    _ack_fd: int | None = field(default=None, repr=False)
    _identity: WorkProcessIdentity | None = field(default=None, repr=False)
    _external_process_identity: Mapping[str, object] | None = field(default=None, repr=False)
    _gate_released: bool = False
    returncode: int | None = None
    reason: str | None = None
    _wait_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @property
    def state(self) -> WorkProcessState:
        """Read-only lifecycle state; only the supervisor can confirm termination."""

        return self._state


_BOOTSTRAP = r"""import os, sys

gate_fd = int(sys.argv[1])
ack_fd = int(sys.argv[2])
argv = sys.argv[3:]

def wait_for_controller_abort():
    while True:
        try:
            if not os.read(gate_fd, 1):
                os._exit(125)
        except OSError:
            os._exit(125)

def send_ack(value):
    try:
        return os.write(ack_fd, value) == 1
    except BaseException:
        return False

if not argv:
    send_ack(b"E")
    wait_for_controller_abort()

try:
    if os.read(gate_fd, 1) != b"I":
        raise RuntimeError("controller did not request seccomp installation")
    from cli_agent_orchestrator.services.work_process_seccomp import (
        install_work_process_seccomp_filter,
    )
    install_work_process_seccomp_filter()
except BaseException:
    send_ack(b"E")
    wait_for_controller_abort()

if not send_ack(b"S"):
    wait_for_controller_abort()

try:
    release = os.read(gate_fd, 1)
except OSError:
    os._exit(125)
os.close(gate_fd)
os.close(ack_fd)
if release == b"R":
    os.execvpe(argv[0], argv, os.environ)
os._exit(125)
"""


class WorkProcessSupervisor:
    """Launch, wait for, and terminate one isolated process-tree attempt.

    A fresh instance represents a fresh attempt.  It is intentionally one-shot:
    a caller cannot turn cleanup into an automatic re-delivery by calling
    ``start`` again on the same supervisor. Hosts without an executable
    ``unshare``, usable user/network/IPC/PID namespaces, pidfds, or procfs
    evidence reject before the requested command is released from its startup
    gate.
    """

    def __init__(
        self,
        *,
        unshare_path: str | None = None,
        capability_timeout: float = 3.0,
        startup_timeout: float = 3.0,
        cleanup_timeout: float = 5.0,
        poll_interval: float = 0.01,
    ) -> None:
        if capability_timeout <= 0 or startup_timeout <= 0 or cleanup_timeout < 0:
            raise ValueError("timeouts must be positive (cleanup_timeout may be zero)")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self._unshare_setting = unshare_path
        self._capability_timeout = capability_timeout
        self._startup_timeout = startup_timeout
        self._cleanup_timeout = cleanup_timeout
        self._poll_interval = poll_interval
        self._attempt: WorkProcessAttempt | None = None
        self._lock = threading.RLock()

    @property
    def attempt(self) -> WorkProcessAttempt | None:
        """The owned attempt, retained even after an uncertain cleanup result."""

        with self._lock:
            return self._attempt

    @property
    def is_blocked(self) -> bool:
        """Whether this instance has unresolved cleanup that blocks a new start."""

        with self._lock:
            return self._attempt is not None and self._attempt.state is WorkProcessState.UNCERTAIN

    def start(
        self,
        command: Sequence[str],
        *,
        persist_identity: Callable[[WorkProcessIdentity], None],
        cwd: str | os.PathLike[str] | None = None,
    ) -> WorkProcessAttempt:
        """Start only after the callback durably commits identity and returns.

        The callback runs with the command still behind its startup gate. If it
        raises, startup aborts and cleanup must still prove both processes gone.
        """

        if not callable(persist_identity):
            raise TypeError("persist_identity must be a durable identity callback")
        if isinstance(command, (str, bytes)):
            raise ValueError("command must be a sequence of arguments, not a string")
        argv = tuple(command)
        if not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv):
            raise ValueError("command must be a non-empty sequence of NUL-free strings")
        executable = argv[0]
        if (
            not os.path.isabs(executable)
            or executable.startswith("//")
            or os.path.normpath(executable) != executable
        ):
            raise ValueError("command requires a canonical absolute executable path")

        with self._lock:
            if self._attempt is not None:
                if self._attempt.state is WorkProcessState.UNCERTAIN:
                    raise WorkProcessSupervisorBlocked(
                        "cleanup is UNCERTAIN; reconcile the owned attempt before any new start"
                    )
                raise WorkProcessSupervisorBlocked(
                    "this supervisor is one-shot and already owns an attempt"
                )

            unshare = self._require_capabilities()
            try:
                read_fd, gate_fd = os.pipe2(os.O_CLOEXEC)
            except OSError as exc:
                raise WorkProcessIsolationUnavailable(
                    f"could not create isolated command startup gate: {exc}"
                ) from exc
            try:
                ack_fd, ack_write_fd = os.pipe2(os.O_CLOEXEC)
            except OSError as exc:
                self._close_fd(read_fd)
                self._close_fd(gate_fd)
                raise WorkProcessIsolationUnavailable(
                    f"could not create seccomp startup acknowledgement pipe: {exc}"
                ) from exc
            launch = [
                unshare,
                "--user",
                "--map-root-user",
                "--net",
                "--ipc",
                "--pid",
                "--fork",
                "--kill-child=SIGKILL",
                "--",
                sys.executable,
                "-I",
                "-c",
                _BOOTSTRAP,
                str(read_fd),
                str(ack_write_fd),
                *argv,
            ]
            try:
                monitor = subprocess.Popen(
                    launch,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    # Do not expose server, operator, or provider variables to
                    # agent-visible code. No caller-supplied env is accepted
                    # until a validated worker environment contract exists.
                    env={},
                    pass_fds=(read_fd, ack_write_fd),
                    close_fds=True,
                )
            except OSError as exc:
                self._close_fd(gate_fd)
                self._close_fd(ack_fd)
                raise WorkProcessIsolationUnavailable(
                    f"could not start unshare PID-namespace monitor: {exc}"
                ) from exc
            except BaseException:
                self._close_fd(gate_fd)
                self._close_fd(ack_fd)
                raise
            finally:
                self._close_fd(read_fd)
                self._close_fd(ack_write_fd)

            attempt = WorkProcessAttempt(
                command=argv,
                monitor_pid=monitor.pid,
                _state=WorkProcessState.STARTING,
                _monitor=monitor,
                _gate_fd=gate_fd,
                _ack_fd=ack_fd,
            )
            self._attempt = attempt

            try:
                init_pid = self._wait_for_namespace_init(attempt)
                try:
                    before = self._read_process_identity(
                        monitor.pid, init_pid, strict_monitor_argv=False
                    )
                except OSError as exc:
                    self._abort_start(
                        attempt,
                        f"could not capture process identity through procfs: {exc}",
                    )
                except WorkProcessIsolationUnavailable as exc:
                    self._abort_start(attempt, str(exc))
                try:
                    attempt._monitor_pidfd = os.pidfd_open(monitor.pid)
                    attempt._pidfd = os.pidfd_open(init_pid)
                except OSError as exc:
                    if attempt._monitor_pidfd is not None and attempt._pidfd is None:
                        try:
                            partial_after = self._read_process_identity(
                                monitor.pid, init_pid, strict_monitor_argv=False
                            )
                        except (OSError, WorkProcessIsolationUnavailable):
                            pass
                        else:
                            if (
                                before == partial_after
                                and self._pidfd_is_readable(attempt._monitor_pidfd) is False
                            ):
                                attempt._identity = partial_after
                    self._abort_start(
                        attempt,
                        f"could not pin unshare monitor and namespace PID 1 with pidfds: {exc}",
                    )
                try:
                    after = self._read_process_identity(
                        monitor.pid, init_pid, strict_monitor_argv=False
                    )
                except OSError as exc:
                    self._abort_start(
                        attempt,
                        f"could not recheck process identity through procfs: {exc}",
                    )
                except WorkProcessIsolationUnavailable as exc:
                    self._abort_start(attempt, str(exc))
                if before != after:
                    self._abort_start(attempt, "process identity changed while opening pidfds")
                attempt._identity = after
                if after.monitor_argv != tuple(launch):
                    self._abort_start(attempt, "unshare monitor argv changed during startup")
                valid, reason = self._verify_process_isolation(after)
                if not valid:
                    self._abort_start(attempt, reason)
                if (
                    self._pidfd_is_readable(attempt._monitor_pidfd) is not False
                    or self._pidfd_is_readable(attempt._pidfd) is not False
                ):
                    self._abort_start(attempt, "process exited before its identity was persisted")

                try:
                    persist_identity(after)
                except BaseException as exc:
                    self._abort_start(attempt, f"could not durably persist process identity: {exc}")

                if (
                    self._pidfd_is_readable(attempt._monitor_pidfd) is not False
                    or self._pidfd_is_readable(attempt._pidfd) is not False
                ):
                    self._abort_start(attempt, "process exited while its identity was persisted")

                live_gate_fd = attempt._gate_fd
                assert live_gate_fd is not None
                try:
                    written = os.write(live_gate_fd, b"I")
                except OSError as exc:
                    self._abort_start(attempt, f"could not request seccomp installation: {exc}")
                if written != 1:
                    self._abort_start(attempt, "seccomp install request was not accepted")

                live_ack_fd = attempt._ack_fd
                assert live_ack_fd is not None
                deadline = time.monotonic() + self._startup_timeout
                poller = select.poll()
                poller.register(live_ack_fd, select.POLLIN | select.POLLHUP | select.POLLERR)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._abort_start(
                            attempt,
                            "timed out waiting for seccomp installation acknowledgement",
                        )
                    try:
                        events = poller.poll(max(1, int(remaining * 1000 + 0.999)))
                    except InterruptedError:
                        continue
                    except OSError as exc:
                        self._abort_start(
                            attempt,
                            f"could not wait for seccomp installation acknowledgement: {exc}",
                        )
                    if not events:
                        continue
                    if any(flags & (select.POLLERR | select.POLLNVAL) for _, flags in events):
                        self._abort_start(
                            attempt,
                            "seccomp installation acknowledgement poll reported an error",
                        )
                    if not any(flags & (select.POLLIN | select.POLLHUP) for _, flags in events):
                        self._abort_start(
                            attempt,
                            "seccomp installation acknowledgement poll reported no readable event",
                        )
                    try:
                        acknowledgement = os.read(live_ack_fd, 1)
                    except OSError as exc:
                        self._abort_start(
                            attempt,
                            f"could not read seccomp installation acknowledgement: {exc}",
                        )
                    if acknowledgement == b"E":
                        self._abort_start(
                            attempt,
                            "isolated bootstrap reported seccomp import or installation failure",
                        )
                    if acknowledgement != b"S":
                        self._abort_start(
                            attempt,
                            "isolated bootstrap closed the seccomp acknowledgement pipe",
                        )
                    break
                self._close_ack_fd(attempt)

                try:
                    after_install = self._read_process_identity(monitor.pid, init_pid)
                except OSError as exc:
                    self._abort_start(
                        attempt,
                        f"could not recheck process identity after seccomp installation: {exc}",
                    )
                except WorkProcessIsolationUnavailable as exc:
                    self._abort_start(attempt, str(exc))
                if after_install != after:
                    self._abort_start(
                        attempt,
                        "process identity changed after seccomp installation",
                    )
                valid, reason = self._verify_process_isolation(after_install)
                if not valid:
                    self._abort_start(attempt, reason)
                if (
                    self._pidfd_is_readable(attempt._monitor_pidfd) is not False
                    or self._pidfd_is_readable(attempt._pidfd) is not False
                ):
                    self._abort_start(attempt, "process exited before command release")

                # Be conservative across an interruption around the final pipe
                # write: cleanup must assume the command may have received it.
                attempt._gate_released = True
                try:
                    written = os.write(gate_fd, b"R")
                except OSError as exc:
                    self._abort_start(attempt, f"could not release isolated command gate: {exc}")
                finally:
                    self._close_gate(attempt)
                if written != 1:
                    self._abort_start(attempt, "isolated command gate accepted no release token")
                attempt._state = WorkProcessState.RUNNING
                return attempt
            except WorkProcessStartUncertain:
                raise
            except WorkProcessIsolationUnavailable:
                raise
            except BaseException as exc:
                self._abort_start(attempt, f"PID-namespace startup failed: {exc}")
                raise AssertionError("_abort_start always raises")

    def reattach(
        self,
        identity: WorkProcessIdentity | Mapping[str, object],
    ) -> WorkProcessAttempt:
        """Reopen a live monitor only when its complete persisted identity matches."""

        if isinstance(identity, WorkProcessIdentity):
            expected = WorkProcessIdentity.from_dict(identity.to_dict())
        elif isinstance(identity, Mapping):
            expected = WorkProcessIdentity.from_dict(identity)
        else:
            raise ValueError("process identity must be a WorkProcessIdentity or mapping")

        with self._lock:
            if self._attempt is not None:
                raise WorkProcessSupervisorBlocked(
                    "this supervisor is one-shot and already owns an attempt"
                )
            self._require_pidfd_support()
            try:
                before = self._read_process_identity(expected.monitor_pid, expected.init_pid)
            except (OSError, WorkProcessIsolationUnavailable) as exc:
                raise WorkProcessIsolationUnavailable(
                    f"cannot verify persisted Work process identity: {exc}"
                ) from exc
            if not _matches_live_identity(expected, before):
                raise WorkProcessIsolationUnavailable(
                    "persisted Work process identity does not match the live processes"
                )

            monitor_pidfd: int | None = None
            init_pidfd: int | None = None
            try:
                monitor_pidfd = os.pidfd_open(expected.monitor_pid)
                init_pidfd = os.pidfd_open(expected.init_pid)
                after = self._read_process_identity(expected.monitor_pid, expected.init_pid)
                if before != after or not _matches_live_identity(expected, after):
                    raise WorkProcessIsolationUnavailable(
                        "Work process identity changed while opening pidfds"
                    )
                valid, reason = self._verify_process_isolation(after)
                if not valid:
                    raise WorkProcessIsolationUnavailable(reason)
                if (
                    self._pidfd_is_readable(monitor_pidfd) is not False
                    or self._pidfd_is_readable(init_pidfd) is not False
                ):
                    raise WorkProcessIsolationUnavailable(
                        "Work process exited while its pidfds were being opened"
                    )
            except OSError as exc:
                self._close_fd(monitor_pidfd)
                self._close_fd(init_pidfd)
                raise WorkProcessIsolationUnavailable(
                    f"could not pin persisted Work processes with pidfds: {exc}"
                ) from exc
            except BaseException:
                self._close_fd(monitor_pidfd)
                self._close_fd(init_pidfd)
                raise

            assert monitor_pidfd is not None and init_pidfd is not None
            command_start = before.monitor_argv.index("--") + 7
            attempt = WorkProcessAttempt(
                command=before.monitor_argv[command_start:],
                monitor_pid=expected.monitor_pid,
                _state=WorkProcessState.RUNNING,
                _monitor=None,
                _pidfd=init_pidfd,
                _monitor_pidfd=monitor_pidfd,
                _identity=expected,
            )
            self._attempt = attempt
            return attempt

    def adopt_bubblewrap_process_tree(
        self,
        process,
        monitor_pidfd: int,
        init_pidfd: int,
        identity: Mapping[str, object],
    ) -> WorkProcessAttempt:
        """Adopt a freshly captured Bubblewrap pair for common wait/kill ownership.

        The composition captures the Bubblewrap-specific argv and executable
        digest. The supervisor independently binds duplicate pidfds to the
        durable PID/starttime/namespace identity before it takes ownership.
        The caller retains its descriptors for ACK parsing and isolation proofs.
        """
        from cli_agent_orchestrator.clients.work_repository import (
            _validate_bubblewrap_identity,
        )

        normalized, _ = _validate_bubblewrap_identity(identity)
        if (
            process is None
            or type(getattr(process, "pid", None)) is not int
            or process.pid != normalized["monitor_pid"]
            or not callable(getattr(process, "poll", None))
            or process.poll() is not None
            or type(monitor_pidfd) is not int
            or type(init_pidfd) is not int
            or monitor_pidfd == init_pidfd
        ):
            raise WorkProcessIsolationUnavailable("Bubblewrap process pair is not live and pinned")
        try:
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
            monitor_parent, monitor_start, monitor_state = self._read_proc_stat(process.pid)
            init_parent, init_start, init_state = self._read_proc_stat(normalized["init_pid"])
            pidfd_targets = (
                self._pidfd_target(monitor_pidfd),
                self._pidfd_target(init_pidfd),
            )
            namespaces = {
                name: list(
                    (
                        os.stat(f"/proc/{normalized['init_pid']}/ns/{name}").st_dev,
                        os.stat(f"/proc/{normalized['init_pid']}/ns/{name}").st_ino,
                    )
                )
                for name in ("pid", "net", "ipc")
            }
            if (
                boot_id != normalized["boot_id"]
                or monitor_parent <= 0
                or monitor_start != normalized["monitor_start_time_ticks"]
                or monitor_state in {"Z", "X", "x"}
                or init_parent != process.pid
                or init_start != normalized["init_start_time_ticks"]
                or init_state in {"Z", "X", "x"}
                or pidfd_targets != (process.pid, normalized["init_pid"])
                or namespaces
                != {
                    "pid": normalized["pid_namespace"],
                    "net": normalized["net_namespace"],
                    "ipc": normalized["ipc_namespace"],
                }
                or self._pidfd_is_readable(monitor_pidfd) is not False
                or self._pidfd_is_readable(init_pidfd) is not False
            ):
                raise ValueError("Bubblewrap process pair changed before supervisor adoption")
            duplicate_monitor_fd = os.dup(monitor_pidfd)
            duplicate_init_fd = os.dup(init_pidfd)
            os.set_inheritable(duplicate_monitor_fd, False)
            os.set_inheritable(duplicate_init_fd, False)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            for descriptor in locals().get("duplicate_monitor_fd", None), locals().get(
                "duplicate_init_fd", None
            ):
                if descriptor is not None:
                    self._close_fd(descriptor)
            raise WorkProcessIsolationUnavailable(
                "Bubblewrap process pair could not be adopted by the supervisor"
            ) from exc

        command = getattr(process, "args", ())
        if (
            isinstance(command, (str, bytes))
            or not isinstance(command, Sequence)
            or any(not isinstance(argument, str) for argument in command)
        ):
            self._close_fd(duplicate_monitor_fd)
            self._close_fd(duplicate_init_fd)
            raise WorkProcessIsolationUnavailable("Bubblewrap monitor arguments are invalid")
        with self._lock:
            if self._attempt is not None:
                self._close_fd(duplicate_monitor_fd)
                self._close_fd(duplicate_init_fd)
                raise WorkProcessSupervisorBlocked("this supervisor already owns a process tree")
            attempt = WorkProcessAttempt(
                command=tuple(command),
                monitor_pid=process.pid,
                _state=WorkProcessState.RUNNING,
                _monitor=process,
                _pidfd=duplicate_init_fd,
                _monitor_pidfd=duplicate_monitor_fd,
                _external_process_identity=normalized,
            )
            self._attempt = attempt
            return attempt

    def cleanup_bubblewrap_identity(self, identity: Mapping[str, object]) -> bool:
        """Reattach to the exact durable Bubblewrap pair and clean it through pidfds."""
        from cli_agent_orchestrator.clients.work_repository import (
            _validate_bubblewrap_identity,
        )

        normalized, _ = _validate_bubblewrap_identity(identity)
        with self._lock:
            if self._attempt is not None:
                raise WorkProcessSupervisorBlocked("this supervisor already owns a process tree")

        # Share the Bubblewrap-specific proc/argv/digest verifier with the
        # durable store layer; pidfd opening, ownership and termination remain
        # this supervisor's responsibility.
        from cli_agent_orchestrator.services import work_service

        if work_service._original_bubblewrap_pair_gone(normalized):
            return True
        try:
            observed = work_service._read_bubblewrap_live_identity(
                normalized["monitor_pid"], normalized["init_pid"]
            )
        except (OSError, ValueError, KeyError, TypeError):
            observed = None
        expected_fields = {
            key: value
            for key, value in normalized.items()
            if key
            in (
                "boot_id",
                "monitor_pid",
                "monitor_start_time_ticks",
                "init_pid",
                "init_start_time_ticks",
                "init_parent_pid",
                "pid_namespace",
                "net_namespace",
                "ipc_namespace",
                "monitor_argv_sha256",
                "monitor_executable_sha256",
            )
        }
        if observed != expected_fields:
            # A monitor can die while PID 1 survives. Recheck that exact
            # orphan identity, then signal only its freshly pinned pidfd.
            return self._cleanup_orphaned_bubblewrap_init(normalized, work_service)
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            return False

        monitor_fd = init_fd = None
        try:
            monitor_fd = os.pidfd_open(normalized["monitor_pid"])
            init_fd = os.pidfd_open(normalized["init_pid"])
            if (
                self._pidfd_target(monitor_fd) != normalized["monitor_pid"]
                or self._pidfd_target(init_fd) != normalized["init_pid"]
                or work_service._read_bubblewrap_live_identity(
                    normalized["monitor_pid"], normalized["init_pid"]
                )
                != expected_fields
                or self._pidfd_is_readable(monitor_fd) is not False
                or self._pidfd_is_readable(init_fd) is not False
            ):
                return False
            attempt = WorkProcessAttempt(
                command=(),
                monitor_pid=normalized["monitor_pid"],
                _state=WorkProcessState.RUNNING,
                _monitor=None,
                _pidfd=init_fd,
                _monitor_pidfd=monitor_fd,
                _external_process_identity=normalized,
            )
            with self._lock:
                if self._attempt is not None:
                    raise WorkProcessSupervisorBlocked(
                        "this supervisor already owns a process tree"
                    )
                self._attempt = attempt
            monitor_fd = init_fd = None  # The attempt now owns both descriptors.
            return self.terminate(attempt) is WorkProcessState.TERMINATED
        except (OSError, ValueError, KeyError, TypeError):
            return False
        finally:
            self._close_fd(init_fd)
            self._close_fd(monitor_fd)

    def _cleanup_orphaned_bubblewrap_init(self, identity, work_service) -> bool:
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            return False
        try:
            if not work_service._orphaned_bubblewrap_init_matches(identity):
                return False
            init_fd = os.pidfd_open(identity["init_pid"])
            try:
                if (
                    self._pidfd_target(init_fd) != identity["init_pid"]
                    or not work_service._orphaned_bubblewrap_init_matches(identity)
                    or self._pidfd_is_readable(init_fd) is not False
                ):
                    return False
                signal.pidfd_send_signal(init_fd, signal.SIGKILL)
                poller = select.poll()
                poller.register(init_fd, select.POLLIN | select.POLLHUP | select.POLLERR)
                events = poller.poll(round(self._cleanup_timeout * 1000))
                return (
                    bool(events)
                    and not any(flags & (select.POLLERR | select.POLLNVAL) for _, flags in events)
                    and any(flags & (select.POLLIN | select.POLLHUP) for _, flags in events)
                )
            finally:
                self._close_fd(init_fd)
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def original_pair_terminated(
        self, identity: WorkProcessIdentity | Mapping[str, object]
    ) -> bool:
        """Observe whether both exact v3 processes have ended, without signalling."""
        try:
            payload = identity.to_dict() if isinstance(identity, WorkProcessIdentity) else identity
            if not isinstance(payload, Mapping):
                return False
            expected = WorkProcessIdentity.from_dict(payload)
            if expected.version != _WORK_PROCESS_DURABLE_VERSION:
                return False
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            if not boot_id:
                return False
            if boot_id == expected.boot_id:
                for pid, start_ticks in (
                    (expected.monitor_pid, expected.monitor_start_time_ticks),
                    (expected.init_pid, expected.init_start_time_ticks),
                ):
                    try:
                        _, observed_start, state = self._read_proc_stat(pid)
                    except FileNotFoundError:
                        continue
                    if observed_start == start_ticks and state not in {"Z", "X", "x"}:
                        return False
        except (OSError, ValueError, WorkProcessIdentityProtocolIncompatible):
            return False

        with self._lock:
            attempt = self._attempt
        if attempt is None:
            return True
        # wait() takes these locks in this order; never close pidfds while it polls.
        with attempt._wait_lock:
            with self._lock:
                if self._attempt is not attempt or attempt._identity is None:
                    return True
                try:
                    local = WorkProcessIdentity.from_dict(attempt._identity.to_dict())
                except (ValueError, WorkProcessIdentityProtocolIncompatible):
                    return True
                if local != expected:
                    return True
                if attempt._monitor is not None:
                    try:
                        attempt.returncode = attempt._monitor.wait(timeout=0)
                    except (OSError, subprocess.TimeoutExpired):
                        return False
                self._close_pidfd(attempt)
                self._close_monitor_pidfd(attempt)
                attempt._state = WorkProcessState.TERMINATED
                attempt.reason = None
        return True

    def wait(
        self,
        attempt: WorkProcessAttempt,
        *,
        timeout: float | None = None,
    ) -> WorkProcessState:
        """Wait for both namespace init and monitor exit; timeout is never proof."""

        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative or None")
        self._require_owned(attempt)
        deadline = None if timeout is None else time.monotonic() + timeout
        with attempt._wait_lock:
            while True:
                with self._lock:
                    if attempt._identity is None and attempt._external_process_identity is None:
                        return self.uncertain(
                            attempt, "unverified pidfd ownership cannot prove process-tree exit"
                        )
                    if attempt.state is WorkProcessState.TERMINATED:
                        return attempt.state
                    monitor_pidfd = attempt._monitor_pidfd
                    init_pidfd = attempt._pidfd
                    if monitor_pidfd is None or init_pidfd is None:
                        return self.uncertain(
                            attempt,
                            "monitor and PID 1 pidfds are required to prove process-tree exit",
                        )
                    monitor_exited = self._pidfd_is_readable(monitor_pidfd)
                    init_exited = self._pidfd_is_readable(init_pidfd)
                    if monitor_exited is None or init_exited is None:
                        return self.uncertain(
                            attempt,
                            "pidfd poll error cannot prove process-tree exit",
                        )
                    if monitor_exited and init_exited:
                        if attempt._monitor is not None:
                            try:
                                attempt.returncode = attempt._monitor.wait(timeout=0)
                            except subprocess.TimeoutExpired:
                                return self.uncertain(
                                    attempt,
                                    "monitor pidfd became readable before its child status was waitable",
                                )
                            except OSError as exc:
                                return self.uncertain(
                                    attempt,
                                    f"could not reap unshare monitor after pidfd exit: {exc}",
                                )
                        attempt._state = WorkProcessState.TERMINATED
                        attempt.reason = None
                        self._close_pidfd(attempt)
                        self._close_monitor_pidfd(attempt)
                        return attempt.state

                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            return (
                                WorkProcessState.UNCERTAIN
                                if attempt.state is WorkProcessState.UNCERTAIN
                                else WorkProcessState.RUNNING
                            )
                        timeout_ms = max(1, int(remaining * 1000 + 0.999))
                    else:
                        timeout_ms = -1

                poller = select.poll()
                poller.register(monitor_pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
                poller.register(init_pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
                try:
                    events = poller.poll(timeout_ms)
                except InterruptedError:
                    continue
                except OSError as exc:
                    return self.uncertain(attempt, f"could not wait on process pidfds: {exc}")
                if any(flags & (select.POLLERR | select.POLLNVAL) for _, flags in events):
                    return self.uncertain(
                        attempt, "pidfd poll error cannot prove process-tree exit"
                    )

    def terminate(
        self,
        attempt: WorkProcessAttempt,
        *,
        timeout: float | None = None,
    ) -> WorkProcessState:
        """SIGKILL the pinned PID-namespace init and confirm the tree is empty."""

        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative or None")
        self._require_owned(attempt)
        with self._lock:
            if attempt._identity is None and attempt._external_process_identity is None:
                return self.uncertain(
                    attempt, "unverified pidfd ownership cannot authorize cleanup signal"
                )
            if attempt.state is WorkProcessState.TERMINATED:
                return attempt.state
            if attempt._pidfd is None:
                return self.uncertain(
                    attempt,
                    "no pidfd identifies the namespace init; cleanup cannot be signalled safely",
                )
            try:
                signal.pidfd_send_signal(attempt._pidfd, signal.SIGKILL)
            except ProcessLookupError:
                # It may have exited naturally. wait() still has to observe the
                # monitor/reaper boundary before this can become TERMINATED.
                pass
            except OSError as exc:
                return self.uncertain(attempt, f"could not signal namespace PID 1: {exc}")

        wait_timeout = self._cleanup_timeout if timeout is None else timeout
        state = self.wait(attempt, timeout=wait_timeout)
        if state is WorkProcessState.RUNNING:
            return self.uncertain(
                attempt,
                "cleanup deadline elapsed before the namespace became empty",
            )
        return state

    def uncertain(self, attempt: WorkProcessAttempt, reason: str) -> WorkProcessState:
        """Record unresolved cleanup; this one-shot supervisor then rejects starts."""

        self._require_owned(attempt)
        if not reason.strip():
            raise ValueError("uncertain cleanup requires a reason")
        with self._lock:
            if attempt.state is not WorkProcessState.TERMINATED:
                attempt._state = WorkProcessState.UNCERTAIN
                attempt.reason = reason
            return attempt.state

    def _require_capabilities(self) -> str:
        if not sys.platform.startswith("linux"):
            raise WorkProcessIsolationUnavailable(
                "Linux PID, network, and IPC namespaces are required to contain Work"
            )
        self._require_pidfd_support()
        if not hasattr(os, "pipe2"):
            raise WorkProcessIsolationUnavailable("this host lacks the close-on-exec startup gate")

        unshare = self._unshare_setting or shutil.which("unshare")
        if not unshare or not os.path.isfile(unshare) or not os.access(unshare, os.X_OK):
            raise WorkProcessIsolationUnavailable(
                "an executable util-linux unshare command is required for PID namespaces"
            )

        probe = [
            unshare,
            "--user",
            "--map-root-user",
            "--net",
            "--ipc",
            "--pid",
            "--fork",
            "--kill-child=SIGKILL",
            "--",
            sys.executable,
            "-I",
            "-c",
            "import os,sys; "
            "from cli_agent_orchestrator.services.work_process_seccomp "
            "import install_work_process_seccomp_filter; "
            "sys.exit(os.getpid() != 1)",
        ]
        try:
            result = subprocess.run(
                probe,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self._capability_timeout,
                env={},
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkProcessIsolationUnavailable(
                "rootless user/network/IPC/PID namespace capability probe timed out"
            ) from exc
        except OSError as exc:
            raise WorkProcessIsolationUnavailable(
                f"could not execute the PID-namespace capability probe: {exc}"
            ) from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
            raise WorkProcessIsolationUnavailable(
                "rootless user/network/IPC/PID namespace probe failed before work launch: "
                f"exit={result.returncode}; {detail}"
            )
        return unshare

    @staticmethod
    def _require_pidfd_support() -> None:
        if not sys.platform.startswith("linux"):
            raise WorkProcessIsolationUnavailable("reattachment requires Linux procfs and pidfds")
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise WorkProcessIsolationUnavailable(
                "this Python/Linux host lacks pidfd_open or pidfd_send_signal"
            )

    @classmethod
    def _read_process_identity(
        cls, monitor_pid: int, init_pid: int, *, strict_monitor_argv: bool = True
    ) -> WorkProcessIdentity:
        """Read a complete process and namespace snapshot from procfs."""

        if monitor_pid <= 0 or init_pid <= 0 or monitor_pid == init_pid:
            raise WorkProcessIsolationUnavailable("persisted process PIDs are invalid")
        try:
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            monitor_parent, monitor_start, monitor_state = cls._read_proc_stat(monitor_pid)
            init_parent, init_start, init_state = cls._read_proc_stat(init_pid)
            monitor_argv = cls._read_cmdline(monitor_pid)
            children_path = Path(f"/proc/{monitor_pid}/task/{monitor_pid}/children")
            children = [int(pid) for pid in children_path.read_text().split()]
            pid_ns = cls._namespace_identity(init_pid, "pid")
            net_ns = cls._namespace_identity(init_pid, "net")
            ipc_ns = cls._namespace_identity(init_pid, "ipc")
        except (OSError, ValueError) as exc:
            raise WorkProcessIsolationUnavailable(
                f"cannot read persisted process identity through procfs: {exc}"
            ) from exc

        if not boot_id:
            raise WorkProcessIsolationUnavailable("kernel boot ID is unavailable")
        if monitor_state in {"Z", "X", "x"} or init_state in {"Z", "X", "x"}:
            raise WorkProcessIsolationUnavailable("monitor or namespace PID 1 has exited")
        if init_parent != monitor_pid or children != [init_pid]:
            raise WorkProcessIsolationUnavailable(
                "namespace PID 1 is no longer the monitor's sole direct child"
            )
        if not monitor_argv or (strict_monitor_argv and not cls._valid_monitor_argv(monitor_argv)):
            raise WorkProcessIsolationUnavailable(
                "unshare monitor argv does not match the launcher"
            )
        if monitor_parent <= 0 or monitor_start <= 0 or init_start <= 0:
            raise WorkProcessIsolationUnavailable("monitor or PID 1 process identity is invalid")

        return WorkProcessIdentity(
            boot_id=boot_id,
            monitor_pid=monitor_pid,
            monitor_start_time_ticks=monitor_start,
            init_pid=init_pid,
            init_start_time_ticks=init_start,
            init_parent_pid=init_parent,
            pid_namespace=pid_ns,
            net_namespace=net_ns,
            ipc_namespace=ipc_ns,
            monitor_argv=monitor_argv,
        )

    @classmethod
    def _verify_process_isolation(cls, identity: WorkProcessIdentity) -> tuple[bool, str]:
        valid, reason = cls._verify_namespace_init(identity.init_pid)
        if not valid:
            return False, reason
        try:
            current = (
                cls._namespace_identity(identity.init_pid, "pid"),
                cls._namespace_identity(identity.init_pid, "net"),
                cls._namespace_identity(identity.init_pid, "ipc"),
            )
        except OSError as exc:
            return False, f"cannot recheck persisted namespaces through procfs: {exc}"
        expected = (identity.pid_namespace, identity.net_namespace, identity.ipc_namespace)
        if current != expected:
            return False, "persisted PID/network/IPC namespace identity changed"
        return True, ""

    @staticmethod
    def _read_proc_stat(pid: int) -> tuple[int, int, str]:
        stat = Path(f"/proc/{pid}/stat").read_text()
        close_paren = stat.rfind(")")
        if close_paren < 0:
            raise ValueError("proc stat has no command boundary")
        fields = stat[close_paren + 2 :].split()
        if len(fields) <= 19:
            raise ValueError("proc stat omits required process fields")
        return int(fields[1]), int(fields[19]), fields[0]

    @staticmethod
    def _read_cmdline(pid: int) -> tuple[str, ...]:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        if not raw or not raw.endswith(b"\x00"):
            raise ValueError("proc cmdline is empty or incomplete")
        return tuple(os.fsdecode(argument) for argument in raw[:-1].split(b"\x00"))

    @staticmethod
    def _namespace_identity(pid: int, kind: str) -> tuple[int, int]:
        namespace = os.stat(f"/proc/{pid}/ns/{kind}")
        return namespace.st_dev, namespace.st_ino

    @staticmethod
    def _valid_monitor_argv(argv: tuple[str, ...]) -> bool:
        expected = (
            "--user",
            "--map-root-user",
            "--net",
            "--ipc",
            "--pid",
            "--fork",
            "--kill-child=SIGKILL",
            "--",
            sys.executable,
            "-I",
            "-c",
            _BOOTSTRAP,
        )
        if len(argv) < 16 or not argv[0] or argv[1:13] != expected:
            return False
        try:
            gate_fd = int(argv[13])
            ack_fd = int(argv[14])
        except ValueError:
            return False
        return gate_fd >= 3 and ack_fd >= 3 and gate_fd != ack_fd and bool(argv[15:])

    def _wait_for_namespace_init(self, attempt: WorkProcessAttempt) -> int:
        monitor = attempt._monitor
        assert monitor is not None
        deadline = time.monotonic() + self._startup_timeout
        children_path = Path(f"/proc/{attempt.monitor_pid}/task/{attempt.monitor_pid}/children")
        while time.monotonic() < deadline:
            try:
                children = [int(pid) for pid in children_path.read_text().split()]
            except (FileNotFoundError, ProcessLookupError):
                children = []
            except OSError as exc:
                self._abort_start(attempt, f"cannot inspect unshare child via procfs: {exc}")

            candidates = [pid for pid in children if self._is_namespace_init(pid)]
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                self._abort_start(attempt, "unshare exposed multiple candidate PID namespace inits")

            exit_code = monitor.poll()
            if exit_code is not None:
                self._abort_start(
                    attempt,
                    "unshare exited before a gated PID-namespace init was established "
                    f"(exit={exit_code})",
                )
            time.sleep(self._poll_interval)

        self._abort_start(
            attempt,
            "timed out waiting for procfs to identify the gated PID namespace init",
        )
        raise AssertionError("_abort_start always raises")

    @classmethod
    def _is_namespace_init(cls, pid: int) -> bool:
        # Identify the gated PID-namespace init independently from the other
        # isolation proofs so startup can abort promptly with their exact reason.
        valid, _ = cls._verify_pid_namespace_init(pid)
        return valid

    @staticmethod
    def _verify_pid_namespace_init(pid: int) -> tuple[bool, str]:
        try:
            nspid_line = next(
                line
                for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                if line.startswith("NSpid:")
            )
            namespace_pid = int(nspid_line.split()[-1])
            candidate_ns = os.stat(f"/proc/{pid}/ns/pid")
            current_ns = os.stat("/proc/self/ns/pid")
        except (OSError, StopIteration, ValueError) as exc:
            return False, f"cannot verify namespace init through procfs: {exc}"
        if namespace_pid != 1:
            return False, f"candidate process {pid} is not PID 1 in its namespace"
        if (candidate_ns.st_dev, candidate_ns.st_ino) == (
            current_ns.st_dev,
            current_ns.st_ino,
        ):
            return False, f"candidate process {pid} is not in a distinct PID namespace"
        return True, ""

    @classmethod
    def _verify_namespace_init(cls, pid: int) -> tuple[bool, str]:
        valid, reason = cls._verify_pid_namespace_init(pid)
        if not valid:
            return False, reason
        try:
            candidate_net = os.stat(f"/proc/{pid}/ns/net")
            current_net = os.stat("/proc/self/ns/net")
            candidate_ipc = os.stat(f"/proc/{pid}/ns/ipc")
            current_ipc = os.stat("/proc/self/ns/ipc")
        except OSError as exc:
            return False, f"cannot verify namespace init through procfs: {exc}"
        if (candidate_net.st_dev, candidate_net.st_ino) == (
            current_net.st_dev,
            current_net.st_ino,
        ):
            return False, f"candidate process {pid} is not in a distinct network namespace"
        if (candidate_ipc.st_dev, candidate_ipc.st_ino) == (
            current_ipc.st_dev,
            current_ipc.st_ino,
        ):
            return False, f"candidate process {pid} is not in a distinct IPC namespace"
        return True, ""

    def _abort_start(self, attempt: WorkProcessAttempt, reason: str) -> None:
        """Close the gate, kill a known init, then require the same teardown proof."""

        self._close_gate(attempt)
        self._close_ack_fd(attempt)
        if attempt._identity is None:
            self.uncertain(attempt, f"{reason}; unverified pidfd ownership blocks cleanup")
            raise WorkProcessStartUncertain(attempt.reason or reason, attempt)
        if attempt._pidfd is not None:
            try:
                signal.pidfd_send_signal(attempt._pidfd, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as exc:
                reason = f"{reason}; could not kill failed bootstrap: {exc}"
        # start() still owns _lock. A concurrent wait() may already own
        # _wait_lock while waiting for _lock, so never block on it here.
        if not attempt._wait_lock.acquire(blocking=False):
            self.uncertain(attempt, f"{reason}; concurrent wait blocks bootstrap cleanup proof")
            raise WorkProcessStartUncertain(attempt.reason or reason, attempt)
        try:
            state = self.wait(attempt, timeout=self._cleanup_timeout)
        finally:
            attempt._wait_lock.release()
        if state is WorkProcessState.TERMINATED:
            raise WorkProcessIsolationUnavailable(reason)
        self.uncertain(attempt, f"{reason}; bootstrap cleanup could not be confirmed")
        raise WorkProcessStartUncertain(attempt.reason or reason, attempt)

    @staticmethod
    def _pidfd_is_readable(pidfd: int | None) -> bool | None:
        if pidfd is None:
            return None
        try:
            poller = select.poll()
            poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
            events = poller.poll(0)
        except (OSError, ValueError):
            return None
        if any(flags & (select.POLLERR | select.POLLNVAL) for _, flags in events):
            return None
        if not events:
            return False
        return (
            True if any(flags & (select.POLLIN | select.POLLHUP) for _, flags in events) else None
        )

    @staticmethod
    def _close_gate(attempt: WorkProcessAttempt) -> None:
        if attempt._gate_fd is not None:
            try:
                os.close(attempt._gate_fd)
            except OSError:
                pass
            attempt._gate_fd = None

    @staticmethod
    def _close_ack_fd(attempt: WorkProcessAttempt) -> None:
        if attempt._ack_fd is not None:
            try:
                os.close(attempt._ack_fd)
            except OSError:
                pass
            attempt._ack_fd = None

    @staticmethod
    def _close_pidfd(attempt: WorkProcessAttempt) -> None:
        if attempt._pidfd is not None:
            try:
                os.close(attempt._pidfd)
            except OSError:
                pass
            attempt._pidfd = None

    @staticmethod
    def _close_monitor_pidfd(attempt: WorkProcessAttempt) -> None:
        if attempt._monitor_pidfd is not None:
            try:
                os.close(attempt._monitor_pidfd)
            except OSError:
                pass
            attempt._monitor_pidfd = None

    @staticmethod
    def _close_fd(pidfd: int | None) -> None:
        if pidfd is not None:
            try:
                os.close(pidfd)
            except OSError:
                pass

    def _require_owned(self, attempt: WorkProcessAttempt) -> None:
        if attempt is not self._attempt:
            raise ValueError("attempt is not owned by this supervisor")

    @staticmethod
    def _pidfd_target(descriptor: int) -> int:
        fields = Path(f"/proc/self/fdinfo/{descriptor}").read_text(encoding="ascii").splitlines()
        targets = [line.split(":", 1)[1].strip() for line in fields if line.startswith("Pid:")]
        if len(targets) != 1:
            raise ValueError("pidfd target is unavailable")
        target = int(targets[0])
        if target <= 0:
            raise ValueError("pidfd target has already exited")
        return target
