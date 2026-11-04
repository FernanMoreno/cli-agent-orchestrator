"""Versioned admission policy storage with read-only runtime access and owner CAS writes."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from uuid import UUID

from psycopg import sql

from cli_agent_orchestrator.clients.admission_policy_schema import (
    ADMISSION_POLICY_MIGRATIONS,
    ADMISSION_POLICY_TABLE,
)
from cli_agent_orchestrator.clients.funded_control_session import FundedControlSession
from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget, connect
from cli_agent_orchestrator.clients.postgres.config import ConfigurationError
from cli_agent_orchestrator.clients.postgres.connection import Record
from cli_agent_orchestrator.clients.postgres.migrations import (
    SchemaMismatch,
    verify_component,
    verify_runtime_role,
)
from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.models.admission_policy_control import (
    AdmissionPolicyHistory,
    AdmissionPolicyRecord,
    AdmissionPolicyValues,
)

_POLICY_FIELDS = (
    "memory_per_delivery_bytes",
    "cpu_per_delivery_millicores",
    "memory_reserve_percent",
    "cpu_reserve_percent",
    "max_sample_age_s",
    "postgres_reserve_percent",
    "postgres_statement_timeout_ms",
    "sample_interval_s",
    "healthy_samples_before_growth",
)
_PRINCIPAL_ID = re.compile(r"principal-[0-9a-f]{64}")
_BIGINT_MAX = 2**63 - 1
_HISTORY_LIMIT_MAX = 100


class PolicyRevisionConflict(RuntimeError):
    """The expected policy revision no longer names the latest record."""


class PolicyWriteNotStarted(RuntimeError):
    """Proven pre-write refusal; no policy body ran and its SQL prefix retired."""


class PolicyNotFound(LookupError):
    """No policy record exists for the trusted profile; no revocation was written."""


def _record(row: Record, *, expected_profile_id: str) -> AdmissionPolicyRecord:
    try:
        values = {name: row[name] for name in _POLICY_FIELDS}
        recorded_at: object = row["recorded_at"]
        profile_id = str(UUID(str(row["profile_id"])))
        if profile_id != expected_profile_id:
            raise ValueError("record profile does not match the configured profile")
        if (
            not isinstance(recorded_at, datetime)
            or recorded_at.tzinfo is None
            or recorded_at.utcoffset() is None
        ):
            raise ValueError("record timestamp is not timezone-aware")
        return AdmissionPolicyRecord(
            profile_id=profile_id,
            revision=row["revision"],
            active=row["active"],
            policy=AdmissionPolicyValues(**values),
            actor_principal_id=row["actor_principal_id"],
            recorded_at=recorded_at,
        )
    except (KeyError, TypeError, ValueError):
        raise SchemaMismatch("Invalid PostgreSQL admission policy record") from None


class AdmissionPolicyRepository:
    """Read policy from the main store; only an explicit owner writes revisions.

    The profile UUID is trusted runtime configuration captured at construction,
    never selected by a request. Runtime calls never install migrations or
    promote configured credentials to a schema owner.
    """

    def __init__(
        self,
        *,
        profile_id: str,
        target_supplier: Callable[[], DatabaseTarget] = DatabaseTarget.from_settings,
        connect_factory: Callable[..., AbstractContextManager[Connection]] = connect,
    ) -> None:
        if not isinstance(profile_id, str):
            raise ValueError("Admission policy profile identity must be a UUID")
        try:
            self._profile_id = str(UUID(profile_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Admission policy profile identity must be a UUID") from None
        self._target_supplier = target_supplier
        self._connect = connect_factory

    @property
    def profile_id(self) -> str:
        return self._profile_id

    def resolve_target(self) -> DatabaseTarget:
        target = self._target_supplier()
        if not isinstance(target, DatabaseTarget) or target.scope != "main":
            raise ConfigurationError("Admission policy requires the configured main store")
        return target

    @staticmethod
    def _check_target(target: DatabaseTarget) -> None:
        if not isinstance(target, DatabaseTarget) or target.scope != "main":
            raise ConfigurationError("Admission policy requires the configured main store")

    @staticmethod
    def _verify_reader(connection: Connection) -> None:
        verify_runtime_role(connection)
        verify_component(connection, ADMISSION_POLICY_MIGRATIONS)

    def _read_latest_from(
        self, reader: Connection, target: DatabaseTarget
    ) -> AdmissionPolicyRecord | None:
        self._verify_reader(reader)
        row = reader.execute(
            sql.SQL(
                "SELECT profile_id,revision,active,{},actor_principal_id,recorded_at "
                "FROM {} WHERE profile_id=%s ORDER BY revision DESC LIMIT 1"
            ).format(
                sql.SQL(",").join(map(sql.Identifier, _POLICY_FIELDS)),
                sql.Identifier(target.schema, ADMISSION_POLICY_TABLE),
            ),
            (self._profile_id,),
        ).fetchone()
        return None if row is None else _record(row, expected_profile_id=self._profile_id)

    def read_latest(
        self,
        target: DatabaseTarget,
        *,
        control_session: FundedControlSession | None = None,
    ) -> AdmissionPolicyRecord | None:
        self._check_target(target)
        if control_session is not None:
            if type(control_session) is not FundedControlSession:
                raise StorageAdmissionUnavailable()
            control_session.check_worker()
            control_session.check_target(target)
            with control_session.transaction(readonly=True) as reader:
                return self._read_latest_from(reader, target)

        with self._connect(target, readonly=True) as reader:
            return self._read_latest_from(reader, target)

    def history(
        self,
        target: DatabaseTarget,
        *,
        limit: int = 50,
        before_revision: int | None = None,
        control_session: FundedControlSession | None = None,
    ) -> AdmissionPolicyHistory:
        self._check_target(target)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= _HISTORY_LIMIT_MAX
        ):
            raise ValueError("Admission policy history limit is outside its bounds")
        if before_revision is not None and (
            isinstance(before_revision, bool)
            or not isinstance(before_revision, int)
            or not 1 <= before_revision <= _BIGINT_MAX
        ):
            raise ValueError("Admission policy history cursor is invalid")

        if control_session is not None:
            if type(control_session) is not FundedControlSession:
                raise StorageAdmissionUnavailable()
            control_session.check_worker()
            control_session.check_target(target)
            with control_session.transaction(readonly=True) as reader:
                return self._history_from(reader, target, limit, before_revision)

        with self._connect(target, readonly=True) as reader:
            return self._history_from(reader, target, limit, before_revision)

    def _history_from(
        self,
        reader: Connection,
        target: DatabaseTarget,
        limit: int,
        before_revision: int | None,
    ) -> AdmissionPolicyHistory:
        self._verify_reader(reader)
        table = sql.Identifier(target.schema, ADMISSION_POLICY_TABLE)
        columns = sql.SQL(",").join(
            [
                sql.Identifier("profile_id"),
                sql.Identifier("revision"),
                sql.Identifier("active"),
                *map(sql.Identifier, _POLICY_FIELDS),
                sql.Identifier("actor_principal_id"),
                sql.Identifier("recorded_at"),
            ]
        )
        parameters: tuple[object, ...]
        if before_revision is None:
            statement = sql.SQL(
                "SELECT {} FROM {} WHERE profile_id=%s " "ORDER BY revision DESC LIMIT %s"
            ).format(columns, table)
            parameters = (self._profile_id, limit + 1)
        else:
            statement = sql.SQL(
                "SELECT {} FROM {} WHERE profile_id=%s AND revision<%s "
                "ORDER BY revision DESC LIMIT %s"
            ).format(columns, table)
            parameters = (self._profile_id, before_revision, limit + 1)
        rows = reader.execute(statement, parameters).fetchall()
        has_more = len(rows) > limit
        entries = tuple(_record(row, expected_profile_id=self._profile_id) for row in rows[:limit])
        cursor = entries[-1].revision if has_more and entries else None
        return AdmissionPolicyHistory(entries=entries, next_before_revision=cursor)

    @staticmethod
    def _validate_write(
        *,
        policy: AdmissionPolicyValues,
        expected_revision: int,
        principal_id: str,
        active: bool,
    ) -> None:
        if type(policy) is not AdmissionPolicyValues:
            raise ValueError("Admission policy values must be validated")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or not 0 <= expected_revision < _BIGINT_MAX
        ):
            raise ValueError("Admission policy expected revision is invalid")
        if not isinstance(principal_id, str) or _PRINCIPAL_ID.fullmatch(principal_id) is None:
            raise ValueError("Admission policy actor identity is invalid")
        if type(active) is not bool:
            raise ValueError("Admission policy active state must be explicit")

    @contextmanager
    def _owner(self, target: DatabaseTarget) -> Iterator[Connection]:
        """Keep unknown opens/close/commit failures distinct from proven refusal."""
        try:
            owner_target = target.migration_target()  # Pure configuration, before SQL.
        except ConfigurationError:
            if type(target) is DatabaseTarget:
                raise PolicyWriteNotStarted() from None
            raise
        handles: list[Connection] = []
        opening = False
        writer_entered = False
        try:
            opening = True
            with self._connect(target, readonly=True) as reader:
                handles.append(reader)
                opening = False
                self._verify_reader(reader)
            opening = True
            with self._connect(owner_target) as owner:
                handles.append(owner)
                opening = False
                with owner.transaction():
                    owner.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                    verify_component(owner, ADMISSION_POLICY_MIGRATIONS)
                    owned = owner.execute(
                        """SELECT n.nspowner=r.oid AND c.relowner=r.oid
                        FROM pg_catalog.pg_namespace n
                        JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid
                        JOIN pg_catalog.pg_roles r ON r.rolname=current_user
                        WHERE n.nspname=%s AND c.relname=%s""",
                        (target.schema, ADMISSION_POLICY_TABLE),
                    ).fetchone()
                    if owned is None or owned[0] is not True:
                        raise SchemaMismatch(
                            "Admission policy writer must own its namespace and table"
                        )
                    writer_entered = True
                    yield owner
        except SchemaMismatch:
            if (
                not writer_entered
                and not opening
                and handles
                and all(
                    type(connection) is Connection
                    and getattr(connection.raw, "closed", None) is True
                    for connection in handles
                )
            ):
                raise PolicyWriteNotStarted() from None
            raise

    def _lock_profile(self, owner: Connection, target: DatabaseTarget) -> None:
        owner.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,0))",
            (f"cao-admission-policy:{target.store_id}:{target.schema}:{self._profile_id}",),
        )

    def _latest_from_owner(self, owner: Connection, target: DatabaseTarget) -> Record | None:
        return owner.execute(
            sql.SQL(
                "SELECT profile_id,revision,active,{},actor_principal_id,recorded_at "
                "FROM {} WHERE profile_id=%s ORDER BY revision DESC LIMIT 1"
            ).format(
                sql.SQL(",").join(map(sql.Identifier, _POLICY_FIELDS)),
                sql.Identifier(target.schema, ADMISSION_POLICY_TABLE),
            ),
            (self._profile_id,),
        ).fetchone()

    def _insert(
        self,
        owner: Connection,
        target: DatabaseTarget,
        *,
        policy: AdmissionPolicyValues,
        revision: int,
        principal_id: str,
        active: bool,
    ) -> AdmissionPolicyRecord:
        fields = (
            "profile_id",
            "revision",
            "active",
            *_POLICY_FIELDS,
            "actor_principal_id",
        )
        values: tuple[object, ...] = (
            self._profile_id,
            revision,
            active,
            *(getattr(policy, name) for name in _POLICY_FIELDS),
            principal_id,
        )
        row = owner.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING {}").format(
                sql.Identifier(target.schema, ADMISSION_POLICY_TABLE),
                sql.SQL(",").join(map(sql.Identifier, fields)),
                sql.SQL(",").join(sql.Placeholder() for _ in fields),
                sql.SQL(",").join(
                    map(
                        sql.Identifier,
                        (*fields[:3], *_POLICY_FIELDS, "actor_principal_id", "recorded_at"),
                    )
                ),
            ),
            values,
        ).fetchone()
        if row is None:
            raise SchemaMismatch("PostgreSQL did not return the admission policy record")
        return _record(row, expected_profile_id=self._profile_id)

    def append(
        self,
        target: DatabaseTarget,
        *,
        policy: AdmissionPolicyValues,
        expected_revision: int,
        principal_id: str,
        active: bool = True,
    ) -> AdmissionPolicyRecord:
        self._check_target(target)
        self._validate_write(
            policy=policy,
            expected_revision=expected_revision,
            principal_id=principal_id,
            active=active,
        )
        with self._owner(target) as owner:
            self._lock_profile(owner, target)
            latest = self._latest_from_owner(owner, target)
            actual_revision = 0 if latest is None else latest["revision"]
            if actual_revision != expected_revision:
                raise PolicyRevisionConflict("Admission policy revision changed")
            return self._insert(
                owner,
                target,
                policy=policy,
                revision=expected_revision + 1,
                principal_id=principal_id,
                active=active,
            )

    def revoke(
        self,
        target: DatabaseTarget,
        *,
        expected_revision: int,
        principal_id: str,
    ) -> AdmissionPolicyRecord:
        self._check_target(target)
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or not 0 <= expected_revision < _BIGINT_MAX
        ):
            raise ValueError("Admission policy expected revision is invalid")
        if not isinstance(principal_id, str) or _PRINCIPAL_ID.fullmatch(principal_id) is None:
            raise ValueError("Admission policy actor identity is invalid")
        with self._owner(target) as owner:
            self._lock_profile(owner, target)
            latest = self._latest_from_owner(owner, target)
            if latest is None:
                raise PolicyNotFound("Admission policy does not exist")
            if latest["revision"] != expected_revision:
                raise PolicyRevisionConflict("Admission policy revision changed")
            current = _record(latest, expected_profile_id=self._profile_id)
            return self._insert(
                owner,
                target,
                policy=current.policy,
                revision=expected_revision + 1,
                principal_id=principal_id,
                active=False,
            )
