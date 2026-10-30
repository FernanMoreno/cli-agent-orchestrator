"""Operator-attested coverage bound to the current process and resource view."""

import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal
from uuid import UUID

from cli_agent_orchestrator.clients.admission_bootstrap_repository import (
    EnvelopeNotFound,
    EnvelopeRevisionConflict,
    ResourceEnvelopeRepository,
)
from cli_agent_orchestrator.clients.postgres import DatabaseTarget
from cli_agent_orchestrator.clients.postgres.config import ConfigurationError
from cli_agent_orchestrator.clients.postgres.connection import DatabaseError
from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch
from cli_agent_orchestrator.models.resource_envelope import (
    EnvelopeClaims,
    ResourceBinding,
    ResourceEnvelopeRecord,
    envelope_integer,
)
from cli_agent_orchestrator.security.auth import SCOPE_ADMIN, is_verified_principal
from cli_agent_orchestrator.services.admission_policy import validate_clock
from cli_agent_orchestrator.services.local_peer_identity import (
    LocalProcessIdentity,
    current_process_identity,
)


def _view_file(path: Path) -> bytes:
    maximum_bytes = 1024 * 1024
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Resource view requires regular proc files")
        payload = stream.read(maximum_bytes + 1)
        if len(payload) > maximum_bytes:
            raise ValueError("Resource view exceeds its observation limit")
        if not payload or not payload.endswith(b"\n"):
            raise ValueError("Resource view is empty or incomplete")
        return payload


def capture_server_binding(
    target: DatabaseTarget,
    *,
    identity_supplier: Callable[[], LocalProcessIdentity] = current_process_identity,
    proc_root: Path = Path("/proc"),
) -> ResourceBinding:
    """Capture a view fingerprint, never proof of unseen ancestors.

    Suppliers and proc_root belong to trusted bootstrap/test wiring. They are
    not request fields, generic settings, environment flags or peer claims.
    """

    def current_identity() -> LocalProcessIdentity:
        try:
            return identity_supplier()
        except RuntimeError:
            # This factory has explicit identity-unavailable failures whose
            # messages can include private paths. Do not catch repo bugs here.
            raise ValueError("Resource process identity is unavailable") from None

    identity = current_identity()
    if identity.pid != os.getpid():
        raise ValueError("Resource identity does not belong to this process")

    def namespaces() -> tuple[tuple[int, int], tuple[int, int]]:
        entries = [os.stat(proc_root / "self/ns" / name) for name in ("mnt", "cgroup")]
        return (entries[0].st_dev, entries[0].st_ino), (entries[1].st_dev, entries[1].st_ino)

    before = namespaces()
    membership = _view_file(proc_root / "self/cgroup")
    mounts = _view_file(proc_root / "self/mountinfo")
    if (
        namespaces() != before
        or _view_file(proc_root / "self/cgroup") != membership
        or _view_file(proc_root / "self/mountinfo") != mounts
        or current_identity() != identity
    ):
        raise ValueError("Resource view changed during observation")
    view = hashlib.sha256(
        len(membership).to_bytes(8, "big") + membership + len(mounts).to_bytes(8, "big") + mounts
    ).hexdigest()
    target_digest = hashlib.sha256(
        json.dumps(target.identity, separators=(",", ":")).encode()
    ).hexdigest()
    return ResourceBinding(
        instance_id=identity.instance_id,
        process_generation=identity.process_generation,
        process_pid=identity.pid,
        process_started_at=identity.process_started_at,
        store_id=target.store_id,
        target_identity_sha256=target_digest,
        mount_ns_device=before[0][0],
        mount_ns_inode=before[0][1],
        cgroup_ns_device=before[1][0],
        cgroup_ns_inode=before[1][1],
        resource_view_sha256=view,
    )


@dataclass(frozen=True, init=False)
class VerifiedResourceAttestation:
    binding: ResourceBinding
    claims: EnvelopeClaims
    revision: int
    verified_at: float


_ATTESTATION_SEAL = object()


def is_verified_resource_attestation(value: object) -> bool:
    return (
        type(value) is VerifiedResourceAttestation
        and getattr(value, "_factory_seal", None) is _ATTESTATION_SEAL
    )


@dataclass(frozen=True)
class AttestationLoadResult:
    attestation: VerifiedResourceAttestation | None
    reason: Literal[
        "authority_verified",
        "authority_missing",
        "authority_revoked",
        "authority_unavailable",
        "binding_changed",
        "hierarchy_unverified",
    ]


