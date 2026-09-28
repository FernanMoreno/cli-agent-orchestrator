"""Reject unverified continuity packages before they affect durable work state."""

from dataclasses import FrozenInstanceError, dataclass
import hashlib
import importlib
import importlib.util
import json
import time

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContract,
)
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.step_output_store import ArtifactRef, ImmutableResultStore
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_contract import WorkContracts
from test.fixtures.work_store import work_store_paths  # noqa: F401


def continuation_module():
    name = "cli_agent_orchestrator.services.work_continuation"
    assert importlib.util.find_spec(name) is not None, "continuation public API missing"
    return importlib.import_module(name)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _package(payload):
    unsigned = dict(payload)
    unsigned.pop("package_hash", None)
    return _canonical(
        {**unsigned, "package_hash": hashlib.sha256(_canonical(unsigned)).hexdigest()}
    )


@dataclass(frozen=True)
class ContinuationContext:
    repository: WorkRepository
    principal: object
    grant: object
    source_work: dict
    binding: object
    artifact_store: ImmutableResultStore
    artifact: ArtifactRef
    orphan: ArtifactRef
    package: bytes


@pytest.fixture
def continuation_context(work_store_paths):
    repository = WorkRepository(work_store_paths.database)
    repository.initialize()
    root = work_store_paths.database.parent
    principal = auth._verified_principal("https://issuer.test", "owner", [auth.SCOPE_ADMIN], "jwt")
    job = repository.create_job(
        project_id="project",
        principal_id=principal.id,
        allowed_providers=["mock_cli"],
        grant_id="root",
    )
    authority = WorkAuthority(repository)
    grant = authority.issue_root(
        principal,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read", "Read"}, paths={str(root)}),
        expires_at=time.time() + 600,
    )
    snapshot = DelegationSnapshots(
        repository, policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision)
    ).freeze(
        principal=principal,
        job_id=job["id"],
        contract_id="source-contract",
        binding_key="continuation-source",
        request_hash="a" * 64,
        scope="project",
        scope_id="project",
        resolver=lambda connection, actor: ResolvedSnapshot(""),
    )
    source_work = repository.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key="continuation-source",
        request_hash="a" * 64,
        contract_id="source-contract",
        snapshot_id=snapshot.id,
        provider="mock_cli",
        actor_id=principal.id,
    )
    contract = EffectiveWorkContract(
        id="source-contract",
        operation_kind="launch",
        provider="mock_cli",
        backend="test_backend",
        permissions=ContractPermissions(tools=("Read",), paths=(str(root),)),
        resources=ContractResources(
            checkout_root=str(root), write_paths=(str(root / "target"),), units=1
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )
    attempt = source_work["attempts"][0]
    binding = WorkContracts(repository).bind(
        principal=principal,
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_attempt_revision=attempt["revision"],
        grant_id=grant.id,
        expected_grant_revision=grant.revision,
        contract=contract,
    )
    artifact_store = ImmutableResultStore(work_store_paths.artifacts)
    artifact_bytes = b"Bearer 0123456789abcdef"

    def register_verified_result(ref):
        assert artifact_store.read(ref) == artifact_bytes
        repository.register_result(
            attempt_id=attempt["id"],
            generation=attempt["generation"],
            content_hash=ref.content_hash,
            immutable_location=ref.immutable_location,
            byte_length=ref.byte_length,
            validator_id="fixture-validator",
            validation_evidence={
                "artifact_sha256": ref.content_hash,
                "bytes_verified": True,
                "byte_length": len(artifact_bytes),
            },
            actor_id=principal.id,
        )
        return ref

    artifact = artifact_store.publish(artifact_bytes, register_verified_result)
    orphan = artifact_store.publish(b"unregistered orphan artifact", lambda ref: ref)
    source_work = repository.get_work(source_work["id"])
    assert source_work["state"] == "queued"
    assert source_work["accepted_result_id"] is None
    assert source_work["attempts"][0]["state"] == "planned"
    assert source_work["attempts"][0]["result_id"] is None
    context = ContinuationContext(
        repository=repository,
        principal=principal,
        grant=grant,
        source_work=source_work,
        binding=binding,
        artifact_store=artifact_store,
        artifact=artifact,
        orphan=orphan,
        package=b"",
    )
    module = continuation_module()
    package = module.WorkContinuations(repository, artifact_store).export_continuation(
        principal=principal,
        source_attempt_id=binding.attempt_id,
        generation=binding.generation,
        grant_id=grant.id,
        expected_grant_revision=grant.revision,
        reason="provider replacement",
    )
    assert package == _package(_payload(context))
    assert artifact_store.read(artifact) == artifact_bytes
    assert binding.contract_hash == contract.canonical_hash()
    assert binding.contract.snapshot.id == snapshot.id
    assert binding.contract.snapshot.delivered_hash == snapshot.delivered_hash
    return ContinuationContext(**{**context.__dict__, "package": package})


def _state(repository, artifacts):
    with repository.connection() as connection:
        schema = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        )
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        rows = tuple(
            (
                name,
                tuple(
                    tuple(row)
                    for row in connection.execute(
                        f'SELECT * FROM "{name.replace(chr(34), chr(34) * 2)}" ORDER BY rowid'
                    )
                ),
            )
            for name in tables
        )
    artifacts_state = tuple(
        (path.name, path.read_bytes())
        for path in sorted(artifacts.root.iterdir(), key=lambda path: path.name)
        if path.is_file()
    )
    return schema, rows, artifacts_state


