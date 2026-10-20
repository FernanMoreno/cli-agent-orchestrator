"""PostgreSQL-native, offline-cut recovery bundles.

This module captures the configured main PostgreSQL store plus the external
objects its durable rows reference, and restores only to a distinct, prepared
PostgreSQL target. It never publishes a configuration or makes a restored
store executable.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import stat
import tempfile
import uuid
from abc import ABC, abstractmethod
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import date, datetime
from datetime import time as datetime_time
from datetime import timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, TypedDict

from psycopg import sql

from cli_agent_orchestrator.clients.postgres import DatabaseTarget, connect
from cli_agent_orchestrator.clients.postgres.migrations import (
    SchemaMismatch,
    catalog_manifest,
    verify_component,
    verify_runtime_role,
)
from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope, component_migrations
from cli_agent_orchestrator.clients.work_postgres_schema import WORK_POSTGRES_MIGRATIONS
from cli_agent_orchestrator.clients.work_repository import WorkRepository, bootstrap_work_store
from cli_agent_orchestrator.services.secret_gate import scan_for_secrets
from cli_agent_orchestrator.services.work_authority import (
    OfflineCutLease,
    WorkAuthority,
    public_offline_store_identity,
)

_ERROR = "recovery bundle rejected"
_FORMAT = "cao-postgres-recovery-bundle-v3"
_MANIFEST = "manifest.json"
_MAX_EXECUTABLE_BYTES = 8 * 1024 * 1024
_MAX_OBJECT_BYTES = 512 * 1024 * 1024
_OBJECT_ROLES = frozenset(
    {
        "result-content",
        "worktree-evidence",
        "delivery-content",
        "executable-content",
        "memory-content",
    }
)


class RecoveryBundleError(RuntimeError):
    """Fixed, non-sensitive error raised for malformed or unsafe bundles."""


class _ObjectMetadata(TypedDict):
    digest: str
    size: int
    roles: list[str]


class PostgresArchiveRunner(ABC):
    """Server-owned pg_dump/pg_restore adapter.

    Implementations are composed by the installer or a controlled test. The
    method receives targets and private file paths, never caller-supplied
    command fragments or credentials.
    """

    @abstractmethod
    def dump(self, target: DatabaseTarget, destination: Path, *, snapshot_id: str) -> None:
        """Dump the target application schema from the supplied exported snapshot."""

    @abstractmethod
    def restore(self, target: DatabaseTarget, archive: Path) -> None:
        """Restore to an absent schema atomically; failure must roll back all DDL/data."""


@dataclass(frozen=True)
class RecoveryCaptureSource:
    """Configured main target and its declared file-backed content roots."""

    database: DatabaseTarget
    immutable_result_root: Path | None = None
    delivery_content_root: Path | None = None
    executable_content_root: Path | None = None
    memory_root: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.database, DatabaseTarget) or self.database.scope != "main":
            raise RecoveryBundleError(_ERROR)
        roots = (
            self.immutable_result_root,
            self.delivery_content_root,
            self.executable_content_root,
            self.memory_root,
        )
        if any(root is not None and not isinstance(root, Path) for root in roots):
            raise RecoveryBundleError(_ERROR)


@dataclass(frozen=True)
class RecoveryBundleReceipt:
    """Verified private bundle locator and its server-published source receipt."""

    bundle_path: Path
    manifest_digest: str
    manifest_size: int
    source_target_identity: tuple[str, int, str, str, str]
    source_store_id: str
    capture_id: str
    lease_id: str
    publication_revision: int


@dataclass(frozen=True)
class RecoveryRestoreReceipt:
    """Outcome of a validated restore; configuration activation is separate."""

    target_identity: tuple[str, int, str, str, str]
    source_store_id: str
    bundle_digest: str
    work_installation_uuid: str
    execution_state: str


@dataclass(frozen=True)
class _ExternalRef:
    role: str
    identifier: str
    root: Path
    relative_path: str
    expected_digest: str | None
    expected_size: int | None
    relative_memory_path: str | None = None


def _reject() -> RecoveryBundleError:
    return RecoveryBundleError(_ERROR)


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _private_directory(path: Path) -> None:
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or stat.S_IMODE(info.st_mode) & 0o077
        or info.st_uid != os.geteuid()
    ):
        raise _reject()


def _private_file(path: Path) -> os.stat_result:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or stat.S_IMODE(info.st_mode) & 0o077
        or info.st_uid != os.geteuid()
    ):
        raise _reject()
    return info


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _normal(value: object) -> object:
    """Losslessly normalize native values for stable row fingerprints."""
    if value is None or type(value) in {str, int, bool}:
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise _reject()
        return {"$float64": value.hex()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$bytes": bytes(value).hex()}
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return {"$datetime": value.isoformat(), "aware": value.tzinfo is not None}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, datetime_time):
        return {"$time": value.isoformat()}
    if isinstance(value, uuid.UUID):
        return {"$uuid": str(value)}
    if isinstance(value, list):
        return [_normal(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normal(item) for key, item in value.items()}
    raise _reject()


def _complete_histories(scope: str) -> tuple[tuple, ...]:
    histories: dict[tuple[str, str], tuple] = {}
    for history in component_migrations(scope):
        key = (history[0].scope, history[0].component)
        if key not in histories or len(history) > len(histories[key]):
            histories[key] = tuple(history)
    return tuple(histories[key] for key in sorted(histories))


def _owned_tables(scope: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                "_cao_schema_migrations",
                "_cao_store_identity",
                *(
                    table
                    for history in _complete_histories(scope)
                    for step in history
                    for table in step.tables
                ),
            }
        )
    )


def _table_primary_key(connection, target: DatabaseTarget, table: str) -> tuple[str, ...]:
    rows = connection.execute(
        """SELECT a.attname FROM pg_constraint c
        JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
        JOIN unnest(c.conkey) WITH ORDINALITY k(attnum,position) ON TRUE
        JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum
        WHERE c.contype='p' AND n.nspname=%s AND t.relname=%s ORDER BY k.position""",
        (target.schema, table),
    ).fetchall()
    return tuple(row[0] for row in rows)


def _row_digest(
    connection,
    target: DatabaseTarget,
    table: str,
    *,
    memory_root: Path | None,
    excluded_capture_id: str | None,
) -> tuple[int, str]:
    columns = tuple(
        row[0]
        for row in connection.execute(
            """SELECT a.attname FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
            JOIN pg_attribute a ON a.attrelid=t.oid WHERE n.nspname=%s AND t.relname=%s
            AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""",
            (target.schema, table),
        ).fetchall()
    )
    if not columns:
        raise _reject()
    order = _table_primary_key(connection, target, table) or columns
    query = sql.SQL("SELECT * FROM {} ORDER BY {}").format(
        sql.Identifier(target.schema, table),
        sql.SQL(",").join(sql.Identifier(column) for column in order),
    )
    parameters: tuple[object, ...] = ()
    if table == "work_offline_cut_observations" and excluded_capture_id is not None:
        query = sql.SQL("SELECT * FROM {} WHERE capture_id<>%s ORDER BY {}").format(
            sql.Identifier(target.schema, table),
            sql.SQL(",").join(sql.Identifier(column) for column in order),
        )
        parameters = (excluded_capture_id,)
    digest = hashlib.sha256()
    count = 0
    for row in connection.execute(query, parameters):
        normalized = {column: _normal(row[column]) for column in columns}
        if table == "memory_metadata" and "file_path" in columns:
            if memory_root is None or not isinstance(row["file_path"], str):
                raise _reject()
            normalized["file_path"] = _safe_relative_path(row["file_path"], memory_root)
        payload = _json_bytes(normalized)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        count += 1
    return count, digest.hexdigest()


def _scan_exportable_rows(connection, tables: tuple[str, ...]) -> None:
    """Reject secrets and unsafe text before a database dump is published."""
    byte_content_columns = {
        ("work_delegation_snapshots", "content"),
        ("workflow_plan_snapshot_component", "content"),
    }
    for table in tables:
        query = sql.SQL("SELECT * FROM {}").format(sql.Identifier(table))
        for row in connection.execute(query):
            for column, value in zip(row.keys(), row):
                if isinstance(value, str):
                    if (
                        any(
                            character not in "\n\r\t" and not character.isprintable()
                            for character in value
                        )
                        or scan_for_secrets(value) is not None
                    ):
                        raise _reject()
                elif isinstance(value, (bytes, bytearray, memoryview)):
                    if (table, column) not in byte_content_columns:
                        raise _reject()
                    try:
                        decoded = bytes(value).decode("utf-8")
                    except UnicodeDecodeError:
                        raise _reject() from None
                    if (
                        any(
                            character not in "\n\r\t" and not character.isprintable()
                            for character in decoded
                        )
                        or scan_for_secrets(decoded) is not None
                    ):
                        raise _reject()


def _sequence_inventory(
    connection, target: DatabaseTarget, tables: tuple[str, ...]
) -> list[dict[str, object]]:
    sequences = connection.execute(
        """SELECT t.relname AS table_name, a.attname AS column_name, s.relname AS sequence_name,
        q.seqstart, q.seqincrement, q.seqmin, q.seqmax, q.seqcache, q.seqcycle
        FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
        JOIN pg_attribute a ON a.attrelid=t.oid
        JOIN pg_depend d ON d.refobjid=t.oid AND d.refobjsubid=a.attnum AND d.deptype IN ('a','i')
        JOIN pg_class s ON s.oid=d.objid JOIN pg_sequence q ON q.seqrelid=s.oid
        WHERE n.nspname=%s AND t.relname=ANY(%s) ORDER BY t.relname,a.attnum""",
        (target.schema, list(tables)),
    ).fetchall()
    result: list[dict[str, object]] = []
    for row in sequences:
        state_query = sql.SQL("SELECT last_value,is_called FROM {}").format(
            sql.Identifier(target.schema, row["sequence_name"])
        )
        state = connection.execute(state_query).fetchone()
        result.append(
            {
                "table": row["table_name"],
                "column": row["column_name"],
                "start": row["seqstart"],
                "increment": row["seqincrement"],
                "minimum": row["seqmin"],
                "maximum": row["seqmax"],
                "cache": row["seqcache"],
                "cycle": row["seqcycle"],
                "last_value": state["last_value"],
                "is_called": state["is_called"],
            }
        )
    return result


def _safe_relative_path(value: str, root: Path) -> str:
    path = Path(value)
    if not path.is_absolute():
        raise _reject()
    try:
        relative = path.relative_to(root.absolute())
    except ValueError:
        raise _reject() from None
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise _reject()
    return PurePosixPath(*relative.parts).as_posix()


def _external_refs(connection, source: RecoveryCaptureSource) -> tuple[_ExternalRef, ...]:
    roots = {
        "result-content": source.immutable_result_root,
        "worktree-evidence": source.immutable_result_root,
        "delivery-content": source.delivery_content_root,
        "executable-content": source.executable_content_root,
    }
    queries = (
        (
            "result-content",
            "SELECT id,content_hash,immutable_location,byte_length FROM work_results",
        ),
        (
            "worktree-evidence",
            "SELECT id,content_hash,immutable_location,byte_length FROM work_worktree_evidence",
        ),
        (
            "delivery-content",
            "SELECT attempt_id || ':' || generation,content_hash,content_ref,byte_length FROM work_delivery_content_refs",
        ),
        (
            "executable-content",
            "SELECT content_hash,content_hash,immutable_location,byte_length FROM work_executable_contents",
        ),
    )
    refs: list[_ExternalRef] = []
    for role, query in queries:
        root = roots[role]
        for identifier, digest, location, size in connection.execute(query):
            if (
                not isinstance(identifier, str)
                or not identifier
                or not _is_digest(digest)
                or location != digest
                or type(size) is not int
                or size < 0
                or root is None
                or (role == "executable-content" and not 1 <= size <= _MAX_EXECUTABLE_BYTES)
            ):
                raise _reject()
            _directory_no_symlink(root.absolute())
            refs.append(_ExternalRef(role, identifier, root.absolute(), digest, digest, size))
    memory_rows = connection.execute("SELECT id,file_path FROM memory_metadata").fetchall()
    if memory_rows and source.memory_root is None:
        raise _reject()
    if source.memory_root is not None and memory_rows:
        root = source.memory_root.absolute()
        _directory_no_symlink(root)
        for identifier, path in memory_rows:
            if not isinstance(identifier, str) or not identifier or not isinstance(path, str):
                raise _reject()
            relative = _safe_relative_path(path, root)
            refs.append(
                _ExternalRef("memory-content", identifier, root, relative, None, None, relative)
            )
    addresses = [(reference.role, reference.identifier) for reference in refs]
    if len(addresses) != len(set(addresses)):
        raise _reject()
    return tuple(sorted(refs, key=lambda item: (item.role, item.identifier)))


def _directory_no_symlink(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise _reject()


def _open_regular_file_beneath(root: Path, relative_path: str) -> tuple[int, os.stat_result]:
    relative = PurePosixPath(relative_path)
    if not _valid_relative_memory(relative_path):
        raise _reject()
    absolute = root.absolute()
    if ".." in absolute.parts:
        raise _reject()
    directory = os.open(
        absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        for component in absolute.parts[1:]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        for component in relative.parts[:-1]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        descriptor = os.open(
            relative.parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=directory,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(descriptor)
            raise _reject()
        return descriptor, info
    finally:
        os.close(directory)


def _hash_open_file(
    descriptor: int,
    *,
    before: os.stat_result,
    scan_text: bool,
    maximum_size: int = _MAX_OBJECT_BYTES,
) -> tuple[str, int, tuple[int, ...]]:
    if before.st_size > maximum_size:
        raise _reject()
    digest = hashlib.sha256()
    content = bytearray() if scan_text else None
    size = 0
    while block := os.read(descriptor, 1024 * 1024):
        size += len(block)
        if size > maximum_size:
            raise _reject()
        digest.update(block)
        if content is not None:
            content.extend(block)
    after = os.fstat(descriptor)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if identity_before != identity_after or size != after.st_size:
        raise _reject()
    if content is not None:
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError:
            raise _reject() from None
        if scan_for_secrets(decoded) is not None:
            raise _reject()
    return digest.hexdigest(), size, identity_after


def _file_hash(path: Path, *, scan_text: bool) -> tuple[str, int, tuple[int, ...]]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise _reject()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        return _hash_open_file(descriptor, before=os.fstat(descriptor), scan_text=scan_text)
    finally:
        os.close(descriptor)


def _object_size_limit(role: str) -> int:
    return _MAX_EXECUTABLE_BYTES if role == "executable-content" else _MAX_OBJECT_BYTES


def _external_hash(reference: _ExternalRef) -> tuple[str, int, tuple[int, ...]]:
    descriptor, before = _open_regular_file_beneath(reference.root, reference.relative_path)
    try:
        return _hash_open_file(
            descriptor,
            before=before,
            scan_text=reference.role != "executable-content",
            maximum_size=_object_size_limit(reference.role),
        )
    finally:
        os.close(descriptor)


def _copy_object(
    reference: _ExternalRef, expected: dict[str, _ObjectMetadata], objects: Path
) -> dict[str, object]:
    descriptor, source_info = _open_regular_file_beneath(reference.root, reference.relative_path)
    try:
        digest, size, identity = _hash_open_file(
            descriptor,
            before=source_info,
            scan_text=reference.role != "executable-content",
            maximum_size=_object_size_limit(reference.role),
        )
        if (reference.expected_digest is not None and digest != reference.expected_digest) or (
            reference.expected_size is not None and size != reference.expected_size
        ):
            raise _reject()
        temporary = objects / f".object-{uuid.uuid4().hex}"
        target = objects / digest
        destination_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        observed = hashlib.sha256()
        copied_size = 0
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            with (
                os.fdopen(descriptor, "rb", closefd=False) as source,
                os.fdopen(destination_descriptor, "wb", closefd=False) as output,
            ):
                while block := source.read(1024 * 1024):
                    copied_size += len(block)
                    observed.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if (observed.hexdigest(), copied_size) != (digest, size):
                raise _reject()
            after = os.fstat(descriptor)
            if (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) != identity:
                raise _reject()
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                if _file_hash(target, scan_text=False)[:2] != (digest, size):
                    raise _reject()
            os.unlink(temporary)
        finally:
            os.close(destination_descriptor)
            temporary.unlink(missing_ok=True)
        expected.setdefault(digest, {"digest": digest, "size": size, "roles": []})
        roles = expected[digest]["roles"]
        if reference.role not in roles:
            roles.append(reference.role)
        return {
            "role": reference.role,
            "identifier": reference.identifier,
            "digest": digest,
            "size": size,
            "relative_path": reference.relative_memory_path,
        }
    finally:
        os.close(descriptor)


def _endpoint_fingerprint(target: DatabaseTarget) -> str:
    return _digest_bytes(_json_bytes([target.host, target.port, target.database, target.schema]))


def _verify_closed_relations(connection, target: DatabaseTarget, tables: tuple[str, ...]) -> None:
    """Reject relations or sequences outside the reviewed main-store catalog."""
    relations = {
        row[0]
        for row in connection.execute(
            """SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m','f')""",
            (target.schema,),
        ).fetchall()
    }
    if relations != set(tables):
        raise SchemaMismatch("PostgreSQL schema contains unreviewed relations")

    owned_sequences = {
        row[0]
        for row in connection.execute(
            """SELECT s.relname FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
            JOIN pg_attribute a ON a.attrelid=t.oid
            JOIN pg_depend d ON d.refobjid=t.oid AND d.refobjsubid=a.attnum
                AND d.deptype IN ('a','i')
            JOIN pg_class s ON s.oid=d.objid AND s.relkind='S'
            WHERE n.nspname=%s AND t.relname=ANY(%s)""",
            (target.schema, list(tables)),
        ).fetchall()
    }
    sequences = {
        row[0]
        for row in connection.execute(
            """SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relkind='S'""",
            (target.schema,),
        ).fetchall()
    }
    if sequences != owned_sequences:
        raise SchemaMismatch("PostgreSQL schema contains unreviewed sequences")


