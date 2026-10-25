"""A native-child plan and terminal registration must share one ID claim."""

import asyncio
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from test.services.test_terminal_service_inbox_registration import (
    SESSION_NAME,
    TERMINAL_ID,
    create_mocks,
)

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.services import terminal_service


def plan(terminal_id, **overrides):
    values = dict(
        parent_terminal_id="parent",
        terminal_id=terminal_id,
        provider="claude_code",
        agent_profile="developer",
        lease_seconds=60,
    )
    values.update(overrides)
    return db.plan_native_child(**values)


def test_planning_refuses_existing_terminal_without_attaching_child():
    assert db.engine.dialect.name == "postgresql"
    db.create_terminal(TERMINAL_ID, "existing-owner", "existing-window", "claude_code", "developer")
    before = db.get_terminal_metadata(TERMINAL_ID)
    assert db.get_native_child_for_terminal(TERMINAL_ID) is None
    with pytest.raises(ValueError):
        plan(TERMINAL_ID)
    assert db.get_terminal_metadata(TERMINAL_ID) == before
    assert db.get_native_child_for_terminal(TERMINAL_ID) is None


@pytest.mark.asyncio
async def test_actual_creation_collision_does_not_attach_receipt_or_allocate_backend(create_mocks):
    db.create_terminal(TERMINAL_ID, "existing-owner", "existing-window", "claude_code", "developer")
    before = db.get_terminal_metadata(TERMINAL_ID)
    create_mocks.db_create_terminal.side_effect = db.create_terminal
    with pytest.raises((IntegrityError, ValueError)):
        await terminal_service.create_terminal(
            "claude_code", "developer", session_name=SESSION_NAME, caller_id="parent"
        )
    assert db.get_terminal_metadata(TERMINAL_ID) == before
    assert db.get_native_child_for_terminal(TERMINAL_ID) is None
    create_mocks.backend.create_window.assert_not_called()
    create_mocks.provider_manager.create_provider.assert_not_called()


def test_unclaimed_root_cannot_occupy_planned_child_id():
    child = plan(TERMINAL_ID)
    with pytest.raises(ValueError):
        db.create_terminal(
            TERMINAL_ID, "foreign-owner", "foreign-window", "claude_code", "developer"
        )
    assert db.get_terminal_metadata(TERMINAL_ID) is None
    assert db.get_native_child_for_terminal(TERMINAL_ID) == child


def register_claim(terminal_id, child, **overrides):
    values = dict(
        caller_id="parent",
        provider="claude_code",
        agent_profile="developer",
        expected_native_child_id=child["id"],
    )
    values.update(overrides)
    return db.create_terminal(terminal_id, "claimed-session", "claimed-window", **values)


def test_exact_witness_registers_only_the_planned_child():
    child = plan(TERMINAL_ID)
    row = register_claim(TERMINAL_ID, child)
    assert row["caller_id"] == child["parent_terminal_id"]
    assert row["provider"] == child["provider"]
    assert row["agent_profile"] == child["agent_profile"]
    assert db.get_native_child_for_terminal(TERMINAL_ID) == child


@pytest.mark.parametrize(
    "mismatch",
    ["witness", "missing", "parent", "provider", "profile", "acknowledged", "failed", "reconcile"],
)
def test_mismatching_claim_has_no_registration_incarnation_or_idempotency_effect(mismatch):
    child = plan(TERMINAL_ID)
    overrides = dict(
        new_session_incarnation=True,
        session_incarnation_id="attempt-incarnation",
        idempotency_key="attempt-key",
        request_fingerprint="attempt",
    )
    if mismatch == "witness":
        overrides["expected_native_child_id"] = "foreign-witness"
    elif mismatch == "missing":
        overrides["expected_native_child_id"] = None
    elif mismatch == "parent":
        overrides["caller_id"] = "foreign-parent"
    elif mismatch == "provider":
        overrides["provider"] = "codex"
    elif mismatch == "profile":
        overrides["agent_profile"] = "foreign-profile"
    else:
        db.transition_native_child(TERMINAL_ID, mismatch)
    before = db.get_native_child_for_terminal(TERMINAL_ID)
    with pytest.raises(db.NativeChildIdentityConflict):
        register_claim(TERMINAL_ID, child, **overrides)
    assert db.get_terminal_metadata(TERMINAL_ID) is None
    assert db.get_native_child_for_terminal(TERMINAL_ID) == before
    assert db.get_idempotency_record("attempt-key") is None
    with db.SessionLocal() as session:
        assert session.get(db.SessionIncarnationModel, "claimed-session") is None


def test_missing_receipt_does_not_accept_a_witness():
    with pytest.raises(db.NativeChildIdentityConflict):
        register_claim(TERMINAL_ID, {"id": "absent-receipt"})
    assert db.get_terminal_metadata(TERMINAL_ID) is None


def wait_for_advisory_wait(pid):
    deadline = time.monotonic() + 5
    with db.engine.connect() as connection:
        while time.monotonic() < deadline:
            connection.execute(text("SELECT pg_stat_clear_snapshot()"))
            row = connection.execute(
                text("SELECT wait_event_type,wait_event FROM pg_stat_activity WHERE pid=:pid"),
                {"pid": pid},
            ).first()
            if row == ("Lock", "advisory"):
                return
            time.sleep(0.01)
    pytest.fail("Owned PostgreSQL actor never reached advisory admission wait")


