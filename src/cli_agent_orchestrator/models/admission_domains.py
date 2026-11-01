"""Shared resource values, not proof of physical grouping or operation authority."""

import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from cli_agent_orchestrator.models.resource_envelope import envelope_integer

DomainKind = Literal["host", "node", "storage"]
DomainUnit = Literal["bytes", "millicores", "postgres_session"]
ReservationState = Literal["preparing", "active", "releasing", "uncertain", "released"]


def canonical_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Resource identity requires a UUID")
    return str(UUID(value))


def canonical_digest(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Resource identity requires a canonical digest")


@dataclass(frozen=True)
class DomainSpec:
    domain_id: str
    incarnation: str
    kind: DomainKind
    unit: DomainUnit
    capacity: int
    physical_binding_sha256: str

    def __post_init__(self) -> None:
        for field in ("domain_id", "incarnation"):
            object.__setattr__(self, field, canonical_id(getattr(self, field)))
        envelope_integer(self.capacity, minimum=0)
        canonical_digest(self.physical_binding_sha256)
        if self.kind not in {"host", "node", "storage"} or self.unit not in {
            "bytes",
            "millicores",
            "postgres_session",
        }:
            raise ValueError("Unsupported resource domain or unit")
        if (self.kind == "storage") != (self.unit == "postgres_session"):
            raise ValueError("Resource domain unit mismatch")


@dataclass(frozen=True)
class DomainRecord:
    spec: DomainSpec
    revision: int
    active: bool


@dataclass(frozen=True)
class DomainRequest:
    domain_id: str
    incarnation: str
    domain_revision: int
    amount: int

    def __post_init__(self) -> None:
        for field in ("domain_id", "incarnation"):
            object.__setattr__(self, field, canonical_id(getattr(self, field)))
        envelope_integer(self.domain_revision)
        envelope_integer(self.amount)


@dataclass(frozen=True)
class SharedReservation:
    operation_id: str
    revision: int
    state: ReservationState


@dataclass(frozen=True)
class UsageMismatch:
    domain_id: str
    committed: int | None
    allocated: int
