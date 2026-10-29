"""Focused FIFO generation controls for disconnected turn cancellation."""

import os
import queue
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import fifo_reader as fr
from cli_agent_orchestrator.services import status_monitor as status_module
from cli_agent_orchestrator.services import terminal_service as terminal_module
from cli_agent_orchestrator.services.fifo_reader import FifoManager
from cli_agent_orchestrator.services.status_monitor import StatusMonitor

pytestmark = [
    pytest.mark.skipif(
        not hasattr(os, "mkfifo"), reason="FIFOs require a POSIX platform (os.mkfifo)"
    ),
    pytest.mark.offline_database,
]


def _manager(tmp_path, monkeypatch, published):
    monkeypatch.setattr(fr, "FIFO_DIR", tmp_path)
    monkeypatch.setattr(fr.bus, "publish", lambda topic, data: published.put((topic, dict(data))))
    real_open = fr._open_fifo
    read_opened = threading.Event()

    def observed_open(path, flags):
        fd = real_open(path, flags)
        if flags & os.O_ACCMODE == os.O_RDONLY:
            read_opened.set()
        return fd

    monkeypatch.setattr(fr, "_open_fifo", observed_open)
    return FifoManager(), read_opened


def _event_with_payload(published, expected):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        topic, payload = published.get(timeout=max(0.01, deadline - time.monotonic()))
        if payload.get("data") == expected:
            return topic, payload
    raise AssertionError(f"FIFO did not publish {expected!r}")


def _write_fifo(path, payload):
    writer = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
    try:
        os.write(writer, payload)
    finally:
        os.close(writer)


def test_real_fifo_queued_old_capture_is_rejected_after_restart(tmp_path, monkeypatch):
    published = queue.Queue()
    manager, read_opened = _manager(tmp_path, monkeypatch, published)
    monkeypatch.setattr(fr, "fifo_manager", manager)
    terminal_id = "fifo-cancel"
    path = tmp_path / f"{terminal_id}.fifo"
    provider = MagicMock()
    provider.supports_screen_detection = False
    provider.get_status.return_value = TerminalStatus.UNKNOWN
    monitor = StatusMonitor()

    try:
        with patch.object(status_module, "provider_manager") as providers:
            providers.get_provider.return_value = provider
            manager.create_reader(terminal_id)
            assert read_opened.wait(timeout=2)
            old_identity = manager.current_capture_identity(terminal_id)
            assert isinstance(old_identity, str) and len(old_identity) == 32

            _write_fifo(path, b"old queued output")
            _topic, old_payload = _event_with_payload(published, "old queued output")
            assert old_payload.get("capture_identity") is None

            replacement = manager.prepare_reader_restart(terminal_id)
            assert isinstance(replacement, str) and replacement != old_identity
            monitor.reset_buffer(terminal_id, provider=provider, capture_identity=replacement)
            read_opened.clear()
            assert manager.restart_reader(terminal_id, replacement) == replacement
            assert read_opened.wait(timeout=2)

            # The old event was already published to the shared queue before reset.
            # It must be rejected before any provider evidence or monitor buffer write.
            monitor._process_chunk(
                terminal_id,
                old_payload["data"],
                capture_identity=old_payload.get("capture_identity"),
            )
            monitor._process_chunk(terminal_id, "legacy untagged output")
            provider.record_status_chunk.assert_not_called()
            assert monitor.get_buffer(terminal_id) == ""

            # A failed attach can have written to the newly created pipe before
            # its caller reports failure. The next cancel attempt must rotate
            # the reader identity and discard this already queued packet too.
            _write_fifo(path, b"failed attach output")
            _topic, failed_attempt_payload = _event_with_payload(published, "failed attach output")
            assert failed_attempt_payload["capture_identity"] == replacement
            retry_identity = manager.prepare_reader_restart(terminal_id)
            assert retry_identity != replacement
            monitor.reset_buffer(terminal_id, provider=provider, capture_identity=retry_identity)
            read_opened.clear()
            assert manager.restart_reader(terminal_id, retry_identity) == retry_identity
            assert read_opened.wait(timeout=2)
            monitor._process_chunk(
                terminal_id,
                failed_attempt_payload["data"],
                capture_identity=failed_attempt_payload["capture_identity"],
            )
            provider.record_status_chunk.assert_not_called()
            assert monitor.get_buffer(terminal_id) == ""

            _write_fifo(path, b"new generation output")
            _topic, new_payload = _event_with_payload(published, "new generation output")
            assert new_payload["capture_identity"] == retry_identity
            monitor._process_chunk(
                terminal_id,
                new_payload["data"],
                capture_identity=new_payload["capture_identity"],
            )
            assert monitor.get_buffer(terminal_id) == "new generation output"
            provider.record_status_chunk.assert_called_once()
            assert manager.activate_reader_restart(terminal_id, retry_identity)
    finally:
        manager.stop_all_readers()
        manager.stop_watchdog()
        monitor.clear_terminal(terminal_id)


