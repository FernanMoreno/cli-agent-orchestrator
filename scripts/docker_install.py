#!/usr/bin/env python3
"""Install the complete local CAO application in Docker, preserving personal state."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path.home() / ".local/share/cao-personal"
SOCKET = Path("/var/run/docker.sock")
LABEL = "org.cao.personal.root"
INSTANCE_LABEL = "org.cao.personal.installation"
DATABASE_SERVICE_LABEL = "org.cao.personal.database.service"
DATABASE_OWNER_LABEL = "org.cao.personal.database.owner"
DATABASE_INSTANCE_LABEL = "org.cao.personal.database.instance"
POSTGRES_IMAGE = (
    "postgres:18.6-trixie@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336"
)
_RESTORE_ADMIN_HOME = Path("/run/cao-admin-home")
_ADMIN_WORKING_DIRECTORY = Path("/opt/cao")


def mount_argument(source: Path, destination: Path | None = None, *, readonly=False) -> str:
    source = source.absolute()
    destination = (destination or source).absolute()
    if any(character in str(path) for path in (source, destination) for character in ",\n\r\x00"):
        raise ValueError("invalid mount path")
    return f"type=bind,src={source},dst={destination}" + (",readonly" if readonly else "")


def _admin_container_paths(path):
    path = Path(path)
    if not path.is_absolute():
        path = _ADMIN_WORKING_DIRECTORY / path
    absolute = Path(os.path.abspath(path))
    return {absolute, absolute.resolve(strict=False)}


def _validate_restore_admin_home_paths(root, arguments, selected_config, readonly_mounts):
    admin_home = Path(os.path.abspath(_RESTORE_ADMIN_HOME))
    protected_paths = [("restore destination", root, True)]
    if len(arguments) > 1:
        protected_paths.append(("restore source", Path(arguments[1]), True))
    protected_paths.append(("database config", selected_config, False))
    for _, target in readonly_mounts:
        # mount_argument() makes the target absolute relative to the host cwd.
        protected_paths.append(("read-only mount target", Path(os.path.abspath(target)), True))

    for label, raw_path, is_directory_scope in protected_paths:
        overlaps = False
        for path in _admin_container_paths(raw_path):
            overlaps = overlaps or path == admin_home or path.is_relative_to(admin_home)
            if is_directory_scope:
                overlaps = overlaps or admin_home.is_relative_to(path)
        if overlaps:
            raise ValueError(f"{label} overlaps private restore home")


def private_directory(path: Path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError(f"private owner-only directory required: {path}")


def container_arguments(root, image, name, config, mounts, *, port=None, network=None):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}", name):
        raise ValueError("invalid container name")
    home = root.with_name(root.name + "-container-home")
    sockets = root.with_name(root.name + "-container-sockets")
    private_directory(home)
    private_directory(sockets)
    arguments = [
        "run",
        "--detach",
        "--init",
        "--name",
        name,
        "--restart",
        "unless-stopped",
        "--stop-timeout",
        "30",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--group-add",
        str(SOCKET.stat().st_gid),
        "--label",
        f"{LABEL}={root}",
        "--publish",
        f"127.0.0.1:{config['server_port'] if port is None else port}:8080",
        "--env",
        f"HOME={Path.home()}",
        "--env",
        f"CAO_PERSONAL_ROOT={root}",
        "--env",
        f"CAO_HOME_DIR={root / 'cao'}",
        "--env",
        f"CAO_DATABASE_CONFIG_FILE={root / 'cao' / 'database-config.json'}",
        "--mount",
        mount_argument(root),
        "--mount",
        mount_argument(home, Path.home()),
        "--mount",
        mount_argument(sockets, root.with_name(root.name + "-sockets")),
        "--mount",
        mount_argument(SOCKET),
    ]
    if network is not None:
        arguments[1:1] = ["--network", network, "--network-alias", "cao-app"]
    projects = [mount["target"] for mount in mounts if mount.get("purpose") == "project"]
    if projects:
        arguments += ["--env", "CAO_REGISTERED_PROJECTS=" + json.dumps(projects)]
    for mount in mounts:
        if mount.get("purpose") == "obsidian-vault":
            arguments += ["--env", "CLAUDE_OBSIDIAN_VAULT=" + mount["target"]]
        arguments += [
            "--mount",
            mount_argument(
                Path(mount["source"]), Path(mount["target"]), readonly=mount.get("readonly", False)
            ),
        ]
    return arguments + [image]


def write_manifest(path: Path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as writer:
            json.dump(value, writer, indent=2)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_home(image, root):
    """Create writable HOME parents before Docker creates nested mount points as root."""
    home = root.with_name(root.name + "-container-home")
    private_directory(home)
    code = (
        "import os,sys;from pathlib import Path;root=Path('/home-state');"
        "paths=['.local','.local/share','.local/state','.cache','.config','.local/share/playwright-mcp'];"
        "uid,gid=map(int,sys.argv[1:]);"
        "\nfor relative in paths:\n"
        " p=root/relative\n"
        " if p.is_symlink(): raise ValueError('HOME parent symlink rejected')\n"
        " p.mkdir(mode=0o700,parents=True,exist_ok=True);os.chown(p,uid,gid)\n"
        "lib=root/'.local/share/playwright-mcp/lib'\n"
        "if lib.is_symlink():\n"
        " if os.readlink(lib)!='/usr/lib/x86_64-linux-gnu': raise ValueError('unexpected browser library link')\n"
        "else:\n"
        " if lib.exists(): lib.rmdir()\n"
        " lib.symlink_to('/usr/lib/x86_64-linux-gnu')\n"
    )
    docker(
        [
            "run",
            "--rm",
            "--user",
            "0:0",
            "--mount",
            mount_argument(home, Path("/home-state")),
            "--entrypoint",
            "python",
            image,
            "-c",
            code,
            str(os.getuid()),
            str(os.getgid()),
        ]
    )


def cutover(stop_previous, restore_previous, start_candidate, stop_candidate, verify, commit):
    candidate_attempted = False
    try:
        stop_previous()
        candidate_attempted = True
        start_candidate()
        verify()
        commit()
    except BaseException:
        try:
            if candidate_attempted:
                stop_candidate()
        finally:
            restore_previous()
        raise


def docker(arguments, *, check=True, input_file=None, input_data=None, stream=False):
    executable = shutil.which("docker")
    if not executable:
        raise RuntimeError(
            "Install Docker and enable its Linux/WSL integration before running install.sh"
        )
    env = os.environ.copy()
    for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        env.pop(key, None)
    env["DOCKER_HOST"] = f"unix://{SOCKET}"
    if input_file is not None and input_data is not None:
        raise ValueError("provide only one Docker stdin source")
    result = subprocess.run(
        [executable, *arguments],
        env=env,
        stdin=input_file,
        input=input_data,
        stdout=None if stream else subprocess.PIPE,
        stderr=None if stream else subprocess.PIPE,
        text=True,
        timeout=1800 if arguments[0] == "build" else 120 if arguments[0] == "run" else 45,
    )
    if check and result.returncode:
        # Arguments contain no credentials; do not echo daemon responses carrying owner data.
        raise RuntimeError(f"Docker {arguments[0]} failed (exit {result.returncode})")
    return result


def inspect(name):
    result = docker(["inspect", name], check=False)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None


def inspect_network(name):
    result = docker(["network", "inspect", name], check=False)
    return json.loads(result.stdout)[0] if result.returncode == 0 else None


def image_id(tag):
    result = docker(["image", "inspect", "--format", "{{.Id}}", tag])
    value = result.stdout.strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise RuntimeError("Docker returned an invalid image ID")
    return value


def _private_file(path: Path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("private regular database secret required")
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise ValueError("database secret must be owner-only and owned by the operator")


def _write_private(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_directory(path.parent)
    if path.exists() or path.is_symlink():
        _private_file(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _secret(path: Path):
    if path.exists() or path.is_symlink():
        _private_file(path)
        value = json.loads(path.read_text())
        password = value.get("password") if isinstance(value, dict) else None
        if not isinstance(password, str) or not password:
            raise ValueError("invalid private database secret")
        return password
    password = secrets.token_urlsafe(48)
    _write_private(path, json.dumps({"password": password}).encode())
    return password


def _password_file(path: Path, password: str):
    if path.exists() or path.is_symlink():
        _private_file(path)
        if path.read_bytes() != (password + "\n").encode("utf-8"):
            raise ValueError("PostgreSQL bootstrap secret differs from its stored role secret")
        return
    _write_private(path, (password + "\n").encode())


def _sql_literal(value: str):
    return "'" + value.replace("'", "''") + "'"


def _role_sql(roles):
    statements = []
    for role, password in roles:
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
            raise ValueError("invalid managed database role")
        literal = _sql_literal(password)
        statements.append(
            f"DO $cao$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname={_sql_literal(role)}) "
            f'THEN CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
            f"NOBYPASSRLS PASSWORD {literal}; END IF; END $cao$;"
        )
        statements.append(
            f'ALTER ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
            f"NOBYPASSRLS PASSWORD {literal};"
        )
    return "\n".join(statements) + "\n"


def _instance_id(config):
    value = config.get("instance_id")
    if value is None:
        value = config.get("issuer", "").rsplit(":", 1)[-1]
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("invalid personal database installation identity")
    return value


def _ensure_network(name, labels):
    current = inspect_network(name)
    if current is not None:
        actual = current.get("Labels") or {}
        if any(actual.get(key) != value for key, value in labels.items()):
            raise RuntimeError("Docker network name belongs to another deployment")
        return
    arguments = ["network", "create", "--driver", "bridge"]
    for key, value in sorted(labels.items()):
        arguments += ["--label", f"{key}={value}"]
    docker(arguments + [name])


def _ensure_postgres_container(name, network, alias, data, password_file, owner, database, labels):
    current = inspect(name)
    if current is not None:
        actual = current.get("Config", {}).get("Labels") or {}
        if any(actual.get(key) != value for key, value in labels.items()):
            raise RuntimeError("PostgreSQL container name belongs to another deployment")
        if current.get("Config", {}).get("Image") != POSTGRES_IMAGE:
            raise RuntimeError("managed PostgreSQL image differs from the pinned deployment image")
        if not current["State"]["Running"]:
            docker(["start", name])
    else:
        private_directory(data)
        docker(
            [
                "run",
                "--detach",
                "--init",
                "--name",
                name,
                "--restart",
                "unless-stopped",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--network",
                network,
                "--network-alias",
                alias,
                *[
                    value
                    for key, item in sorted(labels.items())
                    for value in ("--label", f"{key}={item}")
                ],
                "--mount",
                mount_argument(data, Path("/var/lib/postgresql")),
                "--mount",
                mount_argument(
                    password_file, Path("/run/secrets/postgres-password"), readonly=True
                ),
                "--env",
                f"POSTGRES_USER={owner}",
                "--env",
                f"POSTGRES_DB={database}",
                "--env",
                "POSTGRES_PASSWORD_FILE=/run/secrets/postgres-password",
                "--env",
                "PGDATA=/var/lib/postgresql/18/docker",
                POSTGRES_IMAGE,
            ]
        )
    _attach_container_to_network(name, network, alias)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = docker(
            [
                "exec",
                name,
                "pg_isready",
                "--host",
                "127.0.0.1",
                "--username",
                owner,
                "--dbname",
                database,
            ],
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("managed PostgreSQL did not become ready")


def _attach_container_to_network(container, network, alias):
    current = inspect(container)
    networks = (current or {}).get("NetworkSettings", {}).get("Networks", {})
    if network in networks:
        aliases = networks[network].get("Aliases") or []
        if alias not in aliases:
            raise RuntimeError("managed PostgreSQL network alias differs")
        return
    docker(["network", "connect", "--alias", alias, network, container])


def _bootstrap_roles(container, owner, database, roles):
    docker(
        [
            "exec",
            "--interactive",
            "--user",
            "postgres",
            container,
            "psql",
            "--username",
            owner,
            "--dbname",
            database,
            "--set",
            "ON_ERROR_STOP=1",
        ],
        input_data=_role_sql(roles),
    )


def _target_password(target: dict, key="secret_file"):
    path = Path(target[key])
    if not path.is_absolute():
        raise ValueError("managed target secret paths must be absolute before bootstrap")
    _private_file(path)
    value = json.loads(path.read_text())
    password = value.get("password") if isinstance(value, dict) else None
    if not isinstance(password, str) or not password:
        raise ValueError("invalid managed PostgreSQL secret")
    return password


def _managed_peer(root: Path, network: str, *, isolated=False):
    from cli_agent_orchestrator.services.local_peers.host_identity import current_host_identity

    host_id = current_host_identity(
        Path.home() / ".aws" / "cli-agent-orchestrator" / "local-peers"
    ).host_id
    peer_root = (
        root.with_name(root.name + "-isolated-peer")
        if isolated
        else Path.home() / ".local/share/cao-peer-postgres"
    )
    private_directory(peer_root)
    secret_dir = peer_root / "secrets"
    data_dir = peer_root / "data"
    private_directory(secret_dir)
    private_directory(data_dir)
    settings_path = peer_root / "service.json"
    if settings_path.exists():
        _private_file(settings_path)
        settings = json.loads(settings_path.read_text())
        if settings.get("owner_id") != host_id or settings.get("isolated") is not isolated:
            raise RuntimeError("shared peer database owner identity differs")
    else:
        settings = {
            "owner_id": host_id,
            "isolated": isolated,
            "store_id": str(uuid.uuid4()),
            "container": "cao-peer-pg-"
            + host_id
            + ("-" + _instance_id(_read_deployment(root))[:8] if isolated else ""),
            "owner": "cao_peer_owner",
            "runtime": "cao_peer_runtime",
            "database": "cao_peer",
        }
        write_manifest(settings_path, settings)
    owner_json = secret_dir / "owner.json"
    runtime_json = secret_dir / "runtime.json"
    owner_password = _secret(owner_json)
    runtime_password = _secret(runtime_json)
    owner_file = secret_dir / "postgres-password"
    _password_file(owner_file, owner_password)
    _ensure_postgres_container(
        settings["container"],
        network,
        "peer-db",
        data_dir,
        owner_file,
        settings["owner"],
        settings["database"],
        {
            DATABASE_SERVICE_LABEL: "peer",
            DATABASE_OWNER_LABEL: host_id,
            DATABASE_INSTANCE_LABEL: settings["store_id"],
        },
    )
    _bootstrap_roles(
        settings["container"],
        settings["owner"],
        settings["database"],
        ((settings["runtime"], runtime_password),),
    )
    peer_networks = (
        (inspect(settings["container"]) or {}).get("NetworkSettings", {}).get("Networks", {})
    )
    if network not in peer_networks:
        docker(["network", "connect", "--alias", "peer-db", network, settings["container"]])
    return settings, owner_password, runtime_password, owner_json, runtime_json, peer_root


def _read_deployment(root: Path):
    path = root / "deployment.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("personal deployment configuration is required")
    value = json.loads(path.read_text())
    if value.get("host") != "127.0.0.1":
        raise ValueError("personal deployment must bind to loopback")
    return value


def _copy_secret(source: Path, destination: Path):
    _private_file(source)
    value = json.loads(source.read_text())
    if not isinstance(value, dict) or not isinstance(value.get("password"), str):
        raise ValueError("invalid PostgreSQL secret file")
    _write_private(destination, json.dumps(value).encode())


def _external_database_config(
    root: Path,
    source: Path,
):
    _private_file(source)
    payload = json.loads(source.read_text())
    if payload.get("version") != 1 or set(payload.get("targets", {})) != {"main", "auth", "peer"}:
        raise ValueError("external PostgreSQL configuration must define all three scopes")
    secret_dir = root / "cao/postgres/secrets"
    private_directory(secret_dir)
    targets = payload["targets"]
    for scope, target in targets.items():
        for key in ("secret_file", "migration_secret_file"):
            value = target.get(key)
            if not value:
                continue
            secret_source = Path(value)
            if not secret_source.is_absolute():
                secret_source = source.parent / secret_source
            target[key] = str((secret_dir / f"external-{scope}-{key}.json").absolute())
            _copy_secret(secret_source, Path(target[key]))
    destination = root / "cao" / "database-config.json"
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination.name)
    write_manifest(destination, {"version": 1, "targets": targets})


def _managed_database_config(
    root: Path,
    network: str,
    *,
    isolated=False,
):
    deployment = _read_deployment(root)
    instance = _instance_id(deployment)
    profile = instance[:12]
    database_root = root / "cao/postgres"
    secrets_dir = database_root / "secrets"
    data_dir = database_root / "data"
    private_directory(database_root)
    private_directory(secrets_dir)
    private_directory(data_dir)
    owner_name = "cao_o_" + profile
    main_runtime = "cao_m_" + profile
    auth_runtime = "cao_a_" + profile
    owner_json = secrets_dir / "main-owner.json"
    main_json = secrets_dir / "main-runtime.json"
    auth_json = secrets_dir / "auth-runtime.json"
    owner_password = _secret(owner_json)
    main_password = _secret(main_json)
    auth_password = _secret(auth_json)
    owner_file = secrets_dir / "postgres-password"
    _password_file(owner_file, owner_password)
    container = "cao-main-pg-" + profile
    _ensure_postgres_container(
        container,
        network,
        "main-db",
        data_dir,
        owner_file,
        owner_name,
        "cao",
        {
            DATABASE_SERVICE_LABEL: "main-auth",
            DATABASE_OWNER_LABEL: instance,
            DATABASE_INSTANCE_LABEL: instance,
        },
    )
    _bootstrap_roles(
        container, owner_name, "cao", ((main_runtime, main_password), (auth_runtime, auth_password))
    )
    (
        peer,
        _peer_owner_password,
        peer_runtime_password,
        peer_owner_source,
        peer_runtime_source,
        peer_root,
    ) = _managed_peer(root, network, isolated=isolated)
    # The app container needs only the same two private credential documents;
    # the shared peer data directory and server secret are not mounted into it.
    peer_owner_copy = secrets_dir / "peer-owner.json"
    peer_runtime_copy = secrets_dir / "peer-runtime.json"
    _copy_secret(peer_owner_source, peer_owner_copy)
    _copy_secret(peer_runtime_source, peer_runtime_copy)
    namespace = uuid.UUID(instance)
    targets = {
        "main": {
            "host": "main-db",
            "port": 5432,
            "database": "cao",
            "user": main_runtime,
            "schema": "cao_main",
            "store_id": str(uuid.uuid5(namespace, "main")),
            "secret_file": "postgres/secrets/main-runtime.json",
            "sslmode": "disable",
            "migration_user": owner_name,
            "migration_secret_file": "postgres/secrets/main-owner.json",
        },
        "auth": {
            "host": "main-db",
            "port": 5432,
            "database": "cao",
            "user": auth_runtime,
            "schema": "cao_auth",
            "store_id": str(uuid.uuid5(namespace, "auth")),
            "secret_file": "postgres/secrets/auth-runtime.json",
            "sslmode": "disable",
            "migration_user": owner_name,
            "migration_secret_file": "postgres/secrets/main-owner.json",
        },
        "peer": {
            "host": "peer-db",
            "port": 5432,
            "database": peer["database"],
            "user": peer["runtime"],
            "schema": "cao_peer",
            "store_id": peer["store_id"],
            "secret_file": "postgres/secrets/peer-runtime.json",
            "sslmode": "disable",
            "migration_user": peer["owner"],
            "migration_secret_file": "postgres/secrets/peer-owner.json",
        },
    }
    config_path = root / "cao" / "database-config.json"
    if config_path.exists() or config_path.is_symlink():
        raise FileExistsError(config_path.name)
    write_manifest(
        config_path,
        {
            "version": 1,
            "targets": targets,
            "managed": {
                "image": POSTGRES_IMAGE,
                "profile_network": network,
                "main_container": container,
                "peer_container": peer["container"],
                "peer_root": str(peer_root),
                "isolated": isolated,
            },
        },
    )
    return config_path, peer["container"], peer_root


def _ensure_existing_managed_services(root: Path, network: str, config_payload: dict):
    managed = config_payload.get("managed") or {}
    if managed.get("isolated"):
        if not managed.get("profile_network") == network:
            raise RuntimeError("managed preflight network identity differs")
    elif managed.get("profile_network") != network:
        raise RuntimeError("managed PostgreSQL profile network identity differs")
    targets = config_payload["targets"]
    main = targets["main"]
    owner = main["migration_user"]
    owner_json = Path(main["migration_secret_file"])
    if not owner_json.is_absolute():
        owner_json = root / "cao" / owner_json
    owner_password = _target_password({"secret_file": str(owner_json)})
    main_runtime = _target_password(
        {"secret_file": str(root / "cao" / targets["main"]["secret_file"])}
    )
    auth_runtime = _target_password(
        {"secret_file": str(root / "cao" / targets["auth"]["secret_file"])}
    )
    instance = _instance_id(_read_deployment(root))
    main_container = managed.get("main_container")
    if not isinstance(main_container, str):
        raise RuntimeError("managed main PostgreSQL container identity is missing")
    expected_main_container = "cao-main-pg-" + instance[:12]
    if main_container != expected_main_container:
        raise RuntimeError("managed main PostgreSQL container identity differs")
    main_secret_file = root / "cao/postgres/secrets/postgres-password"
    _password_file(main_secret_file, owner_password)
    labels = {
        DATABASE_SERVICE_LABEL: "main-auth",
        DATABASE_OWNER_LABEL: instance,
        DATABASE_INSTANCE_LABEL: instance,
    }
    _ensure_postgres_container(
        main_container,
        network,
        "main-db",
        root / "cao/postgres/data",
        main_secret_file,
        owner,
        "cao",
        labels,
    )
    _bootstrap_roles(
        main_container,
        owner,
        "cao",
        ((targets["main"]["user"], main_runtime), (targets["auth"]["user"], auth_runtime)),
    )

    peer_container = managed.get("peer_container")
    peer_root_value = managed.get("peer_root")
    if not isinstance(peer_container, str) or not isinstance(peer_root_value, str):
        raise RuntimeError("managed shared peer store identity is missing")
    peer_root = Path(peer_root_value)
    peer_settings_path = peer_root / "service.json"
    _private_file(peer_settings_path)
    peer = json.loads(peer_settings_path.read_text())
    if peer.get("container") != peer_container or peer.get("isolated") != managed.get(
        "isolated", False
    ):
        raise RuntimeError("managed shared peer store configuration differs")
    peer_owner_file = peer_root / "secrets/postgres-password"
    peer_owner_json = peer_root / "secrets/owner.json"
    peer_runtime_json = peer_root / "secrets/runtime.json"
    peer_owner_password = _target_password({"secret_file": str(peer_owner_json)})
    peer_runtime_password = _target_password({"secret_file": str(peer_runtime_json)})
    _password_file(peer_owner_file, peer_owner_password)
    peer_labels = {
        DATABASE_SERVICE_LABEL: "peer",
        DATABASE_OWNER_LABEL: peer["owner_id"],
        DATABASE_INSTANCE_LABEL: peer["store_id"],
    }
    _ensure_postgres_container(
        peer_container,
        network,
        "peer-db",
        peer_root / "data",
        peer_owner_file,
        peer["owner"],
        peer["database"],
        peer_labels,
    )
    _bootstrap_roles(
        peer_container, peer["owner"], peer["database"], ((peer["runtime"], peer_runtime_password),)
    )
    return peer_container, peer_root


def ensure_database(
    root: Path,
    *,
    external_config: Path | None = None,
    isolated=False,
):
    """Prepare managed PG for a new profile without replacing existing state."""
    deployment = _read_deployment(root)
    instance = _instance_id(deployment)
    network = "cao-profile-" + instance[:12] + ("-preflight" if isolated else "")
    labels = {
        DATABASE_SERVICE_LABEL: "profile-network",
        DATABASE_INSTANCE_LABEL: instance,
        DATABASE_OWNER_LABEL: hashlib.sha256(
            f"{socket.gethostname()}:{os.getuid()}".encode()
        ).hexdigest()[:16],
    }
    if isolated:
        labels[DATABASE_SERVICE_LABEL] = "preflight-network"
    config_path = root / "cao" / "database-config.json"
    created = False
    peer_container = None
    peer_root = None
    if config_path.exists() or config_path.is_symlink():
        _private_file(config_path)
        target_map = json.loads(config_path.read_text())
        managed = target_map.get("managed")
        if managed:
            _ensure_network(network, labels)
            peer_container, peer_root = _ensure_existing_managed_services(root, network, target_map)
        elif external_config is not None:
            raise ValueError("an external database configuration is already installed")
    else:
        cao_home = root / "cao"
        if cao_home.is_dir() and any(cao_home.iterdir()):
            raise RuntimeError(
                "unrecognized existing database state; refusing to initialize PostgreSQL"
            )
        _ensure_network(network, labels)
        if external_config is not None:
            _external_database_config(root, external_config)
            created = True
        else:
            config_path, peer_container, peer_root = _managed_database_config(
                root, network, isolated=isolated
            )
            created = True
    return network, created, peer_container, peer_root


def build_archive(destination: Path):
    """An explicit context also protects legacy builders that ignore per-file ignores."""
    excluded = {
        "node_modules",
        "__pycache__",
        "target",
        "web_ui",
        "_bin",
        "apps_static",
        "test-results",
        "playwright-report",
        "dist",
    }
    files = [
        Path(name)
        for name in (
            "pyproject.toml",
            "uv.lock",
            "README.md",
            "LICENSE",
            "NOTICE",
            "test/fixtures/work_contract_v1.json",
        )
    ]
    files += [
        Path("scripts") / name
        for name in (
            "hatch_build_tui_tag.py",
            "personal_deployment.py",
            "local_work_docker_demo.py",
            "docker_personal_runtime.py",
            "docker_windows_mcp.py",
        )
    ]
    for directory in ("src", "web", "cao_mcp_apps", "docker/personal"):
        for current, directories, names in os.walk(REPOSITORY / directory):
            directories[:] = [
                name for name in directories if name not in excluded and not name.startswith(".")
            ]
            files += [
                (Path(current) / name).relative_to(REPOSITORY)
                for name in names
                if not name.startswith((".env", "cao-tui"))
                and not name.endswith(
                    (".pyc", ".pem", ".db", ".sqlite", ".sqlite3", "-wal", "-shm")
                )
            ]
    with tarfile.open(destination, "w:gz") as archive:
        for relative in files:
            source = REPOSITORY / relative
            if source.is_symlink():
                raise ValueError(f"source build context refuses symlink: {relative}")
            archive.add(source, arcname=str(relative), recursive=False)


def build(tag, *, worker=False):
    source = REPOSITORY / "src/cli_agent_orchestrator/backends/docker_work_supervisor.c"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="cao-image-") as scratch:
        archive = Path(scratch) / "context.tar"
        build_archive(archive)
        with archive.open("rb") as reader:
            docker(
                [
                    "build",
                    "--target",
                    "worker-rootfs" if worker else "application",
                    "--build-arg",
                    f"CAO_WORK_SUPERVISOR_SOURCE_SHA256={digest}",
                    "-f",
                    "docker/personal/Dockerfile",
                    "--tag",
                    tag,
                    "-",
                ],
                input_file=reader,
                stream=True,
            )
    return image_id(tag)


def admin(
    image,
    root,
    arguments,
    *,
    network=None,
    database_config: Path | None = None,
    readonly_mounts=(),
):
    readonly_mounts = tuple(readonly_mounts)
    selected_config = database_config or (root / "cao" / "database-config.json")
    is_restore = bool(arguments and arguments[0] == "restore")
    if is_restore:
        # Check only paths exposed inside the container. The parent bind may be
        # /; it is not itself a restore artifact or mount target.
        _validate_restore_admin_home_paths(root, arguments, selected_config, readonly_mounts)
    command = [
        "run",
        "--rm",
        "--init",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
    ]
    if arguments and arguments[0] in {"backup", "restore"}:
        if SOCKET.is_symlink():
            raise RuntimeError("Docker socket path must be a Unix socket")
        try:
            socket_metadata = SOCKET.lstat()
        except OSError as exc:
            raise RuntimeError("Docker socket is unavailable for database maintenance") from exc
        if not stat.S_ISSOCK(socket_metadata.st_mode):
            raise RuntimeError("Docker socket path must be a Unix socket")
        command += [
            "--group-add",
            str(socket_metadata.st_gid),
            "--mount",
            mount_argument(SOCKET, Path("/var/run/docker.sock"), readonly=True),
        ]
    if network is not None:
        command += ["--network", network]
    if is_restore:
        command += [
            "--tmpfs",
            (
                f"{_RESTORE_ADMIN_HOME}:rw,noexec,nosuid,nodev,size=16m,"
                f"uid={os.getuid()},gid={os.getgid()},mode=0700"
            ),
        ]
        admin_home = _RESTORE_ADMIN_HOME
    else:
        admin_home = root
    command += [
        "--env",
        f"HOME={admin_home}",
        "--env",
        f"CAO_HOME_DIR={admin_home / 'cao'}",
        "--env",
        f"CAO_DATABASE_CONFIG_FILE={selected_config}",
        "--mount",
        mount_argument(root.parent),
    ]
    for source, target in readonly_mounts:
        command += ["--mount", mount_argument(Path(source), Path(target), readonly=True)]
    command += [
        "--entrypoint",
        "python",
        image,
        "/opt/cao/scripts/personal_deployment.py",
        "--root",
        str(root),
        *arguments,
    ]
    return docker(command)


def config_for(root):
    private_directory(root)
    for file in (root / "deployment.json", root / "issuer.pem"):
        if file.is_symlink() or file.stat().st_uid != os.getuid() or file.stat().st_mode & 0o077:
            raise ValueError("deployment files must remain private and owned by the operator")
    config = json.loads((root / "deployment.json").read_text())
    if config.get("host") != "127.0.0.1" or any(
        type(config.get(key)) is not int or not 1024 <= config[key] <= 65535 or config[key] == 8080
        for key in ("server_port", "jwks_port")
    ):
        raise ValueError("local deployment ports required; 8080 is reserved for the internal proxy")
    return config


def identity_fingerprint(root):
    config = config_for(root)
    identity = {
        key: config.get(key)
        for key in ("issuer", "audience", "subject", "scopes", "kid", "browser_login")
    }
    return hashlib.sha256(
        (root / "issuer.pem").read_bytes() + json.dumps(identity, sort_keys=True).encode()
    ).hexdigest()


def windows_mcp_mount():
    """Windows Studio remains an external integration; CAO itself stays in Docker."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10 uses the declared runtime dependency.
        import tomli as tomllib

    config_path = Path.home() / ".codex/config.toml"
    if not config_path.exists():
        return None
    config = tomllib.loads(config_path.read_text())
    arguments = [
        server.get("args", [])
        for server in config.get("mcp_servers", {}).values()
        if server.get("command") == "cmd.exe"
    ]
    if not arguments:
        return None
    executable = shutil.which("cmd.exe")
    if not executable:
        raise RuntimeError("configured Windows MCP requires WSL cmd.exe before migration")
    root = Path.home() / ".local/share/cao-windows-mcp"
    private_directory(root)
    previous = (root / "commands.json").read_text() if (root / "commands.json").exists() else None
    script = (REPOSITORY / "scripts/docker_windows_mcp.py").read_bytes()
    changed = not (root / "relay.py").exists() or (root / "relay.py").read_bytes() != script
    write_manifest(root / "commands.json", {"executable": executable, "arguments": arguments})
    changed |= previous != (root / "commands.json").read_text()
    (root / "relay.py").write_bytes(script)
    (root / "relay.py").chmod(0o600)
    unit = Path.home() / ".config/systemd/user/cao-windows-mcp.service"
    unit.parent.mkdir(parents=True, exist_ok=True)

    def quote(value):
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'

    content = (
        "[Unit]\nDescription=CAO external Windows MCP stdio relay\n"
        "[Service]\nExecStart="
        + quote(sys.executable)
        + " "
        + quote(root / "relay.py")
        + " --serve "
        + quote(root)
        + "\nRestart=on-failure\nUMask=0077\n"
        "[Install]\nWantedBy=default.target\n"
    )
    if not unit.exists() or unit.read_text() != content:
        unit.write_text(content)
        changed = True
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", unit.name], check=True, capture_output=True
    )
    if changed:
        subprocess.run(
            ["systemctl", "--user", "restart", unit.name], check=True, capture_output=True
        )
    deadline = time.monotonic() + 10
    while not (root / "mcp.sock").exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("Windows MCP relay failed to become ready")
        time.sleep(0.1)
    return {"source": str(root), "target": "/opt/cao/windows-mcp", "readonly": True}


