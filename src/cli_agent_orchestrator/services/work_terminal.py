"""Server-owned terminal identity and dispatch coordination for Work terminals."""

from __future__ import annotations

import hashlib
import struct
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Callable, ParamSpec, TypeVar, cast

from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget, connect
from cli_agent_orchestrator.constants import SESSION_PREFIX
from cli_agent_orchestrator.utils.terminal import validate_tmux_name

_terminal_identity: ContextVar[str | None] = ContextVar("managed_work_terminal_id", default=None)
_TERMINAL_DISPATCH_LOCK_TIMEOUT_SECONDS = 10.0
_TERMINAL_DISPATCH_LOCK_POLL_SECONDS = 0.05
_active_terminal_dispatch_lock: ContextVar[tuple[str, _TerminalDispatchLock] | None] = ContextVar(
    "active_terminal_dispatch_lock", default=None
)


class TerminalDispatchLockError(RuntimeError):
    """Terminal dispatch coordination could not be established safely."""


class _TerminalDispatchLock:
    def __init__(self, target, terminal_id):
        self.target = target
        self.terminal_id = terminal_id
        self._entered = False
        self._connection: Connection | None = None
        self._lock_key: int | None = None

    def __enter__(self):
        if self._entered:
            raise RuntimeError("terminal dispatch lock context cannot be reused")
        self._entered = True
        if not isinstance(self.terminal_id, str) or not self.terminal_id:
            raise ValueError("terminal dispatch lock requires a terminal id")
        if not isinstance(self.target, DatabaseTarget):
            raise ValueError("terminal dispatch lock requires an explicit PostgreSQL target")
        target = self.target
        identity = "\0".join(map(str, (*target.identity, self.terminal_id)))
        lock_key = struct.unpack(">q", hashlib.sha256(identity.encode("utf-8")).digest()[:8])[0]
        connection = connect(target)
        try:
            deadline = time.monotonic() + _TERMINAL_DISPATCH_LOCK_TIMEOUT_SECONDS
            while True:
                acquired = connection.execute(
                    "SELECT pg_try_advisory_lock(%s)", (lock_key,)
                ).fetchone()
                if acquired is not None and acquired[0]:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("timed out waiting for terminal dispatch lock")
                time.sleep(min(_TERMINAL_DISPATCH_LOCK_POLL_SECONDS, remaining))
            self._connection = connection
            self._lock_key = lock_key
            return self
        except BaseException:
            connection.close()
            raise

    def release(self):
        """Release the advisory lock once; safe for explicit release plus context exit."""
        connection = self._connection
        lock_key = self._lock_key
        if connection is None or lock_key is None:
            return
        self._connection = None
        self._lock_key = None
        try:
            connection.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
        finally:
            connection.close()

    def __exit__(self, _exc_type, _exc, _traceback):
        self.release()
        return False


def terminal_dispatch_lock(target: DatabaseTarget, terminal_id):
    """Hold a PostgreSQL advisory lock for one logical store/terminal pair."""
    return _TerminalDispatchLock(target, terminal_id)


def release_terminal_dispatch_lock(terminal_id):
    """Release the active service send lock before noncritical callbacks."""
    active = _active_terminal_dispatch_lock.get()
    if active is None or active[0] != terminal_id:
        return False
    active[1].release()
    return True


_TerminalCall = ParamSpec("_TerminalCall")
_TerminalResult = TypeVar("_TerminalResult")


def with_terminal_dispatch_lock(
    function: Callable[_TerminalCall, _TerminalResult],
) -> Callable[_TerminalCall, _TerminalResult]:
    """Serialize a terminal send from ownership lookup through its receipt CAS."""

    @wraps(function)
    def wrapped(terminal_id, *args, **kwargs) -> _TerminalResult:
        with terminal_dispatch_lock(
            DatabaseTarget.from_settings(scope="main"), terminal_id
        ) as lock:
            token = _active_terminal_dispatch_lock.set((terminal_id, lock))
            try:
                return function(terminal_id, *args, **kwargs)
            finally:
                _active_terminal_dispatch_lock.reset(token)

    # The wrapper forwards every argument and return value without changing the
    # decorated callable signature, including a keyword terminal_id.
    return cast(Callable[_TerminalCall, _TerminalResult], wrapped)


def current_managed_terminal_id():
    """Return the identity reserved by the active protected delivery, if any."""
    return _terminal_identity.get()


@contextmanager
def managed_terminal_identity(terminal_id):
    """Expose a durable terminal identity only while its port remains live."""
    token = _terminal_identity.set(terminal_id)
    try:
        yield
    finally:
        _terminal_identity.reset(token)


def managed_session_name(session_name):
    """Normalize and validate the session name sealed by a managed launch."""
    if not isinstance(session_name, str) or not session_name:
        raise ValueError("managed Work session name is required")
    if not session_name.startswith(SESSION_PREFIX):
        session_name = f"{SESSION_PREFIX}{session_name}"
    if session_name.startswith(f"{SESSION_PREFIX}flow-"):
        raise ValueError("managed Work sessions cannot use the Flow session namespace")
    return validate_tmux_name(session_name, "session_name")


def managed_window_name(agent_profile, terminal_id):
    """Stable reserved initial pane name for one durable Work terminal id."""
    if (
        not isinstance(agent_profile, str)
        or not agent_profile
        or not isinstance(terminal_id, str)
        or len(terminal_id) != 8
        or any(character not in "0123456789abcdef" for character in terminal_id)
    ):
        raise ValueError("managed Work window identity is invalid")
    return validate_tmux_name(f"{agent_profile}-{terminal_id[:4]}", "window_name")
