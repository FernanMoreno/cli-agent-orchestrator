"""Operator reconciliation keeps legacy terminal outcomes and releases fenced work."""

from click.testing import CliRunner

from cli_agent_orchestrator.cli.commands import peer


def test_reconcile_reviews_terminal_receipt_without_stop_evidence(monkeypatch):
    task = {
        "task_id": "task",
        "project_id": "project",
        "state": "cancelled",
        "terminal_id": "deadbeef",
        "result": {"state": "cancelled"},
    }
    monkeypatch.setattr(peer.local_peer_service, "task_from_peer", lambda _: task)
    reviewed = []
    monkeypatch.setattr(
        peer.local_peer_service,
        "reconcile_local_task_after_operator_review",
        lambda task_id: reviewed.append(task_id) or task,
    )
    result = CliRunner().invoke(peer.peer, ["reconcile", "task"], input="y\n")
    assert result.exit_code == 0, result.output
    assert reviewed == ["task"]
    assert "cancelled" in result.output
