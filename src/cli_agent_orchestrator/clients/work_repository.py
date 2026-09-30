"""Verified SQLite persistence for durable work, independent of terminal lifetime.

The schema is additive. An incomplete or newer schema is not silently repaired:
new admissions must fail closed until the operator restores a verified store.
"""

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Mapping
from uuid import UUID, uuid4

from cli_agent_orchestrator.clients.knowledge_access_schema import KNOWLEDGE_ACCESS_SCHEMA
from cli_agent_orchestrator.clients.knowledge_schema import (
    KNOWLEDGE_CURSOR_SCHEMA,
    KNOWLEDGE_SCHEMA,
)
from cli_agent_orchestrator.clients.memory_access_schema import MEMORY_ACCESS_SCHEMA
from cli_agent_orchestrator.clients.step_contract_schema import STEP_CONTRACT_SCHEMA
from cli_agent_orchestrator.clients.work_attempt_credential_schema import (
    WORK_ATTEMPT_CREDENTIAL_SCHEMA,
)
from cli_agent_orchestrator.clients.work_authority_schema import AUTHORITY_SCHEMA
from cli_agent_orchestrator.clients.work_bubblewrap_identity_schema import (
    BUBBLEWRAP_IDENTITY_SCHEMA,
)
from cli_agent_orchestrator.clients.work_bubblewrap_setup_intent_schema import (
    BUBBLEWRAP_RELEASE_CLAIM_SCHEMA,
    BUBBLEWRAP_SETUP_INTENT_SCHEMA,
)
from cli_agent_orchestrator.clients.work_decisions_schema import DECISIONS_SCHEMA
from cli_agent_orchestrator.clients.work_delivery_schema import (
    DELIVERY_CONTENT_SCHEMA,
    DELIVERY_SCHEMA,
)
from cli_agent_orchestrator.clients.work_dispatch_schema import (
    DISPATCH_SCHEMA,
    DISPATCH_V2_EVIDENCE_SCHEMA,
)
from cli_agent_orchestrator.clients.work_executable_content_schema import EXECUTABLE_CONTENT_SCHEMA
from cli_agent_orchestrator.clients.work_inbox_schema import (
    INBOX_SCHEMA,
    INBOX_STORE_CONTEXT_SCHEMA,
    ManagedInboxStoreIdentity,
)
from cli_agent_orchestrator.clients.work_mcp_proxy_schema import (
    WORK_MCP_PROXY_EFFECT_SCHEMA,
    WORK_MCP_PROXY_ISSUE_RECOVERY_SCHEMA,
    WORK_MCP_PROXY_ISSUE_SCHEMA,
)
from cli_agent_orchestrator.clients.work_origin_schema import (
    LAUNCH_ORIGIN_SCHEMA,
    LINEAGE_INTEGRITY_SCHEMA,
    LINEAGE_ORIGIN_SCHEMA,
    ORIGIN_AUTHORITY_SCHEMA,
    ORIGIN_SCHEMA,
    TASK_RECEIVED_RECEIPT_SCHEMA,
    WORK_TASK_RECEIVER_ACCEPTANCE_SCHEMA,
    WORKFLOW_STEP_SCHEMA,
)
from cli_agent_orchestrator.clients.work_process_identity_schema import (
    PROCESS_IDENTITY_SCHEMA,
)
from cli_agent_orchestrator.clients.work_recovery_schema import (
    OFFLINE_CUT_OBSERVATION_SCHEMA,
    OFFLINE_CUT_REJECTION_SCHEMA,
    OFFLINE_CUT_SCHEMA,
    RECOVERY_CONTEXT_SCHEMA,
    RECOVERY_CONTEXT_VERSION,
    RECOVERY_STATE_BLOCKED_RESTORE,
    RECOVERY_STATE_NORMAL,
    WORK_WRITER_REGISTRY_SCHEMA,
    RecoveryStoreContext,
)
from cli_agent_orchestrator.clients.work_reservations_schema import RESERVATIONS_SCHEMA
from cli_agent_orchestrator.clients.work_scheduler_schema import (
    SCHEDULER_FAIRNESS_SCHEMA,
    SCHEDULER_SCHEMA,
)
from cli_agent_orchestrator.clients.work_snapshot_schema import SNAPSHOT_SCHEMA
from cli_agent_orchestrator.clients.work_task_receiver_credential_schema import (
    WORK_TASK_RECEIVER_CREDENTIAL_SCHEMA,
)
from cli_agent_orchestrator.clients.worktree_evidence_schema import WORKTREE_EVIDENCE_SCHEMA
from cli_agent_orchestrator.services.work_reducer import (
    TransitionConflict,
    TransitionEvidence,
    delivery_transition,
    transition,
)


class SchemaMismatch(RuntimeError):
    """The installed schema cannot safely be used by this writer."""


class WorkConflict(ValueError):
    """An identity or revision does not match the durable operation."""


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _identity(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f"invalid {label}")
    return value


_PROCESS_IDENTITY_V3_FIELDS = frozenset(
    {
        "version",
        "boot_id",
        "monitor_pid",
        "monitor_start_time_ticks",
        "init_pid",
        "init_start_time_ticks",
        "init_parent_pid",
        "pid_namespace",
        "net_namespace",
        "ipc_namespace",
        "monitor_argv_prefix_sha256",
        "identity_sha256",
    }
)
_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _validate_process_identity_v3(
    value: Mapping[str, object],
) -> tuple[dict[str, object], str]:
    """Validate exact, argv-redacted v3 identity and its canonical content digest."""
    if not isinstance(value, Mapping):
        raise ValueError("process identity must be a mapping")
    if set(value) != _PROCESS_IDENTITY_V3_FIELDS:
        raise ValueError("process identity has an incomplete or unknown field set")

    version = value["version"]
    boot_id = value["boot_id"]
    monitor_pid = value["monitor_pid"]
    monitor_start = value["monitor_start_time_ticks"]
    init_pid = value["init_pid"]
    init_start = value["init_start_time_ticks"]
    init_parent = value["init_parent_pid"]
    numeric_fields = (version, monitor_pid, monitor_start, init_pid, init_start, init_parent)
    if any(type(field) is not int for field in numeric_fields):
        raise ValueError("process identity numeric fields must be integers")
    if version != 3:
        raise ValueError("process identity protocol version must be 3")
    if not isinstance(boot_id, str) or not boot_id.strip() or "\x00" in boot_id:
        raise ValueError("process identity boot ID is invalid")
    if monitor_pid <= 0 or init_pid <= 0 or monitor_pid == init_pid:
        raise ValueError("process identity PIDs are invalid")
    if monitor_start <= 0 or init_start <= 0 or init_parent != monitor_pid:
        raise ValueError("process identity process relationship is invalid")

    namespaces: dict[str, list[int]] = {}
    for key in ("pid_namespace", "net_namespace", "ipc_namespace"):
        namespace = value[key]
        if (
            not isinstance(namespace, (list, tuple))
            or len(namespace) != 2
            or any(type(component) is not int or component < 0 for component in namespace)
        ):
            raise ValueError(f"process identity {key} is invalid")
        namespaces[key] = [namespace[0], namespace[1]]

    argv_prefix_digest = value["monitor_argv_prefix_sha256"]
    identity_digest = value["identity_sha256"]
    if not isinstance(argv_prefix_digest, str) or not _LOWER_SHA256.fullmatch(argv_prefix_digest):
        raise ValueError("process identity monitor argv prefix digest is invalid")
    if not isinstance(identity_digest, str) or not _LOWER_SHA256.fullmatch(identity_digest):
        raise ValueError("process identity digest is invalid")

    payload: dict[str, object] = {
        "version": version,
        "boot_id": boot_id,
        "monitor_pid": monitor_pid,
        "monitor_start_time_ticks": monitor_start,
        "init_pid": init_pid,
        "init_start_time_ticks": init_start,
        "init_parent_pid": init_parent,
        **namespaces,
        "monitor_argv_prefix_sha256": argv_prefix_digest,
    }
    expected_digest = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
    if identity_digest != expected_digest:
        raise ValueError("process identity digest does not match its fields")
    return {**payload, "identity_sha256": expected_digest}, expected_digest


def _read_stored_process_identity(row: sqlite3.Row) -> tuple[dict[str, object], str]:
    """Decode one immutable row and recheck its exact shape and both digests."""
    try:
        value = json.loads(row["identity_json"])
        identity, digest = _validate_process_identity_v3(value)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise SchemaMismatch("stored Work process identity is corrupt") from error
    if (
        row["protocol_version"] != 3
        or row["identity_sha256"] != digest
        or _json(identity) != row["identity_json"]
    ):
        raise SchemaMismatch("stored Work process identity digest or encoding is corrupt")
    return identity, digest


_BUBBLEWRAP_IDENTITY_FIELDS = frozenset(
    {
        "version",
        "kind",
        "boot_id",
        "monitor_pid",
        "monitor_start_time_ticks",
        "init_pid",
        "init_start_time_ticks",
        "init_parent_pid",
        "pid_namespace",
        "net_namespace",
        "ipc_namespace",
        "monitor_argv_sha256",
        "monitor_executable_sha256",
        "identity_sha256",
    }
)


def _validate_bubblewrap_identity(
    value: Mapping[str, object],
) -> tuple[dict[str, object], str]:
    """Accept the exact Bwrap shape; argv and executable bytes never enter SQLite."""
    if not isinstance(value, Mapping) or set(value) != _BUBBLEWRAP_IDENTITY_FIELDS:
        raise ValueError("Bubblewrap process identity has an incomplete or unknown field set")
    payload = {key: value[key] for key in _BUBBLEWRAP_IDENTITY_FIELDS if key != "identity_sha256"}
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("Bubblewrap process identity version is invalid")
    if payload["kind"] != "bubblewrap":
        raise ValueError("Bubblewrap process identity kind is invalid")
    if (
        not isinstance(payload["boot_id"], str)
        or not payload["boot_id"].strip()
        or "\x00" in payload["boot_id"]
    ):
        raise ValueError("Bubblewrap process identity boot ID is invalid")
    for name in (
        "monitor_pid",
        "monitor_start_time_ticks",
        "init_pid",
        "init_start_time_ticks",
        "init_parent_pid",
    ):
        if type(payload[name]) is not int or payload[name] <= 0:
            raise ValueError("Bubblewrap process identity process fields are invalid")
    if (
        payload["monitor_pid"] == payload["init_pid"]
        or payload["init_parent_pid"] != payload["monitor_pid"]
    ):
        raise ValueError("Bubblewrap process identity process relationship is invalid")
    for name in ("pid_namespace", "net_namespace", "ipc_namespace"):
        namespace = payload[name]
        if (
            not isinstance(namespace, (tuple, list))
            or len(namespace) != 2
            or any(type(part) is not int or part < 0 for part in namespace)
        ):
            raise ValueError("Bubblewrap process identity namespace is invalid")
        payload[name] = list(namespace)
    for name in ("monitor_argv_sha256", "monitor_executable_sha256"):
        if not isinstance(payload[name], str) or not _LOWER_SHA256.fullmatch(payload[name]):
            raise ValueError("Bubblewrap process identity digest is invalid")
    digest = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
    if value["identity_sha256"] != digest:
        raise ValueError("Bubblewrap process identity digest does not match its fields")
    return {**payload, "identity_sha256": digest}, digest


def _read_stored_bubblewrap_identity(row: sqlite3.Row) -> tuple[dict[str, object], str]:
    try:
        identity, digest = _validate_bubblewrap_identity(json.loads(row["identity_json"]))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SchemaMismatch("stored Bubblewrap process identity is corrupt") from exc
    if (
        row["protocol_version"] != 1
        or row["identity_sha256"] != digest
        or row["identity_json"] != _json(identity)
    ):
        raise SchemaMismatch("stored Bubblewrap process identity digest or encoding is corrupt")
    return identity, digest


def _requires_managed_lineage(*, operation_kind: str, parent_work_item_id: str | None) -> bool:
    """Public callers cannot create child lineage without the sealed origin owner."""
    return parent_work_item_id is not None or operation_kind in {"child", "handoff"}


def _store_identity(value: Path | str) -> str:
    try:
        path = Path(value).resolve(strict=False)
    except (TypeError, OSError, RuntimeError) as error:
        raise ValueError("invalid inbox store identity") from error
    if not path.is_absolute():
        raise ValueError("inbox store identity must resolve absolutely")
    return str(path)


def _connection_store_identity(connection: sqlite3.Connection) -> str:
    """Return the canonical main SQLite file for this exact live connection."""
    rows = connection.execute("PRAGMA database_list").fetchall()
    main = next((row[2] for row in rows if row[1] == "main"), None)
    if not main:
        raise SchemaMismatch("managed inbox requires a file-backed work store")
    try:
        return _store_identity(main)
    except ValueError as error:
        raise SchemaMismatch("work store identity cannot be resolved") from error


def _store_uuid(value) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid managed inbox store UUID")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError("invalid managed inbox store UUID") from error
    if parsed.hex != value:
        raise ValueError("managed inbox store UUID must be canonical")
    return value


def _read_stored_inbox_context(connection: sqlite3.Connection) -> ManagedInboxStoreIdentity:
    """Read canonical v16 provenance without treating it as this file's identity."""
    try:
        rows = connection.execute(
            "SELECT singleton,store_identity,store_uuid FROM work_inbox_store_context"
        ).fetchall()
    except sqlite3.DatabaseError as error:
        raise SchemaMismatch("managed inbox store context is absent") from error
    if len(rows) != 1:
        raise SchemaMismatch("managed inbox store context is absent or contradictory")
    row = rows[0]
    try:
        singleton, stored_identity, stored_uuid = row[0], row[1], row[2]
    except (IndexError, TypeError) as error:
        raise SchemaMismatch("managed inbox store context is corrupt") from error
    if singleton != 1:
        raise SchemaMismatch("managed inbox store context is absent or contradictory")
    try:
        context = ManagedInboxStoreIdentity(
            store_identity=_store_identity(stored_identity),
            store_uuid=_store_uuid(stored_uuid),
        )
    except ValueError as error:
        raise SchemaMismatch("managed inbox store context is corrupt") from error
    return context


def _stored_inbox_context(connection: sqlite3.Connection) -> ManagedInboxStoreIdentity:
    """Read the v16 context and prove it names this connection's actual file."""
    context = _read_stored_inbox_context(connection)
    if context.store_identity != _connection_store_identity(connection):
        raise SchemaMismatch("managed inbox store context names another file")
    return context


