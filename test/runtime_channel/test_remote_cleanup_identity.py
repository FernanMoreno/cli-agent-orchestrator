"""Exact old remote teardown must preserve unrelated current central identity."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.runtime_channel_schema import RemoteBase, RemotePlacementModel
from cli_agent_orchestrator.runtime_channel import server


@pytest.fixture
def central(tmp_path, monkeypatch):
    path = tmp_path / "central.sqlite"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    database.Base.metadata.create_all(engine)
    RemoteBase.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(constants, "DATABASE_FILE", path)
    monkeypatch.setattr(constants, "LOCK_DIR", tmp_path / "locks")
    # Only the physical old-runtime result is simulated. The ordinary Work guard
    # reads this actual isolated DB and remains intact; no Work schema is minted.
    effect = Mock(return_value={"deleted": True})
    forget = Mock()
    monkeypatch.setattr(server.runtime_registry, "call_blocking", effect)
    monkeypatch.setattr(server.runtime_registry, "forget", forget)
    yield engine, effect, forget
    engine.dispose()


def seed_terminal(engine, incarnation="local-inc"):
    with engine.begin() as connection:
        connection.execute(
            database.TerminalModel.__table__.insert().values(
                id="abcdef01",
                tmux_session="current-session",
                tmux_window="current-worker",
                provider="mock_cli",
                session_incarnation_id=incarnation,
            )
        )


async def compensate_old_launch():
    operation = {
        "op_id": "old-launch",
        "result": {
            "payload": {"terminal": {"id": "abcdef01", "session_incarnation_id": "old-session"}}
        },
    }
    await server.compensate_launch(
        SimpleNamespace(runtime_id="runtime", incarnation_id="runtime-inc"), operation
    )


@pytest.mark.asyncio
async def test_old_compensation_preserves_same_id_unrelated_local_terminal(central):
    engine, effect, forget = central
    seed_terminal(engine)
    await compensate_old_launch()
    with engine.connect() as connection:
        row = (
            connection.execute(
                select(database.TerminalModel.__table__).where(
                    database.TerminalModel.id == "abcdef01"
                )
            )
            .mappings()
            .one()
        )
        assert row["session_incarnation_id"] == "local-inc"
    assert effect.call_count == 1
    assert effect.call_args.kwargs["op_id"] == "cleanup-old-launch"
    forget.assert_not_called()


@pytest.mark.asyncio
async def test_old_compensation_preserves_displaced_remote_placement_and_projection(central):
    engine, effect, forget = central
    seed_terminal(engine, "replacement-session")
    identity = json.dumps({"id": "abcdef01", "session_incarnation_id": "replacement-session"})
    with engine.begin() as connection:
        connection.execute(
            RemotePlacementModel.__table__.insert().values(
                terminal_id="abcdef01",
                runtime_id="runtime",
                incarnation_id="runtime-inc",
                launch_op_id="replacement-launch",
                session_incarnation_id="replacement-session",
                identity_json=identity,
                projection_json=identity,
                revision=42,
            )
        )
    await compensate_old_launch()
    with engine.connect() as connection:
        row = connection.execute(select(RemotePlacementModel.__table__)).mappings().one()
        assert row["launch_op_id"] == "replacement-launch"
        assert row["session_incarnation_id"] == "replacement-session"
        assert row["projection_json"] == identity and row["revision"] == 42
        terminal = connection.execute(select(database.TerminalModel.__table__)).mappings().one()
        assert terminal["session_incarnation_id"] == "replacement-session"
    assert effect.call_count == 1
    forget.assert_not_called()


def test_publisher_cannot_restore_volatile_mapping_after_exact_cleanup(central, monkeypatch):
    """A delayed launch notification and exact teardown share writer ordering."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from cli_agent_orchestrator.runtime_channel.registry import RuntimeRegistry
    from cli_agent_orchestrator.runtime_channel.store import RemoteOperationStore
    from cli_agent_orchestrator.services import remote_terminal_service as remote

    engine, _, _ = central
    registry = RuntimeRegistry()
    monkeypatch.setattr(remote, "runtime_registry", registry)
    store = RemoteOperationStore(engine)
    epoch = store.activate_runtime("runtime", "runtime-inc")
    store.prepare("old-launch", "runtime", "runtime-inc", "launch", {})
    assert store.claim("old-launch", connection_epoch=epoch)
    terminal = {
        "id": "abcdef01",
        "session_incarnation_id": "old-session",
        "session_name": "old-native-session",
        "name": "old-worker",
        "provider": "mock_cli",
    }
    assert store.settle(
        "old-launch", "runtime", "runtime-inc", {"ok": True, "payload": {"terminal": terminal}}
    )
    notification_started = threading.Event()
    cleanup_finished = threading.Event()
    original_place = registry.place

    def delayed_place(*args):
        notification_started.set()
        # Before the fix, deletion can commit while the notification is paused.
        # Under writer ownership, cleanup waits until this notification finishes.
        cleanup_finished.wait(timeout=0.5)
        original_place(*args)

    monkeypatch.setattr(registry, "place", delayed_place)

    def cleanup():
        removed = remote.remove_confirmed(
            "abcdef01",
            runtime_id="runtime",
            incarnation_id="runtime-inc",
            launch_op_id="old-launch",
            session_incarnation_id="old-session",
        )
        cleanup_finished.set()
        return removed

    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(
            remote.record_placement, "runtime", "runtime-inc", "old-launch", terminal
        )
        assert notification_started.wait(timeout=5)
        deletion = pool.submit(cleanup)
        publication.result(timeout=10)
        assert deletion.result(timeout=10) is True
    assert remote.placement("abcdef01") is None
    assert registry.is_placed("abcdef01", "runtime") is False
