"""Descriptive root SQL opening records and a confirmed-commit signing cap.

Records and certificates carry data only.  A signing cap can be issued only by
the SQL repository after it has observed the original transaction commit.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from uuid import UUID
from weakref import WeakKeyDictionary

from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.sql_process_origin import is_sql_process_application_name
from cli_agent_orchestrator.models.sql_startup_permission import (
    SqlStartupPermissionRecord,
    sql_startup_binding_sha256,
)

ROOT_SQL_OPENING_VERSION = 1
ROOT_SQL_OPENING_KIND = "root_sql_opening"
ROOT_SQL_CERTIFICATE_VERSION = 1
ROOT_SQL_CERTIFICATE_KIND = "root_sql_certificate"
ROOT_SQL_CERTIFICATE_MAX_BYTES = 16 * 1024

_DIGEST = re.compile(r"[0-9a-f]{64}")
_PRINCIPAL = re.compile(r"principal-[0-9a-f]{64}")
_KEY_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_SIGNATURE_TEXT = re.compile(r"[A-Za-z0-9_-]{86}")


def _canonical_uuid(value: object, *, field: str) -> str:
    if type(value) is not str:
        raise ValueError(f"root SQL opening requires a canonical {field}")
    try:
        canonical = str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"root SQL opening requires a canonical {field}") from exc
    if value != canonical:
        raise ValueError(f"root SQL opening requires a canonical {field}")
    return value


def _digest(value: object, *, field: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"root SQL opening requires a canonical {field}")
    return value


def _bounded_positive_integer(value: object, *, field: str) -> int:
    if type(value) is not int or not 1 <= value < 2**63:
        raise ValueError(f"root SQL opening requires a bounded positive {field}")
    return value


def _signature_bytes(value: object) -> bytes:
    if type(value) is not str or _SIGNATURE_TEXT.fullmatch(value) is None:
        raise ValueError("root SQL certificate requires a canonical Ed25519 signature")
    try:
        raw = base64.b64decode(value.encode("ascii") + b"==", altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise ValueError("root SQL certificate requires a canonical Ed25519 signature") from exc
    if len(raw) != 64 or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value:
        raise ValueError("root SQL certificate requires a canonical Ed25519 signature")
    return raw


@dataclass(frozen=True, kw_only=True)
class RootSqlOpeningIntent:
    """Exact descriptive identity of the first root SQL opening."""

    intent_id: str
    journal_id: str
    ordinal: int
    credit_id: str
    credit_ids: tuple[str, ...]
    membership_sha256: str
    session_id: str
    subject: ResourceBinding
    subject_binding_sha256: str
    subject_sql_application_name: str
    boot_id: str
    start_time_ticks: int
    pid_namespace: tuple[int, int]
    path: str
    destination_sha256: str
    destination_scope: str
    issuer_principal_id: str
    slots: int = 1

    def __post_init__(self) -> None:
        for name in ("intent_id", "journal_id", "credit_id", "session_id", "boot_id"):
            _canonical_uuid(getattr(self, name), field=name)

        if type(self.credit_ids) is not tuple or not self.credit_ids:
            raise ValueError("root SQL opening requires a non-empty credit tuple")
        for credit_id in self.credit_ids:
            _canonical_uuid(credit_id, field="credit ID")
        if (
            tuple(sorted(set(self.credit_ids))) != self.credit_ids
            or self.credit_id not in self.credit_ids
        ):
            raise ValueError(
                "root SQL opening credits must be sorted, unique and include credit_id"
            )

        if type(self.ordinal) is not int or self.ordinal != 1:
            raise ValueError("root SQL opening must have ordinal one")
        if type(self.slots) is not int or self.slots != 1:
            raise ValueError("root SQL opening must reserve exactly one slot")

        _digest(self.membership_sha256, field="membership digest")
        _digest(self.subject_binding_sha256, field="subject binding digest")
        _digest(self.destination_sha256, field="destination digest")
        if type(self.subject) is not ResourceBinding:
            raise ValueError("root SQL opening requires a validated resource binding")
        if self.subject_binding_sha256 != sql_startup_binding_sha256(self.subject):
            raise ValueError("root SQL opening subject binding digest does not match")
        if not is_sql_process_application_name(self.subject_sql_application_name):
            raise ValueError("root SQL opening requires a process SQL label")

        _bounded_positive_integer(self.start_time_ticks, field="start time")
        if (
            type(self.pid_namespace) is not tuple
            or len(self.pid_namespace) != 2
            or any(type(part) is not int or not 1 <= part < 2**63 for part in self.pid_namespace)
        ):
            raise ValueError("root SQL opening requires bounded positive pid namespace identity")
        if type(self.path) is not str or self.path != "native":
            raise ValueError("root SQL opening path must be native")
        if type(self.destination_scope) is not str or self.destination_scope != "main":
            raise ValueError("root SQL opening destination scope must be main")
        if (
            type(self.issuer_principal_id) is not str
            or _PRINCIPAL.fullmatch(self.issuer_principal_id) is None
        ):
            raise ValueError("root SQL opening requires a canonical issuer principal")

    def to_packet(self) -> dict[str, object]:
        packet: dict[str, object] = {
            "version": ROOT_SQL_OPENING_VERSION,
            "kind": ROOT_SQL_OPENING_KIND,
            **asdict(self),
        }
        # JSON arrays reconstruct as lists.  Keep the packet vocabulary exact
        # and translate those wire arrays back to immutable tuples on read.
        packet["credit_ids"] = list(self.credit_ids)
        packet["pid_namespace"] = list(self.pid_namespace)
        return packet

    @classmethod
    def from_packet(cls, packet: object) -> RootSqlOpeningIntent:
        expected = {"version", "kind", *(field.name for field in fields(cls))}
        binding_fields = {field.name for field in fields(ResourceBinding)}
        if (
            type(packet) is not dict
            or set(packet) != expected
            or type(packet.get("version")) is not int
            or packet["version"] != ROOT_SQL_OPENING_VERSION
            or type(packet.get("kind")) is not str
            or packet["kind"] != ROOT_SQL_OPENING_KIND
            or type(packet.get("subject")) is not dict
            or set(packet["subject"]) != binding_fields
            or type(packet.get("credit_ids")) is not list
            or type(packet.get("pid_namespace")) is not list
        ):
            raise ValueError("root SQL opening packet schema is invalid")
        try:
            binding = ResourceBinding(**packet["subject"])
            if asdict(binding) != packet["subject"]:
                raise ValueError("root SQL opening binding is not canonical")
            values = {field.name: packet[field.name] for field in fields(cls)}
            values["subject"] = binding
            values["credit_ids"] = tuple(packet["credit_ids"])
            values["pid_namespace"] = tuple(packet["pid_namespace"])
            return cls(**values)
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError("root SQL opening packet schema is invalid") from exc

    def startup_permission(self) -> SqlStartupPermissionRecord:
        """Return descriptive SQL permission data; it grants no opening."""
        return SqlStartupPermissionRecord(
            session_id=self.session_id,
            journal_id=self.journal_id,
            intent_id=self.intent_id,
            subject_binding_sha256=self.subject_binding_sha256,
            sql_application_name=self.subject_sql_application_name,
            path=self.path,
            destination_sha256=self.destination_sha256,
            destination_scope=self.destination_scope,
            slots=self.slots,
        )


_CAP_FACTORY = object()
_CONFIRMED_CAPS: WeakKeyDictionary[ConfirmedRootSqlOpening, object] = WeakKeyDictionary()


@dataclass(frozen=True, eq=False, init=False, repr=False)
class ConfirmedRootSqlOpening:
    """Non-transferable signer capability tied to the live original issuer."""

    record: RootSqlOpeningIntent
    _validate: Callable[[], None]
    _issuer_pid: int
    _issuer_thread: threading.Thread
    _token: object
    _active: bool
    _claimed: bool
    _factory: object

    def __init__(self) -> None:
        raise TypeError("confirmed root SQL opening requires a committed live issuer")

    def __copy__(self):
        raise TypeError("confirmed root SQL opening is not transferable")

    def __deepcopy__(self, memo):
        raise TypeError("confirmed root SQL opening is not transferable")

    def __reduce_ex__(self, protocol):
        raise TypeError("confirmed root SQL opening is not serializable")

    def check(self) -> None:
        if (
            type(self) is not ConfirmedRootSqlOpening
            or getattr(self, "_factory", None) is not _CAP_FACTORY
            or getattr(self, "_issuer_pid", None) != os.getpid()
            or getattr(self, "_issuer_thread", None) is not threading.current_thread()
            or not getattr(self, "_active", False)
            or _CONFIRMED_CAPS.get(self) is not getattr(self, "_token", None)
        ):
            raise ValueError("root SQL opening issuer is not live on its original worker")
        self._validate()

    def claim_sign(self) -> None:
        """Consume the only signing attempt; failures never make it reusable."""
        self.check()
        if self._claimed:
            raise ValueError("root SQL opening signing is single use")
        object.__setattr__(self, "_claimed", True)


def _issue_confirmed_root_sql_opening(
    record: RootSqlOpeningIntent,
    *,
    validate: Callable[[], None],
) -> ConfirmedRootSqlOpening:
    """Issue only after the original repository transaction confirms commit."""
    if type(record) is not RootSqlOpeningIntent or not callable(validate):
        raise ValueError("confirmed root SQL opening requires a validated committed record")
    value = object.__new__(ConfirmedRootSqlOpening)
    token = object()
    for name, field_value in (
        ("record", record),
        ("_validate", validate),
        ("_issuer_pid", os.getpid()),
        ("_issuer_thread", threading.current_thread()),
        ("_token", token),
        ("_active", True),
        ("_claimed", False),
        ("_factory", _CAP_FACTORY),
    ):
        object.__setattr__(value, name, field_value)
    _CONFIRMED_CAPS[value] = token
    try:
        value.check()
    except BaseException:
        _CONFIRMED_CAPS.pop(value, None)
        object.__setattr__(value, "_active", False)
        raise
    return value


def _canonical_certificate_json(packet: dict[str, object]) -> bytes:
    return json.dumps(
        packet,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


@dataclass(frozen=True)
class SignedRootSqlOpening:
    """Canonical signed certificate; key_id references external trusted keys."""

    record: RootSqlOpeningIntent
    key_id: str
    signature: str

    def __post_init__(self) -> None:
        if type(self.record) is not RootSqlOpeningIntent:
            raise ValueError("root SQL certificate requires a validated opening record")
        if type(self.key_id) is not str or _KEY_ID.fullmatch(self.key_id) is None:
            raise ValueError("root SQL certificate requires a bounded ASCII key ID")
        _signature_bytes(self.signature)

    def to_packet(self) -> dict[str, object]:
        return {
            "version": ROOT_SQL_CERTIFICATE_VERSION,
            "kind": ROOT_SQL_CERTIFICATE_KIND,
            "record": self.record.to_packet(),
            "key_id": self.key_id,
            "signature": self.signature,
        }

    def to_bytes(self) -> bytes:
        encoded = _canonical_certificate_json(self.to_packet())
        if len(encoded) > ROOT_SQL_CERTIFICATE_MAX_BYTES:
            raise ValueError("root SQL certificate exceeds its size bound")
        return encoded

    @classmethod
    def from_bytes(cls, value: object) -> SignedRootSqlOpening:
        if type(value) is not bytes or not value or len(value) > ROOT_SQL_CERTIFICATE_MAX_BYTES:
            raise ValueError("root SQL certificate byte length is invalid")

        def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError("root SQL certificate contains a duplicate key")
                result[key] = item
            return result

        try:
            packet = json.loads(
                value.decode("utf-8"),
                object_pairs_hook=reject_duplicate_keys,
                parse_constant=lambda token: (_ for _ in ()).throw(
                    ValueError(f"invalid JSON constant: {token}")
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
            raise ValueError("root SQL certificate JSON is invalid") from exc
        if (
            type(packet) is not dict
            or set(packet) != {"version", "kind", "record", "key_id", "signature"}
            or type(packet.get("version")) is not int
            or packet["version"] != ROOT_SQL_CERTIFICATE_VERSION
            or type(packet.get("kind")) is not str
            or packet["kind"] != ROOT_SQL_CERTIFICATE_KIND
        ):
            raise ValueError("root SQL certificate schema is invalid")
        certificate = cls(
            record=RootSqlOpeningIntent.from_packet(packet["record"]),
            key_id=packet["key_id"],
            signature=packet["signature"],
        )
        if certificate.to_bytes() != value:
            raise ValueError("root SQL certificate JSON is not canonical")
        return certificate
