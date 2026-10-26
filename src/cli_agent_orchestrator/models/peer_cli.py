"""Versioned machine outputs for operator pairing; authority stays in services."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

InstanceId = Annotated[
    str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]
ProjectId = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PairData(_StrictOutput):
    challenge_id: InstanceId
    code: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{43}$", repr=False)]
    expires_at: Annotated[float, Field(allow_inf_nan=False)]
    peer_instance_id: InstanceId
    peer_display_name: str
    project_id: ProjectId
    canonical_root: Annotated[str, Field(min_length=1)]
    requested_scopes: list[str]


class AcceptData(_StrictOutput):
    peer_instance_id: InstanceId
    peer_display_name: str
    project_id: ProjectId
    scopes: list[str]


class _Envelope(_StrictOutput):
    @field_validator("success", mode="before", check_fields=False)
    @classmethod
    def boolean_success(cls, value):
        if type(value) is not bool:
            raise ValueError("success must be a JSON boolean")
        return value


class PeerPairOutput(_Envelope):
    schema_id: Literal["cao.peer.pair.v1"] = Field(alias="schema")
    success: Literal[True]
    data: PairData


class PeerAcceptOutput(_Envelope):
    schema_id: Literal["cao.peer.accept.v1"] = Field(alias="schema")
    success: Literal[True]
    data: AcceptData


class PeerErrorData(_StrictOutput):
    operation: Literal["pair", "accept"]
    code: Literal["pairing_failed", "pairing_declined", "pairing_aborted", "pairing_result_invalid"]


class PeerErrorOutput(_Envelope):
    schema_id: Literal["cao.peer.error.v1"] = Field(alias="schema")
    success: Literal[False]
    error: PeerErrorData