def runtime_mounts(workspaces, readonly_workspaces=()):
    mounts = []
    for relative in (
        ".claude",
        ".claude.json",
        ".codex",
        ".agents",
        ".config/opencode",
        ".local/share/opencode",
        ".cache/ms-playwright",
    ):
        source = Path.home() / relative
        if source.exists():
            mounts.append(
                {
                    "source": str(source),
                    "target": str(source),
                    "readonly": relative in (".cache/ms-playwright", ".agents"),
                }
            )
    bridge = windows_mcp_mount()
    if bridge:
        mounts.append(bridge)
    opencode = shutil.which("opencode")
    if opencode:
        mounts.append(
            {
                "source": str(Path(opencode).resolve()),
                "target": "/opt/cao/host-tools/opencode",
                "readonly": True,
            }
        )
    projects = {}
    for supplied, readonly in [
        *((p, False) for p in workspaces),
        *((p, True) for p in readonly_workspaces),
    ]:
        path = supplied.expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"workspace directory does not exist: {path}")
        for previous, mode in projects.items():
            if readonly != mode and (
                path.is_relative_to(previous) or previous.is_relative_to(path)
            ):
                raise ValueError("conflicting workspace modes for overlapping projects")
        projects[path] = readonly
    mounts.extend(
        {"source": str(path), "target": str(path), "readonly": readonly, "purpose": "project"}
        for path, readonly in projects.items()
    )
    vault = os.environ.get("CLAUDE_OBSIDIAN_VAULT")
    if vault:
        path = Path(vault).expanduser().absolute()
        if not path.is_dir():
            raise ValueError("configured Obsidian vault must exist before migration")
        mounts.append({"source": str(path), "target": str(path), "purpose": "obsidian-vault"})
    return mounts