def _verify_closed_routines(
    connection, target: DatabaseTarget, expected_routines: list[dict[str, Any]]
) -> None:
    """Reject standalone schema routines that are absent from the table catalog."""

    def identity_key(name, arguments, kind):
        normalized_arguments = tuple(tuple(argument) for argument in (arguments or ()))
        return name, normalized_arguments, kind

    expected = {
        identity_key(routine["name"], routine["identity_arguments"], routine["kind"])
        for routine in expected_routines
    }
    actual = {
        identity_key(row[0], row[1], row[2])
        for row in connection.execute(
            """SELECT p.proname,
            (SELECT jsonb_agg(jsonb_build_array(type_namespace.nspname,type_catalog.typname)
                    ORDER BY argument.ordinal)
             FROM unnest(p.proargtypes::oid[]) WITH ORDINALITY AS argument(type_oid,ordinal)
             JOIN pg_catalog.pg_type type_catalog ON type_catalog.oid=argument.type_oid
             JOIN pg_catalog.pg_namespace type_namespace
                ON type_namespace.oid=type_catalog.typnamespace
            ) AS identity_arguments,
            p.prokind
            FROM pg_catalog.pg_proc p
            JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=%s
            ORDER BY p.proname, p.prokind""",
            (target.schema,),
        ).fetchall()
    }
    if actual != expected:
        raise SchemaMismatch("PostgreSQL schema contains unreviewed routines")


