"""Private OpenCode ownership must survive the API's in-memory provider map."""

from test.providers.test_opencode_profile_initialization import launch
from test.providers.test_opencode_reinitialization_ownership import current, prepared

import pytest

from cli_agent_orchestrator.providers import manager, opencode_cli
from cli_agent_orchestrator.services import remote_terminal_service

pytestmark = [pytest.mark.offline_database, pytest.mark.asyncio]


@pytest.mark.parametrize("major", [1, 2])
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("corrupt", [False, True])
async def test_reconstructed_cleanup_retires_only_owned_native_tree(
    prepared, monkeypatch, major, cached, corrupt
):
    original, _, parent, sibling, source = prepared
    original_auth = source.read_bytes()
    assert await original.initialize()
    owned = current(original, major)
    assert owned.is_dir()
    metadata = {
        "provider": "opencode_cli",
        "provider_variant": f"opencode-v{major}",
        "tmux_session": original.session_name,
        "tmux_window": original.window_name,
        "agent_profile": "reviewer",
        "allowed_tools": [],
        "shell_command": "bash",
    }
    monkeypatch.setattr(manager, "get_terminal_metadata", lambda terminal_id: metadata)
    monkeypatch.setattr(manager, "get_terminal_turn_receipt", lambda terminal_id: None)
    monkeypatch.setattr(remote_terminal_service, "placement", lambda terminal_id: None)
    restarted = manager.ProviderManager()
    if cached:
        recovered = restarted.get_provider(original.terminal_id)
        assert isinstance(recovered, opencode_cli.OpenCodeCliProvider)
        assert recovered is not original
    if corrupt:
        proof = original._config_ownership().path
        original_proof = proof.read_bytes()
        proof.write_text("{}")
        assert restarted.cleanup_provider(original.terminal_id) is False
        assert owned.is_dir()
        if cached:
            assert restarted._providers[original.terminal_id] is recovered
        proof.write_bytes(original_proof)
    assert restarted.cleanup_provider(original.terminal_id) is True
    assert not owned.exists(), "API reconstruction orphaned the private configuration"
    assert set(parent.iterdir()) == {sibling}
    assert (sibling / "keep").read_text() == "unrelated private state"
    assert source.read_bytes() == original_auth
    # Duplicate cleanup is safe and does not need a vendor executable.
    assert restarted.cleanup_provider(original.terminal_id) is True
    assert (sibling / "keep").exists()
