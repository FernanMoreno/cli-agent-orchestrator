"""Memory repair verifies PostgreSQL invariants and never runs runtime DDL."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database


def test_repair_index_check_uses_verified_native_target(monkeypatch, tmp_path):
    from cli_agent_orchestrator.clients import database, postgres
    from cli_agent_orchestrator.clients.postgres import migrations
    from cli_agent_orchestrator.services.memory_reconciliation import MemoryReconciliationService

    service = MemoryReconciliationService(base_dir=tmp_path)
    target = object()
    connection = object()
    observed = []
    service._memory._legacy_repository = lambda: SimpleNamespace(target=target)

    @contextmanager
    def read_only(selected, *, readonly=False):
        assert selected is target and readonly
        yield connection

    monkeypatch.setattr(postgres, "connect", read_only)
    monkeypatch.setattr(
        migrations, "verify_runtime_role", lambda selected: observed.append(("role", selected))
    )
    monkeypatch.setattr(
        migrations,
        "verify_component",
        lambda selected, component: observed.append((component, selected)),
    )
    service._verify_metadata_schema()
    assert observed == [("role", connection), (database.CORE_MIGRATIONS, connection)]
