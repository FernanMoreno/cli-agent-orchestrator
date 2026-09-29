"""Closed, read-only inventory of the current v25 Work SQLite profile.

This module deliberately describes references only.  It does not resolve a
path, read an artifact, or treat a digest as capture authority.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from cli_agent_orchestrator.clients.work_inbox_schema import ManagedInboxStoreIdentity
from cli_agent_orchestrator.clients.work_repository import (
    _CHECKSUMS,
    _EXPECTED_SCHEMAS,
    SCHEMA_VERSION,
    SchemaMismatch,
    WorkRepository,
    _schema_objects,
    _store_identity,
    _store_uuid,
    _stored_recovery_context,
)

WORK_SQLITE_PROFILE_VERSION = 29
_WORK_SCHEMA_VERSION = 38
_PROFILE_SCHEMA_VERSIONS = {28: 30, 29: 38}
_INCOMPATIBLE = "recovery inventory incompatible"

_V25_TABLES = (
    "flows",
    "idempotency_keys",
    "inbox",
    "memory_metadata",
    "memory_relationships",
    "native_children",
    "project_aliases",
    "terminal_turn_receipts",
    "terminals",
    "work_attempts",
    "work_bubblewrap_process_identities",
    "work_child_origin_bindings",
    "work_delegation_snapshots",
    "work_delivery_content_refs",
    "work_delivery_orders",
    "work_dispatch_bindings",
    "work_dispatch_v2_evidence",
    "work_event_sequences",
    "work_events",
    "work_executable_contents",
    "work_grant_revocations",
    "work_grants",
    "work_human_decision_claims",
    "work_human_decision_revocations",
    "work_human_decisions",
    "work_inbox_bindings",
    "work_inbox_store_context",
    "work_inbox_store_identity",
    "work_items",
    "work_jobs",
    "work_knowledge_access_audit",
    "work_knowledge_cursors",
    "work_knowledge_decisions",
    "work_knowledge_events",
    "work_knowledge_records",
    "work_knowledge_revisions",
    "work_knowledge_tombstones",
    "work_launch_origin_bindings",
    "work_launch_provisions",
    "work_lineage_integrity",
    "work_lineage_projection_intents",
    "work_memory_access_audit",
    "work_migrations",
    "work_offline_cut_observations",
    "work_offline_cut_rejections",
    "work_offline_cuts",
    "work_origin_authorizations",
    "work_origin_subjects",
    "work_path_reservations",
    "work_principals",
    "work_process_identities",
    "work_recovery_context",
    "work_registered_writers",
    "work_reservation_sets",
    "work_results",
    "work_scheduler_dependencies",
    "work_scheduler_policy",
    "work_scheduler_requests",
    "work_snapshot_sources",
    "work_step_contracts",
    "work_task_received_receipts",
    "work_transition_receipts",
    "work_worktree_evidence",
    "workflow_index",
    "workflow_outcomes",
    "workflow_plan_approval",
    "workflow_run",
    "workflow_run_event",
    "workflow_run_seq",
    "workflow_run_step",
)

_LEGACY_TABLE_COLUMNS = {
    "flows": (
        "name",
        "file_path",
        "schedule",
        "agent_profile",
        "provider",
        "script",
        "last_run",
        "next_run",
        "enabled",
    ),
    "idempotency_keys": ("key", "terminal_id", "request_fingerprint", "created_at"),
    "inbox": ("id", "sender_id", "receiver_id", "message", "status", "created_at"),
    "memory_metadata": (
        "id",
        "key",
        "memory_type",
        "scope",
        "scope_id",
        "file_path",
        "tags",
        "source_provider",
        "source_terminal_id",
        "token_estimate",
        "created_at",
        "updated_at",
        "access_count",
        "last_accessed_at",
        "last_compiled_at",
        "related_keys",
    ),
    "memory_relationships": (
        "id",
        "scope",
        "scope_id",
        "source_key",
        "target_key",
        "type",
        "origin",
        "status",
        "confidence",
        "rank",
        "attributes_json",
        "source_updated_at",
        "created_at",
        "updated_at",
    ),
    "native_children": (
        "id",
        "parent_terminal_id",
        "terminal_id",
        "provider",
        "agent_profile",
        "state",
        "lease_expires_at",
        "error_kind",
        "error_summary",
        "cleanup_completed_at",
        "created_at",
        "updated_at",
        "settled_at",
    ),
    "project_aliases": ("alias", "project_id", "kind", "created_at"),
    "terminal_turn_receipts": (
        "terminal_id",
        "provider",
        "generation",
        "receipt_sha256",
        "phase",
        "result_sha256",
        "created_at",
        "updated_at",
        "verified_at",
    ),
    "terminals": (
        "id",
        "tmux_session",
        "tmux_window",
        "provider",
        "agent_profile",
        "working_directory",
        "allowed_tools",
        "shell_command",
        "caller_id",
        "engine",
        "group",
        "metadata",
        "last_active",
    ),
    "workflow_index": ("name", "source_path", "mode", "step_count", "description", "indexed_at"),
    "workflow_outcomes": (
        "id",
        "session_name",
        "workflow_name",
        "task_label",
        "agent_profile",
        "source_terminal_id",
        "success",
        "score",
        "friction_notes",
        "created_at",
    ),
    "workflow_plan_approval": ("plan_id", "approved_at", "approved_by"),
    "workflow_run": (
        "run_id",
        "workflow_name",
        "spec_snapshot",
        "inputs_json",
        "state",
        "current_step_id",
        "started_at",
        "finished_at",
        "tier",
        "generation",
        "manifest_json",
        "error",
    ),
    "workflow_run_event": (
        "run_id",
        "seq",
        "event_type",
        "event_schema_version",
        "ts",
        "step_id",
        "attempt",
        "state",
        "elapsed_ms",
        "provider",
        "agent_profile",
        "engine",
        "terminal_id",
        "terminal_offset_start",
        "terminal_offset_len",
        "error_kind",
        "reason",
        "validation_result",
        "output_ref",
        "iteration",
        "which_guard_fired",
    ),
    "workflow_run_seq": ("run_id", "high_water"),
    "workflow_run_step": (
        "run_id",
        "step_id",
        "state",
        "attempts",
        "output_json",
        "error",
        "updated_at",
        "call_fingerprint",
        "terminal_id",
        "reprompted",
        "error_kind",
        "result_json",
    ),
}


class RecoveryInventoryError(RuntimeError):
    """The installed store is not the one closed v24 profile this step supports."""


@dataclass(frozen=True, order=True)
class ForeignKeyReference:
    """One PRAGMA-visible SQLite FK, preserving composite-column correspondence."""

    source_table: str
    source_columns: tuple[str, ...]
    target_table: str
    target_columns: tuple[str, ...]
    on_update: str
    on_delete: str
    match_observed: str


# Reviewed against the real v25 ``init_db()`` profile (112 constraints, 175
# component rows).  This is deliberately a fixed multiset rather than an
# inventory learned from the inspected connection or current migrations.
_V25_FOREIGN_KEYS = (
    ForeignKeyReference(
        "work_attempts", ("result_id",), "work_results", ("id",), "NO ACTION", "NO ACTION", "NONE"
    ),
    ForeignKeyReference(
        "work_attempts", ("work_item_id",), "work_items", ("id",), "NO ACTION", "NO ACTION", "NONE"
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("child_attempt_id", "child_generation"),
        "work_dispatch_bindings",
        ("attempt_id", "generation"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("child_subject_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("child_subject_id", "child_authorization_kind", "child_authorization_revision"),
        "work_origin_authorizations",
        ("subject_id", "origin_kind", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("child_subject_id", "child_subject_revision"),
        "work_origin_subjects",
        ("subject_id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("child_work_item_id",),
        "work_items",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("executor_principal_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("job_id",),
        "work_jobs",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("job_id", "child_grant_id", "child_grant_revision"),
        "work_grants",
        ("job_id", "id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("job_id", "receiver_grant_id", "receiver_grant_revision"),
        "work_grants",
        ("job_id", "id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("parent_attempt_id", "parent_generation"),
        "work_dispatch_bindings",
        ("attempt_id", "generation"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("parent_work_item_id",),
        "work_items",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("receiver_subject_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("receiver_subject_id", "receiver_authorization_kind", "receiver_authorization_revision"),
        "work_origin_authorizations",
        ("subject_id", "origin_kind", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("receiver_subject_id", "receiver_subject_revision"),
        "work_origin_subjects",
        ("subject_id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("requester_principal_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_child_origin_bindings",
        ("snapshot_id",),
        "work_delegation_snapshots",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_delegation_snapshots",
        ("job_id",),
        "work_jobs",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_delegation_snapshots",
        ("producer_principal_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_delivery_content_refs",
        ("attempt_id", "generation"),
        "work_delivery_orders",
        ("attempt_id", "generation"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_delivery_content_refs",
        ("attempt_id", "generation"),
        "work_dispatch_bindings",
        ("attempt_id", "generation"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_delivery_content_refs",
        ("owner_principal_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_delivery_content_refs",
        ("snapshot_id",),
        "work_delegation_snapshots",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_delivery_orders",
        ("attempt_id", "generation"),
        "work_dispatch_bindings",
        ("attempt_id", "generation"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_dispatch_bindings",
        ("job_id", "grant_id", "grant_revision"),
        "work_grants",
        ("job_id", "id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_dispatch_bindings",
        ("job_id", "work_item_id"),
        "work_items",
        ("job_id", "id"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_dispatch_bindings",
        ("principal_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_dispatch_bindings",
        ("snapshot_id",),
        "work_delegation_snapshots",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_dispatch_bindings",
        ("work_item_id", "attempt_id", "generation"),
        "work_attempts",
        ("work_item_id", "id", "generation"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_event_sequences", ("job_id",), "work_jobs", ("id",), "NO ACTION", "NO ACTION", "NONE"
    ),
    ForeignKeyReference(
        "work_events", ("attempt_id",), "work_attempts", ("id",), "NO ACTION", "NO ACTION", "NONE"
    ),
    ForeignKeyReference(
        "work_events", ("job_id",), "work_jobs", ("id",), "NO ACTION", "NO ACTION", "NONE"
    ),
    ForeignKeyReference(
        "work_events", ("work_item_id",), "work_items", ("id",), "NO ACTION", "NO ACTION", "NONE"
    ),
    ForeignKeyReference(
        "work_grant_revocations",
        ("actor_principal_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_grant_revocations",
        ("grant_id", "grant_revision"),
        "work_grants",
        ("id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_grants", ("job_id",), "work_jobs", ("id",), "NO ACTION", "NO ACTION", "NONE"
    ),
    ForeignKeyReference(
        "work_grants",
        ("job_id", "parent_grant_id", "parent_revision"),
        "work_grants",
        ("job_id", "id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_grants",
        ("principal_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_human_decision_claims",
        ("actor_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_human_decision_claims",
        ("decision_id",),
        "work_human_decisions",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_human_decision_revocations",
        ("actor_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_human_decision_revocations",
        ("decision_id",),
        "work_human_decisions",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_human_decisions",
        ("actor_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_human_decisions",
        ("attempt_id",),
        "work_attempts",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_human_decisions",
        ("attempt_id", "generation"),
        "work_dispatch_bindings",
        ("attempt_id", "generation"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_human_decisions",
        ("job_id", "work_item_id"),
        "work_items",
        ("job_id", "id"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_inbox_bindings",
        ("attempt_id", "generation"),
        "work_dispatch_bindings",
        ("attempt_id", "generation"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_inbox_bindings",
        ("store_identity",),
        "work_inbox_store_identity",
        ("store_identity",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_items",
        ("accepted_result_id",),
        "work_results",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_items", ("job_id",), "work_jobs", ("id",), "NO ACTION", "NO ACTION", "NONE"
    ),
    ForeignKeyReference(
        "work_items",
        ("job_id", "parent_work_item_id"),
        "work_items",
        ("job_id", "id"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_cursors",
        ("grant_id", "grant_revision"),
        "work_grants",
        ("id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_cursors",
        ("job_id",),
        "work_jobs",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_cursors",
        ("principal_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_decisions",
        ("actor_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_decisions",
        ("record_id", "event_sequence"),
        "work_knowledge_events",
        ("record_id", "sequence"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_decisions",
        ("record_id", "revision"),
        "work_knowledge_revisions",
        ("record_id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_events",
        ("actor_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_events",
        ("record_id",),
        "work_knowledge_records",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_events",
        ("record_id", "revision"),
        "work_knowledge_revisions",
        ("record_id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_revisions",
        ("attempt_id",),
        "work_attempts",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_revisions",
        ("producer_principal_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_revisions",
        ("record_id",),
        "work_knowledge_records",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_revisions",
        ("record_id", "supersedes"),
        "work_knowledge_revisions",
        ("record_id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_revisions",
        ("source_artifact_id",),
        "work_results",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_revisions",
        ("work_item_id",),
        "work_items",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_tombstones",
        ("actor_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_tombstones",
        ("record_id", "event_sequence"),
        "work_knowledge_events",
        ("record_id", "sequence"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_knowledge_tombstones",
        ("record_id", "revision"),
        "work_knowledge_revisions",
        ("record_id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_launch_origin_bindings",
        ("attempt_id", "generation"),
        "work_dispatch_bindings",
        ("attempt_id", "generation"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_launch_origin_bindings",
        ("executor_principal_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_launch_origin_bindings",
        ("job_id",),
        "work_jobs",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_launch_origin_bindings",
        ("principal_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_launch_origin_bindings",
        ("principal_id", "selector", "provision_revision"),
        "work_launch_provisions",
        ("principal_id", "selector", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_launch_origin_bindings",
        ("requester_principal_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_launch_origin_bindings",
        ("work_item_id",),
        "work_items",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_launch_provisions",
        ("issuer_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_launch_provisions",
        ("job_id",),
        "work_jobs",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_launch_provisions",
        ("job_id", "grant_id", "grant_revision"),
        "work_grants",
        ("job_id", "id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_launch_provisions",
        ("principal_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_launch_provisions",
        ("snapshot_id",),
        "work_delegation_snapshots",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_lineage_integrity",
        ("child_attempt_id", "child_generation"),
        "work_child_origin_bindings",
        ("child_attempt_id", "child_generation"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_lineage_projection_intents",
        ("child_attempt_id", "child_generation"),
        "work_child_origin_bindings",
        ("child_attempt_id", "child_generation"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_origin_authorizations",
        ("issuer_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_origin_authorizations",
        ("job_id",),
        "work_jobs",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_origin_authorizations",
        ("job_id", "grant_id", "grant_revision"),
        "work_grants",
        ("job_id", "id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_origin_authorizations",
        ("subject_id", "subject_revision"),
        "work_origin_subjects",
        ("subject_id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_origin_subjects",
        ("issuer_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_origin_subjects",
        ("subject_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_path_reservations",
        ("reservation_set_id",),
        "work_reservation_sets",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_reservation_sets",
        ("job_id", "work_item_id"),
        "work_items",
        ("job_id", "id"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_reservation_sets",
        ("work_item_id", "attempt_id", "generation"),
        "work_attempts",
        ("work_item_id", "id", "generation"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_results", ("attempt_id",), "work_attempts", ("id",), "NO ACTION", "NO ACTION", "NONE"
    ),
    ForeignKeyReference(
        "work_scheduler_dependencies",
        ("job_id", "dependency_id"),
        "work_items",
        ("job_id", "id"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_scheduler_dependencies",
        ("job_id", "work_item_id"),
        "work_items",
        ("job_id", "id"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_scheduler_requests",
        ("job_id", "work_item_id"),
        "work_items",
        ("job_id", "id"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_scheduler_requests",
        ("work_item_id", "attempt_id", "generation"),
        "work_attempts",
        ("work_item_id", "id", "generation"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_snapshot_sources",
        ("record_id", "revision"),
        "work_knowledge_revisions",
        ("record_id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_snapshot_sources",
        ("snapshot_id",),
        "work_delegation_snapshots",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_step_contracts",
        ("run_id",),
        "workflow_run",
        ("run_id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_task_received_receipts",
        ("attempt_id", "generation"),
        "work_child_origin_bindings",
        ("child_attempt_id", "child_generation"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_task_received_receipts",
        ("job_id",),
        "work_jobs",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_task_received_receipts",
        ("job_id", "receiver_grant_id", "receiver_grant_revision"),
        "work_grants",
        ("job_id", "id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_task_received_receipts",
        ("receiver_subject_id",),
        "work_principals",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_task_received_receipts",
        (
            "receiver_subject_id",
            "receiver_authorization_kind",
            "receiver_authorization_revision",
        ),
        "work_origin_authorizations",
        ("subject_id", "origin_kind", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_task_received_receipts",
        ("receiver_subject_id", "receiver_subject_revision"),
        "work_origin_subjects",
        ("subject_id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_task_received_receipts",
        ("work_item_id",),
        "work_items",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_transition_receipts",
        ("work_item_id",),
        "work_items",
        ("id",),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_worktree_evidence",
        ("grant_id", "grant_revision"),
        "work_grants",
        ("id", "revision"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_worktree_evidence",
        ("job_id", "work_item_id"),
        "work_items",
        ("job_id", "id"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
    ForeignKeyReference(
        "work_worktree_evidence",
        ("work_item_id", "attempt_id", "generation"),
        "work_attempts",
        ("work_item_id", "id", "generation"),
        "NO ACTION",
        "NO ACTION",
        "NONE",
    ),
)

_V26_FOREIGN_KEYS = tuple(
    sorted(
        (
            *_V25_FOREIGN_KEYS,
            ForeignKeyReference(
                "work_offline_cuts",
                ("operator_principal_id",),
                "work_principals",
                ("id",),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            ),
            ForeignKeyReference(
                "work_offline_cut_observations",
                ("lease_id",),
                "work_offline_cuts",
                ("id",),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            ),
            ForeignKeyReference(
                "work_registered_writers",
                ("writer_id",),
                "work_attempts",
                ("id",),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            ),
        )
    )
)

_V27_FOREIGN_KEYS = tuple(
    sorted(
        (
            *_V26_FOREIGN_KEYS,
            ForeignKeyReference(
                "work_process_identities",
                ("attempt_id",),
                "work_attempts",
                ("id",),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            ),
            ForeignKeyReference(
                "work_dispatch_v2_evidence",
                ("attempt_id", "generation"),
                "work_dispatch_bindings",
                ("attempt_id", "generation"),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            ),
        )
    )
)

_V28_FOREIGN_KEYS = tuple(
    sorted(
        (
            *_V27_FOREIGN_KEYS,
            ForeignKeyReference(
                "work_bubblewrap_process_identities",
                ("attempt_id",),
                "work_attempts",
                ("id",),
                "NO ACTION",
                "NO ACTION",
                "NONE",
            ),
        )
    )
)

_V29_ADDITIONAL_TABLES = (
    "work_attempt_credentials",
    "work_bubblewrap_release_claims",
    "work_bubblewrap_setup_intents",
    "work_mcp_proxy_effect_events",
    "work_mcp_proxy_effects",
    "work_mcp_proxy_issue_events",
    "work_mcp_proxy_issues",
    "work_task_receiver_acceptances",
    "work_task_receiver_credentials",
)
_V29_TABLES = tuple(sorted((*_V25_TABLES, *_V29_ADDITIONAL_TABLES)))


def _known_v29_fk(
    source: str, source_columns: tuple[str, ...], target: str, target_columns: tuple[str, ...]
) -> ForeignKeyReference:
    return ForeignKeyReference(
        source, source_columns, target, target_columns, "NO ACTION", "NO ACTION", "NONE"
    )


# Frozen FK additions from Work migrations 31 through 38. The v28 catalog
# remains tied to schema 30 for historical bundle verification.
_V29_FOREIGN_KEYS = tuple(
    sorted(
        (
            *_V28_FOREIGN_KEYS,
            _known_v29_fk(
                "work_attempt_credentials",
                ("attempt_id", "generation", "contract_hash"),
                "work_dispatch_bindings",
                ("attempt_id", "generation", "contract_hash"),
            ),
            _known_v29_fk(
                "work_attempt_credentials",
                ("job_id", "grant_id", "grant_revision"),
                "work_grants",
                ("job_id", "id", "revision"),
            ),
            _known_v29_fk(
                "work_attempt_credentials",
                ("job_id", "work_item_id"),
                "work_items",
                ("job_id", "id"),
            ),
            _known_v29_fk(
                "work_attempt_credentials", ("principal_id",), "work_principals", ("id",)
            ),
            _known_v29_fk(
                "work_attempt_credentials",
                ("work_item_id", "attempt_id", "generation"),
                "work_attempts",
                ("work_item_id", "id", "generation"),
            ),
            _known_v29_fk(
                "work_bubblewrap_release_claims",
                (
                    "attempt_id",
                    "generation",
                    "contract_hash",
                    "command_token",
                    "executable_sha256",
                    "ack_sha256",
                    "process_identity_sha256",
                ),
                "work_bubblewrap_setup_intents",
                (
                    "attempt_id",
                    "generation",
                    "contract_hash",
                    "command_token",
                    "executable_sha256",
                    "ack_sha256",
                    "process_identity_sha256",
                ),
            ),
            _known_v29_fk(
                "work_bubblewrap_setup_intents", ("attempt_id",), "work_attempts", ("id",)
            ),
            _known_v29_fk(
                "work_bubblewrap_setup_intents",
                ("attempt_id", "generation", "process_identity_sha256"),
                "work_bubblewrap_process_identities",
                ("attempt_id", "generation", "identity_sha256"),
            ),
            _known_v29_fk(
                "work_bubblewrap_setup_intents",
                ("executable_sha256",),
                "work_executable_contents",
                ("content_hash",),
            ),
            _known_v29_fk(
                "work_mcp_proxy_effect_events",
                ("effect_id",),
                "work_mcp_proxy_effects",
                ("effect_id",),
            ),
            _known_v29_fk(
                "work_mcp_proxy_effects", ("attempt_id",), "work_mcp_proxy_issues", ("attempt_id",)
            ),
            _known_v29_fk(
                "work_mcp_proxy_effects",
                ("attempt_id", "generation"),
                "work_mcp_proxy_issues",
                ("attempt_id", "generation"),
            ),
            _known_v29_fk(
                "work_mcp_proxy_issue_events",
                ("attempt_id", "generation"),
                "work_mcp_proxy_issues",
                ("attempt_id", "generation"),
            ),
            _known_v29_fk(
                "work_mcp_proxy_issues",
                ("attempt_id", "generation", "contract_hash"),
                "work_dispatch_bindings",
                ("attempt_id", "generation", "contract_hash"),
            ),
            _known_v29_fk(
                "work_task_receiver_acceptances",
                ("attempt_id", "generation"),
                "work_child_origin_bindings",
                ("child_attempt_id", "child_generation"),
            ),
            _known_v29_fk(
                "work_task_receiver_acceptances",
                ("receiver_subject_id", "receiver_origin_kind", "receiver_authorization_revision"),
                "work_origin_authorizations",
                ("subject_id", "origin_kind", "revision"),
            ),
            _known_v29_fk(
                "work_task_receiver_acceptances",
                ("receiver_subject_id", "receiver_origin_kind", "receiver_subject_revision"),
                "work_origin_subjects",
                ("subject_id", "origin_kind", "revision"),
            ),
            _known_v29_fk(
                "work_task_receiver_credentials",
                ("attempt_id", "generation"),
                "work_attempt_credentials",
                ("attempt_id", "generation"),
            ),
            _known_v29_fk(
                "work_task_receiver_credentials",
                ("attempt_id", "generation"),
                "work_child_origin_bindings",
                ("child_attempt_id", "child_generation"),
            ),
            _known_v29_fk(
                "work_task_receiver_credentials",
                ("job_id", "receiver_grant_id", "receiver_grant_revision"),
                "work_grants",
                ("job_id", "id", "revision"),
            ),
            _known_v29_fk(
                "work_task_receiver_credentials",
                ("job_id", "work_item_id"),
                "work_items",
                ("job_id", "id"),
            ),
            _known_v29_fk(
                "work_task_receiver_credentials",
                ("receiver_subject_id",),
                "work_principals",
                ("id",),
            ),
            _known_v29_fk(
                "work_task_receiver_credentials",
                ("work_item_id", "attempt_id", "generation"),
                "work_attempts",
                ("work_item_id", "id", "generation"),
            ),
        )
    )
)

_PROFILE_TABLES = {28: _V25_TABLES, 29: _V29_TABLES}
_PROFILE_FOREIGN_KEYS = {28: _V28_FOREIGN_KEYS, 29: _V29_FOREIGN_KEYS}


@dataclass(frozen=True, order=True)
class ReferenceFamily:
    """A reviewed reference family, never a resolver or content reader."""

    family: str
    resolution: str
    source_table: str
    source_columns: tuple[str, ...]
    target_table: str | None = None
    target_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkStoreInventory:
    """Canonical v24 inventory suitable as a later fingerprint input."""

    profile_version: int
    tables: tuple[str, ...]
    foreign_keys: tuple[ForeignKeyReference, ...]
    references: tuple[ReferenceFamily, ...]


def _portable_inbox_store_identity(connection: sqlite3.Connection) -> ManagedInboxStoreIdentity:
    """Read a canonical inbox identity without binding it to this connection's path."""
    try:
        rows = connection.execute(
            "SELECT singleton,store_identity,store_uuid FROM work_inbox_store_context"
        ).fetchall()
    except sqlite3.DatabaseError as error:
        raise SchemaMismatch("managed inbox store context is absent") from error
    if len(rows) != 1:
        raise SchemaMismatch("managed inbox store context is absent or contradictory")
    try:
        singleton, stored_identity, stored_uuid = rows[0]
        canonical_identity = _store_identity(stored_identity)
        canonical_uuid = _store_uuid(stored_uuid)
    except (IndexError, TypeError, ValueError) as error:
        raise SchemaMismatch("managed inbox store context is corrupt") from error
    if singleton != 1 or stored_identity != canonical_identity:
        raise SchemaMismatch("managed inbox store context is corrupt")
    return ManagedInboxStoreIdentity(canonical_identity, canonical_uuid)


