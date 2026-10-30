"""Fresh runtime reads and explicitly privileged CAS writes for resource authority."""

import hashlib
import json
from contextlib import AbstractContextManager
from dataclasses import asdict, fields
from typing import Callable
from uuid import UUID

from psycopg import sql

from cli_agent_orchestrator.clients.admission_bootstrap_schema import (
    RESOURCE_ENVELOPE_MIGRATIONS,
    RESOURCE_ENVELOPE_TABLE,
)
from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget, connect
from cli_agent_orchestrator.clients.postgres.config import ConfigurationError
from cli_agent_orchestrator.clients.postgres.connection import Record
from cli_agent_orchestrator.clients.postgres.migrations import (
    SchemaMismatch,
    verify_component,
    verify_runtime_role,
)
from cli_agent_orchestrator.models.resource_envelope import (
    EnvelopeClaims,
    ResourceBinding,
    ResourceEnvelopeRecord,
    envelope_integer,
)


class EnvelopeRevisionConflict(RuntimeError):
    """The operator's expected revision no longer names the latest record."""


class EnvelopeNotFound(LookupError):
    """No record exists; no durable revocation has been written."""


def _record(row: Record) -> ResourceEnvelopeRecord:
    try:
        binding_values = {field.name: row[field.name] for field in fields(ResourceBinding)}
        for name in ("instance_id", "process_generation", "store_id"):
            binding_values[name] = str(binding_values[name])
        return ResourceEnvelopeRecord(
            binding=ResourceBinding(**binding_values),
            claims=EnvelopeClaims(
                **{field.name: row[field.name] for field in fields(EnvelopeClaims)}
            ),
            revision=row["revision"],
            active=row["active"],
            actor_principal_id=row["actor_principal_id"],
        )
    except (KeyError, ValueError, TypeError):
        raise SchemaMismatch("Invalid PostgreSQL resource authority record") from None


class ResourceEnvelopeRepository:
    """No migration on read and no implicit runtime credential promotion."""

    def __init__(
        self,
        *,
        target_supplier: Callable[[], DatabaseTarget] = DatabaseTarget.from_settings,
        connect_factory: Callable[..., AbstractContextManager[Connection]] = connect,
    ):
        self._target_supplier = target_supplier
        self._connect = connect_factory

    def resolve_target(self) -> DatabaseTarget:
        target = self._target_supplier()
        if not isinstance(target, DatabaseTarget) or target.scope != "main":
            raise ConfigurationError("Resource authority requires the configured main store")
        return target

    def _verify_reader(self, connection: Connection) -> None:
        verify_runtime_role(connection)
        verify_component(connection, RESOURCE_ENVELOPE_MIGRATIONS)

    def read_latest(
        self, target: DatabaseTarget, instance_id: str
    ) -> ResourceEnvelopeRecord | None:
        instance_id = str(UUID(instance_id))
        with self._connect(target, readonly=True) as reader:
            self._verify_reader(reader)
            row = reader.execute(
                sql.SQL(
                    "SELECT * FROM {} WHERE instance_id=%s ORDER BY revision DESC LIMIT 1"
                ).format(sql.Identifier(target.schema, RESOURCE_ENVELOPE_TABLE)),
                (instance_id,),
            ).fetchone()
            return None if row is None else _record(row)

    def append(
        self,
        target: DatabaseTarget,
        *,
        binding: ResourceBinding,
        claims: EnvelopeClaims,
        expected_revision: int,
        principal_id: str,
        active: bool = True,
    ) -> ResourceEnvelopeRecord:
        envelope_integer(expected_revision, minimum=0)
        candidate = ResourceEnvelopeRecord(
            binding, claims, expected_revision + 1, active, principal_id
        )
        if binding.store_id != str(UUID(target.store_id)):
            raise SchemaMismatch("Resource authority store binding mismatch")
        target_digest = hashlib.sha256(
            json.dumps(target.identity, separators=(",", ":")).encode()
        ).hexdigest()
        if active and binding.target_identity_sha256 != target_digest:
            raise SchemaMismatch("Resource authority target binding mismatch")
        # Refuse a deployment whose runtime can already forge authority, before
        # resolving the distinct, explicitly configured owner credentials.
        with self._connect(target, readonly=True) as reader:
            self._verify_reader(reader)
        with self._connect(target.migration_target()) as owner, owner.transaction():
            owner.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            verify_component(owner, RESOURCE_ENVELOPE_MIGRATIONS)
            ownership = owner.execute(
                """SELECT n.nspowner=r.oid AND c.relowner=r.oid
                FROM pg_catalog.pg_namespace n
                JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid
                JOIN pg_catalog.pg_roles r ON r.rolname=current_user
                WHERE n.nspname=%s AND c.relname=%s""",
                (target.schema, RESOURCE_ENVELOPE_TABLE),
            ).fetchone()
            if ownership is None or ownership[0] is not True:
                raise SchemaMismatch("Resource authority writer must own its namespace and table")
            owner.execute(
                "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s,0))",
                (f"cao-resource:{target.store_id}:{target.schema}:{binding.instance_id}",),
            )
            table = sql.Identifier(target.schema, RESOURCE_ENVELOPE_TABLE)
            row = owner.execute(
                sql.SQL("SELECT max(revision) FROM {} WHERE instance_id=%s").format(table),
                (binding.instance_id,),
            ).fetchone()
            if row is None or (row[0] if row[0] is not None else 0) != expected_revision:
                raise EnvelopeRevisionConflict("Resource authority revision changed")
            values = {
                **asdict(binding),
                **asdict(claims),
                "revision": candidate.revision,
                "protocol_version": 1,
                "active": active,
                "actor_principal_id": principal_id,
            }
            owner.execute(
                sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    table,
                    sql.SQL(",").join(map(sql.Identifier, values)),
                    sql.SQL(",").join(sql.Placeholder() for _ in values),
                ),
                tuple(values.values()),
            )
        return candidate
