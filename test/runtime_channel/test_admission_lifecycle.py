"""The Bridge participant must join its real reader and physical handlers."""

import asyncio
import importlib
import signal
from test.services.test_admission_monitor import fixture
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from cli_agent_orchestrator.runtime_channel.bridge import Bridge
from cli_agent_orchestrator.services.inbox_admission_runtime import (
    AdmissionRuntimeUnavailable,
    InboxAdmissionRuntime,
)
from cli_agent_orchestrator.services.work_blocking import run_owned_blocking

pytestmark = pytest.mark.offline_database


def component():
    api = importlib.import_module("cli_agent_orchestrator.runtime_channel.admission")
    bridge = Bridge("ws://isolated", "resource-node", "test", store=SimpleNamespace())
    f = fixture()
    port = api.BridgeAdmissionParticipant(bridge)
    value = InboxAdmissionRuntime(
        port,
        monitor=f.monitor,
        bootstrap=f.bootstrap,
        clock=lambda: f.clock[0],
        drain_timeout_s=10,
    )
    return bridge, f, port, value


@pytest.mark.asyncio
async def test_participant_binds_same_owner_clock_and_forbids_live_rebinding():
    bridge, f, port, value = component()
    await value.start()
    try:
        assert bridge._resource_admission is f.monitor.owner
        assert bridge._resource_clock is value.clock
        with pytest.raises(RuntimeError):
            port.bind_admission(f.monitor.owner, clock=value.clock, retirement=lambda: True)
        replacement = type(port)(bridge)
        with pytest.raises(RuntimeError):
            replacement.bind_admission(f.monitor.owner, clock=value.clock, retirement=lambda: True)
    finally:
        await value.stop()
    assert value.retired and bridge._stop.is_set()


@pytest.mark.asyncio
async def test_cancelled_drain_waits_for_owned_physical_handler():
    bridge, f, port, value = component()
    await value.start()
    # This is controlled capacity, not a host/hierarchy acceptance claim.
    from test.services.test_admission_monitor import warm

    warm(f)
    lease = f.monitor.owner.try_reserve("terminal", now=f.clock[0]).reservation
    assert lease is not None
    entered, release = Event(), Event()

    def physical():
        assert lease.start()
        entered.set()
        try:
            assert release.wait(15)
        finally:
            lease.finish()

    worker = asyncio.create_task(run_owned_blocking(physical))
    bridge._tasks.add(worker)
    worker.add_done_callback(bridge._tasks.discard)
    drain = None
    try:
        assert await asyncio.to_thread(entered.wait, 15)
        value.close_admission()
        drain = asyncio.create_task(port.drain(timeout_s=10))
        await asyncio.sleep(0)
        drain.cancel()
        await asyncio.sleep(0)
        assert not drain.done() and lease.state == "started"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(drain), 15)
        assert worker.done() and lease.state == "released"
    finally:
        release.set()
        await asyncio.gather(
            worker, *([drain] if drain is not None else []), return_exceptions=True
        )
        await value.stop()
    assert value.retired


@pytest.mark.asyncio
async def test_drain_waits_for_reader_before_empty_handler_snapshot(monkeypatch):
    bridge, _, port, value = component()
    await value.start()
    entered, release = asyncio.Event(), asyncio.Event()

    async def reader():
        entered.set()
        await bridge._stop.wait()
        await release.wait()

    monkeypatch.setattr(bridge, "_run", reader)
    reading = asyncio.create_task(bridge.run())
    drain = None
    try:
        await asyncio.wait_for(entered.wait(), 10)
        value.close_admission()
        drain = asyncio.create_task(port.drain(timeout_s=10))
        await asyncio.sleep(0)
        assert not drain.done()
        release.set()
        await asyncio.wait_for(asyncio.gather(reading, drain), 10)
    finally:
        release.set()
        await asyncio.gather(
            reading, *([drain] if drain is not None else []), return_exceptions=True
        )
        await value.stop()


