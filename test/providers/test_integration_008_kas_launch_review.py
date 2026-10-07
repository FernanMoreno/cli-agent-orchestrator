"""Actual KAS initialization consumes only its admitted private launch grant."""

import json
import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.models.kiro_launch import KiroLaunchRefusedError
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers import kiro_cli
from cli_agent_orchestrator.services import kiro_profiles
from cli_agent_orchestrator.services.status_monitor import status_monitor
from cli_agent_orchestrator.utils.kiro_launch_guard import kas_policy_digest


@pytest.fixture
def launch(monkeypatch, tmp_path):
    monkeypatch.setattr(constants, "ENABLE_KAS_LAUNCH", True)
    monkeypatch.setattr(kiro_profiles, "KIRO_AGENTS_DIR", tmp_path)
    profile = AgentProfile(
        name="review",
        description="Private grant",
        engine="kas",
        allowedTools=[],
        model="frozen-model",
    )
    artifact = kiro_profiles.install_runtime_kas_profile("1234abcd", profile)
    metadata = dict(
        agent_profile="review",
        allowed_tools=[],
        engine="kas",
        kiro_policy_digest=kas_policy_digest(profile),
    )
    monkeypatch.setattr(database, "get_terminal_metadata", lambda _: metadata)
    backend = Mock()
    backend.get_pane_current_command.return_value = "bash"
    monkeypatch.setattr(kiro_cli, "get_backend", lambda: backend)
    monkeypatch.setattr(kiro_cli, "wait_for_shell", AsyncMock(return_value=True))
    monkeypatch.setattr(kiro_cli, "wait_until_status", AsyncMock(return_value=True))
    monkeypatch.setattr(status_monitor, "get_status", lambda _: TerminalStatus.IDLE)
    monkeypatch.setattr(status_monitor, "notify_input_sent", Mock())

    def forbidden(*args, **kwargs):
        pytest.fail("Initialization consulted mutable operator profile")

    monkeypatch.setattr(kiro_cli, "load_agent_profile", forbidden)
    provider = kiro_cli.KiroCliProvider(
        "1234abcd", "cao-review", "%1", "review", allowed_tools=[], engine="kas"
    )
    return provider, backend, artifact, metadata


@pytest.mark.asyncio
async def test_fresh_kas_initialize_uses_exact_private_grant(launch):
    provider, backend, artifact, _ = launch
    assert await provider.initialize() is True
    backend.send_keys.assert_called_once()
    command = shlex.split(backend.send_keys.call_args.args[2])
    assert command == [
        "kiro-cli",
        "--v3",
        "chat",
        "--model",
        "frozen-model",
        "--agent",
        "cao-runtime-1234abcd",
    ]
    assert json.loads(artifact.read_text())["tools"] == []
    assert artifact.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["missing-proof", "missing-artifact", "artifact-drift", "public-artifact"]
)
async def test_invalid_kas_proof_has_no_backend_launch_effect(launch, failure):
    provider, backend, artifact, metadata = launch
    if failure == "missing-proof":
        metadata["kiro_policy_digest"] = None
    elif failure == "missing-artifact":
        artifact.unlink()
    elif failure == "artifact-drift":
        artifact.write_text(
            artifact.read_text().replace('"tools": []', '"tools": ["execute_bash"]')
        )
    else:
        artifact.chmod(0o644)
    with pytest.raises(KiroLaunchRefusedError):
        await provider.initialize()
    backend.send_keys.assert_not_called()
    backend.get_pane_current_command.assert_not_called()


@pytest.mark.asyncio
async def test_kas_artifact_drift_during_shell_wait_refuses_launch(launch, monkeypatch):
    provider, backend, artifact, _ = launch

    async def shell_wait(*args, **kwargs):
        artifact.write_text(
            artifact.read_text().replace('"tools": []', '"tools": ["execute_bash"]')
        )
        return True

    monkeypatch.setattr(kiro_cli, "wait_for_shell", shell_wait)
    with pytest.raises(KiroLaunchRefusedError):
        await provider.initialize()
    backend.send_keys.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("_model", "unadmitted-model"), ("_allowed_tools", ["*"])])
async def test_kas_launch_cannot_override_frozen_identity(launch, field, value):
    provider, backend, _, _ = launch
    setattr(provider, field, value)
    with pytest.raises(KiroLaunchRefusedError):
        await provider.initialize()
    backend.send_keys.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", [False, True])
