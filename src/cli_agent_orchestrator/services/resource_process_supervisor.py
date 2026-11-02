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
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

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
from cli_agent_orchestrator.models.sql_startup_permission import (
    SQL_BROKER_FD_ENV,
    SQL_STARTUP_PERMISSION_ENV,
    SQL_STARTUP_PERMISSION_VERSION,
    SqlBrokerIdle,
    SqlBrokerPermissionRecord,
    SqlBrokerRequest,
    SqlStartupPermissionRecord,
    _approve_verified_sql_startup_permission,
    _expire_verified_sql_startup_permission,
    _issue_verified_sql_startup_permission,
    sql_startup_binding_sha256,
    sql_startup_destination_sha256,
)
from cli_agent_orchestrator.utils.process_sql_origin import (
    _deny_sql_startup_permission,
    _install_sql_broker,
    _install_sql_startup_permission,
    current_sql_application_name,
)

if TYPE_CHECKING:
    from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget
    from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository
    from cli_agent_orchestrator.clients.storage_admission import ChildSqlSessionCharge
    from cli_agent_orchestrator.models.sql_opening_intent import (
        ChildSqlFollowupIntent,
        ChildSqlOpeningIntent,
    )

BINDING_PROTOCOL_VERSION = 2
_MAX_PACKET_BYTES = 8192
_CREDENTIALS = struct.Struct("3i")

ResourceProcessFailureStage = Literal[
    "channel_setup",
    "recvmsg",
    "ancillary",
    "packet_frame",
    "sender_credentials",
    "packet_decode",
    "packet_schema",
    "binding_decode",
    "kernel_validation",
    "subject_issue",
]
ResourceProcessFailureType = Literal[
    "os_error",
    "value_error",
    "type_error",
    "index_error",
    "key_error",
    "process_error",
    "protocol_error",
]


class ResourceProcessUnproven(RuntimeError):
    """The exact owned child's binding or exit could not be established."""

    failure_stage: ResourceProcessFailureStage | None = None
    failure_type: ResourceProcessFailureType | None = None


def _add_failure_note(error: BaseException, note: str) -> None:
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(note)


def _binding_failure_type(failure: Exception) -> ResourceProcessFailureType:
    if isinstance(failure, OSError):
        return "os_error"
    if isinstance(failure, ValueError):
        return "value_error"
    if isinstance(failure, TypeError):
        return "type_error"
    if isinstance(failure, IndexError):
        return "index_error"
    if isinstance(failure, KeyError):
        return "key_error"
    if isinstance(failure, psutil.Error):
        return "process_error"
    return "protocol_error"


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


def publish_resource_binding(
    binding: ResourceBinding, *, timeout_s: float = 30, first_sql_grant: bool = False
) -> bool:
    """Trusted child startup hook. Without the private inherited FD, do nothing.

    No SQL, file receipt, credentials or authority is created. The child waits
    for its parent's ACK before its caller proceeds to resource admission.
    """
    if type(first_sql_grant) is not bool:
        raise ValueError("SQL startup mode must be explicit")
    if first_sql_grant or SQL_STARTUP_PERMISSION_ENV in os.environ:
        if not first_sql_grant:
            _deny_sql_startup_permission()
            raise ResourceProcessUnproven(
                "strict SQL startup cannot use an identity-only publisher"
            )
        return _publish_sql_startup_binding(binding, timeout_s=timeout_s)
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


