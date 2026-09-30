"""Durable cursor behavior exercised against the real SQLite authority."""

import sqlite3
import threading
import time
from test.services.test_knowledge_policy import policy_context  # noqa: F401

import pytest

from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.knowledge_revisions import (
    KnowledgeCursorExpired,
    KnowledgeCursorInvalid,
    KnowledgeDenied,
    KnowledgeRevisions,
)
from cli_agent_orchestrator.services.work_authority import Permissions


def _proposal(service, principal, record_id, expected_version=0):
    return service.propose_revision(
        principal,
        scope="project",
        scope_id="project",
        record_id=record_id,
        expected_version=expected_version,
        content=record_id,
        confidence=0.8,
        fresh_until=time.time() + 300,
    )


def test_recovery_cursor_ttl_is_absolute_and_survives_service_recreation(policy_context):
    """Would fail if pages renewed TTL or depended on process-local cursor state."""
    _, repository, owner, job, grant, _, policy = policy_context()
    writer = KnowledgeRevisions(repository, authorize=policy)
    for record_id in ("first", "second", "third"):
        _proposal(writer, owner, record_id)
    now = [100.0]
    reader = KnowledgeRevisions(
        repository, authorize=policy, cursor_ttl_seconds=10, clock=lambda: now[0]
    )
    first = reader.recovery_page(
        owner,
        scope="project",
        scope_id="project",
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=1,
        limit=1,
    )
    assert first["expires_at"] == 110.0
    now[0] = 105.0
    restarted_reader = KnowledgeRevisions(
        repository, authorize=policy, cursor_ttl_seconds=10, clock=lambda: now[0]
    )
    second = restarted_reader.recovery_page(
        owner,
        scope="project",
        scope_id="project",
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=1,
        limit=1,
        cursor=first["next_cursor"],
    )
    assert second["expires_at"] == 110.0
    assert second["next_cursor"] == first["next_cursor"]
    now[0] = 110.0
    with pytest.raises(KnowledgeCursorExpired):
        restarted_reader.recovery_page(
            owner,
            scope="project",
            scope_id="project",
            job_id=job["id"],
            grant_id=grant.id,
            grant_revision=1,
            limit=1,
            cursor=second["next_cursor"],
        )


def test_recovery_cursor_expires_while_waiting_for_sqlite_writer(policy_context):
    """Would fail if a continuation captured its expiry clock before BEGIN IMMEDIATE."""
    _, repository, owner, job, grant, _, policy = policy_context()
    writer = KnowledgeRevisions(repository, authorize=policy)
    for record_id in ("first", "second"):
        _proposal(writer, owner, record_id)
    now = [100.0]
    arm_clock = [False]
    clock_read = threading.Event()
    release_clock = threading.Event()

    def clock():
        sampled = now[0]
        if arm_clock[0]:
            clock_read.set()
            assert release_clock.wait(5)
        return sampled

    reader = KnowledgeRevisions(repository, authorize=policy, cursor_ttl_seconds=1, clock=clock)
    first = reader.recovery_page(
        owner,
        scope="project",
        scope_id="project",
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=1,
        limit=1,
    )
    arm_clock[0] = True
    outcome = {}
    worker_started = threading.Event()

    def continue_page():
        worker_started.set()
        try:
            outcome["page"] = reader.recovery_page(
                owner,
                scope="project",
                scope_id="project",
                job_id=job["id"],
                grant_id=grant.id,
                grant_revision=1,
                limit=1,
                cursor=first["next_cursor"],
            )
        except BaseException as error:
            outcome["error"] = error

    with repository.transaction():
        worker = threading.Thread(target=continue_page)
        worker.start()
        assert worker_started.wait(1)
        clock_read.wait(1)
        now[0] = 102.0
    release_clock.set()
    worker.join(5)
    assert not worker.is_alive()
    assert isinstance(outcome.get("error"), KnowledgeCursorExpired)
    assert "page" not in outcome
    with repository.connection() as connection:
        row = connection.execute(
            "SELECT last_record_id,last_revision FROM work_knowledge_cursors"
        ).fetchone()
    assert tuple(row) == ("first", 1)


