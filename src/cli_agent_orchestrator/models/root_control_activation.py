"""Signed control startup data and sealed process-local verification proofs."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import threading
from dataclasses import asdict, dataclass, fields
from typing import Mapping, NoReturn, SupportsIndex

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from cli_agent_orchestrator.models.resource_compute import ComputeObservation
from cli_agent_orchestrator.models.resource_envelope import (
    EnvelopeClaims,
    ResourceBinding,
    ResourceEnvelopeRecord,
    envelope_integer,
)
from cli_agent_orchestrator.models.sql_root_genesis import (
    ConfirmedRootSqlOpening,
    SignedRootSqlOpening,
)

ROOT_CONTROL_ACTIVATION_VERSION = 1
ROOT_CONTROL_ACTIVATION_KIND = "cao_root_control_activation"
ROOT_CONTROL_ACTIVATION_MAX_BYTES = 32 * 1024
ROOT_CONTROL_ACTIVATION_DOMAIN = b"cao-root-control-activation-v1\x00"

_KEY_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_SIGNATURE_TEXT = re.compile(r"[A-Za-z0-9_-]{86}")
_ACTIVATION_SEAL = object()
_STARTUP_SAMPLE_SEAL = object()
_STARTUP_BASIS_SEAL = object()
_SIGNATURE_VERIFICATION_SEAL = object()


def _signature_bytes(value: object) -> bytes:
    if type(value) is not str or _SIGNATURE_TEXT.fullmatch(value) is None:
        raise ValueError("root control activation signature is invalid")
    try:
        raw = base64.b64decode(value.encode("ascii") + b"==", altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as error:
        raise ValueError("root control activation signature is invalid") from error
    if len(raw) != 64 or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value:
        raise ValueError("root control activation signature is invalid")
    return raw


@dataclass(frozen=True)
class RootControlFootprint:
    """Positive RAM/CPU charge; this protocol never grants a storage slot."""

    memory_bytes: int
    cpu_millicores: int
    storage_slots: int = 0

    def __post_init__(self) -> None:
        envelope_integer(self.memory_bytes)
        envelope_integer(self.cpu_millicores)
        if type(self.storage_slots) is not int or self.storage_slots != 0:
            raise ValueError("root control footprint must have zero storage slots")


def _binding_packet(binding: ResourceBinding) -> dict[str, object]:
    return asdict(binding)


def _record_packet(record: ResourceEnvelopeRecord) -> dict[str, object]:
    return {
        "binding": _binding_packet(record.binding),
        "claims": asdict(record.claims),
        "revision": record.revision,
        "active": record.active,
        "actor_principal_id": record.actor_principal_id,
    }


def _record_from_packet(value: object) -> ResourceEnvelopeRecord:
    expected_record = {"binding", "claims", "revision", "active", "actor_principal_id"}
    binding_fields = {field.name for field in fields(ResourceBinding)}
    claim_fields = {field.name for field in fields(EnvelopeClaims)}
    if (
        type(value) is not dict
        or set(value) != expected_record
        or type(value["binding"]) is not dict
        or set(value["binding"]) != binding_fields
        or type(value["claims"]) is not dict
        or set(value["claims"]) != claim_fields
    ):
        raise ValueError("root control activation envelope is invalid")
    binding = ResourceBinding(**value["binding"])
    claims = EnvelopeClaims(**value["claims"])
    if _binding_packet(binding) != value["binding"] or asdict(claims) != value["claims"]:
        raise ValueError("root control activation envelope is not canonical")
    return ResourceEnvelopeRecord(
        binding=binding,
        claims=claims,
        revision=value["revision"],
        active=value["active"],
        actor_principal_id=value["actor_principal_id"],
    )


def _canonical_json(packet: Mapping[str, object]) -> bytes:
    return json.dumps(
        dict(packet),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


@dataclass(frozen=True)
class RootControlActivation:
    """Strict wire packet; the key ID references an external deployment keyring."""

    certificate: SignedRootSqlOpening
    envelope: ResourceEnvelopeRecord
    startup_footprint: RootControlFootprint
    refresh_footprint: RootControlFootprint
    key_id: str
    signature: str

    def __post_init__(self) -> None:
        if (
            type(self.certificate) is not SignedRootSqlOpening
            or type(self.envelope) is not ResourceEnvelopeRecord
            or type(self.startup_footprint) is not RootControlFootprint
            or type(self.refresh_footprint) is not RootControlFootprint
            or type(self.key_id) is not str
            or _KEY_ID.fullmatch(self.key_id) is None
        ):
            raise ValueError("root control activation fields are invalid")
        if (
            not self.envelope.active
            or not self.envelope.claims.complete_hierarchy
            or self.certificate.record.subject != self.envelope.binding
        ):
            raise ValueError("root control activation binding is incomplete or inactive")
        _signature_bytes(self.signature)

    def _unsigned_packet(self) -> dict[str, object]:
        certificate = base64.urlsafe_b64encode(self.certificate.to_bytes()).decode("ascii")
        return {
            "version": ROOT_CONTROL_ACTIVATION_VERSION,
            "kind": ROOT_CONTROL_ACTIVATION_KIND,
            "key_id": self.key_id,
            "certificate": certificate,
            "envelope": _record_packet(self.envelope),
            "startup_footprint": asdict(self.startup_footprint),
            "refresh_footprint": asdict(self.refresh_footprint),
        }

    def signing_bytes(self) -> bytes:
        return ROOT_CONTROL_ACTIVATION_DOMAIN + _canonical_json(self._unsigned_packet())

    def to_packet(self) -> dict[str, object]:
        return {**self._unsigned_packet(), "signature": self.signature}

    def to_bytes(self) -> bytes:
        payload = _canonical_json(self.to_packet())
        if len(payload) > ROOT_CONTROL_ACTIVATION_MAX_BYTES:
            raise ValueError("root control activation exceeds its size limit")
        return payload

    @classmethod
    def from_bytes(cls, value: object) -> RootControlActivation:
        if type(value) is not bytes or not value or len(value) > ROOT_CONTROL_ACTIVATION_MAX_BYTES:
            raise ValueError("root control activation byte length is invalid")

        def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError("root control activation contains duplicate fields")
                result[key] = item
            return result

        try:
            packet = json.loads(
                value.decode("utf-8"),
                object_pairs_hook=reject_duplicates,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    ValueError(f"invalid JSON constant: {token}")
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
            raise ValueError("root control activation JSON is invalid") from error
        expected = {
            "version",
            "kind",
            "key_id",
            "certificate",
            "envelope",
            "startup_footprint",
            "refresh_footprint",
            "signature",
        }
        if (
            type(packet) is not dict
            or set(packet) != expected
            or type(packet["version"]) is not int
            or packet["version"] != ROOT_CONTROL_ACTIVATION_VERSION
            or type(packet["kind"]) is not str
            or packet["kind"] != ROOT_CONTROL_ACTIVATION_KIND
            or type(packet["certificate"]) is not str
        ):
            raise ValueError("root control activation schema is invalid")
        try:
            encoded_certificate = packet["certificate"].encode("ascii")
            certificate_bytes = base64.b64decode(encoded_certificate, altchars=b"-_", validate=True)
            if base64.urlsafe_b64encode(certificate_bytes).decode("ascii") != packet["certificate"]:
                raise ValueError("inner root SQL certificate encoding is not canonical")
            activation = cls(
                certificate=SignedRootSqlOpening.from_bytes(certificate_bytes),
                envelope=_record_from_packet(packet["envelope"]),
                startup_footprint=RootControlFootprint(**packet["startup_footprint"]),
                refresh_footprint=RootControlFootprint(**packet["refresh_footprint"]),
                key_id=packet["key_id"],
                signature=packet["signature"],
            )
        except (KeyError, TypeError, OverflowError, UnicodeEncodeError, binascii.Error) as error:
            raise ValueError("root control activation schema is invalid") from error
        if activation.to_bytes() != value:
            raise ValueError("root control activation JSON is not canonical")
        return activation


def _sign_root_control_activation(
    *,
    committed: ConfirmedRootSqlOpening,
    certificate: SignedRootSqlOpening,
    envelope: ResourceEnvelopeRecord,
    startup_footprint: RootControlFootprint,
    refresh_footprint: RootControlFootprint,
    key_id: str,
    private_key: Ed25519PrivateKey,
) -> RootControlActivation:
    if (
        type(committed) is not ConfirmedRootSqlOpening
        or type(certificate) is not SignedRootSqlOpening
        or not isinstance(private_key, Ed25519PrivateKey)
    ):
        raise ValueError("root control activation requires an Ed25519 signer")
    committed.check()
    if committed.record != certificate.record:
        raise ValueError("root control activation differs from its committed SQL opening")
    # Build the only unsigned value shape through the normal validator with a
    # valid temporary signature; only its canonical unsigned bytes are signed.
    placeholder = base64.urlsafe_b64encode(bytes(64)).decode("ascii").rstrip("=")
    unsigned = RootControlActivation(
        certificate,
        envelope,
        startup_footprint,
        refresh_footprint,
        key_id,
        placeholder,
    )
    committed.claim_activation_sign()
    signature = (
        base64.urlsafe_b64encode(_sign_activation_bytes(private_key, unsigned.signing_bytes()))
        .decode("ascii")
        .rstrip("=")
    )
    return RootControlActivation(
        certificate,
        envelope,
        startup_footprint,
        refresh_footprint,
        key_id,
        signature,
    )


def _sign_activation_bytes(private_key: Ed25519PrivateKey, payload: bytes) -> bytes:
    return private_key.sign(payload)


def verify_root_control_activation_signature(
    activation: RootControlActivation,
    *,
    trusted_keys: Mapping[str, Ed25519PublicKey],
) -> _VerifiedActivationSignature:
    if type(activation) is not RootControlActivation or not isinstance(trusted_keys, Mapping):
        raise InvalidSignature
    key = trusted_keys.get(activation.key_id)
    if not isinstance(key, Ed25519PublicKey):
        raise InvalidSignature
    key.verify(_signature_bytes(activation.signature), activation.signing_bytes())
    proof = object.__new__(_VerifiedActivationSignature)
    proof.activation = activation
    proof.pid = os.getpid()
    proof._seal = _SIGNATURE_VERIFICATION_SEAL
    return proof


class _VerifiedActivationSignature:
    __slots__ = ("activation", "pid", "_seal")
    activation: RootControlActivation
    pid: int
    _seal: object


class VerifiedRootControlActivation:
    """Non-transferable, process-local verification result; never wire authority."""

    __slots__ = ("activation", "pid", "_seal", "_claimed", "_guard", "_startup_owner")
    activation: RootControlActivation
    pid: int
    _seal: object
    _claimed: bool
    _guard: threading.Lock
    _startup_owner: object | None

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError("verified root control activation is immutable")

    def __init__(self) -> None:
        raise TypeError("verified root control activation is issued by its verifier")

    def __copy__(self) -> NoReturn:
        raise TypeError("verified root control activation cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError("verified root control activation cannot be copied")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("verified root control activation cannot be serialized")

    def _claim_startup(self) -> bool:
        if self._seal is not _ACTIVATION_SEAL or self.pid != os.getpid():
            return False
        with self._guard:
            if self._claimed:
                return False
            object.__setattr__(self, "_claimed", True)
            return True

    def _bind_startup_owner(self, owner: object) -> bool:
        if self._seal is not _ACTIVATION_SEAL or self.pid != os.getpid():
            return False
        with self._guard:
            if not self._claimed or self._startup_owner is not None:
                return False
            object.__setattr__(self, "_startup_owner", owner)
            return True


def _issue_verified_root_control_activation(
    activation: RootControlActivation,
    *,
    signature_proof: object,
) -> VerifiedRootControlActivation:
    if (
        type(activation) is not RootControlActivation
        or type(signature_proof) is not _VerifiedActivationSignature
        or signature_proof._seal is not _SIGNATURE_VERIFICATION_SEAL
        or signature_proof.activation is not activation
        or signature_proof.pid != os.getpid()
    ):
        raise ValueError("verified root control activation requires its wire contract")
    value = object.__new__(VerifiedRootControlActivation)
    object.__setattr__(value, "activation", activation)
    object.__setattr__(value, "pid", os.getpid())
    object.__setattr__(value, "_seal", _ACTIVATION_SEAL)
    object.__setattr__(value, "_claimed", False)
    object.__setattr__(value, "_guard", threading.Lock())
    object.__setattr__(value, "_startup_owner", None)
    return value


def is_verified_root_control_activation(value: object) -> bool:
    return (
        type(value) is VerifiedRootControlActivation
        and getattr(value, "_seal", None) is _ACTIVATION_SEAL
        and value.pid == os.getpid()
    )


class VerifiedComputeStartupSample:
    """Opaque result of the consumer's verified two-cycle Linux sampling pass."""

    __slots__ = ("activation", "observation", "envelope", "pid", "_seal")
    activation: VerifiedRootControlActivation
    observation: ComputeObservation
    envelope: ResourceEnvelopeRecord
    pid: int
    _seal: object

    def __init__(self) -> None:
        raise TypeError("compute startup sample is issued only by its Linux consumer")

    def __copy__(self) -> NoReturn:
        raise TypeError("compute startup sample cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError("compute startup sample cannot be copied")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("compute startup sample cannot be serialized")

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError("compute startup sample is immutable")


def _issue_verified_compute_startup_sample(
    activation: VerifiedRootControlActivation,
    *,
    observation: ComputeObservation,
) -> VerifiedComputeStartupSample:
    if (
        not is_verified_root_control_activation(activation)
        or not activation._claimed
        or activation._startup_owner is not None
        or type(observation) is not ComputeObservation
    ):
        raise ValueError("compute sample is not linked to a claimed root activation")
    value = object.__new__(VerifiedComputeStartupSample)
    object.__setattr__(value, "activation", activation)
    object.__setattr__(value, "observation", observation)
    object.__setattr__(value, "envelope", activation.activation.envelope)
    object.__setattr__(value, "pid", os.getpid())
    object.__setattr__(value, "_seal", _STARTUP_SAMPLE_SEAL)
    return value


def is_verified_compute_startup_sample(value: object) -> bool:
    return (
        type(value) is VerifiedComputeStartupSample
        and getattr(value, "_seal", None) is _STARTUP_SAMPLE_SEAL
        and value.pid == os.getpid()
        and is_verified_root_control_activation(value.activation)
        and value.activation._claimed
        and value.envelope == value.activation.activation.envelope
    )


class VerifiedComputeStartupBasis:
    """Sealed compute-only observation bound to one ledger generation."""

    __slots__ = (
        "sample",
        "observation",
        "envelope",
        "budget",
        "pid",
        "generation",
        "_owner",
        "_seal",
    )
    sample: VerifiedComputeStartupSample
    observation: ComputeObservation
    envelope: ResourceEnvelopeRecord
    budget: tuple[int, int]
    pid: int
    generation: int
    _owner: object
    _seal: object

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError("compute startup basis is immutable")

    def __init__(self) -> None:
        raise TypeError("compute startup basis is issued only by the pre-SQL consumer")

    def __copy__(self) -> NoReturn:
        raise TypeError("compute startup basis cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError("compute startup basis cannot be copied")

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("compute startup basis cannot be serialized")

    def belongs_to(self, owner: object, *, generation: int) -> bool:
        return (
            self._seal is _STARTUP_BASIS_SEAL
            and self.pid == os.getpid()
            and self._owner is owner
            and self.generation == generation
        )


def _issue_verified_compute_startup_basis(
    sample: VerifiedComputeStartupSample,
    *,
    owner: object,
    generation: int,
) -> VerifiedComputeStartupBasis:
    if (
        not is_verified_compute_startup_sample(sample)
        or sample.activation._startup_owner is not owner
        or isinstance(generation, bool)
        or type(generation) is not int
        or generation < 0
    ):
        raise ValueError("compute startup basis is not bound to its consumed activation")
    value = object.__new__(VerifiedComputeStartupBasis)
    record = sample.envelope
    object.__setattr__(value, "sample", sample)
    object.__setattr__(value, "observation", sample.observation)
    object.__setattr__(value, "envelope", record)
    object.__setattr__(
        value, "budget", (record.claims.memory_budget_bytes, record.claims.cpu_budget_millicores)
    )
    object.__setattr__(value, "pid", os.getpid())
    object.__setattr__(value, "generation", generation)
    object.__setattr__(value, "_owner", owner)
    object.__setattr__(value, "_seal", _STARTUP_BASIS_SEAL)
    return value


def is_verified_compute_startup_basis(value: object) -> bool:
    return (
        type(value) is VerifiedComputeStartupBasis
        and getattr(value, "_seal", None) is _STARTUP_BASIS_SEAL
        and value.pid == os.getpid()
    )
