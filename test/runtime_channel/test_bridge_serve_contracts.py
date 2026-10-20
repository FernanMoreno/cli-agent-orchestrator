"""Bridge socket-loop contracts without a database or provider process."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Iterable

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.runtime_channel import bridge as bridge_module
from cli_agent_orchestrator.runtime_channel.bridge import Bridge, ChannelRefused
from cli_agent_orchestrator.runtime_channel.protocol import (
    PROTOCOL_VERSION,
    Hello,
    Result,
    Status,
    decode,
    encode,
)


class MemoryBridgeStore:
    """Small store port for the bridge's connection and replay boundary."""

    def __init__(self, durable_results: Iterable[dict] = ()) -> None:
        self.durable_results = list(durable_results)
        self.admitted_epochs: list[tuple[str, str, int]] = []
        self.result_queries: list[tuple[str, str]] = []
        self.revision_calls: list[tuple[str, str]] = []
        self.on_revision: Callable[[], None] | None = None

    def admit_epoch(self, runtime_id: str, incarnation_id: str, epoch: int) -> None:
        self.admitted_epochs.append((runtime_id, incarnation_id, epoch))

    def unsettled_results(self, runtime_id: str, incarnation_id: str) -> list[dict]:
        self.result_queries.append((runtime_id, incarnation_id))
        return list(self.durable_results)

    def next_revision(self, terminal_id: str, incarnation_id: str) -> int:
        self.revision_calls.append((terminal_id, incarnation_id))
        if self.on_revision is not None:
            self.on_revision()
        return len(self.revision_calls)


class FakeWebSocket:
    """The subset of ClientConnection consumed by Bridge.serve."""

    def __init__(
        self,
        reply: Hello,
        messages: Iterable[str] = (),
        *,
        before_messages: Callable[[], Awaitable[None]] | None = None,
        on_send: Callable[[object], None] | None = None,
    ) -> None:
        self.reply = encode(reply)
        self.messages = tuple(messages)
        self.before_messages = before_messages
        self.on_send = on_send
        self.sent: list[object] = []
        self.receive_count = 0

    async def send(self, raw: str) -> None:
        frame = decode(raw)
        self.sent.append(frame)
        if self.on_send is not None:
            self.on_send(frame)

    async def recv(self) -> str:
        self.receive_count += 1
        return self.reply

    def __aiter__(self):
        async def receive_messages():
            if self.before_messages is not None:
                await self.before_messages()
            for raw in self.messages:
                yield raw

        return receive_messages()


def _server_hello(
    *,
    protocol_version: int = PROTOCOL_VERSION,
    incarnation_id: str = "inc-1",
    epoch: int | None = 7,
) -> Hello:
    return Hello(
        protocol_version=protocol_version,
        runtime_id="runtime-1",
        runtime_incarnation_id=incarnation_id,
        connection_epoch=epoch,
    )


@pytest.mark.asyncio
async def test_serve_accepts_exact_hello_epoch_and_clears_readiness_on_close(monkeypatch, tmp_path):
    ready_file = tmp_path / "bridge" / "ready"
    store = MemoryBridgeStore()
    bridge = Bridge(
        "ws://isolated",
        "runtime-1",
        "unused-token",
        ready_file,
        store=store,
        incarnation_id="inc-1",
    )
    statuses = {"term-1": TerminalStatus.PROCESSING}
    pushed_statuses: list[str] = []
    monkeypatch.setattr(bridge, "_current_statuses", lambda: statuses)

    async def push_status(terminal_id: str) -> None:
        pushed_statuses.append(terminal_id)

    monkeypatch.setattr(bridge, "_push_status", push_status)
    ready_during_connection: list[bool] = []
    ws: FakeWebSocket

    async def inspect_ready_connection() -> None:
        assert bridge._ws is ws
        assert ready_file.read_text() == f"{os.getpid()}\n"
        ready_during_connection.append(True)

    ws = FakeWebSocket(_server_hello(), before_messages=inspect_ready_connection)

    await bridge.serve(ws)

    runtime_hello = ws.sent[0]
    assert isinstance(runtime_hello, Hello)
    assert runtime_hello.protocol_version == PROTOCOL_VERSION
    assert runtime_hello.runtime_id == "runtime-1"
    assert runtime_hello.runtime_incarnation_id == "inc-1"
    assert runtime_hello.connection_epoch is None
    assert runtime_hello.statuses == statuses
    assert store.admitted_epochs == [("runtime-1", "inc-1", 7)]
    assert store.result_queries == [("runtime-1", "inc-1")]
    assert pushed_statuses == ["term-1"]
    assert ready_during_connection == [True]
    assert bridge.connection_epoch == 7
    assert bridge._ws is None
    assert not ready_file.exists()


