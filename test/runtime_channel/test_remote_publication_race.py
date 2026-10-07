"""The WebSocket result and HTTP waiter can publish the same launch concurrently."""

import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, event, func, select

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.runtime_channel_schema import RemoteBase, RemotePlacementModel
from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore
from cli_agent_orchestrator.services import remote_terminal_service as remote


def test_concurrent_same_launch_publication_is_atomic_and_idempotent(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'central.sqlite'}", connect_args={"check_same_thread": False}
    )
    database.Base.metadata.create_all(engine)
    RemoteBase.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(remote.runtime_registry, "place", lambda *args: None)
    store = RemoteOperationStore(engine)
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
    first_insert = threading.Event()
    second_read = threading.Event()
    first_thread = None

    def interleave(conn, cursor, statement, params, context, executemany):
        nonlocal first_thread
        if statement.startswith("INSERT INTO terminals") and not first_insert.is_set():
            first_thread = threading.get_ident()
            first_insert.set()
            second_read.wait(timeout=0.5)
        elif (
            first_insert.is_set()
            and statement.startswith("SELECT remote_terminal_placements")
            and threading.get_ident() != first_thread
        ):
            second_read.set()

    event.listen(engine, "before_cursor_execute", interleave)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                remote.record_placement, "runtime", "incarnation", "launch", terminal
            )
            assert first_insert.wait(timeout=5)
            second = pool.submit(
                remote.record_placement, "runtime", "incarnation", "launch", terminal
            )
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
                connection.execute(
                    select(func.count()).select_from(RemotePlacementModel)
                ).scalar_one()
                == 1
            )
        assert store.get("launch")["state"] == "settled"
    finally:
        event.remove(engine, "before_cursor_execute", interleave)
        engine.dispose()
