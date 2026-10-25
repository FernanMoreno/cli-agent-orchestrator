"""Independent PostgreSQL reconstruction retains each adapter's receipt fence."""

import threading
from concurrent.futures import ThreadPoolExecutor

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.providers import manager as module
from cli_agent_orchestrator.providers.codex import CodexProvider


def test_postgres_restore_progress_is_independent_and_receipts_stay_private(
    isolated_memory_db,
    monkeypatch,
):
    assert db.engine.dialect.name == "postgresql"
    expected = {}
    for terminal_id, digit in (("restore-a", "a"), ("restore-b", "b")):
        db.create_terminal(terminal_id, "restore-session", "worker", "codex", "reviewer")
        generation, digest = digit * 32, digit * 64
        assert db.begin_terminal_turn_receipt(terminal_id, "codex", generation, digest)
        assert db.mark_terminal_turn_receipt_sent(terminal_id, generation, digest)
        expected[terminal_id] = dict(generation=generation, receipt_sha256=digest)

    manager = module.ProviderManager()
    entered = threading.Event()
    release = threading.Event()
    b_done = threading.Event()
    original = CodexProvider.restore_turn_receipt_state

    def held_restore(provider, state):
        if provider.terminal_id == "restore-a":
            entered.set()
            assert release.wait(timeout=10), "receipt barrier was not released"
        original(provider, state)

    def restore_b():
        result = manager.get_provider("restore-b")
        b_done.set()
        return result

    monkeypatch.setattr(CodexProvider, "restore_turn_receipt_state", held_restore)
    with ThreadPoolExecutor(max_workers=2) as workers:
        a_future = workers.submit(manager.get_provider, "restore-a")
        try:
            assert entered.wait(timeout=5)
            assert "restore-a" not in manager._providers
            b_future = workers.submit(restore_b)
            assert b_done.wait(timeout=5), "B waited behind A's private receipt restoration"
            b = b_future.result(timeout=5)
            assert isinstance(b, CodexProvider)
            assert b.pending_turn_receipt_state() == expected["restore-b"]
            assert b.blocks_new_task_input_for_reconciliation
            assert "restore-a" not in manager._providers
        finally:
            release.set()
        a = a_future.result(timeout=5)

    assert a is not b
    assert a.pending_turn_receipt_state() == expected["restore-a"]
    assert a.blocks_new_task_input_for_reconciliation
    assert manager.get_provider("restore-a") is a
    assert manager.get_provider("restore-b") is b
    assert manager._restore_locks == {}
    for terminal_id in expected:
        row = db.get_terminal_turn_receipt(terminal_id)
        assert row["phase"] == "sent"
        assert {key: row[key] for key in expected[terminal_id]} == expected[terminal_id]
