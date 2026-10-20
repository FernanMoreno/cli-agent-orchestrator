#!/usr/bin/env python3
"""Private, persistent localhost CAO service; host providers plus Docker Work."""

from __future__ import annotations

import argparse
import base64
import fcntl
import getpass
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _private(path: Path, *, directory=False):
    if path.is_symlink() or not path.exists():
        raise ValueError(f"private {'directory' if directory else 'file'} required: {path.name}")
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise ValueError(f"private owner-only permissions required: {path.name}")
    if directory != path.is_dir():
        raise ValueError(f"invalid private path: {path.name}")


def _write(path: Path, content: bytes):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as writer:
            writer.write(content)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _config(root: Path):
    _private(root, directory=True)
    for name in ("deployment.json", "issuer.pem"):
        _private(root / name)
    _private(root / "cao", directory=True)
    config = json.loads((root / "deployment.json").read_text())
    if config.get("host") != "127.0.0.1":
        raise ValueError("personal deployment must bind to loopback")
    for field in ("server_port", "jwks_port"):
        if type(config.get(field)) is not int or not 1024 <= config[field] <= 65535:
            raise ValueError("invalid deployment port")
    if config["server_port"] == config["jwks_port"]:
        raise ValueError("server and issuer ports must differ")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", config.get("image_id", "")):
        raise ValueError("immutable Docker image ID required")
    worker_image_reference = config.get("worker_image_reference")
    if worker_image_reference is not None and not _valid_worker_image_reference(
        worker_image_reference
    ):
        raise ValueError("invalid worker image reference")
    policy = config.get("browser_login")
    if policy is not None:
        if not isinstance(policy, dict) or type(policy.get("enabled")) is not bool:
            raise ValueError("invalid browser login configuration")
        if (
            policy.get("canonical_origin") != f"http://127.0.0.1:{config['server_port']}"
            or policy.get("transport_policy") != "loopback_http"
        ):
            raise ValueError("invalid browser login origin or transport")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", policy.get("installation_id", "")):
            raise ValueError("invalid browser login installation ID")
        if policy.get("binding") != _local_binding(root, config):
            raise ValueError("browser login binding differs from configured operator")
    return config


def _valid_worker_image_reference(value: object) -> bool:
    """Accept only an immutable worker image ID."""
    return isinstance(value, str) and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value))


def database_config_path(root: Path) -> Path:
    """The profile-scoped target map lives beside CAO's private runtime state."""
    path = root / "cao" / "database-config.json"
    if path.exists() or path.is_symlink():
        _private(path)
    return path


def write_database_config(
    root: Path,
    targets: dict,
    *,
    managed: dict | None = None,
):
    """Atomically publish a versioned target map; passwords remain separate files."""
    if not isinstance(targets, dict) or set(targets) != {"main", "auth", "peer"}:
        raise ValueError("all three PostgreSQL targets are required")
    for scope, target in targets.items():
        if not isinstance(target, dict):
            raise ValueError("invalid PostgreSQL target configuration")
        if "password" in target or "connection_string" in target or "dsn" in target:
            raise ValueError("database credentials must be stored in private secret files")
        if target.get("schema") in {"public", "information_schema"}:
            raise ValueError("application targets require a private PostgreSQL schema")
        for field_name in ("secret_file", "migration_secret_file"):
            value = target.get(field_name)
            if value is not None:
                candidate = Path(value)
                if not candidate.is_absolute():
                    candidate = root / "cao" / candidate
                _private(candidate)
    home = root / "cao"
    _private(home, directory=True)
    path = database_config_path(root)
    if path.exists() or path.is_symlink():
        _private(path)
    payload = {"version": 1, "targets": targets}
    if managed is not None:
        if not isinstance(managed, dict):
            raise ValueError("invalid managed PostgreSQL configuration")
        payload["managed"] = managed
    _write(path, json.dumps(payload, sort_keys=True, indent=2).encode())
    return path


@contextmanager
def _database_settings(root: Path, config_path: Path | None = None):
    """Select this deployment's explicit targets while constructing clients."""
    config_path = config_path or database_config_path(root)
    _private(config_path)
    old_config = os.environ.get("CAO_DATABASE_CONFIG_FILE")
    old_home = os.environ.get("CAO_HOME_DIR")
    os.environ["CAO_DATABASE_CONFIG_FILE"] = str(config_path)
    os.environ["CAO_HOME_DIR"] = str(root / "cao")
    try:
        yield
    finally:
        if old_config is None:
            os.environ.pop("CAO_DATABASE_CONFIG_FILE", None)
        else:
            os.environ["CAO_DATABASE_CONFIG_FILE"] = old_config
        if old_home is None:
            os.environ.pop("CAO_HOME_DIR", None)
        else:
            os.environ["CAO_HOME_DIR"] = old_home


def database_target(root: Path, scope: str, config_path: Path | None = None):
    from cli_agent_orchestrator.clients.postgres import DatabaseTarget

    with _database_settings(root, config_path):
        return DatabaseTarget.from_settings(scope=scope, home=root / "cao")


def verify_database(root: Path, *, config_path: Path | None = None):
    """Read-only verification of every scope; startup never runs migrations."""
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.postgres.migrations import (
        ComponentMigration,
        verify_component,
        verify_runtime_role,
    )
    from cli_agent_orchestrator.clients.postgres.registry import component_migrations

    for scope in ("main", "auth", "peer"):
        target = database_target(root, scope, config_path)
        complete_components: dict[tuple[str, str], tuple[ComponentMigration, ...]] = {}
        for history in component_migrations(scope):
            key = (history[0].scope, history[0].component)
            if key not in complete_components or len(history) > len(complete_components[key]):
                complete_components[key] = history
        with connect(target, readonly=True) as connection:
            verify_runtime_role(connection)
            for migrations in complete_components.values():
                verify_component(connection, migrations)


def bootstrap_database(root: Path, *, config_path: Path | None = None, seed_work=True):
    """Explicit installer operation; ordinary service startup only verifies."""
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    for scope in ("main", "auth", "peer"):
        bootstrap_scope(database_target(root, scope, config_path), seed_work=seed_work)
    verify_database(root, config_path=config_path)
    if seed_work:
        configure_personal_scheduler(WorkRepository(database_target(root, "main", config_path)))
    renew_client(root)