def test_failed_drain_keeps_restart_nonce_and_watchdog_callbacks_for_retry(tmp_path, monkeypatch):
    published = queue.Queue()
    manager, read_opened = _manager(tmp_path, monkeypatch, published)
    terminal_id = "fifo-retry"
    probe = lambda: "pane snapshot"
    rearm = lambda: None
    real_stop = manager.stop_reader
    attempts = 0

    def fail_once(candidate, *, preserve_restart=False):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return False
        return real_stop(candidate, preserve_restart=preserve_restart)

    try:
        manager.create_reader(terminal_id, pane_probe=probe, rearm=rearm)
        assert read_opened.wait(timeout=2)
        first_nonce = manager.prepare_reader_restart(terminal_id)
        assert first_nonce is not None

        monkeypatch.setattr(manager, "stop_reader", fail_once)
        assert manager.restart_reader(terminal_id, first_nonce) is None

        # A fresh cancel attempt gets a fresh fence while retaining the exact owned
        # callbacks and old reader retirement authority.
        retry_nonce = manager.prepare_reader_restart(terminal_id)
        assert retry_nonce is not None and retry_nonce != first_nonce
        assert manager.restart_reader(terminal_id, retry_nonce) == retry_nonce
        assert manager._pane_probe[terminal_id] is probe
        assert manager._rearm[terminal_id] is rearm
        assert manager.current_capture_identity(terminal_id) == retry_nonce
        assert manager.activate_reader_restart(terminal_id, retry_nonce)
    finally:
        monkeypatch.setattr(manager, "stop_reader", real_stop)
        manager.stop_all_readers()
        manager.stop_watchdog()


def test_late_watchdog_replay_cannot_borrow_replacement_identity(tmp_path, monkeypatch):
    published = queue.Queue()
    manager, read_opened = _manager(tmp_path, monkeypatch, published)
    terminal_id = "fifo-watchdog"
    entered, release = threading.Event(), threading.Event()
    errors = []
    worker = None

    def late_rearm():
        entered.set()
        assert release.wait(10)

    def replay():
        try:
            manager._rearm_stalled_pipe(
                terminal_id,
                "old pane snapshot",
                late_rearm,
                cold_start=False,
                capture_identity=old_identity,
            )
        except BaseException as error:
            errors.append(error)

    try:
        manager.create_reader(terminal_id, pane_probe=lambda: "pane", rearm=lambda: None)
        assert read_opened.wait(timeout=2)
        old_identity = manager.current_capture_identity(terminal_id)
        worker = threading.Thread(target=replay)
        worker.start()
        assert entered.wait(10)
        replacement = manager.prepare_reader_restart(terminal_id)
        assert replacement is not None and replacement != old_identity
        assert manager.restart_reader(terminal_id, replacement) == replacement
        release.set()
        worker.join(10)
        assert not worker.is_alive() and not errors
        assert published.empty()
        assert manager.current_capture_identity(terminal_id) == replacement
    finally:
        release.set()
        if worker is not None:
            worker.join(10)
        manager.stop_all_readers()
        manager.stop_watchdog()


