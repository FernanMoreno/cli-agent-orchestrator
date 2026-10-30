"""Read-only pressure telemetry for one target server, not shared reservations."""

import threading
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Callable, Literal

from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget, connect
from cli_agent_orchestrator.clients.postgres.config import ConfigurationError
from cli_agent_orchestrator.clients.postgres.connection import DatabaseError, Record
from cli_agent_orchestrator.services.admission_policy import validate_clock
from cli_agent_orchestrator.services.admission_resources import StorageObservation

PressureReason = Literal[
    "pressure_observed",
    "postgres_unavailable",
    "invalid_pressure_data",
    "identity_mismatch",
    "insufficient_visibility",
    "probe_busy",
    "probe_cleanup_unconfirmed",
]

_PRESSURE_SQL = """
SELECT pg_catalog.current_setting('max_connections')::integer AS maximum,
       pg_catalog.current_setting('superuser_reserved_connections')::integer
           AS superuser_reserved,
       COALESCE(pg_catalog.current_setting('reserved_connections', true), '0')::integer
           AS reserved,
       (SELECT COALESCE(sum(numbackends), 0) FROM pg_catalog.pg_stat_database)::bigint
           AS backends,
       pg_catalog.current_database() AS database,
       CURRENT_USER AS role
"""


@dataclass(frozen=True)
class PostgresPressurePolicy:
    reserve_percent: int = 20
    statement_timeout_ms: int = 500

    def __post_init__(self) -> None:
        if (
            isinstance(self.reserve_percent, bool)
            or not isinstance(self.reserve_percent, int)
            or not 0 <= self.reserve_percent < 100
        ):
            raise ValueError("PostgreSQL reserve must be integer percent in [0, 100)")
        if (
            isinstance(self.statement_timeout_ms, bool)
            or not isinstance(self.statement_timeout_ms, int)
            or self.statement_timeout_ms <= 0
        ):
            raise ValueError("pressure statement timeout must be positive integer milliseconds")


@dataclass(frozen=True)
class PostgresPressureSample:
    observation: StorageObservation
    reason: PressureReason
    maximum_connections: int | None = None
    ordinary_connection_budget: int | None = None
    policy_reserved_slots: int | None = None
    observed_backends: int | None = None


def _unknown(at: float, reason: PressureReason) -> PostgresPressureSample:
    return PostgresPressureSample(StorageObservation(at, None), reason)


def _counter(row: Record, key: str, *, minimum: int = 0) -> int:
    value: object = row[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("invalid PostgreSQL pressure counter")
    return value


def _closed(connection: Connection) -> bool:
    # A borrowed facade without a native close witness is not an owned probe.
    return getattr(connection.raw, "closed", False) is True


class PostgresPressureSampler:
    """One owned metrics connection at a time; failures cannot multiply probes.

    Target resolution is fresh for every actual probe. Only aggregate counters
    leave this object; identities and error messages remain private. Backend
    inventory is conservative pressure, not a claim of globally reserved slots.
    Logical target identity must never stand in for a shared-server domain ID.
    """

    def __init__(
        self,
        *,
        target_supplier: Callable[[], DatabaseTarget] = DatabaseTarget.from_settings,
        connect_factory: Callable[..., AbstractContextManager[Connection]] = connect,
        clock: Callable[[], float] = time.monotonic,
        policy: PostgresPressurePolicy | None = None,
    ):
        self._target_supplier = target_supplier
        self._connect = connect_factory
        self._clock = clock
        self._policy = policy if policy is not None else PostgresPressurePolicy()
        self._guard = threading.Lock()
        self._uncertain: Connection | None = None
        self._open_cleanup_unconfirmed = False

    def _inventory(
        self, row: Record | None, target: DatabaseTarget, at: float
    ) -> PostgresPressureSample:
        if row is None:
            return _unknown(at, "invalid_pressure_data")
        try:
            if row["database"] != target.database or row["role"] != target.user:
                return _unknown(at, "identity_mismatch")
            maximum = _counter(row, "maximum", minimum=1)
            superuser_reserved = _counter(row, "superuser_reserved")
            reserved = _counter(row, "reserved")
            backends = _counter(row, "backends")
            ordinary = maximum - superuser_reserved - reserved
            if ordinary < 0:
                raise ValueError("invalid PostgreSQL reserved capacity")
        except (KeyError, ValueError):
            return _unknown(at, "invalid_pressure_data")
        if backends == 0:
            # This query itself has a backend. Zero cannot prove an idle server.
            return _unknown(at, "insufficient_visibility")
        protected = (ordinary * self._policy.reserve_percent + 99) // 100
        available = max(0, ordinary - protected - backends)
        return PostgresPressureSample(
            StorageObservation(at, available),
            "pressure_observed",
            maximum,
            ordinary,
            protected,
            backends,
        )

    def sample(self) -> PostgresPressureSample:
        at = self._clock()
        validate_clock(at)
        if not self._guard.acquire(blocking=False):
            return _unknown(at, "probe_busy")
        try:
            if self._open_cleanup_unconfirmed:
                return _unknown(at, "probe_cleanup_unconfirmed")
            if self._uncertain is not None:
                if not _closed(self._uncertain):
                    return _unknown(at, "probe_cleanup_unconfirmed")
                self._uncertain = None
            entered: Connection | None = None
            opening_attempted = False
            try:
                target = self._target_supplier()
                opening_attempted = True
                with self._connect(target, readonly=True) as connection:
                    entered = connection
                    # The existing connect timeout is preserved. Once entered,
                    # this probe has a separate bounded, read-only query budget.
                    timeout = self._policy.statement_timeout_ms
                    connection.execute(f"SET LOCAL statement_timeout TO '{timeout}ms'")
                    row = connection.execute(_PRESSURE_SQL).fetchone()
                    result = self._inventory(row, target, at)
            except (DatabaseError, OSError, ConfigurationError):
                result = _unknown(at, "postgres_unavailable")
            finally:
                if opening_attempted and entered is None:
                    # Construction/entry can fail after acquiring a physical
                    # connection. Without its handle no retirement is provable;
                    # never retry by creating another potentially live probe.
                    self._open_cleanup_unconfirmed = True
                if entered is not None and not _closed(entered):
                    self._uncertain = entered
            if self._open_cleanup_unconfirmed or self._uncertain is not None:
                return _unknown(at, "probe_cleanup_unconfirmed")
            return result
        finally:
            self._guard.release()
