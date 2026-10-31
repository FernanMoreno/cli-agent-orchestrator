"""Private, isolated PostgreSQL targets for ``@pytest.mark.postgres`` tests."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

IMAGE_ID = "sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336"
OWNER_LABEL = "org.cao.owner"
OWNER_VALUE = "spec021-runtime"
RUN_LABEL = "org.cao.run"
_POSTGRES_CAPACITY_GUARD_INTERVAL = 100
_POSTGRES_MIN_TMPFS_FREE_BYTES = 128 * 1024 * 1024
_POSTGRES_LOCAL_PROFILE = "cao-spec021-local-postgres-v3"
_POSTGRES_LOCAL_TMPFS_BYTES = 3 * 1024 * 1024 * 1024
_POSTGRES_LOCAL_MEMORY_BYTES = 4 * 1024 * 1024 * 1024
_POSTGRES_LOCAL_MAX_CONNECTIONS = 256
_POSTGRES_LOCAL_MAX_LOCKS = 256
_POSTGRES_LOCAL_MAX_WAL_SIZE = "256MB"
_POSTGRES_LOCAL_MIN_WAL_SIZE = "64MB"
_POSTGRES_LOCAL_CHECKPOINT_TIMEOUT = "1min"
_POSTGRES_PROFILE_LABEL = "org.cao.postgres.profile"
_T097_GUEST_MODE = "cao-t097-owned-postgres-v1"
_T097_GUEST_STORAGE_LIMIT_BYTES = 1024 * 1024 * 1024
_POSTGRES_WORKER_RESET_COUNT = 0


def _docker(args: list[str], *, timeout: float = 30) -> str:
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("PostgreSQL test container inspection failed") from exc
    return result.stdout.strip()


def _private_json(path: Path) -> dict[str, Any]:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & 0o077
        or metadata.st_uid != os.geteuid()
    ):
        raise RuntimeError("PostgreSQL test metadata must be a private regular file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("Invalid PostgreSQL test metadata")
    return value


def _write_private(path: Path, value: str | bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value.encode() if isinstance(value, str) else value)
        stream.flush()
        os.fsync(stream.fileno())


def _inspect(container_id: str) -> dict[str, Any]:
    parsed = json.loads(_docker(["docker", "inspect", container_id]))
    if len(parsed) != 1:
        raise RuntimeError("Cannot verify PostgreSQL test container")
    return parsed[0]


def _size_setting_bytes(value: object) -> int:
    """Parse the Docker size syntax used by HostConfig.Tmpfs."""
    import re

    if not isinstance(value, str):
        raise RuntimeError("PostgreSQL test tmpfs size is invalid")
    match = re.search(r"(?:^|,)size=(\d+)([kmgt]?)(?:i?b)?(?:,|$)", value, re.IGNORECASE)
    if match is None:
        raise RuntimeError("PostgreSQL test tmpfs size is invalid")
    scale = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
    return int(match.group(1)) * scale[match.group(2).lower()]


def _container_metadata(container_id: str) -> dict[str, Any]:
    inspected = _inspect(container_id)
    labels = inspected.get("Config", {}).get("Labels") or {}
    run_id = labels.get(RUN_LABEL)
    if labels.get(OWNER_LABEL) != OWNER_VALUE or not isinstance(run_id, str):
        raise RuntimeError("Refusing a PostgreSQL container not owned by Spec 021 tests")
    if (
        inspected.get("Image") != IMAGE_ID
        or inspected.get("Config", {}).get("Image")
        != "postgres@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336"
    ):
        raise RuntimeError("PostgreSQL test image digest does not match the pinned image")
    ports = inspected.get("NetworkSettings", {}).get("Ports", {}).get("5432/tcp") or []
    mounts = inspected.get("Mounts") or []
    host_config = inspected.get("HostConfig") or {}
    tmpfs = host_config.get("Tmpfs") or {}
    if len(ports) != 1 or len(mounts) != 1:
        raise RuntimeError("PostgreSQL test container has unexpected mounts or port bindings")
    secret_path = Path(mounts[0].get("Source", ""))
    if (
        ports[0].get("HostIp") != "127.0.0.1"
        or mounts[0].get("Type") != "bind"
        or mounts[0].get("Destination") != "/run/secrets/postgres_password"
        or mounts[0].get("RW") is not False
        or "/var/lib/postgresql" not in tmpfs
    ):
        raise RuntimeError("PostgreSQL test container security configuration is not accepted")
    file_info = secret_path.lstat()
    if (
        not stat.S_ISREG(file_info.st_mode)
        or file_info.st_mode & 0o077
        or file_info.st_uid != os.geteuid()
    ):
        raise RuntimeError("PostgreSQL test administrator secret is not private")
    return {
        "run_id": run_id,
        "container_id": inspected["Id"],
        "host": ports[0]["HostIp"],
        "port": int(ports[0]["HostPort"]),
        "database": "postgres",
        "user": "cao_spec021_admin",
        "sslmode": "disable",
        "password_file": str(secret_path),
        "service_profile": labels.get(_POSTGRES_PROFILE_LABEL),
        "storage_limit_bytes": _size_setting_bytes(tmpfs["/var/lib/postgresql"]),
        "memory_limit_bytes": int(host_config.get("Memory", 0)),
        "memory_swap_bytes": int(host_config.get("MemorySwap", 0)),
    }


def _cleanup_owned_postgres_service(
    *, container_id: str, container_name: str, network_id: str, network_name: str, run_id: str
) -> None:
    """Remove only the exact uniquely labelled service created for one test session."""
    if container_id:
        try:
            container = _inspect(container_id)
            labels = container.get("Config", {}).get("Labels") or {}
            if (
                container.get("Id") == container_id
                and container.get("Name") == f"/{container_name}"
                and labels.get(OWNER_LABEL) == OWNER_VALUE
                and labels.get(RUN_LABEL) == run_id
            ):
                _docker(["docker", "rm", "--force", container_id])
        except RuntimeError:
            pass
    if network_id:
        try:
            parsed = json.loads(_docker(["docker", "network", "inspect", network_id]))
            if len(parsed) == 1:
                network = parsed[0]
                labels = network.get("Labels") or {}
                if (
                    network.get("Id") == network_id
                    and network.get("Name") == network_name
                    and labels.get(OWNER_LABEL) == OWNER_VALUE
                    and labels.get(RUN_LABEL) == run_id
                ):
                    _docker(["docker", "network", "rm", network_id])
        except RuntimeError:
            pass


@contextmanager
def _provision_owned_postgres_service():
    """Start one pinned, loopback-only PostgreSQL service for a pytest process."""
    if shutil.which("docker") is None:
        raise RuntimeError(
            "PostgreSQL tests need Docker or CAO_TEST_POSTGRES_METADATA_FILE pointing to "
            "a private, verified test service"
        )

    run_id = secrets.token_hex(6)
    container_name = f"cao-spec021-pg-{run_id}"
    network_name = f"cao-spec021-test-net-{run_id}"
    container_id = ""
    network_id = ""
    with tempfile.TemporaryDirectory(prefix="cao-spec021-postgres-") as temporary_directory:
        private_dir = Path(temporary_directory)
        os.chmod(private_dir, 0o700)
        password_path = private_dir / "admin-password"
        _write_private(password_path, secrets.token_urlsafe(32) + "\n")
        metadata_path = private_dir / "metadata.json"
        try:
            network_id = _docker(
                [
                    "docker",
                    "network",
                    "create",
                    "--driver",
                    "bridge",
                    # This unique, ephemeral bridge is used only by the disposable
                    # PostgreSQL test service and retains normal bridge egress. A
                    # non-internal bridge is required for host-side test connections;
                    # PostgreSQL is still published only on 127.0.0.1 below.
                    "--label",
                    f"{OWNER_LABEL}={OWNER_VALUE}",
                    "--label",
                    f"{RUN_LABEL}={run_id}",
                    "--label",
                    f"{_POSTGRES_PROFILE_LABEL}={_POSTGRES_LOCAL_PROFILE}",
                    network_name,
                ]
            )
            container_id = _docker(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    container_name,
                    "--network",
                    network_name,
                    "--label",
                    f"{OWNER_LABEL}={OWNER_VALUE}",
                    "--label",
                    f"{RUN_LABEL}={run_id}",
                    "--label",
                    f"{_POSTGRES_PROFILE_LABEL}={_POSTGRES_LOCAL_PROFILE}",
                    "--publish",
                    "127.0.0.1::5432",
                    "--mount",
                    f"type=bind,source={password_path},target=/run/secrets/postgres_password,readonly",
                    "--tmpfs",
                    "/var/lib/postgresql:rw,size=3g",
                    "--memory",
                    "4g",
                    "--memory-swap",
                    "4g",
                    "--env",
                    "POSTGRES_USER=cao_spec021_admin",
                    "--env",
                    "POSTGRES_DB=postgres",
                    "--env",
                    "POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password",
                    "--health-cmd",
                    "pg_isready -U cao_spec021_admin -d postgres",
                    "--health-interval",
                    "2s",
                    "--health-timeout",
                    "3s",
                    "--health-retries",
                    "60",
                    "postgres@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336",
                    "postgres",
                    "-c",
                    f"max_connections={_POSTGRES_LOCAL_MAX_CONNECTIONS}",
                    "-c",
                    f"max_locks_per_transaction={_POSTGRES_LOCAL_MAX_LOCKS}",
                    "-c",
                    f"max_wal_size={_POSTGRES_LOCAL_MAX_WAL_SIZE}",
                    "-c",
                    f"min_wal_size={_POSTGRES_LOCAL_MIN_WAL_SIZE}",
                    "-c",
                    f"checkpoint_timeout={_POSTGRES_LOCAL_CHECKPOINT_TIMEOUT}",
                ],
                timeout=300,
            )
            if len(container_id) != 64 or any(c not in "0123456789abcdef" for c in container_id):
                raise RuntimeError("Docker returned an invalid PostgreSQL test container ID")

            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                inspected = _inspect(container_id)
                labels = inspected.get("Config", {}).get("Labels") or {}
                networks = inspected.get("NetworkSettings", {}).get("Networks") or {}
                if (
                    inspected.get("Name") != f"/{container_name}"
                    or inspected.get("Image") != IMAGE_ID
                    or inspected.get("Config", {}).get("Image")
                    != "postgres@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336"
                    or labels.get(OWNER_LABEL) != OWNER_VALUE
                    or labels.get(RUN_LABEL) != run_id
                    or list(networks) != [network_name]
                ):
                    raise RuntimeError("Started PostgreSQL test container identity is invalid")
                health = inspected.get("State", {}).get("Health", {}).get("Status")
                if health == "healthy":
                    break
                if health == "unhealthy":
                    raise RuntimeError("PostgreSQL test container became unhealthy")
                time.sleep(1)
            else:
                raise RuntimeError("PostgreSQL test container did not become healthy within 120s")

            actual = _container_metadata(container_id)
            _write_private(
                metadata_path,
                json.dumps(
                    {
                        "container_id": container_id,
                        "published_host": actual["host"],
                        "published_port": actual["port"],
                        "password_file": str(password_path),
                        "run_id": run_id,
                        "service_profile": _POSTGRES_LOCAL_PROFILE,
                    },
                    sort_keys=True,
                ),
            )
            yield _service_from_metadata_path(metadata_path)
        finally:
            _cleanup_owned_postgres_service(
                container_id=container_id,
                container_name=container_name,
                network_id=network_id,
                network_name=network_name,
                run_id=run_id,
            )


@dataclass(frozen=True)
class PostgresService:
    metadata: dict[str, Any]
    password: str = field(repr=False)

    def connect_admin(self, database: str = "postgres"):
        import psycopg

        return psycopg.connect(
            host=self.metadata["host"],
            port=self.metadata["port"],
            dbname=database,
            user=self.metadata["user"],
            password=self.password,
            sslmode="disable",
            connect_timeout=10,
            autocommit=True,
        )


def _t097_guest_service(metadata_path: Path, metadata: dict[str, Any]) -> PostgresService:
    """Validate a test-owned PG service reached through the QEMU host proxy."""
    parent = metadata_path.parent
    parent_info = parent.lstat()
    if (
        not metadata_path.is_absolute()
        or metadata_path.name != "metadata.json"
        or parent.resolve(strict=True) != parent
        or not stat.S_ISDIR(parent_info.st_mode)
        or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_mode & 0o077
        or parent_info.st_uid != os.geteuid()
    ):
        raise RuntimeError("T097 PostgreSQL metadata directory must be private and owned")
    if metadata.get("service_mode") != _T097_GUEST_MODE:
        raise RuntimeError("Unsupported external PostgreSQL test service mode")
    expected_keys = {
        "service_mode",
        "host",
        "port",
        "database",
        "user",
        "password_file",
        "expected_server_major",
        "storage_limit_bytes",
        "container_id",
        "service_run_id",
        "image_id",
        "network_name",
        "marker_database",
        "marker_nonce",
    }
    if set(metadata) != expected_keys:
        raise RuntimeError("T097 PostgreSQL service metadata has an unexpected shape")

    container_id = metadata.get("container_id")
    run_id = metadata.get("service_run_id")
    image_id = metadata.get("image_id")
    marker_nonce = metadata.get("marker_nonce")
    marker_database = metadata.get("marker_database")
    network_name = metadata.get("network_name")
    if (
        not isinstance(container_id, str)
        or len(container_id) != 64
        or any(character not in "0123456789abcdef" for character in container_id)
        or not isinstance(run_id, str)
        or len(run_id) != 12
        or any(character not in "0123456789abcdef" for character in run_id)
        or image_id != IMAGE_ID
        or not isinstance(marker_nonce, str)
        or len(marker_nonce) != 32
        or any(character not in "0123456789abcdef" for character in marker_nonce)
        or marker_database != f"t097_marker_{marker_nonce}"
        or network_name
        not in {
            f"cao-spec021-net-{run_id}",
            f"cao-spec021-test-net-{run_id}",
        }
    ):
        raise RuntimeError("T097 PostgreSQL service identity metadata is invalid")
    if (
        metadata.get("host") != "10.0.2.2"
        or metadata.get("port") != 5432
        or metadata.get("database") != "postgres"
        or metadata.get("user") != "cao_spec021_admin"
        or metadata.get("expected_server_major") != 18
        or metadata.get("storage_limit_bytes") != _T097_GUEST_STORAGE_LIMIT_BYTES
    ):
        raise RuntimeError("T097 PostgreSQL service endpoint or policy is invalid")

    password_name = metadata.get("password_file")
    if password_name != "admin-password":
        raise RuntimeError("T097 PostgreSQL password path is invalid")
    password_path = parent / password_name
    password_info = password_path.lstat()
    if (
        not stat.S_ISREG(password_info.st_mode)
        or stat.S_ISLNK(password_info.st_mode)
        or password_info.st_mode & 0o077
        or password_info.st_uid != os.geteuid()
    ):
        raise RuntimeError("T097 PostgreSQL administrator secret must be private")

    password = password_path.read_text(encoding="utf-8").strip()
    if not password:
        raise RuntimeError("T097 PostgreSQL administrator secret is empty")
    service = PostgresService(metadata, password)
    try:
        with service.connect_admin("postgres") as connection:
            version = connection.execute(
                "SELECT current_setting('server_version_num')::integer"
            ).fetchone()
            role = connection.execute(
                "SELECT current_user, rolsuper FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
        if not version or int(version[0]) // 10000 != metadata["expected_server_major"]:
            raise RuntimeError("T097 PostgreSQL server version does not match the pinned service")
        if role != (metadata["user"], True):
            raise RuntimeError("T097 PostgreSQL test account is not the expected administrator")
        with service.connect_admin(marker_database) as connection:
            owner = connection.execute(
                "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"
            ).fetchone()
            marker = connection.execute(
                "SELECT nonce, container_id, run_id, image_id " "FROM public.t097_service_identity"
            ).fetchall()
        if owner != (metadata["user"],):
            raise RuntimeError("T097 PostgreSQL marker database has an unexpected owner")
        expected_marker = (
            marker_nonce,
            container_id,
            run_id,
            image_id,
        )
        if marker != [expected_marker]:
            raise RuntimeError("T097 PostgreSQL run marker does not match this service")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("T097 PostgreSQL service identity check failed") from exc
    return service


def _service_from_metadata_path(metadata_path: Path) -> PostgresService:
    metadata = _private_json(metadata_path)
    if metadata.get("service_mode") == _T097_GUEST_MODE:
        return _t097_guest_service(metadata_path, metadata)
    container = _inspect(str(metadata.get("container_id", "")))
    actual = _container_metadata(container["Id"])
    for key in ("host", "port", "password_file"):
        expected_key = {"host": "published_host", "port": "published_port"}.get(key, key)
        if metadata.get(expected_key) != actual[key]:
            raise RuntimeError("PostgreSQL test metadata does not match the owned service")
    if metadata.get("run_id") != actual["run_id"]:
        raise RuntimeError("PostgreSQL test metadata has a stale run identity")
    service_profile = metadata.get("service_profile")
    if service_profile is not None and service_profile != actual["service_profile"]:
        raise RuntimeError("PostgreSQL test metadata has a stale service profile")
    password_path = Path(actual["password_file"])
    service = PostgresService(actual, password_path.read_text(encoding="utf-8").strip())
    if service_profile == _POSTGRES_LOCAL_PROFILE:
        _verify_local_postgres_profile(service)
    return service


def _verify_local_postgres_profile(service: PostgresService) -> None:
    """Check the configured local v3 service against its live container and server."""
    expected_container = {
        "storage_limit_bytes": _POSTGRES_LOCAL_TMPFS_BYTES,
        "memory_limit_bytes": _POSTGRES_LOCAL_MEMORY_BYTES,
        "memory_swap_bytes": _POSTGRES_LOCAL_MEMORY_BYTES,
    }
    if any(service.metadata.get(key) != value for key, value in expected_container.items()):
        raise RuntimeError("PostgreSQL test service resource profile does not match v3")
    with service.connect_admin("postgres") as connection:
        row = connection.execute(
            "SELECT current_setting('max_connections'), "
            "current_setting('max_locks_per_transaction'), "
            "current_setting('max_wal_size'), current_setting('min_wal_size'), "
            "current_setting('checkpoint_timeout')"
        ).fetchone()
    expected_server = (
        str(_POSTGRES_LOCAL_MAX_CONNECTIONS),
        str(_POSTGRES_LOCAL_MAX_LOCKS),
        _POSTGRES_LOCAL_MAX_WAL_SIZE,
        _POSTGRES_LOCAL_MIN_WAL_SIZE,
        _POSTGRES_LOCAL_CHECKPOINT_TIMEOUT,
    )
    if row != expected_server:
        raise RuntimeError("PostgreSQL test service server settings do not match v3")


def _discover_postgres_service() -> PostgresService:
    """Resolve exactly one disposable, pinned service owned by this test suite."""
    import shutil

    metadata_path = os.environ.get("CAO_TEST_POSTGRES_METADATA_FILE")
    if metadata_path:
        return _service_from_metadata_path(Path(metadata_path))
    else:
        if shutil.which("docker") is None:
            raise RuntimeError("Docker or CAO_TEST_POSTGRES_METADATA_FILE is required")
        candidates = _docker(
            ["docker", "ps", "--filter", f"label={OWNER_LABEL}={OWNER_VALUE}", "--quiet"]
        ).splitlines()
        if len(candidates) != 1:
            raise RuntimeError("Exactly one owned Spec 021 PostgreSQL container must be running")
        actual = _container_metadata(candidates[0])
    password_path = Path(actual["password_file"])
    return PostgresService(actual, password_path.read_text(encoding="utf-8").strip())


def _postgres_tmpfs_available_bytes(df_output: str) -> int:
    """Parse the owned container's POSIX ``df -Pk`` line for its PG tmpfs."""
    rows = [line.split() for line in df_output.splitlines() if line.strip()]
    if len(rows) < 2 or len(rows[-1]) < 6 or rows[-1][-1] != "/var/lib/postgresql":
        raise RuntimeError("Cannot verify PostgreSQL test tmpfs free space")
    try:
        return int(rows[-1][3]) * 1024
    except (TypeError, ValueError) as error:
        raise RuntimeError("Cannot verify PostgreSQL test tmpfs free space") from error


