"""Resource quantities, not a worker constant, determine new admission units."""

import importlib
from dataclasses import FrozenInstanceError, replace

import pytest

pytestmark = pytest.mark.offline_database

MIB = 1024 * 1024


def module():
    return importlib.import_module("cli_agent_orchestrator.services.admission_policy")


def observation(**changes):
    values = {
        "observed_at": 10.0,
        "memory_available_bytes": 1024 * MIB,
        "memory_limit_bytes": 1024 * MIB,
        "cpu_available_millicores": 2000,
        "cpu_limit_millicores": 2000,
        "storage_available_slots": 100,
    }
    values.update(changes)
    return module().CapacityObservation(**values)


def test_capacity_grows_with_resources_without_a_fixed_worker_ceiling():
    api = module()
    policy = api.AdmissionPolicy()
    small = api.evaluate_capacity(policy, observation(), now=10.0)
    large = api.evaluate_capacity(
        policy,
        observation(
            memory_available_bytes=4096 * MIB,
            memory_limit_bytes=4096 * MIB,
            cpu_available_millicores=16000,
            cpu_limit_millicores=16000,
        ),
        now=10.0,
    )
    assert small.available_slots == 6
    assert large.available_slots == 51
    assert large.available_slots > 32 > small.available_slots > 4
    assert large.limiting_resources == ("memory", "cpu")


def test_each_resource_independently_limits_admission():
    api = module()
    policy = api.AdmissionPolicy()
    memory = api.evaluate_capacity(policy, observation(memory_available_bytes=256 * MIB), now=10.0)
    cpu = api.evaluate_capacity(policy, observation(cpu_available_millicores=650), now=10.0)
    storage = api.evaluate_capacity(policy, observation(storage_available_slots=2), now=10.0)
    assert (memory.available_slots, memory.limiting_resources) == (0, ("memory",))
    assert (cpu.available_slots, cpu.limiting_resources) == (1, ("cpu",))
    assert (storage.available_slots, storage.limiting_resources) == (2, ("storage",))


def test_integer_rounding_reserves_headroom_conservatively():
    api = module()
    policy = api.AdmissionPolicy(
        memory_per_delivery_bytes=1,
        cpu_per_delivery_millicores=1,
        memory_reserve_percent=1,
        cpu_reserve_percent=0,
    )
    result = api.evaluate_capacity(
        policy,
        observation(
            memory_available_bytes=101,
            memory_limit_bytes=101,
            cpu_available_millicores=1000,
            cpu_limit_millicores=1000,
        ),
        now=10.0,
    )
    assert result.available_slots == 99  # ceil(1% of101)=2, never round down the reserve


def test_pressure_closes_new_admission_without_claiming_active_work_finished():
    api = module()
    decision = api.evaluate_capacity(
        api.AdmissionPolicy(), observation(storage_available_slots=0), now=10.0
    )
    assert decision.available_slots == 0
    assert decision.reason == "resource_exhausted"
    assert not hasattr(decision, "cancel_active")


@pytest.mark.parametrize("coverage", ["unknown", "partial"])
def test_incomplete_coverage_does_not_turn_missing_values_into_capacity(coverage):
    api = module()
    sample = observation(
        memory_available_bytes=None,
        memory_limit_bytes=None,
        cpu_available_millicores=None,
        cpu_limit_millicores=None,
        storage_available_slots=None,
        coverage=coverage,
    )
    decision = api.evaluate_capacity(api.AdmissionPolicy(), sample, now=10.0)
    assert decision.available_slots == 0
    assert decision.reason == "unknown_capacity"


@pytest.mark.parametrize(
    ("now", "reason"), [(13.001, "stale_observation"), (9.999, "future_observation")]
)
def test_stale_and_future_observations_are_not_admission_authority(now, reason):
    api = module()
    decision = api.evaluate_capacity(api.AdmissionPolicy(), observation(), now=now)
    assert decision.available_slots == 0
    assert decision.reason == reason


def test_freshness_boundary_and_policy_changes_use_explicit_time():
    api = module()
    policy = api.AdmissionPolicy()
    assert api.evaluate_capacity(policy, observation(), now=13.0).available_slots == 6
    tighter = replace(policy, cpu_per_delivery_millicores=1000)
    assert api.evaluate_capacity(tighter, observation(), now=10.0).available_slots == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"memory_per_delivery_bytes": 0},
        {"memory_per_delivery_bytes": True},
        {"memory_per_delivery_bytes": 1.5},
        {"cpu_per_delivery_millicores": -1},
        {"cpu_per_delivery_millicores": False},
        {"memory_reserve_percent": 100},
        {"cpu_reserve_percent": -1},
        {"cpu_reserve_percent": 0.5},
        {"max_sample_age_s": float("nan")},
        {"max_sample_age_s": float("inf")},
        {"max_sample_age_s": 0},
        {"max_sample_age_s": True},
    ],
)
def test_invalid_policy_never_silently_substitutes_capacity(changes):
    with pytest.raises(ValueError):
        module().AdmissionPolicy(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"memory_available_bytes": -1},
        {"memory_available_bytes": True},
        {"memory_limit_bytes": 0},
        {"memory_limit_bytes": 100},
        {"cpu_available_millicores": 2001},
        {"cpu_available_millicores": 1.5},
        {"cpu_limit_millicores": 0},
        {"storage_available_slots": -1},
        {"storage_available_slots": False},
        {"storage_available_slots": None},
        {"observed_at": float("nan")},
        {"observed_at": float("inf")},
        {"observed_at": -1},
        {"observed_at": True},
        {"coverage": "pretend-known"},
    ],
)
def test_invalid_observation_cannot_claim_usable_resources(changes):
    with pytest.raises(ValueError):
        observation(**changes)


@pytest.mark.parametrize("now", [float("nan"), float("inf"), -1, True])
def test_invalid_evaluation_clock_is_rejected(now):
    api = module()
    with pytest.raises(ValueError):
        api.evaluate_capacity(api.AdmissionPolicy(), observation(), now=now)


def test_policy_observation_and_decision_are_immutable_and_content_free():
    api = module()
    policy = api.AdmissionPolicy()
    sample = observation()
    decision = api.evaluate_capacity(policy, sample, now=10.0)
    for value, field, replacement in (
        (policy, "memory_per_delivery_bytes", 1),
        (sample, "storage_available_slots", 999),
        (decision, "available_slots", 999),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, replacement)
    assert dict(decision.resource_limits) == {"memory": 12, "cpu": 6, "storage": 100}
    assert decision.reason == "capacity_available"
