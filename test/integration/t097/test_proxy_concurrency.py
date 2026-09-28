"""Real Work launches keep concurrent proxy sockets and secrets attempt-bound."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from cli_agent_orchestrator.models.work_contract import (
    ContractSnapshot,
    EffectiveWorkContractV2,
)
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services.work_launch_gateway import (
    DurableLaunchRequest,
    build_durable_launch_gateway,
)
from cli_agent_orchestrator.services.work_mcp_proxy import WorkMcpProxy
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning
from cli_agent_orchestrator.services.work_contract import WorkContracts
from cli_agent_orchestrator.services.work_service import DeliveryUncertain, WorkService
from cli_agent_orchestrator.services.work_bubblewrap_setup_intent import WorkBubblewrapSetupIntent
from test.integration.t097.test_host_acceptance import (
    _host_backend_factory,
    accepted_linux_host,
)
from test.integration.t098.test_work_launch_dispatch import _setup

pytestmark = [pytest.mark.integration, pytest.mark.t097_host]

_PROXY_FD_SIBLING_PROBE = r"""
import ctypes, errno, json, os, sys
pid = int(sys.argv[1])
target_user_ns_inode = int(sys.argv[2])
def attempt(operation):
    try:
        operation()
        return {"allowed": True}
    except OSError as error:
        return {"allowed": False, "errno": error.errno}
def duplicate_proxy_fd():
    pidfd = os.pidfd_open(pid, 0)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        ctypes.set_errno(0)
        duplicate = libc.syscall(438, pidfd, 3, 0)
        error = ctypes.get_errno()
        if duplicate < 0:
            raise OSError(error, os.strerror(error))
        os.close(duplicate)
    finally:
        os.close(pidfd)