def _verify_closed_types(connection, target: DatabaseTarget, tables: tuple[str, ...]) -> None:
    """Reject standalone user-defined types outside reviewed table row types."""
    expected: set[int] = set()
    for row in connection.execute(
        """SELECT t.oid, t.typarray FROM pg_catalog.pg_type t
        JOIN pg_catalog.pg_class c ON c.oid=t.typrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s AND c.relname=ANY(%s) AND c.relkind IN ('r','p','v','m','f')""",
        (target.schema, list(tables)),
    ).fetchall():
        expected.add(row[0])
        if row[1]:
            expected.add(row[1])
    actual = {
        row[0]
        for row in connection.execute(
            """SELECT t.oid FROM pg_catalog.pg_type t
            JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
            WHERE n.nspname=%s""",
            (target.schema,),
        ).fetchall()
    }
    if actual != expected:
        raise SchemaMismatch("PostgreSQL schema contains unreviewed types")


def _verify_closed_schema_auxiliaries(connection, target: DatabaseTarget) -> None:
    """Reject schema-scoped PostgreSQL objects without a reviewed migration owner."""
    row = connection.execute(
        """WITH target_namespace AS (
                SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=%s
            ), auxiliary_namespaces(namespace_oid) AS (
                SELECT oprnamespace FROM pg_catalog.pg_operator
                UNION ALL SELECT opcnamespace FROM pg_catalog.pg_opclass
                UNION ALL SELECT opfnamespace FROM pg_catalog.pg_opfamily
                UNION ALL SELECT collnamespace FROM pg_catalog.pg_collation
                UNION ALL SELECT connamespace FROM pg_catalog.pg_conversion
                UNION ALL SELECT cfgnamespace FROM pg_catalog.pg_ts_config
                UNION ALL SELECT dictnamespace FROM pg_catalog.pg_ts_dict
                UNION ALL SELECT prsnamespace FROM pg_catalog.pg_ts_parser
                UNION ALL SELECT tmplnamespace FROM pg_catalog.pg_ts_template
            )
            SELECT 1 FROM auxiliary_namespaces a
            JOIN target_namespace n ON n.oid=a.namespace_oid
            LIMIT 1""",
        (target.schema,),
    ).fetchone()
    if row is not None:
        raise SchemaMismatch("PostgreSQL schema contains unreviewed auxiliary objects")


