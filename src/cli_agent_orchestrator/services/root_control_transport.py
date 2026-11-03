"""Authenticated, bounded private transport for an already-funded Root child."""

from __future__ import annotations

import asyncio
import math
import os
import socket
import struct
import threading
import weakref
from typing import Literal, NoReturn, SupportsIndex

from cli_agent_orchestrator.models.root_control_protocol import (
    ROOT_CONTROL_ACTIVATION_MAX_BYTES,
    ROOT_CONTROL_ERROR_CODE,
    ROOT_CONTROL_FD_ENV,
    ROOT_CONTROL_MAX_COMMAND_BYTES,
    ROOT_CONTROL_MAX_FRAME_BYTES,
    RootControlReply,
    RootControlRequest,
    decode_root_control_request,
    encode_root_control_reply,
)
from cli_agent_orchestrator.models.sql_process_origin import RESOURCE_BINDING_FD_ENV
from cli_agent_orchestrator.models.sql_startup_permission import (
    SQL_BROKER_FD_ENV,
    SQL_ROOT_GENESIS_ENV,
    SQL_STARTUP_PERMISSION_ENV,
)

RootCommand = Literal["refresh", "stop"]
_CREDENTIALS = struct.Struct("3i")
_DEFAULT_IDLE_TIMEOUT = 1.0
_MAX_IDLE_TIMEOUT = 1.0
_SEND_TIMEOUT = 5.0
_MAX_RIGHTS = 64


class RootControlTransportUnavailable(RuntimeError):
    """The private Root control endpoint could not be authenticated or used."""


class RootControlChannelEOF(EOFError):
    """The authenticated Root control peer closed its channel."""


def _current_async_task() -> object | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _timeout(value: object, *, idle: bool = False) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or (idle and value > _MAX_IDLE_TIMEOUT)
    ):
        raise ValueError("root control timeout must be finite and bounded")
    return float(value)


def _close_received_rights(ancillary: list[tuple[int, int, bytes]]) -> bool:
    found_rights = False
    for level, kind, raw in ancillary:
        if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS:
            continue
        found_rights = True
        size = struct.calcsize("i")
        for offset in range(0, len(raw) - len(raw) % size, size):
            try:
                os.close(struct.unpack_from("i", raw, offset)[0])
            except OSError:
                pass
    return found_rights


def _receive_frame(
    channel: socket.socket, *, timeout_s: float
) -> tuple[bytes, tuple[int, int, int]]:
    channel.settimeout(timeout_s)
    ancillary_size = socket.CMSG_SPACE(_CREDENTIALS.size) + socket.CMSG_SPACE(
        struct.calcsize("i") * _MAX_RIGHTS
    )
    try:
        payload, ancillary, flags, _ = channel.recvmsg(
            ROOT_CONTROL_MAX_FRAME_BYTES + 1,
            ancillary_size,
            socket.MSG_CMSG_CLOEXEC,
        )
    except socket.timeout:
        raise
    except BlockingIOError:
        raise socket.timeout() from None
    except OSError as error:
        raise RootControlTransportUnavailable() from error
    received_rights = _close_received_rights(ancillary)
    if received_rights:
        raise RootControlTransportUnavailable()
    if (
        flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
        or len(payload) > ROOT_CONTROL_MAX_FRAME_BYTES
    ):
        raise RootControlTransportUnavailable()
    if not payload and not ancillary:
        raise RootControlChannelEOF()
    if len(ancillary) != 1:
        raise RootControlTransportUnavailable()
    level, kind, raw = ancillary[0]
    if (
        level != socket.SOL_SOCKET
        or kind != socket.SCM_CREDENTIALS
        or len(raw) != _CREDENTIALS.size
    ):
        raise RootControlTransportUnavailable()
    if not payload:
        raise RootControlTransportUnavailable()
    return payload, _CREDENTIALS.unpack(raw)


_ROOT_SOCKET_OWNERS: weakref.WeakSet[object] = weakref.WeakSet()


def _register_root_socket_owner(owner: object) -> None:
    """Register a weak owner for the one process-wide fork-close hook."""
    _ROOT_SOCKET_OWNERS.add(owner)


def _after_fork_close_root_sockets() -> None:
    # Do not acquire inherited locks or call owner methods in the fork child.
    for owner in tuple(_ROOT_SOCKET_OWNERS):
        for name in ("_socket", "_root_channel"):
            try:
                channel = object.__getattribute__(owner, name)
            except (AttributeError, TypeError):
                continue
            if isinstance(channel, socket.socket):
                try:
                    channel.close()
                except OSError:
                    pass


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_close_root_sockets)


