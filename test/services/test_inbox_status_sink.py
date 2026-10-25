"""Bounded sink admission must preserve accepted work and other subscribers."""

from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.services import event_bus
from cli_agent_orchestrator.services import inbox_service as module

pytestmark = pytest.mark.offline_database


def ready(key):
    return {"topic": f"terminal.{key}.status", "data": {"status": "idle"}}


def test_full_sink_rejects_without_evicting_any_accepted_event():
    sink = module._StatusEventSink(4, lambda _key: False)
    for key in ("a", "b", "a", "c"):
        sink.put_nowait(ready(key))
    with pytest.raises(module.asyncio.QueueFull):
        sink.put_nowait(ready("overflow"))
    assert sink.qsize() == 4
    result = []
    while not sink.empty():
        key = sink.pending.pop()
        result.append(key)
        sink.pending.completed(key)
    assert result == ["a", "b", "a", "c"]


def test_full_sink_does_not_prevent_delivery_to_another_subscriber(monkeypatch):
    monkeypatch.setattr(event_bus, "get_server_settings", lambda: {"event_bus_max_queue_size": 8})
    bus = event_bus.EventBus()
    sink = module._StatusEventSink(2, lambda _key: False)
    bus.subscribe_sink("terminal.*.status", sink)
    observer = bus.subscribe("terminal.*.status")
    dropped = Mock()
    monkeypatch.setattr(bus, "_record_drop", dropped)
    for key in ("a", "a", "b"):
        bus._dispatch(f"terminal.{key}.status", {"status": "idle"})
    assert sink.qsize() == 2
    assert [observer.get_nowait()["topic"] for _ in range(3)] == [
        "terminal.a.status",
        "terminal.a.status",
        "terminal.b.status",
    ]
    dropped.assert_called_once_with("terminal.b.status")


def test_nonready_and_malformed_events_are_isolated_before_other_subscribers(monkeypatch):
    monkeypatch.setattr(event_bus, "get_server_settings", lambda: {"event_bus_max_queue_size": 8})
    bus = event_bus.EventBus()
    sink = module._StatusEventSink(2, lambda _key: False)
    bus.subscribe_sink("terminal.*.status", sink)
    observer = bus.subscribe("terminal.*.status")
    for data in ({"status": "processing"}, {}, {"status": None}, {"status": "idle"}):
        bus._dispatch("terminal.a.status", data)
    assert observer.qsize() == 4
    assert sink.qsize() == 1
    assert sink.pending.pop() == "a"


def test_closed_unsubscribed_sink_releases_state_and_ignores_late_input():
    bus = event_bus.EventBus()
    sink = module._StatusEventSink(4, lambda _key: False)
    bus.subscribe_sink("terminal.*.status", sink)
    sink.put_nowait(ready("a"))
    sink.close()
    bus.unsubscribe("terminal.*.status", sink)
    bus._dispatch("terminal.a.status", {"status": "idle"})
    sink.put_nowait(ready("late"))
    assert sink.closed
    assert sink.empty()
    assert sink.pending._events == {}
    assert sink.pending._ready == []
    assert bus._wildcard == {}


def test_exact_subscription_sink_unsubscribe_preserves_fifo_subscriber(monkeypatch):
    monkeypatch.setattr(event_bus, "get_server_settings", lambda: {"event_bus_max_queue_size": 8})
    bus = event_bus.EventBus()
    sink = module._StatusEventSink(4, lambda _key: False)
    bus.subscribe_sink("terminal.a.status", sink)
    observer = bus.subscribe("terminal.a.status")
    bus.unsubscribe("terminal.a.status", sink)
    bus._dispatch("terminal.a.status", {"status": "idle"})
    assert sink.empty()
    assert observer.get_nowait()["topic"] == "terminal.a.status"
