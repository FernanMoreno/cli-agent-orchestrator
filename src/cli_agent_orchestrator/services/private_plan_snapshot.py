"""Private, integrity-checked executable material for ``plan-v2``.

This module is intentionally not imported by any public API surface. Public approval
documents carry only the digests in :mod:`plan_identifier`; this store holds the raw
pre-redaction bytes and returns them only after every byte string matches that public
contract.

The 256 KiB total is an explicit exact-material ceiling. Per-scope memory's
separate 1000-character budget belongs to its later producer, not this store.
Private credentials and a non-owner database role protect raw executable material.
Snapshot attachment, run deletion and final-reference garbage collection share
one native PostgreSQL transaction. Reads use a repeatable read-only snapshot and
never bootstrap schema. Removing rows does not claim secure physical erasure.

"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from cli_agent_orchestrator.clients.postgres import (
    ConfigurationError,
    Connection,
    DatabaseError,
    DatabaseTarget,
    Record,
    connect,
)
from cli_agent_orchestrator.clients.postgres.migrations import (
    SchemaMismatch,
    verify_component,
    verify_runtime_role,
)
from cli_agent_orchestrator.constants import WORKFLOW_MANIFEST_MAX_BYTES
from cli_agent_orchestrator.services import plan_identifier

logger = logging.getLogger(__name__)

_STRUCTURED_MATERIALS = frozenset(
    {"declaration", "targets", "limits", "retry_policy", "policy", "memory"}
)
COMPONENT_MATERIAL_KINDS = {
    "artifact_hash": "source-bytes",
    **{name: "json-utf8" for name in _STRUCTURED_MATERIALS},
}


class PrivateSnapshotError(RuntimeError):
    """Base class whose messages never contain private snapshot bytes or paths."""


class PrivateSnapshotIntegrityError(PrivateSnapshotError):
    """Private bytes are absent or do not match the approved component digests."""

    def __init__(self, component: str, condition: str = "is invalid") -> None:
        super().__init__(
            f"private snapshot component {component} {condition}; start a new approved run"
        )


class PrivateSnapshotTooLargeError(PrivateSnapshotError):
    """Exact executable material exceeded its hard ceiling and was not truncated."""

    def __init__(self, component: str, limit: int) -> None:
        super().__init__(
            f"private snapshot component {component} exceeds {limit} bytes; "
            "executable material was refused"
        )


class PrivateSnapshotStoreError(PrivateSnapshotError):
    """The private store could not be opened or updated safely."""

    def __init__(self, cause: str = "unavailable") -> None:
        super().__init__(f"private snapshot store {cause}")


class PrivateSnapshotPermissionsError(PrivateSnapshotStoreError):
    """The shared database is exposed beyond its owner."""

    def __init__(self) -> None:
        super().__init__("runtime database privileges are unsafe")


@dataclass(frozen=True)
class VerifiedPrivateSnapshot:
    """Raw bytes returned only after full digest verification."""

    plan_id: str
    components: tuple[tuple[str, bytes], ...]

    def material(self, component: str) -> bytes:
        for name, content in self.components:
            if name == component:
                return content
        raise PrivateSnapshotIntegrityError(component, "is missing")

    def decode_material(self, component: str) -> Any:
        """Decode one already integrity-checked component by its fixed kind."""
        return decode_material(component, self.material(component))


def structured_material_bytes(value: Any) -> bytes:
    """Serialize one structured private component with the sole JSON contract."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise PrivateSnapshotIntegrityError("structured material") from None


def decode_material(name: str, material: bytes) -> Any:
    """Decode a verified material according to the fixed producer-kind table.

    JSON callers must obtain ``material`` from :class:`VerifiedPrivateSnapshot`;
    this function does not perform storage integrity verification itself.
    """
    if not isinstance(name, str) or not isinstance(material, bytes):
        raise PrivateSnapshotIntegrityError("material")
    kind = COMPONENT_MATERIAL_KINDS.get(name)
    try:
        if kind == "source-bytes":
            return material
        if kind == "json-utf8":
            value = json.loads(material.decode("utf-8"))
            if structured_material_bytes(value) != material:
                raise PrivateSnapshotIntegrityError(name)
            return value
        if name.startswith("input-key:"):
            value = material.decode("utf-8")
            if plan_identifier.canonical_key_bytes(value) != material:
                raise PrivateSnapshotIntegrityError("input key")
            return value
        if name.startswith("input-value:"):
            return plan_identifier.decode_component_bytes(material)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        plan_identifier.PlanV2ComponentError,
    ):
        raise PrivateSnapshotIntegrityError(name) from None
    raise PrivateSnapshotIntegrityError("material kind")


