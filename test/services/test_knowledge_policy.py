"""Knowledge ACL uses a live durable grant in the caller's transaction."""

import importlib
import importlib.util
import time

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority


@pytest.fixture
def policy_context(tmp_path):
    return lambda: _policy_context(tmp_path)


def _policy_context(tmp_path):
    name = "cli_agent_orchestrator.services.knowledge_policy"
    assert importlib.util.find_spec(name), "knowledge authorization boundary is missing"
    module = importlib.import_module(name)
    owner = auth._verified_principal("https://issuer.test", "owner", [auth.SCOPE_ADMIN], "jwt")
    repository = WorkRepository(tmp_path / "knowledge.db")
    repository.initialize()
    job = repository.create_job(
        project_id="project",
        principal_id=owner.id,
        allowed_providers=["mock_cli"],
        grant_id="root",
    )
    authority = WorkAuthority(repository)
    grant = authority.issue_root(
        owner,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read", "knowledge.propose", "knowledge.review", "knowledge.tombstone"}
        ),
        expires_at=time.time() + 300,
    )
    policy = module.KnowledgePolicy(
        repository, job_id=job["id"], grant_id=grant.id, expected_grant_revision=1
    )
    return module, repository, owner, job, grant, authority, policy


@pytest.mark.parametrize("action", ["read", "instructions", "propose", "review", "tombstone"])
def test_grant_owner_can_access_only_bound_job_and_project(policy_context, action):
    module, repository, owner, job, grant, authority, policy = policy_context()
    with repository.transaction() as connection:
        assert policy(connection, owner, action, "job", job["id"]) is None
        assert policy(connection, owner, action, "project", "project") is None
        with pytest.raises(PermissionError):
            policy(connection, owner, action, "project", "other-project")
        with pytest.raises(PermissionError):
            policy(connection, owner, action, "job", "other-job")


def test_request_identity_or_caller_id_does_not_supply_authority(policy_context):
    module, repository, owner, job, grant, authority, policy = policy_context()
    with repository.transaction() as connection:
        with pytest.raises(PermissionError):
            policy(
                connection, {"id": owner.id, "caller_id": owner.id}, "read", "project", "project"
            )


def test_read_only_scope_cannot_review_or_publish(policy_context):
    module, repository, owner, job, grant, authority, policy = policy_context()
    reader = auth._verified_principal(owner.issuer, owner.subject, [auth.SCOPE_READ], "jwt")
    with repository.transaction() as connection:
        policy(connection, reader, "read", "project", "project")
        for action in ("review", "propose", "tombstone"):
            with pytest.raises(PermissionError):
                policy(connection, reader, action, "project", "project")


def test_narrow_child_and_parent_revocation_are_rechecked(policy_context):
    module, repository, owner, job, grant, authority, policy = policy_context()
    child = auth._verified_principal("https://issuer.test", "worker", [auth.SCOPE_WRITE], "jwt")
    delegated = authority.delegate(
        owner,
        parent_grant_id=grant.id,
        expected_parent_revision=1,
        child_principal=child,
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}),
        expires_at=time.time() + 100,
    )
    child_policy = module.KnowledgePolicy(
        repository, job_id=job["id"], grant_id=delegated.id, expected_grant_revision=1
    )
    with repository.transaction() as connection:
        child_policy(connection, child, "instructions", "project", "project")
        with pytest.raises(PermissionError):
            child_policy(connection, child, "propose", "project", "project")
        with pytest.raises(PermissionError):
            child_policy(connection, owner, "read", "project", "project")
    authority.revoke(owner, grant_id=grant.id, expected_grant_revision=1, reason="revoked")
    with repository.transaction() as connection:
        with pytest.raises(PermissionError):
            child_policy(connection, child, "read", "project", "project")


def test_no_admin_bypass_for_unknown_scope_action_or_stale_revision(policy_context):
    module, repository, owner, job, grant, authority, policy = policy_context()
    stale = module.KnowledgePolicy(
        repository, job_id=job["id"], grant_id=grant.id, expected_grant_revision=2
    )
    with repository.transaction() as connection:
        for action, scope in (("read", "global"), ("export_secrets", "project")):
            with pytest.raises(PermissionError):
                policy(connection, owner, action, scope, "project")
        with pytest.raises(PermissionError):
            stale(connection, owner, "read", "project", "project")


def test_policy_refuses_a_transaction_from_another_store(policy_context, tmp_path):
    module, repository, owner, job, grant, authority, policy = policy_context()
    other = WorkRepository(tmp_path / "other.db")
    other.initialize()
    with other.transaction() as connection:
        with pytest.raises(PermissionError):
            policy(connection, owner, "read", "project", "project")
