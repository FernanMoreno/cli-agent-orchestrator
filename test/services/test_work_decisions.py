"""RED contract for durable human decisions (T048 / FR-016).

The future ``WorkDecisions`` boundary owns the durable HumanDecision record.
Every case starts from a real effective principal, live durable grant, frozen
snapshot and revalidated attempt binding.  The local recorder observes a
successful decision claim only; it neither grants authority nor models the
atomicity of an external effect.
"""

from __future__ import annotations

import importlib
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.work_authority import (
    AuthorityDenied,
    Permissions,
    WorkAuthority,
)
from cli_agent_orchestrator.services.work_contract import ContractConflict, WorkContracts
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence

EVIDENCE = ("evidence://quota-review/2026-09-23",)
EFFECT = "resume-attempt"


@dataclass
class LocalEffectRecorder:
    """A local observer, not an authority or external-effect transaction double."""

    applied: list[tuple[str, str]] = field(default_factory=list)

    def apply(self, *, decision_id: str, effect: str) -> None:
        self.applied.append((decision_id, effect))


@dataclass(frozen=True)
class VerifiedAttempt:
    """Identifiers returned from a real, durable contract binding."""

    work_item_id: str
    attempt_id: str
    generation: int
    contract_id: str
    snapshot_id: str
    contract_hash: str


@dataclass(frozen=True)
class DecisionContext:
    """SQLite authority and the initial bound attempt for one work item."""

    repository: WorkRepository
    actor: Any
    job: dict
    grant: Any
    snapshots: DelegationSnapshots
    original: VerifiedAttempt


def _contract_with_snapshot(
    *,
    repository: WorkRepository,
    actor: Any,
    job: dict,
    snapshots: DelegationSnapshots,
    label: str,
    request_hash: str,
    backend: str,
):
    """Create a fresh persisted snapshot and the contract that names it."""
    models = importlib.import_module("cli_agent_orchestrator.models.work_contract")
    contract_id = "decision-contract-" + label
    snapshot = snapshots.freeze(
        principal=actor,
        job_id=job["id"],
        contract_id=contract_id,
        binding_key="decision-context-" + label,
        request_hash=request_hash,
        scope="project",
        scope_id="project",
        resolver=lambda _connection, _principal: ResolvedSnapshot(""),
    )
    return models.EffectiveWorkContract(
        id=contract_id,
        operation_kind="launch",
        provider="mock_cli",
        backend=backend,
        permissions=models.ContractPermissions(
            tools=("Read",), paths=(str(repository.path.parent),)
        ),
        resources=models.ContractResources(
            checkout_root=str(repository.path.parent),
            write_paths=(str(repository.path.parent / ("target-" + label)),),
            units=1,
        ),
        snapshot=models.ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )


def _bind_attempt(
    *,
    repository: WorkRepository,
    actor: Any,
    grant: Any,
    item: dict,
    contract: Any,
) -> VerifiedAttempt:
    """Bind and then independently revalidate the item's latest attempt."""
    attempt = item["attempts"][-1]
    binding = WorkContracts(repository).bind(
        principal=actor,
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_attempt_revision=attempt["revision"],
        grant_id=grant.id,
        expected_grant_revision=grant.revision,
        contract=contract,
    )
    restored = WorkContracts(WorkRepository(repository.path)).revalidate_order(
        binding.attempt_id, generation=binding.generation
    )
    return VerifiedAttempt(
        work_item_id=item["id"],
        attempt_id=restored.attempt_id,
        generation=restored.generation,
        contract_id=restored.contract.id,
        snapshot_id=restored.contract.snapshot.id,
        contract_hash=restored.contract_hash,
    )