def _expected_digests(
    components: plan_identifier.PlanV2Components,
) -> dict[str, str]:
    plan_identifier.compute_v2(components)
    expected = {
        "artifact_hash": components.artifact_hash,
        "declaration": components.declaration,
        "targets": components.targets,
    }
    for item in components.inputs:
        expected[f"input-key:{item.key_digest}"] = item.key_digest
        expected[f"input-value:{item.key_digest}"] = item.value_digest
    expected.update(
        {
            "limits": components.limits,
            "retry_policy": components.retry_policy,
            "policy": components.policy,
            "memory": components.memory,
        }
    )
    return expected


def _validated_materials(
    components: plan_identifier.PlanV2Components, materials: Any
) -> tuple[str, dict[str, bytes], dict[str, str]]:
    expected = _expected_digests(components)
    if not isinstance(materials, dict) or set(materials) != set(expected):
        raise PrivateSnapshotIntegrityError("set")
    checked: dict[str, bytes] = {}
    total = 0
    for name, digest in expected.items():
        content = materials[name]
        if not isinstance(content, bytes):
            raise PrivateSnapshotIntegrityError(name)
        size = len(content)
        if size > WORKFLOW_MANIFEST_MAX_BYTES:
            raise PrivateSnapshotTooLargeError(name, WORKFLOW_MANIFEST_MAX_BYTES)
        total += size
        if plan_identifier.digest_bytes(content) != digest:
            raise PrivateSnapshotIntegrityError(name)
        checked[name] = content
    if total > WORKFLOW_MANIFEST_MAX_BYTES:
        raise PrivateSnapshotTooLargeError("total snapshot", WORKFLOW_MANIFEST_MAX_BYTES)
    return plan_identifier.compute_v2(components), checked, expected


def _verify_storage_permissions(connection: Connection) -> None:
    try:
        verify_runtime_role(connection)
        connection.target.connection_parameters()
    except (ConfigurationError, SchemaMismatch):
        raise PrivateSnapshotPermissionsError() from None


def _verify_borrowed_connection(connection: Connection) -> None:
    from cli_agent_orchestrator.clients.private_plan_postgres_schema import PRIVATE_PLAN_MIGRATIONS

    if not connection.in_transaction:
        raise PrivateSnapshotStoreError("transaction required")
    _verify_storage_permissions(connection)
    try:
        verify_component(connection, PRIVATE_PLAN_MIGRATIONS)
    except SchemaMismatch:
        raise PrivateSnapshotStoreError("schema verification failed") from None


def _connect() -> Connection:
    from cli_agent_orchestrator.clients.private_plan_postgres_schema import PRIVATE_PLAN_MIGRATIONS

    connection = None
    try:
        connection = connect(DatabaseTarget.from_settings())
        connection.begin()
        _verify_storage_permissions(connection)
        verify_component(connection, PRIVATE_PLAN_MIGRATIONS)
        return connection
    except (OSError, ConfigurationError, DatabaseError, SchemaMismatch):
        if connection is not None:
            connection.close()
        raise PrivateSnapshotStoreError() from None


def _connect_readonly() -> Connection:
    from cli_agent_orchestrator.clients.private_plan_postgres_schema import PRIVATE_PLAN_MIGRATIONS

    connection = None
    try:
        connection = connect(DatabaseTarget.from_settings(), readonly=True)
        _verify_storage_permissions(connection)
        verify_component(connection, PRIVATE_PLAN_MIGRATIONS)
        return connection
    except (OSError, ConfigurationError, DatabaseError, SchemaMismatch):
        if connection is not None:
            connection.close()
        raise PrivateSnapshotStoreError() from None


def _table_exists(connection: Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s AND c.relkind='r'",
            (connection.target.schema, table),
        ).fetchone()
        is not None
    )


