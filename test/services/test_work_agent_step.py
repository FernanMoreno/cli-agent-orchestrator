"""Managed agent-step delivery through the durable WorkAdmission boundary."""

import importlib
import json
from test.integration.test_work_dispatch import AdmissionOnlyBackend, admit, context  # noqa: F401
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.backends.registry import get_backend
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.mock_cli import MockCliProvider
from cli_agent_orchestrator.services import settings_service, terminal_service
from cli_agent_orchestrator.services.work_admission import WorkAdmission
from cli_agent_orchestrator.services.work_service import DeliveryUncertain


class AgentStepBackend(AdmissionOnlyBackend):
    def __init__(self):
        super().__init__()
        self.sessions = {}

    def session_exists(self, name):
        return name in self.sessions

    def create_session(
        self, name, window, terminal_id, directory, extra_env=None, *, work_safe=False
    ):
        assert work_safe is True
        self.sessions[name] = (window, terminal_id, directory)
        return window

    def supports_event_inbox(self):
        return True


def _adapter():
    """Resolve the production registry entry; a test double cannot replace it."""
    module = importlib.import_module("cli_agent_orchestrator.services.work_agent_step")
    return module.agent_step_adapter()


@pytest.fixture
def agent_step_context(context, isolated_memory_db, monkeypatch):
    providers, scoped_backends = {}, []

    class UnscopedBackend:
        def __getattr__(self, name):
            raise AssertionError("managed agent-step escaped its protected backend scope")

    monkeypatch.setattr("cli_agent_orchestrator.backends.registry._backend", UnscopedBackend())
    backend = AgentStepBackend()
    context.backend = backend
    context.after_startup = lambda: None

    class Provider(MockCliProvider):
        async def initialize(self):
            scoped_backends.append(get_backend())
            get_backend().send_keys(self.session_name, self.window_name, "mock_cli startup")
            self._initialized = True
            context.after_startup()
            return True

    def create_provider(provider, terminal_id, session, window, profile, allowed_tools, **kwargs):
        instance = Provider(terminal_id, session, window, allowed_tools)
        providers[terminal_id] = instance
        return instance

    monkeypatch.setattr(terminal_service.provider_manager, "create_provider", create_provider)
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", providers.get)
    monkeypatch.setattr(terminal_service, "load_agent_profile", lambda name: None)
    monkeypatch.setattr(terminal_service, "get_herdr_inbox_service", lambda: None)
    monkeypatch.setattr(terminal_service, "get_max_terminals", lambda: None)
    monkeypatch.setattr(
        terminal_service,
        "status_monitor",
        SimpleNamespace(
            get_status=lambda terminal_id: TerminalStatus.IDLE,
            notify_input_sent=lambda *args, **kwargs: None,
            clear_rolling_buffer=lambda *args, **kwargs: None,
            clear_terminal=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(terminal_service, "_memory_injected_terminals", set())
    context.service = WorkAdmission(
        context.repo,
        backends={"test": backend},
        delivery_adapters={("agent_step", 1): _adapter()},
    )
    context.scoped_backends = scoped_backends
    context.providers = providers
    return context


def agent_step_envelope(**overrides):
    from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope

    payload = {
        "terminal_id": "a1b2c3d4",
        "agent_profile": "developer",
        "message": "implement the frozen task",
    }
    payload.update(overrides)
    return WorkDeliveryEnvelope(
        operation_kind="agent_step",
        adapter_version=1,
        payload_json=json.dumps(payload),
    )


@pytest.mark.asyncio
async def test_registered_agent_step_uses_real_managed_branch_and_frozen_context(
    agent_step_context,
):
    context = agent_step_context
    work = admit(
        context,
        "managed-agent-step",
        operation="agent_step",
        delivery=agent_step_envelope(),
    )

    result = await context.service.dispatch_registered_next()

    metadata = database.get_terminal_metadata("a1b2c3d4")
    assert result["id"] == work["id"]
    assert result["attempts"][0]["terminal_id"] == "a1b2c3d4"
    assert metadata["provider"] == "mock_cli"
    assert metadata["working_directory"] == str(context.root)
    assert context.providers["a1b2c3d4"]._allowed_tools == []
    assert metadata["caller_id"] is None
    assert context.backend.effects[0][2] == "mock_cli startup"
    assert "frozen context managed-agent-step" in context.backend.effects[1][2]
    assert "implement the frozen task" in context.backend.effects[1][2]
    assert result["attempts"][0]["state"] == "sent"
    assert await context.service.dispatch_registered_next() is None

    with pytest.raises(ValueError):
        context.scoped_backends[0].send_keys("late", "late", "duplicate delivery")
    assert len(context.backend.effects) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "snapshot_text", "max_content_bytes", "redacted", "truncated"),
    [
        ("step-unicode", "contexto durable: π\nlínea dos", 65536, False, False),
        (
            "step-redacted",
            "Authorization: Bearer a-very-long-token-that-must-never-be-delivered",
            65536,
            True,
            False,
        ),
        ("step-truncated", "é" * 8, 7, False, True),
        ("step-empty", "", 65536, False, False),
    ],
)
async def test_restarted_registered_agent_step_delivers_exact_persisted_snapshot_without_live_memory(
    agent_step_context,
    monkeypatch,
    key,
    snapshot_text,
    max_content_bytes,
    redacted,
    truncated,
):
    """Changing managed agent-step to use live memory instead of its snapshot breaks this."""
    context = agent_step_context
    work = admit(
        context,
        key,
        operation="agent_step",
        delivery=agent_step_envelope(),
        snapshot_content=snapshot_text,
        snapshot_max_content_bytes=max_content_bytes,
    )
    with context.repo.connection() as connection:
        persisted = connection.execute(
            "SELECT content,redacted,truncated FROM work_delegation_snapshots"
        ).fetchone()
    assert persisted["redacted"] == int(redacted)
    assert persisted["truncated"] == int(truncated)
    if redacted:
        assert b"[REDACTED:bearer_token]" in persisted["content"]
        assert (
            b"a-very-long-token-that-must-never-be-delivered"
            not in persisted["content"]
        )
    if truncated:
        assert persisted["content"] == "é".encode() * 3

    monkeypatch.setattr(settings_service, "is_memory_enabled", lambda: True)
    monkeypatch.setattr(
        terminal_service,
        "MemoryService",
        lambda: pytest.fail(
            "managed agent-step must not resolve live memory after restart"
        ),
    )
    context.service = WorkAdmission(
        WorkRepository(context.repo.path),
        backends={"test": context.backend},
        delivery_adapters={("agent_step", 1): _adapter()},
    )

    result = await context.service.dispatch_registered_next()

    assert result["id"] == work["id"]
    expected = "implement the frozen task"
    if persisted["content"]:
        expected = persisted["content"].decode("utf-8") + "\n\n" + expected
    assert [effect[2] for effect in context.backend.effects] == [
        "mock_cli startup",
        expected,
    ]
    assert await context.service.dispatch_registered_next() is None


def test_forged_caller_id_is_rejected_before_admission_effects(agent_step_context):
    context = agent_step_context

    with pytest.raises(ValueError):
        admit(
            context,
            "forged-caller",
            operation="agent_step",
            delivery=agent_step_envelope(caller_id="forged-terminal"),
        )

    assert context.backend.effects == []
    with context.repo.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_revocation_after_provider_startup_blocks_task_send_and_receipt(
    agent_step_context, monkeypatch
):
    context = agent_step_context
    monkeypatch.setattr(MockCliProvider, "requires_turn_receipt", True)

    def revoke_after_startup():
        context.control.revoke(
            context.actor,
            grant_id=context.jobs[0][1].id,
            expected_grant_revision=1,
            reason="revoked after managed agent-step startup",
        )

    context.after_startup = revoke_after_startup
    work = admit(
        context,
        "revoke-agent-step",
        operation="agent_step",
        delivery=agent_step_envelope(),
    )

    with pytest.raises(DeliveryUncertain):
        await context.service.dispatch_registered_next()

    assert [effect[2] for effect in context.backend.effects] == ["mock_cli startup"]
    assert database.get_terminal_turn_receipt("a1b2c3d4") is None
    assert context.repo.get_work(work["id"])["state"] == "reconcile"