def _verify_closed_statistics(connection, target: DatabaseTarget) -> None:
    """No current component migration owns extended-statistics objects."""
    row = connection.execute(
        """SELECT 1 FROM pg_catalog.pg_statistic_ext s
        JOIN pg_catalog.pg_namespace n ON n.oid=s.stxnamespace
        WHERE n.nspname=%s LIMIT 1""",
        (target.schema,),
    ).fetchone()
    if row is not None:
        raise SchemaMismatch("PostgreSQL schema contains unreviewed statistics")


def _database_inventory(
    target: DatabaseTarget,
    source: RecoveryCaptureSource,
    *,
    excluded_capture_id: str | None = None,
    connection=None,
) -> dict[str, Any]:
    histories = _complete_histories("main")
    tables = _owned_tables("main")
    connection_context = (
        connect(target, readonly=True) if connection is None else nullcontext(connection)
    )
    with connection_context as connection:
        verify_runtime_role(connection)
        _verify_closed_relations(connection, target, tables)
        for history in histories:
            verify_component(connection, history)
        catalog = catalog_manifest(connection, tables)
        _verify_closed_routines(connection, target, catalog["routines"])
        _verify_closed_types(connection, target, tables)
        _verify_closed_schema_auxiliaries(connection, target)
        _verify_closed_statistics(connection, target)
        _scan_exportable_rows(connection, tables)
        rows: dict[str, dict[str, object]] = {}
        for table in tables:
            count, digest = _row_digest(
                connection,
                target,
                table,
                memory_root=source.memory_root,
                excluded_capture_id=excluded_capture_id,
            )
            rows[table] = {"count": count, "sha256": digest}
        sequences = _sequence_inventory(connection, target, tables)
        context = connection.execute(
            "SELECT store_uuid,store_identity FROM work_inbox_store_context WHERE singleton=1"
        ).fetchone()
        recovery = connection.execute(
            "SELECT installation_uuid,execution_state FROM work_recovery_context WHERE singleton=1"
        ).fetchone()
        if context is None or recovery is None:
            raise _reject()
        refs = _external_refs(connection, source)
        external = []
        for reference in refs:
            digest, size, _identity = _external_hash(reference)
            if (reference.expected_digest is not None and digest != reference.expected_digest) or (
                reference.expected_size is not None and size != reference.expected_size
            ):
                raise _reject()
            external.append(
                {
                    "role": reference.role,
                    "identifier": reference.identifier,
                    "digest": digest,
                    "size": size,
                    "relative_path": reference.relative_memory_path,
                }
            )
        migrations = [
            {
                "component": step.component,
                "scope": step.scope,
                "version": step.version,
                "checksum": step.checksum,
            }
            for history in histories
            for step in history
        ]
    body = {
        "schema_sha256": _digest_bytes(_json_bytes(catalog)),
        "catalog": catalog,
        "rows": rows,
        "sequences": sequences,
        "migrations": migrations,
        "external": external,
        "source_store_id": target.store_id,
        "work_store_uuid": context["store_uuid"],
        "work_store_identity": context["store_identity"],
        "installation_uuid": recovery["installation_uuid"],
        "execution_state": recovery["execution_state"],
        "endpoint_sha256": _endpoint_fingerprint(target),
    }
    body["fingerprint"] = _digest_bytes(
        _json_bytes({key: value for key, value in body.items() if key != "endpoint_sha256"})
    )
    body["profile_version"] = max(step.version for history in histories for step in history)
    return body


def _cut_inventory(inventory: dict[str, Any]) -> dict[str, object]:
    return {
        "profile_version": inventory["profile_version"],
        "fingerprint": inventory["fingerprint"],
    }


def _lease_document(lease: OfflineCutLease) -> dict[str, object]:
    return {
        "id": lease.id,
        "store_identity": lease.store_identity,
        "operator_principal_id": lease.operator_principal_id,
        "owner": lease.owner,
        "scope": lease.scope,
        "epoch": lease.epoch,
        "fence": lease.fence,
        "expires_at": lease.expires_at,
        "revision": lease.revision,
    }