@dataclass(frozen=True)
class AttestationUpdate:
    revision: int
    active: bool


class ResourceBootstrap:
    def __init__(
        self,
        *,
        repository: ResourceEnvelopeRepository | None = None,
        binding_supplier: Callable[[DatabaseTarget], ResourceBinding] = capture_server_binding,
        instance_supplier: Callable[[], str] = lambda: current_process_identity().instance_id,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._repository = repository if repository is not None else ResourceEnvelopeRepository()
        self._capture = binding_supplier
        self._instance = instance_supplier
        self._clock = clock

    @staticmethod
    def _administrator(principal: object) -> str:
        if (
            not is_verified_principal(principal)
            or principal.kind != "jwt"
            or SCOPE_ADMIN not in principal.scopes
        ):
            raise PermissionError("Resource authority requires a verified administrator")
        return principal.id

    def registered_current(self, *, principal: object) -> ResourceEnvelopeRecord:
        """Administrative registration state; it is not a capacity grant."""
        self._administrator(principal)
        target = self._repository.resolve_target()
        instance = self._server_instance()
        latest = self._repository.read_latest(target, instance)
        if latest is None:
            raise EnvelopeNotFound("Resource authority does not exist")
        if latest.binding.instance_id != instance:
            raise SchemaMismatch("Resource authority instance mismatch")
        return latest

    def _server_instance(self) -> str:
        try:
            value = self._instance()
        except RuntimeError:
            raise ValueError("Resource instance identity is unavailable") from None
        if not isinstance(value, str):
            raise ValueError("Resource instance identity is invalid")
        return str(UUID(value))

    def attest_current(
        self, *, principal: object, claims: EnvelopeClaims, expected_revision: int
    ) -> AttestationUpdate:
        actor = self._administrator(principal)
        envelope_integer(expected_revision, minimum=0)
        if type(claims) is not EnvelopeClaims:
            raise ValueError("Resource authority requires validated operator claims")
        target = self._repository.resolve_target()
        binding = self._capture(target)
        if binding.instance_id != self._server_instance():
            raise ValueError("Resource bootstrap instance binding mismatch")
        record = self._repository.append(
            target,
            binding=binding,
            claims=claims,
            expected_revision=expected_revision,
            principal_id=actor,
        )
        return AttestationUpdate(record.revision, record.active)

    def revoke_current(self, *, principal: object, expected_revision: int) -> AttestationUpdate:
        actor = self._administrator(principal)
        envelope_integer(expected_revision, minimum=0)
        target = self._repository.resolve_target()
        instance_id = self._server_instance()
        latest = self._repository.read_latest(target, instance_id)
        if latest is None:
            if expected_revision != 0:
                raise EnvelopeRevisionConflict("Resource authority revision changed")
            raise EnvelopeNotFound("Resource authority does not exist; no revocation recorded")
        if latest.binding.instance_id != instance_id:
            raise SchemaMismatch("Resource authority instance mismatch")
        record = self._repository.append(
            target,
            binding=latest.binding,
            claims=latest.claims,
            expected_revision=expected_revision,
            principal_id=actor,
            active=False,
        )
        return AttestationUpdate(record.revision, record.active)

    def load_current(self) -> AttestationLoadResult:
        try:
            at = self._clock()
            validate_clock(at)
            target = self._repository.resolve_target()
            binding = self._capture(target)
            if binding.instance_id != self._server_instance():
                return AttestationLoadResult(None, "binding_changed")
            latest = self._repository.read_latest(target, binding.instance_id)
            if latest is None:
                return AttestationLoadResult(None, "authority_missing")
            if not latest.active:
                return AttestationLoadResult(None, "authority_revoked")
            if latest.binding != binding or self._capture(target) != binding:
                return AttestationLoadResult(None, "binding_changed")
            if not latest.claims.complete_hierarchy:
                return AttestationLoadResult(None, "hierarchy_unverified")
            verified = object.__new__(VerifiedResourceAttestation)
            for name, value in (
                ("binding", binding),
                ("claims", latest.claims),
                ("revision", latest.revision),
                ("verified_at", at),
                ("_factory_seal", _ATTESTATION_SEAL),
            ):
                object.__setattr__(verified, name, value)
            return AttestationLoadResult(verified, "authority_verified")
        except (OSError, ValueError, DatabaseError, ConfigurationError, SchemaMismatch):
            return AttestationLoadResult(None, "authority_unavailable")
