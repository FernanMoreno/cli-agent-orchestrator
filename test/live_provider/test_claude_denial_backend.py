"""Actual loopback fixture and pure primary identity tests; no CLI/login."""

import json
import urllib.request
from test.live_provider.claude_denial_backend import DenialBackend
from test.live_provider.test_claude_client_denial import ALLOWED, CALL, TOOL, request

import pytest

pytestmark = pytest.mark.offline_database


def backend():
    return DenialBackend(
        excluded=TOOL,
        permitted={ALLOWED},
        call_id=CALL,
        nonce="caos-denial-nonce",
        arguments={"question": "control"},
    )


def invoke(server, data, path="/v1/messages?beta=true"):
    return server.response(path, json.dumps(data).encode())


def test_auxiliary_request_first_does_not_consume_injection():
    server = backend()
    auxiliary = {"model": "synthetic", "messages": [{"role": "user", "content": "title"}]}
    assert invoke(server, auxiliary)[0] == "application/json"
    assert server.primary is None and server.auxiliary == 1
    assert b'"tool_use"' in invoke(server, request())[1]
    assert server.primary is not None


def test_error_followup_must_be_correlated_and_not_replayed():
    server = backend()
    invoke(server, request())
    followup = request()
    followup["messages"].append(
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": CALL, "is_error": True, "content": "denied"}
            ],
        }
    )
    assert b'"end_turn"' in invoke(server, followup)[1]
    assert server.followup_error is True
    with pytest.raises(ValueError, match="synthetic_followup_invalid"):
        invoke(server, followup)


@pytest.mark.parametrize("value", [1, False, None, "true"])
def test_followup_requires_exact_boolean_error(value):
    server = backend()
    invoke(server, request())
    followup = request()
    followup["messages"].append(
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": CALL, "is_error": value}],
        }
    )
    with pytest.raises(ValueError, match="synthetic_followup_invalid"):
        invoke(server, followup)


def test_real_loopback_http_exchange_and_retirement():
    server = backend()
    url = server.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        req = urllib.request.Request(
            url + "/v1/messages",
            data=json.dumps(request()).encode(),
            headers={"Content-Type": "application/json"},
        )
        with opener.open(req, timeout=5) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "text/event-stream"
            assert b'"tool_use"' in response.read()
    finally:
        server.stop()
    assert not server.thread.is_alive()


def test_routes_duplicate_json_and_limits_cannot_pass_as_primary():
    server = backend()
    assert invoke(server, {}, "/v1/messages/count_tokens")[1] == b'{"input_tokens":1}'
    with pytest.raises(ValueError, match="synthetic_route_invalid"):
        invoke(server, {}, "/unknown")
    with pytest.raises(ValueError, match="invalid_control_json"):
        server.response("/v1/messages", b'{"stream":true,"stream":false}')
    server.requests = 16
    with pytest.raises(ValueError, match="synthetic_request_limit"):
        invoke(server, request())
