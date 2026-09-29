"""Run one real immutable Work process in the local Docker Engine profile."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from dataclasses import replace

import pytest

from cli_agent_orchestrator.backends.docker_backend import DockerWorkBackend
from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
from cli_agent_orchestrator.models.work_origin import ManagedLineageIntent, WorkAttemptRef
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_contract import WorkContracts
from cli_agent_orchestrator.services.work_launch_gateway import build_durable_launch_gateway
from test.integration.t098.test_work_launch_dispatch import (
    ProcessOnlyBackend,
    _minimal_static_worker,
    _setup,
)


def _minimal_static_mcp_worker(tmp_path):
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("a static C compiler is required for the Docker MCP acceptance")
    source = Path(__file__).resolve().parents[2] / "fixtures" / "work_docker_mcp_worker.c"
    executable = tmp_path / "cao-work-mcp-worker"
    built = subprocess.run(
        [compiler, "-static", "-O2", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(executable)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert built.returncode == 0, built.stderr
    return executable.read_bytes()


def _minimal_static_mcp_worker_from(tmp_path, fixture_name):
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("a static C compiler is required for the Docker MCP acceptance")
    source = Path(__file__).resolve().parents[2] / "fixtures" / fixture_name
    executable = tmp_path / fixture_name.removesuffix(".c")
    built = subprocess.run(
        [compiler, "-static", "-O2", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(executable)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert built.returncode == 0, built.stderr
    return executable.read_bytes()


def _preprovision_lineage(runtime, repository, owner, tmp_path, *, tools):
    child = auth._verified_principal(
        "https://issuer.test", "t019-docker-child", [auth.SCOPE_WRITE], "jwt"
    )
    receiver = auth._verified_principal(
        "https://issuer.test", "t019-docker-receiver", [auth.SCOPE_WRITE], "jwt"
    )
    root_permissions = Permissions(
        tools={"knowledge.read", "tool.read", *tools},
        paths={str(tmp_path)},
        commands={"/worker"},
    )
    with repository.read_snapshot() as connection:
        root = connection.execute(
            "SELECT revision,expires_at FROM work_grants WHERE id=?", ("t098-root",)
        ).fetchone()
    assert root is not None
    authority = WorkAuthority(repository)
    child_grant = authority.delegate(
        owner,
        parent_grant_id="t098-root",
        expected_parent_revision=root["revision"],
        child_principal=child,
        providers={"scratch_worker"},
        permissions=root_permissions,
        expires_at=min(root["expires_at"], time.time() + 240),
    )
    receiver_grant = authority.delegate(
        owner,
        parent_grant_id="t098-root",
        expected_parent_revision=root["revision"],
        child_principal=receiver,
        providers={"scratch_worker"},
        permissions=root_permissions,
        expires_at=min(root["expires_at"], time.time() + 240),
    )
    origins = runtime.origins
    child_subject = origins.origin_authority.register_subject(
        owner,
        verified_subject=child,
        kind="child",
        issuer_id=owner.id,
        expected_revision=0,
    )
    child_authorization = origins.origin_authority.authorize(
        owner,
        subject=child,
        origin_kind="child",
        grant_id=child_grant.id,
        grant_revision=child_grant.revision,
        actions={"admit_child", "execute"},
        expires_at=min(child_grant.expires_at, time.time() + 120),
        expected_revision=0,
    )
    receiver_subject = origins.origin_authority.register_subject(
        owner,
        verified_subject=receiver,
        kind="receiver",
        issuer_id=owner.id,
        expected_revision=0,
    )
    receiver_authorization = origins.origin_authority.authorize(
        owner,
        subject=receiver,
        origin_kind="receiver",
        grant_id=receiver_grant.id,
        grant_revision=receiver_grant.revision,
        actions={"task_received"},
        expires_at=min(receiver_grant.expires_at, time.time() + 120),
        expected_revision=0,
    )
    return child, receiver, child_subject, child_authorization, receiver_subject, receiver_authorization


def _managed_launch_intent(contract, *, message, allowed_tools):
    payload = {
        "terminal_id": "d0190001",
        "agent_profile": "developer",
        "session_name": "t019-managed-child",
        "message": message,
        "allowed_tools": list(allowed_tools),
        "command_token": "/worker",
    }
    delivery = WorkDeliveryEnvelope(
        operation_kind="launch",
        adapter_version=2,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )
    return ManagedLineageIntent(contract=contract, delivery=delivery, lease_seconds=120)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_docker_backend_runs_one_bound_static_worker_and_removes_container(tmp_path):
    image_id = os.environ.get("CAO_T019_DOCKER_IMAGE_ID")
    docker_cli = os.environ.get("CAO_T019_DOCKER_CLI", "docker")
    if not image_id:
        pytest.skip("run test/integration/t019/run-docker-backend-acceptance.sh for real Docker")

    outputs = []
    docker_calls = []

    def backend_factory(marker, repository):
        backend = DockerWorkBackend(
            image_ref=image_id,
            docker_command=docker_cli,
            repository=repository,
        )
        execute = backend.execute_bound_process
        run = backend._run

        def capture_run(arguments, **kwargs):
            docker_calls.append(tuple(arguments))
            return run(arguments, **kwargs)

        backend._run = capture_run

        def capture(*args, **kwargs):
            result = execute(*args, **kwargs)
            outputs.append(result)
            return result

        backend.execute_bound_process = capture
        return backend

    repository, principal, gateway, backend, _marker, request = _setup(
        tmp_path,
        backend_factory=backend_factory,
        worker_binary=_minimal_static_worker(),
        # Work's checkout must remain within its read authority. Docker grants
        # no host path mount, so this broader permission stays inaccessible.
        contract_paths=(str(tmp_path),),
        contract_write_paths=(),
    )
    receipt = gateway.admit(principal, request)

    result = await gateway.dispatch_registered_next()

    assert result["id"] == receipt.work_item_id
    assert result["state"] == "running"
    assert len(outputs) == 1
    assert outputs[0].returncode == 0
    assert outputs[0].stdout == b"CAO_STAGED_WORKER_RAN_AFTER_AUTHORIZATION\n"
    assert outputs[0].stderr == b""
    create = next(call for call in docker_calls if call[:2] == ("container", "create"))
    assert "--network=none" in create
    assert "--cap-drop=ALL" in create
    assert "--cap-add=SETUID" in create and "--cap-add=SETGID" in create
    assert "--security-opt=no-new-privileges" in create
    assert "--interactive" in create and "--pids-limit=2" in create
    assert "--pid=host" not in create and "--pid=private" not in create
    assert "--mount" not in create
    image_tag = backend._attempt_image_tag(receipt.attempt_id, receipt.generation)
    assert any(
        call[:3] == ("image", "build", "--platform=linux/amd64")
        and call[-2:] == (image_tag, "-")
        for call in docker_calls
    )
    assert not any(call[:2] == ("image", "import") for call in docker_calls)
    assert any(
        call[:3] == ("container", "inspect", "--format")
        and "{{json .}}" in call
        for call in docker_calls
    )
    container_name = backend._container_name(receipt.attempt_id, receipt.generation)
    assert backend._run(["container", "inspect", container_name], check=False).returncode != 0
    assert backend._run(["image", "inspect", image_tag], check=False).returncode != 0
    assert not tuple(repository.executable_content_root.glob(".docker-attempt-*"))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_docker_backend_bridges_only_managed_work_mcp_without_container_network(tmp_path):
    image_id = os.environ.get("CAO_T019_DOCKER_IMAGE_ID")
    docker_cli = os.environ.get("CAO_T019_DOCKER_CLI", "docker")
    if not image_id:
        pytest.skip("run test/integration/t019/run-docker-backend-acceptance.sh for real Docker")

    outputs = []
    backend_holder = []

    def backend_factory(_marker, repository):
        backend = DockerWorkBackend(
            image_ref=image_id,
            docker_command=docker_cli,
            repository=repository,
        )
        backend_holder.append(backend)
        execute = backend.execute_bound_process
        backend.execute_bound_process = lambda *args, **kwargs: outputs.append(
            execute(*args, **kwargs)
        ) or outputs[-1]
        return backend

    repository, principal, gateway, _backend, _marker, request = _setup(
        tmp_path,
        backend_factory=backend_factory,
        worker_binary=_minimal_static_mcp_worker(tmp_path),
        contract_tools=("cao.work.child",),
        request_tools=("cao.work.child",),
        contract_paths=(str(tmp_path),),
        contract_write_paths=(),
    )
    receipt = gateway.admit(principal, request)
    result = await gateway.dispatch_registered_next()

    assert result["id"] == receipt.work_item_id
    assert result["state"] == "running"
    assert len(outputs) == 1
    assert outputs[0].returncode == 0
    response = json.loads(outputs[0].stdout)
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "docker-mcp"
    assert response["error"] == {"code": -32001, "message": "Managed Work request rejected"}
    assert outputs[0].stderr == b""
    with repository.read_snapshot() as connection:
        assert connection.execute(
            "SELECT count(*) FROM work_mcp_proxy_effects WHERE attempt_id=?",
            (receipt.attempt_id,),
        ).fetchone()[0] == 1
    backend = backend_holder[0]
    container_name = backend._container_name(receipt.attempt_id, receipt.generation)
    image_tag = backend._attempt_image_tag(receipt.attempt_id, receipt.generation)
    assert backend._run(["container", "inspect", container_name], check=False).returncode != 0
    assert backend._run(["image", "inspect", image_tag], check=False).returncode != 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_docker_mcp_admits_only_preprovisioned_managed_child(tmp_path):
    image_id = os.environ.get("CAO_T019_DOCKER_IMAGE_ID")
    docker_cli = os.environ.get("CAO_T019_DOCKER_CLI", "docker")
    if not image_id:
        pytest.skip("run test/integration/t019/run-docker-backend-acceptance.sh for real Docker")

    outputs = []
    backend_holder = []
    worker_binary = _minimal_static_mcp_worker_from(
        tmp_path, "work_docker_child_worker.c"
    )

    def backend_factory(_marker, repository):
        backend = DockerWorkBackend(
            image_ref=image_id,
            docker_command=docker_cli,
            repository=repository,
        )
        backend_holder.append(backend)
        execute = backend.execute_bound_process
        backend.execute_bound_process = lambda *args, **kwargs: outputs.append(
            execute(*args, **kwargs)
        ) or outputs[-1]
        return backend

    repository, owner, gateway, _backend, _marker, request = _setup(
        tmp_path,
        backend_factory=backend_factory,
        worker_binary=worker_binary,
        contract_tools=("cao.work.child",),
        request_tools=("cao.work.child",),
        contract_paths=(str(tmp_path),),
        contract_write_paths=(),
    )
    child, receiver, child_subject, child_authorization, receiver_subject, receiver_authorization = (
        _preprovision_lineage(
            gateway._launch_runtime_provider._runtime,
            repository,
            owner,
            tmp_path,
            tools={"cao.work.child"},
        )
    )
    parent_contract = gateway._launch_runtime_provider._runtime._provisioning.resolve_launch(
        owner, "t098-selection"
    ).contract
    child_contract = parent_contract.model_copy(update={"id": "t019-docker-child"})
    intent = _managed_launch_intent(
        child_contract,
        message="child worker",
        allowed_tools=("cao.work.child",),
    )
    arguments = {
        "child_subject_id": child.id,
        "receiver_subject_id": receiver.id,
        "intent": intent.model_dump(mode="json"),
        "idempotency_key": "t019-docker-child-call",
    }
    receipt = gateway.admit(
        owner, replace(request, message="MCPARGS:" + json.dumps(arguments, separators=(",", ":")))
    )

    result = await gateway.dispatch_registered_next()

    assert result["id"] == receipt.work_item_id
    assert result["state"] == "running"
    assert len(outputs) == 1 and outputs[0].returncode == 0
    response = json.loads(outputs[0].stdout)
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "docker-child"
    assert "result" in response
    child_result = json.loads(response["result"]["content"][0]["text"])
    child_work = repository.get_work(child_result["work_item_id"])
    assert child_work["lineage_protocol"] == "managed-v1"
    assert child_work["parent_work_item_id"] == receipt.work_item_id
    assert child_work["state"] == "queued"
    assert child_subject.subject_id == child.id
    assert child_authorization.ref.subject_id == child.id
    assert receiver_subject.subject_id == receiver.id
    assert receiver_authorization.ref.subject_id == receiver.id
    with repository.read_snapshot() as connection:
        event = connection.execute(
            "SELECT events.state FROM work_mcp_proxy_effect_events AS events "
            "JOIN work_mcp_proxy_effects AS effects USING(effect_id) "
            "WHERE effects.attempt_id=? ORDER BY events.sequence DESC LIMIT 1",
            (receipt.attempt_id,),
        ).fetchone()
        assert event is not None and event["state"] == "completed"
        child_binding = WorkContracts(repository)._revalidate_order(
            connection,
            child_work["attempts"][0]["id"],
            generation=child_work["attempts"][0]["generation"],
        )
        assert child_binding.principal_id == child.id
    backend = backend_holder[0]
    assert backend._run(
        ["container", "inspect", backend._container_name(receipt.attempt_id, receipt.generation)],
        check=False,
    ).returncode != 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_docker_mcp_commits_exact_receiver_acceptance_before_receipt(tmp_path):
    image_id = os.environ.get("CAO_T019_DOCKER_IMAGE_ID")
    docker_cli = os.environ.get("CAO_T019_DOCKER_CLI", "docker")
    if not image_id:
        pytest.skip("run test/integration/t019/run-docker-backend-acceptance.sh for real Docker")

    worker_binary = _minimal_static_mcp_worker_from(
        tmp_path, "work_docker_receiver_worker.c"
    )

    def test_backend_factory(marker, repository):
        return ProcessOnlyBackend(marker, repository)

    repository, owner, _original_gateway, test_backend, _marker, request = _setup(
        tmp_path,
        backend_factory=test_backend_factory,
        worker_binary=worker_binary,
        contract_tools=("cao.work.task_received",),
        request_tools=(),
        capacity=2,
        contract_paths=(str(tmp_path),),
        contract_write_paths=(),
    )
    docker_backend = DockerWorkBackend(
        image_ref=image_id,
        docker_command=docker_cli,
        repository=repository,
    )
    gateway = build_durable_launch_gateway(
        repository,
        backends={"test": test_backend, "docker": docker_backend},
    )
    runtime = gateway._launch_runtime_provider._runtime
    child, receiver, child_subject, child_authorization, receiver_subject, receiver_authorization = (
        _preprovision_lineage(
            runtime,
            repository,
            owner,
            tmp_path,
            tools={"cao.work.task_received"},
        )
    )
    parent_receipt = gateway.admit(owner, request)

    first = await gateway.dispatch_registered_next()
    assert first["id"] == parent_receipt.work_item_id
    with repository.read_snapshot() as connection:
        parent_binding = WorkContracts(repository)._revalidate_order(
            connection,
            parent_receipt.attempt_id,
            generation=parent_receipt.generation,
        )
    child_contract = parent_binding.contract.model_copy(
        update={"id": "t019-docker-receiver-child", "backend": "docker"}
    )
    intent = _managed_launch_intent(
        child_contract,
        message="TASK_RECEIVED",
        allowed_tools=("cao.work.task_received",),
    )
    handoff = runtime.origins.admit(
        authenticated_context=runtime.origins.authenticated_context(owner, child, receiver),
        parent_attempt_ref=WorkAttemptRef(
            work_item_id=parent_receipt.work_item_id,
            attempt_id=parent_receipt.attempt_id,
            generation=parent_receipt.generation,
        ),
        child_subject_ref=child_subject,
        child_authorization_ref=child_authorization.ref,
        receiver_subject_ref=receiver_subject,
        receiver_authorization_ref=receiver_authorization.ref,
        intent=intent,
        idempotency_key="t019-docker-receiver-child",
        kind="child",
    )
    child_work = runtime._admission.admit_managed_lineage(handoff)

    result = await gateway.dispatch_registered_next()

    assert result["id"] == child_work["id"]
    assert result["attempts"][0]["state"] == "acknowledged"
    with repository.read_snapshot() as connection:
        acceptance = connection.execute(
            "SELECT receiver_subject_id,delivery_id,delivery_hash "
            "FROM work_task_receiver_acceptances WHERE attempt_id=? AND generation=1",
            (child_work["attempts"][0]["id"],),
        ).fetchone()
        receipt = connection.execute(
            "SELECT delivery_id,delivery_hash FROM work_task_received_receipts "
            "WHERE attempt_id=? AND generation=1",
            (child_work["attempts"][0]["id"],),
        ).fetchone()
        assert acceptance is not None and receipt is not None
        assert acceptance["receiver_subject_id"] == receiver.id
        assert (acceptance["delivery_id"], acceptance["delivery_hash"]) == (
            receipt["delivery_id"],
            receipt["delivery_hash"],
        )
        child_binding = WorkContracts(repository)._revalidate_order(
            connection,
            child_work["attempts"][0]["id"],
            generation=child_work["attempts"][0]["generation"],
        )
        assert child_binding.principal_id == child.id
        assert connection.execute(
            "SELECT count(*) FROM work_mcp_proxy_effects WHERE attempt_id=?",
            (child_work["attempts"][0]["id"],),
        ).fetchone()[0] == 1
    container_name = docker_backend._container_name(
        child_work["attempts"][0]["id"], child_work["attempts"][0]["generation"]
    )
    image_tag = docker_backend._attempt_image_tag(
        child_work["attempts"][0]["id"], child_work["attempts"][0]["generation"]
    )
    assert docker_backend._run(["container", "inspect", container_name], check=False).returncode != 0
    assert docker_backend._run(["image", "inspect", image_tag], check=False).returncode != 0
