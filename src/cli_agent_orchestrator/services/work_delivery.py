"""Durable, versioned server adapters; no dynamic code loading from stored input."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from types import MappingProxyType
from typing import Callable

from pydantic import BaseModel, ValidationError

from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
from cli_agent_orchestrator.services.step_output_store import ArtifactRef, ImmutableResultStore
from cli_agent_orchestrator.services.work_contract import ContractConflict, _nonsecret


@dataclass(frozen=True)
class DeliveryAdapter:
    payload_model: type[BaseModel]
    send: Callable
    terminal_identity: Callable | None = None
    validate_contract: Callable | None = None
    terminal_target: Callable | None = None

    def __post_init__(self):
        if (
            not isinstance(self.payload_model, type)
            or not issubclass(self.payload_model, BaseModel)
            or self.payload_model.model_config.get("extra") != "forbid"
            or self.payload_model.model_config.get("frozen") is not True
            or not callable(self.send)
            or (self.terminal_identity is not None and not callable(self.terminal_identity))
            or (self.terminal_target is not None and not callable(self.terminal_target))
            or (self.validate_contract is not None and not callable(self.validate_contract))
        ):
            raise ValueError("delivery adapter requires a closed frozen payload model and sender")


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _invalid_constant(value):
    raise ValueError("non-finite delivery number")


def _digest(envelope, contract_hash, request_hash=None):
    value = {
        "envelope": envelope.model_dump(),
        "contract_hash": contract_hash,
        "request_hash": request_hash,
    }
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate delivery key")
        result[key] = value
    return result


def _reject_credential_fields(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(
                r"(?i)(password|passwd|(?:^|_)pwd(?:$|_)|secret|credential|authorization|"
                r"api.?key|(?:access|refresh|id|session|auth).?token|private.?key)",
                key,
            ):
                raise ContractConflict("credential fields cannot enter delivery data")
            _reject_credential_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_credential_fields(child)


class WorkDeliveries:
    def __init__(self, repository, adapters=None):
        if adapters is None:
            adapters = {}
        if not isinstance(adapters, Mapping) or any(
            not isinstance(key, tuple)
            or len(key) != 2
            or not isinstance(key[0], str)
            or not key[0]
            or type(key[1]) is not int
            or key[1] < 1
            or not isinstance(adapter, DeliveryAdapter)
            for key, adapter in adapters.items()
        ):
            raise ValueError("explicit versioned server delivery registry required")
        self.repository = repository
        self.adapters = MappingProxyType(dict(adapters))
        self.content = ImmutableResultStore(repository.delivery_content_root, max_bytes=65536)

    @staticmethod
    def envelope(envelope, operation_kind):
        """Canonical request identity independent of installed execution adapters."""
        try:
            envelope = WorkDeliveryEnvelope.model_validate(envelope)
            if envelope.operation_kind != operation_kind:
                raise ValueError("delivery operation mismatch")
            raw = json.loads(
                envelope.payload_json, object_pairs_hook=_pairs, parse_constant=_invalid_constant
            )
            if not isinstance(raw, dict):
                raise ValueError("object payload required")
            _nonsecret(raw)
            _reject_credential_fields(raw)
            encoded = _canonical(raw)
            if len(encoded.encode()) > 65536:
                raise ValueError("delivery payload too large")
            return WorkDeliveryEnvelope(
                operation_kind=operation_kind,
                adapter_version=envelope.adapter_version,
                payload_json=encoded,
            )
        except (ValidationError, TypeError, ValueError, RecursionError):
            raise ContractConflict("invalid delivery data") from None

    def prepare(self, envelope, operation_kind, *, contract=None):
        """Normalize through the registered schema; never echo rejected input."""
        try:
            envelope = self.envelope(envelope, operation_kind)
            adapter = self.adapters[(operation_kind, envelope.adapter_version)]
            payload = adapter.payload_model.model_validate_json(envelope.payload_json)
            # Model defaults and validators are also part of the persisted input.
            normalized = payload.model_dump(mode="json", by_alias=True)
            _nonsecret(normalized)
            _reject_credential_fields(normalized)
            encoded = _canonical(normalized)
            if len(encoded.encode()) > 65536:
                raise ValueError("delivery payload too large")
            recovered = adapter.payload_model.model_validate_json(encoded)
            if _canonical(recovered.model_dump(mode="json", by_alias=True)) != encoded:
                raise ValueError("delivery model must preserve its serialized input")
            if adapter.validate_contract is not None:
                if contract is None:
                    raise ValueError("delivery contract required")
                adapter.validate_contract(recovered, contract)
            return WorkDeliveryEnvelope(
                operation_kind=operation_kind,
                adapter_version=envelope.adapter_version,
                payload_json=encoded,
            )
        except (ValidationError, TypeError, ValueError, KeyError, RecursionError):
            raise ContractConflict("invalid or unsupported delivery data") from None

    def _load(self, connection, binding):
        row = connection.execute(
            "SELECT * FROM work_delivery_orders WHERE attempt_id=? AND generation=?",
            (binding.attempt_id, binding.generation),
        ).fetchone()
        if row is None:
            return None
        if (
            row["contract_hash"] != binding.contract_hash
            or row["operation_kind"] != binding.contract.operation_kind
        ):
            raise ContractConflict("delivery order integrity check failed")
        try:
            envelope = WorkDeliveryEnvelope(
                schema_version=row["schema_version"],
                operation_kind=row["operation_kind"],
                adapter_version=row["adapter_version"],
                payload_json=row["payload_json"],
            )
        except (ValidationError, ValueError):
            raise ContractConflict("stored delivery order is invalid") from None
        if _digest(envelope, binding.contract_hash, row["request_hash"]) != row["delivery_hash"]:
            raise ContractConflict("delivery order integrity check failed")
        return envelope

    @staticmethod
    def _opaque_envelope(envelope, reference):
        return WorkDeliveryEnvelope(
            operation_kind=envelope.operation_kind,
            adapter_version=envelope.adapter_version,
            payload_json=_canonical(
                {
                    "byte_length": reference.byte_length,
                    "content_hash": reference.content_hash,
                    "content_ref": reference.immutable_location,
                }
            ),
        )

    @staticmethod
    def _reference(envelope):
        try:
            raw = json.loads(
                envelope.payload_json, object_pairs_hook=_pairs, parse_constant=_invalid_constant
            )
            if set(raw) != {"byte_length", "content_hash", "content_ref"}:
                raise ValueError("opaque delivery reference fields required")
            return ArtifactRef(raw["content_hash"], raw["content_ref"], raw["byte_length"])
        except (TypeError, ValueError, RecursionError):
            raise ContractConflict("delivery content reference is invalid") from None

    def _restore(self, connection, binding):
        """Return only this binding's verified payload after order revalidation."""
        envelope = self._load(connection, binding)
        if (
            envelope is None
            or (envelope.operation_kind, envelope.adapter_version) not in self.adapters
        ):
            raise ContractConflict("delivery adapter is unavailable")
        reference = self._reference(envelope)
        row = connection.execute(
            "SELECT * FROM work_delivery_content_refs WHERE attempt_id=? AND generation=?",
            (binding.attempt_id, binding.generation),
        ).fetchone()
        if row is None or (
            row["owner_principal_id"],
            row["contract_hash"],
            row["snapshot_id"],
            row["content_ref"],
            row["content_hash"],
            row["byte_length"],
        ) != (
            binding.principal_id,
            binding.contract_hash,
            binding.contract.snapshot.id,
            reference.immutable_location,
            reference.content_hash,
            reference.byte_length,
        ):
            raise ContractConflict("delivery content reference is not bound to this operation")
        try:
            payload_json = self.content.read(reference).decode("utf-8")
            restored = WorkDeliveryEnvelope(
                operation_kind=envelope.operation_kind,
                adapter_version=envelope.adapter_version,
                payload_json=payload_json,
            )
            normalized = self.prepare(
                restored, binding.contract.operation_kind, contract=binding.contract
            )
            if normalized != restored:
                raise ContractConflict("delivery adapter changed without a version change")
            adapter = self.adapters[(envelope.operation_kind, envelope.adapter_version)]
            payload = adapter.payload_model.model_validate_json(normalized.payload_json)
            return adapter, payload
        except ContractConflict:
            raise
        except (OSError, UnicodeError, ValidationError, ValueError, RecursionError):
            raise ContractConflict("delivery content is unavailable or invalid") from None

    def _compare(self, connection, binding, envelope):
        existing = self._load(connection, binding)
        if existing is None and envelope is None:
            return
        row = connection.execute(
            "SELECT request_hash FROM work_delivery_orders WHERE attempt_id=? AND generation=?",
            (binding.attempt_id, binding.generation),
        ).fetchone()
        if (
            existing is None
            or envelope is None
            or row["request_hash"] != _digest(envelope, binding.contract_hash)
        ):
            raise ContractConflict("delivery data differs from the admitted operation")

    def _bind(self, connection, binding, envelope, *, request):
        if envelope is None:
            return
        if self._load(connection, binding) is not None:
            self._compare(connection, binding, request)
            return
        request_hash = _digest(request, binding.contract_hash)
        try:
            def accept(reference):
                opaque = self._opaque_envelope(envelope, reference)
                digest = _digest(opaque, binding.contract_hash, request_hash)
                connection.execute(
                    "INSERT INTO work_delivery_orders VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        binding.attempt_id,
                        binding.generation,
                        opaque.operation_kind,
                        opaque.schema_version,
                        opaque.adapter_version,
                        binding.contract_hash,
                        opaque.payload_json,
                        request_hash,
                        digest,
                    ),
                )
                connection.execute(
                    "INSERT INTO work_delivery_content_refs VALUES (?,?,?,?,?,?,?,?)",
                    (
                        binding.attempt_id,
                        binding.generation,
                        binding.principal_id,
                        binding.contract_hash,
                        binding.contract.snapshot.id,
                        reference.immutable_location,
                        reference.content_hash,
                        reference.byte_length,
                    ),
                )
                self.repository._append_event(
                    connection,
                    job_id=binding.job_id,
                    work_item_id=binding.work_item_id,
                    attempt_id=binding.attempt_id,
                    actor_id=binding.principal_id,
                    event_type="delivery.bound",
                    metadata={"adapter_version": opaque.adapter_version, "delivery_hash": digest},
                )

            self.content.publish(envelope.payload_json.encode("utf-8"), accept)
        except ContractConflict:
            raise
        except (OSError, ValueError, RecursionError):
            raise ContractConflict("delivery content could not be persisted") from None

    def _executable(self, connection, binding):
        try:
            self._restore(connection, binding)
            return True
        except ContractConflict:
            raise

    def _terminal_identity(self, connection, binding):
        adapter, payload = self._restore(connection, binding)
        if adapter.terminal_identity is None:
            return None
        return adapter.terminal_identity(payload)

    def _terminal_target(self, connection, binding):
        """Return the sealed Work terminal/session/window tuple for a new launch."""
        adapter, payload = self._restore(connection, binding)
        if adapter.terminal_target is None:
            return None
        target = adapter.terminal_target(payload)
        if (
            not isinstance(target, tuple)
            or len(target) != 3
            or any(not isinstance(value, str) or not value for value in target)
        ):
            raise ContractConflict("delivery target identity is invalid")
        if adapter.terminal_identity is not None and target[0] != adapter.terminal_identity(payload):
            raise ContractConflict("delivery target terminal differs from its identity")
        return target
