"""Closed diagnostic DTOs, separate from the historical WorkView contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class DiagnosticModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapacityObservation(DiagnosticModel):
    id: str
    state: Literal["queued", "held", "released", "withdrawn"]
    units: int
    revision: int
    stop_evidence_ref: str | None


class ReservationObservation(DiagnosticModel):
    id: str
    state: Literal["active", "released"]
    revision: int
    stop_evidence_ref: str | None
    path_count: int


class RetryExpectations(DiagnosticModel):
    run_generation: int
    workflow_step_attempt: int
    work_attempt_id: str
    work_generation: int


class OperationalAction(DiagnosticModel):
    kind: Literal["inspect_work", "resume_workflow", "retry_managed_step"]
    method: Literal["GET", "POST"]
    path: str
    requires_current_validation: bool
    required_scopes: list[Literal["cao:read", "cao:write", "cao:admin"]]
    expectations: RetryExpectations | None = None


class ControllerObservation(DiagnosticModel):
    run_id: str
    run_generation: str
    coordinator_id: str | None
    state: str
    driver_state: str | None
    driver_lease_expired: bool
    plan_id: str | None
    source_hash: str | None


class WorkOperations(DiagnosticModel):
    schema_version: Literal[1]
    job_id: str
    work_item_id: str
    revision: int
    attempt_id: str | None
    generation: int | None
    work_state: str
    attempt_state: str | None
    execution_state: Literal["normal", "blocked_restore"]
    execution_allowed: bool
    lease_expired: bool
    process_state: Literal["unknown"]
    cleanup_state: str
    capacity: CapacityObservation | None
    reservations: ReservationObservation | None
    release_allowed: Literal[False]
    reactivation_allowed: Literal[False]
    required_action: str
    controller: ControllerObservation | None
    actions: list[OperationalAction]


class OperationObservation(DiagnosticModel):
    op_id: str
    incarnation_id: str
    state: Literal["prepared", "dispatching", "reconcile"]
    inspect_path: str


class RuntimeObservation(DiagnosticModel):
    runtime_id: str
    incarnation_id: str
    connection_epoch: int
    connection_state: Literal["connected", "disconnected", "identity_mismatch"]
    unresolved_operation_count: int
    unresolved_operations: list[OperationObservation]
    operations_truncated: bool
    required_action: str
    automatic_replay_allowed: Literal[False]
    protected_work_supported: Literal[False]


class RuntimeOperations(DiagnosticModel):
    schema_version: Literal[1]
    nodes: list[RuntimeObservation]
    next_cursor: str | None