def _guard_postgres_tmpfs_capacity() -> None:
    """Fail before the owned disposable PG tmpfs can approach exhaustion."""
    service = _discover_postgres_service()
    if service.metadata.get("service_mode") == _T097_GUEST_MODE:
        with service.connect_admin("postgres") as connection:
            database_bytes = connection.execute(
                "SELECT coalesce(sum(pg_database_size(oid)), 0) FROM pg_database"
            ).fetchone()
            wal_bytes = connection.execute(
                "SELECT coalesce(sum(size), 0) FROM pg_ls_waldir()"
            ).fetchone()
        available = _T097_GUEST_STORAGE_LIMIT_BYTES - int(database_bytes[0]) - int(wal_bytes[0])
        if available < _POSTGRES_MIN_TMPFS_FREE_BYTES:
            raise RuntimeError(
                "T097 PostgreSQL test storage has only "
                f"{available // (1024 * 1024)} MiB free (128 MiB required)"
            )
        return
    output = _docker(
        [
            "docker",
            "exec",
            service.metadata["container_id"],
            "df",
            "-Pk",
            "/var/lib/postgresql",
        ]
    )
    available = _postgres_tmpfs_available_bytes(output)
    if available < _POSTGRES_MIN_TMPFS_FREE_BYTES:
        raise RuntimeError(
            "Owned PostgreSQL test tmpfs has only "
            f"{available // (1024 * 1024)} MiB free (128 MiB required); stop the run and "
            "inspect WAL/database growth before continuing"
        )


