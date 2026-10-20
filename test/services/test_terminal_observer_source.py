"""Observer-only interaction sources are registered for native notices."""

import uuid
from test.postgres.interactions import postgres_interaction_store
from unittest.mock import MagicMock

import pytest

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.hermes import HermesProvider
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.interactions.capability import canonical_tool_fingerprint
from cli_agent_orchestrator.services.interactions.service import (
    InteractionForbidden,
    InteractionStale,
)

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_normal_hermes_terminal_creation_registers_only_an_observer_source(
    monkeypatch, tmp_path, postgres_interaction_store
):
    """A manual native notice must have a durable source after normal launch."""
    from cli_agent_orchestrator.services import terminal_service

    suffix = uuid.uuid4().hex[:10]
    terminal_id = "observer-" + suffix
    session_name = "cao-observer-" + suffix
    window_name = "hermes-" + suffix
    principal = auth._verified_principal(
        "https://test.issuer", "observer-owner-" + suffix, [auth.SCOPE_ADMIN], "jwt"
    )
    service = postgres_interaction_store.service
    provider = HermesProvider(
        terminal_id, session_name, window_name, agent_profile="hermes-observer"
    )

    async def fake_initialize(_provider):
        return True

    monkeypatch.setattr(HermesProvider, "initialize", fake_initialize)
    backend = MagicMock()
    backend.session_exists.return_value = False
    backend.supports_event_inbox.return_value = True

    monkeypatch.setattr(constants, "CAO_HOME_DIR", tmp_path / "cao-home")
    monkeypatch.setattr(terminal_service, "_interaction_service", lambda: service)
    monkeypatch.setattr(terminal_service, "generate_terminal_id", lambda: terminal_id)
    monkeypatch.setattr(terminal_service, "generate_session_name", lambda: session_name)
    monkeypatch.setattr(terminal_service, "generate_window_name", lambda _profile: window_name)
    monkeypatch.setattr(terminal_service, "get_backend", lambda: backend)
    monkeypatch.setattr(
        terminal_service,
        "load_agent_profile",
        lambda _name: AgentProfile(
            name="hermes-observer",
            description="Hermes observer test",
            mcpServers={"cao-mcp-server": {"command": "cao-mcp-server"}},
        ),
    )
    monkeypatch.setattr(
        terminal_service.provider_manager,
        "create_provider",
        lambda *_args, **_kwargs: provider,
    )
    monkeypatch.setattr(
        terminal_service.status_monitor, "get_status", lambda _id: TerminalStatus.IDLE
    )
    monkeypatch.setattr(terminal_service, "dispatch_plugin_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(terminal_service, "get_herdr_inbox_service", lambda: None)

    terminal = await terminal_service.create_terminal(
        "hermes",
        "hermes-observer",
        new_session=True,
        allowed_tools=["@cao-mcp-server"],
        creation_principal=principal,
    )

    assert terminal.id == terminal_id
    status = service.source_status(terminal_id, terminal.session_incarnation_id)
    assert status.state == "current"
    assert status.capability_available is False
    assert provider.interaction_capability_file is None
    assert not list((tmp_path / "cao-home").rglob("*.cap"))
    with pytest.raises(InteractionForbidden):
        service.authenticate_agent("not-a-registered-source-capability" * 2)

    # A normal Hermes launch with no overlay now reaches the same persisted
    # manual-only observation path used by StatusMonitor in production.
    import cli_agent_orchestrator.services.remote_terminal_service as remote
    from cli_agent_orchestrator.services.status_monitor import StatusMonitor

    monkeypatch.setattr(remote, "placement", lambda _terminal: None)
    monitor = StatusMonitor()
    private_viewport_text = "Sign in with private-code-839201"
    monitor._observe_native_interaction(
        terminal_id,
        provider,
        TerminalStatus.WAITING_USER_ANSWER,
        ["Hermes clarify", private_viewport_text],
    )
    page = service.list_metadata(principal, {"terminal_id": terminal_id})
    assert len(page.items) == 1
    question_id = page.items[0]["question_id"]
    detail = service.get_detail(principal, question_id)
    assert detail["origin"] == "native"
    assert detail["can_answer"] is False
    assert detail["lifecycle"] == "manual_only"
    assert detail["manual_reason"] == "native_dialog_manual_action"
    assert detail["prompt"] == "A native provider dialog requires manual action in its terminal."
    assert detail["options"] == []
    assert private_viewport_text not in repr(detail)

    history = service.history(principal, question_id)
    assert any(event.event_type == "interaction.native_manual_notice" for event in history)
    with service.repository.read_connection() as connection:
        source_events = connection.execute(
            "SELECT reason_code FROM interaction_events WHERE source_terminal_id=%s "
            "AND event_type='interaction.source_registered'",
            (terminal_id,),
        ).fetchall()
    assert [event["reason_code"] for event in source_events] == ["observer_source_registered"]


def _seed_terminal(terminal_id: str, session_name: str, incarnation_id: str) -> None:
    database.create_terminal(
        terminal_id,
        session_name,
        "observer-window",
        "hermes",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id=incarnation_id,
        new_session_incarnation=True,
    )


def test_observer_source_requires_exact_local_current_source_and_sealed_owner(
    postgres_interaction_store,
):
    suffix = uuid.uuid4().hex[:10]
    terminal_id = "observer-check-" + suffix
    session_name = "cao-observer-check-" + suffix
    incarnation_id = "observer-check-inc-" + suffix
    principal = auth._verified_principal(
        "https://test.issuer", "observer-check-owner-" + suffix, [auth.SCOPE_ADMIN], "jwt"
    )
    _seed_terminal(terminal_id, session_name, incarnation_id)
    service = postgres_interaction_store.service
    fingerprint, _ = canonical_tool_fingerprint(["@cao-mcp-server"])

    projection = service.register_observer_source(
        terminal_id=terminal_id,
        incarnation_id=incarnation_id,
        session_name=session_name,
        provider="hermes",
        principal=principal,
        allowed_tool_fingerprint=fingerprint,
    )

    assert projection["terminal_id"] == terminal_id
    assert projection["incarnation_id"] == incarnation_id
    assert not hasattr(projection, "token")
    assert service.source_status(terminal_id, incarnation_id).capability_available is False

    with pytest.raises(InteractionStale):
        service.register_observer_source(
            terminal_id=terminal_id,
            incarnation_id=incarnation_id,
            session_name=session_name + "-wrong",
            provider="hermes",
            principal=principal,
        )
    with pytest.raises(InteractionStale):
        service.register_observer_source(
            terminal_id=terminal_id,
            incarnation_id=incarnation_id + "-old",
            session_name=session_name,
            provider="hermes",
            principal=principal,
        )
    with pytest.raises(InteractionForbidden):
        service.register_observer_source(
            terminal_id=terminal_id,
            incarnation_id=incarnation_id,
            session_name=session_name,
            provider="hermes",
            principal=object(),
        )
    with pytest.raises(InteractionForbidden):
        service.register_observer_source(
            terminal_id=terminal_id,
            incarnation_id=incarnation_id,
            session_name=session_name,
            provider="hermes",
            principal=principal,
            allowed_tool_fingerprint="0" * 64,
        )


def test_observer_source_rejects_work_execution_and_durable_work_terminal(
    postgres_interaction_store,
):
    suffix = uuid.uuid4().hex[:10]
    terminal_id = "observer-work-" + suffix
    session_name = "cao-observer-work-" + suffix
    incarnation_id = "observer-work-inc-" + suffix
    _seed_terminal(terminal_id, session_name, incarnation_id)
    service = postgres_interaction_store.service
    fingerprint, _ = canonical_tool_fingerprint(["@cao-mcp-server"])

    with pytest.raises(InteractionForbidden):
        service.register_source(
            terminal_id=terminal_id,
            incarnation_id=incarnation_id,
            session_name=session_name,
            provider="hermes",
            execution_mode="work_v1",
            capability_available=False,
            _registration_purpose="observer",
        )

    now = 1_800_000_000.0
    with WorkRepository(postgres_interaction_store.target).transaction() as connection:
        connection.execute(
            "INSERT INTO work_jobs (id,project_id,principal_id,allowed_providers,grant_id,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            ("observer-job-" + suffix, "project", "principal", '["hermes"]', "grant", now),
        )
        connection.execute(
            "INSERT INTO work_items "
            "(id,job_id,operation_kind,idempotency_key,request_hash,contract_id,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                "observer-item-" + suffix,
                "observer-job-" + suffix,
                "launch",
                "observer-key-" + suffix,
                "hash",
                "contract",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO work_attempts "
            "(id,work_item_id,attempt_number,generation,provider,terminal_id,state,lease_expires_at,created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                "observer-attempt-" + suffix,
                "observer-item-" + suffix,
                1,
                1,
                "hermes",
                terminal_id,
                "running",
                now + 60,
                now,
            ),
        )

    with pytest.raises(InteractionForbidden):
        service.register_observer_source(
            terminal_id=terminal_id,
            incarnation_id=incarnation_id,
            session_name=session_name,
            provider="hermes",
            allowed_tool_fingerprint=fingerprint,
        )


def test_observer_registration_rolls_back_source_when_registration_event_fails(
    monkeypatch, postgres_interaction_store
):
    suffix = uuid.uuid4().hex[:10]
    terminal_id = "observer-atomic-" + suffix
    session_name = "cao-observer-atomic-" + suffix
    incarnation_id = "observer-atomic-inc-" + suffix
    _seed_terminal(terminal_id, session_name, incarnation_id)
    service = postgres_interaction_store.service

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("injected source event failure")

    monkeypatch.setattr(service.repository, "append_event", fail_event)
    with pytest.raises(RuntimeError, match="injected source event failure"):
        service.register_observer_source(
            terminal_id=terminal_id,
            incarnation_id=incarnation_id,
            session_name=session_name,
            provider="hermes",
        )

    assert service.source_status(terminal_id, incarnation_id).state == "unregistered"
