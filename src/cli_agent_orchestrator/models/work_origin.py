"""Strict, immutable internal records for durable launch provisioning v1."""

import hashlib
import json

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from cli_agent_orchestrator.models.work_contract import EffectiveWorkContract, EffectiveWorkContractV2
from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope

Identity = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Positive = Annotated[int, Field(gt=0, le=2**63 - 1)]
OriginKind = Literal["child", "workflow", "receiver"]
OriginAction = Literal[
    "launch",
    "admit_child",
    "admit_handoff",
    "admit_step",
    "execute",
    "task_received",
    "delegate",
]
LineageKind = Literal["child", "handoff"]

_LINEAGE_INTEGRITY_DOMAIN = "cao.work-lineage.integrity.v1"
_LINEAGE_INTEGRITY_CANONICALIZATION_VERSION = 1
_LINEAGE_INTEGRITY_DIGEST_FIELDS = frozenset(
    {
        "parent_contract_hash",
        "child_contract_hash",
        "snapshot_hash",
        "delivery_hash",
        "request_hash",
    }
)
_LINEAGE_INTEGRITY_INTEGER_FIELDS = frozenset(
    {
        "schema_version",
        "parent_generation",
        "child_generation",
        "child_subject_revision",
        "child_authorization_revision",
        "child_grant_revision",
        "receiver_subject_revision",
        "receiver_authorization_revision",
        "receiver_grant_revision",
    }
)
_LINEAGE_INTEGRITY_NULLABLE_FIELDS = frozenset({"native_child_id", "terminal_id", "handoff_id"})


def _lineage_integrity_value(record, field: str):
    try:
        return record[field]
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError(f"lineage integrity record is missing {field}") from error


def _lineage_integrity_string(record, field: str) -> str | None:
    value = _lineage_integrity_value(record, field)
    if value is None and field in _LINEAGE_INTEGRITY_NULLABLE_FIELDS:
        return None
    if type(value) is not str or not value:
        raise ValueError(f"lineage integrity {field} must be a nonempty string")
    if field in _LINEAGE_INTEGRITY_DIGEST_FIELDS and (
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"lineage integrity {field} must be a lowercase sha256 digest")
    return value


def _lineage_integrity_integer(record, field: str) -> int:
    value = _lineage_integrity_value(record, field)
    if type(value) is not int or value <= 0:
        raise ValueError(f"lineage integrity {field} must be a positive integer")
    return value


def lineage_integrity_fingerprint(record) -> str:
    """Return the exact v23 integrity fingerprint for one v22 lineage binding.

    ``record`` is intentionally only a read-only, subscriptable durable row.
    This keeps producer and verifier on precisely the same canonical input and
    prevents model adapters from silently normalizing persisted evidence.
    """

    integers = {
        field: _lineage_integrity_integer(record, field)
        for field in _LINEAGE_INTEGRITY_INTEGER_FIELDS
    }
    if integers["schema_version"] != 1:
        raise ValueError("lineage integrity requires a v1 binding")
    strings = {
        field: _lineage_integrity_string(record, field)
        for field in (
            "kind",
            "job_id",
            "parent_work_item_id",
            "parent_attempt_id",
            "child_work_item_id",
            "child_attempt_id",
            "requester_principal_id",
            "executor_principal_id",
            "child_subject_id",
            "child_authorization_kind",
            "child_grant_id",
            "receiver_subject_id",
            "receiver_authorization_kind",
            "receiver_grant_id",
            "parent_contract_hash",
            "child_contract_hash",
            "snapshot_id",
            "snapshot_hash",
            "delivery_id",
            "delivery_hash",
            "request_hash",
            "idempotency_key",
            "native_child_id",
            "terminal_id",
            "handoff_id",
        )
    }
    if strings["kind"] not in {"child", "handoff"}:
        raise ValueError("lineage integrity kind is invalid")
    if strings["child_authorization_kind"] != "child":
        raise ValueError("lineage integrity child authorization kind is invalid")
    if strings["receiver_authorization_kind"] != "receiver":
        raise ValueError("lineage integrity receiver authorization kind is invalid")
    canonical = {
        "binding": {
            "child": {
                "attempt_id": strings["child_attempt_id"],
                "authorization_kind": strings["child_authorization_kind"],
                "authorization_revision": integers["child_authorization_revision"],
                "generation": integers["child_generation"],
                "grant_id": strings["child_grant_id"],
                "grant_revision": integers["child_grant_revision"],
                "subject_id": strings["child_subject_id"],
                "subject_revision": integers["child_subject_revision"],
                "work_item_id": strings["child_work_item_id"],
            },
            "child_contract_hash": strings["child_contract_hash"],
            "delivery_hash": strings["delivery_hash"],
            "delivery_id": strings["delivery_id"],
            "executor_principal_id": strings["executor_principal_id"],
            "handoff_id": strings["handoff_id"],
            "idempotency_key": strings["idempotency_key"],
            "job_id": strings["job_id"],
            "kind": strings["kind"],
            "native_child_id": strings["native_child_id"],
            "parent": {
                "attempt_id": strings["parent_attempt_id"],
                "generation": integers["parent_generation"],
                "work_item_id": strings["parent_work_item_id"],
            },
            "parent_contract_hash": strings["parent_contract_hash"],
            "receiver": {
                "authorization_kind": strings["receiver_authorization_kind"],
                "authorization_revision": integers["receiver_authorization_revision"],
                "grant_id": strings["receiver_grant_id"],
                "grant_revision": integers["receiver_grant_revision"],
                "subject_id": strings["receiver_subject_id"],
                "subject_revision": integers["receiver_subject_revision"],
            },
            "request_hash": strings["request_hash"],
            "requester_principal_id": strings["requester_principal_id"],
            "schema_version": integers["schema_version"],
            "snapshot_hash": strings["snapshot_hash"],
            "snapshot_id": strings["snapshot_id"],
            "terminal_id": strings["terminal_id"],
        },
        "canonicalization_version": _LINEAGE_INTEGRITY_CANONICALIZATION_VERSION,
        "domain": _LINEAGE_INTEGRITY_DOMAIN,
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class FrozenOriginModel(BaseModel):
    """Wire-safe v1 records: no coercion, mutation, or unknown fields."""

    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", revalidate_instances="always"
    )


