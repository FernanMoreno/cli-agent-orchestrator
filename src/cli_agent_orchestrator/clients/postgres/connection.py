"""Native psycopg connections and named/ordinal records. SQL is never rewritten."""

import os
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from types import NotImplementedType, TracebackType
from typing import Any, Callable, ContextManager, Protocol, TypeVar

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus

from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    StorageConnectionLoan,
    acquire_storage_connection,
    note_storage_denial,
    retain_storage_close,
    storage_close_tracks_handle,
    storage_scope_present,
)

from .config import DatabaseTarget
from .fork_handles import track_storage_handle

DatabaseError = psycopg.DatabaseError
IntegrityError = psycopg.IntegrityError
OperationalError = psycopg.OperationalError


class Record:
    def __init__(self, columns: Sequence[str], values: Sequence[Any]) -> None:
        self._columns = tuple(columns)
        self._values = tuple(values)
        self._index = {name: index for index, name in enumerate(self._columns)}

    def __getitem__(self, key: str | int | slice) -> Any:
        return self._values[self._index[key] if isinstance(key, str) else key]

    def keys(self) -> tuple[str, ...]:
        return self._columns

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __eq__(self, other: object) -> bool | NotImplementedType:
        if isinstance(other, Record):
            return self._columns == other._columns and self._values == other._values
        if isinstance(other, tuple):
            return self._values == other
        return NotImplemented


class ConnectionInfo(Protocol):
    """The guarded connection metadata used by native repository code."""

    @property
    def transaction_status(self) -> TransactionStatus: ...


_CursorRowT = TypeVar("_CursorRowT")


class ConnectionCursor(Protocol[_CursorRowT]):
    """Cursor operations supported by native and lease-guarded connections."""

    @property
    def closed(self) -> bool: ...

    @property
    def rowcount(self) -> int: ...

    @property
    def statusmessage(self) -> str | None: ...

    def execute(
        self,
        query: Any,
        params: Any = None,
        *,
        prepare: bool | None = None,
        binary: bool | None = None,
    ) -> "ConnectionCursor[_CursorRowT]": ...

    def executemany(
        self, query: Any, params_seq: Iterable[Any], *, returning: bool = False
    ) -> None: ...

    def fetchone(self) -> _CursorRowT | None: ...

    def fetchmany(self, size: int = 0) -> list[_CursorRowT]: ...

    def fetchall(self) -> list[_CursorRowT]: ...

    def close(self) -> None: ...

    def __iter__(self) -> Iterator[_CursorRowT]: ...


class ConnectionRaw(Protocol):
    """Raw operations exposed by a native connection or guarded lease facade."""

    @property
    def info(self) -> ConnectionInfo: ...

    @property
    def autocommit(self) -> bool: ...

    @property
    def execute(self) -> Callable[..., ConnectionCursor[Any]]: ...

    @property
    def cursor(self) -> Callable[..., ConnectionCursor[Any]]: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...

    def transaction(
        self, savepoint_name: str | None = None, force_rollback: bool = False
    ) -> ContextManager[Any]: ...


def record_factory(cursor: psycopg.Cursor[Any]) -> Callable[[Sequence[Any]], Record]:
    columns = tuple(column.name for column in cursor.description) if cursor.description else ()
    return lambda values: Record(columns, values)