async def test_kas_never_broadens_grant_by_accepting_trust_all_consent(launch, monkeypatch, drift):
    provider, backend, artifact, _ = launch
    backend.get_history.return_value = (
        Path(__file__).parent / "fixtures" / "kiro_trust_all_tools_dialog_active.txt"
    ).read_text()

    async def status_wait(*args, **kwargs):
        if drift:
            artifact.write_text(
                artifact.read_text().replace('"tools": []', '"tools": ["execute_bash"]')
            )
        return True

    monkeypatch.setattr(kiro_cli, "wait_until_status", status_wait)
    monkeypatch.setattr(status_monitor, "get_status", lambda _: TerminalStatus.WAITING_USER_ANSWER)
    with pytest.raises(KiroLaunchRefusedError):
        await provider._wait_ready_accepting_trust_dialog()
    backend.send_special_key.assert_not_called()


def test_retention_keeps_kas_proof_when_native_stop_is_unknown(launch, monkeypatch):
    from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult
    from cli_agent_orchestrator.services import cleanup_service, terminal_service

    _, backend, artifact, metadata = launch
    metadata.update(id="1234abcd", tmux_session="cao-review", tmux_window="%1")
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [SimpleNamespace(id="1234abcd")]
    db.query.return_value.filter.return_value.delete.return_value = 0
    session = MagicMock()
    session.__enter__.return_value = db
    monkeypatch.setattr(cleanup_service, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        terminal_service, "should_retain_deferred_failure_tombstone", lambda _: False
    )
    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", lambda _: None)
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda _: metadata)
    monkeypatch.setattr(terminal_service, "get_backend", lambda: backend)
    backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.UNKNOWN
    )
    stop_reader = Mock(return_value=True)
    clear_status = Mock()
    cleanup_provider = Mock(return_value=True)
    monkeypatch.setattr(cleanup_service.fifo_manager, "stop_reader", stop_reader)
    monkeypatch.setattr(cleanup_service.status_monitor, "clear_terminal", clear_status)
    monkeypatch.setattr(cleanup_service.provider_manager, "cleanup_provider", cleanup_provider)
    delete_row = Mock(return_value=True)
    monkeypatch.setattr(terminal_service, "delete_terminal_row", delete_row)
    monkeypatch.setattr(cleanup_service, "TERMINAL_LOG_DIR", SimpleNamespace(exists=lambda: False))
    monkeypatch.setattr(cleanup_service, "LOG_DIR", SimpleNamespace(exists=lambda: False))
    monkeypatch.setattr(cleanup_service, "delete_old_handoff_results", lambda _: 0)
    # Avoid a workspace lock file; the dispatch fence is not the behavior under test.
    import contextlib

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.work_terminal.terminal_dispatch_lock",
        lambda *args: contextlib.nullcontext(),
    )
    cleanup_service.cleanup_old_data()
    delete_row.assert_not_called()
    stop_reader.assert_not_called()
    clear_status.assert_not_called()
    cleanup_provider.assert_not_called()
    assert artifact.exists()


@pytest.mark.parametrize("sweep_succeeds", [True, False])
def test_session_bulk_fallback_releases_kas_proof_only_after_success(
    launch, monkeypatch, sweep_succeeds
):
    import contextlib

    from cli_agent_orchestrator.services import session_service, terminal_service

    _, backend, artifact, metadata = launch
    metadata.update(
        id="1234abcd",
        tmux_session="cao-review",
        tmux_window="%1",
        session_incarnation_id="review-incarnation",
    )
    monkeypatch.setattr(session_service, "get_backend", lambda: backend)
    backend.session_exists_strict.return_value = False
    monkeypatch.setattr(session_service, "list_terminals_by_session", lambda _: [metadata])
    monkeypatch.setattr(session_service, "get_session_incarnation", lambda _: "review-incarnation")
    monkeypatch.setattr(session_service, "_collect_deferred_failures", lambda _: {})
    monkeypatch.setattr(
        session_service, "session_lifecycle_lock", lambda _: contextlib.nullcontext()
    )
    monkeypatch.setattr(
        session_service, "terminal_dispatch_lock", lambda *args: contextlib.nullcontext()
    )
    monkeypatch.setattr(terminal_service, "ensure_terminal_is_not_work_owned", lambda _: None)
    monkeypatch.setattr(terminal_service, "capture_terminal_snapshot", lambda _: metadata)
    monkeypatch.setattr(terminal_service, "dismantle_terminal_runtime", Mock(return_value=True))
    monkeypatch.setattr(
        terminal_service,
        "delete_terminal_row",
        Mock(side_effect=RuntimeError("isolated row-delete outage")),
    )

    def sweep(ids):
        assert ids == ["1234abcd"]
        if not sweep_succeeds:
            raise RuntimeError("isolated sweep outage")

    monkeypatch.setattr(session_service, "delete_terminals_by_ids", sweep)
    monkeypatch.setattr(session_service, "clear_session_env", lambda _: None)
    monkeypatch.setattr(session_service, "dispatch_plugin_event", Mock())
    session_service.delete_session("cao-review")
    assert artifact.exists() is not sweep_succeeds
    source = kiro_profiles._runtime_kas_source_path(artifact.parent, "1234abcd")
    assert source.exists() is not sweep_succeeds


