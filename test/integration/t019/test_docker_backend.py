"""Run real immutable Work processes in the local Docker Engine profile."""

import asyncio
import errno
import hashlib
import hmac
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from test.integration.t098.test_work_launch_dispatch import (
    ProcessOnlyBackend,
    _minimal_static_worker,
    _setup,
)

import pytest

from cli_agent_orchestrator.backends.docker_backend import DockerWorkBackend
from cli_agent_orchestrator.models.work_contract import (
    ContractSnapshot,
    EffectiveWorkContractV2,
)
from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
from cli_agent_orchestrator.models.work_origin import ManagedLineageIntent, WorkAttemptRef
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_contract import WorkContracts
from cli_agent_orchestrator.services.work_launch_gateway import (
    DurableLaunchRequest,
    build_durable_launch_gateway,
)
from cli_agent_orchestrator.services.work_mcp_proxy import WorkMcpProxy
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning


def _minimal_static_mcp_worker(tmp_path):
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("a static C compiler is required for the Docker MCP acceptance")
    source = Path(__file__).resolve().parents[2] / "fixtures" / "work_docker_mcp_worker.c"
    executable = tmp_path / "cao-work-mcp-worker"
    built = subprocess.run(
        [
            compiler,
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(source),
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert built.returncode == 0, built.stderr
    return executable.read_bytes()


def _minimal_static_waiting_worker(tmp_path):
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("a static C compiler is required for the Docker restart acceptance")
    source = Path(__file__).resolve().parents[2] / "fixtures" / "work_docker_waiting_worker.c"
    executable = tmp_path / "cao-work-waiting-worker"
    built = subprocess.run(
        [
            compiler,
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(source),
            "-o",
            str(executable),
        ],
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
        [
            compiler,
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(source),
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert built.returncode == 0, built.stderr
    return executable.read_bytes()


def _minimal_static_sibling_probe_worker(tmp_path):
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("a static C compiler is required for the Docker MCP acceptance")
    source = tmp_path / "cao-work-sibling-probe.c"
    source.write_text(
        r"""#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

static int write_all(int descriptor, const char *data, size_t size) {
    size_t written = 0U;
    while (written < size) {
        ssize_t count = write(descriptor, data + written, size - written);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return -1;
        written += (size_t)count;
    }
    return 0;
}

int main(void) {
    char input[65536];
    size_t input_size = 0U;
    while (input_size < sizeof(input) - 1U) {
        ssize_t count = read(STDIN_FILENO, input + input_size,
                             sizeof(input) - 1U - input_size);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) return 10;
        if (count == 0) break;
        input_size += (size_t)count;
    }
    input[input_size] = '\0';
    char *payload = input;
    if (input_size == 0U) return 11;
    char *context_end = strstr(input, "\n\n");
    if (context_end != NULL) payload = context_end + 2;

    const char *request_id = NULL;
    const char *request = NULL;
    long victim_pid = -1;
    if (strcmp(payload, "victim") == 0) {
        request_id = "victim";
        request = "{\"jsonrpc\":\"2.0\",\"id\":\"victim\",\"method\":\"tools/call\","
                  "\"params\":{\"name\":\"cao.work.child\",\"arguments\":{}}}\n";
    } else if (strncmp(payload, "attacker:", 9U) == 0) {
        char *end = NULL;
        errno = 0;
        victim_pid = strtol(payload + 9U, &end, 10);
        if (errno != 0 || end == payload + 9U || *end != '\0' || victim_pid <= 2) return 12;
        request_id = "attacker";
        request = "{\"jsonrpc\":\"2.0\",\"id\":\"attacker\",\"method\":\"tools/call\","
                  "\"params\":{\"name\":\"cao.work.child\",\"arguments\":{}}}\n";
    } else {
        return 13;
    }

    struct stat namespace_stat;
    struct stat mcp_descriptor_stat;
    if (stat("/proc/self/ns/pid", &namespace_stat) != 0 ||
        fstat(3, &mcp_descriptor_stat) != 0 || !S_ISSOCK(mcp_descriptor_stat.st_mode)) return 14;

    int proc_fd3_visible = 0;
    int proc_fd3_errno = -1;
    int pidfd_open_succeeded = 0;
    int pidfd_open_errno = -1;
    int pidfd_getfd_attempted = 0;
    int pidfd_getfd_succeeded = 0;
    int pidfd_getfd_errno = -1;
    if (victim_pid > 0) {
        char path[64];
        char link_target[128];
        int path_size = snprintf(path, sizeof(path), "/proc/%ld/fd/3", victim_pid);
        if (path_size <= 0 || (size_t)path_size >= sizeof(path)) return 15;
        errno = 0;
        ssize_t link_size = readlink(path, link_target, sizeof(link_target));
        if (link_size >= 0) {
            proc_fd3_visible = 1;
        } else {
            proc_fd3_errno = errno;
        }
#ifdef SYS_pidfd_open
        errno = 0;
        long pidfd = syscall(SYS_pidfd_open, (pid_t)victim_pid, 0U);
        if (pidfd >= 0) {
            pidfd_open_succeeded = 1;
#ifdef SYS_pidfd_getfd
            pidfd_getfd_attempted = 1;
            errno = 0;
            long duplicate = syscall(SYS_pidfd_getfd, (int)pidfd, 3, 0U);
            if (duplicate >= 0) {
                pidfd_getfd_succeeded = 1;
                (void)close((int)duplicate);
            } else {
                pidfd_getfd_errno = errno;
            }
#else
            pidfd_getfd_errno = ENOSYS;
#endif
            (void)close((int)pidfd);
        } else {
            pidfd_open_errno = errno;
        }
#else
        pidfd_open_errno = ENOSYS;
#endif
    }

    char report[1024];
    int report_size = snprintf(
        report, sizeof(report),
        "{\"role\":\"%s\",\"pid_namespace_dev\":%llu,\"pid_namespace_ino\":%llu,"
        "\"fd3_is_socket\":true,\"proc_fd3_visible\":%s,\"proc_fd3_errno\":%d,"
        "\"pidfd_open_succeeded\":%s,\"pidfd_open_errno\":%d,"
        "\"pidfd_getfd_attempted\":%s,\"pidfd_getfd_succeeded\":%s,"
        "\"pidfd_getfd_errno\":%d}\n",
        request_id,
        (unsigned long long)namespace_stat.st_dev,
        (unsigned long long)namespace_stat.st_ino,
        proc_fd3_visible ? "true" : "false", proc_fd3_errno,
        pidfd_open_succeeded ? "true" : "false", pidfd_open_errno,
        pidfd_getfd_attempted ? "true" : "false",
        pidfd_getfd_succeeded ? "true" : "false", pidfd_getfd_errno);
    if (report_size <= 0 || (size_t)report_size >= sizeof(report) ||
        write_all(STDOUT_FILENO, report, (size_t)report_size) != 0 ||
        write_all(3, request, strlen(request)) != 0) return 16;

    char response[8192];
    size_t response_size = 0U;
    while (response_size < sizeof(response)) {
        ssize_t count = read(3, response + response_size, 1U);
        if (count < 0 && errno == EINTR) continue;
        if (count != 1) return 17;
        if (response[response_size++] == '\n') break;
    }
    if (response_size == 0U || response[response_size - 1U] != '\n' ||
        write_all(STDOUT_FILENO, response, response_size) != 0) return 18;
    return 0;
}
""",
        encoding="utf-8",
    )
    executable = tmp_path / "cao-work-sibling-probe"
    built = subprocess.run(
        [
            compiler,
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(source),
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert built.returncode == 0, built.stderr
    return executable.read_bytes()


def _provision_second_docker_selection(repository, principal, first_binding, selector):
    contract_id = f"t019-{selector}"
    request_hash = hashlib.sha256(selector.encode("utf-8")).hexdigest()
    policy = KnowledgePolicy(
        repository,
        first_binding.job_id,
        first_binding.grant_id,
        first_binding.grant_revision,
    )
    with repository.read_snapshot() as connection:
        project_id = connection.execute(
            "SELECT project_id FROM work_jobs WHERE id=?", (first_binding.job_id,)
        ).fetchone()[0]
    snapshot = DelegationSnapshots(repository, policy=policy).freeze(
        principal=principal,
        job_id=first_binding.job_id,
        contract_id=contract_id,
        binding_key=selector,
        request_hash=request_hash,
        scope="project",
        scope_id=project_id,
        resolver=lambda _connection, _actor: ResolvedSnapshot("frozen task context"),
    )
    contract_data = first_binding.contract.model_dump()
    contract_data["id"] = contract_id
    resources = dict(contract_data["resources"])
    resources["write_paths"] = ()
    contract_data["resources"] = resources
    contract_data["snapshot"] = ContractSnapshot(
        state="present",
        id=snapshot.id,
        delivered_hash=snapshot.delivered_hash,
    ).model_dump()
    contract = EffectiveWorkContractV2.model_validate(contract_data)
    WorkProvisioning(repository).provision_launch(
        principal,
        subject=principal,
        selector=selector,
        expected_revision=0,
        job_id=first_binding.job_id,
        grant_id=first_binding.grant_id,
        grant_revision=first_binding.grant_revision,
        contract=contract,
        adapter_version=2,
        lease_seconds=300,
    )


def _docker_sibling_worker_pid(backend, attempt_id, generation):
    name = backend._container_name(attempt_id, generation)
    inspected = backend._run(["container", "inspect", "--format", "{{json .}}", name], check=False)
    assert inspected.returncode == 0, "Docker container was not inspectable while blocked"
    container = json.loads(inspected.stdout)
    state = container.get("State", {})
    host = container.get("HostConfig", {})
    assert state.get("Running") is True
    assert host.get("PidMode") in {"", "private"}
    assert host.get("PidsLimit") == 2
    assert host.get("NetworkMode") == "none"
    assert host.get("ReadonlyRootfs") is True
    assert host.get("Privileged") is False
    assert not host.get("Binds")
    assert container.get("Mounts") == []
    supervisor_pid = state.get("Pid")
    assert type(supervisor_pid) is int and supervisor_pid > 2

    top = backend._run(["container", "top", name, "-eo", "pid,ppid"], check=False)
    assert top.returncode == 0, "Docker did not expose the blocked worker process table"
    rows = []
    for line in top.stdout.splitlines()[1:]:
        fields = line.split()
        if len(fields) == 2 and all(field.isdigit() for field in fields):
            rows.append((int(fields[0]), int(fields[1])))
    children = [pid for pid, ppid in rows if ppid == supervisor_pid]
    assert len(children) == 1, "the blocked Docker supervisor must have one worker child"
    worker_pid = children[0]
    assert worker_pid > 2
    return worker_pid


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
    return (
        child,
        receiver,
        child_subject,
        child_authorization,
        receiver_subject,
        receiver_authorization,
    )


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
        contract_tools=("cao.work.child",),
        request_tools=("cao.work.child",),
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
    with repository.read_snapshot() as connection:
        issue_states = connection.execute(
            "SELECT state FROM work_mcp_proxy_issue_events "
            "WHERE attempt_id=? AND generation=? ORDER BY sequence",
            (receipt.attempt_id, receipt.generation),
        ).fetchall()
        assert [row[0] for row in issue_states] == ["issued", "abandoned"]
        assert (
            connection.execute(
                "SELECT count(*) FROM work_mcp_proxy_effects WHERE attempt_id=? AND generation=?",
                (receipt.attempt_id, receipt.generation),
            ).fetchone()[0]
            == 0
        )
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
        call[:3] == ("image", "build", "--platform=linux/amd64") and call[-2:] == (image_tag, "-")
        for call in docker_calls
    )
    assert not any(call[:2] == ("image", "import") for call in docker_calls)
    assert any(
        call[:3] == ("container", "inspect", "--format") and "{{json .}}" in call
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
        backend.execute_bound_process = (
            lambda *args, **kwargs: outputs.append(execute(*args, **kwargs)) or outputs[-1]
        )
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
        assert (
            connection.execute(
                "SELECT count(*) FROM work_mcp_proxy_effects WHERE attempt_id=?",
                (receipt.attempt_id,),
            ).fetchone()[0]
            == 1
        )
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
    worker_binary = _minimal_static_mcp_worker_from(tmp_path, "work_docker_child_worker.c")

    def backend_factory(_marker, repository):
        backend = DockerWorkBackend(
            image_ref=image_id,
            docker_command=docker_cli,
            repository=repository,
        )
        backend_holder.append(backend)
        execute = backend.execute_bound_process
        backend.execute_bound_process = (
            lambda *args, **kwargs: outputs.append(execute(*args, **kwargs)) or outputs[-1]
        )
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
    (
        child,
        receiver,
        child_subject,
        child_authorization,
        receiver_subject,
        receiver_authorization,
    ) = _preprovision_lineage(
        gateway._launch_runtime_provider._runtime,
        repository,
        owner,
        tmp_path,
        tools={"cao.work.child"},
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
    assert (
        backend._run(
            [
                "container",
                "inspect",
                backend._container_name(receipt.attempt_id, receipt.generation),
            ],
            check=False,
        ).returncode
        != 0
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_docker_mcp_commits_exact_receiver_acceptance_before_receipt(tmp_path):
    image_id = os.environ.get("CAO_T019_DOCKER_IMAGE_ID")
    docker_cli = os.environ.get("CAO_T019_DOCKER_CLI", "docker")
    if not image_id:
        pytest.skip("run test/integration/t019/run-docker-backend-acceptance.sh for real Docker")

    worker_binary = _minimal_static_mcp_worker_from(tmp_path, "work_docker_receiver_worker.c")

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
    (
        child,
        receiver,
        child_subject,
        child_authorization,
        receiver_subject,
        receiver_authorization,
    ) = _preprovision_lineage(
        runtime,
        repository,
        owner,
        tmp_path,
        tools={"cao.work.task_received"},
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
        assert (
            connection.execute(
                "SELECT count(*) FROM work_mcp_proxy_effects WHERE attempt_id=?",
                (child_work["attempts"][0]["id"],),
            ).fetchone()[0]
            == 1
        )
    container_name = docker_backend._container_name(
        child_work["attempts"][0]["id"], child_work["attempts"][0]["generation"]
    )
    image_tag = docker_backend._attempt_image_tag(
        child_work["attempts"][0]["id"], child_work["attempts"][0]["generation"]
    )
    assert (
        docker_backend._run(["container", "inspect", container_name], check=False).returncode != 0
    )
    assert docker_backend._run(["image", "inspect", image_tag], check=False).returncode != 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_docker_sibling_cannot_observe_or_duplicate_victim_mcp_fd(tmp_path):
    image_id = os.environ.get("CAO_T019_DOCKER_IMAGE_ID")
    docker_cli = os.environ.get("CAO_T019_DOCKER_CLI", "docker")
    if not image_id:
        pytest.skip("run test/integration/t019/run-docker-backend-acceptance.sh for real Docker")

    worker_binary = _minimal_static_sibling_probe_worker(tmp_path)
    victim_mcp_entered = threading.Event()
    both_mcp_entered = threading.Event()
    release_upstreams = threading.Event()
    state_lock = threading.Lock()
    expected_secrets = {}
    secret_matches = {}
    upstream_attempts = {}
    executions = {}
    dispatch_tasks = []

    def make_proxy(repository, attempt_id, generation):
        assert generation == 1
        # This synthetic per-attempt canary is server-side test data only.
        secret = b"t019-test-proxy-canary:" + attempt_id.encode("ascii")
        with state_lock:
            expected_secrets[attempt_id] = secret

        def upstream(request, supplied_secret):
            request_id = request["id"]
            with state_lock:
                secret_matches[attempt_id] = hmac.compare_digest(supplied_secret, secret)
                upstream_attempts[request_id] = attempt_id
                if request_id == "victim":
                    victim_mcp_entered.set()
                elif request_id != "attacker":
                    raise RuntimeError("unexpected test worker request id")
                if len(upstream_attempts) == 2:
                    both_mcp_entered.set()
            if not release_upstreams.wait(timeout=60):
                raise TimeoutError("test did not release blocked sibling requests")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"attempt_id": attempt_id},
            }

        return WorkMcpProxy(
            repository,
            server_secret_factory=lambda: secret,
            upstream=upstream,
        )

    backend_holder = []

    def backend_factory(_marker, repository):
        backend = DockerWorkBackend(
            image_ref=image_id,
            docker_command=docker_cli,
            repository=repository,
            timeout_seconds=120,
            mcp_proxy_factory=lambda attempt_id, generation: make_proxy(
                repository, attempt_id, generation
            ),
        )
        execute = backend.execute_bound_process

        def capture_execution(*args, **kwargs):
            execution = execute(*args, **kwargs)
            with state_lock:
                executions[kwargs["binding"].attempt_id] = execution
            return execution

        backend.execute_bound_process = capture_execution
        backend_holder.append(backend)
        return backend

    repository, principal, gateway, _backend, _marker, first_request = _setup(
        tmp_path,
        backend_factory=backend_factory,
        worker_binary=worker_binary,
        contract_tools=("cao.work.child",),
        request_tools=("cao.work.child",),
        capacity=2,
        contract_paths=(str(tmp_path),),
        contract_write_paths=(),
    )
    backend = backend_holder[0]

    first_receipt = gateway.admit(principal, replace(first_request, message="victim"))
    dispatch_tasks.append(asyncio.create_task(gateway.dispatch_registered_next()))
    victim_started = False
    early_dispatch_result = None
    try:
        for _ in range(1200):
            if victim_mcp_entered.is_set():
                victim_started = True
                break
            if dispatch_tasks[0].done():
                early_dispatch_result = dispatch_tasks[0].result()
                break
            await asyncio.sleep(0.05)
        early_execution = executions.get(first_receipt.attempt_id)
        assert victim_started, (
            "victim did not reach its live managed MCP endpoint; "
            f"dispatch={early_dispatch_result!r}; "
            f"worker_returncode={getattr(early_execution, 'returncode', None)!r}; "
            f"worker_stdout={getattr(early_execution, 'stdout', None)!r}; "
            f"worker_stderr={getattr(early_execution, 'stderr', None)!r}"
        )

        first_binding = WorkContracts(repository).revalidate_order(
            first_receipt.attempt_id, generation=first_receipt.generation
        )
        victim_pid = _docker_sibling_worker_pid(
            backend, first_receipt.attempt_id, first_receipt.generation
        )
        assert victim_pid > 2

        second_selector = "t019-sibling-probe"
        _provision_second_docker_selection(repository, principal, first_binding, second_selector)
        second_receipt = gateway.admit(
            principal,
            DurableLaunchRequest(
                selection=second_selector,
                agent_profile="developer",
                session_name="unused-by-process-launch",
                message=f"attacker:{victim_pid}",
                allowed_tools=("cao.work.child",),
            ),
        )
        dispatch_tasks.append(asyncio.create_task(gateway.dispatch_registered_next()))

        both_started = False
        for _ in range(1200):
            if both_mcp_entered.is_set():
                both_started = True
                break
            if dispatch_tasks[-1].done():
                break
            await asyncio.sleep(0.05)
        assert both_started, "both workers did not reach their bound managed MCP endpoints"

        # Both calls remain blocked here, so inspect actual concurrent runtime state.
        victim_worker_pid = _docker_sibling_worker_pid(
            backend, first_receipt.attempt_id, first_receipt.generation
        )
        attacker_worker_pid = _docker_sibling_worker_pid(
            backend, second_receipt.attempt_id, second_receipt.generation
        )
        assert victim_worker_pid == victim_pid
        assert attacker_worker_pid > 2

        def inspect_attempt(receipt):
            name = backend._container_name(receipt.attempt_id, receipt.generation)
            result = backend._run(
                ["container", "inspect", "--format", "{{json .}}", name], check=False
            )
            assert (
                result.returncode == 0
            ), "concurrent Docker container disappeared before probe completion"
            return json.loads(result.stdout)

        victim_container = inspect_attempt(first_receipt)
        attacker_container = inspect_attempt(second_receipt)
        assert victim_container["Id"] != attacker_container["Id"]
        assert victim_container["Image"] != attacker_container["Image"]
        assert victim_container["HostConfig"].get("PidMode") in {"", "private"}
        assert attacker_container["HostConfig"].get("PidMode") in {"", "private"}

        release_upstreams.set()
        results = await asyncio.gather(*dispatch_tasks)
    finally:
        release_upstreams.set()
        if dispatch_tasks:
            await asyncio.gather(*dispatch_tasks, return_exceptions=True)

    assert len(results) == 2
    assert {result["id"] for result in results} == {
        first_receipt.work_item_id,
        second_receipt.work_item_id,
    }
    assert set(expected_secrets) == {first_receipt.attempt_id, second_receipt.attempt_id}
    assert secret_matches == {
        first_receipt.attempt_id: True,
        second_receipt.attempt_id: True,
    }
    assert upstream_attempts == {
        "victim": first_receipt.attempt_id,
        "attacker": second_receipt.attempt_id,
    }
    assert set(executions) == set(expected_secrets)

    reports = {}
    for attempt_id, execution in executions.items():
        assert execution.returncode == 0
        assert execution.stderr == b""
        lines = execution.stdout.splitlines()
        assert len(lines) == 2
        report = json.loads(lines[0])
        response = json.loads(lines[1])
        assert report["fd3_is_socket"] is True
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == report["role"]
        assert response["result"] == {"attempt_id": attempt_id}
        reports[report["role"]] = report
        assert not any(secret in execution.stdout for secret in expected_secrets.values())
    assert set(reports) == {"victim", "attacker"}
    victim_namespace = (
        reports["victim"]["pid_namespace_dev"],
        reports["victim"]["pid_namespace_ino"],
    )
    attacker_namespace = (
        reports["attacker"]["pid_namespace_dev"],
        reports["attacker"]["pid_namespace_ino"],
    )
    assert victim_namespace != attacker_namespace

    attacker_probe = reports["attacker"]
    assert attacker_probe["proc_fd3_visible"] is False
    assert attacker_probe["proc_fd3_errno"] == errno.ENOENT
    assert attacker_probe["pidfd_open_succeeded"] is False
    assert attacker_probe["pidfd_open_errno"] == errno.ESRCH
    assert attacker_probe["pidfd_getfd_attempted"] is False
    assert attacker_probe["pidfd_getfd_succeeded"] is False
    with repository.read_snapshot() as connection:
        effect_rows = connection.execute(
            "SELECT effect.attempt_id,event.state FROM work_mcp_proxy_effects AS effect "
            "JOIN work_mcp_proxy_effect_events AS event USING(effect_id) "
            "WHERE event.sequence=(SELECT max(latest.sequence) "
            "FROM work_mcp_proxy_effect_events AS latest WHERE latest.effect_id=effect.effect_id) "
            "ORDER BY effect.attempt_id"
        ).fetchall()
    assert [(row[0], row[1]) for row in effect_rows] == sorted(
        (attempt_id, "completed") for attempt_id in expected_secrets
    )
    for receipt in (first_receipt, second_receipt):
        name = backend._container_name(receipt.attempt_id, receipt.generation)
        image_tag = backend._attempt_image_tag(receipt.attempt_id, receipt.generation)
        assert backend._run(["container", "inspect", name], check=False).returncode != 0
        assert backend._run(["image", "inspect", image_tag], check=False).returncode != 0


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("delay_exit_reader", [False, True])
async def test_docker_fresh_repository_reconciles_stopped_artifacts_without_redelivery(
    tmp_path, monkeypatch, delay_exit_reader
):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.services.work_launch_gateway import build_durable_launch_gateway
    from cli_agent_orchestrator.services.work_service import DeliveryUncertain, WorkService

    image_id = os.environ.get("CAO_T019_DOCKER_IMAGE_ID")
    docker_cli = os.environ.get("CAO_T019_DOCKER_CLI", "docker")
    if not image_id:
        pytest.skip("run test/integration/t019/run-docker-backend-acceptance.sh for real Docker")

    delayed_exits = []
    if delay_exit_reader:
        original_popen = subprocess.Popen

        class DelayedExitReader:
            def __init__(self, pipe, process):
                self.pipe = pipe
                self.process = process

            def readline(self, *args):
                header = self.pipe.readline(*args)
                if header.startswith(b"CAO-EXIT/1 "):
                    # The attach process can exit while its valid frame is still
                    # buffered and the protocol reader has not consumed it.
                    self.process.wait(timeout=5)
                    delayed_exits.append(header)
                    time.sleep(0.15)
                return header

            def __getattr__(self, name):
                return getattr(self.pipe, name)

        def delayed_popen(arguments, *args, **kwargs):
            process = original_popen(arguments, *args, **kwargs)
            if "--attach" in arguments:
                process.stdout = DelayedExitReader(process.stdout, process)
            return process

        monkeypatch.setattr(subprocess, "Popen", delayed_popen)

    cleanup_container_calls = []
    cleanup_image_calls = []
    execution_attempts = []

    def backend_factory(_marker, repository):
        backend = DockerWorkBackend(
            image_ref=image_id,
            docker_command=docker_cli,
            repository=repository,
        )

        def leave_container(container_id, *, kill):
            cleanup_container_calls.append((container_id, kill))
            return False

        def leave_image(image_tag):
            cleanup_image_calls.append(image_tag)
            return False

        backend._cleanup_container = leave_container
        backend._cleanup_attempt_image = leave_image
        execute = backend.execute_bound_process

        def record_execution(*args, **kwargs):
            execution_attempts.append(kwargs["binding"].attempt_id)
            return execute(*args, **kwargs)

        backend.execute_bound_process = record_execution
        return backend

    repository, principal, gateway, backend, _marker, request = _setup(
        tmp_path,
        backend_factory=backend_factory,
        worker_binary=_minimal_static_worker(),
        contract_paths=(str(tmp_path),),
        contract_write_paths=(),
    )
    receipt = gateway.admit(principal, request)
    fresh_repository = WorkRepository(repository.path)
    fresh_backend = DockerWorkBackend(
        image_ref=image_id,
        docker_command=docker_cli,
        repository=fresh_repository,
    )
    fresh_reconcile = fresh_backend.reconcile_attempt
    reconcile_calls = []

    def record_reconciliation(attempt_id, generation):
        result = fresh_reconcile(attempt_id, generation)
        reconcile_calls.append((attempt_id, generation, result))
        return result

    fresh_backend.reconcile_attempt = record_reconciliation
    try:
        with pytest.raises(DeliveryUncertain):
            await gateway.dispatch_registered_next()

        uncertain = repository.get_work(receipt.work_item_id)
        attempt = uncertain["attempts"][-1]
        assert uncertain["state"] == "reconcile"
        assert attempt["state"] == "reconcile"
        assert attempt["cleanup_state"] == "not_requested"
        assert len(uncertain["attempts"]) == 1
        assert execution_attempts == [receipt.attempt_id]

        container_name = backend._container_name(receipt.attempt_id, receipt.generation)
        image_tag = backend._attempt_image_tag(receipt.attempt_id, receipt.generation)
        attempt_hash = hashlib.sha256(receipt.attempt_id.encode("utf-8")).hexdigest()
        container_inspect = backend._run(
            ["container", "inspect", "--format", "{{json .}}", container_name],
            check=False,
        )
        image_inspect = backend._run(
            ["image", "inspect", "--format", "{{json .}}", image_tag], check=False
        )
        assert container_inspect.returncode == 0
        assert image_inspect.returncode == 0
        container = json.loads(container_inspect.stdout)
        image = json.loads(image_inspect.stdout)
        container_labels = container["Config"]["Labels"]
        image_labels = image["Config"]["Labels"]
        assert container["State"]["Running"] is False
        assert container_labels["cao.work.backend"] == "docker"
        assert container_labels["cao.work.attempt_sha256"] == attempt_hash
        assert container_labels["cao.work.generation"] == str(receipt.generation)
        assert image_labels["org.cao.work.backend"] == "docker"
        assert image_labels["org.cao.work.attempt_sha256"] == attempt_hash
        assert image_labels["org.cao.work.generation"] == str(receipt.generation)
        assert cleanup_container_calls == [(container["Id"], False), (container["Id"], True)]
        if delay_exit_reader:
            assert len(delayed_exits) == 1
        assert cleanup_image_calls == [image_tag]

        recovered = WorkService(fresh_repository).recover_process_cleanup(
            receipt.attempt_id,
            backend_reconciler=fresh_backend,
            actor_id=principal.id,
        )

        assert reconcile_calls == [
            (
                receipt.attempt_id,
                receipt.generation,
                {"container_removed": True, "image_removed": True},
            )
        ]
        assert recovered["state"] == "reconcile"
        assert recovered["attempts"][-1]["state"] == "reconcile"
        assert recovered["attempts"][-1]["cleanup_state"] == "complete"
        for arguments in (
            ["container", "inspect", container_name],
            ["image", "inspect", image_tag],
        ):
            result = fresh_backend._run(arguments, check=False)
            assert fresh_backend._inspect_reports_present(result) is False

        def reject_redelivery(*_args, **_kwargs):
            execution_attempts.append("unexpected-redelivery")
            raise AssertionError("reconciled Docker work must not be executed again")

        fresh_backend.execute_bound_process = reject_redelivery
        restarted_gateway = build_durable_launch_gateway(
            fresh_repository, backends={"test": fresh_backend}
        )
        assert await restarted_gateway.dispatch_registered_next() is None
        final = fresh_repository.get_work(receipt.work_item_id)
        assert final["state"] == "reconcile"
        assert len(final["attempts"]) == 1
        assert execution_attempts == [receipt.attempt_id]
    finally:
        # A failed assertion must not leave real Docker artifacts behind.
        fresh_reconcile(receipt.attempt_id, receipt.generation)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_docker_owner_process_death_reconciles_artifacts_without_redelivery(
    tmp_path, monkeypatch
):
    from cli_agent_orchestrator.clients import work_repository as work_repository_module
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.services.work_launch_gateway import build_durable_launch_gateway
    from cli_agent_orchestrator.services.work_service import WorkService

    image_id = os.environ.get("CAO_T019_DOCKER_IMAGE_ID")
    docker_cli = os.environ.get("CAO_T019_DOCKER_CLI", "docker")
    if not image_id:
        pytest.skip("run test/integration/t019/run-docker-backend-acceptance.sh for real Docker")

    worker = _minimal_static_waiting_worker(tmp_path)

    def backend_factory(_marker, repository):
        return DockerWorkBackend(
            image_ref=image_id,
            docker_command=docker_cli,
            repository=repository,
            timeout_seconds=120,
        )

    repository, principal, gateway, backend, _marker, request = _setup(
        tmp_path,
        backend_factory=backend_factory,
        worker_binary=worker,
        contract_tools=("cao.work.child",),
        request_tools=("cao.work.child",),
        contract_paths=(str(tmp_path),),
        contract_write_paths=(),
        lease_seconds=30,
    )
    receipt = gateway.admit(principal, request)
    container_name = backend._container_name(receipt.attempt_id, receipt.generation)
    owner_program = """
import asyncio
import sys
from cli_agent_orchestrator.backends.docker_backend import DockerWorkBackend
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services.work_launch_gateway import build_durable_launch_gateway

repository = WorkRepository(sys.argv[1])
repository.initialize()
backend = DockerWorkBackend(
    image_ref=sys.argv[2], docker_command=sys.argv[3], repository=repository
)
gateway = build_durable_launch_gateway(repository, backends={"test": backend})
asyncio.run(gateway.dispatch_registered_next())
"""
    child_environment = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[3] / "src")
    child_environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (source_root, child_environment.get("PYTHONPATH", "")) if value
    )
    owner = None
    fresh_repository = WorkRepository(repository.path)
    cleanup_backend = DockerWorkBackend(
        image_ref=image_id,
        docker_command=docker_cli,
        repository=fresh_repository,
    )
    try:
        owner = subprocess.Popen(
            [sys.executable, "-c", owner_program, str(repository.path), image_id, docker_cli],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_environment,
            start_new_session=True,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if owner.poll() is not None:
                pytest.fail(
                    f"Docker owner exited before its container became live: {owner.returncode}"
                )
            running = backend._run(
                ["container", "inspect", "--format", "{{.State.Running}}", container_name],
                check=False,
            )
            if running.returncode == 0 and running.stdout.strip() == "true":
                with fresh_repository.read_snapshot() as connection:
                    issue_states = connection.execute(
                        "SELECT state FROM work_mcp_proxy_issue_events "
                        "WHERE attempt_id=? AND generation=? ORDER BY sequence",
                        (receipt.attempt_id, receipt.generation),
                    ).fetchall()
                if issue_states and issue_states[-1][0] == "issued":
                    break
            backend._inspect_reports_present(running)
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Docker owner did not start a worker with an open MCP issue")

        owner.kill()
        assert owner.wait(timeout=10) == -signal.SIGKILL
        artifact_after_owner_death = backend._run(
            ["container", "inspect", "--format", "{{json .}}", container_name],
            check=False,
        )
        assert artifact_after_owner_death.returncode == 0
        container = json.loads(artifact_after_owner_death.stdout)
        assert container["State"]["Running"] in (True, False)
        assert (
            container["Config"]["Labels"]["cao.work.attempt_sha256"]
            == hashlib.sha256(receipt.attempt_id.encode("utf-8")).hexdigest()
        )
        assert container["Config"]["Labels"]["cao.work.generation"] == str(receipt.generation)

        attempt = fresh_repository.get_attempt(receipt.attempt_id)
        with monkeypatch.context() as clock:
            clock.setattr(
                work_repository_module.time,
                "time",
                lambda: attempt["lease_expires_at"] + 1,
            )
            expired = fresh_repository.reconcile_expired(actor_id=principal.id)
        assert expired == [receipt.work_item_id]

        recovered = WorkService(fresh_repository).recover_process_cleanup(
            receipt.attempt_id,
            backend_reconciler=cleanup_backend,
            actor_id=principal.id,
        )
        assert recovered["state"] == "reconcile"
        assert recovered["attempts"][-1]["cleanup_state"] == "complete"
        with fresh_repository.read_snapshot() as connection:
            issue_states = connection.execute(
                "SELECT state FROM work_mcp_proxy_issue_events "
                "WHERE attempt_id=? AND generation=? ORDER BY sequence",
                (receipt.attempt_id, receipt.generation),
            ).fetchall()
            effect_count = connection.execute(
                "SELECT count(*) FROM work_mcp_proxy_effects "
                "WHERE attempt_id=? AND generation=?",
                (receipt.attempt_id, receipt.generation),
            ).fetchone()[0]
        assert [row[0] for row in issue_states] == ["issued", "abandoned"]
        assert effect_count == 0
        assert (
            cleanup_backend._inspect_reports_present(
                cleanup_backend._run(["container", "inspect", container_name], check=False)
            )
            is False
        )
        image_tag = backend._attempt_image_tag(receipt.attempt_id, receipt.generation)
        assert (
            cleanup_backend._inspect_reports_present(
                cleanup_backend._run(["image", "inspect", image_tag], check=False)
            )
            is False
        )

        def reject_redelivery(*_args, **_kwargs):
            raise AssertionError("recovered Docker work must not be dispatched again")

        cleanup_backend.execute_bound_process = reject_redelivery
        restarted_gateway = build_durable_launch_gateway(
            fresh_repository, backends={"test": cleanup_backend}
        )
        assert await restarted_gateway.dispatch_registered_next() is None
        final = fresh_repository.get_work(receipt.work_item_id)
        assert final["state"] == "reconcile"
        assert len(final["attempts"]) == 1
    finally:
        if owner is not None and owner.poll() is None:
            owner.kill()
            owner.wait(timeout=10)
        cleanup_backend.reconcile_attempt(receipt.attempt_id, receipt.generation)