class ProvisionRef(FrozenOriginModel):
    """Stable logical binding identity with one immutable current revision."""

    schema_version: Literal[1] = 1
    id: Identity
    principal_id: Identity
    selector: Identity
    revision: Positive

    @field_validator("schema_version", "revision", mode="before")
    @classmethod
    def exact_integer(cls, value):
        if type(value) is not int:
            raise ValueError("provision versions and revisions must be integers")
        return value


class ProvisionedLaunch(FrozenOriginModel):
    """The active durable launch context reconstructed from SQLite only."""

    schema_version: Literal[1] = 1
    ref: ProvisionRef
    job_id: Identity
    grant_id: Identity
    grant_revision: Positive
    contract: EffectiveWorkContract | EffectiveWorkContractV2
    contract_hash: Digest
    snapshot_id: Identity
    snapshot_hash: Digest
    adapter_version: Positive
    lease_seconds: Annotated[int, Field(gt=0, le=3600)]

    @field_validator(
        "schema_version", "grant_revision", "adapter_version", "lease_seconds", mode="before"
    )
    @classmethod
    def exact_integer(cls, value):
        if type(value) is not int:
            raise ValueError("provision versions and bounds must be integers")
        return value

    def fingerprint(self) -> str:
        """Hash every effective provision field carried across the launch handoff."""
        encoded = json.dumps(
            {
                "adapter_version": self.adapter_version,
                "contract_hash": self.contract_hash,
                "grant_id": self.grant_id,
                "grant_revision": self.grant_revision,
                "job_id": self.job_id,
                "lease_seconds": self.lease_seconds,
                "ref": self.ref.model_dump(mode="json"),
                "schema_version": self.schema_version,
                "snapshot_hash": self.snapshot_hash,
                "snapshot_id": self.snapshot_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class OriginSubjectRef(FrozenOriginModel):
    """An immutable, registered origin identity revision."""

    schema_version: Literal[1] = 1
    subject_id: Identity
    kind: OriginKind
    revision: Positive

    @field_validator("schema_version", "revision", mode="before")
    @classmethod
    def exact_integer(cls, value):
        if type(value) is not int:
            raise ValueError("origin subject versions and revisions must be integers")
        return value


class OriginAuthorizationRef(FrozenOriginModel):
    """The immutable current revision selector for a subject authorization."""

    schema_version: Literal[1] = 1
    subject_id: Identity
    origin_kind: OriginKind
    revision: Positive

    @field_validator("schema_version", "revision", mode="before")
    @classmethod
    def exact_integer(cls, value):
        if type(value) is not int:
            raise ValueError("origin authorization versions and revisions must be integers")
        return value


class OriginAuthorization(FrozenOriginModel):
    """A resolved active action set; it is not a client capability token."""

    schema_version: Literal[1] = 1
    ref: OriginAuthorizationRef
    subject_revision: Positive
    issuer_id: Identity
    job_id: Identity
    grant_id: Identity
    grant_revision: Positive
    actions: tuple[OriginAction, ...]
    expires_at: float

    @field_validator("schema_version", "subject_revision", "grant_revision", mode="before")
    @classmethod
    def exact_integer(cls, value):
        if type(value) is not int:
            raise ValueError("origin authorization versions and revisions must be integers")
        return value

    @field_validator("actions")
    @classmethod
    def unique_actions(cls, value):
        if not value or len(set(value)) != len(value):
            raise ValueError("origin actions must be nonempty and unique")
        return value


class WorkAttemptRef(FrozenOriginModel):
    """Exact durable parent identity; a work id alone never selects an attempt."""

    schema_version: Literal[1] = 1
    work_item_id: Identity
    attempt_id: Identity
    generation: Positive

    @field_validator("schema_version", "generation", mode="before")
    @classmethod
    def exact_integer(cls, value):
        if type(value) is not int:
            raise ValueError("attempt versions and generations must be integers")
        return value


class ManagedLineageIntent(FrozenOriginModel):
    """Unprivileged child request content; effective authority is resolved server-side."""

    schema_version: Literal[1] = 1
    contract: EffectiveWorkContract | EffectiveWorkContractV2
    delivery: WorkDeliveryEnvelope
    lease_seconds: Annotated[int, Field(gt=0, le=3600)]
    native_child_id: Identity | None = None
    terminal_id: Identity | None = None
    handoff_id: Identity | None = None

    @field_validator("schema_version", "lease_seconds", mode="before")
    @classmethod
    def exact_integer(cls, value):
        if type(value) is not int:
            raise ValueError("lineage versions and lease seconds must be integers")
        return value
