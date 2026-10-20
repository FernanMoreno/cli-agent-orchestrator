"""The WebSocket result and HTTP waiter can publish the same launch concurrently."""

import threading
from concurrent.futures import ThreadPoolExecutor
from test.postgres.fixtures import install_core_runtime_components, make_orm_engine

import pytest
from sqlalchemy import func, select

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.runtime_channel_schema import RemotePlacementModel
from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore
from cli_agent_orchestrator.services import remote_terminal_service as remote

pytestmark = pytest.mark.postgres


def test_concurrent_same_launch_publication_is_atomic_and_idempotent(postgres_targets, monkeypatch):
    target = postgres_targets.main
    install_core_runtime_components(target)
    engine = make_orm_engine(target)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "main_target", lambda: target)
    monkeypatch.setattr(remote.runtime_registry, "place", lambda *args: None)
    store = RemoteOperationStore(target)
    epoch = store.activate_runtime("runtime", "incarnation")
    store.prepare("launch", "runtime", "incarnation", "launch", {})
    assert store.claim("launch", connection_epoch=epoch)
    terminal = {
        "id": "abcdef01",
        "session_incarnation_id": "session-inc",
        "session_name": "cao-test",
        "name": "worker",
        "provider": "mock_cli",
    }
    assert store.settle(
        "launch", "runtime", "incarnation", {"ok": True, "payload": {"terminal": terminal}}
    )
    first_in_placement = threading.Event()
    release_first = threading.Event()
    place_calls = []

    def delayed_place(*args):
        place_calls.append(args)
        if len(place_calls) == 1:
            first_in_placement.set()
            assert release_first.wait(timeout=5)

    monkeypatch.setattr(remote.runtime_registry, "place", delayed_place)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(remote.record_placement, "runtime", "incarnation", "launch", terminal)
        assert first_in_placement.wait(timeout=5)
        second = pool.submit(remote.record_placement, "runtime", "incarnation", "launch", terminal)
        release_first.set()
        first.result(timeout=10)
        second.result(timeout=10)
    with engine.connect() as connection:
        assert (
            connection.execute(
                select(func.count()).select_from(database.TerminalModel)
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(select(func.count()).select_from(RemotePlacementModel)).scalar_one()
            == 1
        )
    assert store.get("launch")["state"] == "settled"
    assert len(place_calls) == 1
    engine.dispose()
