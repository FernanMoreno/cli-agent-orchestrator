"""Scoped positive child identity; no TCP, budget or retirement authority."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Callable
from uuid import UUID
from weakref import WeakKeyDictionary

from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.sql_process_origin import is_sql_process_application_name

_FACTORY = object()
_ISSUED: WeakKeyDictionary[VerifiedChildSqlSubject, object] = WeakKeyDictionary()


@dataclass(frozen=True, eq=False, init=False, repr=False)
class VerifiedChildSqlSubject:
    binding: ResourceBinding
    sql_application_name: str
    boot_id: str
    start_time_ticks: int
    pid_namespace: tuple[int, int]
    _issuer_pid: int
    _issuer_thread: threading.Thread
    _validate: Callable[[VerifiedChildSqlSubject], None]
    _handshake_token: object
    _active: bool
    _claimed: bool
    _session_claimed: bool
    _factory: object

    def __init__(self):
        raise TypeError("child subject requires an owned authenticated handshake")

    def __copy__(self):
        raise TypeError("child subject is not transferable")

    def __deepcopy__(self, memo):
        raise TypeError("child subject is not transferable")

    def __reduce_ex__(self, protocol):
        raise TypeError("child subject is not serializable")

    def check(self) -> None:
        # PID/thread checks precede any callback or possibly inherited lock.
        if (
            type(self) is not VerifiedChildSqlSubject
            or getattr(self, "_factory", None) is not _FACTORY
            or self._issuer_pid != os.getpid()
            or self._issuer_thread is not threading.current_thread()
            or not self._active
        ):
            raise ValueError("child subject is outside its original live handshake")
        if _ISSUED.get(self) is not self._handshake_token:
            raise ValueError("child subject was not issued by its active supervisor")
        self._validate(self)

    def claim(self) -> None:
        self.check()
        if self._claimed:
            raise ValueError("child subject registration is single use")
        object.__setattr__(self, "_claimed", True)

    def claim_session(self) -> None:
        """Separate one-shot parent debit; never a grant to the child driver."""
        self.check()
        if self._session_claimed:
            raise ValueError("child session prepayment is single use")
        object.__setattr__(self, "_session_claimed", True)


def _issue_verified_child_sql_subject(
    binding: ResourceBinding,
    *,
    sql_application_name: str,
    boot_id: str,
    start_time_ticks: int,
    pid_namespace: tuple[int, int],
    validate: Callable[[VerifiedChildSqlSubject], None],
    handshake_token: object | None = None,
) -> VerifiedChildSqlSubject:
    """Build an inert candidate; live issuer approval is required before use."""
    if (
        type(binding) is not ResourceBinding
        or not is_sql_process_application_name(sql_application_name)
        or str(UUID(boot_id)) != boot_id
        or type(start_time_ticks) is not int
        or start_time_ticks < 1
        or type(pid_namespace) is not tuple
        or len(pid_namespace) != 2
        or any(type(x) is not int or x < 0 for x in pid_namespace)
        or not callable(validate)
    ):
        raise ValueError("child subject requires validated kernel evidence")
    value = object.__new__(VerifiedChildSqlSubject)
    for name, field in (
        ("binding", binding),
        ("sql_application_name", sql_application_name),
        ("boot_id", boot_id),
        ("start_time_ticks", start_time_ticks),
        ("pid_namespace", pid_namespace),
        ("_issuer_pid", os.getpid()),
        ("_issuer_thread", threading.current_thread()),
        ("_validate", validate),
        ("_handshake_token", handshake_token if handshake_token is not None else object()),
        ("_active", True),
        ("_claimed", False),
        ("_session_claimed", False),
        ("_factory", _FACTORY),
    ):
        object.__setattr__(value, name, field)
    return value


def _approve_verified_child_sql_subject(value: VerifiedChildSqlSubject) -> None:
    """Trusted issuer step, after adding this candidate to its live handshake."""
    if (
        type(value) is not VerifiedChildSqlSubject
        or value._factory is not _FACTORY
        or value._issuer_pid != os.getpid()
        or value._issuer_thread is not threading.current_thread()
        or not value._active
    ):
        raise ValueError("child subject issuer is unavailable")
    value._validate(value)
    _ISSUED[value] = value._handshake_token
    value.check()


def _expire_verified_child_sql_subject(value: VerifiedChildSqlSubject) -> None:
    object.__setattr__(value, "_active", False)
    _ISSUED.pop(value, None)
