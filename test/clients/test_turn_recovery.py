"""Durable recovery contract against real SQLite transactions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database as db

GEN = "a" * 32
RECEIPT = "b" * 64
RESULT = "c" * 64


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "turns.sqlite"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine))
    db.create_terminal("supervisor", "session", "window", "codex", "developer")
    assert db.begin_terminal_turn_receipt("supervisor", "codex", GEN, RECEIPT)
    assert db.mark_terminal_turn_receipt_sent("supervisor", GEN, RECEIPT)
    yield path
    engine.dispose()


def test_supervisor_failure_budget_survives_restart(store, monkeypatch):
    for attempt in range(1, 4):
        row = db.record_terminal_turn_verification_failure(
            "supervisor", GEN, RECEIPT, "missing_receipt"
        )
        assert row["attempts"] == attempt
        assert row["state"] == ("reconcile" if attempt == 3 else "verifying")
        assert row["completion_detected_at"] is not None
        assert "receipt_sha256" not in row
    engine = create_engine(f"sqlite:///{store}")
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine))
    assert db.get_terminal_turn_recovery("supervisor")["attempts"] == 3
    assert (
        db.record_terminal_turn_verification_failure("supervisor", GEN, RECEIPT, "missing_receipt")[
            "attempts"
        ]
        == 3
    )
    engine.dispose()


def test_cancel_blocks_late_verification_and_only_confirmed_stop_releases_turn(store):
    assert db.begin_terminal_turn_cancellation("supervisor", GEN)
    assert not db.settle_terminal_turn_receipt_result("supervisor", GEN, RECEIPT, RESULT)
    assert not db.verify_terminal_turn_receipt_result("supervisor", GEN, RECEIPT, RESULT)
    assert db.begin_terminal_turn_receipt("supervisor", "codex", "d" * 32, "e" * 64) is None
    assert db.finish_terminal_turn_cancellation("supervisor", GEN)
    assert not db.settle_terminal_turn_receipt_result("supervisor", GEN, RECEIPT, RESULT)
    assert db.begin_terminal_turn_receipt("supervisor", "codex", "d" * 32, "e" * 64)
    assert db.get_terminal_turn_recovery("supervisor", GEN)["state"] == "cancelled"
    assert db.get_terminal_turn_recovery("supervisor")["state"] == "pending"
    assert not db.begin_terminal_turn_cancellation("supervisor", GEN)


def test_verified_result_is_stable_and_archived(store):
    assert db.settle_terminal_turn_receipt_result(
        "supervisor", GEN, RECEIPT, RESULT, result_text="original"
    )
    assert db.settle_terminal_turn_receipt_result(
        "supervisor", GEN, RECEIPT, RESULT, result_text="different"
    )
    assert db.get_terminal_turn_recovery("supervisor")["result_text"] == "original"
    assert not db.begin_terminal_turn_cancellation("supervisor", GEN)
    assert not db.settle_terminal_turn_receipt_result("supervisor", GEN, RECEIPT, "f" * 64)
    assert db.begin_terminal_turn_receipt("supervisor", "codex", "d" * 32, "e" * 64)
    assert db.get_terminal_turn_recovery("supervisor", GEN)["result_text"] == "original"
    assert db.delete_terminal("supervisor")
    assert db.get_terminal_turn_recovery("supervisor", GEN) is None


def test_verify_cancel_race_has_exactly_one_winner(store):
    gate = Barrier(2)

    def verify():
        gate.wait()
        return db.settle_terminal_turn_receipt_result(
            "supervisor", GEN, RECEIPT, RESULT, result_text="answer"
        )

    def cancel():
        gate.wait()
        return db.begin_terminal_turn_cancellation("supervisor", GEN)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(verify), pool.submit(cancel)
        verified, cancelled = first.result(), second.result()
    assert verified != cancelled
    row = db.get_terminal_turn_recovery("supervisor")
    assert row["state"] == ("verified" if verified else "cancelling")
    assert row["result_text"] == ("answer" if verified else None)


def test_wrong_identity_and_unconfirmed_cancel_preserve_state(store):
    assert db.record_terminal_turn_verification_failure("supervisor", GEN, "f" * 64, "bad") is None
    assert not db.finish_terminal_turn_cancellation("supervisor", GEN)
    assert not db.begin_terminal_turn_cancellation("supervisor", "f" * 32)
    assert db.get_terminal_turn_recovery("supervisor")["state"] == "pending"


def test_read_failure_propagates(store, monkeypatch):
    def unavailable():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db, "SessionLocal", unavailable)
    with pytest.raises(RuntimeError, match="database unavailable"):
        db.get_terminal_turn_recovery("supervisor")


def test_concurrent_failures_share_one_bounded_budget(store):
    gate = Barrier(4)

    def fail():
        gate.wait()
        return db.record_terminal_turn_verification_failure(
            "supervisor", GEN, RECEIPT, "missing_receipt"
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(fail) for _ in range(4)]
        rows = [future.result() for future in futures]
    assert all(row["attempts"] <= 3 for row in rows)
    assert db.get_terminal_turn_recovery("supervisor")["attempts"] == 3
    assert db.get_terminal_turn_recovery("supervisor")["state"] == "reconcile"


def test_failure_cannot_undo_verified_result(store):
    assert db.verify_terminal_turn_receipt_result("supervisor", GEN, RECEIPT, RESULT)
    row = db.record_terminal_turn_verification_failure("supervisor", GEN, RECEIPT, "late_failure")
    assert row["state"] == "verified"
    assert row["attempts"] == 0
    assert row["reason"] is None


def test_actual_sqlite_read_failure_is_not_pending(store):
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    with db.SessionLocal() as session:
        session.execute(text("DROP TABLE terminal_turn_recovery"))
        session.commit()
    with pytest.raises(SQLAlchemyError):
        db.get_terminal_turn_recovery("supervisor")


@pytest.mark.parametrize("mode", ["session", "ids"])
def test_bulk_terminal_removal_erases_generation_history(store, mode):
    assert db.settle_terminal_turn_receipt_result(
        "supervisor", GEN, RECEIPT, RESULT, result_text="answer"
    )
    assert db.begin_terminal_turn_receipt("supervisor", "codex", "d" * 32, "e" * 64)
    if mode == "session":
        assert db.delete_terminals_by_session("session") == 1
    else:
        assert db.delete_terminals_by_ids(["supervisor"]) == 1
    assert db.get_terminal_turn_recovery("supervisor", GEN) is None
    assert db.get_terminal_turn_recovery("supervisor", "d" * 32) is None


def test_final_detection_timestamp_survives_repeated_calls_and_restart(store, monkeypatch):
    assert db.get_terminal_turn_recovery("supervisor")["completion_detected_at"] is None
    started = db.mark_terminal_turn_verification_started("supervisor", GEN, RECEIPT)
    assert started["state"] == "verifying"
    assert started["attempts"] == 0
    detected = started["completion_detected_at"]
    assert detected is not None
    repeated = db.mark_terminal_turn_verification_started("supervisor", GEN, RECEIPT)
    assert repeated["completion_detected_at"] == detected
    engine = create_engine(f"sqlite:///{store}")
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine))
    restored = db.mark_terminal_turn_verification_started("supervisor", GEN, RECEIPT)
    assert restored["completion_detected_at"] == detected
    assert restored["attempts"] == 0
    engine.dispose()


def test_final_detection_rejects_stale_evidence_and_preserves_final_state(store):
    assert db.mark_terminal_turn_verification_started("supervisor", GEN, "f" * 64) is None
    assert db.get_terminal_turn_recovery("supervisor")["completion_detected_at"] is None
    assert db.begin_terminal_turn_cancellation("supervisor", GEN)
    row = db.mark_terminal_turn_verification_started("supervisor", GEN, RECEIPT)
    assert row["state"] == "cancelling"
    assert row["completion_detected_at"] is None
    assert row["attempts"] == 0


@pytest.mark.parametrize("prefix", ["CAO-TURN-RECEIPT-", "CAO_TURN_RECEIPT_"])
def test_cached_result_redacts_plaintext_receipt_and_preserves_original_digest(store, prefix):
    import hashlib

    nonce = "123456789abcdef0123456789abcdef0"
    result = f"Completed the task.\n{prefix}{nonce}\n"
    digest = hashlib.sha256(result.encode()).hexdigest()
    assert db.settle_terminal_turn_receipt_result(
        "supervisor", GEN, RECEIPT, digest, result_text=result
    )
    cached = db.get_terminal_turn_recovery("supervisor")["result_text"]
    assert "Completed the task." in cached
    assert nonce not in cached
    assert prefix not in cached
    assert db.get_terminal_turn_receipt("supervisor")["result_sha256"] == digest
    assert db.settle_terminal_turn_receipt_result(
        "supervisor", GEN, RECEIPT, digest, result_text="later changed capture"
    )
    assert db.get_terminal_turn_recovery("supervisor")["result_text"] == cached


def test_new_turn_after_confirmed_cancel_does_not_revive_historic_child(store):
    child = db.plan_native_child(
        parent_terminal_id="parent",
        terminal_id="supervisor",
        provider="codex",
        agent_profile="developer",
        lease_seconds=30,
    )
    db.transition_native_child("supervisor", "sent")
    assert db.begin_terminal_turn_cancellation("supervisor", GEN)
    assert db.finish_terminal_turn_cancellation("supervisor", GEN)
    db.transition_native_child("supervisor", "cancelled")
    assert not db.settle_terminal_turn_receipt_result("supervisor", GEN, RECEIPT, RESULT)
    next_generation, next_receipt = "d" * 32, "e" * 64
    assert db.begin_terminal_turn_receipt("supervisor", "codex", next_generation, next_receipt)
    assert db.mark_terminal_turn_receipt_sent("supervisor", next_generation, next_receipt)
    assert db.settle_terminal_turn_receipt_result(
        "supervisor", next_generation, next_receipt, RESULT, result_text="independent task"
    )
    assert db.get_native_child(child["id"])["state"] == "cancelled"
    assert db.get_terminal_turn_recovery("supervisor", GEN)["state"] == "cancelled"
    assert db.get_terminal_turn_recovery("supervisor")["result_text"] == "independent task"
    assert not db.settle_terminal_turn_receipt_result("supervisor", GEN, RECEIPT, RESULT)


def test_historic_child_cancellation_requires_confirmed_turn_boundary(store):
    child = db.plan_native_child(
        parent_terminal_id="parent",
        terminal_id="supervisor",
        provider="codex",
        agent_profile="developer",
        lease_seconds=30,
    )
    db.transition_native_child("supervisor", "sent")
    db.transition_native_child("supervisor", "cancelled")
    assert not db.settle_terminal_turn_receipt_result("supervisor", GEN, RECEIPT, RESULT)
    assert db.get_native_child(child["id"])["state"] == "cancelled"