def _write_manifest(directory: Path, manifest: dict[str, Any]) -> tuple[str, int]:
    payload = _json_bytes(manifest)
    path = directory / _MANIFEST
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return _digest_bytes(payload), len(payload)


class OfflineRecoveryCapture:
    """Capture a complete PostgreSQL main store while a durable Work cut is held."""

    def __init__(
        self, authority: WorkAuthority, lease: OfflineCutLease, runner: PostgresArchiveRunner
    ) -> None:
        if not isinstance(authority, WorkAuthority) or not isinstance(lease, OfflineCutLease):
            raise _reject()
        if not isinstance(runner, PostgresArchiveRunner):
            raise _reject()
        self._authority = authority
        self._lease = lease
        self._runner = runner

    def capture(self, source: RecoveryCaptureSource, destination: Path) -> RecoveryBundleReceipt:
        stage: Path | None = None
        published = False
        promoted = False
        phase = "before"
        destination_path: Path | None = None
        try:
            if not isinstance(source, RecoveryCaptureSource) or not isinstance(destination, Path):
                raise _reject()
            target = source.database
            if not self._authority.repository.same_store(WorkRepository(target)):
                raise _reject()
            destination = destination.absolute()
            destination_path = destination
            parent = destination.parent
            _private_directory(parent)
            if destination.exists() or destination.is_symlink():
                raise _reject()

            checked = self._authority.verify_offline_cut(self._lease, source_database=target)
            capture_id = uuid.uuid4().hex

            stage = Path(tempfile.mkdtemp(prefix=".postgres-recovery-", dir=parent))
            os.chmod(stage, 0o700)
            objects = stage / "objects"
            objects.mkdir(mode=0o700)
            dump = stage / "main.dump"
            external_rows: dict[str, _ObjectMetadata] = {}
            references: list[dict[str, object]] = []
            with connect(target, readonly=True) as snapshot_connection:
                snapshot_row = snapshot_connection.execute("SELECT pg_export_snapshot()").fetchone()
                if snapshot_row is None or not isinstance(snapshot_row[0], str):
                    raise _reject()
                snapshot_id = snapshot_row[0]
                inventory = _database_inventory(
                    target,
                    source,
                    excluded_capture_id=capture_id,
                    connection=snapshot_connection,
                )
                before_observation = self._authority.observe_offline_cut(
                    checked,
                    source_database=target,
                    capture_id=capture_id,
                    phase="before",
                    inventory=_cut_inventory(inventory),
                )
                self._runner.dump(target, dump, snapshot_id=snapshot_id)
                os.chmod(dump, 0o600)
                _private_file(dump)
                dump_hash, dump_size, _ = _file_hash(dump, scan_text=False)
                object_metadata: dict[str, _ObjectMetadata] = {
                    dump_hash: {
                        "digest": dump_hash,
                        "size": dump_size,
                        "roles": ["postgres-main"],
                    }
                }
                dump_target = objects / dump_hash
                os.replace(dump, dump_target)
                os.chmod(dump_target, 0o600)
                for ref in _external_refs(snapshot_connection, source):
                    references.append(_copy_object(ref, external_rows, objects))
            for digest, info in external_rows.items():
                if digest in object_metadata:
                    raise _reject()
                # External objects are stored by digest; dump role stays isolated.
                info["roles"] = sorted(info["roles"])
                object_metadata[digest] = info

            after = _database_inventory(target, source, excluded_capture_id=capture_id)
            if after != inventory:
                raise _reject()
            checked = self._authority.verify_offline_cut(checked, source_database=target)
            after_observation = self._authority.observe_offline_cut(
                checked,
                source_database=target,
                capture_id=capture_id,
                phase="after",
                inventory=_cut_inventory(after),
            )
            phase = "promote"

            manifest: dict[str, Any] = {
                "format": _FORMAT,
                "format_version": 3,
                "capture_id": capture_id,
                "source": {
                    "scope": "main",
                    "store_id": target.store_id,
                    "endpoint_sha256": inventory["endpoint_sha256"],
                    "work_store_uuid": inventory["work_store_uuid"],
                    "work_store_identity": inventory["work_store_identity"],
                    "installation_uuid": inventory["installation_uuid"],
                    "execution_state": inventory["execution_state"],
                },
                "cut": {
                    "lease": _lease_document(checked),
                    "before": before_observation,
                    "after": after_observation,
                },
                "inventory": inventory,
                "dump": {"digest": dump_hash, "size": dump_size},
                "objects": [
                    {"digest": dump_hash, "size": dump_size, "roles": ["postgres-main"]},
                    *[
                        {"digest": digest, "size": item["size"], "roles": item["roles"]}
                        for digest, item in sorted(external_rows.items())
                    ],
                ],
                "references": sorted(
                    references, key=lambda item: (item["role"], item["identifier"])
                ),
            }

            def promote(
                current_lease: OfflineCutLease, promote_observation: dict
            ) -> dict[str, object]:
                nonlocal promoted, stage
                assert stage is not None
                manifest["cut"]["lease"] = _lease_document(current_lease)
                manifest["cut"]["promote"] = promote_observation
                manifest_digest, manifest_size = _write_manifest(stage, manifest)
                self._verify_directory(stage, manifest_digest, manifest_size)
                os.replace(stage, destination)
                promoted = True
                stage = None
                _fsync_directory(parent)
                return {
                    "bundle_path": destination,
                    "manifest_digest": manifest_digest,
                    "manifest_size": manifest_size,
                }

            published_lease = self._authority.publish_offline_cut(
                checked,
                source_database=target,
                capture_id=capture_id,
                inventory=_cut_inventory(after),
                publish=promote,
            )
            published = True
            manifest_path = destination / _MANIFEST
            manifest_digest, manifest_size, _ = _file_hash(manifest_path, scan_text=False)
            return RecoveryBundleReceipt(
                destination,
                manifest_digest,
                manifest_size,
                target.identity,
                target.store_id,
                capture_id,
                published_lease.id,
                published_lease.revision,
            )
        except Exception:
            try:
                self._authority.record_offline_cut_rejection(self._lease, phase=phase)
            except Exception:
                pass
            raise _reject() from None
        finally:
            if stage is not None and not published and (stage.exists() or stage.is_symlink()):
                shutil.rmtree(stage)
            if promoted and not published and destination_path is not None:
                if destination_path.exists() and not destination_path.is_symlink():
                    shutil.rmtree(destination_path)

    @staticmethod
    def _verify_directory(bundle: Path, expected_digest: str, expected_size: int) -> dict[str, Any]:
        manifest_path = bundle / _MANIFEST
        _private_directory(bundle)
        _private_directory(bundle / "objects")
        info = _private_file(manifest_path)
        payload = manifest_path.read_bytes()
        if (len(payload), _digest_bytes(payload)) != (expected_size, expected_digest):
            raise _reject()
        try:
            manifest = json.loads(payload, object_pairs_hook=_strict_object)
        except (ValueError, TypeError, json.JSONDecodeError):
            raise _reject() from None
        if not isinstance(manifest, dict) or manifest.get("format") != _FORMAT:
            raise _reject()
        expected_files = {_MANIFEST, "objects"}
        objects = manifest.get("objects")
        references = manifest.get("references")
        if not isinstance(objects, list) or not isinstance(references, list) or not objects:
            raise _reject()
        object_digests: set[str] = set()
        for item in objects:
            if (
                not isinstance(item, dict)
                or set(item) != {"digest", "size", "roles"}
                or not _is_digest(item["digest"])
                or type(item["size"]) is not int
                or item["size"] < 0
                or not isinstance(item["roles"], list)
                or not item["roles"]
                or any(role not in _OBJECT_ROLES | {"postgres-main"} for role in item["roles"])
                or item["digest"] in object_digests
            ):
                raise _reject()
            object_digests.add(item["digest"])
            path = bundle / "objects" / item["digest"]
            _private_file(path)
            digest, size, _ = _file_hash(path, scan_text=False)
            if (digest, size) != (item["digest"], item["size"]):
                raise _reject()
        for reference in references:
            if (
                not isinstance(reference, dict)
                or set(reference) != {"role", "identifier", "digest", "size", "relative_path"}
                or reference["role"] not in _OBJECT_ROLES
                or not isinstance(reference["identifier"], str)
                or not reference["identifier"]
                or not _is_digest(reference["digest"])
                or type(reference["size"]) is not int
                or reference["size"] < 0
                or reference["digest"] not in object_digests
            ):
                raise _reject()
            relative = reference["relative_path"]
            if reference["role"] == "memory-content":
                if not isinstance(relative, str) or not _valid_relative_memory(relative):
                    raise _reject()
            elif relative is not None:
                raise _reject()
        dump = manifest.get("dump")
        if (
            not isinstance(dump, dict)
            or set(dump) != {"digest", "size"}
            or not _is_digest(dump["digest"])
            or type(dump["size"]) is not int
            or dump["digest"] not in object_digests
            or not any(
                item["digest"] == dump["digest"] and "postgres-main" in item["roles"]
                for item in objects
            )
        ):
            raise _reject()
        source = manifest.get("source")
        if (
            not isinstance(source, dict)
            or source.get("scope") != "main"
            or not isinstance(source.get("store_id"), str)
            or not _is_digest(source.get("endpoint_sha256"))
            or not isinstance(source.get("work_store_uuid"), str)
            or not isinstance(source.get("work_store_identity"), str)
            or not isinstance(source.get("installation_uuid"), str)
        ):
            raise _reject()
        _verify_cut_manifest(manifest)
        if set(os.listdir(bundle)) != expected_files:
            raise _reject()
        if set(os.listdir(bundle / "objects")) != object_digests:
            raise _reject()
        return manifest


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _verify_cut_manifest(manifest: dict[str, Any]) -> None:
    """Cross-check every durable cut phase against the captured inventory.

    A caller may recompute a bundle's unkeyed file digest after editing JSON.
    That must not make an internally inconsistent lease or a missing phase
    look like a published capture receipt.
    """
    inventory = manifest.get("inventory")
    source = manifest.get("source")
    cut = manifest.get("cut")
    capture_id = manifest.get("capture_id")
    if (
        not isinstance(inventory, dict)
        or not isinstance(source, dict)
        or not isinstance(cut, dict)
        or set(cut) != {"lease", "before", "after", "promote"}
        or not isinstance(capture_id, str)
        or len(capture_id) != 32
        or any(character not in "0123456789abcdef" for character in capture_id)
    ):
        raise _reject()
    expected_inventory = {
        "fingerprint": inventory.get("fingerprint"),
        "profile_version": inventory.get("profile_version"),
    }
    if (
        not _is_digest(expected_inventory["fingerprint"])
        or type(expected_inventory["profile_version"]) is not int
        or expected_inventory["profile_version"] < 1
        or source.get("store_id") != inventory.get("source_store_id")
        or source.get("endpoint_sha256") != inventory.get("endpoint_sha256")
        or source.get("work_store_uuid") != inventory.get("work_store_uuid")
        or source.get("work_store_identity") != inventory.get("work_store_identity")
        or source.get("installation_uuid") != inventory.get("installation_uuid")
        or source.get("execution_state") != inventory.get("execution_state")
        or inventory.get("schema_sha256") != _digest_bytes(_json_bytes(inventory.get("catalog")))
    ):
        raise _reject()
    lease = cut.get("lease")
    lease_fields = {
        "id",
        "store_identity",
        "operator_principal_id",
        "owner",
        "scope",
        "epoch",
        "fence",
        "expires_at",
        "revision",
    }
    if (
        not isinstance(lease, dict)
        or set(lease) != lease_fields
        or not isinstance(lease.get("id"), str)
        or len(lease["id"]) != 32
        or any(character not in "0123456789abcdef" for character in lease["id"])
        or not isinstance(lease.get("store_identity"), str)
        or lease["store_identity"] != inventory.get("work_store_identity")
        or not isinstance(lease.get("operator_principal_id"), str)
        or not lease["operator_principal_id"]
        or lease.get("owner") != "work"
        or lease.get("scope") != "registered-work-writers"
        or type(lease.get("epoch")) is not int
        or lease["epoch"] < 1
        or lease.get("fence") != lease.get("epoch")
        or type(lease.get("expires_at")) not in {int, float}
        or not math.isfinite(lease["expires_at"])
        or type(lease.get("revision")) is not int
        or lease["revision"] != 1
    ):
        raise _reject()
    expected_observed_lease = {
        key: value for key, value in lease.items() if key != "store_identity"
    }
    expected_observed_lease["store_identity"] = public_offline_store_identity(
        lease["store_identity"]
    )
    observed_times: list[float] = []
    for phase in ("before", "after", "promote"):
        observation = cut.get(phase)
        if (
            not isinstance(observation, dict)
            or set(observation) != {"lease", "writer_count", "inventory", "observed_at"}
            or observation.get("lease") != expected_observed_lease
            or observation.get("writer_count") != 0
            or observation.get("inventory") != expected_inventory
            or type(observation.get("observed_at")) not in {int, float}
            or not math.isfinite(observation["observed_at"])
        ):
            raise _reject()
        observed_times.append(float(observation["observed_at"]))
    if (
        not observed_times[0] <= observed_times[1] <= observed_times[2]
        or observed_times[-1] >= lease["expires_at"]
    ):
        raise _reject()