@contextmanager
def _isolated_live_targets(
    home: Path,
    scopes: tuple[str, ...],
    service: PostgresService | None = None,
):
    """Provision runtime-only targets for standalone subprocess journeys."""
    from psycopg import sql

    from cli_agent_orchestrator.clients.postgres import DatabaseTarget
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    home = Path(home)
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = home.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or home.is_symlink() or metadata.st_mode & 0o077:
        raise RuntimeError("Live PostgreSQL home must be a private directory")
    service = service or _discover_postgres_service()
    suffix = secrets.token_hex(4)
    databases = {scope: f"c21live_{suffix}_{scope}" for scope in scopes}
    owners = {scope: f"c21live_{suffix}_{scope}_owner" for scope in scopes}
    runtimes = {scope: f"c21live_{suffix}_{scope}_app" for scope in scopes}
    owner_passwords = {scope: secrets.token_urlsafe(32) for scope in scopes}
    runtime_passwords = {scope: secrets.token_urlsafe(32) for scope in scopes}
    schemas = {scope: f"live_{suffix}_{scope}" for scope in scopes}
    role_names: list[str] = []
    database_names: list[str] = []
    created_files: list[Path] = []
    try:
        with service.connect_admin() as admin:
            for scope in scopes:
                for role, password in (
                    (owners[scope], owner_passwords[scope]),
                    (runtimes[scope], runtime_passwords[scope]),
                ):
                    admin.execute(
                        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                            sql.Identifier(role), sql.Literal(password)
                        )
                    )
                    role_names.append(role)
                admin.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(databases[scope]), sql.Identifier(owners[scope])
                    )
                )
                database_names.append(databases[scope])
                admin.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(databases[scope]), sql.Identifier(runtimes[scope])
                    )
                )

        config_targets: dict[str, dict[str, Any]] = {}
        owner_password_files: dict[str, Path] = {}
        runtime_password_files: dict[str, Path] = {}
        for scope in scopes:
            owner_password_file = home / f"{scope}-owner.json"
            runtime_password_file = home / f"{scope}-runtime.json"
            _write_private(owner_password_file, json.dumps({"password": owner_passwords[scope]}))
            created_files.append(owner_password_file)
            _write_private(
                runtime_password_file, json.dumps({"password": runtime_passwords[scope]})
            )
            created_files.append(runtime_password_file)
            owner_password_files[scope] = owner_password_file
            runtime_password_files[scope] = runtime_password_file
            config_targets[scope] = {
                "host": service.metadata["host"],
                "port": service.metadata["port"],
                "database": databases[scope],
                "user": runtimes[scope],
                "schema": schemas[scope],
                "store_id": str(uuid4()),
                "secret_file": runtime_password_file.name,
                "sslmode": "disable",
            }
        config_path = home / "database-config.json"
        _write_private(config_path, json.dumps({"version": 1, "targets": config_targets}, indent=2))
        created_files.append(config_path)

        runtime_targets: dict[str, DatabaseTarget] = {}
        for scope in scopes:
            target_data = config_targets[scope]
            with service.connect_admin(databases[scope]) as admin:
                admin.execute(
                    sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                        sql.Identifier(schemas[scope]), sql.Identifier(owners[scope])
                    )
                )
                admin.execute(
                    sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(
                        sql.Identifier(schemas[scope])
                    )
                )
                admin.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                        sql.Identifier(schemas[scope]), sql.Identifier(runtimes[scope])
                    )
                )
            target = DatabaseTarget(
                host=target_data["host"],
                port=target_data["port"],
                database=target_data["database"],
                user=target_data["user"],
                schema=target_data["schema"],
                store_id=target_data["store_id"],
                secret_file=runtime_password_files[scope],
                sslmode=target_data["sslmode"],
                home=home,
                scope=scope,
                migration_user=owners[scope],
                migration_secret_file=owner_password_files[scope],
            )
            bootstrap_scope(target)
            owner_password_files[scope].unlink()
            created_files.remove(owner_password_files[scope])
            runtime_targets[scope] = DatabaseTarget(
                host=target.host,
                port=target.port,
                database=target.database,
                user=target.user,
                schema=target.schema,
                store_id=target.store_id,
                secret_file=runtime_password_files[scope],
                sslmode=target.sslmode,
                home=home,
                scope=scope,
            )

        yield runtime_targets, config_path
    finally:
        for path in reversed(created_files):
            path.unlink(missing_ok=True)
        with service.connect_admin() as admin:
            for database in reversed(database_names):
                admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=%s AND pid<>pg_backend_pid()",
                    (database,),
                )
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database))
                )
            for role in reversed(role_names):
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