def configure_scheduler(root: Path):
    """Seed the personal scheduler only after every configured store verifies."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    verify_database(root)
    configure_personal_scheduler(WorkRepository(database_target(root, "main")))


def _browser_auth_service(root: Path, *, validate=True):
    from cli_agent_orchestrator.services.browser_auth import BrowserAuthService

    config = _config(root)
    policy = config.get("browser_login")
    if policy is None:
        raise ValueError("browser login account has not been configured")
    service = BrowserAuthService(database_target(root, "auth"), policy["binding"], policy)
    if validate:
        service.validate_binding()
    return service


def _local_binding(root, config):
    """Verify with the existing local public key; no running HTTP issuer required."""
    from cli_agent_orchestrator.security.auth import _scopes_from_claims, _verified_principal

    scopes = config.get("scopes", ["cao:read", "cao:write", "cao:admin"])
    if (
        not isinstance(scopes, list)
        or not scopes
        or any(scope not in ("cao:read", "cao:write", "cao:admin") for scope in scopes)
    ):
        raise ValueError("invalid configured operator scopes")
    key = serialization.load_pem_private_key((root / "issuer.pem").read_bytes(), password=None)
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": config["issuer"],
            "sub": config["subject"],
            "aud": config["audience"],
            "exp": now + 60,
            "scope": " ".join(scopes),
        },
        key,
        algorithm="RS256",
    )
    claims = jwt.decode(
        token,
        key.public_key(),
        algorithms=["RS256"],
        issuer=config["issuer"],
        audience=config["audience"],
        options={"require": ["iss", "sub", "aud", "exp"]},
    )
    principal = _verified_principal(
        claims["iss"], claims["sub"], _scopes_from_claims(claims), "jwt"
    )
    return {
        "id": principal.id,
        "issuer": principal.issuer,
        "subject": principal.subject,
        "kind": principal.kind,
        "scopes": scopes,
    }


def browser_auth_service(root: Path, *, validate=True):
    return _browser_auth_service(root, validate=validate)


def _password():
    password = getpass.getpass("New password: ")
    if password != getpass.getpass("Confirm password: "):
        raise ValueError("passwords do not match")
    return password


def account_create(root: Path, *, username: str):
    config = _config(root)
    password = _password()
    with _lock(root), _lock(root, name="account.lock"):
        config = _config(root)
        if config.get("browser_login", {}).get("enabled"):
            raise ValueError("browser login account is already configured; use account-reset")
        config["scopes"] = config.get("scopes", ["cao:read", "cao:write", "cao:admin"])
        policy = {
            "enabled": False,
            "provisioning_pending": True,
            "installation_id": uuid.uuid4().hex,
            "canonical_origin": f"http://127.0.0.1:{config['server_port']}",
            "transport_policy": "loopback_http",
            "binding": _local_binding(root, config),
            "limits": {
                "access_seconds": 3600,
                "remembered_idle_seconds": 604800,
                "remembered_absolute_seconds": 2592000,
                "temporal_idle_seconds": 28800,
                "temporal_absolute_seconds": 86400,
            },
        }
        policy = config.get("browser_login", policy)
        config["browser_login"] = policy
        _write(root / "deployment.json", json.dumps(config, indent=2).encode())
        service = browser_auth_service(root, validate=False)
        from cli_agent_orchestrator.models.browser_auth import BrowserAuthError

        try:
            service.initialize_store()
            service.create_account(username, password)
        except BrowserAuthError as error:
            if (
                error.code != "account_exists"
                or not policy.get("provisioning_pending")
                or not service.account_matches(username, password)
            ):
                raise
        policy.pop("provisioning_pending", None)
        policy["enabled"] = True
        config["browser_login"] = policy
        _write(root / "deployment.json", json.dumps(config, indent=2).encode())
    renew_client(root)


def account_reset(root: Path):
    _config(root)
    password = _password()
    with _lock(root, name="account.lock"):
        service = browser_auth_service(root, validate=False)
        service.reset_password(password)
        config = _config(root)
        config["browser_login"].pop("provisioning_pending", None)
        config["browser_login"]["enabled"] = True
        _write(root / "deployment.json", json.dumps(config, indent=2).encode())
        renew_client(root)


def account_disable(root: Path):
    with _lock(root, name="account.lock"):
        browser_auth_service(root).disable()
        config = _config(root)
        config["browser_login"]["enabled"] = False
        _write(root / "deployment.json", json.dumps(config, indent=2).encode())
        renew_client(root)


def initialize(
    root: Path,
    *,
    server_port=9889,
    jwks_port=9890,
    image_id: str,
    worker_image_reference: str | None = None,
):
    if worker_image_reference is not None and not _valid_worker_image_reference(
        worker_image_reference
    ):
        raise ValueError("invalid worker image reference")
    if root.exists():
        existing = _config(root)
        if (existing["server_port"], existing["jwks_port"], existing["image_id"]) != (
            server_port,
            jwks_port,
            image_id,
        ) or (
            worker_image_reference is not None
            and existing.get("worker_image_reference") != worker_image_reference
        ):
            raise ValueError("existing deployment differs; do not overwrite live configuration")
        return
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("immutable Docker image ID required")
    if (
        any(type(port) is not int or not 1024 <= port <= 65535 for port in (server_port, jwks_port))
        or server_port == jwks_port
    ):
        raise ValueError("distinct unprivileged ports required")
    root.mkdir(parents=True, mode=0o700)
    (root / "cao").mkdir(mode=0o700)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _write(
        root / "issuer.pem",
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    config = {
        "instance_id": uuid.uuid4().hex,
        "host": "127.0.0.1",
        "server_port": server_port,
        "jwks_port": jwks_port,
        "image_id": image_id,
        "issuer": "urn:cao:personal:" + uuid.uuid4().hex,
        "audience": "cao-personal",
        "subject": "personal-operator",
        "scopes": ["cao:read", "cao:write", "cao:admin"],
        "kid": uuid.uuid4().hex,
    }
    if worker_image_reference is not None:
        config["worker_image_reference"] = worker_image_reference
    _write(
        root / "deployment.json",
        json.dumps(config, indent=2).encode(),
    )


def _key(root):
    _config(root)
    return serialization.load_pem_private_key((root / "issuer.pem").read_bytes(), password=None)


def jwks(root: Path):
    config = _config(root)
    numbers = _key(root).public_key().public_numbers()

    def encode(n):
        return (
            base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big"))
            .rstrip(b"=")
            .decode()
        )

    return {
        "keys": [
            {
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "kid": config["kid"],
                "n": encode(numbers.n),
                "e": encode(numbers.e),
            }
        ]
    }


def mint_token(root: Path, *, lifetime=3600):
    if type(lifetime) is not int or not 60 <= lifetime <= 86400:
        raise ValueError("token lifetime must be 60..86400 seconds")
    config = _config(root)
    now = int(time.time())
    return jwt.encode(
        {
            "iss": config["issuer"],
            "aud": config["audience"],
            "sub": config["subject"],
            "iat": now,
            "exp": now + lifetime,
            "scope": " ".join(config.get("scopes", ["cao:read", "cao:write", "cao:admin"])),
        },
        _key(root),
        algorithm="RS256",
        headers={"kid": config["kid"], "typ": "JWT"},
    )


def publish_local_token(root: Path, *, lifetime=3600):
    """Atomically publish a renewable operator bearer under the private root."""
    token = mint_token(root, lifetime=lifetime)
    path = root / "mcp-bearer.jwt"
    if path.exists() or path.is_symlink():
        _private(path)
    _write(path, token.encode("ascii"))
    return jwt.decode(token, options={"verify_signature": False})["exp"]


def rotate_local_tokens(root: Path, stopping, expires_at: int, *, lifetime=3600, clock=time.time):
    """Renew before expiry; publication failure neither replays work nor restarts agents."""
    margin = min(300, lifetime // 2)
    while not stopping.is_set():
        if expires_at - clock() <= margin:
            try:
                expires_at = publish_local_token(root, lifetime=lifetime)
            except (OSError, ValueError):
                print("Local MCP credential renewal failed; retrying publication", file=sys.stderr)
                if stopping.wait(5):
                    break
                continue
        if stopping.wait(min(30, max(0.2, expires_at - clock() - margin))):
            break


def environment(root: Path):
    root = root.absolute()
    config = _config(root)
    database_path = database_config_path(root)
    if config.get("browser_login", {}).get("enabled"):
        browser_auth_service(root)
    env = {
        "CAO_BROWSER_LOGIN_ROOT": str(root),
        "CAO_HOME_DIR": str(root / "cao"),
        "CAO_DATABASE_CONFIG_FILE": str(database_path),
        "CAO_API_HOST": "127.0.0.1",
        "CAO_API_PORT": str(config["server_port"]),
        "CAO_TERMINAL_BACKEND": "tmux",
        "TMUX_TMPDIR": str(root.with_name(root.name + "-sockets")),
        "CAO_AUTH_JWKS_URI": f"http://127.0.0.1:{config['jwks_port']}/jwks.json",
        "CAO_AUTH_ISSUER": config["issuer"],
        "CAO_AUTH_AUDIENCE": config["audience"],
        "CAO_AUTH_LOCAL_TOKEN": mint_token(root),
        "CAO_WORK_DOCKER_LOCAL": "1",
        "CAO_WORK_DOCKER_IMAGE_ID": config["image_id"],
        "CAO_ENABLE_PUBLIC_WORK_INGRESS": "true",
    }

    credential = root / "mcp-bearer.jwt"
    if credential.exists() or credential.is_symlink():
        _private(credential)
        env["CAO_AUTH_LOCAL_TOKEN_FILE"] = str(credential)
    return env


def renew_client(root: Path, *, final_root: Path | None = None):
    env = environment(root)
    if final_root is not None:
        for name in (
            "CAO_HOME_DIR",
            "CAO_BROWSER_LOGIN_ROOT",
            "CAO_DATABASE_CONFIG_FILE",
            "TMUX_TMPDIR",
        ):
            env[name] = env[name].replace(str(root.absolute()), str(final_root.absolute()))
    _write(
        root / "client.env",
        ("\n".join("export " + k + "=" + shlex.quote(v) for k, v in env.items()) + "\n").encode(),
    )


def write_browser_link(root: Path) -> Path:
    """Write an operator-only access link; fragments are not sent to HTTP servers."""
    config = _config(root)
    link = root / "web-link.txt"
    if config.get("browser_login", {}).get("enabled"):
        browser_auth_service(root)
        url = f"http://127.0.0.1:{config['server_port']}/\n"
    else:
        url = f"http://127.0.0.1:{config['server_port']}/#cao_token={mint_token(root)}\n"
    _write(link, url.encode())
    return link


def open_browser(root: Path):
    link = write_browser_link(root)
    url = link.read_text().strip()
    powershell = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if powershell.is_file():
        subprocess.run(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$url = [Console]::In.ReadToEnd().Trim(); Start-Process $url",
            ],
            input=url,
            text=True,
            check=True,
        )
    else:
        import webbrowser

        if not webbrowser.open(url):
            raise RuntimeError(f"open the private access link in {link}")


@contextmanager
def _lock(root: Path, *, name="service.lock"):
    _private(root, directory=True)
    path = root / name
    if path.is_symlink():
        raise ValueError("private service lock required")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "stop personal service before backup or duplicate startup"
                if name == "service.lock"
                else "another account operation is in progress"
            ) from error
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


_BACKUP_EXCLUDED_FILES = {
    "service.lock",
    "account.lock",
    "database-config.json",
    "client.env",
    "mcp-bearer.jwt",
    "web-link.txt",
}


def _copy_state(source: Path, destination: Path, *, relative=Path("")):
    destination.mkdir(mode=0o700, exist_ok=True)
    if (
        destination.is_symlink()
        or destination.stat().st_uid != os.getuid()
        or destination.stat().st_mode & 0o077
    ):
        raise ValueError("backup staging directories must remain private")
    for entry in source.iterdir():
        child_relative = relative / entry.name
        if entry.name.endswith(("-wal", "-shm")) or entry.name in _BACKUP_EXCLUDED_FILES:
            continue
        if relative == Path("cao") and entry.name.startswith("pg-client-"):
            continue
        if relative == Path("cao") and entry.name == "postgres":
            continue
        if entry.is_symlink():
            raise ValueError("backup refuses symbolic links")
        target = destination / entry.name
        if entry.is_dir():
            _copy_state(entry, target, relative=child_relative)
        elif entry.is_file():
            if entry.suffix in (".db", ".sqlite", ".sqlite3"):
                continue
            else:
                _write(target, entry.read_bytes())
        else:
            raise ValueError("backup refuses sockets/devices")


def _database_groups(root: Path):
    targets = {scope: database_target(root, scope) for scope in ("main", "auth", "peer")}
    groups: dict[tuple[str, int, str, str], list[str]] = {}
    for scope, target in targets.items():
        key = (target.host, target.port, target.database, target.sslmode)
        groups.setdefault(key, []).append(scope)
    return targets, tuple(tuple(scopes) for scopes in groups.values())


def _safe_passfile_value(value: str) -> str:
    if any(char in value for char in ("\n", "\r", "\x00")):
        raise ValueError("invalid PostgreSQL credential field")
    return value.replace("\\", "\\\\").replace(":", "\\:")


def _database_client_is_loopback(host: str) -> bool:
    if host.casefold() in {"localhost", "localhost."}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _docker_operating_system() -> str:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.OperatingSystem}}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Docker daemon platform cannot be verified") from exc
    operating_system = result.stdout.strip()
    if not operating_system:
        raise RuntimeError("Docker daemon platform cannot be verified")
    return operating_system


def _database_client_network_mode(host: str, operating_system: str) -> str:
    """Route loopback through host networking except on Docker Desktop."""
    if not _database_client_is_loopback(host):
        return "bridge"
    if "docker desktop" in operating_system.casefold():
        return "bridge"
    return "host"


_DOCKER_DESKTOP_HOSTADDR_SCRIPT = r"""set -eu
hostaddr="$(getent ahostsv4 host.docker.internal | awk 'NR == 1 { print $1; exit }')"
case "$hostaddr" in
    ""|*[!0-9.]*)
        echo "Docker host gateway did not resolve to an IPv4 address" >&2
        exit 1
        ;;
