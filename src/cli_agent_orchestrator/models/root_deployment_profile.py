"""Strict deployment configuration, with no resource or signing authority."""

from pathlib import Path

from pydantic import Field, StrictFloat, StrictInt, StrictStr, field_validator

from cli_agent_orchestrator.models.admission_control import StrictControlModel


class RootDeploymentFootprint(StrictControlModel):
    memory_bytes: StrictInt = Field(ge=1, le=2**63 - 1)
    cpu_millicores: StrictInt = Field(ge=1, le=2**63 - 1)
    storage_slots: StrictInt = Field(default=0, ge=0, le=0)


class RootDeploymentProfile(StrictControlModel):
    version: StrictInt = Field(ge=1, le=1)
    signing_key_file: StrictStr = Field(min_length=1, max_length=4096, repr=False)
    trust_file: StrictStr = Field(min_length=1, max_length=4096, repr=False)
    policy_file: StrictStr = Field(min_length=1, max_length=4096, repr=False)
    scope: StrictStr = Field(min_length=1, max_length=256)
    worker_footprint: RootDeploymentFootprint
    child_footprint: RootDeploymentFootprint
    command_footprint: RootDeploymentFootprint
    startup_footprint: RootDeploymentFootprint
    refresh_footprint: RootDeploymentFootprint
    timeout_s: StrictFloat = Field(default=30.0, gt=0, le=30)
    close_timeout_s: StrictFloat = Field(default=5.0, gt=0, le=30)

    @field_validator("signing_key_file", "trust_file", "policy_file")
    @classmethod
    def absolute_private_path(cls, value: str) -> str:
        if "\0" in value or not Path(value).is_absolute():
            raise ValueError("deployment paths must be absolute")
        return value

    @field_validator("scope")
    @classmethod
    def canonical_scope(cls, value: str) -> str:
        if value.strip() != value or not value.isprintable():
            raise ValueError("deployment scope must be canonical and printable")
        return value