@contextmanager
def isolated_live_targets(home: Path, *, service: PostgresService | None = None):
    """Provision private runtime-only main, auth, and peer targets for a subprocess."""
    with _isolated_live_targets(home, ("main", "auth", "peer"), service=service) as (
        targets,
        config_path,
    ):
        yield PostgresTargets(
            main=targets["main"],
            auth=targets["auth"],
            peer=targets["peer"],
            home=Path(home),
            config_file=config_path,
        )


@contextmanager
def isolated_live_main_target(home: Path):
    """Provision one private main target for an opt-in live-provider journey."""
    with _isolated_live_targets(home, ("main",)) as (targets, _config_path):
        yield targets["main"]


@dataclass(frozen=True)
class _Worker:
    service: PostgresService
    prefix: str
    databases: dict[str, str]
    owners: dict[str, str]
    runtimes: dict[str, str]
    owner_passwords: dict[str, str] = field(repr=False)
    runtime_passwords: dict[str, str] = field(repr=False)


@dataclass(frozen=True)
class PostgresTargets(Mapping[str, Any]):
    main: Any
    auth: Any
    peer: Any
    home: Path
    config_file: Path
    schema_fingerprints: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    def __getitem__(self, key: str):
        if key not in ("main", "auth", "peer"):
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self) -> Iterator[str]:
        return iter(("main", "auth", "peer"))

    def __len__(self) -> int:
        return 3


