"""Immutable resource-claim contracts; neither values nor identities prove authority."""

import math
import re
from dataclasses import dataclass
from uuid import UUID


def envelope_integer(value: int, *, minimum: int = 1) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value < 2**63:
        raise ValueError("resource envelope requires bounded integer units")


def _digest(value: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("resource envelope requires a canonical digest")


@dataclass(frozen=True)
class ResourceBinding:
    instance_id: str
    process_generation: str
    process_pid: int
    process_started_at: float
    store_id: str
    target_identity_sha256: str
    mount_ns_device: int
    mount_ns_inode: int
    cgroup_ns_device: int
    cgroup_ns_inode: int
    resource_view_sha256: str

    def __post_init__(self) -> None:
        for name in ("instance_id", "process_generation", "store_id"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError("resource binding requires UUID identities")
            object.__setattr__(self, name, str(UUID(value)))
        envelope_integer(self.process_pid)
        try:
            valid_start = (
                not isinstance(self.process_started_at, bool)
                and isinstance(self.process_started_at, (int, float))
                and math.isfinite(self.process_started_at)
                and self.process_started_at > 0
            )
        except OverflowError:
            valid_start = False
        if not valid_start:
            raise ValueError("resource binding requires a finite process start")
        for value in (self.mount_ns_device, self.cgroup_ns_device):
            envelope_integer(value, minimum=0)
        for value in (self.mount_ns_inode, self.cgroup_ns_inode):
            envelope_integer(value)
        _digest(self.target_identity_sha256)
        _digest(self.resource_view_sha256)


@dataclass(frozen=True)
class EnvelopeClaims:
    memory_budget_bytes: int
    cpu_budget_millicores: int
    storage_budget_slots: int
    complete_hierarchy: bool
    root_is_host: bool

    def __post_init__(self) -> None:
        for value in (
            self.memory_budget_bytes,
            self.cpu_budget_millicores,
            self.storage_budget_slots,
        ):
            envelope_integer(value)
        if type(self.complete_hierarchy) is not bool or type(self.root_is_host) is not bool:
            raise ValueError("resource coverage claims require explicit booleans")
        if self.root_is_host and not self.complete_hierarchy:
            raise ValueError("host root requires complete operator-attested coverage")


@dataclass(frozen=True)
class ResourceEnvelopeRecord:
    binding: ResourceBinding
    claims: EnvelopeClaims
    revision: int
    active: bool
    actor_principal_id: str

    def __post_init__(self) -> None:
        if type(self.binding) is not ResourceBinding or type(self.claims) is not EnvelopeClaims:
            raise ValueError("resource record requires validated contracts")
        envelope_integer(self.revision)
        if type(self.active) is not bool:
            raise ValueError("resource record requires an explicit active state")
        if (
            not isinstance(self.actor_principal_id, str)
            or re.fullmatch(r"principal-[0-9a-f]{64}", self.actor_principal_id) is None
        ):
            raise ValueError("resource record requires a principal identifier")
