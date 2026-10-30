"""Installed OpenCode profiles must be frozen before any terminal allocation."""

import base64
import json
from dataclasses import FrozenInstanceError
from test.providers.test_opencode_profile_initialization import launch
from test.services.test_terminal_service_inbox_registration import create_mocks
from unittest.mock import AsyncMock, MagicMock

import pytest

from cli_agent_orchestrator.providers import opencode_cli
from cli_agent_orchestrator.services import terminal_service
from cli_agent_orchestrator.utils import opencode_config
from cli_agent_orchestrator.utils.opencode_policy import NATIVE_POLICY_PREFIX

pytestmark = pytest.mark.offline_database


class AllocationReached(RuntimeError):
    pass


@pytest.fixture
def early(monkeypatch, tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    monkeypatch.setattr(opencode_cli, "OPENCODE_AGENTS_DIR", agents)
    monkeypatch.setattr(terminal_service, "get_max_terminals", lambda: None)
    monkeypatch.setattr(terminal_service, "managed_launch_required", lambda: False)
    monkeypatch.setattr(
        terminal_service, "load_agent_profile", MagicMock(side_effect=FileNotFoundError)
    )
    monkeypatch.setattr(opencode_config, "read_config", lambda: {})
    allocation = MagicMock(side_effect=AllocationReached("identity allocation reached"))
    monkeypatch.setattr(terminal_service, "generate_terminal_id", allocation)
    side_effects = {}
    for name in ("plan_native_child", "db_create_terminal", "get_backend"):
        side_effects[name] = MagicMock(side_effect=AssertionError("unexpected " + name))
        monkeypatch.setattr(terminal_service, name, side_effects[name])
    return agents, allocation, side_effects


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize("caller", [None, "supervisor"])
@pytest.mark.parametrize("fault", ["missing", "yaml", "mode", "pending", "encoding"])
async def test_invalid_native_profile_refuses_before_allocation(early, fault, caller, deferred):
    agents, allocation, side_effects = early
    path = agents / "reviewer.md"
    if fault == "yaml":
        path.write_text("---\ndescription: [\n---\nPrivate prompt.")
    elif fault == "mode":
        path.write_text("---\nmode: invalid\n---\nPrivate prompt.")
    elif fault == "pending":
        data = {
            "version": 1,
            "agent_id": "reviewer",
            "state": "pending",
            "profiles": {"cao": "interaction"},
            "binding": None,
        }
        path.write_text(
            NATIVE_POLICY_PREFIX
            + base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
            + " -->\nPrivate prompt."
        )
    elif fault == "encoding":
        path.write_bytes(b"\xffPrivate prompt.")
    with pytest.raises(ValueError, match="native profile") as error:
        await terminal_service.create_terminal(
            "opencode_cli", "reviewer", new_session=True, caller_id=caller, defer_init=deferred
        )
    assert "Private prompt" not in str(error.value)
    allocation.assert_not_called()
    for effect in side_effects.values():
        effect.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["opencode_cli", "codex", "mock_cli"])