@pytest.mark.asyncio
async def test_real_control_listener_preserves_signals_and_exposes_only_router(monkeypatch):
    api = importlib.import_module("cli_agent_orchestrator.api.admission_server")
    _, _, _, value = component()
    monkeypatch.setattr("cli_agent_orchestrator.security.auth.is_auth_enabled", lambda: False)
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    await value.start()
    server = api.AdmissionControlServer(value)
    try:
        await server.start()
        assert server.url.startswith("http://127.0.0.1:")
        assert {sig: signal.getsignal(sig) for sig in before} == before
        async with httpx.AsyncClient(base_url=server.url, trust_env=False) as client:
            assert (await client.get("/admission/resource-envelope")).status_code == 403
            for path in ("/docs", "/openapi.json", "/terminals", "/work"):
                assert (await client.get(path)).status_code == 404
    finally:
        value.close_admission()
        await server.stop()
        await value.stop()
    assert server.url is None and server.task.done()
    assert {sig: signal.getsignal(sig) for sig in before} == before


@pytest.mark.asyncio
async def test_control_start_failure_has_joinable_owner(monkeypatch):
    api = importlib.import_module("cli_agent_orchestrator.api.admission_server")
    _, _, _, value = component()
    await value.start()
    server = api.AdmissionControlServer(value)

    async def failure(self, sockets=None):
        raise RuntimeError("controlled startup failure")

    monkeypatch.setattr(api._LoopbackServer, "startup", failure)
    try:
        with pytest.raises(RuntimeError, match="controlled startup failure"):
            await server.start()
    finally:
        await server.stop()
        await value.stop()
    assert server.task.done() and server.url is None


@pytest.mark.asyncio
async def test_cancelled_control_start_and_stop_close_late_startup_listener(monkeypatch):
    api = importlib.import_module("cli_agent_orchestrator.api.admission_server")
    _, _, _, value = component()
    await value.start()
    server = api.AdmissionControlServer(value)
    entered, release = asyncio.Event(), asyncio.Event()
    original = api._LoopbackServer.startup

    async def startup(self, sockets=None):
        entered.set()
        await release.wait()
        return await original(self, sockets)

    monkeypatch.setattr(api._LoopbackServer, "startup", startup)
    starting = asyncio.create_task(server.start())
    stopping = None
    try:
        await asyncio.wait_for(entered.wait(), 10)
        starting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await starting
        stopping = asyncio.create_task(server.stop())
        await asyncio.sleep(0)
        stopping.cancel()
        await asyncio.sleep(0)
        assert not stopping.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(stopping), 10)
        assert server.task.done() and server.url is None
        assert all(not listener.is_serving() for listener in server._server.servers)
    finally:
        release.set()
        await asyncio.gather(starting, *([stopping] if stopping else []), return_exceptions=True)
        await server.stop()
        await value.stop()