esac
export PGHOSTADDR="$hostaddr"
exec "$@"
"""


def _managed_client_container(root: Path, target) -> str | None:
    config = json.loads(database_config_path(root).read_text(encoding="utf-8"))
    managed = config.get("managed")
    if not isinstance(managed, dict):
        return None
    name = managed.get("peer_container" if target.scope == "peer" else "main_container")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", name):
        raise ValueError("managed PostgreSQL container identity is invalid")
    try:
        inspected = subprocess.run(
            ["docker", "inspect", name], check=True, capture_output=True, text=True, timeout=15
        )
        values = json.loads(inspected.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError("managed PostgreSQL container cannot be verified") from exc
    if len(values) != 1:
        raise RuntimeError("managed PostgreSQL container identity is ambiguous")
    labels = values[0].get("Config", {}).get("Labels") or {}
    expected_service = "peer" if target.scope == "peer" else "main-auth"
    if labels.get("org.cao.personal.database.service") != expected_service:
        raise RuntimeError("managed PostgreSQL container label does not match its scope")
    return name


def _run_database_client(
    root: Path,
    target,
    executable: str,
    arguments: list[str],
    *,
    output=None,
    input_path: Path | None = None,
):
    """Run version-matched native PostgreSQL tools without exposing credentials."""
    migration_target = target.migration_target()
    container = _managed_client_container(root, target)
    command: list[str]
    environment = os.environ.copy()
    passfile_directory: Path | None = None
    input_stream = input_path.open("rb") if input_path is not None else None
    try:
        if container is not None:
            command = [
                "docker",
                "exec",
                *(["-i"] if input_stream is not None else []),
                "--user",
                "postgres",
                container,
                executable,
                "--username",
                migration_target.user,
                "--dbname",
                migration_target.database,
                *arguments,
            ]
        else:
            secret = migration_target.connection_parameters()
            home = root / "cao"
            passfile_directory = Path(tempfile.mkdtemp(prefix="pg-client-", dir=home))
            os.chmod(passfile_directory, 0o700)
            passfile = passfile_directory / "pgpass"
            fields = (
                migration_target.host,
                str(migration_target.port),
                migration_target.database,
                migration_target.user,
                secret["password"],
            )
            _write(
                passfile,
                (":".join(_safe_passfile_value(value) for value in fields) + "\n").encode(),
            )
            is_loopback = _database_client_is_loopback(migration_target.host)
            operating_system = _docker_operating_system() if is_loopback else ""
            network_mode = _database_client_network_mode(migration_target.host, operating_system)
            desktop_gateway = is_loopback and "docker desktop" in operating_system.casefold()
            command = [
                "docker",
                "run",
                "--rm",
                "--pull=missing",
                *(["--interactive"] if input_stream is not None else []),
                "--network",
                network_mode,
                *(["--add-host", "host.docker.internal:host-gateway"] if desktop_gateway else []),
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--mount",
                f"type=bind,src={passfile},dst=/run/secrets/pgpass,readonly",
                "--env",
                "PGPASSFILE=/run/secrets/pgpass",
                "--env",
                f"PGSSLMODE={migration_target.sslmode}",
                "--entrypoint",
                "/bin/sh" if desktop_gateway else executable,
                "postgres:18.6-trixie@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336",
                *(
                    ["-c", _DOCKER_DESKTOP_HOSTADDR_SCRIPT, "cao-pg-client", executable]
                    if desktop_gateway
                    else []
                ),
                "--no-password",
                "--host",
                migration_target.host,
                "--port",
                str(migration_target.port),
                "--username",
                migration_target.user,
                "--dbname",
                migration_target.database,
                *arguments,
            ]
        result = subprocess.run(
            command,
            check=False,
            stdin=input_stream,
            stdout=output if output is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=input_stream is None,
            env=environment,
            timeout=900,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"{executable} failed: {result.stderr[-2000:] if result.stderr else 'unknown error'}"
            )
        return result.stdout
    except OSError as exc:
        raise RuntimeError(f"{executable} is unavailable") from exc
    finally:
        if input_stream is not None:
            input_stream.close()
        if passfile_directory is not None:
            shutil.rmtree(passfile_directory)


class PersonalDeploymentArchiveRunner:
    """Protected PostgreSQL archive runner for private recovery bundles.

    This adapter is composed only by the personal deployment service. It
    reuses the version-matched Docker client path and the existing private
    passfile handling; no database password is placed in arguments, the
    environment, or recovery-bundle metadata.
    """

    def __init__(self, root: Path):
        self.root = Path(root).absolute()

    def dump(self, target, destination: Path, *, snapshot_id: str) -> None:
        if target.scope != "main" or destination.exists() or destination.is_symlink():
            raise ValueError("invalid recovery dump target")
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as output:
                _run_database_client(
                    self.root,
                    target,
                    "pg_dump",
                    [
                        "--format=custom",
                        "--no-owner",
                        "--no-privileges",
                        "--no-comments",
                        "--schema",
                        target.schema,
                        "--snapshot",
                        snapshot_id,
                    ],
                    output=output,
                )
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    def restore(self, target, archive: Path) -> None:
        if target.scope != "main" or archive.is_symlink() or not archive.is_file():
            raise ValueError("invalid recovery restore target")
        _run_database_client(
            self.root,
            target,
            "pg_restore",
            [
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-acl",
            ],
            input_path=archive,
        )


def _component_manifest(scope: str):
    from cli_agent_orchestrator.clients.postgres.registry import component_migrations

    return [
        {
            "scope": migration.scope,
            "component": migration.component,
            "version": migration.version,
            "checksum": migration.checksum,
        }
        for history in component_migrations(scope)
        for migration in history
    ]


def backup(root: Path, destination: Path):
    root = root.absolute()
    destination = destination.absolute()
    _config(root)
    if destination.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(
        destination.resolve()
    ):
        raise ValueError("backup must be outside deployment")
    if destination.exists():
        raise FileExistsError(destination)
    with _lock(root), _lock(root, name="account.lock"):
        staging = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            staging.mkdir(mode=0o700)
            _config(root)
            verify_database(root)
            targets, groups = _database_groups(root)
            dump_dir = staging / "postgres"
            dump_dir.mkdir(mode=0o700)
            dumps = []
            for scopes in groups:
                representative = targets[scopes[0]]
                filename = "pg-" + "-".join(scopes) + ".dump"
                path = dump_dir / filename
                with path.open("xb") as output:
                    _run_database_client(
                        root,
                        representative,
                        "pg_dump",
                        [
                            "--format=custom",
                            "--no-owner",
                            "--no-privileges",
                            "--no-comments",
                            *[
                                arg
                                for scope in scopes
                                for arg in ("--schema", targets[scope].schema)
                            ],
                        ],
                        output=output,
                    )
                os.chmod(path, 0o600)
                dumps.append(
                    {
                        "file": filename,
                        "scopes": list(scopes),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
            _copy_state(root, staging)
            manifest = {
                "format_version": 1,
                "backup_id": str(uuid.uuid4()),
                "created_at": time.time(),
                "scopes": {
                    scope: {
                        "host": target.host,
                        "port": target.port,
                        "database": target.database,
                        "schema": target.schema,
                        "store_id": target.store_id,
                        "migrations": _component_manifest(scope),
                    }
                    for scope, target in targets.items()
                },
                "dumps": dumps,
            }
            _write(
                staging / "backup-manifest.json",
                json.dumps(manifest, sort_keys=True, indent=2).encode(),
            )
            _config(staging)
            os.rename(staging, destination)
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def _read_backup_manifest(source: Path):
    manifest_path = source / "backup-manifest.json"
    _private(source, directory=True)
    _private(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("PostgreSQL backup manifest is invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("format_version") != 1
        or not isinstance(manifest.get("scopes"), dict)
        or set(manifest["scopes"]) != {"main", "auth", "peer"}
        or not isinstance(manifest.get("dumps"), list)
    ):
        raise ValueError("PostgreSQL backup manifest is unsupported")
    covered: set[str] = set()
    for item in manifest["dumps"]:
        if not isinstance(item, dict) or not isinstance(item.get("file"), str):
            raise ValueError("PostgreSQL backup dump manifest is invalid")
        if Path(item["file"]).name != item["file"] or not item["file"].endswith(".dump"):
            raise ValueError("PostgreSQL backup dump path is invalid")
        file_path = source / "postgres" / item["file"]
        _private(file_path)
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if digest != item.get("sha256"):
            raise ValueError("PostgreSQL backup dump digest differs")
        scopes = item.get("scopes")
        if (
            not isinstance(scopes, list)
            or not scopes
            or any(s not in {"main", "auth", "peer"} for s in scopes)
        ):
            raise ValueError("PostgreSQL backup scope grouping is invalid")
        if covered.intersection(scopes):
            raise ValueError("PostgreSQL backup repeats a scope")
        covered.update(scopes)
    if covered != {"main", "auth", "peer"}:
        raise ValueError("PostgreSQL backup does not contain every storage scope")
    return manifest


def _copy_restore_database_config(source_config: Path, staging: Path, manifest: dict):
    _private(source_config)
    try:
        payload = json.loads(source_config.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Destination PostgreSQL configuration is invalid") from exc
    if payload.get("version") != 1 or set(payload.get("targets", {})) != {"main", "auth", "peer"}:
        raise ValueError("Destination PostgreSQL configuration must define all three scopes")
    targets = payload["targets"]
    secret_dir = staging / "cao" / "postgres" / "secrets"
    secret_dir.mkdir(mode=0o700, parents=True)
    for scope, target in targets.items():
        if not isinstance(target, dict):
            raise ValueError("Destination PostgreSQL scope configuration is invalid")
        archived = manifest["scopes"][scope]
        if target.get("schema") != archived.get("schema"):
            raise ValueError("Restore requires destination schemas to retain their archived names")
        target["store_id"] = archived["store_id"]
        physical = (
            target.get("host"),
            target.get("port"),
            target.get("database"),
            target.get("schema"),
        )
        original = (
            archived.get("host"),
            archived.get("port"),
            archived.get("database"),
            archived.get("schema"),
        )
        if physical == original:
            raise ValueError(
                "Restore destination must be physically distinct from the backup source"
            )
        for key in ("secret_file", "migration_secret_file"):
            value = target.get(key)
            if not value:
                continue
            secret_source = Path(value)
            if not secret_source.is_absolute():
                secret_source = source_config.parent / secret_source
            _private(secret_source)
            secret_name = f"{scope}-{key}.json"
            _write(secret_dir / secret_name, secret_source.read_bytes())
            target[key] = str(Path("postgres") / "secrets" / secret_name)
    write_database_config(staging, targets, managed=payload.get("managed"))


def _target_schema_exists(target):
    from cli_agent_orchestrator.clients.postgres import connect

    with connect(target.migration_target(), readonly=True) as connection:
        return (
            connection.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname=%s", (target.schema,)
            ).fetchone()
            is not None
        )


def _restore_group_schema_receipts(targets):
    """Inventory every schema committed by one PostgreSQL restore group at once."""
    from cli_agent_orchestrator.clients.postgres import connect

    if not targets:
        return {}
    first = next(iter(targets.values()))
    if any(
        (target.host, target.port, target.database) != (first.host, first.port, first.database)
        for target in targets.values()
    ):
        raise ValueError("Restore schema inventory requires one PostgreSQL database group")
    schemas = tuple(target.schema for target in targets.values())
    with connect(first.migration_target(), readonly=True) as connection:
        rows = connection.execute(
            """SELECT current_database(), d.oid, n.nspname, n.oid, r.oid, r.rolname
               FROM pg_database AS d
               JOIN pg_namespace AS n ON n.nspname=ANY(%s)
               JOIN pg_roles AS r ON r.oid=n.nspowner
               WHERE d.datname=current_database()""",
            (list(schemas),),
        ).fetchall()
    rows_by_schema = {row[2]: row for row in rows}
    receipts = {}
    for scope, target in targets.items():
        row = rows_by_schema.get(target.schema)
        if row is None:
            continue
        receipts[scope] = {
            "target_identity": target.identity,
            "database": row[0],
            "database_oid": int(row[1]),
            "schema": row[2],
            "schema_oid": int(row[3]),
            "owner_oid": int(row[4]),
            "owner": row[5],
        }
    return receipts


def _restore_schema_receipt(target):
    """Capture one schema identity for focused verification and preflight tests."""
    return _restore_group_schema_receipts({"target": target}).get("target")


def _remove_restored_schema_if_unchanged(target, receipt):
    """Compensate only a namespace whose identity still matches this restore."""
    from psycopg import sql

    from cli_agent_orchestrator.clients.postgres import connect

    if target.identity != receipt["target_identity"]:
        raise RuntimeError("restore compensation target identity changed")
    migration_target = target.migration_target()
    with connect(migration_target) as connection:
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"cao-schema:{target.database}:{target.schema}",),
            )
            current = connection.execute(
                """SELECT current_database(), d.oid, n.oid, r.oid, r.rolname
                   FROM pg_database AS d
                   JOIN pg_namespace AS n ON n.nspname=%s
                   JOIN pg_roles AS r ON r.oid=n.nspowner
                   WHERE d.datname=current_database()""",
                (target.schema,),
            ).fetchone()
            if current is None:
                return
            actual = (current[0], int(current[1]), int(current[2]), int(current[3]), current[4])
            expected = (
                receipt["database"],
                receipt["database_oid"],
                receipt["schema_oid"],
                receipt["owner_oid"],
                receipt["owner"],
            )
            if actual != expected:
                raise RuntimeError(
                    "restore compensation refused because destination schema identity changed"
                )
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(target.schema))
            )


def _validate_restore_migrations(manifest: dict):
    from cli_agent_orchestrator.clients.postgres.registry import component_migrations

    for scope in ("main", "auth", "peer"):
        archived = manifest["scopes"][scope].get("migrations")
        if not isinstance(archived, list):
            raise ValueError("PostgreSQL backup migration manifest is missing")
        current = [migration for history in component_migrations(scope) for migration in history]
        expected = [
            {
                "scope": migration.scope,
                "component": migration.component,
                "version": migration.version,
                "checksum": migration.checksum,
            }
            for migration in current
        ]
        known = {(item["component"], item["version"]): item for item in expected}
        if not archived or any(
            not isinstance(item, dict)
            or known.get((item.get("component"), item.get("version"))) != item
            for item in archived
        ):
            raise ValueError("PostgreSQL backup schema version is not compatible with this build")


def restore(source: Path, destination: Path, *, database_config: Path):
    _config(source)
    if destination.exists():
        raise FileExistsError(destination)
    source, destination = source.absolute(), destination.absolute()
    if destination.resolve().is_relative_to(source.resolve()) or source.resolve().is_relative_to(
        destination.resolve()
    ):
        raise ValueError("restore must be outside source deployment")
    manifest = _read_backup_manifest(source)
    _validate_restore_migrations(manifest)
    database_config = database_config.absolute()
    if destination.resolve() == source.resolve() or database_config.resolve().is_relative_to(
        source.resolve()
    ):
        raise ValueError("restore configuration must be outside the backup source")
    staging = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
    restored_schemas = []
    published = False
    committed_group_untracked = False
    try:
        _copy_state(source, staging)
        config = _config(staging)
        _copy_restore_database_config(database_config, staging, manifest)
        targets = {scope: database_target(staging, scope) for scope in ("main", "auth", "peer")}
        target_namespaces = [
            (target.host, target.port, target.database, target.schema)
            for target in targets.values()
        ]
        if len(set(target_namespaces)) != len(target_namespaces):
            raise ValueError("Restore scopes must target distinct PostgreSQL schemas")

        groups = []
        for item in manifest["dumps"]:
            scopes = item["scopes"]
            target = targets[scopes[0]]
            if any(
                (targets[scope].host, targets[scope].port, targets[scope].database)
                != (target.host, target.port, target.database)
                for scope in scopes
            ):
                raise ValueError("Destination scopes must share each archived database grouping")
            groups.append((item, tuple(scopes), target))

        # Refuse a partially occupied destination before committing any dump group.
        if any(_target_schema_exists(target) for target in targets.values()):
            raise ValueError("Restore target schema must be absent and empty")

        for item, scopes, target in groups:
            # Keep the per-group check immediately before pg_restore as a race fence.
            if any(_target_schema_exists(targets[scope]) for scope in scopes):
                raise ValueError("Restore target schema must be absent and empty")
            dump_path = source / "postgres" / item["file"]
            _run_database_client(
                staging,
                target,
                "pg_restore",
                ["--exit-on-error", "--single-transaction", "--no-owner", "--no-acl"],
                input_path=dump_path,
            )
            try:
                group_receipts = _restore_group_schema_receipts(
                    {scope: targets[scope] for scope in scopes}
                )
            except BaseException as inventory_failure:
                committed_group_untracked = True
                raise RuntimeError(
                    "PostgreSQL restore committed but its schema identities could not be inventoried"
                ) from inventory_failure
            for scope in scopes:
                receipt = group_receipts.get(scope)
                if receipt is None:
                    raise RuntimeError("PostgreSQL restore did not create its target schema")
                if receipt["owner"] != targets[scope].migration_user:
                    committed_group_untracked = True
                    raise RuntimeError(
                        "PostgreSQL restore target schema has an unexpected migration owner"
                    )
                restored_schemas.append((targets[scope], receipt))

        bootstrap_database(staging)
        _block_restored_work(staging, targets["main"], manifest, source)
        if "browser_login" in config:
            browser_auth_service(staging, validate=False).invalidate_all("restore")
        renew_client(staging, final_root=destination)
        os.rename(staging, destination)
        published = True
    except BaseException as failure:
        incomplete_cleanup = []
        if not published:
            if committed_group_untracked:
                incomplete_cleanup.append(
                    "A committed restore group contained a schema that was left untouched "
                    "because its ownership or identity could not be verified safely."
                )
            for target, receipt in reversed(restored_schemas):
                try:
                    _remove_restored_schema_if_unchanged(target, receipt)
                except BaseException as cleanup_failure:
                    incomplete_cleanup.append(
                        "A captured destination schema could not be removed safely "
                        f"({type(cleanup_failure).__name__})."
                    )
        if incomplete_cleanup and isinstance(failure, Exception):
            raise RuntimeError(
                "PostgreSQL restore failed and rollback was incomplete: "
                + " ".join(incomplete_cleanup)
            ) from failure
        if incomplete_cleanup:
            failure.restore_cleanup_incomplete = tuple(incomplete_cleanup)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _block_restored_work(root: Path, target: Any, manifest: dict[str, Any], source: Path) -> None:
    """Keep globally restored Work data inert until its operator reconciles it.

    The deployment archive and Work recovery bundle are separate interfaces,
    but restoring the three-scope deployment archive still copies Work rows.
    Preserve their logical store UUID while binding the copied recovery context
    to a new installation and durable restore receipt. No service is started by
    this function.
    """
    from uuid import UUID

    from cli_agent_orchestrator.clients.work_repository import WorkRepository

    archived_store_id = manifest["scopes"]["main"]["store_id"]
    source_store_uuid = UUID(archived_store_id).hex
    bundle_digest = hashlib.sha256((source / "backup-manifest.json").read_bytes()).hexdigest()
    restore_id = uuid.uuid4().hex
    installation_uuid = uuid.uuid4().hex
    receipt_payload = {
        "format_version": 1,
        "restore_id": restore_id,
        "backup_id": manifest["backup_id"],
        "bundle_digest": bundle_digest,
        "source_store_uuid": source_store_uuid,
        "target_store_id": target.store_id,
        "created_at": time.time(),
        "state": "blocked_restore",
    }
    receipt_bytes = json.dumps(
        receipt_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    restore_receipt = hashlib.sha256(receipt_bytes).hexdigest()
    WorkRepository(target).block_imported_restore(
        source_store_uuid=source_store_uuid,
        installation_uuid=installation_uuid,
        bundle_digest=bundle_digest,
        restore_receipt=restore_receipt,
    )
    _write(
        root / "cao" / "restore-receipt.json",
        json.dumps(
            {**receipt_payload, "installation_uuid": installation_uuid, "receipt": restore_receipt},
            sort_keys=True,
            indent=2,
        ).encode(),
    )


def service_unit(root: Path):
    _config(root)

    # systemd command words use double quotes, not shell quoting; escape specifiers.
    def word(value):
        if any(char in str(value) for char in ("\n", "\r", "\x00")):
            raise ValueError("invalid systemd command value")
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'

    command = " ".join(
        map(word, (sys.executable, Path(__file__).absolute(), "--root", root.absolute(), "serve"))
    )
    return (
        "[Unit]\nDescription=CAO personal localhost service\nAfter=default.target\n\n[Service]\nType=simple\nExecStart="
        + command
        + "\nEnvironment="
        + word("PATH=" + str(Path(sys.executable).parent) + ":" + os.environ.get("PATH", ""))
        + "\nRestart=on-failure\nRestartSec=5\nKillMode=control-group\nTimeoutStopSec=30\nUMask=0077\n\n[Install]\nWantedBy=default.target\n"
    )


def configure_personal_scheduler(repository):
    """Explicit one-worker policy; preserve operator changes across restart."""
    from cli_agent_orchestrator.services.work_scheduler import WorkScheduler

    repository.verify_schema()
    with repository.read_snapshot() as connection:
        configured = connection.execute(
            "SELECT revision FROM work_scheduler_policy WHERE singleton=1"
        ).fetchone()
    if configured is None:
        WorkScheduler(repository).configure(
            capacity=1, max_queue=8, aging_seconds=10, expected_policy_revision=0
        )


def provision(root: Path, *, worker: Path, checkout: Path, selection: str, project_id: str):
    """Publish a bounded static launch contract for the signature-verified operator."""
    env = environment(root)
    os.environ.update({key: value for key, value in env.items() if key.startswith("CAO_")})
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.security.auth import principal_from_token

    principal = principal_from_token(env["CAO_AUTH_LOCAL_TOKEN"])
    path = Path(__file__).with_name("local_work_docker_demo.py")
    spec = importlib.util.spec_from_file_location("personal_static_provision", path)
    if spec is None or spec.loader is None:
        raise ImportError("personal Work provisioner could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.provision_local_static_work(
        WorkRepository(database_target(root, "main")),
        principal,
        selection=selection,
        project_id=project_id,
        checkout_root=checkout,
        worker_bytes=worker.read_bytes(),
    )


def serve(root: Path):
    config = _config(root)
    environment(root)  # Validate browser storage/binding before opening any listener.
    with _lock(root):
        lifetime = config.get("mcp_token_lifetime_seconds", 3600)
        expires_at = publish_local_token(root, lifetime=lifetime)
        renewal_stop = threading.Event()
        renewal_thread = None
        payload = json.dumps(jwks(root)).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/jwks.json":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        issuer = ThreadingHTTPServer(("127.0.0.1", config["jwks_port"]), Handler)
        issuer.daemon_threads = True
        thread = threading.Thread(target=issuer.serve_forever, daemon=True)
        thread.start()
        env = os.environ.copy()
        # Explicit personal settings override ambient auth and remote Docker config.
        env.update(environment(root))
        env.pop("AUTH0_DOMAIN", None)
        env["CAO_AUTH_LOCAL_TOKEN"] = mint_token(root, lifetime=86400)
        env["DOCKER_HOST"] = "unix:///var/run/docker.sock"
        for name in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
            env.pop(name, None)
        env["PATH"] = str(Path(sys.executable).parent) + ":" + env.get("PATH", "")
        runtime = root.with_name(root.name + "-sockets")
        runtime.mkdir(mode=0o700, exist_ok=True)
        _private(runtime, directory=True)
        env["TMUX_TMPDIR"] = str(runtime)
        # This process imports CAO only after the durable home is selected.
        os.environ.update({k: v for k, v in env.items() if k.startswith("CAO_")})
        verify_database(root)
        renew_client(root)
        process = None
        stopping = False

        def stop(_signum, _frame):
            nonlocal stopping
            stopping = True
            if process is not None and process.poll() is None:
                process.terminate()

        original = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            process = subprocess.Popen(
                [
                    str(Path(sys.executable).parent / "cao-server"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(config["server_port"]),
                    "--terminal",
                    "tmux",
                ],
                env=env,
            )
            renewal_thread = threading.Thread(
                target=rotate_local_tokens,
                args=(root, renewal_stop, expires_at),
                kwargs={"lifetime": lifetime},
                name="cao-local-token-renewal",
                daemon=True,
            )
            renewal_thread.start()
            code = process.wait()
            if code and not stopping:
                raise RuntimeError(f"personal server exited with code {code}")
        finally:
            renewal_stop.set()
            if renewal_thread is not None:
                renewal_thread.join(timeout=2)
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            issuer.shutdown()
            issuer.server_close()
            thread.join(timeout=2)
            for sig, handler in original.items():
                signal.signal(sig, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / ".local/share/cao-personal")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--image-id", required=True)
    init.add_argument("--worker-image-reference")
    init.add_argument("--port", type=int, default=9889)
    init.add_argument("--jwks-port", type=int, default=9890)
    sub.add_parser("serve")
    sub.add_parser("bootstrap-database")
    sub.add_parser("verify-database")
    sub.add_parser("configure-personal-scheduler")
    sub.add_parser("renew")
    sub.add_parser("web")
    sub.add_parser("unit")
    sub.add_parser("account-create").add_argument("--username", required=True)
    sub.add_parser("account-reset")
    sub.add_parser("account-disable")
    static = sub.add_parser("provision")
    static.add_argument("--worker", type=Path, required=True)
    static.add_argument("--checkout", type=Path, required=True)
    static.add_argument("--selection", default="personal-docker")
    static.add_argument("--project", default="personal-docker")
    sub.add_parser("backup").add_argument("path", type=Path)
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("path", type=Path)
    restore_parser.add_argument("--database-config", type=Path, required=True)
    arguments = list(sys.argv[1:] if argv is None else argv)
    retired_options = {
        "--source-stopped",
        "--main-source",
        "--auth-source",
        "--peer-source",
        "--peer-host-id",
        "--peer-owner-uid",
    }
    if any(argument.partition("=")[0] in retired_options for argument in arguments):
        parser.error("SQLite import options were removed; use PostgreSQL-native backup and restore")
    position = 0
    while position < len(arguments):
        argument = arguments[position]
        if argument == "--root":
            position += 2
            continue
        if argument.startswith("--root=") or argument.startswith("-"):
            position += 1
            continue
        if argument == "import-sqlite":
            parser.error(
                "the SQLite import command was removed; use PostgreSQL-native backup and restore"
            )
        break
    args = parser.parse_args(arguments)
    root = args.root.absolute()
    if args.command == "init":
        initialize(
            root,
            server_port=args.port,
            jwks_port=args.jwks_port,
            image_id=args.image_id,
            worker_image_reference=args.worker_image_reference,
        )
    elif args.command == "account-create":
        account_create(root, username=args.username)
    elif args.command == "account-reset":
        account_reset(root)
    elif args.command == "account-disable":
        account_disable(root)
    elif args.command == "serve":
        serve(root)
    elif args.command == "bootstrap-database":
        bootstrap_database(root)
    elif args.command == "verify-database":
        verify_database(root)
    elif args.command == "configure-personal-scheduler":
        configure_scheduler(root)
    elif args.command == "renew":
        renew_client(root)
    elif args.command == "web":
        open_browser(root)
    elif args.command == "unit":
        print(service_unit(root), end="")
    elif args.command == "provision":
        reference = provision(
            root,
            worker=args.worker,
            checkout=args.checkout,
            selection=args.selection,
            project_id=args.project,
        )
        print(f"provisioned {reference.selector} revision {reference.revision}")
    elif args.command == "backup":
        backup(root, args.path)
    elif args.command == "restore":
        restore(args.path, root, database_config=args.database_config)


if __name__ == "__main__":
    main()
