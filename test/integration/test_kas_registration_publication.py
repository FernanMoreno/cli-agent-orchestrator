"""Registration ownership must precede deterministic per-terminal KAS files."""

import asyncio
import threading
from test.services.test_terminal_service_inbox_registration import SESSION_NAME, create_mocks
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import IntegrityError

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult
from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.models.kiro_launch import KiroLaunchRefusedError
from cli_agent_orchestrator.services import kiro_profiles, provider_lifecycle, terminal_service


@pytest.fixture
def kas_creation(create_mocks, monkeypatch, tmp_path):
    terminal_id = "1234abcd"
    create_mocks.generate_terminal_id.return_value = terminal_id
    profile = AgentProfile(
        name="developer", description="Private grant", engine="kas", allowedTools=[]
    )
    create_mocks.load_agent_profile.return_value = profile
    create_mocks.db_create_terminal.side_effect = db.create_terminal
    monkeypatch.setattr(constants, "ENABLE_KAS_LAUNCH", True)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(mode=0o700)
    monkeypatch.setattr(kiro_profiles, "KIRO_AGENTS_DIR", agents_dir)
    effective = kiro_profiles.resolve_kas_launch_profile(profile, [], None)
    return create_mocks, terminal_id, effective, agents_dir


def snapshot(directory):
    return {p.name: p.read_bytes() for p in directory.iterdir()}


@pytest.mark.asyncio
@pytest.mark.parametrize("files", ["absent", "artifact_only"])
@pytest.mark.parametrize("failure", ["collision", "backend", "ambiguous_commit"])
async def test_rejected_registration_does_not_publish_kas_files(kas_creation, files, failure):
    mocks, terminal_id, effective, directory = kas_creation
    assert db.engine.dialect.name == "postgresql"
    if files == "artifact_only":
        kiro_profiles.install_runtime_kas_profile(terminal_id, effective)
        (directory / f".cao-runtime-{terminal_id}.kas.source.json").unlink()
    before = snapshot(directory)
    if failure == "collision":
        db.create_terminal(
            terminal_id, "existing-owner", "existing-window", "kiro_cli", "ready", engine="v2"
        )
    original_row = db.get_terminal_metadata(terminal_id)
    error = RuntimeError("controlled registration failure")
    if failure == "backend":
        mocks.backend.create_window.side_effect = error
    elif failure == "ambiguous_commit":

        def publish_then_raise(*args, **kwargs):
            db.create_terminal(*args, **kwargs)
            raise error

        mocks.db_create_terminal.side_effect = publish_then_raise
    with pytest.raises(IntegrityError if failure == "collision" else RuntimeError):
        await terminal_service.create_terminal(
            "kiro_cli",
            "developer",
            session_name=SESSION_NAME,
            kiro_capability_probe=Mock(),
            allowed_tools=[],
        )
    if failure == "ambiguous_commit":
        assert db.get_terminal_metadata(terminal_id) is not None
    else:
        assert db.get_terminal_metadata(terminal_id) == original_row
    assert snapshot(directory) == before
    assert terminal_id not in provider_lifecycle._startups
    mocks.provider_manager.create_provider.assert_not_called()


@pytest.mark.asyncio
async def test_existing_different_private_grant_remains_unchanged(kas_creation):
    mocks, terminal_id, effective, directory = kas_creation
    old = effective.model_copy(update={"description": "Other admitted owner"})
    kiro_profiles.install_runtime_kas_profile(terminal_id, old)
    before = snapshot(directory)
    db.create_terminal(
        terminal_id, "existing-owner", "existing-window", "kiro_cli", "ready", engine="kas"
    )
    row = db.get_terminal_metadata(terminal_id)
    with pytest.raises((IntegrityError, ValueError)):
        await terminal_service.create_terminal(
            "kiro_cli",
            "developer",
            session_name=SESSION_NAME,
            kiro_capability_probe=Mock(),
            allowed_tools=[],
        )
    assert snapshot(directory) == before
    assert db.get_terminal_metadata(terminal_id) == row
    mocks.provider_manager.create_provider.assert_not_called()


