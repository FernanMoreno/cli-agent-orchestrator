"""Closed immutable evidence for a server-resolved order, never an authority token."""

import hashlib
import json
import posixpath
import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Identity = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
]
Text = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Positive = Annotated[int, Field(gt=0, le=2**63 - 1)]


class FrozenContractModel(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", revalidate_instances="always"
    )


class ObservedValue(FrozenContractModel):
    status: Literal["known", "unknown", "not_applicable"] = "unknown"
    value: Text | None = None
    provenance: Literal[
        "launch_config", "provider_observation", "not_observed", "not_applicable"
    ] = "not_observed"

    @model_validator(mode="after")
    def consistent_observation(self):
        if self.status == "known":
            if self.value is None or self.provenance not in {
                "launch_config",
                "provider_observation",
            }:
                raise ValueError("known values require observed provenance")
        elif self.value is not None or self.provenance != (
            "not_observed" if self.status == "unknown" else "not_applicable"
        ):
            raise ValueError("unobserved values cannot claim effective settings")
        return self


class ContractPermissions(FrozenContractModel):
    tools: tuple[Text, ...] = Field(default=(), max_length=128)
    paths: tuple[Text, ...] = Field(default=(), max_length=128)
    commands: tuple[Text, ...] = Field(default=(), max_length=128)
    network: tuple[Text, ...] = Field(default=(), max_length=128)
    artifacts: tuple[Text, ...] = Field(default=(), max_length=128)

    @field_validator("tools", "paths", "commands", "network", "artifacts")
    @classmethod
    def canonical_set(cls, value):
        return tuple(sorted(set(value)))


class ContractResources(FrozenContractModel):
    checkout_root: Text
    write_paths: tuple[Text, ...] = Field(default=(), max_length=128)
    units: Positive
    dependencies: tuple[Identity, ...] = Field(default=(), max_length=64)

    @field_validator("write_paths", "dependencies")
    @classmethod
    def canonical_set(cls, value):
        return tuple(sorted(set(value)))


class ContractSnapshot(FrozenContractModel):
    state: Literal["present", "absent"]
    id: Identity | None = None
    delivered_hash: Digest | None = None
    absence_reason: Literal["legacy_parent_has_no_snapshot"] | None = None

    @model_validator(mode="after")
    def consistent_snapshot(self):
        if self.state == "present":
            if self.id is None or self.delivered_hash is None or self.absence_reason is not None:
                raise ValueError("present snapshot requires exact identity and delivered hash")
        elif self.id is not None or self.delivered_hash is not None or self.absence_reason is None:
            raise ValueError("snapshot absence must be explicit")
        return self


class EffectiveWorkContract(FrozenContractModel):
    schema_version: Literal[1] = 1
    id: Identity
    operation_kind: Identity
    provider: Identity
    backend: Identity
    required_enforcement: Literal["process_boundary"] = "process_boundary"
    permissions: ContractPermissions
    resources: ContractResources
    snapshot: ContractSnapshot
    model: ObservedValue = Field(default_factory=ObservedValue)
    effort: ObservedValue = Field(default_factory=ObservedValue)
    profile_revision: ObservedValue = Field(default_factory=ObservedValue)
    source_revision: ObservedValue = Field(default_factory=ObservedValue)
    repo_baseline: ObservedValue = Field(default_factory=ObservedValue)

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("only integer contract version 1 is supported")
        return value

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @model_validator(mode="after")
    def bounded_json(self):
        if len(self.canonical_json().encode("utf-8")) > 32768:
            raise ValueError("effective work contract exceeds byte bound")
        return self


class ExecutableIdentity(FrozenContractModel):
    """Descriptive executable evidence; it does not inspect bytes or prevent TOCTOU."""

    command_token: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    content_reference: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    sha256_digest: Digest
    elf_machine: Literal["x86_64"]
    elf_class: Literal["ELF64"]
    endianness: Literal["little"]
    static: Literal[True] = True

    @field_validator("command_token")
    @classmethod
    def canonical_absolute_command(cls, value):
        if (
            re.fullmatch(r"/[A-Za-z0-9._/+@-]+", value) is None
            or value.startswith("//")
            or "\x00" in value
            or posixpath.normpath(value) != value
        ):
            raise ValueError("command token must be an absolute normalized path")
        return value

    @model_validator(mode="after")
    def consistent_executable_identity(self):
        if self.content_reference.removeprefix("sha256:") != self.sha256_digest:
            raise ValueError("content reference must match the SHA-256 digest")
        return self


class EffectiveWorkContractV2(FrozenContractModel):
    """Version 2 contract shape, kept separate from the persisted v1 contract."""

    schema_version: Literal[2] = 2
    id: Identity
    operation_kind: Identity
    provider: Identity
    backend: Identity
    required_enforcement: Literal["process_boundary"] = "process_boundary"
    permissions: ContractPermissions
    resources: ContractResources
    snapshot: ContractSnapshot
    model: ObservedValue = Field(default_factory=ObservedValue)
    effort: ObservedValue = Field(default_factory=ObservedValue)
    profile_revision: ObservedValue = Field(default_factory=ObservedValue)
    source_revision: ObservedValue = Field(default_factory=ObservedValue)
    repo_baseline: ObservedValue = Field(default_factory=ObservedValue)
    executable_identities: tuple[ExecutableIdentity, ...] = Field(default=(), max_length=128)

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 2:
            raise ValueError("only integer contract version 2 is supported")
        return value

    @field_validator("executable_identities")
    @classmethod
    def sorted_executable_identities(cls, value):
        return tuple(sorted(value, key=lambda identity: identity.command_token))

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @model_validator(mode="after")
    def bounded_json_and_exact_command_mapping(self):
        if len(self.canonical_json().encode("utf-8")) > 32768:
            raise ValueError("effective work contract exceeds byte bound")
        command_tokens = tuple(identity.command_token for identity in self.executable_identities)
        if len(set(command_tokens)) != len(command_tokens):
            raise ValueError("executable identities cannot duplicate command tokens")
        if command_tokens != self.permissions.commands:
            raise ValueError("executable identities must exactly match permitted commands")
        return self


class WorkContractBinding(FrozenContractModel):
    attempt_id: Identity
    generation: Positive
    job_id: Identity
    work_item_id: Identity
    principal_id: Identity
    grant_id: Identity
    grant_revision: Positive
    contract: EffectiveWorkContract | EffectiveWorkContractV2
    contract_hash: Digest
    created_at: float
