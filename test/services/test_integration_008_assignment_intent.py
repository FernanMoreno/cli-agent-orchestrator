"""A whole assignment, not just terminal creation, owns response-loss retries."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.terminal import Terminal


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'assign.sqlite'}", connect_args={"check_same_thread": False}
    )
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine))
    db.create_terminal("1234abcd", "cao-parent", "0", "codex", "developer")
    yield engine
    engine.dispose()


@pytest.mark.asyncio
async def test_response_loss_concurrent_retry_and_restart_one_assignment(store):
    from cli_agent_orchestrator.services import assignment_service as service

    terminal = Terminal(
        id="abcd1234", name="developer", provider="codex", session_name="cao-parent"
    )

    async def create(**kwargs):
        await asyncio.sleep(0.02)
        assert kwargs["initial_message"] == "exact work"
        assert kwargs["defer_init"] is True and kwargs["caller_id"] == "1234abcd"
        return terminal

    with (
        patch.object(service.terminal_service, "ensure_terminal_is_not_work_owned"),
        patch.object(service, "resolve_provider", return_value="codex"),
        patch.object(
            service.terminal_service, "create_terminal", new=AsyncMock(side_effect=create)
        ) as launch,
    ):
        payload = service.AssignmentRequest(
            operation_key="retry-key-1", agent_profile="developer", message="exact work"
        )
        first = await service.assign("1234abcd", payload, owner="operator", registry=None)
        # Pretend first HTTP body was lost; concurrent retries and a new service caller inspect it.
        rows = await asyncio.gather(
            *(
                service.assign("1234abcd", payload, owner="operator", registry=None)
                for _ in range(4)
            )
        )
        assert all(row == first for row in rows)
        assert launch.await_count == 1
        assert first["terminal_id"] == "abcd1234" and first["state"] == "submitted"
        changed = service.AssignmentRequest(
            operation_key="retry-key-1", agent_profile="developer", message="different work"
        )
        with pytest.raises(service.AssignmentConflict):
            await service.assign("1234abcd", changed, owner="operator", registry=None)
        with pytest.raises(service.AssignmentConflict):
            service.inspect_assignment(first["assignment_id"], owner="other")


@pytest.mark.asyncio
async def test_accepted_creation_failure_remains_uncertain_and_never_reexecutes(store):
    from cli_agent_orchestrator.services import assignment_service as service

    payload = service.AssignmentRequest(
        operation_key="retry-key-2", agent_profile="developer", message="exact work"
    )
    with (
        patch.object(service.terminal_service, "ensure_terminal_is_not_work_owned"),
        patch.object(service, "resolve_provider", return_value="codex"),
        patch.object(
            service.terminal_service,
            "create_terminal",
            new=AsyncMock(side_effect=RuntimeError("response unknown")),
        ) as launch,
    ):
        first = await service.assign("1234abcd", payload, owner="operator", registry=None)
        second = await service.assign("1234abcd", payload, owner="operator", registry=None)
    assert first == second and first["state"] == "reconcile"
    assert first["success"] is False and launch.await_count == 1


@pytest.mark.asyncio
async def test_disconnect_keeps_owned_admission_and_work_refusal_has_zero_launch(store):
    from cli_agent_orchestrator.services import assignment_service as service

    started, release = asyncio.Event(), asyncio.Event()

    async def create(**kwargs):
        started.set()
        await release.wait()
        return Terminal(
            id="abcd1234", name="developer", provider="codex", session_name="cao-parent"
        )

    payload = service.AssignmentRequest(
        operation_key="retry-key-3", agent_profile="developer", message="exact work"
    )
    with (
        patch.object(service.terminal_service, "ensure_terminal_is_not_work_owned"),
        patch.object(service, "resolve_provider", return_value="codex"),
        patch.object(
            service.terminal_service, "create_terminal", new=AsyncMock(side_effect=create)
        ) as launch,
    ):
        caller = asyncio.create_task(
            service.assign("1234abcd", payload, owner="operator", registry=None)
        )
        await started.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        release.set()
        await asyncio.gather(*tuple(service._jobs))
        row = await service.assign("1234abcd", payload, owner="operator", registry=None)
        assert row["state"] == "submitted" and launch.await_count == 1
    with (
        patch.object(
            service.terminal_service,
            "ensure_terminal_is_not_work_owned",
            side_effect=RuntimeError("owned by Work"),
        ),
        patch.object(service.terminal_service, "create_terminal", new_callable=AsyncMock) as launch,
    ):
        with pytest.raises(RuntimeError, match="owned by Work"):
            await service.assign(
                "1234abcd",
                service.AssignmentRequest(
                    operation_key="retry-key-4", agent_profile="developer", message="exact work"
                ),
                owner="operator",
                registry=None,
            )
        launch.assert_not_awaited()
