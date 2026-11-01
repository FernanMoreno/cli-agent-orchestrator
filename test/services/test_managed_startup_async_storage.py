"""Offline checks: managed startup storage reads run off the event loop."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.services import agent_step, terminal_service, work_launch
from cli_agent_orchestrator.utils import terminal as terminal_utils

pytestmark = pytest.mark.offline_database


class _Recorder:
    def __init__(self) -> None:
        self.loop_thread = threading.get_ident()
        self.events: list[tuple[str, int, tuple]] = []
        self.lock = threading.Lock()

    def record(self, name: str, *args) -> None:
        with self.lock:
            self.events.append((name, threading.get_ident(), args))

    def names(self) -> list[str]:
        return [name for name, _, _ in self.events]

    def thread(self, name: str) -> int:
        return next(thread for event, thread, _ in self.events if event == name)

    def assert_off_loop(self, *names: str) -> None:
        for name in names:
            matching = [thread for event, thread, _ in self.events if event == name]
            assert matching, name
            assert all(thread != self.loop_thread for thread in matching), name


class _FakePort:
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder
        self.expected_target = ("cao-session", "agent-0123abcd")

    def revalidate(self) -> None:
        self.recorder.record("revalidate")

    @contextmanager
    def backend_scope(self):
        self.recorder.record("backend_scope")
        yield


def _binding(tools=("tool",)):
    return SimpleNamespace(
        contract=SimpleNamespace(
            provider="claude_code",
            model=SimpleNamespace(status="known", value="model-x"),
            resources=SimpleNamespace(checkout_root="/tmp/checkout"),
            permissions=SimpleNamespace(tools=frozenset(tools)),
        )
    )


def _install_fake_terminal(monkeypatch, recorder: _Recorder) -> None:
    async def create_terminal(**kwargs):
        recorder.record("create_terminal")
        return SimpleNamespace(id="0123abcd")

    def send_input(terminal_id, message, **kwargs):
        recorder.record("send_input", terminal_id, message, tuple(sorted(kwargs.items())))

    monkeypatch.setattr(terminal_service, "create_terminal", create_terminal)
    monkeypatch.setattr(terminal_service, "send_input", send_input)


@pytest.mark.asyncio
async def test_managed_agent_step_revalidates_and_sends_on_owned_workers(monkeypatch):
    recorder = _Recorder()
    _install_fake_terminal(monkeypatch, recorder)
    payload = SimpleNamespace(terminal_id="0123abcd", agent_profile="dev", message="hello")
    snapshot = SimpleNamespace(content=b"memory")

    await agent_step.run_managed_agent_step(_binding(), payload, snapshot, _FakePort(recorder))

    assert recorder.names() == [
        "revalidate",
        "backend_scope",
        "create_terminal",
        "revalidate",
        "send_input",
    ]
    recorder.assert_off_loop("revalidate", "send_input")
    assert recorder.thread("create_terminal") == recorder.loop_thread
    _, _, send_args = recorder.events[-1]
    assert send_args == (
        "0123abcd",
        "hello",
        (("frozen_memory", "memory"), ("task_delivery", True)),
    )


@pytest.mark.asyncio
async def test_managed_agent_step_revalidation_denial_prevents_input(monkeypatch):
    recorder = _Recorder()
    _install_fake_terminal(monkeypatch, recorder)
    port = _FakePort(recorder)
    calls = 0

    def revalidate():
        nonlocal calls
        calls += 1
        recorder.record("revalidate")
        if calls == 2:
            raise StorageAdmissionUnavailable()

    port.revalidate = revalidate
    payload = SimpleNamespace(terminal_id="0123abcd", agent_profile="dev", message="hello")

    with pytest.raises(StorageAdmissionUnavailable):
        await agent_step.run_managed_agent_step(
            _binding(), payload, SimpleNamespace(content=b""), port
        )
    assert "send_input" not in recorder.names()


@pytest.mark.asyncio
async def test_legacy_launch_sends_input_on_owned_worker(monkeypatch):
    recorder = _Recorder()
    _install_fake_terminal(monkeypatch, recorder)
    payload = SimpleNamespace(
        terminal_id="0123abcd",
        agent_profile="dev",
        session_name="cao-session",
        message="hello",
        allowed_tools=("tool",),
    )

    await work_launch._launch(
        _binding(), payload, SimpleNamespace(content=b"memory"), _FakePort(recorder)
    )

    assert recorder.names() == ["backend_scope", "create_terminal", "send_input"]
    recorder.assert_off_loop("send_input")


class _FakeBackend:
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def supports_event_inbox(self) -> bool:
        return False

    def get_history(self, session_name, window_name, strip_escapes=True):
        self.recorder.record("get_history", session_name, window_name)
        return "$ "


def _install_wait_fakes(monkeypatch, recorder: _Recorder, get_provider):
    from cli_agent_orchestrator.providers.manager import provider_manager
    from cli_agent_orchestrator.services.status_monitor import status_monitor

    monkeypatch.setattr(
        "cli_agent_orchestrator.backends.registry.get_backend", lambda: _FakeBackend(recorder)
    )
    monkeypatch.setattr(provider_manager, "get_provider", get_provider)

    def get_buffer(terminal_id):
        recorder.record("get_buffer")
        return "$ "

    monkeypatch.setattr(status_monitor, "get_buffer", get_buffer)


@pytest.mark.asyncio
async def test_wait_for_shell_resolves_provider_off_loop(monkeypatch):
    recorder = _Recorder()

    def get_provider(terminal_id):
        recorder.record("get_provider", terminal_id)
        return SimpleNamespace(session_name="s", window_name="w")

    _install_wait_fakes(monkeypatch, recorder, get_provider)

    assert await terminal_utils.wait_for_shell(
        "0123abcd", timeout=2.0, stable_duration=0.0, polling_interval=0.01
    )
    assert recorder.names()[0] == "get_provider"
    recorder.assert_off_loop("get_provider", "get_history")


@pytest.mark.asyncio
async def test_wait_for_shell_propagates_storage_denial(monkeypatch):
    recorder = _Recorder()

    def get_provider(terminal_id):
        recorder.record("get_provider", terminal_id)
        raise StorageAdmissionUnavailable()

    _install_wait_fakes(monkeypatch, recorder, get_provider)

    with pytest.raises(StorageAdmissionUnavailable):
        await terminal_utils.wait_for_shell("0123abcd", timeout=1.0, polling_interval=0.01)
    assert "get_buffer" not in recorder.names()


@pytest.mark.asyncio
async def test_wait_for_shell_ordinary_lookup_failure_falls_back(monkeypatch):
    recorder = _Recorder()

    def get_provider(terminal_id):
        recorder.record("get_provider", terminal_id)
        raise RuntimeError("lookup failed")

    _install_wait_fakes(monkeypatch, recorder, get_provider)

    assert await terminal_utils.wait_for_shell(
        "0123abcd", timeout=2.0, stable_duration=0.0, polling_interval=0.01
    )
    assert "get_buffer" in recorder.names()
    recorder.assert_off_loop("get_provider", "get_buffer")


class _Stop(Exception):
    pass


def _install_create_fakes(monkeypatch, recorder: _Recorder, **overrides):
    def record_call(name, result=None, raises=None):
        def call(*args, **kwargs):
            recorder.record(name, *args)
            if raises is not None:
                raise raises
            return result

        return call

    defaults = {
        "get_idempotency_record": record_call(
            "get_idempotency_record",
            SimpleNamespace(terminal_id="deadbeef", request_fingerprint="other"),
        ),
        "get_terminal": record_call("get_terminal", raises=ValueError("gone")),
        "delete_idempotency_key": record_call("delete_idempotency_key"),
        "get_max_terminals": record_call("get_max_terminals", raises=_Stop()),
        "count_runtime_allocated_terminals": record_call("count_runtime_allocated_terminals", 0),
        "get_terminal_metadata": record_call("get_terminal_metadata"),
    }
    defaults.update({name: record_call(name, *spec) for name, spec in overrides.items()})
    for name, function in defaults.items():
        monkeypatch.setattr(terminal_service, name, function)


@pytest.mark.asyncio
async def test_create_stale_idempotency_mapping_uses_workers(monkeypatch):
    recorder = _Recorder()
    _install_create_fakes(monkeypatch, recorder)

    with pytest.raises(_Stop):
        await terminal_service.create_terminal(
            provider="claude_code", agent_profile="dev", idempotency_key="key-1"
        )

    assert recorder.names() == [
        "get_idempotency_record",
        "get_terminal",
        "delete_idempotency_key",
        "get_max_terminals",
    ]
    delete = next(event for event in recorder.events if event[0] == "delete_idempotency_key")
    assert delete[2] == ("key-1", "deadbeef")
    recorder.assert_off_loop(*recorder.names())


@pytest.mark.asyncio
async def test_create_idempotency_conflict_reads_row_off_loop(monkeypatch):
    recorder = _Recorder()
    _install_create_fakes(monkeypatch, recorder, get_terminal=({"id": "deadbeef"},))

    with pytest.raises(terminal_service.IdempotencyKeyConflict):
        await terminal_service.create_terminal(
            provider="claude_code", agent_profile="dev", idempotency_key="key-1"
        )

    assert recorder.names() == ["get_idempotency_record", "get_terminal"]
    recorder.assert_off_loop(*recorder.names())


@pytest.mark.asyncio
async def test_create_terminal_cap_reads_off_loop(monkeypatch):
    recorder = _Recorder()
    _install_create_fakes(
        monkeypatch,
        recorder,
        get_max_terminals=(1,),
        count_runtime_allocated_terminals=(1,),
    )

    with pytest.raises(terminal_service.TerminalLimitError):
        await terminal_service.create_terminal(provider="claude_code", agent_profile="dev")

    assert recorder.names() == ["get_max_terminals", "count_runtime_allocated_terminals"]
    recorder.assert_off_loop(*recorder.names())


@pytest.mark.asyncio
async def test_create_managed_identity_metadata_check_off_loop(monkeypatch):
    from cli_agent_orchestrator.services.work_terminal import managed_terminal_identity

    recorder = _Recorder()
    _install_create_fakes(
        monkeypatch,
        recorder,
        get_max_terminals=(None,),
        get_terminal_metadata=({"id": "0123abcd"},),
    )

    def load_agent_profile(name):
        raise FileNotFoundError(name)

    monkeypatch.setattr(terminal_service, "load_agent_profile", load_agent_profile)

    with managed_terminal_identity("0123abcd"):
        with pytest.raises(ValueError, match="already in use"):
            await terminal_service.create_terminal(provider="claude_code", agent_profile="dev")

    assert recorder.names() == ["get_max_terminals", "get_terminal_metadata"]
    recorder.assert_off_loop(*recorder.names())
