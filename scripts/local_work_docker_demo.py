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
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Mapping

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


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


def run_demo(*, requested_port: int = 0) -> int:
    repository_root = Path(__file__).resolve().parents[1]
    if requested_port and not 1 <= requested_port <= 65535:
        raise ValueError("API port must be between 1 and 65535")

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
            os.environ.update(environment)

            worker_path = temporary_root / "cao-local-demo-worker"
            worker_bytes = _compile_demo_worker(repository_root, worker_path)

            from cli_agent_orchestrator.clients.work_repository import WorkRepository
            from cli_agent_orchestrator.constants import DATABASE_FILE, DB_DIR
            from cli_agent_orchestrator.security import auth

            DB_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
            repository = WorkRepository(DATABASE_FILE)
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
                "The bearer, issuer key, database, and client env are temporary and are "
                "deleted when this server stops.",
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
    args = parser.parse_args(argv)
    try:
        return run_demo(requested_port=args.api_port)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"Local Docker Work demo failed closed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