def verified_inbox_store_identity(
    connection: sqlite3.Connection, *, profile_version: int = WORK_SQLITE_PROFILE_VERSION
) -> ManagedInboxStoreIdentity:
    """Return inbox identity only after the selected closed profile verifies."""
    inspect_work_store(connection, profile_version=profile_version)
    return _portable_inbox_store_identity(connection)


def _verify_portable_work_profile(connection: sqlite3.Connection, *, schema_version: int) -> None:
    """Mirror one repository verifier except for staging's physical filename.

    The strict verifier remains the only execution gate.  This narrow variant
    preserves all its DDL, migration ledger, recovery-context and foreign-key
    checks while leaving path substitution local to recovery inspection.
    """
    expected = _EXPECTED_SCHEMAS[schema_version]
    if _schema_objects(connection) != expected:
        raise SchemaMismatch("work schema is incomplete, incompatible or modified")
    records = connection.execute(
        "SELECT version,checksum,verification_result FROM work_migrations ORDER BY version"
    ).fetchall()
    if [tuple(row) for row in records] != [
        (number, _CHECKSUMS[number], "verified") for number in range(1, schema_version + 1)
    ]:
        raise SchemaMismatch("work migration ledger is incompatible or modified")
    _stored_recovery_context(connection)
    for table, sql in expected.items():
        if (
            sql.startswith("CREATE TABLE")
            and connection.execute(f"PRAGMA foreign_key_check({table})").fetchone()
        ):
            raise SchemaMismatch("work store contains invalid references")


