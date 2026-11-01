"""Coordinator locks use canonical identities, not configuration spellings."""

from dataclasses import replace
from pathlib import Path

import pytest

from cli_agent_orchestrator.clients.admission_repository import ResourceAdmissionRepository
from cli_agent_orchestrator.clients.postgres import DatabaseTarget

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize("alias", ["store", "operation"])
def test_lock_identity_normalizes_uuid_spellings_without_opening_credentials(alias):
    identity = "abcdefab-abcd-4abc-8abc-abcdefabcdef"
    target = DatabaseTarget(
        "localhost",
        5432,
        "not_opened",
        "runtime",
        "resource_identity",
        identity,
        Path("/not-read"),
        sslmode="disable",
    )

    # These methods must remain pure. The supplied binding is never requested.
    def unavailable():
        raise AssertionError("lock identity must not resolve a process or credential")

    first = ResourceAdmissionRepository(target, binding_supplier=unavailable)
    second = ResourceAdmissionRepository(
        replace(target, store_id=identity.upper()) if alias == "store" else target,
        binding_supplier=unavailable,
    )
    assert first._lock_key(identity) == second._lock_key(
        identity.upper() if alias == "operation" else identity
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("capacity", True),
        ("capacity", -1),
        ("capacity", 1.5),
        ("capacity", 2**63),
        ("unit", "tokens_per_minute"),
        ("kind", "quota"),
    ],
)
def test_domain_values_reject_unsupported_or_unbounded_units(field, value):
    from cli_agent_orchestrator.models.admission_domains import DomainSpec

    values = dict(
        domain_id="abcdefab-abcd-4abc-8abc-abcdefabcdef",
        incarnation="abcdefab-abcd-4abc-8abc-abcdefabcdef",
        kind="storage",
        unit="postgres_session",
        capacity=1,
        physical_binding_sha256="a" * 64,
    )
    values[field] = value
    with pytest.raises(ValueError):
        DomainSpec(**values)
