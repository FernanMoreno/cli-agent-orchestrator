"""Lifecycle ordering for exact, terminal-owned Claude MCP children."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Barrier, BrokenBarrierError, Event, Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from cli_agent_orchestrator.backends.base import (
    TerminalCleanupOutcome,
    TerminalCleanupResult,
)
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider
from cli_agent_orchestrator.services import (
    cleanup_service,
    remote_terminal_service,
    session_service,
    terminal_service,
    work_terminal,
)
from cli_agent_orchestrator.utils import mcp_owner_stop

pytestmark = pytest.mark.offline_database


def _metadata(terminal_id="claude-one", *, provider="claude_code", **overrides):
    metadata = {
        "id": terminal_id,
        "provider": provider,
        "tmux_session": "cao-test",
        "tmux_window": terminal_id,
        "engine": None,
        "deferred_init_external_owner": False,
        "deferred_init_failure": None,
        "deferred_init_runtime_reclaimed": False,
        "live_working_directory": None,
    }
    metadata.update(overrides)
    return metadata


def _install_runtime_fakes(monkeypatch, events):
    class Backend:
        def stop_pipe_pane(self, session, window):
            events.append(("stop_pipe", window))

        def kill_window(self, session, window):
            events.append(("kill_window", window))
            return True

        def cleanup_terminal_exact(self, terminal_id, session, window, *, close=True):
            events.append(("exact", terminal_id, close))
            return TerminalCleanupResult(TerminalCleanupOutcome.ABSENT)

    backend = Backend()
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _terminal_id: None)
    monkeypatch.setattr(terminal_service, "get_backend", lambda: backend)
    monkeypatch.setattr(
        terminal_service,
        "get_herdr_inbox_service",
        lambda: SimpleNamespace(
            unregister_terminal=lambda terminal_id: events.append(("inbox", terminal_id))
        ),
    )
    monkeypatch.setattr(
        terminal_service.fifo_manager,
        "stop_reader",
        lambda terminal_id: events.append(("fifo", terminal_id)) or True,
    )
    monkeypatch.setattr(
        terminal_service.status_monitor,
        "clear_terminal",
        lambda terminal_id: events.append(("status", terminal_id)),
    )
    monkeypatch.setattr(
        terminal_service.worktree_service,
        "parse_worktree_path",
        lambda _path: None,
    )
    monkeypatch.setattr(
        terminal_service.provider_manager,
        "cleanup_provider",
        lambda terminal_id: events.append(("provider", terminal_id)) or True,
    )
    monkeypatch.setattr(
        terminal_service,
        "_remove_interaction_capability_files",
        lambda terminal_id: events.append(("capabilities", terminal_id)),
    )


def _install_session_fakes(monkeypatch, rows_by_session, metadata_by_id, events):
    class Backend:
        def session_exists_strict(self, session_name):
            events.append(("exists", session_name))
            return True

        def kill_session(self, session_name):
            events.append(("kill_session", session_name))
            return True

        def stop_pipe_pane(self, session, window):
            events.append(("stop_pipe", window))

        def kill_window(self, session, window):
            events.append(("kill_window", window))
            return True

        def cleanup_terminal_exact(self, terminal_id, session, window, *, close=True):
            events.append(("exact", terminal_id, close))
            return TerminalCleanupResult(TerminalCleanupOutcome.ABSENT)

    backend = Backend()
    monkeypatch.setattr(remote_terminal_service, "session_terminals", lambda _name: None)
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _terminal_id: None)
    monkeypatch.setattr(
        session_service,
        "list_terminals_by_session",
        lambda session_name: rows_by_session[session_name],
    )
    monkeypatch.setattr(session_service, "_collect_deferred_failures", lambda _rows: {})
    monkeypatch.setattr(session_service, "get_session_incarnation", lambda _name: "inc-current")
    monkeypatch.setattr(session_service, "get_backend", lambda: backend)
    monkeypatch.setattr(terminal_service, "get_backend", lambda: backend)
    monkeypatch.setattr(
        session_service,
        "DatabaseTarget",
        SimpleNamespace(from_settings=lambda **_kw: None),
    )
    monkeypatch.setattr(
        session_service,
        "terminal_dispatch_lock",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        work_terminal,
        "terminal_dispatch_lock",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        terminal_service,
        "ensure_terminal_is_not_work_owned",
        lambda _terminal_id: None,
    )
    monkeypatch.setattr(
        terminal_service,
        "capture_terminal_snapshot",
        lambda terminal_id: events.append(("capture", terminal_id))
        or metadata_by_id.get(terminal_id),
    )
    monkeypatch.setattr(
        terminal_service,
        "_delete_terminal_rows_with_sidecars",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        terminal_service.provider_manager,
        "retirement_fence",
        lambda _terminal_id: nullcontext(True),
    )
    monkeypatch.setattr(
        terminal_service,
        "delete_terminal_row",
        lambda terminal_id, _metadata, **_kwargs: events.append(("delete_row", terminal_id))
        or True,
    )
    monkeypatch.setattr(
        terminal_service.provider_manager,
        "cleanup_provider",
        lambda terminal_id: events.append(("provider", terminal_id)) or True,
    )
    monkeypatch.setattr(
        terminal_service.fifo_manager,
        "stop_reader",
        lambda terminal_id: events.append(("fifo", terminal_id)) or True,
    )
    monkeypatch.setattr(
        terminal_service.status_monitor,
        "clear_terminal",
        lambda terminal_id: events.append(("status", terminal_id)),
    )
    monkeypatch.setattr(
        terminal_service.worktree_service,
        "parse_worktree_path",
        lambda _path: None,
    )
    monkeypatch.setattr(
        terminal_service,
        "_remove_interaction_capability_files",
        lambda terminal_id: events.append(("capabilities", terminal_id)),
    )
    monkeypatch.setattr(terminal_service, "get_herdr_inbox_service", lambda: None)
    monkeypatch.setattr(session_service, "delete_terminals_by_ids", lambda _ids: None)
    monkeypatch.setattr(
        session_service,
        "dispatch_plugin_event",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(session_service, "clear_session_env", lambda _name: None)


def _claude_provider(events, terminal_id):
    provider = object.__new__(ClaudeCodeProvider)
    provider.exit_cli = Mock(
        side_effect=lambda: events.append(("exit_cli", terminal_id)) or "/exit"
    )
    return provider


def _install_exit_fakes(monkeypatch, events, providers, *, statuses=None, metadata_by_id=None):
    statuses = statuses or {}
    metadata_by_id = metadata_by_id or {}
    dispatch_locks = {}
    dispatch_locks_guard = Lock()

    class DispatchContext:
        def __init__(self, terminal_id):
            self.terminal_id = terminal_id
            with dispatch_locks_guard:
                self.lock = dispatch_locks.setdefault(terminal_id, Lock())
            self._entered = False

        def __enter__(self):
            self.lock.acquire()
            self._entered = True
            events.append(("dispatch_enter", self.terminal_id))
            return self

        def release(self):
            if self._entered:
                self._entered = False
                events.append(("dispatch_release", self.terminal_id))
                self.lock.release()

        def __exit__(self, _exc_type, _exc, _traceback):
            self.release()
            return False

    def get_provider(terminal_id):
        events.append(("provider_lookup", terminal_id))
        return providers.get(terminal_id)

    def get_status(terminal_id):
        events.append(("status", terminal_id))
        return statuses.get(terminal_id, TerminalStatus.IDLE)

    def get_metadata(terminal_id):
        events.append(("metadata", terminal_id))
        return metadata_by_id.get(terminal_id, _metadata(terminal_id))

    def prestop(terminal_id, deadline):
        events.append(("prestop", terminal_id, deadline))
        return SimpleNamespace(outcome="retired_normal")

    monkeypatch.setattr(remote_terminal_service, "placement", lambda _id: None)
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", get_provider)
    monkeypatch.setattr(terminal_service.status_monitor, "get_status", get_status)
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", get_metadata)
    monkeypatch.setattr(terminal_service, "get_backend", lambda: SimpleNamespace())
    monkeypatch.setattr(
        terminal_service,
        "ensure_terminal_is_not_work_owned",
        lambda terminal_id: events.append(("ensure_work", terminal_id)),
    )
    monkeypatch.setattr(
        work_terminal,
        "DatabaseTarget",
        SimpleNamespace(from_settings=lambda **_kw: None),
    )
    monkeypatch.setattr(
        work_terminal,
        "terminal_dispatch_lock",
        lambda _target, terminal_id: DispatchContext(terminal_id),
    )
    monkeypatch.setattr(mcp_owner_stop, "prestop_owned_mcp", Mock(side_effect=prestop))
    monkeypatch.setattr(
        terminal_service,
        "send_input",
        lambda terminal_id, command, **kwargs: events.append(
            ("send_input", terminal_id, command, kwargs)
        ),
    )
    monkeypatch.setattr(
        terminal_service,
        "send_special_key",
        lambda terminal_id, command: events.append(("send_special_key", terminal_id, command)),
    )
    return mcp_owner_stop.prestop_owned_mcp, terminal_service.ensure_terminal_is_not_work_owned


def _install_cleanup_fakes(monkeypatch, events, terminal_id="stale-claude"):
    old_query, inbox_query, idempotency_query = Mock(), Mock(), Mock()
    old_query.filter.return_value.all.return_value = [SimpleNamespace(id=terminal_id)]
    inbox_query.filter.return_value.delete.return_value = 0
    idempotency_query.filter.return_value.delete.return_value = 0
    db = Mock()
    db.query.side_effect = [old_query, inbox_query, idempotency_query]
    session_local = MagicMock()
    session_local.return_value.__enter__.return_value = db
    metadata = _metadata(terminal_id)
    single_prestop = Mock()
    batch_prestop = Mock()

    monkeypatch.setattr(cleanup_service, "SessionLocal", session_local)
    monkeypatch.setattr(
        cleanup_service,
        "DatabaseTarget",
        SimpleNamespace(from_settings=lambda **_kw: None),
    )
    monkeypatch.setattr(cleanup_service, "LOG_DIR", SimpleNamespace(exists=lambda: False))
    monkeypatch.setattr(
        cleanup_service,
        "TERMINAL_LOG_DIR",
        SimpleNamespace(exists=lambda: False),
    )
    monkeypatch.setattr(cleanup_service, "delete_old_handoff_results", lambda _cutoff: 0)
    monkeypatch.setattr(mcp_owner_stop, "prestop_owned_mcp", single_prestop)
    monkeypatch.setattr(mcp_owner_stop, "prestop_owned_mcp_many", batch_prestop)
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _id: None)
    monkeypatch.setattr(
        terminal_service,
        "should_retain_deferred_failure_tombstone",
        lambda _id: False,
    )
    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", lambda _id: None)
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda _id: metadata)
    monkeypatch.setattr(
        work_terminal,
        "terminal_dispatch_lock",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        cleanup_service.fifo_manager,
        "stop_reader",
        lambda terminal: events.append(("fifo", terminal)),
    )
    monkeypatch.setattr(
        cleanup_service.status_monitor,
        "clear_terminal",
        lambda terminal: events.append(("status", terminal)),
    )
    monkeypatch.setattr(
        cleanup_service.provider_manager,
        "cleanup_provider",
        lambda terminal: events.append(("provider", terminal)) or True,
    )
    monkeypatch.setattr(
        terminal_service,
        "delete_terminal_row",
        lambda terminal, _metadata, **_kwargs: events.append(("delete_row", terminal)) or True,
    )
    return single_prestop, batch_prestop


def test_post_exit_route_orders_prestop_before_exit_sender(monkeypatch):
    events = []
    provider = _claude_provider(events, "claude-exit")
    wrapped_sender = Mock()
    raw_sender = Mock(
        side_effect=lambda terminal_id, command, **kwargs: events.append(
            ("send_input", terminal_id, command, kwargs)
        )
    )
    wrapped_sender.__wrapped__ = raw_sender
    prestop, _ensure = _install_exit_fakes(
        monkeypatch,
        events,
        {"claude-exit": provider},
        metadata_by_id={"claude-exit": _metadata("claude-exit")},
    )
    monkeypatch.setattr(terminal_service, "send_input", wrapped_sender)

    def unsealed_prestop(terminal_id, deadline):
        events.append(("prestop", terminal_id, deadline))
        return SimpleNamespace(outcome="retired_unsealed")

    prestop.side_effect = unsealed_prestop
    from cli_agent_orchestrator.api import main as api_main

    response = asyncio.run(api_main.exit_terminal("claude-exit", _scopes=[]))

    assert response == {"success": True}
    names = [event[0] for event in events]
    assert names.index("dispatch_enter") < names.index("ensure_work")
    assert names.index("ensure_work") < names.index("prestop")
    assert names.index("prestop") < names.index("exit_cli") < names.index("send_input")
    assert names.index("send_input") < names.index("dispatch_release")
    prestop.assert_called_once()
    provider.exit_cli.assert_called_once_with()
    wrapped_sender.assert_not_called()
    raw_sender.assert_called_once_with("claude-exit", "/exit", attach_turn_receipt=False)


@pytest.mark.parametrize(
    "waiting_status",
    [TerminalStatus.WAITING_USER_ANSWER, TerminalStatus.WAITING_QUOTA],
)
def test_claude_wait_refusal_precedes_work_check_and_mcp_signal(monkeypatch, waiting_status):
    events = []
    provider = _claude_provider(events, "claude-waiting")
    prestop, _ensure = _install_exit_fakes(
        monkeypatch,
        events,
        {"claude-waiting": provider},
        statuses={"claude-waiting": waiting_status},
    )

    terminal_service.exit_terminal_cli("claude-waiting")

    prestop.assert_not_called()
    provider.exit_cli.assert_not_called()
    assert not any(event[0] in {"ensure_work", "metadata", "send_input"} for event in events)


def test_claude_work_owned_denial_precedes_mcp_signal(monkeypatch):
    events = []
    provider = _claude_provider(events, "claude-work-owned")
    prestop, _ensure = _install_exit_fakes(
        monkeypatch,
        events,
        {"claude-work-owned": provider},
        metadata_by_id={"claude-work-owned": _metadata("claude-work-owned")},
    )

    def deny_work_owned(terminal_id):
        events.append(("work_denied", terminal_id))
        raise terminal_service.WorkOwnedTerminalError("owned by Work")

    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", deny_work_owned)

    with pytest.raises(terminal_service.WorkOwnedTerminalError):
        terminal_service.exit_terminal_cli("claude-work-owned")

    prestop.assert_not_called()
    provider.exit_cli.assert_not_called()
    assert not any(event[0] in {"prestop", "send_input"} for event in events)


def test_managed_work_backend_binds_claude_target_before_mcp_prestop(monkeypatch):
    from cli_agent_orchestrator.backends.registry import get_backend, work_backend_scope
    from cli_agent_orchestrator.backends.work_backend import WorkBackendView

    events = []
    terminal_id = "claude-managed-work"
    provider = _claude_provider(events, terminal_id)
    prestop, _ensure = _install_exit_fakes(
        monkeypatch,
        events,
        {terminal_id: provider},
        metadata_by_id={terminal_id: _metadata(terminal_id)},
    )

    class GuardedBackend:
        def _before_work_effect(self, _restriction, effect):
            events.append(("work_backend_guard",))
            effect()

    backend = WorkBackendView(
        GuardedBackend(),
        restriction=object(),
        before_effect=lambda effect, target_id, session, window, _path=None: events.append(
            ("work_effect", effect, target_id, session, window)
        ),
        terminal_id=terminal_id,
        expected_target=("cao-test", terminal_id),
    )
    monkeypatch.setattr(terminal_service, "get_backend", get_backend)

    def send_input(target_id, command, **kwargs):
        assert get_backend() is backend
        assert ("cao-test", target_id) in backend._authorized_targets
        events.append(("send_input", target_id, command, kwargs))

    monkeypatch.setattr(terminal_service, "send_input", send_input)

    with work_backend_scope(backend):
        terminal_service.exit_terminal_cli(terminal_id)

    names = [event[0] for event in events]
    assert names.index("work_backend_guard") < names.index("prestop")
    assert names.index("work_effect") < names.index("prestop")
    assert names.index("prestop") < names.index("exit_cli") < names.index("send_input")
    assert not any(event[0] == "ensure_work" for event in events)
    prestop.assert_called_once()
    provider.exit_cli.assert_called_once_with()


def test_wait_published_during_work_lookup_refuses_mcp_signal(monkeypatch):
    events = []
    terminal_id = "claude-new-wait"
    statuses = {terminal_id: TerminalStatus.IDLE}
    provider = _claude_provider(events, terminal_id)
    prestop, _ = _install_exit_fakes(
        monkeypatch, events, {terminal_id: provider}, statuses=statuses
    )

    def work_lookup(_terminal_id):
        statuses[terminal_id] = TerminalStatus.WAITING_USER_ANSWER

    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", work_lookup)
    terminal_service.exit_terminal_cli(terminal_id)
    prestop.assert_not_called()
    provider.exit_cli.assert_not_called()


def test_exit_command_fallback_survives_mcp_prestop_exception(monkeypatch, caplog):
    events = []
    provider = _claude_provider(events, "claude-prestop-error")
    prestop, _ensure = _install_exit_fakes(
        monkeypatch,
        events,
        {"claude-prestop-error": provider},
        metadata_by_id={"claude-prestop-error": _metadata("claude-prestop-error")},
    )

    def fail_prestop(terminal_id, _deadline):
        events.append(("prestop_error", terminal_id))
        raise RuntimeError("private prestop exception payload")

    prestop.side_effect = fail_prestop
    terminal_service.exit_terminal_cli("claude-prestop-error")

    exit_command = events.index(("exit_cli", "claude-prestop-error"))
    send_command = events.index(
        ("send_input", "claude-prestop-error", "/exit", {"attach_turn_receipt": False})
    )
    assert events.index(("prestop_error", "claude-prestop-error")) < exit_command < send_command
    assert "private prestop exception payload" not in caplog.text


def test_remote_exit_keeps_early_route_without_local_prestop(monkeypatch):
    events = []
    provider = _claude_provider(events, "remote-terminal")
    prestop, _ensure = _install_exit_fakes(
        monkeypatch,
        events,
        {"remote-terminal": provider},
    )
    monkeypatch.setattr(
        remote_terminal_service,
        "placement",
        lambda _terminal_id: {"runtime_id": "remote-runtime"},
    )
    monkeypatch.setattr(
        remote_terminal_service,
        "call",
        lambda terminal_id, command: events.append(("remote_call", terminal_id, command)),
    )

    terminal_service.exit_terminal_cli("remote-terminal")

    prestop.assert_not_called()
    provider.exit_cli.assert_not_called()
    assert len(events) == 1
    assert events[0][:2] == ("remote_call", "remote-terminal")
    assert getattr(events[0][2], "value", events[0][2]) == "exit"


def test_prestopping_one_claude_exit_does_not_block_healthy_terminal(monkeypatch):
    events = []
    slow_id, healthy_id = "claude-slow", "claude-healthy"
    providers = {
        slow_id: _claude_provider(events, slow_id),
        healthy_id: _claude_provider(events, healthy_id),
    }
    prestop, _ensure = _install_exit_fakes(
        monkeypatch,
        events,
        providers,
        metadata_by_id={terminal_id: _metadata(terminal_id) for terminal_id in providers},
    )
    slow_started = Event()
    healthy_sent = Event()
    failures = []

    def hold_slow_prestop(terminal_id, deadline):
        events.append(("prestop", terminal_id, deadline))
        if terminal_id == slow_id:
            slow_started.set()
            if not healthy_sent.wait(timeout=3):
                failures.append("healthy Claude exit was blocked by another terminal")
                raise RuntimeError("unrelated terminal remained serialized")
        return SimpleNamespace(outcome="retired_normal")

    prestop.side_effect = hold_slow_prestop

    def send_input(terminal_id, command, **kwargs):
        events.append(("send_input", terminal_id, command, kwargs))
        if terminal_id == healthy_id:
            healthy_sent.set()

    monkeypatch.setattr(terminal_service, "send_input", send_input)

    with ThreadPoolExecutor(max_workers=2) as pool:
        slow_exit = pool.submit(terminal_service.exit_terminal_cli, slow_id)
        assert slow_started.wait(timeout=2)
        healthy_exit = pool.submit(terminal_service.exit_terminal_cli, healthy_id)
        slow_exit.result(timeout=4)
        healthy_exit.result(timeout=4)

    assert not failures
    assert healthy_sent.is_set()


def test_terminal_prestop_precedes_writer_and_reconcile_precedes_resource_cleanup(monkeypatch):
    events = []
    _install_runtime_fakes(monkeypatch, events)

    # An unsealed pre-stop result is deliberately not writer-stop proof. The
    # independent reconciliation result and backend kill remain authoritative.
    monkeypatch.setattr(
        mcp_owner_stop,
        "prestop_owned_mcp",
        lambda terminal_id, deadline: events.append(("prestop", terminal_id, deadline))
        or SimpleNamespace(outcome="retired_unsealed"),
    )
    monkeypatch.setattr(
        mcp_owner_stop,
        "reconcile_owned_mcp",
        lambda terminal_id: events.append(("reconcile", terminal_id)) or True,
    )

    assert terminal_service.dismantle_terminal_runtime("claude-one", _metadata()) is True

    names = [event[0] for event in events]
    assert names.index("prestop") < names.index("stop_pipe") < names.index("kill_window")
    assert names.index("kill_window") < names.index("reconcile") < names.index("inbox")
    assert names.index("reconcile") < names.index("fifo")
    assert names.index("reconcile") < names.index("status")
    assert names.index("reconcile") < names.index("provider")


def test_legacy_config_mode_unknown_prestop_keeps_normal_teardown(tmp_path, monkeypatch):
    # DrvFS may expose legacy config mode0644/0777 despite chmod. Such a
    # config cannot authorize a signal; UNKNOWN is not normal-exit evidence.
    # With no persisted owner registry, it must still allow legacy teardown.
    home = tmp_path / "legacy-home"
    home.mkdir(mode=0o755)
    temporary = home / "tmp"
    temporary.mkdir(mode=0o777)
    temporary.chmod(0o777)
    config = temporary / "claude-one.mcp.json"
    config.write_text(json.dumps({"mcpServers": {}}))
    config.chmod(0o644)
    monkeypatch.setattr(mcp_owner_stop, "CAO_HOME_DIR", home)
    events = []
    _install_runtime_fakes(monkeypatch, events)
    observed = []
    original_prestop = mcp_owner_stop.prestop_owned_mcp

    def observe_prestop(*args, **kwargs):
        result = original_prestop(*args, **kwargs)
        observed.append(result)
        return result

    monkeypatch.setattr(mcp_owner_stop, "prestop_owned_mcp", observe_prestop)
    assert terminal_service.dismantle_terminal_runtime("claude-one", _metadata()) is True
    assert len(observed) == 1
    assert observed[0].outcome is mcp_owner_stop.PrestopOutcome.UNKNOWN
    assert observed[0].signaled_count == observed[0].normal_count == 0
    names = [event[0] for event in events]
    assert names.index("kill_window") < names.index("fifo") < names.index("provider")


def test_unproven_backend_fallback_still_defers_before_mcp_reconcile(monkeypatch):
    events = []
    _install_runtime_fakes(monkeypatch, events)

    def cleanup_exact(terminal_id, _session, _window, *, close=True):
        events.append(("exact", terminal_id, close))
        return TerminalCleanupResult(TerminalCleanupOutcome.UNKNOWN, "private detail")

    backend = SimpleNamespace(
        stop_pipe_pane=lambda _session, window: events.append(("stop_pipe", window)),
        kill_window=lambda _session, window: events.append(("kill_window", window)) or False,
        cleanup_terminal_exact=cleanup_exact,
    )
    monkeypatch.setattr(terminal_service, "get_backend", lambda: backend)
    monkeypatch.setattr(
        mcp_owner_stop,
        "prestop_owned_mcp",
        lambda terminal_id, deadline: events.append(("prestop", terminal_id, deadline)),
    )
    reconcile = Mock(return_value=True)
    monkeypatch.setattr(mcp_owner_stop, "reconcile_owned_mcp", reconcile)

    assert terminal_service.dismantle_terminal_runtime("claude-one", _metadata()) is False

    assert [event[0] for event in events] == [
        "prestop",
        "stop_pipe",
        "kill_window",
        "exact",
    ]
    assert events[-1] == ("exact", "claude-one", False)
    reconcile.assert_not_called()


def test_prestop_exception_keeps_backend_fallback_but_unproven_reconcile_retains_row(
    monkeypatch, caplog
):
    events = []
    _install_runtime_fakes(monkeypatch, events)
    monkeypatch.setattr(
        mcp_owner_stop,
        "prestop_owned_mcp",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("private exception payload")),
    )
    monkeypatch.setattr(
        mcp_owner_stop,
        "reconcile_owned_mcp",
        lambda terminal_id: events.append(("reconcile", terminal_id)) or False,
    )
    delete_row = Mock(return_value=True)
    monkeypatch.setattr(
        terminal_service,
        "DatabaseTarget",
        SimpleNamespace(from_settings=lambda **_kw: None),
    )
    monkeypatch.setattr(
        work_terminal,
        "terminal_dispatch_lock",
        lambda *_args, **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", lambda _id: None)
    monkeypatch.setattr(
        terminal_service, "terminal_dispatch_lock", lambda *_args, **_kwargs: nullcontext()
    )
    monkeypatch.setattr(terminal_service, "capture_terminal_snapshot", lambda _id: _metadata())
    monkeypatch.setattr(terminal_service, "delete_terminal_row", delete_row)

    assert terminal_service.delete_terminal("claude-one") is False

    assert ("kill_window", "claude-one") in events
    assert events.index(("kill_window", "claude-one")) < events.index(("reconcile", "claude-one"))
    assert not any(
        event[0] in {"inbox", "fifo", "status", "provider", "capabilities"} for event in events
    )
    assert "private exception payload" not in caplog.text
    delete_row.assert_not_called()

    def failed_reconcile(terminal_id):
        events.append(("reconcile_exception", terminal_id))
        raise RuntimeError("private reconciliation payload")

    monkeypatch.setattr(mcp_owner_stop, "reconcile_owned_mcp", failed_reconcile)
    assert terminal_service.dismantle_terminal_runtime("claude-one", _metadata()) is False
    second_reconcile = events.index(("reconcile", "claude-one")) + 1
    assert events.index(("kill_window", "claude-one"), second_reconcile) < events.index(
        ("reconcile_exception", "claude-one")
    )
    assert not any(
        event[0] in {"inbox", "fifo", "status", "provider", "capabilities"} for event in events
    )
    assert "private reconciliation payload" not in caplog.text


def test_remote_and_tombstone_paths_do_not_prestop_children(monkeypatch):
    events = []
    _install_runtime_fakes(monkeypatch, events)
    monkeypatch.setattr(
        mcp_owner_stop,
        "prestop_owned_mcp",
        lambda *_args: (_ for _ in ()).throw(AssertionError("pre-stop called")),
    )
    monkeypatch.setattr(
        mcp_owner_stop,
        "prestop_owned_mcp_many",
        lambda *_args: (_ for _ in ()).throw(AssertionError("batch pre-stop called")),
    )
    monkeypatch.setattr(
        mcp_owner_stop,
        "reconcile_owned_mcp",
        lambda terminal_id: events.append(("reconcile", terminal_id)) or True,
    )
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _id: {"runtime_id": "remote"})
    monkeypatch.setattr(
        remote_terminal_service,
        "call",
        lambda *_args, **_kwargs: {"deleted": True},
    )
    assert terminal_service.dismantle_terminal_runtime("remote-one", _metadata()) is True
    assert events == []

    monkeypatch.setattr(remote_terminal_service, "placement", lambda _id: None)
    result = terminal_service.dismantle_terminal_runtime(
        "claude-old",
        _metadata("claude-old", deferred_init_external_owner=True),
    )
    assert result is True
    assert ("exact", "claude-old", True) in events
    assert ("reconcile", "claude-old") in events

    monkeypatch.setattr(
        remote_terminal_service,
        "session_terminals",
        lambda _name: [{"id": "remote-one"}],
    )
    monkeypatch.setattr(remote_terminal_service, "delete_remote", lambda _id: True)
    assert session_service.delete_session("cao-remote") == {
        "deleted": ["cao-remote"],
        "errors": [],
    }


def test_session_batches_local_claude_prestop_once_before_kill_with_one_deadline(
    monkeypatch, caplog
):
    events = []
    terminal_ids = ["claude-a", "claude-b", "codex-one", "claude-old"]
    rows = [
        {"id": terminal_id, "session_incarnation_id": "inc-current"} for terminal_id in terminal_ids
    ]
    metadata_by_id = {
        "claude-a": _metadata("claude-a"),
        "claude-b": _metadata("claude-b"),
        "codex-one": _metadata("codex-one", provider="codex"),
        "claude-old": _metadata("claude-old", deferred_init_external_owner=True),
    }
    failed_rows = [{"id": "claude-fail", "session_incarnation_id": "inc-current"}]
    metadata_by_id["claude-fail"] = _metadata("claude-fail")
    _install_session_fakes(
        monkeypatch,
        {"cao-batch": rows, "cao-failed": failed_rows},
        metadata_by_id,
        events,
    )
    monkeypatch.setattr(terminal_service.time, "monotonic", lambda: 100.0)

    def record_batch(ids, deadline):
        events.append(("prestop_many", list(ids), deadline))
        if ids == ["claude-fail"]:
            raise RuntimeError("private batch exception payload")
        return {}

    monkeypatch.setattr(
        mcp_owner_stop,
        "prestop_owned_mcp_many",
        record_batch,
    )
    monkeypatch.setattr(
        mcp_owner_stop,
        "reconcile_owned_mcp",
        lambda terminal_id: events.append(("reconcile", terminal_id)) or True,
    )

    assert session_service.delete_session("cao-batch") == {"deleted": ["cao-batch"], "errors": []}

    batches = [event for event in events if event[0] == "prestop_many"]
    assert batches == [
        (
            "prestop_many",
            ["claude-a", "claude-b"],
            100.0 + mcp_owner_stop.MCP_STOP_ALLOWANCE_SECONDS,
        )
    ]
    assert events.index(batches[0]) < events.index(("kill_session", "cao-batch"))
    assert events.index(("kill_session", "cao-batch")) < events.index(("reconcile", "claude-a"))
    assert events.index(("reconcile", "claude-a")) < events.index(("fifo", "claude-a"))
    assert ("exact", "claude-old", False) in events
    assert not any(event[0] == "prestop" for event in events)

    assert session_service.delete_session("cao-failed") == {
        "deleted": ["cao-failed"],
        "errors": [],
    }
    failed_batch = (
        "prestop_many",
        ["claude-fail"],
        100.0 + mcp_owner_stop.MCP_STOP_ALLOWANCE_SECONDS,
    )
    assert events.index(failed_batch) < events.index(("kill_session", "cao-failed"))
    assert "private batch exception payload" not in caplog.text


def test_distinct_sessions_can_enter_mcp_batch_stop_concurrently(monkeypatch):
    events = []
    sessions = {
        name: [{"id": terminal_id, "session_incarnation_id": "inc-current"}]
        for name, terminal_id in (("cao-left", "claude-left"), ("cao-right", "claude-right"))
    }
    metadata = {
        terminal_id: _metadata(terminal_id) for terminal_id in ("claude-left", "claude-right")
    }
    _install_session_fakes(monkeypatch, sessions, metadata, events)
    rendezvous = Barrier(2, timeout=3)
    failures = []
    events_lock = Lock()

    def enter_batch(ids, _deadline):
        with events_lock:
            events.append(("prestop_many", tuple(ids)))
        try:
            rendezvous.wait()
        except BrokenBarrierError:
            failures.append("the two sessions did not reach pre-stop concurrently")
            raise
        return {}

    monkeypatch.setattr(mcp_owner_stop, "prestop_owned_mcp_many", enter_batch)
    monkeypatch.setattr(mcp_owner_stop, "reconcile_owned_mcp", lambda _terminal_id: True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(session_service.delete_session, ("cao-left", "cao-right")))

    assert results == [
        {"deleted": ["cao-left"], "errors": []},
        {"deleted": ["cao-right"], "errors": []},
    ]
    assert not failures
    assert {event[1][0] for event in events if event[0] == "prestop_many"} == {
        "claude-left",
        "claude-right",
    }


@pytest.mark.parametrize("state", ["live", "unknown"])
def test_gc_retains_claude_resources_when_mcp_owner_is_live_or_unknown(monkeypatch, caplog, state):
    events = []
    single_prestop, batch_prestop = _install_cleanup_fakes(monkeypatch, events)
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(
        mcp_owner_stop,
        "reconcile_owned_mcp",
        lambda terminal_id: events.append((f"reconcile_{state}", terminal_id)) or False,
    )

    cleanup_service.cleanup_old_data()

    assert events == [(f"reconcile_{state}", "stale-claude")]
    assert "Retaining stale terminal stale-claude" in caplog.text
    assert "Deferred cleanup for 1 stale Claude terminals" in caplog.text
    single_prestop.assert_not_called()
    batch_prestop.assert_not_called()


def test_gc_retains_claude_resources_when_mcp_reconciliation_raises(monkeypatch, caplog):
    events = []
    single_prestop, batch_prestop = _install_cleanup_fakes(monkeypatch, events)
    caplog.set_level(logging.INFO)

    def reconcile_error(terminal_id):
        events.append(("reconcile_error", terminal_id))
        raise RuntimeError("private reconcile exception payload")

    monkeypatch.setattr(mcp_owner_stop, "reconcile_owned_mcp", reconcile_error)

    cleanup_service.cleanup_old_data()

    assert events == [("reconcile_error", "stale-claude")]
    assert "Deferred cleanup for 1 stale Claude terminals" in caplog.text
    assert "private reconcile exception payload" not in caplog.text
    single_prestop.assert_not_called()
    batch_prestop.assert_not_called()


def test_gc_keeps_legacy_claude_cleanup_when_no_mcp_records_are_reported(monkeypatch):
    events = []
    single_prestop, batch_prestop = _install_cleanup_fakes(monkeypatch, events)
    monkeypatch.setattr(
        mcp_owner_stop,
        "reconcile_owned_mcp",
        lambda terminal_id: events.append(("reconcile_no_records", terminal_id)) or True,
    )

    cleanup_service.cleanup_old_data()

    names = [event[0] for event in events]
    assert names == ["reconcile_no_records", "fifo", "status", "provider", "delete_row"]
    single_prestop.assert_not_called()
    batch_prestop.assert_not_called()