class Connection:
    def __init__(self, target: DatabaseTarget, readonly: bool = False) -> None:
        self.target = target
        self._storage_pid = os.getpid()
        self._storage_loan: StorageConnectionLoan | None = acquire_storage_connection()
        raw = None
        attempted = False
        try:
            parameters = target.connection_parameters()
            try:
                attempted = True
                raw = psycopg.connect(**parameters, autocommit=True, row_factory=record_factory)
            finally:
                parameters["password"] = ""
            self.raw: psycopg.Connection[Record] | ConnectionRaw = raw
            track_storage_handle(raw)
            setattr(raw, "_cao_storage_pid", self._storage_pid)
            self.raw.execute(
                sql.SQL("SET search_path TO pg_catalog, {}").format(sql.Identifier(target.schema))
            )
            self.raw.execute("SET TIME ZONE 'UTC'")
            self.raw.execute("SET statement_timeout TO '30s'")
            self.raw.execute("SET lock_timeout TO '10s'")
            if readonly:
                self.begin(readonly=True)
        except BaseException:
            loan = self._storage_loan
            if raw is not None:
                try:
                    self.close()
                except BaseException:
                    pass  # close retains the exact unconfirmed handle/token.
            elif loan is not None:
                if attempted:
                    retain_storage_close(loan, close=None, closed=lambda: False, loan=loan)
                else:
                    self._storage_loan = None
                    loan.finish()
            raise

    def _check_process(self) -> None:
        if getattr(self, "_storage_pid", os.getpid()) != os.getpid():
            note_storage_denial()
            raise StorageAdmissionUnavailable()

    def _check_live(self) -> None:
        self._check_process()
        if storage_close_tracks_handle(self.raw):
            note_storage_denial()
            raise StorageAdmissionUnavailable()

    @classmethod
    def borrow(cls, raw: psycopg.Connection[Any], target: DatabaseTarget) -> "Connection":
        """Wrap an already owned native connection without opening/closing it."""
        if (
            raw.info.dbname != target.database
            or raw.info.user != target.user
            or raw.info.host != target.host
            or int(raw.info.port) != target.port
        ):
            raise ValueError("Native connection belongs to another PostgreSQL target")
        connection = cls.__new__(cls)
        connection.raw = raw
        connection.target = target
        connection._storage_loan = None  # The enclosing physical owner keeps its token.
        origin = getattr(raw, "_cao_storage_pid", None)
        if isinstance(origin, bool) or not isinstance(origin, int):
            if storage_scope_present():
                note_storage_denial()
                raise StorageAdmissionUnavailable()
            origin = os.getpid()  # Legacy external facade, outside admitted work.
        connection._storage_pid = origin
        connection._check_live()
        return connection

    @property
    def in_transaction(self) -> bool:
        self._check_process()
        return self.raw.info.transaction_status != TransactionStatus.IDLE

    @property
    def read_only(self) -> bool:
        """Ask PostgreSQL about the current transaction, including borrowed ones."""
        self._check_live()
        row = self.raw.execute("SHOW transaction_read_only").fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL did not report transaction read-only state")
        return bool(row[0] == "on")

    def execute(
        self,
        statement: Any,
        params: Any = (),
        *,
        prepare: bool | None = None,
    ) -> ConnectionCursor[Record]:
        # SQLAlchemy owns tuple-row connections. A borrowed wrapper must expose
        # Work's named records without altering the owner's row factory or its
        # transaction. Configure this cursor only.
        self._check_live()
        cursor = self.raw.cursor(row_factory=record_factory)
        if prepare is None:
            return cursor.execute(statement, params or None)
        return cursor.execute(statement, params or None, prepare=prepare)

    def executemany(self, statement: Any, params: Any) -> ConnectionCursor[Record]:
        self._check_live()
        cursor = self.raw.cursor(row_factory=record_factory)
        cursor.executemany(statement, params)
        return cursor

    def begin(self, readonly: bool = False) -> None:
        self._check_live()
        self.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY" if readonly else "BEGIN")

    def commit(self) -> None:
        self._check_live()
        self.raw.commit()

    def rollback(self) -> None:
        self._check_process()
        self.raw.rollback()

    def close(self) -> None:
        self._check_process()
        loan = getattr(self, "_storage_loan", None)
        try:
            self.raw.close()
        finally:
            if loan is not None:
                if getattr(self.raw, "closed", False) is True:
                    self._storage_loan = None
                    loan.finish()
                else:
                    retain_storage_close(
                        self.raw,
                        close=self.close,
                        closed=lambda: getattr(self.raw, "closed", False) is True,
                        loan=loan,
                    )

    @contextmanager
    def transaction(self) -> Iterator["Connection"]:
        self._check_live()
        with self.raw.transaction():
            yield self

    def __enter__(self) -> "Connection":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if self.in_transaction:
                self.rollback() if exc_type else self.commit()
        finally:
            self.close()


def connect(target: DatabaseTarget, readonly: bool = False) -> Connection:
    return Connection(target, readonly)
