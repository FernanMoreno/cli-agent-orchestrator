import asyncio

import pytest
from sqlalchemy import create_engine

from cli_agent_orchestrator.runtime_channel.durable_connection import DurableRuntimeConnection
from cli_agent_orchestrator.runtime_channel.protocol import CommandType, Result, decode
from cli_agent_orchestrator.runtime_channel.registry import RemoteOutcomeUnknownError
from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore


def store(tmp_path):
    result = RemoteOperationStore(create_engine(f'sqlite:///{tmp_path / "journal"}'))
    result.create_schema()
    return result


@pytest.mark.asyncio
async def test_response_loss_and_reconnect_reuses_settled_proof_without_another_send(tmp_path):
    journal = store(tmp_path)
    frames = []
    epoch = journal.activate_runtime("worker", "inc-one")

    async def send(raw):
        frames.append(decode(raw))

    connection = DurableRuntimeConnection("worker", "inc-one", epoch, journal, send)
    connection.active = True
    with pytest.raises(RemoteOutcomeUnknownError):
        await connection.call(
            CommandType.INPUT,
            {"message": "task"},
            "1234abcd",
            timeout=1.0,
            op_id="once",
            generation="g1",
        )
    assert journal.get("once")["state"] == "reconcile"
    replacement = DurableRuntimeConnection(
        "worker", "inc-one", journal.activate_runtime("worker", "inc-one"), journal, send
    )
    replacement.active = True
    assert await replacement.resolve_durable(
        Result(op_id="once", ok=True, payload={"success": True})
    )
    assert await replacement.call(
        CommandType.INPUT, {"message": "task"}, "1234abcd", op_id="once", generation="g1"
    ) == {"success": True}
    assert len(frames) == 1


@pytest.mark.asyncio
async def test_cancel_before_channel_write_is_durably_unsent(tmp_path):
    journal = store(tmp_path)
    frames = []
    epoch = journal.activate_runtime("worker", "inc-one")

    async def send(raw):
        frames.append(raw)

    connection = DurableRuntimeConnection("worker", "inc-one", epoch, journal, send)
    connection.active = True
    await connection._send_lock.acquire()
    task = asyncio.create_task(connection.call(CommandType.LAUNCH, {}, timeout=1, op_id="unsent"))
    while journal.get("unsent") is None:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    connection._send_lock.release()
    assert not frames
    assert journal.get("unsent")["state"] == "cancelled"


@pytest.mark.asyncio
async def test_duplicate_concurrent_calls_one_physical_write(tmp_path):
    journal = store(tmp_path)
    frames = []
    epoch = journal.activate_runtime("worker", "inc-one")

    async def send(raw):
        command = decode(raw)
        frames.append(command)
        await connection.resolve_durable(
            Result(op_id=command.op_id, ok=True, payload={"success": True})
        )

    connection = DurableRuntimeConnection("worker", "inc-one", epoch, journal, send)
    connection.active = True
    results = await asyncio.gather(
        *(
            connection.call(CommandType.INPUT, {"message": "task"}, "1234abcd", op_id="once")
            for _ in range(3)
        )
    )
    assert results == [{"success": True}] * 3
    assert len(frames) == 1
