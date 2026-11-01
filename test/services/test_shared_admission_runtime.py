"""Runtime composition of the shared delivery coordinator (T013, pure offline)."""

import asyncio
import importlib
import threading
from contextlib import contextmanager

import pytest

from cli_agent_orchestrator.clients.storage_admission import _current_storage_owner
from cli_agent_orchestrator.security.auth import _verified_principal
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

pytestmark = pytest.mark.offline_database


def principal():
    return _verified_principal("urn:caos:test", "operator", ["cao:admin"], "jwt")


def reader():
    return _verified_principal("urn:caos:test", "operator", ["cao:read"], "jwt")


class StrictRepository:
    """Any access proves the runtime touched the shared repository itself."""

    def __getattr__(self, name):
        raise AssertionError(f"runtime touched shared repository: {name}")


class Participant:
    def __init__(self, events, owner):
        self.events = events
        self.owner = owner
        self.required_at_bind = None

    def bind_admission(self, admission, *, clock, retirement):
        self.required_at_bind = self.owner.shared_required

    def notify_admission_changed(self, terminal_id=None):
        pass

    def start_accepting(self):
        pass

    def stop_accepting(self):
        self.events.append("participant.stop_accepting")

    async def drain(self, *, timeout_s):
        self.events.append("participant.drain")


class Monitor:
    def __init__(self, events):
        self.events = events
        self.owner, self._permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
        self.in_scope = False
        self.stopped = False

    @property
    def storage_control(self):
        return self._permit

    def set_on_change(self, callback):
        pass

    @contextmanager
    def authority_read(self):
        yield

    def control_call(self, callback):
        with self._permit.operation(clock=lambda: 1.0):
            self.in_scope = True
            try:
                return callback()
            finally:
                self.in_scope = False

    async def start(self):
        pass

    def stop_accepting(self):
        self.stopped = True

    async def stop(self):
        self.stopped = True

    def retire_storage(self):
        self.events.append("retire_storage")
        return True

    @property
    def retired(self):
        return self.stopped


class Coordinator:
    def __init__(self, owner, events):
        self._owner = owner
        self.events = events
        self.accepting = True
        self.drain_error = None
        self.drain_gate = None
        self.timeouts = []

    def prepare(self, lease):
        raise AssertionError("runtime must not prepare deliveries")

    def stop_accepting(self):
        self.accepting = False
        self.events.append("shared.stop_accepting")

    def drain(self, timeout_s):
        self.timeouts.append(timeout_s)
        self.events.append("shared.drain")
        if self.drain_gate is not None:
            assert self.drain_gate.wait(10)
        if self.drain_error is not None:
            raise self.drain_error


@pytest.fixture
def harness(monkeypatch):
    api = importlib.import_module("cli_agent_orchestrator.services.inbox_admission_runtime")
    storage = importlib.import_module("cli_agent_orchestrator.clients.storage_admission")
    events = []
    monitor = Monitor(events)
    repository = StrictRepository()
    calls = []
    state = {"gate": None, "entered": threading.Event(), "coordinator": None}

    def authorize(**kwargs):
        assert monitor.in_scope and _current_storage_owner() is monitor.owner._control_lease
        calls.append(kwargs)
        state["entered"].set()
        if state["gate"] is not None:
            assert state["gate"].wait(10)
        coordinator = Coordinator(kwargs["owner"], events)
        state["coordinator"] = coordinator
        return coordinator

    class Clean:
        clean = True

    with monkeypatch.context() as patch:
        patch.setattr(api, "authorize_shared_delivery", authorize)
        patch.setattr(storage, "retry_storage_closes", lambda: Clean())
        inbox = Participant(events, monitor.owner)
        runtime = api.InboxAdmissionRuntime(
            inbox,
            monitor=monitor,
            bootstrap=object(),
            clock=lambda: 1.0,
            drain_timeout_s=7,
            shared_repository=repository,
        )
        yield api, runtime, monitor, repository, calls, state, events, inbox
    assert api.authorize_shared_delivery is not authorize


def coordinator_of(state):
    return state["coordinator"]