def _decision_context(tmp_path) -> DecisionContext:
    """An effective principal, durable grant and one currently bound attempt."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    actor = auth._verified_principal("https://issuer.test", "operator", [auth.SCOPE_ADMIN], "jwt")
    repository = WorkRepository(tmp_path / "work-decisions.sqlite3")
    repository.initialize()
    job = repository.create_job(
        project_id="project",
        principal_id=actor.id,
        allowed_providers=["mock_cli"],
        grant_id="root-grant",
    )
    authority = WorkAuthority(repository)
    grant = authority.issue_root(
        actor,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read", "Read"}, paths={str(tmp_path)}, artifacts={"project"}
        ),
        expires_at=time.time() + 600,
    )
    snapshots = DelegationSnapshots(
        repository, policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision)
    )
    contract = _contract_with_snapshot(
        repository=repository,
        actor=actor,
        job=job,
        snapshots=snapshots,
        label="original",
        request_hash="a" * 64,
        backend="test_backend_v1",
    )
    item = repository.admit_work(
        job_id=job["id"],
        operation_kind=contract.operation_kind,
        idempotency_key="decision-work-original",
        request_hash="a" * 64,
        contract_id=contract.id,
        snapshot_id=contract.snapshot.id,
        provider=contract.provider,
        actor_id=actor.id,
    )
    original = _bind_attempt(
        repository=repository, actor=actor, grant=grant, item=item, contract=contract
    )
    return DecisionContext(repository, actor, job, grant, snapshots, original)


def _prepare_revised_attempt(context: DecisionContext) -> VerifiedAttempt:
    """Fixture-only historical setup for a reconciled retry of the same work item.

    This deliberately prepares historical SQLite rows.  It is not a public
    replanning API, a claim of backend evidence, or a proof that external work
    stopped; the fixture has dispatched no effect.  The subsequent retry still
    uses real generation fencing, grant authorization, binding and revalidation.
    """
    prior = context.repository.get_attempt(context.original.attempt_id)
    work = context.repository.get_work(context.original.work_item_id)
    with context.repository.transaction() as connection:
        context.repository._verify(connection)
        changed_attempt = connection.execute(
            "UPDATE work_attempts SET state='reconcile',revision=revision+1 "
            "WHERE id=? AND generation=? AND revision=? AND state='planned'",
            (context.original.attempt_id, context.original.generation, prior["revision"]),
        )
        changed_work = connection.execute(
            "UPDATE work_items SET state='reconcile',revision=revision+1 WHERE id=? AND revision=?",
            (context.original.work_item_id, work["revision"]),
        )
        assert changed_attempt.rowcount == changed_work.rowcount == 1

    WorkAuthority(context.repository).authorize(
        context.actor,
        job_id=context.job["id"],
        grant_id=context.grant.id,
        expected_grant_revision=context.grant.revision,
        provider="mock_cli",
        requested_permissions=Permissions(
            tools={"Read"}, paths={str(context.repository.path.parent)}
        ),
    )
    reconciled = context.repository.get_work(context.original.work_item_id)
    retried = context.repository.retry_work(
        context.original.work_item_id,
        expected_revision=reconciled["revision"],
        provider="mock_cli",
        evidence=TransitionEvidence(
            generation=context.original.generation,
            expected_generation=context.original.generation,
            prior_stopped=True,
            reconciliation_authorized=True,
        ),
        actor_id=context.actor.id,
        lease_seconds=60,
    )
    retry = retried["attempts"][-1]
    assert retry["id"] != context.original.attempt_id
    assert retry["generation"] == context.original.generation + 1

    contract = _contract_with_snapshot(
        repository=context.repository,
        actor=context.actor,
        job=context.job,
        snapshots=context.snapshots,
        label="revised",
        request_hash="b" * 64,
        backend="test_backend_v2",
    )
    before_revision = context.repository.get_work(context.original.work_item_id)["revision"]
    with context.repository.transaction() as connection:
        context.repository._verify(connection)
        updated = connection.execute(
            "UPDATE work_items SET contract_id=?,snapshot_id=?,revision=revision+1 "
            "WHERE id=? AND revision=?",
            (contract.id, contract.snapshot.id, context.original.work_item_id, before_revision),
        )
        assert updated.rowcount == 1

    with context.repository.read_snapshot() as connection:
        original_binding = connection.execute(
            "SELECT contract_id,snapshot_id,contract_hash FROM work_dispatch_bindings "
            "WHERE attempt_id=? AND generation=?",
            (context.original.attempt_id, context.original.generation),
        ).fetchone()
    assert original_binding is not None
    assert tuple(original_binding) == (
        context.original.contract_id,
        context.original.snapshot_id,
        context.original.contract_hash,
    )
    with pytest.raises(ContractConflict):
        WorkContracts(WorkRepository(context.repository.path)).revalidate_order(
            context.original.attempt_id, generation=context.original.generation
        )

    revised = _bind_attempt(
        repository=context.repository,
        actor=context.actor,
        grant=context.grant,
        item=context.repository.get_work(context.original.work_item_id),
        contract=contract,
    )
    assert revised.work_item_id == context.original.work_item_id
    assert revised.attempt_id == retry["id"]
    assert revised.generation == context.original.generation + 1
    assert revised.contract_hash != context.original.contract_hash
    return revised


def _module():
    """Import at the public boundary so an absent service is a RED test failure."""
    return importlib.import_module("cli_agent_orchestrator.services.work_decisions")


def _decide(service, actor: Any, attempt: VerifiedAttempt, **overrides):
    request = dict(
        principal=actor,
        work_item_id=attempt.work_item_id,
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        idempotency_key="quota-resume-v1",
        evidence_refs=EVIDENCE,
        action="resume",
        reason="operator reviewed the quota evidence",
        authorized_effects=(EFFECT,),
    )
    request.update(overrides)
    return service.decide_work(
        **request,
    )


def _consume_and_apply(service, actor: Any, decision, attempt: VerifiedAttempt, recorder):
    """Apply only after an authenticated, bound attempt receives a new claim."""
    if service.consume_effect(
        principal=actor,
        decision_id=decision.id,
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        effect=EFFECT,
    ):
        recorder.apply(decision_id=decision.id, effect=EFFECT)
        return True
    return False


def test_repeated_decision_preserves_original_audit_and_consumes_effect_once(tmp_path):
    """Break caught: a retry overwrites the original decision or replays its effect."""
    context = _decision_context(tmp_path)
    module = _module()
    service = module.WorkDecisions(context.repository)

    first = _decide(service, context.actor, context.original)
    repeated = _decide(service, context.actor, context.original)

    assert repeated.id == first.id
    assert repeated.actor_id == context.actor.id
    assert repeated.created_at == first.created_at
    assert repeated.contract_hash == context.original.contract_hash
    assert repeated.evidence_refs == EVIDENCE
    assert repeated.reason == "operator reviewed the quota evidence"
    assert repeated.authorized_effects == (EFFECT,)

    effects = LocalEffectRecorder()
    assert _consume_and_apply(service, context.actor, first, context.original, effects)
    assert not _consume_and_apply(service, context.actor, repeated, context.original, effects)
    assert effects.applied == [(first.id, EFFECT)]

    restored = module.WorkDecisions(WorkRepository(context.repository.path)).get(first.id)
    assert restored.actor_id == context.actor.id
    assert restored.created_at == first.created_at
    assert restored.contract_hash == context.original.contract_hash
    assert restored.evidence_refs == EVIDENCE
    assert restored.authorized_effects == (EFFECT,)
    assert restored.consumed_at is not None


def test_changed_contract_revision_cannot_reuse_or_consume_old_decision(tmp_path):
    """Break caught: the same work item reuses a decision after its revision changes."""
    context = _decision_context(tmp_path)
    module = _module()
    service = module.WorkDecisions(context.repository)
    old_decision = _decide(service, context.actor, context.original)
    revised = _prepare_revised_attempt(context)
    revised_decision = _decide(service, context.actor, revised)

    assert revised.work_item_id == context.original.work_item_id
    assert revised_decision.id != old_decision.id
    assert revised_decision.contract_hash == revised.contract_hash
    assert revised_decision.contract_hash != old_decision.contract_hash

    effects = LocalEffectRecorder()
    with pytest.raises(module.DecisionConflict):
        _consume_and_apply(service, context.actor, old_decision, revised, effects)

    assert effects.applied == []
    assert service.get(old_decision.id).consumed_at is None
    assert _consume_and_apply(service, context.actor, revised_decision, revised, effects)
    assert not _consume_and_apply(service, context.actor, revised_decision, revised, effects)
    assert effects.applied == [(revised_decision.id, EFFECT)]


def test_revoked_decision_blocks_new_effect_and_retains_durable_audit(tmp_path):
    """Break caught: revocation permits a later effect or erases decision history."""
    context = _decision_context(tmp_path)
    module = _module()
    service = module.WorkDecisions(context.repository)
    decision = _decide(service, context.actor, context.original)

    revoked = service.revoke(
        principal=context.actor,
        decision_id=decision.id,
        reason="operator withdrew the resume approval",
    )

    effects = LocalEffectRecorder()
    with pytest.raises(module.DecisionRevoked):
        _consume_and_apply(service, context.actor, decision, context.original, effects)

    assert effects.applied == []
    restored = module.WorkDecisions(WorkRepository(context.repository.path)).get(decision.id)
    assert restored.id == decision.id
    assert restored.actor_id == context.actor.id
    assert restored.contract_hash == context.original.contract_hash
    assert restored.evidence_refs == EVIDENCE
    assert restored.authorized_effects == (EFFECT,)
    assert restored.consumed_at is None
    assert restored.revoked_at == revoked.revoked_at
    assert restored.revoked_at is not None


def test_divergent_idempotency_payload_preserves_first_decision_and_audit(tmp_path):
    """Break caught: an idempotency replay overwrites immutable human evidence."""
    context = _decision_context(tmp_path)
    module = _module()
    service = module.WorkDecisions(context.repository)

    first = _decide(service, context.actor, context.original)
    with pytest.raises(module.DecisionConflict):
        _decide(
            service,
            context.actor,
            context.original,
            reason="different operator rationale",
        )

    restored = service.get(first.id)
    assert restored.reason == "operator reviewed the quota evidence"
    with context.repository.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM work_human_decisions").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT count(*) FROM work_events WHERE event_type='decision.recorded'"
            ).fetchone()[0]
            == 1
        )


def test_two_concurrent_consumers_claim_one_local_effect_once(tmp_path):
    """Break caught: racing local consumers both receive a successful claim."""
    context = _decision_context(tmp_path)
    module = _module()
    decision = module.WorkDecisions(context.repository).decide_work(
        principal=context.actor,
        work_item_id=context.original.work_item_id,
        attempt_id=context.original.attempt_id,
        generation=context.original.generation,
        idempotency_key="concurrent-resume-v1",
        evidence_refs=EVIDENCE,
        action="resume",
        reason="operator reviewed concurrent claim",
        authorized_effects=(EFFECT,),
    )

    def consume():
        return module.WorkDecisions(WorkRepository(context.repository.path)).consume_effect(
            principal=context.actor,
            decision_id=decision.id,
            attempt_id=context.original.attempt_id,
            generation=context.original.generation,
            effect=EFFECT,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _unused: consume(), range(2)))

    assert sorted(outcomes) == [False, True]
    with context.repository.read_snapshot() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_human_decision_claims WHERE decision_id=?",
                (decision.id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM work_events WHERE event_type='decision.effect_claimed'"
            ).fetchone()[0]
            == 1
        )


def test_consumed_effect_survives_later_revocation_but_other_effects_do_not(tmp_path):
    """Break caught: revocation deletes history or fails to block a later effect."""
    context = _decision_context(tmp_path)
    module = _module()
    service = module.WorkDecisions(context.repository)
    decision = _decide(
        service,
        context.actor,
        context.original,
        idempotency_key="two-effects-v1",
        authorized_effects=(EFFECT, "finalize-attempt"),
    )

    assert service.consume_effect(
        principal=context.actor,
        decision_id=decision.id,
        attempt_id=context.original.attempt_id,
        generation=context.original.generation,
        effect=EFFECT,
    )
    revoked = service.revoke(
        principal=context.actor,
        decision_id=decision.id,
        reason="operator withdrew remaining approval",
    )
    assert (
        service.revoke(
            principal=context.actor,
            decision_id=decision.id,
            reason="operator withdrew remaining approval",
        )
        == revoked
    )
    with pytest.raises(module.DecisionConflict):
        service.revoke(
            principal=context.actor,
            decision_id=decision.id,
            reason="a conflicting revocation rationale",
        )
    with pytest.raises(module.DecisionRevoked):
        service.consume_effect(
            principal=context.actor,
            decision_id=decision.id,
            attempt_id=context.original.attempt_id,
            generation=context.original.generation,
            effect="finalize-attempt",
        )

    restored = service.get(decision.id)
    assert restored.consumed_at is not None
    assert restored.revoked_at == revoked.revoked_at
    with context.repository.read_snapshot() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_human_decision_claims WHERE decision_id=?",
                (decision.id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM work_events WHERE event_type='decision.revoked'"
            ).fetchone()[0]
            == 1
        )


def test_invalid_principal_revoked_grant_and_expired_lease_cannot_claim(tmp_path):
    """Break caught: stale authority or an expired attempt can still claim an effect."""
    module = _module()

    untrusted = _decision_context(tmp_path / "untrusted")
    untrusted_service = module.WorkDecisions(untrusted.repository)
    outsider = auth._verified_principal(
        "https://issuer.test", "outsider", [auth.SCOPE_WRITE], "jwt"
    )
    with pytest.raises(AuthorityDenied):
        _decide(untrusted_service, outsider, untrusted.original)

    revoked_grant = _decision_context(tmp_path / "revoked-grant")
    revoked_service = module.WorkDecisions(revoked_grant.repository)
    revoked_decision = _decide(revoked_service, revoked_grant.actor, revoked_grant.original)
    WorkAuthority(revoked_grant.repository).revoke(
        revoked_grant.actor,
        grant_id=revoked_grant.grant.id,
        expected_grant_revision=revoked_grant.grant.revision,
        reason="grant is no longer active",
    )
    with pytest.raises(AuthorityDenied):
        _consume_and_apply(
            revoked_service,
            revoked_grant.actor,
            revoked_decision,
            revoked_grant.original,
            LocalEffectRecorder(),
        )

    expired_lease = _decision_context(tmp_path / "expired-lease")
    lease_service = module.WorkDecisions(expired_lease.repository)
    lease_decision = _decide(lease_service, expired_lease.actor, expired_lease.original)
    with expired_lease.repository.transaction() as connection:
        connection.execute(
            "UPDATE work_attempts SET lease_expires_at=? WHERE id=?",
            (time.time() - 1, expired_lease.original.attempt_id),
        )
    with pytest.raises(ContractConflict):
        _consume_and_apply(
            lease_service,
            expired_lease.actor,
            lease_decision,
            expired_lease.original,
            LocalEffectRecorder(),
        )
    for repository, decision in (
        (revoked_grant.repository, revoked_decision),
        (expired_lease.repository, lease_decision),
    ):
        with repository.read_snapshot() as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM work_human_decision_claims WHERE decision_id=?",
                    (decision.id,),
                ).fetchone()[0]
                == 0
            )


def test_original_actor_can_revoke_historical_decision_without_live_binding(tmp_path):
    """Break caught: revocation wrongly needs a live lease or unrevoked grant."""
    context = _decision_context(tmp_path)
    module = _module()
    service = module.WorkDecisions(context.repository)
    decision = _decide(service, context.actor, context.original)

    with context.repository.transaction() as connection:
        connection.execute(
            "UPDATE work_attempts SET lease_expires_at=? WHERE id=?",
            (time.time() - 1, context.original.attempt_id),
        )
    WorkAuthority(context.repository).revoke(
        context.actor,
        grant_id=context.grant.id,
        expected_grant_revision=context.grant.revision,
        reason="grant no longer authorizes future effects",
    )

    revocation = service.revoke(
        principal=context.actor,
        decision_id=decision.id,
        reason="operator withdrew historical approval",
    )

    assert revocation.decision_id == decision.id
    assert service.get(decision.id).revoked_at == revocation.revoked_at
    with context.repository.read_snapshot() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_human_decision_revocations WHERE decision_id=?",
                (decision.id,),
            ).fetchone()[0]
            == 1
        )


def test_non_owner_cannot_claim_another_principals_durable_decision(tmp_path):
    """Break caught: a valid but unrelated principal can consume the receipt."""
    context = _decision_context(tmp_path)
    module = _module()
    decision = _decide(module.WorkDecisions(context.repository), context.actor, context.original)
    outsider = auth._verified_principal(
        "https://issuer.test", "another-operator", [auth.SCOPE_WRITE], "jwt"
    )

    with pytest.raises(AuthorityDenied):
        module.WorkDecisions(WorkRepository(context.repository.path)).consume_effect(
            principal=outsider,
            decision_id=decision.id,
            attempt_id=context.original.attempt_id,
            generation=context.original.generation,
            effect=EFFECT,
        )

    with context.repository.read_snapshot() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM work_human_decision_claims WHERE decision_id=?",
                (decision.id,),
            ).fetchone()[0]
            == 0
        )


def test_v17_migration_preserves_rows_and_verifies_decision_history_schema(tmp_path):
    """Break caught: v18 mutates prior history or accepts altered immutable schema."""
    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    path = tmp_path / "v17-work.sqlite3"
    repository = WorkRepository(path)
    with sqlite3.connect(path) as connection:
        for version in range(1, 18):
            for statement in repository_module._MIGRATIONS[version]:
                connection.execute(statement)
            if version == 16:
                repository._initialize_inbox_store_context(connection)
            connection.execute(
                "INSERT INTO work_migrations VALUES (?,?,?,?)",
                (version, repository_module._CHECKSUMS[version], float(version), "verified"),
            )
        connection.execute(
            "INSERT INTO work_jobs "
            "(id,project_id,principal_id,allowed_providers,grant_id,created_at) "
            "VALUES ('v17-job','project','owner','[]','root-grant',1)"
        )
        prior_ledger = connection.execute(
            "SELECT * FROM work_migrations WHERE version<=17 ORDER BY version"
        ).fetchall()
        prior_job = connection.execute("SELECT * FROM work_jobs WHERE id='v17-job'").fetchone()

    repository.initialize()
    repository.verify_schema()
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT * FROM work_migrations WHERE version<=17 ORDER BY version"
            ).fetchall()
            == prior_ledger
        )
        assert (
            connection.execute("SELECT * FROM work_jobs WHERE id='v17-job'").fetchone() == prior_job
        )
        assert connection.execute(
            "SELECT version FROM work_migrations ORDER BY version"
        ).fetchall() == [(number,) for number in range(1, repository_module.SCHEMA_VERSION + 1)]
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='work_human_decision_claims'"
        ).fetchone() == ("work_human_decision_claims",)
        connection.execute("DROP TRIGGER work_human_decisions_immutable_update")
    with pytest.raises(repository_module.SchemaMismatch):
        repository.verify_schema()


def test_v18_migration_ledger_rejects_altered_history(tmp_path):
    """Break caught: an altered v18 ledger is trusted after an additive upgrade."""
    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    path = tmp_path / "altered-v18-ledger.sqlite3"
    repository = WorkRepository(path)
    repository.initialize()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE work_migrations SET checksum='altered' WHERE version=18")

    with pytest.raises(repository_module.SchemaMismatch):
        repository.verify_schema()