def _payload(context):
    return {
        "schema_version": 1,
        "source_attempt_id": context.binding.attempt_id,
        "source_generation": context.binding.generation,
        "contract": {"id": context.binding.contract.id, "hash": context.binding.contract_hash},
        "snapshot": {
            "id": context.binding.contract.snapshot.id,
            "delivered_hash": context.binding.contract.snapshot.delivered_hash,
        },
        "artifacts": [
            {
                "content_hash": context.artifact.content_hash,
                "content_ref": context.artifact.immutable_location,
                "byte_length": context.artifact.byte_length,
            }
        ],
        "completed": [],
        "pending": [context.source_work["id"]],
        "uncertain": [],
        "reason": "provider replacement",
    }


def _import(
    module,
    context,
    package,
    *,
    principal=None,
    grant_id=None,
    expected_grant_revision=None,
):
    return module.WorkContinuations(context.repository, context.artifact_store).import_continuation(
        package,
        principal=context.principal if principal is None else principal,
        grant_id=context.grant.id if grant_id is None else grant_id,
        expected_grant_revision=(
            context.grant.revision if expected_grant_revision is None else expected_grant_revision
        ),
    )


def test_export_omits_artifact_bytes_authority_and_unregistered_orphan(continuation_context):
    """Would fail if export serialized bytes, authority, or an unregistered orphan."""
    module = continuation_module()

    package = module.WorkContinuations(
        continuation_context.repository, continuation_context.artifact_store
    ).export_continuation(
        principal=continuation_context.principal,
        source_attempt_id=continuation_context.binding.attempt_id,
        generation=continuation_context.binding.generation,
        grant_id=continuation_context.grant.id,
        expected_grant_revision=continuation_context.grant.revision,
        reason="provider replacement",
    )

    payload = json.loads(package)
    unsigned = dict(payload)
    package_hash = unsigned.pop("package_hash")
    assert package == _canonical(payload)
    assert package_hash == hashlib.sha256(_canonical(unsigned)).hexdigest()
    assert payload == {
        "schema_version": 1,
        "source_attempt_id": continuation_context.binding.attempt_id,
        "source_generation": continuation_context.binding.generation,
        "contract": {
            "id": continuation_context.binding.contract.id,
            "hash": continuation_context.binding.contract_hash,
        },
        "snapshot": {
            "id": continuation_context.binding.contract.snapshot.id,
            "delivered_hash": continuation_context.binding.contract.snapshot.delivered_hash,
        },
        "artifacts": [
            {
                "content_hash": continuation_context.artifact.content_hash,
                "content_ref": continuation_context.artifact.immutable_location,
                "byte_length": continuation_context.artifact.byte_length,
            }
        ],
        "completed": [],
        "pending": [continuation_context.source_work["id"]],
        "uncertain": [],
        "reason": "provider replacement",
        "package_hash": package_hash,
    }
    assert b"Bearer 0123456789abcdef" not in package
    assert continuation_context.artifact.content_hash.encode() in package
    assert b"unregistered orphan artifact" not in package
    assert continuation_context.orphan.content_hash.encode() not in package


