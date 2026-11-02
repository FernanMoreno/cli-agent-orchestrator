"""Local owned-child exit evidence, never whole-generation retirement authority.

The launcher owns Popen and pins the child before a kernel-authenticated binding
handshake. It neither adopts an arbitrary PID nor proves descendants/SQL closed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import select
import signal
import socket
import stat
import struct
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import psutil

from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.resource_generation import (
    ProcessExitObservation,
    _issue_process_exit_observation,
    is_process_exit_observation,
)
from cli_agent_orchestrator.models.sql_child_subject import (
    VerifiedChildSqlSubject,
    _approve_verified_child_sql_subject,
    _expire_verified_child_sql_subject,
    _issue_verified_child_sql_subject,
)
from cli_agent_orchestrator.models.sql_process_origin import (
    RESOURCE_BINDING_FD_ENV as BINDING_FD_ENV,
)
from cli_agent_orchestrator.models.sql_process_origin import (
    is_sql_process_application_name,
)
from cli_agent_orchestrator.utils.process_sql_origin import current_sql_application_name

if TYPE_CHECKING:
    from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget
    from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository
    from cli_agent_orchestrator.clients.storage_admission import ChildSqlSessionCharge
    from cli_agent_orchestrator.models.sql_opening_intent import ChildSqlOpeningIntent

BINDING_PROTOCOL_VERSION = 2
_MAX_PACKET_BYTES = 8192
_CREDENTIALS = struct.Struct("3i")


class ResourceProcessUnproven(RuntimeError):
    """The exact owned child's binding or exit could not be established."""


