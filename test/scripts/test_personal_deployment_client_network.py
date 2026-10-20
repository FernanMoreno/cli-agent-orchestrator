"""Offline checks for the private PostgreSQL-client container network policy."""

from __future__ import annotations

import importlib.util
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/personal_deployment.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("personal_deployment_network", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the personal deployment module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


@pytest.mark.offline_database
@pytest.mark.parametrize(
    ("host", "operating_system", "expected_network", "uses_gateway"),
    [
        ("localhost", "Docker Desktop", "bridge", True),
        ("localhost.", "Docker Desktop", "bridge", True),
        ("127.0.0.1", "Docker Desktop", "bridge", True),
        ("::1", "Docker Desktop", "bridge", True),
        ("127.0.0.1", "Ubuntu 24.04", "host", False),
        ("db.example.test", "Docker Desktop", "bridge", False),
    ],
)
def test_client_network_keeps_postgres_hostname_tls_and_pgpass_aligned(
    tmp_path, monkeypatch, host, operating_system, expected_network, uses_gateway
):
    module = _load_module()
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    cao_home = root / "cao"
    cao_home.mkdir(mode=0o700)

    target_configs = {}
    passwords = {}
    for scope in ("main", "auth", "peer"):
        runtime_password = f"runtime:{scope}\\secret"
        migration_password = f"migration:{scope}\\secret"
        runtime_secret = cao_home / f"{scope}-runtime.json"
        migration_secret = cao_home / f"{scope}-migration.json"
        _private_json(runtime_secret, {"password": runtime_password})
        _private_json(migration_secret, {"password": migration_password})
        passwords[scope] = migration_password
        target_configs[scope] = {
            "host": host if scope == "main" else "db.example.test",
            "port": 5432,
            "database": f"cao_{scope}",
            "user": f"cao_{scope}_runtime",
            "schema": f"cao_{scope}",
            "store_id": str(uuid.uuid4()),
            "secret_file": runtime_secret.name,
            "sslmode": "verify-full",
            "migration_user": f"cao_{scope}_owner",
            "migration_secret_file": migration_secret.name,
        }
    module.write_database_config(root, target_configs)
    target = module.database_target(root, "main")
    observed = {}

    def capture_run(command, **kwargs):
        if command[:3] == ["docker", "info", "--format"]:
            observed.setdefault("docker_info_calls", 0)
            observed["docker_info_calls"] += 1
            return SimpleNamespace(returncode=0, stderr="", stdout=operating_system + "\n")
        observed["command"] = list(command)
        observed["environment"] = dict(kwargs["env"])
        mount = command[command.index("--mount") + 1]
        passfile = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
        observed["passfile_path"] = passfile
        observed["passfile_mode"] = passfile.stat().st_mode & 0o777
        observed["passfile_content"] = passfile.read_text(encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(module.subprocess, "run", capture_run)
    assert module._run_database_client(root, target, "pg_dump", ["--version"]) == ""

    command = observed["command"]
    assert command[command.index("--network") + 1] == expected_network
    assert command[command.index("--host") + 1] == host
    assert observed.get("docker_info_calls", 0) == (
        1 if host.casefold() in {"localhost", "localhost."} or host in {"127.0.0.1", "::1"} else 0
    )
    assert "PGSSLMODE=verify-full" in command
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    expected_passfile = (
        ":".join(
            module._safe_passfile_value(value)
            for value in (host, "5432", "cao_main", "cao_main_owner", passwords["main"])
        )
        + "\n"
    )
    assert observed["passfile_content"] == expected_passfile
    assert observed["passfile_mode"] == 0o600
    assert not observed["passfile_path"].exists()
    assert passwords["main"] not in command
    assert "PGPASSFILE=/run/secrets/pgpass" in command
    if uses_gateway:
        assert command[command.index("--add-host") + 1] == "host.docker.internal:host-gateway"
        assert command[command.index("--entrypoint") + 1] == "/bin/sh"
        script_index = command.index("-c") + 1
        assert "getent ahostsv4 host.docker.internal" in command[script_index]
        assert 'export PGHOSTADDR="$hostaddr"' in command[script_index]
        assert command[script_index + 1 : script_index + 3] == ["cao-pg-client", "pg_dump"]
    else:
        assert "--add-host" not in command
        assert command[command.index("--entrypoint") + 1] == "pg_dump"
