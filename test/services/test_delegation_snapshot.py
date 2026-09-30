"""Durable snapshot ordering, authority, sibling races and explicit legacy absence."""

import hashlib
import importlib
import multiprocessing
import sqlite3
import time
from test.clients.test_work_repository import (
    legacy_admit,
    legacy_v21_store,
    migrate_legacy_v21_store,
)

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security.auth import local_operator_principal
from cli_agent_orchestrator.services.knowledge_policy import KnowledgeAccessDenied, KnowledgePolicy
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority


def setup(tmp_path, monkeypatch, *, legacy=False, **limits):
    module = importlib.import_module("cli_agent_orchestrator.services.delegation_snapshot")
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    monkeypatch.delenv("CAO_AUTH_JWKS_URI", raising=False)
    actor = local_operator_principal()
    path = tmp_path / "snapshots.sqlite3"
    repo = legacy_v21_store(path) if legacy else WorkRepository(path)
    if not legacy:
        repo.initialize()
    job = repo.create_job(
        project_id="project", principal_id=actor.id, allowed_providers=["mock_cli"], grant_id="root"
    )
    WorkAuthority(repo).issue_root(
        actor,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read", "knowledge.propose", "knowledge.tombstone"}
        ),
        expires_at=time.time() + 3600,
    )
    policy = KnowledgePolicy(repo, job["id"], "root", 1)
    store = module.DelegationSnapshots(repo, policy=policy, **limits)
    arguments = dict(
        principal=actor,
        job_id=job["id"],
        contract_id="contract",
        binding_key="siblings",
        request_hash="a" * 64,
        scope="job",
        scope_id=job["id"],
    )
    return module, repo, store, arguments


def test_empty_snapshot_is_resolved_once_and_survives_restart(tmp_path, monkeypatch):
    module, repo, store, args = setup(tmp_path, monkeypatch)
    calls = []

    def resolve(connection, principal):
        assert connection.in_transaction
        calls.append(principal.id)
        return module.ResolvedSnapshot("")

    first = store.freeze(**args, resolver=resolve)
    second = module.DelegationSnapshots(repo, policy=store.policy).freeze(**args, resolver=resolve)
    assert first == second and first.content == b"" and len(calls) == 1
    assert first.source_hash == hashlib.sha256(b"").hexdigest()
    assert first.delivered_hash == first.source_hash


def test_redaction_precedes_utf8_truncation_and_preserves_distinct_hashes(tmp_path, monkeypatch):
    module, repo, store, args = setup(tmp_path, monkeypatch, max_content_bytes=24)
    source = "prefix " + "AKIA1234567890ABCDEF" + " trailing text"
    result = store.freeze(**args, resolver=lambda c, p: module.ResolvedSnapshot(source))
    assert result.redacted and result.truncated
    assert b"AKIA" not in result.content and len(result.content) <= 24
    assert result.source_hash == hashlib.sha256(source.encode()).hexdigest()
    assert result.delivered_hash == hashlib.sha256(result.content).hexdigest()
    assert result.source_hash != result.delivered_hash
    with repo.connection() as connection:
        assert (
            connection.execute("SELECT content FROM work_delegation_snapshots").fetchone()[0]
            == result.content
        )
    assert source not in str(repo.read_events(args["job_id"]))


def test_changed_request_binding_conflicts_without_resolving_again(tmp_path, monkeypatch):
    module, repo, store, args = setup(tmp_path, monkeypatch)
    store.freeze(**args, resolver=lambda c, p: module.ResolvedSnapshot("first"))
    args["request_hash"] = "b" * 64
    with pytest.raises(module.SnapshotConflict):
        store.freeze(**args, resolver=lambda c, p: pytest.fail("must not resolve changed request"))