class RootControlChildEndpoint:
    """Non-transferable child endpoint; no SQL opening or process retirement."""

    _socket: socket.socket
    _pid: int
    _thread: threading.Thread
    _task: object | None
    _parent_pid: int
    _parent_uid: int
    _state: str
    _sequence: int
    _pending: RootControlRequest | None
    _gate: threading.Lock
    _fork_invalid: bool

    __slots__ = (
        "_socket",
        "_pid",
        "_thread",
        "_task",
        "_parent_pid",
        "_parent_uid",
        "_state",
        "_sequence",
        "_pending",
        "_gate",
        "_fork_invalid",
        "__weakref__",
    )

    def __init__(self) -> None:
        raise TypeError("root control endpoints must adopt a private descriptor")

    def __copy__(self) -> NoReturn:
        raise TypeError("root control endpoints are not transferable")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError("root control endpoints are not transferable")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("root control endpoints are not serializable")

    def _check_context(self) -> None:
        if (
            self._pid != os.getpid()
            or self._thread is not threading.current_thread()
            or self._task is not _current_async_task()
        ):
            raise PermissionError("root control endpoint belongs to another context")

    def _invalidate(self) -> None:
        self._state = "failed"
        try:
            self._socket.close()
        except OSError:
            pass

    def _check_parent(self) -> None:
        if self._fork_invalid or os.getppid() != self._parent_pid:
            self._invalidate()
            raise RootControlTransportUnavailable()
        try:
            peer_pid, peer_uid, peer_gid = _CREDENTIALS.unpack(
                self._socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _CREDENTIALS.size)
            )
            if (
                peer_pid != self._parent_pid
                or peer_uid != self._parent_uid
                or peer_gid != os.getgid()
            ):
                self._invalidate()
                raise RootControlTransportUnavailable()
        except RootControlTransportUnavailable:
            raise
        except BaseException as error:
            self._invalidate()
            raise RootControlTransportUnavailable() from error

    def _enter(self) -> None:
        self._check_context()
        if not self._gate.acquire(blocking=False):
            raise RootControlTransportUnavailable()
        if self._state in {"failed", "closed"}:
            self._gate.release()
            raise RootControlTransportUnavailable()

    def _receive(self, timeout_s: float) -> bytes | None:
        self._check_parent()
        try:
            payload, credentials = _receive_frame(self._socket, timeout_s=timeout_s)
        except socket.timeout:
            self._check_parent()
            return None
        except RootControlChannelEOF:
            self._invalidate()
            raise
        except BaseException as error:
            self._invalidate()
            if isinstance(error, RootControlTransportUnavailable):
                raise
            raise RootControlTransportUnavailable() from error
        if credentials[0] != self._parent_pid or credentials[1] != self._parent_uid:
            self._invalidate()
            raise RootControlTransportUnavailable()
        self._check_parent()
        return payload

    def _send_reply(self, reply: RootControlReply) -> None:
        self._check_parent()
        payload = encode_root_control_reply(reply)
        self._socket.settimeout(_SEND_TIMEOUT)
        try:
            sent = self._socket.send(payload)
        except OSError as error:
            self._invalidate()
            raise RootControlTransportUnavailable() from error
        if sent != len(payload):
            self._invalidate()
            raise RootControlTransportUnavailable()
        self._check_parent()

    def receive_activation(self, timeout_s: float = _DEFAULT_IDLE_TIMEOUT) -> bytes | None:
        timeout = _timeout(timeout_s, idle=True)
        self._enter()
        try:
            if self._state != "await_activation" or self._pending is not None:
                self._invalidate()
                raise RootControlTransportUnavailable()
            payload = self._receive(timeout)
            if payload is None:
                return None
            try:
                request = decode_root_control_request(payload)
                if request.type != "activation" or request.sequence != 0:
                    raise ValueError("invalid initial request")
            except ValueError as error:
                self._invalidate()
                raise RootControlTransportUnavailable() from error
            self._pending = request
            self._state = "activation_received"
            return request.packet
        finally:
            self._gate.release()

    def send_ready(self) -> None:
        self._enter()
        try:
            if self._state != "activation_received" or self._pending is None:
                self._invalidate()
                raise RootControlTransportUnavailable()
            self._send_reply(RootControlReply(0, "ready"))
            self._pending = None
            self._sequence = 1
            self._state = "active"
        finally:
            self._gate.release()

    def receive_command(self, timeout_s: float = _DEFAULT_IDLE_TIMEOUT) -> RootCommand | None:
        timeout = _timeout(timeout_s, idle=True)
        self._enter()
        try:
            if self._state != "active" or self._pending is not None:
                self._invalidate()
                raise RootControlTransportUnavailable()
            payload = self._receive(timeout)
            if payload is None:
                return None
            try:
                request = decode_root_control_request(payload)
                if request.type not in {"refresh", "stop"} or request.sequence != self._sequence:
                    raise ValueError("invalid command sequence")
            except ValueError as error:
                self._invalidate()
                raise RootControlTransportUnavailable() from error
            self._pending = request
            if request.type == "refresh":
                return "refresh"
            if request.type == "stop":
                return "stop"
            self._invalidate()
            raise RootControlTransportUnavailable()
        finally:
            self._gate.release()

    def send_refresh(self, reason: str) -> None:
        self._enter()
        try:
            pending = self._pending
            if self._state != "active" or pending is None or pending.type != "refresh":
                self._invalidate()
                raise RootControlTransportUnavailable()
            try:
                reply = RootControlReply(pending.sequence, "refresh", reason=reason)
            except ValueError as error:
                self._invalidate()
                raise RootControlTransportUnavailable() from error
            self._send_reply(reply)
            self._pending = None
            self._sequence += 1
        finally:
            self._gate.release()

    def send_stopping(self) -> None:
        self._enter()
        try:
            pending = self._pending
            if self._state != "active" or pending is None or pending.type != "stop":
                self._invalidate()
                raise RootControlTransportUnavailable()
            self._send_reply(RootControlReply(pending.sequence, "stopping"))
            self._pending = None
            self._sequence += 1
            self._state = "stopping"
        finally:
            self._gate.release()

    def send_error(self, code: str = ROOT_CONTROL_ERROR_CODE) -> None:
        self._enter()
        try:
            pending = self._pending
            if pending is None or code != ROOT_CONTROL_ERROR_CODE:
                self._invalidate()
                raise RootControlTransportUnavailable()
            self._send_reply(RootControlReply(pending.sequence, "error", code=code))
            self._pending = None
            self._state = "failed"
        finally:
            self._gate.release()

    def close(self) -> None:
        self._check_context()
        if not self._gate.acquire(blocking=False):
            raise RootControlTransportUnavailable()
        try:
            if self._state == "closed":
                return
            try:
                self._socket.close()
            except OSError as error:
                self._state = "failed"
                raise RootControlTransportUnavailable() from error
            self._state = "closed"
            self._pending = None
        finally:
            self._gate.release()