def test_recovery_rechecks_a_revoked_ancestor_before_the_next_page(policy_context):
    """Would fail if a cursor authorization were cached after its first page."""
    _, repository, owner, job, grant, authority, owner_policy = policy_context()
    writer = KnowledgeRevisions(repository, authorize=owner_policy)
    for record_id in ("first", "second"):
        _proposal(writer, owner, record_id)
    child = auth._verified_principal("https://issuer.test", "reader", [auth.SCOPE_READ], "jwt")
    delegated = authority.delegate(
        owner,
        parent_grant_id=grant.id,
        expected_parent_revision=1,
        child_principal=child,
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}),
        expires_at=time.time() + 100,
    )
    child_policy = KnowledgePolicy(repository, job["id"], delegated.id, 1)
    reader = KnowledgeRevisions(repository, authorize=child_policy)
    first = reader.recovery_page(
        child,
        scope="project",
        scope_id="project",
        job_id=job["id"],
        grant_id=delegated.id,
        grant_revision=1,
        limit=1,
    )
    authority.revoke(owner, grant_id=grant.id, expected_grant_revision=1, reason="ancestor")
    with pytest.raises(PermissionError):
        reader.recovery_page(
            child,
            scope="project",
            scope_id="project",
            job_id=job["id"],
            grant_id=delegated.id,
            grant_revision=1,
            limit=1,
            cursor=first["next_cursor"],
        )


def test_recovery_cursor_rejects_other_scope_or_authority_and_kcr2(policy_context):
    """Would fail if opaque cursor context or its protocol version were ignored."""
    _, repository, owner, job, grant, authority, owner_policy = policy_context()
    writer = KnowledgeRevisions(repository, authorize=owner_policy)
    for record_id in ("first", "second"):
        _proposal(writer, owner, record_id)
    owner_reader = KnowledgeRevisions(repository, authorize=owner_policy)
    first = owner_reader.recovery_page(
        owner,
        scope="project",
        scope_id="project",
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=1,
        limit=1,
    )
    with pytest.raises(KnowledgeDenied):
        owner_reader.recovery_page(
            owner,
            scope="job",
            scope_id=job["id"],
            job_id=job["id"],
            grant_id=grant.id,
            grant_revision=1,
            limit=1,
            cursor=first["next_cursor"],
        )
    child = auth._verified_principal(
        "https://issuer.test", "other-reader", [auth.SCOPE_READ], "jwt"
    )
    delegated = authority.delegate(
        owner,
        parent_grant_id=grant.id,
        expected_parent_revision=1,
        child_principal=child,
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}),
        expires_at=time.time() + 100,
    )
    child_reader = KnowledgeRevisions(
        repository, authorize=KnowledgePolicy(repository, job["id"], delegated.id, 1)
    )
    with pytest.raises(KnowledgeDenied):
        child_reader.recovery_page(
            child,
            scope="project",
            scope_id="project",
            job_id=job["id"],
            grant_id=delegated.id,
            grant_revision=1,
            limit=1,
            cursor=first["next_cursor"],
        )
    with pytest.raises(KnowledgeCursorInvalid):
        owner_reader.recovery_page(
            owner,
            scope="project",
            scope_id="project",
            job_id=job["id"],
            grant_id=grant.id,
            grant_revision=1,
            limit=1,
            cursor="kcr2_" + "A" * 43,
        )


def test_recovery_cursor_persistence_failure_rolls_back_its_authorization_audit(
    policy_context, monkeypatch
):
    """Would fail if a cursor write error committed an allowed audit or page state."""
    from cli_agent_orchestrator.services import knowledge_revisions as revisions_module

    _, repository, owner, job, grant, _, policy = policy_context()
    writer = KnowledgeRevisions(repository, authorize=policy)
    for record_id in ("first", "second"):
        _proposal(writer, owner, record_id)
    monkeypatch.setattr(revisions_module.secrets, "token_urlsafe", lambda _: "A" * 43)
    reader = KnowledgeRevisions(repository, authorize=policy)
    first = reader.recovery_page(
        owner,
        scope="project",
        scope_id="project",
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=1,
        limit=1,
    )
    with repository.connection() as connection:
        audit_count = connection.execute(
            "SELECT count(*) FROM work_knowledge_access_audit"
        ).fetchone()[0]
        before = connection.execute(
            "SELECT last_record_id,last_revision FROM work_knowledge_cursors"
        ).fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        reader.recovery_page(
            owner,
            scope="project",
            scope_id="project",
            job_id=job["id"],
            grant_id=grant.id,
            grant_revision=1,
            limit=1,
        )
    with repository.connection() as connection:
        assert (
            connection.execute("SELECT count(*) FROM work_knowledge_access_audit").fetchone()[0]
            == audit_count
        )
        after = connection.execute(
            "SELECT last_record_id,last_revision FROM work_knowledge_cursors"
        ).fetchone()
    assert tuple(after) == tuple(before) == ("first", 1)
    assert first["next_cursor"] == "kcr1_" + "A" * 43
