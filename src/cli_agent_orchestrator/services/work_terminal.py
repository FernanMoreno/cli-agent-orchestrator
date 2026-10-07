"""Server-owned terminal identity and dispatch coordination for Work terminals."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from types import ModuleType
from typing import Callable, ParamSpec, TypeVar, cast

from cli_agent_orchestrator import constants

fcntl: ModuleType | None
try:
    import fcntl as _fcntl

    fcntl = _fcntl
except ImportError:  # pragma: no cover - exercised only on non-Unix platforms
    fcntl = None

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
    def __init__(self, database_path, terminal_id):
        self.database_path = database_path
        self.terminal_id = terminal_id
        self._entered = False
        self._fd = None

    def __enter__(self):
        if self._entered:
            raise RuntimeError("terminal dispatch lock context cannot be reused")
        self._entered = True
        if fcntl is None:
            raise TerminalDispatchLockError("terminal dispatch requires interprocess flock support")
        if not isinstance(self.terminal_id, str) or not self.terminal_id:
            raise ValueError("terminal dispatch lock requires a terminal id")
        try:
            canonical_database_path = Path(self.database_path).resolve(strict=False)
        except (TypeError, OSError, RuntimeError) as error:
            raise ValueError("terminal dispatch database path is invalid") from error

        key = f"{canonical_database_path}\0{self.terminal_id}"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        lock_directory = Path(constants.LOCK_DIR)
        lock_path = lock_directory / f"terminal-dispatch-{digest}.lock"
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)

        try:
            lock_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if lock_directory.is_symlink() or not lock_directory.is_dir():
                raise TerminalDispatchLockError("terminal dispatch lock directory is unsafe")
            os.chmod(lock_directory, 0o700)
            lock_fd = os.open(lock_path, flags, 0o600)
        except TerminalDispatchLockError:
            raise
        except OSError as error:
            raise TerminalDispatchLockError(
                "terminal dispatch lock could not be created safely"
            ) from error

        try:
            os.fchmod(lock_fd, 0o600)
            if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                raise TerminalDispatchLockError("terminal dispatch lock file is unsafe")
            deadline = time.monotonic() + _TERMINAL_DISPATCH_LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN):
                        raise TerminalDispatchLockError(
                            "terminal dispatch lock could not be acquired"
                        ) from error
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(
                            "timed out waiting for terminal dispatch lock"
                        ) from error
                    time.sleep(min(_TERMINAL_DISPATCH_LOCK_POLL_SECONDS, remaining))
            self._fd = lock_fd
            return self
        except BaseException:
            try:
                os.close(lock_fd)
            except OSError:
                pass
            raise

    def release(self):
        """Release the flock once; safe for explicit release plus context exit."""
        lock_fd = self._fd
        if lock_fd is None:
            return
        self._fd = None
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    def __exit__(self, _exc_type, _exc, _traceback):
        self.release()
        return False


def terminal_dispatch_lock(database_path, terminal_id):
    """Hold a strict interprocess lock for one canonical store/terminal pair.

    Lock files live in CAO's private lock directory and are intentionally kept
    after release: unlinking a flock inode can let two processes lock different
    files for the same terminal. The same helper is used by ordinary terminal
    sends and registered Work dispatch so both paths derive exactly one key.
    """
    return _TerminalDispatchLock(database_path, terminal_id)


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
        with terminal_dispatch_lock(constants.DATABASE_FILE, terminal_id) as lock:
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
