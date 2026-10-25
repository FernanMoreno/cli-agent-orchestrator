"""Remote terminal placement publishes atomically in the PostgreSQL main store."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.runtime_channel_schema import RemotePlacementModel
from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore
from cli_agent_orchestrator.services import remote_terminal_service as remote

pytestmark = pytest.mark.postgres


def _seed_settled_launch(target, terminal):
    store = RemoteOperationStore(target)
    epoch = store.activate_runtime("publication-runtime", "publication-incarnation")
    store.prepare(
        "publication-launch", "publication-runtime", "publication-incarnation", "launch", {}
    )
    assert store.claim("publication-launch", connection_epoch=epoch)
    assert store.settle(
        "publication-launch",
        "publication-runtime",
        "publication-incarnation",
        {"ok": True, "payload": {"terminal": terminal}},
    )
    return store


def test_postgres_placement_is_atomic_and_exactly_removable(
    postgres_core, postgres_runtime_channel, monkeypatch
):
    target = postgres_runtime_channel
    assert target.identity == postgres_core.identity
    runtime_registry = remote.runtime_registry
    monkeypatch.setattr(runtime_registry, "place", Mock())
    monkeypatch.setattr(runtime_registry, "forget", Mock())
    terminal = {
        "id": "remote-terminal-1",
        "session_incarnation_id": "session-inc-1",
        "session_name": "cao-review",
        "name": "worker",
        "provider": "codex",
        "allowed_tools": ["read_file"],
    }
    store = _seed_settled_launch(target, terminal)

    remote.record_placement(
        "publication-runtime", "publication-incarnation", "publication-launch", terminal
    )
    remote.record_placement(
        "publication-runtime", "publication-incarnation", "publication-launch", terminal
    )
    placement = remote.placement(terminal["id"])
    assert placement is not None
    assert placement["runtime_id"] == "publication-runtime"
    assert json.loads(placement["identity_json"]) == terminal
    with database.engine.connect() as connection:
        local = (
            connection.execute(
                select(database.TerminalModel.__table__).where(
                    database.TerminalModel.id == terminal["id"]
                )
            )
            .mappings()
            .one()
        )
        remote_count = connection.execute(
            select(RemotePlacementModel.__table__.c.terminal_id).where(
                RemotePlacementModel.terminal_id == terminal["id"]
            )
        ).all()
    assert local["tmux_session"] == "remote-publication-runtime-cao-review"
    assert len(remote_count) == 1
    assert store.get("publication-launch")["state"] == "settled"

    assert remote.remove_confirmed(
        terminal["id"],
        runtime_id="publication-runtime",
        incarnation_id="publication-incarnation",
        launch_op_id="publication-launch",
        session_incarnation_id="session-inc-1",
    )
    assert remote.placement(terminal["id"]) is None
    assert not remote.remove_confirmed(
        terminal["id"],
        runtime_id="publication-runtime",
        incarnation_id="publication-incarnation",
        launch_op_id="publication-launch",
        session_incarnation_id="session-inc-1",
    )
    runtime_registry.forget.assert_called_once_with(terminal["id"])


def test_postgres_same_launch_publications_share_transaction_lock(
    postgres_core, postgres_runtime_channel, monkeypatch
):
    target = postgres_runtime_channel
    monkeypatch.setattr(remote.runtime_registry, "place", Mock())
    terminal = {
        "id": "remote-terminal-race",
        "session_incarnation_id": "session-inc-race",
        "session_name": "cao-race",
        "name": "worker",
        "provider": "codex",
    }
    _seed_settled_launch(target, terminal)
    barrier = threading.Barrier(2)

    def publish():
        barrier.wait(timeout=5)
        remote.record_placement(
            "publication-runtime",
            "publication-incarnation",
            "publication-launch",
            terminal,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish), pool.submit(publish)]
        for future in futures:
            future.result(timeout=10)

    assert remote.placement(terminal["id"]) is not None
    with database.engine.connect() as connection:
        assert connection.execute(
            select(database.TerminalModel.id).where(database.TerminalModel.id == terminal["id"])
        ).all() == [(terminal["id"],)]


@pytest.mark.parametrize("plan_first", [False, True])
def test_remote_publication_and_local_child_plan_cannot_share_identity(
    postgres_core, postgres_runtime_channel, monkeypatch, plan_first
):
    from cli_agent_orchestrator.runtime_channel.store import RemoteIdentityConflict

    target = postgres_runtime_channel
    monkeypatch.setattr(remote.runtime_registry, "place", Mock())
    terminal = {
        "id": "remote-child-claim",
        "session_incarnation_id": "remote-claim-inc",
        "session_name": "remote-claim-session",
        "name": "worker",
        "provider": "codex",
        "agent_profile": "developer",
    }
    _seed_settled_launch(target, terminal)

    def plan():
        return database.plan_native_child(
            parent_terminal_id="parent",
            terminal_id=terminal["id"],
            provider="codex",
            agent_profile="developer",
            lease_seconds=60,
        )

    def publish():
        remote.record_placement(
            "publication-runtime", "publication-incarnation", "publication-launch", terminal
        )

    if plan_first:
        child = plan()
        with pytest.raises(RemoteIdentityConflict):
            publish()
        assert remote.placement(terminal["id"]) is None
        assert database.get_terminal_metadata(terminal["id"]) is None
        assert database.get_native_child_for_terminal(terminal["id"]) == child
        remote.runtime_registry.place.assert_not_called()
    else:
        publish()
        before = remote.placement(terminal["id"])
        with pytest.raises(database.NativeChildIdentityConflict):
            plan()
        assert database.get_native_child_for_terminal(terminal["id"]) is None
        assert remote.placement(terminal["id"]) == before
        # Exact replay is still idempotent, without creating another placement.
        publish()
        assert remote.placement(terminal["id"]) == before
