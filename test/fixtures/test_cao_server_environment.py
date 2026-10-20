"""The managed server owns its profile even when the runner owns another."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from test.fixtures.cao_server import _subprocess_env

import pytest

pytestmark = pytest.mark.offline_database


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


def test_child_imports_the_checked_out_source_with_relative_runner_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    expected_package = project_root / "src" / "cli_agent_orchestrator" / "__init__.py"
    home = tmp_path / "server-home"
    home.mkdir()
    decoy = tmp_path / "decoy"
    (decoy / "cli_agent_orchestrator").mkdir(parents=True)
    (decoy / "cli_agent_orchestrator" / "__init__.py").write_text(
        "SOURCE_PIN_DECOY = True\n", encoding="utf-8"
    )
    monkeypatch.setenv("PYTHONPATH", "src" + os.pathsep + ".")

    env = _subprocess_env(home, 12345, extra={"PYTHONPATH": str(decoy)})
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import cli_agent_orchestrator; print(cli_agent_orchestrator.__file__)",
        ],
        cwd=home,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert Path(result.stdout.strip()).resolve() == expected_package.resolve()


def test_managed_child_config_omits_migration_credentials(tmp_path: Path) -> None:
    from test.fixtures.cao_server import (
        _remove_runtime_database_config,
        _runtime_only_database_targets,
    )
    from test.postgres.fixtures import PostgresTargets
    from uuid import uuid4

    from cli_agent_orchestrator.clients.postgres import DatabaseTarget

    home = tmp_path / "server-home"
    targets = {}
    for scope in ("main", "auth", "peer"):
        runtime_secret = home / f"{scope}-runtime.json"
        migration_secret = home / f"{scope}-migration.json"
        runtime_secret.parent.mkdir(parents=True, exist_ok=True)
        runtime_secret.write_text('{"password":"runtime-secret"}\n', encoding="utf-8")
        migration_secret.write_text('{"password":"owner-secret"}\n', encoding="utf-8")
        targets[scope] = DatabaseTarget(
            host="127.0.0.1",
            port=5432,
            database=f"test_{scope}",
            user=f"runtime_{scope}",
            schema=f"s_{scope}",
            store_id=str(uuid4()),
            secret_file=runtime_secret,
            sslmode="disable",
            home=home,
            scope=scope,
            migration_user=f"owner_{scope}",
            migration_secret_file=migration_secret,
        )
    supplied = PostgresTargets(
        main=targets["main"],
        auth=targets["auth"],
        peer=targets["peer"],
        home=home,
        config_file=home / "database-config.json",
    )

    runtime_targets, config_path = _runtime_only_database_targets(supplied, home)

    assert config_path is not None
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    serialized = config_path.read_text(encoding="utf-8")
    assert "migration_user" not in serialized
    assert "migration_secret_file" not in serialized
    assert "owner_main" not in serialized
    assert str(home / "main-migration.json") not in serialized
    config = json.loads(serialized)
    for scope in ("main", "auth", "peer"):
        target = runtime_targets[scope]
        assert target.identity == supplied[scope].identity
        assert target.migration_user is None
        assert target.migration_secret_file is None
        assert config["targets"][scope]["user"] == f"runtime_{scope}"

    _remove_runtime_database_config(config_path)
    assert not config_path.exists()
