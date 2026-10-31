"""SQLAlchemy checkout ownership includes creation, setup, ping and return."""

import os
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.exc import TimeoutError as PoolCheckoutTimeout
from sqlalchemy.pool import QueuePool
from sqlalchemy.pool.base import ConnectionPoolEntry, PoolProxiedConnection

from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    StorageConnectionLoan,
    acquire_storage_connection,
    note_storage_denial,
    retain_storage_close,
    storage_close_owns_loan,
    storage_close_tracks_handle,
)

from .fork_handles import track_storage_handle

_KEY = "cao_physical_storage_loan"


@dataclass(repr=False)
class _Attempt:
    loan: StorageConnectionLoan | None
    raws: list[Any] = field(default_factory=list)
    ready: bool = False


@dataclass(frozen=True, eq=False, repr=False)
class _Checkout:
    attempt: _Attempt


class StorageQueuePool(QueuePool):
    """Preserve QueuePool limits; charge before its public checkout attempt.

    _do_get attaches the owner before pre-ping/reuse. A serial internal retry
    retains its one attempt rather than freeing it between two physical opens.
    _do_return_conn retires only after reset and physical publication/close.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._pid = os.getpid()
        self._loan_guard = threading.RLock()
        self._raw_loans: dict[int, tuple[Any, StorageConnectionLoan]] = {}
        self._controlled = False
        self._attempt: ContextVar[_Attempt | None] = ContextVar(
            "cao_pool_storage_attempt", default=None
        )
        super().__init__(*args, **kwargs)
        original = self._invoke_creator

        def create(record):
            attempt = self._attempt.get()
            loan = attempt.loan if attempt is not None else None
            if loan is not None and storage_close_owns_loan(loan):
                note_storage_denial()
                raise StorageAdmissionUnavailable()
            try:
                raw = original(record)
            except BaseException:
                if loan is not None:
                    retain_storage_close(loan, close=None, closed=lambda: False, loan=loan)
                raise
            setattr(raw, "_cao_storage_pid", self._pid)
            if attempt is not None:
                attempt.raws.append(raw)
            track_storage_handle(raw)
            if loan is not None:
                with self._loan_guard:
                    self._raw_loans[id(raw)] = (raw, loan)
            return raw

        self._invoke_creator = create

    def _check_process(self) -> None:
        if self._pid != os.getpid():
            note_storage_denial()
            raise StorageAdmissionUnavailable()

    def _forget_raw(self, raw: Any, loan: StorageConnectionLoan | None = None) -> None:
        with self._loan_guard:
            current = self._raw_loans.get(id(raw))
            if current is not None and current[0] is raw and (loan is None or current[1] is loan):
                self._raw_loans.pop(id(raw), None)

    def _close_owned_raw(self, raw: Any) -> None:
        self._check_process()
        raw.close()
        if getattr(raw, "closed", False) is True:
            self._forget_raw(raw)

    def _close_connection(self, connection: Any, *, terminate: bool = False) -> None:
        self._check_process()
        with self._loan_guard:
            held = self._raw_loans.get(id(connection))
        attempt = self._attempt.get()
        loan = held[1] if held is not None else (attempt.loan if attempt is not None else None)
        try:
            super()._close_connection(connection, terminate=terminate)
        finally:
            if getattr(connection, "closed", False) is True:
                self._forget_raw(connection)
            elif loan is not None or self._controlled:
                # A checkout-return record may already retain this token. The
                # residual socket then has its own pool-owned retirement proof.
                held_loan = loan if loan is not None and not storage_close_owns_loan(loan) else None
                retain_storage_close(
                    connection,
                    close=lambda: self._close_owned_raw(connection),
                    closed=lambda: getattr(connection, "closed", False) is True,
                    loan=held_loan,
                )

    def connect(self) -> PoolProxiedConnection:
        self._check_process()
        loan = acquire_storage_connection()
        if loan is not None:
            self._controlled = True
        attempt = _Attempt(loan)
        token = self._attempt.set(attempt)
        try:
            try:
                connection = super().connect()
            except BaseException as error:
                # first_connect/connect listeners can fail before QueuePool
                # returns a record; retain and close those exact created raws.
                for raw in attempt.raws:
                    if getattr(raw, "closed", False) is not True:
                        self._close_connection(raw)
                if loan is not None and not storage_close_owns_loan(loan):
                    loan.finish()
                if loan is not None and isinstance(error, PoolCheckoutTimeout):
                    note_storage_denial()
                    raise StorageAdmissionUnavailable() from None
                raise
            attempt.ready = True
            return connection
        finally:
            self._attempt.reset(token)

    def _do_get(self) -> ConnectionPoolEntry:
        attempt = self._attempt.get()
        if (
            attempt is not None
            and attempt.loan is not None
            and storage_close_owns_loan(attempt.loan)
        ):
            note_storage_denial()
            raise StorageAdmissionUnavailable()
        record = super()._do_get()
        if attempt is not None:
            with self._loan_guard:
                raw = record.dbapi_connection
                quarantined = raw is not None and storage_close_tracks_handle(raw)
                if quarantined:
                    # The registry owns this retiring socket; a new checkout
                    # cannot execute pre-ping/application SQL against it.
                    setattr(record, "dbapi_connection", None)
                info = record.record_info
                assert info is not None
                info[_KEY] = _Checkout(attempt)
                if raw is not None and attempt.loan is not None and not quarantined:
                    self._raw_loans[id(raw)] = (raw, attempt.loan)
        return record

    def _return_proved(self, record: ConnectionPoolEntry, checkout: _Checkout, raw: Any) -> bool:
        with self._loan_guard:
            info = record.record_info
            current = info.get(_KEY) if info is not None else None
            return (
                getattr(raw, "closed", False) is True
                or isinstance(current, _Checkout)
                and current.attempt is not checkout.attempt
            )

    def _retire_failed_return(
        self, record: ConnectionPoolEntry, checkout: _Checkout, raw: Any
    ) -> None:
        self._check_process()
        with self._loan_guard:
            if self._return_proved(record, checkout, raw) or record.in_use:
                return
            if record.dbapi_connection is raw:
                record.invalidate()
            elif getattr(raw, "closed", False) is not True:
                self._close_owned_raw(raw)

    def _do_return_conn(self, record: ConnectionPoolEntry) -> None:
        self._check_process()
        with self._loan_guard:
            info = record.record_info
            checkout = info.pop(_KEY, None) if info is not None else None
            raw = record.dbapi_connection
        loan = checkout.attempt.loan if isinstance(checkout, _Checkout) else None
        try:
            super()._do_return_conn(record)
        except BaseException:
            if isinstance(checkout, _Checkout) and (loan is not None or self._controlled):
                if loan is None or not storage_close_owns_loan(loan):
                    retain_storage_close(
                        checkout,
                        close=lambda: self._retire_failed_return(record, checkout, raw),
                        closed=lambda: self._return_proved(record, checkout, raw),
                        loan=loan,
                    )
            raise
        else:
            if (
                isinstance(checkout, _Checkout)
                and loan is not None
                and not storage_close_owns_loan(loan)
            ):
                if checkout.attempt.ready:
                    loan.finish()
                if raw is not None:
                    self._forget_raw(raw, loan)

    def dispose(self) -> None:
        self._check_process()
        super().dispose()
