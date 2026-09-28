"""Authority decisions use real durable grants and fresh parent-chain checks."""

import importlib
import sqlite3
import time

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth


def context(tmp_path, monkeypatch):
    module = importlib.import_module("cli_agent_orchestrator.services.work_authority")
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    monkeypatch.delenv("CAO_AUTH_JWKS_URI", raising=False)
    actor = auth.local_operator_principal()
    repo = WorkRepository(tmp_path / "authority.sqlite3")
    repo.initialize()
    job = repo.create_job(
        project_id="project",
        principal_id=actor.id,
        allowed_providers=["codex", "claude_code"],
        grant_id="root-grant",
    )
    authority = module.WorkAuthority(repo)
    permissions = module.Permissions(
        tools={"read", "edit"},
        paths={str(tmp_path)},
        commands={"pytest"},
        network={"example.test"},
        artifacts={"project"},
    )
    grant = authority.issue_root(
        actor,
        job_id=job["id"],
        providers={"codex", "claude_code"},
        permissions=permissions,
        expires_at=time.time() + 300,
    )
    return module, actor, repo, job, authority, grant


def test_authorize_helper_uses_callers_transaction_and_revalidates_principal(tmp_path, monkeypatch):
    module, actor, repo, job, authority, grant = context(tmp_path, monkeypatch)
    arguments = dict(
        job_id=job["id"],
        grant_id=grant.id,
        expected_grant_revision=1,
        provider="codex",
        requested_permissions=module.Permissions(),
    )
    with repo.connection() as connection:
        with pytest.raises(ValueError):
            authority._authorize(connection, actor, **arguments)
    with pytest.raises(RuntimeError, match="rollback caller"):
        with repo.transaction() as connection:
            repo._verify(connection)
            connection.execute("UPDATE work_jobs SET priority=9 WHERE id=?", (job["id"],))
            with pytest.raises(module.AuthorityDenied):
                authority._authorize(connection, {"id": actor.id}, **arguments)
            assert authority._authorize(connection, actor, **arguments).id == grant.id
            assert connection.in_transaction
            raise RuntimeError("rollback caller")
    assert repo.get_job(job["id"])["priority"] == 0


def test_authority_schema_exports_immutable_grants_and_parent_references():
    schema = importlib.import_module("cli_agent_orchestrator.clients.work_authority_schema")
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("CREATE TABLE work_jobs(id TEXT PRIMARY KEY)")
        for statement in schema.AUTHORITY_SCHEMA:
            connection.execute(statement)
        assert connection.execute("PRAGMA foreign_key_list(work_grants)").fetchall()
        assert connection.execute("SELECT count(*) FROM work_grants").fetchone()[0] == 0


def test_spoofed_principal_body_never_creates_authority(tmp_path, monkeypatch):
    module, actor, repo, job, authority, grant = context(tmp_path, monkeypatch)
    with pytest.raises(module.AuthorityDenied):
        authority.authorize(
            {"id": actor.id, "caller_id": actor.id, "scopes": ["cao:admin"]},
            job_id=job["id"],
            grant_id=grant.id,
            expected_grant_revision=1,
            provider="codex",
            requested_permissions=module.Permissions(),
        )
    with pytest.raises(module.AuthorityDenied):
        authority.issue_root(
            {"id": actor.id, "scopes": ["cao:admin"]},
            job_id=job["id"],
            providers={"codex"},
            permissions=module.Permissions(),
            expires_at=time.time() + 60,
        )


@pytest.mark.parametrize(
    "dimension,value",
    [
        ("tools", "shell"),
        ("commands", "rm"),
        ("network", "evil.test"),
        ("artifacts", "other-project"),
    ],
)
def test_child_cannot_widen_permissions(tmp_path, monkeypatch, dimension, value):
    module, actor, repo, job, authority, grant = context(tmp_path, monkeypatch)
    with pytest.raises(module.AuthorityDenied):
        authority.delegate(
            actor,
            parent_grant_id=grant.id,
            expected_parent_revision=1,
            child_principal=actor,
            providers={"codex"},
            permissions=module.Permissions(**{dimension: {value}}),
            expires_at=time.time() + 100,
        )


def test_child_paths_normalize_symlinks_and_cannot_escape_parent(tmp_path, monkeypatch):
    module, actor, repo, job, authority, grant = context(tmp_path, monkeypatch)
    (tmp_path / "escape").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(module.AuthorityDenied):
        authority.delegate(
            actor,
            parent_grant_id=grant.id,
            expected_parent_revision=1,
            child_principal=actor,
            providers={"codex"},
            permissions=module.Permissions(paths={str(tmp_path / "escape" / "elsewhere")}),
            expires_at=time.time() + 100,
        )


def test_child_inherits_narrowing_and_rechecks_parent_revocation_before_effect(
    tmp_path, monkeypatch
):
    module, actor, repo, job, authority, grant = context(tmp_path, monkeypatch)
    child = authority.delegate(
        actor,
        parent_grant_id=grant.id,
        expected_parent_revision=1,
        child_principal=actor,
        providers={"codex"},
        permissions=module.Permissions(tools={"read"}),
        expires_at=time.time() + 100,
    )
    arguments = dict(
        job_id=job["id"],
        grant_id=child.id,
        expected_grant_revision=1,
        provider="codex",
        requested_permissions=module.Permissions(tools={"read"}),
    )
    assert authority.authorize(actor, **arguments).id == child.id
    authority.revoke(actor, grant_id=grant.id, expected_grant_revision=1, reason="operator revoked")
    restarted = module.WorkAuthority(WorkRepository(repo.path))
    with pytest.raises(module.AuthorityDenied):
        restarted.authorize(actor, **arguments)


