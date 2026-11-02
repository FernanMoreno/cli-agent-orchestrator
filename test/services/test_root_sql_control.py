"""G3 root SQL control negatives; these do not prove a real first TCP."""

from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.util
import json
import os
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cli_agent_orchestrator.clients import sql_intent_repository as intent_repository_module
from cli_agent_orchestrator.clients.admission_bootstrap_repository import (
    ResourceEnvelopeRepository,
)
from cli_agent_orchestrator.clients.postgres import DatabaseTarget
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.sql_root_genesis import (
    RootSqlOpeningIntent,
    SignedRootSqlOpening,
    _issue_confirmed_root_sql_opening,
)
from cli_agent_orchestrator.models.sql_startup_permission import sql_startup_binding_sha256
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.services.resource_process_supervisor import (
    _identity,
    _KernelIdentity,
)
from cli_agent_orchestrator.utils import process_sql_origin as origin

pytestmark = pytest.mark.offline_database

ROOT_MODE = "CAO_INTERNAL_ROOT_SQL_GENESIS"
CHILD_MODE = "CAO_INTERNAL_SQL_STARTUP_PERMISSION"
BROKER_FD = "CAO_INTERNAL_SQL_BROKER_FD"
BINDING_FD = "CAO_INTERNAL_RESOURCE_BINDING_FD"


def api():
    name = "cli_agent_orchestrator.services.root_sql_control"
    assert importlib.util.find_spec(name) is not None, "missing root SQL control service"
    return importlib.import_module(name)


def _root_mode(monkeypatch, *, enabled: bool = True) -> None:
    for name in (ROOT_MODE, CHILD_MODE, BROKER_FD, BINDING_FD):
        monkeypatch.delenv(name, raising=False)
    if enabled:
        monkeypatch.setenv(ROOT_MODE, "1")
    monkeypatch.setattr(origin, "_STATE", origin._State(os.getpid()))


def _target() -> DatabaseTarget:
    return DatabaseTarget(
        host="127.0.0.1",
        port=5432,
        database="root-control-test",
        user="runtime",
        schema="cao_root_control_test",
        store_id=str(uuid4()),
        secret_file=Path("/root-control-test/unused-secret.json"),
        sslmode="disable",
    )


def _kernel_binding(
    target: DatabaseTarget,
    *,
    resource_view_sha256: str | None = None,
) -> tuple[_KernelIdentity, ResourceBinding]:
    live = _identity(os.getpid())
    target_sha256 = hashlib.sha256(
        json.dumps(target.identity, separators=(",", ":")).encode()
    ).hexdigest()
    binding = ResourceBinding(
        instance_id=str(uuid4()),
        process_generation=str(uuid4()),
        process_pid=os.getpid(),
        process_started_at=live.birth,
        store_id=target.store_id,
        target_identity_sha256=target_sha256,
        mount_ns_device=live.mount_ns[0],
        mount_ns_inode=live.mount_ns[1],
        cgroup_ns_device=live.cgroup_ns[0],
        cgroup_ns_inode=live.cgroup_ns[1],
        resource_view_sha256=resource_view_sha256 or live.view,
    )
    return live, binding


def _bootstrap(
    target: DatabaseTarget,
    binding: ResourceBinding,
    *,
    target_calls: list[object] | None = None,
    binding_calls: list[object] | None = None,
    repository_connect_calls: list[object] | None = None,
) -> ResourceBootstrap:
    target_calls = [] if target_calls is None else target_calls
    binding_calls = [] if binding_calls is None else binding_calls
    repository_connect_calls = [] if repository_connect_calls is None else repository_connect_calls

    def target_supplier() -> DatabaseTarget:
        target_calls.append(target)
        return target

    @contextmanager
    def connect_factory(selected: DatabaseTarget, **kwargs):
        repository_connect_calls.append((selected, kwargs))
        raise AssertionError("negative root-control path opened the resource repository")
        yield  # pragma: no cover - preserve context-manager shape

    def binding_supplier(_selected: DatabaseTarget) -> ResourceBinding:
        binding_calls.append(binding)
        return binding

    repository = ResourceEnvelopeRepository(
        target_supplier=target_supplier,
        connect_factory=connect_factory,
    )
    return ResourceBootstrap(
        repository=repository,
        binding_supplier=binding_supplier,
        instance_supplier=lambda: binding.instance_id,
    )