def _valid_relative_memory(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and bool(path.parts)
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _verify_durable_publication(
    target: DatabaseTarget,
    receipt: RecoveryBundleReceipt,
    manifest: dict[str, Any],
) -> None:
    """Bind the local bundle to the source store's durable publication ledger."""
    if (
        not isinstance(target, DatabaseTarget)
        or target.scope != "main"
        or target.identity != receipt.source_target_identity
        or target.store_id != receipt.source_store_id
    ):
        raise _reject()

    repository = WorkRepository(target)
    with repository.read_snapshot() as connection:
        stored = connection.execute(
            "SELECT * FROM work_offline_cuts WHERE id=%s", (receipt.lease_id,)
        ).fetchone()
        lease = manifest["cut"]["lease"]
        if (
            stored is None
            or stored["state"] != "published"
            or stored["store_identity"] != lease["store_identity"]
            or stored["operator_principal_id"] != lease["operator_principal_id"]
            or stored["owner"] != lease["owner"]
            or stored["scope"] != lease["scope"]
            or stored["epoch"] != lease["epoch"]
            or stored["fence"] != lease["fence"]
            or stored["expires_at"] != lease["expires_at"]
            or stored["revision"] != receipt.publication_revision
            or receipt.publication_revision != lease["revision"] + 1
            or stored["published_capture_id"] != receipt.capture_id
            or stored["published_manifest_digest"] != receipt.manifest_digest
            or stored["published_manifest_size"] != receipt.manifest_size
            or stored["published_path"] != str(receipt.bundle_path.resolve())
            or type(stored["published_at"]) not in {int, float}
            or not math.isfinite(stored["published_at"])
            or stored["published_at"] >= lease["expires_at"]
        ):
            raise _reject()

        observations = connection.execute(
            "SELECT phase,observation FROM work_offline_cut_observations "
            "WHERE lease_id=%s AND capture_id=%s ORDER BY phase",
            (receipt.lease_id, receipt.capture_id),
        ).fetchall()
        if {item["phase"] for item in observations} != {"after", "before", "promote"}:
            raise _reject()
        for item in observations:
            try:
                observed = json.loads(item["observation"], object_pairs_hook=_strict_object)
            except (TypeError, ValueError, json.JSONDecodeError):
                raise _reject() from None
            if not isinstance(observed, dict) or observed != manifest["cut"].get(item["phase"]):
                raise _reject()


def verify_recovery_bundle(
    receipt: RecoveryBundleReceipt,
    *,
    source_target: DatabaseTarget | None = None,
) -> RecoveryBundleReceipt:
    """Verify bundle integrity and its source store's durable publication receipt.

    The source target defaults to the configured private main target. Restore
    callers handling an archived source may pass that exact target explicitly;
    the supplied identity must still equal the captured source identity.
    """
    try:
        if not isinstance(receipt, RecoveryBundleReceipt):
            raise _reject()
        manifest_path = receipt.bundle_path / _MANIFEST
        digest, size, _ = _file_hash(manifest_path, scan_text=False)
        if (digest, size) != (receipt.manifest_digest, receipt.manifest_size):
            raise _reject()
        manifest = OfflineRecoveryCapture._verify_directory(
            receipt.bundle_path, receipt.manifest_digest, receipt.manifest_size
        )
        if (
            manifest.get("format_version") != 3
            or manifest.get("capture_id") != receipt.capture_id
            or manifest.get("source", {}).get("store_id") != receipt.source_store_id
            or manifest.get("cut", {}).get("lease", {}).get("id") != receipt.lease_id
            or receipt.publication_revision
            != manifest.get("cut", {}).get("lease", {}).get("revision", -1) + 1
            or len(receipt.source_target_identity) != 5
            or receipt.source_target_identity[4] != receipt.source_store_id
            or _digest_bytes(_json_bytes(list(receipt.source_target_identity[:4])))
            != manifest.get("source", {}).get("endpoint_sha256")
        ):
            raise _reject()
        selected_source = source_target or DatabaseTarget.from_settings("main")
        _verify_durable_publication(selected_source, receipt, manifest)
        return receipt
    except Exception:
        raise _reject() from None


def _restore_component_compatibility(manifest: dict[str, Any]) -> None:
    actual = [
        {
            "component": step.component,
            "scope": step.scope,
            "version": step.version,
            "checksum": step.checksum,
        }
        for history in _complete_histories("main")
        for step in history
    ]
    if actual != manifest.get("inventory", {}).get("migrations"):
        raise _reject()


def _preflight_restore_target(target: DatabaseTarget, source: dict[str, Any]) -> None:
    if (
        target.scope != "main"
        or target.store_id != source.get("store_id")
        or _endpoint_fingerprint(target) == source.get("endpoint_sha256")
    ):
        raise _reject()
    with connect(target.migration_target(), readonly=True) as connection:
        present = connection.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname=%s", (target.schema,)
        ).fetchone()
        if present is not None:
            raise _reject()


