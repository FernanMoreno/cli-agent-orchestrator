from __future__ import annotations

import json
import stat
import uuid
from pathlib import Path

import pytest

from scripts import local_work_docker_demo

pytestmark = pytest.mark.offline_database


def _write_private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def _dedicated_config(directory: Path, *, duplicate_main_auth_schema: bool = False) -> Path:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    secrets = directory / "secrets"
    secrets.mkdir(mode=0o700)
    targets = {}
    for scope in ("main", "auth", "peer"):
        runtime_secret = secrets / f"{scope}-runtime.json"
        owner_secret = secrets / f"{scope}-owner.json"
        _write_private_json(runtime_secret, {"password": f"{scope}-runtime-secret"})
        _write_private_json(owner_secret, {"password": f"{scope}-owner-secret"})
        schema = (
            "dedicated_main"
            if scope == "main" or (scope == "auth" and duplicate_main_auth_schema)
            else f"dedicated_{scope}"
        )
        targets[scope] = {
            "host": "127.0.0.1",
            "port": 5432,
            "database": "cao_demo",
            "user": f"cao_{scope}_runtime",
            "schema": schema,
            "store_id": str(uuid.uuid4()),
            "secret_file": f"secrets/{runtime_secret.name}",
            "sslmode": "disable",
            "migration_user": f"cao_{scope}_owner",
            "migration_secret_file": f"secrets/{owner_secret.name}",
        }
    config_path = directory / "database-config.json"
    _write_private_json(config_path, {"version": 1, "targets": targets})
    return config_path


def test_run_demo_requires_explicit_database_config_even_if_operator_override_exists(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CAO_DATABASE_CONFIG_FILE", str(tmp_path / "operator-config.json"))
    monkeypatch.setattr(
        local_work_docker_demo,
        "_build_local_rootfs",
        lambda *_args, **_kwargs: pytest.fail(
            "Docker work must not start without an explicit target"
        ),
    )

    with pytest.raises(ValueError, match="explicit.*database config"):
        local_work_docker_demo.run_demo()


def test_run_demo_rejects_inherited_operator_config_when_selected_explicitly(monkeypatch, tmp_path):
    source = _dedicated_config(tmp_path / "source")
    monkeypatch.setenv("CAO_DATABASE_CONFIG_FILE", str(source))
    monkeypatch.setattr(
        local_work_docker_demo,
        "_build_local_rootfs",
        lambda *_args, **_kwargs: pytest.fail("the inherited operator target must be rejected"),
    )

    with pytest.raises(ValueError, match="separate from the inherited CAO profile"):
        local_work_docker_demo.run_demo(database_config=source)


def test_run_demo_rejects_a_copied_config_that_targets_operator_schemas(monkeypatch, tmp_path):
    inherited = _dedicated_config(tmp_path / "operator")
    selected = tmp_path / "copied-demo-config.json"
    _write_private_json(selected, json.loads(inherited.read_text(encoding="utf-8")))
    monkeypatch.setenv("CAO_DATABASE_CONFIG_FILE", str(inherited))
    monkeypatch.setattr(
        local_work_docker_demo,
        "_build_local_rootfs",
        lambda *_args, **_kwargs: pytest.fail("operator schemas must be rejected before Docker"),
    )

    with pytest.raises(ValueError, match="schemas must be separate"):
        local_work_docker_demo.run_demo(database_config=selected)


def test_private_demo_config_copies_all_scopes_and_owner_only_secrets(tmp_path):
    source = _dedicated_config(tmp_path / "source")
    private_home = tmp_path / "temporary-profile"
    private_home.mkdir(mode=0o700)

    config_path, targets = local_work_docker_demo._copy_private_database_config(
        source, private_home
    )

    assert set(targets) == {"main", "auth", "peer"}
    assert all(target.migration_user != target.user for target in targets.values())
    assert len({target.store_id for target in targets.values()}) == 3
    assert (
        len(
            {
                (target.host, target.port, target.database, target.schema)
                for target in targets.values()
            }
        )
        == 3
    )
    assert config_path == private_home / "database-config.json"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    copied = json.loads(config_path.read_text(encoding="utf-8"))
    assert set(copied) == {"version", "targets"}
    for scope, target_config in copied["targets"].items():
        assert target_config["secret_file"].startswith("postgres/secrets/")
        assert target_config["migration_secret_file"].startswith("postgres/secrets/")
        for key in ("secret_file", "migration_secret_file"):
            secret_path = private_home / target_config[key]
            assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600
            assert json.loads(secret_path.read_text(encoding="utf-8")) == {
                "password": f"{scope}-{'runtime' if key == 'secret_file' else 'owner'}-secret"
            }
            assert str(source.parent) not in target_config[key]


def test_private_demo_config_rejects_shared_schema_namespace(tmp_path):
    source = _dedicated_config(tmp_path / "source", duplicate_main_auth_schema=True)
    private_home = tmp_path / "temporary-profile"
    private_home.mkdir(mode=0o700)

    with pytest.raises(ValueError, match="distinct.*schema"):
        local_work_docker_demo._copy_private_database_config(source, private_home)


def test_private_demo_config_rejects_group_readable_source(tmp_path):
    source = _dedicated_config(tmp_path / "source")
    source.chmod(0o640)
    private_home = tmp_path / "temporary-profile"
    private_home.mkdir(mode=0o700)

    with pytest.raises(ValueError, match="private"):
        local_work_docker_demo._copy_private_database_config(source, private_home)


@pytest.mark.postgres
def test_demo_bootstraps_only_clean_native_scopes_and_never_adopts_them_again(
    tmp_path, postgres_targets
):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    private_home = tmp_path / "temporary-profile"
    private_home.mkdir(mode=0o700)
    _config_path, targets = local_work_docker_demo._copy_private_database_config(
        postgres_targets.config_file, private_home
    )

    local_work_docker_demo._require_clean_demo_namespaces(targets)
    local_work_docker_demo._bootstrap_demo_database(targets)
    WorkRepository(targets["main"], filesystem_home=private_home).initialize()

    with pytest.raises(ValueError, match="must be empty"):
        local_work_docker_demo._require_clean_demo_namespaces(targets)
