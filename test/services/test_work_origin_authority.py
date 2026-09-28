"""T093 stage 2 origin-subject authority uses real SQLite grants and history."""

import importlib
import importlib.util
import sqlite3
import time

import pytest
from pydantic import ValidationError

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority


def origin_module():
    """An absent service remains a behavioral RED assertion, never an import error."""
    name = "cli_agent_orchestrator.services.work_origin"
    assert importlib.util.find_spec(name) is not None, "origin authority service is missing"
    return importlib.import_module(name)


def origin_models():
    name = "cli_agent_orchestrator.models.work_origin"
    assert importlib.util.find_spec(name) is not None, "origin authority models are missing"
    return importlib.import_module(name)


def root_grant(repository, root, owner, subject, *, name):
    job = repository.create_job(
        project_id=f"project-{name}",
        principal_id=owner.id,
        allowed_providers=["mock_cli"],
        grant_id=f"root-{name}",
    )
    authority = WorkAuthority(repository)
    grant = authority.issue_root(
        owner,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(tools={"read"}, paths={str(root)}, commands={"pytest"}),
        expires_at=time.time() + 600,
    )
    subject_grant = authority.delegate(
        owner,
        parent_grant_id=grant.id,
        expected_parent_revision=grant.revision,
        child_principal=subject,
        providers={"mock_cli"},
        permissions=Permissions(tools={"read"}),
        expires_at=time.time() + 300,
    )
    return authority, job, grant, subject_grant


def setup(tmp_path):
    repository = WorkRepository(tmp_path / "origin-authority.sqlite3")
    repository.initialize()
    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    assert repository_module.SCHEMA_VERSION >= 23, "T019 v23 requires verified schema"
    module = origin_module()
    owner = auth._verified_principal("https://issuer.test", "owner", [auth.SCOPE_ADMIN], "jwt")
    subject = auth._verified_principal(
        "https://issuer.test", "child-subject", [auth.SCOPE_WRITE], "jwt"
    )
    authority, job, root_grant_ref, subject_grant = root_grant(
        repository, tmp_path, owner, subject, name="one"
    )
    return module, repository, owner, subject, authority, job, root_grant_ref, subject_grant


def issuer_setup(tmp_path):
    """Two durable admins deliberately receive separate jobs for the same subject."""
    repository = WorkRepository(tmp_path / "issuer-authority.sqlite3")
    repository.initialize()
    module = origin_module()
    first_owner = auth._verified_principal(
        "https://issuer.test", "issuer-owner-a", [auth.SCOPE_ADMIN], "jwt"
    )
    second_owner = auth._verified_principal(
        "https://issuer.test", "issuer-owner-b", [auth.SCOPE_ADMIN], "jwt"
    )
    subject = auth._verified_principal(
        "https://issuer.test", "shared-child-subject", [auth.SCOPE_WRITE], "jwt"
    )
    first_authority, first_job, first_root, first_grant = root_grant(
        repository, tmp_path, first_owner, subject, name="issuer-a"
    )
    second_authority, second_job, second_root, second_grant = root_grant(
        repository, tmp_path, second_owner, subject, name="issuer-b"
    )
    return (
        module,
        repository,
        first_owner,
        second_owner,
        subject,
        first_authority,
        first_job,
        first_root,
        first_grant,
        second_authority,
        second_job,
        second_root,
        second_grant,
    )


def origin_arguments(subject, grant, *, expected_revision, actions=None):
    if actions is None:
        actions = {"admit_child"}
    return {
        "subject": subject,
        "origin_kind": "child",
        "grant_id": grant.id,
        "grant_revision": grant.revision,
        "actions": actions,
        "expires_at": time.time() + 120,
        "expected_revision": expected_revision,
    }


def origin_history_counts(repository):
    with repository.read_snapshot() as connection:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("work_origin_subjects", "work_origin_authorizations")
        )


