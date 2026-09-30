"""Disposable JWT issuer and HEAD-compatible Docker launch provision fixture."""

from __future__ import annotations
import base64
import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContractV2,
)
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

CONTEXT = b"Browser Docker frozen context."
MARKER = b"BROWSER_DOCKER_WORKER_COMPLETED\n"


def provision_launch(repository, actor, tmp_path, worker):
    WorkScheduler(repository).configure(
        capacity=1, max_queue=8, aging_seconds=5, expected_policy_revision=0
    )
    job = repository.create_job(
        project_id="browser-docker-project",
        principal_id=actor.id,
        allowed_providers=["scratch_worker"],
        grant_id="browser-docker-root",
        budget={"scheduler_units": 8},
    )
    grant = WorkAuthority(repository).issue_root(
        actor,
        job_id=job["id"],
        providers={"scratch_worker"},
        permissions=Permissions(
            paths={str(tmp_path)}, tools={"knowledge.read"}, commands={"/browser-worker"}
        ),
        expires_at=time.time() + 300,
    )
    snapshot = DelegationSnapshots(
        repository, policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision)
    ).freeze(
        principal=actor,
        job_id=job["id"],
        contract_id="browser-docker-contract",
        binding_key="browser-docker-context",
        request_hash=hashlib.sha256(CONTEXT).hexdigest(),
        scope="project",
        scope_id="browser-docker-project",
        resolver=lambda _connection, _actor: ResolvedSnapshot(CONTEXT.decode()),
    )
    executable = identify_static_executable("/browser-worker", worker)
    WorkExecutableContent(repository).publish(executable, worker)
    contract = EffectiveWorkContractV2(
        id="browser-docker-contract",
        operation_kind="launch",
        provider="scratch_worker",
        backend="docker-local",
        permissions=ContractPermissions(paths=(str(tmp_path),), commands=("/browser-worker",)),
        resources=ContractResources(checkout_root=str(tmp_path), write_paths=(), units=1),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
        executable_identities=(executable,),
    )
    reference = WorkProvisioning(repository).provision_launch(
        actor,
        subject=actor,
        selector="browser-docker",
        expected_revision=0,
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        contract=contract,
        adapter_version=2,
        lease_seconds=240,
    )
    return reference


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
