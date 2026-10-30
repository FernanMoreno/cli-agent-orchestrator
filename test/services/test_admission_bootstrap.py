"""Only fresh, process-bound operator authority can become a verified attestation."""

import importlib
import os
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = pytest.mark.offline_database


def models():
    return importlib.import_module("cli_agent_orchestrator.models.resource_envelope")


def api():
    return importlib.import_module("cli_agent_orchestrator.services.admission_bootstrap")


def binding(**changes):
    fields = dict(
        instance_id="fa909c12-4645-4605-ab7c-9ac5f4c8125a",
        process_generation="395141ce-9910-43f6-b4c4-9fe21790309e",
        process_pid=os.getpid(),
        process_started_at=1.0,
        store_id="d18bcbac-9c97-4408-baf0-24f7e2331486",
        target_identity_sha256="a" * 64,
        mount_ns_device=1,
        mount_ns_inode=2,
        cgroup_ns_device=1,
        cgroup_ns_inode=3,
        resource_view_sha256="b" * 64,
    )
    fields.update(changes)
    return models().ResourceBinding(**fields)


def claims(**changes):
    fields = dict(
        memory_budget_bytes=1024**3,
        cpu_budget_millicores=1000,
        storage_budget_slots=10,
        complete_hierarchy=True,
        root_is_host=False,
    )
    fields.update(changes)
    return models().EnvelopeClaims(**fields)


def principal(*, admin=True):
    from cli_agent_orchestrator.security.auth import _verified_principal

    return _verified_principal(
        "urn:caos:admission-test", "operator", ["cao:admin"] if admin else ["cao:read"], "jwt"
    )


class Repository:
    def __init__(self):
        self.events = []
        self.latest = None
        self.failure = None
        self.target = SimpleNamespace(scope="main")

    def resolve_target(self):
        self.events.append("resolve")
        return self.target

    def read_latest(self, target, instance_id):
        self.events.append("read")
        if self.failure:
            raise self.failure
        return self.latest

    def append(self, target, *, binding, claims, expected_revision, principal_id, active=True):
        self.events.append("append")
        current = self.latest.revision if self.latest else 0
        if current != expected_revision:
            raise importlib.import_module(
                "cli_agent_orchestrator.clients.admission_bootstrap_repository"
            ).EnvelopeRevisionConflict("revision conflict")
        self.latest = models().ResourceEnvelopeRecord(
            binding=binding,
            claims=claims,
            revision=current + 1,
            active=active,
            actor_principal_id=principal_id,
        )
        return self.latest


def controller(repository=None, current=None):
    repository = repository if repository is not None else Repository()
    current = current if current is not None else [binding()]
    return (
        api().ResourceBootstrap(
            repository=repository,
            binding_supplier=lambda _target: current[0],
            instance_supplier=lambda: current[0].instance_id,
            clock=lambda: 10.0,
        ),
        repository,
        current,
    )


@pytest.mark.parametrize(
    "invalid",
    [
        {"memory_budget_bytes": True},
        {"memory_budget_bytes": 0},
        {"memory_budget_bytes": 2**63},
        {"cpu_budget_millicores": 1.5},
        {"cpu_budget_millicores": -1},
        {"storage_budget_slots": False},
        {"storage_budget_slots": 0},
        {"complete_hierarchy": 1},
        {"root_is_host": "true"},
        {"root_is_host": True, "complete_hierarchy": False},
    ],
)
def test_operator_claims_require_strict_finite_budget_units(invalid):
    with pytest.raises(ValueError):
        claims(**invalid)


@pytest.mark.parametrize(
    "invalid",
    [
        {"process_generation": "unverified"},
        {"process_pid": True},
        {"process_started_at": float("nan")},
        {"process_started_at": float("inf")},
        {"mount_ns_inode": 0},
        {"cgroup_ns_device": -1},
        {"target_identity_sha256": "z" * 64},
    ],
)
def test_process_binding_is_strict(invalid):
    with pytest.raises(ValueError):
        binding(**invalid)


