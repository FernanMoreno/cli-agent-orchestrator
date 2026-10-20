"""Bounded native connection reuse for legacy-memory audit storage only.

This module is intentionally private. Generic WorkRepository instances keep
their direct connection lifecycle; MemoryService's legacy audit repository is
the only production caller of the process-wide manager.
"""

from __future__ import annotations

import atexit
import hashlib
import hmac
import json
import os
import secrets
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, cast

import psycopg
from psycopg.pq import TransactionStatus

from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget
from cli_agent_orchestrator.clients.postgres.connection import (
    ConnectionCursor,
    ConnectionRaw,
    record_factory,
)
from cli_agent_orchestrator.clients.work_repository import WorkRepository


class PooledAuditLeaseExpired(RuntimeError):
    """A retained pooled connection or cursor was used after lease return."""


class StaleMemoryAuditTarget(RuntimeError):
    """A repository's configured logical target changed after it was created."""


class UnsupportedLibpqEnvironment(RuntimeError):
    """An inherited libpq setting is outside the private lease profile."""


_REPRESENTED_ENVIRONMENT = {
    "PGHOST": "host",
    "PGPORT": "port",
    "PGDATABASE": "dbname",
    "PGUSER": "user",
    "PGSSLMODE": "sslmode",
    "PGCONNECT_TIMEOUT": "connect_timeout",
    "PGAPPNAME": "application_name",
}
_TLS_DEFAULT_FILES = (
    "root.crt",
    "root.crl",
    "postgresql.crt",
    "postgresql.key",
)
_FORKED_RAW_CONNECTIONS: list[psycopg.Connection] = []


def _target_base(target: DatabaseTarget) -> tuple[object, ...]:
    return (target.scope, target.identity, target.user)


def _target_resolver(target: DatabaseTarget) -> DatabaseTarget:
    return cast(DatabaseTarget, DatabaseTarget.from_settings(scope=target.scope, home=target.home))


def _assert_same_repository_target(expected: DatabaseTarget, resolved: DatabaseTarget) -> None:
    if (
        expected.scope != resolved.scope
        or expected.identity != resolved.identity
        or expected.user != resolved.user
    ):
        raise StaleMemoryAuditTarget(
            "legacy memory audit repository target changed; create a new repository"
        )


