"""Process adapter cancellation must drain Docker work before port ownership ends."""

import asyncio
import threading
from test.services.test_delivery_admission import create, observe
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.backends.docker_backend import DockerWorkExecution
from cli_agent_orchestrator.services import work_admission, work_agent_step, work_launch
from cli_agent_orchestrator.services.work_admission import WorkAdmission


class _BlockingProcessPort:
    def __init__(self, resource_bound):
        self.owner = create()
        assert observe(self.owner, 2)
        self._resource_reservation = (
            self.owner.try_reserve("process-port", now=10.0, storage_slots=2).reservation
            if resource_bound
            else None
        )
        if resource_bound:
            assert self._resource_reservation is not None
            assert self._resource_reservation.start()
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.closed = False
        self.closed_while_executing = None

    def _storage_scope(self):
        from cli_agent_orchestrator.clients.storage_admission import physical_storage_scope

        return physical_storage_scope(self._resource_reservation, clock=lambda: 10.0)

    def _finish_resource(self):
        if self._resource_reservation is not None:
            self._resource_reservation.finish_after_storage()
            self._resource_reservation = None

    def execute_process(self, *_args):
        self.started.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("test process release barrier was not opened")
        self.closed_while_executing = self.closed
        self.finished.set()
        return DockerWorkExecution(0, b"", b"", True, True, True)

    def close(self):
        self.closed = True


class _InlineWorkService:
    def __init__(self, _repository, *, origins):
        self.origins = origins

    async def _send_committed_async(self, _sent, send, *, actor_id):
        assert actor_id == "test-operator"
        return await send()


@pytest.mark.parametrize("resource_bound", [False, True])
@pytest.mark.parametrize("operation", ["launch", "agent_step"])
@pytest.mark.asyncio
async def test_process_adapter_cancellation_joins_before_admission_closes_port(
    operation, resource_bound, monkeypatch
):
    port = _BlockingProcessPort(resource_bound)
    snapshot = SimpleNamespace(content=b"frozen context")

    if operation == "launch":
        monkeypatch.setattr(work_launch, "_validate_process_contract", lambda *_args: None)
        binding = SimpleNamespace(contract=object(), principal_id="test-operator")
        payload = SimpleNamespace(command_token="/frozen-worker", message="run")

        async def send(_binding, delivery_port):
            return await work_launch._launch_process(binding, payload, snapshot, delivery_port)

    else:
        monkeypatch.setattr(work_agent_step, "_validate_process_contract", lambda *_args: None)
        binding = SimpleNamespace(
            contract=SimpleNamespace(
                executable_identities=(SimpleNamespace(command_token="/frozen-worker"),)
            ),
            principal_id="test-operator",
            attempt_id="attempt-1",
            generation=1,
        )
        payload = SimpleNamespace(message="run")

        async def send(_binding, delivery_port):
            return await work_agent_step._deliver_process(binding, payload, snapshot, delivery_port)

    monkeypatch.setattr(work_admission, "WorkService", _InlineWorkService)
    admission = SimpleNamespace(repository=object(), origins=None)
    sent = {"attempts": [{"id": "attempt-1"}]}
    task = asyncio.create_task(
        WorkAdmission._send_prepared_dispatch(admission, (binding, port, sent), send)
    )

    try:
        assert await asyncio.wait_for(asyncio.to_thread(port.started.wait, 3), timeout=4)

        task.cancel()
        await asyncio.sleep(0)
        assert port.owner.snapshot(now=10.0).inflight == int(resource_bound)
        assert (
            not task.done() and not port.closed
        ), "adapter cancellation returned and closed the port while execute_process was active"

        task.cancel()
        await asyncio.sleep(0)
        assert port.owner.snapshot(now=10.0).inflight == int(resource_bound)
        assert (
            not task.done() and not port.closed
        ), "repeated cancellation discarded the owned process execution"

        port.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert port.finished.is_set()
        assert port.closed_while_executing is False
        assert port.closed is True
        assert port.owner.snapshot(now=10.0).inflight == 0
    finally:
        port.release.set()
        if not task.done():
            await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.wait_for(asyncio.to_thread(port.finished.wait, 3), timeout=4)
