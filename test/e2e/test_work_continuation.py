"""Completed-work continuity uses only durable local work evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
from pathlib import Path

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
from cli_agent_orchestrator.services.work_continuation import ContinuationRejected, WorkContinuations
from cli_agent_orchestrator.services.work_contract import ContractConflict, WorkContracts
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
from cli_agent_orchestrator.services.work_service import DeliveryObservation, WorkService


# This is an e2e state-machine test, not an operator/provider e2e test.  It deliberately
# replaces the directory's external-server/tmux autouse fixtures with local doubles.
@pytest.fixture(scope="session", autouse=True)
def require_cao_server():
    yield


@pytest.fixture(scope="session", autouse=True)
def require_tmux():
    yield


_OUTPUT = b"adapter-a accepted result"


@dataclass
class FakeAdapter:
    """A local adapter whose B instance raises if continuity tries an effect."""

    instance_id: str
    effects_forbidden: bool = False
    sends: int = 0
    executions: int = 0
    callbacks: int = 0

    def send(self) -> DeliveryObservation:
        self.sends += 1
        if self.effects_forbidden:
            raise AssertionError(f"{self.instance_id} must not send completed work")
        return DeliveryObservation()

    def validate_result(self, content: bytes) -> dict:
        self.callbacks += 1
        if self.effects_forbidden:
            raise AssertionError(f"{self.instance_id} must not validate completed work")
        return {"valid": content == _OUTPUT, "adapter_instance": self.instance_id}

    def execute(self, service: WorkService, work: dict, artifacts: ImmutableResultStore) -> dict:
        self.executions += 1
        if self.effects_forbidden:
            raise AssertionError(f"{self.instance_id} must not execute completed work")
        return service.settle_attempt(
            work["id"],
            generation=work["attempts"][-1]["generation"],
            content=_OUTPUT,
            artifacts=artifacts,
            validate=self.validate_result,
            validator_id="fake-adapter-validator",
            actor_id="owner",
        )


@dataclass(frozen=True)
class CompletedWork:
    database: Path
    artifacts_root: Path
    repository: WorkRepository
    artifacts: ImmutableResultStore
    principal: object
    grant: object
    binding: object
    work: dict


def _completed_work(tmp_path: Path) -> tuple[CompletedWork, FakeAdapter]:
    database = tmp_path / "work.sqlite3"
    artifacts_root = tmp_path / "artifacts"
    repository = WorkRepository(database)
    repository.initialize()
    artifacts = ImmutableResultStore(artifacts_root)
    principal = auth._verified_principal("https://issuer.test", "owner", [auth.SCOPE_ADMIN], "jwt")
    job = repository.create_job(
        project_id="project",
        principal_id=principal.id,
        allowed_providers=["fake-shared-provider"],
        grant_id="root",
    )
    grant = WorkAuthority(repository).issue_root(
        principal,
        job_id=job["id"],
        providers={"fake-shared-provider"},
        permissions=Permissions(tools={"Read", "knowledge.read"}, paths={str(tmp_path)}),
        expires_at=time.time() + 600,
    )
    snapshot = DelegationSnapshots(
        repository, policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision)
    ).freeze(
        principal=principal,
        job_id=job["id"],
        contract_id="adapter-continuation-contract",
        binding_key="adapter-continuation",
        request_hash="a" * 64,
        scope="project",
        scope_id="project",
        resolver=lambda connection, actor: ResolvedSnapshot(""),
    )
    work = repository.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key="adapter-continuation",
        request_hash="a" * 64,
        contract_id="adapter-continuation-contract",
        snapshot_id=snapshot.id,
        provider="fake-shared-provider",
        actor_id=principal.id,
    )
    attempt = work["attempts"][0]
    contract = EffectiveWorkContract(
        id="adapter-continuation-contract",
        operation_kind="launch",
        provider="fake-shared-provider",
        backend="fake-local-backend",
        permissions=ContractPermissions(tools=("Read",), paths=(str(tmp_path),)),
        resources=ContractResources(
            checkout_root=str(tmp_path), write_paths=(str(tmp_path / "target"),), units=1
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )
    binding = WorkContracts(repository).bind(
        principal=principal,
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_attempt_revision=attempt["revision"],
        grant_id=grant.id,
        expected_grant_revision=grant.revision,
        contract=contract,
    )
    service = WorkService(repository)
    adapter_a = FakeAdapter("adapter-a")
    service.dispatch(
        work["id"],
        adapter_a.send,
        admission=TransitionEvidence(
            generation=attempt["generation"],
            expected_generation=attempt["generation"],
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
        actor_id="owner",
    )
    # Continuity starts after durable acceptance. Seed that trusted state here;
    # adapter telemetry booleans are intentionally not acceptance evidence.
    sent_attempt = repository.get_work(work["id"])["attempts"][-1]
    repository.transition_attempt(
        attempt_id=sent_attempt["id"],
        generation=sent_attempt["generation"],
        expected_revision=sent_attempt["revision"],
        expected_state="sent",
        target="acknowledged",
        actor_id=principal.id,
        event_id="continuation-fixture-task-accepted",
        evidence=TransitionEvidence(
            generation=sent_attempt["generation"],
            expected_generation=sent_attempt["generation"],
            task_received=True,
        ),
    )
    settled = adapter_a.execute(service, repository.get_work(work["id"]), artifacts)
    assert settled["state"] == "succeeded"
    assert settled["attempts"][-1]["state"] == "finished"
    assert adapter_a.sends == adapter_a.executions == adapter_a.callbacks == 1
    return (
        CompletedWork(
            database=database,
            artifacts_root=artifacts_root,
            repository=repository,
            artifacts=artifacts,
            principal=principal,
            grant=grant,
            binding=binding,
            work=settled,
        ),
        adapter_a,
    )


def _durable_snapshot(repository: WorkRepository, artifacts: ImmutableResultStore) -> tuple:
    with repository.connection() as connection:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        rows = tuple(
            (
                table,
                tuple(
                    tuple(row)
                    for row in connection.execute(
                        f'SELECT * FROM "{table.replace(chr(34), chr(34) * 2)}" ORDER BY rowid'
                    )
                ),
            )
            for table in tables
        )
    files = tuple(
        (path.name, path.read_bytes())
        for path in sorted(artifacts.root.iterdir(), key=lambda path: path.name)
        if path.is_file()
    )
    return rows, files


def _export(completed: CompletedWork) -> bytes:
    return WorkContinuations(completed.repository, completed.artifacts).export_continuation(
        principal=completed.principal,
        source_attempt_id=completed.binding.attempt_id,
        generation=completed.binding.generation,
        grant_id=completed.grant.id,
        expected_grant_revision=completed.grant.revision,
        reason="provider replacement",
    )


def _rehashed(payload: dict) -> bytes:
    unsigned = dict(payload)
    unsigned.pop("package_hash", None)
    encoded = json.dumps(unsigned, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return json.dumps(
        {**unsigned, "package_hash": hashlib.sha256(encoded).hexdigest()},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _import(continuity: WorkContinuations, completed: CompletedWork, package: bytes):
    return continuity.import_continuation(
        package,
        principal=completed.principal,
        grant_id=completed.grant.id,
        expected_grant_revision=completed.grant.revision,
    )


def test_completed_work_continues_to_a_reopened_fake_adapter_without_effects(tmp_path):
    """Would fail if completed continuity used the executable terminal gate or replayed A."""
    completed, adapter_a = _completed_work(tmp_path)
    package = _export(completed)
    before = _durable_snapshot(completed.repository, completed.artifacts)
    winner = completed.repository.get_result(completed.work["accepted_result_id"])

    adapter_b = FakeAdapter("adapter-b", effects_forbidden=True)
    repository_b = WorkRepository(completed.database)
    artifacts_b = ImmutableResultStore(completed.artifacts_root)
    continuity_b = WorkContinuations(repository_b, artifacts_b)
    first = continuity_b.import_continuation(
        package,
        principal=completed.principal,
        grant_id=completed.grant.id,
        expected_grant_revision=completed.grant.revision,
    )

    assert first.source_attempt_id == completed.binding.attempt_id
    assert first.source_generation == completed.binding.generation
    assert first.source_work_item_id == completed.work["id"]
    assert first.package_hash == hashlib.sha256(package).hexdigest()
    assert WorkService(repository_b).read_result(completed.work["id"], artifacts=artifacts_b) == _OUTPUT
    assert repository_b.get_result(completed.work["accepted_result_id"]) == winner
    assert continuity_b.import_continuation(
        package,
        principal=completed.principal,
        grant_id=completed.grant.id,
        expected_grant_revision=completed.grant.revision,
    ) == first

    repository_reopened = WorkRepository(completed.database)
    artifacts_reopened = ImmutableResultStore(completed.artifacts_root)
    continuity_reopened = WorkContinuations(repository_reopened, artifacts_reopened)
    assert continuity_reopened.export_continuation(
        principal=completed.principal,
        source_attempt_id=completed.binding.attempt_id,
        generation=completed.binding.generation,
        grant_id=completed.grant.id,
        expected_grant_revision=completed.grant.revision,
        reason="provider replacement",
    ) == package
    assert continuity_reopened.import_continuation(
        package,
        principal=completed.principal,
        grant_id=completed.grant.id,
        expected_grant_revision=completed.grant.revision,
    ) == first
    assert WorkService(repository_reopened).read_result(
        completed.work["id"], artifacts=artifacts_reopened
    ) == _OUTPUT

    with pytest.raises(ContractConflict):
        WorkContracts(repository_reopened).revalidate_order(
            completed.binding.attempt_id, generation=completed.binding.generation
        )
    assert WorkService(repository_reopened).dispatch(
        completed.work["id"],
        adapter_b.send,
        admission=TransitionEvidence(
            generation=completed.binding.generation,
            expected_generation=completed.binding.generation,
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
        actor_id="owner",
    )["state"] == "succeeded"
    assert adapter_a.sends == adapter_a.executions == adapter_a.callbacks == 1
    assert adapter_b.sends == adapter_b.executions == adapter_b.callbacks == 0
    assert _durable_snapshot(repository_reopened, artifacts_reopened) == before


def test_late_nonwinner_never_replaces_completed_result_or_causes_b_effect(tmp_path):
    """Would fail if continuity selected an artifact list entry instead of the finish-CAS winner."""
    completed, _ = _completed_work(tmp_path)
    winner = completed.repository.get_result(completed.work["accepted_result_id"])
    late_content = next(
        candidate
        for index in range(128)
        if (candidate := f"late nonwinner {index}".encode())
        and hashlib.sha256(candidate).hexdigest() < winner["content_hash"]
    )

    def register_late(reference: ArtifactRef):
        return completed.repository.register_result(
            attempt_id=completed.binding.attempt_id,
            generation=completed.binding.generation,
            content_hash=reference.content_hash,
            immutable_location=reference.immutable_location,
            byte_length=reference.byte_length,
            validator_id="late-evidence-validator",
            validation_evidence={"valid": True, "kind": "late"},
            actor_id="owner",
        )

    late = completed.artifacts.publish(late_content, register_late)
    assert completed.repository.get_work(completed.work["id"])["accepted_result_id"] == winner["id"]
    package = _export(completed)
    payload = json.loads(package)
    assert payload["completed"] == [completed.work["id"]]
    assert payload["artifacts"][0]["content_hash"] == late["content_hash"]

    adapter_b = FakeAdapter("adapter-b", effects_forbidden=True)
    repository_b = WorkRepository(completed.database)
    artifacts_b = ImmutableResultStore(completed.artifacts_root)
    continuity_b = WorkContinuations(repository_b, artifacts_b)
    assert _import(continuity_b, completed, package)
    assert WorkService(repository_b).read_result(completed.work["id"], artifacts=artifacts_b) == _OUTPUT

    nonwinner_only = dict(payload)
    nonwinner_only["artifacts"] = [payload["artifacts"][0]]
    before = _durable_snapshot(repository_b, artifacts_b)
    with pytest.raises(ContinuationRejected):
        _import(continuity_b, completed, _rehashed(nonwinner_only))
    assert adapter_b.sends == adapter_b.executions == adapter_b.callbacks == 0
    assert _durable_snapshot(repository_b, artifacts_b) == before


def test_equal_length_corruption_after_preflight_is_rejected_without_b_effect(tmp_path):
    """Would fail if a replay trusted an earlier preflight or a hash without current bytes."""
    completed, _ = _completed_work(tmp_path)
    package = _export(completed)
    adapter_b = FakeAdapter("adapter-b", effects_forbidden=True)
    repository_b = WorkRepository(completed.database)
    artifacts_b = ImmutableResultStore(completed.artifacts_root)
    continuity_b = WorkContinuations(repository_b, artifacts_b)
    assert _import(continuity_b, completed, package)

    winner = repository_b.get_result(completed.work["accepted_result_id"])
    path = artifacts_b.root / winner["immutable_location"]
    path.write_bytes(b"x" * winner["byte_length"])
    before = _durable_snapshot(repository_b, artifacts_b)
    with pytest.raises(ContinuationRejected):
        _import(continuity_b, completed, package)

    assert adapter_b.sends == adapter_b.executions == adapter_b.callbacks == 0
    assert _durable_snapshot(repository_b, artifacts_b) == before


def test_revoked_grant_after_first_import_is_rejected_without_b_effect(tmp_path):
    """Would fail if package hash replay cached authority after the first preflight."""
    completed, _ = _completed_work(tmp_path)
    package = _export(completed)
    adapter_b = FakeAdapter("adapter-b", effects_forbidden=True)
    repository_b = WorkRepository(completed.database)
    artifacts_b = ImmutableResultStore(completed.artifacts_root)
    continuity_b = WorkContinuations(repository_b, artifacts_b)
    assert _import(continuity_b, completed, package)

    WorkAuthority(repository_b).revoke(
        completed.principal,
        grant_id=completed.grant.id,
        expected_grant_revision=completed.grant.revision,
        reason="test revocation",
    )
    before = _durable_snapshot(repository_b, artifacts_b)
    with pytest.raises(ContinuationRejected):
        _import(continuity_b, completed, package)

    assert adapter_b.sends == adapter_b.executions == adapter_b.callbacks == 0
    assert _durable_snapshot(repository_b, artifacts_b) == before


def test_rehashed_binding_change_is_rejected_without_b_effect(tmp_path):
    """Would fail if a self-consistent package could replace the frozen binding."""
    completed, _ = _completed_work(tmp_path)
    package = json.loads(_export(completed))
    package["contract"]["hash"] = "b" * 64
    adapter_b = FakeAdapter("adapter-b", effects_forbidden=True)
    repository_b = WorkRepository(completed.database)
    artifacts_b = ImmutableResultStore(completed.artifacts_root)
    continuity_b = WorkContinuations(repository_b, artifacts_b)
    before = _durable_snapshot(repository_b, artifacts_b)

    with pytest.raises(ContinuationRejected):
        _import(continuity_b, completed, _rehashed(package))

    assert adapter_b.sends == adapter_b.executions == adapter_b.callbacks == 0
    assert _durable_snapshot(repository_b, artifacts_b) == before


def test_finished_work_without_the_finish_cas_winner_is_not_completed(tmp_path):
    """Would fail if terminal state alone, rather than accepted_result_id, meant completion."""
    completed, _ = _completed_work(tmp_path)
    with completed.repository.transaction() as connection:
        connection.execute(
            "UPDATE work_items SET accepted_result_id=NULL WHERE id=?", (completed.work["id"],)
        )
    before = _durable_snapshot(completed.repository, completed.artifacts)

    with pytest.raises(ContinuationRejected):
        _export(completed)

    assert _durable_snapshot(completed.repository, completed.artifacts) == before
