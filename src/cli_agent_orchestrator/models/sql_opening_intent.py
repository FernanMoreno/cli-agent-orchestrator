"""Descriptive durable intent receipts, never TCP or retirement capabilities."""

import re
from dataclasses import dataclass
from uuid import UUID

from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.sql_process_origin import is_sql_process_application_name


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


@dataclass(frozen=True)
class ChildSqlFollowupIntent:
    """An anchored subsequent request; neither funding nor a driver grant."""

    intent_id: str
    journal_id: str
    ordinal: int
    startup_intent_id: str
    sequence: int
    path: str
    destination_scope: str
    destination_sha256: str
    subject_binding_sha256: str
    subject_sql_application_name: str

    def __post_init__(self) -> None:
        for value in (self.intent_id, self.journal_id, self.startup_intent_id):
            if type(value) is not str or str(UUID(value)) != value:
                raise ValueError("follow-up requires canonical request identities")
        if any(type(v) is not int or not 1 <= v < 2**63 for v in (self.ordinal, self.sequence)):
            raise ValueError("follow-up requires positive bounded positions")
        if (
            type(self.path) is not str
            or self.path not in {"native", "orm", "audit"}
            or type(self.destination_scope) is not str
            or not 1 <= len(self.destination_scope) <= 128
            or not is_sql_process_application_name(self.subject_sql_application_name)
            or any(
                type(v) is not str or re.fullmatch("[0-9a-f]{64}", v) is None
                for v in (self.destination_sha256, self.subject_binding_sha256)
            )
        ):
            raise ValueError("follow-up requires an exact subject and destination")