def open_root_control_endpoint() -> RootControlChildEndpoint | None:
    """Adopt the supervisor's private FD before any Root bootstrap work.

    No marker is the legacy path. A present but malformed or unauthenticated
    marker is terminal and must never fall back to stdio.
    """
    raw = os.environ.pop(ROOT_CONTROL_FD_ENV, None)
    if raw is None:
        return None
    try:
        binding_raw = os.environ.get(RESOURCE_BINDING_FD_ENV)
        if (
            os.environ.get(SQL_ROOT_GENESIS_ENV) != "1"
            or SQL_STARTUP_PERMISSION_ENV in os.environ
            or SQL_BROKER_FD_ENV in os.environ
            or binding_raw is None
            or not binding_raw.isdecimal()
            or str(int(binding_raw)) != binding_raw
            or not raw.isdecimal()
            or str(int(raw)) != raw
        ):
            raise ValueError("root control environment is invalid")
        fd = int(raw)
        if fd < 3 or fd == int(binding_raw):
            raise ValueError("root control descriptor is invalid")
        if not hasattr(socket, "SO_PEERCRED") or not hasattr(socket, "SCM_CREDENTIALS"):
            raise ValueError("root control credentials are unavailable")
        channel = socket.socket(fileno=fd)
        try:
            channel.set_inheritable(False)
            if (
                channel.family != socket.AF_UNIX
                or channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_SEQPACKET
            ):
                raise ValueError("root control descriptor type is invalid")
            peer_pid, peer_uid, peer_gid = _CREDENTIALS.unpack(
                channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _CREDENTIALS.size)
            )
            parent_pid = os.getppid()
            if peer_pid != parent_pid or peer_uid != os.getuid() or peer_gid != os.getgid():
                raise ValueError("root control parent credentials are invalid")
            if os.getppid() != parent_pid:
                raise ValueError("root control parent identity changed")
            channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            endpoint = object.__new__(RootControlChildEndpoint)
            endpoint._socket = channel
            endpoint._pid = os.getpid()
            endpoint._thread = threading.current_thread()
            endpoint._task = _current_async_task()
            endpoint._parent_pid = parent_pid
            endpoint._parent_uid = peer_uid
            endpoint._state = "await_activation"
            endpoint._sequence = 0
            endpoint._pending = None
            endpoint._gate = threading.Lock()
            endpoint._fork_invalid = False
            _register_root_socket_owner(endpoint)
            return endpoint
        except BaseException:
            channel.close()
            raise
    except RootControlTransportUnavailable:
        raise
    except BaseException as error:
        raise RootControlTransportUnavailable() from error
