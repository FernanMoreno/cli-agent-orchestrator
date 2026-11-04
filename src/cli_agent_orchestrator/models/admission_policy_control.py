"""Strict policy-control DTOs shared by authenticated HTTP and its SDK."""

from __future__ import annotations

import math
import re
from datetime import datetime
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from cli_agent_orchestrator.models.admission_control import StrictControlModel

_MAX_REVISION = 2**63 - 1
_MAX_EXPECTED_REVISION = _MAX_REVISION - 1
_PRINCIPAL_ID = re.compile(r"principal-[0-9a-f]{64}")


class AdmissionPolicyValues(StrictControlModel):
    """Normalized tunables; these values describe policy, not granted capacity."""

    memory_per_delivery_bytes: StrictInt = Field(default=67_108_864, ge=1, le=_MAX_REVISION)
    cpu_per_delivery_millicores: StrictInt = Field(default=250, ge=1, le=_MAX_REVISION)
    memory_reserve_percent: StrictInt = Field(default=20, ge=0, le=99)
    cpu_reserve_percent: StrictInt = Field(default=20, ge=0, le=99)
    max_sample_age_s: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    postgres_reserve_percent: StrictInt = Field(default=20, ge=0, le=99)
    postgres_statement_timeout_ms: StrictInt = Field(default=500, ge=1, le=_MAX_REVISION)
    sample_interval_s: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    healthy_samples_before_growth: StrictInt = Field(default=3, ge=1, le=_MAX_REVISION)

    @field_validator("max_sample_age_s", "sample_interval_s", mode="before")
    @classmethod
    def finite_positive_seconds(cls, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("policy durations must be finite positive seconds")
        if type(value) not in (int, float):
            raise ValueError("policy durations must be finite positive seconds")
        try:
            valid = math.isfinite(value) and value > 0
        except (OverflowError, TypeError):
            valid = False
        if not valid:
            raise ValueError("policy durations must be finite positive seconds")
        return float(value)

    @model_validator(mode="after")
    def sampling_precedes_expiry(self) -> "AdmissionPolicyValues":
        if self.sample_interval_s >= self.max_sample_age_s:
            raise ValueError("sampling interval must be shorter than observation freshness")
        return self


class AdmissionPolicyRecord(StrictControlModel):
    """One persisted, auditable revision returned by the control API."""

    profile_id: StrictStr
    revision: StrictInt = Field(ge=1, le=_MAX_REVISION)
    active: StrictBool
    policy: AdmissionPolicyValues
    actor_principal_id: StrictStr = Field(min_length=1)
    recorded_at: datetime

    @field_validator("profile_id")
    @classmethod
    def canonical_profile_uuid(cls, value: str) -> str:
        try:
            if str(UUID(value)) != value:
                raise ValueError
        except (AttributeError, TypeError, ValueError):
            raise ValueError("profile_id must be a canonical UUID") from None
        return value

    @field_validator("actor_principal_id")
    @classmethod
    def canonical_actor_principal_id(cls, value: str) -> str:
        if _PRINCIPAL_ID.fullmatch(value) is None:
            raise ValueError("actor_principal_id must be a verified principal identifier")
        return value

    @field_validator("recorded_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a timezone")
        return value


class AdmissionPolicyUpdate(StrictControlModel):
    """Complete policy replacement guarded by an expected revision."""

    expected_revision: StrictInt = Field(ge=0, le=_MAX_EXPECTED_REVISION)
    policy: AdmissionPolicyValues


class AdmissionPolicyRevision(StrictControlModel):
    """Expected revision for explicit seed or revoke operations."""

    expected_revision: StrictInt = Field(ge=0, le=_MAX_EXPECTED_REVISION)


class AdmissionPolicyState(StrictControlModel):
    """Persisted state and the separately applied runtime revision."""

    persisted: AdmissionPolicyRecord | None
    applied_revision: StrictInt | None = Field(default=None, ge=1, le=_MAX_REVISION)


class AdmissionPolicyHistory(StrictControlModel):
    """A bounded descending keyset page of append-only policy revisions."""

    entries: tuple[AdmissionPolicyRecord, ...] = Field(max_length=100)
    next_before_revision: StrictInt | None = Field(default=None, ge=1, le=_MAX_REVISION)