results = {
    "proc_fd3_readlink": attempt(lambda: os.readlink(f"/proc/{pid}/fd/3")),
    "pidfd_getfd3": attempt(duplicate_proxy_fd),
    "actor_uid": os.getuid(),
    "actor_user_ns_inode": os.stat("/proc/self/ns/user").st_ino,
    "target_user_ns_inode": target_user_ns_inode,
    "ptrace_scope": open("/proc/sys/kernel/yama/ptrace_scope").read().strip(),
}
print(json.dumps(results, sort_keys=True))
"""


def _proxy_worker(tmp_path: Path) -> bytes:
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("static C compiler is unavailable")
    source = Path(__file__).resolve().parents[2] / "fixtures" / "t097_mcp_proxy_worker.c"
    executable = tmp_path / "t097-mcp-proxy-worker"
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
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert built.returncode == 0, built.stderr
    return executable.read_bytes()


def _use_test_worker_timeout(monkeypatch) -> None:
    from cli_agent_orchestrator.services import work_bubblewrap_composition

    worker_timeout = float(os.environ.get("T097_TEST_WORKER_TIMEOUT_SECONDS", "10"))
    assert 0 < worker_timeout <= 300
    original_launcher = work_bubblewrap_composition.launch_recorded_bound_work_static_elf

    def launch_with_acceptance_timeout(*args, **kwargs):
        return original_launcher(*args, timeout_seconds=worker_timeout, **kwargs)

    monkeypatch.setattr(
        work_bubblewrap_composition,
        "launch_recorded_bound_work_static_elf",
        launch_with_acceptance_timeout,
    )


def _provision_second_selection(
    repository, principal, first_binding, selector, checkout_root: Path
):
    contract_id = "t097-concurrent-contract"
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
    resources["checkout_root"] = str(checkout_root)
    resources["write_paths"] = (str(checkout_root / "worker-output"),)
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


@pytest.mark.asyncio
async def test_two_concurrent_work_attempts_use_distinct_live_proxy_endpoints(
    tmp_path, monkeypatch, accepted_linux_host
):
    worker = _proxy_worker(tmp_path)
    _use_test_worker_timeout(monkeypatch)
    rendezvous = threading.Barrier(2)
    factory_calls = {}
    upstream_calls = []
    upstream_started = []
    proxy_serves = []
    proxy_errors = []
    factory_lock = threading.Lock()
    supervisors = []
    executions = []
    execution_records = []

    def make_proxy(repository, attempt_id, generation):
        assert generation == 1
        secret = f"proxy-secret:{attempt_id}".encode("utf-8")
        with factory_lock:
            assert attempt_id not in factory_calls
            factory_calls[attempt_id] = secret

        def upstream(request, supplied_secret):
            assert supplied_secret == secret
            upstream_started.append(attempt_id)
            rendezvous.wait(timeout=9)
            upstream_calls.append(attempt_id)
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {"attempt_id": attempt_id},
            }

        return WorkMcpProxy(
            repository,
            server_secret_factory=lambda: secret,
            upstream=upstream,
        )

    repository, principal, gateway, _backend, _marker, first_request = _setup(
        tmp_path,
        backend_factory=lambda _marker, _repository: _host_backend_factory(
            supervisors,
            executions,
            mcp_proxy_factory=make_proxy,
            execution_records=execution_records,
        )(_marker, _repository),
        worker_binary=worker,
        contract_tools=("test.echo",),
        request_tools=("test.echo",),
        capacity=2,
    )
    first_receipt = gateway.admit(principal, first_request)
    first_binding = WorkContracts(repository).revalidate_order(
        first_receipt.attempt_id, generation=first_receipt.generation
    )
    second_selector = "t097-concurrent-selection"
    second_checkout_root = tmp_path / "second-checkout"
    second_checkout_root.mkdir()
    _provision_second_selection(
        repository, principal, first_binding, second_selector, second_checkout_root
    )
    second_receipt = gateway.admit(
        principal,
        DurableLaunchRequest(
            selection=second_selector,
            agent_profile="developer",
            session_name="unused-by-process-launch",
            message="second concurrent payload",
            allowed_tools=("test.echo",),
        ),
    )

    original_serve = WorkMcpProxy.serve

    def observe_proxy_serve(proxy, attempt_id, generation):
        proxy_serves.append(attempt_id)
        try:
            return original_serve(proxy, attempt_id, generation)
        except BaseException as error:
            proxy_errors.append((attempt_id, type(error).__name__, str(error)))
            raise

    monkeypatch.setattr(WorkMcpProxy, "serve", observe_proxy_serve)

    original_release = WorkBubblewrapSetupIntent.release_if_current
    sibling_probes = []
    go_rendezvous = threading.Barrier(2)

    def probe_other_user_namespace(
        setup, evidence, *, expected_attempt_revision, release, authorize=None
    ):
        identity = repository.read_bubblewrap_process_identity(
            evidence.attempt_id, evidence.generation
        )
        assert identity is not None
        probe = subprocess.run(
            [
                "/usr/bin/bwrap",
                "--unshare-user",
                "--uid",
                "0",
                "--gid",
                "0",
                "--die-with-parent",
                "--ro-bind",
                "/",
                "/",
                "--tmpfs",
                "/tmp",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--",
                sys.executable,
                "-I",
                "-c",
                _PROXY_FD_SIBLING_PROBE,
                str(identity["init_pid"]),
                str(os.stat(f"/proc/{identity['init_pid']}/ns/user").st_ino),
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
            env={"LC_ALL": "C"},
        )
        assert probe.returncode == 0, probe.stderr
        sibling_probes.append(json.loads(probe.stdout))
        go_rendezvous.wait(timeout=18)
        return original_release(
            setup,
            evidence,
            expected_attempt_revision=expected_attempt_revision,
            release=release,
            authorize=authorize,
        )

    monkeypatch.setattr(WorkBubblewrapSetupIntent, "release_if_current", probe_other_user_namespace)

    results = await asyncio.gather(
        gateway.dispatch_registered_next(),
        gateway.dispatch_registered_next(),
        return_exceptions=True,
    )
    errors = [result for result in results if isinstance(result, BaseException)]
    error_chains = []
    for error in errors:
        chain = []
        current = error
        while current is not None:
            detail = (
                f"timed out after {current.timeout}s"
                if isinstance(current, subprocess.TimeoutExpired)
                else str(current)
            )
            chain.append(f"{type(current).__name__}: {detail}")
            for stream in ("stdout", "stderr"):
                value = getattr(current, stream, None)
                if value:
                    chain.append(f"{stream}={value[-500:]!r}")
            current = current.__cause__
        error_chains.append(tuple(chain))
    assert not errors, (
        f"dispatch failures={error_chains!r}; proxy factories={tuple(factory_calls)}; "
        f"upstream started={tuple(upstream_started)}; "
        f"upstream calls={tuple(upstream_calls)}; "
        f"proxy serves={tuple(proxy_serves)}; proxy errors={tuple(proxy_errors)}; "
        f"executions={len(execution_records)}"
    )
    first_result, second_result = results

    assert {first_result["id"], second_result["id"]} == {
        first_receipt.work_item_id,
        second_receipt.work_item_id,
    }
    assert set(factory_calls) == {first_receipt.attempt_id, second_receipt.attempt_id}
    assert set(upstream_started) == set(factory_calls)
    assert set(upstream_calls) == set(factory_calls)
    assert set(proxy_serves) == set(factory_calls)
    assert proxy_errors == []
    assert {attempt_id for attempt_id, _execution in execution_records} == set(factory_calls)
    assert len(supervisors) == len(executions) == len(execution_records) == 2
    assert len(sibling_probes) == 2
    for probe in sibling_probes:
        assert probe["proc_fd3_readlink"]["allowed"] is False
        assert probe["proc_fd3_readlink"]["errno"] in {1, 13}
        assert probe["pidfd_getfd3"]["allowed"] is False
        assert probe["pidfd_getfd3"]["errno"] in {1, 13}
        assert probe["actor_user_ns_inode"] != probe["target_user_ns_inode"]
        assert probe["ptrace_scope"] == "1"
    for attempt_id, execution in execution_records:
        expected = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"attempt_id": attempt_id},
        }
        assert json.loads(execution.stdout) == expected
        assert execution.stderr == b"worker-started\nproxy-request-sent\n"
        assert execution.returncode == 0
    with repository.read_snapshot() as connection:
        effects = connection.execute(
            "SELECT effect.attempt_id,event.state FROM work_mcp_proxy_effects AS effect "
            "JOIN work_mcp_proxy_effect_events AS event USING(effect_id) "
            "WHERE event.sequence=(SELECT max(latest.sequence) "
            "FROM work_mcp_proxy_effect_events AS latest WHERE latest.effect_id=effect.effect_id) "
            "ORDER BY effect.attempt_id"
        ).fetchall()
    assert [(row[0], row[1]) for row in effects] == sorted(
        (attempt_id, "completed") for attempt_id in factory_calls
    )
    assert all(supervisor.attempt.state.value == "terminated" for supervisor in supervisors)


@pytest.mark.asyncio
async def test_uncertain_proxy_effect_survives_restart_without_replay(
    tmp_path, monkeypatch, accepted_linux_host
):
    worker = _proxy_worker(tmp_path)
    _use_test_worker_timeout(monkeypatch)
    supervisors = []
    executions = []
    upstream_calls = []
    proxy_holder = []

    def create_proxy(repository, _attempt_id, _generation):
        def upstream(_request, _secret):
            upstream_calls.append("started")
            raise TimeoutError("simulated lost upstream response")

        proxy = WorkMcpProxy(
            repository,
            server_secret_factory=lambda: b"attempt-bound-test-secret",
            upstream=upstream,
        )
        proxy_holder.append(proxy)
        return proxy

    repository, principal, gateway, _backend, marker, request = _setup(
        tmp_path,
        backend_factory=lambda _marker, _repository: _host_backend_factory(
            supervisors,
            executions,
            mcp_proxy_factory=create_proxy,
        )(_marker, _repository),
        worker_binary=worker,
        contract_tools=("test.echo",),
        request_tools=("test.echo",),
    )
    receipt = gateway.admit(principal, request)

    with pytest.raises(DeliveryUncertain):
        await gateway.dispatch_registered_next()

    current = repository.get_work(receipt.work_item_id)
    assert current["state"] == "reconcile"
    assert current["attempts"][0]["state"] == "reconcile"
    assert len(upstream_calls) == 1
    assert len(supervisors) == 1
    assert supervisors[0].attempt.state.value == "terminated"
    restarted_repository = WorkRepository(repository.path)
    restarted_repository.initialize()
    restarted_proxy = WorkMcpProxy(restarted_repository)
    effects = restarted_proxy.unresolved_effects()
    assert len(effects) == 1
    assert effects[0]["attempt_id"] == receipt.attempt_id
    assert effects[0]["generation"] == receipt.generation
    assert effects[0]["state"] == "uncertain"
    assert WorkService(restarted_repository).recover_incomplete_proxy_effects() == 0
    restarted_backend = _host_backend_factory(
        [],
        [],
        mcp_proxy_factory=lambda *_args: pytest.fail("uncertain effects must not relaunch"),
    )(marker, restarted_repository)
    restarted_gateway = build_durable_launch_gateway(
        restarted_repository, backends={"test": restarted_backend}
    )
    assert await restarted_gateway.dispatch_registered_next() is None
    assert len(upstream_calls) == 1
    assert len(proxy_holder) == 1
