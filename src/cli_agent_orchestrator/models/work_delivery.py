"""Versioned delivery data. Adapter code and authenticated identity never come from it."""

from typing import Annotated, Literal

from pydantic import StringConstraints, field_validator

from cli_agent_orchestrator.models.work_contract import FrozenContractModel, Identity, Positive


class WorkDeliveryEnvelope(FrozenContractModel):
    schema_version: Literal[1] = 1
    operation_kind: Identity
    adapter_version: Positive
    payload_json: Annotated[str, StringConstraints(min_length=2, max_length=65536)]

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("unsupported delivery envelope version")
        return value
