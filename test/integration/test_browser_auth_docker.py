"""Opt-in cookie API -> durable launch -> real Docker -> validated durable bytes.

CAO_BROWSER_DOCKER_ACCEPTANCE=1 uv run pytest -o addopts= --no-cov -q
 test/integration/test_browser_auth_docker.py

Uses existing immutable local rootfs and temporary account/JWKS/Work stores.
The server validates and persists actual worker stdout with WorkService.settle_attempt; this
HEAD-compatible test does not claim worker MCP task_received/submit_result.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from test.fixtures import browser_docker_harness as fixture

import pytest
from fastapi.testclient import TestClient

from cli_agent_orchestrator.api import main
from cli_agent_orchestrator.backends.docker_backend import DockerWorkBackend
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.browser_auth import BrowserAuthService
from cli_agent_orchestrator.services.step_output_store import ArtifactRef, ImmutableResultStore
from cli_agent_orchestrator.services.work_launch_gateway import build_durable_launch_gateway
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning
from cli_agent_orchestrator.services.work_service import WorkService


@pytest.mark.integration
def test_cookie_renewal_and_logout_preserve_running_real_docker_work(tmp_path, monkeypatch):
    if os.environ.get("CAO_BROWSER_DOCKER_ACCEPTANCE") != "1":
        pytest.skip("set CAO_BROWSER_DOCKER_ACCEPTANCE=1 for real local Docker acceptance")
    image = (
        os.environ.get("CAO_BROWSER_DOCKER_IMAGE_ID")
        or subprocess.check_output(
            ["docker", "image", "inspect", "--format", "{{.Id}}", "cao-work-empty-rootfs:local"],
            text=True,
            timeout=15,
        ).strip()
    )
    assert image.startswith("sha256:") and len(image) == 71
    source = Path(__file__).resolve().parents[1] / "fixtures/browser_docker_worker.c"
    worker_path = tmp_path / "worker"
    subprocess.run(
        [
            "cc",
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(source),
            "-o",
            str(worker_path),
        ],
        check=True,
        timeout=60,
    )
    worker = worker_path.read_bytes()
    repository = WorkRepository(tmp_path / "work.sqlite3")
    repository.initialize()
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    monkeypatch.setenv("CAO_ENABLE_PUBLIC_WORK_INGRESS", "true")
    with fixture.LocalWorkJwksIssuer() as issuer:
        for name, value in issuer.auth_environment().items():
            monkeypatch.setenv(name, value)
        actor = auth.principal_from_token(issuer.token)
        assert actor.kind == "jwt" and auth.SCOPE_ADMIN in actor.scopes
        reference = fixture.provision_launch(repository, actor, tmp_path, worker)
        provision_before = WorkProvisioning(repository).resolve_launch(actor, "browser-docker")
        service = BrowserAuthService(
            tmp_path / "browser-auth.sqlite3",
            {
                "id": actor.id,
                "issuer": actor.issuer,
                "subject": actor.subject,
                "kind": actor.kind,
                "scopes": sorted(actor.scopes),
            },
        )
        service.create_account("browser-operator", "Docker-fixture-passphrase")
        backend = DockerWorkBackend(image_ref=image, repository=repository)
        results, inputs = [], []
        execute = backend.execute_bound_process

        def capture(*args, **kwargs):
            inputs.append(kwargs["worker_input"])
            result = execute(*args, **kwargs)
            results.append(result)
            return result

        monkeypatch.setattr(backend, "execute_bound_process", capture)
        gateway = build_durable_launch_gateway(repository, backends={"docker-local": backend})
        monkeypatch.setattr(main.app.state, "durable_launch_gateway", gateway, raising=False)
        monkeypatch.setattr(main.app.state, "browser_auth", service, raising=False)
        monkeypatch.setattr(
            main.app.state,
            "browser_auth_config",
            {
                "enabled": True,
                "installation_id": "dockerproof",
                "canonical_origin": "http://127.0.0.1",
                "transport_policy": "loopback_http",
            },
            raising=False,
        )
        # No lifespan: only unrelated daemons/startup are omitted. The actual
        # production routes, dependencies, middleware and launch gateway run.
        client = TestClient(
            main.app,
            base_url="http://127.0.0.1",
            client=("127.0.0.1", 50000),
            headers={"Origin": "http://127.0.0.1", "X-CAO-Browser": "1"},
        )
        try:
            login = client.post(
                "/auth/login",
                json={
                    "username": "browser-operator",
                    "password": "Docker-fixture-passphrase",
                    "remember": True,
                },
            )
            assert login.status_code == 200, login.text
            assert "HttpOnly" in login.headers["set-cookie"]
            assert not main.app.dependency_overrides
            body = {
                "selection": "browser-docker",
                "agent_profile": "developer",
                "session_name": "browser-docker",
                "message": "Keep running after logout.",
                "allowed_tools": [],
            }
            admitted = client.post("/work-launches", json=body)
            assert admitted.status_code == 202, admitted.text
            receipt = admitted.json()
            attempt_id, generation, work_id = (
                receipt["attempt_id"],
                receipt["generation"],
                receipt["work_item_id"],
            )
            container_name = backend._container_name(attempt_id, generation)
            with ThreadPoolExecutor(max_workers=1) as executor:
                dispatched = executor.submit(
                    lambda: asyncio.run(gateway.dispatch_registered_next())
                )
                deadline = time.monotonic() + 45
                while True:
                    inspected = backend._run(
                        ["container", "inspect", "--format", "{{.State.Running}}", container_name],
                        check=False,
                    )
                    if inspected.returncode == 0 and inspected.stdout.strip() == "true":
                        break
                    if dispatched.done():
                        dispatched.result()
                        pytest.fail("Docker worker exited before its running state was observed")
                    assert time.monotonic() < deadline, "Docker worker did not start"
                    time.sleep(0.1)
                for _ in range(3):
                    renewed = client.post("/auth/renew")
                    assert renewed.status_code == 200, renewed.text
                    assert "set-cookie" not in renewed.headers
                    assert (
                        WorkProvisioning(repository).resolve_launch(actor, "browser-docker")
                        == provision_before
                    )
                assert client.post("/auth/logout").status_code == 204
                assert client.get("/auth/session").status_code == 401
                assert client.post("/work-launches", json=body).status_code == 401
                assert not dispatched.done(), "logout must precede the real worker output"
                after_logout = backend._run(
                    ["container", "inspect", "--format", "{{.State.Running}}", container_name],
                    check=False,
                )
                assert after_logout.returncode == 0 and after_logout.stdout.strip() == "true"
                assert dispatched.result(timeout=45)["id"] == work_id
            assert len(results) == 1 and results[0].returncode == 0
            assert results[0].stdout == fixture.MARKER and results[0].stderr == b""
            assert inputs == [fixture.CONTEXT + b"\n\n" + body["message"].encode()]
            artifacts = ImmutableResultStore(tmp_path / "artifacts")

            def validate(content):
                assert content == fixture.MARKER
                return {"valid": True, "worker_stdout_sha256": hashlib.sha256(content).hexdigest()}

            settled = WorkService(repository).settle_attempt(
                work_id,
                generation=generation,
                content=results[0].stdout,
                artifacts=artifacts,
                validate=validate,
                validator_id="browser-docker-fixed-marker-v1",
                actor_id=actor.id,
            )
            # HEAD's launch adapter intentionally leaves receipt/execution
            # unproven. A validated Docker output is durable, but cannot invent
            # an authenticated worker receipt or a successful Work transition.
            assert settled["state"] == "running"
            assert settled["attempts"][0]["state"] == "sent"
            assert settled["accepted_result_id"] is None
            result_id = hashlib.sha256(
                f"{attempt_id}:{hashlib.sha256(fixture.MARKER).hexdigest()}".encode()
            ).hexdigest()
            stored_result = repository.get_result(result_id)
            assert stored_result["validation_state"] == "verified"
            assert json.loads(stored_result["validation_evidence"])["valid"] is True
            assert (
                artifacts.read(
                    ArtifactRef(
                        stored_result["content_hash"],
                        stored_result["immutable_location"],
                        stored_result["byte_length"],
                    )
                )
                == fixture.MARKER
            )
            assert asyncio.run(gateway.dispatch_registered_next()) is None
            assert len(results) == 1 and len(settled["attempts"]) == 1
            with repository.read_snapshot() as connection:
                counts = {
                    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in (
                        "work_jobs",
                        "work_grants",
                        "work_items",
                        "work_attempts",
                        "work_results",
                        "work_launch_provisions",
                    )
                }
            assert all(value == 1 for value in counts.values())
            assert (
                WorkProvisioning(repository).resolve_launch(
                    auth.principal_from_token(issuer.token), "browser-docker"
                )
                == provision_before
            )
            assert (
                backend._run(["container", "inspect", container_name], check=False).returncode != 0
            )
            proof = {
                "image_id": image,
                "worker_sha256": hashlib.sha256(worker).hexdigest(),
                "principal_id": actor.id,
                "provision_revision": reference.revision,
                "work_item_id": work_id,
                "attempt_id": attempt_id,
                "result_id": result_id,
                "accepted_result_id": None,
                "work_state": settled["state"],
                "attempt_state": settled["attempts"][0]["state"],
                "counts": counts,
                "browser_renewals": 3,
                "logout_before_worker_exit": True,
                "result_transport": "real Docker stdout, server WorkService.settle_attempt validator",
                "worker_mcp_result_transport": False,
            }
            proof_path = os.environ.get("CAO_BROWSER_DOCKER_PROOF")
            if proof_path:
                Path(proof_path).write_text(json.dumps(proof, indent=2) + "\n")
        finally:
            client.close()
