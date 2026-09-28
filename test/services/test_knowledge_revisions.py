"""Knowledge history is immutable, scoped, reviewed and never implicitly instructional."""

import importlib.util
import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.step_output_store import ImmutableResultStore


def test_knowledge_schema_is_exported_for_the_verified_migration():
    assert importlib.util.find_spec("cli_agent_orchestrator.clients.knowledge_schema") is not None


@pytest.fixture
def knowledge(tmp_path, monkeypatch):
    from cli_agent_orchestrator.services.knowledge_revisions import (
        KnowledgeRevisions,
        KnowledgeDenied,
    )

    monkeypatch.delenv("CAO_AUTH_JWKS_URI", raising=False)
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    principal = auth.local_operator_principal()
    repository = WorkRepository(tmp_path / "knowledge.db")
    repository.initialize()
    job = repository.create_job(
        project_id="project",
        principal_id=principal.id,
        allowed_providers=["codex"],
        grant_id="grant",
    )
    work = repository.admit_work(
        job_id=job["id"],
        operation_kind="test",
        idempotency_key="one",
        request_hash=hashlib.sha256(b"request").hexdigest(),
        contract_id="contract",
        snapshot_id=None,
        provider="codex",
        actor_id=principal.id,
    )
    attempt = work["attempts"][0]
    evidence = ImmutableResultStore(tmp_path / "artifacts").publish(
        b"reviewed evidence",
        lambda ref: repository.register_result(
            attempt_id=attempt["id"],
            generation=1,
            content_hash=ref.content_hash,
            immutable_location=ref.immutable_location,
            byte_length=ref.byte_length,
            validator_id="test",
            validation_evidence={"verified": True},
            actor_id=principal.id,
        ),
    )
    access = {"enabled": True}

    def policy(connection, actor, action, scope, scope_id):
        assert connection.in_transaction
        if (
            not access["enabled"]
            or actor.id != principal.id
            or str(scope.value) != "project"
            or scope_id != "project"
        ):
            raise KnowledgeDenied("denied")

    service = KnowledgeRevisions(repository, authorize=policy)
    return service, repository, principal, work, evidence, access


def propose(fixture, **overrides):
    service, _, principal, work, evidence, _ = fixture
    arguments = dict(
        scope="project",
        scope_id="project",
        record_id="record",
        expected_version=0,
        content="bounded knowledge",
        work_item_id=work["id"],
        attempt_id=work["attempts"][0]["id"],
        source_artifact_id=evidence["id"],
        evidence_refs=(evidence["id"],),
        confidence=0.8,
        fresh_until=time.time() + 3600,
    )
    arguments.update(overrides)
    return service.propose_revision(principal, **arguments)


def approve(fixture, revision):
    service, _, actor, _, evidence, _ = fixture
    return service.review_revision(
        actor,
        revision.record_id,
        revision.revision,
        "approved",
        expected_version=revision.record_version,
        examined_refs=(evidence["id"],),
    )


def test_proposed_and_verified_are_evidence_not_instructions(knowledge):
    service, _, actor, _, evidence, _ = knowledge
    initial = propose(knowledge)
    assert initial.decision.value == "proposed"
    assert service.instructions(actor, scope="project", scope_id="project") == ()
    verified = service.review_revision(
        actor, "record", 1, "verified", expected_version=1, examined_refs=(evidence["id"],)
    )
    assert verified.decision.value == "verified"
    assert service.instructions(actor, scope="project", scope_id="project") == ()
    approved = approve(knowledge, verified)
    assert (
        service.instructions(actor, scope="project", scope_id="project")[0].revision
        == approved.revision
    )


def test_new_revision_supersedes_old_without_inheriting_approval(knowledge):
    service, _, actor, _, _, _ = knowledge
    approved = approve(knowledge, propose(knowledge))
    new = propose(knowledge, expected_version=approved.record_version, content="new evidence")
    assert new.revision == 2 and new.decision.value == "proposed"
    assert service.read_revision(actor, "record", 1).decision.value == "superseded"
    assert service.instructions(actor, scope="project", scope_id="project") == ()


