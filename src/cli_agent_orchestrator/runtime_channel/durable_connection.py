"""Transport calls fenced by persistent operation identity and connection epoch."""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager

from cli_agent_orchestrator.runtime_channel.registry import (
    COMMAND_TIMEOUT,
    RemoteCommandError,
    RemoteOutcomeUnknownError,
    RuntimeConnection,
    RuntimeUnavailableError,
    _acquire_within,
)


class DurableRuntimeConnection(RuntimeConnection):
    def __init__(
        self, runtime_id, incarnation_id, connection_epoch, store, send_text, close_socket=None
    ):
        super().__init__(runtime_id, send_text, close_socket)
        self.incarnation_id = incarnation_id
        self.connection_epoch = connection_epoch
        self.store = store
        self._operation_locks = {}

    @asynccontextmanager
    async def _operation(self, op_id, deadline):
        lock, owners = self._operation_locks.get(op_id, (asyncio.Lock(), 0))
        self._operation_locks[op_id] = (lock, owners + 1)
        acquired = False
        try:
            acquired = await _acquire_within(lock, max(0, deadline - time.time()))
            if not acquired:
                raise RemoteOutcomeUnknownError(
                    f"Remote operation {op_id} is still owned by another waiter"
                )
            yield
        finally:
            if acquired:
                lock.release()
            lock, owners = self._operation_locks[op_id]
            if owners == 1:
                self._operation_locks.pop(op_id)
            else:
                self._operation_locks[op_id] = (lock, owners - 1)

    @staticmethod
    def _result(result):
        if not result.get("ok"):
            from cli_agent_orchestrator.runtime_channel.errors import raise_error
            from cli_agent_orchestrator.runtime_channel.protocol import Result

            raise_error(Result.model_validate(result))
        return result.get("payload", {})

    async def call(
        self,
        command_type,
        payload,
        terminal_id=None,
        timeout=COMMAND_TIMEOUT,
        on_sent=None,
        *,
        op_id=None,
        generation=None,
    ):
        op_id = op_id or uuid.uuid4().hex
        deadline = time.time() + timeout
        # The same caller-controlled op_id never means a fresh delivery. A
        # reconnect may observe its result, but cannot restart a claimed effect.
        async with self._operation(op_id, deadline):
            previous = await asyncio.to_thread(self.store.get, op_id)
            preparation = asyncio.create_task(
                asyncio.to_thread(
                    self.store.prepare,
                    op_id,
                    self.runtime_id,
                    self.incarnation_id,
                    command_type.value,
                    payload,
                    terminal_id=terminal_id,
                    generation=generation,
                    deadline=None if previous else deadline,
                )
            )
            try:
                row = await asyncio.shield(preparation)
            except asyncio.CancelledError:
                # Cancellation may race the SQLite commit. Own that worker to
                # completion before declaring this never-claimed operation unsent.
                await preparation
                await asyncio.shield(asyncio.to_thread(self.store.cancel_unsent, op_id))
                raise
            if row["state"] == "settled":
                return self._result(row["result"])
            if row["state"] in {"dispatching", "reconcile"}:
                raise RemoteOutcomeUnknownError(f"Remote operation {op_id} awaits reconciliation")
            if row["state"] == "cancelled":
                raise RuntimeUnavailableError(f"Remote operation {op_id} was not dispatched")
            deadline = min(deadline, row["deadline"])
            claimed = False

            async def before_send():
                nonlocal claimed
                if not self.active or self.closed:
                    raise RuntimeUnavailableError("Runtime channel is unavailable before dispatch")
                claimed = await asyncio.to_thread(
                    self.store.claim, op_id, connection_epoch=self.connection_epoch
                )
                if not claimed:
                    raise RuntimeUnavailableError(
                        "Remote operation authority expired or changed before dispatch"
                    )

            try:
                return await super().call(
                    command_type,
                    payload,
                    terminal_id,
                    timeout=max(0, deadline - time.time()),
                    on_sent=on_sent,
                    op_id=op_id,
                    before_send=before_send,
                    frame_metadata={
                        "runtime_incarnation_id": self.incarnation_id,
                        "connection_epoch": self.connection_epoch,
                        "deadline": row["deadline"],
                        "generation": generation,
                    },
                )
            except BaseException:
                if claimed:
                    await asyncio.shield(asyncio.to_thread(self.store.mark_unknown, op_id))
                else:
                    await asyncio.shield(asyncio.to_thread(self.store.cancel_unsent, op_id))
                raise

    async def resolve_durable(self, result):
        # Called only by the authenticated owner of this incarnation. Late
        # results remain useful after a wait timeout or channel replacement.
        changed = await asyncio.to_thread(
            self.store.settle,
            result.op_id,
            self.runtime_id,
            self.incarnation_id,
            result.model_dump(mode="json"),
        )
        row = await asyncio.to_thread(self.store.get, result.op_id)
        if row is None or row["state"] != "settled":
            return False
        if row["runtime_id"] != self.runtime_id or row["incarnation_id"] != self.incarnation_id:
            return False
        # Wake only with the immutable first settled result, never a duplicate.
        from cli_agent_orchestrator.runtime_channel.protocol import Result

        canonical = Result.model_validate(row["result"])
        super().resolve(canonical)
        return changed