def inspect_portable_work_store(
    connection: sqlite3.Connection,
    *,
    expected_source_identity: ManagedInboxStoreIdentity,
    profile_version: int = WORK_SQLITE_PROFILE_VERSION,
) -> WorkStoreInventory:
    """Inspect a read-only staging copy against one strictly verified source identity.

    A SQLite backup retains the source inbox identity, so its physical staging
    filename must not be adopted.  This function compares the canonical stored
    identity/UUID pair to the strictly verified source and returns inventory
    only; it never grants execution authority to the copy.
    """
    if not isinstance(expected_source_identity, ManagedInboxStoreIdentity):
        raise RecoveryInventoryError(_INCOMPATIBLE)
    return _inspect_work_profile(
        connection,
        profile_version=profile_version,
        portable=True,
        expected_source_identity=expected_source_identity,
    )


def inspect_offline_work_store(
    connection: sqlite3.Connection, *, profile_version: int = WORK_SQLITE_PROFILE_VERSION
) -> WorkStoreInventory:
    """Verify a bundle SQLite object without adopting its historical store path.

    Unlike the staging inspector, this pure verifier has no source-store path
    available to compare.  It retains every v24 profile check and validates the
    persisted inbox identity's canonical form, but never grants execution
    authority to the historical copy.
    """
    return _inspect_work_profile(connection, profile_version=profile_version, portable=True)


