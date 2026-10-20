"""Offline contract checks for the private T097 guest PostgreSQL handoff."""

from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from test.postgres import fixtures
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database


def _valid_metadata():
    run_id = "a1b2c3d4e5f6"
    nonce = "0123456789abcdef0123456789abcdef"
    return {
        "service_mode": "cao-t097-owned-postgres-v1",
        "host": "10.0.2.2",
        "port": 5432,
        "database": "postgres",
        "user": "cao_spec021_admin",
        "password_file": "admin-password",
        "expected_server_major": 18,
        "storage_limit_bytes": 1024 * 1024 * 1024,
        "container_id": "c" * 64,
        "service_run_id": run_id,
        "image_id": fixtures.IMAGE_ID,
        "network_name": f"cao-spec021-net-{run_id}",
        "marker_database": f"t097_marker_{nonce}",
        "marker_nonce": nonce,
    }


def _write_handoff(tmp_path, metadata=None, *, password="private-test-password\n"):
    handoff = tmp_path / "private-handoff"
    handoff.mkdir(mode=0o700)
    os.chmod(handoff, 0o700)
    metadata = _valid_metadata() if metadata is None else metadata
    metadata_path = handoff / "metadata.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    metadata_path.chmod(0o600)
    password_path = handoff / "admin-password"
    password_path.write_text(password, encoding="utf-8")
    password_path.chmod(0o600)
    return handoff, metadata_path, password_path


class _Result:
    def __init__(self, one=None, all_rows=None):
        self._one = one
        self._all = all_rows or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class _Connection:
    def __init__(self, database, metadata):
        self.database = database
        self.metadata = metadata

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement):
        if "server_version_num" in statement:
            return _Result((180006,))
        if "rolsuper" in statement:
            return _Result((self.metadata["user"], True))
        if "pg_get_userbyid" in statement:
            return _Result((self.metadata["user"],))
        if "t097_service_identity" in statement:
            return _Result(
                all_rows=[
                    (
                        self.metadata["marker_nonce"],
                        self.metadata["container_id"],
                        self.metadata["service_run_id"],
                        self.metadata["image_id"],
                    )
                ]
            )
        raise AssertionError(f"unexpected PostgreSQL identity query: {statement}")


@pytest.mark.parametrize("network_prefix", ["cao-spec021-net", "cao-spec021-test-net"])
def test_t097_handoff_requires_exact_private_shape_before_connecting(
    tmp_path, monkeypatch, network_prefix
):
    metadata = _valid_metadata()
    metadata["network_name"] = f"{network_prefix}-{metadata['service_run_id']}"
    handoff, metadata_path, _password_path = _write_handoff(tmp_path, metadata)
    connections = []

    def connect_admin(service, database="postgres"):
        connections.append(database)
        return _Connection(database, service.metadata)

    monkeypatch.setattr(fixtures.PostgresService, "connect_admin", connect_admin)
    service = fixtures._service_from_metadata_path(metadata_path)

    assert connections == ["postgres", "t097_marker_0123456789abcdef0123456789abcdef"]
    assert service.password == "private-test-password"
    assert service.metadata["host"] == "10.0.2.2"
    assert service.metadata["network_name"] == f"{network_prefix}-a1b2c3d4e5f6"
    assert "private-test-password" not in repr(service)
    assert stat.S_IMODE(handoff.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("host", "0.0.0.0"),
        ("port", 5433),
        ("user", "postgres"),
        ("image_id", "sha256:" + "0" * 64),
        ("network_name", "untrusted-network"),
        ("marker_database", "operator_db"),
        ("storage_limit_bytes", 2 * 1024 * 1024 * 1024),
    ],
)
def test_t097_handoff_rejects_unpinned_endpoint_and_identity_before_connecting(
    tmp_path, monkeypatch, field, value
):
    metadata = _valid_metadata()
    metadata[field] = value
    _handoff, metadata_path, _password_path = _write_handoff(tmp_path, metadata)
    monkeypatch.setattr(
        fixtures.PostgresService,
        "connect_admin",
        lambda *_args, **_kwargs: pytest.fail("invalid metadata reached PostgreSQL"),
    )

    with pytest.raises(RuntimeError, match="T097 PostgreSQL"):
        fixtures._service_from_metadata_path(metadata_path)


def test_t097_handoff_rejects_unapproved_metadata_fields(tmp_path, monkeypatch):
    metadata = _valid_metadata()
    metadata["admin_password"] = "must never be accepted in metadata"
    _handoff, metadata_path, _password_path = _write_handoff(tmp_path, metadata)
    monkeypatch.setattr(
        fixtures.PostgresService,
        "connect_admin",
        lambda *_args, **_kwargs: pytest.fail("unexpected metadata reached PostgreSQL"),
    )

    with pytest.raises(RuntimeError, match="unexpected shape"):
        fixtures._service_from_metadata_path(metadata_path)


