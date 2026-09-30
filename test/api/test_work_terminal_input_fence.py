"""Ordinary terminal mutation routes must honor durable Work terminal ownership."""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.backends.work_backend import WorkBackendView
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import terminal_service
from cli_agent_orchestrator.services.work_admission import WorkAdmission


class BackendSpy:
    def __init__(self):
        self.effects = []

    def send_keys(self, *args, **kwargs):
        self.effects.append(("input", args, kwargs))

    def send_special_key(self, *args, **kwargs):
        self.effects.append(("key", args, kwargs))

    def _before_work_effect(self, _restriction, before_effect):
        if before_effect() is not None:
            raise AssertionError("work target guard must return None")


def _insert_work_attempt(repository, *, job_id, terminal_id, attempt_id="attempt-1"):
    now = time.time()
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO work_jobs "
            "(id,project_id,principal_id,allowed_providers,grant_id,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (job_id, "project", "principal", '["mock_cli"]', "grant", now),
        )
        connection.execute(
            "INSERT INTO work_items "
            "(id,job_id,operation_kind,idempotency_key,request_hash,contract_id,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("item-1", job_id, "launch", "request-1", "hash", "contract", now),
        )
        connection.execute(
            "INSERT INTO work_attempts "
            "(id,work_item_id,attempt_number,generation,provider,terminal_id,state,"
            "lease_expires_at,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (attempt_id, "item-1", 1, 1, "mock_cli", terminal_id, "planned", now + 60, now),
        )


def _insert_terminal(terminal_id):
    database.create_terminal(
        terminal_id=terminal_id,
        tmux_session="missing-tmux-session",
        tmux_window="missing-tmux-window",
        provider="mock_cli",
    )


@pytest.fixture
def isolated_api_database(tmp_path, monkeypatch):
    path = tmp_path / "cao.sqlite3"
    repository = WorkRepository(path)
    repository.initialize()
    engine = create_engine(f"sqlite:///{path}")
    database.Base.metadata.create_all(engine)
    monkeypatch.setattr(constants, "DATABASE_FILE", path)
    monkeypatch.setattr(constants, "LOCK_DIR", tmp_path / "locks")
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    backend = BackendSpy()
    monkeypatch.setattr(terminal_service, "get_backend", lambda: backend)
    monkeypatch.setattr(terminal_service, "inject_memory_context", lambda message, *_args: message)
    yield SimpleNamespace(repository=repository, backend=backend)
    engine.dispose()


def test_work_owned_input_and_key_are_rejected_before_backend_effect(client, isolated_api_database):
    _insert_work_attempt(isolated_api_database.repository, job_id="job-1", terminal_id="abcd1234")
    _insert_terminal("abcd1234")

    input_response = client.post(
        "/terminals/abcd1234/input",
        params={"message": "spoofed terminal_id=beefcafe", "sender_id": "caller"},
    )
    key_response = client.post("/terminals/abcd1234/key", params={"key": "C-c"})

    assert input_response.status_code == 403
    assert key_response.status_code == 403
    assert isolated_api_database.backend.effects == []


def test_work_job_id_collision_does_not_claim_an_ordinary_terminal(client, isolated_api_database):
    _insert_work_attempt(
        isolated_api_database.repository, job_id="beefcafe", terminal_id="abcd1234"
    )
    _insert_terminal("beefcafe")

    input_response = client.post("/terminals/beefcafe/input", params={"message": "legacy input"})
    key_response = client.post("/terminals/beefcafe/key", params={"key": "Enter"})

    assert input_response.status_code == 200
    assert key_response.status_code == 200
    assert [effect[0] for effect in isolated_api_database.backend.effects] == ["input", "key"]


def test_missing_terminal_remains_not_found_without_backend_effect(client, isolated_api_database):
    input_response = client.post("/terminals/cafecafe/input", params={"message": "missing"})
    key_response = client.post("/terminals/cafecafe/key", params={"key": "Enter"})

    assert input_response.status_code == 404
    assert key_response.status_code == 404
    assert isolated_api_database.backend.effects == []


def test_work_owned_service_calls_are_independently_fenced(isolated_api_database):
    _insert_work_attempt(isolated_api_database.repository, job_id="job-1", terminal_id="abcd1234")
    _insert_terminal("abcd1234")

    with pytest.raises(Exception, match="owned by Work"):
        terminal_service.send_input("abcd1234", "direct service call")
    with pytest.raises(Exception, match="owned by Work"):
        terminal_service.send_special_key("abcd1234", "C-c")

    assert isolated_api_database.backend.effects == []


