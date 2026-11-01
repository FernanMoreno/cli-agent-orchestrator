"""Provider I/O shares one worker mechanism and preserves fresh KAS guards."""

import asyncio
import threading
from test.services.test_delivery_admission import create, observe, reserve
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.clients import storage_admission as storage
from cli_agent_orchestrator.providers.base import BaseProvider

pytestmark = pytest.mark.offline_database


@pytest.mark.asyncio
async def test_common_provider_worker_carries_exact_credit_and_arguments():
    owner = create()
    assert observe(owner, 2)
    lease = reserve(owner, "provider-worker")
    assert lease.start()
    loop_thread = threading.get_ident()
    calls = []

    def effect(value, *, option):
        assert threading.get_ident() != loop_thread
        assert storage._current_storage_owner() is lease
        loan = storage.acquire_storage_connection()
        assert loan is not None
        try:
            calls.append((value, option))
            return "complete"
        finally:
            assert loan.finish()

    try:
        with storage.logical_storage_scope(lease, clock=lambda: 10):
            assert (
                await BaseProvider._run_blocking(object(), effect, "input", option=7) == "complete"
            )
        assert calls == [("input", 7)]
        assert owner.snapshot(now=10).storage_connection_loans == 0
    finally:
        lease.finish_after_storage()


@pytest.mark.asyncio
async def test_common_provider_worker_refuses_unstarted_lease_before_effect():
    owner = create()
    assert observe(owner, 2)
    lease = reserve(owner, "unstarted-provider")
    calls = []
    try:
        with storage.logical_storage_scope(lease, clock=lambda: 10):
            with pytest.raises(storage.StorageAdmissionUnavailable):
                await BaseProvider._run_blocking(object(), lambda: calls.append("effect"))
        assert calls == []
    finally:
        assert lease.cancel_before_start()


@pytest.mark.asyncio
@pytest.mark.parametrize("policy_changes", [False, True])
async def test_kas_rechecks_metadata_in_owned_worker_before_launch(monkeypatch, policy_changes):
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.models.kiro_engine import KiroEngine
    from cli_agent_orchestrator.providers import kiro_cli
    from cli_agent_orchestrator.services import kiro_profiles
    from cli_agent_orchestrator.services import status_monitor as status_module

    owner = create()
    assert observe(owner, 2)
    lease = reserve(owner, "kas-provider")
    assert lease.start()
    loop_thread = threading.get_ident()
    reads, guards, effects = [], [], []

    def metadata(terminal_id):
        assert threading.get_ident() != loop_thread
        loan = storage.acquire_storage_connection()
        assert loan is not None
        try:
            reads.append(terminal_id)
            return {"revision": len(reads)}
        finally:
            assert loan.finish()

    def guard(terminal_id, current):
        assert threading.get_ident() != loop_thread
        guards.append(current["revision"])
        if policy_changes and current["revision"] == 2:
            raise RuntimeError("policy changed after shell wait")
        return SimpleNamespace(model=None, allowedTools=None)

    def send(*args, **kwargs):
        assert threading.get_ident() != loop_thread
        loan = storage.acquire_storage_connection()
        assert loan is not None
        try:
            effects.append((args, kwargs))
        finally:
            assert loan.finish()

    async def shell(*args, **kwargs):
        assert threading.get_ident() == loop_thread
        assert guards == [1]
        await asyncio.sleep(0)
        return True

    original = database.get_terminal_metadata
    with monkeypatch.context() as patch:
        patch.setattr(database, "get_terminal_metadata", metadata)
        patch.setattr(kiro_profiles, "guard_existing_kas_runtime", guard)
        patch.setattr(kiro_cli, "get_server_settings", lambda: {"provider_init_timeout": 30})
        patch.setattr(kiro_cli, "wait_for_shell", shell)
        patch.setattr(
            kiro_cli,
            "get_backend",
            lambda: SimpleNamespace(get_pane_current_command=lambda *_: "bash", send_keys=send),
        )
        patch.setattr(
            status_module, "status_monitor", SimpleNamespace(notify_input_sent=lambda *_: None)
        )
        provider = kiro_cli.KiroCliProvider(
            "ab12cd34", "session", "worker", "developer", engine=KiroEngine.KAS
        )
        patch.setattr(provider, "_get_profile_model", lambda: None)
        patch.setattr(provider, "_wait_ready_accepting_trust_dialog", AsyncMock(return_value=True))
        try:
            with storage.logical_storage_scope(lease, clock=lambda: 10):
                if policy_changes:
                    with pytest.raises(RuntimeError, match="policy changed"):
                        await provider.initialize()
                else:
                    assert await provider.initialize()
            assert reads == ["ab12cd34", "ab12cd34"]
            assert guards == [1, 2]
            assert len(effects) == int(not policy_changes)
        finally:
            lease.finish_after_storage()
    assert database.get_terminal_metadata is original
