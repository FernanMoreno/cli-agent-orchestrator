"""A fenced pane reset must not orphan private OpenCode configuration."""

import asyncio
from pathlib import Path
from test.providers.test_opencode_profile_initialization import launch
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.providers import opencode_cli
from cli_agent_orchestrator.utils import opencode_config

pytestmark = [pytest.mark.offline_database, pytest.mark.asyncio]


@pytest.fixture
def prepared(launch, monkeypatch, major):
    instance, backend, home = launch
    monkeypatch.setattr(opencode_cli, "detect_opencode_major", lambda _: major)
    monkeypatch.setattr(opencode_config, "read_config", lambda: {"mcp": {}, "agent": {}})
    source = Path(__import__("os").environ["XDG_DATA_HOME"]) / "opencode" / "auth.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"synthetic":{"type":"api","key":"synthetic-reset-only"}}')
    parent = home / f"opencode-v{major}"
    sibling = parent / "terminal-unrelated"
    sibling.mkdir(parents=True)
    (sibling / "keep").write_text("unrelated private state")
    return instance, backend, parent, sibling, source


def current(instance, major):
    return getattr(instance, f"_owned_v{major}_config_dir")


@pytest.mark.parametrize("major", [1, 2])
async def test_each_replacement_retires_only_own_old_root_after_shell(prepared, monkeypatch, major):
    instance, backend, parent, sibling, source = prepared
    original = source.read_bytes()
    assert await instance.initialize()
    instance.prepare_input("synthetic fenced turn")
    receipt_state = instance.pending_turn_receipt_state()
    assert receipt_state is not None
    for _ in range(3):
        old = current(instance, major)
        (old / "old-secret-sentinel").write_bytes(b"private old state")
        events = []

        async def shell_ready(*args, **kwargs):
            assert old.is_dir(), "do not retire configuration before the shell boundary"
            events.append("shell")
            return True

        def launched(*args, **kwargs):
            assert not old.exists(), "old configuration survived replacement launch"
            events.append("launch")

        monkeypatch.setattr(opencode_cli, "wait_for_shell", shell_ready)
        backend.send_keys.side_effect = launched
        instance._initialized = False  # the recovery service fences/resets the old pane first
        assert await instance.initialize()
        new = current(instance, major)
        assert new != old and new.is_dir()
        assert events == ["shell", "launch"]
        assert set(parent.iterdir()) == {sibling, new}
        assert source.read_bytes() == original
        assert instance.pending_turn_receipt_state() == receipt_state
        assert (sibling / "keep").read_text() == "unrelated private state"
    instance.cleanup()
    assert set(parent.iterdir()) == {sibling}


@pytest.mark.parametrize("major", [1, 2])
@pytest.mark.parametrize("failure", ["readiness", "cancel", "configuration"])
async def test_failed_replacement_cleans_old_and_new_but_preserves_sibling(
    prepared, monkeypatch, major, failure
):
    instance, backend, parent, sibling, source = prepared
    assert await instance.initialize()
    old = current(instance, major)
    original = source.read_bytes()
    if failure == "configuration":

        def reject():
            raise ValueError("synthetic invalid replacement configuration")

        monkeypatch.setattr(opencode_config, "read_config", reject)
        error = ValueError
    elif failure == "cancel":
        monkeypatch.setattr(
            instance, "_wait_for_initial_ready", AsyncMock(side_effect=asyncio.CancelledError)
        )
        error = asyncio.CancelledError
    else:
        monkeypatch.setattr(instance, "_wait_for_initial_ready", AsyncMock(return_value=False))
        error = TimeoutError
    instance._initialized = False
    with pytest.raises(error):
        await instance.initialize()
    assert not old.exists()
    assert current(instance, major) is None
    assert set(parent.iterdir()) == {sibling}
    assert source.read_bytes() == original


@pytest.mark.parametrize("major", [1, 2])
@pytest.mark.parametrize("cancel", [False, True])
async def test_shell_failure_does_not_deliver_another_launch(prepared, monkeypatch, major, cancel):
    instance, backend, parent, sibling, source = prepared
    assert await instance.initialize()
    original = source.read_bytes()
    if cancel:
        ready = AsyncMock(side_effect=asyncio.CancelledError)
        error = asyncio.CancelledError
    else:
        ready = AsyncMock(return_value=False)
        error = TimeoutError
    monkeypatch.setattr(opencode_cli, "wait_for_shell", ready)
    with pytest.raises(error):
        await instance.initialize()
    assert backend.send_keys.call_count == 1
    assert current(instance, major) is None
    assert set(parent.iterdir()) == {sibling}
    assert source.read_bytes() == original
