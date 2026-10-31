"""Pure diagnostic projection: cached telemetry is separate from admission authority."""

from dataclasses import asdict

from cli_agent_orchestrator.models.admission_control import (
    AdmissionDiagnostics,
    AdmissionOperationDiagnostics,
    AdmissionPolicyDiagnostics,
    LocalResourceProbeDiagnostics,
    MonitorDiagnostics,
    PostgresProbeDiagnostics,
    ResourceQuantities,
    ResourceSampleDiagnostics,
    SamplingPolicyDiagnostics,
)
from cli_agent_orchestrator.services.admission_monitor import AdmissionMonitorDiagnostic
from cli_agent_orchestrator.services.admission_policy import CapacityObservation, ResourceAmounts


def _age(at: float | None, now: float) -> float | None:
    return now - at if at is not None and now >= at else None


def _amounts(value: ResourceAmounts | None) -> ResourceQuantities | None:
    return ResourceQuantities(**asdict(value)) if value is not None else None


def _sample(
    observation: CapacityObservation, *, now: float, max_age: float
) -> ResourceSampleDiagnostics:
    age = _age(observation.observed_at, now)
    return ResourceSampleDiagnostics(
        coverage=observation.coverage,
        age_s=age,
        within_max_sample_age=age is not None and age <= max_age,
        memory_available_bytes=observation.memory_available_bytes,
        memory_limit_bytes=observation.memory_limit_bytes,
        cpu_available_millicores=observation.cpu_available_millicores,
        cpu_limit_millicores=observation.cpu_limit_millicores,
        storage_available_slots=observation.storage_available_slots,
    )


def diagnostics_response(value: AdmissionMonitorDiagnostic, *, now: float) -> AdmissionDiagnostics:
    ledger = value.ledger
    status = ledger.snapshot
    current = status.accepting and status.reason in ("capacity_available", "resource_exhausted")
    capacity = ledger.decision.resource_capacity if current else None
    postgres = value.postgres
    resources = value.resources
    cycle = value.last_cycle
    configuration = value.configuration
    postgres_age = _age(postgres.sample.observation.observed_at, now) if postgres else None
    return AdmissionDiagnostics(
        generation=status.generation,
        accepting=status.accepting,
        reason=status.reason,
        policy=AdmissionPolicyDiagnostics(**asdict(ledger.policy)),
        sampling_policy=(
            SamplingPolicyDiagnostics(
                generation=configuration.generation,
                current_generation=configuration.generation == status.generation,
                basis_current=(
                    current
                    and configuration.generation == status.generation
                    and configuration.basis is ledger.observation
                ),
                sample_interval_s=configuration.configuration.sample_interval_s,
                healthy_samples_before_growth=configuration.configuration.healthy_samples_before_growth,
                postgres_reserve_percent=configuration.configuration.postgres.reserve_percent,
                postgres_statement_timeout_ms=configuration.configuration.postgres.statement_timeout_ms,
            )
            if configuration is not None
            else None
        ),
        applied_budget=_amounts(ledger.budget) if current else None,
        sample=(
            _sample(ledger.observation, now=now, max_age=ledger.policy.max_sample_age_s)
            if ledger.observation is not None
            else None
        ),
        capacity=_amounts(capacity),
        remaining=(
            _amounts(capacity.excess_over(status.committed)) if capacity is not None else None
        ),
        committed=ResourceQuantities(**asdict(status.committed)),
        debt=ResourceQuantities(**asdict(status.resource_debt)),
        recovery_ceiling=_amounts(ledger.ceiling),
        operations=AdmissionOperationDiagnostics(
            inflight_operations=status.inflight,
            reserved_operations=status.reserved,
            started_operations=status.started,
            inflight_deliveries=status.delivery_inflight,
            started_deliveries=status.delivery_started,
            inflight_storage_only=status.storage_only_inflight,
            started_storage_only=status.storage_only_started,
            storage_connection_loans=status.storage_connection_loans,
            storage_completion_credits=status.storage_completion_credits,
            storage_retirement_pending=status.storage_retirement_pending,
        ),
        monitor=MonitorDiagnostics(
            stopping=value.stopping,
            sampling=value.sampling,
            authority_changing=value.authority_changing,
            authority_uncertain=value.authority_uncertain,
            last_cycle_reason=cycle.result.reason if cycle is not None else None,
            last_cycle_age_s=_age(cycle.observed_at, now) if cycle is not None else None,
            generation_at_last_cycle_completion=(
                cycle.generation_at_completion if cycle is not None else None
            ),
        ),
        postgres=(
            PostgresProbeDiagnostics(
                generation=postgres.generation,
                current_generation=postgres.generation == status.generation,
                age_s=postgres_age,
                within_max_sample_age=(
                    postgres_age is not None and postgres_age <= ledger.policy.max_sample_age_s
                ),
                reason=postgres.sample.reason,
                available_slots=postgres.sample.observation.available_slots,
                maximum_connections=postgres.sample.maximum_connections,
                ordinary_connection_budget=postgres.sample.ordinary_connection_budget,
                policy_reserved_slots=postgres.sample.policy_reserved_slots,
                observed_backends=postgres.sample.observed_backends,
                reserve_percent=postgres.policy.reserve_percent,
                statement_timeout_ms=postgres.policy.statement_timeout_ms,
            )
            if postgres is not None
            else None
        ),
        resource_probe=(
            LocalResourceProbeDiagnostics(
                **_sample(
                    resources.sample.observation, now=now, max_age=ledger.policy.max_sample_age_s
                ).model_dump(),
                generation=resources.generation,
                current_generation=resources.generation == status.generation,
                issues=resources.sample.issues,
            )
            if resources is not None
            else None
        ),
    )