def test_tampered_package_is_rejected_before_import_or_work_side_effect(continuation_context):
    """Would fail if import trusted package bytes before verifying package_hash."""
    package = json.loads(continuation_context.package)
    package["reason"] = "tampered replacement reason"
    before = _state(continuation_context.repository, continuation_context.artifact_store)
    module = continuation_module()

    with pytest.raises(module.ContinuationRejected):
        _import(module, continuation_context, _canonical(package))

    assert _state(continuation_context.repository, continuation_context.artifact_store) == before


def test_unknown_schema_version_is_rejected_before_v1_deserialization_or_effect(
    continuation_context,
):
    """Would fail if an unknown package version fell through to v1 validation."""
    package = json.loads(continuation_context.package)
    package["schema_version"] = 2
    before = _state(continuation_context.repository, continuation_context.artifact_store)
    module = continuation_module()

    with pytest.raises(module.UnsupportedContinuationSchema):
        _import(module, continuation_context, _package(package))

    assert _state(continuation_context.repository, continuation_context.artifact_store) == before


def test_missing_referenced_artifact_does_not_complete_or_replace_work(continuation_context):
    """Would fail if import used a missing artifact before it can create executable work."""
    module = continuation_module()
    assert _import(module, continuation_context, continuation_context.package)
    artifact_path = (
        continuation_context.artifact_store.root / continuation_context.artifact.immutable_location
    )
    assert artifact_path.is_file()
    artifact_path.unlink()
    before = _state(continuation_context.repository, continuation_context.artifact_store)

    with pytest.raises(module.ContinuationRejected):
        _import(module, continuation_context, continuation_context.package)

    assert (
        continuation_context.repository.get_work(continuation_context.source_work["id"])
        == continuation_context.source_work
    )
    assert _state(continuation_context.repository, continuation_context.artifact_store) == before


def test_corrupt_referenced_artifact_is_rejected_without_durable_side_effect(
    continuation_context,
):
    """Would fail if import checked artifact metadata without verifying artifact bytes."""
    module = continuation_module()
    assert _import(module, continuation_context, continuation_context.package)
    artifact_path = (
        continuation_context.artifact_store.root / continuation_context.artifact.immutable_location
    )
    artifact_path.write_bytes(b"x" * continuation_context.artifact.byte_length)
    before = _state(continuation_context.repository, continuation_context.artifact_store)

    with pytest.raises(module.ContinuationRejected):
        _import(module, continuation_context, continuation_context.package)

    assert _state(continuation_context.repository, continuation_context.artifact_store) == before


def test_noncanonical_or_rehashed_extra_package_is_rejected_without_writes(continuation_context):
    """Would fail if import normalized wire bytes or tolerated unauthenticated extra fields."""
    module = continuation_module()
    extra = json.loads(continuation_context.package)
    extra["unexpected"] = True
    before = _state(continuation_context.repository, continuation_context.artifact_store)

    for package in (b" " + continuation_context.package, _package(extra)):
        with pytest.raises(module.ContinuationRejected):
            _import(module, continuation_context, package)
        assert (
            _state(continuation_context.repository, continuation_context.artifact_store) == before
        )


def test_closed_canonical_parser_rejects_duplicate_nested_type_and_oversize_packages(
    continuation_context,
):
    """Would fail if import normalized malformed JSON or coerced a v1 field."""
    module = continuation_module()
    nested_extra = json.loads(continuation_context.package)
    nested_extra["contract"]["unexpected"] = True
    wrong_type = json.loads(continuation_context.package)
    wrong_type["source_generation"] = True
    duplicate_nested_key = continuation_context.package.replace(
        b'"id":"source-contract"',
        b'"id":"source-contract","id":"source-contract"',
        1,
    )
    before = _state(continuation_context.repository, continuation_context.artifact_store)

    for package in (
        duplicate_nested_key,
        _package(nested_extra),
        _package(wrong_type),
        continuation_context.package + b" " * (32 * 1024),
    ):
        with pytest.raises(module.ContinuationRejected):
            _import(module, continuation_context, package)
        assert (
            _state(continuation_context.repository, continuation_context.artifact_store) == before
        )