def _recovery_context_uuid(value, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid recovery {label}")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError(f"invalid recovery {label}") from error
    if parsed.hex != value:
        raise ValueError(f"recovery {label} must be canonical")
    return value


def _recovery_digest(value, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"invalid recovery {label}")
    return value


def _stored_recovery_context(connection: sqlite3.Connection) -> RecoveryStoreContext:
    """Read the v24 singleton; its presence never itself authorizes execution."""
    try:
        rows = connection.execute(
            "SELECT singleton,context_version,execution_state,installation_uuid,"
            "source_installation_uuid,bundle_digest,restore_receipt,created_at "
            "FROM work_recovery_context"
        ).fetchall()
    except sqlite3.DatabaseError as error:
        raise SchemaMismatch("recovery context is absent") from error
    if len(rows) != 1:
        raise SchemaMismatch("recovery context is absent or contradictory")
    try:
        (
            singleton,
            context_version,
            execution_state,
            installation_uuid,
            source_installation_uuid,
            bundle_digest,
            restore_receipt,
            created_at,
        ) = rows[0]
    except (IndexError, TypeError, ValueError) as error:
        raise SchemaMismatch("recovery context is corrupt") from error
    if (
        singleton != 1
        or context_version != RECOVERY_CONTEXT_VERSION
        or type(created_at) not in {int, float}
        or not math.isfinite(created_at)
    ):
        raise SchemaMismatch("recovery context is corrupt or unsupported")
    try:
        installation_uuid = _recovery_context_uuid(installation_uuid, "installation UUID")
        if execution_state == RECOVERY_STATE_NORMAL:
            if any(
                value is not None
                for value in (source_installation_uuid, bundle_digest, restore_receipt)
            ):
                raise ValueError("normal recovery context contains restore metadata")
        elif execution_state == RECOVERY_STATE_BLOCKED_RESTORE:
            source_installation_uuid = _recovery_context_uuid(
                source_installation_uuid, "source installation UUID"
            )
            bundle_digest = _recovery_digest(bundle_digest, "bundle digest")
            restore_receipt = _recovery_digest(restore_receipt, "restore receipt")
            if source_installation_uuid == installation_uuid:
                raise ValueError("restored context reuses source installation UUID")
        else:
            raise ValueError("unknown recovery execution state")
    except ValueError as error:
        raise SchemaMismatch("recovery context is corrupt or unsupported") from error
    return RecoveryStoreContext(
        context_version=context_version,
        execution_state=execution_state,
        installation_uuid=installation_uuid,
        source_installation_uuid=source_installation_uuid,
        bundle_digest=bundle_digest,
        restore_receipt=restore_receipt,
    )


SCHEMA_VERSION = 39
_SCHEMA = (
    """CREATE TABLE work_migrations (
        version INTEGER PRIMARY KEY, checksum TEXT NOT NULL,
        applied_at REAL NOT NULL, verification_result TEXT NOT NULL
    )""",
    """CREATE TABLE work_jobs (
        id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version=1),
        project_id TEXT NOT NULL, principal_id TEXT NOT NULL,
        allowed_providers TEXT NOT NULL, grant_id TEXT NOT NULL,
        budget TEXT NOT NULL DEFAULT '{}', priority INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'planning' CHECK(state IN ('planning','running','waiting','completed','failed','revoked')),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0), created_at REAL NOT NULL
    )""",
    """CREATE TABLE work_items (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES work_jobs(id),
        parent_work_item_id TEXT, operation_kind TEXT NOT NULL,
        idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
        contract_id TEXT NOT NULL, snapshot_id TEXT,
        state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','waiting_children','succeeded','failed','reconcile','cancelled')),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
        accepted_result_id TEXT REFERENCES work_results(id),
        created_at REAL NOT NULL, UNIQUE(job_id,id),
        FOREIGN KEY(job_id,parent_work_item_id) REFERENCES work_items(job_id,id)
    )""",
    "CREATE UNIQUE INDEX work_items_idempotency ON work_items(job_id,operation_kind,idempotency_key)",
    """CREATE TABLE work_attempts (
        id TEXT PRIMARY KEY, work_item_id TEXT NOT NULL REFERENCES work_items(id),
        attempt_number INTEGER NOT NULL CHECK(attempt_number>0),
        generation INTEGER NOT NULL CHECK(generation>0), provider TEXT NOT NULL,
        terminal_id TEXT, state TEXT NOT NULL DEFAULT 'planned'
            CHECK(state IN ('planned','sent','acknowledged','running','finished','failed','reconcile','cancelled')),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
        lease_expires_at REAL NOT NULL, result_id TEXT REFERENCES work_results(id),
        reconcile_reason TEXT, cleanup_state TEXT NOT NULL DEFAULT 'not_requested',
        created_at REAL NOT NULL,
        UNIQUE(work_item_id,attempt_number), UNIQUE(work_item_id,generation)
    )""",
    """CREATE TABLE work_results (
        id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES work_attempts(id),
        schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version=1),
        content_hash TEXT NOT NULL, immutable_location TEXT NOT NULL,
        byte_length INTEGER NOT NULL CHECK(byte_length>=0),
        validation_state TEXT NOT NULL CHECK(validation_state IN ('verified','rejected')),
        validator_id TEXT NOT NULL, validation_evidence TEXT NOT NULL,
        created_at REAL NOT NULL
    )""",
    """CREATE TABLE work_event_sequences (
        job_id TEXT PRIMARY KEY REFERENCES work_jobs(id),
        high_water INTEGER NOT NULL DEFAULT 0 CHECK(high_water>=0),
        retained_after INTEGER NOT NULL DEFAULT 0 CHECK(retained_after>=0)
    )""",
    """CREATE TABLE work_events (
        event_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES work_jobs(id),
        work_item_id TEXT REFERENCES work_items(id), attempt_id TEXT REFERENCES work_attempts(id),
        sequence INTEGER NOT NULL CHECK(sequence>0), event_type TEXT NOT NULL,
        schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version=1),
        actor_id TEXT NOT NULL, occurred_at REAL NOT NULL, metadata TEXT NOT NULL DEFAULT '{}',
        UNIQUE(job_id,sequence)
    )""",
    """CREATE TABLE work_transition_receipts (
        event_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
        work_item_id TEXT NOT NULL REFERENCES work_items(id)
    )""",
)
SCHEMA_CHECKSUM = hashlib.sha256(";\n".join(_SCHEMA).encode()).hexdigest()
_MIGRATIONS = {
    1: _SCHEMA,
    2: AUTHORITY_SCHEMA,
    3: RESERVATIONS_SCHEMA,
    4: STEP_CONTRACT_SCHEMA,
    5: SCHEDULER_SCHEMA,
    6: WORKTREE_EVIDENCE_SCHEMA,
    7: KNOWLEDGE_SCHEMA,
    8: SCHEDULER_FAIRNESS_SCHEMA,
    9: SNAPSHOT_SCHEMA,
    10: DISPATCH_SCHEMA,
    11: DELIVERY_SCHEMA,
    12: KNOWLEDGE_ACCESS_SCHEMA,
    13: MEMORY_ACCESS_SCHEMA,
    14: DELIVERY_CONTENT_SCHEMA,
    15: INBOX_SCHEMA,
    16: INBOX_STORE_CONTEXT_SCHEMA,
    17: KNOWLEDGE_CURSOR_SCHEMA,
    18: DECISIONS_SCHEMA,
    19: ORIGIN_SCHEMA,
    20: ORIGIN_AUTHORITY_SCHEMA,
    21: LAUNCH_ORIGIN_SCHEMA,
    22: LINEAGE_ORIGIN_SCHEMA,
    23: LINEAGE_INTEGRITY_SCHEMA,
    24: RECOVERY_CONTEXT_SCHEMA,
    25: TASK_RECEIVED_RECEIPT_SCHEMA,
    26: (
        *OFFLINE_CUT_SCHEMA,
        *OFFLINE_CUT_OBSERVATION_SCHEMA,
        *OFFLINE_CUT_REJECTION_SCHEMA,
        *WORK_WRITER_REGISTRY_SCHEMA,
    ),
    27: PROCESS_IDENTITY_SCHEMA,
    28: DISPATCH_V2_EVIDENCE_SCHEMA,
    29: EXECUTABLE_CONTENT_SCHEMA,
    30: BUBBLEWRAP_IDENTITY_SCHEMA,
    31: BUBBLEWRAP_SETUP_INTENT_SCHEMA,
    32: BUBBLEWRAP_RELEASE_CLAIM_SCHEMA,
    33: WORK_MCP_PROXY_ISSUE_SCHEMA,
    34: WORK_MCP_PROXY_EFFECT_SCHEMA,
    35: WORK_MCP_PROXY_ISSUE_RECOVERY_SCHEMA,
    36: WORK_ATTEMPT_CREDENTIAL_SCHEMA,
    37: WORK_TASK_RECEIVER_ACCEPTANCE_SCHEMA,
    38: WORK_TASK_RECEIVER_CREDENTIAL_SCHEMA,
    39: WORKFLOW_STEP_SCHEMA,
}
_CHECKSUMS = {
    version: hashlib.sha256(";\n".join(statements).encode()).hexdigest()
    for version, statements in _MIGRATIONS.items()
}


def _schema_objects(connection: sqlite3.Connection) -> dict[str, str]:
    return dict(
        connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE substr(name,1,5)='work_' AND sql IS NOT NULL"
        ).fetchall()
    )


def _expected_schemas() -> dict[int, dict[str, str]]:
    schemas = {}
    with sqlite3.connect(":memory:") as connection:
        for version, statements in _MIGRATIONS.items():
            for statement in statements:
                connection.execute(statement)
            schemas[version] = _schema_objects(connection)
    return schemas


_EXPECTED_SCHEMAS = _expected_schemas()


