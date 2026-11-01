"""Deployment enrollment authority precedes repository or process identity access."""

from test.services.test_admission_bootstrap import principal
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from cli_agent_orchestrator.models.admission_domains import DomainSpec
from cli_agent_orchestrator.services.admission_domains import ResourceDomainAuthority

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize(
    "actor", [None, SimpleNamespace(kind="jwt", scopes={"cao:admin"}), principal(admin=False)]
)
@pytest.mark.parametrize("action", ["domain", "member", "audit"])
def test_enrollment_rejects_unverified_or_nonadmin_before_repository(actor, action):
    repository = Mock()
    authority = ResourceDomainAuthority(repository)
    with pytest.raises(PermissionError):
        if action == "domain":
            authority.provision_domain(
                principal=actor,
                spec=DomainSpec(
                    str(uuid4()), str(uuid4()), "storage", "postgres_session", 1, "a" * 64
                ),
                expected_revision=0,
            )
        elif action == "member":
            authority.grant_current_member(
                principal=actor, domain_id=str(uuid4()), expected_revision=0
            )
        else:
            authority.audit_usage(principal=actor)
    assert repository.mock_calls == []


def test_enrollment_derives_the_member_from_private_server_binding():
    repository = Mock()
    authority = ResourceDomainAuthority(repository)
    actor = principal()
    domain_id = str(uuid4())
    authority.grant_current_member(principal=actor, domain_id=domain_id, expected_revision=2)
    repository.current_binding.assert_called_once_with()
    repository.provision_member.assert_called_once_with(
        domain_id,
        repository.current_binding.return_value,
        expected_revision=2,
        principal_id=actor.id,
        active=True,
    )