def _store_or_verify(
    conn: Connection,
    plan_id: str,
    components: plan_identifier.PlanV2Components,
    checked: dict[str, bytes],
    expected: dict[str, str],
) -> None:
    existing = conn.execute(
        "SELECT component_set_version FROM workflow_plan_snapshot WHERE plan_id = %s",
        (plan_id,),
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO workflow_plan_snapshot (plan_id, component_set_version) VALUES (%s, %s)",
            (plan_id, plan_identifier.PLAN_V2_COMPONENT_SET_VERSION),
        )
        conn.executemany(
            "INSERT INTO workflow_plan_snapshot_component "
            "(plan_id, component_name, content_digest, content) VALUES (%s, %s, %s, %s)",
            [(plan_id, name, expected[name], checked[name]) for name in sorted(checked)],
        )
    else:
        _load_verified(conn, plan_id, components)


def _require_run(conn: Connection, run_id: str) -> None:
    if not _table_exists(conn, "workflow_run"):
        raise PrivateSnapshotIntegrityError("run reference")
    if (
        conn.execute(
            "SELECT 1 FROM workflow_run WHERE run_id = %s FOR UPDATE", (run_id,)
        ).fetchone()
        is None
    ):
        raise PrivateSnapshotIntegrityError("run reference")


def _attach(conn: Connection, run_id: str, plan_id: str) -> None:
    existing = conn.execute(
        "SELECT plan_id FROM workflow_run_plan_snapshot WHERE run_id = %s",
        (run_id,),
    ).fetchone()
    if existing is not None and existing[0] != plan_id:
        raise PrivateSnapshotIntegrityError("run reference")
    conn.execute(
        "INSERT INTO workflow_run_plan_snapshot (run_id, plan_id) VALUES (%s, %s) ON CONFLICT (run_id) DO NOTHING",
        (run_id, plan_id),
    )


def freeze_and_attach(
    run_id: str,
    components: plan_identifier.PlanV2Components,
    materials: Any,
    conn: Optional[Connection] = None,
) -> str:
    """Atomically validate, freeze, and attach exact bytes to an existing run.

    With no connection this owns one native PostgreSQL transaction. A caller
    creating an approved run may pass its already-active transaction so the run
    row and private material commit or roll back together; this function never
    commits or rolls back a borrowed connection.
    """
    plan_id, checked, expected = _validated_materials(components, materials)

    def _freeze(active: Connection) -> None:
        active.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"private-plan:{active.target.store_id}:{plan_id}",),
        )
        _require_run(active, run_id)
        _store_or_verify(active, plan_id, components, checked, expected)
        _attach(active, run_id, plan_id)

    if conn is not None:
        try:
            _verify_borrowed_connection(conn)
            _freeze(conn)
        except (PrivateSnapshotError, plan_identifier.PlanV2ComponentError):
            raise
        except (OSError, DatabaseError):
            raise PrivateSnapshotStoreError() from None
        return plan_id

    try:
        with closing(_connect()) as own:
            _verify_storage_permissions(own)
            with own:
                _freeze(own)
    except (PrivateSnapshotError, plan_identifier.PlanV2ComponentError):
        raise
    except DatabaseError:
        raise PrivateSnapshotStoreError() from None
    return plan_id


