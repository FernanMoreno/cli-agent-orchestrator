"""A metadata grant is a resource admission, including its knowledge fences."""

from pathlib import Path
from test.postgres.test_admission_reservations import binding
from uuid import uuid4

import pytest

import cli_agent_orchestrator.clients.admission_repository as module
from cli_agent_orchestrator.clients.postgres import DatabaseTarget

pytestmark = pytest.mark.offline_database


def test_known_uncertainty_blocks_new_credit_before_any_sql(monkeypatch):
    target = DatabaseTarget(
        "localhost",
        5432,
        "not_opened",
        "runtime",
        "resource_identity",
        str(uuid4()),
        Path("/not-read"),
        sslmode="disable",
    )
    current = binding(target)
    repository = module.ResourceAdmissionRepository(target, binding_supplier=lambda: current)
    with repository._usage_guard:
        repository._usage_known = True
        repository._usage_uncertain = True
    monkeypatch.setattr(
        module, "connect", lambda *a, **kw: pytest.fail("known uncertainty reached SQL")
    )
    with pytest.raises(module.SharedAdmissionDenied):
        repository.grant_metadata_credit(
            str(uuid4()),
            str(uuid4()),
            current,
            principal_id="principal-" + "b" * 64,
        )
