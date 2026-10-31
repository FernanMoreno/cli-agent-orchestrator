"""Versioned control DTOs shared by HTTP, SDK and administrative clients."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from cli_agent_orchestrator.models.resource_envelope import EnvelopeClaims


class StrictControlModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ResourceClaims(StrictControlModel):
    memory_budget_bytes: StrictInt = Field(ge=1, le=2**63 - 1)
    cpu_budget_millicores: StrictInt = Field(ge=1, le=2**63 - 1)
    storage_budget_slots: StrictInt = Field(ge=1, le=2**63 - 1)
    complete_hierarchy: StrictBool
    root_is_host: StrictBool

    @model_validator(mode="after")
    def consistent_hierarchy(self) -> "ResourceClaims":
        self.to_claims()
        return self

    def to_claims(self) -> EnvelopeClaims:
        return EnvelopeClaims(**self.model_dump())


class ResourceRevisionRequest(StrictControlModel):
    expected_revision: StrictInt = Field(ge=0, le=2**63 - 2)


class ResourceAttestationRequest(ResourceRevisionRequest):
    claims: ResourceClaims


class ResourceUpdate(StrictControlModel):
    revision: StrictInt = Field(ge=1, le=2**63 - 1)
    active: StrictBool


class RegisteredResourceEnvelope(ResourceUpdate):
    """The latest registered record, including stale/revoked claims; not live capacity."""

    claims: ResourceClaims


AdmissionDiagnosticReason = Literal[
    "capacity_available",
    "resource_exhausted",
    "unknown_capacity",
    "stale_observation",
    "future_observation",
    "receiver_busy",
    "stopping",
]
MonitorDiagnosticReason = Literal[
    "capacity_available",
    "resource_exhausted",
    "unknown_capacity",
    "stale_observation",
    "future_observation",
    "receiver_busy",
    "stopping",
    "recovering",
    "policy_unavailable",
    "authority_unavailable",
    "generation_changed",
    "probe_busy",
    "monitor_failed",
    "duplicate_observation",
    "authority_changing",
    "authority_uncertain",
]
PressureDiagnosticReason = Literal[
    "pressure_observed",
    "postgres_unavailable",
    "invalid_pressure_data",
    "identity_mismatch",
    "insufficient_visibility",
    "probe_busy",
    "probe_cleanup_unconfirmed",
]
ResourceDiagnosticIssue = Literal[
    "unsupported_cgroup",
    "invalid_cgroup_path",
    "invalid_resource_data",
    "resource_unavailable",
    "cpu_warming",
    "hidden_ancestors",
    "storage_unknown",
    "future_storage",
    "unverified_hierarchy",
]


class ResourceQuantities(StrictControlModel):
    memory_bytes: StrictInt = Field(ge=0)
    cpu_millicores: StrictInt = Field(ge=0)
    storage_slots: StrictInt = Field(ge=0)


class AdmissionPolicyDiagnostics(StrictControlModel):
    memory_per_delivery_bytes: StrictInt = Field(ge=1)
    cpu_per_delivery_millicores: StrictInt = Field(ge=1)
    memory_reserve_percent: StrictInt = Field(ge=0, lt=100)
    cpu_reserve_percent: StrictInt = Field(ge=0, lt=100)
    max_sample_age_s: float = Field(gt=0, allow_inf_nan=False)


class SamplingPolicyDiagnostics(StrictControlModel):
    """Installed sampler parameters; generation equality alone is not a basis witness."""

    generation: StrictInt = Field(ge=0)
    current_generation: StrictBool
    basis_current: StrictBool
    sample_interval_s: float = Field(gt=0, allow_inf_nan=False)
    healthy_samples_before_growth: StrictInt = Field(ge=1)
    postgres_reserve_percent: StrictInt = Field(ge=0, lt=100)
    postgres_statement_timeout_ms: StrictInt = Field(ge=1)


class ResourceSampleDiagnostics(StrictControlModel):
    coverage: Literal["known", "partial", "unknown"]
    age_s: float | None = Field(ge=0, allow_inf_nan=False)
    within_max_sample_age: StrictBool
    memory_available_bytes: StrictInt | None = Field(ge=0)
    memory_limit_bytes: StrictInt | None = Field(ge=0)
    cpu_available_millicores: StrictInt | None = Field(ge=0)
    cpu_limit_millicores: StrictInt | None = Field(ge=0)
    storage_available_slots: StrictInt | None = Field(ge=0)


class LocalResourceProbeDiagnostics(ResourceSampleDiagnostics):
    generation: StrictInt = Field(ge=0)
    current_generation: StrictBool
    issues: tuple[ResourceDiagnosticIssue, ...]


class PostgresProbeDiagnostics(StrictControlModel):
    generation: StrictInt = Field(ge=0)
    current_generation: StrictBool
    age_s: float | None = Field(ge=0, allow_inf_nan=False)
    within_max_sample_age: StrictBool
    reason: PressureDiagnosticReason
    available_slots: StrictInt | None = Field(ge=0)
    maximum_connections: StrictInt | None = Field(ge=1)
    ordinary_connection_budget: StrictInt | None = Field(ge=0)
    policy_reserved_slots: StrictInt | None = Field(ge=0)
    observed_backends: StrictInt | None = Field(ge=0)
    reserve_percent: StrictInt = Field(ge=0, lt=100)
    statement_timeout_ms: StrictInt = Field(ge=1)


class AdmissionOperationDiagnostics(StrictControlModel):
    inflight_operations: StrictInt = Field(ge=0)
    reserved_operations: StrictInt = Field(ge=0)
    started_operations: StrictInt = Field(ge=0)
    inflight_deliveries: StrictInt = Field(ge=0)
    started_deliveries: StrictInt = Field(ge=0)
    inflight_storage_only: StrictInt = Field(ge=0)
    started_storage_only: StrictInt = Field(ge=0)
    storage_connection_loans: StrictInt = Field(ge=0)
    storage_completion_credits: StrictInt = Field(ge=0)
    storage_retirement_pending: StrictInt = Field(ge=0)
    control_storage_slots: StrictInt = Field(default=0, ge=0, le=1)
    control_inflight: StrictInt = Field(default=0, ge=0, le=1)
    control_connection_loans: StrictInt = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def consistent_counts(self) -> "AdmissionOperationDiagnostics":
        if (
            self.inflight_operations != self.inflight_deliveries + self.inflight_storage_only
            or self.started_operations != self.started_deliveries + self.started_storage_only
            or self.reserved_operations != self.inflight_operations - self.started_operations
            or self.started_deliveries > self.inflight_deliveries
            or self.started_storage_only > self.inflight_storage_only
            or self.control_inflight > self.control_storage_slots
            or self.control_inflight > self.started_storage_only
            or self.control_connection_loans > self.control_inflight
            or self.control_connection_loans > self.storage_connection_loans
        ):
            raise ValueError("inconsistent admission operation counters")
        return self


class MonitorDiagnostics(StrictControlModel):
    stopping: StrictBool
    sampling: StrictBool
    authority_changing: StrictBool
    authority_uncertain: StrictBool
    last_cycle_reason: MonitorDiagnosticReason | None
    last_cycle_age_s: float | None = Field(ge=0, allow_inf_nan=False)
    generation_at_last_cycle_completion: StrictInt | None = Field(ge=0)


class AdmissionDiagnostics(StrictControlModel):
    version: Literal[1] = 1
    scope: Literal["process_local"] = "process_local"
    distributed_observed: Literal[False] = False
    generation: StrictInt = Field(ge=0)
    accepting: StrictBool
    reason: AdmissionDiagnosticReason
    policy: AdmissionPolicyDiagnostics
    sampling_policy: SamplingPolicyDiagnostics | None
    applied_budget: ResourceQuantities | None
    sample: ResourceSampleDiagnostics | None
    capacity: ResourceQuantities | None
    remaining: ResourceQuantities | None
    committed: ResourceQuantities
    debt: ResourceQuantities
    recovery_ceiling: ResourceQuantities | None
    operations: AdmissionOperationDiagnostics
    monitor: MonitorDiagnostics
    postgres: PostgresProbeDiagnostics | None
    resource_probe: LocalResourceProbeDiagnostics | None
