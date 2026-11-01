"""Offline checks: async Work delivery runs every SQL segment off the event loop."""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager

import pytest

from cli_agent_orchestrator.services.work_service import (
    DeliveryObservation,
    DeliveryUncertain,
    WorkService,
)


class _Recorder:
    def __init__(self, loop_thread: int):
        self.loop_thread = loop_thread
        self.events: list[tuple[str, int]] = []
        self.lock = threading.Lock()

    def record(self, name: str) -> None:
        with self.lock:
            self.events.append((name, threading.get_ident()))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def assert_off_loop(self) -> None:
        assert self.events
        for name, thread in self.events:
            assert thread != self.loop_thread, name


class _FakeRepository:
    def __init__(self, recorder: _Recorder, *, register_barrier=None):
        self.recorder = recorder
        self.register_barrier = register_barrier

    @contextmanager
    def transaction(self):
        self.recorder.record("transaction")
        yield object()
        self.recorder.record("commit")

    def _verify(self, connection):
        self.recorder.record("verify")

    def _register_writer_effect(self, connection, *, writer_id):
        if self.register_barrier is not None:
            started, release = self.register_barrier
            started.set()
            assert release.wait(5)
        self.recorder.record("register")

    def _release_writer_effect(self, connection, *, writer_id):
        self.recorder.record("release")


def _service(recorder: _Recorder, **kwargs) -> WorkService:
    service = WorkService.__new__(WorkService)
    service.repository = _FakeRepository(recorder, **kwargs)

    def reconcile(sent, *, actor_id):
        recorder.record("reconcile")
        return sent

    def record_delivery(sent, observation, *, actor_id):
        recorder.record("record_delivery")
        return {"recorded": True}

    service._reconcile = reconcile
    service._record_delivery = record_delivery
    return service


pytestmark = pytest.mark.offline_database

_SENT = {"id": "work-1", "attempts": [{"id": "attempt-1"}]}


def test_success_runs_all_sql_off_loop():
    async def scenario():
        recorder = _Recorder(threading.get_ident())
        service = _service(recorder)

        async def send():
            recorder.events.append(("send", -1))
            return DeliveryObservation()

        result = await service._send_committed_async(_SENT, send, actor_id="a")
        assert result == {"recorded": True}
        names = recorder.names()
        assert names.index("register") < names.index("send") < names.index("release")
        assert names[-1] == "record_delivery"
        recorder.events.remove(("send", -1))
        recorder.assert_off_loop()

    asyncio.run(scenario())


def test_error_releases_and_reconciles_off_loop():
    async def scenario():
        recorder = _Recorder(threading.get_ident())
        service = _service(recorder)

        async def send():
            raise RuntimeError("boom")

        with pytest.raises(DeliveryUncertain):
            await service._send_committed_async(_SENT, send, actor_id="a")
        names = recorder.names()
        assert "release" in names and names[-1] == "reconcile"
        assert "record_delivery" not in names
        recorder.assert_off_loop()

    asyncio.run(scenario())


def test_cancelled_send_releases_and_reconciles_off_loop():
    async def scenario():
        recorder = _Recorder(threading.get_ident())
        service = _service(recorder)
        entered = asyncio.Event()

        async def send():
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(service._send_committed_async(_SENT, send, actor_id="a"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        names = recorder.names()
        assert "release" in names and names[-1] == "reconcile"
        recorder.assert_off_loop()

    asyncio.run(scenario())


def test_cancel_during_register_keeps_worker_and_releases_writer():
    async def scenario():
        recorder = _Recorder(threading.get_ident())
        started, release = threading.Event(), threading.Event()
        service = _service(recorder, register_barrier=(started, release))
        sent_calls = []

        async def send():
            sent_calls.append(True)
            return DeliveryObservation()

        task = asyncio.create_task(service._send_committed_async(_SENT, send, actor_id="a"))
        assert await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)
        task.cancel()
        await asyncio.sleep(0.05)
        # The waiter is cancelled but the SQL worker still owns the transaction.
        assert not task.done()
        assert "register" not in recorder.names()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        names = recorder.names()
        assert not sent_calls
        assert names.index("register") < names.index("release")
        assert names[-1] == "reconcile"
        recorder.assert_off_loop()

    asyncio.run(scenario())


class _Loan:
    def finish(self) -> bool:
        return True


class _Owner:
    storage_process_current = True

    def __init__(self):
        self.acquired: list[int] = []

    def try_acquire_storage_connection(self, *, now):
        self.acquired.append(threading.get_ident())
        return _Loan()


def test_logical_scope_denies_loop_sql_and_hands_physical_scope_to_worker():
    from cli_agent_orchestrator.clients.storage_admission import (
        StorageAdmissionUnavailable,
        acquire_storage_connection,
        logical_storage_scope,
    )
    from cli_agent_orchestrator.services.work_blocking import run_owned_blocking

    async def scenario():
        owner = _Owner()
        loop_thread = threading.get_ident()
        with logical_storage_scope(owner, clock=lambda: 0.0):
            loan = await run_owned_blocking(acquire_storage_connection)
            assert isinstance(loan, _Loan)
            assert owner.acquired and owner.acquired[0] != loop_thread
            with pytest.raises(StorageAdmissionUnavailable):
                acquire_storage_connection()
        assert owner.acquired == owner.acquired[:1]

    asyncio.run(scenario())


def test_deliver_registered_loads_off_loop_and_sends_on_loop():
    from cli_agent_orchestrator.services.work_admission import WorkAdmission

    async def scenario():
        loop_thread = threading.get_ident()
        seen = {}

        class _Adapter:
            async def send(self, binding, payload, snapshot, port):
                seen["send"] = threading.get_ident()
                return (binding, payload, snapshot, port)

        class _Admission:
            def _load_registered_delivery(self, binding):
                seen["load"] = threading.get_ident()
                return _Adapter(), "payload", "snapshot"

        result = await WorkAdmission._deliver_registered(_Admission(), "binding", "port")
        assert result == ("binding", "payload", "snapshot", "port")
        assert seen["load"] != loop_thread
        assert seen["send"] == loop_thread

    asyncio.run(scenario())