def test_t097_handoff_rejects_symlinked_password_file(tmp_path, monkeypatch):
    handoff, metadata_path, password_path = _write_handoff(tmp_path)
    password_path.unlink()
    outside = tmp_path / "outside-secret"
    outside.write_text("not-private\n", encoding="utf-8")
    password_path.symlink_to(outside)
    monkeypatch.setattr(
        fixtures.PostgresService,
        "connect_admin",
        lambda *_args, **_kwargs: pytest.fail("symlinked password reached PostgreSQL"),
    )

    with pytest.raises(RuntimeError, match="secret must be private"):
        fixtures._service_from_metadata_path(metadata_path)
    assert handoff.is_dir()


def test_t097_handoff_rejects_group_readable_metadata_directory(tmp_path):
    handoff, metadata_path, _password_path = _write_handoff(tmp_path)
    handoff.chmod(0o750)

    with pytest.raises(RuntimeError, match="metadata directory must be private"):
        fixtures._t097_guest_service(metadata_path, _valid_metadata())


def test_owned_service_cleanup_requires_matching_run_identity(monkeypatch):
    container = {
        "Id": "c" * 64,
        "Name": "/cao-spec021-pg-a1b2c3d4e5f6",
        "Config": {
            "Labels": {
                fixtures.OWNER_LABEL: fixtures.OWNER_VALUE,
                fixtures.RUN_LABEL: "a1b2c3d4e5f6",
            }
        },
    }
    network = {
        "Id": "d" * 64,
        "Name": "cao-spec021-test-net-a1b2c3d4e5f6",
        "Labels": {
            fixtures.OWNER_LABEL: fixtures.OWNER_VALUE,
            fixtures.RUN_LABEL: "a1b2c3d4e5f6",
        },
    }
    commands = []
    monkeypatch.setattr(fixtures, "_inspect", lambda _container_id: container)

    def docker(args, **_kwargs):
        commands.append(args)
        if args[1:3] == ["network", "inspect"]:
            return json.dumps([network])
        return "removed"

    monkeypatch.setattr(fixtures, "_docker", docker)
    fixtures._cleanup_owned_postgres_service(
        container_id="c" * 64,
        container_name="cao-spec021-pg-a1b2c3d4e5f6",
        network_id="d" * 64,
        network_name="cao-spec021-test-net-a1b2c3d4e5f6",
        run_id="a1b2c3d4e5f6",
    )
    assert commands == [
        ["docker", "rm", "--force", "c" * 64],
        ["docker", "network", "inspect", "d" * 64],
        ["docker", "network", "rm", "d" * 64],
    ]

    commands.clear()
    fixtures._cleanup_owned_postgres_service(
        container_id="c" * 64,
        container_name="cao-spec021-pg-a1b2c3d4e5f6",
        network_id="d" * 64,
        network_name="cao-spec021-test-net-a1b2c3d4e5f6",
        run_id="ffffffffffff",
    )
    assert commands == [
        ["docker", "network", "inspect", "d" * 64],
    ]


def test_postgres_service_fixture_fails_actionably_when_docker_is_missing(monkeypatch):
    monkeypatch.delenv("CAO_TEST_POSTGRES_METADATA_FILE", raising=False)
    monkeypatch.setattr(fixtures.shutil, "which", lambda _name: None)
    provider = fixtures.postgres_service.__wrapped__()

    with pytest.raises(pytest.fail.Exception, match="PostgreSQL tests require Docker"):
        next(provider)


