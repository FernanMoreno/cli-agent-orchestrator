"""Readiness polling preserves the admitted operation's physical storage owner."""

from test.services.test_delivery_admission import create, observe, reserve

import pytest

from cli_agent_orchestrator.clients import storage_admission as storage
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.utils.terminal import wait_until_status

pytestmark = pytest.mark.offline_database


@pytest.mark.asyncio
async def test_initialization_probe_borrows_in_an_explicit_owned_worker():
    owner = create()
    assert observe(owner, 1)
    lease = reserve(owner, "owned-readiness-probe")
    assert lease.start()
    seen = []

    def probe():
        loan = storage.acquire_storage_connection()
        assert loan is not None
        try:
            assert owner.snapshot(now=10.0).storage_connection_loans == 1
            seen.append(True)
            return TerminalStatus.IDLE
        finally:
            loan.finish()

    try:
        with storage.physical_storage_scope(lease, clock=lambda: 10.0):
            assert await wait_until_status(
                "owned-readiness-probe", TerminalStatus.IDLE, initialization_probe=probe
            )
            assert seen == [True]
            assert not storage.storage_admission_failed()
    finally:
        lease.finish_after_storage()
    assert owner.snapshot(now=10.0).inflight == 0