def test_wrong_actor_or_grant_is_rejected_without_writes(continuation_context):
    """Would fail if a package hash substituted for caller and live grant authorization."""
    module = continuation_module()
    wrong_actor = auth._verified_principal(
        "https://issuer.test", "other-owner", [auth.SCOPE_ADMIN], "other-jwt"
    )
    before = _state(continuation_context.repository, continuation_context.artifact_store)

    for kwargs in (
        {"principal": wrong_actor},
        {"grant_id": "missing-grant"},
        {"expected_grant_revision": continuation_context.grant.revision + 1},
    ):
        with pytest.raises(module.ContinuationRejected):
            _import(module, continuation_context, continuation_context.package, **kwargs)
        assert (
            _state(continuation_context.repository, continuation_context.artifact_store) == before
        )


def test_revoked_grant_is_rejected_without_preflight_writes(continuation_context):
    """Would fail if import skipped the source's live grant-chain authorization."""
    module = continuation_module()
    WorkAuthority(continuation_context.repository).revoke(
        continuation_context.principal,
        grant_id=continuation_context.grant.id,
        expected_grant_revision=continuation_context.grant.revision,
        reason="fixture revocation",
    )
    before = _state(continuation_context.repository, continuation_context.artifact_store)

    with pytest.raises(module.ContinuationRejected):
        _import(module, continuation_context, continuation_context.package)

    assert _state(continuation_context.repository, continuation_context.artifact_store) == before


def test_rehashed_source_contract_snapshot_generation_or_state_is_rejected(continuation_context):
    """Would fail if a self-consistent package could replace source evidence."""
    module = continuation_module()
    contract = json.loads(continuation_context.package)
    contract["contract"]["hash"] = "b" * 64
    snapshot = json.loads(continuation_context.package)
    snapshot["snapshot"]["delivered_hash"] = "c" * 64
    generation = json.loads(continuation_context.package)
    generation["source_generation"] += 1
    classification = json.loads(continuation_context.package)
    classification["completed"] = [continuation_context.source_work["id"]]
    classification["pending"] = []
    before = _state(continuation_context.repository, continuation_context.artifact_store)

    for package in map(_package, (contract, snapshot, generation, classification)):
        with pytest.raises(module.ContinuationRejected):
            _import(module, continuation_context, package)
        assert (
            _state(continuation_context.repository, continuation_context.artifact_store) == before
        )


def test_rehashed_foreign_or_omitted_artifact_evidence_is_rejected(continuation_context):
    """Would fail if import trusted a rehashed package instead of current source evidence."""
    module = continuation_module()
    foreign = json.loads(continuation_context.package)
    foreign["artifacts"] = [
        {
            "content_hash": continuation_context.orphan.content_hash,
            "content_ref": continuation_context.orphan.immutable_location,
            "byte_length": continuation_context.orphan.byte_length,
        }
    ]
    omitted = json.loads(continuation_context.package)
    omitted["artifacts"] = []
    before = _state(continuation_context.repository, continuation_context.artifact_store)

    for package in (_package(foreign), _package(omitted)):
        with pytest.raises(module.ContinuationRejected):
            _import(module, continuation_context, package)
        assert (
            _state(continuation_context.repository, continuation_context.artifact_store) == before
        )


def test_valid_preflight_is_frozen_descriptive_and_replay_has_no_writes(continuation_context):
    """Would fail if a valid import created executable state or retained replay authority."""
    module = continuation_module()
    before = _state(continuation_context.repository, continuation_context.artifact_store)

    first = _import(module, continuation_context, continuation_context.package)
    second = _import(module, continuation_context, continuation_context.package)

    expected = module.ContinuationPreflight(
        package_hash=hashlib.sha256(continuation_context.package).hexdigest(),
        source_attempt_id=continuation_context.binding.attempt_id,
        source_generation=continuation_context.binding.generation,
        source_work_item_id=continuation_context.source_work["id"],
        source_job_id=continuation_context.binding.job_id,
    )
    assert first == second == expected
    with pytest.raises(FrozenInstanceError):
        first.package_hash = "0" * 64
    assert _state(continuation_context.repository, continuation_context.artifact_store) == before