def test_ordinary_delete_rejects_work_owned_terminal_before_teardown(
    client, isolated_api_database, monkeypatch
):
    terminal_id = "abcd1234"
    _insert_work_attempt(isolated_api_database.repository, job_id="job-1", terminal_id=terminal_id)
    _insert_terminal(terminal_id)
    teardown_calls = []
    snapshot_calls = []
    monkeypatch.setattr(
        terminal_service,
        "capture_terminal_snapshot",
        lambda _id: snapshot_calls.append(_id),
    )
    monkeypatch.setattr(
        terminal_service,
        "dismantle_terminal_runtime",
        lambda *_args, **_kwargs: teardown_calls.append(True) or True,
    )

    response = client.delete(f"/terminals/{terminal_id}")
    assert response.status_code == 403
    with pytest.raises(terminal_service.WorkOwnedTerminalError):
        terminal_service.delete_terminal(terminal_id)
    assert teardown_calls == []
    assert snapshot_calls == []
    assert database.get_terminal_metadata(terminal_id) is not None


def test_ordinary_delete_fails_closed_when_work_store_cannot_be_verified(
    client, isolated_api_database, monkeypatch
):
    terminal_id = "abcd1234"
    _insert_terminal(terminal_id)
    with isolated_api_database.repository.transaction() as connection:
        connection.execute("UPDATE work_migrations SET checksum='bad' WHERE version=1")
    teardown_calls = []
    snapshot_calls = []
    monkeypatch.setattr(
        terminal_service,
        "capture_terminal_snapshot",
        lambda _id: snapshot_calls.append(_id),
    )
    monkeypatch.setattr(
        terminal_service,
        "dismantle_terminal_runtime",
        lambda *_args, **_kwargs: teardown_calls.append(True) or True,
    )

    response = client.delete(f"/terminals/{terminal_id}")
    assert response.status_code == 503
    with pytest.raises(terminal_service.WorkOwnershipStoreUnavailableError):
        terminal_service.delete_terminal(terminal_id)
    assert teardown_calls == []
    assert snapshot_calls == []
    assert database.get_terminal_metadata(terminal_id) is not None


def test_ordinary_delete_holds_dispatch_lock_through_row_delete_then_emits_plugin(
    isolated_api_database, monkeypatch
):
    from cli_agent_orchestrator.services import work_terminal
    from cli_agent_orchestrator.services.work_terminal import terminal_dispatch_lock

    terminal_id = "abcd1234"
    _insert_terminal(terminal_id)
    monkeypatch.setattr(work_terminal, "_TERMINAL_DISPATCH_LOCK_TIMEOUT_SECONDS", 0.3)
    metadata = {"tmux_session": "s", "tmux_window": "w", "agent_profile": None}
    monkeypatch.setattr(terminal_service, "capture_terminal_snapshot", lambda _id: metadata)
    monkeypatch.setattr(
        terminal_service, "dismantle_terminal_runtime", lambda *_args, **_kwargs: True
    )
    acquired = threading.Event()
    probe_threads = []
    observed = []

    def delete_row(*_args, **_kwargs):
        def probe():
            with terminal_dispatch_lock(constants.DATABASE_FILE, terminal_id):
                acquired.set()

        thread = threading.Thread(target=probe, daemon=True)
        probe_threads.append(thread)
        thread.start()
        observed.append(("row_delete_lock_held", not acquired.wait(timeout=0.1)))
        return True

    def plugin_callback(*_args):
        with terminal_dispatch_lock(constants.DATABASE_FILE, terminal_id):
            observed.append(("plugin_lock_released", True))

    monkeypatch.setattr(terminal_service, "delete_terminal_row", delete_row)
    monkeypatch.setattr(terminal_service, "dispatch_plugin_event", plugin_callback)
    try:
        assert terminal_service.delete_terminal(terminal_id, registry=object()) is True
    finally:
        for thread in probe_threads:
            thread.join(timeout=5)
    assert observed == [("row_delete_lock_held", True), ("plugin_lock_released", True)]
    assert acquired.is_set()


@pytest.mark.parametrize("ownership", ["owned", "store_corrupt"])
def test_ordinary_exit_maps_work_ownership_failure_before_transport(
    client, isolated_api_database, monkeypatch, ownership
):
    from cli_agent_orchestrator.services import terminal_service

    terminal_id = "abcd1234"
    _insert_terminal(terminal_id)
    if ownership == "owned":
        _insert_work_attempt(
            isolated_api_database.repository, job_id="job-1", terminal_id=terminal_id
        )
    else:
        with isolated_api_database.repository.transaction() as connection:
            connection.execute("UPDATE work_migrations SET checksum='bad' WHERE version=1")
    monkeypatch.setattr(
        terminal_service.provider_manager,
        "get_provider",
        lambda _id: SimpleNamespace(exit_cli=lambda: "C-d"),
    )
    monkeypatch.setattr(
        terminal_service.status_monitor, "get_status", lambda _id: TerminalStatus.IDLE
    )

    response = client.post(f"/terminals/{terminal_id}/exit")
    assert response.status_code == (403 if ownership == "owned" else 503)
    assert isolated_api_database.backend.effects == []


