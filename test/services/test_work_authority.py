"""Authority decisions use real PostgreSQL grants and fresh parent-chain checks."""

import importlib
import time

import pytest

from cli_agent_orchestrator.clients.postgres import IntegrityError
from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth

pytestmark = pytest.mark.postgres


@pytest.fixture
def authority_context(tmp_path, monkeypatch, postgres_targets):
    module = importlib.import_module("cli_agent_orchestrator.services.work_authority")
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    monkeypatch.delenv("CAO_AUTH_JWKS_URI", raising=False)
    target = postgres_targets.main
    bootstrap_scope(target)
    actor = auth.local_operator_principal()
    repo = WorkRepository(target, filesystem_home=postgres_targets.home)
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


def test_authorize_helper_uses_callers_transaction_and_revalidates_principal(authority_context):
    module, actor, repo, job, authority, grant = authority_context
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
            connection.execute("UPDATE work_jobs SET priority=9 WHERE id=%s", (job["id"],))
            with pytest.raises(module.AuthorityDenied):
                authority._authorize(connection, {"id": actor.id}, **arguments)
            assert authority._authorize(connection, actor, **arguments).id == grant.id
            assert connection.in_transaction
            raise RuntimeError("rollback caller")
    assert repo.get_job(job["id"])["priority"] == 0


def test_authority_schema_has_immutable_grants_and_parent_references(authority_context):
    _module, _actor, repo, _job, _authority, _grant = authority_context
    with repo.connection() as connection:
        foreign_keys = connection.execute(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid='work_grants'::regclass AND contype='f'"
        ).fetchall()
        triggers = connection.execute(
            "SELECT tgname FROM pg_trigger "
            "WHERE tgrelid='work_grants'::regclass AND NOT tgisinternal"
        ).fetchall()
        assert len(foreign_keys) >= 3
        assert {row["tgname"] for row in triggers} >= {
            "work_grants_immutable_update",
            "work_grants_immutable_delete",
        }
        assert connection.execute("SELECT count(*) FROM work_grants").fetchone()[0] == 1


def test_spoofed_principal_body_never_creates_authority(authority_context):
    module, actor, _repo, job, authority, _grant = authority_context
    with pytest.raises(module.AuthorityDenied):
        authority.authorize(
            {"id": actor.id, "caller_id": actor.id, "scopes": ["cao:admin"]},
            job_id=job["id"],
            grant_id="root-grant",
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
def test_child_cannot_widen_permissions(authority_context, dimension, value):
    module, actor, _repo, _job, authority, grant = authority_context
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


def test_child_paths_normalize_symlinks_and_cannot_escape_parent(tmp_path, authority_context):
    module, actor, _repo, _job, authority, grant = authority_context
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


def test_child_inherits_narrowing_and_rechecks_parent_revocation_before_effect(authority_context):
    module, actor, repo, job, authority, grant = authority_context
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
    restarted = module.WorkAuthority(
        WorkRepository(repo.target, filesystem_home=repo.filesystem_home)
    )
    with pytest.raises(module.AuthorityDenied):
        restarted.authorize(actor, **arguments)


def test_grant_revision_provider_and_expiry_fail_closed(monkeypatch, authority_context):
    module, actor, _repo, job, authority, grant = authority_context
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


def test_job_provider_allowlist_is_checked_again_not_only_at_grant_creation(authority_context):
    module, actor, repo, job, authority, grant = authority_context
    with repo.transaction() as connection:
        connection.execute("UPDATE work_jobs SET allowed_providers='[]' WHERE id=%s", (job["id"],))
    with pytest.raises(module.AuthorityDenied):
        authority.authorize(
            actor,
            job_id=job["id"],
            grant_id=grant.id,
            expected_grant_revision=1,
            provider="codex",
            requested_permissions=module.Permissions(),
        )


def test_grant_rows_cannot_be_overwritten_or_deleted(authority_context):
    _module, _actor, repo, _job, _authority, grant = authority_context
    with pytest.raises(IntegrityError):
        with repo.transaction() as connection:
            connection.execute("UPDATE work_grants SET permissions='{}' WHERE id=%s", (grant.id,))
    with pytest.raises(IntegrityError):
        with repo.transaction() as connection:
            connection.execute("DELETE FROM work_grants WHERE id=%s", (grant.id,))


def test_permissions_default_deny_and_none_is_not_unrestricted():
    module = importlib.import_module("cli_agent_orchestrator.services.work_authority")
    assert module.Permissions().tools == frozenset()
    with pytest.raises(ValueError):
        module.Permissions(tools=None)


def test_child_cannot_widen_provider_allowlist_or_expiry(authority_context):
    module, actor, _repo, _job, authority, grant = authority_context
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


def test_disconnected_root_cannot_authorize_after_job_binding_changes(authority_context):
    module, actor, repo, job, authority, grant = authority_context
    with repo.transaction() as connection:
        connection.execute("UPDATE work_jobs SET grant_id='replacement' WHERE id=%s", (job["id"],))
    with pytest.raises(module.AuthorityDenied):
        authority.authorize(
            actor,
            job_id=job["id"],
            grant_id=grant.id,
            expected_grant_revision=1,
            provider="codex",
            requested_permissions=module.Permissions(),
        )


def test_managed_origin_subject_cannot_bypass_delegate_without_live_action(authority_context):
    """Direct grant delegation remains compatible unless a subject is managed."""
    module, actor, repo, job, authority, grant = authority_context
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
    origin = importlib.import_module("cli_agent_orchestrator.services.work_origin")
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
