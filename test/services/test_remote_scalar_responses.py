"""Typed remote service returns reject malformed decoded scalar payloads."""

from contextlib import nullcontext
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.services import remote_terminal_service as remote
from cli_agent_orchestrator.services import (
    terminal_service,
    turn_recovery_service,
    work_terminal,
)


@pytest.fixture
def remote_response(monkeypatch):
    monkeypatch.setattr(remote, "placement", lambda _: {"runtime_id": "remote"})
    monkeypatch.setattr(work_terminal, "terminal_dispatch_lock", lambda *args: nullcontext())
    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", lambda _: None)
    monkeypatch.setattr(terminal_service, "release_terminal_dispatch_lock", lambda _: False)
    monkeypatch.setattr(terminal_service, "update_last_active", lambda _: None)

    def set_payload(payload):
        monkeypatch.setattr(remote, "call", lambda *args, **kwargs: payload)
        monkeypatch.setattr(remote, "call_async", AsyncMock(return_value=payload))
        monkeypatch.setattr(remote, "terminal_projection", lambda _: payload)

    return set_payload


@pytest.mark.parametrize(
    "read",
    [
        terminal_service.get_output,
        lambda terminal: terminal_service.read_output_range(terminal, 0, 16),
    ],
)
@pytest.mark.parametrize("value", [None, True, 1, [], {}])
def test_remote_output_rejects_non_string(remote_response, read, value):
    remote_response({"output": value})
    with pytest.raises(RuntimeError, match="malformed.*output"):
        read("remote-scalar")


@pytest.mark.parametrize(
    "read",
    [
        terminal_service.get_output,
        lambda terminal: terminal_service.read_output_range(terminal, 0, 16),
    ],
)
@pytest.mark.parametrize("value", ["", "answer"])
def test_remote_output_preserves_strings(remote_response, read, value):
    remote_response({"output": value})
    assert read("remote-scalar") == value


@pytest.mark.parametrize(
    "read",
    [
        terminal_service.get_output,
        lambda terminal: terminal_service.read_output_range(terminal, 0, 16),
    ],
)
def test_remote_output_missing_field_keeps_key_error(remote_response, read):
    remote_response({})
    with pytest.raises(KeyError, match="output"):
        read("remote-scalar")


@pytest.mark.parametrize("value", [True, 1, [], {}])
def test_remote_working_directory_rejects_non_string(remote_response, value):
    remote_response({"working_directory": value})
    with pytest.raises(RuntimeError, match="malformed.*working directory"):
        terminal_service.get_working_directory("remote-scalar")


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({}, None),
        ({"working_directory": None}, None),
        ({"working_directory": ""}, ""),
        ({"working_directory": "/tmp/work"}, "/tmp/work"),
    ],
)
def test_remote_working_directory_preserves_optional_default(remote_response, payload, expected):
    remote_response(payload)
    assert terminal_service.get_working_directory("remote-scalar") == expected


@pytest.mark.parametrize("value", [None, True, False, "1", [], {}])
def test_remote_input_rejects_non_integer_turn_sequence(remote_response, value):
    remote_response({"turn_sequence": value})
    with pytest.raises(RuntimeError, match="malformed.*turn sequence"):
        terminal_service.send_input("remote-scalar", "answer")


@pytest.mark.parametrize("value", [-1, 0, 1])
def test_remote_input_preserves_integer_turn_sequence(remote_response, value):
    remote_response({"turn_sequence": value})
    assert terminal_service.send_input("remote-scalar", "answer") == value


def test_remote_input_missing_sequence_keeps_key_error(remote_response):
    remote_response({})
    with pytest.raises(KeyError, match="turn_sequence"):
        terminal_service.send_input("remote-scalar", "answer")


@pytest.mark.parametrize("value", [False, 0, "", [], "turn"])
def test_remote_get_turn_rejects_non_dictionary(remote_response, value):
    remote_response({"turn": value})
    with pytest.raises(RuntimeError, match="malformed.*turn"):
        turn_recovery_service.get_turn("remote-scalar")


@pytest.mark.parametrize("payload", [{}, {"turn": None}, {"turn": {}}])
def test_remote_get_turn_preserves_no_turn_default(remote_response, payload):
    remote_response(payload)
    assert turn_recovery_service.get_turn("remote-scalar") == {
        "terminal_id": "remote-scalar",
        "generation": None,
        "state": "none",
        "allowed_actions": [],
    }


def test_remote_get_turn_preserves_dictionary(remote_response):
    turn = {"generation": "g", "state": "pending"}
    remote_response({"turn": turn})
    assert turn_recovery_service.get_turn("remote-scalar") is turn


@pytest.mark.parametrize("value", [None, False, 0, "turn", []])
def test_remote_verify_turn_rejects_non_dictionary(remote_response, value):
    remote_response({"turn": value})
    with pytest.raises(RuntimeError, match="malformed.*turn"):
        turn_recovery_service.verify_turn("remote-scalar", "generation")


@pytest.mark.parametrize("turn", [{}, {"state": "verified"}])
def test_remote_verify_turn_preserves_dictionary(remote_response, turn):
    remote_response({"turn": turn})
    assert turn_recovery_service.verify_turn("remote-scalar", "generation") is turn


def test_remote_verify_turn_missing_field_keeps_key_error(remote_response):
    remote_response({})
    with pytest.raises(KeyError, match="turn"):
        turn_recovery_service.verify_turn("remote-scalar", "generation")


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, False, 0, "turn", []])
async def test_remote_cancel_turn_rejects_non_dictionary(remote_response, value):
    remote_response({"turn": value})
    with pytest.raises(RuntimeError, match="malformed.*turn"):
        await turn_recovery_service.cancel_turn("remote-scalar", "generation")


@pytest.mark.asyncio
@pytest.mark.parametrize("turn", [{}, {"state": "none"}])
async def test_remote_cancel_turn_preserves_dictionary(remote_response, turn):
    remote_response({"turn": turn})
    assert await turn_recovery_service.cancel_turn("remote-scalar", "generation") is turn


@pytest.mark.asyncio
async def test_remote_cancel_turn_missing_field_keeps_key_error(remote_response):
    remote_response({})
    with pytest.raises(KeyError, match="turn"):
        await turn_recovery_service.cancel_turn("remote-scalar", "generation")