def test_event_failure_rolls_back_snapshot_and_delivers_nothing(tmp_path, monkeypatch):
    module, repo, store, args = setup(tmp_path, monkeypatch)
    with repo.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER fail_snapshot BEFORE INSERT ON work_events WHEN NEW.event_type='snapshot.frozen' BEGIN SELECT RAISE(ABORT,'event unavailable'); END"
        )
    with pytest.raises(module.SnapshotUnavailable):
        store.freeze(**args, resolver=lambda c, p: module.ResolvedSnapshot("must not escape"))
    with repo.connection() as connection:
        assert (
            connection.execute("SELECT count(*) FROM work_delegation_snapshots").fetchone()[0] == 0
        )


def test_policy_and_live_grant_required_on_replay(tmp_path, monkeypatch):
    module, repo, store, args = setup(tmp_path, monkeypatch)
    with pytest.raises(KnowledgeAccessDenied):
        module.DelegationSnapshots(repo, policy=lambda *a: True)
    first = store.freeze(**args, resolver=lambda c, p: module.ResolvedSnapshot("frozen"))
    WorkAuthority(repo).revoke(
        args["principal"], grant_id="root", expected_grant_revision=1, reason="revoke"
    )
    with pytest.raises(KnowledgeAccessDenied):
        store.read(args["principal"], first.id)
    with pytest.raises(KnowledgeAccessDenied):
        store.freeze(**args, resolver=lambda c, p: pytest.fail("must not resolve revoked grant"))


def test_children_reuse_exact_parent_snapshot_and_legacy_absence_is_explicit(tmp_path, monkeypatch):
    module, repo, store, args = setup(tmp_path, monkeypatch)
    snapshot = store.freeze(**args, resolver=lambda c, p: module.ResolvedSnapshot("parent bytes"))

    def parent(key, snapshot_id):
        return repo.admit_work(
            job_id=args["job_id"],
            operation_kind="parent",
            idempotency_key=key,
            request_hash=hashlib.sha256(key.encode()).hexdigest(),
            contract_id="contract",
            snapshot_id=snapshot_id,
            provider="mock_cli",
            actor_id=args["principal"].id,
        )

    bound = parent("bound", snapshot.id)
    assert store.child_snapshot(args["principal"], bound["id"]) == snapshot
    assert store.child_snapshot(args["principal"], bound["id"]).content == b"parent bytes"
    legacy = parent("legacy", None)
    absence = store.child_snapshot(args["principal"], legacy["id"])
    assert isinstance(absence, module.LegacySnapshotAbsence)
    assert absence.parent_work_id == legacy["id"]


def test_grandchildren_inherit_snapshot_through_distinct_contracts(tmp_path, monkeypatch):
    module, repo, store, args = setup(tmp_path, monkeypatch, legacy=True)
    snapshot = store.freeze(**args, resolver=lambda c, p: module.ResolvedSnapshot("origin bytes"))
    parent_id = None
    descendants = []
    for contract_id in ("contract", "child-contract", "grandchild-contract"):
        work = legacy_admit(
            repo,
            {"id": args["job_id"]},
            operation_kind="launch",
            idempotency_key=contract_id,
            request_hash=hashlib.sha256(contract_id.encode()).hexdigest(),
            contract_id=contract_id,
            snapshot_id=snapshot.id,
            provider="mock_cli",
            actor_id=args["principal"].id,
            parent_work_item_id=parent_id,
        )
        parent_id = work["id"]
        descendants.append(work)
    migrate_legacy_v21_store(repo)
    for parent in descendants:
        assert store.child_snapshot(args["principal"], parent["id"]) == snapshot
    # Missing origin or a forged cycle must not turn same-job bytes into ancestry.
    with repo.transaction() as connection:
        connection.execute(
            "UPDATE work_items SET parent_work_item_id=? WHERE id=?",
            (descendants[2]["id"], descendants[1]["id"]),
        )
    with pytest.raises(module.SnapshotConflict):
        store.child_snapshot(args["principal"], descendants[2]["id"])