@pytest.mark.parametrize("who", [None, object(), {"scopes": ["cao:admin"]}, "unsealed", "reader"])
def test_unauthorized_actor_cannot_even_resolve_operator_credentials(who):
    from cli_agent_orchestrator.security.auth import Principal

    if who == "unsealed":
        who = object.__new__(Principal)
    elif who == "reader":
        who = principal(admin=False)
    service, repository, _ = controller()
    with pytest.raises(PermissionError):
        service.attest_current(principal=who, claims=claims(), expected_revision=0)
    assert repository.events == []


def test_authorized_write_uses_server_binding_and_principal_then_revocation_denies_old_record():
    service, repository, current = controller()
    actor = principal()
    update = service.attest_current(principal=actor, claims=claims(), expected_revision=0)
    assert update.revision == 1 and update.active
    assert repository.latest.binding == current[0]
    assert repository.latest.actor_principal_id == actor.id
    result = service.load_current()
    assert result.reason == "authority_verified"
    assert api().is_verified_resource_attestation(result.attestation)
    assert result.attestation.revision == 1
    assert result.attestation.verified_at == 10.0
    revoked = service.revoke_current(principal=actor, expected_revision=1)
    assert revoked.revision == 2 and not revoked.active
    assert service.load_current().attestation is None
    assert service.load_current().reason == "authority_revoked"


@pytest.mark.parametrize(
    "change",
    [
        {"process_generation": str(uuid4())},
        {"store_id": str(uuid4())},
        {"target_identity_sha256": "c" * 64},
        {"mount_ns_inode": 4},
        {"cgroup_ns_inode": 5},
        {"resource_view_sha256": "d" * 64},
    ],
)
def test_old_attestation_cannot_cover_a_changed_process_target_or_view(change):
    service, repository, current = controller()
    service.attest_current(principal=principal(), claims=claims(), expected_revision=0)
    current[0] = replace(current[0], **change)
    result = service.load_current()
    assert result.attestation is None
    assert result.reason == "binding_changed"


def test_unavailable_authority_is_redacted_and_does_not_reuse_last_verified_result():
    service, repository, _ = controller()
    service.attest_current(principal=principal(), claims=claims(), expected_revision=0)
    assert service.load_current().attestation is not None
    repository.failure = OSError("private-authority-path-canary")
    result = service.load_current()
    assert result.attestation is None
    assert result.reason == "authority_unavailable"
    assert "canary" not in repr(result)


def test_partial_operator_claim_does_not_become_verified_coverage():
    service, _, _ = controller()
    service.attest_current(
        principal=principal(), claims=claims(complete_hierarchy=False), expected_revision=0
    )
    result = service.load_current()
    assert result.attestation is None
    assert result.reason == "hierarchy_unverified"


def test_namespace_change_during_authority_read_cannot_seal_the_old_view():
    service, repository, current = controller()
    service.attest_current(principal=principal(), claims=claims(), expected_revision=0)
    original = repository.read_latest

    def changing_read(target, instance_id):
        record = original(target, instance_id)
        current[0] = replace(current[0], resource_view_sha256="e" * 64)
        return record

    repository.read_latest = changing_read
    result = service.load_current()
    assert result.attestation is None
    assert result.reason == "binding_changed"


def test_opaque_attestation_cannot_be_forged_with_an_unsealed_instance():
    forged = object.__new__(api().VerifiedResourceAttestation)
    assert not api().is_verified_resource_attestation(forged)
    assert not api().is_verified_resource_attestation({"complete_hierarchy": True})


@pytest.mark.parametrize("revision", [True, -1, 1.0])
def test_invalid_revision_is_rejected_before_resolving_target(revision):
    service, repository, _ = controller()
    with pytest.raises(ValueError):
        service.attest_current(principal=principal(), claims=claims(), expected_revision=revision)
    assert repository.events == []


def test_replayed_attestation_and_revocation_cannot_append_again():
    from cli_agent_orchestrator.clients.admission_bootstrap_repository import (
        EnvelopeRevisionConflict,
    )

    service, repository, _ = controller()
    actor = principal()
    service.attest_current(principal=actor, claims=claims(), expected_revision=0)
    with pytest.raises(EnvelopeRevisionConflict):
        service.attest_current(principal=actor, claims=claims(), expected_revision=0)
    assert repository.latest.revision == 1
    service.revoke_current(principal=actor, expected_revision=1)
    with pytest.raises(EnvelopeRevisionConflict):
        service.revoke_current(principal=actor, expected_revision=1)
    assert repository.latest.revision == 2 and not repository.latest.active