def _publish_sql_startup_binding(binding: ResourceBinding, *, timeout_s: float) -> bool:
    """Accept only a bounded grant sent by the original live kernel parent."""
    permission = None
    try:
        if os.environ.get(SQL_STARTUP_PERMISSION_ENV) != "1" or BINDING_FD_ENV not in os.environ:
            raise ResourceProcessUnproven("SQL startup requires its original supervised channel")
        label = current_sql_application_name()  # Latch pending mode before consuming the FD.
        raw = os.environ.pop(BINDING_FD_ENV)
        _timeout(timeout_s)
        if not raw.isdecimal() or int(raw) < 3:
            raise ResourceProcessUnproven("resource binding channel is invalid")
        fd = int(raw)
        os.set_inheritable(fd, False)
        with socket.socket(fileno=fd) as channel:
            active = True
            try:
                channel.settimeout(timeout_s)
                if (
                    channel.family != socket.AF_UNIX
                    or channel.type != socket.SOCK_SEQPACKET
                    or type(binding) is not ResourceBinding
                    or binding.process_pid != os.getpid()
                ):
                    raise ResourceProcessUnproven("SQL startup channel or subject is invalid")
                identity = _identity(os.getpid())
                parent_pid = os.getppid()
                parent = _identity(parent_pid)
                peer = _CREDENTIALS.unpack(
                    channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _CREDENTIALS.size)
                )
                if (
                    not _matches(binding, identity)
                    or identity.parent_pid != parent_pid
                    or peer[0] != parent_pid
                    or peer[1] != os.getuid()
                    or parent.uid != os.getuid()
                ):
                    raise ResourceProcessUnproven("SQL startup parent or subject is unproven")
                channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
                payload = json.dumps(
                    dict(
                        version=SQL_STARTUP_PERMISSION_VERSION,
                        binding=asdict(binding),
                        kernel=asdict(identity),
                        sql_application_name=label,
                    ),
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
                if len(payload) > _MAX_PACKET_BYTES or channel.send(payload) != len(payload):
                    raise ResourceProcessUnproven("SQL startup binding was not sent")
                reply, ancillary, flags, _ = channel.recvmsg(
                    _MAX_PACKET_BYTES + 1,
                    socket.CMSG_SPACE(_CREDENTIALS.size) + socket.CMSG_SPACE(struct.calcsize("i")),
                    socket.MSG_CMSG_CLOEXEC,
                )
                rights = False
                for level, kind, data in ancillary:
                    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                        rights = True
                        size = struct.calcsize("i")
                        for offset in range(0, len(data) - len(data) % size, size):
                            try:
                                os.close(struct.unpack_from("i", data, offset)[0])
                            except OSError:
                                pass
                if (
                    rights
                    or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
                    or not reply
                    or len(reply) > _MAX_PACKET_BYTES
                    or len(ancillary) != 1
                ):
                    raise ResourceProcessUnproven("SQL startup reply is incomplete")
                level, kind, credentials = ancillary[0]
                if (
                    level != socket.SOL_SOCKET
                    or kind != socket.SCM_CREDENTIALS
                    or len(credentials) != _CREDENTIALS.size
                ):
                    raise ResourceProcessUnproven("SQL startup reply sender is unproven")
                sender, uid, _ = _CREDENTIALS.unpack(credentials)
                if sender != parent_pid or uid != os.getuid():
                    raise ResourceProcessUnproven(
                        "SQL startup reply is not from the original parent"
                    )
                record = SqlStartupPermissionRecord.from_packet(
                    json.loads(reply, object_pairs_hook=_unique_object)
                )
                if (
                    record.subject_binding_sha256 != sql_startup_binding_sha256(binding)
                    or record.sql_application_name != label
                ):
                    raise ResourceProcessUnproven("SQL startup reply belongs to another subject")

                def validate_process() -> None:
                    try:
                        live_process = _identity(os.getpid())
                    except (OSError, ValueError, psutil.Error):
                        raise ResourceProcessUnproven(
                            "original SQL startup process view is unavailable"
                        ) from None
                    if live_process != identity or not _matches(binding, live_process):
                        raise ResourceProcessUnproven("original SQL startup process view changed")

                def validate_live() -> None:
                    if (
                        not active
                        or channel.fileno() < 0
                        or os.getppid() != parent_pid
                        or _identity(parent_pid) != parent
                    ):
                        raise ResourceProcessUnproven("original SQL startup issuer is unavailable")
                    validate_process()

                permission = _issue_verified_sql_startup_permission(
                    record,
                    binding,
                    validate=validate_live,
                    validate_process=validate_process,
                )
                _approve_verified_sql_startup_permission(permission)
                _install_sql_startup_permission(permission)
                _attach_sql_broker(
                    permission,
                    parent_pid=parent_pid,
                    parent=parent,
                    validate_process=validate_process,
                    timeout_s=timeout_s,
                    startup_fd=fd,
                )
            finally:
                active = False
                if permission is not None:
                    _expire_verified_sql_startup_permission(permission)
        return True
    except (OSError, ValueError, TypeError, RecursionError, psutil.Error):
        _deny_sql_startup_permission()
        _close_uninstalled_broker()
        raise ResourceProcessUnproven("SQL startup permission could not be authenticated") from None
    except BaseException:
        _deny_sql_startup_permission()
        _close_uninstalled_broker()
        raise


def _broker_receive(channel: socket.socket, sender: int, *, eof: bool = False):
    """Bounded authenticated message; reject and close every received descriptor."""
    payload, ancillary, flags, _ = channel.recvmsg(
        _MAX_PACKET_BYTES + 1,
        socket.CMSG_SPACE(_CREDENTIALS.size) + socket.CMSG_SPACE(struct.calcsize("i")),
        socket.MSG_CMSG_CLOEXEC,
    )
    rights = False
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            rights = True
            size = struct.calcsize("i")
            for offset in range(0, len(data) - len(data) % size, size):
                try:
                    os.close(struct.unpack_from("i", data, offset)[0])
                except OSError:
                    pass
    if rights or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(payload) > _MAX_PACKET_BYTES:
        raise ResourceProcessUnproven("SQL broker message is incomplete")
    if not payload and eof and not ancillary:
        return None
    if not payload or len(ancillary) != 1:
        raise ResourceProcessUnproven("SQL broker message has no authenticated sender")
    level, kind, data = ancillary[0]
    if (
        level != socket.SOL_SOCKET
        or kind != socket.SCM_CREDENTIALS
        or len(data) != _CREDENTIALS.size
        or _CREDENTIALS.unpack(data)[:2] != (sender, os.getuid())
    ):
        raise ResourceProcessUnproven("SQL broker message sender differs from its owner")
    return json.loads(payload, object_pairs_hook=_unique_object)


def _broker_send(channel: socket.socket, packet: dict[str, object]) -> None:
    payload = json.dumps(packet, separators=(",", ":"), allow_nan=False).encode()
    if len(payload) > _MAX_PACKET_BYTES or channel.send(payload) != len(payload):
        raise ResourceProcessUnproven("SQL broker message was not sent completely")


class _SqlBrokerClient:
    """One process-wide exchange, never held across the physical driver call."""

    def __init__(self, channel, permission, parent_pid, parent, validate_process, timeout_s):
        self.channel = channel
        self.initial = permission.record
        self.binding = permission.binding
        self.parent_pid = parent_pid
        self.parent = parent
        self.pid = os.getpid()
        self.validate_process = validate_process
        self.timeout_s = timeout_s
        self.exchange = threading.Lock()
        self.sequence = 0
        self.failed = False
        # One immutable broker installation per process; no growing callback registry.
        os.register_at_fork(after_in_child=self._after_fork)

    def _after_fork(self):
        self.failed = True
        self.channel.close()  # Do not touch any inherited Python mutex.

    def close(self):
        self.failed = True
        self.channel.close()

    def _check(self):
        if (
            self.pid != os.getpid()
            or self.failed
            or self.channel.fileno() < 0
            or os.getppid() != self.parent_pid
            or _identity(self.parent_pid) != self.parent
        ):
            raise ResourceProcessUnproven("original SQL broker is unavailable")
        self.validate_process()

    def request(self, path, destination):
        from cli_agent_orchestrator.utils.process_sql_origin import (
            SqlBrokerRequestRefused,
            SqlBrokerUnavailable,
            SqlStartupPermissionUnavailable,
        )

        if self.pid != os.getpid():
            raise SqlStartupPermissionUnavailable("SQL broker belongs to another process")
        # Validate without taking the shared channel or poisoning another worker.
        try:
            template = SqlBrokerRequest(
                journal_id=self.initial.journal_id,
                startup_intent_id=self.initial.intent_id,
                intent_id=str(uuid4()),
                sequence=1,
                subject_binding_sha256=self.initial.subject_binding_sha256,
                sql_application_name=self.initial.sql_application_name,
                path=path,
                destination_sha256=sql_startup_destination_sha256(destination),
                destination_scope=getattr(destination, "scope", None),
            )
        except (ValueError, TypeError, AttributeError):
            raise SqlBrokerRequestRefused("invalid local SQL broker request") from None
        if not self.exchange.acquire(timeout=self.timeout_s):
            raise SqlBrokerRequestRefused("SQL broker exchange is busy")
        request = None
        sent = False
        try:
            self._check()
            request = replace(template, sequence=self.sequence + 1)
            self.sequence = request.sequence
            self.channel.settimeout(self.timeout_s)
            sent = True
            _broker_send(self.channel, request.to_packet())
            record = SqlBrokerPermissionRecord.from_packet(
                _broker_receive(self.channel, self.parent_pid)
            )
            self._check()
            if (
                record.intent_id != request.intent_id
                or record.sequence != request.sequence
                or record.startup_intent_id != request.startup_intent_id
                or record.journal_id != request.journal_id
                or record.subject_binding_sha256 != request.subject_binding_sha256
                or record.sql_application_name != request.sql_application_name
                or record.path != request.path
                or record.destination_sha256 != request.destination_sha256
                or record.destination_scope != request.destination_scope
            ):
                raise ResourceProcessUnproven("SQL broker returned an unrelated permission")
            return record
        except BaseException as failure:
            self.close()
            if isinstance(failure, (OSError, ValueError, TypeError, RuntimeError, psutil.Error)):
                if not sent:
                    raise SqlStartupPermissionUnavailable(
                        "SQL broker transport is unavailable"
                    ) from failure
                error = SqlBrokerUnavailable(
                    "SQL broker did not establish a physical opening permission"
                )
                if request is not None:
                    error.journal_id = request.journal_id
                    error.intent_id = request.intent_id
                    error.sequence = request.sequence
                raise error from failure
            raise
        finally:
            self.exchange.release()


def _attach_sql_broker(permission, *, parent_pid, parent, validate_process, timeout_s, startup_fd):
    raw = os.environ.pop(SQL_BROKER_FD_ENV, None)
    if raw is None:
        return
    if not raw.isdecimal() or int(raw) < 3 or int(raw) == startup_fd:
        raise ResourceProcessUnproven("SQL broker requires its distinct inherited channel")
    channel = socket.socket(fileno=int(raw))
    try:
        os.set_inheritable(channel.fileno(), False)
        peer = _CREDENTIALS.unpack(
            channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _CREDENTIALS.size)
        )
        if (
            channel.family != socket.AF_UNIX
            or channel.type != socket.SOCK_SEQPACKET
            or peer[:2] != (parent_pid, os.getuid())
        ):
            raise ResourceProcessUnproven("SQL broker channel parent is unproven")
        channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        client = _SqlBrokerClient(
            channel, permission, parent_pid, parent, validate_process, timeout_s
        )
        _install_sql_broker(permission, client.request, close=client.close)
    except BaseException:
        channel.close()
        raise