def test_expired_tombstoned_and_legacy_are_not_implicitly_instructions(knowledge):
    service, _, actor, _, _, _ = knowledge
    legacy = propose(knowledge, legacy=True)
    assert legacy.legacy and legacy.decision.value == "proposed"
    assert not service.instructions(actor, scope="project", scope_id="project")
    approved = approve(knowledge, legacy)
    retired = service.tombstone_revision(
        actor, "record", 1, expected_version=approved.record_version
    )
    assert retired.tombstone and retired.content is None
    assert not service.instructions(actor, scope="project", scope_id="project")
    expired = propose(knowledge, record_id="expired", fresh_until=time.time() - 1)
    approve(knowledge, expired)
    assert not service.instructions(actor, scope="project", scope_id="project")


def test_redaction_precedes_truncation_and_audit_has_no_content(knowledge):
    service, repository, actor, _, _, _ = knowledge
    from cli_agent_orchestrator.services.knowledge_revisions import KnowledgeRevisions

    service = KnowledgeRevisions(repository, authorize=service.authorize, max_content_bytes=8)
    raw = "xxxxxAKIAIOSFODNN7EXAMPLE"
    local = (service, *knowledge[1:])
    revision = propose(local, content=raw)
    assert revision.redacted and revision.truncated
    assert revision.content == "xxxxx[RE"
    assert revision.source_hash == hashlib.sha256(raw.encode()).hexdigest()
    assert revision.delivered_hash == hashlib.sha256(revision.content.encode()).hexdigest()
    with repository.connection() as connection:
        dump = "\n".join(connection.iterdump())
    assert "AKIAIOSFODNN7EXAMPLE" not in dump
    with repository.connection() as connection:
        event = dict(connection.execute("SELECT * FROM work_knowledge_events").fetchone())
    assert "content" not in event and raw not in json.dumps(event)


def test_missing_policy_and_spoofed_principal_fail_closed(knowledge):
    service, repository, actor, _, _, _ = knowledge
    from cli_agent_orchestrator.services.knowledge_revisions import (
        KnowledgeRevisions,
        KnowledgeDenied,
    )

    with pytest.raises(KnowledgeDenied):
        KnowledgeRevisions(repository).instructions(actor, scope="project", scope_id="project")
    with pytest.raises(KnowledgeDenied):
        service.instructions(
            {"id": actor.id, "scopes": ["cao:admin"]}, scope="project", scope_id="project"
        )


def test_revocation_is_checked_on_each_read_and_write(knowledge):
    service, _, actor, _, _, access = knowledge
    initial = propose(knowledge)
    access["enabled"] = False
    with pytest.raises(PermissionError):
        service.read_revision(actor, "record")
    with pytest.raises(PermissionError):
        approve(knowledge, initial)


def test_review_requires_actual_examined_evidence(knowledge):
    service, _, actor, _, _, _ = knowledge
    propose(knowledge)
    for refs in ((), ("fabricated",)):
        with pytest.raises(ValueError):
            service.review_revision(
                actor, "record", 1, "approved", expected_version=1, examined_refs=refs
            )


def test_cas_concurrent_proposals_have_one_winner(knowledge):
    propose(knowledge)

    def contender(content):
        try:
            return propose(knowledge, expected_version=1, content=content).revision
        except ValueError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(contender, ("first", "second")))
    assert sorted(results, key=str) == [2, "conflict"]