class WorkRepository:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    @property
    def delivery_content_root(self) -> Path:
        """The private, content-addressed payload root paired with this SQLite store."""
        return self.path.with_name(f"{self.path.name}.delivery-content")

    @property
    def executable_content_root(self) -> Path:
        """The private static executable namespace paired with this SQLite store."""
        return self.path.with_name(f"{self.path.name}.executable-content")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @contextmanager
    def read_snapshot(self) -> Iterator[sqlite3.Connection]:
        """Read a verified existing store; queries never create or migrate a database."""
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN")
            self._verify(connection, version=SCHEMA_VERSION)
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Verify installed history, then apply additive migrations atomically."""
        with self.transaction() as connection:
            present = _schema_objects(connection)
            installed = 0
            if present:
                if present.get("work_migrations") != _EXPECTED_SCHEMAS[1]["work_migrations"]:
                    raise SchemaMismatch("work migration ledger is missing or modified")
                row = connection.execute("SELECT max(version) FROM work_migrations").fetchone()
                installed = row[0]
                if installed not in _MIGRATIONS:
                    raise SchemaMismatch("unknown or empty work migration history")
                self._verify(connection, version=installed)
            for version in range(installed + 1, SCHEMA_VERSION + 1):
                for statement in _MIGRATIONS[version]:
                    connection.execute(statement)
                if version == 16:
                    self._initialize_inbox_store_context(connection)
                if version == 24:
                    self._initialize_recovery_context(connection)
                connection.execute(
                    "INSERT INTO work_migrations VALUES (?,?,?,?)",
                    (version, _CHECKSUMS[version], time.time(), "verified"),
                )
                self._verify(connection, version=version)
            self._verify(connection, version=SCHEMA_VERSION)

    @staticmethod
    def _expire_offline_cut(connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE work_offline_cuts SET state='expired',revision=revision+1 "
            "WHERE state='live' AND expires_at<=?",
            (time.time(),),
        )

    @staticmethod
    def _registered_writer_count(connection: sqlite3.Connection) -> int:
        return connection.execute(
            "SELECT count(*) FROM work_registered_writers WHERE state='active'"
        ).fetchone()[0]

    @classmethod
    def _assert_offline_cut_allows_writer(cls, connection: sqlite3.Connection) -> None:
        """Registered Work writers are denied while a durable cut is live."""
        if (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='work_registered_writers'"
            ).fetchone()
            is None
        ):
            return  # Historical readers have no v26 cut state to interpret.
        cls._expire_offline_cut(connection)
        if (
            connection.execute(
                "SELECT 1 FROM work_offline_cuts WHERE state='live' LIMIT 1"
            ).fetchone()
            is not None
        ):
            raise WorkConflict("registered Work writer is fenced by offline cut")

    @classmethod
    def _register_writer_effect(cls, connection: sqlite3.Connection, *, writer_id: str) -> None:
        """Durably register one actual Work effect before its external boundary."""
        if not connection.in_transaction:
            raise ValueError("writer registration requires caller transaction")
        _identity(writer_id, "writer_id")
        cls._assert_offline_cut_allows_writer(connection)
        row = connection.execute(
            "SELECT state FROM work_registered_writers WHERE writer_id=?", (writer_id,)
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO work_registered_writers "
                "(writer_id,owner,scope,state,revision,registered_at) VALUES (?,?,?,?,?,?)",
                (writer_id, "work", "registered-work-writers", "active", 1, time.time()),
            )
        elif row["state"] != "active":
            raise WorkConflict("released Work writer cannot resume an effect")

    @staticmethod
    def _release_writer_effect(connection: sqlite3.Connection, *, writer_id: str) -> None:
        if not connection.in_transaction:
            raise ValueError("writer release requires caller transaction")
        changed = connection.execute(
            "UPDATE work_registered_writers SET state='released',revision=revision+1,released_at=? "
            "WHERE writer_id=? AND state='active'",
            (time.time(), writer_id),
        )
        if changed.rowcount != 1:
            raise WorkConflict("registered Work writer is absent or already released")

    def _acquire_offline_cut(
        self, connection: sqlite3.Connection, *, operator_principal_id: str, ttl: float
    ):
        """Create the one live server-owned cut after proving all registered writers stopped."""
        if not connection.in_transaction:
            raise ValueError("caller-owned transaction required")
        if (
            not isinstance(ttl, (int, float))
            or isinstance(ttl, bool)
            or not math.isfinite(ttl)
            or ttl <= 0
        ):
            raise ValueError("cut TTL must be finite and positive")
        self._expire_offline_cut(connection)
        if self._registered_writer_count(connection):
            raise WorkConflict("registered Work writer is active")
        epoch = connection.execute(
            "SELECT coalesce(max(epoch),0)+1 FROM work_offline_cuts"
        ).fetchone()[0]
        now = time.time()
        lease = {
            "id": uuid4().hex,
            "store_identity": _connection_store_identity(connection),
            "operator_principal_id": operator_principal_id,
            "owner": "work",
            "scope": "registered-work-writers",
            "epoch": epoch,
            "fence": epoch,
            "expires_at": now + float(ttl),
            "revision": 1,
            "state": "live",
        }
        connection.execute(
            "INSERT INTO work_offline_cuts "
            "(id,store_identity,operator_principal_id,owner,scope,epoch,fence,expires_at,revision,state,created_at) "
            "VALUES (:id,:store_identity,:operator_principal_id,:owner,:scope,:epoch,:fence,:expires_at,:revision,:state,:created_at)",
            {**lease, "created_at": now},
        )
        return lease

    def _verify_offline_cut(self, connection: sqlite3.Connection, lease: dict) -> dict:
        """Fence capture and publication against expiry, revocation and new writers."""
        self._expire_offline_cut(connection)
        row = connection.execute(
            "SELECT * FROM work_offline_cuts WHERE id=?", (lease.get("id"),)
        ).fetchone()
        if (
            row is None
            or any(
                row[key] != lease.get(key)
                for key in (
                    "store_identity",
                    "operator_principal_id",
                    "owner",
                    "scope",
                    "epoch",
                    "fence",
                    "revision",
                )
            )
            or row["state"] != "live"
            or row["expires_at"] <= time.time()
        ):
            raise WorkConflict("offline cut lease is absent, stale or expired")
        if row["store_identity"] != _connection_store_identity(
            connection
        ) or self._registered_writer_count(connection):
            raise WorkConflict("offline cut writer fence changed")
        return dict(row)

    def _revoke_offline_cut(self, connection: sqlite3.Connection, lease: dict) -> None:
        row = self._verify_offline_cut(connection, lease)
        changed = connection.execute(
            "UPDATE work_offline_cuts SET state='revoked',revision=revision+1,revoked_at=? "
            "WHERE id=? AND state='live' AND revision=?",
            (time.time(), row["id"], row["revision"]),
        )
        if changed.rowcount != 1:
            raise WorkConflict("offline cut revocation lost its fence")

    def _publish_offline_cut(
        self,
        connection: sqlite3.Connection,
        lease: dict,
        *,
        bundle_path: Path,
        capture_id: str,
        manifest_digest: str,
        manifest_size: int,
    ) -> dict:
        """Commit the receipt eligibility only while the checked live fence is held."""
        row = self._verify_offline_cut(connection, lease)
        if (
            not isinstance(bundle_path, Path)
            or not bundle_path.is_absolute()
            or not isinstance(manifest_digest, str)
            or len(manifest_digest) != 64
            or any(character not in "0123456789abcdef" for character in manifest_digest)
            or type(manifest_size) is not int
            or manifest_size < 0
            or not isinstance(capture_id, str)
            or len(capture_id) != 32
            or any(character not in "0123456789abcdef" for character in capture_id)
        ):
            raise WorkConflict("offline cut publication is invalid")
        changed = connection.execute(
            "UPDATE work_offline_cuts SET state='published',revision=revision+1,"
            "published_path=?,published_capture_id=?,published_manifest_digest=?,published_manifest_size=?,published_at=? "
            "WHERE id=? AND state='live' AND revision=?",
            (
                str(bundle_path),
                capture_id,
                manifest_digest,
                manifest_size,
                time.time(),
                row["id"],
                row["revision"],
            ),
        )
        if changed.rowcount != 1:
            raise WorkConflict("offline cut publication lost its fence")
        return dict(
            connection.execute(
                "SELECT * FROM work_offline_cuts WHERE id=?", (row["id"],)
            ).fetchone()
        )

    def _observe_offline_cut(
        self,
        connection: sqlite3.Connection,
        lease: dict,
        *,
        capture_id: str,
        phase: str,
        observation: str,
    ) -> dict:
        row = self._verify_offline_cut(connection, lease)
        if (
            not isinstance(capture_id, str)
            or len(capture_id) != 32
            or any(character not in "0123456789abcdef" for character in capture_id)
            or phase not in {"before", "after", "promote"}
            or not isinstance(observation, str)
        ):
            raise WorkConflict("offline cut observation is invalid")
        connection.execute(
            "INSERT INTO work_offline_cut_observations(lease_id,capture_id,phase,observation) VALUES (?,?,?,?)",
            (row["id"], capture_id, phase, observation),
        )
        return dict(
            connection.execute(
                "SELECT lease_id,capture_id,phase,observation FROM work_offline_cut_observations "
                "WHERE lease_id=? AND capture_id=? AND phase=?",
                (row["id"], capture_id, phase),
            ).fetchone()
        )

    @staticmethod
    def _record_offline_cut_rejection(
        connection: sqlite3.Connection, *, lease_id: str, phase: str
    ) -> None:
        if phase not in {"before", "after", "promote", "stage"}:
            raise ValueError("invalid offline cut rejection phase")
        connection.execute(
            "INSERT INTO work_offline_cut_rejections VALUES (?,?,?,?,?)",
            (uuid4().hex, lease_id, phase, "capture_rejected", time.time()),
        )

    @staticmethod
    def _verify_v15_inbox_bridge_context(connection, store_identity: str) -> None:
        """Carry v15 rows forward literally only when they name this same file."""
        paired = connection.execute(
            "SELECT store_identity FROM work_inbox_store_identity WHERE singleton=1"
        ).fetchone()
        if paired is not None and paired["store_identity"] != store_identity:
            raise SchemaMismatch("v15 managed inbox store identity is contradictory")
        inconsistent = connection.execute(
            "SELECT 1 FROM work_inbox_bindings WHERE store_identity<>? LIMIT 1",
            (store_identity,),
        ).fetchone()
        if inconsistent is not None:
            raise SchemaMismatch("v15 managed inbox bridge names another file")

    def _initialize_inbox_store_context(self, connection) -> None:
        """Create the one non-secret UUID during migration, never admission."""
        store_identity = _connection_store_identity(connection)
        self._verify_v15_inbox_bridge_context(connection, store_identity)
        existing = connection.execute(
            "SELECT singleton,store_identity,store_uuid FROM work_inbox_store_context"
        ).fetchall()
        if not existing:
            connection.execute(
                "INSERT INTO work_inbox_store_context VALUES (1,?,?,?)",
                (uuid4().hex, store_identity, time.time()),
            )
        # A concurrent initializer cannot enter this transaction. If a recovered
        # partial store has a winner, validate and retain it instead of regenerating.
        _stored_inbox_context(connection)

    @staticmethod
    def _initialize_recovery_context(connection) -> None:
        """Install the one normal v24 context inside the migration transaction."""
        existing = connection.execute("SELECT singleton FROM work_recovery_context").fetchall()
        if existing:
            raise SchemaMismatch("recovery migration context is contradictory")
        connection.execute(
            "INSERT INTO work_recovery_context VALUES (?,?,?,?,?,?,?,?)",
            (
                1,
                RECOVERY_CONTEXT_VERSION,
                RECOVERY_STATE_NORMAL,
                uuid4().hex,
                None,
                None,
                None,
                time.time(),
            ),
        )
        _stored_recovery_context(connection)

    def _block_recovery_for_restore(
        self,
        connection: sqlite3.Connection,
        *,
        installation_uuid: str,
        bundle_digest: str,
        restore_receipt: str,
    ) -> RecoveryStoreContext:
        """Internal one-way transition used only by a future verified restore path."""
        self._verify(connection)
        context = _stored_recovery_context(connection)
        if context.execution_state != RECOVERY_STATE_NORMAL:
            raise SchemaMismatch("recovery context is not eligible for restore blocking")
        try:
            installation_uuid = _recovery_context_uuid(installation_uuid, "installation UUID")
            bundle_digest = _recovery_digest(bundle_digest, "bundle digest")
            restore_receipt = _recovery_digest(restore_receipt, "restore receipt")
        except ValueError as error:
            raise SchemaMismatch("recovery restore metadata is invalid") from error
        if installation_uuid == context.installation_uuid:
            raise SchemaMismatch("restored context must have a new installation UUID")
        updated = connection.execute(
            "UPDATE work_recovery_context SET execution_state=?,installation_uuid=?,"
            "source_installation_uuid=?,bundle_digest=?,restore_receipt=? "
            "WHERE singleton=1 AND execution_state=?",
            (
                RECOVERY_STATE_BLOCKED_RESTORE,
                installation_uuid,
                context.installation_uuid,
                bundle_digest,
                restore_receipt,
                RECOVERY_STATE_NORMAL,
            ),
        )
        if updated.rowcount != 1:
            raise SchemaMismatch("recovery context transition was not applied")
        return _stored_recovery_context(connection)

    @staticmethod
    def _verify_portable_restore_copy(
        connection: sqlite3.Connection, *, source_inbox_context: ManagedInboxStoreIdentity
    ) -> RecoveryStoreContext:
        """Verify a v26 copy while retaining, rather than adopting, inbox provenance.

        This is deliberately not a mode of ``_verify``: ordinary repositories
        must continue to require that their inbox context names their live file.
        The restore owner supplies the identity/UUID already verified from the
        source manifest, so the copied database can only retain that provenance.
        """
        try:
            expected_context = ManagedInboxStoreIdentity(
                store_identity=_store_identity(source_inbox_context.store_identity),
                store_uuid=_store_uuid(source_inbox_context.store_uuid),
            )
        except (AttributeError, ValueError) as error:
            raise SchemaMismatch("portable restore inbox provenance is invalid") from error
        expected = _EXPECTED_SCHEMAS[SCHEMA_VERSION]
        if _schema_objects(connection) != expected:
            raise SchemaMismatch("work schema is incomplete, incompatible or modified")
        records = connection.execute(
            "SELECT version,checksum,verification_result FROM work_migrations ORDER BY version"
        ).fetchall()
        if [tuple(row) for row in records] != [
            (number, _CHECKSUMS[number], "verified") for number in range(1, SCHEMA_VERSION + 1)
        ]:
            raise SchemaMismatch("work migration ledger is incompatible or modified")
        historical_context = _read_stored_inbox_context(connection)
        if historical_context != expected_context:
            raise SchemaMismatch("portable restore inbox provenance differs from the source")
        if historical_context.store_identity == _connection_store_identity(connection):
            raise SchemaMismatch("portable restore requires a path-distinct copied store")
        WorkRepository._verify_v15_inbox_bridge_context(
            connection, historical_context.store_identity
        )
        context = _stored_recovery_context(connection)
        for table, sql in expected.items():
            if not sql.startswith("CREATE TABLE"):
                continue
            if connection.execute(f"PRAGMA foreign_key_check({table})").fetchone():
                raise SchemaMismatch("work store contains invalid references")
        return context

    def _reconcile_portable_restore_attempts(self, connection: sqlite3.Connection) -> None:
        """Convert only uncertain copied delivery history to durable reconciliation."""
        attempts = connection.execute(
            "SELECT id FROM work_attempts "
            "WHERE state IN ('sent','acknowledged','running') "
            "ORDER BY work_item_id,attempt_number,id"
        ).fetchall()
        for row in attempts:
            attempt = connection.execute(
                "SELECT * FROM work_attempts WHERE id=?", (row["id"],)
            ).fetchone()
            if attempt is None:
                raise SchemaMismatch("portable restore attempt disappeared")
            work = self._work(connection, attempt["work_item_id"])
            if work["attempts"][-1]["id"] != attempt["id"]:
                raise SchemaMismatch("portable restore contains a stale uncertain attempt")
            try:
                self._transition_attempt(
                    connection,
                    attempt_id=attempt["id"],
                    generation=attempt["generation"],
                    expected_revision=attempt["revision"],
                    expected_state=attempt["state"],
                    target="reconcile",
                    actor_id="portable-restore",
                    event_id=uuid4().hex,
                    evidence=TransitionEvidence(
                        generation=attempt["generation"],
                        expected_generation=attempt["generation"],
                    ),
                )
            except WorkConflict as error:
                raise SchemaMismatch("portable restore attempt cannot be reconciled") from error

    def _block_portable_restore_copy(
        self,
        *,
        source_inbox_context: ManagedInboxStoreIdentity,
        installation_uuid: str,
        bundle_digest: str,
        restore_receipt: str,
    ) -> RecoveryStoreContext:
        """Atomically block and reconcile an already-verified path-distinct v26 copy.

        The stored inbox path/UUID and v15 bridge are immutable source
        provenance.  They are validated against the supplied source context but
        never rewritten to make the staging path operational.
        """
        with self.transaction() as connection:
            context = self._verify_portable_restore_copy(
                connection, source_inbox_context=source_inbox_context
            )
            if context.execution_state != RECOVERY_STATE_NORMAL:
                raise SchemaMismatch("recovery context is not eligible for restore blocking")
            try:
                installation_uuid = _recovery_context_uuid(installation_uuid, "installation UUID")
                bundle_digest = _recovery_digest(bundle_digest, "bundle digest")
                restore_receipt = _recovery_digest(restore_receipt, "restore receipt")
            except ValueError as error:
                raise SchemaMismatch("recovery restore metadata is invalid") from error
            if installation_uuid == context.installation_uuid:
                raise SchemaMismatch("restored context must have a new installation UUID")
            updated = connection.execute(
                "UPDATE work_recovery_context SET execution_state=?,installation_uuid=?,"
                "source_installation_uuid=?,bundle_digest=?,restore_receipt=? "
                "WHERE singleton=1 AND execution_state=?",
                (
                    RECOVERY_STATE_BLOCKED_RESTORE,
                    installation_uuid,
                    context.installation_uuid,
                    bundle_digest,
                    restore_receipt,
                    RECOVERY_STATE_NORMAL,
                ),
            )
            if updated.rowcount != 1:
                raise SchemaMismatch("recovery context transition was not applied")
            self._reconcile_portable_restore_attempts(connection)
            return _stored_recovery_context(connection)

    def assert_execution_allowed(self, connection: sqlite3.Connection) -> RecoveryStoreContext:
        """Fail closed unless this verified installation remains explicitly normal."""
        self._verify(connection)
        context = _stored_recovery_context(connection)
        if context.execution_state != RECOVERY_STATE_NORMAL:
            raise SchemaMismatch("recovery context blocks execution")
        return context

    def _validate_managed_inbox_store_context(
        self, connection, *, expected: ManagedInboxStoreIdentity | None = None
    ) -> ManagedInboxStoreIdentity:
        """Validate this repository connection against server configuration and pin."""
        context = _stored_inbox_context(connection)
        try:
            from cli_agent_orchestrator import constants

            configured = _store_identity(constants.DATABASE_FILE)
        except (ImportError, ValueError) as error:
            raise WorkConflict("managed inbox configured database is unavailable") from error
        if (
            context.store_identity != _store_identity(self.path)
            or context.store_identity != configured
        ):
            raise WorkConflict("managed inbox configured database differs from work store")
        if expected is not None:
            if not isinstance(expected, ManagedInboxStoreIdentity):
                raise ValueError("verified managed inbox store context required")
            if context != expected:
                raise WorkConflict("managed inbox store UUID changed during this server context")
        return context

    def managed_inbox_store_context(
        self, *, expected: ManagedInboxStoreIdentity | None = None
    ) -> ManagedInboxStoreIdentity:
        """Open and validate a real read connection for the managed inbox pair."""
        with self.read_snapshot() as connection:
            return self._validate_managed_inbox_store_context(connection, expected=expected)

    def verify_schema(self) -> None:
        with self.connection() as connection:
            connection.execute("BEGIN")
            self._verify(connection)

    def _attach_dispatch_terminal(
        self, connection, attempt_id, generation, *, terminal_id, actor_id
    ):
        """Assign identity in the same transaction as the first sent transition."""
        _identity(terminal_id, "terminal_id")
        row = connection.execute("SELECT * FROM work_attempts WHERE id=?", (attempt_id,)).fetchone()
        if (
            row is None
            or row["generation"] != generation
            or row["state"] != "planned"
            or row["terminal_id"] not in (None, terminal_id)
        ):
            raise WorkConflict("terminal assignment requires the planned dispatch winner")
        if (
            connection.execute(
                "SELECT id FROM work_attempts WHERE terminal_id=? AND id<>?",
                (terminal_id, attempt_id),
            ).fetchone()
            is not None
        ):
            raise WorkConflict("terminal identity is already assigned to another attempt")
        connection.execute(
            "UPDATE work_attempts SET terminal_id=? WHERE id=?", (terminal_id, attempt_id)
        )
        job_id = connection.execute(
            "SELECT job_id FROM work_items WHERE id=?", (row["work_item_id"],)
        ).fetchone()[0]
        self._append_event(
            connection,
            job_id=job_id,
            work_item_id=row["work_item_id"],
            attempt_id=attempt_id,
            actor_id=actor_id,
            event_type="terminal.bound",
            metadata={"terminal_id": terminal_id},
        )

    def create_job(
        self,
        *,
        project_id: str,
        principal_id: str,
        allowed_providers: list[str],
        grant_id: str,
        budget: dict | None = None,
        priority: int = 0,
    ) -> dict:
        for label, value in (
            ("project_id", project_id),
            ("principal_id", principal_id),
            ("grant_id", grant_id),
        ):
            _identity(value, label)
        providers = sorted({_identity(provider, "provider") for provider in allowed_providers})
        if type(priority) is not int:
            raise ValueError("invalid priority")
        identifier, now = uuid4().hex, time.time()
        with self.transaction() as connection:
            self._verify(connection)
            connection.execute(
                "INSERT INTO work_jobs (id,project_id,principal_id,allowed_providers,grant_id,budget,priority,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    project_id,
                    principal_id,
                    _json(providers),
                    grant_id,
                    _json(budget or {}),
                    priority,
                    now,
                ),
            )
            connection.execute("INSERT INTO work_event_sequences(job_id) VALUES (?)", (identifier,))
            self._append_event(
                connection, job_id=identifier, event_type="job.created", actor_id=principal_id
            )
            return self._job(connection, identifier)

    def persist_process_identity(
        self,
        attempt_id: str,
        generation: int,
        identity_mapping: Mapping[str, object],
    ) -> None:
        """Persist one exact v3, argv-redacted process identity for an attempt.

        The canonical identity digest covers every field except
        ``identity_sha256`` itself. Repeating the same write is idempotent;
        replacing the identity for an attempt is rejected.
        """
        _identity(attempt_id, "attempt_id")
        if type(generation) is not int or generation <= 0:
            raise ValueError("process identity generation must be a positive integer")
        identity, digest = _validate_process_identity_v3(identity_mapping)
        identity_json = _json(identity)

        with self.transaction() as connection:
            self._verify(connection)
            attempt = connection.execute(
                "SELECT generation,state FROM work_attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if attempt is None:
                raise WorkConflict("process identity attempt does not exist")
            if attempt["generation"] != generation:
                raise WorkConflict("process identity generation does not match the attempt")
            if attempt["state"] not in {"sent", "acknowledged", "running"}:
                raise WorkConflict("process identity can only be stored for an active attempt")
            existing = connection.execute(
                "SELECT generation,protocol_version,identity_json,identity_sha256 "
                "FROM work_process_identities WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if existing is not None:
                if existing["generation"] != generation:
                    raise SchemaMismatch("stored Work process identity generation is corrupt")
                existing_identity, existing_digest = _read_stored_process_identity(existing)
                if existing_identity == identity and existing_digest == digest:
                    return
                raise WorkConflict("process identity is already recorded and cannot be replaced")

            connection.execute(
                "INSERT INTO work_process_identities "
                "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (attempt_id, generation, 3, identity_json, digest, time.time()),
            )

    def read_process_identity(
        self,
        attempt_id: str,
        generation: int,
    ) -> dict[str, object] | None:
        """Read one verified v3 identity; absent identity returns ``None``."""
        _identity(attempt_id, "attempt_id")
        if type(generation) is not int or generation <= 0:
            raise ValueError("process identity generation must be a positive integer")

        with self.read_snapshot() as connection:
            attempt = connection.execute(
                "SELECT generation FROM work_attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if attempt is None:
                raise WorkConflict("process identity attempt does not exist")
            if attempt["generation"] != generation:
                raise WorkConflict("process identity generation does not match the attempt")
            row = connection.execute(
                "SELECT generation,protocol_version,identity_json,identity_sha256 "
                "FROM work_process_identities WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                return None
            if row["generation"] != generation:
                raise SchemaMismatch("stored Work process identity generation is corrupt")
            identity, _digest = _read_stored_process_identity(row)
            return identity

    def persist_bubblewrap_process_identity(
        self, attempt_id: str, generation: int, identity_mapping: Mapping[str, object]
    ) -> None:
        """Record one exact, argv-redacted Bwrap process pair before worker release."""
        _identity(attempt_id, "attempt_id")
        if type(generation) is not int or generation <= 0:
            raise ValueError("Bubblewrap process identity generation is invalid")
        identity, digest = _validate_bubblewrap_identity(identity_mapping)
        with self.transaction() as connection:
            self._verify(connection)
            self._persist_bubblewrap_process_identity(
                connection, attempt_id, generation, identity, digest
            )

    @staticmethod
    def _persist_bubblewrap_process_identity(
        connection: sqlite3.Connection,
        attempt_id: str,
        generation: int,
        identity: Mapping[str, object],
        digest: str,
    ) -> None:
        if not connection.in_transaction:
            raise ValueError("caller-owned transaction required")
        attempt = connection.execute(
            "SELECT generation,state FROM work_attempts WHERE id=?", (attempt_id,)
        ).fetchone()
        if attempt is None or attempt["generation"] != generation:
            raise WorkConflict("Bubblewrap process identity attempt or generation changed")
        if attempt["state"] not in {"sent", "acknowledged", "running"}:
            raise WorkConflict("Bubblewrap process identity requires an active attempt")
        existing = connection.execute(
            "SELECT generation,protocol_version,identity_json,identity_sha256 "
            "FROM work_bubblewrap_process_identities WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        if existing is not None:
            if existing["generation"] != generation:
                raise SchemaMismatch("stored Bubblewrap process identity generation is corrupt")
            stored, stored_digest = _read_stored_bubblewrap_identity(existing)
            if stored == identity and stored_digest == digest:
                return
            raise WorkConflict("Bubblewrap process identity is already recorded")
        connection.execute(
            "INSERT INTO work_bubblewrap_process_identities "
            "(attempt_id,generation,protocol_version,identity_json,identity_sha256,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (attempt_id, generation, 1, _json(identity), digest, time.time()),
        )

    def read_bubblewrap_process_identity(
        self, attempt_id: str, generation: int
    ) -> dict[str, object] | None:
        """Load and verify the Bwrap identity through a closed SQLite snapshot."""
        _identity(attempt_id, "attempt_id")
        if type(generation) is not int or generation <= 0:
            raise ValueError("Bubblewrap process identity generation is invalid")
        with self.read_snapshot() as connection:
            attempt = connection.execute(
                "SELECT generation FROM work_attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if attempt is None or attempt["generation"] != generation:
                raise WorkConflict("Bubblewrap process identity attempt or generation changed")
            row = connection.execute(
                "SELECT generation,protocol_version,identity_json,identity_sha256 "
                "FROM work_bubblewrap_process_identities WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                return None
            if row["generation"] != generation:
                raise SchemaMismatch("stored Bubblewrap process identity generation is corrupt")
            identity, _digest = _read_stored_bubblewrap_identity(row)
            return identity

    @contextmanager
    def cleanup_owner_lock(self, attempt_id: str, generation: int) -> Iterator[None]:
        """Exclude another live cleanup owner without holding a SQLite transaction.

        The lock file may remain after a crash. The kernel releases its flock
        when the last inherited open-file description closes, so an ordinary
        owner exit permits pending cleanup to resume. A surviving forked child
        can delay recovery; the lock remains held and cleanup fails closed.
        """
        _identity(attempt_id, "attempt_id")
        if type(generation) is not int or generation <= 0:
            raise WorkConflict("cleanup owner generation is invalid")
        try:
            import fcntl
        except ImportError as exc:
            raise WorkConflict("cleanup owner lock is unavailable: fcntl is missing") from exc

        digest = hashlib.sha256(f"{attempt_id}\0{generation}".encode("utf-8")).hexdigest()
        database = self.path.resolve()
        lock_path = database.with_name(f"{database.name}.cleanup-{digest}.lock")
        descriptor = None
        locked = False
        try:
            descriptor = os.open(
                lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
            )
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise WorkConflict("cleanup owner lock file is not private")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except BlockingIOError as exc:
                raise WorkConflict("cleanup is owned by another worker") from exc
        except OSError as exc:
            raise WorkConflict(f"cleanup owner lock is unavailable: {exc}") from exc
        finally:
            if descriptor is not None and not locked:
                os.close(descriptor)
        try:
            yield
        finally:
            os.close(descriptor)

    @staticmethod
    def _job(connection: sqlite3.Connection, job_id: str) -> dict:
        row = connection.execute("SELECT * FROM work_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("job not found")
        result = dict(row)
        for field in ("allowed_providers", "budget"):
            result[field] = json.loads(result[field])
        return result

    def get_job(self, job_id: str) -> dict:
        with self.connection() as connection:
            return self._job(connection, job_id)

    @staticmethod
    def _work(connection: sqlite3.Connection, work_item_id: str) -> dict:
        row = connection.execute("SELECT * FROM work_items WHERE id=?", (work_item_id,)).fetchone()
        if row is None:
            raise KeyError("work item not found")
        result = dict(row)
        result["attempts"] = [
            dict(attempt)
            for attempt in connection.execute(
                "SELECT * FROM work_attempts WHERE work_item_id=? ORDER BY attempt_number",
                (work_item_id,),
            )
        ]
        return result

    def get_work(self, work_item_id: str) -> dict:
        with self.connection() as connection:
            connection.execute("BEGIN")
            return self._work(connection, work_item_id)

    def admit_work(
        self,
        *,
        job_id: str,
        operation_kind: str,
        idempotency_key: str,
        request_hash: str,
        contract_id: str,
        snapshot_id: str | None,
        provider: str,
        actor_id: str,
        lease_seconds: float = 60,
        parent_work_item_id: str | None = None,
    ) -> dict:
        """Run admit_work in its own verified transaction."""
        with self.transaction() as connection:
            self._verify(connection)
            if _requires_managed_lineage(
                operation_kind=operation_kind, parent_work_item_id=parent_work_item_id
            ):
                existing = connection.execute(
                    "SELECT id FROM work_items WHERE job_id=? AND operation_kind=? "
                    "AND idempotency_key=?",
                    (job_id, operation_kind, idempotency_key),
                ).fetchone()
                if existing is None:
                    raise WorkConflict("managed lineage origin required")
                work = self._admit_work(
                    connection,
                    job_id=job_id,
                    operation_kind=operation_kind,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    contract_id=contract_id,
                    snapshot_id=snapshot_id,
                    provider=provider,
                    actor_id=actor_id,
                    lease_seconds=lease_seconds,
                    parent_work_item_id=parent_work_item_id,
                )
                if work["lineage_protocol"] != "legacy":
                    raise WorkConflict("managed lineage must use its internal origin")
                return work
            return self._admit_work(
                connection,
                job_id=job_id,
                operation_kind=operation_kind,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                contract_id=contract_id,
                snapshot_id=snapshot_id,
                provider=provider,
                actor_id=actor_id,
                lease_seconds=lease_seconds,
                parent_work_item_id=parent_work_item_id,
            )

    def _admit_work(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: str,
        operation_kind: str,
        idempotency_key: str,
        request_hash: str,
        contract_id: str,
        snapshot_id: str | None,
        provider: str,
        actor_id: str,
        lease_seconds: float = 60,
        parent_work_item_id: str | None = None,
    ) -> dict:
        """Persist a logical operation and planned attempt; never start a backend.

        This is a trusted repository boundary, not a public authorization API.
        Authority and resource checks must precede dispatch in the owning service.
        """
        if not connection.in_transaction:
            raise ValueError("caller-owned transaction required")
        for label, value in (
            ("operation_kind", operation_kind),
            ("idempotency_key", idempotency_key),
            ("contract_id", contract_id),
            ("provider", provider),
            ("actor_id", actor_id),
        ):
            _identity(value, label)
        if snapshot_id is not None:
            _identity(snapshot_id, "snapshot_id")
        if (
            not isinstance(request_hash, str)
            or len(request_hash) != 64
            or any(c not in "0123456789abcdef" for c in request_hash)
        ):
            raise ValueError("request_hash must be lowercase SHA-256")
        if (
            isinstance(lease_seconds, bool)
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
        ):
            raise ValueError("lease_seconds must be finite and positive")
        job = self._job(connection, job_id)
        if provider not in job["allowed_providers"]:
            raise WorkConflict("provider not allowed by job")
        row = connection.execute(
            "SELECT id FROM work_items WHERE job_id=? AND operation_kind=? AND idempotency_key=?",
            (job_id, operation_kind, idempotency_key),
        ).fetchone()
        if row is not None:
            existing = self._work(connection, row["id"])
            expected = (request_hash, contract_id, snapshot_id, parent_work_item_id, provider)
            actual = (
                existing["request_hash"],
                existing["contract_id"],
                existing["snapshot_id"],
                existing["parent_work_item_id"],
                existing["attempts"][0]["provider"],
            )
            if actual != expected:
                raise WorkConflict("idempotency key reused with a different request")
            return existing
        if job["state"] in {"revoked", "completed", "failed"}:
            raise WorkConflict("job no longer admits work")
        self._assert_offline_cut_allows_writer(connection)
        if parent_work_item_id is not None:
            parent = connection.execute(
                "SELECT job_id,state FROM work_items WHERE id=?", (parent_work_item_id,)
            ).fetchone()
            if (
                parent is None
                or parent["job_id"] != job_id
                or parent["state"] in {"succeeded", "failed", "cancelled"}
            ):
                raise WorkConflict("parent must be active in the same job")
        identifier, attempt_id, now = uuid4().hex, uuid4().hex, time.time()
        connection.execute(
            "INSERT INTO work_items (id,job_id,parent_work_item_id,operation_kind,idempotency_key,request_hash,contract_id,snapshot_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                job_id,
                parent_work_item_id,
                operation_kind,
                idempotency_key,
                request_hash,
                contract_id,
                snapshot_id,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO work_attempts (id,work_item_id,attempt_number,generation,provider,lease_expires_at,created_at) VALUES (?,?,1,1,?,?,?)",
            (attempt_id, identifier, provider, now + lease_seconds, now),
        )
        self._append_event(
            connection,
            job_id=job_id,
            work_item_id=identifier,
            attempt_id=attempt_id,
            event_type="work.admitted",
            actor_id=actor_id,
        )
        return self._work(connection, identifier)

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        event_type: str,
        actor_id: str,
        work_item_id: str | None = None,
        attempt_id: str | None = None,
        event_id: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        event_id = event_id or uuid4().hex
        payload = _json(metadata or {})
        previous = connection.execute(
            "SELECT * FROM work_events WHERE event_id=?", (event_id,)
        ).fetchone()
        if previous:
            expected = (job_id, event_type, actor_id, work_item_id, attempt_id, payload)
            actual = tuple(
                previous[key]
                for key in (
                    "job_id",
                    "event_type",
                    "actor_id",
                    "work_item_id",
                    "attempt_id",
                    "metadata",
                )
            )
            if actual != expected:
                raise WorkConflict("event_id reused with different data")
            return dict(previous)
        connection.execute(
            "UPDATE work_event_sequences SET high_water=high_water+1 WHERE job_id=?", (job_id,)
        )
        sequence = connection.execute(
            "SELECT high_water FROM work_event_sequences WHERE job_id=?", (job_id,)
        ).fetchone()
        if sequence is None:
            raise KeyError("job event sequence not found")
        connection.execute(
            "INSERT INTO work_events (event_id,job_id,work_item_id,attempt_id,sequence,event_type,actor_id,occurred_at,metadata) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                job_id,
                work_item_id,
                attempt_id,
                sequence[0],
                event_type,
                actor_id,
                time.time(),
                payload,
            ),
        )
        return dict(
            connection.execute("SELECT * FROM work_events WHERE event_id=?", (event_id,)).fetchone()
        )

    def read_events(self, job_id: str, *, after_sequence: int = 0, limit: int = 100) -> dict:
        with self.connection() as connection:
            connection.execute("BEGIN")
            return self._read_events(connection, job_id, after_sequence=after_sequence, limit=limit)

    @staticmethod
    def _read_events(connection, job_id: str, *, after_sequence: int = 0, limit: int = 100) -> dict:
        if (
            type(after_sequence) is not int
            or after_sequence < 0
            or type(limit) is not int
            or not 1 <= limit <= 1000
        ):
            raise ValueError("invalid event cursor or limit")
        head = connection.execute(
            "SELECT * FROM work_event_sequences WHERE job_id=?", (job_id,)
        ).fetchone()
        if head is None:
            raise KeyError("job not found")
        if after_sequence > head["high_water"]:
            raise WorkConflict("cursor exceeds job high water")
        rows = connection.execute(
            "SELECT * FROM work_events WHERE job_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (job_id, after_sequence, limit),
        ).fetchall()
        events = [dict(row) for row in rows]
        for event in events:
            event["metadata"] = json.loads(event["metadata"])
        floor = head["retained_after"]
        gaps = (
            [{"from_sequence": after_sequence + 1, "through_sequence": floor}]
            if after_sequence < floor
            else []
        )
        return dict(
            schema_version=1,
            events=events,
            high_water=head["high_water"],
            gaps=gaps,
            next_cursor=events[-1]["sequence"] if events else max(after_sequence, floor),
        )

    def prune_events(self, job_id: str, *, through_sequence: int) -> None:
        """Retention is explicit and monotonic; it never changes work state."""
        if type(through_sequence) is not int or through_sequence < 0:
            raise ValueError("invalid retention cursor")
        with self.transaction() as connection:
            self._verify(connection)
            head = connection.execute(
                "SELECT * FROM work_event_sequences WHERE job_id=?", (job_id,)
            ).fetchone()
            if head is None:
                raise KeyError("job not found")
            if through_sequence > head["high_water"]:
                raise WorkConflict("retention exceeds job high water")
            connection.execute(
                "DELETE FROM work_events WHERE job_id=? AND sequence<=?", (job_id, through_sequence)
            )
            connection.execute(
                "UPDATE work_event_sequences SET retained_after=max(retained_after,?) WHERE job_id=?",
                (through_sequence, job_id),
            )

    def transition_attempt(
        self,
        *,
        attempt_id: str,
        generation: int,
        expected_revision: int,
        expected_state: str,
        target: str,
        actor_id: str,
        event_id: str,
        evidence: TransitionEvidence,
    ) -> dict:
        """Run transition_attempt in its own verified transaction."""
        with self.transaction() as connection:
            self._verify(connection)
            return self._transition_attempt(
                connection,
                attempt_id=attempt_id,
                generation=generation,
                expected_revision=expected_revision,
                expected_state=expected_state,
                target=target,
                actor_id=actor_id,
                event_id=event_id,
                evidence=evidence,
            )

    def _task_received_receipt_replay(
        self,
        connection: sqlite3.Connection,
        *,
        attempt_id: str,
        generation: int,
        nonce: str,
        receipt_hash: str,
    ) -> dict | None:
        """Return only an identical durable receipt; contradictions fail closed.

        This deliberately precedes runtime-proof verification so an exact receipt
        can be replayed after restart even though its in-memory sealing key has
        rotated.  It can never create a new acknowledgement on that path.
        """
        _identity(attempt_id, "task receipt attempt")
        _identity(nonce, "task receipt nonce")
        if type(generation) is not int or generation <= 0:
            raise WorkConflict("task receipt generation is invalid")
        if (
            not isinstance(receipt_hash, str)
            or len(receipt_hash) != 64
            or any(character not in "0123456789abcdef" for character in receipt_hash)
        ):
            raise WorkConflict("task receipt hash is invalid")
        receipt = connection.execute(
            "SELECT * FROM work_task_received_receipts WHERE attempt_id=? AND generation=?",
            (attempt_id, generation),
        ).fetchone()
        if receipt is not None:
            if (receipt["nonce"], receipt["receipt_hash"]) != (nonce, receipt_hash):
                raise WorkConflict("task receipt contradicts durable receipt history")
            return self._work(connection, receipt["work_item_id"])
        nonce_owner = connection.execute(
            "SELECT attempt_id,generation FROM work_task_received_receipts WHERE nonce=?", (nonce,)
        ).fetchone()
        if nonce_owner is not None:
            raise WorkConflict("task receipt nonce is already bound to another attempt")
        return None

    def _record_task_received_receipt(
        self,
        connection: sqlite3.Connection,
        *,
        attempt_id: str,
        generation: int,
        attempt_revision: int,
        work_item_id: str,
        receiver_subject_id: str,
        receiver_subject_revision: int,
        receiver_authorization_kind: str,
        receiver_authorization_revision: int,
        receiver_grant_id: str,
        receiver_grant_revision: int,
        delivery_id: str,
        delivery_hash: str,
        nonce: str,
        receipt_hash: str,
    ) -> dict:
        """Persist one already-authenticated receipt before its ACK transition.

        Callers must have revalidated the receipt through ``WorkOrigins`` in
        this same transaction.  The repository repeats every durable identity
        comparison here so a caller cannot redirect a valid receipt to another
        attempt, receiver, or delivery.
        """
        if not connection.in_transaction:
            raise ValueError("caller-owned transaction required")
        replay = self._task_received_receipt_replay(
            connection,
            attempt_id=attempt_id,
            generation=generation,
            nonce=nonce,
            receipt_hash=receipt_hash,
        )
        if replay is not None:
            return replay
        for label, value in (
            ("task receipt work", work_item_id),
            ("task receipt receiver", receiver_subject_id),
            ("task receipt grant", receiver_grant_id),
            ("task receipt delivery", delivery_id),
        ):
            _identity(value, label)
        if receiver_authorization_kind != "receiver":
            raise WorkConflict("task receipt authorization kind is invalid")
        for label, value in (
            ("task receipt attempt revision", attempt_revision),
            ("task receipt receiver revision", receiver_subject_revision),
            ("task receipt authorization revision", receiver_authorization_revision),
            ("task receipt grant revision", receiver_grant_revision),
        ):
            if type(value) is not int or value <= 0:
                raise WorkConflict(f"{label} is invalid")
        if (
            not isinstance(delivery_hash, str)
            or len(delivery_hash) != 64
            or any(character not in "0123456789abcdef" for character in delivery_hash)
        ):
            raise WorkConflict("task receipt delivery hash is invalid")
        origin = connection.execute(
            "SELECT * FROM work_child_origin_bindings WHERE child_attempt_id=? "
            "AND child_generation=?",
            (attempt_id, generation),
        ).fetchone()
        if origin is None:
            raise WorkConflict("task receipt has no managed lineage binding")
        expected_origin = (
            work_item_id,
            receiver_subject_id,
            receiver_subject_revision,
            receiver_authorization_kind,
            receiver_authorization_revision,
            receiver_grant_id,
            receiver_grant_revision,
            delivery_id,
            delivery_hash,
        )
        actual_origin = (
            origin["child_work_item_id"],
            origin["receiver_subject_id"],
            origin["receiver_subject_revision"],
            origin["receiver_authorization_kind"],
            origin["receiver_authorization_revision"],
            origin["receiver_grant_id"],
            origin["receiver_grant_revision"],
            origin["delivery_id"],
            origin["delivery_hash"],
        )
        if actual_origin != expected_origin:
            raise WorkConflict("task receipt does not match its managed delivery")
        attempt = connection.execute(
            "SELECT * FROM work_attempts WHERE id=?", (attempt_id,)
        ).fetchone()
        if attempt is None:
            raise WorkConflict("task receipt attempt disappeared")
        work = self._work(connection, attempt["work_item_id"])
        if (
            attempt["work_item_id"] != work_item_id
            or work["attempts"][-1]["id"] != attempt_id
            or attempt["generation"] != generation
            or attempt["revision"] != attempt_revision
            or attempt["state"] != "sent"
        ):
            raise WorkConflict("task receipt attempt is stale or no longer sent")
        job = self._job(connection, work["job_id"])
        if (
            job["state"] in {"revoked", "completed", "failed"}
            or attempt["lease_expires_at"] <= time.time()
        ):
            raise WorkConflict("task receipt cannot acknowledge an inactive attempt")
        try:
            next_attempt = delivery_transition(
                attempt["state"],
                "acknowledged",
                evidence=TransitionEvidence(
                    generation=generation,
                    expected_generation=generation,
                    task_received=True,
                ),
            ).value
        except TransitionConflict as error:
            raise WorkConflict("task receipt acknowledgement is not a valid transition") from error
        connection.execute(
            """INSERT INTO work_task_received_receipts
               (attempt_id,generation,schema_version,work_item_id,job_id,attempt_revision,
                receiver_subject_id,receiver_subject_revision,receiver_authorization_kind,
                receiver_authorization_revision,receiver_grant_id,receiver_grant_revision,
                delivery_id,delivery_hash,nonce,receipt_hash,received_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                attempt_id,
                generation,
                1,
                work_item_id,
                work["job_id"],
                attempt_revision,
                receiver_subject_id,
                receiver_subject_revision,
                receiver_authorization_kind,
                receiver_authorization_revision,
                receiver_grant_id,
                receiver_grant_revision,
                delivery_id,
                delivery_hash,
                nonce,
                receipt_hash,
                time.time(),
            ),
        )
        self._commit_checked_transition(
            connection,
            work=work,
            attempt=attempt,
            next_work=work["state"],
            next_attempt=next_attempt,
            actor_id=receiver_subject_id,
            event_id=nonce,
            fingerprint=receipt_hash,
            event_type="attempt.acknowledged",
        )
        return self._work(connection, work_item_id)

    def _record_workflow_step_receiver_credential(
        self,
        connection: sqlite3.Connection,
        *,
        binding_id: str,
        attempt_id: str,
        generation: int,
        schema_version: int,
        job_id: str,
        work_item_id: str,
        receiver_subject_id: str,
        receiver_subject_revision: int,
        receiver_authorization_revision: int,
        receiver_grant_id: str,
        receiver_grant_revision: int,
        installation_uuid: str,
        attempt_revision: int,
        lease_expires_at: float,
        expires_at: float,
        delivery_id: str,
        delivery_hash: str,
        credential_sha256: str,
        issued_at: float,
    ) -> None:
        """Store the receiver digest against the exact immutable workflow binding."""
        if not connection.in_transaction:
            raise WorkConflict("workflow receiver credential requires a caller transaction")
        self._verify(connection)
        for label, value in (
            ("workflow binding", binding_id),
            ("workflow Work attempt", attempt_id),
            ("workflow Work item", work_item_id),
            ("workflow job", job_id),
            ("workflow receiver", receiver_subject_id),
            ("workflow receiver grant", receiver_grant_id),
            ("workflow delivery", delivery_id),
        ):
            _identity(value, label)
        if schema_version != 1 or type(schema_version) is not int:
            raise WorkConflict("workflow receiver credential schema version is invalid")
        for label, value in (
            ("workflow Work generation", generation),
            ("workflow receiver subject revision", receiver_subject_revision),
            ("workflow receiver authorization revision", receiver_authorization_revision),
            ("workflow receiver grant revision", receiver_grant_revision),
            ("workflow Work attempt revision", attempt_revision),
        ):
            if type(value) is not int or value <= 0:
                raise WorkConflict(f"{label} is invalid")
        for label, value in (
            ("workflow delivery hash", delivery_hash),
            ("workflow receiver credential digest", credential_sha256),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise WorkConflict(f"{label} is invalid")
        if (
            not isinstance(installation_uuid, str)
            or len(installation_uuid) != 32
            or any(character not in "0123456789abcdef" for character in installation_uuid)
        ):
            raise WorkConflict("workflow receiver installation identity is invalid")
        if (
            isinstance(lease_expires_at, bool)
            or not isinstance(lease_expires_at, (int, float))
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
            or isinstance(issued_at, bool)
            or not isinstance(issued_at, (int, float))
            or issued_at <= 0
            or expires_at <= issued_at
            or expires_at > lease_expires_at
            or lease_expires_at <= 0
        ):
            raise WorkConflict("workflow receiver credential expiry is invalid")
        binding = connection.execute(
            "SELECT * FROM work_workflow_step_bindings WHERE binding_id=?",
            (binding_id,),
        ).fetchone()
        if binding is None or (
            binding["work_attempt_id"],
            binding["work_generation"],
            binding["job_id"],
            binding["work_item_id"],
            binding["receiver_subject_id"],
            binding["receiver_subject_revision"],
            binding["receiver_authorization_revision"],
            binding["receiver_grant_id"],
            binding["receiver_grant_revision"],
            binding["delivery_id"],
            binding["delivery_hash"],
        ) != (
            attempt_id,
            generation,
            job_id,
            work_item_id,
            receiver_subject_id,
            receiver_subject_revision,
            receiver_authorization_revision,
            receiver_grant_id,
            receiver_grant_revision,
            delivery_id,
            delivery_hash,
        ):
            raise WorkConflict("workflow receiver credential differs from its immutable binding")
        attempt = connection.execute(
            "SELECT state,revision,lease_expires_at,work_item_id FROM work_attempts "
            "WHERE id=? AND generation=?",
            (attempt_id, generation),
        ).fetchone()
        issued = connection.execute(
            "SELECT installation_uuid,attempt_revision,lease_expires_at,expires_at "
            "FROM work_attempt_credentials WHERE attempt_id=? AND generation=?",
            (attempt_id, generation),
        ).fetchone()
        if (
            attempt is None
            or attempt["state"] != "sent"
            or attempt["revision"] != attempt_revision
            or attempt["work_item_id"] != work_item_id
            or attempt["lease_expires_at"] != lease_expires_at
            or issued is None
            or issued["installation_uuid"] != installation_uuid
            or issued["attempt_revision"] != attempt_revision
            or issued["lease_expires_at"] != lease_expires_at
            or issued["expires_at"] != expires_at
        ):
            raise WorkConflict("workflow receiver credential is not for the current sent attempt")
        connection.execute(
            "INSERT INTO work_workflow_step_receiver_credentials "
            "(binding_id,attempt_id,generation,schema_version,job_id,work_item_id,"
            "receiver_subject_id,receiver_subject_revision,receiver_authorization_revision,"
            "receiver_grant_id,receiver_grant_revision,installation_uuid,attempt_revision,"
            "lease_expires_at,expires_at,delivery_id,delivery_hash,credential_sha256,issued_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                binding_id,
                attempt_id,
                generation,
                schema_version,
                job_id,
                work_item_id,
                receiver_subject_id,
                receiver_subject_revision,
                receiver_authorization_revision,
                receiver_grant_id,
                receiver_grant_revision,
                installation_uuid,
                attempt_revision,
                lease_expires_at,
                expires_at,
                delivery_id,
                delivery_hash,
                credential_sha256,
                issued_at,
            ),
        )

    def _record_workflow_step_receiver_acceptance(
        self,
        connection: sqlite3.Connection,
        *,
        binding_id: str,
        attempt_id: str,
        generation: int,
        receiver_subject_id: str,
        receiver_subject_revision: int,
        receiver_authorization_kind: str,
        receiver_authorization_revision: int,
        delivery_id: str,
        delivery_hash: str,
        acceptance_sha256: str,
        accepted_at: float,
        schema_version: int,
    ) -> dict:
        """Persist one acceptance digest after its live receiver authority check."""
        if not connection.in_transaction:
            raise WorkConflict("workflow receiver acceptance requires a caller transaction")
        self._verify(connection)
        for label, value in (
            ("workflow binding", binding_id),
            ("workflow Work attempt", attempt_id),
            ("workflow receiver", receiver_subject_id),
            ("workflow delivery", delivery_id),
        ):
            _identity(value, label)
        if type(generation) is not int or generation <= 0:
            raise WorkConflict("workflow receiver acceptance generation is invalid")
        for label, value in (
            ("workflow receiver subject revision", receiver_subject_revision),
            ("workflow receiver authorization revision", receiver_authorization_revision),
        ):
            if type(value) is not int or value <= 0:
                raise WorkConflict(f"{label} is invalid")
        if receiver_authorization_kind != "receiver" or schema_version != 1:
            raise WorkConflict("workflow receiver acceptance version or authority is invalid")
        for label, value in (
            ("workflow delivery hash", delivery_hash),
            ("workflow acceptance digest", acceptance_sha256),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise WorkConflict(f"{label} is invalid")
        if (
            isinstance(accepted_at, bool)
            or not isinstance(accepted_at, (int, float))
            or accepted_at <= 0
        ):
            raise WorkConflict("workflow receiver acceptance timestamp is invalid")
        binding = connection.execute(
            "SELECT * FROM work_workflow_step_bindings WHERE binding_id=?",
            (binding_id,),
        ).fetchone()
        if binding is None or (
            binding["work_attempt_id"],
            binding["work_generation"],
            binding["receiver_subject_id"],
            binding["receiver_subject_revision"],
            binding["receiver_authorization_revision"],
            binding["delivery_id"],
            binding["delivery_hash"],
        ) != (
            attempt_id,
            generation,
            receiver_subject_id,
            receiver_subject_revision,
            receiver_authorization_revision,
            delivery_id,
            delivery_hash,
        ):
            raise WorkConflict("workflow receiver acceptance differs from its immutable binding")
        attempt = connection.execute(
            "SELECT state,work_item_id FROM work_attempts WHERE id=? AND generation=?",
            (attempt_id, generation),
        ).fetchone()
        if (
            attempt is None
            or attempt["state"] != "sent"
            or attempt["work_item_id"] != binding["work_item_id"]
        ):
            raise WorkConflict("workflow receiver acceptance requires the bound sent attempt")
        prior = connection.execute(
            "SELECT * FROM work_workflow_step_receiver_acceptances "
            "WHERE attempt_id=? AND generation=?",
            (attempt_id, generation),
        ).fetchone()
        expected = (
            binding_id,
            receiver_subject_id,
            receiver_subject_revision,
            receiver_authorization_kind,
            receiver_authorization_revision,
            delivery_id,
            delivery_hash,
            acceptance_sha256,
        )
        if prior is not None:
            actual = (
                prior["binding_id"],
                prior["receiver_subject_id"],
                prior["receiver_subject_revision"],
                prior["receiver_authorization_kind"],
                prior["receiver_authorization_revision"],
                prior["delivery_id"],
                prior["delivery_hash"],
                prior["acceptance_sha256"],
            )
            if actual != expected:
                raise WorkConflict("workflow receiver acceptance replay contradicts its history")
            return dict(prior)
        connection.execute(
            "INSERT INTO work_workflow_step_receiver_acceptances "
            "(binding_id,attempt_id,generation,receiver_subject_id,receiver_subject_revision,"
            "receiver_authorization_kind,receiver_authorization_revision,delivery_id,delivery_hash,"
            "acceptance_sha256,accepted_at,schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                binding_id,
                attempt_id,
                generation,
                receiver_subject_id,
                receiver_subject_revision,
                receiver_authorization_kind,
                receiver_authorization_revision,
                delivery_id,
                delivery_hash,
                acceptance_sha256,
                accepted_at,
                schema_version,
            ),
        )
        return dict(
            connection.execute(
                "SELECT * FROM work_workflow_step_receiver_acceptances "
                "WHERE attempt_id=? AND generation=?",
                (attempt_id, generation),
            ).fetchone()
        )

    def _workflow_step_task_received_receipt(
        self, connection: sqlite3.Connection, attempt_id: str, generation: int
    ) -> dict | None:
        if not connection.in_transaction:
            raise WorkConflict("workflow task receipt reads require a stable transaction")
        _identity(attempt_id, "workflow task receipt attempt")
        if type(generation) is not int or generation <= 0:
            raise WorkConflict("workflow task receipt generation is invalid")
        row = connection.execute(
            "SELECT * FROM work_workflow_step_task_received_receipts "
            "WHERE attempt_id=? AND generation=?",
            (attempt_id, generation),
        ).fetchone()
        if row is None:
            return None
        for field in ("receipt_hash", "delivery_hash"):
            value = row[field]
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise WorkConflict("workflow task receipt digest is corrupt")
        binding = connection.execute(
            "SELECT * FROM work_workflow_step_bindings WHERE binding_id=?",
            (row["binding_id"],),
        ).fetchone()
        if binding is None or (
            binding["work_attempt_id"],
            binding["work_generation"],
            binding["work_item_id"],
            binding["job_id"],
            binding["receiver_subject_id"],
            binding["receiver_subject_revision"],
            binding["receiver_authorization_revision"],
            binding["receiver_grant_id"],
            binding["receiver_grant_revision"],
            binding["delivery_id"],
            binding["delivery_hash"],
        ) != (
            row["attempt_id"],
            row["generation"],
            row["work_item_id"],
            row["job_id"],
            row["receiver_subject_id"],
            row["receiver_subject_revision"],
            row["receiver_authorization_revision"],
            row["receiver_grant_id"],
            row["receiver_grant_revision"],
            row["delivery_id"],
            row["delivery_hash"],
        ):
            raise WorkConflict("workflow task receipt differs from its binding")
        return dict(row)

    def _record_workflow_step_task_received_receipt(
        self,
        connection: sqlite3.Connection,
        *,
        binding_id: str,
        attempt_id: str,
        generation: int,
        schema_version: int,
        work_item_id: str,
        job_id: str,
        attempt_revision: int,
        receiver_subject_id: str,
        receiver_subject_revision: int,
        receiver_authorization_kind: str,
        receiver_authorization_revision: int,
        receiver_grant_id: str,
        receiver_grant_revision: int,
        delivery_id: str,
        delivery_hash: str,
        nonce: str,
        receipt_hash: str,
        received_at: float,
    ) -> dict:
        """Commit the immutable receiver receipt and Work ACK transition together."""
        if not connection.in_transaction:
            raise WorkConflict("workflow task receipt requires a caller transaction")
        self._verify(connection)
        for label, value in (
            ("workflow binding", binding_id),
            ("workflow Work attempt", attempt_id),
            ("workflow Work item", work_item_id),
            ("workflow job", job_id),
            ("workflow receiver", receiver_subject_id),
            ("workflow receiver grant", receiver_grant_id),
            ("workflow delivery", delivery_id),
            ("workflow task receipt nonce", nonce),
        ):
            _identity(value, label)
        if schema_version != 1 or type(schema_version) is not int:
            raise WorkConflict("workflow task receipt schema version is invalid")
        for label, value in (
            ("workflow Work generation", generation),
            ("workflow attempt revision", attempt_revision),
            ("workflow receiver revision", receiver_subject_revision),
            ("workflow receiver authorization revision", receiver_authorization_revision),
            ("workflow receiver grant revision", receiver_grant_revision),
        ):
            if type(value) is not int or value <= 0:
                raise WorkConflict(f"{label} is invalid")
        if receiver_authorization_kind != "receiver":
            raise WorkConflict("workflow task receipt authority kind is invalid")
        for label, value in (
            ("workflow delivery hash", delivery_hash),
            ("workflow receipt hash", receipt_hash),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise WorkConflict(f"{label} is invalid")
        if (
            isinstance(received_at, bool)
            or not isinstance(received_at, (int, float))
            or received_at <= 0
        ):
            raise WorkConflict("workflow task receipt timestamp is invalid")
        identity = (
            binding_id,
            work_item_id,
            job_id,
            attempt_revision,
            receiver_subject_id,
            receiver_subject_revision,
            receiver_authorization_kind,
            receiver_authorization_revision,
            receiver_grant_id,
            receiver_grant_revision,
            delivery_id,
            delivery_hash,
        )
        prior = self._workflow_step_task_received_receipt(connection, attempt_id, generation)
        if prior is not None:
            if (
                prior["nonce"] != nonce
                or prior["receipt_hash"] != receipt_hash
                or (
                    prior["binding_id"],
                    prior["work_item_id"],
                    prior["job_id"],
                    prior["attempt_revision"],
                    prior["receiver_subject_id"],
                    prior["receiver_subject_revision"],
                    prior["receiver_authorization_kind"],
                    prior["receiver_authorization_revision"],
                    prior["receiver_grant_id"],
                    prior["receiver_grant_revision"],
                    prior["delivery_id"],
                    prior["delivery_hash"],
                )
                != identity
            ):
                raise WorkConflict("workflow task receipt contradicts immutable receipt history")
            return self._work(connection, work_item_id)
        binding = connection.execute(
            "SELECT * FROM work_workflow_step_bindings WHERE binding_id=?",
            (binding_id,),
        ).fetchone()
        acceptance = connection.execute(
            "SELECT * FROM work_workflow_step_receiver_acceptances "
            "WHERE attempt_id=? AND generation=?",
            (attempt_id, generation),
        ).fetchone()
        attempt = connection.execute(
            "SELECT * FROM work_attempts WHERE id=? AND generation=?",
            (attempt_id, generation),
        ).fetchone()
        if (
            binding is None
            or (
                binding["work_attempt_id"],
                binding["work_generation"],
                binding["work_item_id"],
                binding["job_id"],
                binding["receiver_subject_id"],
                binding["receiver_subject_revision"],
                binding["receiver_authorization_revision"],
                binding["receiver_grant_id"],
                binding["receiver_grant_revision"],
                binding["delivery_id"],
                binding["delivery_hash"],
            )
            != (
                attempt_id,
                generation,
                work_item_id,
                job_id,
                receiver_subject_id,
                receiver_subject_revision,
                receiver_authorization_revision,
                receiver_grant_id,
                receiver_grant_revision,
                delivery_id,
                delivery_hash,
            )
            or acceptance is None
            or acceptance["binding_id"] != binding_id
            or acceptance["acceptance_sha256"] is None
            or attempt is None
            or attempt["work_item_id"] != work_item_id
            or attempt["state"] != "sent"
            or attempt["revision"] != attempt_revision
            or attempt["lease_expires_at"] <= received_at
        ):
            raise WorkConflict(
                "workflow task receipt requires its exact live acceptance and sent attempt"
            )
        work = self._work(connection, work_item_id)
        if (
            work["job_id"] != job_id
            or work["attempts"][-1]["id"] != attempt_id
            or work["attempts"][-1]["generation"] != generation
        ):
            raise WorkConflict("workflow task receipt attempt is no longer current")
        connection.execute(
            "INSERT INTO work_workflow_step_task_received_receipts "
            "(binding_id,attempt_id,generation,schema_version,work_item_id,job_id,attempt_revision,"
            "receiver_subject_id,receiver_subject_revision,receiver_authorization_kind,"
            "receiver_authorization_revision,receiver_grant_id,receiver_grant_revision,delivery_id,"
            "delivery_hash,nonce,receipt_hash,received_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                binding_id,
                attempt_id,
                generation,
                schema_version,
                work_item_id,
                job_id,
                attempt_revision,
                receiver_subject_id,
                receiver_subject_revision,
                receiver_authorization_kind,
                receiver_authorization_revision,
                receiver_grant_id,
                receiver_grant_revision,
                delivery_id,
                delivery_hash,
                nonce,
                receipt_hash,
                received_at,
            ),
        )
        try:
            next_attempt = delivery_transition(
                attempt["state"],
                "acknowledged",
                evidence=TransitionEvidence(
                    generation=generation,
                    expected_generation=generation,
                    task_received=True,
                ),
            ).value
        except TransitionConflict as error:
            raise WorkConflict(
                "workflow task receipt acknowledgement is not a valid transition"
            ) from error
        self._commit_checked_transition(
            connection,
            work=work,
            attempt=attempt,
            next_work=work["state"],
            next_attempt=next_attempt,
            actor_id=receiver_subject_id,
            event_id=nonce,
            fingerprint=receipt_hash,
            event_type="attempt.acknowledged",
        )
        return self._work(connection, work_item_id)

    def reconcile_cancelled_descendants(
        self, *, work_item_id: str, attempt_id: str, generation: int, actor_id: str
    ) -> dict:
        """Converge a historically cancelled root's live descendants to cancelled.

        This is recovery for an already committed root cancellation, not a new
        transition.  It deliberately leaves every root field, event and receipt
        untouched while retaining the same transaction boundary as a new
        cancellation cascade.
        """
        _identity(work_item_id, "work_item_id")
        _identity(attempt_id, "attempt_id")
        _identity(actor_id, "actor_id")
        if type(generation) is not int or generation <= 0:
            raise WorkConflict("invalid cancelled root generation")
        with self.transaction() as connection:
            self._verify(connection)
            return self._reconcile_cancelled_descendants(
                connection,
                work_item_id=work_item_id,
                attempt_id=attempt_id,
                generation=generation,
                actor_id=actor_id,
            )

    def _reconcile_cancelled_descendants(
        self,
        connection: sqlite3.Connection,
        *,
        work_item_id: str,
        attempt_id: str,
        generation: int,
        actor_id: str,
    ) -> dict:
        """Validate a cancelled root and reuse the caller-owned cascade transaction."""
        work = self._work(connection, work_item_id)
        attempt = connection.execute(
            "SELECT * FROM work_attempts WHERE id=?", (attempt_id,)
        ).fetchone()
        if (
            attempt is None
            or attempt["work_item_id"] != work_item_id
            or work["attempts"][-1]["id"] != attempt_id
            or attempt["generation"] != generation
            or work["state"] != "cancelled"
            or attempt["state"] != "cancelled"
        ):
            raise WorkConflict("cancelled root identity, generation or state changed")
        self._cancel_descendants(connection, work_item_id=work_item_id, actor_id=actor_id)
        return self._work(connection, work_item_id)

    def _commit_checked_transition(
        self,
        connection: sqlite3.Connection,
        *,
        work: dict,
        attempt: sqlite3.Row,
        next_work: str,
        next_attempt: str,
        actor_id: str,
        event_id: str,
        fingerprint: str,
        event_type: str,
        event_metadata: dict | None = None,
        after_state_change: Callable[[], None] | None = None,
    ) -> None:
        """Persist one already-validated work/attempt transition and its receipt."""
        if not connection.in_transaction:
            raise ValueError("caller-owned transaction required")
        connection.execute(
            "UPDATE work_attempts SET state=?,revision=revision+1 WHERE id=?",
            (next_attempt, attempt["id"]),
        )
        connection.execute(
            "UPDATE work_items SET state=?,revision=revision+1 WHERE id=?",
            (next_work, work["id"]),
        )
        if after_state_change is not None:
            after_state_change()
        self._append_event(
            connection,
            job_id=work["job_id"],
            work_item_id=work["id"],
            attempt_id=attempt["id"],
            event_type=event_type,
            actor_id=actor_id,
            event_id=event_id,
            metadata=event_metadata,
        )
        connection.execute(
            "INSERT INTO work_transition_receipts VALUES (?,?,?)",
            (event_id, fingerprint, work["id"]),
        )

    def _transition_attempt(
        self,
        connection: sqlite3.Connection,
        *,
        attempt_id: str,
        generation: int,
        expected_revision: int,
        expected_state: str,
        target: str,
        actor_id: str,
        event_id: str,
        evidence: TransitionEvidence,
    ) -> dict:
        """CAS state and events together; retries retain identity beyond retention.

        ``sent`` commits dispatch intent before external I/O. It is not a receipt.
        A crash after this transition must reconcile, never resend automatically.
        Evidence is supplied by the service, not by untrusted HTTP bodies.
        """
        if not connection.in_transaction:
            raise ValueError("caller-owned transaction required")
        _identity(event_id, "event_id")
        _identity(actor_id, "actor_id")
        fingerprint = hashlib.sha256(
            _json(
                dict(
                    attempt_id=attempt_id,
                    generation=generation,
                    expected_revision=expected_revision,
                    expected_state=expected_state,
                    target=target,
                    actor_id=actor_id,
                    evidence=evidence.model_dump(mode="json"),
                )
            ).encode()
        ).hexdigest()
        receipt = connection.execute(
            "SELECT * FROM work_transition_receipts WHERE event_id=?", (event_id,)
        ).fetchone()
        if receipt:
            if receipt["request_hash"] != fingerprint:
                raise WorkConflict("event_id reused with a different transition")
            if target == "cancelled":
                return self._reconcile_cancelled_descendants(
                    connection,
                    work_item_id=receipt["work_item_id"],
                    attempt_id=attempt_id,
                    generation=generation,
                    actor_id=actor_id,
                )
            return self._work(connection, receipt["work_item_id"])
        attempt = connection.execute(
            "SELECT * FROM work_attempts WHERE id=?", (attempt_id,)
        ).fetchone()
        if attempt is None:
            raise KeyError("attempt not found")
        work = self._work(connection, attempt["work_item_id"])
        if generation != attempt["generation"] or work["attempts"][-1]["id"] != attempt_id:
            raise WorkConflict("stale attempt generation")
        if expected_revision != attempt["revision"] or expected_state != attempt["state"]:
            raise WorkConflict("attempt revision or expected state changed")
        if evidence.generation != generation or evidence.expected_generation != generation:
            raise WorkConflict("evidence generation differs from stored generation")
        if (
            target == "failed"
            and attempt["state"] != "planned"
            and (attempt["result_id"] is not None or work["accepted_result_id"] is not None)
        ):
            raise WorkConflict("a registered or accepted result prevents process failure")
        job = self._job(connection, work["job_id"])
        if target not in {"cancelled", "failed", "reconcile"}:
            if job["state"] in {"revoked", "completed", "failed"}:
                raise WorkConflict("job no longer permits effects")
            if attempt["lease_expires_at"] <= time.time():
                raise WorkConflict("attempt lease expired")
        next_attempt = delivery_transition(attempt["state"], target, evidence=evidence).value
        next_work = work["state"]
        target_work = {
            "sent": "running",
            "finished": "succeeded",
            "failed": "failed",
            "reconcile": "reconcile",
            "cancelled": "cancelled",
        }.get(target)
        if target == "finished":
            # The result must have been persisted and validated first, not just hashed.
            result = connection.execute(
                "SELECT * FROM work_results WHERE id=? AND attempt_id=? AND validation_state='verified'",
                (attempt["result_id"], attempt_id),
            ).fetchone()
            if result is None:
                raise WorkConflict("verified result reference required before finishing")
            if work["accepted_result_id"] not in (None, result["id"]):
                raise WorkConflict("a different result was already accepted")
            pending_child = connection.execute(
                "SELECT id FROM work_items WHERE parent_work_item_id=? AND state!='succeeded' LIMIT 1",
                (work["id"],),
            ).fetchone()
            if pending_child:
                raise WorkConflict("required children have not succeeded")
        if target_work is not None and target_work != next_work:
            next_work = transition(next_work, target_work, evidence=evidence).value

        def after_state_change() -> None:
            if target == "failed" and attempt["state"] != "planned":
                connection.execute(
                    "UPDATE work_attempts SET cleanup_state='complete' WHERE id=?",
                    (attempt_id,),
                )
            if target == "finished":
                connection.execute(
                    "UPDATE work_items SET accepted_result_id=? WHERE id=?",
                    (attempt["result_id"], work["id"]),
                )
            if target == "sent" and job["state"] == "planning":
                connection.execute(
                    "UPDATE work_jobs SET state='running',revision=revision+1 WHERE id=?",
                    (job["id"],),
                )
            if attempt["state"] == "sent" and target == "running":
                self._append_event(
                    connection,
                    job_id=work["job_id"],
                    work_item_id=work["id"],
                    attempt_id=attempt_id,
                    event_type="attempt.acknowledged",
                    actor_id=actor_id,
                )

        event_metadata = None
        if target == "failed" and attempt["state"] != "planned":
            event_metadata = {
                "process_exit_code": evidence.process_exit_code,
                "process_stopped": evidence.process_stopped,
                "container_removed": evidence.container_removed,
                "image_removed": evidence.image_removed,
            }

        self._commit_checked_transition(
            connection,
            work=work,
            attempt=attempt,
            next_work=next_work,
            next_attempt=next_attempt,
            actor_id=actor_id,
            fingerprint=fingerprint,
            event_id=event_id,
            event_type=f"attempt.{target}",
            event_metadata=event_metadata,
            after_state_change=after_state_change,
        )
        if target == "cancelled":
            self._cancel_descendants(connection, work_item_id=work["id"], actor_id=actor_id)
        return self._work(connection, work["id"])

    def _cancel_descendants(
        self, connection: sqlite3.Connection, *, work_item_id: str, actor_id: str
    ) -> None:
        """Cancel every live descendant in the caller's already-owned transaction.

        Cancellation is the durable fence for dispatch, retries and result
        acceptance.  The recursive query deliberately crosses terminal nodes:
        historic trees can contain a live descendant below one, and stopping at
        that node would leave it authorized after the root cancellation commits.
        Cleanup is intentionally not claimed here; its pending/complete/failed
        lifecycle records an independent external observation.
        """
        descendants = connection.execute(
            """WITH RECURSIVE descendants(id, path) AS (
                SELECT id, id FROM work_items WHERE parent_work_item_id=?
                UNION ALL
                SELECT child.id, descendants.path || '/' || child.id
                FROM work_items AS child
                JOIN descendants ON child.parent_work_item_id=descendants.id
            )
            SELECT id FROM descendants ORDER BY path""",
            (work_item_id,),
        ).fetchall()
        for row in descendants:
            work = self._work(connection, row["id"])
            if work["state"] in {"succeeded", "failed", "cancelled"}:
                continue
            attempt = work["attempts"][-1]
            evidence = TransitionEvidence(
                generation=attempt["generation"], expected_generation=attempt["generation"]
            )
            try:
                next_attempt = delivery_transition(
                    attempt["state"], "cancelled", evidence=evidence
                ).value
                next_work = transition(work["state"], "cancelled", evidence=evidence).value
            except TransitionConflict as error:
                raise WorkConflict("live descendant cannot be cancelled") from error
            event_id = uuid4().hex
            fingerprint = hashlib.sha256(
                _json(
                    dict(
                        attempt_id=attempt["id"],
                        generation=attempt["generation"],
                        expected_revision=attempt["revision"],
                        expected_state=attempt["state"],
                        target="cancelled",
                        actor_id=actor_id,
                        evidence=evidence.model_dump(mode="json"),
                    )
                ).encode()
            ).hexdigest()
            self._commit_checked_transition(
                connection,
                work=work,
                attempt=attempt,
                next_work=next_work,
                next_attempt=next_attempt,
                actor_id=actor_id,
                fingerprint=fingerprint,
                event_id=event_id,
                event_type="attempt.cancelled",
            )

    def register_result(
        self,
        *,
        attempt_id: str,
        generation: int,
        content_hash: str,
        immutable_location: str,
        byte_length: int,
        validator_id: str,
        validation_evidence: dict,
        actor_id: str,
        before_register: Callable[[sqlite3.Connection], None] | None = None,
    ) -> dict:
        """Retain service-verified artifact evidence before claiming success.

        The caller holds the artifact publication lock until this commits. Late
        evidence is retained, but cannot replace a terminal winner or its result.
        This trusted boundary must not be exposed as a client assertion of validity.
        """
        if len(content_hash) != 64 or any(c not in "0123456789abcdef" for c in content_hash):
            raise ValueError("invalid result digest")
        if immutable_location != content_hash or type(byte_length) is not int or byte_length < 0:
            raise ValueError("invalid immutable artifact reference")
        _identity(validator_id, "validator_id")
        _identity(actor_id, "actor_id")
        if not isinstance(validation_evidence, dict) or not validation_evidence:
            raise ValueError("validation evidence required")
        if before_register is not None and not callable(before_register):
            raise ValueError("result registration precondition must be callable")
        encoded_evidence = _json(validation_evidence)
        identifier = hashlib.sha256(f"{attempt_id}:{content_hash}".encode()).hexdigest()
        with self.transaction() as connection:
            self._verify(connection)
            attempt = connection.execute(
                "SELECT * FROM work_attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if attempt is None:
                raise KeyError("attempt not found")
            if attempt["generation"] != generation:
                raise WorkConflict("stale result generation")
            if before_register is not None:
                before_register(connection)
            previous = connection.execute(
                "SELECT * FROM work_results WHERE id=?", (identifier,)
            ).fetchone()
            if previous:
                if (
                    previous["byte_length"],
                    previous["validator_id"],
                    previous["validation_evidence"],
                ) != (byte_length, validator_id, encoded_evidence):
                    raise WorkConflict("result evidence is immutable")
                return dict(previous)
            work = self._work(connection, attempt["work_item_id"])
            connection.execute(
                "INSERT INTO work_results (id,attempt_id,content_hash,immutable_location,byte_length,validation_state,validator_id,validation_evidence,created_at) VALUES (?,?,?,?,?,'verified',?,?,?)",
                (
                    identifier,
                    attempt_id,
                    content_hash,
                    immutable_location,
                    byte_length,
                    validator_id,
                    encoded_evidence,
                    time.time(),
                ),
            )
            if (
                work["attempts"][-1]["id"] == attempt_id
                and attempt["state"] in {"acknowledged", "running"}
                and work["state"] not in {"succeeded", "failed", "cancelled"}
                and attempt["result_id"] is None
            ):
                connection.execute(
                    "UPDATE work_attempts SET result_id=?,revision=revision+1 WHERE id=?",
                    (identifier, attempt_id),
                )
            connection.execute(
                "UPDATE work_items SET revision=revision+1 WHERE id=?", (work["id"],)
            )
            self._append_event(
                connection,
                job_id=work["job_id"],
                work_item_id=work["id"],
                attempt_id=attempt_id,
                event_type="result.recorded",
                actor_id=actor_id,
                metadata={"result_id": identifier},
            )
            return dict(
                connection.execute(
                    "SELECT * FROM work_results WHERE id=?", (identifier,)
                ).fetchone()
            )

    def get_result(self, result_id: str) -> dict:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM work_results WHERE id=?", (result_id,)
            ).fetchone()
            if row is None:
                raise KeyError("result not found")
            return dict(row)

    def referenced_artifact_hashes(self) -> set[str]:
        with self.connection() as connection:
            return {
                row[0]
                for row in connection.execute(
                    "SELECT content_hash FROM work_results UNION SELECT content_hash FROM work_worktree_evidence"
                )
            }

    def renew_attempt(
        self,
        attempt_id: str,
        *,
        generation: int,
        expected_revision: int,
        lease_seconds: float,
        actor_id: str,
    ) -> dict:
        if (
            isinstance(lease_seconds, bool)
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
        ):
            raise ValueError("lease_seconds must be finite and positive")
        _identity(actor_id, "actor_id")
        with self.transaction() as connection:
            self._verify(connection)
            attempt = connection.execute(
                "SELECT * FROM work_attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if attempt is None:
                raise KeyError("attempt not found")
            work = self._work(connection, attempt["work_item_id"])
            if attempt["generation"] != generation or work["attempts"][-1]["id"] != attempt_id:
                raise WorkConflict("stale lease generation")
            if attempt["revision"] != expected_revision:
                raise WorkConflict("attempt revision changed")
            now = time.time()
            if attempt["lease_expires_at"] <= now:
                raise WorkConflict("expired lease cannot be renewed")
            if attempt["state"] not in {"planned", "sent", "acknowledged", "running"}:
                raise WorkConflict("settled or uncertain attempt cannot renew")
            job = self._job(connection, work["job_id"])
            if job["state"] in {"revoked", "completed", "failed"}:
                raise WorkConflict("job no longer permits renewal")
            connection.execute(
                "UPDATE work_attempts SET lease_expires_at=?,revision=revision+1 WHERE id=?",
                (max(attempt["lease_expires_at"], now + lease_seconds), attempt_id),
            )
            connection.execute(
                "UPDATE work_items SET revision=revision+1 WHERE id=?", (work["id"],)
            )
            self._append_event(
                connection,
                job_id=work["job_id"],
                work_item_id=work["id"],
                attempt_id=attempt_id,
                event_type="attempt.renewed",
                actor_id=actor_id,
            )
            return self._work(connection, work["id"])

    def retry_work(
        self,
        work_item_id: str,
        *,
        expected_revision: int,
        provider: str,
        evidence: TransitionEvidence,
        actor_id: str,
        lease_seconds: float,
    ) -> dict:
        """Create a new generation only after externally verified reconciliation."""
        if (
            isinstance(lease_seconds, bool)
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
        ):
            raise ValueError("lease_seconds must be finite and positive")
        _identity(actor_id, "actor_id")
        with self.transaction() as connection:
            self._verify(connection)
            self._assert_offline_cut_allows_writer(connection)
            work = self._work(connection, work_item_id)
            prior = work["attempts"][-1]
            job = self._job(connection, work["job_id"])
            if work["revision"] != expected_revision:
                raise WorkConflict("work revision changed")
            if prior["cleanup_state"] == "pending":
                raise WorkConflict("pending cleanup blocks replacement")
            if (
                evidence.generation != prior["generation"]
                or evidence.expected_generation != prior["generation"]
            ):
                raise WorkConflict("stale reconciliation generation")
            if provider not in job["allowed_providers"] or job["state"] in {
                "revoked",
                "completed",
                "failed",
            }:
                raise WorkConflict("job does not permit replacement provider")
            next_state = transition(work["state"], "queued", evidence=evidence).value
            attempt_id, now = uuid4().hex, time.time()
            connection.execute(
                "INSERT INTO work_attempts (id,work_item_id,attempt_number,generation,provider,lease_expires_at,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    attempt_id,
                    work_item_id,
                    prior["attempt_number"] + 1,
                    prior["generation"] + 1,
                    provider,
                    now + lease_seconds,
                    now,
                ),
            )
            connection.execute(
                "UPDATE work_items SET state=?,revision=revision+1 WHERE id=?",
                (next_state, work_item_id),
            )
            self._append_event(
                connection,
                job_id=work["job_id"],
                work_item_id=work_item_id,
                attempt_id=attempt_id,
                event_type="attempt.replanned",
                actor_id=actor_id,
            )
            return self._work(connection, work_item_id)

    def reconcile_expired(
        self, *, actor_id: str, parent_work_item_id: str | None = None
    ) -> list[str]:
        """Expiry is evidence of uncertainty, not proof an external process stopped."""
        with self.connection() as connection:
            restriction = (
                " AND work_item_id IN (SELECT id FROM work_items WHERE parent_work_item_id=?)"
                if parent_work_item_id is not None
                else ""
            )
            parameters = (
                (time.time(), parent_work_item_id)
                if parent_work_item_id is not None
                else (time.time(),)
            )
            attempts = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM work_attempts WHERE lease_expires_at<=? AND state IN ('planned','sent','acknowledged','running')"
                    + restriction,
                    parameters,
                )
            ]
        changed = []
        for attempt in attempts:
            try:
                self.transition_attempt(
                    attempt_id=attempt["id"],
                    generation=attempt["generation"],
                    expected_revision=attempt["revision"],
                    expected_state=attempt["state"],
                    target="failed" if attempt["state"] == "planned" else "reconcile",
                    actor_id=actor_id,
                    event_id=uuid4().hex,
                    evidence=TransitionEvidence(
                        generation=attempt["generation"], expected_generation=attempt["generation"]
                    ),
                )
            except WorkConflict:
                continue  # A renewal, cancellation, or other observer already won.
            changed.append(attempt["work_item_id"])
        return changed

    def children(self, work_item_id: str) -> list[dict]:
        with self.connection() as connection:
            connection.execute("BEGIN")
            self._work(connection, work_item_id)
            identities = connection.execute(
                "SELECT id FROM work_items WHERE parent_work_item_id=? ORDER BY created_at,id",
                (work_item_id,),
            ).fetchall()
            return [self._work(connection, row["id"]) for row in identities]

    def get_attempt(self, attempt_id: str) -> dict:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM work_attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise KeyError("attempt not found")
            return dict(row)

    @staticmethod
    def _inbox_binding(connection, *, attempt_id, generation):
        if type(generation) is not int or generation <= 0:
            raise WorkConflict("invalid inbox binding generation")
        row = connection.execute(
            "SELECT * FROM work_inbox_bindings WHERE attempt_id=? AND generation=?",
            (attempt_id, generation),
        ).fetchone()
        return row

    def bind_inbox(self, *, attempt_id, generation, inbox_id, store_context) -> dict:
        """Persist one immutable managed-inbox selector for an admitted order.

        A fixed server context, rather than a caller-selected route, identifies
        the SQLAlchemy store paired to this repository connection.
        """
        _identity(attempt_id, "attempt_id")
        if type(generation) is not int or generation <= 0:
            raise ValueError("invalid inbox binding generation")
        if type(inbox_id) is not int or inbox_id <= 0:
            raise ValueError("invalid inbox id")
        with self.transaction() as connection:
            self._verify(connection)
            context = self._validate_managed_inbox_store_context(connection, expected=store_context)
            dispatch = connection.execute(
                "SELECT * FROM work_dispatch_bindings WHERE attempt_id=? AND generation=?",
                (attempt_id, generation),
            ).fetchone()
            if dispatch is None:
                raise WorkConflict("managed inbox requires an admitted dispatch binding")
            current = self._inbox_binding(connection, attempt_id=attempt_id, generation=generation)
            by_inbox = connection.execute(
                "SELECT * FROM work_inbox_bindings WHERE inbox_id=?", (inbox_id,)
            ).fetchone()
            if current is not None:
                if (
                    current["inbox_id"],
                    current["store_identity"],
                ) == (inbox_id, context.store_identity):
                    return dict(current)
                raise WorkConflict("operation is already bound to another managed inbox row")
            if by_inbox is not None:
                raise WorkConflict("managed inbox row is already bound to another operation")
            paired = connection.execute(
                "SELECT store_identity FROM work_inbox_store_identity WHERE singleton=1"
            ).fetchone()
            if paired is None:
                connection.execute(
                    "INSERT INTO work_inbox_store_identity VALUES (1,?,?)",
                    (context.store_identity, time.time()),
                )
            elif paired["store_identity"] != context.store_identity:
                raise WorkConflict("managed inbox store identity changed")
            connection.execute(
                "INSERT INTO work_inbox_bindings VALUES (?,?,?,?,?)",
                (inbox_id, attempt_id, generation, context.store_identity, time.time()),
            )
            self._append_event(
                connection,
                job_id=dispatch["job_id"],
                work_item_id=dispatch["work_item_id"],
                attempt_id=attempt_id,
                actor_id=dispatch["principal_id"],
                event_type="inbox.bound",
                metadata={"inbox_id": inbox_id},
            )
            return dict(
                self._inbox_binding(connection, attempt_id=attempt_id, generation=generation)
            )

    def get_inbox_binding(self, *, attempt_id, generation, inbox_id, store_context) -> dict:
        """Return only the matching bridge for the verified server-owned store."""
        _identity(attempt_id, "attempt_id")
        if (
            type(generation) is not int
            or generation <= 0
            or type(inbox_id) is not int
            or inbox_id <= 0
        ):
            raise ValueError("invalid managed inbox binding identity")
        with self.read_snapshot() as connection:
            context = self._validate_managed_inbox_store_context(connection, expected=store_context)
            paired = connection.execute(
                "SELECT store_identity FROM work_inbox_store_identity WHERE singleton=1"
            ).fetchone()
            row = self._inbox_binding(connection, attempt_id=attempt_id, generation=generation)
            if (
                paired is None
                or paired["store_identity"] != context.store_identity
                or row is None
                or (row["inbox_id"], row["store_identity"]) != (inbox_id, context.store_identity)
            ):
                raise WorkConflict("managed inbox bridge is absent or contradictory")
            return dict(row)

    def find_inbox_binding(self, *, attempt_id, generation, store_context) -> dict | None:
        """Find a retained bridge without treating its absence as permission to create another row."""
        _identity(attempt_id, "attempt_id")
        if type(generation) is not int or generation <= 0:
            raise ValueError("invalid managed inbox binding identity")
        with self.read_snapshot() as connection:
            context = self._validate_managed_inbox_store_context(connection, expected=store_context)
            paired = connection.execute(
                "SELECT store_identity FROM work_inbox_store_identity WHERE singleton=1"
            ).fetchone()
            row = self._inbox_binding(connection, attempt_id=attempt_id, generation=generation)
            if row is None:
                return None
            if (
                paired is None
                or paired["store_identity"] != context.store_identity
                or row["store_identity"] != context.store_identity
            ):
                raise WorkConflict("managed inbox bridge is contradictory")
            return dict(row)

    def claim_reconciled_cleanup(
        self, attempt_id: str, *, generation: int, expected_revision: int, actor_id: str
    ) -> dict:
        """Atomically reserve process cleanup while its reconcile writer is still held."""
        _identity(attempt_id, "attempt_id")
        _identity(actor_id, "actor_id")
        if type(generation) is not int or generation <= 0:
            raise WorkConflict("cleanup generation is invalid")
        if type(expected_revision) is not int or expected_revision <= 0:
            raise WorkConflict("cleanup revision is invalid")
        with self.transaction() as connection:
            self._verify(connection)
            attempt = connection.execute(
                "SELECT * FROM work_attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if attempt is None:
                raise WorkConflict("cleanup attempt does not exist")
            work = self._work(connection, attempt["work_item_id"])
            latest = work["attempts"][-1]
            reservation = connection.execute(
                "SELECT job_id,work_item_id,generation,state "
                "FROM work_scheduler_requests WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if (
                attempt["generation"] != generation
                or attempt["revision"] != expected_revision
                or latest["id"] != attempt_id
                or latest["generation"] != generation
                or attempt["state"] != "reconcile"
                or work["state"] != "reconcile"
                or reservation is None
                or reservation["job_id"] != work["job_id"]
                or reservation["work_item_id"] != work["id"]
                or reservation["generation"] != generation
                or reservation["state"] != "held"
            ):
                raise WorkConflict("reconciled cleanup writer or reservation changed")
            if attempt["cleanup_state"] in {"pending", "complete"}:
                return work
            if attempt["cleanup_state"] not in {"not_requested", "failed"}:
                raise WorkConflict("cleanup state cannot be claimed")
            connection.execute(
                "UPDATE work_attempts SET cleanup_state='pending',revision=revision+1 WHERE id=?",
                (attempt_id,),
            )
            connection.execute(
                "UPDATE work_items SET revision=revision+1 WHERE id=?", (work["id"],)
            )
            self._append_event(
                connection,
                job_id=work["job_id"],
                work_item_id=work["id"],
                attempt_id=attempt_id,
                event_type="cleanup.pending",
                actor_id=actor_id,
            )
            return self._work(connection, work["id"])

    def record_cleanup(
        self, attempt_id: str, *, generation: int, expected_revision: int, state: str, actor_id: str
    ) -> dict:
        """Cleanup observations do not change the work/result winner."""
        _identity(actor_id, "actor_id")
        allowed = {
            "not_requested": {"pending"},
            "pending": {"complete", "failed"},
            "failed": {"pending"},
        }
        with self.transaction() as connection:
            self._verify(connection)
            attempt = connection.execute(
                "SELECT * FROM work_attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if attempt is None:
                raise KeyError("attempt not found")
            if attempt["generation"] != generation or attempt["revision"] != expected_revision:
                raise WorkConflict("cleanup generation or revision changed")
            if attempt["state"] in {"planned", "sent", "acknowledged", "running"}:
                raise WorkConflict("active attempt must be cancelled or reconciled before cleanup")
            if state not in allowed.get(attempt["cleanup_state"], set()):
                raise WorkConflict("cleanup state changed or transition is invalid")
            work = self._work(connection, attempt["work_item_id"])
            connection.execute(
                "UPDATE work_attempts SET cleanup_state=?,revision=revision+1 WHERE id=?",
                (state, attempt_id),
            )
            connection.execute(
                "UPDATE work_items SET revision=revision+1 WHERE id=?", (work["id"],)
            )
            self._append_event(
                connection,
                job_id=work["job_id"],
                work_item_id=work["id"],
                attempt_id=attempt_id,
                event_type=f"cleanup.{state}",
                actor_id=actor_id,
            )
            return self._work(connection, work["id"])

    @staticmethod
    def _verify(connection: sqlite3.Connection, *, version: int | None = None) -> None:
        if version is None:
            version = SCHEMA_VERSION
        expected = _EXPECTED_SCHEMAS[version]
        if _schema_objects(connection) != expected:
            raise SchemaMismatch("work schema is incomplete, incompatible or modified")
        records = connection.execute(
            "SELECT version,checksum,verification_result FROM work_migrations ORDER BY version"
        ).fetchall()
        if [tuple(row) for row in records] != [
            (number, _CHECKSUMS[number], "verified") for number in range(1, version + 1)
        ]:
            raise SchemaMismatch("work migration ledger is incompatible or modified")
        if version >= 16:
            _stored_inbox_context(connection)
        if version >= 24:
            _stored_recovery_context(connection)
        for table, sql in expected.items():
            if not sql.startswith("CREATE TABLE"):
                continue
            if connection.execute(f"PRAGMA foreign_key_check({table})").fetchone():
                raise SchemaMismatch("work store contains invalid references")


def initialize_work_store() -> None:
    """Use the same SQLite path as the existing durable workflow journals."""
    from cli_agent_orchestrator.constants import DATABASE_FILE

    WorkRepository(DATABASE_FILE).initialize()