def _close_uninstalled_broker():
    raw = os.environ.pop(SQL_BROKER_FD_ENV, None)
    if raw is not None and raw.isdecimal() and int(raw) >= 3:
        try:
            os.close(int(raw))
        except OSError:
            pass


class ResourceProcessSupervisor:
    """Single PID/thread owner of one child, its IPC and its pinned kernel handle."""

    _owner_pid: int
    _broker_channel: socket.socket | None
    _broker_startup: ChildSqlOpeningIntent | None
    _broker_control: Connection | None
    _broker_failed: bool
    _broker_receiving: bool
    _broker_sequence: int
    _broker_charges: dict[str, ChildSqlSessionCharge]
    _first_sql_grant: bool
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
        first_sql_grant: bool = False,
        sql_broker: bool = False,
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
        if (
            type(first_sql_grant) is not bool
            or type(sql_broker) is not bool
            or (sql_broker and not first_sql_grant)
        ):
            raise ValueError("SQL startup and broker modes must be explicit")
        environment = dict(os.environ if env is None else env)
        if SQL_STARTUP_PERMISSION_ENV in environment or SQL_BROKER_FD_ENV in environment:
            raise ResourceProcessUnproven(
                "resource process cannot inherit another SQL startup mode"
            )
        if first_sql_grant:
            environment[SQL_STARTUP_PERMISSION_ENV] = "1"
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
        value._first_sql_grant = first_sql_grant
        value._owner_thread = threading.current_thread()
        value._child_sql_session_charge = None
        value._broker_channel = None
        value._broker_startup = None
        value._broker_control = None
        value._broker_failed = False
        value._broker_receiving = False
        value._broker_sequence = 0
        value._broker_charges = {}
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
        broker_child = None
        try:
            inherited = [child.fileno()]
            if sql_broker:
                value._broker_channel, broker_child = socket.socketpair(
                    socket.AF_UNIX, socket.SOCK_SEQPACKET
                )
                value._broker_channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
                environment[SQL_BROKER_FD_ENV] = str(broker_child.fileno())
                inherited.append(broker_child.fileno())
            parent.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            environment[BINDING_FD_ENV] = str(child.fileno())
            value._process = subprocess.Popen(
                list(argv), env=environment, close_fds=True, pass_fds=tuple(inherited)
            )
        except BaseException as error:
            parent.close()
            if value._broker_channel is not None:
                value._broker_channel.close()
            # Losing the Popen handle does NOT establish no-start or cleanup.
            _add_failure_note(
                error, "Child start is unobserved; no resource exit proof was produced."
            )
            raise
        finally:
            child.close()
            if broker_child is not None:
                broker_child.close()
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
            if value._broker_channel is not None:
                value._broker_channel.close()
        except BaseException as error:
            value._binding_failed = True
            parent.close()
            if value._broker_channel is not None:
                value._broker_channel.close()
            # Keep the first exception and the exact physical child owner.
            setattr(error, "resource_process_owner", value)
            _add_failure_note(error, "Resource child still requires its original owner's join.")
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
        """B/C1 registration and optional prepayment; ACK is identity only."""
        return self._receive_registered_binding(
            channel=channel,
            repository=repository,
            journal_id=journal_id,
            intent_id=intent_id,
            destination=destination,
            path=path,
            timeout_s=timeout_s,
            session_reservation=session_reservation,
        )

    def receive_binding_with_child_grant(
        self,
        *,
        channel: Connection,
        repository: SqlIntentRepository,
        journal_id: str,
        intent_id: str,
        destination: DatabaseTarget,
        session_reservation: object,
        path: str = "native",
        timeout_s: float = 30,
    ) -> ChildSqlOpeningIntent:
        """Explicit protocol3: confirmed C1 debit and B row precede one SQL permission."""
        self._check_owner()
        if session_reservation is None:
            raise ValueError("SQL startup permission requires an original data reservation")
        return self._receive_registered_binding(
            channel=channel,
            repository=repository,
            journal_id=journal_id,
            intent_id=intent_id,
            destination=destination,
            path=path,
            timeout_s=timeout_s,
            session_reservation=session_reservation,
            first_sql_grant=True,
        )

    def _receive_registered_binding(
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
        first_sql_grant: bool = False,
    ) -> ChildSqlOpeningIntent:
        """Shared paid registration; only explicit protocol3 can carry a SQL permission."""
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
        permission_packet: bytes | None = None

        def register(subject: VerifiedChildSqlSubject) -> None:
            nonlocal receipt, permission_packet
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
                **(
                    {"session_charge": self._child_sql_session_charge}
                    if self._broker_channel is not None
                    else {}
                ),
            )
            if type(result) is not ChildSqlOpeningIntent or result.subject is not subject.binding:
                raise SqlIntentCommitUncertain(journal_id, intent_id)
            receipt = result
            if self._child_sql_session_charge is not None:
                self._child_sql_session_charge.check()
            if first_sql_grant:
                charge = self._child_sql_session_charge
                if (
                    charge is None
                    or (self._broker_channel is not None and charge._intent_id != result.intent_id)
                    or charge.subject is not subject.binding
                    or result.journal_id != str(UUID(journal_id))
                    or result.intent_id != str(UUID(intent_id))
                    or result.path != path
                    or result.destination_sha256 != sql_startup_destination_sha256(destination)
                    or result.subject_sql_application_name != subject.sql_application_name
                ):
                    raise SqlIntentCommitUncertain(journal_id, intent_id)
                record = SqlStartupPermissionRecord(
                    session_id=charge.session_id,
                    journal_id=result.journal_id,
                    intent_id=result.intent_id,
                    subject_binding_sha256=sql_startup_binding_sha256(subject.binding),
                    sql_application_name=subject.sql_application_name,
                    path=path,
                    destination_sha256=result.destination_sha256,
                    destination_scope=destination.scope,
                )
                permission_packet = json.dumps(
                    record.to_packet(), separators=(",", ":"), allow_nan=False
                ).encode()

        def grant_reply() -> bytes:
            if permission_packet is None or self._child_sql_session_charge is None:
                raise ResourceProcessUnproven("SQL startup has no funded committed permission")
            self._child_sql_session_charge.check()
            return permission_packet

        try:
            self._receive_binding(
                timeout_s=timeout_s,
                before_ack=register,
                first_sql_reply=grant_reply if first_sql_grant else None,
            )
        except BaseException as failure:
            if self._broker_channel is not None:
                self._broker_failed = True
                self._broker_channel.close()
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
        if self._broker_channel is not None:
            self._broker_startup = receipt
            self._broker_control = channel
        return receipt

    @property
    def child_sql_session_charges(self) -> tuple[ChildSqlSessionCharge, ...]:
        """Retained bounded-by-reservation obligations, not physical close evidence."""
        self._check_owner()
        initial = (
            () if self._child_sql_session_charge is None else (self._child_sql_session_charge,)
        )
        return initial + tuple(self._broker_charges.values())

    def serve_child_sql_request(
        self,
        *,
        channel: Connection,
        repository: SqlIntentRepository,
        destination: DatabaseTarget,
        session_reservation: object,
        timeout_s: float = 30,
    ) -> ChildSqlFollowupIntent | SqlBrokerIdle | None:
        """Serve one paid request on the original owner; EOF is transport closure only."""
        from cli_agent_orchestrator.clients.sql_intent_repository import (
            SqlFollowupAckUnavailableAfterCommit,
            SqlIntentCommitUncertain,
            SqlIntentRepository,
        )
        from cli_agent_orchestrator.models.sql_opening_intent import ChildSqlFollowupIntent

        self._check_owner()
        _timeout(timeout_s)
        broker = self._broker_channel
        startup = self._broker_startup
        if (
            broker is None
            or startup is None
            or self._closed
            or self._broker_failed
            or self._broker_receiving
            or self._binding is None
            or self._identity is None
            or type(repository) is not SqlIntentRepository
            or channel is not self._broker_control
        ):
            raise ResourceProcessUnproven("SQL broker requires its original confirmed startup")
        channel._check_funded_worker()
        self._broker_receiving = True
        token = object()
        self._subject_token = token
        subject = None
        charge = None
        receipt = None
        request = None
        try:
            broker.settimeout(timeout_s)
            try:
                packet = _broker_receive(broker, self._process.pid, eof=True)
            except (TimeoutError, BlockingIOError):
                return SqlBrokerIdle.NO_REQUEST
            if packet is None:
                self._broker_failed = True
                broker.close()
                return None
            request = SqlBrokerRequest.from_packet(packet)
            if (
                request.journal_id != startup.journal_id
                or request.startup_intent_id != startup.intent_id
                or request.sequence != self._broker_sequence + 1
                or request.intent_id == startup.intent_id
                or request.subject_binding_sha256 != sql_startup_binding_sha256(self._binding)
                or request.sql_application_name != self._sql_application_name
                or request.destination_sha256 != sql_startup_destination_sha256(destination)
                or request.destination_scope != destination.scope
            ):
                raise ResourceProcessUnproven("SQL broker request differs from its bound operation")
            live = _identity(self._process.pid)
            if (
                live != self._identity
                or not _matches(self._binding, live)
                or self._pidfd is None
                or _exit_ready(self._pidfd) is not False
            ):
                raise ResourceProcessUnproven("SQL broker child view is unavailable")

            def revalidate(value: VerifiedChildSqlSubject) -> None:
                self._check_owner()
                if (
                    not self._broker_receiving
                    or self._broker_failed
                    or self._closed
                    or self._exited
                    or broker.fileno() < 0
                    or value not in self._issued_subjects
                    or value._handshake_token is not token
                    or self._subject_token is not token
                    or self._pidfd is None
                    or _exit_ready(self._pidfd) is not False
                    or _identity(self._process.pid) != live
                ):
                    raise ResourceProcessUnproven("original SQL broker request is no longer live")

            poller = select.poll()
            hangup = select.POLLHUP | select.POLLERR | getattr(select, "POLLRDHUP", 0)
            poller.register(broker.fileno(), hangup)
            if any(flags & hangup for _, flags in poller.poll(0)):
                raise ResourceProcessUnproven("SQL broker peer already closed before financing")
            subject = _issue_verified_child_sql_subject(
                self._binding,
                sql_application_name=self._sql_application_name,
                boot_id=live.boot_id,
                start_time_ticks=live.ticks,
                pid_namespace=live.pid_ns,
                validate=revalidate,
                handshake_token=token,
            )
            self._issued_subjects.add(subject)
            _approve_verified_child_sql_subject(subject)
            charge = channel.reserve_child_sql_session(
                reservation=session_reservation, subject=subject
            )
            self._broker_charges[charge.session_id] = charge
            result = repository.record_child_followup(
                channel,
                journal_id=request.journal_id,
                startup_intent_id=request.startup_intent_id,
                intent_id=request.intent_id,
                sequence=request.sequence,
                subject=subject,
                destination=destination,
                path=request.path,
                session_charge=charge,
            )
            if (
                type(result) is not ChildSqlFollowupIntent
                or (
                    result.intent_id,
                    result.journal_id,
                    result.startup_intent_id,
                    result.sequence,
                    result.path,
                    result.destination_sha256,
                    result.destination_scope,
                    result.subject_binding_sha256,
                    result.subject_sql_application_name,
                )
                != (
                    request.intent_id,
                    request.journal_id,
                    request.startup_intent_id,
                    request.sequence,
                    request.path,
                    request.destination_sha256,
                    request.destination_scope,
                    request.subject_binding_sha256,
                    request.sql_application_name,
                )
                or charge._intent_id != request.intent_id
            ):
                raise SqlIntentCommitUncertain(request.journal_id, request.intent_id)
            receipt = result
            charge.check()
            subject.check()
            record = SqlBrokerPermissionRecord(
                session_id=charge.session_id,
                journal_id=receipt.journal_id,
                startup_intent_id=receipt.startup_intent_id,
                intent_id=receipt.intent_id,
                sequence=receipt.sequence,
                subject_binding_sha256=receipt.subject_binding_sha256,
                sql_application_name=receipt.subject_sql_application_name,
                path=receipt.path,
                destination_sha256=receipt.destination_sha256,
                destination_scope=receipt.destination_scope,
            )
            _broker_send(broker, record.to_packet())
            self._broker_sequence = request.sequence
            return receipt
        except BaseException as failure:
            self._broker_failed = True
            broker.close()
            if request is not None:
                setattr(failure, "sql_broker_request", request)
            if charge is None:
                charge = getattr(failure, "child_sql_session_charge", None)
            if charge is not None:
                self._broker_charges[charge.session_id] = charge
                setattr(failure, "child_sql_session_charge", charge)
            if receipt is not None and not isinstance(
                failure, (SqlFollowupAckUnavailableAfterCommit, SqlIntentCommitUncertain)
            ):
                error = SqlFollowupAckUnavailableAfterCommit(receipt)
                if charge is not None:
                    setattr(error, "child_sql_session_charge", charge)
                if request is not None:
                    setattr(error, "sql_broker_request", request)
                raise error from failure
            raise
        finally:
            try:
                if subject is not None:
                    _expire_verified_child_sql_subject(subject)
            finally:
                self._issued_subjects.clear()
                self._subject_token = None
                self._broker_receiving = False

    def _receive_binding(
        self,
        *,
        timeout_s: float = 30,
        before_ack: Callable[[VerifiedChildSqlSubject], object] | None = None,
        first_sql_reply: Callable[[], bytes] | None = None,
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
        if self._first_sql_grant != (first_sql_reply is not None):
            self._binding_failed = True
            self._channel.close()
            raise ResourceProcessUnproven(
                "SQL startup protocol requires its explicit funded receiver"
            )
        if before_ack is not None and not callable(before_ack):
            raise TypeError("resource process registration must be callable")
        self._receiving = True
        self._subject_token = object()
        subject = None
        stage: ResourceProcessFailureStage = "channel_setup"
        try:
            self._channel.settimeout(timeout_s)
            stage = "recvmsg"
            payload, ancillary, flags, _ = self._channel.recvmsg(
                _MAX_PACKET_BYTES + 1,
                socket.CMSG_SPACE(_CREDENTIALS.size) + socket.CMSG_SPACE(struct.calcsize("i")),
                socket.MSG_CMSG_CLOEXEC,
            )
            # recvmsg installs SCM_RIGHTS descriptors before userspace validates
            # the packet, including a truncated/rejected ancillary envelope.
            stage = "ancillary"
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
            stage = "packet_frame"
            if (
                not payload
                or len(payload) > _MAX_PACKET_BYTES
                or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
            ):
                raise ResourceProcessUnproven("resource binding packet is incomplete")
            stage = "sender_credentials"
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
            stage = "packet_decode"
            packet = json.loads(payload, object_pairs_hook=_unique_object)
            stage = "packet_schema"
            if (
                not isinstance(packet, dict)
                or set(packet) != {"version", "binding", "kernel", "sql_application_name"}
                or type(packet["version"]) is not int
                or packet["version"]
                != (
                    SQL_STARTUP_PERMISSION_VERSION
                    if self._first_sql_grant
                    else BINDING_PROTOCOL_VERSION
                )
                or not is_sql_process_application_name(packet["sql_application_name"])
                or not isinstance(packet["binding"], dict)
                or set(packet["binding"]) != {f.name for f in fields(ResourceBinding)}
            ):
                raise ResourceProcessUnproven("resource binding packet schema is invalid")
            stage = "binding_decode"
            binding = ResourceBinding(**packet["binding"])
            stage = "kernel_validation"
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

                stage = "subject_issue"
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
            reply = b"1" if first_sql_reply is None else first_sql_reply()
            if type(reply) is not bytes or not reply or len(reply) > _MAX_PACKET_BYTES:
                raise ResourceProcessUnproven("resource binding acknowledgement is invalid")
            if self._channel.send(reply) != len(reply):
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
        ) as failure:
            self._binding_failed = True
            if subject is not None:
                raise
            rejected = ResourceProcessUnproven("resource process binding could not be verified")
            rejected.failure_stage = stage
            rejected.failure_type = _binding_failure_type(failure)
            _add_failure_note(
                rejected,
                json.dumps(
                    {
                        "schema": "caos.resource-binding-rejection.v1",
                        "failure_stage": stage,
                        "failure_type": rejected.failure_type,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            raise rejected from None
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
        if self._broker_channel is not None:
            self._broker_channel.close()
        if self._pidfd is not None:
            os.close(self._pidfd)
            self._pidfd = None
        # Only finalizes Popen bookkeeping after actual waitid/pinned exit;
        # its synthesized ECHILD return value is never evidence or exposed.
        self._process.poll()
        self._closed = True
