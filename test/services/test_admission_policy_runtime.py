"""Policy writes and seed snapshots retain physical work after waiter cancellation."""

import asyncio
from test.api.test_admission_policy_control import Repository
from test.services.test_inbox_admission_runtime import principal, runtime
from threading import Event

import pytest

from cli_agent_orchestrator.models.admission_policy_control import (
    AdmissionPolicyUpdate,
    AdmissionPolicyValues,
)

pytestmark = pytest.mark.offline_database


@pytest.mark.asyncio
async def test_cancelled_policy_waiter_keeps_owner_until_actual_write_and_shutdown():
    value, f, _ = runtime()
    repository = Repository()
    value._policy_repository = repository
    entered, release = Event(), Event()
    append = repository.append

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return append(*args, **kwargs)

    repository.append = held
    await value.start()
    request = asyncio.create_task(
        value.policy_update(
            principal=principal(),
            body=AdmissionPolicyUpdate(expected_revision=0, policy=AdmissionPolicyValues()),
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert f.monitor.refresh().reason == "authority_changing"
        stop = asyncio.create_task(value.stop())
        await asyncio.sleep(0)
        assert not stop.done() and not value.retired
        release.set()
        await asyncio.wait_for(stop, 10)
        assert len(repository.rows) == 1 and repository.rows[0].revision == 1
        assert value.retired
    finally:
        release.set()
        await value.stop()
