"""Shared resource values, not proof of physical grouping or operation authority."""

import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from cli_agent_orchestrator.models.resource_envelope import ResourceBinding, envelope_integer

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
class SharedResourceCost:
    """Trusted operation footprint; never a client-selected domain or owner."""

    memory_bytes: int = 0
    cpu_millicores: int = 0
    storage_slots: int = 0

    def __post_init__(self) -> None:
        for value in (self.memory_bytes, self.cpu_millicores, self.storage_slots):
            envelope_integer(value, minimum=0)
        if not any((self.memory_bytes, self.cpu_millicores, self.storage_slots)):
            raise ValueError("Shared resource cost must consume a resource")


@dataclass(frozen=True)
class MetadataCredit:
    """One durable storage slot bound to an exact process view.

    It is never released by membership revocation; only a later physical
    retirement witness may retire it. It is not delivery authorization.
    """

    credit_id: str
    domain_id: str
    instance_id: str
    process_generation: str
    process_pid: int
    process_started_at: float
    binding_sha256: str
    amount: int
    revision: int

    def __post_init__(self) -> None:
        for field in ("credit_id", "domain_id", "instance_id", "process_generation"):
            object.__setattr__(self, field, canonical_id(getattr(self, field)))
        envelope_integer(self.process_pid)
        envelope_integer(self.revision)
        canonical_digest(self.binding_sha256)
        if (
            type(self.process_started_at) is not float
            or not self.process_started_at > 0
            or self.process_started_at == float("inf")
            or type(self.amount) is not int
            or self.amount != 1
        ):
            raise ValueError("Invalid metadata credit")


@dataclass(frozen=True)
class UsageMismatch:
    domain_id: str
    committed: int | None
    allocated: int


DebtKind = Literal["metadata_credit", "operation"]
DebtState = Literal["charged", "preparing", "active", "releasing", "uncertain"]


@dataclass(frozen=True)
class SharedDebtCursor:
    """Keyset position in a discovery pass, never a completeness witness."""

    kind: DebtKind
    entry_id: str

    def __post_init__(self) -> None:
        if type(self.kind) is not str or self.kind not in {"metadata_credit", "operation"}:
            raise ValueError("Invalid shared debt cursor kind")
        object.__setattr__(self, "entry_id", canonical_id(self.entry_id))


@dataclass(frozen=True)
class SharedDebtEntry:
    """Persisted header only; neither death inference nor authority to adopt it."""

    kind: DebtKind
    entry_id: str
    instance_id: str
    process_generation: str
    revision: int
    state: DebtState
    current_owner: bool

    def __post_init__(self) -> None:
        SharedDebtCursor(self.kind, self.entry_id)
        for name in ("entry_id", "instance_id", "process_generation"):
            object.__setattr__(self, name, canonical_id(getattr(self, name)))
        envelope_integer(self.revision)
        states = (
            {"charged"}
            if self.kind == "metadata_credit"
            else {"preparing", "active", "releasing", "uncertain"}
        )
        if (
            type(self.state) is not str
            or self.state not in states
            or type(self.current_owner) is not bool
        ):
            raise ValueError("Invalid shared debt entry")


@dataclass(frozen=True)
class SharedDebtPage:
    """One coherent page, not a snapshot across requests or absence evidence."""

    entries: tuple[SharedDebtEntry, ...]
    next_cursor: SharedDebtCursor | None

    def __post_init__(self) -> None:
        if (
            type(self.entries) is not tuple
            or any(type(entry) is not SharedDebtEntry for entry in self.entries)
            or (self.next_cursor is not None and type(self.next_cursor) is not SharedDebtCursor)
        ):
            raise ValueError("Invalid shared debt page")


@dataclass(frozen=True)
class MetadataCoverage:
    """Private reader result; constructing this value does not grant authority."""

    binding: ResourceBinding
    membership_sha256: str
    storage_domain_ids: tuple[str, ...]
    credits: tuple[MetadataCredit, ...]

    def __post_init__(self) -> None:
        if type(self.binding) is not ResourceBinding:
            raise ValueError("Invalid metadata coverage binding")
        canonical_digest(self.membership_sha256)
        if (
            type(self.storage_domain_ids) is not tuple
            or not self.storage_domain_ids
            or type(self.credits) is not tuple
            or not self.credits
            or any(type(c) is not MetadataCredit for c in self.credits)
        ):
            raise ValueError("Incomplete metadata coverage")
        ids = tuple(canonical_id(value) for value in self.storage_domain_ids)
        if (
            len(set(ids)) != len(ids)
            or len(self.credits) != len(ids)
            or {c.domain_id for c in self.credits} != set(ids)
        ):
            raise ValueError("Incomplete metadata coverage")
        object.__setattr__(self, "storage_domain_ids", tuple(sorted(ids)))
        for c in self.credits:
            if (c.instance_id, c.process_generation, c.process_pid, c.process_started_at) != (
                self.binding.instance_id,
                self.binding.process_generation,
                self.binding.process_pid,
                self.binding.process_started_at,
            ):
                raise ValueError("Metadata coverage belongs to another process")


@dataclass(frozen=True)
class SharedAbortReceipt:
    """Acknowledged administrative abort, never an execution allowance."""

    action_id: str
    operation_id: str
    revision: Literal[2] = 2
    state: Literal["released"] = "released"

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_id", canonical_id(self.action_id))
        object.__setattr__(self, "operation_id", canonical_id(self.operation_id))
        if type(self.revision) is not int or self.revision != 2 or self.state != "released":
            raise ValueError("Invalid administrative abort receipt")


@dataclass(frozen=True)
class SharedReleaseReceipt:
    """Audited resource release; never completion or permission to replay Work."""

    action_id: str
    operation_id: str
    revision: int
    state: Literal["released"] = "released"

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_id", canonical_id(self.action_id))
        object.__setattr__(self, "operation_id", canonical_id(self.operation_id))
        envelope_integer(self.revision, minimum=2)
        if type(self.state) is not str or self.state != "released":
            raise ValueError("Invalid administrative release receipt")