@pytest.mark.asyncio
async def test_start_requires_shared_before_binding_participants(harness):
    _, runtime, monitor, *_rest, inbox = harness
    await runtime.start()
    try:
        assert inbox.required_at_bind is True
        assert monitor.owner.shared_required and not monitor.owner.shared_ready
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_administrator_checked_before_repository_without_fallback(harness):
    api, runtime, monitor, repository, calls, *_ = harness
    await runtime.start()
    try:
        with pytest.raises(PermissionError):
            await runtime.activate_shared(principal=reader())
        with pytest.raises(PermissionError):
            await runtime.activate_shared(principal=object())
        assert calls == [] and not monitor.owner.shared_ready
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_runtime_without_shared_repository_never_falls_back(monkeypatch):
    api = importlib.import_module("cli_agent_orchestrator.services.inbox_admission_runtime")
    monitor = Monitor([])
    called = []
    with monkeypatch.context() as patch:
        patch.setattr(api, "authorize_shared_delivery", lambda **k: called.append(k))
        runtime = api.InboxAdmissionRuntime(
            Participant([], monitor.owner), monitor=monitor, bootstrap=object()
        )
        assert not monitor.owner.shared_required
        with pytest.raises(api.AdmissionRuntimeUnavailable):
            await runtime.activate_shared(principal=principal())
    assert called == []


@pytest.mark.asyncio
async def test_activation_binds_inside_control_scope_and_is_idempotent(harness):
    _, runtime, monitor, repository, calls, state, *_ = harness
    await runtime.start()
    try:
        await runtime.activate_shared(principal=principal())
        assert len(calls) == 1
        call = calls[0]
        assert call["repository"] is repository and call["owner"] is monitor.owner
        assert call["control"] is monitor.storage_control and call["clock"] is runtime.clock
        assert call["retirement"]() is False and call["principal"] is not None
        assert monitor.owner.shared_ready and monitor.owner._shared_coordinator is coordinator_of(
            state
        )
        await runtime.activate_shared(principal=principal())
        assert len(calls) == 1
    finally:
        await runtime.stop()
    assert runtime.retired and calls[0]["retirement"]() is True