def test_subject_issuer_cannot_transfer_or_authorize_from_another_admin(tmp_path):
    """Removing issuer checks lets B take subject S despite B's valid own job/grant."""
    (
        module,
        repository,
        first_owner,
        second_owner,
        subject,
        _,
        _,
        _,
        first_grant,
        _,
        _,
        _,
        second_grant,
    ) = issuer_setup(tmp_path)
    service = module.WorkOriginAuthority(repository)
    service.register_subject(
        first_owner,
        verified_subject=subject,
        kind="child",
        issuer_id=first_owner.id,
        expected_revision=0,
    )
    assert origin_history_counts(repository) == (1, 0)
    with pytest.raises(module.OriginDenied):
        service.register_subject(
            second_owner,
            verified_subject=subject,
            kind="child",
            issuer_id=second_owner.id,
            expected_revision=1,
        )
    with pytest.raises(module.OriginDenied):
        service.authorize(
            second_owner,
            **origin_arguments(subject, second_grant, expected_revision=0),
        )
    assert origin_history_counts(repository) == (1, 0)
    first_authorization = service.authorize(
        first_owner,
        **origin_arguments(subject, first_grant, expected_revision=0),
    )
    with pytest.raises(module.OriginDenied):
        service.authorize(
            second_owner,
            **origin_arguments(subject, second_grant, expected_revision=1),
        )
    with pytest.raises(module.OriginDenied):
        service.revoke(
            owner=second_owner,
            subject=subject,
            origin_kind="child",
            expected_revision=first_authorization.ref.revision,
        )
    assert origin_history_counts(repository) == (1, 1)


