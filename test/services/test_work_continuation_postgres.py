"""The continuation export and preflight execute on the native Work store."""

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
from cli_agent_orchestrator.services.step_output_store import ImmutableResultStore
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_continuation import WorkContinuations
from cli_agent_orchestrator.services.work_contract import WorkContracts
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
from cli_agent_orchestrator.services.work_service import DeliveryObservation, WorkService

pytestmark = pytest.mark.postgres


def test_export_and_preflight_query_live_attempt_and_verified_artifacts(tmp_path, postgres_targets):
    """Would fail if continuation SQL used parameter markers outside psycopg's format."""
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    bootstrap_scope(postgres_targets.main)
    repository = WorkRepository(postgres_targets.main, filesystem_home=postgres_targets.home)
    repository.initialize()
    principal = auth._verified_principal(
        "https://issuer.test", "continuation-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    job = repository.create_job(
        project_id="continuation-project",
        principal_id=principal.id,
        allowed_providers=["continuation-provider"],
        grant_id="continuation-root",
    )
    grant = WorkAuthority(repository).issue_root(
        principal,
        job_id=job["id"],
        providers={"continuation-provider"},
        permissions=Permissions(tools={"Read", "knowledge.read"}, paths={str(tmp_path)}),
        expires_at=time.time() + 600,
    )
    snapshot = DelegationSnapshots(
        repository,
        policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision),
    ).freeze(
        principal=principal,
        job_id=job["id"],
        contract_id="continuation-contract",
        binding_key="continuation-binding",
        request_hash="a" * 64,
        scope="project",
        scope_id="continuation-project",
        resolver=lambda connection, actor: ResolvedSnapshot(""),
    )
    work = repository.admit_work(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key="continuation-launch",
        request_hash="a" * 64,
        contract_id="continuation-contract",
        snapshot_id=snapshot.id,
        provider="continuation-provider",
        actor_id=principal.id,
    )
    attempt = work["attempts"][0]
    contract = EffectiveWorkContract(
        id="continuation-contract",
        operation_kind="launch",
        provider="continuation-provider",
        backend="continuation-test-backend",
        permissions=ContractPermissions(tools=("Read",), paths=(str(tmp_path),)),
        resources=ContractResources(
            checkout_root=str(tmp_path),
            write_paths=(str(tmp_path / "output"),),
            units=1,
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
    service.dispatch(
        work["id"],
        lambda: DeliveryObservation(),
        admission=TransitionEvidence(
            generation=attempt["generation"],
            expected_generation=attempt["generation"],
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
        actor_id=principal.id,
    )
    sent_attempt = repository.get_work(work["id"])["attempts"][-1]
    repository.transition_attempt(
        attempt_id=sent_attempt["id"],
        generation=sent_attempt["generation"],
        expected_revision=sent_attempt["revision"],
        expected_state="sent",
        target="acknowledged",
        actor_id=principal.id,
        event_id="continuation-task-accepted",
        evidence=TransitionEvidence(
            generation=sent_attempt["generation"],
            expected_generation=sent_attempt["generation"],
            task_received=True,
        ),
    )

    artifact_store = ImmutableResultStore(tmp_path / "artifacts")
    artifact_bytes = b"verified continuation artifact"

    def register_result(reference):
        repository.register_result(
            attempt_id=attempt["id"],
            generation=attempt["generation"],
            content_hash=reference.content_hash,
            immutable_location=reference.immutable_location,
            byte_length=reference.byte_length,
            validator_id="continuation-test-validator",
            validation_evidence={
                "artifact_sha256": reference.content_hash,
                "bytes_verified": True,
                "byte_length": reference.byte_length,
            },
            actor_id=principal.id,
        )
        return reference

    artifact_store.publish(artifact_bytes, register_result)
    continuity = WorkContinuations(repository, artifact_store)
    package = continuity.export_continuation(
        principal=principal,
        source_attempt_id=binding.attempt_id,
        generation=binding.generation,
        grant_id=grant.id,
        expected_grant_revision=grant.revision,
        reason="provider replacement",
    )
    preflight = continuity.import_continuation(
        package,
        principal=principal,
        grant_id=grant.id,
        expected_grant_revision=grant.revision,
    )

    assert preflight.source_attempt_id == attempt["id"]
    assert preflight.source_generation == attempt["generation"]
    assert json.loads(package)["uncertain"] == [work["id"]]
    assert len(json.loads(package)["artifacts"]) == 1
