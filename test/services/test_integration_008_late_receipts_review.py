"""Late observer crosses real provider receipt extraction and SQLite settlement."""

import asyncio
import re
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.codex import CodexProvider
from cli_agent_orchestrator.services import late_turn_observer as observer


@pytest.mark.asyncio
async def test_candidate_store_outage_does_not_permanently_stop_observer(monkeypatch):
    retried = asyncio.Event()
    calls = 0
    real_sleep = asyncio.sleep

    def candidates(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("isolated transient candidate store outage")
        return []

    async def fast_sleep(seconds):
        if calls >= 2:
            retried.set()
            await asyncio.Future()
        await real_sleep(0)

    monkeypatch.setattr(db, "list_late_turn_candidates", candidates)
    monkeypatch.setattr(observer.asyncio, "sleep", fast_sleep)
    task = asyncio.create_task(observer.run())
    try:
        await asyncio.wait_for(retried.wait(), timeout=1)
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("authentic", [True, False])
def test_restored_codex_late_evidence_requires_exact_receipt(tmp_path, monkeypatch, authentic):
    path = tmp_path / "late-review.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(constants, "DATABASE_FILE", path)
    db.create_terminal("review", "cao-review", "0", "codex", "developer")
    original = CodexProvider("review", "cao-review", "0")
    prepared = original.prepare_input("Return the late answer.")
    nonce = re.search(r"CAO-TURN-RECEIPT-[0-9a-f]{32}", prepared).group(0)
    state = original.pending_turn_receipt_state()
    generation, digest = state["generation"], state["receipt_sha256"]
    db.begin_terminal_turn_receipt("review", "codex", generation, digest)
    db.mark_terminal_turn_receipt_sent("review", generation, digest)
    for _ in range(3):
        db.record_terminal_turn_verification_failure(
            "review", generation, digest, "missing_receipt"
        )
    restored = CodexProvider("review", "cao-review", "0")
    restored.restore_turn_receipt_state(db.get_terminal_turn_receipt("review"))
    visible_nonce = nonce if authentic else "CAO-TURN-RECEIPT-" + "0" * 32
    transcript = (
        "› Return the late answer.\n• completed the requested task\n"
        f"{visible_nonce}\n\n  done 1:43 PM\n\n"
        "  Approaching rate limits\n"
        "  Switch to gpt-5.6-luna for lower credit usage?\n\n"
        "› 1. Switch to gpt-5.6-luna                 Fast and affordable model.\n"
        "  2. Keep current model\n  3. Keep current model (never show again)\n\n"
        "  Press enter to confirm or esc to go back\n"
    )
    backend = MagicMock()
    backend.get_history.return_value = transcript
    terminal = observer.terminal_service
    try:
        with (
            patch.object(terminal, "get_backend", return_value=backend),
            patch.object(terminal.provider_manager, "get_provider", return_value=restored),
            patch.object(terminal.status_monitor, "get_buffer", return_value=transcript),
            patch.object(terminal.status_monitor, "publish_observed_status") as publish,
            patch.object(terminal, "send_input") as send,
        ):
            assert observer.observe_once(now=100) == int(authentic)
            assert observer.observe_once(now=101) == 0
            send.assert_not_called()
            backend.send_keys.assert_not_called()
            backend.send_special_key.assert_not_called()
            if authentic:
                publish.assert_called_once_with("review", TerminalStatus.WAITING_USER_ANSWER)
            else:
                publish.assert_not_called()
        recovery = db.get_terminal_turn_recovery("review")
        assert recovery["state"] == ("verified" if authentic else "reconcile")
        assert recovery["attempts"] == 3
        if authentic:
            assert recovery["result_text"] == "completed the requested task"
            assert restored.pending_turn_receipt_state() is None
        else:
            assert recovery["result_text"] is None
            assert restored.pending_turn_receipt_state()["generation"] == generation
    finally:
        engine.dispose()