_REFERENCE_FAMILIES = tuple(
    sorted(
        (
            ReferenceFamily(
                "delivery_content_store",
                "external_bytes",
                "work_delivery_content_refs",
                ("content_ref", "content_hash", "byte_length"),
            ),
            ReferenceFamily(
                "executable_content_store",
                "external_bytes",
                "work_executable_contents",
                ("content_hash", "immutable_location", "byte_length"),
            ),
            ReferenceFamily(
                "immutable_result_store",
                "external_bytes",
                "work_results",
                ("content_hash", "immutable_location", "byte_length"),
            ),
            ReferenceFamily(
                "immutable_result_store",
                "external_bytes",
                "work_worktree_evidence",
                ("content_hash", "immutable_location", "byte_length"),
            ),
            ReferenceFamily(
                "inbox_store_identity",
                "observational",
                "work_inbox_store_context",
                ("store_identity",),
            ),
            ReferenceFamily(
                "inbox_store_identity",
                "observational",
                "work_inbox_store_identity",
                ("store_identity",),
            ),
            ReferenceFamily(
                "knowledge_result_relation",
                "sqlite_relation",
                "work_knowledge_revisions",
                ("source_artifact_id",),
                "work_results",
                ("id",),
            ),
            ReferenceFamily(
                "legacy_flow_path",
                "requires_future_profile",
                "flows",
                ("file_path",),
            ),
            ReferenceFamily(
                "legacy_live_terminal",
                "observational",
                "idempotency_keys",
                ("terminal_id",),
            ),
            ReferenceFamily(
                "legacy_live_terminal",
                "observational",
                "native_children",
                ("parent_terminal_id", "terminal_id"),
            ),
            ReferenceFamily(
                "legacy_live_terminal",
                "observational",
                "terminal_turn_receipts",
                ("terminal_id",),
            ),
            ReferenceFamily(
                "legacy_live_terminal",
                "observational",
                "terminals",
                ("tmux_session", "tmux_window", "working_directory"),
            ),
            ReferenceFamily(
                "legacy_live_terminal",
                "observational",
                "workflow_outcomes",
                ("source_terminal_id",),
            ),
            ReferenceFamily(
                "legacy_live_terminal",
                "observational",
                "workflow_run_event",
                ("terminal_id",),
            ),
            ReferenceFamily(
                "legacy_workflow_relation",
                "sqlite_relation",
                "work_step_contracts",
                ("run_id",),
                "workflow_run",
                ("run_id",),
            ),
            ReferenceFamily(
                "legacy_workflow_path",
                "requires_future_profile",
                "workflow_index",
                ("source_path",),
            ),
            ReferenceFamily(
                "legacy_workflow_payload",
                "sqlite_content",
                "workflow_run",
                ("spec_snapshot", "inputs_json", "manifest_json"),
            ),
            ReferenceFamily(
                "legacy_workflow_payload",
                "sqlite_content",
                "workflow_run_step",
                ("output_json", "result_json"),
            ),
            ReferenceFamily(
                "legacy_workflow_run_relation",
                "sqlite_relation",
                "workflow_run_event",
                ("run_id",),
                "workflow_run",
                ("run_id",),
            ),
            ReferenceFamily(
                "legacy_workflow_run_relation",
                "sqlite_relation",
                "workflow_run_seq",
                ("run_id",),
                "workflow_run",
                ("run_id",),
            ),
            ReferenceFamily(
                "legacy_workflow_run_relation",
                "sqlite_relation",
                "workflow_run_step",
                ("run_id",),
                "workflow_run",
                ("run_id",),
            ),
            ReferenceFamily(
                "memory_content_store",
                "external_text",
                "memory_metadata",
                ("id", "file_path"),
            ),
            ReferenceFamily(
                "memory_metadata_relation",
                "sqlite_content",
                "memory_relationships",
                ("scope", "scope_id", "source_key", "target_key"),
            ),
            ReferenceFamily(
                "snapshot_revision_relation",
                "sqlite_relation",
                "work_snapshot_sources",
                ("record_id", "revision"),
                "work_knowledge_revisions",
                ("record_id", "revision"),
            ),
        )
    )
)


