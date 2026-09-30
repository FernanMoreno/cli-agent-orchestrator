from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import jwt
import pytest

from cli_agent_orchestrator.backends import work_registry
from cli_agent_orchestrator.backends.docker_backend import DockerWorkBackend
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_launch_gateway import (
    DurableLaunchRequest,
    build_durable_launch_gateway,
)
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning
from scripts.local_work_docker_demo import (
    LocalWorkJwksIssuer,
    provision_local_static_work,
)


@pytest.fixture(autouse=True)
def _clear_auth_cache(monkeypatch):
    for key in (
        "AUTH0_DOMAIN",
        "CAO_AUTH_JWKS_URI",
        "CAO_AUTH_ISSUER",
        "CAO_AUTH_AUDIENCE",
        "CAO_AUTH_LOCAL_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    auth.get_jwks_cache().clear()
    yield
    auth.get_jwks_cache().clear()


def test_disposable_local_issuer_produces_a_signature_verified_admin_bearer(monkeypatch):
    with LocalWorkJwksIssuer() as issuer:
        for key, value in issuer.auth_environment().items():
            monkeypatch.setenv(key, value)

        principal = auth.principal_from_token(issuer.token)

    assert principal.kind == "jwt"
    assert principal.issuer == issuer.issuer
    assert principal.subject == issuer.subject
    assert principal.scopes == frozenset({auth.SCOPE_ADMIN})
    assert auth.is_verified_principal(principal)


def test_disposable_local_issuer_rejects_a_tampered_bearer(monkeypatch):
    with LocalWorkJwksIssuer() as issuer:
        for key, value in issuer.auth_environment().items():
            monkeypatch.setenv(key, value)
        header, payload, signature = issuer.token.split(".")
        changed_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
        tampered = f"{header}.{payload}.{changed_signature}"

        with pytest.raises(jwt.PyJWTError):
            auth.principal_from_token(tampered)


@pytest.mark.parametrize("existing_scheduler", [False, True])
def test_local_setup_provisions_only_through_internal_authority_services(
    tmp_path, monkeypatch, existing_scheduler
):
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("a static C compiler is required for the local Docker demo worker")
    repository = WorkRepository(tmp_path / "work.sqlite3")
    repository.initialize()
    if existing_scheduler:
        from cli_agent_orchestrator.services.work_scheduler import WorkScheduler

        WorkScheduler(repository).configure(
            capacity=2, max_queue=9, aging_seconds=10, expected_policy_revision=0
        )
    checkout_root = tmp_path / "checkout"
    checkout_root.mkdir(mode=0o700)
    worker_source = Path(__file__).resolve().parent / "local-demo-worker.c"
    worker_path = tmp_path / "cao-local-demo-worker"
    built = subprocess.run(
        [
            compiler,
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(worker_source),
            "-o",
            str(worker_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert built.returncode == 0, built.stderr

    with LocalWorkJwksIssuer() as issuer:
        for key, value in issuer.auth_environment().items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("CAO_AUTH_LOCAL_TOKEN", issuer.token)
        principal = auth.principal_from_token(issuer.token)
        selection = provision_local_static_work(
            repository,
            principal,
            selection="local-docker",
            project_id="local-docker-demo",
            checkout_root=checkout_root,
            worker_bytes=worker_path.read_bytes(),
        )

    assert selection.selector == "local-docker"
    assert selection.revision == 1
    with repository.read_snapshot() as connection:
        policy = tuple(
            connection.execute(
                "SELECT revision,capacity,max_queue FROM work_scheduler_policy"
            ).fetchone()
        )
        assert policy == ((1, 2, 9) if existing_scheduler else (1, 1, 8))
    provision = WorkProvisioning(repository).resolve_launch(principal, selection.selector)
    assert provision.contract.backend == "docker-local"
    assert provision.contract.operation_kind == "launch"
    assert provision.adapter_version == 2
    assert provision.contract.permissions.tools == ()
    assert provision.contract.permissions.network == ()
    assert provision.contract.resources.write_paths == ()
    assert len(provision.contract.executable_identities) == 1
    assert provision.contract.executable_identities[0].static is True

    configured = work_registry.local_work_backends_for(
        repository,
        environ={
            "CAO_WORK_DOCKER_LOCAL": "1",
            "CAO_WORK_DOCKER_IMAGE_ID": "sha256:" + "e" * 64,
        },
    )
    assert configured["docker-local"].repository is repository
    assert work_registry.WORK_BACKENDS == {}

    monkeypatch.setattr(DockerWorkBackend, "preflight_work", lambda self, contract: None)
    gateway = build_durable_launch_gateway(repository, backends=configured)
    receipt = gateway.admit(
        principal,
        DurableLaunchRequest(
            selection="local-docker",
            agent_profile="developer",
            session_name="local-docker-admission",
            message="local static worker check",
            allowed_tools=(),
        ),
    )
    assert receipt.state == "queued"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_local_setup_dispatches_bundled_worker_and_cleans_attempt(tmp_path, monkeypatch):
    image_id = os.environ.get("CAO_T019_DOCKER_IMAGE_ID")
    if not image_id:
        pytest.skip("set CAO_T019_DOCKER_IMAGE_ID to run the local Docker acceptance")
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("a static C compiler is required for the local Docker demo worker")

    repository = WorkRepository(tmp_path / "work.sqlite3")
    repository.initialize()
    checkout_root = tmp_path / "checkout"
    checkout_root.mkdir(mode=0o700)
    worker_source = Path(__file__).resolve().parent / "local-demo-worker.c"
    worker_path = tmp_path / "cao-local-demo-worker"
    built = subprocess.run(
        [
            compiler,
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(worker_source),
            "-o",
            str(worker_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert built.returncode == 0, built.stderr

    with LocalWorkJwksIssuer() as issuer:
        for key, value in issuer.auth_environment().items():
            monkeypatch.setenv(key, value)
        principal = auth.principal_from_token(issuer.token)
        provision_local_static_work(
            repository,
            principal,
            selection="local-docker-acceptance",
            project_id="local-docker-demo",
            checkout_root=checkout_root,
            worker_bytes=worker_path.read_bytes(),
        )

    backends = work_registry.local_work_backends_for(
        repository,
        environ={
            "CAO_WORK_DOCKER_LOCAL": "1",
            "CAO_WORK_DOCKER_IMAGE_ID": image_id,
        },
    )
    backend = backends["docker-local"]
    executions = []
    execute = backend.execute_bound_process

    def capture_execution(*args, **kwargs):
        result = execute(*args, **kwargs)
        executions.append(result)
        return result

    backend.execute_bound_process = capture_execution
    gateway = build_durable_launch_gateway(repository, backends=backends)
    receipt = gateway.admit(
        principal,
        DurableLaunchRequest(
            selection="local-docker-acceptance",
            agent_profile="developer",
            session_name="local-docker-acceptance",
            message="run the bundled static worker",
            allowed_tools=(),
        ),
    )

    dispatched = await gateway.dispatch_registered_next()

    assert dispatched["id"] == receipt.work_item_id
    assert dispatched["state"] == "running"
    assert len(executions) == 1
    assert executions[0].returncode == 0
    assert executions[0].stdout == b"CAO_LOCAL_DOCKER_WORKER_RAN\n"
    assert executions[0].stderr == b""
    container_name = backend._container_name(receipt.attempt_id, receipt.generation)
    image_tag = backend._attempt_image_tag(receipt.attempt_id, receipt.generation)
    assert backend._run(["container", "inspect", container_name], check=False).returncode != 0
    assert backend._run(["image", "inspect", image_tag], check=False).returncode != 0