def test_retired_capture_tag_is_rejected_after_terminal_monitor_cleanup(tmp_path, monkeypatch):
    published = queue.Queue()
    manager, read_opened = _manager(tmp_path, monkeypatch, published)
    monkeypatch.setattr(fr, "fifo_manager", manager)
    terminal_id = "fifo-deleted"
    monitor = StatusMonitor()
    provider = MagicMock()
    try:
        with patch.object(status_module, "provider_manager") as providers:
            providers.get_provider.return_value = provider
            manager.create_reader(terminal_id)
            assert read_opened.wait(timeout=2)
            old_identity = manager.current_capture_identity(terminal_id)
            assert old_identity is not None

            monitor.reset_buffer(terminal_id, provider=provider, capture_identity=old_identity)
            monitor.clear_terminal(terminal_id)
            assert manager.stop_reader(terminal_id)

            # Clearing the monitor drops only the fence state. The manager's
            # current-reader check still rejects a queued tagged event from the
            # retired reader without retaining a per-terminal history map.
            monitor._process_chunk(
                terminal_id, "late retired output", capture_identity=old_identity
            )
            providers.get_provider.assert_not_called()
            provider.record_status_chunk.assert_not_called()
            assert monitor.get_buffer(terminal_id) == ""
    finally:
        manager.stop_all_readers()
        manager.stop_watchdog()
        monitor.clear_terminal(terminal_id)


def test_reset_buffer_invalidates_an_inflight_screen_detection(monkeypatch):
    monitor = StatusMonitor()
    provider = MagicMock()
    entered, release = threading.Event(), threading.Event()
    errors = []

    def screen(*args):
        entered.set()
        assert release.wait(10)
        return TerminalStatus.IDLE, "old ready screen"

    monkeypatch.setattr(monitor, "_detect_screen_snapshot", screen)
    observer = MagicMock()
    monkeypatch.setattr(monitor, "_observe_native_interaction", observer)

    def detection():
        try:
            monitor._on_screen_quiescent("epoch-race", provider)
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=detection)
    worker.start()
    try:
        assert entered.wait(10)
        monitor.reset_buffer("epoch-race", provider=provider)
        with monitor._lock:
            monitor._last_status["epoch-race"] = TerminalStatus.PROCESSING
        release.set()
        worker.join(10)
        assert not worker.is_alive() and not errors
        with monitor._lock:
            assert monitor._last_status["epoch-race"] == TerminalStatus.PROCESSING
        observer.assert_not_called()
        provider.notify_status_buffer_reset.assert_called_once_with(1)
    finally:
        release.set()
        worker.join(10)
        monitor.clear_terminal("epoch-race")


