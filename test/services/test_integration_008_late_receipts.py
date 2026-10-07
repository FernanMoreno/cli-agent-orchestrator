"""Late evidence is observed once per durable slot without repeating tasks."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database as db

GEN = "a" * 32
HASH = "b" * 64


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'late.sqlite'}", connect_args={"check_same_thread": False}
    )
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine))
    db.create_terminal("supervisor", "cao-session", "0", "codex", "developer")
    db.begin_terminal_turn_receipt("supervisor", "codex", GEN, HASH)
    db.mark_terminal_turn_receipt_sent("supervisor", GEN, HASH)
    for _ in range(3):
        db.record_terminal_turn_verification_failure("supervisor", GEN, HASH, "missing_receipt")
    yield engine
    engine.dispose()


def test_concurrent_observers_share_durable_budget_and_cooldown(store):
    gate = Barrier(4)

    def claim():
        gate.wait()
        return db.claim_late_turn_observation("supervisor", GEN, now=100)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: claim(), range(4)))
    assert results.count(True) == 1
    assert not db.claim_late_turn_observation("supervisor", GEN, now=159)
    # A new reader/process sees the same slots; ordinary three-attempt budget is unchanged.
    for slot in range(1, 6):
        assert db.claim_late_turn_observation("supervisor", GEN, now=100 + 60 * slot)
    assert not db.claim_late_turn_observation("supervisor", GEN, now=1000)
    assert db.get_terminal_turn_recovery("supervisor")["attempts"] == 3


def test_cancelled_or_replaced_generation_never_gets_late_slot(store):
    assert db.begin_terminal_turn_cancellation("supervisor", GEN)
    assert not db.claim_late_turn_observation("supervisor", GEN, now=100)
    assert db.finish_terminal_turn_cancellation("supervisor", GEN)
    db.begin_terminal_turn_receipt("supervisor", "codex", "d" * 32, "e" * 64)
    assert not db.claim_late_turn_observation("supervisor", GEN, now=100)


def test_late_authentic_receipt_settles_without_input_or_new_initial_budget(store):
    from cli_agent_orchestrator.services import late_turn_observer as observer

    def evidence(terminal_id, mode, *, expected_generation, ordinary_observation):
        assert (
            terminal_id == "supervisor"
            and expected_generation == GEN
            and ordinary_observation is True
        )
        assert db.settle_terminal_turn_receipt_result(
            terminal_id, GEN, HASH, "c" * 64, result_text="late authentic answer"
        )
        return "late authentic answer"

    with (
        patch.object(observer, "ensure_terminal_is_not_work_owned"),
        patch.object(observer.terminal_service, "get_output", side_effect=evidence) as read,
        patch.object(observer.terminal_service, "send_input") as send,
    ):
        assert observer.observe_once(now=100) == 1
        assert observer.observe_once(now=160) == 0
    read.assert_called_once()
    send.assert_not_called()
    assert db.get_terminal_turn_recovery("supervisor")["state"] == "verified"
    assert db.get_terminal_turn_recovery("supervisor")["result_text"] == "late authentic answer"


def test_work_guard_and_reader_failure_do_not_report_success(store):
    from cli_agent_orchestrator.services import late_turn_observer as observer

    with (
        patch.object(
            observer,
            "ensure_terminal_is_not_work_owned",
            side_effect=RuntimeError("Work owns terminal"),
        ),
        patch.object(observer.terminal_service, "get_output") as read,
    ):
        assert observer.observe_once(now=100) == 0
    read.assert_not_called()
    # The Work refusal did not consume an ordinary observer slot.
    with (
        patch.object(observer, "ensure_terminal_is_not_work_owned"),
        patch.object(
            observer.terminal_service, "get_output", side_effect=RuntimeError("capture failure")
        ),
    ):
        assert observer.observe_once(now=100) == 0
    assert db.get_terminal_turn_recovery("supervisor")["state"] == "reconcile"
    assert db.get_terminal_turn_recovery("supervisor")["attempts"] == 3