def _copy_restored_objects(
    bundle: Path, manifest: dict[str, Any], target: DatabaseTarget, home: Path
) -> tuple[dict[str, Path], dict[str, Path]]:
    home = home.absolute()
    _private_directory(home)
    repository = WorkRepository(target, filesystem_home=home)
    roots = {
        "result-content": repository.result_content_root,
        "worktree-evidence": repository.result_content_root,
        "delivery-content": repository.delivery_content_root,
        "executable-content": repository.executable_content_root,
        "memory-content": home / "memory",
    }
    used_roles = {reference["role"] for reference in manifest["references"]}
    finals = {roots[role] for role in used_roles}
    if any(path.exists() or path.is_symlink() for path in finals):
        raise _reject()
    if not finals:
        return roots, {}
    staged_root = Path(tempfile.mkdtemp(prefix=".recovery-objects-", dir=home))
    stage_paths: dict[str, Path] = {}
    try:
        for path in finals:
            stage_path = staged_root / hashlib.sha256(str(path).encode()).hexdigest()[:16]
            stage_path.mkdir(mode=0o700)
            stage_paths[str(path)] = stage_path
        for reference in manifest["references"]:
            digest = reference["digest"]
            object_path = bundle / "objects" / digest
            root = roots[reference["role"]]
            stage_root = stage_paths[str(root)]
            if reference["role"] == "memory-content":
                relative = PurePosixPath(reference["relative_path"])
                output = stage_root.joinpath(*relative.parts)
                output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                output = stage_root / digest
            if output.exists():
                if _file_hash(output, scan_text=False)[:2] != (digest, reference["size"]):
                    raise _reject()
            else:
                _copy_verified_file(object_path, output, digest, reference["size"])
        return roots, {str(path): stage_paths[str(path)] for path in finals}
    except Exception:
        shutil.rmtree(staged_root, ignore_errors=True)
        raise


