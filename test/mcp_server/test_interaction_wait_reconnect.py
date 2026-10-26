"""Explicit wait reconnect stays bounded and never retries mutations."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from cli_agent_orchestrator.mcp_server import server

pytestmark = pytest.mark.offline_database
GROUP = "2e1fc625-a391-4bfd-a62d-4dddbcbf70a3"


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=100.0, sleeps=[])

    def sleep(seconds):
        state.sleeps.append(seconds)
        state.now += seconds

    monkeypatch.setattr(server, "time", SimpleNamespace(monotonic=lambda: state.now, sleep=sleep))
    monkeypatch.setattr(server.mcp_utils, "_read_interaction_capability", lambda: "test-capability")
    return state


def wait(seconds=20):
    return asyncio.run(
        server.cao_wait_for_user_answers(GROUP, wait_seconds=seconds, after_event_sequence=7)
    ).model_dump(mode="json")


def test_wait_recovers_connection_failures_with_one_deadline(monkeypatch, clock):
    ready = {
        "group_id": GROUP,
        "state": "ready",
        "answers": [],
        "next_event_sequence": 8,
        "next_poll_after_ms": 0,
    }
    request = MagicMock(side_effect=[requests.ConnectionError(), requests.ConnectionError(), ready])
    monkeypatch.setattr(server.mcp_utils, "interaction_post_json", request)
    assert wait()["state"] == "ready"
    assert clock.sleeps == [0.25, 0.25]
    assert [c.kwargs["timeout"] for c in request.call_args_list] == [25, 24.75, 24.5]
    assert [c.args[1]["wait_seconds"] for c in request.call_args_list] == [20, 19, 19]
    assert all(
        c.args[0] == f"/agent/interactions/groups/{GROUP}/wait"
        and c.args[1]["after_event_sequence"] == 7
        for c in request.call_args_list
    )
    assert all(c.kwargs["capability"] == "test-capability" for c in request.call_args_list)


def test_persistent_outage_stops_at_existing_budget(monkeypatch, clock):
    request = MagicMock(side_effect=requests.ConnectionError("private diagnostic"))
    monkeypatch.setattr(server.mcp_utils, "interaction_post_json", request)
    result = wait(1)
    assert result["error"]["code"] == "unavailable"
    assert clock.now == 106
    assert request.call_count == 24
    assert all(c.kwargs["timeout"] > 0 for c in request.call_args_list)
    assert "private diagnostic" not in str(result)


@pytest.mark.parametrize(
    "error",
    [
        requests.ConnectTimeout(),
        requests.ReadTimeout(),
        requests.Timeout(),
        requests.HTTPError(),
        RuntimeError(),
    ],
)
def test_wait_never_retries_nonconnection_errors(monkeypatch, clock, error):
    request = MagicMock(side_effect=error)
    monkeypatch.setattr(server.mcp_utils, "interaction_post_json", request)
    assert "error" in wait()
    assert request.call_count == 1
    assert not clock.sleeps


def test_zero_wait_remains_one_probe(monkeypatch, clock):
    request = MagicMock(side_effect=requests.ConnectionError())
    monkeypatch.setattr(server.mcp_utils, "interaction_post_json", request)
    assert wait(0)["error"]["code"] == "unavailable"
    assert request.call_count == 1
    assert not clock.sleeps


def test_reconnect_pins_capability_and_uses_real_http_helper(monkeypatch, clock):
    reader = MagicMock(side_effect=["original-capability", "replacement-capability"])
    monkeypatch.setattr(server.mcp_utils, "_read_interaction_capability", reader)
    monkeypatch.setattr(server.mcp_utils, "_auth_headers", lambda: {})
    seen = []

    def request(session, method, url, **kwargs):
        seen.append(kwargs)
        assert session.trust_env is False
        assert kwargs["allow_redirects"] is False
        if len(seen) == 1:
            raise requests.ConnectionError()
        response = MagicMock()
        response.json.return_value = {
            "group_id": GROUP,
            "state": "pending",
            "answers": [],
            "next_event_sequence": 7,
            "next_poll_after_ms": 1000,
        }
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    assert wait()["state"] == "pending"
    assert reader.call_count == 1
    assert len(seen) == 2
    assert all(k["headers"]["X-CAO-Interaction-Capability"] == "original-capability" for k in seen)


@pytest.mark.parametrize("operation", ["ask", "ack"])
def test_connection_errors_never_retry_mutators(monkeypatch, clock, operation):
    request = MagicMock(side_effect=requests.ConnectionError())
    monkeypatch.setattr(server.mcp_utils, "interaction_post_json", request)
    if operation == "ask":
        call = server.cao_ask_user(
            "mutator-operation-0001",
            [
                server._InteractionQuestionInput(
                    kind="text", prompt="Synthetic question", required=True
                )
            ],
        )
    else:
        call = server.cao_acknowledge_user_answers(GROUP, [])
    result = asyncio.run(call).model_dump(mode="json")
    assert result["error"]["code"] == "unavailable"
    assert request.call_count == 1
    assert not clock.sleeps
