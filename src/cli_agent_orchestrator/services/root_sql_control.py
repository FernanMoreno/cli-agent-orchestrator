"""Explicit deployment issuance and the single original root control channel.

This composes existing funded storage scopes; it does not bootstrap a deployment
issuer, activate shared admission, or certify retirement of its durable credit.
"""

import os
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget, connect
from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository
from cli_agent_orchestrator.clients.storage_admission import check_storage_admission
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.sql_root_genesis import (
    RootSqlOpeningIntent,
    SignedRootSqlOpening,
)
from cli_agent_orchestrator.models.sql_startup_permission import (
    _approve_verified_sql_startup_permission,
    _expire_verified_sql_startup_permission,
    _issue_verified_sql_startup_permission,
    sql_startup_destination_sha256,
)
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.delivery_admission import (
    DeliveryAdmission,
    StorageControlPermit,
)
from cli_agent_orchestrator.services.resource_process_supervisor import _identity, _matches
from cli_agent_orchestrator.services.sql_root_genesis import (
    RootGenesisUnavailable,
    sign_root_opening,
    verify_root_opening,
)
from cli_agent_orchestrator.utils.process_sql_origin import (
    _check_root_sql_startup_pending,
    _install_root_sql_startup_permission,
    _recording_task,
    current_sql_application_name,
)


@dataclass(frozen=True)
class RootSqlSubject:
    """Descriptive request, never permission to open SQL or issue a certificate."""

    binding: ResourceBinding
    sql_application_name: str
    boot_id: str
    start_time_ticks: int
    pid_namespace: tuple[int, int]


class RootCertificateUnavailableAfterCommit(RootGenesisUnavailable):
    """Retain the confirmed record when trusted signing cannot be completed."""

    def __init__(self, record: RootSqlOpeningIntent):
        super().__init__()
        self.record = record
        self.journal_id = record.journal_id
        self.intent_id = record.intent_id


def current_root_sql_subject(bootstrap: ResourceBootstrap) -> RootSqlSubject:
    _check_root_sql_startup_pending()
    repository = bootstrap.shared_repository()
    binding = repository.current_binding()
    live = _identity(os.getpid())
    if binding.process_pid != os.getpid() or not _matches(binding, live):
        raise RootGenesisUnavailable()
    return RootSqlSubject(
        binding, current_sql_application_name(), live.boot_id, live.ticks, live.pid_ns
    )


def issue_root_sql_certificate(
    bootstrap: ResourceBootstrap,
    channel: Connection,
    *,
    principal: object,
    journal_id: str,
    intent_id: str,
    session_id: str,
    subject: RootSqlSubject,
    key_supplier: Callable[[], tuple[str, Ed25519PrivateKey]],
) -> SignedRootSqlOpening:
    actor = bootstrap._administrator(principal)  # Before target, key, or SQL I/O.
    if type(subject) is not RootSqlSubject or not callable(key_supplier):
        raise RootGenesisUnavailable()
    identity = bootstrap.shared_repository()
    repository = SqlIntentRepository(identity.target, binding_supplier=identity.current_binding)
    capability = repository.record_root_opening(
        channel,
        journal_id=journal_id,
        intent_id=intent_id,
        session_id=session_id,
        subject=subject.binding,
        subject_sql_application_name=subject.sql_application_name,
        boot_id=subject.boot_id,
        start_time_ticks=subject.start_time_ticks,
        pid_namespace=subject.pid_namespace,
        destination=identity.target,
        principal_id=actor,
    )
    try:
        key_id, private_key = key_supplier()
        return sign_root_opening(capability, key_id=key_id, private_key=private_key)
    except Exception:
        raise RootCertificateUnavailableAfterCommit(capability.record) from None


@contextmanager
def root_sql_control_channel(
    bootstrap: ResourceBootstrap,
    owner: DeliveryAdmission,
    control: StorageControlPermit,
    certificate: SignedRootSqlOpening,
    *,
    trusted_keys: Mapping[str, Ed25519PublicKey],
    clock: Callable[[], float],
) -> Iterator[Connection]:
    """Verify the real root before its first TCP, then read back on that channel."""
    _check_root_sql_startup_pending()
    record = verify_root_opening(certificate, trusted_keys=trusted_keys)
    if (
        type(owner) is not DeliveryAdmission
        or type(control) is not StorageControlPermit
        or not control.belongs_to(owner)
    ):
        raise RootGenesisUnavailable()
    identity = bootstrap.shared_repository()
    target = identity.target
    pid, thread, task = os.getpid(), threading.current_thread(), _recording_task()
    live = _identity(pid)
    active = True

    def validate_process() -> None:
        if (
            not active
            or pid != os.getpid()
            or thread is not threading.current_thread()
            or task is not _recording_task()
            or identity.current_binding() != record.subject
            or record.subject.process_pid != pid
            or _identity(pid) != live
            or not _matches(record.subject, live)
            or (live.boot_id, live.ticks, live.pid_ns)
            != (record.boot_id, record.start_time_ticks, record.pid_namespace)
            or current_sql_application_name() != record.subject_sql_application_name
            or type(target) is not DatabaseTarget
            or target.scope != "main"
            or not bootstrap.matches_primary_target(target)
            or sql_startup_destination_sha256(target) != record.destination_sha256
        ):
            raise RootGenesisUnavailable()
        check_storage_admission()

    validate_process()
    repository = SqlIntentRepository(target, binding_supplier=identity.current_binding)
    try:
        with control.operation(clock=clock):
            validate_process()
            permission = _issue_verified_sql_startup_permission(
                record.startup_permission(),
                record.subject,
                validate=validate_process,
                validate_process=validate_process,
            )
            try:
                _approve_verified_sql_startup_permission(permission)
                _install_root_sql_startup_permission(permission)
            finally:
                _expire_verified_sql_startup_permission(permission)
            with connect(target) as channel:
                channel._check_funded_worker()
                validate_process()
                retained = repository.read_root_opening(
                    channel,
                    journal_id=record.journal_id,
                    intent_id=record.intent_id,
                )
                if retained != record:
                    raise RootGenesisUnavailable()
                validate_process()
                yield channel
    finally:
        active = False
