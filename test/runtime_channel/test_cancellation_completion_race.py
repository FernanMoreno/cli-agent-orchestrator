"""Caller cancellation wins when send or result completes in the same turn."""

import asyncio

import pytest

from cli_agent_orchestrator.runtime_channel.protocol import CommandType, Result
from cli_agent_orchestrator.runtime_channel.registry import (
    RemoteOutcomeUnknownError,
    RuntimeConnection,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["send", "result"])
async def test_cancellation_at_completion_is_not_swallowed(boundary):
    sent = asyncio.Event()
    awaiting_result = asyncio.Event()

    async def send(_):
        sent.set()

    connection = RuntimeConnection("isolated", send)
    task = asyncio.create_task(
        connection.call(
            CommandType.LAUNCH,
            {},
            timeout=1,
            op_id="cancel-at-completion",
            on_sent=awaiting_result.set,
        )
    )
    if boundary == "send":
        await sent.wait()
    else:
        await awaiting_result.wait()
        assert connection.resolve(
            Result(op_id="cancel-at-completion", ok=True, payload={"launched": True})
        )
    assert task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled()
    assert not connection._send_lock.locked()
    assert not connection._pending


@pytest.mark.asyncio
async def test_expired_guard_does_not_start_a_send():
    sent = []

    async def send(frame):
        sent.append(frame)

    async def guard():
        await asyncio.sleep(0.02)

    connection = RuntimeConnection("isolated", send)
    with pytest.raises(RemoteOutcomeUnknownError):
        await connection.call(CommandType.INPUT, {}, timeout=0.001, before_send=guard)

    assert sent == []
    assert not connection._send_lock.locked()
    assert not connection._pending