def test_divergent_server_instance_sources_fail_closed():
    repository = Repository()
    service = api().ResourceBootstrap(
        repository=repository,
        binding_supplier=lambda _target: binding(),
        instance_supplier=lambda: str(uuid4()),
        clock=lambda: 10.0,
    )
    with pytest.raises(ValueError):
        service.attest_current(principal=principal(), claims=claims(), expected_revision=0)
    assert repository.latest is None
    assert service.load_current().attestation is None


@pytest.mark.parametrize("failure_stage", ["capture", "instance"])
def test_expected_identity_failure_is_unknown_without_private_error_text(failure_stage):
    def unavailable_identity():
        raise RuntimeError("private-identity-path-canary")

    service = api().ResourceBootstrap(
        repository=Repository(),
        binding_supplier=(
            (
                lambda target: api().capture_server_binding(
                    target, identity_supplier=unavailable_identity
                )
            )
            if failure_stage == "capture"
            else (lambda _target: binding())
        ),
        instance_supplier=(
            unavailable_identity if failure_stage == "instance" else lambda: binding().instance_id
        ),
        clock=lambda: 10.0,
    )
    result = service.load_current()
    assert result.attestation is None and result.reason == "authority_unavailable"
    assert "canary" not in repr(result)


def test_empty_revocation_reports_not_found_without_claiming_a_durable_effect():
    from cli_agent_orchestrator.clients.admission_bootstrap_repository import EnvelopeNotFound

    service, repository, _ = controller()
    with pytest.raises(EnvelopeNotFound, match="does not exist"):
        service.revoke_current(principal=principal(), expected_revision=0)
    assert repository.latest is None
    assert (
        service.attest_current(principal=principal(), claims=claims(), expected_revision=0).revision
        == 1
    )


@pytest.fixture
def resource_view(tmp_path):
    identity = SimpleNamespace(
        instance_id=str(uuid4()),
        process_generation=str(uuid4()),
        pid=os.getpid(),
        process_started_at=1.0,
    )
    root = tmp_path / "proc"
    (root / "self/ns").mkdir(parents=True)
    for name in ("mnt", "cgroup"):
        (root / "self/ns" / name).write_text("")
    (root / "self/cgroup").write_text("0::/test-node\n")
    (root / "self/mountinfo").write_text("test-controlled-mount-view\n")
    target = SimpleNamespace(
        store_id=str(uuid4()), identity=("test-host", 1234, "test-db", "test-schema", "test-store")
    )

    def capture():
        return api().capture_server_binding(
            target, identity_supplier=lambda: identity, proc_root=root
        )

    return capture, root


def test_resource_view_binding_detects_membership_mounts_and_namespace_changes(resource_view):
    capture, root = resource_view
    first = capture()
    assert capture() == first
    (root / "self/cgroup").write_text("0::/other-node\n")
    second = capture()
    assert second.resource_view_sha256 != first.resource_view_sha256
    (root / "self/mountinfo").write_text("other-test-mount-view\n")
    third = capture()
    assert third.resource_view_sha256 != second.resource_view_sha256
    namespace = root / "self/ns/mnt"
    replacement = root / "replacement"
    replacement.write_text("")
    replacement.replace(namespace)
    assert capture().mount_ns_inode != third.mount_ns_inode


@pytest.mark.parametrize("payload", [b"", b"incomplete-view", b"x" * (1024 * 1024 + 1)])
def test_incomplete_or_oversized_resource_view_does_not_produce_a_binding(resource_view, payload):
    capture, root = resource_view
    (root / "self/mountinfo").write_bytes(payload)
    with pytest.raises(ValueError):
        capture()


def test_resource_view_change_during_capture_is_rejected(resource_view, monkeypatch):
    capture, _ = resource_view
    original = api()._view_file
    memberships = []

    def changing_read(path):
        value = original(path)
        if path.name == "cgroup":
            memberships.append(True)
            if len(memberships) == 2:
                return b"0::/changed-during-observation\n"
        return value

    monkeypatch.setattr(api(), "_view_file", changing_read)
    with pytest.raises(ValueError, match="changed"):
        capture()