def _copy_verified_file(source: Path, destination: Path, digest: str, size: int) -> None:
    _private_file(source)
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    output = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    observed = hashlib.sha256()
    count = 0
    try:
        with (
            os.fdopen(descriptor, "rb", closefd=False) as reader,
            os.fdopen(output, "wb", closefd=False) as writer,
        ):
            while block := reader.read(1024 * 1024):
                observed.update(block)
                count += len(block)
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
    finally:
        os.close(descriptor)
        os.close(output)
    if (observed.hexdigest(), count) != (digest, size):
        raise _reject()


def restore_recovery_bundle(
    receipt: RecoveryBundleReceipt,
    destination: DatabaseTarget,
    *,
    filesystem_home: Path,
    runner: PostgresArchiveRunner,
    source_target: DatabaseTarget | None = None,
) -> RecoveryRestoreReceipt:
    """Restore and verify an isolated main target, leaving activation to caller."""
    stage_dirs: dict[str, Path] | None = None
    promoted_dirs: list[Path] = []
    schema_created = False
    restore_succeeded = False
    try:
        verified = verify_recovery_bundle(receipt, source_target=source_target)
        if not isinstance(runner, PostgresArchiveRunner):
            raise _reject()
        manifest = OfflineRecoveryCapture._verify_directory(
            verified.bundle_path, verified.manifest_digest, verified.manifest_size
        )
        _restore_component_compatibility(manifest)
        _preflight_restore_target(destination, manifest["source"])
        roots, stage_dirs = _copy_restored_objects(
            verified.bundle_path, manifest, destination, filesystem_home
        )
        dump_path = verified.bundle_path / "objects" / manifest["dump"]["digest"]
        runner.restore(destination, dump_path)
        # Archive runners are required to restore atomically. Only after a
        # successful return do we own the schema and may remove it if later
        # validation fails. If the runner fails, a schema present at that point
        # may have appeared after the absence preflight and belongs to another
        # operation; never drop it based on owner identity alone.
        restore_succeeded = True
        with connect(destination.migration_target(), readonly=True) as connection:
            schema_created = (
                connection.execute(
                    "SELECT 1 FROM pg_namespace WHERE nspname=%s", (destination.schema,)
                ).fetchone()
                is not None
            )
        if not schema_created:
            raise _reject()
        bootstrap_scope(destination, seed_work=False)
        repository = WorkRepository(destination, filesystem_home=filesystem_home)
        repository.initialize()
        for final_path in set(roots[reference["role"]] for reference in manifest["references"]):
            staged = stage_dirs[str(final_path)]
            os.replace(staged, final_path)
            promoted_dirs.append(final_path)
            _fsync_directory(final_path.parent)
        staged_parent = next(iter(stage_dirs.values())).parent if stage_dirs else None
        if staged_parent is not None:
            shutil.rmtree(staged_parent, ignore_errors=True)
        stage_dirs = None

        if manifest["references"]:
            with connect(destination) as connection:
                with connection.transaction():
                    for reference in manifest["references"]:
                        if reference["role"] != "memory-content":
                            continue
                        new_path = roots["memory-content"] / PurePosixPath(
                            reference["relative_path"]
                        )
                        updated = connection.execute(
                            "UPDATE memory_metadata SET file_path=%s WHERE id=%s",
                            (str(new_path), reference["identifier"]),
                        )
                        if updated.rowcount != 1:
                            raise _reject()

        actual = _database_inventory(
            destination,
            RecoveryCaptureSource(
                destination,
                roots["result-content"],
                roots["delivery-content"],
                roots["executable-content"],
                roots["memory-content"],
            ),
            excluded_capture_id=manifest["capture_id"],
        )
        expected = manifest["inventory"]
        # Physical endpoint identity intentionally changes during restore; all
        # schema, row, sequence, logical-store and external-object evidence must
        # still match exactly.
        actual["endpoint_sha256"] = expected["endpoint_sha256"]
        if actual != expected:
            raise _reject()

        source_store_uuid = manifest["source"]["work_store_uuid"]
        installation_uuid = uuid.uuid4().hex
        restore_receipt = _digest_bytes(
            _json_bytes(
                {
                    "version": 3,
                    "bundle_digest": receipt.manifest_digest,
                    "target_identity": list(destination.identity),
                    "source_store_uuid": source_store_uuid,
                }
            )
        )
        bootstrap_work_store(destination)
        context = WorkRepository(
            destination, filesystem_home=filesystem_home
        ).block_imported_restore(
            source_store_uuid=uuid.UUID(source_store_uuid).hex,
            installation_uuid=installation_uuid,
            bundle_digest=receipt.manifest_digest,
            restore_receipt=restore_receipt,
        )
        if context.execution_state != "blocked_restore":
            raise _reject()
        return RecoveryRestoreReceipt(
            destination.identity,
            manifest["source"]["store_id"],
            receipt.manifest_digest,
            context.installation_uuid,
            context.execution_state,
        )
    except Exception:
        if restore_succeeded:
            _drop_created_schema(destination)
        for path in promoted_dirs:
            if path.exists() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
        raise _reject() from None
    finally:
        if stage_dirs:
            for stage in stage_dirs.values():
                shutil.rmtree(stage.parent, ignore_errors=True)


def _drop_created_schema(target: DatabaseTarget) -> None:
    try:
        with connect(target.migration_target()) as connection:
            with connection.transaction():
                owner = connection.execute(
                    "SELECT n.nspowner=r.oid FROM pg_namespace n JOIN pg_roles r ON r.rolname=current_user WHERE n.nspname=%s",
                    (target.schema,),
                ).fetchone()
                if owner is not None and owner[0]:
                    connection.execute(
                        sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(target.schema))
                    )
    except Exception:
        # Leave a private, inactive schema for operator inspection if cleanup itself fails.
        return
