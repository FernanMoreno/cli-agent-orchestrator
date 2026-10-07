"""A runtime KAS artifact must reflect its narrowed persisted policy."""

import json

import pytest

from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.services.kiro_profiles import (
    install_runtime_kas_profile,
    verify_runtime_kas_profile,
)
from cli_agent_orchestrator.utils.kiro_launch_guard import kas_policy_digest


def test_runtime_artifact_is_private_and_fenced_to_exact_policy(tmp_path):
    profile = AgentProfile(name="review", description="Review", engine="kas", allowedTools=[])
    path = install_runtime_kas_profile("1234abcd", profile, directory=tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600
    data = json.loads(path.read_text())
    assert data["tools"] == []
    assert data["name"] == "cao-runtime-1234abcd"
    assert (
        verify_runtime_kas_profile(
            "1234abcd", profile, kas_policy_digest(profile), directory=tmp_path
        )
        == "cao-runtime-1234abcd"
    )
    path.write_text(path.read_text().replace('"tools": []', '"tools": ["execute_bash"]'))
    with pytest.raises(ValueError):
        verify_runtime_kas_profile(
            "1234abcd", profile, kas_policy_digest(profile), directory=tmp_path
        )


def test_runtime_artifact_rejects_changed_profile_before_reusing(tmp_path):
    profile = AgentProfile(
        name="review", description="Review", engine="kas", allowedTools=["fs_read"]
    )
    install_runtime_kas_profile("1234abcd", profile, directory=tmp_path)
    modified = profile.model_copy(update={"allowedTools": ["fs_read", "fs_write"]})
    with pytest.raises(ValueError):
        verify_runtime_kas_profile(
            "1234abcd", modified, kas_policy_digest(profile), directory=tmp_path
        )


def test_concurrent_runtime_grants_never_replace_the_winner(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    barrier = threading.Barrier(2)
    original = Path.exists
    target = tmp_path / "cao-runtime-1234abcd.kas.json"

    def exists(path):
        if path == target:
            value = original(path)
            barrier.wait(timeout=5)
            return value
        return original(path)

    monkeypatch.setattr(Path, "exists", exists)
    profiles = [
        AgentProfile(name="review", description="Review", engine="kas", allowedTools=tools)
        for tools in ([], ["fs_read"])
    ]

    def attempt(parsed):
        try:
            install_runtime_kas_profile("1234abcd", parsed, directory=tmp_path)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        successes = list(pool.map(attempt, profiles))
    assert sum(successes) == 1
    winner = json.loads(target.read_text())["tools"]
    assert winner in ([], ["fs_read"])


def test_existing_artifact_symlink_or_public_file_never_counts_as_private_proof(tmp_path):
    profile = AgentProfile(name="review", description="Review", engine="kas", allowedTools=[])
    path = install_runtime_kas_profile("1234abcd", profile, directory=tmp_path)
    path.chmod(0o644)
    with pytest.raises(ValueError):
        install_runtime_kas_profile("1234abcd", profile, directory=tmp_path)
    path.chmod(0o600)
    other = tmp_path / "other"
    path.rename(other)
    path.symlink_to(other)
    with pytest.raises(ValueError):
        install_runtime_kas_profile("1234abcd", profile, directory=tmp_path)


def test_uncertain_kas_stop_retains_private_proof_and_runtime_resources(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from cli_agent_orchestrator.services import terminal_service

    backend = Mock()
    backend.cleanup_terminal_exact.return_value = SimpleNamespace(
        reclaimed=False, outcome=SimpleNamespace(value="unknown"), detail="uncertain"
    )
    monkeypatch.setattr(terminal_service, "get_backend", lambda: backend)
    fifo = Mock()
    cleanup = Mock()
    monkeypatch.setattr(terminal_service.fifo_manager, "stop_reader", fifo)
    monkeypatch.setattr(terminal_service.provider_manager, "cleanup_provider", cleanup)
    assert (
        terminal_service.dismantle_terminal_runtime(
            "1234abcd", {"engine": "kas", "tmux_session": "cao-kas", "tmux_window": "1"}
        )
        is False
    )
    fifo.assert_not_called()
    cleanup.assert_not_called()
