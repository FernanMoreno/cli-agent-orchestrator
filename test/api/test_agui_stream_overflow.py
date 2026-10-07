"""Overflow → reconnect → backfill contract for the AG-UI stream endpoint.

Reproduces the reviewer's must-fix #3 residue at the HTTP boundary (PR #436,
``fanhongy``): when a subscriber's bounded queue overflows the stream must
*close* (so the browser's ``EventSource`` reconnects) rather than silently
dropping events while holding the connection open. On reconnect the endpoint
replays the dropped record via ``Last-Event-ID`` exactly once.

The real overflow mechanics live in ``test/services/test_sse_bus_overflow.py``;
here we pin the *endpoint* contract:
* it registers its live subscription with ``overflow_close=True`` (opts into the
  gap-signal behaviour); and
* when the bus drain closes (overflow), the HTTP stream ends and the subscriber
  is unregistered — it is no longer left open; then
* a follow-up request carrying ``Last-Event-ID`` replays the dropped record once.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import cli_agent_orchestrator.api.main as main
from cli_agent_orchestrator.api.main import app
from cli_agent_orchestrator.services.event_log_service import EventLog

client = TestClient(app, base_url="http://localhost")


class _OverflowBus:
    """SseBus stand-in whose ``drain`` yields a finite prefix then returns.

    A finite ``drain`` models the overflow-close path: the endpoint delivers the
    pre-gap events and then the generator ends. Records register/unregister and
    the ``overflow_close`` flag the endpoint passes, so the test can assert the
    subscriber is opened with gap-signalling on and is not left open afterward.
    """

    def __init__(self, events):
        self._events = list(events)
        self.overflow_close = None
        self.registered = 0
        self.unregistered = 0

    def register(self, overflow_close: bool = False):
        self.registered += 1
        self.overflow_close = overflow_close
        return object()

    def unregister(self, sub):
        self.unregistered += 1

    async def drain(self, sub):
        for event in self._events:
            yield event


@pytest.fixture(autouse=True)
def _agui_on(monkeypatch):
    monkeypatch.setenv("CAO_AGUI_ENABLED", "true")
    monkeypatch.setattr(main, "is_auth_enabled", lambda: False)


def test_overflow_triggers_reconnect_backfill(monkeypatch):
    # The retained ring includes the last acknowledged record AND the record
    # whose live delivery is dropped. Replay validates/selects one snapshot;
    # an empty history cannot truthfully acknowledge a retained last cursor.
    log = EventLog()
    detail = {"agent_name": "w", "provider": "mock_cli"}
    pre_gap = [
        log.append("launch", "t-1", "s", detail),
        log.append("completion", "t-1", "s", detail),
    ]
    dropped = log.append("completion", "t-1", "s", detail)
    bus = _OverflowBus(pre_gap)
    monkeypatch.setattr("cli_agent_orchestrator.services.sse_bus.get_bus", lambda: bus)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.event_log_service.get_event_log", lambda: log
    )

    # 1) First connection: overflow closes the stream after the pre-gap events.
    with client.stream("GET", "/agui/v1/stream") as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    assert bus.overflow_close is True
    assert bus.registered == 1 and bus.unregistered == 1
    assert f"id: {pre_gap[0]['id']}" in body and f"id: {pre_gap[1]['id']}" in body
    assert dropped["id"] not in body

    # 2) Reconnect from the actual acknowledged cursor. Also place the dropped
    # record on the live queue to prove replay/live overlap is de-duplicated.
    bus2 = _OverflowBus([dropped])
    monkeypatch.setattr("cli_agent_orchestrator.services.sse_bus.get_bus", lambda: bus2)
    snapshots = []
    real_history = log.history

    def history(*args, **kwargs):
        # The live subscription must exist before the retained replay snapshot.
        assert bus2.registered == 1 and bus2.unregistered == 0
        snapshot = real_history(*args, **kwargs)
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(log, "history", history)
    with client.stream(
        "GET", "/agui/v1/stream", headers={"Last-Event-ID": pre_gap[-1]["id"]}
    ) as resp:
        assert resp.status_code == 200
        body2 = "".join(resp.iter_text())

    assert snapshots == [[*pre_gap, dropped]]
    assert body2.count(f"id: {dropped['id']}\n") == 1
    assert all(event["id"] not in body2 for event in pre_gap)
    assert "cursor_expired" not in body2
    assert bus2.overflow_close is True
    assert bus2.registered == 1 and bus2.unregistered == 1


def test_overflow_reconnect_reports_expired_cursor_without_fallback(monkeypatch):
    log = EventLog()
    retained = log.append("completion", "t-1", "s", {})
    bus = _OverflowBus([retained])
    monkeypatch.setattr("cli_agent_orchestrator.services.sse_bus.get_bus", lambda: bus)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.event_log_service.get_event_log", lambda: log
    )
    with client.stream(
        "GET", "/agui/v1/stream", headers={"Last-Event-ID": "evicted-cursor"}
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    assert "event: cursor_expired\n" in body
    assert '"resync_required":true' in body
    assert retained["id"] not in body
    assert bus.overflow_close is True
    assert bus.registered == 1 and bus.unregistered == 1