@pytest.mark.parametrize("first", ["terminal", "plan", "plan_rollback"])
def test_two_connections_serialize_both_writer_orders_and_release_on_rollback(monkeypatch, first):
    original_factory = db.SessionLocal
    original_lock = db.lock_terminal_registration_identity
    entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
    roles = threading.local()
    waiter_pids = []
    rollback_error = RuntimeError("controlled precommit rollback")

    def factory():
        session = original_factory()
        if getattr(roles, "role", None) == "first":

            def gate(_session):
                entered.set()
                assert release.wait(timeout=10)
                if first == "plan_rollback":
                    raise rollback_error

            event.listen(session, "before_commit", gate)
        return session

    def observed_lock(session, key):
        if getattr(roles, "role", None) == "second":
            waiter_pids.append(session.connection().connection.driver_connection.info.backend_pid)
            waiting.set()
        return original_lock(session, key)

    monkeypatch.setattr(db, "SessionLocal", factory)
    monkeypatch.setattr(db, "lock_terminal_registration_identity", observed_lock)

    def actor(role):
        roles.role = role
        if (role == "first") == (first == "terminal"):
            return db.create_terminal(
                TERMINAL_ID, "root-session", "root-window", "claude_code", "developer"
            )
        return plan(TERMINAL_ID)

    def independent():
        roles.role = "independent"
        return plan("independent-terminal")

    with ThreadPoolExecutor(max_workers=3) as pool:
        holder = pool.submit(actor, "first")
        contender = None
        try:
            assert entered.wait(timeout=5)
            contender = pool.submit(actor, "second")
            assert waiting.wait(timeout=5)
            wait_for_advisory_wait(waiter_pids[0])
            assert not contender.done()
            assert (
                pool.submit(independent).result(timeout=5)["terminal_id"] == "independent-terminal"
            )
        finally:
            release.set()
        assert contender is not None
        if first == "plan_rollback":
            with pytest.raises(RuntimeError) as raised:
                holder.result(timeout=10)
            assert raised.value is rollback_error
            contender.result(timeout=10)
            assert db.get_native_child_for_terminal(TERMINAL_ID) is None
            assert db.get_terminal_metadata(TERMINAL_ID) is not None
        else:
            holder.result(timeout=10)
            with pytest.raises(db.NativeChildIdentityConflict):
                contender.result(timeout=10)
            if first == "plan":
                child = db.get_native_child_for_terminal(TERMINAL_ID)
                assert db.get_terminal_metadata(TERMINAL_ID) is None
                register_claim(TERMINAL_ID, child)
            else:
                assert db.get_native_child_for_terminal(TERMINAL_ID) is None


def test_planning_in_an_independent_process_waits_for_registration_commit(tmp_path):
    marker = tmp_path / "child-admission.json"
    child_code = r"""
import json,sys
from pathlib import Path
from cli_agent_orchestrator.clients import database as db
original=db.lock_terminal_registration_identity

def observed(session,key):
    pid=session.connection().connection.driver_connection.info.backend_pid
    Path(sys.argv[2]).write_text(json.dumps({"pid":pid}))
    original(session,key)

db.lock_terminal_registration_identity=observed
try:
    db.plan_native_child(parent_terminal_id="parent",terminal_id=sys.argv[1],provider="claude_code",agent_profile="developer",lease_seconds=60)
except db.NativeChildIdentityConflict:
    print("identity-conflict",flush=True)
else:
    raise SystemExit(2)
"""
    process = None
    with db.SessionLocal() as session:
        db.lock_terminal_registration_identity(session, TERMINAL_ID)
        session.add(
            db.TerminalModel(
                id=TERMINAL_ID,
                tmux_session="process-owner",
                tmux_window="window",
                provider="claude_code",
                agent_profile="developer",
            )
        )
        session.flush()
        try:
            process = subprocess.Popen(
                [sys.executable, "-c", child_code, TERMINAL_ID, str(marker)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 8
            while not marker.exists() and time.monotonic() < deadline:
                assert process.poll() is None, "Owned process exited before its admission marker"
                time.sleep(0.01)
            assert marker.exists()
            wait_for_advisory_wait(json.loads(marker.read_text())["pid"])
            session.commit()
            stdout, _stderr = process.communicate(timeout=10)
            assert process.returncode == 0
            assert stdout.strip() == "identity-conflict"
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    assert db.get_native_child_for_terminal(TERMINAL_ID) is None
    assert db.get_terminal_metadata(TERMINAL_ID) is not None


def test_unsupported_transaction_snapshot_is_refused(monkeypatch):
    repeatable = db.engine.execution_options(isolation_level="REPEATABLE READ")
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=repeatable))
    with pytest.raises(db.NativeChildIdentityConflict, match="READ COMMITTED"):
        plan(TERMINAL_ID)
    assert db.get_native_child_for_terminal(TERMINAL_ID) is None


@pytest.mark.asyncio
async def test_cancelled_planner_drains_before_returning_without_allocating_backend(
    create_mocks, monkeypatch
):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = terminal_service.plan_native_child

    def paused_plan(**kwargs):
        result = original(**kwargs)
        entered.set()
        try:
            assert release.wait(timeout=5)
            return result
        finally:
            finished.set()

    monkeypatch.setattr(terminal_service, "plan_native_child", paused_plan)
    task = asyncio.create_task(
        terminal_service.create_terminal(
            "claude_code", "developer", session_name=SESSION_NAME, caller_id="parent"
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert db.get_native_child_for_terminal(TERMINAL_ID) is not None
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled() and finished.is_set()
    assert db.get_native_child_for_terminal(TERMINAL_ID)["state"] == "planned"
    assert db.get_terminal_metadata(TERMINAL_ID) is None
    create_mocks.backend.create_window.assert_not_called()
    create_mocks.provider_manager.create_provider.assert_not_called()
    with pytest.raises(db.NativeChildIdentityConflict):
        db.create_terminal(TERMINAL_ID, "foreign", "window", "claude_code", "developer")
