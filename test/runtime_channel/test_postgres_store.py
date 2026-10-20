"""Native PostgreSQL durability contracts for the runtime operation journal."""

from __future__ import annotations

import pytest

from cli_agent_orchestrator.runtime_channel.store import (
    RemoteIdentityConflict,
    RemoteOperationStore,
)

pytestmark = pytest.mark.postgres


def test_postgres_runtime_epoch_claim_settlement_and_revisions(postgres_runtime_channel):
    target = postgres_runtime_channel
    store = RemoteOperationStore(target)
    assert store.activate_runtime("runtime-a", "inc-a") == 1

    prepared = store.prepare(
        "op-a",
        "runtime-a",
        "inc-a",
        "inspect",
        {"label": "ñ"},
        terminal_id="terminal-a",
        generation="turn-a",
    )
    assert prepared["state"] == "prepared"
    assert prepared["request"]["payload"] == {"label": "ñ"}
    assert (
        store.prepare(
            "op-a",
            "runtime-a",
            "inc-a",
            "inspect",
            {"label": "ñ"},
            terminal_id="terminal-a",
            generation="turn-a",
        )
        == prepared
    )
    with pytest.raises(RemoteIdentityConflict):
        store.prepare("op-a", "runtime-a", "inc-a", "inspect", {"label": "other"})

    assert store.claim("op-a", connection_epoch=1)
    assert store.effect_is_current("op-a", connection_epoch=1)
    store.request_cancellation("op-a")
    store.request_cancellation("op-a")
    assert store.cancellation_requested("op-a")
    store.mark_unknown("op-a")
    assert store.settle("op-a", "runtime-a", "inc-a", {"ok": True, "value": 4})
    assert store.get("op-a")["result"] == {"ok": True, "value": 4}
    assert store.unsettled_results("runtime-a", "inc-a") == [{"ok": True, "value": 4}]

    assert store.next_revision("terminal-a", "inc-a") == 1
    assert store.next_revision("terminal-a", "inc-a") == 2
    assert store.get_runtime("runtime-a") == {
        "runtime_id": "runtime-a",
        "incarnation_id": "inc-a",
        "connection_epoch": 1,
    }
    assert store.activate_runtime("runtime-a", "inc-b") == 2
    assert store.get_runtime("runtime-a")["incarnation_id"] == "inc-b"


def test_postgres_runtime_epoch_does_not_roll_back(postgres_runtime_channel):
    store = RemoteOperationStore(postgres_runtime_channel)
    store.admit_epoch("runtime-b", "inc-b", 5)
    store.admit_epoch("runtime-b", "inc-b", 5)
    with pytest.raises(RemoteIdentityConflict):
        store.admit_epoch("runtime-b", "inc-b", 4)
    store.admit_epoch("runtime-b", "inc-c", 1)
    assert store.get_runtime("runtime-b") == {
        "runtime_id": "runtime-b",
        "incarnation_id": "inc-c",
        "connection_epoch": 1,
    }


def test_postgres_counters_exceed_32bit_signed_range(postgres_runtime_channel):
    from cli_agent_orchestrator.clients.postgres import connect

    target = postgres_runtime_channel
    store = RemoteOperationStore(target)
    store.activate_runtime("wide-counter", "inc")
    with connect(target) as connection:
        with connection.transaction():
            connection.execute(
                "UPDATE remote_runtime_instances SET connection_epoch=%s WHERE runtime_id=%s",
                (2**31 - 1, "wide-counter"),
            )
    epoch = store.activate_runtime("wide-counter", "inc")
    assert epoch == 2**31

    store.prepare("wide-op", "wide-counter", "inc", "inspect", {})
    assert store.claim("wide-op", connection_epoch=epoch)
    assert store.get("wide-op")["connection_epoch"] == epoch

    store.next_revision("wide-terminal", "inc")
    with connect(target) as connection:
        with connection.transaction():
            connection.execute(
                "UPDATE remote_observation_revisions SET revision=%s WHERE terminal_id=%s",
                (2**31 - 1, "wide-terminal"),
            )
    assert store.next_revision("wide-terminal", "inc") == 2**31