@pytest.mark.asyncio
async def test_cancelled_activation_waiter_retains_coordinator(harness):
    _, runtime, monitor, _repo, calls, state, events, _ = harness
    await runtime.start()
    state["gate"] = threading.Event()
    task = asyncio.create_task(runtime.activate_shared(principal=principal()))
    try:
        assert await asyncio.to_thread(state["entered"].wait, 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        state["gate"].set()
    await asyncio.to_thread(runtime._call.finished.wait, 10)
    assert runtime._shared is coordinator_of(state)
    assert monitor.owner._shared_coordinator is coordinator_of(state)
    await runtime.stop()
    assert events.index("shared.stop_accepting") < events.index("shared.drain")


@pytest.mark.asyncio
async def test_bind_failure_stops_accepting_but_keeps_reference(harness, monkeypatch):
    _, runtime, monitor, _repo, calls, state, events, _ = harness
    await runtime.start()

    def refuse(coordinator):
        raise RuntimeError("controlled-bind-refusal")

    with monkeypatch.context() as patch:
        patch.setattr(monitor.owner, "bind_shared_admission", refuse)
        with pytest.raises(RuntimeError, match="controlled-bind-refusal"):
            await runtime.activate_shared(principal=principal())
    coordinator = coordinator_of(state)
    assert runtime._shared is coordinator and not coordinator.accepting
    with pytest.raises(Exception):
        await runtime.activate_shared(principal=principal())
    assert len(calls) == 1
    await runtime.stop()
    assert "shared.drain" in events and runtime.retired


@pytest.mark.asyncio
async def test_stop_during_late_activation_joins_and_stops_worker(harness):
    _, runtime, monitor, _repo, calls, state, events, _ = harness
    await runtime.start()
    state["gate"] = threading.Event()
    activation = asyncio.create_task(runtime.activate_shared(principal=principal()))
    assert await asyncio.to_thread(state["entered"].wait, 10)
    stopping = asyncio.create_task(runtime.stop())
    await asyncio.sleep(0.05)
    assert not stopping.done() and "shared.drain" not in events
    state["gate"].set()
    with pytest.raises(Exception):
        await activation
    await stopping
    coordinator = coordinator_of(state)
    assert runtime._shared is coordinator and not coordinator.accepting
    assert events.index("shared.stop_accepting") < events.index("participant.drain")
    assert events.index("participant.drain") < events.index("shared.drain")
    assert events.index("shared.drain") < events.index("retire_storage")
    assert coordinator.timeouts == [7] and runtime.retired


@pytest.mark.asyncio
async def test_activation_after_close_is_denied_without_authorize(harness):
    _, runtime, _monitor, _repo, calls, *_ = harness
    await runtime.start()
    runtime.close_admission()
    with pytest.raises(Exception):
        await runtime.activate_shared(principal=principal())
    assert calls == []
    await runtime.stop()


@pytest.mark.asyncio
async def test_incomplete_shared_drain_keeps_runtime_unretired(harness):
    _, runtime, _monitor, _repo, _calls, state, events, _ = harness
    await runtime.start()
    await runtime.activate_shared(principal=principal())
    coordinator = coordinator_of(state)
    coordinator.drain_error = RuntimeError("controlled-incomplete")
    with pytest.raises(RuntimeError, match="controlled-incomplete"):
        await runtime.stop()
    assert not runtime.retired and runtime._shared is coordinator
    assert "retire_storage" not in events
    coordinator.drain_error = None
    await runtime.stop()
    assert runtime.retired and events.count("shared.drain") == 2


@pytest.mark.asyncio
async def test_cancelled_stop_keeps_physical_shared_drain(harness):
    _, runtime, _monitor, _repo, _calls, state, events, _ = harness
    await runtime.start()
    await runtime.activate_shared(principal=principal())
    coordinator = coordinator_of(state)
    coordinator.drain_gate = threading.Event()
    coordinator.drain_error = RuntimeError("controlled-incomplete")
    stopping = asyncio.create_task(runtime.stop())
    while "shared.drain" not in events:
        await asyncio.sleep(0.01)
    stopping.cancel()
    await asyncio.sleep(0.05)
    assert not runtime.retired and runtime._shared is coordinator
    coordinator.drain_gate.set()
    with pytest.raises((asyncio.CancelledError, RuntimeError)):
        await stopping
    assert not runtime.retired and "retire_storage" not in events
    coordinator.drain_gate = None
    coordinator.drain_error = None
    await runtime.stop()
    assert runtime.retired


def test_bootstrap_shared_repository_binds_private_main_target(monkeypatch):
    from types import SimpleNamespace

    from cli_agent_orchestrator.clients.postgres import DatabaseTarget
    from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap

    target = object.__new__(DatabaseTarget)
    captured = []
    instance = ["fa909c12-4645-4605-ab7c-9ac5f4c8125a"]

    class Binding:
        instance_id = "fa909c12-4645-4605-ab7c-9ac5f4c8125a"

    bootstrap = ResourceBootstrap(
        repository=SimpleNamespace(resolve_target=lambda: target),
        binding_supplier=lambda value: captured.append(value) or Binding(),
        instance_supplier=lambda: instance[0],
    )
    built = []
    admission = importlib.import_module("cli_agent_orchestrator.clients.admission_repository")

    class Fake:
        def __init__(self, value, *, binding_supplier):
            built.append((value, binding_supplier))

    original = admission.ResourceAdmissionRepository
    with monkeypatch.context() as patch:
        patch.setattr(admission, "ResourceAdmissionRepository", Fake)
        bootstrap.shared_repository()
    assert admission.ResourceAdmissionRepository is original
    ((value, supplier),) = built
    assert value is target and captured == []
    assert type(supplier()) is Binding and captured == [target]
    instance[0] = "395141ce-9910-43f6-b4c4-9fe21790309e"
    with pytest.raises(ValueError):
        supplier()


@pytest.mark.asyncio
async def test_shared_drain_incomplete_uses_runtime_failure_contract(harness):
    from cli_agent_orchestrator.services.shared_delivery_admission import (
        SharedDeliveryDrainIncomplete,
    )

    api, runtime, _monitor, _repo, _calls, state, events, _ = harness
    await runtime.start()
    await runtime.activate_shared(principal=principal())
    coordinator = coordinator_of(state)
    coordinator.drain_error = SharedDeliveryDrainIncomplete()
    with pytest.raises(api.AdmissionRuntimeUnavailable, match="shared delivery cleanup"):
        await runtime.stop()
    assert not runtime.retired and runtime._shared is coordinator
    assert "retire_storage" not in events
    coordinator.drain_error = None
    await runtime.stop()
    assert runtime.retired


@pytest.mark.asyncio
async def test_foreign_runtime_rejects_shutdown_before_participant_locks(harness):
    import os

    api, runtime, _monitor, _repo, _calls, _state, events, _ = harness
    assert runtime.monitor.owner.shared_required  # Constructor already closed data.
    runtime._pid = os.getpid() + 1
    with pytest.raises(api.AdmissionRuntimeUnavailable, match="another process"):
        runtime.close_admission()
    with pytest.raises(api.AdmissionRuntimeUnavailable, match="another process"):
        await runtime.stop()
    assert events == [] and not runtime._closing
    runtime._pid = os.getpid()
    await runtime.stop()