def test_restore_reconstructs_the_fenced_fifo_and_seeds_with_its_nonce(tmp_path, monkeypatch):
    published = queue.Queue()
    manager, read_opened = _manager(tmp_path, monkeypatch, published)
    monkeypatch.setattr(fr, "fifo_manager", manager)
    monkeypatch.setattr(terminal_module, "fifo_manager", manager)
    monkeypatch.setattr(terminal_module, "FIFO_DIR", tmp_path)
    terminal_id = "fifo-restore"
    provider = MagicMock()
    provider.supports_screen_detection = False
    provider.get_status.return_value = TerminalStatus.UNKNOWN
    monitor = StatusMonitor()
    metadata = {
        "id": terminal_id,
        "tmux_session": "session-restore",
        "tmux_window": "window-restore",
        "provider": "codex",
    }
    events = []
    original_reset = monitor.reset_buffer
    original_process = monitor._process_chunk
    processed = []
    reset_identities = []
    reset_done = threading.Event()
    missing = object()

    def reset_buffer(candidate, provider=None, *, capture_identity=missing):
        reset_identities.append(capture_identity)
        events.append("reset")
        if capture_identity is missing:
            return original_reset(candidate, provider=provider)
        result = original_reset(candidate, provider=provider, capture_identity=capture_identity)
        reset_done.set()
        return result

    def process_chunk(candidate, chunk, *, capture_identity=missing):
        processed.append((chunk, capture_identity))
        if capture_identity is missing:
            return original_process(candidate, chunk)
        return original_process(candidate, chunk, capture_identity=capture_identity)

    monitor.reset_buffer = reset_buffer
    monitor._process_chunk = process_chunk
    backend = MagicMock()
    backend.supports_event_inbox.return_value = False
    backend.terminal_observation_target_matches.return_value = True
    backend.get_history.return_value = "fresh pane seed"

    def pipe_pane(_session, _window, fifo_path):
        events.append("pipe")
        assert read_opened.wait(timeout=2)
        _write_fifo(Path(fifo_path), b"fifo output")

    backend.pipe_pane.side_effect = pipe_pane

    # A cancellation fence survives while the server rebuilds its local reader.
    monitor.reset_buffer(terminal_id, provider=provider, capture_identity="old-capture-identity")
    reset_identities.clear()
    events.clear()
    try:
        with (
            patch.object(terminal_module, "get_terminal_metadata", return_value=metadata),
            patch.object(terminal_module.DatabaseTarget, "from_settings", return_value=object()),
            patch.object(terminal_module, "session_lifecycle_lock", lambda _name: nullcontext()),
            patch.object(
                terminal_module,
                "terminal_dispatch_lock",
                lambda *_args: nullcontext(),
            ),
            patch.object(terminal_module, "ensure_terminal_is_not_work_owned"),
            patch.object(terminal_module, "_still_owns_incarnation", return_value=True),
            patch.object(terminal_module, "get_backend", return_value=backend),
            patch.object(terminal_module.provider_manager, "get_provider", return_value=provider),
            patch.object(terminal_module, "status_monitor", monitor),
            patch.object(status_module, "provider_manager") as providers,
            patch.object(
                status_module, "get_server_settings", return_value={"state_buffer_max": 1024}
            ),
        ):
            providers.get_provider.return_value = provider
            from cli_agent_orchestrator.services import remote_terminal_service as remote

            with patch.object(remote, "placement", return_value=False):
                assert terminal_module.restore_terminal_observation(terminal_id)

            new_identity = manager.current_capture_identity(terminal_id)
            assert isinstance(new_identity, str) and len(new_identity) == 32
            assert reset_identities == [new_identity]
            assert events.index("reset") < events.index("pipe")
            seed_identity = next(
                identity for chunk, identity in processed if chunk.startswith("fresh pane seed")
            )
            assert seed_identity == new_identity

            _topic, payload = _event_with_payload(published, "fifo output")
            assert payload["capture_identity"] == new_identity
            monitor._process_chunk(
                terminal_id,
                payload["data"],
                capture_identity=payload["capture_identity"],
            )
            assert monitor.get_buffer(terminal_id).startswith("fresh pane seed")
            assert "fifo output" in monitor.get_buffer(terminal_id)
            assert reset_done.is_set()
    finally:
        manager.stop_all_readers()
        manager.stop_watchdog()
        monitor.clear_terminal(terminal_id)


