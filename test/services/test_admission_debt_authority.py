"""Inventory is admin-only and descriptive; a cursor never grants authority."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from cli_agent_orchestrator.security.auth import _verified_principal
from cli_agent_orchestrator.services.admission_domains import ResourceDomainAuthority

pytestmark = pytest.mark.offline_database


def principal(scopes):
    return _verified_principal("urn:caos:test", "operator", scopes, "jwt")


@pytest.mark.parametrize("actor", [None, "admin", principal(["cao:read"])])
def test_inventory_rejects_unverified_or_nonadmin_before_repository(actor):
    class Forbidden:
        def __getattr__(self, name):
            pytest.fail("unauthorized inventory touched repository")

    with pytest.raises(PermissionError):
        ResourceDomainAuthority(Forbidden()).outstanding_resources(principal=actor)


def test_admin_uses_exact_typed_cursor_without_changing_result():
    from cli_agent_orchestrator.models.admission_domains import SharedDebtCursor

    cursor = SharedDebtCursor("operation", str(uuid4()))
    page = object()
    calls = []
    repository = SimpleNamespace(
        outstanding_resources=lambda **kwargs: calls.append(kwargs) or page
    )
    result = ResourceDomainAuthority(repository).outstanding_resources(
        principal=principal(["cao:admin"]), limit=2, after=cursor
    )
    assert result is page
    assert calls == [{"limit": 2, "after": cursor}]


@pytest.mark.parametrize(
    "kind,entry_id", [("unknown", str(uuid4())), ("operation", "bad-id"), (True, str(uuid4()))]
)
def test_cursor_fields_are_strict_and_canonical(kind, entry_id):
    from cli_agent_orchestrator.models.admission_domains import SharedDebtCursor

    with pytest.raises(ValueError):
        SharedDebtCursor(kind, entry_id)
