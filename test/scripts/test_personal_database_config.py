"""Offline checks for private personal PostgreSQL target configuration."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline_database


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/personal_deployment.py"


def _module():
    spec = importlib.util.spec_from_file_location("personal_deployment_config_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _targets(root: Path):
    result = {}
    for scope in ("main", "auth", "peer"):
        secret_file = root / "cao" / f"{scope}-runtime-secret.json"
        migration_secret_file = root / "cao" / f"{scope}-migration-secret.json"
        secret_file.write_text('{"password":"runtime-test-only"}', encoding="utf-8")
        migration_secret_file.write_text('{"password":"migration-test-only"}', encoding="utf-8")
        secret_file.chmod(0o600)
        migration_secret_file.chmod(0o600)
        result[scope] = {
            "host": "127.0.0.1",
            "port": 5432,
            "database": f"cao_{scope}",
            "user": f"cao_{scope}",
            "schema": "cao_app",
            "store_id": f"store-{scope}",
            "secret_file": str(secret_file),
            "migration_user": f"cao_{scope}_owner",
            "migration_secret_file": str(migration_secret_file),
            "sslmode": "disable",
        }
    return result


def test_write_database_config_uses_canonical_name_and_private_target_paths(tmp_path):
    module = _module()
    root = tmp_path / "personal"
    module.initialize(root, server_port=19889, jwks_port=19890, image_id="sha256:" + "a" * 64)
    targets = _targets(root)

    default_path = module.write_database_config(root, targets)
    assert default_path == root / "cao" / "database-config.json"
    assert default_path.stat().st_mode & 0o077 == 0
    stored = json.loads(default_path.read_text(encoding="utf-8"))
    assert stored["targets"] == targets
    assert "runtime-test-only" not in default_path.read_text(encoding="utf-8")
    assert "migration-test-only" not in default_path.read_text(encoding="utf-8")