def _retire_memory_audit_targets(targets: PostgresTargets) -> None:
    """Close pooled audit sessions owned by these fixture target identities."""
    audit_module = sys.modules.get("cli_agent_orchestrator.services.legacy_memory_audit_repository")
    if audit_module is None:
        return
    manager = audit_module._legacy_memory_audit_manager()
    for target in (targets.main, targets.auth, targets.peer):
        manager.retire_target(target)


def make_orm_engine(target):
    """Create an isolated SQLAlchemy facade over one explicit native target."""
    import psycopg
    from psycopg import sql
    from sqlalchemy import create_engine, event

    from cli_agent_orchestrator.clients.postgres.storage_pool import StorageQueuePool

    engine = create_engine(
        "postgresql+psycopg://",
        creator=lambda: psycopg.connect(**target.connection_parameters()),
        poolclass=StorageQueuePool,
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def configure(connection, record):
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("SET search_path TO pg_catalog, {}").format(sql.Identifier(target.schema))
            )
            cursor.execute("SET TIME ZONE 'UTC'")
            cursor.execute("SET statement_timeout TO '30s'")
            cursor.execute("SET lock_timeout TO '10s'")
        connection.commit()
        record.info["cao_target_identity"] = target.identity

    return engine


def install_core_runtime_components(target) -> None:
    """Explicitly provision only the core and runtime-channel test components."""
    from cli_agent_orchestrator.clients.database import core_migrations
    from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
    from cli_agent_orchestrator.clients.runtime_channel_schema import runtime_channel_migrations

    migrate_component(target, core_migrations())
    migrate_component(target, runtime_channel_migrations())