def test_distinct_contract_without_origin_ancestor_cannot_reuse_snapshot(tmp_path, monkeypatch):
    module, repo, store, args = setup(tmp_path, monkeypatch)
    snapshot = store.freeze(**args, resolver=lambda c, p: module.ResolvedSnapshot("origin"))
    unrelated = repo.admit_work(
        job_id=args["job_id"],
        operation_kind="launch",
        idempotency_key="unrelated",
        request_hash="a" * 64,
        contract_id="unrelated-contract",
        snapshot_id=snapshot.id,
        provider="mock_cli",
        actor_id=args["principal"].id,
    )
    with pytest.raises(module.SnapshotConflict):
        store.child_snapshot(args["principal"], unrelated["id"])


def _race(database, actor, job_id, barrier, counter, queue):
    module = importlib.import_module("cli_agent_orchestrator.services.delegation_snapshot")
    repo = WorkRepository(database)
    store = module.DelegationSnapshots(repo, policy=KnowledgePolicy(repo, job_id, "root", 1))

    def resolve(connection, principal):
        with counter.get_lock():
            counter.value += 1
        return module.ResolvedSnapshot("same bytes")

    try:
        barrier.wait(10)
        result = store.freeze(
            principal=actor,
            job_id=job_id,
            contract_id="contract",
            binding_key="siblings",
            request_hash="a" * 64,
            scope="job",
            scope_id=job_id,
            resolver=resolve,
        )
        queue.put((result.id, result.content))
    except Exception as error:
        queue.put(("error", type(error).__name__))


def test_process_siblings_resolve_once_and_share_committed_winner(tmp_path, monkeypatch):
    module, repo, store, args = setup(tmp_path, monkeypatch)
    context = multiprocessing.get_context("fork")
    barrier, counter, queue = context.Barrier(3), context.Value("i", 0), context.Queue()
    processes = [
        context.Process(
            target=_race,
            args=(repo.path, args["principal"], args["job_id"], barrier, counter, queue),
        )
        for _ in range(3)
    ]
    for process in processes:
        process.start()
    try:
        outcomes = [queue.get(timeout=20) for _ in processes]
        assert len(set(outcomes)) == 1 and outcomes[0][1] == b"same bytes"
        assert counter.value == 1
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)


def test_snapshot_source_tombstone_denies_replay(tmp_path, monkeypatch):
    from cli_agent_orchestrator.services.knowledge_revisions import KnowledgeRevisions

    module, repo, store, args = setup(tmp_path, monkeypatch)
    revisions = KnowledgeRevisions(repo, authorize=store.policy)
    revision = revisions.propose_revision(
        args["principal"],
        record_id="source",
        scope="job",
        scope_id=args["job_id"],
        expected_version=0,
        content="context",
        evidence_refs=(),
        confidence=0.5,
        fresh_until=time.time() + 300,
    )
    result = store.freeze(
        **args,
        resolver=lambda c, p: module.ResolvedSnapshot(
            "context", (module.SourceRevision("source", 1),)
        ),
    )
    revisions.tombstone_revision(
        args["principal"], "source", 1, expected_version=revision.record_version
    )
    with pytest.raises(module.SnapshotUnavailable):
        store.read(args["principal"], result.id)


@pytest.mark.parametrize("content", ["oversized", True])
def test_oversized_or_untyped_resolution_blocks_delivery(tmp_path, monkeypatch, content):
    module, repo, store, args = setup(tmp_path, monkeypatch, max_input_bytes=4)
    resolved = module.ResolvedSnapshot(content) if isinstance(content, str) else content
    with pytest.raises(module.SnapshotUnavailable):
        store.freeze(**args, resolver=lambda c, p: resolved)
    with repo.connection() as connection:
        assert (
            connection.execute("SELECT count(*) FROM work_delegation_snapshots").fetchone()[0] == 0
        )


