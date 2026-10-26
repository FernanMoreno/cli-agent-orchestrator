"""Authorized bulk row retirement must order with deferred sidecar publication."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from unittest.mock import MagicMock, Mock

import pytest

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.services import (
    flow_service,
    remote_terminal_service,
    session_service,
    terminal_service,
)

KEY = "sidecar-owner"
SESSION = "cao-sidecar-session"
FAILURE = {
    "phase": "deferred_init",
    "kind": "initialization_error",
    "message": "controlled failure",
}


@pytest.fixture
def retirement(monkeypatch, tmp_path):
    db.create_terminal(
        KEY,
        SESSION,
        "worker",
        "mock_cli",
        "developer",
        session_incarnation_id="sidecar-incarnation",
        new_session_incarnation=True,
    )
    monkeypatch.setattr(terminal_service, "TERMINAL_LOG_DIR", tmp_path)
    manager = MagicMock()
    manager.cleanup_provider.return_value = True
    manager.retirement_fence.side_effect = lambda _key: nullcontext(True)
    monkeypatch.setattr(terminal_service, "provider_manager", manager)
    monkeypatch.setattr(flow_service, "provider_manager", manager)
    monkeypatch.setattr(terminal_service, "capture_terminal_snapshot", db.get_terminal_metadata)
    monkeypatch.setattr(terminal_service, "dismantle_terminal_runtime", lambda *_a, **_k: True)
    monkeypatch.setattr(terminal_service, "terminal_dispatch_lock", lambda *_a: nullcontext())
    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", lambda _key: None)
    monkeypatch.setattr(session_service, "terminal_dispatch_lock", lambda *_a: nullcontext())
    monkeypatch.setattr(remote_terminal_service, "session_terminals", lambda _session: None)
    backend = MagicMock()
    backend.session_exists_strict.return_value = False
    monkeypatch.setattr(session_service, "get_backend", lambda: backend)
    monkeypatch.setattr(flow_service, "get_backend", lambda: backend)
    monkeypatch.setattr(flow_service, "_verified_flow_terminal_rows", lambda *_a: [])
    monkeypatch.setattr(flow_service, "_is_terminal_busy", lambda _key: False)
    monkeypatch.setattr(flow_service, "should_retain_deferred_failure_tombstone", lambda *_a: False)
    monkeypatch.setattr(flow_service, "fifo_manager", Mock())
    monkeypatch.setattr(flow_service, "status_monitor", Mock())
    return backend


def publish(kind):
    if kind == "failure":
        assert terminal_service._publish_deferred_failure_fallback(KEY, FAILURE)
        return terminal_service._deferred_failure_fallback_path(KEY)
    assert terminal_service._publish_deferred_init_complete_fallback(KEY)
    return terminal_service._deferred_init_complete_fallback_path(KEY)


def invoke(path, retirement, monkeypatch):
    if path.startswith("flow"):
        retirement.session_exists.return_value = path == "flow_live"
        assert flow_service._recycle_flow_session("sidecar-flow", SESSION) is True
    else:
        if path == "session_sweep":
            monkeypatch.setattr(
                terminal_service,
                "delete_terminal_row",
                Mock(side_effect=RuntimeError("controlled initial-delete failure")),
            )
        assert session_service.delete_session(SESSION) == {"deleted": [SESSION], "errors": []}


@pytest.mark.parametrize("path", ["flow_live", "flow_missing", "session_sweep", "session_ordinary"])
@pytest.mark.parametrize("kind", ["failure", "complete"])
def test_bulk_retirement_removes_existing_fallback_only_after_row_deletion(
    retirement, monkeypatch, path, kind
):
    assert db.engine.dialect.name == "postgresql"
    sidecar = publish(kind)
    assert sidecar.is_file()
    invoke(path, retirement, monkeypatch)
    assert db.get_terminal_metadata(KEY) is None
    assert not sidecar.exists()


@pytest.mark.parametrize("path", ["flow_live", "flow_missing", "session_sweep"])
@pytest.mark.parametrize("kind", ["failure", "complete"])
@pytest.mark.parametrize("publisher_first", [False, True])
def test_bulk_and_publisher_order_both_ways_without_blocking_unrelated_terminal(
    retirement, monkeypatch, path, kind, publisher_first
):
    entered, release, registered = threading.Event(), threading.Event(), threading.Event()
    guard = terminal_service._deferred_sidecar_file_locks_guard
    write = terminal_service._write_deferred_sidecar
    db.create_terminal("independent-sidecar", "cao-other-session", "other", "mock_cli", "developer")
    sidecar = publish(kind)

    class Guard:
        def __enter__(self):
            return guard.__enter__()

        def __exit__(self, *args):
            result = guard.__exit__(*args)
            if threading.current_thread().name == "sidecar-contender":
                registered.set()
            return result

    monkeypatch.setattr(terminal_service, "_deferred_sidecar_file_locks_guard", Guard())

    def hold():
        entered.set()
        assert release.wait(timeout=10)

    def controlled_write(target, payload):
        if publisher_first and target == sidecar:
            hold()
        return write(target, payload)

    monkeypatch.setattr(terminal_service, "_write_deferred_sidecar", controlled_write)
    module = flow_service if path.startswith("flow") else session_service

    def controlled_delete(ids):
        if not publisher_first:
            hold()
        return db.delete_terminals_by_ids(ids)

    monkeypatch.setattr(module, "delete_terminals_by_ids", controlled_delete)

    def publish_key(key):
        if kind == "failure":
            return terminal_service._publish_deferred_failure_fallback(key, FAILURE)
        return terminal_service._publish_deferred_init_complete_fallback(key)

    def actor(role, publishing):
        threading.current_thread().name = role
        if publishing:
            return publish_key(KEY)
        return invoke(path, retirement, monkeypatch)

    def independent():
        threading.current_thread().name = "sidecar-independent"
        return publish_key("independent-sidecar")

    with ThreadPoolExecutor(max_workers=3) as workers:
        first = workers.submit(actor, "sidecar-holder", publisher_first)
        second = None
        try:
            assert entered.wait(timeout=5)
            second = workers.submit(actor, "sidecar-contender", not publisher_first)
            assert registered.wait(timeout=5)
            # Contenders now register at the outer stripe before they can
            # acquire the local ID lease. Keep proving both holder and waiter.
            file_entries = list(terminal_service._deferred_sidecar_file_locks.values())
            assert len(file_entries) == 1 and file_entries[0].refs == 2
            assert terminal_service._deferred_sidecar_locks[KEY].refs == 1
            assert not second.done()
            assert workers.submit(independent).result(timeout=5) is True
            assert db.get_terminal_metadata(KEY) is not None
        finally:
            release.set()
        first_result = first.result(timeout=10)
        assert second is not None
        second_result = second.result(timeout=10)
    assert (first_result if publisher_first else second_result) is publisher_first
    assert db.get_terminal_metadata(KEY) is None
    assert not sidecar.exists()
    assert db.get_terminal_metadata("independent-sidecar") is not None
    other = (
        terminal_service._deferred_failure_fallback_path("independent-sidecar")
        if kind == "failure"
        else terminal_service._deferred_init_complete_fallback_path("independent-sidecar")
    )
    assert other.is_file()
    assert terminal_service._deferred_sidecar_locks == {}


@pytest.mark.parametrize("after_commit", [False, True])
def test_bulk_failure_or_ambiguous_commit_preserves_both_files(retirement, after_commit):
    failure, complete = publish("failure"), publish("complete")
    before = failure.read_bytes(), complete.read_bytes()
    error = RuntimeError("controlled uncertain database result")

    def failing_delete(ids):
        if after_commit:
            db.delete_terminals_by_ids(ids)
        raise error

    with pytest.raises(RuntimeError) as raised:
        terminal_service._delete_terminal_rows_with_sidecars([KEY], delete_rows=failing_delete)
    assert raised.value is error
    assert (failure.read_bytes(), complete.read_bytes()) == before
    assert (db.get_terminal_metadata(KEY) is None) is after_commit
    assert terminal_service._deferred_sidecar_locks == {}


@pytest.mark.parametrize("kind", ["failure", "complete"])
def test_later_database_read_outage_preserves_evidence_instead_of_inventing_absence(
    retirement, monkeypatch, kind
):
    terminal_service._delete_terminal_rows_with_sidecars(
        [KEY], delete_rows=db.delete_terminals_by_ids
    )
    assert db.get_terminal_metadata(KEY) is None
    monkeypatch.setattr(
        terminal_service, "get_terminal_metadata", Mock(side_effect=OSError("database unavailable"))
    )
    # This is the pre-existing deliberate unknown-outcome policy, outside the
    # successful-read same-process ordering guarantee. It remains explicit.
    sidecar = publish(kind)
    assert sidecar.is_file()
    assert terminal_service._deferred_sidecar_locks == {}


@pytest.mark.parametrize("path", ["retained_flow", "deferred_session"])
def test_excluded_terminal_keeps_its_row_and_both_evidence_files(retirement, monkeypatch, path):
    sidecars = [publish("failure"), publish("complete")]
    before = [file.read_bytes() for file in sidecars]
    if path == "retained_flow":
        retirement.session_exists.return_value = False
        monkeypatch.setattr(
            flow_service, "should_retain_deferred_failure_tombstone", lambda *_a: True
        )
        assert flow_service._recycle_flow_session("retained-flow", SESSION) is True
    else:
        terminal_service.provider_manager.retirement_fence.side_effect = lambda _key: nullcontext(
            False
        )
        result = session_service.delete_session(SESSION)
        assert result["deleted"] == []
        assert result["errors"]
    assert db.get_terminal_metadata(KEY) is not None
    assert [file.read_bytes() for file in sidecars] == before
    assert terminal_service._deferred_sidecar_locks == {}