def test_grant_revision_provider_and_expiry_fail_closed(tmp_path, monkeypatch):
    module, actor, repo, job, authority, grant = context(tmp_path, monkeypatch)
    base = dict(
        job_id=job["id"],
        grant_id=grant.id,
        expected_grant_revision=1,
        provider="codex",
        requested_permissions=module.Permissions(),
    )
    with pytest.raises(module.GrantConflict):
        authority.authorize(actor, **{**base, "expected_grant_revision": 2})
    with pytest.raises(module.AuthorityDenied):
        authority.authorize(actor, **{**base, "provider": "unapproved"})
    monkeypatch.setattr(module.time, "time", lambda: grant.expires_at + 1)
    with pytest.raises(module.AuthorityDenied):
        authority.authorize(actor, **base)


def test_job_provider_allowlist_is_checked_again_not_only_at_grant_creation(tmp_path, monkeypatch):
    module, actor, repo, job, authority, grant = context(tmp_path, monkeypatch)
    with repo.transaction() as connection:
        connection.execute("UPDATE work_jobs SET allowed_providers='[]' WHERE id=?", (job["id"],))
    with pytest.raises(module.AuthorityDenied):
        authority.authorize(
            actor,
            job_id=job["id"],
            grant_id=grant.id,
            expected_grant_revision=1,
            provider="codex",
            requested_permissions=module.Permissions(),
        )


def test_grant_rows_cannot_be_overwritten_or_deleted(tmp_path, monkeypatch):
    module, actor, repo, job, authority, grant = context(tmp_path, monkeypatch)
    with repo.connection() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE work_grants SET permissions='{}' WHERE id=?", (grant.id,))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM work_grants WHERE id=?", (grant.id,))


def test_permissions_default_deny_and_none_is_not_unrestricted(tmp_path, monkeypatch):
    module, actor, repo, job, authority, grant = context(tmp_path, monkeypatch)
    assert module.Permissions().tools == frozenset()
    with pytest.raises(ValueError):
        module.Permissions(tools=None)


def test_child_cannot_widen_provider_allowlist_or_expiry(tmp_path, monkeypatch):
    module, actor, repo, job, authority, grant = context(tmp_path, monkeypatch)
    base = dict(
        parent_grant_id=grant.id,
        expected_parent_revision=1,
        child_principal=actor,
        providers={"codex"},
        permissions=module.Permissions(),
        expires_at=time.time() + 100,
    )
    for override in ({"providers": {"unapproved"}}, {"expires_at": grant.expires_at + 60}):
        with pytest.raises(module.AuthorityDenied):
            authority.delegate(actor, **{**base, **override})


def test_disconnected_root_cannot_authorize_after_job_binding_changes(tmp_path, monkeypatch):
    module, actor, repo, job, authority, grant = context(tmp_path, monkeypatch)
    with repo.transaction() as connection:
        connection.execute("UPDATE work_jobs SET grant_id='replacement' WHERE id=?", (job["id"],))
    with pytest.raises(module.AuthorityDenied):
        authority.authorize(
            actor,
            job_id=job["id"],
            grant_id=grant.id,
            expected_grant_revision=1,
            provider="codex",
            requested_permissions=module.Permissions(),
        )


def test_managed_origin_subject_cannot_bypass_delegate_without_live_action(tmp_path, monkeypatch):
    """Direct grant delegation remains legacy-compatible unless a subject is managed."""
    module, actor, repo, job, authority, grant = context(tmp_path, monkeypatch)
    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    assert repository_module.SCHEMA_VERSION >= 23, "T019 v23 integrity migration is missing"
    origin = importlib.import_module("cli_agent_orchestrator.services.work_origin")
    subject = auth._verified_principal(
        "https://issuer.test", "managed-child", [auth.SCOPE_WRITE], "jwt"
    )
    child_grant = authority.delegate(
        actor,
        parent_grant_id=grant.id,
        expected_parent_revision=grant.revision,
        child_principal=subject,
        providers={"codex"},
        permissions=module.Permissions(tools={"read"}),
        expires_at=time.time() + 120,
    )
    service = origin.WorkOriginAuthority(repo)
    service.register_subject(
        actor,
        verified_subject=subject,
        kind="child",
        issuer_id=actor.id,
        expected_revision=0,
    )
    with pytest.raises(module.AuthorityDenied):
        authority.delegate(
            subject,
            parent_grant_id=child_grant.id,
            expected_parent_revision=child_grant.revision,
            child_principal=auth._verified_principal(
                "https://issuer.test", "blocked-grandchild", [auth.SCOPE_WRITE], "jwt"
            ),
            providers={"codex"},
            permissions=module.Permissions(tools={"read"}),
            expires_at=time.time() + 60,
        )