@pytest.fixture(scope="session")
def postgres_service() -> Iterator[PostgresService]:
    """Use a verified service or provision one; never silently skip PG coverage."""
    metadata_path = os.environ.get("CAO_TEST_POSTGRES_METADATA_FILE")
    if metadata_path:
        try:
            service = _service_from_metadata_path(Path(metadata_path))
        except RuntimeError as error:
            pytest.fail(f"Configured PostgreSQL test service is invalid: {error}", pytrace=False)
        yield service
        return

    if shutil.which("docker") is None:
        pytest.fail(
            "PostgreSQL tests require Docker or CAO_TEST_POSTGRES_METADATA_FILE pointing to "
            "a private, verified test service; install/start Docker and run `uv run pytest`",
            pytrace=False,
        )
    try:
        candidates = _docker(
            ["docker", "ps", "--filter", f"label={OWNER_LABEL}={OWNER_VALUE}", "--quiet"]
        ).splitlines()
    except RuntimeError as error:
        pytest.fail(
            f"Cannot inspect Docker for the PostgreSQL test service: {error}", pytrace=False
        )

    if len(candidates) == 1:
        try:
            service = _discover_postgres_service()
        except RuntimeError as error:
            pytest.fail(f"Owned PostgreSQL test service is invalid: {error}", pytrace=False)
        yield service
        return
    if candidates:
        pytest.fail(
            "Multiple owned PostgreSQL test services are running; set "
            "CAO_TEST_POSTGRES_METADATA_FILE to select the private service for this test run",
            pytrace=False,
        )
    if os.environ.get("CAO_TEST_POSTGRES_AUTOSTART", "1") != "1":
        pytest.fail(
            "No owned PostgreSQL test service is running and automatic provisioning is disabled; "
            "start the pinned service or set CAO_TEST_POSTGRES_METADATA_FILE",
            pytrace=False,
        )

    old_metadata_path = os.environ.get("CAO_TEST_POSTGRES_METADATA_FILE")
    try:
        with _provision_owned_postgres_service() as service:
            os.environ["CAO_TEST_POSTGRES_METADATA_FILE"] = str(
                Path(service.metadata["password_file"]).with_name("metadata.json")
            )
            yield service
    finally:
        if old_metadata_path is None:
            os.environ.pop("CAO_TEST_POSTGRES_METADATA_FILE", None)
        else:
            os.environ["CAO_TEST_POSTGRES_METADATA_FILE"] = old_metadata_path


@pytest.fixture(scope="session")
def _postgres_worker(postgres_service: PostgresService) -> Iterator[_Worker]:
    """Provision three databases and distinct owner/runtime roles per worker."""
    from psycopg import sql

    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    suffix = hashlib.sha256(worker.encode()).hexdigest()[:5] + secrets.token_hex(3)
    prefix = f"c21_{suffix}"
    scopes = ("main", "auth", "peer")
    databases = {scope: f"{prefix}_{scope}" for scope in scopes}
    owners = {scope: f"{prefix}_{scope}_owner" for scope in scopes}
    runtimes = {scope: f"{prefix}_{scope}_app" for scope in scopes}
    owner_passwords = {scope: secrets.token_urlsafe(32) for scope in scopes}
    runtime_passwords = {scope: secrets.token_urlsafe(32) for scope in scopes}
    created_roles: list[str] = []
    created_databases: list[str] = []
    try:
        with postgres_service.connect_admin() as admin:
            for scope in scopes:
                for role, password in (
                    (owners[scope], owner_passwords[scope]),
                    (runtimes[scope], runtime_passwords[scope]),
                ):
                    admin.execute(
                        sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                            sql.Identifier(role), sql.Literal(password)
                        )
                    )
                    created_roles.append(role)
                admin.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(databases[scope]), sql.Identifier(owners[scope])
                    )
                )
                created_databases.append(databases[scope])
                admin.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(databases[scope]), sql.Identifier(runtimes[scope])
                    )
                )
        result = _Worker(
            postgres_service,
            prefix,
            databases,
            owners,
            runtimes,
            owner_passwords,
            runtime_passwords,
        )
        yield result
    finally:
        with postgres_service.connect_admin() as admin:
            for database in reversed(created_databases):
                admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()",
                    (database,),
                )
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database))
                )
            for role in reversed(created_roles):
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