def _timeout(value: float | None, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("resource process requires a finite nonnegative timeout")
    return value


def _proc_bytes(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ResourceProcessUnproven("resource process view is unavailable")
        value = stream.read(1024 * 1024 + 1)
        if not value or len(value) > 1024 * 1024 or not value.endswith(b"\n"):
            raise ResourceProcessUnproven("resource process view is incomplete")
        return value


@dataclass(frozen=True)
class _KernelIdentity:
    boot_id: str
    ticks: int
    parent_pid: int
    uid: int
    birth: float
    pid_ns: tuple[int, int]
    mount_ns: tuple[int, int]
    cgroup_ns: tuple[int, int]
    view: str


def _identity(pid: int) -> _KernelIdentity:
    root = Path("/proc") / str(pid)

    def process_stat() -> tuple[int, int]:
        value = _proc_bytes(root / "stat")
        if int(value.split(b"(", 1)[0]) != pid:
            raise ResourceProcessUnproven("resource process identity changed")
        values = value.rsplit(b")", 1)[1].split()
        if values[0] in {b"Z", b"X", b"x"}:
            raise ResourceProcessUnproven("resource process was not verified while live")
        return int(values[19]), int(values[1])

    def namespaces():
        result = []
        for name in ("pid", "mnt", "cgroup"):
            info = (root / "ns" / name).stat()
            result.append((info.st_dev, info.st_ino))
        return tuple(result)

    before, spaces = process_stat(), namespaces()
    boot = str(UUID(_proc_bytes(Path("/proc/sys/kernel/random/boot_id")).decode().strip()))
    uid = root.stat().st_uid
    birth = psutil.Process(pid).create_time()
    cgroup, mounts = _proc_bytes(root / "cgroup"), _proc_bytes(root / "mountinfo")
    if (
        process_stat() != before
        or namespaces() != spaces
        or _proc_bytes(root / "cgroup") != cgroup
        or _proc_bytes(root / "mountinfo") != mounts
    ):
        raise ResourceProcessUnproven("resource process changed during observation")
    view = hashlib.sha256(
        len(cgroup).to_bytes(8, "big") + cgroup + len(mounts).to_bytes(8, "big") + mounts
    ).hexdigest()
    return _KernelIdentity(
        boot, before[0], before[1], uid, birth, spaces[0], spaces[1], spaces[2], view
    )


def _matches(binding: ResourceBinding, identity: _KernelIdentity) -> bool:
    return (
        binding.process_started_at == identity.birth
        and (binding.mount_ns_device, binding.mount_ns_inode) == identity.mount_ns
        and (binding.cgroup_ns_device, binding.cgroup_ns_inode) == identity.cgroup_ns
        and binding.resource_view_sha256 == identity.view
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate resource process field")
        result[key] = value
    return result


def _exit_ready(fd: int) -> bool | None:
    try:
        poller = select.poll()
        poller.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR | select.POLLNVAL)
        events = poller.poll(0)
        if not events:
            return False
        if any(event_fd != fd for event_fd, _ in events):
            return None
        flags = 0
        for _, value in events:
            flags |= value
        if flags & (select.POLLERR | select.POLLNVAL):
            return None
        return True if flags & (select.POLLIN | select.POLLHUP) else None
    except (OSError, ValueError):
        return None


def publish_resource_binding(binding: ResourceBinding, *, timeout_s: float = 30) -> bool:
    """Trusted child startup hook. Without the private inherited FD, do nothing.

    No SQL, file receipt, credentials or authority is created. The child waits
    for its parent's ACK before its caller proceeds to resource admission.
    """
    if BINDING_FD_ENV not in os.environ:
        return False
    # Latch before consuming the FD, including a child with no earlier SQL.
    sql_application_name = current_sql_application_name()
    raw = os.environ.pop(BINDING_FD_ENV)
    _timeout(timeout_s)
    if not raw.isdecimal() or int(raw) < 3:
        raise ResourceProcessUnproven("resource binding channel is invalid")
    fd = int(raw)
    os.set_inheritable(fd, False)
    with socket.socket(fileno=fd) as channel:
        channel.settimeout(timeout_s)
        if (
            channel.family != socket.AF_UNIX
            or channel.type != socket.SOCK_SEQPACKET
            or type(binding) is not ResourceBinding
            or binding.process_pid != os.getpid()
        ):
            raise ResourceProcessUnproven("resource binding channel or process is invalid")
        identity = _identity(os.getpid())
        if not _matches(binding, identity):
            raise ResourceProcessUnproven("resource binding does not match the live process")
        payload = json.dumps(
            {
                "version": BINDING_PROTOCOL_VERSION,
                "binding": asdict(binding),
                "kernel": asdict(identity),
                "sql_application_name": sql_application_name,
            },
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        if len(payload) > _MAX_PACKET_BYTES:
            raise ResourceProcessUnproven("resource binding packet is too large")
        if channel.send(payload) != len(payload) or channel.recv(2) != b"1":
            raise ResourceProcessUnproven("resource binding was not acknowledged")
    return True


class ResourceProcessSupervisor:
    """Single PID/thread owner of one child, its IPC and its pinned kernel handle."""

    _owner_pid: int
    _owner_thread: threading.Thread
    _binding: ResourceBinding | None
    _sql_application_name: str | None
    _observation: ProcessExitObservation | None
    _identity: _KernelIdentity | None
    _pidfd: int | None
    _closed: bool
    _exited: bool
    _wait_status: tuple[int, int] | None
    _child_sql_session_charge: ChildSqlSessionCharge | None
    _binding_failed: bool
    _receiving: bool
    _issued_subjects: set[VerifiedChildSqlSubject]
    _subject_token: object | None
    _store: str
    _target_digest: str
    _channel: socket.socket
    _process: subprocess.Popen[bytes]

    def __init__(self):
        raise TypeError("resource process supervisors must be launched")

    def __copy__(self):
        raise TypeError("resource process ownership is not transferable")

    def __deepcopy__(self, memo):
        raise TypeError("resource process ownership is not transferable")

    def __reduce_ex__(self, protocol):
        raise TypeError("resource process ownership is not serializable")

    @classmethod
    def launch(
        cls,
        argv: Sequence[str],
        *,
        expected_store_id: str,
        expected_target_identity_sha256: str,
        env: Mapping[str, str] | None = None,
    ) -> ResourceProcessSupervisor:
        if (
            isinstance(argv, (str, bytes))
            or not argv
            or any(not isinstance(value, str) or not value or "\0" in value for value in argv)
        ):
            raise ValueError("resource process requires explicit argv")
        store = str(UUID(expected_store_id))
        if len(expected_target_identity_sha256) != 64 or any(
            value not in "0123456789abcdef" for value in expected_target_identity_sha256
        ):
            raise ValueError("resource process requires a canonical target digest")
        environment = dict(os.environ if env is None else env)
        # The child's explicit diagnostic label is controlled here, not by an
        # inherited optional PGAPPNAME. Keep the parent's environment intact
        # and preserve all other strict libpq/profile validation.
        environment.pop("PGAPPNAME", None)
        if BINDING_FD_ENV in environment:
            raise ResourceProcessUnproven("resource process cannot inherit another supervisor")
        if not hasattr(os, "pidfd_open") or not hasattr(socket, "SCM_CREDENTIALS"):
            raise ResourceProcessUnproven("resource process kernel support is unavailable")
        value = object.__new__(cls)
        value._owner_pid = os.getpid()
        value._owner_thread = threading.current_thread()
        value._child_sql_session_charge = None
        value._binding = None
        value._sql_application_name = None
        value._observation = None
        value._identity = None
        value._pidfd = None
        value._closed = False
        value._exited = False
        value._wait_status = None
        value._binding_failed = False
        value._receiving = False
        value._issued_subjects = set()
        value._subject_token = None
        value._store = store
        value._target_digest = expected_target_identity_sha256
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        value._channel = parent
        try:
            parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            environment[BINDING_FD_ENV] = str(child.fileno())
            value._process = subprocess.Popen(
                list(argv), env=environment, close_fds=True, pass_fds=(child.fileno(),)
            )
        except BaseException as error:
            parent.close()
            # Losing the Popen handle does NOT establish no-start or cleanup.
            error.add_note("Child start is unobserved; no resource exit proof was produced.")
            raise
        finally:
            child.close()
        try:
            value._pidfd = os.pidfd_open(value._process.pid, 0)
            os.set_inheritable(value._pidfd, False)
            before = _identity(value._process.pid)
            after = _identity(value._process.pid)
            own_ns = Path("/proc/self/ns/pid").stat()
            if (
                before != after
                or before.parent_pid != value._owner_pid
                or before.uid != os.getuid()
                or before.pid_ns != (own_ns.st_dev, own_ns.st_ino)
                or _exit_ready(value._pidfd) is not False
            ):
                raise ResourceProcessUnproven("resource child identity is unproven")
            value._identity = before
        except (OSError, ValueError, psutil.Error, ResourceProcessUnproven):
            # Still retain the returned Popen: callers must join their own child.
            value._binding_failed = True
            parent.close()
        except BaseException as error:
            value._binding_failed = True
            parent.close()
            # Keep the first exception and the exact physical child owner.
            setattr(error, "resource_process_owner", value)
            error.add_note("Resource child still requires its original owner's join.")
            raise
        return value

    def _check_owner(self) -> None:
        if self._owner_pid != os.getpid() or self._owner_thread is not threading.current_thread():
            raise PermissionError("resource process belongs to another owner")

    @property
    def observation(self) -> ProcessExitObservation | None:
        self._check_owner()
        return self._observation

    @property
    def sql_application_name(self) -> str | None:
        """Authenticated attribution only, never a server-side absence witness."""
        self._check_owner()
        return self._sql_application_name

    @property
    def child_sql_session_charge(self) -> ChildSqlSessionCharge | None:
        """Retained parent debit only; never permission for another child open."""
        self._check_owner()
        return self._child_sql_session_charge

    def receive_binding(self, *, timeout_s: float = 30) -> ResourceBinding:
        return self._receive_binding(timeout_s=timeout_s)

    def receive_binding_with_child_intent(
        self,
        *,
        channel: Connection,
        repository: SqlIntentRepository,
        journal_id: str,
        intent_id: str,
        destination: DatabaseTarget,
        path: str = "native",
        timeout_s: float = 30,
        session_reservation: object | None = None,
    ) -> ChildSqlOpeningIntent:
        """Explicit parent-funded registration; ACK still grants identity only."""
        self._check_owner()
        from cli_agent_orchestrator.clients.sql_intent_repository import (
            SqlChildAckUnavailableAfterCommit,
            SqlIntentCommitUncertain,
            SqlIntentRepository,
        )
        from cli_agent_orchestrator.models.sql_opening_intent import ChildSqlOpeningIntent

        if type(repository) is not SqlIntentRepository:
            raise ValueError("child registration requires the native intent repository")
        receipt: ChildSqlOpeningIntent | None = None

        def register(subject: VerifiedChildSqlSubject) -> None:
            nonlocal receipt
            if session_reservation is not None:
                self._child_sql_session_charge = channel.reserve_child_sql_session(
                    reservation=session_reservation, subject=subject
                )
            result = repository.record_child_opening(
                channel,
                journal_id=journal_id,
                intent_id=intent_id,
                subject=subject,
                path=path,
                destination=destination,
            )
            if type(result) is not ChildSqlOpeningIntent or result.subject is not subject.binding:
                raise SqlIntentCommitUncertain(journal_id, intent_id)
            receipt = result
            if self._child_sql_session_charge is not None:
                self._child_sql_session_charge.check()

        try:
            self._receive_binding(timeout_s=timeout_s, before_ack=register)
        except BaseException as failure:
            charge = self._child_sql_session_charge
            if charge is None:
                charge = getattr(failure, "child_sql_session_charge", None)
                self._child_sql_session_charge = charge
            if charge is not None:
                setattr(failure, "child_sql_session_charge", charge)
            if receipt is not None:
                self._binding_failed = True
                if not isinstance(
                    failure, (SqlChildAckUnavailableAfterCommit, SqlIntentCommitUncertain)
                ):
                    unavailable = SqlChildAckUnavailableAfterCommit(receipt)
                    if charge is not None:
                        setattr(unavailable, "child_sql_session_charge", charge)
                    raise unavailable from failure
            raise
        if receipt is None:
            raise ResourceProcessUnproven("child registration has no acknowledged receipt")
        return receipt

    def _receive_binding(
        self,
        *,
        timeout_s: float = 30,
        before_ack: Callable[[VerifiedChildSqlSubject], object] | None = None,
    ) -> ResourceBinding:
        self._check_owner()
        _timeout(timeout_s)
        if self._receiving:
            self._binding_failed = True
            raise ResourceProcessUnproven("resource process handshake is already active")
        if self._closed or self._binding_failed:
            raise ResourceProcessUnproven("resource process binding is unavailable")
        if self._binding is not None:
            if before_ack is not None:
                raise ResourceProcessUnproven("an acknowledged child cannot be registered late")
            return self._binding
        if before_ack is not None and not callable(before_ack):
            raise TypeError("resource process registration must be callable")
        self._receiving = True
        self._subject_token = object()
        subject = None
        try:
            self._channel.settimeout(timeout_s)
            payload, ancillary, flags, _ = self._channel.recvmsg(
                _MAX_PACKET_BYTES + 1,
                socket.CMSG_SPACE(_CREDENTIALS.size) + socket.CMSG_SPACE(struct.calcsize("i")),
                socket.MSG_CMSG_CLOEXEC,
            )
            # recvmsg installs SCM_RIGHTS descriptors before userspace validates
            # the packet, including a truncated/rejected ancillary envelope.
            received_rights = False
            for level, kind, raw in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    received_rights = True
                    size = struct.calcsize("i")
                    for offset in range(0, len(raw) - len(raw) % size, size):
                        fd = struct.unpack_from("i", raw, offset)[0]
                        try:
                            os.close(fd)
                        except OSError:
                            pass
            if received_rights:
                raise ResourceProcessUnproven("resource binding cannot transfer descriptors")
            if (
                not payload
                or len(payload) > _MAX_PACKET_BYTES
                or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
            ):
                raise ResourceProcessUnproven("resource binding packet is incomplete")
            if len(ancillary) != 1:
                raise ResourceProcessUnproven("resource binding sender is unproven")
            level, kind, raw = ancillary[0]
            if (
                level != socket.SOL_SOCKET
                or kind != socket.SCM_CREDENTIALS
                or len(raw) != _CREDENTIALS.size
            ):
                raise ResourceProcessUnproven("resource binding credentials are invalid")
            sender, uid, _ = _CREDENTIALS.unpack(raw)
            if sender != self._process.pid or uid != os.getuid():
                raise ResourceProcessUnproven(
                    "resource binding sender differs from the owned child"
                )
            packet = json.loads(payload, object_pairs_hook=_unique_object)
            if (
                not isinstance(packet, dict)
                or set(packet) != {"version", "binding", "kernel", "sql_application_name"}
                or type(packet["version"]) is not int
                or packet["version"] != BINDING_PROTOCOL_VERSION
                or not is_sql_process_application_name(packet["sql_application_name"])
                or not isinstance(packet["binding"], dict)
                or set(packet["binding"]) != {f.name for f in fields(ResourceBinding)}
            ):
                raise ResourceProcessUnproven("resource binding packet schema is invalid")
            binding = ResourceBinding(**packet["binding"])
            live = _identity(self._process.pid)
            expected_kernel = json.loads(json.dumps(asdict(live)))
            if (
                live != self._identity
                or json.dumps(packet["kernel"], sort_keys=True)
                != json.dumps(expected_kernel, sort_keys=True)
                or binding.process_pid != self._process.pid
                or binding.store_id != self._store
                or binding.target_identity_sha256 != self._target_digest
                or not _matches(binding, live)
                or self._pidfd is None
                or _exit_ready(self._pidfd) is not False
            ):
                raise ResourceProcessUnproven("resource binding differs from the original child")
            if before_ack is not None:

                def revalidate(value: VerifiedChildSqlSubject) -> None:
                    self._check_owner()
                    if (
                        self._closed
                        or self._exited
                        or self._binding_failed
                        or not self._receiving
                        or value not in self._issued_subjects
                        or value._handshake_token is not self._subject_token
                        or self._pidfd is None
                        or _exit_ready(self._pidfd) is not False
                        or _identity(self._process.pid) != live
                    ):
                        raise ResourceProcessUnproven("original child identity is no longer live")

                subject = _issue_verified_child_sql_subject(
                    binding,
                    sql_application_name=packet["sql_application_name"],
                    boot_id=live.boot_id,
                    start_time_ticks=live.ticks,
                    pid_namespace=live.pid_ns,
                    validate=revalidate,
                    handshake_token=self._subject_token,
                )
                self._issued_subjects.add(subject)
                _approve_verified_child_sql_subject(subject)
                if before_ack(subject) is not None:
                    raise ResourceProcessUnproven("child registration was not acknowledged")
                subject.check()
            if self._channel.send(b"1") != 1:
                raise ResourceProcessUnproven("resource binding acknowledgement failed")
            self._binding = binding
            self._sql_application_name = packet["sql_application_name"]
            return binding
        except (
            OSError,
            ValueError,
            TypeError,
            IndexError,
            KeyError,
            psutil.Error,
            ResourceProcessUnproven,
        ):
            self._binding_failed = True
            if subject is not None:
                raise
            raise ResourceProcessUnproven(
                "resource process binding could not be verified"
            ) from None
        except BaseException:
            self._binding_failed = True
            raise
        finally:
            try:
                if subject is not None:
                    _expire_verified_child_sql_subject(subject)
            finally:
                self._issued_subjects.clear()
                self._subject_token = None
                self._receiving = False
                self._channel.close()

    def send_signal(self, signum: int) -> None:
        self._check_owner()
        if self._closed or self._exited:
            return
        if self._pidfd is None:
            raise ResourceProcessUnproven("resource child has no pinned signal handle")
        try:
            signal.pidfd_send_signal(self._pidfd, signum)
        except ProcessLookupError:
            pass

    def _observe_reaped(self) -> ProcessExitObservation | None:
        if (
            self._binding is not None
            and not self._binding_failed
            and self._pidfd is not None
            and self._identity is not None
            and self._wait_status is not None
            and self._sql_application_name is not None
            and _exit_ready(self._pidfd) is True
        ):
            code, status = self._wait_status
            kinds = {
                os.CLD_EXITED: "exited",
                os.CLD_KILLED: "signaled",
                os.CLD_DUMPED: "core_dumped",
            }
            self._observation = _issue_process_exit_observation(
                self._binding,
                boot_id=self._identity.boot_id,
                start_time_ticks=self._identity.ticks,
                pid_namespace=self._identity.pid_ns,
                exit_kind=kinds[code],
                exit_status=status,
                sql_application_name=self._sql_application_name,
            )
        return self._observation

    def wait(self, *, timeout_s: float | None = None) -> ProcessExitObservation | None:
        self._check_owner()
        _timeout(timeout_s, optional=True)
        if self._observation is not None:
            return self._observation
        if self._closed:
            return None
        if self._exited:
            return self._observe_reaped()
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            try:
                if self._pidfd is not None:
                    result = os.waitid(os.P_PIDFD, self._pidfd, os.WEXITED | os.WNOHANG)
                else:
                    result = os.waitid(os.P_PID, self._process.pid, os.WEXITED | os.WNOHANG)
            except ChildProcessError:
                # A different reaper or SIGCHLD disposition consumed the status.
                # Popen.wait would invent zero here. Do not emit an observation.
                if self._pidfd is not None and _exit_ready(self._pidfd) is True:
                    self._exited = True
                    return None
                raise ResourceProcessUnproven("resource child wait status is unavailable") from None
            if result is not None:
                if (
                    result.si_pid != self._process.pid
                    or result.si_uid != os.getuid()
                    or result.si_code not in {os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED}
                ):
                    raise ResourceProcessUnproven("resource child wait identity is invalid")
                self._wait_status = (result.si_code, result.si_status)
                self._exited = True
                self._process.returncode = (
                    result.si_status if result.si_code == os.CLD_EXITED else -result.si_status
                )
                return self._observe_reaped()
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                assert timeout_s is not None
                raise subprocess.TimeoutExpired(self._process.args, timeout_s)
            if self._pidfd is None:
                # There is no release authority without a pin; still join the
                # original child conservatively after an initial pin failure.
                time.sleep(min(0.01, remaining) if remaining is not None else 0.01)
            else:
                poller = select.poll()
                poller.register(
                    self._pidfd, select.POLLIN | select.POLLHUP | select.POLLERR | select.POLLNVAL
                )
                milliseconds = (
                    None if remaining is None else min(math.ceil(remaining * 1000), 2**31 - 1)
                )
                events = poller.poll(milliseconds)
                if any(
                    fd != self._pidfd or flags & (select.POLLERR | select.POLLNVAL)
                    for fd, flags in events
                ):
                    raise ResourceProcessUnproven("resource child exit poll is unavailable")

    def close(self) -> None:
        self._check_owner()
        if self._closed:
            return
        if not self._exited:
            try:
                self.wait(timeout_s=0)
            except subprocess.TimeoutExpired:
                raise ResourceProcessUnproven(
                    "live resource process ownership cannot be discarded"
                ) from None
        self._channel.close()
        if self._pidfd is not None:
            os.close(self._pidfd)
            self._pidfd = None
        # Only finalizes Popen bookkeeping after actual waitid/pinned exit;
        # its synthesized ECHILD return value is never evidence or exposed.
        self._process.poll()
        self._closed = True
