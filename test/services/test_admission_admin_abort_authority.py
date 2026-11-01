"""Administrative abort is a separate verified operation, never a profile permission."""

from test.services.test_shared_admission_runtime import principal, reader
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_domains import ResourceDomainAuthority

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize("actor", [None, "admin", reader()])
def test_unverified_or_nonadmin_cannot_touch_abort_repository(actor):
    class Forbidden:
        def __getattr__(self, name):
            pytest.fail("unauthorized abort touched repository")

    with pytest.raises(PermissionError):
        ResourceDomainAuthority(Forbidden()).abort_preparing(
            principal=actor, action_id=str(uuid4()), operation_id=str(uuid4()), expected_revision=1
        )


def test_verified_actor_is_derived_by_server_before_delegation():
    calls = []
    receipt = object()
    repository = SimpleNamespace(
        admin_abort_preparing=lambda *args, **kwargs: calls.append((args, kwargs)) or receipt
    )
    action_id, operation_id = str(uuid4()), str(uuid4())
    result = ResourceDomainAuthority(repository).abort_preparing(
        principal=principal(), action_id=action_id, operation_id=operation_id, expected_revision=1
    )
    assert result is receipt
    assert calls == [
        (
            (operation_id,),
            {
                "action_id": action_id,
                "expected_revision": 1,
                "principal_id": ResourceBootstrap._administrator(principal()),
            },
        )
    ]
