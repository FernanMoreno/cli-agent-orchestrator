"""Private, fail-closed v1 capture of a closed SQLite recovery profile.

This module deliberately provides no restore or public transport surface.  A
server-owned cut verifier is installed when the capture service is constructed;
a digest is integrity evidence only and never grants capture authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypedDict, TypeGuard, cast

from cli_agent_orchestrator.clients.work_inbox_schema import ManagedInboxStoreIdentity
from cli_agent_orchestrator.clients.work_recovery_schema import RECOVERY_STATE_BLOCKED_RESTORE
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services.recovery_inventory import (
    WORK_SQLITE_PROFILE_VERSION,
    RecoveryInventoryError,
    WorkStoreInventory,
    inspect_offline_work_store,
    inspect_portable_work_store,
    inspect_work_store,
    verified_inbox_store_identity,
)
from cli_agent_orchestrator.services.secret_gate import scan_for_secrets
from cli_agent_orchestrator.services.work_authority import (
    OfflineCutLease,
    WorkAuthority,
    public_offline_store_identity,
)

_ERROR = "recovery bundle rejected"
_FORMAT = "recovery-bundle-v1"
_FORMAT_V2 = "recovery-bundle-v2"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_LEGACY_WORK_SQLITE_PROFILE_VERSION = 24
_SUPPORTED_BUNDLE_PROFILE_VERSIONS = frozenset((_LEGACY_WORK_SQLITE_PROFILE_VERSION, 25))
_SUPPORTED_V2_PROFILE_VERSIONS = frozenset((28, 29, 30, 31, 38, WORK_SQLITE_PROFILE_VERSION))
_MAX_EXECUTABLE_CONTENT_BYTES = 8 * 1024 * 1024


class RecoveryBundleError(RuntimeError):
    """Fixed, non-sensitive rejection for every capture or verification failure."""


@dataclass(frozen=True)
class RecoveryCaptureSource:
    """Explicit private storage roots for the one closed v24 capture profile."""

    database_path: Path
    immutable_result_root: Path | None = None
    delivery_content_root: Path | None = None
    executable_content_root: Path | None = None
    memory_root: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.database_path, Path) or any(
            root is not None and not isinstance(root, Path)
            for root in (
                self.immutable_result_root,
                self.delivery_content_root,
                self.executable_content_root,
                self.memory_root,
            )
        ):
            raise RecoveryBundleError(_ERROR)


@dataclass(frozen=True)
class RecoveryBundleReceipt:
    """Digest-bound locator returned only after the private directory is published."""

    bundle_path: Path
    manifest_digest: str
    manifest_size: int
    source_database: Path | None = None
    lease_id: str | None = None
    publication_revision: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.bundle_path, Path)
            or not _is_digest(self.manifest_digest)
            or type(self.manifest_size) is not int
            or self.manifest_size < 0
            or (
                (
                    self.source_database is None
                    or self.lease_id is None
                    or self.publication_revision is None
                )
                and not (
                    self.source_database is None
                    and self.lease_id is None
                    and self.publication_revision is None
                )
            )
            or (
                self.source_database is not None
                and (
                    not isinstance(self.source_database, Path)
                    or not self.source_database.is_absolute()
                    or not _is_lease_id(self.lease_id)
                    or type(self.publication_revision) is not int
                    or self.publication_revision <= 0
                )
            )
        ):
            raise RecoveryBundleError(_ERROR)


@dataclass(frozen=True)
class _ExternalObject:
    digest: str | None
    size: int | None
    root: Path
    relative_path: Path
    role: str
    identifier: str


def _is_digest(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _is_lease_id(value: object) -> TypeGuard[str]:
    return (
        isinstance(value, str) and len(value) == 32 and all(c in "0123456789abcdef" for c in value)
    )


def _reject() -> RecoveryBundleError:
    return RecoveryBundleError(_ERROR)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False
    ).encode("utf-8")


def _inventory_evidence(inventory: WorkStoreInventory) -> dict[str, object]:
    """Stable, non-secret observation of the closed inventory used for each phase."""
    return {
        "profile_version": inventory.profile_version,
        "fingerprint": hashlib.sha256(_canonical_json(asdict(inventory))).hexdigest(),
    }


def _lease_evidence(lease: OfflineCutLease) -> dict[str, object]:
    return {
        "id": lease.id,
        "store_identity": public_offline_store_identity(lease.store_identity),
        "operator_principal_id": lease.operator_principal_id,
        "owner": lease.owner,
        "scope": lease.scope,
        "epoch": lease.epoch,
        "fence": lease.fence,
        "expires_at": lease.expires_at,
        "revision": lease.revision,
    }


def _phase_evidence(lease: OfflineCutLease, inventory: WorkStoreInventory) -> dict[str, object]:
    return {
        "lease": _lease_evidence(lease),
        "writer_count": 0,
        "inventory": _inventory_evidence(inventory),
    }


def _duplicate_key(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _private_directory(path: Path) -> None:
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise _reject()


def _private_regular_file(path: Path) -> os.stat_result:
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise _reject()
    return info


def _stable_file_identity(info: os.stat_result) -> tuple[int, ...]:
    """Detect replacement or mutation of a referenced private source during capture."""
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _regular_file_without_links(path: Path) -> os.stat_result:
    """A capture source need not be private merely to be copied read-only."""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise _reject()
    return info


def _directory_without_links(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise _reject()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _open_readonly_database(path: Path) -> sqlite3.Connection:
    _regular_file_without_links(path)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _verified_source_inventory(path: Path) -> tuple[WorkStoreInventory, ManagedInboxStoreIdentity]:
    """Obtain inventory and the inbox pair only from a strictly verified source."""
    connection = _open_readonly_database(path)
    try:
        inventory = inspect_work_store(connection)
        return inventory, verified_inbox_store_identity(connection)
    finally:
        connection.close()


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _scan_utf8_bytes(value: bytes) -> None:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        raise _reject() from None
    if any(character not in "\n\r\t" and not character.isprintable() for character in text):
        raise _reject()
    if scan_for_secrets(text) is not None:
        raise _reject()


def _scan_exportable_database(
    connection: sqlite3.Connection, inventory: WorkStoreInventory
) -> None:
    """Inspect every logical SQLite cell without parsing or rewriting opaque payloads."""
    for table in inventory.tables:
        cursor = connection.execute(f"SELECT * FROM {_quote(table)}")
        columns = tuple(column[0] for column in cursor.description)
        for row in cursor:
            for column, value in zip(columns, row):
                if isinstance(value, str):
                    if scan_for_secrets(value) is not None:
                        raise _reject()
                elif isinstance(value, bytes):
                    if (table, column) != ("work_delegation_snapshots", "content"):
                        raise _reject()
                    _scan_utf8_bytes(value)
                elif value is not None and type(value) not in {int, float}:
                    raise _reject()
    for reference in inventory.references:
        if reference.resolution == "requires_future_profile":
            present = connection.execute(
                f"SELECT 1 FROM {_quote(reference.source_table)} LIMIT 1"
            ).fetchone()
            if present is not None:
                raise _reject()


def _declared_external_objects(
    connection: sqlite3.Connection, source: RecoveryCaptureSource
) -> tuple[_ExternalObject, ...]:
    declared: list[_ExternalObject] = []
    queries = (
        (
            "result-content",
            "SELECT id,content_hash,immutable_location,byte_length FROM work_results",
            source.immutable_result_root,
        ),
        (
            "worktree-evidence",
            "SELECT id,content_hash,immutable_location,byte_length FROM work_worktree_evidence",
            source.immutable_result_root,
        ),
        (
            "delivery-content",
            "SELECT attempt_id || ':' || generation,content_hash,content_ref,byte_length "
            "FROM work_delivery_content_refs",
            source.delivery_content_root,
        ),
        (
            "executable-content",
            "SELECT content_hash,content_hash,immutable_location,byte_length "
            "FROM work_executable_contents",
            source.executable_content_root,
        ),
    )
    for role, query, root in queries:
        for identifier, digest, location, size in connection.execute(query):
            if (
                not isinstance(identifier, str)
                or not identifier
                or not _is_digest(digest)
                or location != digest
                or type(size) is not int
                or size < 0
                or role == "executable-content"
                and not 1 <= size <= _MAX_EXECUTABLE_CONTENT_BYTES
                or root is None
            ):
                raise _reject()
            declared.append(
                _ExternalObject(
                    digest=digest,
                    size=size,
                    root=root,
                    relative_path=Path(digest),
                    role=role,
                    identifier=identifier,
                )
            )
    memory_rows = connection.execute("SELECT id,file_path FROM memory_metadata").fetchall()
    if memory_rows and source.memory_root is None:
        raise _reject()
    if source.memory_root is not None:
        memory_root = source.memory_root.absolute()
        _directory_without_links(memory_root)
        for identifier, stored_path in memory_rows:
            if (
                not isinstance(identifier, str)
                or not identifier
                or not isinstance(stored_path, str)
            ):
                raise _reject()
            try:
                relative_path = Path(stored_path).absolute().relative_to(memory_root)
            except ValueError:
                raise _reject() from None
            if not relative_path.parts:
                raise _reject()
            declared.append(
                _ExternalObject(
                    digest=None,
                    size=None,
                    root=memory_root,
                    relative_path=relative_path,
                    role="memory-content",
                    identifier=identifier,
                )
            )
    return tuple(sorted(declared, key=lambda item: (item.role, item.identifier)))


def _open_regular_file_beneath(root: Path, relative_path: Path) -> int:
    """Open one regular file without following any component below ``root``."""
    if (
        relative_path.is_absolute()
        or not relative_path.parts
        or any(component in {".", ".."} for component in relative_path.parts)
    ):
        raise _reject()
    directory_descriptor = os.open(
        root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        for component in relative_path.parts[:-1]:
            child_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory_descriptor,
            )
            os.close(directory_descriptor)
            directory_descriptor = child_descriptor
        source_descriptor = os.open(
            relative_path.parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=directory_descriptor,
        )
        try:
            if not stat.S_ISREG(os.fstat(source_descriptor).st_mode):
                raise _reject()
            return source_descriptor
        except BaseException:
            os.close(source_descriptor)
            raise
    finally:
        os.close(directory_descriptor)


def _open_private_executable_root(root: Path) -> int:
    """Anchor every root component without following a symlink in an ancestor."""
    absolute = root.absolute()
    if not absolute.is_absolute() or ".." in absolute.parts:
        raise _reject()
    descriptor = os.open(
        absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        for component in absolute.parts[1:]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.geteuid()
        ):
            raise _reject()
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_private_executable_leaf(root_descriptor: int, digest: str) -> tuple[int, os.stat_result]:
    """Open a digest leaf under an anchored root and validate that exact inode."""
    if not _is_digest(digest):
        raise _reject()
    descriptor = os.open(
        digest,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
        dir_fd=root_descriptor,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or not 1 <= info.st_size <= _MAX_EXECUTABLE_CONTENT_BYTES
        ):
            raise _reject()
        return descriptor, info
    except BaseException:
        os.close(descriptor)
        raise


def _digest_open_executable(descriptor: int) -> tuple[str, int]:
    """Rehash a securely opened leaf within the same fixed byte bound."""
    before = os.fstat(descriptor)
    digest = hashlib.sha256()
    size = 0
    while block := os.read(descriptor, 1024 * 1024):
        size += len(block)
        if size > _MAX_EXECUTABLE_CONTENT_BYTES:
            raise _reject()
        digest.update(block)
    if _stable_file_identity(os.fstat(descriptor)) != _stable_file_identity(before):
        raise _reject()
    return digest.hexdigest(), size


def _copy_open_file_as_object(
    source_descriptor: int,
    objects: Path,
    *,
    expected_digest: str | None = None,
    expected_size: int | None = None,
    scan_utf8: bool = False,
    max_bytes: int | None = None,
) -> tuple[str, int]:
    """Copy an already-open regular file before it can be published."""
    temporary = objects / f".capture-{uuid.uuid4().hex}"
    digest = hashlib.sha256()
    size = 0
    try:
        destination_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        try:
            source_info = os.fstat(source_descriptor)
            if not stat.S_ISREG(source_info.st_mode) or (
                max_bytes is not None and source_info.st_size > max_bytes
            ):
                raise _reject()
            with (
                os.fdopen(source_descriptor, "rb", closefd=False) as input_file,
                os.fdopen(destination_descriptor, "wb", closefd=False) as output_file,
            ):
                if scan_utf8:
                    block = input_file.read()
                    _scan_utf8_bytes(block)
                    digest.update(block)
                    size += len(block)
                    output_file.write(block)
                else:
                    while block := input_file.read(1024 * 1024):
                        size += len(block)
                        if max_bytes is not None and size > max_bytes:
                            raise _reject()
                        digest.update(block)
                        output_file.write(block)
                output_file.flush()
                os.fsync(output_file.fileno())
        finally:
            os.close(destination_descriptor)
        digest_text = digest.hexdigest()
        if (expected_digest is not None and digest_text != expected_digest) or (
            expected_size is not None and size != expected_size
        ):
            raise _reject()
        target = objects / digest_text
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            existing_digest, existing_size = _file_digest(target)
            if (existing_digest, existing_size) != (digest_text, size):
                raise _reject()
        os.unlink(temporary)
        _fsync_directory(objects)
        return digest_text, size
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def _copy_file_as_object(
    source: Path,
    objects: Path,
    *,
    expected_digest: str | None = None,
    expected_size: int | None = None,
    scan_utf8: bool = False,
    require_private_source: bool = True,
) -> tuple[str, int]:
    """Copy one regular file into its digest name, verifying before it can be published."""
    if require_private_source:
        _private_regular_file(source)
    else:
        _regular_file_without_links(source)
    source_descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        return _copy_open_file_as_object(
            source_descriptor,
            objects,
            expected_digest=expected_digest,
            expected_size=expected_size,
            scan_utf8=scan_utf8,
        )
    finally:
        os.close(source_descriptor)


def _copy_executable_file_as_object(
    root: Path, digest: str, expected_size: int, objects: Path
) -> tuple[str, int, tuple[int, int], tuple[int, ...]]:
    root_descriptor = _open_private_executable_root(root)
    try:
        root_info = os.fstat(root_descriptor)
        source_descriptor, source_info = _open_private_executable_leaf(root_descriptor, digest)
        try:
            copied_digest, size = _copy_open_file_as_object(
                source_descriptor,
                objects,
                expected_digest=digest,
                expected_size=expected_size,
                max_bytes=_MAX_EXECUTABLE_CONTENT_BYTES,
            )
            source_identity = _stable_file_identity(source_info)
            if _stable_file_identity(os.fstat(source_descriptor)) != source_identity:
                raise _reject()
            return (
                copied_digest,
                size,
                (root_info.st_dev, root_info.st_ino),
                source_identity,
            )
        finally:
            os.close(source_descriptor)
    finally:
        os.close(root_descriptor)


def _verify_executable_source(
    root: Path,
    digest: str,
    size: int,
    root_identity: tuple[int, int],
    source_identity: tuple[int, ...],
) -> None:
    root_descriptor = _open_private_executable_root(root)
    try:
        root_info = os.fstat(root_descriptor)
        if (root_info.st_dev, root_info.st_ino) != root_identity:
            raise _reject()
        source_descriptor, source_info = _open_private_executable_leaf(root_descriptor, digest)
        try:
            if _stable_file_identity(source_info) != source_identity:
                raise _reject()
            if _digest_open_executable(source_descriptor) != (digest, size):
                raise _reject()
        finally:
            os.close(source_descriptor)
    finally:
        os.close(root_descriptor)


def _copy_memory_file_as_object(root: Path, relative_path: Path, objects: Path) -> tuple[str, int]:
    """Copy memory content through a descriptor confined beneath its physical root."""
    source_descriptor = _open_regular_file_beneath(root, relative_path)
    try:
        return _copy_open_file_as_object(source_descriptor, objects, scan_utf8=True)
    finally:
        os.close(source_descriptor)


def _file_digest(path: Path) -> tuple[str, int]:
    _private_regular_file(path)
    digest = hashlib.sha256()
    size = 0
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise _reject()
        with os.fdopen(descriptor, "rb", closefd=False) as input_file:
            while block := input_file.read(1024 * 1024):
                digest.update(block)
                size += len(block)
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def _compact_staging_database(copied_database: Path, stage: Path) -> Path:
    """Remove free SQLite pages in private staging before inspection and publication."""
    compacted_database = stage / ".sqlite-compact"
    with (
        closing(sqlite3.connect(copied_database)) as _owned_connection,
        _owned_connection as connection,
    ):
        connection.execute("VACUUM INTO ?", (str(compacted_database),))
    os.chmod(compacted_database, 0o600)
    return compacted_database


def _write_manifest(
    stage: Path,
    objects: list[dict[str, object]],
    references: list[dict[str, object]],
    cut_evidence: dict[str, object] | None = None,
    profile_version: int = WORK_SQLITE_PROFILE_VERSION,
) -> tuple[str, int]:
    manifest = {
        "format": _FORMAT_V2 if cut_evidence is not None else _FORMAT,
        "objects": objects,
        "profile_version": profile_version,
        "references": references,
    }
    if cut_evidence is not None:
        manifest["cut_evidence"] = cut_evidence
    encoded = _canonical_json(manifest)
    path = stage / "manifest.json"
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(descriptor)
    _fsync_directory(stage)
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


class _BundleObject(TypedDict):
    digest: str
    path: str
    roles: list[str]
    size: int


class _BundleReference(TypedDict):
    digest: str
    identifier: str
    path: str
    role: str
    size: int
    version: object


class _RecoveryManifest(TypedDict, total=False):
    format: str
    profile_version: int
    objects: list[_BundleObject]
    references: list[_BundleReference]
    cut_evidence: dict[str, object]


def _is_bundle_object(value: object) -> TypeGuard[_BundleObject]:
    return (
        isinstance(value, dict)
        and set(value) == {"digest", "path", "roles", "size"}
        and isinstance(value["digest"], str)
        and isinstance(value["path"], str)
        and type(value["size"]) is int
        and isinstance(value["roles"], list)
        and all(isinstance(role, str) for role in value["roles"])
    )


def _is_bundle_reference(value: object) -> TypeGuard[_BundleReference]:
    return (
        isinstance(value, dict)
        and set(value) == {"digest", "identifier", "path", "role", "size", "version"}
        and all(isinstance(value[field], str) for field in ("digest", "identifier", "path", "role"))
        and type(value["size"]) is int
    )


def _read_manifest(path: Path, receipt: RecoveryBundleReceipt) -> _RecoveryManifest:
    digest, size = _file_digest(path)
    if (digest, size) != (receipt.manifest_digest, receipt.manifest_size):
        raise _reject()
    raw = path.read_bytes()
    try:
        manifest = json.loads(raw, object_pairs_hook=_duplicate_key, parse_constant=lambda _: None)
    except (TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise _reject() from None
    if not isinstance(manifest, dict) or _canonical_json(manifest) != raw:
        raise _reject()
    if manifest.get("format") == _FORMAT:
        expected_keys = {"format", "objects", "profile_version", "references"}
        valid_profile = manifest.get("profile_version") in _SUPPORTED_BUNDLE_PROFILE_VERSIONS
    elif manifest.get("format") == _FORMAT_V2:
        expected_keys = {"format", "objects", "profile_version", "references", "cut_evidence"}
        valid_profile = manifest.get("profile_version") in _SUPPORTED_V2_PROFILE_VERSIONS
    else:
        raise _reject()
    if (
        set(manifest) != expected_keys
        or type(manifest["profile_version"]) is not int
        or not valid_profile
    ):
        raise _reject()
    if manifest["format"] == _FORMAT_V2:
        evidence = manifest["cut_evidence"]
        if (
            not isinstance(evidence, dict)
            or set(evidence)
            != {
                "coverage",
                "capture_id",
                "epoch",
                "expires_at",
                "fence",
                "lease_id",
                "operator_principal_id",
                "owner",
                "phases",
                "revision",
                "scope",
                "store_identity",
                "version",
            }
            or evidence["version"] != 2
            or evidence["scope"] != "registered-work-writers"
            or evidence["owner"] != "work"
            or not _is_lease_id(evidence["lease_id"])
            or not _is_lease_id(evidence["capture_id"])
            or not isinstance(evidence["operator_principal_id"], str)
            or not evidence["operator_principal_id"]
            or not isinstance(evidence["store_identity"], str)
            or not evidence["store_identity"]
            or type(evidence["epoch"]) is not int
            or type(evidence["fence"]) is not int
            or type(evidence["revision"]) is not int
            or evidence["epoch"] <= 0
            or evidence["fence"] <= 0
            or evidence["revision"] <= 0
            or not isinstance(evidence["expires_at"], (int, float))
            or isinstance(evidence["expires_at"], bool)
            or not isinstance(evidence["phases"], dict)
            or set(evidence["phases"])
            != {
                "before",
                "after",
                "promote",
            }
            or not isinstance(evidence["coverage"], list)
        ):
            raise _reject()
    objects = manifest["objects"]
    if not isinstance(objects, list) or not objects:
        raise _reject()
    if not isinstance(manifest["references"], list):
        raise _reject()
    # Decode the nested shape before the verifier checks its semantic constraints.
    if not all(_is_bundle_object(item) for item in objects) or not all(
        _is_bundle_reference(item) for item in manifest["references"]
    ):
        raise _reject()
    return cast(_RecoveryManifest, manifest)


def _verify_v2_phase_evidence(
    evidence: dict[str, object],
    object_roles: dict[str, tuple[str, ...]],
    sqlite_path: Path,
    *,
    profile_version: int,
) -> None:
    expected_lease = {
        "id": evidence["lease_id"],
        "store_identity": evidence["store_identity"],
        "operator_principal_id": evidence["operator_principal_id"],
        "owner": evidence["owner"],
        "scope": evidence["scope"],
        "epoch": evidence["epoch"],
        "fence": evidence["fence"],
        "expires_at": evidence["expires_at"],
        "revision": evidence["revision"],
    }
    phases = evidence["phases"]
    assert isinstance(phases, dict)  # _read_manifest validated the phase mapping.
    observations: list[dict[str, object]] = []
    for name in ("before", "after", "promote"):
        phase = phases[name]
        if not isinstance(phase, dict) or set(phase) != {
            "inventory",
            "lease",
            "observed_at",
            "writer_count",
        }:
            raise _reject()
        if (
            phase["lease"] != expected_lease
            or phase["writer_count"] != 0
            or not isinstance(phase["observed_at"], (int, float))
            or isinstance(phase["observed_at"], bool)
        ):
            raise _reject()
        inventory = phase["inventory"]
        if (
            not isinstance(inventory, dict)
            or set(inventory) != {"fingerprint", "profile_version"}
            or inventory["profile_version"] != profile_version
            or not _is_digest(inventory["fingerprint"])
        ):
            raise _reject()
        observations.append(inventory)
    if observations[0] != observations[1] or observations[1] != observations[2]:
        raise _reject()
    with closing(_open_readonly_database(sqlite_path)) as _owned_copied, _owned_copied as copied:
        if (
            _inventory_evidence(inspect_offline_work_store(copied, profile_version=profile_version))
            != observations[1]
        ):
            raise _reject()
    expected_coverage = [
        {"digest": digest, "classification": "integrity-only", "roles": list(roles)}
        for digest, roles in sorted(object_roles.items())
    ]
    if evidence["coverage"] != expected_coverage:
        raise _reject()


def _verify_v2_publication(receipt: RecoveryBundleReceipt, evidence: dict[str, object]) -> None:
    if receipt.source_database is None or receipt.lease_id != evidence["lease_id"]:
        raise _reject()
    try:
        source = receipt.source_database.resolve()
        with (
            closing(
                sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, isolation_level=None)
            ) as _owned_connection,
            _owned_connection as connection,
        ):
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute(
                "SELECT * FROM work_offline_cuts WHERE id=?", (receipt.lease_id,)
            ).fetchone()
            observations = connection.execute(
                "SELECT phase,observation FROM work_offline_cut_observations "
                "WHERE lease_id=? AND capture_id=? ORDER BY phase",
                (receipt.lease_id, evidence["capture_id"]),
            ).fetchall()
    except (OSError, sqlite3.Error):
        raise _reject() from None
    if (
        row is None
        or row["state"] != "published"
        or row["revision"] != receipt.publication_revision
        or any(
            row[key] != evidence[key]
            for key in (
                "operator_principal_id",
                "owner",
                "scope",
                "epoch",
                "fence",
            )
        )
        or public_offline_store_identity(row["store_identity"]) != evidence["store_identity"]
        or row["published_path"] != str(receipt.bundle_path.resolve())
        or row["published_capture_id"] != evidence["capture_id"]
        or row["published_manifest_digest"] != receipt.manifest_digest
        or row["published_manifest_size"] != receipt.manifest_size
    ):
        raise _reject()
    try:
        observed = {row["phase"]: json.loads(row["observation"]) for row in observations}
    except (TypeError, ValueError, json.JSONDecodeError):
        raise _reject() from None
    if observed != evidence["phases"]:
        raise _reject()


def _verify_recovery_bundle(
    receipt: RecoveryBundleReceipt, *, require_publication: bool
) -> RecoveryBundleReceipt:
    """Verify byte integrity and, for v2, durable server-owned publication eligibility."""
    try:
        if not isinstance(receipt, RecoveryBundleReceipt):
            raise _reject()
        bundle = receipt.bundle_path
        _private_directory(bundle)
        entries = {entry.name: entry for entry in bundle.iterdir()}
        if set(entries) != {"manifest.json", "objects"}:
            raise _reject()
        _private_regular_file(entries["manifest.json"])
        _private_directory(entries["objects"])
        manifest = _read_manifest(entries["manifest.json"], receipt)
        objects = manifest["objects"]
        previous_digest = ""
        expected_names: set[str] = set()
        object_roles: dict[str, tuple[str, ...]] = {}
        for object_ in objects:
            if not isinstance(object_, dict) or set(object_) != {"digest", "path", "roles", "size"}:
                raise _reject()
            digest = object_["digest"]
            size = object_["size"]
            roles = object_["roles"]
            if (
                not _is_digest(digest)
                or type(size) is not int
                or size < 0
                or object_["path"] != f"objects/{digest}"
                or digest <= previous_digest
                or not isinstance(roles, list)
                or not roles
                or tuple(roles) != tuple(sorted(set(roles)))
                or any(
                    not isinstance(role, str) or re.fullmatch(r"[a-z][a-z0-9-]*", role) is None
                    for role in roles
                )
            ):
                raise _reject()
            previous_digest = digest
            expected_names.add(digest)
            object_roles[digest] = tuple(roles)
            if _file_digest(entries["objects"] / digest) != (digest, size):
                raise _reject()
        if {entry.name for entry in entries["objects"].iterdir()} != expected_names:
            raise _reject()
        sqlite_role = f"sqlite-v{manifest['profile_version']}"
        if sum(sqlite_role in roles for roles in object_roles.values()) != 1 or any(
            role.startswith("sqlite-v") and role != sqlite_role
            for roles in object_roles.values()
            for role in roles
        ):
            raise _reject()
        sqlite_digest = next(
            digest for digest, roles in object_roles.items() if sqlite_role in roles
        )
        if manifest["format"] == _FORMAT:
            with (
                closing(
                    _open_readonly_database(entries["objects"] / sqlite_digest)
                ) as _owned_historical,
                _owned_historical as historical,
            ):
                if (
                    historical.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_offline_cuts'"
                    ).fetchone()
                    is not None
                ):
                    raise _reject()
        references = manifest["references"]
        previous_reference: tuple[str, str, str] | None = None
        for reference in references:
            if not isinstance(reference, dict) or set(reference) != {
                "digest",
                "identifier",
                "path",
                "role",
                "size",
                "version",
            }:
                raise _reject()
            digest = reference["digest"]
            size = reference["size"]
            role = reference["role"]
            identifier = reference["identifier"]
            current_reference = (role, identifier, digest)
            if (
                not _is_digest(digest)
                or type(size) is not int
                or size < 0
                or not isinstance(role, str)
                or role not in object_roles.get(digest, ())
                or not isinstance(identifier, str)
                or not identifier
                or reference["path"] != f"objects/{digest}"
                or reference["version"] != 1
                or previous_reference is not None
                and current_reference <= previous_reference
                or _file_digest(entries["objects"] / digest) != (digest, size)
            ):
                raise _reject()
            previous_reference = (role, identifier, digest)
        if manifest["format"] == _FORMAT_V2:
            _verify_v2_phase_evidence(
                manifest["cut_evidence"],
                object_roles,
                entries["objects"] / sqlite_digest,
                profile_version=manifest["profile_version"],
            )
            if require_publication:
                _verify_v2_publication(receipt, manifest["cut_evidence"])
        return receipt
    except (OSError, TypeError, ValueError, RecoveryBundleError):
        raise _reject() from None


def verify_recovery_bundle(receipt: RecoveryBundleReceipt) -> RecoveryBundleReceipt:
    """Verify a receipt-bound private bundle; v2 requires a durable publication record."""
    return _verify_recovery_bundle(receipt, require_publication=True)


def _restore_manifest(receipt: RecoveryBundleReceipt) -> tuple[_RecoveryManifest, Path, str, int]:
    """Return only the currently supported, receipt-verified SQLite bundle object."""
    _verify_recovery_bundle(receipt, require_publication=True)
    manifest = _read_manifest(receipt.bundle_path / "manifest.json", receipt)
    if (
        manifest["format"] != _FORMAT_V2
        or manifest["profile_version"] != WORK_SQLITE_PROFILE_VERSION
    ):
        raise _reject()
    sqlite_role = f"sqlite-v{WORK_SQLITE_PROFILE_VERSION}"
    sqlite_objects = [object_ for object_ in manifest["objects"] if sqlite_role in object_["roles"]]
    if len(sqlite_objects) != 1:
        raise _reject()
    sqlite_object = sqlite_objects[0]
    if sqlite_object["roles"] != [sqlite_role]:
        raise _reject()
    # The receipt verifier already checked every object field before restoration.
    object_path, object_digest, object_size = (
        sqlite_object["path"],
        sqlite_object["digest"],
        sqlite_object["size"],
    )
    assert isinstance(object_path, str)
    assert isinstance(object_digest, str)
    assert isinstance(object_size, int)
    return manifest, receipt.bundle_path / object_path, object_digest, object_size


def _restore_destination(
    receipt: RecoveryBundleReceipt, destination: Path, sqlite_path: Path
) -> Path:
    """Accept one private, new file without ever opening configuration-owned storage."""
    if not isinstance(destination, Path) or receipt.source_database is None:
        raise _reject()
    try:
        from cli_agent_orchestrator import constants

        configured_database = Path(constants.DATABASE_FILE).resolve(strict=False)
    except (ImportError, OSError, TypeError, ValueError):
        raise _reject() from None
    destination = destination.absolute()
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        raise _reject()
    parent = destination.parent
    _private_directory(parent)
    resolved_destination = destination.resolve(strict=False)
    for protected in (
        receipt.bundle_path,
        receipt.source_database,
        sqlite_path,
        configured_database,
    ):
        resolved_protected = protected.resolve()
        if resolved_destination == resolved_protected:
            raise _reject()
        try:
            resolved_destination.relative_to(resolved_protected)
        except ValueError:
            continue
        raise _reject()
    return destination


def _restore_reference_closure(
    connection: sqlite3.Connection, manifest: _RecoveryManifest, inventory: WorkStoreInventory
) -> None:
    """Prove that every portable external object is named by the copied SQLite rows."""
    _scan_exportable_database(connection, inventory)
    expected: Counter[tuple[str, str, str, int]] = Counter()
    expected_memory: Counter[tuple[str, str]] = Counter()

    for role, query in (
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
            "SELECT attempt_id || ':' || generation,content_hash,content_ref,byte_length "
            "FROM work_delivery_content_refs",
        ),
        (
            "executable-content",
            "SELECT content_hash,content_hash,immutable_location,byte_length "
            "FROM work_executable_contents",
        ),
    ):
        for identifier, digest, location, size in connection.execute(query):
            entry = (role, identifier, digest, size)
            if (
                not isinstance(identifier, str)
                or not identifier
                or not _is_digest(digest)
                or location != digest
                or type(size) is not int
                or size < 0
                or role == "executable-content"
                and not 1 <= size <= _MAX_EXECUTABLE_CONTENT_BYTES
                or expected[entry]
            ):
                raise _reject()
            expected[entry] += 1

    for identifier, stored_path in connection.execute("SELECT id,file_path FROM memory_metadata"):
        memory_entry = ("memory-content", identifier)
        if (
            not isinstance(identifier, str)
            or not identifier
            or not isinstance(stored_path, str)
            or not stored_path
            or expected_memory[memory_entry]
        ):
            raise _reject()
        expected_memory[memory_entry] += 1

    actual: Counter[tuple[str, str, str, int]] = Counter()
    actual_memory: Counter[tuple[str, str]] = Counter()
    reference_roles: set[tuple[str, str]] = set()
    used_object_roles: set[tuple[str, str]] = set()
    for reference in manifest["references"]:
        role = reference["role"]
        identifier = reference["identifier"]
        digest = reference["digest"]
        size = reference["size"]
        address = (role, identifier)
        if address in reference_roles:
            raise _reject()
        reference_roles.add(address)
        if role == "memory-content":
            actual_memory[address] += 1
        elif role in {
            "result-content",
            "worktree-evidence",
            "delivery-content",
            "executable-content",
        }:
            entry = (role, identifier, digest, size)
            if actual[entry]:
                raise _reject()
            actual[entry] += 1
        else:
            raise _reject()
        used_object_roles.add((digest, role))
    if actual != expected or actual_memory != expected_memory:
        raise _reject()

    sqlite_role = f"sqlite-v{WORK_SQLITE_PROFILE_VERSION}"
    for object_ in manifest["objects"]:
        digest = object_["digest"]
        roles = object_["roles"]
        for role in roles:
            if role != sqlite_role and (digest, role) not in used_object_roles:
                raise _reject()


def _inspect_restore_sqlite(
    sqlite_path: Path, source_identity: ManagedInboxStoreIdentity, manifest: _RecoveryManifest
) -> None:
    with (
        closing(_open_readonly_database(sqlite_path)) as _owned_connection,
        _owned_connection as connection,
    ):
        inventory = inspect_offline_work_store(connection)
        portable_inventory = inspect_portable_work_store(
            connection, expected_source_identity=source_identity
        )
        if inventory != portable_inventory:
            raise _reject()
        _restore_reference_closure(connection, manifest, portable_inventory)


def _copy_restore_sqlite(source: Path, destination: Path, digest: str, size: int) -> None:
    """Copy the descriptor actually verified, rather than trusting a checked pathname."""
    _private_regular_file(source)
    source_descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    destination_descriptor = None
    try:
        if not stat.S_ISREG(os.fstat(source_descriptor).st_mode):
            raise _reject()
        destination_descriptor = os.open(
            destination, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        observed = hashlib.sha256()
        observed_size = 0
        with (
            os.fdopen(source_descriptor, "rb", closefd=False) as input_file,
            os.fdopen(destination_descriptor, "wb", closefd=False) as output_file,
        ):
            while block := input_file.read(1024 * 1024):
                observed.update(block)
                observed_size += len(block)
                output_file.write(block)
            output_file.flush()
            os.fsync(output_file.fileno())
        if (observed.hexdigest(), observed_size) != (digest, size):
            raise _reject()
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        os.close(source_descriptor)


def _fsync_private_file(path: Path) -> None:
    _private_regular_file(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _restore_receipt(
    receipt: RecoveryBundleReceipt,
    source_identity: ManagedInboxStoreIdentity,
    sqlite_digest: str,
    sqlite_size: int,
) -> str:
    """Bind the staged transform without hashing the SQLite file it changes."""
    evidence = {
        "version": 1,
        "manifest_digest": receipt.manifest_digest,
        "manifest_size": receipt.manifest_size,
        "sqlite_digest": sqlite_digest,
        "sqlite_size": sqlite_size,
        "source_identity": {
            "store_identity": source_identity.store_identity,
            "store_uuid": source_identity.store_uuid,
        },
        "transformations": {
            "execution_state": RECOVERY_STATE_BLOCKED_RESTORE,
            "reconcile_states": ["acknowledged", "running", "sent"],
        },
    }
    return hashlib.sha256(_canonical_json(evidence)).hexdigest()


def restore_recovery_bundle(receipt: RecoveryBundleReceipt, destination: Path) -> None:
    """Restore one v2 bundle into a blocked, provenance-only SQLite copy.

    This is an internal recovery operation: it never adopts the historical inbox
    identity, opens neither configured operator storage nor providers, and has
    no public transport surface.
    """
    staging: Path | None = None
    published = False
    try:
        manifest, sqlite_path, sqlite_digest, sqlite_size = _restore_manifest(receipt)
        assert receipt.source_database is not None  # The v2 publication verifier required it.
        _source_inventory, source_identity = _verified_source_inventory(receipt.source_database)
        destination = _restore_destination(receipt, destination, sqlite_path)
        _inspect_restore_sqlite(sqlite_path, source_identity, manifest)

        descriptor, stage_name = tempfile.mkstemp(
            prefix=".recovery-restore-", dir=destination.parent
        )
        staging = Path(stage_name)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        _copy_restore_sqlite(sqlite_path, staging, sqlite_digest, sqlite_size)

        WorkRepository(staging)._block_portable_restore_copy(
            source_inbox_context=source_identity,
            installation_uuid=uuid.uuid4().hex,
            bundle_digest=receipt.manifest_digest,
            restore_receipt=_restore_receipt(receipt, source_identity, sqlite_digest, sqlite_size),
        )
        _inspect_restore_sqlite(staging, source_identity, manifest)
        _fsync_private_file(staging)

        os.link(staging, destination, follow_symlinks=False)
        published = True
        _fsync_directory(destination.parent)
        staging.unlink()
        staging = None
        _fsync_directory(destination.parent)
    except Exception:
        raise _reject() from None
    finally:
        if staging is not None and not published and (staging.exists() or staging.is_symlink()):
            staging.unlink()


class OfflineRecoveryCapture:
    """Internal v1 capture service; the verifier is supplied only by server composition."""

    def __init__(self, authority: WorkAuthority, lease: OfflineCutLease) -> None:
        if not isinstance(authority, WorkAuthority) or not isinstance(lease, OfflineCutLease):
            raise _reject()
        self._authority = authority
        self._lease = lease

    def _verify_cut(self, *, database: Path, profile_version: int, phase: str) -> OfflineCutLease:
        if profile_version != WORK_SQLITE_PROFILE_VERSION or phase not in {
            "before",
            "after",
            "promote",
        }:
            raise _reject()
        return self._authority.verify_offline_cut(self._lease, source_database=database)

    def capture(self, source: RecoveryCaptureSource, destination: Path) -> RecoveryBundleReceipt:
        """Create and atomically publish a verified bundle, or leave no destination behind."""
        stage: Path | None = None
        published = False
        phase = "before"
        try:
            if not isinstance(source, RecoveryCaptureSource) or not isinstance(destination, Path):
                raise _reject()
            database = source.database_path.absolute()
            capture_profile = WORK_SQLITE_PROFILE_VERSION
            _regular_file_without_links(database)
            destination = destination.absolute()
            parent = destination.parent
            _private_directory(parent)
            if destination.exists() or destination.is_symlink():
                raise _reject()

            lease = self._verify_cut(
                database=database, profile_version=capture_profile, phase="before"
            )
            phase = "stage"
            inventory, source_identity = _verified_source_inventory(database)
            capture_id = uuid.uuid4().hex
            before_observation = self._authority.observe_offline_cut(
                lease,
                source_database=database,
                capture_id=capture_id,
                phase="before",
                inventory=_inventory_evidence(inventory),
            )

            stage = Path(tempfile.mkdtemp(prefix=".recovery-bundle-", dir=parent))
            os.chmod(stage, 0o700)
            objects = stage / "objects"
            objects.mkdir(mode=0o700)
            copied_database = stage / ".sqlite-copy"
            with (
                closing(_open_readonly_database(database)) as _owned_source_connection,
                _owned_source_connection as source_connection,
            ):
                with (
                    closing(sqlite3.connect(copied_database)) as _owned_copy_connection,
                    _owned_copy_connection as copy_connection,
                ):
                    source_connection.backup(copy_connection)
            os.chmod(copied_database, 0o600)
            compacted_database = _compact_staging_database(copied_database, stage)
            copied_database.unlink()
            copied_database = compacted_database

            with (
                closing(_open_readonly_database(copied_database)) as _owned_copied_connection,
                _owned_copied_connection as copied_connection,
            ):
                copied_inventory = inspect_portable_work_store(
                    copied_connection, expected_source_identity=source_identity
                )
                _scan_exportable_database(copied_connection, copied_inventory)
                external = _declared_external_objects(copied_connection, source)

            object_metadata: dict[str, int] = {}
            object_roles: dict[str, set[str]] = {}
            manifest_references: list[dict[str, object]] = []
            executable_sources: list[tuple[Path, str, int, tuple[int, int], tuple[int, ...]]] = []
            database_digest, database_size = _copy_file_as_object(copied_database, objects)
            object_metadata[database_digest] = database_size
            object_roles[database_digest] = {f"sqlite-v{capture_profile}"}
            copied_database.unlink()
            for object_ in external:
                root = object_.root
                if object_.role == "memory-content":
                    digest, size = _copy_memory_file_as_object(root, object_.relative_path, objects)
                elif object_.role == "executable-content":
                    assert object_.digest is not None and object_.size is not None
                    digest, size, root_identity, leaf_identity = _copy_executable_file_as_object(
                        root, object_.digest, object_.size, objects
                    )
                    executable_sources.append((root, digest, size, root_identity, leaf_identity))
                else:
                    _private_directory(root.absolute())
                    digest, size = _copy_file_as_object(
                        root / object_.relative_path,
                        objects,
                        expected_digest=object_.digest,
                        expected_size=object_.size,
                        scan_utf8=True,
                    )
                previous = object_metadata.get(digest)
                if previous is not None and previous != size:
                    raise _reject()
                object_metadata[digest] = size
                object_roles.setdefault(digest, set()).add(object_.role)
                manifest_references.append(
                    {
                        "digest": digest,
                        "identifier": object_.identifier,
                        "path": f"objects/{digest}",
                        "role": object_.role,
                        "size": size,
                        "version": 1,
                    }
                )

            for root, digest, size, root_identity, leaf_identity in executable_sources:
                _verify_executable_source(root, digest, size, root_identity, leaf_identity)

            after_inventory, after_source_identity = _verified_source_inventory(database)
            if after_inventory != inventory or after_source_identity != source_identity:
                raise _reject()
            lease = (
                self._verify_cut(database=database, profile_version=capture_profile, phase="after")
                or lease
            )
            after_observation = self._authority.observe_offline_cut(
                lease,
                source_database=database,
                capture_id=capture_id,
                phase="after",
                inventory=_inventory_evidence(after_inventory),
            )
            phase = "promote"

            manifest_objects = [
                {
                    "digest": digest,
                    "path": f"objects/{digest}",
                    "roles": sorted(object_roles[digest]),
                    "size": object_metadata[digest],
                }
                for digest in sorted(object_metadata)
            ]
            manifest_references.sort(
                key=lambda reference: (
                    reference["role"],
                    reference["identifier"],
                    reference["digest"],
                )
            )
            coverage = [
                {
                    "digest": item["digest"],
                    "classification": "integrity-only",
                    "roles": item["roles"],
                }
                for item in manifest_objects
            ]

            def promote(
                checked_lease: OfflineCutLease, promote_observation: dict
            ) -> dict[str, object]:
                nonlocal stage
                assert stage is not None  # Staging was established before publication.
                evidence = {
                    "version": 2,
                    "capture_id": capture_id,
                    "lease_id": checked_lease.id,
                    "store_identity": public_offline_store_identity(checked_lease.store_identity),
                    "operator_principal_id": checked_lease.operator_principal_id,
                    "owner": checked_lease.owner,
                    "scope": checked_lease.scope,
                    "epoch": checked_lease.epoch,
                    "fence": checked_lease.fence,
                    "expires_at": checked_lease.expires_at,
                    "revision": checked_lease.revision,
                    "phases": {
                        "before": before_observation,
                        "after": after_observation,
                        "promote": promote_observation,
                    },
                    "coverage": coverage,
                }
                manifest_digest, manifest_size = _write_manifest(
                    stage, manifest_objects, manifest_references, evidence, capture_profile
                )
                staged_receipt = RecoveryBundleReceipt(stage, manifest_digest, manifest_size)
                _verify_recovery_bundle(staged_receipt, require_publication=False)
                os.replace(stage, destination)
                stage = None
                _fsync_directory(parent)
                return {
                    "bundle_path": destination,
                    "manifest_digest": manifest_digest,
                    "manifest_size": manifest_size,
                }

            published_lease = self._authority.publish_offline_cut(
                lease,
                source_database=database,
                capture_id=capture_id,
                inventory=_inventory_evidence(after_inventory),
                publish=promote,
            )
            published = True
            published_digest, published_size = _file_digest(destination / "manifest.json")
            return RecoveryBundleReceipt(
                destination,
                manifest_digest=published_digest,
                manifest_size=published_size,
                source_database=database,
                lease_id=published_lease.id,
                publication_revision=published_lease.revision,
            )
        except (
            OSError,
            sqlite3.Error,
            RecoveryInventoryError,
            TypeError,
            ValueError,
            RecoveryBundleError,
        ):
            try:
                if self._lease is not None:
                    self._authority.record_offline_cut_rejection(self._lease, phase=phase)
            except Exception:
                pass
            raise _reject() from None
        finally:
            if stage is not None and not published and (stage.exists() or stage.is_symlink()):
                shutil.rmtree(stage)