@pytest.mark.parametrize("cancel", [False, True, "server_exit"])
@pytest.mark.asyncio
async def test_amain_activates_control_before_reader_and_retires_complete_lifetime(
    monkeypatch, tmp_path, cancel
):
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.runtime_channel import bridge as bridge_module
    from cli_agent_orchestrator.runtime_channel import store as store_module
    from cli_agent_orchestrator.services import admission_bootstrap, inbox_admission_runtime
    from cli_agent_orchestrator.services.log_writer import log_writer
    from cli_agent_orchestrator.services.status_monitor import status_monitor

    server_api = importlib.import_module("cli_agent_orchestrator.api.admission_server")
    f = fixture()
    controls, values, backgrounds = [], [], []
    entered = asyncio.Event()
    original_server = server_api.AdmissionControlServer

    def controller(participant, *, bootstrap_factory):
        assert bootstrap_factory() is f.bootstrap
        value = InboxAdmissionRuntime(
            participant,
            monitor=f.monitor,
            bootstrap=f.bootstrap,
            clock=lambda: f.clock[0],
            drain_timeout_s=10,
        )
        values.append(value)
        return value

    def server(value):
        result = original_server(value)
        controls.append(result)
        return result

    async def background():
        try:
            await asyncio.Event().wait()
        finally:
            backgrounds.append("retired")

    async def reader(self):
        assert values[0].available and controls[0].url
        assert self._resource_admission is f.monitor.owner
        assert self._resource_clock is values[0].clock
        entered.set()
        await asyncio.sleep(0)
        if cancel:
            await self._stop.wait()

    def scoped(scope):
        assert scope == "bridge-resource/Control-Node"
        return f.bootstrap

    monkeypatch.setenv("CAO_BRIDGE_SERVER_URL", "ws://isolated")
    monkeypatch.setenv("CAO_BRIDGE_RUNTIME_ID", "Control-Node")
    monkeypatch.setenv("CAO_BRIDGE_READY_FILE", str(tmp_path / "ready"))
    monkeypatch.setenv("CAO_ADMISSION_POLICY_FILE", "/private-controlled-policy.json")
    monkeypatch.setattr(bridge_module, "runtime_token", lambda: "test-channel-token")
    monkeypatch.setattr(bridge_module, "_drop_stale_rows", lambda: 0)
    monkeypatch.setattr(bridge_module, "restore_incarnation", lambda *args: "inc")
    monkeypatch.setattr(database, "init_runtime_db", lambda: None)
    monkeypatch.setattr(database, "main_target", lambda: SimpleNamespace())
    monkeypatch.setattr(store_module, "RemoteOperationStore", lambda target: SimpleNamespace())
    monkeypatch.setattr(admission_bootstrap, "scoped_process_bootstrap", scoped)
    monkeypatch.setattr(inbox_admission_runtime, "runtime_from_environment", controller)
    monkeypatch.setattr(server_api, "AdmissionControlServer", server)
    monkeypatch.setattr(log_writer, "run", background)
    monkeypatch.setattr(status_monitor, "run", background)
    monkeypatch.setattr(Bridge, "forward_status", lambda self: background())
    monkeypatch.setattr(Bridge, "run", reader)
    signals = []
    monkeypatch.setattr(
        asyncio.get_running_loop(), "add_signal_handler", lambda sig, fn: signals.append(sig)
    )
    main = asyncio.create_task(bridge_module._amain())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        if cancel == "server_exit":
            controls[0].task.cancel()
            with pytest.raises(AdmissionRuntimeUnavailable):
                await asyncio.wait_for(asyncio.shield(main), 15)
        elif cancel:
            main.cancel()
            await asyncio.sleep(0)
            main.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(main), 15)
        else:
            await asyncio.wait_for(asyncio.shield(main), 15)
    finally:
        if not main.done():
            main.cancel()
        await asyncio.gather(main, return_exceptions=True)
    assert values[0].retired and controls[0].task.done()
    assert controls[0].url is None and len(backgrounds) == 3
    assert signals == [signal.SIGINT, signal.SIGTERM]


@pytest.mark.asyncio
async def test_stop_during_silent_hello_retires_reader_control_and_runtime(monkeypatch):
    from websockets.asyncio.server import serve

    api = importlib.import_module("cli_agent_orchestrator.api.admission_server")
    bridge, _, _, value = component()
    monkeypatch.setattr(bridge, "_current_statuses", lambda: {})
    accepted, peers = asyncio.Event(), []

    async def silent(ws):
        peers.append(ws)
        await ws.recv()  # Receive the Bridge Hello, but never acknowledge it.
        accepted.set()
        await ws.wait_closed()

    await value.start()
    control = api.AdmissionControlServer(value)
    reader = None
    async with serve(silent, "127.0.0.1", 0) as remote:
        bridge.server_url = f"ws://127.0.0.1:{remote.sockets[0].getsockname()[1]}"
        try:
            await control.start()
            reader = asyncio.create_task(bridge.run())
            await asyncio.wait_for(accepted.wait(), 10)
            assert bridge._ws is None  # Handshake is not a ready channel.
            value.close_admission()
            await asyncio.wait_for(asyncio.shield(reader), 10)
            await bridge.drain()
            await control.stop()
            await value.stop()
            assert value.retired and control.task.done()
        finally:
            bridge.stop()
            for peer in peers:
                await peer.close()
            if reader is not None:
                await asyncio.gather(reader, return_exceptions=True)
            await control.stop()
            await value.stop()