@pytest.fixture
def postgres_targets(request, monkeypatch, tmp_path, _postgres_worker: _Worker) -> PostgresTargets:
    """Create exact per-test schemas/config; callers explicitly apply migrations."""
    from psycopg import sql

    from cli_agent_orchestrator import constants
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.clients.postgres import DatabaseTarget

    worker = _postgres_worker
    test_hash = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:10]
    home = tmp_path / "cao-postgres-home"
    home.mkdir(mode=0o700)
    config_targets: dict[str, dict[str, Any]] = {}
    for scope in ("main", "auth", "peer"):
        schema = f"t_{test_hash}_{secrets.token_hex(3)}_{scope}"
        owner_secret = f"{scope}-migration.json"
        runtime_secret = f"{scope}-runtime.json"
        _write_private(home / owner_secret, json.dumps({"password": worker.owner_passwords[scope]}))
        _write_private(
            home / runtime_secret, json.dumps({"password": worker.runtime_passwords[scope]})
        )
        config_targets[scope] = {
            "host": worker.service.metadata["host"],
            "port": worker.service.metadata["port"],
            "database": worker.databases[scope],
            "user": worker.runtimes[scope],
            "schema": schema,
            "store_id": str(uuid4()),
            "secret_file": runtime_secret,
            "sslmode": "disable",
            "migration_user": worker.owners[scope],
            "migration_secret_file": owner_secret,
        }
    config_path = home / "database-config.json"
    _write_private(config_path, json.dumps({"version": 1, "targets": config_targets}, indent=2))
    monkeypatch.setenv("CAO_DATABASE_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("CAO_HOME_DIR", str(home))
    monkeypatch.setattr(constants, "CAO_HOME_DIR", home)
    monkeypatch.setattr(constants, "DATABASE_FILE", home / "postgres-only.db")
    database.engine.dispose()

    created: list[tuple[str, str]] = []
    targets: PostgresTargets | None = None
    try:
        for scope in ("main", "auth", "peer"):
            schema = config_targets[scope]["schema"]
            with worker.service.connect_admin(worker.databases[scope]) as admin:
                admin.execute(
                    sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                        sql.Identifier(schema), sql.Identifier(worker.owners[scope])
                    )
                )
                created.append((worker.databases[scope], schema))
                admin.execute(
                    sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(schema))
                )
                admin.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                        sql.Identifier(schema), sql.Identifier(worker.runtimes[scope])
                    )
                )

        loaded = {
            scope: DatabaseTarget.from_settings(scope=scope, home=home) for scope in config_targets
        }
        targets = PostgresTargets(loaded["main"], loaded["auth"], loaded["peer"], home, config_path)
        yield targets
    finally:
        # This fixture owns temporary schemas in the worker databases, not the
        # databases themselves. Retire only pooled audit connections bound to
        # those exact target identities; worker-scoped sessions share the same
        # database and must remain alive across sibling test schemas.
        if targets is not None:
            _retire_memory_audit_targets(targets)
        database.engine.dispose()
        for database_name, schema in created:
            with worker.service.connect_admin(database_name) as admin:
                admin.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
                )


def _worker_component_tables(target) -> tuple[str, ...]:
    from cli_agent_orchestrator.clients.postgres.registry import component_migrations

    return tuple(
        sorted(
            {
                "_cao_schema_migrations",
                "_cao_store_identity",
                *(
                    table
                    for component in component_migrations(target.scope)
                    for migration in component
                    for table in migration.tables
                ),
            }
        )
    )


def _worker_schema_fingerprint(target) -> str:
    import hashlib
    import json

    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.postgres.migrations import catalog_manifest

    # This fixture owns the disposable namespace. Read its baseline with the
    # owner so a test that revokes runtime SELECT cannot prevent cleanup.
    with connect(target.migration_target(), readonly=True) as connection:
        manifest = catalog_manifest(connection, _worker_component_tables(target))
        manifest["migration_rows"] = [
            tuple(row)
            for row in connection.execute(
                "SELECT scope,component,version,checksum,manifest_digest "
                "FROM _cao_schema_migrations ORDER BY scope,component,version"
            ).fetchall()
        ]
        manifest["identity_rows"] = [
            tuple(row)
            for row in connection.execute(
                "SELECT singleton,scope,store_id FROM _cao_store_identity ORDER BY singleton"
            ).fetchall()
        ]
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@pytest.fixture(scope="session")
def postgres_worker_targets(
    tmp_path_factory, _postgres_worker: _Worker
) -> Iterator[PostgresTargets]:
    """One pre-migrated, private schema set per pytest worker for ordinary tests."""
    from psycopg import sql

    from cli_agent_orchestrator.clients.postgres import DatabaseTarget
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    worker = _postgres_worker
    home = tmp_path_factory.mktemp("cao-postgres-worker-" + worker.prefix[-8:])
    os.chmod(home, 0o700)
    config_targets: dict[str, dict[str, Any]] = {}
    created: list[tuple[str, str]] = []
    for scope in ("main", "auth", "peer"):
        schema = f"w_{worker.prefix[-8:]}_{scope}"
        owner_secret = f"{scope}-migration.json"
        runtime_secret = f"{scope}-runtime.json"
        _write_private(home / owner_secret, json.dumps({"password": worker.owner_passwords[scope]}))
        _write_private(
            home / runtime_secret, json.dumps({"password": worker.runtime_passwords[scope]})
        )
        config_targets[scope] = {
            "host": worker.service.metadata["host"],
            "port": worker.service.metadata["port"],
            "database": worker.databases[scope],
            "user": worker.runtimes[scope],
            "schema": schema,
            "store_id": str(uuid4()),
            "secret_file": runtime_secret,
            "sslmode": "disable",
            "migration_user": worker.owners[scope],
            "migration_secret_file": owner_secret,
        }
        with worker.service.connect_admin(worker.databases[scope]) as admin:
            admin.execute(
                sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                    sql.Identifier(schema), sql.Identifier(worker.owners[scope])
                )
            )
            admin.execute(
                sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(schema))
            )
            admin.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(worker.runtimes[scope])
                )
            )
        created.append((worker.databases[scope], schema))

    config_path = home / "database-config.json"
    _write_private(config_path, json.dumps({"version": 1, "targets": config_targets}, indent=2))
    targets_by_scope = {
        scope: DatabaseTarget.from_settings(scope=scope, home=home) for scope in config_targets
    }
    fingerprints: dict[str, str] = {}
    targets = PostgresTargets(
        targets_by_scope["main"],
        targets_by_scope["auth"],
        targets_by_scope["peer"],
        home,
        config_path,
        fingerprints,
    )
    for target in (targets.main, targets.auth, targets.peer):
        bootstrap_scope(target)
        fingerprints[target.scope] = _worker_schema_fingerprint(target)
    yield targets

    _retire_memory_audit_targets(targets)
    for database_name, schema in created:
        with worker.service.connect_admin(database_name) as admin:
            admin.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )


def reset_postgres_worker_targets(targets: PostgresTargets) -> None:
    """Clear one worker's test data, recovering only its schema if tests damaged DDL."""
    global _POSTGRES_WORKER_RESET_COUNT

    from psycopg import sql

    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.postgres.import_receipt_schema import IMPORT_RECEIPT_TABLE
    from cli_agent_orchestrator.clients.postgres.migrations import (
        SchemaMismatch,
        protect_runtime_authority_tables,
    )
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    _POSTGRES_WORKER_RESET_COUNT += 1
    if _POSTGRES_WORKER_RESET_COUNT % _POSTGRES_CAPACITY_GUARD_INTERVAL == 0:
        _guard_postgres_tmpfs_capacity()

    for target in (targets.main, targets.auth, targets.peer):
        expected_fingerprint = targets.schema_fingerprints[target.scope]
        try:
            actual_fingerprint = _worker_schema_fingerprint(target)
        except SchemaMismatch:
            actual_fingerprint = ""
        if actual_fingerprint != expected_fingerprint:
            # The schema name is generated by this fixture and lives in its
            # worker-owned database. Only this disposable test namespace is rebuilt.
            with connect(target.migration_target()) as connection, connection.transaction():
                connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(target.schema)
                    )
                )
                connection.execute(
                    sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                        sql.Identifier(target.schema), sql.Identifier(target.migration_user)
                    )
                )
                connection.execute(
                    sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(
                        sql.Identifier(target.schema)
                    )
                )
                connection.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                        sql.Identifier(target.schema), sql.Identifier(target.user)
                    )
                )
            bootstrap_scope(target)
            actual_fingerprint = _worker_schema_fingerprint(target)
            if actual_fingerprint != expected_fingerprint:
                raise RuntimeError("PostgreSQL worker schema did not recover its baseline")

        tables = [
            table
            for table in _worker_component_tables(target)
            if table not in {"_cao_schema_migrations", "_cao_store_identity"}
        ]
        with connect(target.migration_target()) as connection, connection.transaction():
            if tables:
                connection.execute(
                    sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(
                        sql.SQL(", ").join(sql.Identifier(target.schema, table) for table in tables)
                    )
                )
            # Tests may intentionally revoke a runtime privilege to prove an
            # authorization boundary. Restore the migration-defined runtime
            # grants between tests so one such assertion cannot contaminate
            # later tests sharing this worker schema. The schema owner keeps
            # DDL rights; the runtime role gets DML and function execution,
            # while schema creation and writes to the ledger tables remain
            # denied.
            connection.execute(
                sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(target.schema))
            )
            connection.execute(
                sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(
                    sql.Identifier(target.schema), sql.Identifier(target.user)
                )
            )
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(target.schema), sql.Identifier(target.user)
                )
            )
            connection.execute(
                sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(
                    sql.Identifier(target.schema), sql.Identifier(target.user)
                )
            )
            connection.execute(
                sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM PUBLIC").format(
                    sql.Identifier(target.schema)
                )
            )
            connection.execute(
                sql.SQL(
                    "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {} TO {}"
                ).format(sql.Identifier(target.schema), sql.Identifier(target.user))
            )
            for table in (
                "_cao_schema_migrations",
                "_cao_store_identity",
                IMPORT_RECEIPT_TABLE,
            ):
                connection.execute(
                    sql.SQL("REVOKE ALL ON {}.{} FROM {}").format(
                        sql.Identifier(target.schema),
                        sql.Identifier(table),
                        sql.Identifier(target.user),
                    )
                )
                connection.execute(
                    sql.SQL("GRANT SELECT ON {}.{} TO {}").format(
                        sql.Identifier(target.schema),
                        sql.Identifier(table),
                        sql.Identifier(target.user),
                    )
                )
            protect_runtime_authority_tables(connection, target)
            connection.execute(
                sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM {}").format(
                    sql.Identifier(target.schema), sql.Identifier(target.user)
                )
            )
            connection.execute(
                sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM PUBLIC").format(
                    sql.Identifier(target.schema)
                )
            )
            connection.execute(
                sql.SQL("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                    sql.Identifier(target.schema), sql.Identifier(target.user)
                )
            )
            connection.execute(
                sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {} FROM PUBLIC").format(
                    sql.Identifier(target.schema)
                )
            )
            connection.execute(
                sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {} FROM {}").format(
                    sql.Identifier(target.schema), sql.Identifier(target.user)
                )
            )
            connection.execute(
                sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {} TO {}").format(
                    sql.Identifier(target.schema), sql.Identifier(target.user)
                )
            )
        if target.scope == "main":
            from cli_agent_orchestrator.clients.work_repository import bootstrap_work_store

            bootstrap_work_store(target)


@pytest.fixture
def postgres_core(postgres_targets):
    """Explicitly install the core ORM migration component in the main target."""
    from cli_agent_orchestrator.clients.database import core_migrations
    from cli_agent_orchestrator.clients.postgres.migrations import migrate_component

    migrate_component(postgres_targets.main, core_migrations())
    return postgres_targets.main


@pytest.fixture
def postgres_runtime_channel(postgres_targets):
    """Explicitly install the runtime-channel component in the main target."""
    from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
    from cli_agent_orchestrator.clients.runtime_channel_schema import runtime_channel_migrations

    target = postgres_targets.main
    migrate_component(target, runtime_channel_migrations())
    return target


@pytest.fixture
def postgres_core_runtime(postgres_targets):
    """Provision core plus ordinary runtime-channel state, without Work authority."""
    target = postgres_targets.main
    install_core_runtime_components(target)
    return target