@pytest.mark.asyncio
async def test_cancel_retires_old_pane_before_creating_replacement_capture(tmp_path, monkeypatch):
    from cli_agent_orchestrator.services import turn_recovery_service as recovery

    published = queue.Queue()
    manager, read_opened = _manager(tmp_path, monkeypatch, published)
    monkeypatch.setattr(fr, "fifo_manager", manager)
    monkeypatch.setattr(recovery, "fifo_manager", manager)
    monkeypatch.setattr(recovery, "FIFO_DIR", tmp_path)
    terminal_id = "fifo-pane-order"
    path = tmp_path / f"{terminal_id}.fifo"
    reset_done = threading.Event()
    events = []

    def old_watchdog_rearm():
        events.append(("old-rearm", reset_done.is_set()))
        if not reset_done.is_set():
            _write_fifo(path, b"OLD")

    manager.create_reader(terminal_id, pane_probe=lambda: "old pane", rearm=old_watchdog_rearm)
    assert read_opened.wait(timeout=2)
    real_restart = manager.restart_reader

    def restart_with_late_rearm(candidate, capture_identity):
        read_opened.clear()
        result = real_restart(candidate, capture_identity)
        if result is not None:
            assert read_opened.wait(timeout=2)
            events.append(("new-reader", reset_done.is_set()))
            # Deterministically model a watchdog callback that passed its old
            # probe just before the cancellation began.
            old_watchdog_rearm()
        return result

    monkeypatch.setattr(manager, "restart_reader", restart_with_late_rearm)
    provider = MagicMock()
    provider.initialize = AsyncMock(return_value=True)
    metadata = {
        "provider": "codex",
        "tmux_session": "session-order",
        "tmux_window": "window-order",
        "agent_profile": "test",
    }
    monitor = StatusMonitor()
    backend = MagicMock()
    backend.supports_event_inbox.return_value = False

    def reset_window(*_args, **_kwargs):
        events.append(("window-reset", True))
        reset_done.set()

    def pipe_pane(_session, _window, fifo_path):
        events.append(("pipe-attach", reset_done.is_set()))
        assert read_opened.wait(timeout=2)
        _write_fifo(Path(fifo_path), b"NEW")

    backend.reset_window.side_effect = reset_window
    backend.pipe_pane.side_effect = pipe_pane
    try:
        with (
            patch.object(recovery, "ensure_terminal_is_not_work_owned"),
            patch.object(recovery, "get_terminal_metadata", return_value=metadata),
            patch.object(
                recovery,
                "begin_terminal_turn_cancellation",
                return_value=True,
            ),
            patch.object(
                recovery,
                "finish_terminal_turn_cancellation",
                return_value=True,
            ),
            patch.object(recovery, "get_backend", return_value=backend),
            patch.object(recovery.provider_manager, "get_provider", return_value=provider),
            patch.object(
                recovery,
                "transition_native_child",
                side_effect=lambda *_args: events.append(("transition", reset_done.is_set())),
            ),
            patch.object(
                recovery,
                "cancelled_turn_initialization",
                lambda *_args: nullcontext(SimpleNamespace(readiness_observed=threading.Event())),
            ),
            patch.object(recovery, "get_turn", return_value={"state": "cancelled"}),
            patch.object(recovery, "status_monitor", monitor),
            patch.object(status_module, "provider_manager") as providers,
        ):
            providers.get_provider.return_value = provider
            result = await recovery._cancel_locked(terminal_id, "1" * 32)

        assert result["state"] == "cancelled"
        assert events.index(("window-reset", True)) < next(
            i for i, event in enumerate(events) if event[0] == "new-reader"
        )
        transition_index = next(i for i, event in enumerate(events) if event[0] == "transition")
        new_reader_index = next(i for i, event in enumerate(events) if event[0] == "new-reader")
        assert transition_index < new_reader_index
        assert events[-1] == ("pipe-attach", True)

        delivered = []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not any(
            "NEW" in event["data"] for event in delivered
        ):
            _topic, payload = published.get(timeout=max(0.01, deadline - time.monotonic()))
            delivered.append(payload)
        assert delivered and any("NEW" in event["data"] for event in delivered)
        assert all("OLD" not in event["data"] for event in delivered)
    finally:
        manager.stop_all_readers()
        manager.stop_watchdog()
        monitor.clear_terminal(terminal_id)