def test_utf8_truncation_never_returns_partial_character(tmp_path, monkeypatch):
    module, repo, store, args = setup(tmp_path, monkeypatch, max_content_bytes=3)
    result = store.freeze(**args, resolver=lambda c, p: module.ResolvedSnapshot("éé"))
    assert result.content == "é".encode() and result.truncated


def test_resolver_error_does_not_echo_secret_or_create_snapshot(tmp_path, monkeypatch):
    module, repo, store, args = setup(tmp_path, monkeypatch)

    def resolver(connection, principal):
        raise RuntimeError("password=do-not-persist-this")

    with pytest.raises(module.SnapshotUnavailable) as error:
        store.freeze(**args, resolver=resolver)
    assert "do-not-persist" not in str(error.value)
    assert error.value.__suppress_context__


def test_spoofed_identity_or_foreign_scope_denied_before_resolver(tmp_path, monkeypatch):
    module, repo, store, args = setup(tmp_path, monkeypatch)
    for overrides in ({"principal": {"id": args["principal"].id}}, {"scope_id": "another-job"}):
        forged = dict(args, **overrides)
        with pytest.raises(KnowledgeAccessDenied):
            store.freeze(**forged, resolver=lambda c, p: pytest.fail("unauthorized resolution"))


def test_source_reference_must_exist_before_any_snapshot_is_committed(tmp_path, monkeypatch):
    module, repo, store, args = setup(tmp_path, monkeypatch)
    with pytest.raises(module.SnapshotUnavailable):
        store.freeze(
            **args,
            resolver=lambda c, p: module.ResolvedSnapshot(
                "context", (module.SourceRevision("missing", 1),)
            ),
        )
    with repo.connection() as connection:
        assert (
            connection.execute("SELECT count(*) FROM work_delegation_snapshots").fetchone()[0] == 0
        )


def test_internal_snapshot_loader_requires_transaction_and_validates_hash(tmp_path, monkeypatch):
    module, repo, store, args = setup(tmp_path, monkeypatch)
    snapshot = store.freeze(**args, resolver=lambda c, p: module.ResolvedSnapshot("stored"))
    with repo.connection() as connection:
        row = connection.execute(
            "SELECT * FROM work_delegation_snapshots WHERE id=?", (snapshot.id,)
        ).fetchone()
        with pytest.raises(module.SnapshotUnavailable, match="transaction"):
            module.DelegationSnapshots._load_authorized(connection, row)
    with repo.read_snapshot() as connection:
        assert module.DelegationSnapshots._load_authorized(connection, row) == snapshot
        corrupt = dict(row, content=b"tampered")
        with pytest.raises(module.SnapshotUnavailable, match="integrity"):
            module.DelegationSnapshots._load_authorized(connection, corrupt)


def test_internal_snapshot_loader_rejects_withdrawn_sources(tmp_path, monkeypatch):
    from cli_agent_orchestrator.services.knowledge_revisions import KnowledgeRevisions

    module, repo, store, args = setup(tmp_path, monkeypatch)
    revisions = KnowledgeRevisions(repo, authorize=store.policy)
    revision = revisions.propose_revision(
        args["principal"],
        record_id="withdraw-source",
        scope="job",
        scope_id=args["job_id"],
        expected_version=0,
        content="context",
        confidence=0.5,
        fresh_until=time.time() + 300,
    )
    snapshot = store.freeze(
        **args,
        resolver=lambda c, p: module.ResolvedSnapshot(
            "context", (module.SourceRevision("withdraw-source", 1),)
        ),
    )
    revisions.tombstone_revision(
        args["principal"], "withdraw-source", 1, expected_version=revision.record_version
    )
    with repo.read_snapshot() as connection:
        row = connection.execute(
            "SELECT * FROM work_delegation_snapshots WHERE id=?", (snapshot.id,)
        ).fetchone()
        with pytest.raises(module.SnapshotUnavailable, match="withdrawn"):
            module.DelegationSnapshots._load_authorized(connection, row)