@pytest.mark.parametrize("ownership", ["owned", "store_corrupt"])
def test_session_delete_rejects_uncertain_work_terminal_before_snapshot_or_kill(
    client, isolated_api_database, monkeypatch, ownership
):
    from cli_agent_orchestrator.services import session_service

    terminal_id = "abcd1234"
    _insert_terminal(terminal_id)
    if ownership == "owned":
        _insert_work_attempt(
            isolated_api_database.repository, job_id="job-1", terminal_id=terminal_id
        )
        expected_error = terminal_service.WorkOwnedTerminalError
        expected_status = 403
    else:
        with isolated_api_database.repository.transaction() as connection:
            connection.execute("UPDATE work_migrations SET checksum='bad' WHERE version=1")
        expected_error = terminal_service.WorkOwnershipStoreUnavailableError
        expected_status = 503
    snapshot_calls = []
    monkeypatch.setattr(
        terminal_service,
        "capture_terminal_snapshot",
        lambda _id: snapshot_calls.append(_id),
    )

    response = client.delete("/sessions/missing-tmux-session")
    assert response.status_code == expected_status
    with pytest.raises(expected_error):
        session_service.delete_session("missing-tmux-session")
    assert snapshot_calls == []
    assert database.get_terminal_metadata(terminal_id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("ownership", ["owned", "store_corrupt"])
async def test_flow_recycle_rejects_uncertain_work_terminal_before_fifo_or_kill(
    tmp_path, isolated_api_database, monkeypatch, ownership
):
    from cli_agent_orchestrator.models.flow import Flow
    from cli_agent_orchestrator.services import flow_service

    flow_file = tmp_path / "flow.md"
    flow_file.write_text(
        "---\nname: guarded-flow\nschedule: '* * * * *'\n"
        "agent_profile: developer\n---\nPrompt.\n",
        encoding="utf-8",
    )
    flow = Flow(
        name="guarded-flow",
        file_path=str(flow_file),
        schedule="* * * * *",
        agent_profile="developer",
        provider="kiro_cli",
        script="",
        enabled=True,
    )
    if ownership == "owned":
        _insert_work_attempt(
            isolated_api_database.repository, job_id="job-1", terminal_id="abcd1234"
        )
        expected_error = terminal_service.WorkOwnedTerminalError
    else:
        with isolated_api_database.repository.transaction() as connection:
            connection.execute("UPDATE work_migrations SET checksum='bad' WHERE version=1")
        expected_error = terminal_service.WorkOwnershipStoreUnavailableError
    effects = []
    monkeypatch.setattr(flow_service, "db_get_flow", lambda _name: flow)
    monkeypatch.setattr(flow_service, "db_update_flow_run_times", lambda *_args, **_kw: None)
    monkeypatch.setattr(
        flow_service, "list_terminals_by_session", lambda _name: [{"id": "abcd1234"}]
    )
    monkeypatch.setattr(
        flow_service,
        "get_backend",
        lambda: SimpleNamespace(
            session_exists=lambda _name: True,
            kill_session=lambda _name: effects.append("kill"),
        ),
    )
    monkeypatch.setattr(flow_service, "_is_terminal_busy", lambda _id: False)

    async def unexpected_create(**_kwargs):
        raise AssertionError("flow reached provider creation before ownership rejection")

    monkeypatch.setattr(flow_service, "create_terminal", unexpected_create)
    monkeypatch.setattr(
        flow_service.fifo_manager, "stop_reader", lambda _id: effects.append("fifo")
    )

    with pytest.raises(expected_error):
        await flow_service.execute_flow("guarded-flow")
    assert effects == []


@pytest.mark.parametrize("session_name", ["cao-flow-guarded-flow", "flow-guarded-flow"])
def test_work_session_names_cannot_collide_with_flow_recycle_namespace(session_name):
    from cli_agent_orchestrator.services.work_terminal import managed_session_name

    with pytest.raises(ValueError):
        managed_session_name(session_name)


@pytest.mark.asyncio
@pytest.mark.parametrize("ownership", ["owned", "store_corrupt", "store_missing"])
async def test_flow_preflight_rejects_work_before_script_or_last_run(
    tmp_path, isolated_api_database, monkeypatch, ownership
):
    from cli_agent_orchestrator.models.flow import Flow
    from cli_agent_orchestrator.services import flow_service

    flow_file = tmp_path / "flow.md"
    flow_file.write_text(
        "---\nname: guarded-flow\nschedule: '* * * * *'\n"
        "agent_profile: developer\n---\nPrompt.\n",
        encoding="utf-8",
    )
    script = tmp_path / "flow-script"
    script.write_text("#!/bin/sh\necho should-not-run\n", encoding="utf-8")
    flow = Flow(
        name="guarded-flow",
        file_path=str(flow_file),
        schedule="* * * * *",
        agent_profile="developer",
        provider="kiro_cli",
        script=str(script),
        enabled=True,
    )
    if ownership == "owned":
        _insert_work_attempt(
            isolated_api_database.repository, job_id="job-1", terminal_id="abcd1234"
        )
        expected_error = terminal_service.WorkOwnedTerminalError
    elif ownership == "store_corrupt":
        with isolated_api_database.repository.transaction() as connection:
            connection.execute("UPDATE work_migrations SET checksum='bad' WHERE version=1")
        expected_error = terminal_service.WorkOwnershipStoreUnavailableError
    else:
        monkeypatch.setattr(constants, "DATABASE_FILE", tmp_path / "absent-work.db")
        expected_error = terminal_service.WorkOwnershipStoreUnavailableError

    effects = []
    monkeypatch.setattr(flow_service, "db_get_flow", lambda _name: flow)
    monkeypatch.setattr(
        flow_service,
        "list_terminals_by_session",
        lambda _name: [{"id": "abcd1234"}],
    )
    monkeypatch.setattr(
        flow_service,
        "subprocess",
        SimpleNamespace(
            run=lambda *_args, **_kwargs: effects.append("script")
            or SimpleNamespace(returncode=0, stdout='{"execute": false, "output": {}}')
        ),
    )
    monkeypatch.setattr(
        flow_service,
        "db_update_flow_run_times",
        lambda *_args, **_kwargs: effects.append("last_run"),
    )

    error = None
    try:
        await flow_service.execute_flow("guarded-flow")
    except Exception as caught:
        error = caught
    assert effects == []
    assert isinstance(error, expected_error)


@pytest.mark.parametrize(
    ("error_type", "status_code"),
    [
        (terminal_service.WorkOwnedTerminalError, 403),
        (terminal_service.WorkOwnershipStoreUnavailableError, 503),
    ],
)
def test_manual_flow_run_maps_work_ownership_failures(client, monkeypatch, error_type, status_code):
    from cli_agent_orchestrator.services import flow_service

    async def denied(_name):
        raise error_type("durable Work ownership denied")

    monkeypatch.setattr(flow_service, "execute_flow", denied)
    response = client.post("/flows/guarded-flow/run")
    assert response.status_code == status_code


@pytest.mark.asyncio
@pytest.mark.parametrize("store_change", ["removed", "replaced"])
async def test_flow_recycle_reverifies_work_store_after_script_even_without_rows(
    tmp_path, isolated_api_database, monkeypatch, store_change
):
    from cli_agent_orchestrator.models.flow import Flow
    from cli_agent_orchestrator.services import flow_service

    flow_file = tmp_path / "flow.md"
    flow_file.write_text(
        "---\nname: guarded-flow\nschedule: '* * * * *'\n"
        "agent_profile: developer\n---\nPrompt.\n",
        encoding="utf-8",
    )
    script = tmp_path / "flow-script"
    script.write_text("#!/bin/sh\necho changes-store\n", encoding="utf-8")
    flow = Flow(
        name="guarded-flow",
        file_path=str(flow_file),
        schedule="* * * * *",
        agent_profile="developer",
        provider="kiro_cli",
        script=str(script),
        enabled=True,
    )
    effects = []

    def change_store(*_args, **_kwargs):
        effects.append("script")
        constants.DATABASE_FILE.unlink()
        if store_change == "replaced":
            with sqlite3.connect(constants.DATABASE_FILE) as connection:
                connection.execute("CREATE TABLE unrelated (id TEXT)")
        return SimpleNamespace(returncode=0, stdout='{"execute": true, "output": {}}', stderr="")

    async def unexpected_create(**_kwargs):
        effects.append("create")
        raise AssertionError("flow created a terminal after losing the Work store")

    monkeypatch.setattr(flow_service, "db_get_flow", lambda _name: flow)
    monkeypatch.setattr(flow_service, "list_terminals_by_session", lambda _name: [])
    monkeypatch.setattr(flow_service, "db_update_flow_run_times", lambda *_a, **_k: None)
    monkeypatch.setattr(flow_service, "subprocess", SimpleNamespace(run=change_store))
    monkeypatch.setattr(flow_service, "create_terminal", unexpected_create)
    monkeypatch.setattr(
        flow_service,
        "get_backend",
        lambda: SimpleNamespace(
            session_exists=lambda _name: True,
            kill_session=lambda _name: effects.append("kill"),
        ),
    )
    monkeypatch.setattr(
        flow_service.fifo_manager, "stop_reader", lambda _id: effects.append("fifo")
    )
    monkeypatch.setattr(
        flow_service,
        "delete_terminals_by_session",
        lambda _name: effects.append("delete_rows"),
    )

    error = None
    try:
        await flow_service.execute_flow("guarded-flow")
    except Exception as caught:
        error = caught
    assert effects == ["script"]
    assert isinstance(error, terminal_service.WorkOwnershipStoreUnavailableError)


def test_flow_preflight_enumeration_failure_maps_to_503_before_script_or_last_run(
    client, tmp_path, isolated_api_database, monkeypatch
):
    import asyncio

    from cli_agent_orchestrator.models.flow import Flow
    from cli_agent_orchestrator.services import flow_service

    flow_file = tmp_path / "flow.md"
    flow_file.write_text(
        "---\nname: guarded-flow\nschedule: '* * * * *'\n"
        "agent_profile: developer\n---\nPrompt.\n",
        encoding="utf-8",
    )
    script = tmp_path / "flow-script"
    script.write_text("#!/bin/sh\necho should-not-run\n", encoding="utf-8")
    flow = Flow(
        name="guarded-flow",
        file_path=str(flow_file),
        schedule="* * * * *",
        agent_profile="developer",
        provider="kiro_cli",
        script=str(script),
        enabled=True,
    )
    effects = []

    def unreadable_rows(_name):
        raise sqlite3.DatabaseError("terminal table unreadable")

    monkeypatch.setattr(flow_service, "db_get_flow", lambda _name: flow)
    monkeypatch.setattr(flow_service, "list_terminals_by_session", unreadable_rows)
    monkeypatch.setattr(
        flow_service,
        "subprocess",
        SimpleNamespace(run=lambda *_a, **_k: effects.append("script")),
    )
    monkeypatch.setattr(
        flow_service,
        "db_update_flow_run_times",
        lambda *_a, **_k: effects.append("last_run"),
    )

    response = client.post("/flows/guarded-flow/run")
    assert response.status_code == 503
    with pytest.raises(terminal_service.WorkOwnershipStoreUnavailableError):
        asyncio.run(flow_service.execute_flow("guarded-flow"))
    assert effects == []


@pytest.mark.asyncio
async def test_flow_recycle_enumeration_failure_stops_before_runtime_effects(
    tmp_path, isolated_api_database, monkeypatch
):
    from cli_agent_orchestrator.models.flow import Flow
    from cli_agent_orchestrator.services import flow_service

    flow_file = tmp_path / "flow.md"
    flow_file.write_text(
        "---\nname: guarded-flow\nschedule: '* * * * *'\n"
        "agent_profile: developer\n---\nPrompt.\n",
        encoding="utf-8",
    )
    flow = Flow(
        name="guarded-flow",
        file_path=str(flow_file),
        schedule="* * * * *",
        agent_profile="developer",
        provider="kiro_cli",
        script="",
        enabled=True,
    )
    effects = []
    enumerations = 0

    def list_rows(_name):
        nonlocal enumerations
        enumerations += 1
        if enumerations == 3:
            raise sqlite3.DatabaseError("terminal table unreadable")
        return []

    async def unexpected_create(**_kwargs):
        effects.append("create")

    monkeypatch.setattr(flow_service, "db_get_flow", lambda _name: flow)
    monkeypatch.setattr(flow_service, "list_terminals_by_session", list_rows)
    monkeypatch.setattr(
        flow_service,
        "db_update_flow_run_times",
        lambda *_a, **_k: effects.append("last_run"),
    )
    monkeypatch.setattr(flow_service, "create_terminal", unexpected_create)
    monkeypatch.setattr(
        flow_service,
        "get_backend",
        lambda: SimpleNamespace(
            session_exists=lambda _name: True,
            kill_session=lambda _name: effects.append("kill"),
        ),
    )
    monkeypatch.setattr(
        flow_service.fifo_manager, "stop_reader", lambda _id: effects.append("fifo")
    )
    monkeypatch.setattr(
        flow_service,
        "delete_terminals_by_session",
        lambda _name: effects.append("delete_rows"),
    )

    with pytest.raises(terminal_service.WorkOwnershipStoreUnavailableError):
        await flow_service.execute_flow("guarded-flow")
    assert enumerations == 3
    assert effects == ["last_run"]


def test_flow_recycle_keeps_backend_failures_distinct_from_work_store_failures(
    isolated_api_database, monkeypatch
):
    from cli_agent_orchestrator.services import flow_service

    def backend_unavailable(_name):
        raise RuntimeError("backend liveness failed")

    monkeypatch.setattr(flow_service, "list_terminals_by_session", lambda _name: [])
    monkeypatch.setattr(
        flow_service,
        "get_backend",
        lambda: SimpleNamespace(session_exists=backend_unavailable),
    )

    with pytest.raises(RuntimeError, match="backend liveness failed"):
        flow_service._recycle_flow_session("guarded-flow", "cao-flow-guarded-flow")


@pytest.mark.parametrize("effect", ["input", "key"])
def test_ordinary_transport_rechecks_work_ownership_immediately_before_effect(
    effect, isolated_api_database, monkeypatch
):
    terminal_id = "abcd1234"
    _insert_terminal(terminal_id)
    ownership_persisted = False

    def persist_work_ownership(*_args, **_kwargs):
        nonlocal ownership_persisted
        _insert_work_attempt(
            isolated_api_database.repository,
            job_id="job-1",
            terminal_id=terminal_id,
        )
        ownership_persisted = True

    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", lambda *_args: None)
    if effect == "input":
        monkeypatch.setattr(
            terminal_service.status_monitor, "notify_input_sent", lambda *_args, **_kwargs: None
        )
        monkeypatch.setattr(
            terminal_service.status_monitor, "clear_rolling_buffer", persist_work_ownership
        )
    else:
        monkeypatch.setattr(
            terminal_service.status_monitor, "notify_input_sent", persist_work_ownership
        )

    with pytest.raises(terminal_service.WorkOwnedTerminalError, match="owned by Work"):
        if effect == "input":
            terminal_service.send_input(terminal_id, "ordinary input")
        else:
            terminal_service.send_special_key(terminal_id, "C-c")

    assert ownership_persisted
    assert isolated_api_database.backend.effects == []


@pytest.mark.parametrize("effect", ["input", "key"])
def test_ordinary_send_holds_terminal_dispatch_lock_from_guard_through_transport(
    effect, tmp_path, isolated_api_database, monkeypatch
):
    from cli_agent_orchestrator.services.work_terminal import terminal_dispatch_lock

    monkeypatch.setattr(constants, "LOCK_DIR", tmp_path / "locks")
    terminal_id = "abcd1234"
    _insert_terminal(terminal_id)
    transport_returned = threading.Event()
    probe_threads = []
    probe_results = {}
    blocked_during = {}

    def start_lock_probe(label):
        started = threading.Event()
        acquired = threading.Event()

        def probe():
            started.set()
            with terminal_dispatch_lock(constants.DATABASE_FILE, terminal_id):
                probe_results[label] = transport_returned.is_set()
                acquired.set()

        thread = threading.Thread(target=probe, daemon=True)
        probe_threads.append(thread)
        thread.start()
        assert started.wait(timeout=5)
        return acquired

    original_ensure = terminal_service.ensure_terminal_is_not_work_owned
    checked_once = False

    def observe_ownership_check(target_id):
        nonlocal checked_once
        if target_id == terminal_id and not checked_once:
            checked_once = True
            acquired = start_lock_probe("ownership_check")
            blocked_during["ownership_check"] = not acquired.wait(timeout=0.1)
        return original_ensure(target_id)

    class LockProbeBackend(BackendSpy):
        def _record_effect(self, name, args, kwargs):
            self.effects.append((name, args, kwargs))
            acquired = start_lock_probe("transport")
            blocked_during["transport"] = not acquired.wait(timeout=0.1)
            transport_returned.set()

        def send_keys(self, *args, **kwargs):
            self._record_effect("input", args, kwargs)

        def send_special_key(self, *args, **kwargs):
            self._record_effect("key", args, kwargs)

    backend = LockProbeBackend()
    monkeypatch.setattr(terminal_service, "get_backend", lambda: backend)
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", lambda *_args: None)
    monkeypatch.setattr(
        terminal_service, "ensure_terminal_is_not_work_owned", observe_ownership_check
    )
    monkeypatch.setattr(
        terminal_service.status_monitor, "notify_input_sent", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        terminal_service.status_monitor, "clear_rolling_buffer", lambda *_args, **_kwargs: None
    )

    try:
        if effect == "input":
            assert terminal_service.send_input(terminal_id, "ordinary input") is True
        else:
            assert terminal_service.send_special_key(terminal_id, "C-c") is True
    finally:
        for thread in probe_threads:
            thread.join(timeout=5)

    assert checked_once
    assert blocked_during == {"ownership_check": True, "transport": True}
    assert probe_results == {"ownership_check": True, "transport": True}
    assert [record[0] for record in backend.effects] == [effect]


def test_ordinary_send_releases_terminal_dispatch_lock_before_plugin_callback(
    isolated_api_database, monkeypatch
):
    from cli_agent_orchestrator.models.inbox import OrchestrationType
    from cli_agent_orchestrator.services import work_terminal

    terminal_id = "abcd1234"
    _insert_terminal(terminal_id)
    callback_calls = []
    monkeypatch.setattr(work_terminal, "_TERMINAL_DISPATCH_LOCK_TIMEOUT_SECONDS", 0.3)

    def observe_plugin_dispatch(*_args):
        callback_calls.append(True)
        assert terminal_service.send_input(terminal_id, "nested input") is True

    monkeypatch.setattr(terminal_service, "dispatch_plugin_event", observe_plugin_dispatch)
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", lambda *_args: None)
    monkeypatch.setattr(
        terminal_service.status_monitor, "notify_input_sent", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        terminal_service.status_monitor,
        "clear_rolling_buffer",
        lambda *_args, **_kwargs: None,
    )

    assert (
        terminal_service.send_input(
            terminal_id,
            "ordinary input",
            registry=object(),
            sender_id="operator",
            orchestration_type=OrchestrationType.SEND_MESSAGE,
        )
        is True
    )

    assert callback_calls == [True]
    assert [record[0] for record in isolated_api_database.backend.effects] == [
        "input",
        "input",
    ]


def test_managed_input_rejects_a_session_window_mismatch_before_service_side_effects(
    isolated_api_database, monkeypatch
):
    _insert_work_attempt(isolated_api_database.repository, job_id="job-1", terminal_id="abcd1234")
    _insert_terminal("abcd1234")
    side_effects = []

    def guard(effect, terminal_id, session_name=None, window_name=None, file_path=None):
        with isolated_api_database.repository.read_snapshot() as connection:
            WorkAdmission._check_terminal_effect_target(
                connection,
                attempt_id="attempt-1",
                bound_terminal_id="abcd1234",
                effect=effect,
                terminal_id=terminal_id,
                session_name=session_name,
                window_name=window_name,
                file_path=file_path,
            )

    view = WorkBackendView(
        isolated_api_database.backend,
        object(),
        guard,
        terminal_id="abcd1234",
    )
    monkeypatch.setattr(
        terminal_service,
        "get_terminal_metadata",
        lambda _terminal_id: {
            "id": "abcd1234",
            "tmux_session": "spoofed-session",
            "tmux_window": "spoofed-window",
            "provider": "mock_cli",
        },
    )
    monkeypatch.setattr(terminal_service, "get_backend", lambda: view)
    monkeypatch.setattr(
        terminal_service.provider_manager,
        "get_provider",
        lambda *_args: side_effects.append("provider") or None,
    )
    monkeypatch.setattr(
        terminal_service.status_monitor,
        "notify_input_sent",
        lambda *_args: side_effects.append("status"),
    )
    monkeypatch.setattr(
        terminal_service,
        "inject_memory_context",
        lambda message, *_args: side_effects.append("memory") or message,
    )
    monkeypatch.setattr(
        terminal_service,
        "begin_terminal_turn_receipt",
        lambda *_args: side_effects.append("receipt"),
    )

    with pytest.raises(WorkConflict, match="does not match the durable terminal row"):
        terminal_service.send_input("abcd1234", "task", task_delivery=True)

    assert side_effects == []
    assert isolated_api_database.backend.effects == []


def test_managed_special_key_rejects_a_session_window_mismatch_before_notify(
    isolated_api_database, monkeypatch
):
    _insert_work_attempt(isolated_api_database.repository, job_id="job-1", terminal_id="abcd1234")
    _insert_terminal("abcd1234")
    side_effects = []

    def guard(effect, terminal_id, session_name=None, window_name=None, file_path=None):
        with isolated_api_database.repository.read_snapshot() as connection:
            WorkAdmission._check_terminal_effect_target(
                connection,
                attempt_id="attempt-1",
                bound_terminal_id="abcd1234",
                effect=effect,
                terminal_id=terminal_id,
                session_name=session_name,
                window_name=window_name,
                file_path=file_path,
            )

    view = WorkBackendView(
        isolated_api_database.backend,
        object(),
        guard,
        terminal_id="abcd1234",
    )
    monkeypatch.setattr(
        terminal_service,
        "get_terminal_metadata",
        lambda _terminal_id: {
            "id": "abcd1234",
            "tmux_session": "spoofed-session",
            "tmux_window": "spoofed-window",
            "provider": "mock_cli",
        },
    )
    monkeypatch.setattr(terminal_service, "get_backend", lambda: view)
    monkeypatch.setattr(
        terminal_service.status_monitor,
        "notify_input_sent",
        lambda *_args: side_effects.append("status"),
    )

    with pytest.raises(WorkConflict, match="does not match the durable terminal row"):
        terminal_service.send_special_key("abcd1234", "C-c")

    assert side_effects == []
    assert isolated_api_database.backend.effects == []


def test_managed_input_revalidates_immediately_before_receipt_claim(
    isolated_api_database, monkeypatch
):
    _insert_work_attempt(isolated_api_database.repository, job_id="job-1", terminal_id="abcd1234")
    _insert_terminal("abcd1234")
    effects = []
    guard_calls = 0

    def guard(effect, terminal_id, session_name=None, window_name=None, file_path=None):
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 3:
            raise WorkConflict("revoked before receipt claim")
        with isolated_api_database.repository.read_snapshot() as connection:
            WorkAdmission._check_terminal_effect_target(
                connection,
                attempt_id="attempt-1",
                bound_terminal_id="abcd1234",
                effect=effect,
                terminal_id=terminal_id,
                session_name=session_name,
                window_name=window_name,
                file_path=file_path,
            )

    provider = SimpleNamespace(
        requires_turn_receipt=True,
        blocks_new_task_input_for_reconciliation=False,
        blocks_orchestrated_input_while_waiting_user_answer=False,
        assume_processing_on_dispatch=False,
        paste_enter_count=1,
        paste_submit_delay=0.0,
        prepare_input=lambda message: effects.append("prepare") or message,
    )
    monkeypatch.setattr(
        terminal_service,
        "get_terminal_metadata",
        lambda _terminal_id: {
            "id": "abcd1234",
            "tmux_session": "missing-tmux-session",
            "tmux_window": "missing-tmux-window",
            "provider": "mock_cli",
        },
    )
    view = WorkBackendView(
        isolated_api_database.backend,
        object(),
        guard,
        terminal_id="abcd1234",
    )
    monkeypatch.setattr(terminal_service, "get_backend", lambda: view)
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", lambda *_args: provider)
    monkeypatch.setattr(
        terminal_service.status_monitor,
        "get_status",
        lambda *_args: TerminalStatus.IDLE,
    )
    monkeypatch.setattr(terminal_service, "inject_memory_context", lambda message, *_args: message)
    monkeypatch.setattr(
        terminal_service,
        "begin_terminal_turn_receipt",
        lambda *_args: effects.append("receipt"),
    )
    monkeypatch.setattr(
        terminal_service.status_monitor,
        "notify_input_sent",
        lambda *_args, **_kwargs: effects.append("status"),
    )

    with pytest.raises(WorkConflict, match="revoked before receipt claim"):
        terminal_service.send_input("abcd1234", "task", task_delivery=True)

    assert effects == []
    assert isolated_api_database.backend.effects == []


def test_managed_special_key_revalidates_immediately_before_monitor_notify(
    isolated_api_database, monkeypatch
):
    _insert_work_attempt(isolated_api_database.repository, job_id="job-1", terminal_id="abcd1234")
    _insert_terminal("abcd1234")
    effects = []
    guard_calls = 0

    def guard(effect, terminal_id, session_name=None, window_name=None, file_path=None):
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            raise WorkConflict("revoked before monitor notify")
        with isolated_api_database.repository.read_snapshot() as connection:
            WorkAdmission._check_terminal_effect_target(
                connection,
                attempt_id="attempt-1",
                bound_terminal_id="abcd1234",
                effect=effect,
                terminal_id=terminal_id,
                session_name=session_name,
                window_name=window_name,
                file_path=file_path,
            )

    view = WorkBackendView(
        isolated_api_database.backend,
        object(),
        guard,
        terminal_id="abcd1234",
    )
    monkeypatch.setattr(terminal_service, "get_backend", lambda: view)
    monkeypatch.setattr(
        terminal_service,
        "get_terminal_metadata",
        lambda _terminal_id: {
            "id": "abcd1234",
            "tmux_session": "missing-tmux-session",
            "tmux_window": "missing-tmux-window",
            "provider": "mock_cli",
        },
    )
    monkeypatch.setattr(
        terminal_service.status_monitor,
        "notify_input_sent",
        lambda *_args: effects.append("status"),
    )

    with pytest.raises(WorkConflict, match="revoked before monitor notify"):
        terminal_service.send_special_key("abcd1234", "C-c")

    assert effects == []
    assert isolated_api_database.backend.effects == []


def test_work_terminal_fence_survives_service_process_restart(tmp_path):
    """A fresh service process reads ownership from the same durable SQLite store."""
    from sqlalchemy import create_engine

    home = tmp_path / "server-home"
    database_path = home / ".aws" / "cli-agent-orchestrator" / "db" / "cli-agent-orchestrator.db"
    database_path.parent.mkdir(parents=True)
    repository = WorkRepository(database_path)
    repository.initialize()
    engine = create_engine(f"sqlite:///{database_path}")
    database.Base.metadata.create_all(engine)
    _insert_work_attempt(repository, job_id="job-1", terminal_id="abcd1234")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO terminals (id,tmux_session,tmux_window,provider) " "VALUES (?,?,?,?)",
            ("abcd1234", "missing-session", "missing-window", "mock_cli"),
        )
    engine.dispose()

    child_script = """
import asyncio
import json
from fastapi import HTTPException
from cli_agent_orchestrator.api import main

class BackendSpy:
    def __init__(self):
        self.effects = []

    def send_keys(self, *args, **kwargs):
        self.effects.append("input")

    def send_special_key(self, *args, **kwargs):
        self.effects.append("key")

backend = BackendSpy()
main.terminal_service.get_backend = lambda: backend
statuses = []
for route in (
    main.send_terminal_input(None, "abcd1234", "input"),
    main.send_terminal_key("abcd1234", "C-c"),
):
    try:
        asyncio.run(route)
    except HTTPException as error:
        statuses.append(error.status_code)
    else:
        statuses.append(200)

service_results = []
for effect in ("input", "key"):
    try:
        if effect == "input":
            main.terminal_service.send_input("abcd1234", "input")
        else:
            main.terminal_service.send_special_key("abcd1234", "C-c")
    except main.terminal_service.WorkOwnedTerminalError:
        service_results.append("owned")
    else:
        service_results.append("sent")

print(json.dumps({
    "statuses": statuses,
    "service_results": service_results,
    "backend_effects": backend.effects,
}))
    """
    child_env = os.environ.copy()
    child_env["HOME"] = str(home)
    child_env["CAO_HOME_DIR"] = str(home / ".aws" / "cli-agent-orchestrator")
    child_env.pop("AUTH0_DOMAIN", None)
    child_env.pop("AUTH0_AUDIENCE", None)
    child_env.pop("CAO_AUTH_JWKS_URI", None)
    result = subprocess.run(
        [sys.executable, "-c", child_script],
        cwd=os.getcwd(),
        env=child_env,
        capture_output=True,
        text=True,
        timeout=90,
        check=True,
    )
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "statuses": [403, 403],
        "service_results": ["owned", "owned"],
        "backend_effects": [],
    }