def test_failed_event_commit_rolls_back_revision_and_history_is_immutable(knowledge):
    _, repository, _, _, _, _ = knowledge
    first = propose(knowledge)
    with repository.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER refuse_knowledge BEFORE INSERT ON work_knowledge_events BEGIN SELECT RAISE(ABORT,'refused'); END"
        )
    # The extra trigger changes the verified schema only when named work_*.
    with pytest.raises(sqlite3.IntegrityError):
        propose(knowledge, expected_version=first.record_version)
    with repository.connection() as connection:
        assert (
            connection.execute("SELECT count(*) FROM work_knowledge_revisions").fetchone()[0] == 1
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE work_knowledge_revisions SET content='tampered'")


@pytest.mark.parametrize(
    "override",
    [
        {"confidence": float("nan")},
        {"confidence": True},
        {"confidence": 1.1},
        {"fresh_until": float("inf")},
        {"fresh_until": None},
    ],
)
def test_invalid_confidence_or_freshness_is_rejected(knowledge, override):
    with pytest.raises(ValueError):
        propose(knowledge, **override)


@pytest.mark.parametrize(
    "override",
    [
        {"work_item_id": "missing", "attempt_id": None, "source_artifact_id": None},
        {"attempt_id": "missing"},
        {"source_artifact_id": "missing"},
        {"evidence_refs": ("missing",)},
        {"legacy": "true"},
        {"expected_version": False},
        {"record_id": "AKIAIOSFODNN7EXAMPLE"},
    ],
)
def test_invalid_provenance_and_metadata_never_commit(knowledge, override):
    with pytest.raises(ValueError):
        propose(knowledge, **override)
    with knowledge[1].connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_knowledge_records").fetchone()[0] == 0


def test_cross_project_evidence_cannot_be_laundered_into_revision(knowledge):
    _, repository, actor, _, _, _ = knowledge
    job = repository.create_job(
        project_id="other", principal_id=actor.id, allowed_providers=["codex"], grant_id="other"
    )
    work = repository.admit_work(
        job_id=job["id"],
        operation_kind="test",
        idempotency_key="other",
        request_hash=hashlib.sha256(b"other").hexdigest(),
        contract_id="contract",
        snapshot_id=None,
        provider="codex",
        actor_id=actor.id,
    )
    with pytest.raises(ValueError):
        propose(
            knowledge,
            work_item_id=work["id"],
            attempt_id=work["attempts"][0]["id"],
            source_artifact_id=None,
        )


def test_oversized_input_is_rejected_before_any_redaction_or_persistence(knowledge, monkeypatch):
    from cli_agent_orchestrator.services import knowledge_revisions as module

    service = module.KnowledgeRevisions(
        knowledge[1], authorize=knowledge[0].authorize, max_input_bytes=8
    )

    def must_not_parse(value):
        pytest.fail("oversized input reached secret parser")

    monkeypatch.setattr(module, "redact_knowledge_content", must_not_parse)
    with pytest.raises(ValueError, match="byte limit"):
        propose((service, *knowledge[1:]), content="AKIAIOSFODNN7EXAMPLE")


def test_reopen_reads_frozen_content_and_labels_without_live_memory(knowledge):
    from cli_agent_orchestrator.services.knowledge_revisions import KnowledgeRevisions

    approved = approve(knowledge, propose(knowledge))
    reopened = KnowledgeRevisions(
        WorkRepository(knowledge[1].path), authorize=knowledge[0].authorize
    )
    assert reopened.read_revision(knowledge[2], "record") == approved
    assert reopened.instructions(knowledge[2], scope="project", scope_id="project") == (approved,)


def test_real_durable_policy_composes_and_revocation_blocks_later_reads(knowledge):
    from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
    from cli_agent_orchestrator.services.knowledge_revisions import KnowledgeRevisions
    from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority

    _, repository, actor, work, _, _ = knowledge
    authority = WorkAuthority(repository)
    grant = authority.issue_root(
        actor,
        job_id=work["job_id"],
        providers={"codex"},
        permissions=Permissions(
            tools={"knowledge.read", "knowledge.propose", "knowledge.review", "knowledge.tombstone"}
        ),
        expires_at=time.time() + 300,
    )
    policy = KnowledgePolicy(
        repository, job_id=work["job_id"], grant_id=grant.id, expected_grant_revision=1
    )
    service = KnowledgeRevisions(repository, authorize=policy)
    local = (service, *knowledge[1:])
    approved = approve(local, propose(local))
    assert service.instructions(actor, scope="project", scope_id="project") == (approved,)
    authority.revoke(actor, grant_id=grant.id, expected_grant_revision=1, reason="revoked")
    with pytest.raises(PermissionError):
        service.read_revision(actor, "record")
    with pytest.raises(PermissionError):
        service.instructions(actor, scope="project", scope_id="project")
    with pytest.raises(PermissionError):
        propose(local, expected_version=approved.record_version)


def test_invalid_enum_values_are_not_echoed_in_diagnostics(knowledge):
    secret = "AKIAIOSFODNN7EXAMPLE"
    with pytest.raises(ValueError) as caught:
        propose(knowledge, scope=secret)
    assert secret not in str(caught.value)
    propose(knowledge)
    with pytest.raises(ValueError) as caught:
        knowledge[0].review_revision(knowledge[2], "record", 1, secret, expected_version=1)
    assert secret not in str(caught.value)
