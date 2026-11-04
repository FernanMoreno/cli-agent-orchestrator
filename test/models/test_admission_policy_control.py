"""Strict, shared admission-policy control DTO contract."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from cli_agent_orchestrator.models.admission_policy_control import (
    AdmissionPolicyHistory,
    AdmissionPolicyRecord,
    AdmissionPolicyRevision,
    AdmissionPolicyState,
    AdmissionPolicyUpdate,
    AdmissionPolicyValues,
)

_DEFAULTS = {
    "memory_per_delivery_bytes": 67_108_864,
    "cpu_per_delivery_millicores": 250,
    "memory_reserve_percent": 20,
    "cpu_reserve_percent": 20,
    "max_sample_age_s": 3.0,
    "postgres_reserve_percent": 20,
    "postgres_statement_timeout_ms": 500,
    "sample_interval_s": 1.0,
    "healthy_samples_before_growth": 3,
}


def _record(**overrides: object) -> AdmissionPolicyRecord:
    values: dict[str, object] = {
        "profile_id": str(uuid4()),
        "revision": 1,
        "active": True,
        "policy": AdmissionPolicyValues(),
        "actor_principal_id": "principal-" + "a" * 64,
        "recorded_at": datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return AdmissionPolicyRecord.model_validate(values)


def test_values_defaults_match_the_effective_operator_policy_defaults() -> None:
    policy = AdmissionPolicyValues()

    assert policy.model_dump() == _DEFAULTS
    assert type(policy.max_sample_age_s) is float
    assert type(policy.sample_interval_s) is float


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("memory_per_delivery_bytes", True),
        ("cpu_per_delivery_millicores", "250"),
        ("memory_reserve_percent", 100),
        ("cpu_reserve_percent", -1),
        ("max_sample_age_s", True),
        ("max_sample_age_s", "3.0"),
        ("max_sample_age_s", float("inf")),
        ("max_sample_age_s", 0),
        ("postgres_reserve_percent", 100),
        ("postgres_statement_timeout_ms", False),
        ("sample_interval_s", float("nan")),
        ("healthy_samples_before_growth", 0),
    ],
)
def test_values_reject_invalid_units_and_non_finite_numbers(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AdmissionPolicyValues.model_validate({field: value})


def test_values_reject_extra_fields_and_sampling_interval_not_shorter_than_freshness() -> None:
    with pytest.raises(ValidationError):
        AdmissionPolicyValues.model_validate({**_DEFAULTS, "unexpected": 1})
    with pytest.raises(ValidationError):
        AdmissionPolicyValues(max_sample_age_s=1.0, sample_interval_s=1.0)


def test_values_are_frozen_and_integer_seconds_normalize_to_float() -> None:
    policy = AdmissionPolicyValues(max_sample_age_s=4, sample_interval_s=1)

    assert policy.max_sample_age_s == 4.0
    assert type(policy.max_sample_age_s) is float
    with pytest.raises(ValidationError):
        policy.memory_per_delivery_bytes = 1  # type: ignore[misc]


def test_record_requires_canonical_profile_and_aware_timestamp() -> None:
    record = _record()

    assert record.revision == 1
    assert record.active is True
    assert record.recorded_at.utcoffset() is not None
    with pytest.raises(ValidationError):
        _record(profile_id="not-a-uuid")
    with pytest.raises(ValidationError):
        _record(recorded_at=datetime(2026, 11, 4, 12, 0))
    with pytest.raises(ValidationError):
        _record(revision=0)
    with pytest.raises(ValidationError):
        _record(active=1)
    with pytest.raises(ValidationError):
        _record(actor_principal_id="arbitrary-user-label")


def test_request_and_response_dtos_share_strict_policy_values() -> None:
    policy = AdmissionPolicyValues()
    update = AdmissionPolicyUpdate(expected_revision=0, policy=policy)
    record = _record(policy=policy)
    state = AdmissionPolicyState(persisted=record, applied_revision=1)
    history = AdmissionPolicyHistory(entries=(record,), next_before_revision=None)

    assert update.policy is policy
    assert state.persisted is record
    assert history.entries == (record,)
    assert AdmissionPolicyState(persisted=None, applied_revision=None).persisted is None
    with pytest.raises(ValidationError):
        AdmissionPolicyUpdate(expected_revision=True, policy=policy)
    with pytest.raises(ValidationError):
        AdmissionPolicyRevision(expected_revision=-1)
    assert AdmissionPolicyRevision(expected_revision=0).expected_revision == 0


def test_history_response_is_bounded_to_one_sdk_page() -> None:
    record = _record()

    with pytest.raises(ValidationError):
        AdmissionPolicyHistory(entries=(record,) * 101, next_before_revision=None)
