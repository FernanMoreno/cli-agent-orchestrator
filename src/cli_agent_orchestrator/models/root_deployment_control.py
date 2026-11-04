"""Strict operator commands; no request field grants deployment capabilities."""

from typing import Literal

from pydantic import Field, StrictInt, StrictStr, field_validator

from cli_agent_orchestrator.models.admission_control import (
    MonitorDiagnosticReason,
    StrictControlModel,
)
from cli_agent_orchestrator.models.admission_domains import canonical_id
from cli_agent_orchestrator.models.sql_root_subject import RootSqlSubject

RootDeploymentPhase = Literal[
    "idle",
    "launching",
    "awaiting_authority",
    "activating",
    "ready",
    "stopping",
    "closed",
    "unavailable",
]


class RootDeploymentCommand(StrictControlModel):
    version: StrictInt = Field(ge=1, le=1)


class RootDeploymentActivation(RootDeploymentCommand):
    journal_id: StrictStr = Field(min_length=36, max_length=36)
    intent_id: StrictStr = Field(min_length=36, max_length=36)
    session_id: StrictStr = Field(min_length=36, max_length=36)

    @field_validator("journal_id", "intent_id", "session_id")
    @classmethod
    def canonical_identifier(cls, value: str) -> str:
        if canonical_id(value) != value:
            raise ValueError("root deployment requires canonical identifiers")
        return value


class RootDeploymentStatus(StrictControlModel):
    """Retained state of one configured control Root, never network capacity."""

    version: Literal[1] = 1
    phase: RootDeploymentPhase
    subject: RootSqlSubject | None = None
    journal_id: StrictStr | None = None
    intent_id: StrictStr | None = None
    session_id: StrictStr | None = None
    reason: MonitorDiagnosticReason | None = None