@pytest.mark.asyncio
async def test_actual_terminal_creation_persists_closed_kas_launch_proof(monkeypatch, tmp_path):
    import contextlib

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from cli_agent_orchestrator.providers.manager import ProviderManager
    from cli_agent_orchestrator.services import terminal_service

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    database.Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(constants, "ENABLE_KAS_LAUNCH", True)
    monkeypatch.setattr(kiro_profiles, "KIRO_AGENTS_DIR", tmp_path)
    original = AgentProfile(
        name="review",
        description="Original broad profile",
        engine="kas",
        allowedTools=["*"],
        model="original-model",
    )
    monkeypatch.setattr(terminal_service, "load_agent_profile", lambda _: original)
    monkeypatch.setattr(
        "cli_agent_orchestrator.agent_plugins.mcp_delivery.apply_plugin_mcp_servers",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(terminal_service, "generate_terminal_id", lambda: "1234abcd")
    monkeypatch.setattr(
        terminal_service, "session_lifecycle_lock", lambda _: contextlib.nullcontext()
    )
    monkeypatch.setattr(terminal_service, "get_herdr_inbox_service", lambda: None)
    monkeypatch.setattr(terminal_service, "clear_session_env", lambda _: None)
    monkeypatch.setattr(
        terminal_service, "kiro_install_predates_native_enforcement", lambda *args: False
    )
    backend = Mock()
    backend.session_exists.return_value = False
    backend.supports_event_inbox.return_value = True
    backend.get_pane_current_command.return_value = "bash"
    monkeypatch.setattr(terminal_service, "get_backend", lambda: backend)
    monkeypatch.setattr(kiro_cli, "get_backend", lambda: backend)
    monkeypatch.setattr(kiro_cli, "wait_for_shell", AsyncMock(return_value=True))
    monkeypatch.setattr(kiro_cli, "wait_until_status", AsyncMock(return_value=True))
    monkeypatch.setattr(status_monitor, "get_status", lambda _: TerminalStatus.IDLE)
    monkeypatch.setattr(status_monitor, "notify_input_sent", Mock())
    registry = ProviderManager()
    monkeypatch.setattr(terminal_service, "provider_manager", registry)
    try:
        terminal = await terminal_service.create_terminal(
            provider="kiro_cli",
            agent_profile="review",
            session_name="cao-review",
            new_session=True,
            allowed_tools=[],
            model="closed-model",
            engine="kas",
            working_directory=str(tmp_path),
            kiro_capability_probe=lambda *args: None,
        )
        stored = database.get_terminal_metadata(terminal.id)
        assert stored["allowed_tools"] == []
        effective = original.model_copy(update={"allowedTools": [], "model": "closed-model"})
        assert stored["kiro_policy_digest"] == kas_policy_digest(effective)
        assert database.get_session_incarnation("cao-review") == stored["session_incarnation_id"]
        assert (
            kiro_profiles.verify_runtime_kas_profile(
                terminal.id, effective, stored["kiro_policy_digest"]
            )
            == "cao-runtime-1234abcd"
        )
        command = shlex.split(backend.send_keys.call_args.args[2])
        assert command == [
            "kiro-cli",
            "--v3",
            "chat",
            "--model",
            "closed-model",
            "--agent",
            "cao-runtime-1234abcd",
        ]
        assert original.allowedTools == ["*"] and original.model == "original-model"
    finally:
        engine.dispose()
