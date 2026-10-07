"""Minimal database client with only terminal metadata."""

import json as _json
import logging
import os
import re
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, cast

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    literal_column,
    text,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients.work_inbox_schema import ManagedInboxStoreIdentity
from cli_agent_orchestrator.constants import DATABASE_URL, DB_DIR, DEFAULT_PROVIDER
from cli_agent_orchestrator.models.flow import Flow
from cli_agent_orchestrator.models.inbox import InboxMessage, MessageStatus

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class SessionIncarnationModel(Base):
    """Current logical lifetime of a reusable session name.

    Keep the pointer after teardown so retries cannot claim historical terminal
    rows. A successful new-session creation replaces it in the same transaction
    as its initial terminal; individual terminal deletion never removes it.
    """

    __tablename__ = "session_incarnations"

    session_name: Mapped[str] = mapped_column(String, primary_key=True)
    incarnation_id: Mapped[str] = mapped_column(String, nullable=False)


class TerminalModel(Base):
    """SQLAlchemy model for terminal metadata only."""

    __tablename__ = "terminals"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # "abc123ef"
    tmux_session: Mapped[str] = mapped_column(String, nullable=False)  # "cao-session-name"
    tmux_window: Mapped[str] = mapped_column(String, nullable=False)  # "window-name"
    provider: Mapped[str] = mapped_column(String, nullable=False)  # "kiro_cli", "claude_code"
    agent_profile: Mapped[str | None] = mapped_column(String)  # "developer", "reviewer" (optional)
    working_directory: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # launch-time cwd (optional)
    allowed_tools: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # JSON-encoded list of CAO tool names
    shell_command: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # shell process name captured before kiro launch
    caller_id: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # terminal that created this one (callback target)
    engine: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # resolved Kiro engine; NULL for legacy/non-Kiro rows
    provider_variant: Mapped[str | None] = mapped_column(String, nullable=True)
    kiro_policy_digest: Mapped[str | None] = mapped_column(String, nullable=True)
    # Ordered, general-to-specific array of strings (JSON-encoded), e.g.
    # '["tenant_1", "project_5", "folder_12"]'. CAO only does ordered-prefix
    # matching (list_siblings); consumers own what the levels mean (#432).
    group: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Free-form JSON (JSON-encoded dict), consumer-defined, no fixed schema.
    # Python attribute is ``metadata_json`` (not ``metadata``) because
    # SQLAlchemy's declarative Base reserves ``.metadata`` for the schema
    # MetaData object on every mapped class; the DB column itself is still
    # literally named "metadata" per #432's design.
    metadata_json: Mapped[str | None] = mapped_column("metadata", Text, nullable=True)
    # Server-owned durable deferred-init failure. Kept separate from consumer
    # metadata so PATCH /metadata cannot erase or forge lifecycle truth.
    deferred_init_failure_json: Mapped[str | None] = mapped_column(
        "deferred_init_failure", Text, nullable=True
    )
    # Creation-time lifecycle ownership for deferred initialization.  True
    # means an external observer (rather than CAO itself) owns final failure
    # settlement, so runtime/lifecycle cleanup may dismantle provider resources
    # but must retain this registry row until that observer acknowledges it.
    deferred_init_external_owner: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # True once provider/FIFO/worktree runtime state has been fully dismantled
    # for a retained external-owner tombstone.  The row may remain for durable
    # failure observation, but it no longer consumes a live runtime slot.
    deferred_init_runtime_reclaimed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # Durable identity for one logical lifetime of a reusable session name.
    # Retained deferred-init tombstones can outlive the backend session; when a
    # later session reuses the same label this value lets read/lifecycle paths
    # distinguish the old rows from failures that belong to the CURRENT live
    # session. NULL is reserved for rows created before this column existed.
    session_incarnation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    last_active: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.now)

    # ORDERING CONTRACT: the two session-scoped reads -- ``list_terminals_by_session``
    # and ``list_terminals_in_sessions`` -- order by SQLite's implicit ``rowid``,
    # so index 0 of a session's terminals is its OLDEST SURVIVING row.
    # (``list_siblings_by_group_prefix`` also reads this table by session and is
    # deliberately NOT ordered: its consumer matches on group prefixes and does
    # not take a first element. Order it too if that ever changes.) Several consumers
    # treat that as the session's conductor and one of them kills sessions on it.
    # rowid is not declared above, so the dependency is invisible here. Three
    # things break it SILENTLY:
    #   * an ``INSERT OR REPLACE``/upsert against terminals -- a replace deletes
    #     and re-inserts, so the row gets a NEW rowid and jumps to the end of its
    #     session. Nothing does this today and
    #     ``test_no_upsert_against_the_terminals_table`` fails the build if it
    #     starts; use an UPDATE.
    #   * writing ``rowid`` explicitly (``INSERT (rowid, ...) VALUES (-5, ...)``).
    #   * giving ``id`` INTEGER affinity, which makes rowid an alias for it and
    #     hands the order back to a random uuid.
    # Declaring the table WITHOUT ROWID also breaks it but fails LOUDLY
    # (``no such column: terminals.rowid``), as does reading it through
    # ``aliased()`` or ``union()``.
    #
    # New rows sort after existing ones because SQLite assigns ``max(rowid)+1``
    # among surviving rows -- so deleting rows recycles values but cannot reorder
    # the ones still present. The exception is a table holding a row at
    # 2**63-1, where SQLite picks random unused rowids and within-session order
    # scrambles; unreachable without an explicit rowid write.
    #
    # Deliberately NOT a ``created_at`` column: rowid already records insertion
    # order exactly and correctly for every row in every existing database,
    # whereas a new column has to invent the value for rows that predate it, and
    # there is no honest source for it -- ``last_active`` is written only on
    # input delivery (send_input/send_special_key), so it is LATEST for the
    # busiest terminal, which is usually the conductor, and backfilling from it
    # inverts the very order this contract exists to preserve.
    #
    # Deliberately NOT ``caller_id`` either, and this one was priced rather than
    # dismissed. A conductor created through ``create_session`` records no
    # caller while an MCP-spawned worker records its supervisor, so
    # ``caller_id IS NOT NULL`` looks like root-terminal identity. On its own it
    # is not total -- ``caller_id`` is an optional parameter of the agent-step
    # create path, so a root carrying an explicit caller would be outranked by a
    # later terminal without one. It CAN be made total by also requiring the
    # parent to be in the same session (a correlated ``EXISTS`` on
    # ``tmux_session``, which is immutable after insert, so a root's caller can
    # never be in its own session). Measured, that costs ~+29% on this read
    # (50 sessions over 5k rows: 9.0ms -> 11.6ms) and couples the conductor pick
    # to referential integrity nothing enforces -- there is no FK on
    # ``caller_id`` and ghost terminals are deleted in three places, so a
    # deleted root leaves every worker's caller dangling. Rejected on that cost
    # and coupling, for a hazard the upsert guard above already turns into a red
    # build. ``caller_id`` is still the right thing to read for a specific
    # terminal's spawn parent; it is not worth its price as a sort key.


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TerminalTurnReceiptModel(Base):
    """Private, durable lifecycle evidence for one provider turn.

    This is deliberately *not* stored in ``terminals.metadata``.  That field
    is an agent/user-controlled free-form document exposed through the API,
    whereas a receipt controls whether CAO may send another task after a
    server restart.  The row contains only opaque identifiers and hashes: no
    prompt body, nonce, model output, or terminal transcript.

    A terminal has at most one in-flight receipt.  A settled
    ``result_verified`` row is retained as an audit handle and atomically
    replaced by the next ``prepared`` turn; an active ``prepared``/``sent``
    row blocks a second task until its result is verified or recovery records
    a backend-confirmed cancellation.
    """

    __tablename__ = "terminal_turn_receipts"

    terminal_id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    # Opaque per-turn generation, distinct from the receipt itself, so state
    # transitions have a CAS key even though the plaintext nonce is never
    # persisted.
    generation: Mapped[str] = mapped_column(String, nullable=False)
    receipt_sha256: Mapped[str] = mapped_column(String, nullable=False)
    # prepared | sent | result_verified.  Validation is duplicated at the
    # repository boundary for existing SQLite databases where a CHECK would
    # otherwise be absent.
    phase: Mapped[str] = mapped_column(String, nullable=False)
    result_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "phase IN ('prepared', 'sent', 'result_verified')",
            name="ck_terminal_turn_receipts_phase",
        ),
        CheckConstraint(
            "length(generation) = 32",
            name="ck_terminal_turn_receipts_generation_length",
        ),
        CheckConstraint(
            "length(receipt_sha256) = 64",
            name="ck_terminal_turn_receipts_receipt_hash_length",
        ),
        CheckConstraint(
            "result_sha256 IS NULL OR length(result_sha256) = 64",
            name="ck_terminal_turn_receipts_result_hash_length",
        ),
    )


class TerminalTurnRecoveryModel(Base):
    """Durable diagnostics and verified results, retained by generation."""

    __tablename__ = "terminal_turn_recovery"
    terminal_id: Mapped[str] = mapped_column(String, primary_key=True)
    generation: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'verifying', 'reconcile', 'verified', 'cancelling', 'cancelled')",
            name="ck_terminal_turn_recovery_state",
        ),
        CheckConstraint("attempts >= 0", name="ck_terminal_turn_recovery_attempts"),
    )


class TerminalLateObservationModel(Base):
    """Separate finite evidence-read budget; never authorizes native input."""

    __tablename__ = "terminal_late_observations"
    terminal_id: Mapped[str] = mapped_column(String, primary_key=True)
    generation: Mapped[str] = mapped_column(String, primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_due: Mapped[float] = mapped_column(Float, nullable=False)
    deadline: Mapped[float] = mapped_column(Float, nullable=False)
    __table_args__ = (
        CheckConstraint("attempts >= 0 AND attempts <= 6", name="ck_terminal_late_budget"),
    )


class AssignmentIntentModel(Base):
    """Immutable whole-assignment identity retained even after terminal deletion."""

    __tablename__ = "assignment_intents"
    assignment_id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    parent_terminal_id: Mapped[str] = mapped_column(String, nullable=False)
    parent_incarnation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    generation: Mapped[str | None] = mapped_column(String, nullable=True)
    operation_key: Mapped[str] = mapped_column(String, nullable=False)
    request_hash: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    __table_args__ = (
        CheckConstraint(
            "state IN ('admitted', 'submitted', 'reconcile')", name="ck_assignment_intent_state"
        ),
    )


class LocalPeerProjectModel(Base):
    """Project roots explicitly approved for local CAO peer coordination."""

    __tablename__ = "local_peer_projects"
    project_id: Mapped[str] = mapped_column(String, primary_key=True)
    canonical_root: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    git_common_dir: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class LocalPeerGrantModel(Base):
    """Revocable project-scoped grant pinned to a peer's public signing key."""

    __tablename__ = "local_peer_grants"
    grant_id: Mapped[str] = mapped_column(String, primary_key=True)
    peer_instance_id: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    peer_display_name: Mapped[str] = mapped_column(String, nullable=False)
    peer_public_key: Mapped[str] = mapped_column(String, nullable=False)
    scopes_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        UniqueConstraint("peer_instance_id", "project_id", name="uq_local_peer_grant_project"),
        Index("ix_local_peer_grant_active", "peer_instance_id", "project_id", "revoked_at"),
        CheckConstraint(
            "length(peer_public_key) BETWEEN 40 AND 64", name="ck_local_peer_public_key"
        ),
    )


class LocalPeerChallengeModel(Base):
    """Short-lived one-use pairing challenge, retained for safe retries."""

    __tablename__ = "local_peer_pairing_challenges"
    challenge_id: Mapped[str] = mapped_column(String, primary_key=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    code_hash: Mapped[str] = mapped_column(String, nullable=False)
    initiator_instance_id: Mapped[str] = mapped_column(String, nullable=False)
    initiator_process_generation: Mapped[str] = mapped_column(String, nullable=False)
    initiator_display_name: Mapped[str] = mapped_column(String, nullable=False)
    initiator_public_key: Mapped[str] = mapped_column(String, nullable=False)
    initiator_loopback_port: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_instance_id: Mapped[str] = mapped_column(String, nullable=False)
    candidate_process_generation: Mapped[str] = mapped_column(String, nullable=False)
    candidate_display_name: Mapped[str] = mapped_column(String, nullable=False)
    candidate_public_key: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    canonical_root: Mapped[str] = mapped_column(String, nullable=False)
    git_common_dir: Mapped[str | None] = mapped_column(String, nullable=True)
    requested_scopes_json: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)
    consumed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    __table_args__ = (
        CheckConstraint("role IN ('initiator', 'candidate')", name="ck_local_peer_challenge_role"),
        CheckConstraint("length(code_hash) = 64", name="ck_local_peer_challenge_code_hash"),
    )


class LocalPeerTaskModel(Base):
    """Durable task receipt for an assignment accepted from a local CAO peer."""

    __tablename__ = "local_peer_tasks"
    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_instance_id: Mapped[str] = mapped_column(String, nullable=False)
    target_instance_id: Mapped[str] = mapped_column(String, nullable=False)
    requester_terminal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    requester_principal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    operation_key: Mapped[str] = mapped_column(String, nullable=False)
    request_hash: Mapped[str] = mapped_column(String, nullable=False)
    assignment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    terminal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    use_worktree: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    state: Mapped[str] = mapped_column(String, nullable=False, default="accepted")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    __table_args__ = (
        UniqueConstraint(
            "source_instance_id",
            "requester_terminal_id",
            "project_id",
            "operation_key",
            name="uq_local_peer_task_key",
        ),
        Index("ix_local_peer_task_target_project", "target_instance_id", "project_id"),
        CheckConstraint(
            "state IN ('accepted', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted', 'reconcile')",
            name="ck_local_peer_task_state",
        ),
        CheckConstraint("length(request_hash) = 64", name="ck_local_peer_task_hash"),
    )


class LocalPeerRequestNonceModel(Base):
    """Short-lived replay fence for signed peer HTTP requests."""

    __tablename__ = "local_peer_request_nonces"
    peer_instance_id: Mapped[str] = mapped_column(String, primary_key=True)
    nonce: Mapped[str] = mapped_column(String, primary_key=True)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)
    __table_args__ = (CheckConstraint("length(nonce) = 32", name="ck_local_peer_nonce_length"),)


class ProjectMarkerModel(Base):
    """Private memory identity registry; never a portable Work grant."""

    __tablename__ = "project_markers"
    project_id: Mapped[str] = mapped_column(String, primary_key=True)
    nonce: Mapped[str] = mapped_column(String, nullable=False)
    realpath: Mapped[str] = mapped_column(String, nullable=False)
    device: Mapped[int] = mapped_column(Integer, nullable=False)
    inode: Mapped[int] = mapped_column(Integer, nullable=False)


class BeadsOperationModel(Base):
    """Private operation evidence for bounded external metadata writes."""

    __tablename__ = "beads_operations"
    operation_id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False)
    workspace_identity: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    request_hash: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    __table_args__ = (
        CheckConstraint(
            "state IN ('claimed', 'applied', 'uncertain', 'partial')",
            name="ck_beads_operation_state",
        ),
    )


class NativeChildModel(Base):
    """Durable lifecycle receipt for a terminal created by another terminal.

    ``terminals.caller_id`` answers only *who created this window*.  It cannot
    answer whether a task was delivered, whether it was observed running, or
    whether a timeout left an uncertain worker behind.  This separate record is
    deliberately retained after the terminal row is removed so a parent can
    distinguish a completed child, a cancelled child, and an uncertain child
    that needs reconciliation.

    No prompt body or agent transcript is stored here.  The receipt carries
    handles and lifecycle evidence only.
    """

    __tablename__ = "native_children"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    parent_terminal_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Generated before the backend resource exists, so an interrupted create
    # remains traceable even when no terminal registry row was committed.
    terminal_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    agent_profile: Mapped[str] = mapped_column(String, nullable=False)
    # planned | acknowledged | sent | running | succeeded | failed |
    # reconcile | cancelled.  Validation lives in the service boundary so old
    # SQLite files remain additive-only.
    state: Mapped[str] = mapped_column(String, nullable=False, default="planned")
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    cleanup_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InboxModel(Base):
    """SQLAlchemy model for inbox messages."""

    __tablename__ = "inbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sender_id: Mapped[str] = mapped_column(String, nullable=False)
    receiver_id: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # MessageStatus enum value
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.now)


class ManagedInboxTarget(NamedTuple):
    """The terminal coordinates needed by the protected managed-inbox adapter."""

    message: InboxMessage
    tmux_session: str
    tmux_window: str


class MemoryMetadataModel(Base):
    """SQLAlchemy model for memory metadata (Phase 2 U1).

    SQLite is the source of truth for metadata queries; wiki markdown
    files remain the content store. Each row corresponds to exactly one
    wiki file on disk.
    """

    __tablename__ = "memory_metadata"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String, nullable=False)
    memory_type: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # A NOT NULL discriminator keeps the widened unique constraint total:
    # SQLite considers NULL values distinct inside UNIQUE indexes.
    source_kind: Mapped[str] = mapped_column(
        String, nullable=False, default="native", server_default="native"
    )
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    tags: Mapped[str] = mapped_column(String, nullable=False, default="")
    source_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    source_terminal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    token_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    # 3-factor scoring. ``access_count`` feeds the usage factor;
    # ``last_accessed_at`` backs a server-side rate-limit on increments. NOT
    # NULL DEFAULT 0 so existing rows read as "never recalled" without a
    # backfill. Migrated onto existing DBs by ``_migrate_add_access_count``.
    access_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # LLM wiki compilation. NULL = never LLM-compiled (pre-existing rows, or
    # every compile attempt fell back to append). Non-NULL = UTC timestamp of
    # the last successful compile.
    last_compiled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # Comma-separated sanitised keys of cross-referenced articles. NULL =
    # never computed (pre-existing rows or LLM error). ``""`` = computed, no
    # related found (success — distinct from NULL to avoid endless retries).
    # Practical max ≤ 256 bytes (3 keys × 60 chars + 2 commas). The CHECK
    # constraint applies on FRESH databases only — existing DBs rely on the
    # parse-side cap in ``_parse_related_keys``.
    related_keys: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    __table_args__ = (
        UniqueConstraint("key", "scope", "scope_id", "source_kind", name="uq_memory_key_scope"),
        # SQLite treats NULL scope_id values as distinct in the table-level
        # constraint. Keep PR #674's source_kind-aware identity while enforcing
        # issue #657 uniqueness within each global/federated source tier.
        Index(
            "uq_memory_key_scope_null",
            "key",
            "scope",
            "source_kind",
            unique=True,
            sqlite_where=text("scope_id IS NULL"),
        ),
        CheckConstraint(
            "related_keys IS NULL OR length(related_keys) < 1024",
            name="ck_related_keys_length",
        ),
    )


# Vault-note identity needs a non-null scope id for global mappings: SQLite
# considers NULL values distinct inside UNIQUE indexes. This is table-local;
# memory_metadata keeps its historical nullable global scope_id convention.
VAULT_NOTE_SCOPE_ID_SENTINEL = ""


class VaultNoteModel(Base):
    """Durable projection metadata for a note indexed from an Obsidian vault."""

    __tablename__ = "vault_note"

    note_uid: Mapped[str] = mapped_column(String, primary_key=True)
    vault_id: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    scope_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default=VAULT_NOTE_SCOPE_ID_SENTINEL,
        server_default=VAULT_NOTE_SCOPE_ID_SENTINEL,
    )
    cao_key: Mapped[str] = mapped_column(String, nullable=False)
    vault_relpath: Mapped[str] = mapped_column(String, nullable=False)
    managed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    frontmatter_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mtime_ns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    key_source: Mapped[str | None] = mapped_column(String, nullable=True)
    key_source_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("vault_id", "scope", "scope_id", "cao_key", name="uq_vault_note_key"),
        UniqueConstraint("vault_id", "vault_relpath", name="uq_vault_note_path"),
    )


