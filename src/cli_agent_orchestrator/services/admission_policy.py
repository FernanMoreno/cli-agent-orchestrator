"""Pure resource arithmetic for admission; observations never grant authority."""

import math
from dataclasses import dataclass
from typing import Literal

from cli_agent_orchestrator.models.resource_compute import ComputeObservation as ComputeObservation
from cli_agent_orchestrator.models.resource_compute import ResourceHeadroom as ResourceHeadroom

Coverage = Literal["known", "partial", "unknown"]
CapacityReason = Literal[
    "capacity_available",
    "resource_exhausted",
    "unknown_capacity",
    "stale_observation",
    "future_observation",
]


def _integer(name: str, value: object, *, minimum: int = 0) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def validate_clock(value: float, *, positive: bool = False) -> None:
    """Validate an explicit monotonic duration/instant, including bool rejection."""
    try:
        valid = (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
            and (value > 0 if positive else value >= 0)
        )
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("clock value must be finite and within its nonnegative range")


@dataclass(frozen=True)
class ResourceAmounts:
    """Immutable quantities, independent of the current per-delivery estimate."""

    memory_bytes: int = 0
    cpu_millicores: int = 0
    storage_slots: int = 0

    def __post_init__(self) -> None:
        for name in ("memory_bytes", "cpu_millicores", "storage_slots"):
            _integer(name, getattr(self, name))

    def __add__(self, other: "ResourceAmounts") -> "ResourceAmounts":
        return ResourceAmounts(
            self.memory_bytes + other.memory_bytes,
            self.cpu_millicores + other.cpu_millicores,
            self.storage_slots + other.storage_slots,
        )

    def __sub__(self, other: "ResourceAmounts") -> "ResourceAmounts":
        # A negative ledger is an invariant failure, never silently clamped.
        return ResourceAmounts(
            self.memory_bytes - other.memory_bytes,
            self.cpu_millicores - other.cpu_millicores,
            self.storage_slots - other.storage_slots,
        )

    def excess_over(self, capacity: "ResourceAmounts") -> "ResourceAmounts":
        return ResourceAmounts(
            max(0, self.memory_bytes - capacity.memory_bytes),
            max(0, self.cpu_millicores - capacity.cpu_millicores),
            max(0, self.storage_slots - capacity.storage_slots),
        )


@dataclass(frozen=True)
class AdmissionPolicy:
    """Configurable consumption estimates and reserves, not a worker ceiling."""

    memory_per_delivery_bytes: int = 64 * 1024 * 1024
    cpu_per_delivery_millicores: int = 250
    memory_reserve_percent: int = 20
    cpu_reserve_percent: int = 20
    max_sample_age_s: float = 3.0

    def __post_init__(self) -> None:
        _integer("memory_per_delivery_bytes", self.memory_per_delivery_bytes, minimum=1)
        _integer("cpu_per_delivery_millicores", self.cpu_per_delivery_millicores, minimum=1)
        for name in ("memory_reserve_percent", "cpu_reserve_percent"):
            value = getattr(self, name)
            _integer(name, value)
            if value >= 100:
                raise ValueError(f"{name} must be below 100")
        validate_clock(self.max_sample_age_s, positive=True)


@dataclass(frozen=True)
class CapacityObservation:
    """Available quantities at one process-local monotonic instant in seconds.

    CPU/memory are raw headroom before policy reserves. Storage units already
    exclude protected headroom. The evaluation clock must be the same local
    monotonic clock, never another node's clock. Partial observations
    may retain known fields for diagnostics, but cannot enable new admission.
    """

    observed_at: float
    memory_available_bytes: int | None
    memory_limit_bytes: int | None
    cpu_available_millicores: int | None
    cpu_limit_millicores: int | None
    storage_available_slots: int | None
    coverage: Coverage = "known"
    memory_domains: tuple[ResourceHeadroom, ...] = ()
    cpu_domains: tuple[ResourceHeadroom, ...] = ()

    def __post_init__(self) -> None:
        validate_clock(self.observed_at)
        if self.coverage not in ("known", "partial", "unknown"):
            raise ValueError("coverage must be known, partial or unknown")
        for name in (
            "memory_available_bytes",
            "memory_limit_bytes",
            "cpu_available_millicores",
            "cpu_limit_millicores",
            "storage_available_slots",
        ):
            value = getattr(self, name)
            if value is None:
                if self.coverage == "known":
                    raise ValueError("known coverage requires every resource quantity")
                continue
            _integer(name, value, minimum=1 if "limit" in name else 0)
        for available, limit in (
            (self.memory_available_bytes, self.memory_limit_bytes),
            (self.cpu_available_millicores, self.cpu_limit_millicores),
        ):
            if available is not None and limit is not None and available > limit:
                raise ValueError("available resource cannot exceed its effective limit")
        for domains, available, limit in (
            (self.memory_domains, self.memory_available_bytes, self.memory_limit_bytes),
            (self.cpu_domains, self.cpu_available_millicores, self.cpu_limit_millicores),
        ):
            if not isinstance(domains, tuple) or any(
                not isinstance(domain, ResourceHeadroom) for domain in domains
            ):
                raise ValueError("resource domains must be immutable validated headroom pairs")
            if domains and (
                (available is not None and available != min(d.available_units for d in domains))
                or (limit is not None and limit != min(d.limit_units for d in domains))
            ):
                raise ValueError("aggregate quantities must match their domain observations")


