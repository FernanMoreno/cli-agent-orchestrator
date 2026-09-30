"""T093 stage 1 uses a verified temporary SQLite store, never runtime registries."""

import hashlib
import importlib
import importlib.util
import time

import pytest
from pydantic import ValidationError

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority


def provisioning_module():
    """Keep the RED behavioral: an absent service is an assertion, not an import error."""
    name = "cli_agent_orchestrator.services.work_provisioning"
    assert importlib.util.find_spec(name) is not None, "WorkProvisioning is missing"
    return importlib.import_module(name)


def origin_models():
    name = "cli_agent_orchestrator.models.work_origin"
    assert importlib.util.find_spec(name) is not None, "work origin models are missing"
    return importlib.import_module(name)


def trusted_launch_setup(repository, root, subject):
    """Create independent, real authority and frozen snapshot evidence for one subject."""
    models = importlib.import_module("cli_agent_orchestrator.models.work_contract")
    job = repository.create_job(
        project_id=f"project-{subject.subject}",
        principal_id=subject.id,
        allowed_providers=["mock_cli"],
        grant_id=f"root-{subject.subject}",
    )
    grant = WorkAuthority(repository).issue_root(
        subject,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read", "tool.read"},
            paths={str(root)},
            commands={"pytest", "/bin/alpha"},
        ),
        expires_at=time.time() + 600,
    )
    snapshot = DelegationSnapshots(
        repository, policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision)
    ).freeze(
        principal=subject,
        job_id=job["id"],
        contract_id=f"contract-{subject.subject}",
        binding_key=f"snapshot-{subject.subject}",
        request_hash=hashlib.sha256(subject.id.encode()).hexdigest(),
        scope="project",
        scope_id=f"project-{subject.subject}",
        resolver=lambda connection, principal: ResolvedSnapshot("frozen launch context"),
    )
    contract = models.EffectiveWorkContract(
        id=f"contract-{subject.subject}",
        operation_kind="launch",
        provider="mock_cli",
        backend="test",
        permissions=models.ContractPermissions(tools=("tool.read",), paths=(str(root),)),
        resources=models.ContractResources(
            checkout_root=str(root), write_paths=(str(root / subject.subject),), units=1
        ),
        snapshot=models.ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )
    return job, grant, contract


def provision_arguments(subject, job, grant, contract, *, expected_revision, lease_seconds=300):
    return {
        "subject": subject,
        "selector": "same-selector",
        "expected_revision": expected_revision,
        "job_id": job["id"],
        "grant_id": grant.id,
        "grant_revision": grant.revision,
        "contract": contract,
        "adapter_version": 1,
        "lease_seconds": lease_seconds,
    }


def test_v2_provision_roundtrips_ordered_executable_identity(tmp_path):
    models = importlib.import_module("cli_agent_orchestrator.models.work_contract")
    module = provisioning_module()
    repository = WorkRepository(tmp_path / "v2-provision.sqlite3")
    repository.initialize()
    actor = auth._verified_principal("https://issuer.test", "v2-owner", [auth.SCOPE_ADMIN], "jwt")
    job, grant, original = trusted_launch_setup(repository, tmp_path, actor)
    identity = models.ExecutableIdentity(
        command_token="/bin/alpha",
        content_reference="sha256:" + "a" * 64,
        sha256_digest="a" * 64,
        elf_machine="x86_64",
        elf_class="ELF64",
        endianness="little",
    )
    contract = models.EffectiveWorkContractV2(
        **{
            **original.model_dump(),
            "schema_version": 2,
            "permissions": original.permissions.model_copy(update={"commands": ("/bin/alpha",)}),
            "executable_identities": (identity,),
        }
    )
    service = module.WorkProvisioning(repository)
    ref = service.provision_launch(
        actor, **provision_arguments(actor, job, grant, contract, expected_revision=0)
    )
    resolved = module.WorkProvisioning(WorkRepository(repository.path)).resolve_launch(
        actor, ref.selector
    )
    assert resolved.contract == contract
    assert resolved.contract.executable_identities == (identity,)
    assert resolved.contract_hash == contract.canonical_hash()