def test_incoherent_issuer_history_cannot_resolve_or_bypass_direct_delegate(tmp_path):
    """Removing consumption checks would let a fixture-only B row issue a grandchild grant."""
    (
        module,
        repository,
        first_owner,
        second_owner,
        subject,
        _,
        _,
        _,
        first_grant,
        second_authority,
        second_job,
        _,
        second_grant,
    ) = issuer_setup(tmp_path)
    service = module.WorkOriginAuthority(repository)
    service.register_subject(
        first_owner,
        verified_subject=subject,
        kind="child",
        issuer_id=first_owner.id,
        expected_revision=0,
    )
    service.authorize(
        first_owner,
        **origin_arguments(subject, first_grant, expected_revision=0, actions={"delegate"}),
    )
    with repository.transaction() as connection:
        connection.execute(
            """INSERT INTO work_origin_authorizations
               (subject_id,origin_kind,revision,schema_version,state,issuer_id,subject_revision,
                job_id,grant_id,grant_revision,actions,expires_at,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                subject.id,
                "child",
                2,
                1,
                "active",
                second_owner.id,
                1,
                second_job["id"],
                second_grant.id,
                second_grant.revision,
                '["delegate"]',
                time.time() + 120,
                time.time(),
            ),
        )
    with pytest.raises(module.OriginDenied):
        service.authorize(
            first_owner,
            **origin_arguments(subject, first_grant, expected_revision=2),
        )
    with pytest.raises(module.OriginDenied):
        service.resolve(
            subject,
            origin_kind="child",
            job_id=second_job["id"],
            grant_id=second_grant.id,
            grant_revision=second_grant.revision,
            action="delegate",
        )
    with repository.read_snapshot() as connection:
        grants_before = connection.execute("SELECT count(*) FROM work_grants").fetchone()[0]
    with pytest.raises(PermissionError):
        second_authority.delegate(
            subject,
            parent_grant_id=second_grant.id,
            expected_parent_revision=second_grant.revision,
            child_principal=auth._verified_principal(
                "https://issuer.test", "incoherent-grandchild", [auth.SCOPE_WRITE], "jwt"
            ),
            providers={"mock_cli"},
            permissions=Permissions(tools={"read"}),
            expires_at=time.time() + 60,
        )
    with repository.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM work_grants").fetchone()[0] == grants_before


def test_same_issuer_reauthorizes_across_own_jobs_and_revokes_after_grant_expiry(
    tmp_path, monkeypatch
):
    """Replacing current issuer/history checks breaks CAS fencing or leaves stale grants usable."""
    (
        module,
        repository,
        first_owner,
        _,
        subject,
        _,
        first_job,
        _,
        first_grant,
        _,
        _,
        _,
        _,
    ) = issuer_setup(tmp_path)
    service = module.WorkOriginAuthority(repository)
    first_subject = service.register_subject(
        first_owner,
        verified_subject=subject,
        kind="child",
        issuer_id=first_owner.id,
        expected_revision=0,
    )
    first_authorization = service.authorize(
        first_owner,
        **origin_arguments(subject, first_grant, expected_revision=0, actions={"delegate"}),
    )
    second_subject = service.register_subject(
        first_owner,
        verified_subject=subject,
        kind="child",
        issuer_id=first_owner.id,
        expected_revision=first_subject.revision,
    )
    assert second_subject.revision == 2
    with pytest.raises(module.OriginDenied):
        service.resolve(
            subject,
            origin_kind="child",
            job_id=first_job["id"],
            grant_id=first_grant.id,
            grant_revision=first_grant.revision,
            action="delegate",
        )
    second_authorization = service.authorize(
        first_owner,
        **origin_arguments(
            subject, first_grant, expected_revision=first_authorization.ref.revision
        ),
    )
    _, second_job, _, second_grant = root_grant(
        repository, tmp_path, first_owner, subject, name="issuer-a-replacement"
    )
    with pytest.raises(module.OriginConflict):
        service.authorize(
            first_owner,
            **origin_arguments(subject, second_grant, expected_revision=1),
        )
    assert origin_history_counts(repository) == (2, 2)
    replacement = service.authorize(
        first_owner,
        **origin_arguments(
            subject,
            second_grant,
            expected_revision=second_authorization.ref.revision,
            actions={"delegate"},
        ),
    )
    with pytest.raises(module.OriginDenied):
        service.resolve(
            subject,
            origin_kind="child",
            job_id=first_job["id"],
            grant_id=first_grant.id,
            grant_revision=first_grant.revision,
            action="delegate",
        )
    assert (
        service.resolve(
            subject,
            origin_kind="child",
            job_id=second_job["id"],
            grant_id=second_grant.id,
            grant_revision=second_grant.revision,
            action="delegate",
        ).ref
        == replacement.ref
    )
    monkeypatch.setattr(module.time, "time", lambda: second_grant.expires_at + 1)
    revoked = service.revoke(
        owner=first_owner,
        subject=subject,
        origin_kind="child",
        expected_revision=replacement.ref.revision,
    )
    assert revoked.revision == replacement.ref.revision + 1


def test_managed_subject_requires_live_explicit_delegate_authorization_across_restart(
    tmp_path, monkeypatch
):
    """Removing the managed-subject gate must make direct delegation succeed incorrectly."""
    module, repository, owner, subject, authority, job, _, subject_grant = setup(tmp_path)
    service = module.WorkOriginAuthority(repository)
    subject_ref = service.register_subject(
        owner,
        verified_subject=subject,
        kind="child",
        issuer_id=owner.id,
        expected_revision=0,
    )
    authorization = service.authorize(
        owner,
        subject=subject,
        origin_kind="child",
        grant_id=subject_grant.id,
        grant_revision=subject_grant.revision,
        actions={"admit_child"},
        expires_at=time.time() + 120,
        expected_revision=0,
    )
    assert subject_ref.revision == authorization.subject_revision == 1
    with pytest.raises(PermissionError):
        authority.delegate(
            subject,
            parent_grant_id=subject_grant.id,
            expected_parent_revision=subject_grant.revision,
            child_principal=auth._verified_principal(
                "https://issuer.test", "grandchild-denied", [auth.SCOPE_WRITE], "jwt"
            ),
            providers={"mock_cli"},
            permissions=Permissions(tools={"read"}),
            expires_at=time.time() + 60,
        )

    permitted = service.authorize(
        owner,
        subject=subject,
        origin_kind="child",
        grant_id=subject_grant.id,
        grant_revision=subject_grant.revision,
        actions={"admit_child", "delegate"},
        expires_at=time.time() + 120,
        expected_revision=1,
    )
    restarted = module.WorkOriginAuthority(WorkRepository(repository.path))
    assert (
        restarted.resolve(
            subject,
            origin_kind="child",
            job_id=job["id"],
            grant_id=subject_grant.id,
            grant_revision=subject_grant.revision,
            action="delegate",
        ).ref
        == permitted.ref
    )
    delegated = authority.delegate(
        subject,
        parent_grant_id=subject_grant.id,
        expected_parent_revision=subject_grant.revision,
        child_principal=auth._verified_principal(
            "https://issuer.test", "grandchild-live", [auth.SCOPE_WRITE], "jwt"
        ),
        providers={"mock_cli"},
        permissions=Permissions(tools={"read"}),
        expires_at=time.time() + 60,
    )
    assert delegated.parent_grant_id == subject_grant.id

    monkeypatch.setattr(
        importlib.import_module("cli_agent_orchestrator.services.work_authority").time,
        "time",
        lambda: permitted.expires_at + 1,
    )
    with pytest.raises(PermissionError):
        authority.delegate(
            subject,
            parent_grant_id=subject_grant.id,
            expected_parent_revision=subject_grant.revision,
            child_principal=auth._verified_principal(
                "https://issuer.test", "grandchild-expired", [auth.SCOPE_WRITE], "jwt"
            ),
            providers={"mock_cli"},
            permissions=Permissions(tools={"read"}),
            expires_at=permitted.expires_at,
        )
    monkeypatch.undo()
    service.revoke(
        owner=owner,
        subject=subject,
        origin_kind="child",
        expected_revision=permitted.ref.revision,
    )
    with pytest.raises(PermissionError):
        authority.delegate(
            subject,
            parent_grant_id=subject_grant.id,
            expected_parent_revision=subject_grant.revision,
            child_principal=auth._verified_principal(
                "https://issuer.test", "grandchild-revoked", [auth.SCOPE_WRITE], "jwt"
            ),
            providers={"mock_cli"},
            permissions=Permissions(tools={"read"}),
            expires_at=time.time() + 60,
        )


def test_origin_authority_rejects_spoofing_mixed_grants_and_stale_history_without_writes(tmp_path):
    """Identity, issuer, job and CAS errors leave no contradictory durable rows."""
    module, repository, owner, subject, authority, _, root_grant_ref, subject_grant = setup(
        tmp_path
    )
    service = module.WorkOriginAuthority(repository)
    with pytest.raises(module.OriginDenied):
        service.register_subject(
            owner,
            verified_subject={"id": subject.id, "scopes": [auth.SCOPE_WRITE]},
            kind="child",
            issuer_id=owner.id,
            expected_revision=0,
        )
    with repository.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM work_origin_subjects").fetchone()[0] == 0

    service.register_subject(
        owner,
        verified_subject=subject,
        kind="child",
        issuer_id=owner.id,
        expected_revision=0,
    )
    other_owner = auth._verified_principal(
        "https://issuer.test", "other-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    other_subject = auth._verified_principal(
        "https://issuer.test", "other-child", [auth.SCOPE_WRITE], "jwt"
    )
    _, _, _, other_grant = root_grant(
        repository, tmp_path, other_owner, other_subject, name="other"
    )
    arguments = dict(
        subject=subject,
        origin_kind="child",
        grant_id=subject_grant.id,
        grant_revision=subject_grant.revision,
        actions={"admit_child"},
        expires_at=time.time() + 120,
        expected_revision=0,
    )
    for actor, changed in (
        (other_owner, {}),
        (owner, {"grant_id": other_grant.id, "grant_revision": other_grant.revision}),
        (owner, {"expires_at": time.time() - 1}),
    ):
        with pytest.raises(module.OriginDenied):
            service.authorize(actor, **dict(arguments, **changed))
        with repository.read_snapshot() as connection:
            assert (
                connection.execute("SELECT count(*) FROM work_origin_authorizations").fetchone()[0]
                == 0
            )

    issued = service.authorize(owner, **arguments)
    with pytest.raises(module.OriginConflict):
        service.authorize(owner, **dict(arguments, expected_revision=0))
    with pytest.raises(module.OriginDenied):
        service.revoke(owner=other_owner, subject=subject, origin_kind="child", expected_revision=1)
    with repository.read_snapshot() as connection:
        assert (
            connection.execute("SELECT count(*) FROM work_origin_authorizations").fetchone()[0] == 1
        )
    authority.revoke(
        owner,
        grant_id=root_grant_ref.id,
        expected_grant_revision=root_grant_ref.revision,
        reason="ancestor revoked for origin authority regression",
    )
    with pytest.raises(module.OriginDenied):
        service.resolve(
            subject,
            origin_kind="child",
            job_id=issued.job_id,
            grant_id=issued.grant_id,
            grant_revision=issued.grant_revision,
            action="admit_child",
        )
    service.revoke(
        owner=owner,
        subject=subject,
        origin_kind="child",
        expected_revision=issued.ref.revision,
    )
    with pytest.raises(module.OriginDenied):
        service.resolve(
            subject,
            origin_kind="child",
            job_id=issued.job_id,
            grant_id=issued.grant_id,
            grant_revision=issued.grant_revision,
            action="admit_child",
        )


def test_origin_models_reject_bool_versions_unknown_actions_and_extra_fields(tmp_path):
    """Strict v1 DTO validation must reject coercion before durable use."""
    module, *_ = setup(tmp_path)
    models = origin_models()
    with pytest.raises(ValidationError):
        models.OriginSubjectRef.model_validate(
            {
                "schema_version": True,
                "subject_id": "subject",
                "kind": "child",
                "revision": 1,
            }
        )
    with pytest.raises(ValidationError):
        models.OriginAuthorizationRef.model_validate(
            {
                "schema_version": 1,
                "subject_id": "subject",
                "origin_kind": "child",
                "revision": 1,
                "unexpected": "field",
            }
        )
    with pytest.raises(module.OriginDenied):
        module.WorkOriginAuthority(WorkRepository(tmp_path / "empty.sqlite3"))._actions({"unknown"})