def _private_file_fingerprint(
    path: Path, *, key: bytes, private: bool, label: str
) -> tuple[object, ...]:
    """Return metadata plus a process-keyed content digest without retaining bytes."""
    path = Path(path).expanduser()
    try:
        before = path.lstat()
    except FileNotFoundError:
        return (str(path.absolute()), "missing")
    except OSError as exc:
        raise UnsupportedLibpqEnvironment(f"cannot inspect effective {label}") from exc

    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise UnsupportedLibpqEnvironment(f"effective {label} must be a regular file")
    tls_file = label.startswith("TLS ")
    if tls_file:
        if before.st_uid not in {0, os.geteuid()}:
            raise UnsupportedLibpqEnvironment(f"effective {label} has an unexpected owner")
        if before.st_mode & 0o022:
            raise UnsupportedLibpqEnvironment(f"effective {label} is writable by another user")
        if label == "TLS postgresql.key" and before.st_mode & 0o007:
            raise UnsupportedLibpqEnvironment(f"effective {label} is accessible to other users")
    else:
        if before.st_uid != os.geteuid():
            raise UnsupportedLibpqEnvironment(f"effective {label} has an unexpected owner")
        if private and before.st_mode & 0o077:
            raise UnsupportedLibpqEnvironment(f"effective {label} is not private")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise UnsupportedLibpqEnvironment(f"cannot open effective {label}") from exc
    digest = hmac.new(key, digestmod=hashlib.sha256)
    try:
        current = os.fstat(descriptor)
        allowed_owners = {0, os.geteuid()} if tls_file else {os.geteuid()}
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_uid not in allowed_owners
            or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise UnsupportedLibpqEnvironment(f"effective {label} changed while profiling")
        if tls_file:
            if current.st_mode & 0o022:
                raise UnsupportedLibpqEnvironment(f"effective {label} is writable by another user")
            if label == "TLS postgresql.key" and current.st_mode & 0o007:
                raise UnsupportedLibpqEnvironment(f"effective {label} is accessible to other users")
        elif private and current.st_mode & 0o077:
            raise UnsupportedLibpqEnvironment(f"effective {label} is not private")
        while True:
            block = os.read(descriptor, 64 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(descriptor)
        if (current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise UnsupportedLibpqEnvironment(f"effective {label} changed while profiling")
    finally:
        os.close(descriptor)
    return (
        str(path.absolute()),
        current.st_dev,
        current.st_ino,
        current.st_uid,
        stat.S_IMODE(current.st_mode),
        current.st_size,
        current.st_mtime_ns,
        current.st_ctime_ns,
        digest.hexdigest(),
    )


def _check_libpq_environment(parameters: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Reject inherited libpq inputs not fixed by explicit connection keywords."""
    expected = {
        "PGHOST": str(parameters["host"]),
        "PGPORT": str(parameters["port"]),
        "PGDATABASE": str(parameters["dbname"]),
        "PGUSER": str(parameters["user"]),
        "PGSSLMODE": str(parameters["sslmode"]),
        "PGCONNECT_TIMEOUT": str(parameters["connect_timeout"]),
        "PGAPPNAME": str(parameters["application_name"]),
    }
    inherited: list[tuple[str, str]] = []
    for name, value in os.environ.items():
        if not name.startswith("PG") or not value:
            continue
        if name == "PGPASSWORD":
            raise UnsupportedLibpqEnvironment(
                "PGPASSWORD is not accepted by the private audit connection path"
            )
        if name not in _REPRESENTED_ENVIRONMENT:
            raise UnsupportedLibpqEnvironment(
                f"inherited libpq setting {name} is not represented by the private profile"
            )
        if value != expected[name]:
            raise UnsupportedLibpqEnvironment(
                f"inherited libpq setting {name} contradicts the explicit database target"
            )
        inherited.append((name, value))
    return tuple(sorted(inherited))


@dataclass(frozen=True, repr=False)
class _Profile:
    base: tuple[object, ...]
    digest: str


@dataclass
class _ResolvedProfile:
    target: DatabaseTarget
    parameters: dict[str, Any] = field(repr=False)
    profile: _Profile


def _resolve_profile(target: DatabaseTarget, key: bytes) -> _ResolvedProfile:
    parameters = target.connection_parameters()
    inherited = _check_libpq_environment(parameters)
    password = parameters.get("password")
    if not isinstance(password, str) or not password:
        raise UnsupportedLibpqEnvironment("private audit credential is unavailable")
    secret_file = _private_file_fingerprint(
        target.secret_file, key=key, private=True, label="runtime credential file"
    )
    password_digest = hmac.new(key, password.encode("utf-8"), hashlib.sha256).hexdigest()
    tls_files: tuple[tuple[object, ...], ...] = ()
    if target.sslmode != "disable":
        if os.name == "nt":
            appdata = os.environ.get("APPDATA")
            if not appdata:
                raise UnsupportedLibpqEnvironment("effective TLS profile directory is unavailable")
            tls_home = Path(appdata) / "postgresql"
        else:
            tls_home = Path.home() / ".postgresql"
        tls_files = tuple(
            _private_file_fingerprint(
                tls_home / name,
                key=key,
                private=name == "postgresql.key",
                label=f"TLS {name}",
            )
            for name in _TLS_DEFAULT_FILES
        )
    safe_parameters = {name: value for name, value in parameters.items() if name != "password"}
    material = json.dumps(
        {
            "scope": target.scope,
            "identity": target.identity,
            "user": target.user,
            "sslmode": target.sslmode,
            "parameters": safe_parameters,
            "secret_file": secret_file,
            "password_hmac": password_digest,
            "inherited": inherited,
            "tls": tls_files,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hmac.new(key, material, hashlib.sha256).hexdigest()
    return _ResolvedProfile(target, parameters, _Profile(_target_base(target), digest))


class _LeaseState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.active = True
        self.tainted = False
        self.pid = os.getpid()

    def check_process(self) -> None:
        if self.pid != os.getpid():
            raise PooledAuditLeaseExpired("pooled audit lease cannot cross a process fork")

    def check(self) -> None:
        self.check_process()
        if not self.active:
            raise PooledAuditLeaseExpired("pooled audit lease has expired")
        if self.tainted:
            raise PooledAuditLeaseExpired("pooled audit lease was invalidated")

    def call(self, callback: Callable[[], Any]) -> Any:
        # Check the process before acquiring a lock that may have been owned
        # by a vanished thread when this process forked.
        self.check()
        with self.lock:
            self.check()
            try:
                return callback()
            except BaseException:
                self.tainted = True
                raise

    def invalidate(self) -> None:
        self.check()
        with self.lock:
            self.tainted = True

    def mark_tainted(self) -> None:
        self.check()
        with self.lock:
            self.tainted = True

    def expire(self) -> None:
        if self.pid != os.getpid():
            self.active = False
            return
        with self.lock:
            self.active = False


_INFO_VALUES = {
    "backend_pid",
    "dbname",
    "encoding",
    "host",
    "hostaddr",
    "port",
    "server_version",
    "status",
    "transaction_status",
    "user",
}
_INFO_METHODS = {"parameter_status"}


class _GuardedInfo:
    def __init__(self, raw_info: Any, state: _LeaseState) -> None:
        object.__setattr__(self, "_raw_info", raw_info)
        object.__setattr__(self, "_state", state)

    def __getattr__(self, name: str) -> Any:
        state = object.__getattribute__(self, "_state")
        raw_info = object.__getattribute__(self, "_raw_info")
        if name not in _INFO_VALUES and name not in _INFO_METHODS:
            raise AttributeError(f"pooled connection information does not expose {name}")
        value = state.call(lambda: getattr(raw_info, name))
        if name in _INFO_METHODS and callable(value):

            def guarded(*args: Any, **kwargs: Any) -> Any:
                return state.call(lambda: value(*args, **kwargs))

            return guarded
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise AttributeError(f"pooled connection information does not expose {name}")
        return value

    @property
    def transaction_status(self) -> TransactionStatus:
        return cast(TransactionStatus, self.__getattr__("transaction_status"))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("connection information is read-only")


class _GuardedCursor:
    def __init__(self, cursor: Any, state: _LeaseState, raw_proxy: "_GuardedRaw") -> None:
        object.__setattr__(self, "_cursor", cursor)
        object.__setattr__(self, "_state", state)
        object.__setattr__(self, "_raw_proxy", raw_proxy)

    def __getattr__(self, name: str) -> Any:
        state = object.__getattribute__(self, "_state")
        cursor = object.__getattribute__(self, "_cursor")
        if name == "connection":
            state.check()
            return object.__getattribute__(self, "_raw_proxy")
        if name not in {
            "closed",
            "rowcount",
            "statusmessage",
            "execute",
            "executemany",
            "fetchone",
            "fetchmany",
            "fetchall",
            "close",
        }:
            raise AttributeError(f"pooled cursor does not expose {name}")
        value = state.call(lambda: getattr(cursor, name))
        if not callable(value):
            return value

        def guarded(*args: Any, **kwargs: Any) -> Any:
            result = state.call(lambda: getattr(cursor, name)(*args, **kwargs))
            return self if result is cursor else result

        return guarded

    @property
    def closed(self) -> bool:
        return cast(bool, self._state.call(lambda: self._cursor.closed))

    @property
    def rowcount(self) -> int:
        return cast(int, self._state.call(lambda: self._cursor.rowcount))

    @property
    def statusmessage(self) -> str:
        return cast(str, self._state.call(lambda: self._cursor.statusmessage))

    def execute(
        self,
        query: Any,
        params: Any = None,
        *,
        prepare: bool | None = None,
        binary: bool | None = None,
    ) -> "_GuardedCursor":
        self._state.call(
            lambda: self._cursor.execute(query, params, prepare=prepare, binary=binary)
        )
        return self

    def executemany(
        self, query: Any, params_seq: Iterable[Any], *, returning: bool = False
    ) -> None:
        self._state.call(lambda: self._cursor.executemany(query, params_seq, returning=returning))

    def fetchone(self) -> Any:
        return self._state.call(self._cursor.fetchone)

    def fetchmany(self, size: int = 0) -> list[Any]:
        return cast(list[Any], self._state.call(lambda: self._cursor.fetchmany(size)))

    def fetchall(self) -> list[Any]:
        return cast(list[Any], self._state.call(self._cursor.fetchall))

    def close(self) -> None:
        self._state.call(self._cursor.close)

    def __iter__(self) -> Iterator[Any]:
        state = object.__getattribute__(self, "_state")
        cursor = object.__getattribute__(self, "_cursor")
        iterator = state.call(lambda: iter(cursor))
        return _GuardedIterator(iterator, state)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("pooled cursor references are read-only wrappers")


class _GuardedIterator:
    def __init__(self, iterator: Any, state: _LeaseState) -> None:
        self._iterator = iterator
        self._state = state

    def __iter__(self) -> "_GuardedIterator":
        return self

    def __next__(self) -> Any:
        # As with every other lease operation, reject a forked child before
        # acquiring a lock that could belong to a parent thread.
        self._state.check_process()
        with self._state.lock:
            self._state.check()
            try:
                return next(self._iterator)
            except StopIteration:
                raise
            except BaseException:
                self._state.tainted = True
                raise


class _GuardedRaw:
    """Small guarded surface; never exposes psycopg's underlying connection."""

    def __init__(self, raw: psycopg.Connection, state: _LeaseState) -> None:
        self.__raw = raw
        self.__state = state

    @property
    def info(self) -> _GuardedInfo:
        return cast(
            _GuardedInfo,
            self.__state.call(lambda: _GuardedInfo(self.__raw.info, self.__state)),
        )

    @property
    def autocommit(self) -> bool:
        return cast(bool, self.__state.call(lambda: self.__raw.autocommit))

    def execute(
        self,
        statement: Any,
        params: Any = None,
        *,
        prepare: bool | None = None,
        binary: bool = False,
    ) -> _GuardedCursor:
        cursor = self.__state.call(
            lambda: self.__raw.execute(statement, params, prepare=prepare, binary=binary)
        )
        return _GuardedCursor(cursor, self.__state, self)

    def cursor(self, *, row_factory: Any) -> _GuardedCursor:
        cursor = self.__state.call(lambda: self.__raw.cursor(row_factory=row_factory))
        return _GuardedCursor(cursor, self.__state, self)

    @contextmanager
    def transaction(
        self, savepoint_name: str | None = None, force_rollback: bool = False
    ) -> Iterator[Any]:
        transaction = self.__state.call(
            lambda: self.__raw.transaction(savepoint_name, force_rollback)
        )
        self.__state.call(transaction.__enter__)
        try:
            # Do not expose psycopg's native Transaction object: callers must
            # not retain a transaction handle beyond this guarded lease.
            yield self
        except BaseException as error:
            self.__state.call(lambda: transaction.__exit__(type(error), error, error.__traceback__))
            raise
        else:
            self.__state.call(lambda: transaction.__exit__(None, None, None))

    def commit(self) -> None:
        self.__state.call(self.__raw.commit)

    def rollback(self) -> None:
        self.__state.call(self.__raw.rollback)

    def close(self) -> None:
        self.__state.invalidate()
        raise RuntimeError("pooled audit connections are closed by their manager")

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"pooled raw connection does not expose {name}")

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"_GuardedRaw__raw", "_GuardedRaw__state"}:
            object.__setattr__(self, name, value)
            return
        raise AttributeError("pooled raw connection is a guarded wrapper")


_CONTEXT_BATCH_FALLBACK = object()
_CONTEXT_BATCH_SAVEPOINT = "cao_memory_verify_context_batch"


def _new_context_batch_savepoint() -> str:
    """Return a safe per-call savepoint identifier that cannot shadow a caller's."""
    return f"{_CONTEXT_BATCH_SAVEPOINT}_{secrets.token_hex(8)}"


_CONTEXT_BATCH_LOCK_SQL = (
    "LOCK TABLE work_inbox_store_context, work_store_control, work_recovery_context "
    "IN ACCESS SHARE MODE NOWAIT"
)
_CONTEXT_BATCH_SQL = """SELECT
    'inbox'::text AS context_kind,
    singleton::bigint AS singleton,
    store_identity::text AS store_identity,
    store_uuid::text AS store_uuid,
    NULL::bigint AS context_version,
    NULL::text AS execution_state,
    NULL::text AS installation_uuid,
    NULL::text AS source_installation_uuid,
    NULL::text AS bundle_digest,
    NULL::text AS restore_receipt,
    NULL::double precision AS created_at
FROM work_inbox_store_context
UNION ALL
SELECT
    'control'::text,
    singleton::bigint,
    store_identity::text,
    store_uuid::text,
    NULL::bigint,
    NULL::text,
    NULL::text,
    NULL::text,
    NULL::text,
    NULL::text,
    NULL::double precision
FROM work_store_control
UNION ALL
SELECT
    'recovery'::text,
    singleton::bigint,
    NULL::text,
    NULL::text,
    context_version::bigint,
    execution_state::text,
    installation_uuid::text,
    source_installation_uuid::text,
    bundle_digest::text,
    restore_receipt::text,
    created_at::double precision
FROM work_recovery_context"""


class _PooledAuditConnection(Connection):
    """Connection-compatible, lease-scoped wrapper for Work verification."""

    def __init__(self, raw: psycopg.Connection, target: DatabaseTarget, state: _LeaseState):
        self._pooled_raw = raw
        self._lease_state = state
        self.target = target
        self._raw_view = _GuardedRaw(raw, state)
        # Connection.raw is the compatibility attribute expected by WorkRepository.
        # The guarded facade prevents callers from reaching psycopg's physical handle.
        self.raw = self._raw_view

    @property
    def in_transaction(self) -> bool:
        return cast(
            bool,
            self._lease_state.call(
                lambda: self._pooled_raw.info.transaction_status != TransactionStatus.IDLE
            ),
        )

    @property
    def read_only(self) -> bool:
        row = self.execute("SHOW transaction_read_only").fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL did not report transaction read-only state")
        return bool(row[0] == "on")

    def execute(
        self,
        statement: Any,
        params: Any = (),
        *,
        prepare: bool | None = None,
    ) -> _GuardedCursor:
        def run() -> psycopg.Cursor[Any]:
            cursor = self._pooled_raw.cursor(row_factory=record_factory)
            if prepare is None:
                return cursor.execute(statement, params or None)
            return cursor.execute(statement, params or None, prepare=prepare)

        cursor = self._lease_state.call(run)
        return _GuardedCursor(cursor, self._lease_state, self._raw_view)

    def executemany(self, statement: Any, params: Any) -> _GuardedCursor:
        def run() -> psycopg.Cursor[Any]:
            cursor = self._pooled_raw.cursor(row_factory=record_factory)
            cursor.executemany(statement, params)
            return cursor

        return _GuardedCursor(self._lease_state.call(run), self._lease_state, self._raw_view)

    def _batch_verify_work_contexts(self):
        """Read the three Work context rows inside one guarded pipeline sync.

        The method returns only decoded rows or a private fallback sentinel. A
        server error is recovered to this method's savepoint before it crosses
        `_LeaseState.call`, because that guard invalidates leases on exceptions.
        """

        def run():
            raw = self._pooled_raw
            if raw.info.transaction_status == TransactionStatus.IDLE:
                return _CONTEXT_BATCH_FALLBACK
            savepoint = _new_context_batch_savepoint()
            cursor = None
            try:
                with raw.pipeline():
                    raw.execute(f"SAVEPOINT {savepoint}")
                    raw.execute(_CONTEXT_BATCH_LOCK_SQL)
                    cursor = raw.execute(_CONTEXT_BATCH_SQL)
                    raw.execute(f"RELEASE SAVEPOINT {savepoint}")
            except psycopg.Error as pipeline_error:
                try:
                    raw.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    raw.execute(f"RELEASE SAVEPOINT {savepoint}")
                except BaseException as cleanup_error:
                    # Recovery failure leaves transaction/lease state uncertain.
                    # Preserve the original pipeline error; the explicit cause
                    # records why fallback was unsafe. The lease guard taints it.
                    raise pipeline_error from cleanup_error
                return _CONTEXT_BATCH_FALLBACK
            if cursor is None:
                raise RuntimeError("Work context pipeline returned no cursor")
            return cursor.fetchall()

        return self._lease_state.call(run)

    def begin(self, readonly: bool = False) -> None:
        statement = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY" if readonly else "BEGIN"
        self.execute(statement)

    def commit(self) -> None:
        self._lease_state.call(self._pooled_raw.commit)

    def rollback(self) -> None:
        self._lease_state.call(self._pooled_raw.rollback)

    @contextmanager
    def transaction(self) -> Iterator["_PooledAuditConnection"]:
        self.begin()
        try:
            yield self
        except BaseException:
            # A driver error taints the lease, so rollback bypasses the public
            # guard while remaining serialized against any in-flight cursor.
            # Reject a forked child before touching the inherited lock or
            # libpq handle: the lock may belong to a vanished parent thread,
            # and the child's socket descriptor has already been detached.
            self._lease_state.check_process()
            if not self._lease_state.active:
                raise PooledAuditLeaseExpired("pooled audit lease has expired")
            with self._lease_state.lock:
                self._lease_state.check_process()
                if not self._lease_state.active:
                    raise PooledAuditLeaseExpired("pooled audit lease has expired")
                try:
                    self._pooled_raw.rollback()
                except BaseException:
                    self._lease_state.tainted = True
            raise
        else:
            self.commit()

    def close(self) -> None:
        self._lease_state.invalidate()
        raise RuntimeError("pooled audit connections are closed by their manager")

    def __enter__(self) -> "_PooledAuditConnection":
        self._lease_state.check()
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> None:
        if self.in_transaction:
            self.rollback() if exc_type else self.commit()
        self.close()


@dataclass(repr=False)
class _Entry:
    profile: _Profile
    target: DatabaseTarget
    raw: psycopg.Connection = field(repr=False)
    leased: bool = False
    retired: bool = False
    last_used: float = field(default_factory=time.monotonic)


class LegacyMemoryAuditConnectionManager:
    """A process-wide bounded cache of private memory-audit PG sessions."""

    def __init__(self, *, max_sessions: int = 4, checkout_timeout: float = 2.0):
        if not 1 <= max_sessions <= 4:
            raise ValueError("legacy memory audit pool is limited to four sessions")
        if checkout_timeout <= 0:
            raise ValueError("checkout timeout must be positive")
        self.max_sessions = max_sessions
        self.checkout_timeout = checkout_timeout
        self._profile_key = secrets.token_bytes(32)
        self._condition = threading.Condition(threading.RLock())
        self._entries: dict[_Profile, _Entry] = {}
        self._opening: set[_Profile] = set()
        self._latest_profiles: dict[tuple[object, ...], _Profile] = {}
        self._physical_count = 0
        self._pid = os.getpid()
        self._closed = False
        if hasattr(os, "register_at_fork"):
            os.register_at_fork(after_in_child=self._after_fork_child)

    def _after_fork_child(self) -> None:
        # A libpq finalizer may send Terminate before closing its socket. The
        # child's descriptor table is private after fork, so replace inherited
        # socket descriptors with /dev/null before dropping any libpq refs.
        # If detachment fails, retain the references for the child's lifetime.
        inherited = tuple(self._entries.values())
        detachment_failed = False
        for entry in inherited:
            raw = entry.raw
            try:
                if raw.closed:
                    continue
                socket_fd = raw.pgconn.socket
                if socket_fd < 0:
                    continue
                replacement_fd = os.open(os.devnull, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
                try:
                    os.dup2(replacement_fd, socket_fd, inheritable=False)
                finally:
                    os.close(replacement_fd)
            except BaseException:
                detachment_failed = True
                _FORKED_RAW_CONNECTIONS.append(raw)
        self._condition = threading.Condition(threading.RLock())
        self._entries = {}
        self._opening = set()
        self._latest_profiles = {}
        self._physical_count = 0
        self._pid = os.getpid()
        # The child must not create replacement sessions if an inherited
        # libpq handle may still own a duplicate descriptor for a parent
        # backend. That would exceed the inherited physical-session budget.
        self._closed = detachment_failed
        del inherited

    def _ensure_process(self) -> None:
        if self._pid != os.getpid():
            self._after_fork_child()

    @property
    def managed_session_count(self) -> int:
        self._ensure_process()
        with self._condition:
            return self._physical_count

    def session_count_for(self, target: DatabaseTarget) -> int:
        """Count this target's actual and reserved manager slots for tests/diagnostics."""
        self._ensure_process()
        base = _target_base(target)
        with self._condition:
            return sum(1 for entry in self._entries.values() if entry.profile.base == base) + sum(
                1 for profile in self._opening if profile.base == base
            )

    def _resolve(
        self, target: DatabaseTarget, resolver: Callable[[], DatabaseTarget] | None
    ) -> _ResolvedProfile:
        resolved = resolver() if resolver is not None else _target_resolver(target)
        if not isinstance(resolved, DatabaseTarget):
            raise StaleMemoryAuditTarget("current database target is unavailable")
        _assert_same_repository_target(target, resolved)
        profile = _resolve_profile(resolved, self._profile_key)
        return profile

    def _close_entry_locked(self, profile: _Profile, entry: _Entry) -> None:
        entry.retired = True
        entry.leased = False
        if not entry.raw.closed:
            entry.raw.close()
        if not entry.raw.closed:
            raise RuntimeError("private PostgreSQL session remained open after close")
        if self._entries.get(profile) is entry:
            self._entries.pop(profile)
        self._physical_count -= 1
        self._condition.notify_all()

    def _observe_profile_locked(self, profile: _Profile) -> None:
        self._latest_profiles[profile.base] = profile
        self._retire_other_generations_locked(profile)

    def _retire_other_generations_locked(self, profile: _Profile) -> None:
        for existing_profile, entry in tuple(self._entries.items()):
            if existing_profile.base != profile.base or existing_profile == profile:
                continue
            entry.retired = True
            if not entry.leased:
                self._close_entry_locked(existing_profile, entry)

    def _evict_idle_locked(self) -> bool:
        idle = [(profile, entry) for profile, entry in self._entries.items() if not entry.leased]
        if not idle:
            return False
        profile, entry = min(idle, key=lambda pair: pair[1].last_used)
        self._close_entry_locked(profile, entry)
        return True

    def _reserve(
        self,
        deadline: float,
        target: DatabaseTarget,
        resolver: Callable[[], DatabaseTarget] | None,
    ) -> tuple[_Entry | None, _ResolvedProfile]:
        while True:
            # Resolve on every wake. A password, endpoint, or TLS profile may
            # change while this caller waits for an exclusive lease/capacity.
            current = self._resolve(target, resolver)
            keep_parameters = False
            try:
                with self._condition:
                    self._ensure_process()
                    if self._closed:
                        raise RuntimeError("legacy memory audit connection manager is closed")
                    # Same-target credential/TLS rotation is a new generation,
                    # not a stale repository. Retire the previous generation
                    # and continue lookup under the freshly resolved profile.
                    profile = current.profile
                    self._observe_profile_locked(profile)
                    entry = self._entries.get(profile)
                    if entry is not None:
                        if entry.retired:
                            self._condition.wait(max(0, deadline - time.monotonic()))
                        elif not entry.leased:
                            entry.leased = True
                            keep_parameters = True
                            return entry, current
                        else:
                            self._condition.wait(max(0, deadline - time.monotonic()))
                    elif profile in self._opening:
                        self._condition.wait(max(0, deadline - time.monotonic()))
                    elif self._physical_count >= self.max_sessions:
                        if not self._evict_idle_locked():
                            self._condition.wait(max(0, deadline - time.monotonic()))
                    else:
                        self._opening.add(profile)
                        self._physical_count += 1
                        keep_parameters = True
                        return None, current
                    if time.monotonic() >= deadline:
                        raise TimeoutError("legacy memory audit connection checkout timed out")
            finally:
                if not keep_parameters:
                    current.parameters["password"] = ""

    @staticmethod
    def _configure_raw(raw: psycopg.Connection, target: DatabaseTarget) -> None:
        if raw.autocommit is not True or raw.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError("pooled PostgreSQL connection did not start idle")
        from psycopg import sql

        cursor = raw.execute(
            sql.SQL(
                "RESET ROLE; RESET ALL; "
                "SET search_path TO pg_catalog, {}; "
                "SET TIME ZONE 'UTC'; "
                "SET statement_timeout TO '30s'; "
                "SET lock_timeout TO '10s'; "
                "SET plan_cache_mode TO force_custom_plan; "
                "SELECT current_user,session_user,current_setting('search_path'),"
                "current_setting('TimeZone'),"
                "current_setting('statement_timeout'),current_setting('lock_timeout'),"
                "current_setting('plan_cache_mode')"
            ).format(sql.Identifier(target.schema))
        )
        while cursor.nextset() is not None:
            pass
        row = cursor.fetchone()
        expected = (
            target.user,
            target.user,
            f"pg_catalog, {target.schema}",
            "UTC",
            "30s",
            "10s",
            "force_custom_plan",
        )
        if row is None or tuple(row) != expected:
            raise RuntimeError("pooled PostgreSQL session settings do not match the target")
        if raw.info.user != target.user or raw.info.dbname != target.database:
            raise RuntimeError("pooled PostgreSQL connection identity does not match the target")

    def _open_reserved(
        self, resolved: _ResolvedProfile, resolver: Callable[[], DatabaseTarget] | None
    ) -> _Entry:
        raw = None
        try:
            parameters = dict(resolved.parameters)
            raw = psycopg.connect(**parameters, autocommit=True, row_factory=record_factory)
            current = self._resolve(resolved.target, resolver)
            try:
                if current.profile != resolved.profile:
                    with self._condition:
                        self._observe_profile_locked(current.profile)
                    raise StaleMemoryAuditTarget(
                        "database profile changed while opening a private audit connection"
                    )
            finally:
                current.parameters["password"] = ""
            with self._condition:
                self._opening.discard(resolved.profile)
                if self._closed or self._pid != os.getpid():
                    raise RuntimeError("legacy memory audit manager changed during connection open")
                if self._latest_profiles.get(resolved.profile.base) != resolved.profile:
                    raise StaleMemoryAuditTarget(
                        "database profile changed before publishing a private audit connection"
                    )
                entry = _Entry(resolved.profile, resolved.target, raw, leased=True)
                self._entries[resolved.profile] = entry
                self._condition.notify_all()
            raw = None
            return entry
        except BaseException:
            closed = raw is None
            if raw is not None:
                try:
                    raw.close()
                    closed = raw.closed
                except BaseException:
                    closed = raw.closed
            with self._condition:
                self._opening.discard(resolved.profile)
                if raw is not None and not closed:
                    # A failed close remains a counted physical session. Keep
                    # an owned entry so capacity cannot be reused unsafely.
                    self._entries[resolved.profile] = _Entry(
                        resolved.profile,
                        resolved.target,
                        raw,
                        leased=False,
                        retired=True,
                    )
                else:
                    self._physical_count -= 1
                self._condition.notify_all()
            raise
        finally:
            # The password is needed only for the connect call above.
            resolved.parameters["password"] = ""

    def _release(self, entry: _Entry, reusable: bool) -> None:
        with self._condition:
            profile = entry.profile
            current = self._entries.get(profile)
            if current is not entry:
                return
            entry.leased = False
            entry.last_used = time.monotonic()
            if self._closed or entry.retired or not reusable:
                self._close_entry_locked(profile, entry)
            else:
                self._condition.notify_all()

    @contextmanager
    def checkout(
        self,
        target: DatabaseTarget,
        *,
        resolver: Callable[[], DatabaseTarget] | None = None,
        readonly: bool = False,
    ) -> Iterator[_PooledAuditConnection]:
        self._ensure_process()
        deadline = time.monotonic() + self.checkout_timeout
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("legacy memory audit connection checkout timed out")
            entry, resolved = self._reserve(deadline, target, resolver)
            if entry is None:
                try:
                    entry = self._open_reserved(resolved, resolver)
                except StaleMemoryAuditTarget:
                    if time.monotonic() >= deadline:
                        raise
                    continue
                finally:
                    resolved.parameters["password"] = ""
            else:
                resolved.parameters["password"] = ""

            # Check again after reservation/open and before any application SQL.
            current = None
            active_target = target
            retry = False
            try:
                current = self._resolve(target, resolver)
                active_target = current.target
                with self._condition:
                    self._observe_profile_locked(current.profile)
                    retry = current.profile != entry.profile or entry.retired
            except BaseException:
                self._release(entry, reusable=False)
                raise
            finally:
                if current is not None:
                    current.parameters["password"] = ""
            if retry:
                self._release(entry, reusable=False)
                if time.monotonic() >= deadline:
                    raise TimeoutError("legacy memory audit connection checkout timed out")
                continue
            break

        state = _LeaseState()
        connection = _PooledAuditConnection(entry.raw, active_target, state)
        reusable = False
        try:
            self._configure_raw(entry.raw, active_target)
            if readonly:
                connection.begin(readonly=True)
            yield connection
            if readonly and connection.in_transaction:
                connection.commit()
            state.expire()
            if not state.tainted:
                if entry.raw.info.transaction_status != TransactionStatus.IDLE:
                    raise RuntimeError("pooled audit lease returned with an open transaction")
                self._configure_raw(entry.raw, active_target)
                reusable = True
        except BaseException:
            state.expire()
            if state.pid == os.getpid():
                try:
                    if (
                        not entry.raw.closed
                        and entry.raw.info.transaction_status != TransactionStatus.IDLE
                    ):
                        entry.raw.rollback()
                except BaseException:
                    pass
            raise
        finally:
            state.expire()
            self._release(entry, reusable)

    def retire_target(self, target: DatabaseTarget) -> None:
        """Retire every profile generation for one target; intended for shutdown/tests."""
        self._ensure_process()
        base = _target_base(target)
        with self._condition:
            for profile, entry in tuple(self._entries.items()):
                if profile.base != base:
                    continue
                entry.retired = True
                if not entry.leased:
                    self._close_entry_locked(profile, entry)

    def close(self) -> None:
        self._ensure_process()
        with self._condition:
            self._closed = True
            for profile, entry in tuple(self._entries.items()):
                entry.retired = True
                if not entry.leased:
                    self._close_entry_locked(profile, entry)
            self._condition.notify_all()


_MEMORY_AUDIT_MANAGER = LegacyMemoryAuditConnectionManager()
atexit.register(_MEMORY_AUDIT_MANAGER.close)


def _legacy_memory_audit_manager() -> LegacyMemoryAuditConnectionManager:
    return _MEMORY_AUDIT_MANAGER


class _LegacyMemoryAuditWorkRepository(WorkRepository):
    """Private opt-in WorkRepository used only for legacy memory audit rows."""

    def __init__(
        self,
        target: DatabaseTarget,
        *,
        manager: LegacyMemoryAuditConnectionManager | None = None,
        resolver: Callable[[], DatabaseTarget] | None = None,
    ) -> None:
        super().__init__(target)
        self._audit_manager = manager or _legacy_memory_audit_manager()
        self._audit_target_resolver = resolver or (lambda: _target_resolver(self.target))

    @contextmanager
    def _audit_connection(self) -> Iterator[Connection]:
        with self._audit_manager.checkout(
            self.target, resolver=self._audit_target_resolver
        ) as connection:
            yield cast(Connection, connection)

    @staticmethod
    def _verify_store_context(connection: Connection, identity: str, expected_uuid: str) -> None:
        if not isinstance(connection, _PooledAuditConnection) or not connection.in_transaction:
            return WorkRepository._verify_store_context(connection, identity, expected_uuid)
        rows = connection._batch_verify_work_contexts()
        if rows is _CONTEXT_BATCH_FALLBACK:
            return WorkRepository._verify_store_context(connection, identity, expected_uuid)
        WorkRepository._verify_batched_store_context(rows, identity, expected_uuid)

    @contextmanager
    def read_snapshot(self) -> Iterator[Connection]:
        """Verify initialization/readers in a fresh RR read-only bounded lease."""
        with self._audit_manager.checkout(
            self.target, resolver=self._audit_target_resolver, readonly=True
        ) as connection:
            self._verify(cast(Connection, connection))
            yield cast(Connection, connection)


__all__ = [
    "LegacyMemoryAuditConnectionManager",
    "PooledAuditLeaseExpired",
    "StaleMemoryAuditTarget",
    "UnsupportedLibpqEnvironment",
    "_LegacyMemoryAuditWorkRepository",
    "_legacy_memory_audit_manager",
]