class VaultExclusionModel(Base):
    """Authoritative user-forget intent for a vault memory identity."""

    __tablename__ = "vault_exclusion"

    vault_id: Mapped[str] = mapped_column(String, primary_key=True)
    scope: Mapped[str] = mapped_column(String, primary_key=True)
    scope_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=VAULT_NOTE_SCOPE_ID_SENTINEL,
        server_default=VAULT_NOTE_SCOPE_ID_SENTINEL,
    )
    cao_key: Mapped[str] = mapped_column(String, primary_key=True)
    last_known_relpath: Mapped[str] = mapped_column(String, nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    key_source: Mapped[str | None] = mapped_column(String, nullable=True)
    key_source_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class VaultMigrationReceiptModel(Base):
    """Durable authority binding one native snapshot to its vault migration."""

    __tablename__ = "vault_migration_receipt"

    receipt_id: Mapped[str] = mapped_column(String, primary_key=True)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    scope_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default=VAULT_NOTE_SCOPE_ID_SENTINEL,
        server_default=VAULT_NOTE_SCOPE_ID_SENTINEL,
    )
    cao_key: Mapped[str] = mapped_column(String, nullable=False)
    native_relpath: Mapped[str] = mapped_column(String, nullable=False)
    native_snapshot_sha256: Mapped[str] = mapped_column(String, nullable=False)
    vault_id: Mapped[str] = mapped_column(String, nullable=False)
    managed_relpath: Mapped[str] = mapped_column(String, nullable=False)
    vault_note_uid: Mapped[str] = mapped_column(String, nullable=False)
    published_content_sha256: Mapped[str] = mapped_column(String, nullable=False)
    superseded_edges: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="active", server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class VaultFindingModel(Base):
    """Content-free finding emitted while reconciling a vault."""

    __tablename__ = "vault_finding"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    vault_id: Mapped[str] = mapped_column(String, nullable=False)
    vault_relpath: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(String, nullable=False)
    reconcile_run_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class VaultNoteAliasModel(Base):
    """Former vault paths retained to make note renames observable."""

    __tablename__ = "vault_note_alias"

    vault_id: Mapped[str] = mapped_column(String, primary_key=True)
    former_relpath: Mapped[str] = mapped_column(String, primary_key=True)
    cao_key: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str | None] = mapped_column(String, nullable=True)
    scope_id: Mapped[str | None] = mapped_column(String, nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class VaultRecallCounterModel(Base):
    """Durable, content-free operational counters for vault recall outcomes."""

    __tablename__ = "vault_recall_counter"

    vault_id: Mapped[str] = mapped_column(String, primary_key=True)
    counter_name: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


# Relationship-store sentinel: ``memory_relationships.scope_id`` is NOT NULL and
# stores this value for global/federated scope. SQLite treats ``NULL != NULL``
# in a UNIQUE index, so a nullable scope_id would make the dedup index (and thus
# ``INSERT ... ON CONFLICT``) inert for global scope — silently duplicating
# exactly the edges hardest to notice. Storing a NOT-NULL sentinel keeps the
# dedup tuple total. ``""`` cannot collide with a real sanitized scope_id
# (``MemoryService._sanitize_key``/``_sanitize_scope_id`` never yield empty —
# the latter returns ``"unknown"``). This sentinel is scoped to the
# ``memory_relationships`` table ONLY; ``MemoryMetadataModel.scope_id`` remains
# genuinely nullable (stores real NULL for global), so cross-table endpoint
# checks against it use logical ``None`` + ``.is_(None)`` (see the relationship
# service), never this sentinel.
RELATIONSHIP_SCOPE_ID_SENTINEL = ""


class MemoryRelationshipModel(Base):
    """SQLAlchemy model for a typed, durable memory relationship edge (issue #511).

    The authoritative relationship store that replaces the lossy
    ``memory_metadata.related_keys`` text column. One row per typed edge between
    two memory keys in the same ``(scope, scope_id)``. Written and read ONLY
    through ``MemoryRelationshipService`` — no other component issues SQL against
    this table (FR-2.1 single-boundary invariant).

    ``related_keys`` on ``MemoryMetadataModel`` is retained UNCHANGED as the
    compiler's computation-state marker (NULL = never computed/error, ``""`` =
    computed-empty) and is NOT modified or retired by this table (retirement is a
    separate, later change gated on a loss-free proof).
    """

    __tablename__ = "memory_relationships"

    # Application-generated uuid4 string PK, matching ``MemoryMetadataModel.id``
    # (str(uuid4())). API-stable identifier exposed in mutation responses.
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    scope: Mapped[str] = mapped_column(String, nullable=False)
    # NOT NULL: sentinel RELATIONSHIP_SCOPE_ID_SENTINEL ("") for global/federated
    # so the dedup UNIQUE index is total (see the sentinel comment above).
    scope_id: Mapped[str] = mapped_column(String, nullable=False)
    source_key: Mapped[str] = mapped_column(String, nullable=False)
    target_key: Mapped[str] = mapped_column(String, nullable=False)
    # Closed taxonomy reusing the graph EdgeType values.
    type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # relates_to | contradiction | supersedes
    # compiler | wiki_lint | human | legacy_related_keys | external_import(reserved) | vault
    origin: Mapped[str] = mapped_column(String, nullable=False)
    # active | proposal | rejected | superseded | deleted (auditable soft-delete)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    # Optional evidence metadata. NULL = no evidence (NEVER fabricated / coerced
    # to 0); a stored value is a validated REAL in [0, 1].
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    # Optional ordering hint (e.g. legacy related_keys position). NULL if none.
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    # Bounded JSON blob. NULL if none; the CHECK caps FRESH DBs, the service
    # caps existing DBs (mirrors the ck_related_keys_length precedent).
    attributes_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # The source memory's updated_at at write time; basis for staleness
    # detection (an edge is stale when this predates the source's current
    # updated_at). NULL when unknown.
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        # Dedup: differing type or origin coexist as distinct rows (multi-edge +
        # provenance-aware coexistence); a repeat of the same tuple upserts.
        # Every column is non-NULL (scope_id sentinel), so the index and
        # ON CONFLICT fire for ALL scopes including global.
        UniqueConstraint(
            "scope",
            "scope_id",
            "source_key",
            "target_key",
            "type",
            "origin",
            name="uq_memory_rel",
        ),
        # FRESH-DB CHECKs only (SQLite cannot retro-add a CHECK); the service
        # validates confidence range and attributes size on existing DBs.
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_memory_rel_confidence_range",
        ),
        CheckConstraint(
            "attributes_json IS NULL OR length(attributes_json) <= 2048",
            name="ck_memory_rel_attributes_size",
        ),
    )


class ProjectAliasModel(Base):
    """SQLAlchemy model for project identity aliases (Phase 2.5 U6).

    Maps historical/alternate project identifiers (cwd hashes, manual labels)
    to a canonical ``project_id`` so memory recall survives directory rename
    and worktree layouts.
    """

    __tablename__ = "project_aliases"

    # ``alias`` is the sole primary key: an alias maps to exactly one canonical
    # project_id, so reverse lookups (get_project_id_by_alias) are stable. A
    # cwd-hash first resolved via an override and later via its git remote
    # upserts the same row rather than creating a second, ambiguous mapping.
    alias: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(
        String, nullable=False
    )  # "git_remote" | "cwd_hash" | "manual"
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WorkflowOutcomeModel(Base):
    """SQLAlchemy model for workflow outcome records (self-learning Phase 1).

    One row per reported outcome of a unit of agent work (a workflow step,
    a package conversion, a review round). Outcomes are the raw signal the
    retrospector agent distills into memory lessons — they carry short
    labels and notes, never transcripts or file contents.
    """

    __tablename__ = "workflow_outcomes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_name: Mapped[str] = mapped_column(String, nullable=False)
    workflow_name: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # optional grouping label
    task_label: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "convert package X"
    agent_profile: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # profile that did the work
    source_terminal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # optional 0-100 metric
    friction_notes: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )  # short, content-free
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_utcnow)


class FlowModel(Base):
    """SQLAlchemy model for flow metadata."""

    __tablename__ = "flows"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    schedule: Mapped[str] = mapped_column(String, nullable=False)
    agent_profile: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    script: Mapped[str | None] = mapped_column(String, nullable=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    enabled: Mapped[bool | None] = mapped_column(Boolean, default=True)


class HandoffResultModel(Base):
    """Durable record of a handoff step result (issue #447).

    The caller generates a ``job_id`` and passes it to ``POST /terminals/run-step``;
    the server upserts on that key. Client-side generation exists so the MCP client
    holds the key BEFORE the request it might not get an answer to -- NOT for
    deduplication: ``_handoff_impl`` mints a fresh ``uuid4().hex`` per call, so a
    retry carries a different key and runs a second step.

    ``state``:
      - ``"running"`` — step in progress (written by the run-step handler at
        request start, after the generation fence)
      - ``"completed"`` — step finished successfully; ``last_message`` populated.
        Written inside ``run_agent_step``, between result extraction and terminal
        teardown -- the terminal is the only other copy of the result, so the row
        must exist before it is destroyed.
      - ``"error"`` — step failed; ``error_message`` populated. Written by the
        run-step handler's failure arms, which are the only place that can tell
        which exception occurred.

    ``created_at``/``updated_at`` carry ``DateTime(timezone=True)``, which is a
    no-op on SQLite: the offset is dropped on write, so the stored values are
    NAIVE UTC. The retention sweep must therefore compare against a UTC cutoff --
    see ``cleanup_service.cleanup_old_data``.
    """

    __tablename__ = "handoff_results"

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, nullable=False)  # "running" | "completed" | "error"
    terminal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    last_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class IdempotencyKeyModel(Base):
    """Maps a caller-supplied idempotency key to the terminal it created.

    Review on PR #634, issue #616: a caller that retries the same logical
    create-terminal request (e.g. ``cao agent handoff`` killed after the
    server committed a terminal but before the HTTP response reached the
    client) supplies the SAME key on retry. ``create_terminal`` looks it up
    BEFORE doing any real work (tmux window, provider process) and returns
    the terminal that key already produced instead of creating a second one.

    ``key`` is the primary key (not just unique) specifically so a second
    ``INSERT`` for an already-claimed key raises ``IntegrityError`` at
    ``commit()`` time rather than silently overwriting the first mapping --
    the row is written once, by whichever caller's transaction commits
    first (see ``create_terminal``'s own docs for what happens to the loser
    of that rare race).
    """

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    terminal_id: Mapped[str] = mapped_column(String, nullable=False)
    # sha256 hexdigest of the REQUESTED create fields (review on PR #634).
    # Without it a key means only "some earlier call anywhere on this server
    # used this string", not "this is a retry of THIS request" -- so a second
    # caller reusing a common key (`retry`, `job-1`) was handed the first
    # caller's terminal, and `_handoff_impl` then delivered its prompt into
    # someone else's running worker. `terminal_service._request_fingerprint`
    # owns the computation; see its docstring for why REQUESTED and not
    # resolved values.
    #
    # `nullable=False` with NO default, deliberately: this column and this
    # TABLE ship in the same create-table DDL (neither `idempotency_keys` nor
    # `IdempotencyKeyModel` exists on main or in ANY released tag through
    # v2.5.0), so no pre-existing database can hold a row without one and
    # there is nothing to migrate. A blank fingerprint is therefore not a
    # legacy row to tolerate -- it can only come from a scratch sqlite built
    # from an earlier revision of this branch, whose fix is deleting the file.
    # It is compared like any other value and simply mismatches, loudly.
    request_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.now)


def _ensure_db_dir() -> None:
    """Create the DB dir owner-only (0o700).

    The DB stores sensitive data (workflow spec_snapshot carries full prompt
    bodies + inputs_json), so the dir is owner-only — the same posture as
    claude_code prompt files (0o600) and the audit log (0o700/0o600). mkdir's
    mode is ignored when the dir already exists (exist_ok) and is masked by
    umask on creation — the chmod enforces 0o700 in both cases, best-effort.
    """
    DB_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(DB_DIR, 0o700)
    except OSError as e:
        logger.warning(f"Could not restrict DB dir permissions on {DB_DIR}: {e}")


