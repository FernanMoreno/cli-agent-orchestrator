"""Versioned control DTOs shared by HTTP, SDK and administrative clients."""

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