def _user_tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )


def _foreign_keys(
    connection: sqlite3.Connection, tables: tuple[str, ...]
) -> tuple[ForeignKeyReference, ...]:
    """Return the canonical PRAGMA-visible FK multiset for the closed profile.

    SQLite does not expose deferrability (or the declared MATCH clause) through
    this PRAGMA.  Exact Work DDL remains protected by ``WorkRepository._verify``;
    the legacy tables' expected empty FK multiset rejects any new physical FK.
    """
    relationships: list[ForeignKeyReference] = []
    for table in tables:
        grouped: dict[int, list[tuple]] = defaultdict(list)
        for row in connection.execute(f'PRAGMA foreign_key_list("{table}")'):
            grouped[row[0]].append(tuple(row))
        for rows in grouped.values():
            ordered = sorted(rows, key=lambda row: row[1])
            if tuple(row[1] for row in ordered) != tuple(range(len(ordered))):
                raise RecoveryInventoryError(_INCOMPATIBLE)
            target_table = ordered[0][2]
            on_update = ordered[0][5]
            on_delete = ordered[0][6]
            match_observed = ordered[0][7]
            if any(
                row[2] != target_table
                or row[4] is None
                or row[5] != on_update
                or row[6] != on_delete
                or row[7] != match_observed
                for row in ordered
            ):
                raise RecoveryInventoryError(_INCOMPATIBLE)
            relationships.append(
                ForeignKeyReference(
                    source_table=table,
                    source_columns=tuple(row[3] for row in ordered),
                    target_table=target_table,
                    target_columns=tuple(row[4] for row in ordered),
                    on_update=on_update,
                    on_delete=on_delete,
                    match_observed=match_observed,
                )
            )
    return tuple(sorted(relationships))