@pytest.mark.asyncio
async def test_serve_flushes_offline_and_durable_results_after_hello(monkeypatch):
    volatile_result = Result(
        op_id="volatile-1",
        ok=True,
        payload={"source": "memory"},
        runtime_incarnation_id="inc-1",
    )
    durable_result = Result(
        op_id="durable-1",
        ok=True,
        payload={"source": "store"},
        runtime_incarnation_id="inc-1",
    )
    store = MemoryBridgeStore([durable_result.model_dump(mode="json")])
    bridge = Bridge(
        "ws://isolated", "runtime-1", "unused-token", store=store, incarnation_id="inc-1"
    )
    monkeypatch.setattr(bridge, "_current_statuses", lambda: {})

    await bridge._send(volatile_result)
    await bridge._send(Status(terminal_id="term-1", status=TerminalStatus.IDLE))
    assert bridge._unsent == [volatile_result]

    ws = FakeWebSocket(_server_hello())
    await bridge.serve(ws)

    assert isinstance(ws.sent[0], Hello)
    results = [frame for frame in ws.sent if isinstance(frame, Result)]
    assert [frame.op_id for frame in results] == ["volatile-1", "durable-1"]
    assert [frame.payload["source"] for frame in results] == ["memory", "store"]
    assert bridge._unsent == []
    assert store.result_queries == [("runtime-1", "inc-1")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        _server_hello(protocol_version=PROTOCOL_VERSION + 1),
        _server_hello(incarnation_id="old-incarnation"),
        _server_hello(epoch=None),
    ],
    ids=["protocol-mismatch", "incarnation-mismatch", "missing-epoch"],
)
async def test_serve_refuses_unmatched_server_hello_before_readiness(reply, tmp_path, monkeypatch):
    ready_file = tmp_path / "bridge" / "ready"
    store = MemoryBridgeStore()
    bridge = Bridge(
        "ws://isolated",
        "runtime-1",
        "unused-token",
        ready_file,
        store=store,
        incarnation_id="inc-1",
    )
    monkeypatch.setattr(bridge, "_current_statuses", lambda: {})
    ws = FakeWebSocket(reply)

    with pytest.raises(ChannelRefused):
        await bridge.serve(ws)

    assert len(ws.sent) == 1
    assert isinstance(ws.sent[0], Hello)
    assert store.admitted_epochs == []
    assert bridge._ws is None
    assert not ready_file.exists()


@pytest.mark.asyncio
async def test_serve_rejects_oversized_frame_and_clears_ready_state(monkeypatch, tmp_path):
    ready_file = tmp_path / "bridge" / "ready"
    store = MemoryBridgeStore()
    bridge = Bridge(
        "ws://isolated",
        "runtime-1",
        "unused-token",
        ready_file,
        store=store,
        incarnation_id="inc-1",
    )
    monkeypatch.setattr(bridge, "_current_statuses", lambda: {})
    ready_during_connection: list[bool] = []
    ws: FakeWebSocket

    async def inspect_ready_connection() -> None:
        assert bridge._ws is ws
        assert ready_file.exists()
        ready_during_connection.append(True)

    oversized_frame = "x" * (16 * 1024 * 1024 + 1)
    ws = FakeWebSocket(_server_hello(), [oversized_frame], before_messages=inspect_ready_connection)

    with pytest.raises(ChannelRefused, match="exceeds transport bound"):
        await bridge.serve(ws)

    assert ready_during_connection == [True]
    assert store.admitted_epochs == [("runtime-1", "inc-1", 7)]
    assert bridge._ws is None
    assert not ready_file.exists()


@pytest.mark.asyncio
async def test_push_status_skips_unknown_without_projection_or_revision(monkeypatch):
    from cli_agent_orchestrator.services import terminal_service

    store = MemoryBridgeStore()
    bridge = Bridge(
        "ws://isolated", "runtime-1", "unused-token", store=store, incarnation_id="inc-1"
    )
    ws = FakeWebSocket(_server_hello())
    bridge._ws = ws
    monkeypatch.setattr(bridge, "_status_of", lambda _terminal_id: TerminalStatus.UNKNOWN)
    monkeypatch.setattr(
        terminal_service,
        "get_terminal",
        lambda _terminal_id: pytest.fail("unknown status must not load a terminal projection"),
    )

    await bridge._push_status("term-1", skip_unknown=True)

    assert ws.sent == []
    assert store.revision_calls == []
    assert bridge._terminal_locks == {}


@pytest.mark.asyncio
async def test_push_status_waits_for_terminal_turn_before_snapshot_and_send(monkeypatch):
    from cli_agent_orchestrator.services import terminal_service

    events: list[str] = []
    store = MemoryBridgeStore()
    store.on_revision = lambda: events.append("revision")
    bridge = Bridge(
        "ws://isolated", "runtime-1", "unused-token", store=store, incarnation_id="inc-1"
    )
    ws = FakeWebSocket(_server_hello(), on_send=lambda _frame: events.append("send"))
    bridge._ws = ws
    monkeypatch.setattr(
        bridge,
        "_status_of",
        lambda _terminal_id: events.append("snapshot") or TerminalStatus.PROCESSING,
    )
    monkeypatch.setattr(
        terminal_service,
        "get_terminal",
        lambda _terminal_id: events.append("projection") or {"id": "term-1"},
    )
    monkeypatch.setattr(bridge_module, "_jsonable", lambda terminal: terminal)

    async def inline_to_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(bridge_module.asyncio, "to_thread", inline_to_thread)

    async with bridge._terminal_turn("term-1"):
        pending_status = asyncio.create_task(bridge._push_status("term-1"))
        await asyncio.sleep(0)
        assert events == []
        events.append("effect")

    await pending_status

    assert events == ["effect", "snapshot", "projection", "revision", "send"]
    assert len(ws.sent) == 1
    status = ws.sent[0]
    assert isinstance(status, Status)
    assert status.terminal_id == "term-1"
    assert status.status == TerminalStatus.PROCESSING
    assert status.revision == 1
    assert status.runtime_incarnation_id == "inc-1"