def test_postgres_service_fixture_autostarts_and_restores_private_metadata_env(
    monkeypatch,
):
    monkeypatch.delenv("CAO_TEST_POSTGRES_METADATA_FILE", raising=False)
    # This unit test verifies the autostart branch with a mocked provisioner;
    # it must not inherit an offline-suite setting that disables that branch.
    monkeypatch.setenv("CAO_TEST_POSTGRES_AUTOSTART", "1")
    monkeypatch.setattr(fixtures.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(fixtures, "_docker", lambda _args: "")
    service = SimpleNamespace(metadata={"password_file": "/private/cao-run/admin-password"})

    @contextmanager
    def provision():
        yield service

    monkeypatch.setattr(fixtures, "_provision_owned_postgres_service", provision)
    provider = fixtures.postgres_service.__wrapped__()

    assert next(provider) is service
    assert os.environ["CAO_TEST_POSTGRES_METADATA_FILE"] == "/private/cao-run/metadata.json"
    provider.close()
    assert "CAO_TEST_POSTGRES_METADATA_FILE" not in os.environ


def test_owned_service_provisioner_uses_private_loopback_pinned_container_and_exact_cleanup(
    monkeypatch,
):
    run_id = None
    container_name = None
    network_name = None
    password_path = None
    docker_calls = []

    def fake_docker(args, **_kwargs):
        nonlocal run_id, container_name, network_name, password_path
        docker_calls.append(args)
        if args[1:3] == ["network", "create"]:
            network_name = args[-1]
            run_id = next(
                item.split("=", 1)[1] for item in args if item.startswith(fixtures.RUN_LABEL + "=")
            )
            return "d" * 64
        if args[1:3] == ["run", "--detach"]:
            container_name = args[args.index("--name") + 1]
            password_path = (
                args[args.index("--mount") + 1].split("source=", 1)[1].split(",target=", 1)[0]
            )
            assert args[args.index("--publish") + 1] == "127.0.0.1::5432"
            assert args[args.index("--network") + 1] == network_name
            assert args[args.index("--tmpfs") + 1] == "/var/lib/postgresql:rw,size=3g"
            assert args[args.index("--memory") + 1] == "4g"
            assert args[args.index("--memory-swap") + 1] == "4g"
            assert args[-11:] == [
                "postgres",
                "-c",
                "max_connections=256",
                "-c",
                "max_locks_per_transaction=256",
                "-c",
                "max_wal_size=256MB",
                "-c",
                "min_wal_size=64MB",
                "-c",
                "checkpoint_timeout=1min",
            ]
            network_create = next(
                call for call in docker_calls if call[1:3] == ["network", "create"]
            )
            assert "--internal" not in network_create
            assert network_create[network_create.index("--driver") + 1] == "bridge"
            assert args[args.index("--publish") + 1] == "127.0.0.1::5432"
            return "c" * 64
        if args[1:3] == ["inspect", "c" * 64]:
            return json.dumps(
                [
                    {
                        "Id": "c" * 64,
                        "Name": f"/{container_name}",
                        "Image": fixtures.IMAGE_ID,
                        "Config": {
                            "Image": "postgres@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336",
                            "Labels": {
                                fixtures.OWNER_LABEL: fixtures.OWNER_VALUE,
                                fixtures.RUN_LABEL: run_id,
                                fixtures._POSTGRES_PROFILE_LABEL: fixtures._POSTGRES_LOCAL_PROFILE,
                            },
                        },
                        "NetworkSettings": {
                            "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "54321"}]},
                            "Networks": {network_name: {}},
                        },
                        "Mounts": [
                            {
                                "Source": password_path,
                                "Type": "bind",
                                "Destination": "/run/secrets/postgres_password",
                                "RW": False,
                            }
                        ],
                        "HostConfig": {
                            "Tmpfs": {"/var/lib/postgresql": "rw,size=3g"},
                            "Memory": 4 * 1024**3,
                            "MemorySwap": 4 * 1024**3,
                        },
                        "State": {"Health": {"Status": "healthy"}},
                    }
                ]
            )
        if args[1:4] == ["network", "inspect", "d" * 64]:
            return json.dumps(
                [
                    {
                        "Id": "d" * 64,
                        "Name": network_name,
                        "Labels": {
                            fixtures.OWNER_LABEL: fixtures.OWNER_VALUE,
                            fixtures.RUN_LABEL: run_id,
                        },
                    }
                ]
            )
        return "removed"

    monkeypatch.setattr(fixtures.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(fixtures, "_docker", fake_docker)
    monkeypatch.setattr(fixtures.time, "sleep", lambda _seconds: None)

    class ProfileResult:
        def fetchone(self):
            return ("256", "256", "256MB", "64MB", "1min")

    class ProfileConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _statement):
            return ProfileResult()

    monkeypatch.setattr(
        fixtures.PostgresService,
        "connect_admin",
        lambda _self, _database="postgres": ProfileConnection(),
    )

    with fixtures._provision_owned_postgres_service() as service:
        secret = service.password
        metadata_path = Path(service.metadata["password_file"]).with_name("metadata.json")
        password_file = Path(service.metadata["password_file"])
        assert service.metadata["host"] == "127.0.0.1"
        assert stat.S_IMODE(metadata_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(metadata_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(password_file.stat().st_mode) == 0o600
        assert password_file.read_text(encoding="utf-8").strip() == secret
        assert all(secret not in " ".join(call) for call in docker_calls)

    assert not metadata_path.exists()
    assert not password_file.exists()
    assert [call for call in docker_calls if call[1:4] == ["rm", "--force", "c" * 64]]
    assert [call for call in docker_calls if call[1:4] == ["network", "rm", "d" * 64]]