def _validate_reference_catalog(connection: sqlite3.Connection, tables: tuple[str, ...]) -> None:
    for reference in _REFERENCE_FAMILIES:
        if reference.source_table not in tables:
            raise RecoveryInventoryError(_INCOMPATIBLE)
        source_columns = {
            row[1] for row in connection.execute(f'PRAGMA table_info("{reference.source_table}")')
        }
        if not set(reference.source_columns).issubset(source_columns):
            raise RecoveryInventoryError(_INCOMPATIBLE)
        if reference.resolution != "sqlite_relation":
            if reference.target_table is not None or reference.target_columns:
                raise RecoveryInventoryError(_INCOMPATIBLE)
            continue
        if (
            reference.target_table is None
            or reference.target_table not in tables
            or not reference.target_columns
            or len(reference.source_columns) != len(reference.target_columns)
        ):
            raise RecoveryInventoryError(_INCOMPATIBLE)
        target_columns = {
            row[1] for row in connection.execute(f'PRAGMA table_info("{reference.target_table}")')
        }
        if not set(reference.target_columns).issubset(target_columns):
            raise RecoveryInventoryError(_INCOMPATIBLE)


def _validate_legacy_tables(connection: sqlite3.Connection) -> None:
    for table, expected_columns in _LEGACY_TABLE_COLUMNS.items():
        columns = tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
        if columns != expected_columns:
            raise RecoveryInventoryError(_INCOMPATIBLE)


