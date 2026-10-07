"""Versioned work domain contracts, independent of transport and execution backends."""

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)

Identity = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]
Sha256 = Annotated[
    str, StringConstraints(strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
]


class WorkState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_CHILDREN = "waiting_children"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RECONCILE = "reconcile"
    CANCELLED = "cancelled"


class AttemptState(str, Enum):
    PLANNED = "planned"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    RECONCILE = "reconcile"
    CANCELLED = "cancelled"


class JobState(str, Enum):
    PLANNING = "planning"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    REVOKED = "revoked"


class ProcessState(str, Enum):
    ALIVE = "alive"
    DEAD = "dead"
    UNKNOWN = "unknown"


class TurnState(str, Enum):
    READY = "ready"
    INPUT_SENT = "input_sent"
    ACKNOWLEDGED = "acknowledged"
    PROCESSING = "processing"
    BLOCKED = "blocked"


class VersionedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1

    @field_validator("schema_version", mode="before")
    @classmethod
    def supported_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("only schema version 1 is supported")
        return value


class Job(VersionedModel):
    """A job's provider allowlist may be empty, meaning no provider is authorized."""

    id: Identity
    project_id: Identity
    principal_id: Identity
    allowed_providers: tuple[Identity, ...]
    grant_id: Identity
    budget: dict[str, Any]
    priority: int = Field(default=0, strict=True)
    state: JobState = JobState.PLANNING
    revision: PositiveInt = 1


class WorkItem(VersionedModel):
    id: Identity
    job_id: Identity
    parent_work_item_id: Identity | None = None
    operation_kind: Identity
    idempotency_key: Identity
    request_hash: Sha256
    contract_id: Identity
    snapshot_id: Identity | None
    state: WorkState = WorkState.QUEUED
    revision: PositiveInt = 1
    accepted_result_id: Identity | None = None


class WorkAttempt(VersionedModel):
    id: Identity
    work_item_id: Identity
    attempt_number: PositiveInt
    generation: PositiveInt
    provider: Identity
    terminal_id: Identity | None = None
    state: AttemptState = AttemptState.PLANNED
    revision: PositiveInt = 1
    lease_expires_at: AwareDatetime
    result_id: Identity | None = None
    reconcile_reason: str | None = None
    cleanup_state: Identity = "not_requested"

    @model_validator(mode="before")
    @classmethod
    def serialized_delivery_phase_matches_state(cls, values: Any) -> Any:
        """Accept our serialized derived phase without making it an input authority."""
        if isinstance(values, dict) and "delivery_phase" in values:
            phase = values["delivery_phase"]
            state = values.get("state")
            if state is None or phase != state:
                raise ValueError("delivery_phase must match the authoritative state")
            values = {key: value for key, value in values.items() if key != "delivery_phase"}
        return values

    @computed_field
    def delivery_phase(self) -> AttemptState:
        """Expose delivery phase without introducing a second state authority."""
        return self.state


class WorkView(VersionedModel):
    job_id: Identity
    work_item_id: Identity
    attempt_id: Identity | None = None
    job_state: JobState
    work_state: WorkState
    attempt_state: AttemptState | None = None
    turn_state: TurnState | None = None
    process_state: ProcessState = ProcessState.UNKNOWN
    revision: PositiveInt
    result_ref: Identity | None = None
    cleanup_state: Identity
    required_action: str | None = None


class WorkEvent(VersionedModel):
    event_id: Identity
    job_id: Identity
    work_item_id: Identity | None = None
    attempt_id: Identity | None = None
    sequence: PositiveInt
    event_type: Identity
    actor_id: Identity
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class EventPage(VersionedModel):
    events: list[WorkEvent]
    next_cursor: NonnegativeInt
    high_water: NonnegativeInt
    gaps: list[dict[str, Any]] = Field(default_factory=list)