def test_provisioning_is_scoped_by_principal_survives_restart_and_fences_cas(tmp_path):
    """A selector never becomes a global capability, even across SQLite restart."""
    repository = WorkRepository(tmp_path / "provisioning.sqlite3")
    repository.initialize()
    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    assert repository_module.SCHEMA_VERSION >= 19, "T093 requires verified schema v19"
    with repository.read_snapshot() as connection:
        assert tuple(
            connection.execute(
                "SELECT checksum,verification_result FROM work_migrations WHERE version=19"
            ).fetchone()
        ) == (repository_module._CHECKSUMS[19], "verified")

    module = provisioning_module()
    first = auth._verified_principal("https://issuer.test", "first", [auth.SCOPE_ADMIN], "jwt")
    second = auth._verified_principal("https://issuer.test", "second", [auth.SCOPE_ADMIN], "jwt")
    first_job, first_grant, first_contract = trusted_launch_setup(repository, tmp_path, first)
    second_job, second_grant, second_contract = trusted_launch_setup(repository, tmp_path, second)
    service = module.WorkProvisioning(repository)

    first_ref = service.provision_launch(
        first,
        **provision_arguments(first, first_job, first_grant, first_contract, expected_revision=0),
    )
    second_ref = service.provision_launch(
        second,
        **provision_arguments(
            second, second_job, second_grant, second_contract, expected_revision=0
        ),
    )
    assert first_ref.principal_id == first.id
    assert second_ref.principal_id == second.id
    assert first_ref.selector == second_ref.selector == "same-selector"
    assert first_ref.id != second_ref.id

    first_current = service.resolve_launch(first, "same-selector")
    assert first_current.ref == first_ref
    assert first_current.contract.canonical_hash() == first_contract.canonical_hash()
    with pytest.raises(module.ProvisionUnavailable):
        service.resolve_launch(second, "absent-or-foreign")

    restarted = module.WorkProvisioning(WorkRepository(repository.path))
    assert restarted.resolve_launch(first, "same-selector").ref == first_ref
    replacement = restarted.provision_launch(
        first,
        **provision_arguments(
            first, first_job, first_grant, first_contract, expected_revision=1, lease_seconds=301
        ),
    )
    assert replacement.id == first_ref.id
    assert replacement.revision == 2
    with pytest.raises(module.ProvisionConflict):
        restarted.provision_launch(
            first,
            **provision_arguments(
                first, first_job, first_grant, first_contract, expected_revision=1
            ),
        )
    with repository.read_snapshot() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_launch_provisions WHERE principal_id=? AND selector=?",
                (first.id, "same-selector"),
            ).fetchone()[0]
            == 2
        )

    retired = restarted.retire_launch(
        first, subject=first, selector="same-selector", expected_revision=2
    )
    assert retired.id == first_ref.id and retired.revision == 3
    with pytest.raises(module.ProvisionUnavailable):
        restarted.resolve_launch(first, "same-selector")
    assert restarted.resolve_launch(second, "same-selector").ref == second_ref


def test_provision_models_reject_untrusted_version_coercion_and_extra_fields():
    """Changing strict/frozen v1 validation must make this behavioral contract fail."""
    models = origin_models()
    with pytest.raises(ValidationError):
        models.ProvisionRef.model_validate(
            {
                "schema_version": True,
                "id": "provision",
                "principal_id": "principal",
                "selector": "selection",
                "revision": 1,
            }
        )
    with pytest.raises(ValidationError):
        models.ProvisionRef.model_validate(
            {
                "schema_version": 1,
                "id": "provision",
                "principal_id": "principal",
                "selector": "selection",
                "revision": True,
            }
        )
    with pytest.raises(ValidationError):
        models.ProvisionRef.model_validate(
            {
                "schema_version": 2,
                "id": "provision",
                "principal_id": "principal",
                "selector": "selection",
                "revision": 1,
                "unexpected": "field",
            }
        )
    with pytest.raises(ValidationError):
        models.ProvisionRef.model_validate(
            {
                "schema_version": 1,
                "id": "provision",
                "principal_id": "principal",
                "selector": "selection",
                "revision": 1,
                "unexpected": "field",
            }
        )


def test_provisioning_denies_invalid_admin_or_mixed_durable_evidence_without_writing(tmp_path):
    """A partial provision must never survive an ownership, grant, or snapshot denial."""
    repository = WorkRepository(tmp_path / "denied.sqlite3")
    repository.initialize()
    module = provisioning_module()
    first = auth._verified_principal("https://issuer.test", "first", [auth.SCOPE_ADMIN], "jwt")
    second = auth._verified_principal("https://issuer.test", "second", [auth.SCOPE_ADMIN], "jwt")
    first_job, first_grant, first_contract = trusted_launch_setup(repository, tmp_path, first)
    second_job, second_grant, second_contract = trusted_launch_setup(repository, tmp_path, second)
    service = module.WorkProvisioning(repository)
    arguments = provision_arguments(
        first, first_job, first_grant, first_contract, expected_revision=0
    )
    mixed_snapshot = first_contract.model_copy(update={"snapshot": second_contract.snapshot})

    for admin, changed in (
        ({"id": first.id}, {}),
        (first, {"grant_id": second_grant.id, "grant_revision": second_grant.revision}),
        (first, {"contract": mixed_snapshot}),
        (first, {"job_id": second_job["id"]}),
    ):
        with pytest.raises(module.ProvisionDenied):
            service.provision_launch(admin, **dict(arguments, **changed))
        with repository.read_snapshot() as connection:
            assert (
                connection.execute("SELECT count(*) FROM work_launch_provisions").fetchone()[0] == 0
            )


def test_internal_provision_port_rejects_unsealed_admin_or_subject_without_writing(tmp_path):
    """A Principal-shaped body value must not become provisioning authority."""
    repository = WorkRepository(tmp_path / "forged-principal.sqlite3")
    repository.initialize()
    module = provisioning_module()
    principal = auth._verified_principal(
        "https://issuer.test", "provision-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    job, grant, contract = trusted_launch_setup(repository, tmp_path, principal)
    arguments = provision_arguments(principal, job, grant, contract, expected_revision=0)
    service = module.WorkProvisioning(repository)

    forged = object.__new__(auth.Principal)
    for field in ("id", "issuer", "subject", "scopes", "kind"):
        object.__setattr__(forged, field, getattr(principal, field))

    for admin, subject in ((forged, principal), (principal, forged)):
        with pytest.raises(module.ProvisionDenied, match="verified principal"):
            service.provision_launch(admin, **dict(arguments, subject=subject))
        with repository.read_snapshot() as connection:
            assert (
                connection.execute("SELECT count(*) FROM work_launch_provisions").fetchone()[0] == 0
            )