def request_status(port, path, *, token=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {"Origin": f"http://127.0.0.1:{port}"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with opener.open(
            urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers), timeout=5
        ) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""


def wait_healthy(name, port, *, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if name is None:
            if not systemctl("is-active", check=False):
                raise RuntimeError("previous host service exited before becoming healthy")
        else:
            state = inspect(name)
            if state is None or not state["State"]["Running"]:
                raise RuntimeError("application container exited before becoming healthy")
        try:
            status, html = request_status(port, "/")
            if (
                status == 200
                and b"<html" in html.lower()
                and request_status(port, "/health")[0] == 200
            ):
                if (
                    request_status(port, "/auth/config")[0] == 200
                    and request_status(port, "/sessions")[0] == 401
                ):
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise RuntimeError("frontend/API/authentication did not become healthy within 120 seconds")


def validate_integrations(image, root, config, mounts):
    args = container_arguments(
        root, image, "cao-check-" + uuid.uuid4().hex[:12], config, mounts, port=""
    )
    # One-shot check: neither publish ports nor launch application processes.
    for flag in ("--detach",):
        args.remove(flag)
    for flag in ("--name", "--restart", "--publish"):
        position = args.index(flag)
        del args[position : position + 2]
    args.insert(1, "--rm")
    args[-1:-1] = ["--entrypoint", "python"]
    code = (
        "import sys,subprocess; "
        "sys.path.insert(0,'/opt/cao/scripts'); "
        "from docker_personal_runtime import check_mcp_integrations; "
        "from cli_agent_orchestrator.backends.docker_backend import DockerWorkBackend; "
        "from cli_agent_orchestrator.providers.opencode_cli import "
        "detect_opencode_major, resolve_opencode_runtime; "
        "DockerWorkBackend(image_ref=sys.argv[1])._validate_engine_and_image(); "
        "opencode_runtime=resolve_opencode_runtime() if 'opencode' in sys.argv[2:] else None; "
        "[detect_opencode_major(opencode_runtime) if name=='opencode' else "
        "subprocess.run([name,'--version'],check=True,stdout=subprocess.DEVNULL) "
        "for name in sys.argv[2:]]; check_mcp_integrations()"
    )
    providers = ["claude", "codex"] + (["opencode"] if shutil.which("opencode") else [])
    docker(args + ["-c", code, config["image_id"], *providers])


def systemctl(*arguments, check=True):
    result = subprocess.run(
        ["systemctl", "--user", *arguments, "cao-personal.service"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if check and result.returncode:
        raise RuntimeError("could not change the previous personal service")
    return result.returncode == 0


def _cleanup_isolated_database(root: Path):
    """Remove only the private preflight PostgreSQL resources for this root."""
    deployment = _read_deployment(root)
    instance = _instance_id(deployment)
    profile = instance[:12]
    network_owner = hashlib.sha256(f"{socket.gethostname()}:{os.getuid()}".encode()).hexdigest()[
        :16
    ]
    network = "cao-profile-" + profile + "-preflight"
    peer_root = root.with_name(root.name + "-isolated-peer").absolute()
    if peer_root.is_symlink():
        raise RuntimeError("refusing to remove a non-preflight PostgreSQL store")
    peer_settings_path = peer_root / "service.json"
    peer_settings = None
    if peer_settings_path.is_file() and not peer_settings_path.is_symlink():
        _private_file(peer_settings_path)
        peer_settings = json.loads(peer_settings_path.read_text())
    peer_container = None
    peer_owner = None
    peer_store_id = None
    if peer_settings is not None:
        if peer_settings.get("isolated") is not True:
            raise RuntimeError("refusing to remove a non-preflight peer store")
        identity_dir = Path.home() / ".aws" / "cli-agent-orchestrator" / "local-peers"
        identity_path = identity_dir / "host-id"
        if identity_path.is_symlink() or not identity_path.is_file():
            raise RuntimeError("refusing to remove a peer store without private host identity")
        _private_file(identity_path)
        from cli_agent_orchestrator.services.local_peers.host_identity import (
            current_host_identity,
        )

        canonical_host_id = current_host_identity(identity_dir).host_id
        peer_owner = peer_settings.get("owner_id")
        peer_store_id = peer_settings.get("store_id")
        peer_container = peer_settings.get("container")
        if peer_owner != canonical_host_id:
            raise RuntimeError("refusing to remove a peer store with a different owner identity")
        try:
            canonical_store_id = str(uuid.UUID(peer_store_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError("refusing to remove a peer store with an invalid identity") from exc
        if canonical_store_id != peer_store_id:
            raise RuntimeError("refusing to remove a peer store with an invalid identity")
        expected_peer_container = f"cao-peer-pg-{canonical_host_id}-{instance[:8]}"
        if peer_container != expected_peer_container:
            raise RuntimeError(
                "refusing to remove a peer store with a different container identity"
            )
    expected = {
        "cao-main-pg-"
        + profile: {
            DATABASE_SERVICE_LABEL: "main-auth",
            DATABASE_OWNER_LABEL: instance,
            DATABASE_INSTANCE_LABEL: instance,
        }
    }
    if peer_container is not None:
        expected[peer_container] = {
            DATABASE_SERVICE_LABEL: "peer",
            DATABASE_OWNER_LABEL: peer_owner,
            DATABASE_INSTANCE_LABEL: peer_store_id,
        }
    for name, labels in expected.items():
        state = inspect(name)
        if state is None:
            continue
        actual = state.get("Config", {}).get("Labels") or {}
        if any(actual.get(key) != value for key, value in labels.items()):
            raise RuntimeError("refusing to remove an unowned preflight PostgreSQL container")
        docker(["rm", "--force", name])
    network_state = inspect_network(network)
    if network_state is not None:
        labels = network_state.get("Labels") or {}
        if (
            labels.get(DATABASE_SERVICE_LABEL) != "preflight-network"
            or labels.get(DATABASE_INSTANCE_LABEL) != instance
            or labels.get(DATABASE_OWNER_LABEL) != network_owner
        ):
            raise RuntimeError("refusing to remove a non-preflight PostgreSQL network")
        docker(["network", "rm", network])
    if peer_root.exists():
        if (
            peer_root.is_symlink()
            or peer_root != root.with_name(root.name + "-isolated-peer").absolute()
        ):
            raise RuntimeError("refusing to remove a non-preflight PostgreSQL store")
        shutil.rmtree(peer_root)


def preflight(image, config):
    with tempfile.TemporaryDirectory(prefix="cao-container-preflight-") as scratch:
        root = Path(scratch) / "state"
        admin(
            image,
            root,
            [
                "init",
                "--image-id",
                config["image_id"],
                "--port",
                str(config["server_port"]),
                "--jwks-port",
                str(config["jwks_port"]),
            ],
        )
        name = "cao-preflight-" + uuid.uuid4().hex[:12]
        try:
            network, created, _, _ = ensure_database(root, isolated=True)
            if not created:
                raise RuntimeError("preflight PostgreSQL target was not isolated")
            admin(image, root, ["bootstrap-database"], network=network)
            prepare_home(image, root)
            docker(container_arguments(root, image, name, config, [], port="", network=network))
            port = int(inspect(name)["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"])
            wait_healthy(name, port)
        finally:
            docker(["rm", "--force", name], check=False)
            _cleanup_isolated_database(root)
            for suffix in ("-container-home", "-container-sockets"):
                shutil.rmtree(root.with_name(root.name + suffix), ignore_errors=True)


def install(args):
    root = args.root
    daemon = docker(["info", "--format", "{{.OSType}}/{{.Architecture}}"]).stdout.strip()
    if daemon not in ("linux/x86_64", "linux/amd64"):
        raise RuntimeError("the local Work deployment requires a Linux amd64 Docker engine")
    print("Construyendo imagen de aplicación...", flush=True)
    image = image_id(args.image) if args.no_build else build(args.image)
    if not root.exists():
        if not 1024 <= args.port <= 65534 or args.port in (8079, 8080):
            raise ValueError("choose an API port from 1024 to 65534, excluding 8079/8080")
        root.parent.mkdir(parents=True, exist_ok=True)
        worker = build("cao-personal-worker:local", worker=True)
        admin(
            image,
            root,
            [
                "init",
                "--image-id",
                worker,
                "--port",
                str(args.port),
                "--jwks-port",
                str(args.port + 1),
            ],
        )
    config = config_for(root)
    network, database_created, _peer_container, _peer_root = ensure_database(
        root, external_config=args.database_config
    )
    if database_created:
        admin(image, root, ["bootstrap-database"], network=network)
    else:
        admin(image, root, ["verify-database"], network=network)
    admin(image, root, ["configure-personal-scheduler"], network=network)
    config = config_for(root)
    fingerprint = identity_fingerprint(root)
    manifest_path = root / "docker-installation.json"
    previous_manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    mounts = runtime_mounts(args.workspace, getattr(args, "workspace_readonly", ()))
    prepare_home(image, root)
    current = inspect(args.name)
    if current and current["Config"].get("Labels", {}).get(LABEL) != str(root):
        raise RuntimeError("container name belongs to another deployment")
    if (
        current
        and current["Image"] == image
        and previous_manifest
        and previous_manifest.get("mounts") == mounts
        and previous_manifest.get("database_network") == network
    ):
        if not current["State"]["Running"]:
            docker(["start", args.name])
        _attach_container_to_network(args.name, network, "cao-app")
        wait_healthy(args.name, config["server_port"])
        print(f"Instalación ya activa: http://127.0.0.1:{config['server_port']}/")
        return
    print("Comprobando imagen, proveedores y almacenamiento antes del corte...", flush=True)
    validate_integrations(image, root, config, mounts)
    preflight(image, config)
    host_service = root == DEFAULT_ROOT
    host_active = host_service and systemctl("is-active", check=False)
    host_enabled = host_service and systemctl("is-enabled", check=False)
    previous_name = args.name + "-previous-" + uuid.uuid4().hex[:8] if current else None
    backup_path = root.with_name(
        root.name + "-backup-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    )
    container_stopped = False
    container_parked = False
    host_stopped = False
    instance = uuid.uuid4().hex

    def stop_previous():
        nonlocal container_stopped, container_parked, host_stopped
        if current:
            container_stopped = True
            docker(["stop", args.name])
            docker(["rename", args.name, previous_name])
            container_parked = True
        if host_active:
            host_stopped = True
            systemctl("stop")

    def restore_previous():
        if container_parked:
            docker(["rename", previous_name, args.name])
        if container_stopped:
            docker(["start", args.name])
            wait_healthy(args.name, config["server_port"])
        if host_enabled:
            systemctl("enable")
        if host_stopped:
            systemctl("start")
            wait_healthy(None, config["server_port"])
        if previous_manifest is not None:
            write_manifest(manifest_path, previous_manifest)
        else:
            manifest_path.unlink(missing_ok=True)

    def start_candidate():
        admin(image, root, ["backup", str(backup_path)], network=network)
        arguments = container_arguments(root, image, args.name, config, mounts, network=network)
        arguments[-1:-1] = ["--label", f"{INSTANCE_LABEL}={instance}"]
        docker(arguments)

    def stop_candidate():
        state = inspect(args.name)
        if state and state["Config"].get("Labels", {}).get(INSTANCE_LABEL) == instance:
            docker(["rm", "--force", args.name])

    def verify():
        wait_healthy(args.name, config["server_port"])
        if identity_fingerprint(root) != fingerprint:
            raise RuntimeError("deployment identity changed during migration")
        code = "import sys;sys.path.insert(0,'/opt/cao/scripts');import personal_deployment as p;from pathlib import Path;print(p.mint_token(Path(sys.argv[1])))"
        token = docker(["exec", args.name, "python", "-c", code, str(root)]).stdout.strip()
        if request_status(config["server_port"], "/sessions", token=token)[0] != 200:
            raise RuntimeError("preserved operator identity cannot use the protected API")

    def commit():
        if host_enabled:
            systemctl("disable")
        write_manifest(
            manifest_path,
            {
                "version": 1,
                "container": args.name,
                "image": image,
                "mounts": mounts,
                "database_network": network,
                "backup": str(backup_path),
                "container_instance": instance,
                "previous_container": previous_name,
                "previous_manifest": previous_manifest,
                "previous_host_active": bool(host_active),
                "previous_host_enabled": bool(host_enabled),
            },
        )
        renew_browser_link(args.name, root)

    cutover(
        stop_previous,
        restore_previous,
        start_candidate,
        stop_candidate,
        verify,
        commit,
    )
    print(f"Instalado: http://127.0.0.1:{config['server_port']}/\nCopia privada: {backup_path}")


def renew_browser_link(name, root):
    code = "import sys;sys.path.insert(0,'/opt/cao/scripts');import personal_deployment as p;from pathlib import Path;p.write_browser_link(Path(sys.argv[1]))"
    docker(["exec", name, "python", "-c", code, str(root)])


def _owned_rollback_container(name, root, expected_instance=None):
    state = inspect(name)
    if state is None:
        raise RuntimeError("recorded deployment container is unavailable")
    labels = (state.get("Config") or {}).get("Labels") or {}
    if labels.get(LABEL) != str(root):
        raise RuntimeError("container name belongs to another deployment")
    if expected_instance is not None and labels.get(INSTANCE_LABEL) != expected_instance:
        raise RuntimeError("container instance differs from the deployment manifest")
    container_id = state.get("Id")
    if not isinstance(container_id, str) or not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise RuntimeError("recorded deployment container has no immutable Docker ID")
    return state


def _require_container_id_at_name(name, expected_id):
    state = inspect(name)
    if state is None or state.get("Id") != expected_id:
        raise RuntimeError("container name changed during deployment rollback")


def manage(args):
    config = config_for(args.root)
    manifest = json.loads((args.root / "docker-installation.json").read_text())
    name = manifest["container"]
    if args.command == "status":
        if manifest.get("mode") == "host":
            print("running" if systemctl("is-active", check=False) else "stopped")
            return
        state = inspect(name)
        print("running" if state and state["State"]["Running"] else "stopped")
    elif args.command in ("start", "stop"):
        if manifest.get("mode") == "host":
            systemctl(args.command)
            return
        docker([args.command, name])
        if args.command == "start":
            wait_healthy(name, config["server_port"])
    elif args.command == "rollback":
        previous = manifest.get("previous_container")
        if not previous and not manifest.get("previous_host_active"):
            raise RuntimeError(
                "no previous deployment is recorded; the private backup remains available"
            )
        if manifest.get("mode") == "host":
            raise RuntimeError("the previous host deployment has already been restored")
        current_instance = manifest.get("container_instance")
        current_state = _owned_rollback_container(name, args.root, current_instance)
        current_id = current_state["Id"]
        previous_id = None
        if previous:
            previous_manifest = manifest.get("previous_manifest") or {}
            previous_state = _owned_rollback_container(
                previous,
                args.root,
                previous_manifest.get("container_instance"),
            )
            previous_id = previous_state["Id"]
        parked = name + "-rolled-back-" + uuid.uuid4().hex[:8]
        stopped = renamed = previous_renamed = host_attempted = False
        try:
            stopped = True
            docker(["stop", current_id])
            docker(["rename", current_id, parked])
            renamed = True
            if previous:
                docker(["rename", previous_id, name])
                previous_renamed = True
                docker(["start", previous_id])
                _require_container_id_at_name(name, previous_id)
                wait_healthy(name, config["server_port"])
                _require_container_id_at_name(name, previous_id)
                restored = dict(manifest.get("previous_manifest") or {})
                restored.update(
                    container=name,
                    previous_container=parked,
                    previous_manifest=manifest,
                    mode="docker",
                )
            else:
                host_attempted = True
                if manifest.get("previous_host_enabled"):
                    systemctl("enable")
                systemctl("start")
                wait_healthy(None, config["server_port"])
                _require_container_id_at_name(parked, current_id)
                restored = dict(
                    manifest,
                    mode="host",
                    previous_container=None,
                    previous_host_active=False,
                    previous_host_enabled=False,
                    parked_container=parked,
                )
            write_manifest(args.root / "docker-installation.json", restored)
        except BaseException:
            if host_attempted:
                systemctl("stop", check=False)
                systemctl("disable", check=False)
            if previous_renamed:
                docker(["stop", previous_id], check=False)
                docker(["rename", previous_id, previous])
            if renamed:
                docker(["rename", current_id, name])
            if stopped:
                docker(["start", current_id])
            raise
        print("Despliegue anterior restaurado; datos y copia privada conservados.")
    elif args.command == "account-reset":
        if manifest.get("mode") == "host":
            runtime = args.root.with_name(args.root.name + "-runtime")
            subprocess.run(
                [
                    str(runtime / "bin/python"),
                    str(runtime / "personal_deployment.py"),
                    "--root",
                    str(args.root),
                    "account-reset",
                ],
                check=True,
            )
            return
        subprocess.run(
            [
                shutil.which("docker"),
                "exec",
                "-it",
                name,
                "python",
                "/opt/cao/scripts/personal_deployment.py",
                "--root",
                str(args.root),
                "account-reset",
            ],
            check=True,
        )
    elif args.command == "open":
        if manifest.get("mode") == "host":
            raise RuntimeError("use the previous host deployment web command after rollback")
        renew_browser_link(name, args.root)
        link = args.root / "web-link.txt"
        powershell = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
        if powershell.exists():
            subprocess.run(
                [
                    str(powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "$url = [Console]::In.ReadToEnd().Trim(); Start-Process $url",
                ],
                input=link.read_text(),
                text=True,
                check=True,
            )
        else:
            import webbrowser

            webbrowser.open(link.read_text().strip())


def _build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        default="install",
        choices=("install", "status", "start", "stop", "rollback", "account-reset", "open"),
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--name", default="cao-personal")
    parser.add_argument("--image", default="cao-personal:local")
    parser.add_argument(
        "--database-config",
        type=Path,
        help="private version-1 PostgreSQL target configuration for explicit external stores",
    )
    parser.add_argument("--port", type=int, default=9889)
    parser.add_argument("--workspace", type=Path, action="append", default=[REPOSITORY])
    parser.add_argument("--workspace-readonly", type=Path, action="append", default=[])
    parser.add_argument(
        "--no-build", action="store_true", help="reuse an already-built local image"
    )
    return parser


def parse_arguments(argv=None):
    parser = _build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    retired_options = {
        "--import-sqlite",
        "--source-stopped",
        "--main-source",
        "--auth-source",
        "--peer-source",
    }
    if any(argument.partition("=")[0] in retired_options for argument in arguments):
        parser.error("SQLite import options were removed; use PostgreSQL-native backup and restore")
    return parser.parse_args(arguments)


def main(argv=None):
    args = parse_arguments(argv)
    args.root = args.root.expanduser().absolute()
    args.root.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        args.root.with_name(args.root.name + "-installation.lock"),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        (install if args.command == "install" else manage)(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(
            "Instalación interrumpida; recuperación ejecutada si había un corte activo.",
            file=sys.stderr,
        )
        sys.exit(130)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Instalación interrumpida: {error}", file=sys.stderr)
        sys.exit(1)
