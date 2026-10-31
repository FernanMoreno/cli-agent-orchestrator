"""A storage refusal cannot become an optional memory fallback."""

from test.services.test_delivery_admission import create, observe, reserve

import pytest

from cli_agent_orchestrator.clients.storage_admission import (
    StorageAdmissionUnavailable,
    note_storage_denial,
    physical_storage_scope,
)
from cli_agent_orchestrator.services import terminal_service as module


@pytest.mark.parametrize("wrapped", [False, True])
def test_optional_memory_storage_denial_preserves_retry(monkeypatch, wrapped):
    owner = create()
    observe(owner, 1)
    lease = reserve(owner, "memory-storage-retry")
    assert lease.start()
    calls = []

    def context(*args, **kwargs):
        calls.append(True)
        note_storage_denial()
        if wrapped:
            raise RuntimeError("controlled optional wrapper")
        raise StorageAdmissionUnavailable()

    monkeypatch.setattr(module, "remote_memory_url", lambda: None)
    monkeypatch.setattr(module.MemoryService, "get_curated_memory_context", context)
    try:
        with physical_storage_scope(lease, clock=lambda: 10.0):
            with pytest.raises(StorageAdmissionUnavailable):
                module.inject_memory_context("message", "memory-storage-retry")
        assert "memory-storage-retry" not in module._memory_injected_terminals
        monkeypatch.setattr(
            module.MemoryService, "get_curated_memory_context", lambda *a, **k: "context"
        )
        with physical_storage_scope(lease, clock=lambda: 10.0):
            assert (
                module.inject_memory_context("message", "memory-storage-retry")
                == "context\n\nmessage"
            )
        assert calls == [True]
    finally:
        with module._memory_injected_lock:
            module._memory_injected_terminals.discard("memory-storage-retry")
        lease.finish_after_storage()