def _administrator() -> auth.Principal:
    return auth._verified_principal(
        "urn:caos:root-control-test", "admin", [auth.SCOPE_ADMIN], "jwt"
    )


def _forged_principal() -> auth.Principal:
    value = object.__new__(auth.Principal)
    for name, field_value in (
        ("id", "principal-" + "f" * 64),
        ("issuer", "urn:caos:forged"),
        ("subject", "forged"),
        ("scopes", frozenset({auth.SCOPE_ADMIN})),
        ("kind", "jwt"),
    ):
        object.__setattr__(value, name, field_value)
    return value


def _record(
    target: DatabaseTarget,
    binding: ResourceBinding,
    live: _KernelIdentity,
    **changes: object,
) -> RootSqlOpeningIntent:
    credit_ids = tuple(sorted(str(uuid4()) for _ in range(2)))
    values: dict[str, object] = {
        "intent_id": str(uuid4()),
        "journal_id": str(uuid4()),
        "ordinal": 1,
        "credit_id": credit_ids[0],
        "credit_ids": credit_ids,
        "membership_sha256": "a" * 64,
        "session_id": str(uuid4()),
        "subject": binding,
        "subject_binding_sha256": sql_startup_binding_sha256(binding),
        "subject_sql_application_name": origin.current_sql_application_name(),
        "boot_id": live.boot_id,
        "start_time_ticks": live.ticks,
        "pid_namespace": live.pid_ns,
        "path": "native",
        "destination_sha256": _destination_sha256(target),
        "destination_scope": "main",
        "issuer_principal_id": "principal-" + "e" * 64,
        "slots": 1,
    }
    values.update(changes)
    return RootSqlOpeningIntent(**values)


def _destination_sha256(target: DatabaseTarget) -> str:
    from cli_agent_orchestrator.models.sql_startup_permission import (
        sql_startup_destination_sha256,
    )

    return sql_startup_destination_sha256(target)


def _signed_test_certificate(
    record: RootSqlOpeningIntent,
) -> tuple[SignedRootSqlOpening, Ed25519PrivateKey]:
    """Use an ephemeral key only to exercise verification negatives in memory."""
    from cli_agent_orchestrator.services.sql_root_genesis import sign_root_opening

    private_key = Ed25519PrivateKey.generate()
    capability = _issue_confirmed_root_sql_opening(record, validate=lambda: None)
    return (
        sign_root_opening(capability, key_id="root-test-key", private_key=private_key),
        private_key,
    )


def _control_owner():
    return DeliveryAdmission.with_storage_control(AdmissionPolicy())


def _forbid_connect(monkeypatch, service, calls: list[object]) -> None:
    def connect(selected: DatabaseTarget, **kwargs):
        calls.append((selected, kwargs))
        raise AssertionError("negative root-control path reached SQL connect")

    monkeypatch.setattr(service, "connect", connect)


def _forbid_intent_write(monkeypatch, calls: list[object]) -> None:
    def record_root_opening(repository, channel, **kwargs):
        calls.append((repository, channel, kwargs))
        raise AssertionError("negative root-control path reached intent journal write")

    monkeypatch.setattr(
        intent_repository_module.SqlIntentRepository,
        "record_root_opening",
        record_root_opening,
    )


@pytest.mark.parametrize("principal_kind", ["forged", "local", "nonadmin", "missing"])
def test_certificate_issuer_rejects_non_jwt_admin_before_target_key_or_sql(
    monkeypatch, principal_kind
):
    service = api()
    _root_mode(monkeypatch)
    target = _target()
    live, binding = _kernel_binding(target)
    target_calls: list[object] = []
    repo_connect_calls: list[object] = []
    bootstrap = _bootstrap(
        target,
        binding,
        target_calls=target_calls,
        repository_connect_calls=repo_connect_calls,
    )
    key_calls: list[object] = []
    sql_calls: list[object] = []
    _forbid_intent_write(monkeypatch, sql_calls)
    principals = {
        "forged": _forged_principal(),
        "local": auth._verified_principal(
            "urn:caos:local", "operator", [auth.SCOPE_ADMIN], "local_operator"
        ),
        "nonadmin": auth._verified_principal("urn:caos:test", "reader", [auth.SCOPE_READ], "jwt"),
        "missing": None,
    }

    def key_supplier():
        key_calls.append("resolved")
        return "root-test-key", Ed25519PrivateKey.generate()

    with pytest.raises(PermissionError):
        service.issue_root_sql_certificate(
            bootstrap,
            object(),
            principal=principals[principal_kind],
            journal_id=str(uuid4()),
            intent_id=str(uuid4()),
            session_id=str(uuid4()),
            subject=None,
            key_supplier=key_supplier,
        )

    assert target_calls == []
    assert key_calls == []
    assert repo_connect_calls == []
    assert sql_calls == []