def _load_verified(
    conn: Connection,
    plan_id: str,
    components: plan_identifier.PlanV2Components,
) -> VerifiedPrivateSnapshot:
    expected_plan_id = plan_identifier.compute_v2(components)
    if plan_id != expected_plan_id:
        raise PrivateSnapshotIntegrityError("plan_id")
    header = conn.execute(
        "SELECT component_set_version FROM workflow_plan_snapshot WHERE plan_id = %s",
        (plan_id,),
    ).fetchone()
    if header is None:
        raise PrivateSnapshotIntegrityError("snapshot", "is missing")
    if header[0] != plan_identifier.PLAN_V2_COMPONENT_SET_VERSION:
        raise PrivateSnapshotIntegrityError("component_set_version")
    expected = _expected_digests(components)
    rows = conn.execute(
        "SELECT component_name, content_digest, content "
        "FROM workflow_plan_snapshot_component WHERE plan_id = %s "
        "ORDER BY component_name",
        (plan_id,),
    ).fetchall()
    found = {}
    for name, stored_digest, raw_content in rows:
        if not isinstance(name, str) or name not in expected:
            raise PrivateSnapshotIntegrityError("set")
        if not isinstance(stored_digest, str) or not isinstance(
            raw_content, (bytes, bytearray, memoryview)
        ):
            raise PrivateSnapshotIntegrityError(name)
        found[name] = (stored_digest, bytes(raw_content))
    if set(found) != set(expected):
        missing = next((name for name in expected if name not in found), "set")
        raise PrivateSnapshotIntegrityError(missing, "is missing")
    verified = []
    for name in sorted(expected):
        stored_digest, content = found[name]
        if (
            stored_digest != expected[name]
            or plan_identifier.digest_bytes(content) != expected[name]
        ):
            raise PrivateSnapshotIntegrityError(name)
        verified.append((name, content))
    return VerifiedPrivateSnapshot(plan_id=plan_id, components=tuple(verified))


def _components_from_stored_digests(
    tier: Any, rows: Iterable[Record | tuple[Any, Any]]
) -> plan_identifier.PlanV2Components:
    fixed_names = frozenset(COMPONENT_MATERIAL_KINDS)
    fixed: dict[str, str] = {}
    input_keys: dict[str, str] = {}
    input_values: dict[str, str] = {}
    seen_names: set[str] = set()

    for name, digest in rows:
        if not isinstance(name, str) or not isinstance(digest, str) or name in seen_names:
            raise PrivateSnapshotIntegrityError("component set")
        seen_names.add(name)
        if name in fixed_names:
            fixed[name] = digest
            continue
        if name.startswith("input-key:"):
            key_digest = name.removeprefix("input-key:")
            if digest != key_digest:
                raise PrivateSnapshotIntegrityError("input set")
            input_keys[key_digest] = digest
            continue
        if name.startswith("input-value:"):
            key_digest = name.removeprefix("input-value:")
            input_values[key_digest] = digest
            continue
        raise PrivateSnapshotIntegrityError("component set")

    if set(fixed) != fixed_names:
        raise PrivateSnapshotIntegrityError("component set")
    if set(input_keys) != set(input_values):
        raise PrivateSnapshotIntegrityError("input set")

    try:
        return plan_identifier.PlanV2Components(
            tier=tier,
            artifact_hash=fixed["artifact_hash"],
            declaration=fixed["declaration"],
            targets=fixed["targets"],
            inputs=tuple(
                plan_identifier.InputDigest(
                    key_digest=key_digest,
                    value_digest=input_values[key_digest],
                )
                for key_digest in sorted(input_keys)
            ),
            limits=fixed["limits"],
            retry_policy=fixed["retry_policy"],
            policy=fixed["policy"],
            memory=fixed["memory"],
        )
    except plan_identifier.PlanV2ComponentError as exc:
        raise PrivateSnapshotIntegrityError("component set") from exc


