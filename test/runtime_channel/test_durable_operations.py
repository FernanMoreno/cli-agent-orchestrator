"""Operation response loss must not create a second remote effect."""

import pytest
from sqlalchemy import create_engine

from cli_agent_orchestrator.runtime_channel.store import (
    RemoteIdentityConflict,
    RemoteOperationStore,
)


def open_store(path):
    store = RemoteOperationStore(create_engine(f"sqlite:///{path}"))
    store.create_schema()
    return store


def test_restart_retains_claim_and_prevents_redelivery(tmp_path):
    path = tmp_path / "operations.sqlite"
    store = open_store(path)
    store.activate_runtime("worker", "inc-one")
    store.prepare(
        "operation",
        "worker",
        "inc-one",
        "input",
        {"text": "task"},
        terminal_id="t1",
        generation="g1",
    )
    assert store.claim("operation")
    restarted = open_store(path)
    assert not restarted.claim("operation")
    assert restarted.get("operation")["state"] == "dispatching"


def test_idempotency_key_rejects_changed_material(tmp_path):
    store = open_store(tmp_path / "db")
    store.activate_runtime("worker", "inc-one")
    store.prepare("operation", "worker", "inc-one", "input", {"text": "task"})
    with pytest.raises(RemoteIdentityConflict):
        store.prepare("operation", "worker", "inc-one", "input", {"text": "different"})


def test_old_incarnation_cannot_settle_new_runtime(tmp_path):
    store = open_store(tmp_path / "db")
    store.activate_runtime("worker", "inc-one")
    store.prepare("operation", "worker", "inc-one", "input", {"text": "task"})
    assert store.claim("operation")
    store.activate_runtime("worker", "inc-two")
    assert not store.settle("operation", "worker", "inc-one", {"ok": True})
    assert store.get("operation")["state"] == "reconcile"


def test_late_result_settles_unknown_without_repeating_effect(tmp_path):
    store = open_store(tmp_path / "db")
    store.activate_runtime("worker", "inc-one")
    store.prepare("operation", "worker", "inc-one", "input", {"text": "task"})
    assert store.claim("operation")
    store.mark_unknown("operation")
    assert store.settle("operation", "worker", "inc-one", {"ok": True, "sequence": 4})
    assert store.get("operation")["result"] == {"ok": True, "sequence": 4}
    assert not store.claim("operation")
    assert not store.settle("operation", "worker", "inc-one", {"ok": True, "sequence": 5})
    assert store.get("operation")["result"]["sequence"] == 4


def test_replaced_connection_cannot_claim_a_queued_operation(tmp_path):
    store = open_store(tmp_path / "db")
    old_epoch = store.activate_runtime("worker", "inc-one")
    store.prepare("operation", "worker", "inc-one", "input", {"text": "task"})
    new_epoch = store.activate_runtime("worker", "inc-one")
    assert not store.claim("operation", connection_epoch=old_epoch)
    assert store.get("operation")["state"] == "prepared"
    assert store.claim("operation", connection_epoch=new_epoch)


def test_expired_unsent_operation_never_claims_or_runs(tmp_path):
    import time

    store = open_store(tmp_path / "db")
    epoch = store.activate_runtime("worker", "inc-one")
    store.prepare(
        "operation", "worker", "inc-one", "input", {"text": "task"}, deadline=time.time() - 1
    )
    assert not store.claim("operation", connection_epoch=epoch)
    assert store.cancel_unsent("operation")
    assert store.get("operation")["state"] == "cancelled"


def test_current_effect_fence_checks_deadline_and_connection_epoch(tmp_path):
    import time

    store = open_store(tmp_path / "db")
    old_epoch = store.activate_runtime("worker", "inc-one")
    store.prepare(
        "operation", "worker", "inc-one", "input", {"text": "task"}, deadline=time.time() + 60
    )
    assert store.claim("operation", connection_epoch=old_epoch)
    assert store.effect_is_current("operation", connection_epoch=old_epoch)
    store.activate_runtime("worker", "inc-one")
    assert not store.effect_is_current("operation", connection_epoch=old_epoch)


def test_concurrent_connection_activations_get_distinct_persisted_epochs(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    store = open_store(tmp_path / "db")
    store.activate_runtime("worker", "inc-one")
    start = threading.Barrier(8)

    def activate(_):
        start.wait(timeout=5)
        return store.activate_runtime("worker", "inc-one")

    with ThreadPoolExecutor(max_workers=8) as pool:
        epochs = list(pool.map(activate, range(8)))
    assert sorted(epochs) == list(range(2, 10))