# Module-level singletons
_ensure_db_dir()
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Initialize database tables and apply schema migrations."""
    _migrate_project_aliases_schema()
    Base.metadata.create_all(bind=engine)
    from cli_agent_orchestrator.clients.runtime_channel_schema import RemoteBase

    RemoteBase.metadata.create_all(bind=engine)
    _restrict_db_file_permissions()
    _migrate_terminals_schema()
    _migrate_workflow_plan_snapshot()
    _migrate_add_access_count()
    _migrate_add_last_compiled_at()
    _migrate_add_related_keys()
    # Must run after additive legacy-column migrations and before the separate
    # index migrator, which recreates the three secondary indexes after a rebuild.
    _migrate_memory_source_kind()
    _migrate_memory_indexes()
    _migrate_workflow_index()
    _migrate_workflow_run()
    _migrate_workflow_run_indexes()
    _migrate_workflow_run_step()
    _migrate_workflow_outcome_indexes()
    _migrate_workflow_run_event()
    _migrate_workflow_run_seq()
    # Appended LAST (issue #511). Disjoint from the workflow_run* tables that
    # #504 also migrates, so registry order is immaterial — never reorder the
    # entries above.
    _migrate_memory_relationships()
    # Appended LAST (issue #583 Bolt 2, ``approval-store``). Disjoint from every table above —
    # its own new table, no shared columns — so registry order is immaterial here too.
    _migrate_workflow_plan_approval()
    # Add the nullable columns before the exclusion backfill so a pre-existing
    # excluded note can carry its provenance into the durable tombstone.
    _migrate_vault_key_provenance()
    # Appended LAST (PR #674). Disjoint from every table above except for the
    # one-time backfill read from vault_note.
    _migrate_vault_exclusions()
    # Appended LAST (PR #674 S5). One additive receipt table; no backfill.
    _migrate_vault_migration_receipts()
    # Appended LAST (issue #657). Runs after the source_kind table rebuild so
    # the partial index enforces the full PR #674 memory identity.
    _migrate_memory_scope_null_uniqueness()
    # Work admission requires a verified additive schema; failure must propagate.
    from cli_agent_orchestrator.clients.work_repository import initialize_work_store

    initialize_work_store()
    _migrate_workflow_continuations()
    # Appended LAST (issue #447, ``handoff_results``). Its own new table, no shared
    # columns with anything above, so registry order is immaterial here too.
    _migrate_add_handoff_results()
    _migrate_local_peer_principal()


def _migrate_local_peer_principal() -> None:
    """Expand peer receipts without inventing an owner for legacy records.

    Old readers ignore this nullable column. Failed migration blocks startup;
    repeat application and concurrent startup preserve all existing receipts.
    """
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(local_peer_tasks)")
        }
        if columns and "requester_principal_id" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE local_peer_tasks ADD COLUMN requester_principal_id TEXT"
            )
        connection.commit()


def init_runtime_db() -> None:
    """Initialize ordinary bridge dependencies without creating Work authority.

    Existing Work tables are preserved and checked by the ordinary ownership
    guard. This initializer never creates, migrates or clears a Work ledger.
    """
    names = {
        "terminals",
        "session_incarnations",
        "inbox",
        "idempotency_keys",
        "terminal_turn_receipts",
        "terminal_turn_recovery",
        "terminal_late_observations",
        "native_children",
        "memory_metadata",
        "memory_relationships",
        "project_aliases",
        "vault_note",
        "vault_exclusion",
        "vault_recall_counter",
        "vault_note_alias",
    }
    Base.metadata.create_all(
        bind=engine, tables=[table for name, table in Base.metadata.tables.items() if name in names]
    )
    from cli_agent_orchestrator.clients.runtime_channel_schema import RemoteBase

    RemoteBase.metadata.create_all(bind=engine)
    # Existing terminal schema migration uses the configured private DB path.
    if str(engine.url.database) == str(constants.DATABASE_FILE):
        _migrate_terminals_schema()
    _restrict_db_file_permissions()


def _restrict_db_file_permissions() -> None:
    """Chmod the SQLite file (+ -wal/-shm siblings if present) to 0o600.

    The DB persists sensitive data (workflow spec_snapshot prompt bodies,
    inputs_json), matching the owner-only posture of prompt files and the audit
    log. Called after ``create_all`` so the file exists. Best-effort: a chmod
    failure (exotic filesystems) degrades permissions only, never blocks startup.
    """
    from cli_agent_orchestrator.constants import DATABASE_FILE

    for path in (
        DATABASE_FILE,
        DATABASE_FILE.with_name(DATABASE_FILE.name + "-wal"),
        DATABASE_FILE.with_name(DATABASE_FILE.name + "-shm"),
    ):
        if not path.exists():
            continue
        try:
            os.chmod(path, 0o600)
        except OSError as e:
            logger.warning(f"Could not restrict DB file permissions on {path}: {e}")


def _migrate_add_handoff_results() -> None:
    """Create the handoff_results table on existing databases (issue #447).

    ``Base.metadata.create_all`` already handles fresh databases; this
    idempotent migration handles existing ones where the table does not
    exist yet.  SQLite supports ``CREATE TABLE IF NOT EXISTS``, so we
    delegate to raw SQL rather than a full schema rebuild.

    The bare ``DATETIME`` columns here and the ORM model's
    ``DateTime(timezone=True)`` are not a divergence in what gets STORED:
    ``timezone=True`` is a no-op on SQLite, which keeps no offset either way, so
    both paths hold naive UTC wall-clock (the writer's default is ``_utcnow``).
    Registered LAST in ``init_db`` and order-independent: it touches its own new
    table and no column of any other, so it neither depends on nor perturbs the
    migrators above it.
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS handoff_results (
                    job_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    terminal_id TEXT,
                    last_message TEXT,
                    error_message TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """)
            conn.commit()
    except Exception as e:
        logger.warning(f"Migration check for handoff_results failed: {e}")


def _migrate_project_aliases_schema() -> None:
    """Rebuild project_aliases if it predates the alias-only primary key.

    The table originally used a composite PK ``(project_id, alias)``, which
    allowed one alias to map to several project_ids and made reverse lookups
    nondeterministic. The new schema keys on ``alias`` alone. SQLite cannot
    alter a primary key in place, so drop and recreate. The table is an
    opportunistic identity cache rebuilt by ``resolve_project_id`` on demand,
    so dropping rows is safe. Runs before ``create_all`` so the fresh schema
    is created with the new PK.
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master " "WHERE type='table' AND name='project_aliases'"
            ).fetchone()
            if row is None:
                return  # table doesn't exist yet — create_all builds it fresh
            cols = conn.execute("PRAGMA table_info(project_aliases)").fetchall()
            # PRAGMA returns rows: (cid, name, type, notnull, dflt_value, pk).
            # In the legacy schema both project_id and alias have pk>0; in the
            # new schema only alias does.
            pk_cols = {c[1] for c in cols if c[5]}
            if pk_cols != {"alias"}:
                conn.execute("DROP TABLE project_aliases")
                conn.commit()
                logger.info("Migration: rebuilt project_aliases with alias-only primary key")
    except Exception as e:
        logger.debug(f"project_aliases migration skipped: {e}")


def _migrate_memory_indexes() -> None:
    """Add explicit indexes on memory_metadata for query performance."""
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory_metadata (scope, scope_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_updated ON memory_metadata (updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_metadata (memory_type)"
            )
    except Exception as e:
        logger.debug(f"Memory index migration skipped: {e}")


def _migrate_memory_source_kind() -> None:
    """Widen memory identity with a non-null source discriminator.

    SQLite cannot alter a UNIQUE constraint, so installed databases require a
    transactional table rebuild.  The gate compares UNIQUE-index column lists,
    not index names: SQLite discards names given to table-level constraints.
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    expected_unique_columns = ("key", "scope", "scope_id", "source_kind")
    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_metadata'"
            ).fetchone()
            if table_exists is None:
                return
            columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_metadata)")}
            unique_indexes = [
                row[1]
                for row in conn.execute("PRAGMA index_list(memory_metadata)").fetchall()
                if row[3] == "u"
            ]
            if any(
                tuple(
                    column[2]
                    for column in conn.execute(
                        f'PRAGMA index_info("{index_name.replace(chr(34), chr(34) * 2)}")'
                    ).fetchall()
                )
                == expected_unique_columns
                for index_name in unique_indexes
            ):
                if "source_kind" not in columns:
                    raise RuntimeError(
                        "memory_metadata unique index references missing source_kind"
                    )
                return

            conn.execute("BEGIN")
            conn.execute("""
                CREATE TABLE memory_metadata_new (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    key VARCHAR NOT NULL,
                    memory_type VARCHAR NOT NULL,
                    scope VARCHAR NOT NULL,
                    scope_id VARCHAR,
                    source_kind VARCHAR NOT NULL DEFAULT 'native',
                    file_path VARCHAR NOT NULL,
                    tags VARCHAR NOT NULL,
                    source_provider VARCHAR,
                    source_terminal_id VARCHAR,
                    token_estimate INTEGER,
                    created_at DATETIME,
                    updated_at DATETIME,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    last_accessed_at DATETIME,
                    last_compiled_at DATETIME,
                    related_keys TEXT,
                    CONSTRAINT uq_memory_key_scope UNIQUE (key, scope, scope_id, source_kind)
                )
                """)
            conn.execute("""
                INSERT INTO memory_metadata_new (
                    id, key, memory_type, scope, scope_id, source_kind, file_path, tags,
                    source_provider, source_terminal_id, token_estimate, created_at, updated_at,
                    access_count, last_accessed_at, last_compiled_at, related_keys
                )
                SELECT
                    id, key, memory_type, scope, scope_id, 'native', file_path, tags,
                    source_provider, source_terminal_id, token_estimate, created_at, updated_at,
                    access_count, last_accessed_at, last_compiled_at, related_keys
                FROM memory_metadata
                """)
            conn.execute("DROP TABLE memory_metadata")
            conn.execute("ALTER TABLE memory_metadata_new RENAME TO memory_metadata")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_metadata)")}
            if "source_kind" not in columns:
                raise RuntimeError("memory_metadata rebuild did not add source_kind")
            conn.commit()
            logger.info("Migration: widened memory_metadata identity with source_kind")
    except Exception as e:
        logger.error(f"Memory source_kind migration failed: {e}")
        raise


def _migrate_add_access_count() -> None:
    """Add access_count and last_accessed_at columns to memory_metadata if missing.

    Idempotent: PRAGMA table_info gate, ALTER TABLE ADD COLUMN only
    when missing. Fresh DBs already have the columns from
    ``Base.metadata.create_all``. Existing rows get ``0`` / ``NULL`` — the
    correct values for "never recalled".
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            cursor = conn.execute("PRAGMA table_info(memory_metadata)")
            columns = {row[1] for row in cursor.fetchall()}
            if "access_count" not in columns:
                conn.execute(
                    "ALTER TABLE memory_metadata ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0"
                )
                logger.info("Migration: added access_count column to memory_metadata")
            if "last_accessed_at" not in columns:
                conn.execute("ALTER TABLE memory_metadata ADD COLUMN last_accessed_at DATETIME")
                logger.info("Migration: added last_accessed_at column to memory_metadata")
    except Exception as e:
        logger.debug(f"Migration check for access_count failed: {e}")


def _migrate_add_last_compiled_at() -> None:
    """Add last_compiled_at column to memory_metadata if missing.

    Idempotent: skipped on fresh DBs (the column ships in the model) and on
    repeated runs. Existing Phase 1/2 rows get NULL — correct, since they were
    never LLM-compiled.
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            cursor = conn.execute("PRAGMA table_info(memory_metadata)")
            columns = {row[1] for row in cursor.fetchall()}
            if "last_compiled_at" not in columns:
                conn.execute("ALTER TABLE memory_metadata ADD COLUMN last_compiled_at DATETIME")
                logger.info("Migration: added last_compiled_at column to memory_metadata")
    except Exception as e:
        logger.debug(f"Migration check for last_compiled_at failed: {e}")


def _migrate_add_related_keys() -> None:
    """Add related_keys column to memory_metadata if missing.

    Reuses the idempotent ALTER pattern: PRAGMA table_info gate, ALTER TABLE
    ADD COLUMN only when missing. The CHECK(length < 1024) constraint applies
    to FRESH DBs only — adding a CHECK to an existing SQLite table requires a
    full table rebuild we deliberately avoid. Existing DBs rely on the
    parse-side 1024-byte cap in ``_parse_related_keys``.
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            cursor = conn.execute("PRAGMA table_info(memory_metadata)")
            columns = {row[1] for row in cursor.fetchall()}
            if "related_keys" not in columns:
                conn.execute("ALTER TABLE memory_metadata ADD COLUMN related_keys TEXT")
                logger.info("Migration: added related_keys column to memory_metadata")
    except Exception as e:
        logger.debug(f"Migration check for related_keys failed: {e}")


def _migrate_memory_relationships() -> None:
    """Create the ``memory_relationships`` table + indexes and backfill legacy
    links (issue #511). Appended LAST to the ``init_db()`` registry.

    Idempotent, zero-arg, self-connecting — mirrors the existing migrators.
    Failure is logged at debug and never propagated (a missing table is
    recoverable; the service degrades). ``CREATE TABLE IF NOT EXISTS`` covers
    existing DBs where ``Base.metadata.create_all`` (which builds the model with
    its CHECK constraints on fresh DBs) has already run or will run — the same
    fresh-vs-existing split the codebase uses for ``related_keys``.

    Disjoint from the ``workflow_run*`` tables (#504); registry order is
    immaterial.
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_relationships ("
                "id TEXT PRIMARY KEY, "
                "scope TEXT NOT NULL, "
                "scope_id TEXT NOT NULL, "
                "source_key TEXT NOT NULL, "
                "target_key TEXT NOT NULL, "
                "type TEXT NOT NULL, "
                "origin TEXT NOT NULL, "
                "status TEXT NOT NULL DEFAULT 'active', "
                "confidence REAL, "
                "rank INTEGER, "
                "attributes_json TEXT, "
                "source_updated_at DATETIME, "
                "created_at DATETIME, "
                "updated_at DATETIME"
                ")"
            )
            # Dedup UNIQUE index — total because scope_id is NOT NULL (sentinel),
            # so ON CONFLICT fires for all scopes including global.
            #
            # ACCEPTED REDUNDANCY on a FRESH db (human review, PR #524): there,
            # create_all() has already satisfied the model's UniqueConstraint via
            # an unnamed sqlite_autoindex, so this statement adds a SECOND index
            # over identical columns (the name matches the constraint, but SQLite
            # does not treat a table-level UNIQUE as a named index, so
            # IF NOT EXISTS does not suppress it). Kept deliberately: this
            # migrator must remain zero-arg and idempotent for EXISTING dbs,
            # where CREATE TABLE IF NOT EXISTS is a no-op and this is the ONLY
            # thing that establishes the dedup index that replace_set/create rely
            # on. Making it fresh-db-aware would mean probing pragma index_list
            # and branching — more moving parts in a path whose failure mode is
            # silent duplicate edges. The cost is one extra index on new
            # installs: some write amplification and disk, no correctness or
            # query-plan impact.
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_rel ON memory_relationships "
                "(scope, scope_id, source_key, target_key, type, origin)"
            )
            # Lookup index for the common (scope, scope_id, source_key) read path.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_rel_lookup ON memory_relationships "
                "(scope, scope_id, source_key)"
            )
            conn.commit()
            _backfill_legacy_related_keys(conn)
    except Exception as e:
        logger.debug(f"memory_relationships migration skipped: {e}")


def _migrate_workflow_plan_approval() -> None:
    """Create the durable ``workflow_plan_approval`` table if missing (issue #583 Bolt 2, ``approval-store``).

    FR-8's re-approval mechanism: one row per APPROVED PLAN, keyed by the ``plan_id`` that
    ``plan_identifier.compute`` derives from a run's execution-affecting fields. A changed plan produces a
    different ``plan_id``, finds no row, and is refused until it is approved in its own right.

    ``plan_id`` IS THE PRIMARY KEY, so one-approval-per-plan is enforced by the database rather than by code
    remembering to check. Combined with ``INSERT OR IGNORE`` in ``services/approval_store.py``, that makes an
    approval WRITE-ONCE: a repeated grant cannot overwrite the original ``approved_at`` / ``approved_by``, and
    there is no update path at all. That absence is deliberate and is the unit's central control — an update
    would let an existing approval be pointed at a changed plan, so the row would read as approved while the
    work behind it had never been reviewed.

    KEYED BY PLAN, NOT BY RUN, and deliberately carrying NO foreign key to ``workflow_run``: an approval's
    lifetime is independent of any run, so deleting a run must not be able to revoke one.

    Idempotent, zero-arg, self-connecting; failure logged at debug and never propagated (B4-BR-1 / B4-RD-4),
    same precedent as every migrator above. Because that failure is SILENT, the table's existence is VERIFIED
    rather than assumed: see ``test/services/test_approval_store.py`` for the ``PRAGMA table_info`` assertion on
    a fresh database. A missing table makes every approval lookup answer False, which refuses every run —
    fail-closed, but diagnosed far from its cause.

    NOT registered in ``workflow_journal``'s ``_REQUIRED_RUN_COLUMNS`` / ``_REQUIRED_STEP_COLUMNS``. That
    verification is scoped to the columns the JOURNAL's own SQL reads, and this table is read by neither, so
    coupling the journal's connection cache to it would be wrong. The consequence is that this table gets no
    runtime self-healing the way ``manifest_json`` does; ``approval_store._connect`` runs this migrator on every
    connect instead, so a transient failure is retried on the next operation.
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS workflow_plan_approval ("
                "plan_id TEXT PRIMARY KEY, "
                "approved_at TEXT NOT NULL, "
                "approved_by TEXT NOT NULL"
                ")"
            )
    except Exception as e:  # noqa: BLE001 — derived/recoverable; logged at debug (B4-RD-4)
        logger.debug(f"workflow_plan_approval migration skipped: {e}")


def _migrate_memory_scope_null_uniqueness(engine: Any = None, *, strict: bool = False) -> None:
    """Create the partial unique index backing ``uq_memory_key_scope`` for
    NULL ``scope_id`` rows within each ``source_kind`` (issue #657). Appended LAST to the ``init_db()``
    registry.

    SQLite treats ``NULL != NULL`` in a UNIQUE index, so the table-level
    ``uq_memory_key_scope`` constraint has never fired for global/federated
    memories (``MemoryService.resolve_scope_id`` persists a real NULL for
    both — ``memory_metadata`` deliberately keeps the column nullable; the
    sentinel used by ``memory_relationships`` is wrong here). This index
    covers exactly those rows; non-NULL scopes stay on the table constraint.

    Idempotent, self-connecting — mirrors the existing migrators. ``engine``
    lets a caller bound the attempt to an existing SQLAlchemy engine (the
    memory-repair path passes its own); the default resolves the singleton
    ``DATABASE_FILE`` exactly like the other migrators. Fail-soft by design:
    on a database that still holds duplicate NULL-scope rows,
    ``CREATE UNIQUE INDEX`` raises ``IntegrityError``, so duplicates are
    pre-scanned and the index is skipped with a warning pointing at
    ``cao memory repair`` rather than blocking startup. The repair now
    re-invokes this migrator once its dedupe has cleared the duplicates, so
    the index lands in the same repair run instead of at the next startup.
    ``strict=True`` (the explicit ``cao memory repair --apply`` path) makes
    that re-invocation honest: DDL failure — e.g. a competing SQLite write
    lock — propagates instead of being logged at debug, and the named index
    is verified present before returning, so repair can no longer report
    success while the index is absent; the startup default stays fail-soft.
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    target = str(DATABASE_FILE)
    try:
        if engine is not None:
            # Reuse the caller's engine connection pool so the attempt and the
            # repairs share one database identity.
            with engine.connect() as conn:
                index_rows = conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'index' AND name = 'uq_memory_key_scope_null'"
                ).fetchall()
                if index_rows:
                    return
                duplicates = conn.exec_driver_sql(
                    "SELECT key, scope, source_kind, COUNT(*) FROM memory_metadata "
                    "WHERE scope_id IS NULL GROUP BY key, scope, source_kind HAVING COUNT(*) > 1"
                ).fetchall()
                if duplicates:
                    rendered = ", ".join(
                        f"{scope}:{key}[{source_kind}]x{count}"
                        for key, scope, source_kind, count in duplicates
                    )
                    logger.warning(
                        "Skipping uq_memory_key_scope_null creation: duplicate global/federated "
                        f"rows in memory_metadata ({rendered}). Run `cao memory repair` to "
                        "reconcile them; the unique index is created on the next startup."
                    )
                    return
                conn.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_key_scope_null "
                    "ON memory_metadata (key, scope, source_kind) WHERE scope_id IS NULL"
                )
                conn.commit()
                if strict:
                    created = conn.exec_driver_sql(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'index' AND name = 'uq_memory_key_scope_null'"
                    ).fetchall()
                    if not created:
                        raise RuntimeError(
                            "uq_memory_key_scope_null creation reported success but the "
                            "index is absent from sqlite_master"
                        )
            return
        with closing(sqlite3.connect(target)) as connection, connection as conn:
            duplicates = conn.execute(
                "SELECT key, scope, source_kind, COUNT(*) FROM memory_metadata "
                "WHERE scope_id IS NULL GROUP BY key, scope, source_kind HAVING COUNT(*) > 1"
            ).fetchall()
            if duplicates:
                rendered = ", ".join(
                    f"{scope}:{key}[{source_kind}]x{count}"
                    for key, scope, source_kind, count in duplicates
                )
                logger.warning(
                    "Skipping uq_memory_key_scope_null creation: duplicate global/federated "
                    f"rows in memory_metadata ({rendered}). Run `cao memory repair` to "
                    "reconcile them; the unique index is created on the next startup."
                )
                return
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_key_scope_null "
                "ON memory_metadata (key, scope, source_kind) WHERE scope_id IS NULL"
            )
            if strict:
                created = conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'index' AND name = 'uq_memory_key_scope_null'"
                ).fetchall()
                if not created:
                    raise RuntimeError(
                        "uq_memory_key_scope_null creation reported success but the "
                        "index is absent from sqlite_master"
                    )
    except Exception as e:  # noqa: BLE001 — derived/recoverable; logged at debug
        if strict:
            raise
        logger.debug(f"memory scope NULL uniqueness migration skipped: {e}")


def _migrate_vault_exclusions() -> None:
    """Create and backfill durable vault-forget identities.

    ``vault_note.status`` is a rebuildable projection and cannot safely retain
    user intent across path reuse, quarantine, or rebuild. Existing excluded
    rows are therefore copied into the identity-keyed authoritative table.
    Failure propagates because continuing without the backfill could republish
    content that the user explicitly forgot.
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS vault_exclusion ("
                "vault_id VARCHAR NOT NULL, "
                "scope VARCHAR NOT NULL, "
                "scope_id VARCHAR NOT NULL DEFAULT '', "
                "cao_key VARCHAR NOT NULL, "
                "last_known_relpath VARCHAR NOT NULL, "
                "content_sha256 VARCHAR, "
                "key_source VARCHAR, "
                "key_source_reason VARCHAR, "
                "created_at DATETIME NOT NULL, "
                "PRIMARY KEY (vault_id, scope, scope_id, cao_key)"
                ")"
            )
            note_table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'vault_note'"
            ).fetchone()
            if note_table_exists is None:
                return
            conn.execute(
                "INSERT OR IGNORE INTO vault_exclusion ("
                "vault_id, scope, scope_id, cao_key, last_known_relpath, "
                "content_sha256, key_source, key_source_reason, created_at"
                ") "
                "SELECT vault_id, scope, scope_id, cao_key, vault_relpath, "
                "content_sha256, key_source, key_source_reason, "
                "COALESCE(last_reconciled_at, CURRENT_TIMESTAMP) "
                "FROM vault_note WHERE status = 'excluded'"
            )
    except Exception as e:
        logger.error(f"Vault exclusion migration failed: {e}")
        raise


def _migrate_vault_migration_receipts() -> None:
    """Create the additive migration-receipt table on legacy databases."""
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            columns = conn.execute("PRAGMA table_info(vault_migration_receipt)").fetchall()
            if columns:
                return
            conn.execute(
                "CREATE TABLE vault_migration_receipt ("
                "receipt_id VARCHAR NOT NULL PRIMARY KEY, "
                "scope VARCHAR NOT NULL, "
                "scope_id VARCHAR NOT NULL DEFAULT '', "
                "cao_key VARCHAR NOT NULL, "
                "native_relpath VARCHAR NOT NULL, "
                "native_snapshot_sha256 VARCHAR NOT NULL, "
                "vault_id VARCHAR NOT NULL, "
                "managed_relpath VARCHAR NOT NULL, "
                "vault_note_uid VARCHAR NOT NULL, "
                "published_content_sha256 VARCHAR NOT NULL, "
                "superseded_edges TEXT NOT NULL DEFAULT '[]', "
                "status VARCHAR NOT NULL DEFAULT 'active', "
                "created_at DATETIME NOT NULL"
                ")"
            )
    except Exception as e:
        logger.error(f"Vault migration receipt schema migration failed: {e}")
        raise


def _migrate_vault_key_provenance() -> None:
    """Add nullable key provenance columns to legacy vault tables."""
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            for table in ("vault_note", "vault_exclusion"):
                table_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (table,),
                ).fetchone()
                if table_exists is None:
                    continue
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if "key_source" not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN key_source VARCHAR")
                if "key_source_reason" not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN key_source_reason VARCHAR")
    except Exception as e:
        logger.error(f"Vault key provenance schema migration failed: {e}")
        raise


def _backfill_legacy_related_keys(conn: Any) -> None:
    """One-time, idempotent backfill of ``memory_metadata.related_keys`` into
    ``memory_relationships`` as ``type=relates_to, origin=legacy_related_keys,
    status=active, confidence=NULL`` rows (issue #511, FR-1.4/FR-1.5).

    - Gated per source memory: if any ``legacy_related_keys`` row already exists
      for that ``(scope, scope_id, source_key)``, the source is skipped, so
      re-running ``init_db()`` is a no-op (idempotent).
    - ``related_keys IS NULL`` or ``""`` yields zero rows (never-computed /
      computed-empty carry no edge). The NULL-vs-"" marker stays on
      ``related_keys`` UNCHANGED — this backfill only READS it (ADR-4).
    - ``confidence`` is always NULL (never fabricated — NFR-2.1). Order is
      preserved as ``rank``.
    - A target that no longer resolves to an in-scope memory, a self-link, or a
      key that fails the sanitiser is REPORTED (logged) and NOT written active
      (FR-1.5) — never silently activated.
    - ``scope_id`` is normalised to the sentinel ``""`` for global/federated so
      the dedup index is total.

    Best-effort: any failure is logged at debug and never propagated (the
    service can compute relationships later; a partial backfill is safe because
    the per-source gate resumes cleanly).
    """
    # Lazy import to avoid a circular import (memory_service imports database).
    try:
        from cli_agent_orchestrator.services.memory_service import MemoryService
    except Exception as e:  # pragma: no cover - import guard
        logger.debug(f"backfill skipped (memory_service import): {e}")
        return

    now_iso = _utcnow().isoformat()
    reported: list[str] = []
    try:
        rows = conn.execute(
            "SELECT key, scope, scope_id, related_keys, updated_at "
            "FROM memory_metadata "
            "WHERE related_keys IS NOT NULL AND related_keys != ''"
        ).fetchall()
    except Exception as e:
        logger.debug(f"backfill skipped (memory_metadata read): {e}")
        return

    for key, scope, scope_id, related_keys, src_updated_at in rows:
        sentinel = scope_id if scope_id is not None else RELATIONSHIP_SCOPE_ID_SENTINEL
        # Per-source idempotency gate (exact = on the sentinel, never IS NULL).
        existing = conn.execute(
            "SELECT 1 FROM memory_relationships "
            "WHERE source_key = ? AND scope = ? AND scope_id = ? "
            "AND origin = 'legacy_related_keys' LIMIT 1",
            (key, scope, sentinel),
        ).fetchone()
        if existing is not None:
            continue

        targets = MemoryService._parse_related_keys(related_keys, scope)
        # Resolve which target keys actually exist in the SAME (scope, scope_id).
        for rank, target in enumerate(targets):
            if target == key:
                reported.append(f"{scope}/{scope_id}/{key}->{target}: self-link")
                continue
            # Endpoint existence against memory_metadata: scope_id is genuinely
            # nullable there (real NULL for global), so match logical NULL, NOT
            # the sentinel.
            if scope_id is None:
                found = conn.execute(
                    "SELECT 1 FROM memory_metadata "
                    "WHERE key = ? AND scope = ? AND scope_id IS NULL LIMIT 1",
                    (target, scope),
                ).fetchone()
            else:
                found = conn.execute(
                    "SELECT 1 FROM memory_metadata "
                    "WHERE key = ? AND scope = ? AND scope_id = ? LIMIT 1",
                    (target, scope, scope_id),
                ).fetchone()
            if found is None:
                reported.append(f"{scope}/{scope_id}/{key}->{target}: dangling")
                continue
            try:
                conn.execute(
                    "INSERT INTO memory_relationships "
                    "(id, scope, scope_id, source_key, target_key, type, origin, "
                    "status, confidence, rank, attributes_json, source_updated_at, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'relates_to', 'legacy_related_keys', "
                    "'active', NULL, ?, NULL, ?, ?, ?) "
                    "ON CONFLICT (scope, scope_id, source_key, target_key, type, origin) "
                    "DO NOTHING",
                    (
                        str(uuid.uuid4()),
                        scope,
                        sentinel,
                        key,
                        target,
                        rank,
                        src_updated_at,
                        now_iso,
                        now_iso,
                    ),
                )
            except Exception as e:
                logger.debug(f"backfill insert skipped for {key}->{target}: {e}")
    try:
        conn.commit()
    except Exception:  # pragma: no cover
        pass
    if reported:
        logger.warning(
            "memory_relationships backfill reported %d stale/malformed legacy "
            "links (NOT activated): %s",
            len(reported),
            "; ".join(reported[:20]),
        )


def _migrate_workflow_index() -> None:
    """Create/upgrade the derived ``workflow_index`` table (issue #312, N2).

    The table is a **derived, non-authoritative** projection of the workflow
    spec YAML files on disk (B2-BR-2): it can be dropped and rebuilt
    byte-identically from the files alone (``rebuild_index_from_files``). It
    carries no run/execution state — runs and per-step state are N5/N6.

    Idempotent (``CREATE TABLE IF NOT EXISTS``), zero-arg and self-connecting —
    mirrors the existing ``_migrate_memory_indexes`` pattern. Failure is logged
    at debug and never propagated (a missing index table is recoverable: the
    next ``list`` rebuilds it).

    U5 additively widens ``step_count`` to nullable: script-tier rows carry
    NULL (step count is run-time-determined, unknowable at index time), while
    YAML rows keep populating an int. ``CREATE TABLE IF NOT EXISTS`` only
    covers fresh DBs — on a pre-U5 DB the column already exists as NOT NULL,
    and SQLite cannot ``ALTER COLUMN`` to relax a NOT NULL constraint in
    place. Same drop/rebuild precedent as ``_migrate_project_aliases_schema``:
    the table is fully derived, so dropping it is safe — the next ``list``
    rebuilds it from the workflow files on disk.
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='workflow_index'"
            ).fetchone()
            if row is not None:
                cols = conn.execute("PRAGMA table_info(workflow_index)").fetchall()
                # PRAGMA row: (cid, name, type, notnull, dflt_value, pk).
                step_count_col = next((c for c in cols if c[1] == "step_count"), None)
                if step_count_col is not None and step_count_col[3]:  # notnull flag set
                    conn.execute("DROP TABLE workflow_index")
                    conn.commit()
                    logger.info(
                        "Migration: rebuilt workflow_index with nullable step_count "
                        "(dropped legacy table; rebuilt from workflow files on next list)"
                    )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS workflow_index ("
                "name TEXT PRIMARY KEY, "
                "source_path TEXT NOT NULL, "
                "mode TEXT NOT NULL, "
                "step_count INTEGER, "  # nullable: script-tier rows carry NULL
                "description TEXT NOT NULL DEFAULT '', "
                "indexed_at TEXT NOT NULL"
                ")"
            )
    except Exception as e:  # noqa: BLE001 — derived table; rebuilt on next list
        logger.debug(f"workflow_index migration skipped: {e}")


def _migrate_workflow_run() -> None:
    """Create the durable ``workflow_run`` journal table if missing (issue #312, N6).

    The run aggregate root: one row per run, keyed by ``run_id`` (E1,
    domain-entities). Per Q1=B this is the **source of truth** for run execution
    state; the Bolt-3 in-memory ``run_registry`` is a cache over it. No loop
    columns (``iteration_counter`` etc.) — deferred to N8 (Q4=B, B4-BR-12).

    Idempotent (``CREATE TABLE IF NOT EXISTS``), zero-arg and self-connecting —
    mirrors ``_migrate_workflow_index`` (B2, B4-BR-1). Failure is logged at debug
    and never propagated: a missing table is recoverable, the next write retries
    the path and the live run completes on the in-memory floor (B4-RD-4).

    U3 (issue #312, script-tier journal extension) additively appends two
    columns — ``tier`` and ``generation`` (E1, domain-entities) — via the same
    idempotent ``PRAGMA table_info`` gate used by ``_migrate_add_access_count`` /
    ``_migrate_add_related_keys``. Both default to values that make a pre-U3 /
    YAML row read identically to its pre-extension form (INV-1/INV-2): existing
    rows back-fill to ``tier='yaml'``, ``generation='1'``. ``generation`` is TEXT,
    not INTEGER, so it compares byte-identically against the env-var-transported
    string generation value (domain-entities B4 fix).

    ``manifest-column`` (issue #583 Bolt 2, ADR-583-12) additively appends ONE
    column, ``manifest_json``, through the same PRAGMA-gated idiom — the frozen
    execution manifest envelope, carrying source hash, inputs, repository and
    worktree baseline, provider, model, profile, permissions, limits, retry
    policy, the resolved-memory record, and the ``plan_id`` derived from them.
    ``DEFAULT NULL`` means "manifest absent", which every pre-Bolt-2 row is, so
    such a row reads back observably identical to its pre-extension form
    (INV-1/INV-2) and no back-fill is attempted — a manifest records how a run was
    LAUNCHED, which is not recoverable for a run that already started.

    Because this body's failure is silent (see the ``except`` below), the column's
    existence is VERIFIED rather than assumed: ``test_workflow_run_columns`` in
    ``test/clients/test_workflow_run_migration.py`` asserts it on a fresh database,
    with its TEXT type and NULL default. A silent failure would otherwise surface
    far from its cause, as every run losing its manifest — which the Bolt 2
    approval gate reads as "never approved" and refuses. That direction is
    fail-closed, but the diagnosis is still remote, hence the assertion.

    This column is NOT indexed (ADR-583-12: re-approval compares a ``plan_id``
    read out of the envelope and no query filters on it). Writing and reading it
    belong to the ``manifest-freeze`` unit, not to this migrator.

    Issue #753 additively appends nullable ``error`` for the redacted, bounded
    script-level failure diagnostic. Existing and non-script rows remain NULL.
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS workflow_run ("
                "run_id TEXT PRIMARY KEY, "
                "workflow_name TEXT NOT NULL, "
                "spec_snapshot TEXT NOT NULL, "
                "inputs_json TEXT NOT NULL, "
                "state TEXT NOT NULL, "
                "current_step_id TEXT, "
                "started_at TEXT NOT NULL, "
                "finished_at TEXT"
                ")"
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(workflow_run)")}
            if "tier" not in columns:
                conn.execute(
                    "ALTER TABLE workflow_run ADD COLUMN tier TEXT NOT NULL DEFAULT 'yaml'"
                )
                logger.info("Migration: added tier column to workflow_run")
            if "generation" not in columns:
                conn.execute(
                    "ALTER TABLE workflow_run ADD COLUMN generation TEXT NOT NULL DEFAULT '1'"
                )
                logger.info("Migration: added generation column to workflow_run")
            if "manifest_json" not in columns:
                conn.execute("ALTER TABLE workflow_run ADD COLUMN manifest_json TEXT DEFAULT NULL")
                logger.info("Migration: added manifest_json column to workflow_run")
            if "error" not in columns:
                conn.execute("ALTER TABLE workflow_run ADD COLUMN error TEXT DEFAULT NULL")
                logger.info("Migration: added error column to workflow_run")
    except Exception as e:  # noqa: BLE001 — derived/recoverable; logged at debug (B4-RD-4)
        logger.debug(f"workflow_run migration skipped: {e}")


def _migrate_workflow_run_step() -> None:
    """Create the durable ``workflow_run_step`` table if missing (issue #312, N6).

    Per-step durable state: one row per ``(run_id, step_id)`` (E2,
    domain-entities). ``reprompted``/``terminal_id`` are deliberately NOT
    journaled (F3) — they are in-memory-only and defaulted on rebuild. No
    ``which_guard_fired``/``iterations_run`` columns — N8 adds them via its own
    additive migrator (Q4=B, B4-BR-12).

    Idempotent, zero-arg, self-connecting; failure logged at debug and never
    propagated (B4-BR-1 / B4-RD-4), same precedent as ``_migrate_workflow_index``.

    U3 (issue #312, script-tier journal extension) additively appends
    ``call_fingerprint`` (E2, domain-entities) via the same idempotent
    ``PRAGMA table_info`` gate. Defaults to ``NULL`` so a pre-U3 / YAML row is
    indistinguishable from its pre-extension form (INV-1/INV-2); ``append_step``
    is the sole write path for the column (``update_step`` stays untouched — the
    fingerprint is set once, at the RUNNING insert).

    U1 (issue #504, event-log substrate) additively appends three nullable
    columns via the same PRAGMA-gated ``ALTER TABLE ADD COLUMN`` idiom:
    ``terminal_id`` (associated terminal), ``reprompted`` (reprompt flag), and
    ``error_kind`` (structured error kind). All default to ``NULL`` so a
    pre-U1 row reads back observably identical to its pre-extension form
    (additive-only, C-1/C-4). ``workflow_run`` itself is untouched.

    ``result-envelope`` (issue #583, BR-7) then additively appends ONE column,
    ``result_json``, through the same gate — the serialised ``StepResultEnvelope``
    that replay returns (FR-1). ``DEFAULT NULL`` means every pre-#583 row reads as
    "envelope absent" (BR-10), which is safe rather than a gap: such a row's
    fingerprint is legacy-scheme or NULL, so FR-6 already keeps it off the replay
    path and the two guards agree instead of disagreeing.

    Because the failure is silent, the column's existence is VERIFIED rather than
    assumed (BR-8): see ``test/services/test_step_result.py`` for the
    ``PRAGMA table_info`` assertion on a fresh database. A silent failure would
    otherwise surface far away as every settle losing its envelope, which the
    replay gate would read as crash-window rows and halt on.

    MERGE NOTE (2026-08-17, #583 x #504). #583's original rationale for adding ONE
    column rather than three read: "three statements would triple the chance of a
    partial, silent migration", because this body is wrapped in
    ``except Exception`` -> ``logger.debug``. **That argument is overtaken by the
    merge** — #504 independently added three columns to the same silently-failing
    block, so the combined body now issues FOUR guarded ``ALTER`` statements, not
    one. The risk #583 minimised is materially larger than either change assumed
    alone. #583's mitigation (assert the column exists on a fresh database) is
    therefore MORE load-bearing after this merge. Flagged rather than silently
    reconciled.

    CORRECTION (2026-08-18, issue #583 Bolt 2, unit ``manifest-column``). The
    sentence above previously ended by claiming that "#504's three columns have no
    equivalent assertion". **That claim was false and has been removed.** All three
    ARE asserted, in ``test/clients/test_workflow_run_migration.py``: ``terminal_id``,
    ``reprompted`` and ``error_kind`` appear in ``test_workflow_run_step_columns``'s
    exact ``set(cols) == {...}`` column set, and each carries a nullable check
    (``[3] == 0``) plus a default check (``[4] == "NULL"``) in the same test. The
    locations are named here so the denial cannot rot back: a reader who believed it
    would add a duplicate assertion to close a gap that does not exist. What DOES
    survive from the note above is the crowding itself — four guarded ``ALTER``
    statements under one silent ``except`` is a real and growing risk, and adopting a
    migration framework for it is recorded as a candidate decision (out of scope for
    a single additive column).
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS workflow_run_step ("
                "run_id TEXT NOT NULL, "
                "step_id TEXT NOT NULL, "
                "state TEXT NOT NULL, "
                "attempts INTEGER NOT NULL, "
                "output_json TEXT, "
                "error TEXT, "
                "updated_at TEXT NOT NULL, "
                "PRIMARY KEY (run_id, step_id)"
                ")"
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(workflow_run_step)")}
            if "call_fingerprint" not in columns:
                conn.execute(
                    "ALTER TABLE workflow_run_step ADD COLUMN call_fingerprint TEXT DEFAULT NULL"
                )
                logger.info("Migration: added call_fingerprint column to workflow_run_step")
            if "terminal_id" not in columns:
                conn.execute(
                    "ALTER TABLE workflow_run_step ADD COLUMN terminal_id TEXT DEFAULT NULL"
                )
                logger.info("Migration: added terminal_id column to workflow_run_step")
            if "reprompted" not in columns:
                conn.execute(
                    "ALTER TABLE workflow_run_step ADD COLUMN reprompted INTEGER DEFAULT NULL"
                )
                logger.info("Migration: added reprompted column to workflow_run_step")
            if "error_kind" not in columns:
                conn.execute(
                    "ALTER TABLE workflow_run_step ADD COLUMN error_kind TEXT DEFAULT NULL"
                )
                logger.info("Migration: added error_kind column to workflow_run_step")
            if "result_json" not in columns:
                conn.execute(
                    "ALTER TABLE workflow_run_step ADD COLUMN result_json TEXT DEFAULT NULL"
                )
                logger.info("Migration: added result_json column to workflow_run_step")
    except Exception as e:  # noqa: BLE001 — derived/recoverable; logged at debug (B4-RD-4)
        logger.debug(f"workflow_run_step migration skipped: {e}")


def _migrate_workflow_outcome_indexes() -> None:
    """Add indexes on workflow_outcomes for retrospector queries.

    The table itself is created by ``Base.metadata.create_all`` (it ships in
    the model, so fresh and existing DBs both get it). Retrospection filters
    by session and by agent profile over a recency window — index both.
    Idempotent, self-connecting, failure logged at debug — mirrors
    ``_migrate_memory_indexes``.
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_outcome_session "
                "ON workflow_outcomes (session_name, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_outcome_agent "
                "ON workflow_outcomes (agent_profile, created_at)"
            )
    except Exception as e:
        logger.debug(f"workflow_outcomes index migration skipped: {e}")


def _migrate_workflow_run_event() -> None:
    """Create the durable append-only ``workflow_run_event`` table if missing (issue #504, U1).

    The event log root: one row per emitted workflow domain event, keyed by the
    composite ``(run_id, seq)`` PRIMARY KEY (ADR-1, domain-entities). Per
    NFR-DUR-1 this table is the authoritative, append-only, versioned record of
    workflow execution — rows are inserted and never updated or reordered; ``seq``
    (a per-run monotonically increasing sequence) is the SOLE ordering authority,
    ``ts`` is display/duration only (BR-5). ``run_id``, ``seq``, ``event_type``,
    ``event_schema_version`` (FR-1.1) and ``ts`` are NOT NULL; the remaining
    columns are nullable and populated where applicable. ``iteration`` and
    ``which_guard_fired`` are RESERVED for a later deterministic-loops feature
    (FR-1.5) and stay NULL in the MVP.

    Idempotent (``CREATE TABLE IF NOT EXISTS``), zero-arg and self-connecting —
    mirrors ``_migrate_workflow_run`` (C-1/C-4, additive-only). Failure is logged
    at debug and never propagated: a missing table is recoverable, the next
    best-effort append retries the path.
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS workflow_run_event ("
                "run_id TEXT NOT NULL, "
                "seq INTEGER NOT NULL, "
                "event_type TEXT NOT NULL, "
                "event_schema_version INTEGER NOT NULL, "
                "ts TEXT NOT NULL, "
                "step_id TEXT, "
                "attempt INTEGER, "
                "state TEXT, "
                "elapsed_ms INTEGER, "
                "provider TEXT, "
                "agent_profile TEXT, "
                "engine TEXT, "
                "terminal_id TEXT, "
                "terminal_offset_start INTEGER, "
                "terminal_offset_len INTEGER, "
                "error_kind TEXT, "
                "reason TEXT, "
                "validation_result TEXT, "
                "output_ref TEXT, "
                "iteration INTEGER, "
                "which_guard_fired TEXT, "
                "PRIMARY KEY (run_id, seq)"
                ")"
            )
    except Exception as e:  # noqa: BLE001 — derived/recoverable; logged at debug
        logger.debug(f"workflow_run_event migration skipped: {e}")


def _migrate_workflow_run_seq() -> None:
    """Create the durable ``workflow_run_seq`` high-water table if missing (issue #504, U1).

    One row per run: ``high_water`` records the highest per-run ``seq`` ever
    ALLOCATED (best-effort persisted before the matching event append), so a
    rebuild can resume strictly above any allocated slot even when its append was
    swallowed (BR-3). ``high_water`` advances monotonically (BR-11) and is NOT
    NULL; ``run_id`` is the PRIMARY KEY.

    Idempotent (``CREATE TABLE IF NOT EXISTS``), zero-arg and self-connecting —
    same additive-only posture as ``_migrate_workflow_run_event``. Failure is
    logged at debug and never propagated.
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS workflow_run_seq ("
                "run_id TEXT PRIMARY KEY, "
                "high_water INTEGER NOT NULL"
                ")"
            )
    except Exception as e:  # noqa: BLE001 — derived/recoverable; logged at debug
        logger.debug(f"workflow_run_seq migration skipped: {e}")


def _migrate_workflow_run_indexes() -> None:
    """Add explicit indexes on ``workflow_run`` for list-query performance (U1, FR-3.2).

    Two single-column indexes serving the two shapes ``list_runs`` produces: the
    unfiltered newest-first list orders by ``started_at`` alone (served by
    ``idx_workflow_run_started_at``), and the state-filtered list narrows on
    ``state`` (served by ``idx_workflow_run_state``). Two single-column indexes
    cover both paths; a single composite ``(state, started_at)`` would not serve
    the unfiltered ``started_at``-only ordering (ADR-6, IR-1).

    Zero-arg, self-connecting, and idempotent — mirrors ``_migrate_memory_indexes``.
    Each statement uses ``CREATE INDEX IF NOT EXISTS`` so a second ``init_db()`` is
    a no-op (IR-2); no destructive migration, no Alembic (NFR-5). It creates only
    indexes, never columns — so the C-4 exact-column migration test is untouched
    (IR-4). Registered AFTER ``_migrate_workflow_run`` in ``init_db`` so the base
    table exists first. Failure is logged at debug and never raised: a missing
    index degrades to a table scan, not a crash (IR-3).
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_run_started_at "
                "ON workflow_run (started_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_run_state ON workflow_run (state)"
            )
    except Exception as e:  # noqa: BLE001 — missing index degrades to a scan (IR-3)
        logger.debug(f"workflow_run index migration skipped: {e}")


def _migrate_terminals_schema() -> None:
    """Add terminal metadata columns to existing SQLite databases."""
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as conn:
            cursor = conn.execute("PRAGMA table_info(terminals)")
            columns = {row[1] for row in cursor.fetchall()}
            if "allowed_tools" not in columns:
                conn.execute("ALTER TABLE terminals ADD COLUMN allowed_tools TEXT")
                conn.commit()
                logger.info("Migration: added allowed_tools column to terminals table")
            if "shell_command" not in columns:
                conn.execute("ALTER TABLE terminals ADD COLUMN shell_command TEXT")
                conn.commit()
                logger.info("Migration: added shell_command column to terminals table")
            if "caller_id" not in columns:
                conn.execute("ALTER TABLE terminals ADD COLUMN caller_id TEXT")
                conn.commit()
                logger.info("Migration: added caller_id column to terminals table")
            if "engine" not in columns:
                conn.execute("ALTER TABLE terminals ADD COLUMN engine TEXT")
                conn.commit()
                logger.info("Migration: added engine column to terminals table")
            if "kiro_policy_digest" not in columns:
                conn.execute("ALTER TABLE terminals ADD COLUMN kiro_policy_digest TEXT")
                conn.commit()
            if "provider_variant" not in columns:
                conn.execute("ALTER TABLE terminals ADD COLUMN provider_variant TEXT")
                conn.commit()
            if "group" not in columns:
                # "group" is a SQL reserved word in some dialects but not SQLite;
                # quoted defensively so this ALTER survives if that ever changes.
                conn.execute('ALTER TABLE terminals ADD COLUMN "group" TEXT')
                conn.commit()
                logger.info("Migration: added group column to terminals table")
            if "metadata" not in columns:
                conn.execute('ALTER TABLE terminals ADD COLUMN "metadata" TEXT')
                conn.commit()
                logger.info("Migration: added metadata column to terminals table")
            if "working_directory" not in columns:
                conn.execute("ALTER TABLE terminals ADD COLUMN working_directory TEXT")
                conn.commit()
                logger.info("Migration: added working_directory column to terminals table")
            if "deferred_init_failure" not in columns:
                conn.execute("ALTER TABLE terminals ADD COLUMN deferred_init_failure TEXT")
                conn.commit()
                logger.info("Migration: added deferred_init_failure column to terminals table")
            if "deferred_init_external_owner" not in columns:
                conn.execute(
                    "ALTER TABLE terminals ADD COLUMN deferred_init_external_owner "
                    "INTEGER NOT NULL DEFAULT 0"
                )
                conn.commit()
                logger.info(
                    "Migration: added deferred_init_external_owner column to terminals table"
                )
            if "deferred_init_runtime_reclaimed" not in columns:
                conn.execute(
                    "ALTER TABLE terminals ADD COLUMN deferred_init_runtime_reclaimed "
                    "INTEGER NOT NULL DEFAULT 0"
                )
                conn.commit()
                logger.info(
                    "Migration: added deferred_init_runtime_reclaimed column to terminals table"
                )
            if "session_incarnation_id" not in columns:
                conn.execute("ALTER TABLE terminals ADD COLUMN session_incarnation_id TEXT")
                conn.commit()
                logger.info("Migration: added session_incarnation_id column to terminals table")
    except Exception as e:
        logger.warning(f"Migration check for terminals schema failed: {e}")


def create_terminal(
    terminal_id: str,
    tmux_session: str,
    tmux_window: str,
    provider: str,
    agent_profile: Optional[str] = None,
    allowed_tools: Optional[List[str]] = None,
    shell_command: Optional[str] = None,
    caller_id: Optional[str] = None,
    engine: Optional[str] = None,
    provider_variant: Optional[str] = None,
    group: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    working_directory: Optional[str] = None,
    deferred_init_external_owner: bool = False,
    session_incarnation_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    request_fingerprint: Optional[str] = None,
    new_session_incarnation: bool = False,
    kiro_policy_digest: Optional[str] = None,
) -> Dict[str, Any]:
    """Create terminal metadata record.

    ``idempotency_key``, when given, is persisted in the SAME ``SessionLocal``
    session as the terminal row -- one ``commit()``, so SQLite's single-writer
    transaction covers both inserts atomically (review on PR #634, issue
    #616). This is what lets a retry with the same key find the terminal even
    if the ORIGINAL caller never saw the HTTP response: the mapping is
    durably committed server-side before any response is sent, regardless of
    what happens to that response afterward.

    ``key`` is this table's primary key, so a genuine collision (a second,
    concurrent caller committing a DIFFERENT terminal for the SAME key before
    either saw the other's mapping -- the narrow race this single-transaction
    design does not fully close, only the sequential-retry gap it targets)
    raises ``IntegrityError`` and rolls back BOTH inserts together; neither
    row is left half-committed. The caller (``terminal_service.create_terminal``)
    is responsible for translating that into cleanup of whatever tmux/provider
    resources it had already allocated before this call.
    """
    import json as _json

    if kiro_policy_digest is not None and (
        not isinstance(kiro_policy_digest, str)
        or __import__("re").fullmatch(r"[0-9a-f]{64}", kiro_policy_digest) is None
    ):
        raise ValueError("invalid KAS policy digest")

    with SessionLocal() as db:
        if new_session_incarnation:
            if not session_incarnation_id:
                raise ValueError("A new session incarnation requires an incarnation id")
            incarnation = db.get(SessionIncarnationModel, tmux_session)
            if incarnation is None:
                db.add(
                    SessionIncarnationModel(
                        session_name=tmux_session, incarnation_id=session_incarnation_id
                    )
                )
            else:
                incarnation.incarnation_id = session_incarnation_id
        terminal = TerminalModel(
            id=terminal_id,
            tmux_session=tmux_session,
            tmux_window=tmux_window,
            provider=provider,
            agent_profile=agent_profile,
            working_directory=working_directory,
            allowed_tools=_json.dumps(allowed_tools) if allowed_tools is not None else None,
            shell_command=shell_command,
            caller_id=caller_id,
            engine=engine,
            provider_variant=provider_variant,
            kiro_policy_digest=kiro_policy_digest,
            group=_json.dumps(group) if group else None,
            metadata_json=_json.dumps(metadata) if metadata else None,
            deferred_init_external_owner=bool(deferred_init_external_owner),
            session_incarnation_id=session_incarnation_id,
        )
        db.add(terminal)
        if idempotency_key:
            # `or ""` keeps this insert in the SAME transaction as the terminal
            # row (the property haofeif approved) without a nullable column: a
            # caller that supplies a key but no fingerprint stores a blank one,
            # which every later comparison simply mismatches. Failing loud beats
            # a skip-on-blank branch that would silently hand back a terminal
            # nobody verified.
            db.add(
                IdempotencyKeyModel(
                    key=idempotency_key,
                    terminal_id=terminal_id,
                    request_fingerprint=request_fingerprint or "",
                )
            )
        db.commit()
        return {
            "id": terminal.id,
            "tmux_session": terminal.tmux_session,
            "tmux_window": terminal.tmux_window,
            "provider": terminal.provider,
            "agent_profile": terminal.agent_profile,
            "working_directory": terminal.working_directory,
            "allowed_tools": allowed_tools,
            "shell_command": terminal.shell_command,
            "caller_id": terminal.caller_id,
            "engine": terminal.engine,
            "provider_variant": terminal.provider_variant,
            "kiro_policy_digest": terminal.kiro_policy_digest,
            # Normalized the same way as what was actually stored (an empty
            # container is stored as NULL, same as omitted) -- self-ROAST
            # finding: echoing the raw `group`/`metadata` input here made
            # create_terminal(group=[]) return {"group": []} while an
            # immediately-following get_terminal_metadata() on the same row
            # returns {"group": None}, an API-consistency gap.
            "group": group if group else None,
            "metadata": metadata if metadata else None,
            "deferred_init_external_owner": bool(deferred_init_external_owner),
            "deferred_init_runtime_reclaimed": False,
            "session_incarnation_id": session_incarnation_id,
        }


class IdempotencyRecord(NamedTuple):
    """A key's stored mapping: which terminal, and for WHICH request."""

    terminal_id: str
    request_fingerprint: str


def get_idempotency_record(key: str) -> Optional[IdempotencyRecord]:
    """Return the full mapping for ``key``, or ``None`` if never used.

    Review on PR #634, issue #616. A plain read, no locking: the caller
    (``terminal_service.create_terminal``) uses this to decide whether to do
    any real work at all, before generating a terminal id or touching tmux.

    Returns the fingerprint together with the terminal id in ONE read, so the
    caller can tell a genuine retry (same key, same request) from a key
    COLLISION (same key, different request) rather than returning a terminal
    that answers a question this caller never asked. This deliberately
    REPLACES an earlier ``get_terminal_id_by_idempotency_key`` that returned
    the id alone (review on PR #634): keeping a fingerprint-BLIND public
    lookup beside this one would invite a future caller to reintroduce exactly
    the bug class the fingerprint exists to close.
    """
    with SessionLocal() as db:
        row = db.query(IdempotencyKeyModel).filter(IdempotencyKeyModel.key == key).first()
        if row is None:
            return None
        return IdempotencyRecord(
            terminal_id=cast(str, row.terminal_id),
            request_fingerprint=cast(str, row.request_fingerprint),
        )


def delete_idempotency_key(key: str, expected_terminal_id: str) -> bool:
    """Delete an idempotency-key mapping, but only if it still points to
    ``expected_terminal_id``.

    Review on PR #634, issue #616: ``create_terminal``'s fallthrough
    for a mapping whose terminal no longer exists must clear this row FIRST.
    ``delete_terminal`` does not cascade to ``idempotency_keys``, so leaving
    a stale row in place would make the replacement terminal's own
    idempotency insert collide on the same primary key and raise
    ``IntegrityError``.

    The ``expected_terminal_id`` guard is a compare-and-delete: if a
    concurrent caller already replaced this mapping (it now points to
    SOME OTHER terminal), this deletes nothing and this caller's own
    create falls through to the normal atomic insert below, which then
    correctly raises ``IntegrityError`` for the loser -- the same
    already-accepted race behavior as two concurrent callers sharing a
    brand-new key. Without this guard, an unconditional delete-by-key
    could silently erase a concurrent winner's fresh, valid mapping
    instead.
    """
    with SessionLocal() as db:
        deleted = (
            db.query(IdempotencyKeyModel)
            .filter(
                IdempotencyKeyModel.key == key,
                IdempotencyKeyModel.terminal_id == expected_terminal_id,
            )
            .delete()
        )
        db.commit()
        return deleted > 0


def get_terminal_metadata(terminal_id: str) -> Optional[Dict[str, Any]]:
    """Get terminal metadata by ID."""
    import json as _json

    with SessionLocal() as db:
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if not terminal:
            logger.warning(f"Terminal metadata not found for terminal_id: {terminal_id}")
            return None
        logger.debug(
            f"Retrieved terminal metadata for {terminal_id}: provider={terminal.provider}, session={terminal.tmux_session}"
        )
        allowed_tools = _json.loads(terminal.allowed_tools) if terminal.allowed_tools else None
        group = _json.loads(terminal.group) if terminal.group else None
        metadata = _json.loads(terminal.metadata_json) if terminal.metadata_json else None
        raw_deferred_failure = getattr(terminal, "deferred_init_failure_json", None)
        deferred_init_failure = (
            _json.loads(raw_deferred_failure) if isinstance(raw_deferred_failure, str) else None
        )
        return {
            "id": terminal.id,
            "tmux_session": terminal.tmux_session,
            "tmux_window": terminal.tmux_window,
            "provider": terminal.provider,
            "agent_profile": terminal.agent_profile,
            "working_directory": terminal.working_directory,
            "allowed_tools": allowed_tools,
            "shell_command": terminal.shell_command,
            "caller_id": terminal.caller_id,
            "engine": terminal.engine or ("v2" if terminal.provider == "kiro_cli" else None),
            "provider_variant": terminal.provider_variant,
            "kiro_policy_digest": getattr(terminal, "kiro_policy_digest", None),
            "group": group,
            "metadata": metadata,
            "deferred_init_failure": deferred_init_failure,
            "deferred_init_external_owner": bool(
                getattr(terminal, "deferred_init_external_owner", False)
            ),
            "deferred_init_runtime_reclaimed": bool(
                getattr(terminal, "deferred_init_runtime_reclaimed", False)
            ),
            "session_incarnation_id": getattr(terminal, "session_incarnation_id", None),
            "last_active": terminal.last_active,
        }


def update_terminal_group(terminal_id: str, group: Optional[List[str]]) -> bool:
    """Replace a terminal's group array. ``None``/``[]`` clears it (opts out of discovery)."""
    import json as _json

    with SessionLocal() as db:
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if not terminal:
            return False
        terminal.group = _json.dumps(group) if group else None
        db.commit()
        return True


def update_terminal_metadata(terminal_id: str, metadata: Optional[Dict[str, Any]]) -> bool:
    """Replace a terminal's free-form metadata dict. ``None``/``{}`` clears it."""
    import json as _json

    with SessionLocal() as db:
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if not terminal:
            return False
        terminal.metadata_json = _json.dumps(metadata) if metadata else None
        db.commit()
        return True


_TURN_RECEIPT_ACTIVE_PHASES = frozenset({"prepared", "sent"})
_TURN_RECEIPT_SETTLED_PHASE = "result_verified"
_TURN_RECEIPT_GENERATION_RE = re.compile(r"^[0-9a-f]{32}$")
_TURN_RECEIPT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CACHED_TURN_RECEIPT_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:CAO-TURN-RECEIPT-|CAO_TURN_RECEIPT_)[0-9a-fA-F]{32}(?![A-Za-z0-9_])"
)


def _validate_turn_receipt_value(name: str, value: str, pattern: "re.Pattern[str]") -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"invalid terminal turn receipt {name}")


def _turn_receipt_row(row: TerminalTurnReceiptModel) -> Dict[str, Any]:
    """Return internal receipt state without ever exposing prompt/nonce text."""
    return {
        "terminal_id": cast(str, row.terminal_id),
        "provider": cast(str, row.provider),
        "generation": cast(str, row.generation),
        "receipt_sha256": cast(str, row.receipt_sha256),
        "phase": cast(str, row.phase),
        "result_sha256": cast(Optional[str], row.result_sha256),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "verified_at": row.verified_at,
    }


def _lock_turn_parent(db: Session, terminal_id: str) -> bool:
    """Serialize all turn decisions with claims and terminal teardown."""
    return (
        db.query(TerminalModel)
        .filter(TerminalModel.id == terminal_id)
        .update({TerminalModel.last_active: TerminalModel.last_active}, synchronize_session=False)
        == 1
    )


def _turn_recovery(db: Any, terminal_id: str, generation: str) -> Any:
    return (
        db.query(TerminalTurnRecoveryModel)
        .filter(
            TerminalTurnRecoveryModel.terminal_id == terminal_id,
            TerminalTurnRecoveryModel.generation == generation,
        )
        .first()
    )


def _ensure_turn_recovery(db: Any, receipt: Any) -> Any:
    row = _turn_recovery(db, receipt.terminal_id, receipt.generation)
    if row is None:
        row = TerminalTurnRecoveryModel(
            terminal_id=receipt.terminal_id,
            generation=receipt.generation,
            state="verified" if receipt.phase == "result_verified" else "pending",
            attempts=0,
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        db.add(row)
    return row


def _turn_recovery_row(row: Any) -> Dict[str, Any]:
    return {
        key: getattr(row, key)
        for key in (
            "terminal_id",
            "generation",
            "state",
            "attempts",
            "completion_detected_at",
            "reason",
            "result_text",
            "created_at",
            "updated_at",
        )
    }


def get_terminal_turn_recovery(
    terminal_id: str,
    generation: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Read a generation without modifying budget or suppressing read failures."""
    with SessionLocal() as db:
        if generation is None:
            receipt = (
                db.query(TerminalTurnReceiptModel)
                .filter(
                    TerminalTurnReceiptModel.terminal_id == terminal_id,
                )
                .first()
            )
            if receipt is None:
                return None
            generation = receipt.generation
        row = _turn_recovery(db, terminal_id, generation)
        return _turn_recovery_row(row) if row is not None else None


def list_late_turn_candidates(
    *, limit: int = 16, after: str = "", now: float | None = None
) -> List[Dict[str, str]]:
    """Only the current unsettled generation with detected final evidence."""
    import time

    observed_at = time.time() if now is None else now
    with SessionLocal() as db:
        rows = (
            db.query(TerminalTurnRecoveryModel)
            .outerjoin(
                TerminalLateObservationModel,
                (TerminalTurnRecoveryModel.terminal_id == TerminalLateObservationModel.terminal_id)
                & (TerminalTurnRecoveryModel.generation == TerminalLateObservationModel.generation),
            )
            .join(
                TerminalTurnReceiptModel,
                (TerminalTurnRecoveryModel.terminal_id == TerminalTurnReceiptModel.terminal_id)
                & (TerminalTurnRecoveryModel.generation == TerminalTurnReceiptModel.generation),
            )
            .filter(
                TerminalTurnRecoveryModel.state == "reconcile",
                TerminalTurnRecoveryModel.terminal_id > after,
                (
                    (TerminalLateObservationModel.terminal_id.is_(None))
                    | (
                        (TerminalLateObservationModel.attempts < 6)
                        & (TerminalLateObservationModel.next_due <= observed_at)
                        & (TerminalLateObservationModel.deadline > observed_at)
                    )
                ),
                TerminalTurnRecoveryModel.completion_detected_at.isnot(None),
                TerminalTurnReceiptModel.phase != "result_verified",
            )
            .order_by(TerminalTurnRecoveryModel.terminal_id)
            .limit(min(max(limit, 1), 64))
            .all()
        )
        return [{"terminal_id": row.terminal_id, "generation": row.generation} for row in rows]


def claim_late_turn_observation(terminal_id: str, generation: str, *, now: float) -> bool:
    """Claim before reading, preserving six slots/cooldown across server restart.

    Parent serialization also fences cancellation, settlement, deletion and new
    generations. A lost observer consumes its slot; it never causes a replay.
    """
    import math

    _validate_turn_receipt_value("generation", generation, _TURN_RECEIPT_GENERATION_RE)
    if not math.isfinite(now) or now < 0:
        raise ValueError("invalid observation time")
    with SessionLocal() as db:
        if not _lock_turn_parent(db, terminal_id):
            return False
        receipt = (
            db.query(TerminalTurnReceiptModel)
            .filter_by(terminal_id=terminal_id, generation=generation)
            .first()
        )
        recovery = _turn_recovery(db, terminal_id, generation)
        if (
            receipt is None
            or receipt.phase == "result_verified"
            or recovery is None
            or recovery.state != "reconcile"
            or recovery.completion_detected_at is None
        ):
            return False
        row = (
            db.query(TerminalLateObservationModel)
            .filter_by(terminal_id=terminal_id, generation=generation)
            .first()
        )
        if row is None:
            row = TerminalLateObservationModel(
                terminal_id=terminal_id,
                generation=generation,
                attempts=0,
                next_due=now,
                deadline=now + 3600,
            )
            db.add(row)
        if row.attempts >= 6 or now < row.next_due or now >= row.deadline:
            return False
        row.attempts += 1
        row.next_due = now + 60
        db.commit()
        return True


def mark_terminal_turn_verification_started(
    terminal_id: str,
    generation: str,
    receipt_sha256: str,
) -> Optional[Dict[str, Any]]:
    """Record final-response detection before capture, without consuming an attempt."""
    _validate_turn_receipt_value("generation", generation, _TURN_RECEIPT_GENERATION_RE)
    _validate_turn_receipt_value("hash", receipt_sha256, _TURN_RECEIPT_HASH_RE)
    with SessionLocal() as db:
        if not _lock_turn_parent(db, terminal_id):
            return None
        receipt = (
            db.query(TerminalTurnReceiptModel)
            .filter(
                TerminalTurnReceiptModel.terminal_id == terminal_id,
                TerminalTurnReceiptModel.generation == generation,
                TerminalTurnReceiptModel.receipt_sha256 == receipt_sha256,
            )
            .first()
        )
        if receipt is None:
            return None
        row = _ensure_turn_recovery(db, receipt)
        if row.state in {"pending", "verifying"}:
            row.completion_detected_at = row.completion_detected_at or _utcnow()
            row.state = "verifying"
            row.updated_at = _utcnow()
        db.commit()
        return _turn_recovery_row(row)


def record_terminal_turn_verification_failure(
    terminal_id: str,
    generation: str,
    receipt_sha256: str,
    reason: str,
) -> Optional[Dict[str, Any]]:
    """Count final-response verification failures, with a durable budget of three."""
    _validate_turn_receipt_value("generation", generation, _TURN_RECEIPT_GENERATION_RE)
    _validate_turn_receipt_value("hash", receipt_sha256, _TURN_RECEIPT_HASH_RE)
    with SessionLocal() as db:
        if not _lock_turn_parent(db, terminal_id):
            return None
        receipt = (
            db.query(TerminalTurnReceiptModel)
            .filter(
                TerminalTurnReceiptModel.terminal_id == terminal_id,
                TerminalTurnReceiptModel.generation == generation,
                TerminalTurnReceiptModel.receipt_sha256 == receipt_sha256,
            )
            .first()
        )
        if receipt is None:
            return None
        row = _ensure_turn_recovery(db, receipt)
        if row.state in {"pending", "verifying"}:
            row.attempts += 1
            row.state = "reconcile" if row.attempts >= 3 else "verifying"
            row.completion_detected_at = row.completion_detected_at or _utcnow()
            row.reason = reason
            row.updated_at = _utcnow()
        db.commit()
        return _turn_recovery_row(row)


def _transition_turn_cancellation(terminal_id: str, generation: str, finish: bool) -> bool:
    _validate_turn_receipt_value("generation", generation, _TURN_RECEIPT_GENERATION_RE)
    with SessionLocal() as db:
        if not _lock_turn_parent(db, terminal_id):
            return False
        receipt = (
            db.query(TerminalTurnReceiptModel)
            .filter(
                TerminalTurnReceiptModel.terminal_id == terminal_id,
                TerminalTurnReceiptModel.generation == generation,
            )
            .first()
        )
        if receipt is None:
            return False
        row = _ensure_turn_recovery(db, receipt)
        target = "cancelled" if finish else "cancelling"
        allowed = {"cancelling"} if finish else {"pending", "verifying", "reconcile"}
        if row.state == target:
            return True
        if row.state not in allowed or receipt.phase == "result_verified":
            return False
        row.state = target
        row.updated_at = _utcnow()
        db.commit()
        return True


def begin_terminal_turn_cancellation(terminal_id: str, generation: str) -> bool:
    """Fence result settlement before asking the backend to stop execution."""
    return _transition_turn_cancellation(terminal_id, generation, finish=False)


def finish_terminal_turn_cancellation(terminal_id: str, generation: str) -> bool:
    """Record backend-confirmed stoppage; callers must prove it before invoking."""
    return _transition_turn_cancellation(terminal_id, generation, finish=True)


def begin_terminal_turn_receipt(
    terminal_id: str,
    provider: str,
    generation: str,
    receipt_sha256: str,
) -> Optional[Dict[str, Any]]:
    """Atomically claim a terminal's next turn before any input is pasted.

    ``None`` means an active receipt already owns the terminal or the terminal
    no longer exists.  Both cases are intentionally non-destructive: callers
    must reconcile rather than overwrite an uncertain delivery.  A prior
    ``result_verified`` row or backend-confirmed ``cancelled`` recovery may
    be replaced; merely beginning cancellation never releases the claim.
    """
    if not isinstance(provider, str) or not provider.strip() or len(provider) > 128:
        raise ValueError("invalid terminal turn receipt provider")
    _validate_turn_receipt_value("generation", generation, _TURN_RECEIPT_GENERATION_RE)
    _validate_turn_receipt_value("hash", receipt_sha256, _TURN_RECEIPT_HASH_RE)
    now = _utcnow()
    try:
        with SessionLocal() as db:
            # Claim a write lock on the parent row before looking at or changing
            # its receipt.  A read-then-insert sequence is not sufficient: a
            # concurrent terminal teardown can delete the row after the read
            # and before the receipt commit, leaving private lifecycle state
            # with no terminal.  This no-op update is portable across the
            # SQLite and PostgreSQL backends CAO supports: it serializes
            # SQLite's writer transaction and holds PostgreSQL's row lock.
            # Terminal deletion follows the complementary order -- parent
            # first, receipt cleanup second, in the same transaction -- so a
            # deletion which waits for this claim will erase the receipt it
            # observes after the claim commits.
            parent_claimed = (
                db.query(TerminalModel)
                .filter(TerminalModel.id == terminal_id)
                .update(
                    {TerminalModel.last_active: TerminalModel.last_active},
                    synchronize_session=False,
                )
            )
            if parent_claimed != 1:
                return None
            existing = (
                db.query(TerminalTurnReceiptModel)
                .filter(TerminalTurnReceiptModel.terminal_id == terminal_id)
                .first()
            )
            old_recovery = (
                _turn_recovery(db, terminal_id, existing.generation)
                if existing is not None
                else None
            )
            stopped = old_recovery is not None and old_recovery.state == "cancelled"
            if (
                existing is not None
                and existing.phase != _TURN_RECEIPT_SETTLED_PHASE
                and not stopped
            ):
                return None
            if _turn_recovery(db, terminal_id, generation) is not None:
                return None
            if existing is None:
                existing = TerminalTurnReceiptModel(
                    terminal_id=terminal_id,
                    provider=provider,
                    generation=generation,
                    receipt_sha256=receipt_sha256,
                    phase="prepared",
                    created_at=now,
                    updated_at=now,
                )
                db.add(existing)
            else:
                # A verified result or confirmed cancellation releases this generation.
                # Do not mutate the loaded ORM object: two senders can both
                # observe result_verified, and a plain assignment would let
                # both commit a new task.  This CAS makes exactly one winner
                # replace the settled generation; the loser sees rowcount=0
                # and must reconcile rather than paste a second prompt.
                replaced = (
                    db.query(TerminalTurnReceiptModel)
                    .filter(
                        TerminalTurnReceiptModel.terminal_id == terminal_id,
                        TerminalTurnReceiptModel.phase == existing.phase,
                        TerminalTurnReceiptModel.generation == existing.generation,
                    )
                    .update(
                        {
                            "provider": provider,
                            "generation": generation,
                            "receipt_sha256": receipt_sha256,
                            "phase": "prepared",
                            "result_sha256": None,
                            "created_at": now,
                            "updated_at": now,
                            "verified_at": None,
                        },
                        synchronize_session=False,
                    )
                )
                if replaced != 1:
                    db.rollback()
                    return None
            db.add(
                TerminalTurnRecoveryModel(
                    terminal_id=terminal_id,
                    generation=generation,
                    state="pending",
                    attempts=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.commit()
            stored = (
                db.query(TerminalTurnReceiptModel)
                .filter(
                    TerminalTurnReceiptModel.terminal_id == terminal_id,
                    TerminalTurnReceiptModel.generation == generation,
                    TerminalTurnReceiptModel.receipt_sha256 == receipt_sha256,
                    TerminalTurnReceiptModel.phase == "prepared",
                )
                .first()
            )
            return _turn_receipt_row(stored) if stored is not None else None
    except IntegrityError:
        # A concurrent claim won the primary-key race.  Treat it exactly like
        # an already-active row; never retry by replacing unknown work.
        logger.info("Terminal turn receipt claim raced for terminal %s", terminal_id)
        return None
    except SQLAlchemyError as exc:
        logger.exception("Could not persist prepared turn receipt for terminal %s", terminal_id)
        raise RuntimeError("could not persist prepared terminal turn receipt") from exc


def get_terminal_turn_receipt(terminal_id: str) -> Optional[Dict[str, Any]]:
    """Read private receipt state for recovery; this is not an API projection."""
    with SessionLocal() as db:
        row = (
            db.query(TerminalTurnReceiptModel)
            .filter(TerminalTurnReceiptModel.terminal_id == terminal_id)
            .first()
        )
        return _turn_receipt_row(row) if row is not None else None


def mark_terminal_turn_receipt_sent(
    terminal_id: str,
    generation: str,
    receipt_sha256: str,
) -> bool:
    """CAS ``prepared -> sent`` after the backend accepted the paste.

    A very fast CLI can produce and have CAO verify its receipt while
    ``send_keys`` is still returning from its submit delay.  In that narrow
    race, accepting only this exact generation's already-verified result is
    safe and keeps delivery idempotent; a different generation or any other
    phase still returns ``False`` for reconciliation.
    """
    _validate_turn_receipt_value("generation", generation, _TURN_RECEIPT_GENERATION_RE)
    _validate_turn_receipt_value("hash", receipt_sha256, _TURN_RECEIPT_HASH_RE)
    try:
        with SessionLocal() as db:
            updated = (
                db.query(TerminalTurnReceiptModel)
                .filter(
                    TerminalTurnReceiptModel.terminal_id == terminal_id,
                    TerminalTurnReceiptModel.generation == generation,
                    TerminalTurnReceiptModel.receipt_sha256 == receipt_sha256,
                    TerminalTurnReceiptModel.phase == "prepared",
                )
                .update(
                    {"phase": "sent", "updated_at": _utcnow()},
                    synchronize_session=False,
                )
            )
            db.commit()
            if updated == 1:
                return True
            settled = (
                db.query(TerminalTurnReceiptModel)
                .filter(
                    TerminalTurnReceiptModel.terminal_id == terminal_id,
                    TerminalTurnReceiptModel.generation == generation,
                    TerminalTurnReceiptModel.receipt_sha256 == receipt_sha256,
                    TerminalTurnReceiptModel.phase == _TURN_RECEIPT_SETTLED_PHASE,
                )
                .first()
            )
            return settled is not None
    except SQLAlchemyError as exc:
        logger.exception("Could not persist sent turn receipt for terminal %s", terminal_id)
        raise RuntimeError("could not persist sent terminal turn receipt") from exc


def verify_terminal_turn_receipt_result(
    terminal_id: str,
    generation: str,
    receipt_sha256: str,
    result_sha256: str,
) -> bool:
    """CAS an active receipt to a durable, content-free result verification."""
    _validate_turn_receipt_value("generation", generation, _TURN_RECEIPT_GENERATION_RE)
    _validate_turn_receipt_value("hash", receipt_sha256, _TURN_RECEIPT_HASH_RE)
    _validate_turn_receipt_value("result hash", result_sha256, _TURN_RECEIPT_HASH_RE)
    now = _utcnow()
    try:
        with SessionLocal() as db:
            if not _lock_turn_parent(db, terminal_id):
                return False
            recovery = _turn_recovery(db, terminal_id, generation)
            if recovery is not None and recovery.state in {"cancelling", "cancelled"}:
                return False
            updated = (
                db.query(TerminalTurnReceiptModel)
                .filter(
                    TerminalTurnReceiptModel.terminal_id == terminal_id,
                    TerminalTurnReceiptModel.generation == generation,
                    TerminalTurnReceiptModel.receipt_sha256 == receipt_sha256,
                    TerminalTurnReceiptModel.phase.in_(sorted(_TURN_RECEIPT_ACTIVE_PHASES)),
                )
                .update(
                    {
                        "phase": _TURN_RECEIPT_SETTLED_PHASE,
                        "result_sha256": result_sha256,
                        "updated_at": now,
                        "verified_at": now,
                    },
                    synchronize_session=False,
                )
            )
            if updated == 1:
                receipt = (
                    db.query(TerminalTurnReceiptModel)
                    .filter(
                        TerminalTurnReceiptModel.terminal_id == terminal_id,
                    )
                    .first()
                )
                recovery = _ensure_turn_recovery(db, receipt)
                recovery.state = "verified"
                recovery.reason = None
                recovery.updated_at = now
            db.commit()
            if updated == 1:
                return True
            # The output observer and the delivery path can both see the
            # same completed turn.  Retrying the exact result receipt is
            # idempotent, but a different result must never overwrite the
            # durable evidence for this generation.
            settled = (
                db.query(TerminalTurnReceiptModel)
                .filter(
                    TerminalTurnReceiptModel.terminal_id == terminal_id,
                    TerminalTurnReceiptModel.generation == generation,
                    TerminalTurnReceiptModel.receipt_sha256 == receipt_sha256,
                    TerminalTurnReceiptModel.phase == _TURN_RECEIPT_SETTLED_PHASE,
                    TerminalTurnReceiptModel.result_sha256 == result_sha256,
                )
                .first()
            )
            return settled is not None
    except SQLAlchemyError as exc:
        logger.exception("Could not verify terminal turn result for terminal %s", terminal_id)
        raise RuntimeError("could not persist verified terminal turn result") from exc


def settle_terminal_turn_receipt_result(
    terminal_id: str,
    generation: str,
    receipt_sha256: str,
    result_sha256: str,
    *,
    result_text: Optional[str] = None,
) -> bool:
    """Atomically settle a receipt and its native-child completion projection.

    A result digest is not enough to safely announce a child as complete: a
    concurrent terminal teardown can cancel that child and erase the terminal
    between separate ``verify`` and ``transition`` transactions.  Claim the
    terminal parent, verify the exact receipt, and settle the child in one
    transaction instead.  The bool means that a public terminal COMPLETED
    observation is still safe to publish.

    ``cancelled`` and ``failed`` are final operator/lifecycle decisions.  An
    already verified result may remain as private audit evidence in that case,
    but it cannot revive their child or publish a stale completion.  A
    ``reconcile`` child is intentionally recoverable only through this
    receipt-backed proof path.
    """
    _validate_turn_receipt_value("generation", generation, _TURN_RECEIPT_GENERATION_RE)
    _validate_turn_receipt_value("hash", receipt_sha256, _TURN_RECEIPT_HASH_RE)
    _validate_turn_receipt_value("result hash", result_sha256, _TURN_RECEIPT_HASH_RE)
    now = _utcnow()
    try:
        with SessionLocal() as db:
            # Serialize with terminal deletion.  The teardown path deletes the
            # parent first and auxiliary receipt rows second in its own
            # transaction, so either this claim wins and commits the complete
            # proof/projection, or teardown wins and no stale completion can
            # be emitted afterwards.
            parent_claimed = (
                db.query(TerminalModel)
                .filter(TerminalModel.id == terminal_id)
                .update(
                    {TerminalModel.last_active: TerminalModel.last_active},
                    synchronize_session=False,
                )
            )
            if parent_claimed != 1:
                db.rollback()
                return False

            receipt = (
                db.query(TerminalTurnReceiptModel)
                .filter(
                    TerminalTurnReceiptModel.terminal_id == terminal_id,
                    TerminalTurnReceiptModel.generation == generation,
                    TerminalTurnReceiptModel.receipt_sha256 == receipt_sha256,
                )
                .first()
            )
            if receipt is None:
                db.rollback()
                return False

            recovery = _ensure_turn_recovery(db, receipt)
            if recovery.state in {"cancelling", "cancelled"}:
                db.rollback()
                return False

            if receipt.phase in _TURN_RECEIPT_ACTIVE_PHASES:
                updated = (
                    db.query(TerminalTurnReceiptModel)
                    .filter(
                        TerminalTurnReceiptModel.terminal_id == terminal_id,
                        TerminalTurnReceiptModel.generation == generation,
                        TerminalTurnReceiptModel.receipt_sha256 == receipt_sha256,
                        TerminalTurnReceiptModel.phase.in_(sorted(_TURN_RECEIPT_ACTIVE_PHASES)),
                    )
                    .update(
                        {
                            "phase": _TURN_RECEIPT_SETTLED_PHASE,
                            "result_sha256": result_sha256,
                            "updated_at": now,
                            "verified_at": now,
                        },
                        synchronize_session=False,
                    )
                )
                if updated != 1:
                    db.rollback()
                    return False
            elif not (
                receipt.phase == _TURN_RECEIPT_SETTLED_PHASE
                and receipt.result_sha256 == result_sha256
            ):
                # Never replace another result or a newer receipt generation.
                db.rollback()
                return False

            recovery.state = "verified"
            recovery.reason = None
            recovery.updated_at = now
            # The first proven output is immutable even if later captures differ.
            if recovery.result_text is None and result_text is not None:
                # Cache presentation text only; the digest above still proves
                # the unmodified provider output, including its private marker.
                recovery.result_text = _CACHED_TURN_RECEIPT_RE.sub(
                    "[redacted receipt]", result_text
                )

            # Do not load a child and assign to its ORM object: a concurrent
            # wait/status path could set ``failed`` after that read, and a
            # stale assignment to ``succeeded`` would erase a final outcome.
            # The conditional write is the reciprocal CAS to
            # ``transition_native_child``. Only an active/reconcile child can
            # become succeeded by this receipt proof; terminal states remain
            # immutable whichever writer commits first.
            child_updated = (
                db.query(NativeChildModel)
                .filter(
                    NativeChildModel.terminal_id == terminal_id,
                    NativeChildModel.state.in_(sorted(_NATIVE_CHILD_ACTIVE_STATES | {"reconcile"})),
                )
                .update(
                    {
                        "state": "succeeded",
                        "updated_at": now,
                        "settled_at": now,
                        "error_kind": None,
                        "error_summary": None,
                    },
                    synchronize_session=False,
                )
            )
            completion_safe = True
            if child_updated != 1:
                winner = (
                    db.query(NativeChildModel)
                    .filter(NativeChildModel.terminal_id == terminal_id)
                    .first()
                )
                if winner is not None and winner.state != "succeeded":
                    # A confirmed cancellation can release this terminal for a
                    # later independent turn. Its proof belongs to that new
                    # generation, while the original child outcome stays final.
                    previous_cancellation = None
                    if winner.state == "cancelled":
                        previous_cancellation = (
                            db.query(TerminalTurnRecoveryModel)
                            .filter(
                                TerminalTurnRecoveryModel.terminal_id == terminal_id,
                                TerminalTurnRecoveryModel.generation != generation,
                                TerminalTurnRecoveryModel.state == "cancelled",
                            )
                            .first()
                        )
                    # Other final failures, and cancellations without a
                    # confirmed prior turn boundary, still block completion.
                    completion_safe = previous_cancellation is not None

            db.commit()
            return completion_safe
    except SQLAlchemyError as exc:
        logger.exception("Could not atomically settle terminal turn result for %s", terminal_id)
        raise RuntimeError("could not atomically settle verified terminal turn result") from exc


def _delete_terminal_turn_receipt_rows(db: Any, terminal_ids: List[str]) -> None:
    """Erase receipt records only when their terminal is actually removed."""
    if terminal_ids:
        (
            db.query(TerminalTurnReceiptModel)
            .filter(TerminalTurnReceiptModel.terminal_id.in_(terminal_ids))
            .delete(synchronize_session=False)
        )

        db.query(TerminalTurnRecoveryModel).filter(
            TerminalTurnRecoveryModel.terminal_id.in_(terminal_ids)
        ).delete(synchronize_session=False)
        db.query(TerminalLateObservationModel).filter(
            TerminalLateObservationModel.terminal_id.in_(terminal_ids)
        ).delete(synchronize_session=False)


def update_terminal_deferred_init_failure(
    terminal_id: str, failure: Optional[Dict[str, Any]]
) -> bool:
    """Replace CAO-owned deferred-init failure state for one terminal."""

    import json as _json

    with SessionLocal() as db:
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if not terminal:
            return False
        terminal.deferred_init_failure_json = _json.dumps(failure) if failure else None
        db.commit()
        return True


def update_terminal_deferred_init_external_owner(terminal_id: str, owned: bool) -> bool:
    """Update server-owned deferred-init lifecycle ownership for one terminal."""

    with SessionLocal() as db:
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if not terminal:
            return False
        terminal.deferred_init_external_owner = bool(owned)
        db.commit()
        return True


def update_terminal_deferred_init_runtime_reclaimed(terminal_id: str, reclaimed: bool) -> bool:
    """Persist whether a retained deferred-init row still owns live runtime resources."""

    with SessionLocal() as db:
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if not terminal:
            return False
        terminal.deferred_init_runtime_reclaimed = bool(reclaimed)
        db.commit()
        return True


def get_session_incarnation(session_name: str) -> Optional[str]:
    """Read the durable current pointer, including for an already-deleted session."""

    if not session_name:
        return None
    with SessionLocal() as db:
        incarnation = db.get(SessionIncarnationModel, session_name)
        return str(incarnation.incarnation_id) if incarnation is not None else None


def get_session_incarnations(session_names: List[str]) -> Dict[str, str]:
    """Read current pointers in one query for a fleet listing."""

    names = [name for name in session_names if name]
    if not names:
        return {}
    with SessionLocal() as db:
        return {
            str(row.session_name): str(row.incarnation_id)
            for row in db.query(SessionIncarnationModel)
            .filter(SessionIncarnationModel.session_name.in_(names))
            .all()
        }


def update_terminals_session_incarnation(
    terminal_ids: List[str], incarnation_id: str, *, session_name: Optional[str] = None
) -> bool:
    """Atomically assign one session incarnation to the specified terminal rows.

    Used while the per-session lifecycle lock is held. All requested rows must
    exist and have no conflicting identity. When session_name is supplied, its
    durable pointer is committed atomically with the backfill. A conflicting
    pointer or a row belonging to another session rejects the whole assignment.
    """

    unique_ids = list(dict.fromkeys(str(terminal_id) for terminal_id in terminal_ids))
    if not unique_ids and session_name is None:
        return True
    with SessionLocal() as db:
        terminals = db.query(TerminalModel).filter(TerminalModel.id.in_(unique_ids)).all()
        if len(terminals) != len(unique_ids) or any(
            (terminal.session_incarnation_id not in (None, incarnation_id))
            or (session_name is not None and terminal.tmux_session != session_name)
            for terminal in terminals
        ):
            db.rollback()
            return False
        if session_name is not None:
            current = db.get(SessionIncarnationModel, session_name)
            if current is not None and current.incarnation_id != incarnation_id:
                db.rollback()
                return False
            if current is None:
                db.add(
                    SessionIncarnationModel(
                        session_name=session_name, incarnation_id=incarnation_id
                    )
                )
        for terminal in terminals:
            terminal.session_incarnation_id = str(incarnation_id)
        db.commit()
        return True


def list_pending_deferred_init_external_owner_terminal_ids() -> List[str]:
    """External-owner deferred inits whose background task cannot resume after restart."""

    with SessionLocal() as db:
        rows = (
            db.query(TerminalModel.id)
            .filter(TerminalModel.deferred_init_external_owner.is_(True))
            .all()
        )
        return [str(row[0]) for row in rows]


def count_runtime_allocated_terminals() -> int:
    """Count terminal rows that still represent live/allocated provider runtime."""

    with SessionLocal() as db:
        return int(
            db.query(TerminalModel)
            .filter(TerminalModel.deferred_init_runtime_reclaimed.is_(False))
            .count()
        )


def get_terminal_group(terminal_id: str) -> Optional[List[str]]:
    """Return a terminal's own group array, or None if unset or the terminal doesn't exist."""
    import json as _json

    with SessionLocal() as db:
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if not terminal or not terminal.group:
            return None
        return cast(List[str], _json.loads(terminal.group))


def list_siblings_by_group_prefix(
    caller_id: str,
    prefix: List[str],
    caller_session: Optional[str] = None,
    cross_session: bool = False,
) -> List[Dict[str, Any]]:
    """Return ``{id, group, metadata}`` for every OTHER terminal sharing ``prefix``.

    ``prefix`` is the caller's own group truncated to the (already-clamped)
    depth — this function does no clamping itself, it only matches. A
    candidate terminal with no group, or a group shorter than ``len(prefix)``,
    is excluded rather than compared partially or raising (#432).

    Session-scoped by default (issue #432 design discussion, tedswinyar +
    klabulan, 2026-07-17/18): ``caller_session`` (the caller's own
    ``tmux_session``) is an implicit, non-bypassable first filter ON TOP of
    the group-prefix match, unless ``cross_session=True`` is explicitly
    passed. Without this, two unrelated CAO sessions that happen to reuse
    the same ``group`` prefix (a naming collision, a copy-pasted template,
    two features that picked the same tenant/project id) would silently
    discover each other -- the same class of "implicitly-scoped state that
    turns out not to be" mistake cited in that discussion's incident
    history. Cross-session discovery is a legitimate use case and stays
    available, just opt-in rather than the unstated default.

    ``group`` is stored JSON-encoded (see ``TerminalModel.group``), so the
    query prefilters with a SQL ``LIKE`` prefix match on that encoding before
    loading/decoding candidate rows in Python (Copilot review, PR #433) —
    without it this scanned and JSON-decoded every grouped terminal on the
    server regardless of how narrow ``prefix`` is. Because ``json.dumps``
    closes each string element in a quote immediately, the encoded prefix
    (full array minus its trailing ``]``) can't false-positive match a
    longer sibling element that merely shares a text prefix (e.g. prefix
    element ``"project_5"`` vs. a sibling group containing ``"project_50"``
    — the sibling's extra ``0`` before its closing ``"`` breaks the SQL
    match). The exact Python-level comparison below is kept regardless, as
    the source of truth — the SQL match only narrows candidates (a prefilter
    defect can only cause a false negative here, i.e. a missed perf win,
    never a false positive / correctness or security regression).

    This SQL-level match assumes the stored ``group`` was encoded with the
    same ``json.dumps`` defaults used below (notably ``ensure_ascii=True``,
    the default) — true today of both write paths (``create_terminal`` and
    ``update_terminal_group``), which both use plain ``json.dumps(group)``.
    If either write path ever changes its encoding, this prefilter must
    change with it.

    A single row with corrupt ``group`` JSON (e.g. hand-edited DB, a future
    write-path bug) is logged and excluded rather than raising and failing
    discovery for every OTHER terminal in the same request (tedswinyar, PR
    #433 review). Corrupt ``metadata`` JSON on an otherwise-matching sibling
    is likewise logged and reported back as ``metadata=None`` -- the sibling
    itself is still real and discoverable, only its metadata is unreadable.
    """
    import json as _json

    depth = len(prefix)
    # Encode the prefix array and drop its trailing ']' so this matches both
    # a sibling group of the same length and a longer one that starts with
    # it, e.g. prefix ["a", "b"] -> '["a", "b"' matches '["a", "b"]' and
    # '["a", "b", "c"]'.
    like_prefix = _json.dumps(prefix)[:-1]
    with SessionLocal() as db:
        query = db.query(TerminalModel).filter(
            TerminalModel.id != caller_id,
            TerminalModel.group.isnot(None),
            TerminalModel.group.startswith(like_prefix, autoescape=True),
        )
        if not cross_session and caller_session is not None:
            query = query.filter(TerminalModel.tmux_session == caller_session)
        rows = query.all()
        siblings = []
        for row in rows:
            try:
                sibling_group = _json.loads(row.group)
                if not isinstance(sibling_group, list):
                    raise ValueError(f"decoded to {type(sibling_group).__name__}, expected list")
            except (TypeError, ValueError) as e:
                logger.warning(
                    "list_siblings_by_group_prefix: skipping terminal %s -- "
                    "corrupt group JSON (%s)",
                    row.id,
                    e,
                )
                continue
            if len(sibling_group) < depth:
                continue
            if sibling_group[:depth] == prefix:
                metadata = None
                if row.metadata_json:
                    try:
                        metadata = _json.loads(row.metadata_json)
                    except (TypeError, ValueError) as e:
                        logger.warning(
                            "list_siblings_by_group_prefix: terminal %s has "
                            "corrupt metadata JSON (%s); returning it with "
                            "metadata=None",
                            row.id,
                            e,
                        )
                siblings.append(
                    {
                        "id": row.id,
                        "group": sibling_group,
                        "metadata": metadata,
                    }
                )
        return siblings


def list_terminals_by_session(tmux_session: str) -> List[Dict[str, Any]]:
    """List a tmux session's terminals, oldest first.

    **Index 0 is the session's oldest surviving terminal -- normally its
    conductor.** That is a contract, not an accident of the query plan:
    ``flow_service`` decides whether to kill a session by whether index 0 is
    busy, ``cao session status``/``list`` label index 0 as the Conductor over
    HTTP, and ``session_service`` derives a session's reported profile and
    directory from the earliest terminal that HAS either (#497) -- a first-match
    scan rather than a bare index, so a conductor row with both fields NULL
    still cedes ownership to a worker. Callers should cite this docstring rather
    than restate the rule.

    Ordered by ``rowid``, which is insertion order, and normally creation order:
    every row is written at one site (``db_create_terminal``, called from
    ``terminal_service.create_terminal``), and a worker's row cannot precede its
    conductor's because the MCP handoff either resolves a caller that already
    has a row (``GET /terminals/{caller_id}``, which raises if the id is stale)
    or, when it is running outside a CAO terminal, records no caller at all and
    starts a NEW session in which the worker is itself the conductor. Reuse
    after deletion does not reorder surviving rows -- see ``TerminalModel`` for
    the mechanism and its limits.

    Two known gaps, both pre-existing and neither introduced here:

    * ``session_lifecycle_lock`` serialises creation against teardown, but it is
      a ``threading.Lock`` and therefore per-PROCESS. ``cao schedule run`` calls
      ``execute_flow`` in the CLI process, and ``flow_service`` does not take the
      lock at all, so its kill-and-recreate is not serialised against cao-server.
      A worker insert landing inside that window can take index 0.
    * Index 0 is the oldest SURVIVING row, which is not the conductor once the
      conductor's own row is gone -- ``DELETE /terminals/{id}`` has no guard, the
      MCP tool exposes it, and ghost-terminal cleanup deletes rows while the
      session lives on.

    Both mean consumers should treat index 0 as best-effort, which is what
    ``_enrich_session_ownership`` already does. The ordering is what makes it
    *predictable*; it does not make it an identity.

    This ORDER BY does not change what this function returned before it was
    added -- an unordered scan of a rowid table already yielded rowid order.
    It states the order instead of inheriting it, so an index added later
    cannot quietly change which terminal is the conductor.
    """
    with SessionLocal() as db:
        terminals = (
            db.query(TerminalModel)
            .filter(TerminalModel.tmux_session == tmux_session)
            .order_by(literal_column("terminals.rowid"))
            .all()
        )
        return [
            {
                "id": t.id,
                "tmux_session": t.tmux_session,
                "tmux_window": t.tmux_window,
                "provider": t.provider,
                "agent_profile": t.agent_profile,
                "working_directory": t.working_directory,
                "engine": t.engine or ("v2" if t.provider == "kiro_cli" else None),
                "deferred_init_failure": (
                    _json.loads(t.deferred_init_failure_json)
                    if isinstance(t.deferred_init_failure_json, str)
                    and t.deferred_init_failure_json
                    else None
                ),
                "deferred_init_external_owner": bool(t.deferred_init_external_owner),
                "deferred_init_runtime_reclaimed": bool(t.deferred_init_runtime_reclaimed),
                "session_incarnation_id": t.session_incarnation_id,
                "last_active": t.last_active,
            }
            for t in terminals
        ]


def update_last_active(terminal_id: str) -> bool:
    """Update last active timestamp."""
    with SessionLocal() as db:
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if terminal:
            terminal.last_active = datetime.now()
            db.commit()
            return True
        return False


def update_terminal_shell_command(terminal_id: str, shell_command: str) -> bool:
    """Update the shell_command baseline for a terminal."""
    with SessionLocal() as db:
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if terminal:
            terminal.shell_command = shell_command
            db.commit()
            return True
        return False


def list_terminals_in_sessions(tmux_sessions: List[str]) -> List[Dict[str, Any]]:
    """List terminals for several tmux sessions in one query.

    Exists so ``list_sessions`` can enrich N sessions without N queries (issue
    #629) while still reading only the rows it will use. ``list_all_terminals``
    would also collapse the query count, but its cost scales with the whole
    table — including rows for sessions tmux no longer reports, which accumulate
    because ``cleanup_service.cleanup_old_data`` only runs at server startup, so
    a long-uptime server never sweeps them. Bounding the read by the live
    session names keeps the cost proportional to the workload instead of to the
    leak.

    Ordered by ``rowid``, identically to ``list_terminals_by_session`` -- see
    that function for the index-0-is-the-conductor contract and why rowid
    expresses creation order. The two MUST order the same way: the caller picks
    a session's "first known terminal", so the pick is order-sensitive, and an
    ordered batched read beside an unordered per-session read is what let this
    function silently disagree with the one it replaced.

    The ORDER BY is not decoration. Without it the order is whatever the engine
    plans: today every plan is a rowid scan (there is no index on
    ``tmux_session``), but adding one -- the obvious reaction to a slow
    per-session lookup -- reorders the probe. Measured, a plain ASC index on
    ``(tmux_session, id)`` makes an unordered read return a worker ahead of the
    session creator; ordering here is what keeps the conductor first.

    Ordering by ``id`` would NOT do that: ``id`` is ``uuid4().hex[:8]``
    (utils/terminal.generate_terminal_id), so it makes the conductor a
    deterministic *random* terminal, and a session whose creator happens to sort
    above one of its own workers advertises the worker's profile and directory as
    the session's.

    Returns an empty list without querying when given no session names.
    """
    if not tmux_sessions:
        return []
    with SessionLocal() as db:
        terminals = (
            db.query(TerminalModel)
            .filter(TerminalModel.tmux_session.in_(tmux_sessions))
            .order_by(literal_column("terminals.rowid"))
            .all()
        )
        return [
            {
                "id": t.id,
                "tmux_session": t.tmux_session,
                "tmux_window": t.tmux_window,
                "provider": t.provider,
                "agent_profile": t.agent_profile,
                "working_directory": t.working_directory,
                "engine": t.engine or ("v2" if t.provider == "kiro_cli" else None),
                "deferred_init_failure": (
                    _json.loads(t.deferred_init_failure_json)
                    if isinstance(t.deferred_init_failure_json, str)
                    and t.deferred_init_failure_json
                    else None
                ),
                "deferred_init_external_owner": bool(t.deferred_init_external_owner),
                "deferred_init_runtime_reclaimed": bool(t.deferred_init_runtime_reclaimed),
                "session_incarnation_id": t.session_incarnation_id,
                "last_active": t.last_active,
            }
            for t in terminals
        ]


def list_terminal_observation_candidates(*, after: str = "", limit: int = 16) -> List[str]:
    """Bounded reconstruction inventory; terminal ids carry no launch authority."""
    with SessionLocal() as db:
        rows = (
            db.query(TerminalModel.id)
            .filter(
                TerminalModel.id > after, TerminalModel.deferred_init_runtime_reclaimed.is_(False)
            )
            .order_by(TerminalModel.id)
            .limit(min(max(limit, 1), 64))
            .all()
        )
        return [row[0] for row in rows]


def list_all_terminals() -> List[Dict[str, Any]]:
    """List all terminals."""
    with SessionLocal() as db:
        terminals = db.query(TerminalModel).all()
        return [
            {
                "id": t.id,
                "tmux_session": t.tmux_session,
                "tmux_window": t.tmux_window,
                "provider": t.provider,
                "agent_profile": t.agent_profile,
                "working_directory": t.working_directory,
                "engine": t.engine or ("v2" if t.provider == "kiro_cli" else None),
                "deferred_init_failure": (
                    _json.loads(t.deferred_init_failure_json)
                    if isinstance(t.deferred_init_failure_json, str)
                    and t.deferred_init_failure_json
                    else None
                ),
                "deferred_init_external_owner": bool(t.deferred_init_external_owner),
                "deferred_init_runtime_reclaimed": bool(t.deferred_init_runtime_reclaimed),
                "session_incarnation_id": t.session_incarnation_id,
                "last_active": t.last_active,
            }
            for t in terminals
        ]


def list_pending_receiver_ids_by_provider(provider: str) -> List[str]:
    """List receiver terminal IDs with pending messages for a specific provider."""
    with SessionLocal() as db:
        rows = (
            db.query(InboxModel.receiver_id)
            .join(TerminalModel, TerminalModel.id == InboxModel.receiver_id)
            .filter(
                TerminalModel.provider == provider,
                InboxModel.status == MessageStatus.PENDING.value,
            )
            .distinct()
            .all()
        )
        return [row[0] for row in rows]


def list_pending_receiver_ids_older_than(min_age_seconds: int) -> List[str]:
    """List receiver terminal IDs whose messages have been PENDING too long.

    Returns the distinct receivers of any message still PENDING for longer than
    ``min_age_seconds``. Used by the inbox reconciliation sweep to find messages
    the immediate and watchdog delivery paths missed, without competing with
    them for freshly queued ones (issue #131).

    The join on ``terminals`` drops messages whose receiver terminal no longer
    exists, so the sweep does not keep retrying deliveries to deleted agents.

    ``created_at`` is stored local-naive (``InboxModel.created_at`` defaults to
    ``datetime.now``), so the cutoff uses ``datetime.now()`` to match — the same
    convention as the retention query in ``cleanup_service.cleanup_old_data``.
    """
    cutoff = datetime.now() - timedelta(seconds=min_age_seconds)
    with SessionLocal() as db:
        rows = (
            db.query(InboxModel.receiver_id)
            .join(TerminalModel, TerminalModel.id == InboxModel.receiver_id)
            .filter(
                InboxModel.status == MessageStatus.PENDING.value,
                InboxModel.created_at < cutoff,
            )
            .distinct()
            .all()
        )
        return [row[0] for row in rows]


def delete_terminal(terminal_id: str) -> bool:
    """Delete terminal metadata."""
    with SessionLocal() as db:
        deleted = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).delete()
        # Delete the parent before its auxiliary lifecycle rows.  Receipt
        # claims acquire the reciprocal parent-row write lock, preventing a
        # claim from committing after this cleanup sweep has already run.
        _record_native_child_cleanup_rows(db, [terminal_id])
        _delete_terminal_turn_receipt_rows(db, [terminal_id])
        db.commit()
        return deleted > 0


# Native-child lifecycle -----------------------------------------------------
#
# This is intentionally a small, database-only boundary.  ``terminal_service``
# owns terminal/process actions and ``agent_step`` owns prompt/result actions;
# neither should manufacture state by parsing a TUI.  The receipt is therefore
# updated only at known command boundaries, and an expired lease becomes
# ``reconcile`` rather than a guessed success or a destructive cleanup.
_NATIVE_CHILD_ACTIVE_STATES = {"planned", "acknowledged", "sent", "running"}
_NATIVE_CHILD_TERMINAL_STATES = {"succeeded", "failed", "reconcile", "cancelled"}
_NATIVE_CHILD_STATES = _NATIVE_CHILD_ACTIVE_STATES | _NATIVE_CHILD_TERMINAL_STATES
_NATIVE_CHILD_ACTIVE_ORDER = {
    "planned": 0,
    "acknowledged": 1,
    "sent": 2,
    "running": 3,
}


def _native_child_row(row: NativeChildModel) -> Dict[str, Any]:
    """Return the stable public receipt shape without exposing task content."""
    return {
        "id": row.id,
        "parent_terminal_id": row.parent_terminal_id,
        "terminal_id": row.terminal_id,
        "provider": row.provider,
        "agent_profile": row.agent_profile,
        "state": row.state,
        "lease_expires_at": row.lease_expires_at,
        "error_kind": row.error_kind,
        "error_summary": row.error_summary,
        "cleanup_completed_at": row.cleanup_completed_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "settled_at": row.settled_at,
    }


def plan_native_child(
    *,
    parent_terminal_id: str,
    terminal_id: str,
    provider: str,
    agent_profile: str,
    lease_seconds: float,
) -> Dict[str, Any]:
    """Persist a child *before* its tmux window/provider is created.

    A process crash between planning and terminal creation is intentionally
    visible as ``planned``.  The lease sweeper will turn it into
    ``reconcile``; it is never silently deleted and never reported as done.
    ``terminal_id`` is already generated by the terminal layer, making this a
    deterministic recovery handle even in that narrow gap.
    """
    if not parent_terminal_id:
        raise ValueError("parent_terminal_id is required for a native child")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    now = _utcnow()
    with SessionLocal() as db:
        row = NativeChildModel(
            parent_terminal_id=parent_terminal_id,
            terminal_id=terminal_id,
            provider=provider,
            agent_profile=agent_profile,
            state="planned",
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _native_child_row(row)


def transition_native_child(
    terminal_id: str,
    state: str,
    *,
    error_kind: Optional[str] = None,
    error_summary: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Write a receipt transition for ``terminal_id`` when it is a child.

    This function is deliberately idempotent.  A late status observation may
    not overwrite a terminal state, and a terminal state never moves backwards
    into a guessed active state.  Error text is capped because exceptions are
    diagnostic evidence, not a second transcript store.
    """
    if state not in _NATIVE_CHILD_STATES:
        raise ValueError(f"invalid native child state: {state}")
    now = _utcnow()
    with SessionLocal() as db:
        # This is deliberately a conditional UPDATE rather than a read/ORM
        # assignment. ``settle_terminal_turn_receipt_result`` can atomically
        # move an active child to succeeded while a wait/status worker is
        # about to record ``running`` or ``reconcile``. A stale ORM object
        # would then overwrite proof-backed success on PostgreSQL's default
        # isolation level. Restrict this write to still-active states and read
        # the winner afterwards.
        allowed_previous_states = set(_NATIVE_CHILD_ACTIVE_STATES)
        if state in _NATIVE_CHILD_ACTIVE_STATES:
            # Active lifecycle events are monotonic too: a late acknowledge
            # cannot regress a child already sent/running.
            target_order = _NATIVE_CHILD_ACTIVE_ORDER[state]
            allowed_previous_states = {
                candidate
                for candidate, order in _NATIVE_CHILD_ACTIVE_ORDER.items()
                if order <= target_order
            }

        values: Dict[str, Any] = {"state": state, "updated_at": now}
        if error_kind is not None:
            values["error_kind"] = error_kind
        if error_summary is not None:
            values["error_summary"] = error_summary[:1024]
        if state in _NATIVE_CHILD_TERMINAL_STATES:
            values["settled_at"] = now

        db.query(NativeChildModel).filter(
            NativeChildModel.terminal_id == terminal_id,
            NativeChildModel.state.in_(sorted(allowed_previous_states)),
        ).update(values, synchronize_session=False)
        db.commit()
        row = db.query(NativeChildModel).filter(NativeChildModel.terminal_id == terminal_id).first()
        return _native_child_row(row) if row is not None else None


def get_native_child(child_id: str) -> Optional[Dict[str, Any]]:
    """Read one receipt, reconciling an expired active lease first."""
    reconcile_expired_native_children(child_id=child_id)
    with SessionLocal() as db:
        row = db.query(NativeChildModel).filter(NativeChildModel.id == child_id).first()
        return _native_child_row(row) if row is not None else None


def get_native_child_for_terminal(terminal_id: str) -> Optional[Dict[str, Any]]:
    """Read a terminal's child receipt, if the terminal has a parent."""
    reconcile_expired_native_children(terminal_id=terminal_id)
    with SessionLocal() as db:
        row = db.query(NativeChildModel).filter(NativeChildModel.terminal_id == terminal_id).first()
        return _native_child_row(row) if row is not None else None


def list_native_children(parent_terminal_id: str) -> List[Dict[str, Any]]:
    """List a parent's durable children, oldest first, with leases reconciled."""
    reconcile_expired_native_children(parent_terminal_id=parent_terminal_id)
    with SessionLocal() as db:
        rows = (
            db.query(NativeChildModel)
            .filter(NativeChildModel.parent_terminal_id == parent_terminal_id)
            .order_by(NativeChildModel.created_at.asc())
            .all()
        )
        return [_native_child_row(row) for row in rows]


def reconcile_expired_native_children(
    *,
    child_id: Optional[str] = None,
    terminal_id: Optional[str] = None,
    parent_terminal_id: Optional[str] = None,
) -> List[str]:
    """Turn only expired *active* leases into ``reconcile``.

    Expiry is not proof that the child failed or completed.  It is exactly the
    uncertain-stop condition: preserve the terminal handle and ask the parent
    to inspect/reconcile it.  No process is killed from this data-layer sweep.
    """
    now = _utcnow()
    with SessionLocal() as db:
        query = db.query(NativeChildModel).filter(
            NativeChildModel.state.in_(sorted(_NATIVE_CHILD_ACTIVE_STATES)),
            NativeChildModel.lease_expires_at <= now,
        )
        if child_id is not None:
            query = query.filter(NativeChildModel.id == child_id)
        if terminal_id is not None:
            query = query.filter(NativeChildModel.terminal_id == terminal_id)
        if parent_terminal_id is not None:
            query = query.filter(NativeChildModel.parent_terminal_id == parent_terminal_id)
        # The candidate read is only a convenience for portable return IDs;
        # it must not authorize a later stale ORM write. A receipt settlement
        # can atomically change the same child to succeeded between this read
        # and the write (particularly on PostgreSQL where those transactions
        # genuinely overlap). Re-check state and lease in a conditional UPDATE
        # so expiry can never overwrite proof-backed success with reconcile.
        candidate_ids = [
            cast(str, row[0]) for row in query.with_entities(NativeChildModel.id).all()
        ]
        reconciled_ids: List[str] = []
        for candidate_id in candidate_ids:
            updated = (
                db.query(NativeChildModel)
                .filter(
                    NativeChildModel.id == candidate_id,
                    NativeChildModel.state.in_(sorted(_NATIVE_CHILD_ACTIVE_STATES)),
                    NativeChildModel.lease_expires_at <= now,
                )
                .update(
                    {
                        "state": "reconcile",
                        "error_kind": "lease_expired",
                        "error_summary": "child lease expired without a durable result receipt",
                        "updated_at": now,
                        "settled_at": now,
                    },
                    synchronize_session=False,
                )
            )
            if updated == 1:
                reconciled_ids.append(candidate_id)
        db.commit()
        return reconciled_ids


def record_native_child_cleanup(terminal_id: str) -> Optional[Dict[str, Any]]:
    """Record successful terminal removal without erasing the child receipt.

    Removal of an unfinished worker is a cancellation, never a successful task
    result.  A task already settled as succeeded/failed/reconcile retains that
    result while gaining a durable cleanup receipt.
    """
    with SessionLocal() as db:
        _record_native_child_cleanup_rows(db, [terminal_id])
        db.commit()
        row = db.query(NativeChildModel).filter(NativeChildModel.terminal_id == terminal_id).first()
        return _native_child_row(row) if row is not None else None


def _record_native_child_cleanup_rows(db: Any, terminal_ids: List[str]) -> None:
    """Apply cleanup evidence through the caller's active transaction.

    Keep the cancellation as a conditional UPDATE: terminal cleanup and receipt
    settlement can race, so a stale ORM row must never overwrite a state that
    another writer already settled.
    """
    if not terminal_ids:
        return
    now = _utcnow()
    db.query(NativeChildModel).filter(NativeChildModel.terminal_id.in_(terminal_ids)).update(
        {"cleanup_completed_at": now, "updated_at": now},
        synchronize_session=False,
    )
    db.query(NativeChildModel).filter(
        NativeChildModel.terminal_id.in_(terminal_ids),
        NativeChildModel.state.in_(sorted(_NATIVE_CHILD_ACTIVE_STATES)),
    ).update(
        {
            "state": "cancelled",
            "settled_at": now,
            "error_kind": "cancelled",
            "error_summary": "terminal removed before a durable task result was recorded",
            "updated_at": now,
        },
        synchronize_session=False,
    )


def delete_terminals_by_session(tmux_session: str) -> int:
    """Delete all terminals in a session."""
    with SessionLocal() as db:
        terminal_ids = [
            cast(str, row[0])
            for row in db.query(TerminalModel.id)
            .filter(TerminalModel.tmux_session == tmux_session)
            .all()
        ]
        deleted = (
            db.query(TerminalModel)
            .filter(TerminalModel.id.in_(terminal_ids))
            .delete(synchronize_session=False)
        )
        _record_native_child_cleanup_rows(db, terminal_ids)
        _delete_terminal_turn_receipt_rows(db, terminal_ids)
        db.commit()
        return deleted


def delete_terminals_by_ids(terminal_ids: List[str]) -> int:
    """Delete specific terminal rows by id. Returns the number deleted.

    Unlike ``delete_terminals_by_session`` (which deletes EVERY row for a
    session name), this deletes only the given ids. Session teardown uses it to
    scope its reconciliation sweep to the incarnation it started tearing down,
    so a concurrent same-name recreate — whose rows carry freshly generated ids
    — is never swept (#498).
    """
    if not terminal_ids:
        return 0
    with SessionLocal() as db:
        deleted = (
            db.query(TerminalModel)
            .filter(TerminalModel.id.in_(terminal_ids))
            .delete(synchronize_session=False)
        )
        _record_native_child_cleanup_rows(db, terminal_ids)
        _delete_terminal_turn_receipt_rows(db, terminal_ids)
        db.commit()
        return deleted


def create_inbox_message(sender_id: str, receiver_id: str, message: str) -> InboxMessage:
    """Create inbox message with status=MessageStatus.PENDING.

    Raises:
        ValueError: If the receiver terminal does not exist.
    """
    with SessionLocal() as db:
        if not db.query(TerminalModel).filter(TerminalModel.id == receiver_id).first():
            raise ValueError(f"Terminal '{receiver_id}' not found")
        inbox_msg = InboxModel(
            sender_id=sender_id,
            receiver_id=receiver_id,
            message=message,
            status=MessageStatus.PENDING.value,
        )
        db.add(inbox_msg)
        db.commit()
        db.refresh(inbox_msg)
        return InboxMessage(
            id=inbox_msg.id,
            sender_id=inbox_msg.sender_id,
            receiver_id=inbox_msg.receiver_id,
            message=inbox_msg.message,
            status=MessageStatus(inbox_msg.status),
            created_at=inbox_msg.created_at,
        )


def _managed_inbox_store_identity_for_session(
    db, *, expected: ManagedInboxStoreIdentity | None = None
) -> ManagedInboxStoreIdentity:
    """Validate path and UUID using the exact SQLAlchemy connection being used."""
    connection = db.connection()
    rows = connection.exec_driver_sql("PRAGMA database_list").fetchall()
    main = next((row[2] for row in rows if row[1] == "main"), None)
    if not main:
        raise RuntimeError("managed inbox requires a file-backed SQLite store")
    try:
        store_identity = str(Path(main).resolve(strict=False))
        configured = str(Path(constants.DATABASE_FILE).resolve(strict=False))
    except (OSError, RuntimeError) as error:
        raise RuntimeError("managed inbox store identity cannot be resolved") from error
    if store_identity != configured:
        raise RuntimeError("managed inbox configured database differs from SQLAlchemy store")
    row = connection.exec_driver_sql(
        "SELECT singleton,store_identity,store_uuid FROM work_inbox_store_context"
    ).fetchall()
    if len(row) != 1 or row[0][0] != 1:
        raise RuntimeError("managed inbox store context is absent or contradictory")
    context = ManagedInboxStoreIdentity(store_identity=row[0][1], store_uuid=row[0][2])
    if context.store_identity != store_identity:
        raise RuntimeError("managed inbox store context names another SQLAlchemy store")
    if expected is not None:
        if not isinstance(expected, ManagedInboxStoreIdentity):
            raise ValueError("verified managed inbox store context required")
        if context != expected:
            raise RuntimeError("managed inbox store UUID changed during this server context")
    return context


def _managed_inbox_store_identity(
    *, expected: ManagedInboxStoreIdentity | None = None
) -> ManagedInboxStoreIdentity:
    """Read the logical context from a real SQLAlchemy connection."""
    with SessionLocal() as db:
        return _managed_inbox_store_identity_for_session(db, expected=expected)


def _create_managed_inbox_message(
    sender_id: str,
    receiver_id: str,
    message: str,
    *,
    store_context: ManagedInboxStoreIdentity,
) -> InboxMessage:
    """Reserve a managed row outside legacy selection, retained from its first write."""
    with SessionLocal() as db:
        _managed_inbox_store_identity_for_session(db, expected=store_context)
        if not db.query(TerminalModel).filter(TerminalModel.id == receiver_id).first():
            raise ValueError(f"Terminal '{receiver_id}' not found")
        inbox_msg = InboxModel(
            sender_id=sender_id,
            receiver_id=receiver_id,
            message=message,
            status=MessageStatus.RECONCILE.value,
        )
        db.add(inbox_msg)
        db.commit()
        db.refresh(inbox_msg)
        return InboxMessage(
            id=inbox_msg.id,
            sender_id=inbox_msg.sender_id,
            receiver_id=inbox_msg.receiver_id,
            message=inbox_msg.message,
            status=MessageStatus(inbox_msg.status),
            created_at=inbox_msg.created_at,
        )


def _managed_inbox_target(
    inbox_id: int, *, store_context: ManagedInboxStoreIdentity
) -> ManagedInboxTarget:
    """Load only a retained managed row and its durable terminal coordinates."""
    if type(inbox_id) is not int or inbox_id <= 0:
        raise ValueError("invalid managed inbox id")
    with SessionLocal() as db:
        _managed_inbox_store_identity_for_session(db, expected=store_context)
        row = (
            db.query(InboxModel, TerminalModel)
            .join(TerminalModel, TerminalModel.id == InboxModel.receiver_id)
            .filter(InboxModel.id == inbox_id)
            .first()
        )
        if row is None or row[0].status != MessageStatus.RECONCILE.value:
            raise ValueError("managed inbox row is absent or not retained")
        inbox_msg, terminal = row
        return ManagedInboxTarget(
            InboxMessage(
                id=inbox_msg.id,
                sender_id=inbox_msg.sender_id,
                receiver_id=inbox_msg.receiver_id,
                message=inbox_msg.message,
                status=MessageStatus(inbox_msg.status),
                created_at=inbox_msg.created_at,
            ),
            terminal.tmux_session,
            terminal.tmux_window,
        )


def get_pending_messages(receiver_id: str, limit: int = 1) -> List[InboxMessage]:
    """Get pending messages ordered by created_at ASC (oldest first)."""
    return get_inbox_messages(receiver_id, limit=limit, status=MessageStatus.PENDING)


def get_inbox_messages(
    receiver_id: str, limit: int = 10, status: Optional[MessageStatus] = None
) -> List[InboxMessage]:
    """Get inbox messages with optional status filter ordered by created_at ASC (oldest first).

    Args:
        receiver_id: Terminal ID to get messages for
        limit: Maximum number of messages to return (default: 10)
        status: Optional filter by message status (None = all statuses)

    Returns:
        List of inbox messages ordered by creation time (oldest first)
    """
    with SessionLocal() as db:
        query = db.query(InboxModel).filter(InboxModel.receiver_id == receiver_id)

        if status is not None:
            query = query.filter(InboxModel.status == status.value)

        messages = query.order_by(InboxModel.created_at.asc()).limit(limit).all()

        return [
            InboxMessage(
                id=msg.id,
                sender_id=msg.sender_id,
                receiver_id=msg.receiver_id,
                message=msg.message,
                status=MessageStatus(msg.status),
                created_at=msg.created_at,
            )
            for msg in messages
        ]


def record_project_alias(project_id: str, alias: str, kind: str) -> None:
    """Idempotently record a project_id ↔ alias mapping (Phase 2.5 U6).

    Used opportunistically by ``resolve_project_id`` to track historical
    cwd-hash and git-remote-url aliases for a canonical project_id. Best-effort
    only — DB errors are swallowed so identity resolution is never blocked.
    """
    if not project_id or not alias or project_id == alias:
        return
    try:
        with SessionLocal() as db:
            # Upsert by alias (the primary key). If the same alias was already
            # mapped — e.g. recorded against an override id, then re-resolved
            # via git remote — repoint it to the current canonical project_id
            # so reverse lookups stay deterministic instead of duplicating.
            existing = db.query(ProjectAliasModel).filter(ProjectAliasModel.alias == alias).first()
            if existing is None:
                db.add(ProjectAliasModel(project_id=project_id, alias=alias, kind=kind))
                db.commit()
            elif existing.project_id != project_id or existing.kind != kind:
                existing.project_id = project_id
                existing.kind = kind
                db.commit()
    except Exception as e:
        logger.debug(f"record_project_alias failed (non-fatal): {e}")


class ProjectAliasLookupUnavailableError(RuntimeError):
    """Raised when a required project-alias lookup cannot reach the database."""


def get_project_id_by_alias(alias: str, *, fail_closed: bool = False) -> Optional[str]:
    """Return the canonical ``project_id`` for an alias, or None if unknown.

    Callers that enforce a vault boundary can request ``fail_closed`` so a
    database outage cannot be mistaken for an unrecognized alias.
    """
    if not alias:
        return None
    try:
        with SessionLocal() as db:
            row = db.query(ProjectAliasModel).filter(ProjectAliasModel.alias == alias).first()
            return cast(Optional[str], row.project_id) if row else None
    except Exception as e:
        if fail_closed:
            raise ProjectAliasLookupUnavailableError(str(e)) from e
        logger.debug(f"get_project_id_by_alias failed (non-fatal): {e}")
        return None


def list_aliases_for_project(project_id: str) -> List[Dict[str, Any]]:
    """List all aliases recorded for a canonical ``project_id``."""
    if not project_id:
        return []
    try:
        with SessionLocal() as db:
            rows = (
                db.query(ProjectAliasModel).filter(ProjectAliasModel.project_id == project_id).all()
            )
            return [{"project_id": r.project_id, "alias": r.alias, "kind": r.kind} for r in rows]
    except Exception as e:
        logger.debug(f"list_aliases_for_project failed (non-fatal): {e}")
        return []


# ---------------------------------------------------------------------------
# Handoff result durability helpers (issue #447)
# ---------------------------------------------------------------------------


def upsert_handoff_result(
    job_id: str,
    state: str,
    *,
    terminal_id: Optional[str] = None,
    last_message: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """Create or update the durable record for a handoff step (issue #447).

    Called from three places, NOT two, and only one of them is the handler:

    1. ``api.main.run_step``, at request start — ``state="running"``.
    2. ``services.agent_step.run_agent_step``, between result extraction and
       terminal teardown — ``state="completed"``. NOT the handler after
       ``run_agent_step`` returns: by then the terminal holding the only other
       copy of the result is already gone.
    3. ``api.main.run_step``'s failure arms — ``state="error"``. The handler owns
       these because only it can distinguish the exception types.

    Together, 2 and 3 make the result retrievable via
    ``GET /handoff-results/{job_id}`` even if the transport closes before the
    response arrives.

    Idempotent per key: a second call for the same ``job_id`` updates the existing
    row. That is last-write-wins bookkeeping, NOT execution deduplication -- there
    is no mechanism by which a concurrent or retried call observes ``"running"``
    and waits; a second call with the same key runs a second step.
    """
    now = _utcnow()
    with SessionLocal() as db:
        row = db.query(HandoffResultModel).filter(HandoffResultModel.job_id == job_id).first()
        if row is None:
            row = HandoffResultModel(
                job_id=job_id,
                state=state,
                terminal_id=terminal_id,
                last_message=last_message,
                error_message=error_message,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
        else:
            row.state = state
            if terminal_id is not None:
                row.terminal_id = terminal_id
            if last_message is not None:
                row.last_message = last_message
            if error_message is not None:
                row.error_message = error_message
            row.updated_at = now
        db.commit()


def get_handoff_result(job_id: str) -> Optional[dict]:
    """Return the handoff result record for ``job_id``, or None if not found."""
    with SessionLocal() as db:
        row = db.query(HandoffResultModel).filter(HandoffResultModel.job_id == job_id).first()
        if row is None:
            return None
        return {
            "job_id": row.job_id,
            "state": row.state,
            "terminal_id": row.terminal_id,
            "last_message": row.last_message,
            "error_message": row.error_message,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


def delete_old_handoff_results(cutoff: datetime) -> int:
    """Delete handoff result rows older than ``cutoff`` (retention sweep).

    Returns the number of rows deleted.
    """
    with SessionLocal() as db:
        deleted = (
            db.query(HandoffResultModel).filter(HandoffResultModel.created_at < cutoff).delete()
        )
        db.commit()
        return deleted


def update_message_status(message_id: int, status: MessageStatus) -> bool:
    """Update a message's durable delivery status.

    ``MessageStatus.RECONCILE`` marks an input that may already have reached
    its terminal. It is intentionally excluded from pending-message queries,
    so background delivery never replays it automatically.
    """
    with SessionLocal() as db:
        message = db.query(InboxModel).filter(InboxModel.id == message_id).first()
        if message:
            message.status = status.value
            db.commit()
            return True
        return False


# Flow database functions


def create_flow(
    name: str,
    file_path: str,
    schedule: str,
    agent_profile: str,
    provider: str,
    script: str,
    next_run: datetime,
) -> Flow:
    """Create flow record."""
    with SessionLocal() as db:
        flow = FlowModel(
            name=name,
            file_path=file_path,
            schedule=schedule,
            agent_profile=agent_profile,
            provider=provider,
            script=script,
            next_run=next_run,
        )
        db.add(flow)
        db.commit()
        db.refresh(flow)
        return Flow(
            name=flow.name,
            file_path=flow.file_path,
            schedule=flow.schedule,
            agent_profile=flow.agent_profile,
            provider=flow.provider,
            script=flow.script,
            last_run=flow.last_run,
            next_run=flow.next_run,
            enabled=flow.enabled,
            prompt_template=None,
        )


def get_flow(name: str) -> Optional[Flow]:
    """Get flow by name."""
    with SessionLocal() as db:
        flow = db.query(FlowModel).filter(FlowModel.name == name).first()
        if not flow:
            return None
        return Flow(
            name=flow.name,
            file_path=flow.file_path,
            schedule=flow.schedule,
            agent_profile=flow.agent_profile,
            provider=flow.provider,
            script=flow.script,
            last_run=flow.last_run,
            next_run=flow.next_run,
            enabled=flow.enabled,
            prompt_template=None,
        )


def list_flows() -> List[Flow]:
    """List all flows."""
    with SessionLocal() as db:
        flows = db.query(FlowModel).order_by(FlowModel.next_run).all()
        return [
            Flow(
                name=f.name,
                file_path=f.file_path,
                schedule=f.schedule,
                agent_profile=f.agent_profile,
                provider=f.provider,
                script=f.script,
                last_run=f.last_run,
                next_run=f.next_run,
                enabled=f.enabled,
                prompt_template=None,
            )
            for f in flows
        ]


def update_flow_run_times(name: str, last_run: datetime, next_run: datetime) -> bool:
    """Update flow run times after execution."""
    with SessionLocal() as db:
        flow = db.query(FlowModel).filter(FlowModel.name == name).first()
        if flow:
            flow.last_run = last_run
            flow.next_run = next_run
            db.commit()
            return True
        return False


def update_flow_enabled(name: str, enabled: bool, next_run: Optional[datetime] = None) -> bool:
    """Update flow enabled status and optionally next_run."""
    with SessionLocal() as db:
        flow = db.query(FlowModel).filter(FlowModel.name == name).first()
        if flow:
            flow.enabled = enabled
            if next_run is not None:
                flow.next_run = next_run
            db.commit()
            return True
        return False


def delete_flow(name: str) -> bool:
    """Delete flow."""
    with SessionLocal() as db:
        deleted = db.query(FlowModel).filter(FlowModel.name == name).delete()
        db.commit()
        return deleted > 0


def get_flows_to_run() -> List[Flow]:
    """Get enabled flows where next_run <= now."""
    with SessionLocal() as db:
        now = datetime.now()
        flows = (
            db.query(FlowModel).filter(FlowModel.enabled == True, FlowModel.next_run <= now).all()
        )
        return [
            Flow(
                name=f.name,
                file_path=f.file_path,
                schedule=f.schedule,
                agent_profile=f.agent_profile,
                provider=f.provider,
                script=f.script,
                last_run=f.last_run,
                next_run=f.next_run,
                enabled=f.enabled,
                prompt_template=None,
            )
            for f in flows
        ]


def update_terminal_provider_variant(terminal_id: str, provider_variant: str) -> bool:
    """Persist a resolved provider runtime variant for restart reconstruction."""

    with SessionLocal() as db:
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if terminal:
            terminal.provider_variant = provider_variant
            db.commit()
            return True
        return False


def _migrate_workflow_plan_snapshot() -> None:
    """Create the integrity-checked private plan snapshot schema.

    This migration is deliberately separate from the private snapshot store.
    Callers that will lend a SQLite transaction to ``freeze_and_attach`` must
    run it before opening that transaction; attempting DDL through the borrowed
    connection could commit caller work or deadlock against its write lock.

    Startup's existing database chmod remains best-effort for compatibility;
    this migrator itself is mode-agnostic. Private snapshot reads and writes
    independently enforce their stricter fail-closed permission contract before
    touching persisted executable material.
    """
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        with closing(sqlite3.connect(str(DATABASE_FILE))) as connection, connection as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS workflow_plan_snapshot ("
                "plan_id TEXT PRIMARY KEY, "
                "component_set_version TEXT NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS workflow_plan_snapshot_component ("
                "plan_id TEXT NOT NULL, "
                "component_name TEXT NOT NULL, "
                "content_digest TEXT NOT NULL, "
                "content BLOB NOT NULL, "
                "PRIMARY KEY (plan_id, component_name), "
                "FOREIGN KEY (plan_id) REFERENCES workflow_plan_snapshot(plan_id) "
                "ON DELETE CASCADE"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS workflow_run_plan_snapshot ("
                "run_id TEXT PRIMARY KEY, "
                "plan_id TEXT NOT NULL, "
                "FOREIGN KEY (run_id) REFERENCES workflow_run(run_id) ON DELETE CASCADE, "
                "FOREIGN KEY (plan_id) REFERENCES workflow_plan_snapshot(plan_id) "
                "ON DELETE RESTRICT"
                ")"
            )
            for statement in (
                "CREATE TABLE IF NOT EXISTS workflow_prepared_plan (prepared_id TEXT PRIMARY KEY,plan_id TEXT NOT NULL REFERENCES workflow_plan_snapshot(plan_id),workflow_name TEXT NOT NULL,tier TEXT NOT NULL,source_hash TEXT NOT NULL,principal_id TEXT NOT NULL,created_at REAL NOT NULL,expires_at REAL NOT NULL,public_manifest_json TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS workflow_scoped_run (run_id TEXT PRIMARY KEY REFERENCES workflow_run(run_id) ON DELETE CASCADE,prepared_id TEXT NOT NULL REFERENCES workflow_prepared_plan(prepared_id),principal_id TEXT NOT NULL,plan_id TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS workflow_plan_step_alias (run_id TEXT NOT NULL REFERENCES workflow_run(run_id) ON DELETE CASCADE,step_id TEXT NOT NULL,target_key TEXT NOT NULL,agent_profile TEXT NOT NULL,workflow_alias TEXT NOT NULL,seed_id TEXT NOT NULL,seed_revision INTEGER NOT NULL,provision_id TEXT NOT NULL,PRIMARY KEY(run_id,step_id))",
            ):
                conn.execute(statement)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_workflow_run_plan_snapshot_plan "
                "ON workflow_run_plan_snapshot(plan_id)"
            )
    except Exception as e:  # noqa: BLE001 — retryable; strict operations fail closed
        logger.debug(f"workflow_plan_snapshot migration skipped: {e}")


def update_terminal_kiro_policy_digest(terminal_id: str, digest: Optional[str]) -> bool:
    if digest is not None and not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("KAS policy digest must be lowercase SHA-256")
    with SessionLocal() as db:
        terminal = db.get(TerminalModel, terminal_id)
        if terminal is None:
            return False
        terminal.kiro_policy_digest = digest
        db.commit()
        return True


def _migrate_workflow_continuations() -> None:
    """Initialize verified additive controller/correlation tables after Work."""
    from cli_agent_orchestrator.clients.beads_work_schema import initialize as initialize_beads
    from cli_agent_orchestrator.clients.work_continuation_schema import initialize

    connection = engine.raw_connection()
    try:
        initialize(connection)
        initialize_beads(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
