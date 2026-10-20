#!/usr/bin/env python3
"""Run an isolated local Docker Work demo with a disposable verified admin issuer.

This is a development acceptance path, not normal server configuration. It uses
a fresh temporary CAO home, a short-lived RS256 token whose JWKS is served only
on loopback, and a static worker that does not start a model provider.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Mapping

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

if TYPE_CHECKING:
    from cli_agent_orchestrator.clients.postgres import DatabaseTarget


def _base64url_integer(value: int) -> str:
    width = max(1, (value.bit_length() + 7) // 8)
    raw = value.to_bytes(width, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class LocalWorkJwksIssuer:
    """Ephemeral loopback JWKS issuer for one isolated local Work demo."""

    def __init__(self, *, token_lifetime_seconds: int = 1800):
        if type(token_lifetime_seconds) is not int or not 60 <= token_lifetime_seconds <= 3600:
            raise ValueError("local demo token lifetime must be between 60 and 3600 seconds")
        self.issuer = "urn:cao:local-work-demo:" + os.urandom(8).hex()
        self.audience = "cao-local-work-demo"
        self.subject = "local-docker-operator"
        self.kid = "cao-local-work-" + os.urandom(8).hex()
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_numbers = self._private_key.public_key().public_numbers()
        jwks_bytes = json.dumps(
            {
                "keys": [
                    {
                        "kty": "RSA",
                        "use": "sig",
                        "alg": "RS256",
                        "kid": self.kid,
                        "n": _base64url_integer(public_numbers.n),
                        "e": _base64url_integer(public_numbers.e),
                    }
                ]
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/jwks.json":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(jwks_bytes)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(jwks_bytes)

            def log_message(self, _format, *_args):
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1},
            daemon=True,
            name="cao-local-work-jwks",
        )
        self._thread.start()
        now = int(time.time())
        self.token = jwt.encode(
            {
                "iss": self.issuer,
                "aud": self.audience,
                "sub": self.subject,
                "iat": now,
                "exp": now + token_lifetime_seconds,
                "scope": "cao:admin",
            },
            self._private_key,
            algorithm="RS256",
            headers={"kid": self.kid, "typ": "JWT"},
        )

    @property
    def jwks_uri(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/jwks.json"

    def auth_environment(self) -> dict[str, str]:
        return {
            "CAO_AUTH_JWKS_URI": self.jwks_uri,
            "CAO_AUTH_ISSUER": self.issuer,
            "CAO_AUTH_AUDIENCE": self.audience,
        }

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def __enter__(self) -> LocalWorkJwksIssuer:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


def provision_local_static_work(
    repository,
    principal,
    *,
    selection: str,
    project_id: str,
    checkout_root: Path,
    worker_bytes: bytes,
):
    """Provision one no-tools static worker using verified internal Work services."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.models.work_contract import (
        ContractPermissions,
        ContractResources,
        ContractSnapshot,
        EffectiveWorkContractV2,
    )
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.delegation_snapshot import (
        DelegationSnapshots,
        ResolvedSnapshot,
    )
    from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
    from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
    from cli_agent_orchestrator.services.work_elf_identity import identify_static_executable
    from cli_agent_orchestrator.services.work_executable_content import WorkExecutableContent
    from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning
    from cli_agent_orchestrator.services.work_scheduler import WorkScheduler

    if not isinstance(repository, WorkRepository):
        raise ValueError("verified Work repository required")
    if (
        not auth.is_verified_principal(principal)
        or principal.kind != "jwt"
        or auth.SCOPE_ADMIN not in principal.scopes
    ):
        raise PermissionError("a signature-verified cao:admin bearer is required")
    if not isinstance(checkout_root, Path) or not checkout_root.is_dir():
        raise ValueError("existing local checkout directory required")
    repository.verify_schema()
    canonical_checkout_root = str(checkout_root.resolve(strict=True))

    command_token = "/cao-work-worker"
    executable = identify_static_executable(command_token, worker_bytes)
    WorkExecutableContent(repository).publish(executable, worker_bytes)

    with repository.read_snapshot() as connection:
        policy_exists = connection.execute(
            "SELECT revision FROM work_scheduler_policy WHERE singleton=1"
        ).fetchone()
    if policy_exists is None:
        WorkScheduler(repository).configure(
            capacity=1,
            max_queue=8,
            aging_seconds=10,
            expected_policy_revision=0,
        )
    grant_id = "local-docker-root"
    job = repository.create_job(
        project_id=project_id,
        principal_id=principal.id,
        allowed_providers=["mock_cli"],
        grant_id=grant_id,
        budget={"scheduler_units": 8},
    )
    grant = WorkAuthority(repository).issue_root(
        principal,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            paths={canonical_checkout_root},
            tools={"knowledge.read"},
            commands={command_token},
        ),
        expires_at=time.time() + 1800,
    )

    contract_id = "local-docker-demo-contract"
    snapshot_request_hash = hashlib.sha256(
        json.dumps(
            {
                "command_token": command_token,
                "executable_sha256": executable.sha256_digest,
                "selection": selection,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    snapshot = DelegationSnapshots(
        repository,
        policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision),
    ).freeze(
        principal=principal,
        job_id=job["id"],
        contract_id=contract_id,
        binding_key="local-docker-demo-snapshot",
        request_hash=snapshot_request_hash,
        scope="project",
        scope_id=project_id,
        resolver=lambda _connection, _actor: ResolvedSnapshot(
            "Local Docker static worker demo. No model provider or workspace mount is used."
        ),
    )
    contract = EffectiveWorkContractV2(
        id=contract_id,
        operation_kind="launch",
        provider="mock_cli",
        backend="docker-local",
        permissions=ContractPermissions(
            paths=(canonical_checkout_root,),
            commands=(command_token,),
        ),
        resources=ContractResources(
            checkout_root=canonical_checkout_root,
            units=1,
        ),
        snapshot=ContractSnapshot(
            state="present",
            id=snapshot.id,
            delivered_hash=snapshot.delivered_hash,
        ),
        executable_identities=(executable,),
    )
    provisioning = WorkProvisioning(repository)
    reference = provisioning.provision_launch(
        principal,
        subject=principal,
        selector=selection,
        expected_revision=0,
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        contract=contract,
        adapter_version=2,
        lease_seconds=1200,
    )
    resolved = provisioning.resolve_launch(principal, selection)
    if resolved.ref != reference:
        raise RuntimeError("local Work provision did not resolve to its exact revision")
    return reference


def _docker_environment(docker_host: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["DOCKER_HOST"] = docker_host
    for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        environment.pop(key, None)
    return environment


def _docker_tag_image_id(tag: str, *, docker_host: str) -> str | None:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=_docker_environment(docker_host),
    )
    if result.returncode != 0:
        return None
    image_id = result.stdout.strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise RuntimeError("Docker returned an invalid rootfs image ID")
    return image_id


def _build_local_rootfs(repository_root: Path, *, docker_host: str) -> str:
    builder = repository_root / "test/integration/t019/build-work-rootfs.sh"
    rootfs_tag = "cao-work-empty-rootfs:local"
    prior_image_id = _docker_tag_image_id(rootfs_tag, docker_host=docker_host)
    try:
        result = subprocess.run(
            ["bash", str(builder)],
            cwd=repository_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
            env=_docker_environment(docker_host),
        )
    finally:
        if prior_image_id is not None:
            current_image_id = _docker_tag_image_id(rootfs_tag, docker_host=docker_host)
            if current_image_id != prior_image_id:
                restored = subprocess.run(
                    ["docker", "image", "tag", prior_image_id, rootfs_tag],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                    env=_docker_environment(docker_host),
                )
                if restored.returncode != 0:
                    raise RuntimeError("could not restore the existing local Work rootfs tag")
    if result.stdout:
        print(result.stdout, file=sys.stderr, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    if result.returncode != 0:
        raise RuntimeError("local Docker Work rootfs preflight failed")
    image_ids = [
        line.partition("=")[2]
        for line in result.stdout.splitlines()
        if line.startswith("CAO_WORK_DOCKER_IMAGE_ID=")
    ]
    if len(image_ids) != 1 or not image_ids[0].startswith("sha256:"):
        raise RuntimeError("local Docker Work rootfs builder returned no immutable image ID")
    return image_ids[0]


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _compile_demo_worker(repository_root: Path, output: Path) -> bytes:
    compiler = shutil.which("cc")
    if compiler is None:
        raise RuntimeError("a C compiler is required for the static local demo worker")
    source = repository_root / "test/integration/t019/local-demo-worker.c"
    built = subprocess.run(
        [compiler, "-static", "-O2", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(output)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if built.returncode != 0:
        details = built.stderr[-2000:].strip()
        raise RuntimeError(
            "static local demo worker could not be built" + (f": {details}" if details else "")
        )
    return output.read_bytes()


def _write_private_environment(path: Path, values: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        for key, value in values.items():
            stream.write(f"export {key}={shlex.quote(value)}\n")
    os.chmod(path, 0o600)


def _read_private_json(path: Path) -> dict:
    """Read one owner-only JSON file without following a symlink."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            metadata = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_mode & 0o077
                or metadata.st_uid != os.geteuid()
            ):
                raise ValueError("demo database configuration and secrets must be private")
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError("invalid demo database configuration")
        return value
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("cannot read private demo database configuration or secret") from error


def _write_private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o077 or parent.st_uid != os.geteuid():
        raise ValueError("demo database configuration and secrets must stay in private directories")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())


def _copy_private_database_config(
    source: Path, profile_home: Path
) -> tuple[Path, dict[str, DatabaseTarget]]:
    """Copy explicit demo targets and credentials into the temporary private profile."""
    from cli_agent_orchestrator.clients.postgres import DatabaseTarget

    source_path = Path(source).expanduser().absolute()
    configuration = _read_private_json(source_path)
    if type(configuration.get("version")) is not int or configuration.get("version") != 1:
        raise ValueError("unsupported demo database configuration version")
    if configuration.keys() - {"version", "targets", "managed"}:
        raise ValueError("demo database configuration has unsupported fields")
    raw_targets = configuration.get("targets")
    scopes = ("main", "auth", "peer")
    if not isinstance(raw_targets, dict) or set(raw_targets) != set(scopes):
        raise ValueError("demo database configuration must define main, auth, and peer targets")

    home = Path(profile_home)
    home_info = home.lstat()
    if (
        not stat.S_ISDIR(home_info.st_mode)
        or home_info.st_mode & 0o077
        or home_info.st_uid != os.geteuid()
    ):
        raise ValueError("temporary demo profile must be a private owner-only directory")

    postgres_dir = home / "postgres"
    postgres_dir.mkdir(mode=0o700)
    secrets_dir = postgres_dir / "secrets"
    secrets_dir.mkdir(mode=0o700)
    copied_targets: dict[str, dict] = {}
    targets: dict[str, DatabaseTarget] = {}
    allowed_fields = {
        "host",
        "port",
        "database",
        "user",
        "schema",
        "store_id",
        "secret_file",
        "sslmode",
        "migration_user",
        "migration_secret_file",
    }
    required_fields = allowed_fields - {"sslmode"}
    for scope in scopes:
        raw_target = raw_targets[scope]
        if not isinstance(raw_target, dict):
            raise ValueError("invalid demo database target")
        if (
            raw_target.keys() - allowed_fields
            or required_fields - raw_target.keys()
            or "password" in raw_target
            or "connection_string" in raw_target
            or "dsn" in raw_target
        ):
            raise ValueError("demo database target has unsupported or missing fields")
        if raw_target["user"] == raw_target["migration_user"]:
            raise ValueError("demo database runtime and migration roles must be separate")

        target_values = dict(raw_target)
        for role in ("runtime", "owner"):
            field = "secret_file" if role == "runtime" else "migration_secret_file"
            original_name = target_values[field]
            if not isinstance(original_name, str) or not original_name:
                raise ValueError("demo database target is missing a credential file")
            original_path = Path(original_name).expanduser()
            if not original_path.is_absolute():
                original_path = source_path.parent / original_path
            secret = _read_private_json(original_path.absolute())
            password = secret.get("password")
            if set(secret) != {"password"} or not isinstance(password, str) or not password:
                raise ValueError("demo database credential file has an invalid shape")
            copied_secret = secrets_dir / f"{scope}-{role}.json"
            _write_private_json(copied_secret, {"password": password})
            relative_secret = copied_secret.relative_to(home).as_posix()
            target_values[field] = relative_secret

        copied_targets[scope] = dict(target_values)
        target_values["secret_file"] = home / target_values["secret_file"]
        target_values["migration_secret_file"] = home / target_values["migration_secret_file"]
        targets[scope] = DatabaseTarget(**target_values, scope=scope, home=home)

    namespace_keys = {
        (target.host, target.port, target.database, target.schema) for target in targets.values()
    }
    if len(namespace_keys) != len(scopes):
        raise ValueError("demo database scopes must use distinct PostgreSQL schemas")
    if len({target.store_id for target in targets.values()}) != len(scopes):
        raise ValueError("demo database scopes must use distinct store identities")
    if len({target.user for target in targets.values()}) != len(scopes):
        raise ValueError("demo database scopes must use separate runtime roles")

    config_path = home / "database-config.json"
    _write_private_json(config_path, {"version": 1, "targets": copied_targets})
    return config_path, targets


def _require_clean_demo_namespaces(targets: Mapping[str, DatabaseTarget]) -> None:
    """Refuse existing PostgreSQL schemas; the demo never adopts or clears data."""
    from cli_agent_orchestrator.clients.postgres import connect

    for scope in ("main", "auth", "peer"):
        target = targets[scope]
        if not target.migration_user or not target.migration_secret_file:
            raise ValueError("demo database migration and runtime roles must be separate")
        if target.user == target.migration_user:
            raise ValueError("demo database migration and runtime roles must be separate")
        with connect(target.migration_target(), readonly=True) as connection:
            current_role = connection.execute("SELECT current_user").fetchone()
            if current_role is None or current_role[0] != target.migration_user:
                raise ValueError("demo database migration credential does not match its owner role")
            runtime_role = connection.execute(
                """SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolreplication
                    FROM pg_roles WHERE rolname=%s""",
                (target.user,),
            ).fetchone()
            if runtime_role is None or any(runtime_role):
                raise ValueError("demo database runtime role is missing or privileged")
            namespace = connection.execute(
                """SELECT r.rolname AS owner,
                    EXISTS (SELECT 1 FROM pg_catalog.aclexplode(
                        COALESCE(n.nspacl, pg_catalog.acldefault('n', n.nspowner))) acl
                        WHERE acl.privilege_type='CREATE' AND acl.grantee<>n.nspowner)
                        AS untrusted_create,
                    pg_catalog.has_schema_privilege(%s, n.nspname, 'CREATE') AS runtime_create,
                    EXISTS (SELECT 1 FROM pg_depend d
                        WHERE d.refclassid='pg_namespace'::regclass
                          AND d.refobjid=n.oid AND d.deptype='n') AS has_objects
                    FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner
                    WHERE n.nspname=%s""",
                (target.user, target.schema),
            ).fetchone()
            if namespace is not None and (
                namespace["owner"] != target.migration_user
                or namespace["untrusted_create"]
                or namespace["runtime_create"]
                or namespace["has_objects"]
            ):
                raise ValueError(
                    f"demo {scope} PostgreSQL schema must be empty and owned by its migration role"
                )


def _bootstrap_demo_database(targets: Mapping[str, DatabaseTarget]) -> None:
    """Apply controlled component migrations using only the explicit owner targets."""
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.postgres.migrations import verify_runtime_role
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    for scope in ("main", "auth", "peer"):
        target = targets[scope]
        bootstrap_scope(target, seed_work=scope == "main")
        with connect(target, readonly=True) as connection:
            verify_runtime_role(connection)


def _database_namespaces(path: Path) -> set[tuple[str, int, str, str]]:
    configuration = _read_private_json(path)
    raw_targets = configuration.get("targets")
    scopes = ("main", "auth", "peer")
    if type(configuration.get("version")) is not int or configuration.get("version") != 1:
        raise ValueError("unsupported demo database configuration version")
    if not isinstance(raw_targets, dict) or set(raw_targets) != set(scopes):
        raise ValueError("demo database configuration must define main, auth, and peer targets")
    namespaces = set()
    for scope in scopes:
        target = raw_targets[scope]
        if not isinstance(target, dict):
            raise ValueError("invalid demo database target")
        host, port, database, schema = (
            target.get("host"),
            target.get("port"),
            target.get("database"),
            target.get("schema"),
        )
        if (
            not isinstance(host, str)
            or type(port) is not int
            or not isinstance(database, str)
            or not isinstance(schema, str)
        ):
            raise ValueError("invalid demo database namespace")
        namespaces.add((host, port, database, schema))
    if len(namespaces) != len(scopes):
        raise ValueError("demo database scopes must use distinct PostgreSQL schemas")
    return namespaces


def _reject_inherited_database_config_overlap(selected: Path) -> None:
    inherited = os.environ.get("CAO_DATABASE_CONFIG_FILE")
    if not inherited:
        return
    inherited_path = Path(inherited).expanduser().absolute()
    try:
        if os.path.samefile(selected, inherited_path):
            raise ValueError(
                "the demo database config must be separate from the inherited CAO profile"
            )
    except FileNotFoundError:
        return
    if _database_namespaces(selected) & _database_namespaces(inherited_path):
        raise ValueError(
            "the demo database schemas must be separate from the inherited CAO profile"
        )


@contextmanager
def _temporary_environment(values: Mapping[str, str]) -> Iterator[None]:
    missing = object()
    previous = {key: os.environ.get(key, missing) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in previous.items():
            if value is missing:
                os.environ.pop(key, None)
            elif isinstance(value, str):
                os.environ[key] = value


def run_demo(*, requested_port: int = 0, database_config: Path | None = None) -> int:
    repository_root = Path(__file__).resolve().parents[1]
    if requested_port and not 1 <= requested_port <= 65535:
        raise ValueError("API port must be between 1 and 65535")
    if database_config is None:
        raise ValueError("pass an explicit dedicated database config with --database-config")
    selected_database_config = Path(database_config).expanduser().absolute()
    _reject_inherited_database_config_overlap(selected_database_config)

    docker_host = (
        os.environ.get("CAO_T019_DOCKER_HOST")
        or os.environ.get("DOCKER_HOST")
        or "unix:///var/run/docker.sock"
    )
    if not docker_host.startswith("unix:///"):
        raise RuntimeError("local Docker Work accepts only a local Unix socket")
    if os.environ.get("CAO_T019_DOCKER_CLI") not in (None, "", "docker"):
        raise RuntimeError("local Docker Work demo uses the Docker CLI named 'docker'")

    with tempfile.TemporaryDirectory(prefix="cao-local-docker-work-") as temporary:
        temporary_root = Path(temporary)
        os.chmod(temporary_root, 0o700)
        home = temporary_root / "cao-home"
        home.mkdir(mode=0o700)
        checkout_root = temporary_root / "checkout"
        checkout_root.mkdir(mode=0o700)
        private_config_path, database_targets = _copy_private_database_config(
            selected_database_config, home
        )
        image_id = _build_local_rootfs(repository_root, docker_host=docker_host)
        api_port = requested_port or _free_loopback_port()
        if requested_port:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                try:
                    listener.bind(("127.0.0.1", requested_port))
                except OSError as error:
                    raise RuntimeError("requested local API port is already in use") from error

        with LocalWorkJwksIssuer() as issuer:
            environment = os.environ.copy()
            for key in ("AUTH0_DOMAIN", "AUTH0_AUDIENCE"):
                environment.pop(key, None)
            environment.update(issuer.auth_environment())
            environment.update(
                {
                    "CAO_AUTH_LOCAL_TOKEN": issuer.token,
                    "CAO_HOME_DIR": str(home),
                    "CAO_DATABASE_CONFIG_FILE": str(private_config_path),
                    "CAO_API_HOST": "127.0.0.1",
                    "CAO_API_PORT": str(api_port),
                    "CAO_ENABLE_PUBLIC_WORK_INGRESS": "true",
                    "CAO_WORK_DOCKER_LOCAL": "1",
                    "CAO_WORK_DOCKER_IMAGE_ID": image_id,
                    "CAO_WORK_LAUNCH_MODE": "required",
                    "DOCKER_HOST": docker_host,
                }
            )
            for key in ("DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
                environment.pop(key, None)

            worker_path = temporary_root / "cao-local-demo-worker"
            worker_bytes = _compile_demo_worker(repository_root, worker_path)

            from cli_agent_orchestrator.clients.work_repository import WorkRepository
            from cli_agent_orchestrator.security import auth

            with _temporary_environment(
                {
                    **issuer.auth_environment(),
                    "CAO_AUTH_LOCAL_TOKEN": issuer.token,
                    "CAO_HOME_DIR": str(home),
                    "CAO_DATABASE_CONFIG_FILE": str(private_config_path),
                }
            ):
                _require_clean_demo_namespaces(database_targets)
                _bootstrap_demo_database(database_targets)
                repository = WorkRepository(database_targets["main"], filesystem_home=home)
                repository.initialize()
                principal = auth.principal_from_token(issuer.token)
                reference = provision_local_static_work(
                    repository,
                    principal,
                    selection="local-docker",
                    project_id="local-docker-demo",
                    checkout_root=checkout_root,
                    worker_bytes=worker_bytes,
                )

            client_environment = {
                "CAO_HOME_DIR": str(home),
                "CAO_DATABASE_CONFIG_FILE": str(private_config_path),
                "CAO_API_HOST": "127.0.0.1",
                "CAO_API_PORT": str(api_port),
                "CAO_AUTH_JWKS_URI": issuer.jwks_uri,
                "CAO_AUTH_ISSUER": issuer.issuer,
                "CAO_AUTH_AUDIENCE": issuer.audience,
                "CAO_AUTH_LOCAL_TOKEN": issuer.token,
                "CAO_WORK_DOCKER_LOCAL": "1",
                "CAO_WORK_DOCKER_IMAGE_ID": image_id,
                "CAO_WORK_LAUNCH_MODE": "required",
                "DOCKER_HOST": docker_host,
            }
            client_env_path = temporary_root / "client.env"
            _write_private_environment(client_env_path, client_environment)
            launch_command = " ".join(
                shlex.quote(value)
                for value in (
                    sys.executable,
                    "-m",
                    "cli_agent_orchestrator.cli.main",
                    "launch",
                    "--queue-work",
                    "--session-name",
                    "local-docker-demo",
                    "--agents",
                    "developer",
                    "--work-selection",
                    "local-docker",
                    "local static worker check",
                )
            )

            print(
                "Local Docker Work static-worker demo is ready.\n"
                f"  API: http://127.0.0.1:{api_port}\n"
                f"  selector: local-docker (provision revision {reference.revision})\n"
                f"  client env: {client_env_path}\n"
                "In another shell, run:\n"
                f"  source {shlex.quote(str(client_env_path))}\n"
                f"  {launch_command}\n"
                "This runs the bundled static worker; it does not launch a model provider.\n"
                "The bearer, issuer key, private database configuration, and client env are "
                "temporary and are deleted when this server stops. PostgreSQL schemas and "
                "Work data remain in the selected database service.",
                file=sys.stderr,
                flush=True,
            )
            command = [
                sys.executable,
                "-m",
                "uvicorn",
                "cli_agent_orchestrator.api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
                "--workers",
                "1",
            ]
            return subprocess.run(
                command,
                cwd=repository_root,
                env=environment,
                check=False,
            ).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-port",
        type=int,
        default=0,
        help="loopback API port; defaults to an available ephemeral port",
    )
    parser.add_argument(
        "--database-config",
        type=Path,
        required=True,
        help="private config with dedicated empty PostgreSQL schemas for main, auth, and peer",
    )
    args = parser.parse_args(argv)
    try:
        return run_demo(requested_port=args.api_port, database_config=args.database_config)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"Local Docker Work demo failed closed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
