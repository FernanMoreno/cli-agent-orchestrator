"""Durable proxy owner-recovery scenarios from the T097 host suite."""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _provision_work_schema(postgres_targets):
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    bootstrap_scope(postgres_targets.main)


def test_proxy_effect_recovery_waits_for_proven_dead_owner(tmp_path, monkeypatch):
    from test.services.test_work_bubblewrap_cleanup_recovery import _bubblewrap_identity
    from test.services.test_work_mcp_proxy import _bound_worker

    from cli_agent_orchestrator.services import work_service
    from cli_agent_orchestrator.services.work_mcp_proxy import WorkMcpProxy
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, binding, _, _ = _bound_worker(tmp_path)
    repository.persist_bubblewrap_process_identity(binding.attempt_id, 1, _bubblewrap_identity())
    effect_id = "e2000000000000000000000000000000"
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO work_mcp_proxy_issues "
            "(attempt_id,generation,attempt_revision,contract_hash,expires_at,issued_at) "
            "VALUES (%s,1,1,%s,%s,%s)",
            (binding.attempt_id, binding.contract_hash, time.time() + 30, time.time()),
        )
        connection.execute(
            "INSERT INTO work_mcp_proxy_effects "
            "(effect_id,attempt_id,generation,request_sha256,created_at) "
            "VALUES (%s,%s,1,%s,%s)",
            (effect_id, binding.attempt_id, "f" * 64, time.time()),
        )
        connection.execute(
            "INSERT INTO work_mcp_proxy_effect_events "
            "(effect_id,sequence,state,occurred_at) VALUES (%s,1,'intent',%s)",
            (effect_id, time.time()),
        )

    owner_gone = [False]
    monkeypatch.setattr(
        work_service, "_original_bubblewrap_pair_gone", lambda _identity: owner_gone[0]
    )
    service = WorkService(repository)

    assert service.recover_incomplete_proxy_effects() == 0
    with repository.read_snapshot() as connection:
        states = [
            row[0]
            for row in connection.execute(
                "SELECT state FROM work_mcp_proxy_effect_events "
                "WHERE effect_id=%s ORDER BY sequence",
                (effect_id,),
            )
        ]
    assert states == ["intent"]

    owner_gone[0] = True
    assert service.recover_incomplete_proxy_effects() == 1
    assert service.recover_incomplete_proxy_effects() == 0
    unresolved = WorkMcpProxy(repository).unresolved_effects()
    assert len(unresolved) == 1
    assert unresolved[0]["state"] == "uncertain"
    with repository.read_snapshot() as connection:
        states = [
            row[0]
            for row in connection.execute(
                "SELECT state FROM work_mcp_proxy_effect_events "
                "WHERE effect_id=%s ORDER BY sequence",
                (effect_id,),
            )
        ]
    assert states == ["intent", "uncertain"]


def test_issued_proxy_recovery_waits_for_proven_dead_owner(tmp_path, monkeypatch):
    from test.services.test_work_bubblewrap_cleanup_recovery import _bubblewrap_identity
    from test.services.test_work_mcp_proxy import _bound_worker

    from cli_agent_orchestrator.services import work_service
    from cli_agent_orchestrator.services.work_mcp_proxy import (
        WorkMcpProxy,
        WorkMcpProxyRejected,
    )
    from cli_agent_orchestrator.services.work_service import WorkService

    repository, binding, _, _ = _bound_worker(tmp_path)
    repository.persist_bubblewrap_process_identity(binding.attempt_id, 1, _bubblewrap_identity())
    now = time.time()
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO work_mcp_proxy_issues "
            "(attempt_id,generation,attempt_revision,contract_hash,expires_at,issued_at) "
            "VALUES (%s,1,1,%s,%s,%s)",
            (binding.attempt_id, binding.contract_hash, now + 30, now),
        )
        connection.execute(
            "INSERT INTO work_mcp_proxy_issue_events "
            "(attempt_id,generation,sequence,state,occurred_at) "
            "VALUES (%s,1,1,'issued',%s)",
            (binding.attempt_id, now),
        )

    owner_gone = [False]
    monkeypatch.setattr(
        work_service, "_original_bubblewrap_pair_gone", lambda _identity: owner_gone[0]
    )
    service = WorkService(repository)
    proxy = WorkMcpProxy(repository)

    assert service.recover_incomplete_proxy_effects() == 0
    assert proxy.unresolved_issues()[0]["state"] == "issued"
    with pytest.raises(WorkMcpProxyRejected, match="not abandoned"):
        proxy.reconcile_issue(binding.attempt_id, 1, "checked provider; safe")

    owner_gone[0] = True
    assert service.recover_incomplete_proxy_effects() == 1
    assert service.recover_incomplete_proxy_effects() == 0
    assert proxy.unresolved_issues()[0]["state"] == "abandoned"
    proxy.reconcile_issue(binding.attempt_id, 1, "confirmed no request was sent")
    assert proxy.unresolved_issues() == ()
    with repository.read_snapshot() as connection:
        states = [
            row[0]
            for row in connection.execute(
                "SELECT state FROM work_mcp_proxy_issue_events "
                "WHERE attempt_id=%s AND generation=1 ORDER BY sequence",
                (binding.attempt_id,),
            )
        ]
        resolution_digest = connection.execute(
            "SELECT resolution_sha256 FROM work_mcp_proxy_issue_events "
            "WHERE attempt_id=%s AND generation=1 ORDER BY sequence DESC LIMIT 1",
            (binding.attempt_id,),
        ).fetchone()[0]
    assert states == ["issued", "abandoned", "reconciled"]
    assert len(resolution_digest) == 64
