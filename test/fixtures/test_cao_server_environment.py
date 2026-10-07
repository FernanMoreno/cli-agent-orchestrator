"""The managed server owns its profile even when the runner owns another."""

import os
import subprocess
import sys
from pathlib import Path
from test.fixtures.cao_server import _subprocess_env

import pytest


@pytest.mark.parametrize("explicit_override", [False, True])
def test_child_profile_does_not_inherit_runner_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit_override: bool
) -> None:
    parent_profile = tmp_path / "runner-profile"
    monkeypatch.setenv("CAO_HOME_DIR", str(parent_profile))
    home = tmp_path / "server-home"
    profile = (
        tmp_path / "explicit-profile"
        if explicit_override
        else home / ".aws" / "cli-agent-orchestrator"
    )
    extra = {"CAO_HOME_DIR": str(profile)} if explicit_override else None

    env = _subprocess_env(home, 12345, extra=extra)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from cli_agent_orchestrator.constants import DB_DIR; print(DB_DIR)",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert Path(result.stdout.strip()) == profile / "db"
    assert profile.is_dir()
    assert not parent_profile.exists()
    assert os.environ["CAO_HOME_DIR"] == str(parent_profile)
