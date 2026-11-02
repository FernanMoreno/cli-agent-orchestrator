"""Descriptive durable intent receipts, never TCP or retirement capabilities."""

from dataclasses import dataclass

from cli_agent_orchestrator.models.resource_envelope import ResourceBinding


class SqlOpeningPreparationUncertain(RuntimeError):
    """Preparation may have committed; this does not say that TCP was attempted."""


@dataclass(frozen=True)
class SqlOpeningIntent:
    intent_id: str
    journal_id: str
    ordinal: int
    path: str
    destination_sha256: str
    sql_application_name: str


@dataclass(frozen=True)
class ChildSqlOpeningIntent:
    """A parent's retained child-startup plan, never a child session grant."""

    intent_id: str
    journal_id: str
    ordinal: int
    path: str
    destination_sha256: str
    parent_binding_sha256: str
    parent_process_pid: int
    parent_process_started_at: float
    parent_sql_application_name: str
    subject: ResourceBinding
    subject_sql_application_name: str
    boot_id: str
    start_time_ticks: int
    pid_namespace: tuple[int, int]
