"""WebSocket input respects the interaction generation guard."""

import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import HTTPException

from cli_agent_orchestrator.api import main as main_module
from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError


@pytest.mark.asyncio
async def test_websocket_closes_safely_when_required_interaction_blocks_input():
    websocket = MagicMock()
    websocket.client = MagicMock(host="127.0.0.1")
    websocket.headers = {"host": "localhost"}
    websocket.scope = {"scheme": "ws"}
    websocket.query_params = {"expected_incarnation_id": "question-source-incarnation"}
    websocket.accept = AsyncMock()
    websocket.close = AsyncMock()
    websocket.receive_text = AsyncMock(
        return_value=json.dumps({"type": "input", "data": "secret input"})
    )
    websocket.send_bytes = AsyncMock()

    terminal_process = MagicMock()
    terminal_process.poll.return_value = None
    fake_loop = MagicMock()

    with (
        patch.object(main_module, "WS_ALLOWED_CLIENTS", ["127.0.0.1"]),
        patch.object(
            main_module,
            "get_terminal_metadata",
            return_value={
                "tmux_session": "cao-s",
                "tmux_window": "w",
                "session_incarnation_id": "question-source-incarnation",
            },
        ),
        patch(
            "cli_agent_orchestrator.services.remote_terminal_service.placement",
            return_value=None,
        ),
        patch.object(main_module, "get_backend") as get_backend,
        patch.object(main_module.pty, "openpty", return_value=(100, 101)),
        patch.object(main_module.fcntl, "ioctl"),
        patch.object(main_module.fcntl, "fcntl", return_value=0),
        patch.object(main_module.subprocess, "Popen", return_value=terminal_process),
        patch.object(main_module.asyncio, "get_event_loop", return_value=fake_loop),
        patch.object(main_module.os, "close"),
        patch.object(main_module.os, "write") as write,
        patch.object(
            main_module.terminal_service,
            "record_direct_interaction_input",
            side_effect=TerminalInputBlockedError(
                "private interaction details", action="wait_for_interaction"
            ),
        ) as advance_input,
    ):
        get_backend.return_value.prepare_web_attach.return_value = ["tmux", "attach"]
        await main_module.terminal_ws(websocket, "abcd1234")

    advance_input.assert_called_once_with(
        "abcd1234",
        expected_incarnation_id="question-source-incarnation",
        reason="websocket_input",
    )
    write.assert_not_called()
    websocket.close.assert_awaited_with(code=4409, reason="Input blocked by a required interaction")
    assert "private interaction details" not in websocket.close.call_args.kwargs["reason"]


@pytest.mark.asyncio
async def test_websocket_rejects_replaced_incarnation_before_pty_attach():
    websocket = MagicMock()
    websocket.client = MagicMock(host="127.0.0.1")
    websocket.headers = {"host": "localhost"}
    websocket.scope = {"scheme": "ws"}
    websocket.query_params = {"expected_incarnation_id": "question-source-incarnation"}
    websocket.accept = AsyncMock()
    websocket.close = AsyncMock()

    with (
        patch.object(main_module, "WS_ALLOWED_CLIENTS", ["127.0.0.1"]),
        patch.object(main_module, "is_auth_enabled", return_value=False),
        patch.object(
            main_module,
            "get_terminal_metadata",
            return_value={
                "tmux_session": "cao-s",
                "tmux_window": "w",
                "session_incarnation_id": "replacement-incarnation",
            },
        ),
        patch.object(main_module, "get_backend") as get_backend,
    ):
        await main_module.terminal_ws(websocket, "abcd1234")

    get_backend.assert_not_called()
    websocket.close.assert_awaited_with(code=4409, reason="Interaction source incarnation is stale")


@pytest.mark.asyncio
async def test_websocket_rechecks_incarnation_before_each_input_byte():
    websocket = MagicMock()
    websocket.client = MagicMock(host="127.0.0.1")
    websocket.headers = {"host": "localhost"}
    websocket.scope = {"scheme": "ws"}
    websocket.query_params = {"expected_incarnation_id": "question-source-incarnation"}
    websocket.accept = AsyncMock()
    websocket.close = AsyncMock()
    websocket.receive_text = AsyncMock(
        return_value=json.dumps({"type": "input", "data": "answer text"})
    )
    websocket.send_bytes = AsyncMock()

    terminal_process = MagicMock()
    terminal_process.poll.return_value = None
    fake_loop = MagicMock()
    current_metadata = {
        "tmux_session": "cao-s",
        "tmux_window": "w",
        "session_incarnation_id": "question-source-incarnation",
    }
    replacement_metadata = {
        "tmux_session": "cao-s",
        "tmux_window": "w",
        "session_incarnation_id": "replacement-incarnation",
    }
    with (
        patch.object(main_module, "WS_ALLOWED_CLIENTS", ["127.0.0.1"]),
        patch.object(main_module, "is_auth_enabled", return_value=False),
        patch.object(
            main_module,
            "get_terminal_metadata",
            side_effect=[current_metadata, replacement_metadata],
        ),
        patch(
            "cli_agent_orchestrator.services.remote_terminal_service.placement",
            return_value=None,
        ),
        patch.object(main_module, "get_backend") as get_backend,
        patch.object(main_module.pty, "openpty", return_value=(100, 101)),
        patch.object(main_module.fcntl, "ioctl"),
        patch.object(main_module.fcntl, "fcntl", return_value=0),
        patch.object(main_module.subprocess, "Popen", return_value=terminal_process),
        patch.object(main_module.asyncio, "get_event_loop", return_value=fake_loop),
        patch.object(main_module.os, "close"),
        patch.object(main_module.os, "write") as write,
        patch.object(
            main_module.terminal_service, "record_direct_interaction_input"
        ) as advance_input,
    ):
        get_backend.return_value.prepare_web_attach.return_value = ["tmux", "attach"]
        await main_module.terminal_ws(websocket, "abcd1234")

    advance_input.assert_not_called()
    write.assert_not_called()
    websocket.close.assert_awaited_with(code=4409, reason="Interaction source incarnation is stale")


@pytest.mark.asyncio
async def test_legacy_key_route_reports_a_durable_interaction_block_as_conflict(monkeypatch):
    blocked = TerminalInputBlockedError(
        "terminal waits for its owner", action="wait_for_interaction"
    )
    monkeypatch.setattr(
        main_module.terminal_service, "ensure_terminal_is_not_work_owned", lambda _terminal_id: None
    )
    monkeypatch.setattr(
        main_module.terminal_service,
        "send_special_key",
        Mock(side_effect=blocked),
    )

    with pytest.raises(HTTPException) as raised:
        await main_module.send_terminal_key("abcd1234", "Enter", ["cao:write"])

    assert raised.value.status_code == 409
    assert raised.value.detail["action"] == "wait_for_interaction"
    assert raised.value.detail["delivery_may_have_occurred"] is False
