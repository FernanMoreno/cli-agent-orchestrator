"""Operational access to an already-funded native PostgreSQL control channel."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import TracebackType
from typing import NoReturn, SupportsIndex

from psycopg.pq import TransactionStatus

from cli_agent_orchestrator.clients.postgres.config import DatabaseTarget
from cli_agent_orchestrator.clients.postgres.connection import (
    Connection,
    FundedSqlCommitUncertain,
)
from cli_agent_orchestrator.clients.storage_admission import (
    _CURRENT,
    StorageAdmissionUnavailable,
)


def _current_task() -> object | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


class FundedControlSession:
    """Reuse one funded ``Connection`` without taking ownership of its lifetime."""

    def __init__(self, channel: Connection) -> None:
        if type(channel) is not Connection:
            raise StorageAdmissionUnavailable()

        self._channel = channel
        self._pid = os.getpid()
        self._thread = threading.current_thread()
        self._task = _current_task()
        scope = _CURRENT.get()
        raw = channel.raw
        target = channel.target
        loan = channel._storage_loan
        funding = channel._control_sql_funding
        self._entered = False
        self._exited = False
        self._busy = False
        self._poisoned = False

        if (
            scope is None
            or funding is None
            or funding.scope is not scope
            or loan is None
            or channel._funded_raw is not raw
            or channel._funded_target is not target
        ):
            raise StorageAdmissionUnavailable()
        self._scope = scope
        self._raw = raw
        self._target = target
        self._loan = loan
        self._funding = funding
        self._check_worker_identity(require_entered=False)
        self._check_channel_identity()
        self._check_idle()
        self._check_funding_and_live()

    def __copy__(self) -> NoReturn:
        raise TypeError("Funded control sessions cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        raise TypeError("Funded control sessions cannot be copied")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("Funded control sessions cannot be serialized")

    @property
    def target(self) -> DatabaseTarget:
        self.check_worker()
        return self._target

    def _check_worker_identity(self, *, require_entered: bool) -> None:
        # Reject the wrong worker/scope before touching funding or close-registry locks.
        if (
            self._pid != os.getpid()
            or self._thread is not threading.current_thread()
            or self._task is not _current_task()
            or _CURRENT.get() is not self._scope
            or (require_entered and (not self._entered or self._exited))
            or self._exited
            or self._poisoned
        ):
            raise StorageAdmissionUnavailable()

    def _check_worker(self, *, require_entered: bool = True) -> None:
        self._check_worker_identity(require_entered=require_entered)
        self._check_channel_identity()
        self._check_funding_and_live()

    def _check_channel_identity(self) -> None:
        channel = self._channel
        if (
            type(channel) is not Connection
            or channel.raw is not self._raw
            or channel.target is not self._target
            or channel._funded_raw is not self._raw
            or channel._funded_target is not self._target
            or channel._storage_loan is not self._loan
            or channel._control_sql_funding is not self._funding
            or self._funding.loan is not self._loan
            or self._funding.scope is not self._scope
            or getattr(self._raw, "closed", None) is not False
        ):
            raise StorageAdmissionUnavailable()

    def _check_funding_and_live(self) -> None:
        channel = self._channel
        try:
            self._funding.check(channel, self._loan)
            channel._check_live()
        except StorageAdmissionUnavailable:
            raise
        except Exception:
            raise StorageAdmissionUnavailable() from None

    def _check_idle(self) -> None:
        if self._raw.info.transaction_status != TransactionStatus.IDLE:
            raise StorageAdmissionUnavailable()

    def _check_rolled_back(self) -> None:
        self._check_worker()
        self._check_idle()

    def check_worker(self) -> None:
        """Validate original PID/thread/task/scope and channel, including while BUSY."""
        self._check_worker()

    def check_target(self, target: DatabaseTarget) -> None:
        """Validate the original funded worker and an exact target before SQL."""
        self._check_worker_identity(require_entered=True)
        self._check_channel_identity()
        if type(target) is not DatabaseTarget or target != self._target:
            raise StorageAdmissionUnavailable()
        if self._busy:
            raise StorageAdmissionUnavailable()
        self._check_idle()
        self._check_funding_and_live()

    def __enter__(self) -> FundedControlSession:
        if self._entered or self._exited:
            raise StorageAdmissionUnavailable()
        self._check_worker_identity(require_entered=False)
        self._check_channel_identity()
        self._check_idle()
        self._check_funding_and_live()
        self._entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if (
            self._pid != os.getpid()
            or self._thread is not threading.current_thread()
            or self._task is not _current_task()
        ):
            raise StorageAdmissionUnavailable()
        # The original Connection owner alone closes the handle and retires its loan.
        self._entered = False
        self._exited = True

    @contextmanager
    def transaction(self, *, readonly: bool = False) -> Iterator[Connection]:
        if type(readonly) is not bool:
            raise StorageAdmissionUnavailable()
        self.check_target(self._target)
        self._busy = True
        operation_failure: BaseException | None = None
        try:
            try:
                with self._channel.funded_transaction():
                    try:
                        if readonly:
                            self._channel.execute(
                                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                            )
                        yield self._channel
                        # Validate mutations and the exact financing scope before commit.
                        self.check_worker()
                    except BaseException as failure:
                        operation_failure = failure
                        raise
            except FundedSqlCommitUncertain:
                self._poisoned = True
                raise
            except BaseException as failure:
                rollback_verified = True
                try:
                    self._check_rolled_back()
                except BaseException:
                    rollback_verified = False
                if not rollback_verified or (
                    operation_failure is not None and failure is not operation_failure
                ):
                    self._poisoned = True
                if operation_failure is not None and failure is not operation_failure:
                    raise operation_failure from None
                raise
            try:
                self.check_worker()
                self._check_idle()
            except BaseException:
                self._poisoned = True
                raise FundedSqlCommitUncertain(
                    "Funded control commit requires reconciliation"
                ) from None
        finally:
            self._busy = False