@pytest.mark.asyncio
async def test_success_publishes_exact_frozen_grant_after_registration_before_provider(
    kas_creation, monkeypatch
):
    mocks, terminal_id, effective, directory = kas_creation
    events = []
    install = kiro_profiles.install_runtime_kas_profile

    def observed_install(key, profile):
        assert key == terminal_id
        assert db.get_terminal_metadata(key) is not None
        assert key in provider_lifecycle._startups
        events.append("install")
        return install(key, profile)

    monkeypatch.setattr(kiro_profiles, "install_runtime_kas_profile", observed_install)
    adapter = mocks.provider_manager.create_provider.return_value

    def construct(*args, **kwargs):
        assert db.get_terminal_metadata(terminal_id)["kiro_policy_digest"]
        assert (directory / f"cao-runtime-{terminal_id}.kas.json").is_file()
        assert (directory / f".cao-runtime-{terminal_id}.kas.source.json").is_file()
        events.append("provider")
        return adapter

    mocks.provider_manager.create_provider.side_effect = construct
    result = await terminal_service.create_terminal(
        "kiro_cli",
        "developer",
        session_name=SESSION_NAME,
        kiro_capability_probe=Mock(),
        allowed_tools=[],
    )
    assert result.id == terminal_id
    assert events == ["install", "provider"]
    adapter.initialize.assert_awaited_once()
    assert terminal_id not in provider_lifecycle._startups


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["conflict", "partial"])
@pytest.mark.parametrize("child", [False, True])
async def test_failed_post_registration_publication_retains_proof_and_row(
    kas_creation, monkeypatch, fault, child
):
    mocks, terminal_id, effective, directory = kas_creation
    error = OSError("source publication refused")
    if fault == "conflict":
        kiro_profiles.install_runtime_kas_profile(
            terminal_id, effective.model_copy(update={"description": "Previous grant"})
        )
    before = snapshot(directory)
    publish = kiro_profiles._publish_private_runtime_text

    def fail_source(path, rendered):
        if path.name.endswith(".kas.source.json"):
            raise error
        return publish(path, rendered)

    if fault == "partial":
        monkeypatch.setattr(kiro_profiles, "_publish_private_runtime_text", fail_source)
    with pytest.raises(ValueError if fault == "conflict" else OSError):
        await terminal_service.create_terminal(
            "kiro_cli",
            "developer",
            session_name=SESSION_NAME,
            kiro_capability_probe=Mock(),
            allowed_tools=[],
            caller_id="parent" if child else None,
        )
    row = db.get_terminal_metadata(terminal_id)
    assert row is not None
    assert row["engine"] == "kas"
    if fault == "conflict":
        assert snapshot(directory) == before
    else:
        assert set(snapshot(directory)) == {f"cao-runtime-{terminal_id}.kas.json"}
    with pytest.raises(KiroLaunchRefusedError):
        kiro_profiles.guard_existing_kas_runtime(terminal_id, row)
    mocks.provider_manager.create_provider.assert_not_called()
    mocks.backend.kill_window.assert_not_called()
    mocks.backend.kill_session.assert_not_called()
    assert terminal_id not in provider_lifecycle._startups

    receipt = db.get_native_child_for_terminal(terminal_id)
    if child:
        assert receipt["state"] == "reconcile"
        assert receipt["error_kind"] == "kas_publication_failed"
        assert receipt["parent_terminal_id"] == "parent"
    else:
        assert receipt is None


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_cancelled_publisher_stops_before_startup_release(kas_creation, monkeypatch, partial):
    mocks, terminal_id, effective, directory = kas_creation
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    install = kiro_profiles.install_runtime_kas_profile

    def controlled_install(key, profile):
        entered.set()
        try:
            assert release.wait(timeout=5)
            install(key, profile)
            if partial:
                (directory / f".cao-runtime-{terminal_id}.kas.source.json").unlink()
                raise OSError("partial publication")
        finally:
            finished.set()

    monkeypatch.setattr(kiro_profiles, "install_runtime_kas_profile", controlled_install)
    task = asyncio.create_task(
        terminal_service.create_terminal(
            "kiro_cli",
            "developer",
            session_name=SESSION_NAME,
            kiro_capability_probe=Mock(),
            allowed_tools=[],
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert db.get_terminal_metadata(terminal_id) is not None
        assert terminal_id in provider_lifecycle._startups
        # The row is visible before the proof. Cold restoration fails closed;
        # startup admission does not promise to block or queue that reader.
        with pytest.raises(KiroLaunchRefusedError):
            kiro_profiles.guard_existing_kas_runtime(
                terminal_id, db.get_terminal_metadata(terminal_id)
            )
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert not finished.is_set()
        assert terminal_id in provider_lifecycle._startups
        assert await asyncio.to_thread(terminal_service.delete_terminal, terminal_id) is False
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled() and finished.is_set()
    assert terminal_id not in provider_lifecycle._startups
    row = db.get_terminal_metadata(terminal_id)
    assert row is not None
    if partial:
        with pytest.raises(KiroLaunchRefusedError):
            kiro_profiles.guard_existing_kas_runtime(terminal_id, row)
    else:
        assert kiro_profiles.guard_existing_kas_runtime(terminal_id, row) == effective
    mocks.provider_manager.create_provider.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_registration_worker_never_publishes_grant(kas_creation):
    mocks, terminal_id, _, directory = kas_creation
    mocks.backend.kill_window.return_value = True
    mocks.backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.ABSENT
    )
    entered, release = threading.Event(), threading.Event()

    def controlled_insert(*args, **kwargs):
        db.create_terminal(*args, **kwargs)
        entered.set()
        assert release.wait(timeout=5)

    mocks.db_create_terminal.side_effect = controlled_insert
    task = asyncio.create_task(
        terminal_service.create_terminal(
            "kiro_cli",
            "developer",
            session_name=SESSION_NAME,
            kiro_capability_probe=Mock(),
            allowed_tools=[],
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert db.get_terminal_metadata(terminal_id) is not None
        task.cancel()
        await asyncio.sleep(0)
        assert snapshot(directory) == {}
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert snapshot(directory) == {}
    assert db.get_terminal_metadata(terminal_id) is None
    assert terminal_id not in provider_lifecycle._startups
    mocks.provider_manager.create_provider.assert_not_called()
