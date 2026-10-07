"""Exercise startup prerequisites and the actual shell launch environment."""

import subprocess
from unittest.mock import AsyncMock

import pytest

from cli_agent_orchestrator.providers import opencode_cli


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    tools = tmp_path / "tools with spaces"
    tools.mkdir()
    executable = tools / "opencode"
    executable.write_text(
        '#!/bin/sh\nif [ "$1" = --version ]; then echo 2.0.18; '
        'else printf \'%s\\n\' "$PATH" "$@"; fi\n'
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tools))
    agents = tmp_path / "agents"
    agents.mkdir()
    monkeypatch.setattr(opencode_cli, "OPENCODE_AGENTS_DIR", agents)
    monkeypatch.setattr(opencode_cli, "CAO_HOME_DIR", tmp_path / "cao")
    monkeypatch.setattr(opencode_cli, "wait_for_shell", AsyncMock(return_value=True))
    from cli_agent_orchestrator.utils import opencode_config

    monkeypatch.setattr(opencode_config, "read_config", lambda: {})
    return executable, agents


def instance(profile="reviewer"):
    return opencode_cli.OpenCodeCliProvider("native-test", "session", "window", profile)


@pytest.mark.asyncio
async def test_missing_native_profile_rejected_before_shell_or_config(runtime, monkeypatch):
    executable, agents = runtime
    provider = instance("team/reviewer")
    monkeypatch.setattr(provider, "_wait_for_initial_ready", AsyncMock(return_value=True))
    commands = []

    class Backend:
        def send_keys(self, *args, **kwargs):
            commands.append(args[2])

    monkeypatch.setattr(opencode_cli, "get_backend", Backend)

    with pytest.raises(ValueError, match=r"team__reviewer.*cao install"):
        await provider.initialize()

    assert commands == []
    assert not (opencode_cli.CAO_HOME_DIR / "opencode-v2").exists()
    assert opencode_cli.wait_for_shell.await_count == 0
    assert not provider._initialized


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["1.18.32", "2.0.18"])
async def test_launch_survives_shell_path_reset_and_preserves_native_profile(
    runtime, monkeypatch, version
):
    executable, agents = runtime
    executable.write_text(executable.read_text().replace("2.0.18", version))
    native = agents / "team__reviewer.md"
    content = "---\ndescription: Owner reviewer\nmode: all\n---\nKeep owner instructions.\n"
    native.write_text(content)
    provider = instance("team/reviewer")
    monkeypatch.setattr(provider, "_wait_for_initial_ready", AsyncMock(return_value=True))
    launched = []

    class Backend:
        def send_keys(self, session, window, command, **kwargs):
            launched.append(
                subprocess.run(
                    ["/bin/sh", "-c", "PATH=/usr/bin:/bin; " + command],
                    text=True,
                    capture_output=True,
                )
            )

    monkeypatch.setattr(opencode_cli, "get_backend", Backend)

    assert await provider.initialize()
    assert launched[0].returncode == 0, launched[0].stderr
    expected = [str(executable.parent), "--agent", "team__reviewer"]
    if version == "2.0.18":
        expected = [str(executable.parent), "mini", "--standalone", "--agent", "team__reviewer"]
    assert launched[0].stdout.splitlines() == expected
    assert native.read_text() == content
    private = provider._v2_config_dir
    provider.cleanup()
    if private is not None:
        assert not private.exists()
    assert native.read_text() == content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content", ["---\ndescription: [\n---\nReview.", "---\nmode: invalid-mode\n---\nReview."]
)
async def test_invalid_native_profile_rejected_before_startup(runtime, content, monkeypatch):
    _, agents = runtime
    native = agents / "reviewer.md"
    native.write_text(content)
    provider = instance()
    monkeypatch.setattr(provider, "_wait_for_initial_ready", AsyncMock(return_value=True))

    class Backend:
        def send_keys(self, *args, **kwargs):
            pass

    monkeypatch.setattr(opencode_cli, "get_backend", Backend)

    with pytest.raises(ValueError, match="native profile.*invalid"):
        await provider.initialize()

    assert opencode_cli.wait_for_shell.await_count == 0
    assert native.read_text() == content
    assert not opencode_cli.CAO_HOME_DIR.exists()


@pytest.mark.asyncio
async def test_unavailable_executable_rejected_before_startup(runtime):
    executable, agents = runtime
    (agents / "reviewer.md").write_text("Review.")
    executable.unlink()

    with pytest.raises(ValueError, match="executable.*unavailable"):
        await instance().initialize()

    assert opencode_cli.wait_for_shell.await_count == 0
    assert not opencode_cli.CAO_HOME_DIR.exists()


@pytest.mark.asyncio
async def test_failed_readiness_removes_only_owned_private_config(runtime, monkeypatch):
    _, agents = runtime
    (agents / "reviewer.md").write_text("---\ndescription: Reviewer\n---\nReview.\n")
    owner_dir = opencode_cli.CAO_HOME_DIR / "opencode-v2" / "native-test"
    owner_dir.mkdir(parents=True, mode=0o700)
    owner_file = owner_dir / "opencode.json"
    owner_file.write_text("owner configuration")
    provider = instance()
    monkeypatch.setattr(provider, "_wait_for_initial_ready", AsyncMock(return_value=False))

    class Backend:
        def send_keys(self, *args, **kwargs):
            pass

    monkeypatch.setattr(opencode_cli, "get_backend", Backend)

    with pytest.raises(TimeoutError):
        await provider.initialize()

    assert owner_file.read_text() == "owner configuration"
    assert list(owner_dir.parent.iterdir()) == [owner_dir]
    assert not provider._initialized