async def test_valid_native_profile_and_other_providers_reach_allocation(early, provider):
    agents, allocation, _ = early
    if provider == "opencode_cli":
        (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.")
    with pytest.raises(AllocationReached):
        await terminal_service.create_terminal(provider, "reviewer", new_session=True)
    allocation.assert_called_once()


def test_native_snapshot_is_immutable_and_rejects_cross_agent_reuse(early):
    agents, _, _ = early
    (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.")
    snapshot = opencode_cli.OpenCodeCliProvider.load_native_profile("reviewer")
    with pytest.raises(FrozenInstanceError):
        snapshot.text = "Changed"
    worker = opencode_cli.OpenCodeCliProvider("test5678", "session", "worker", "worker")
    with pytest.raises(ValueError, match="identity"):
        worker.prepare_native_profile(snapshot)


@pytest.mark.asyncio
async def test_prepared_snapshot_survives_profile_replacement_at_launch(launch, monkeypatch):
    instance, _, _ = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: 1)
    monkeypatch.setattr(opencode_config, "read_config", lambda: {})
    snapshot = opencode_cli.OpenCodeCliProvider.load_native_profile("reviewer")
    instance.prepare_native_profile(snapshot)
    (opencode_cli.OPENCODE_AGENTS_DIR / "reviewer.md").write_text("Changed after validation.")
    assert await instance.initialize()
    config = json.loads((instance._v1_config_dir / "opencode.json").read_text())
    assert config["agent"]["reviewer"]["prompt"] == "Review."


def test_provider_manager_transfers_snapshot_before_publication(early, monkeypatch):
    from cli_agent_orchestrator.providers.manager import ProviderManager
    from cli_agent_orchestrator.services import remote_terminal_service

    monkeypatch.setattr(remote_terminal_service, "placement", lambda _: None)

    agents, _, _ = early
    (agents / "reviewer.md").write_text("Review.")
    snapshot = opencode_cli.OpenCodeCliProvider.load_native_profile("reviewer")
    manager = ProviderManager()
    instance = manager.create_provider(
        "opencode_cli",
        "test1234",
        "session",
        "window",
        "reviewer",
        opencode_native_profile=snapshot,
    )
    assert instance._prepared_native_profile is snapshot
    assert manager.get_provider("test1234") is instance
    with pytest.raises(ValueError):
        manager.create_provider(
            "opencode_cli",
            "test5678",
            "session",
            "window",
            "worker",
            opencode_native_profile=snapshot,
        )
    assert "test5678" not in manager._providers
    with pytest.raises(ValueError):
        manager.create_provider(
            "mock_cli",
            "test9999",
            "session",
            "window",
            "reviewer",
            opencode_native_profile=snapshot,
        )
    assert "test9999" not in manager._providers


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred", [False, True])
async def test_creation_passes_frozen_selection_through_real_manager(
    create_mocks, tmp_path, monkeypatch, deferred
):
    from cli_agent_orchestrator.agent_plugins import projection
    from cli_agent_orchestrator.providers.manager import ProviderManager

    agents = tmp_path / "agents"
    agents.mkdir()
    path = agents / "reviewer.md"
    path.write_text("Review.")
    monkeypatch.setattr(opencode_cli, "OPENCODE_AGENTS_DIR", agents)
    monkeypatch.setattr(terminal_service, "get_max_terminals", lambda: None)
    monkeypatch.setattr(
        terminal_service, "_resolve_existing_session_incarnation_locked", lambda _: "incarnation"
    )
    monkeypatch.setattr(terminal_service, "get_session_env", lambda _: {})
    monkeypatch.setattr(terminal_service, "has_unreclaimed_window_label", lambda *_args: False)
    monkeypatch.setattr(terminal_service, "transition_native_child", MagicMock())
    monkeypatch.setattr(terminal_service, "update_terminal_provider_variant", MagicMock())
    monkeypatch.setattr(terminal_service, "status_monitor", MagicMock())
    monkeypatch.setattr(terminal_service, "_schedule_deferred_init", MagicMock())
    monkeypatch.setattr(projection, "reconcile_native_skill_projection", lambda: None)

    def replace_after_preparation(*args, **kwargs):
        path.write_text("Replacement after allocation.")
        return "reviewer-window"

    create_mocks.backend.create_window.side_effect = replace_after_preparation
    # This remains an offline controller/profile composition test. Model the
    # actual registry write before asking the real manager to witness it.
    from cli_agent_orchestrator.providers import manager as manager_module

    registration = {}
    reads = []

    def register(key, session, window, provider, *_args, **kwargs):
        registration[key] = {
            "provider": provider,
            "tmux_session": session,
            "tmux_window": window,
            "session_incarnation_id": kwargs["session_incarnation_id"],
        }

    def read(key):
        reads.append(key)
        return registration.get(key)

    create_mocks.db_create_terminal.side_effect = register
    monkeypatch.setattr(manager_module, "get_terminal_metadata", read)
    manager = ProviderManager()
    created = []

    def create(*args, **kwargs):
        instance = manager.create_provider(*args, **kwargs)
        instance._validate_native_profile()
        assert instance._native_agent_source[1].endswith("Review.")
        instance.initialize = AsyncMock(return_value=True)
        created.append(instance)
        return instance

    create_mocks.provider_manager.create_provider.side_effect = create
    result = await terminal_service.create_terminal(
        "opencode_cli", "reviewer", session_name="session", allowed_tools=[], defer_init=deferred
    )
    assert result.id == "term-abc123"
    assert path.read_text() == "Replacement after allocation."
    assert len(created) == 1 and created[0]._prepared_native_profile is not None
    assert created[0].initialize.await_count == (0 if deferred else 1)

    assert reads == [result.id]
    assert registration[result.id]["tmux_window"] == "reviewer-window"


@pytest.mark.asyncio
async def test_ready_binding_is_rejected_before_allocation(early, monkeypatch):
    from test.providers.test_opencode_native_policy import ready_agent

    _, allocation, _ = early
    config, _, _ = ready_agent(monkeypatch)
    config["mcp"]["servers"].clear()
    with pytest.raises(ValueError, match="native MCP policy"):
        await terminal_service.create_terminal("opencode_cli", "reviewer", new_session=True)
    allocation.assert_not_called()


@pytest.mark.asyncio
async def test_prepared_managed_snapshot_rechecks_configuration_at_launch(launch, monkeypatch):
    from test.providers.test_opencode_native_policy import ready_agent

    instance, backend, _ = launch
    config, path, _ = ready_agent(monkeypatch)
    instance.prepare_native_profile(
        opencode_cli.OpenCodeCliProvider.load_native_profile("reviewer")
    )
    path.write_text("Replacement after preparation.")
    config["mcp"]["servers"].clear()
    assert instance.interaction_source_configured(None) is False
    with pytest.raises(ValueError, match="native MCP policy"):
        await instance.initialize()
    backend.send_keys.assert_not_called()


@pytest.mark.asyncio
async def test_idempotent_reuse_does_not_reload_or_allocate_native_profile(early, monkeypatch):
    from cli_agent_orchestrator.clients.database import IdempotencyRecord

    _, allocation, _ = early
    loader = MagicMock(side_effect=AssertionError("reuse must not reload a profile"))
    monkeypatch.setattr(opencode_cli.OpenCodeCliProvider, "load_native_profile", loader)
    monkeypatch.setattr(terminal_service, "_request_fingerprint", lambda *a, **kw: "same")
    monkeypatch.setattr(
        terminal_service, "get_idempotency_record", lambda _: IdempotencyRecord("existing", "same")
    )
    monkeypatch.setattr(
        terminal_service,
        "get_terminal",
        lambda _: {
            "id": "existing",
            "name": "worker",
            "provider": "opencode_cli",
            "session_name": "session",
            "agent_profile": "reviewer",
        },
    )
    result = await terminal_service.create_terminal(
        "opencode_cli", "reviewer", new_session=True, idempotency_key="retry"
    )
    assert result.id == "existing"
    loader.assert_not_called()
    allocation.assert_not_called()