def stored_plan_components_for_run(
    run_id: str,
) -> tuple[str, plan_identifier.PlanV2Components]:
    """Reconstruct one run's approved identity from stored digest rows only.

    This read does not inspect or verify private component BLOBs. Call
    :func:`private_snapshot_for_run` with the returned components to perform
    that separate byte-integrity check.
    """
    if not isinstance(run_id, str):
        raise PrivateSnapshotIntegrityError("run reference")
    try:
        run_id.encode("utf-8")
    except UnicodeEncodeError:
        raise PrivateSnapshotIntegrityError("run reference") from None
    try:
        with closing(_connect_readonly()) as conn:
            try:
                if not all(
                    _table_exists(conn, table)
                    for table in (
                        "workflow_run",
                        "workflow_run_plan_snapshot",
                        "workflow_plan_snapshot",
                        "workflow_plan_snapshot_component",
                    )
                ):
                    raise PrivateSnapshotIntegrityError("run reference", "is missing")

                links = conn.execute(
                    "SELECT plan_id FROM workflow_run_plan_snapshot WHERE run_id = %s",
                    (run_id,),
                ).fetchall()
                tiers = conn.execute(
                    "SELECT tier FROM workflow_run WHERE run_id = %s",
                    (run_id,),
                ).fetchall()
                if len(links) != 1 or len(tiers) != 1:
                    raise PrivateSnapshotIntegrityError("run reference", "is missing")
                plan_id = links[0][0]
                tier = tiers[0][0]
                if not isinstance(plan_id, str):
                    raise PrivateSnapshotIntegrityError("run reference")

                headers = conn.execute(
                    "SELECT component_set_version FROM workflow_plan_snapshot "
                    "WHERE plan_id = %s",
                    (plan_id,),
                ).fetchall()
                if headers != [(plan_identifier.PLAN_V2_COMPONENT_SET_VERSION,)]:
                    raise PrivateSnapshotIntegrityError("component_set_version")

                rows = conn.execute(
                    "SELECT component_name, content_digest "
                    "FROM workflow_plan_snapshot_component WHERE plan_id = %s",
                    (plan_id,),
                ).fetchall()
                components = _components_from_stored_digests(tier, rows)
                try:
                    reconstructed_plan_id = plan_identifier.compute_v2(components)
                except plan_identifier.PlanV2ComponentError as exc:
                    raise PrivateSnapshotIntegrityError("component set") from exc
                if reconstructed_plan_id != plan_id:
                    raise PrivateSnapshotIntegrityError("plan_id")
                return plan_id, components
            finally:
                if conn.in_transaction:
                    conn.rollback()
    except (PrivateSnapshotError, plan_identifier.PlanV2ComponentError):
        raise
    except DatabaseError:
        raise PrivateSnapshotStoreError() from None


def private_snapshot_for_run(
    run_id: str, components: plan_identifier.PlanV2Components
) -> VerifiedPrivateSnapshot:
    try:
        with closing(_connect_readonly()) as conn:
            if not _table_exists(conn, "workflow_run_plan_snapshot"):
                raise PrivateSnapshotIntegrityError("run reference", "is missing")
            row = conn.execute(
                "SELECT plan_id FROM workflow_run_plan_snapshot WHERE run_id = %s",
                (run_id,),
            ).fetchone()
            if row is None:
                raise PrivateSnapshotIntegrityError("run reference", "is missing")
            return _load_verified(conn, row[0], components)
    except (PrivateSnapshotError, plan_identifier.PlanV2ComponentError):
        raise
    except DatabaseError:
        raise PrivateSnapshotStoreError() from None


def release_run_reference(run_id: str, conn: Optional[Connection] = None) -> None:
    """Release one run and garbage-collect bytes only after the last reference.

    Native foreign keys enforce references; the plan advisory lock serializes
    attachment and final-reference collection. Removal does not claim physical
    erasure from database pages or backup history.
    """

    def _release(active: Connection) -> None:
        row = active.execute(
            "SELECT plan_id FROM workflow_run_plan_snapshot WHERE run_id = %s",
            (run_id,),
        ).fetchone()
        if row is None:
            return
        plan_id = row[0]
        active.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"private-plan:{active.target.store_id}:{plan_id}",),
        )
        active.execute("DELETE FROM workflow_run_plan_snapshot WHERE run_id = %s", (run_id,))
        remaining = active.execute(
            "SELECT 1 FROM workflow_run_plan_snapshot WHERE plan_id = %s LIMIT 1",
            (plan_id,),
        ).fetchone()
        if remaining is None and _table_exists(active, "workflow_prepared_plan"):
            remaining = active.execute(
                "SELECT 1 FROM workflow_prepared_plan WHERE plan_id=%s LIMIT 1", (plan_id,)
            ).fetchone()
        if remaining is None:
            active.execute(
                "DELETE FROM workflow_plan_snapshot_component WHERE plan_id = %s",
                (plan_id,),
            )
            active.execute("DELETE FROM workflow_plan_snapshot WHERE plan_id = %s", (plan_id,))

    if conn is not None:
        try:
            _verify_borrowed_connection(conn)
            _release(conn)
        except PrivateSnapshotError:
            raise
        except DatabaseError:
            raise PrivateSnapshotStoreError() from None
        return
    try:
        with closing(_connect()) as own:
            with own:
                _release(own)
    except PrivateSnapshotError:
        raise
    except DatabaseError:
        raise PrivateSnapshotStoreError() from None