def test_certificate_issuer_rejects_descriptive_subject_dto_without_target_or_key(monkeypatch):
    service = api()
    _root_mode(monkeypatch)
    target = _target()
    live, binding = _kernel_binding(target)
    target_calls: list[object] = []
    repo_connect_calls: list[object] = []
    bootstrap = _bootstrap(
        target,
        binding,
        target_calls=target_calls,
        repository_connect_calls=repo_connect_calls,
    )
    key_calls: list[object] = []
    sql_calls: list[object] = []
    _forbid_intent_write(monkeypatch, sql_calls)

    def key_supplier():
        key_calls.append("resolved")
        return "root-test-key", Ed25519PrivateKey.generate()

    # Neither descriptive DTO is a capability; a subject alone cannot authorize signing.
    with pytest.raises(service.RootGenesisUnavailable):
        service.issue_root_sql_certificate(
            bootstrap,
            object(),
            principal=_administrator(),
            journal_id=str(uuid4()),
            intent_id=str(uuid4()),
            session_id=str(uuid4()),
            subject=_record(target, binding, live),
            key_supplier=key_supplier,
        )

    assert target_calls == []
    assert key_calls == []
    assert repo_connect_calls == []
    assert sql_calls == []


def test_current_root_subject_requires_root_mode_before_target_resolution(monkeypatch):
    service = api()
    _root_mode(monkeypatch, enabled=False)
    target = _target()
    live, binding = _kernel_binding(target)
    target_calls: list[object] = []
    bootstrap = _bootstrap(target, binding, target_calls=target_calls)

    with pytest.raises(origin.SqlStartupPermissionUnavailable):
        service.current_root_sql_subject(bootstrap)

    assert target_calls == []


def test_current_root_subject_returns_only_the_actual_process_binding(monkeypatch):
    service = api()
    _root_mode(monkeypatch)
    target = _target()
    live, binding = _kernel_binding(target)
    target_calls: list[object] = []
    binding_calls: list[object] = []
    bootstrap = _bootstrap(
        target,
        binding,
        target_calls=target_calls,
        binding_calls=binding_calls,
    )

    subject = service.current_root_sql_subject(bootstrap)

    assert subject.binding == binding
    assert subject.binding.process_pid == os.getpid()
    assert subject.boot_id == live.boot_id
    assert subject.start_time_ticks == live.ticks
    assert subject.pid_namespace == live.pid_ns
    assert subject.sql_application_name == origin.current_sql_application_name()
    assert len(target_calls) == 1
    assert len(binding_calls) == 1


def test_current_root_subject_rejects_stale_process_resource_view(monkeypatch):
    service = api()
    _root_mode(monkeypatch)
    target = _target()
    _live, _ = _kernel_binding(target)
    _, stale_binding = _kernel_binding(target, resource_view_sha256="b" * 64)
    target_calls: list[object] = []
    repo_connect_calls: list[object] = []
    bootstrap = _bootstrap(
        target,
        stale_binding,
        target_calls=target_calls,
        repository_connect_calls=repo_connect_calls,
    )

    with pytest.raises(service.RootGenesisUnavailable):
        service.current_root_sql_subject(bootstrap)

    assert target_calls == [target]
    assert repo_connect_calls == []


def test_invalid_certificate_is_rejected_before_resolving_target_or_connecting(monkeypatch):
    service = api()
    _root_mode(monkeypatch)
    target = _target()
    live, binding = _kernel_binding(target)
    record = _record(target, binding, live)
    certificate, key = _signed_test_certificate(record)
    invalid_signature = base64.urlsafe_b64encode(b"x" * 64).decode("ascii").rstrip("=")
    certificate = replace(certificate, signature=invalid_signature)
    target_calls: list[object] = []
    repo_connect_calls: list[object] = []
    bootstrap = _bootstrap(
        target,
        binding,
        target_calls=target_calls,
        repository_connect_calls=repo_connect_calls,
    )
    owner, control = _control_owner()
    connect_calls: list[object] = []
    _forbid_connect(monkeypatch, service, connect_calls)

    with pytest.raises(service.RootGenesisUnavailable):
        with service.root_sql_control_channel(
            bootstrap,
            owner,
            control,
            certificate,
            trusted_keys={certificate.key_id: key.public_key()},
            clock=lambda: 10.0,
        ):
            pytest.fail("invalid certificate authorized root SQL")

    assert target_calls == []
    assert repo_connect_calls == []
    assert connect_calls == []