def _inspect_work_profile(
    connection: sqlite3.Connection,
    *,
    profile_version: int,
    portable: bool,
    expected_source_identity: ManagedInboxStoreIdentity | None = None,
) -> WorkStoreInventory:
    if not isinstance(connection, sqlite3.Connection) or type(profile_version) is not int:
        raise RecoveryInventoryError(_INCOMPATIBLE)
    schema_version = _PROFILE_SCHEMA_VERSIONS.get(profile_version)
    tables_expected = _PROFILE_TABLES.get(profile_version)
    foreign_keys_expected = _PROFILE_FOREIGN_KEYS.get(profile_version)
    if (
        schema_version is None
        or tables_expected is None
        or foreign_keys_expected is None
        or schema_version > SCHEMA_VERSION
        or schema_version not in _EXPECTED_SCHEMAS
    ):
        raise RecoveryInventoryError(_INCOMPATIBLE)
    try:
        if portable:
            stored_identity = _portable_inbox_store_identity(connection)
            if expected_source_identity is not None and stored_identity != expected_source_identity:
                raise RecoveryInventoryError(_INCOMPATIBLE)
            _verify_portable_work_profile(connection, schema_version=schema_version)
        else:
            WorkRepository._verify(connection, version=schema_version)
        tables = _user_tables(connection)
        if tables != tables_expected:
            raise RecoveryInventoryError(_INCOMPATIBLE)
        _validate_legacy_tables(connection)
        _validate_reference_catalog(connection, tables)
        foreign_keys = _foreign_keys(connection, tables)
        if foreign_keys != foreign_keys_expected:
            raise RecoveryInventoryError(_INCOMPATIBLE)
    except (SchemaMismatch, sqlite3.DatabaseError, IndexError, TypeError, ValueError) as error:
        raise RecoveryInventoryError(_INCOMPATIBLE) from error
    return WorkStoreInventory(
        profile_version=profile_version,
        tables=tables,
        foreign_keys=foreign_keys,
        references=_REFERENCE_FAMILIES,
    )


def inspect_work_store(
    connection: sqlite3.Connection, *, profile_version: int = WORK_SQLITE_PROFILE_VERSION
) -> WorkStoreInventory:
    """Inspect one exact current or historical Work profile without writing.

    The repository verifier proves the full migration ledger and each schema
    object. The profile's explicit table/FK catalog also rejects extensions and
    legacy tables that the Work verifier deliberately ignores.
    """
    return _inspect_work_profile(connection, profile_version=profile_version, portable=False)