def compute_capacity(
    policy: AdmissionPolicy,
    observation: ComputeObservation,
    *,
    now: float,
    budget_memory_bytes: int,
    budget_cpu_millicores: int,
) -> ResourceAmounts | None:
    """Return only policy-adjusted RAM/CPU headroom, or None if unusable.

    This deliberately has no storage field and cannot be converted into a
    complete CapacityObservation. A root startup charge therefore never turns
    its unknown data/storage admission state into a known capacity claim.
    """
    validate_clock(now)
    if (
        type(policy) is not AdmissionPolicy
        or type(observation) is not ComputeObservation
        or isinstance(budget_memory_bytes, bool)
        or type(budget_memory_bytes) is not int
        or budget_memory_bytes <= 0
        or isinstance(budget_cpu_millicores, bool)
        or type(budget_cpu_millicores) is not int
        or budget_cpu_millicores <= 0
        or now < observation.observed_at
        or now - observation.observed_at > policy.max_sample_age_s
    ):
        return None
    memory = min(
        _available_units(domain.available_units, domain.limit_units, policy.memory_reserve_percent)
        for domain in observation.memory_domains
    )
    cpu = min(
        _available_units(domain.available_units, domain.limit_units, policy.cpu_reserve_percent)
        for domain in observation.cpu_domains
    )
    return ResourceAmounts(
        min(memory, budget_memory_bytes),
        min(cpu, budget_cpu_millicores),
        0,
    )


@dataclass(frozen=True)
class AdmissionDecision:
    """New units under this observation; a reservation owner must account reuse."""

    available_slots: int
    reason: CapacityReason
    resource_limits: tuple[tuple[str, int], ...] = ()
    limiting_resources: tuple[str, ...] = ()
    resource_capacity: ResourceAmounts = ResourceAmounts()
    resource_debt: ResourceAmounts = ResourceAmounts()


def _available_units(available: int, limit: int, reserve_percent: int) -> int:
    # Ceil the safety reserve using integers: float rounding cannot mint a unit.
    reserve = (limit * reserve_percent + 99) // 100
    return max(0, available - reserve)


def evaluate_capacity(
    policy: AdmissionPolicy,
    observation: CapacityObservation,
    *,
    now: float,
    committed: ResourceAmounts = ResourceAmounts(),
    budget: ResourceAmounts | None = None,
) -> AdmissionDecision:
    """Derive capacity without I/O, cancellation, persistence or identity claims."""
    validate_clock(now)
    if not isinstance(committed, ResourceAmounts) or (
        budget is not None and not isinstance(budget, ResourceAmounts)
    ):
        raise ValueError("resource quantities must be validated amounts")
    age = now - observation.observed_at
    if age < 0:
        return AdmissionDecision(0, "future_observation", resource_debt=committed)
    if age > policy.max_sample_age_s:
        return AdmissionDecision(0, "stale_observation", resource_debt=committed)
    if observation.coverage != "known":
        return AdmissionDecision(0, "unknown_capacity", resource_debt=committed)
    # Known observations are validated at construction; narrow optional fields.
    assert observation.memory_available_bytes is not None
    assert observation.memory_limit_bytes is not None
    assert observation.cpu_available_millicores is not None
    assert observation.cpu_limit_millicores is not None
    assert observation.storage_available_slots is not None
    memory_domains = observation.memory_domains or (
        ResourceHeadroom(observation.memory_available_bytes, observation.memory_limit_bytes),
    )
    cpu_domains = observation.cpu_domains or (
        ResourceHeadroom(observation.cpu_available_millicores, observation.cpu_limit_millicores),
    )
    capacity = ResourceAmounts(
        min(
            _available_units(d.available_units, d.limit_units, policy.memory_reserve_percent)
            for d in memory_domains
        ),
        min(
            _available_units(d.available_units, d.limit_units, policy.cpu_reserve_percent)
            for d in cpu_domains
        ),
        observation.storage_available_slots,
    )
    if budget is not None:
        capacity = ResourceAmounts(
            min(capacity.memory_bytes, budget.memory_bytes),
            min(capacity.cpu_millicores, budget.cpu_millicores),
            min(capacity.storage_slots, budget.storage_slots),
        )
    remaining = capacity.excess_over(committed)
    limits = (
        ("memory", remaining.memory_bytes // policy.memory_per_delivery_bytes),
        ("cpu", remaining.cpu_millicores // policy.cpu_per_delivery_millicores),
        ("storage", remaining.storage_slots),
    )
    available = min(value for _, value in limits)
    return AdmissionDecision(
        available,
        "capacity_available" if available else "resource_exhausted",
        limits,
        tuple(name for name, value in limits if value == available),
        capacity,
        committed.excess_over(capacity),
    )