def test_wrong_trusted_key_is_rejected_before_target_resolution_or_connect(monkeypatch):
    service = api()
    _root_mode(monkeypatch)
    target = _target()
    live, binding = _kernel_binding(target)
    certificate, _ = _signed_test_certificate(_record(target, binding, live))
    wrong_key = Ed25519PrivateKey.generate().public_key()
    target_calls: list[object] = []
    repo_connect_calls: list[object] = []
    bootstrap = _bootstrap(
        target,
        binding,
        target_calls=target_calls,
        repository_connect_calls=repo_connect_calls,
    )
    owner, control = _control_owner()
    connect_calls: list[object] = []
    _forbid_connect(monkeypatch, service, connect_calls)

    with pytest.raises(service.RootGenesisUnavailable):
        with service.root_sql_control_channel(
            bootstrap,
            owner,
            control,
            certificate,
            trusted_keys={certificate.key_id: wrong_key},
            clock=lambda: 10.0,
        ):
            pytest.fail("untrusted key authorized root SQL")

    assert target_calls == []
    assert repo_connect_calls == []
    assert connect_calls == []


@pytest.mark.parametrize("mismatch", ["kernel", "binding", "destination", "label"])
def test_signed_subject_mismatch_is_rejected_before_root_connect(monkeypatch, mismatch):
    service = api()
    _root_mode(monkeypatch)
    target = _target()
    live, binding = _kernel_binding(target)
    values: dict[str, object] = {}
    if mismatch == "kernel":
        values["boot_id"] = str(uuid4())
    elif mismatch == "binding":
        values["subject"] = replace(binding, process_generation=str(uuid4()))
        values["subject_binding_sha256"] = sql_startup_binding_sha256(values["subject"])
    elif mismatch == "destination":
        values["destination_sha256"] = "f" * 64
    else:
        values["subject_sql_application_name"] = "caos-sql-v1-" + uuid4().hex
    certificate, key = _signed_test_certificate(_record(target, binding, live, **values))

    target_calls: list[object] = []
    binding_calls: list[object] = []
    repo_connect_calls: list[object] = []
    bootstrap = _bootstrap(
        target,
        binding,
        target_calls=target_calls,
        binding_calls=binding_calls,
        repository_connect_calls=repo_connect_calls,
    )
    owner, control = _control_owner()
    connect_calls: list[object] = []
    _forbid_connect(monkeypatch, service, connect_calls)

    with pytest.raises(service.RootGenesisUnavailable):
        with service.root_sql_control_channel(
            bootstrap,
            owner,
            control,
            certificate,
            trusted_keys={certificate.key_id: key.public_key()},
            clock=lambda: 10.0,
        ):
            pytest.fail(f"signed {mismatch} mismatch authorized root SQL")

    assert target_calls
    assert binding_calls
    assert repo_connect_calls == []
    assert connect_calls == []


def test_root_sql_control_rejects_a_record_dto_without_certificate_authority(monkeypatch):
    service = api()
    _root_mode(monkeypatch)
    target = _target()
    live, binding = _kernel_binding(target)
    record = _record(target, binding, live)
    target_calls: list[object] = []
    repo_connect_calls: list[object] = []
    bootstrap = _bootstrap(
        target,
        binding,
        target_calls=target_calls,
        repository_connect_calls=repo_connect_calls,
    )
    owner, control = _control_owner()
    connect_calls: list[object] = []
    _forbid_connect(monkeypatch, service, connect_calls)

    with pytest.raises(service.RootGenesisUnavailable):
        with service.root_sql_control_channel(
            bootstrap,
            owner,
            control,
            record,
            trusted_keys={},
            clock=lambda: 10.0,
        ):
            pytest.fail("descriptive DTO authorized root SQL")

    assert target_calls == []
    assert repo_connect_calls == []
    assert connect_calls == []
