"""Structured startup permission data and ephemeral authenticated acceptance.

A record is descriptive. Only trusted kernel transport approves a live issuer;
this is not an in-process sandbox against arbitrary private-module mutation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from enum import Enum
from uuid import UUID
from weakref import WeakKeyDictionary

from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.sql_process_origin import is_sql_process_application_name

SQL_STARTUP_PERMISSION_ENV = "CAO_INTERNAL_SQL_STARTUP_PERMISSION"
SQL_STARTUP_PERMISSION_VERSION = 3
_FACTORY = object()
_ISSUED: WeakKeyDictionary[VerifiedSqlStartupPermission, object] = WeakKeyDictionary()


def sql_startup_binding_sha256(binding: ResourceBinding) -> str:
    if type(binding) is not ResourceBinding:
        raise ValueError("startup permission requires a typed binding")
    return hashlib.sha256(
        json.dumps(asdict(binding), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def sql_startup_target_identity_sha256(destination: object) -> str:
    identity = getattr(destination, "identity", None)
    if type(identity) is not tuple or len(identity) != 5:
        raise ValueError("startup permission requires an explicit destination")
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def sql_startup_destination_sha256(destination: object) -> str:
    sql_startup_target_identity_sha256(destination)
    user, sslmode = getattr(destination, "user", None), getattr(destination, "sslmode", None)
    if type(user) is not str or type(sslmode) is not str:
        raise ValueError("startup permission requires an explicit destination role")
    return hashlib.sha256(
        json.dumps(
            (getattr(destination, "identity"), user, sslmode), separators=(",", ":")
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class SqlStartupPermissionRecord:
    session_id: str
    journal_id: str
    intent_id: str
    subject_binding_sha256: str
    sql_application_name: str
    path: str
    destination_sha256: str
    destination_scope: str
    slots: int = 1

    def __post_init__(self) -> None:
        for name in ("session_id", "journal_id", "intent_id"):
            value = getattr(self, name)
            if type(value) is not str or str(UUID(value)) != value:
                raise ValueError("startup permission requires canonical identities")
        for name in ("subject_binding_sha256", "destination_sha256"):
            value = getattr(self, name)
            if type(value) is not str or re.fullmatch("[0-9a-f]{64}", value) is None:
                raise ValueError("startup permission requires canonical digests")
        if (
            not is_sql_process_application_name(self.sql_application_name)
            or type(self.path) is not str
            or self.path not in {"native", "orm", "audit"}
            or type(self.destination_scope) is not str
            or not 1 <= len(self.destination_scope) <= 128
            or type(self.slots) is not int
            or self.slots != 1
        ):
            raise ValueError("startup permission has invalid bounded quantities or scope")

    def to_packet(self) -> dict[str, object]:
        return {"version": SQL_STARTUP_PERMISSION_VERSION, "kind": "sql_startup", **asdict(self)}

    @classmethod
    def from_packet(cls, packet: object) -> SqlStartupPermissionRecord:
        if (
            type(packet) is not dict
            or set(packet) != {"version", "kind", *(f.name for f in fields(cls))}
            or type(packet["version"]) is not int
            or packet["version"] != SQL_STARTUP_PERMISSION_VERSION
            or packet["kind"] != "sql_startup"
        ):
            raise ValueError("startup permission packet schema is invalid")
        return cls(**{f.name: packet[f.name] for f in fields(cls)})


@dataclass(frozen=True, eq=False, init=False, repr=False)
class VerifiedSqlStartupPermission:
    record: SqlStartupPermissionRecord
    binding: ResourceBinding
    validate_process: Callable[[], None]
    _validate: Callable[[], None]
    _pid: int
    _thread: threading.Thread
    _token: object
    _active: bool
    _claimed: bool
    _factory: object

    def __init__(self):
        raise TypeError("startup permission requires an authenticated live issuer")

    def __copy__(self):
        raise TypeError("startup permission is not transferable")

    def __deepcopy__(self, memo):
        raise TypeError("startup permission is not transferable")

    def __reduce_ex__(self, protocol):
        raise TypeError("startup permission is not serializable")

    def check(self) -> None:
        if (
            type(self) is not VerifiedSqlStartupPermission
            or self._factory is not _FACTORY
            or self._pid != os.getpid()
            or self._thread is not threading.current_thread()
            or not self._active
            or _ISSUED.get(self) is not self._token
        ):
            raise ValueError("startup permission issuer is not live on this worker")
        self._validate()

    def claim(self) -> None:
        self.check()
        if self._claimed:
            raise ValueError("startup permission acceptance is single use")
        object.__setattr__(self, "_claimed", True)


def _issue_verified_sql_startup_permission(
    record: SqlStartupPermissionRecord,
    binding: ResourceBinding,
    *,
    validate: Callable[[], None],
    validate_process: Callable[[], None],
) -> VerifiedSqlStartupPermission:
    if (
        type(record) is not SqlStartupPermissionRecord
        or type(binding) is not ResourceBinding
        or record.subject_binding_sha256 != sql_startup_binding_sha256(binding)
        or binding.process_pid != os.getpid()
        or not callable(validate)
        or not callable(validate_process)
    ):
        raise ValueError("startup permission requires its original verified subject")
    candidate = object.__new__(VerifiedSqlStartupPermission)
    for name, value in (
        ("record", record),
        ("binding", binding),
        ("validate_process", validate_process),
        ("_validate", validate),
        ("_pid", os.getpid()),
        ("_thread", threading.current_thread()),
        ("_token", object()),
        ("_active", True),
        ("_claimed", False),
        ("_factory", _FACTORY),
    ):
        object.__setattr__(candidate, name, value)
    return candidate


def _approve_verified_sql_startup_permission(value: VerifiedSqlStartupPermission) -> None:
    if (
        type(value) is not VerifiedSqlStartupPermission
        or value._factory is not _FACTORY
        or value._pid != os.getpid()
        or value._thread is not threading.current_thread()
        or not value._active
    ):
        raise ValueError("startup permission issuer is unavailable")
    value._validate()
    _ISSUED[value] = value._token
    value.check()


def _expire_verified_sql_startup_permission(value: VerifiedSqlStartupPermission) -> None:
    object.__setattr__(value, "_active", False)
    _ISSUED.pop(value, None)


SQL_BROKER_FD_ENV = "CAO_INTERNAL_SQL_BROKER_FD"
SQL_ROOT_GENESIS_ENV = "CAO_INTERNAL_ROOT_SQL_GENESIS"


@dataclass(frozen=True)
class SqlBrokerRequest:
    journal_id: str
    startup_intent_id: str
    intent_id: str
    sequence: int
    subject_binding_sha256: str
    sql_application_name: str
    path: str
    destination_sha256: str
    destination_scope: str

    def __post_init__(self) -> None:
        # Share the bounded destination/identity validation, without issuing a
        # capability or pretending this request already has a paid session.
        for value in (self.journal_id, self.startup_intent_id, self.intent_id):
            if type(value) is not str or str(UUID(value)) != value:
                raise ValueError("broker request requires canonical identities")
        if type(self.sequence) is not int or not 1 <= self.sequence < 2**63:
            raise ValueError("broker request requires a positive bounded sequence")
        if (
            not is_sql_process_application_name(self.sql_application_name)
            or type(self.path) is not str
            or self.path not in {"native", "orm", "audit"}
            or type(self.destination_scope) is not str
            or not 1 <= len(self.destination_scope) <= 128
            or any(
                type(v) is not str or re.fullmatch("[0-9a-f]{64}", v) is None
                for v in (self.subject_binding_sha256, self.destination_sha256)
            )
        ):
            raise ValueError("broker request has an invalid bounded destination")

    def to_packet(self) -> dict[str, object]:
        return dict(version=4, kind="sql_opening_request", **asdict(self))

    @classmethod
    def from_packet(cls, packet: object) -> SqlBrokerRequest:
        if (
            type(packet) is not dict
            or set(packet) != {"version", "kind", *(f.name for f in fields(cls))}
            or type(packet["version"]) is not int
            or packet["version"] != 4
            or packet["kind"] != "sql_opening_request"
        ):
            raise ValueError("broker request schema is invalid")
        return cls(**{f.name: packet[f.name] for f in fields(cls)})


@dataclass(frozen=True, kw_only=True)
class SqlBrokerPermissionRecord(SqlStartupPermissionRecord):
    startup_intent_id: str
    sequence: int

    def __post_init__(self) -> None:
        super().__post_init__()
        if (
            type(self.startup_intent_id) is not str
            or str(UUID(self.startup_intent_id)) != self.startup_intent_id
            or type(self.sequence) is not int
            or not 1 <= self.sequence < 2**63
        ):
            raise ValueError("broker permission requires an original anchor and sequence")

    def to_packet(self) -> dict[str, object]:
        return dict(version=4, kind="sql_opening", **asdict(self))

    @classmethod
    def from_packet(cls, packet: object) -> SqlBrokerPermissionRecord:
        if (
            type(packet) is not dict
            or set(packet) != {"version", "kind", *(f.name for f in fields(cls))}
            or type(packet["version"]) is not int
            or packet["version"] != 4
            or packet["kind"] != "sql_opening"
        ):
            raise ValueError("broker permission schema is invalid")
        return cls(**{f.name: packet[f.name] for f in fields(cls)})


class SqlBrokerIdle(Enum):
    """No request was received; this is distinct from terminal transport EOF."""

    NO_REQUEST = "no_request"
