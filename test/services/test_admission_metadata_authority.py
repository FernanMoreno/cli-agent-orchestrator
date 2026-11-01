"""Metadata credit grants require a sealed administrator before any binding or SQL."""

from test.services.test_admission_bootstrap import principal
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from cli_agent_orchestrator.models.admission_domains import MetadataCredit
from cli_agent_orchestrator.services.admission_domains import ResourceDomainAuthority

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize(
    "actor",
    [
        None,
        SimpleNamespace(kind="jwt", scopes={"cao:admin"}, id="principal-" + "a" * 64),
        principal(admin=False),
    ],
)
def test_metadata_credit_rejects_unverified_or_nonadmin_before_binding(actor):
    repository = Mock()
    with pytest.raises(PermissionError):
        ResourceDomainAuthority(repository).grant_current_metadata_credit(
            principal=actor, credit_id=str(uuid4()), domain_id=str(uuid4())
        )
    assert repository.mock_calls == []


def test_metadata_credit_uses_private_binding_and_returns_typed_credit():
    repository = Mock()
    credit = MetadataCredit(
        str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4()), 7, 1.5, "a" * 64, 1, 1
    )
    repository.grant_metadata_credit.return_value = credit
    actor = principal()
    result = ResourceDomainAuthority(repository).grant_current_metadata_credit(
        principal=actor, credit_id=credit.credit_id, domain_id=credit.domain_id
    )
    assert result is credit
    assert [call[0] for call in repository.mock_calls] == [
        "current_binding",
        "grant_metadata_credit",
        "current_binding",
    ]
    repository.grant_metadata_credit.assert_called_once_with(
        credit.credit_id,
        credit.domain_id,
        repository.current_binding.return_value,
        principal_id=actor.id,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"amount": 2},
        {"amount": True},
        {"process_started_at": 1},
        {"process_started_at": float("inf")},
        {"binding_sha256": "A" * 64},
        {"revision": 0},
        {"credit_id": "not-a-uuid"},
    ],
)
def test_metadata_credit_value_rejects_invented_or_malformed_capacity(change):
    fields = dict(
        credit_id=str(uuid4()),
        domain_id=str(uuid4()),
        instance_id=str(uuid4()),
        process_generation=str(uuid4()),
        process_pid=7,
        process_started_at=1.5,
        binding_sha256="a" * 64,
        amount=1,
        revision=1,
    )
    fields.update(change)
    with pytest.raises(ValueError):
        MetadataCredit(**fields)


def test_private_view_change_after_grant_is_not_a_current_allowance():
    from cli_agent_orchestrator.clients.admission_repository import SharedCommitUncertain

    repository = Mock()
    repository.current_binding.side_effect = [object(), object()]
    repository.grant_metadata_credit.return_value = object()
    with pytest.raises(SharedCommitUncertain):
        ResourceDomainAuthority(repository).grant_current_metadata_credit(
            principal=principal(), credit_id=str(uuid4()), domain_id=str(uuid4())
        )
    assert repository.grant_metadata_credit.call_count == 1
