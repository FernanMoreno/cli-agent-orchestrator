"""No-skip acceptance path for the approved T097 Linux host."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import platform
import pwd
import random
import shutil
import subprocess
from pathlib import Path
from test.integration.t098.test_work_launch_dispatch import (
    _WORKER_MARKER,
    _adversarial_static_worker,
    _minimal_static_worker,
    _setup,
)

import pytest

from cli_agent_orchestrator.backends.bubblewrap_backend import (
    _BWRAP_SHA256_ALLOWLIST,
    BubblewrapWorkBackend,
)
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_bubblewrap_composition import (
    WorkBubblewrapExecutionUncertain,
)
from cli_agent_orchestrator.services.work_bubblewrap_setup_intent import WorkBubblewrapSetupIntent
from cli_agent_orchestrator.services.work_launch_gateway import build_durable_launch_gateway
from cli_agent_orchestrator.services.work_process_landlock import _query_abi_version
from cli_agent_orchestrator.services.work_process_supervisor import (
    WorkProcessState,
    WorkProcessSupervisor,
)
from cli_agent_orchestrator.services.work_service import DeliveryUncertain

pytestmark = [pytest.mark.integration, pytest.mark.t097_host]


def _require_dedicated_broker_identity():
    broker_name = os.environ.get("CAO_WORK_BROKER_ACCOUNT")
    assert broker_name, "CAO_WORK_BROKER_ACCOUNT must name the dedicated non-login Work account"
    try:
        broker = pwd.getpwnam(broker_name)
    except KeyError as error:
        raise AssertionError("the dedicated Work broker account is absent from the host") from error
    assert broker.pw_uid != 0, "the Work broker must not run as root"
    assert os.geteuid() == broker.pw_uid, "host acceptance must run as the dedicated Work account"
    assert Path(broker.pw_shell).name in {
        "nologin",
        "false",
    }, "the Work broker account must have a non-login shell"
    other_host_processes = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdecimal():
            continue
        try:
            status = Path(entry.path, "status").read_text()
        except FileNotFoundError:
            continue
        uid_line = next(line for line in status.splitlines() if line.startswith("Uid:"))
        if int(uid_line.split()[2]) == broker.pw_uid and int(entry.name) != os.getpid():
            other_host_processes.append(int(entry.name))
    assert not other_host_processes, (
        "the dedicated Work broker account has unrelated host processes: "
        f"{sorted(other_host_processes)}"
    )


@pytest.fixture(scope="module", autouse=True)
def accepted_linux_host():
    if os.environ.get("T097_REQUIRE_HOST_ACCEPTANCE") != "1":
        pytest.skip("set T097_REQUIRE_HOST_ACCEPTANCE=1 on the approved Linux runner")
    _require_dedicated_broker_identity()

    assert platform.system() == "Linux"
    bwrap = Path("/usr/bin/bwrap")
    assert bwrap.is_file() and os.access(bwrap, os.X_OK)
    version = subprocess.run(
        [str(bwrap), "--version"], check=True, capture_output=True, text=True, timeout=5
    )
    assert version.stdout.strip() == "bubblewrap 0.13.0"
    digest = hashlib.sha256(bwrap.read_bytes()).hexdigest()
    assert digest in _BWRAP_SHA256_ALLOWLIST, f"unapproved Bubblewrap SHA-256: {digest}"
    assert _query_abi_version() >= 9
    assert Path("/proc/sys/kernel/yama/ptrace_scope").read_text().strip() == "1"
    apparmor_policy = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
    assert not apparmor_policy.exists() or apparmor_policy.read_text().strip() == "0"
    assert shutil.which("cc") and shutil.which("readelf")
    assert Path("/usr/bin/false").is_file()
    # This performs the same rootless user/net/IPC/PID namespace probe used by
    # the production process supervisor. Failure is an acceptance failure.
    assert WorkProcessSupervisor()._require_capabilities()


def _host_backend_factory(
    supervisors,
    executions,
    *,
    mcp_proxy_factory=None,
    execution_records=None,
):
    def create_backend(_marker, repository):
        def make_supervisor():
            supervisor = WorkProcessSupervisor()
            supervisors.append(supervisor)
            return supervisor

        backend = BubblewrapWorkBackend(
            repository=repository,
            bwrap_sha256_digest=hashlib.sha256(Path("/usr/bin/bwrap").read_bytes()).hexdigest(),
            supervisor_factory=make_supervisor,
            broker_account=os.environ.get("CAO_WORK_BROKER_ACCOUNT"),
            mcp_proxy_factory=(
                None
                if mcp_proxy_factory is None
                else lambda attempt_id, generation: mcp_proxy_factory(
                    repository, attempt_id, generation
                )
            ),
        )
        execute = backend.execute_bound_process

        def capture_execution(*args, **kwargs):
            result = execute(*args, **kwargs)
            executions.append(result)
            if execution_records is not None:
                execution_records.append((kwargs["binding"].attempt_id, result))
            return result

        backend.execute_bound_process = capture_execution
        backend.create_session = lambda *_args, **_kwargs: pytest.fail(
            "Work process launch fell back to terminal creation"
        )
        backend.send_keys = lambda *_args, **_kwargs: pytest.fail(
            "Work process launch fell back to terminal input"
        )
        return backend

    return create_backend


async def _dispatch_one(tmp_path, worker, supervisors, executions):
    repository, principal, gateway, backend, marker, request = _setup(
        tmp_path,
        backend_factory=_host_backend_factory(supervisors, executions),
        worker_binary=worker,
    )
    receipt = gateway.admit(principal, request)
    try:
        result = await gateway.dispatch_registered_next()
    except Exception as error:
        details = []
        current = error
        while current is not None:
            details.append(f"{type(current).__name__}: {current}")
            for stream in ("stderr", "stdout"):
                value = getattr(current, stream, None)
                if value:
                    details.append(f"{stream}: {value[-2000:]!r}")
            current = current.__cause__
        raise AssertionError("host dispatch failed:\n" + "\n".join(details)) from error
    assert result["id"] == receipt.work_item_id
    assert result["state"] == "running"
    assert result["attempts"][0]["state"] == "sent"
    assert result["attempts"][0]["terminal_id"] is None
    assert marker.exists() is False
    return repository, principal, gateway, backend, receipt, result


def _assert_acknowledgement(execution):
    acknowledgement = execution.acknowledgement
    assert execution.returncode == 0
    assert acknowledgement.open_fds == (0, 1, 2)
    assert acknowledgement.landlock_abi >= 9
    assert acknowledgement.seccomp_mode == 2
    assert acknowledgement.unlisted_path_denied is True
    assert acknowledgement.unmounted_host_path_absent is True
    assert acknowledgement.connect_denied_errno == errno.EPERM


@pytest.mark.asyncio
async def test_durable_gateway_executes_bound_worker_after_real_host_preflight(tmp_path):
    supervisors = []
    executions = []
    repository, principal, gateway, backend, receipt, result = await _dispatch_one(
        tmp_path, _minimal_static_worker(), supervisors, executions
    )

    assert len(executions) == len(supervisors) == 1
    assert executions[0].stdout == _WORKER_MARKER
    _assert_acknowledgement(executions[0])
    assert supervisors[0].attempt is not None
    assert supervisors[0].attempt.state is WorkProcessState.TERMINATED
    evidence = WorkBubblewrapSetupIntent(repository).read_historical(
        receipt.attempt_id, receipt.generation
    )
    assert evidence.release_intent == "pending"
    restarted_gateway = build_durable_launch_gateway(repository, backends={"test": backend})
    assert await restarted_gateway.dispatch_registered_next() is None
    assert result["attempts"][0]["state"] == "sent"


@pytest.mark.asyncio
async def test_gateway_enforces_descendant_exec_policy_on_approved_host(tmp_path):
    supervisors = []
    executions = []
    worker = _adversarial_static_worker(tmp_path)
    _repository, _principal, _gateway, _backend, _receipt, _result = await _dispatch_one(
        tmp_path, worker, supervisors, executions
    )

    assert len(executions) == len(supervisors) == 1
    assert executions[0].stdout == (
        b"memfd-create-denied\n"
        b"execveat-denied\n"
        b"mapped-descendant-exec-allowed\n"
        b"unmapped-exec-denied\n"
        b"direct-loader-denied\n"
        b"double-fork-descendant-reaped\n"
    )
    _assert_acknowledgement(executions[0])
    assert supervisors[0].attempt.state is WorkProcessState.TERMINATED


def _unused_sysv_key() -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.shmget.argtypes = (ctypes.c_int, ctypes.c_size_t, ctypes.c_int)
    libc.shmget.restype = ctypes.c_int
    for _ in range(100):
        key = random.randrange(0x1000000, 0x7FFFFFFE)
        ctypes.set_errno(0)
        if libc.shmget(key, 1, 0o600) < 0 and ctypes.get_errno() == errno.ENOENT:
            return key
    raise AssertionError("could not find an unused System V shared-memory key")


@pytest.mark.asyncio
async def test_gateway_contains_filesystem_network_ipc_fds_and_proc_environment(
    tmp_path, monkeypatch
):
    supervisors = []
    executions = []
    key = _unused_sysv_key()
    source = Path(__file__).resolve().parents[2] / "fixtures" / "t097_host_isolation_worker.c"
    worker_path = tmp_path / "t097-host-isolation-worker"
    build = subprocess.run(
        [
            shutil.which("cc"),
            "-static",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            f"-DT097_SHM_KEY={key}",
            str(source),
            "-o",
            str(worker_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert build.returncode == 0, build.stderr
    monkeypatch.setenv("T097_SECRET", "must-not-cross-clearenv")

    _repository, _principal, _gateway, _backend, _receipt, _result = await _dispatch_one(
        tmp_path, worker_path.read_bytes(), supervisors, executions
    )

    assert len(executions) == len(supervisors) == 1
    expected_output = (
        b"host-etc-hidden\n"
        b"readonly-usr-denied\n"
        b"tmpfs-write-denied\n"
        b"network-connect-denied\n"
        b"private-sysv-shm-created\n"
        b"secret-environment-hidden\n"
        b"proc-fd-path-denied\n"
        b"dev-fd-stdin-alias-available\n"
    )
    assert executions[0].stdout == expected_output, executions[0].stdout.decode(errors="replace")
    _assert_acknowledgement(executions[0])
    assert not (tmp_path / "t097-private-tmp-effect").exists()
    libc = ctypes.CDLL(None, use_errno=True)
    ctypes.set_errno(0)
    assert libc.shmget(key, 1, 0o600) == -1
    assert ctypes.get_errno() == errno.ENOENT
    assert supervisors[0].attempt.state is WorkProcessState.TERMINATED


@pytest.mark.asyncio
async def test_revocation_after_ack_withholds_go_and_prevents_redelivery(tmp_path, monkeypatch):
    supervisors = []
    executions = []
    repository, principal, gateway, _backend, _marker, request = _setup(
        tmp_path,
        backend_factory=_host_backend_factory(supervisors, executions),
        worker_binary=_minimal_static_worker(),
    )
    with repository.read_snapshot() as connection:
        grant_id = connection.execute(
            "SELECT id FROM work_grants WHERE principal_id=? ORDER BY created_at LIMIT 1",
            (principal.id,),
        ).fetchone()["id"]
    original_release = WorkBubblewrapSetupIntent.release_if_current

    def revoke_before_go(self, evidence, *, expected_attempt_revision, release, authorize=None):
        WorkAuthority(repository).revoke(
            principal,
            grant_id=grant_id,
            expected_grant_revision=1,
            reason="T097 acceptance revocation between ACK and GO",
        )
        return original_release(
            self,
            evidence,
            expected_attempt_revision=expected_attempt_revision,
            release=release,
            authorize=authorize,
        )

    monkeypatch.setattr(WorkBubblewrapSetupIntent, "release_if_current", revoke_before_go)
    receipt = gateway.admit(principal, request)

    with pytest.raises(DeliveryUncertain):
        await gateway.dispatch_registered_next()

    current = repository.get_work(receipt.work_item_id)
    assert current["state"] == "reconcile"
    assert current["attempts"][0]["state"] == "reconcile"
    assert executions == []
    assert len(supervisors) == 1
    assert supervisors[0].attempt.state is WorkProcessState.TERMINATED
    assert await gateway.dispatch_registered_next() is None


@pytest.mark.asyncio
async def test_lost_result_ack_reconciles_without_worker_redelivery(tmp_path):
    supervisors = []
    executions = []
    base_factory = _host_backend_factory(supervisors, executions)

    def lose_result_ack(_marker, repository):
        backend = base_factory(_marker, repository)
        execute = backend.execute_bound_process

        def complete_but_lose_ack(*args, **kwargs):
            execute(*args, **kwargs)
            raise WorkBubblewrapExecutionUncertain(
                "injected result acknowledgement loss after confirmed process cleanup",
                cleanup_confirmed=False,
            )

        backend.execute_bound_process = complete_but_lose_ack
        return backend

    repository, principal, gateway, backend, _marker, request = _setup(
        tmp_path,
        backend_factory=lose_result_ack,
        worker_binary=_minimal_static_worker(),
    )
    receipt = gateway.admit(principal, request)

    with pytest.raises(DeliveryUncertain):
        await gateway.dispatch_registered_next()

    current = repository.get_work(receipt.work_item_id)
    assert current["state"] == "reconcile"
    assert current["attempts"][0]["state"] == "reconcile"
    assert len(executions) == len(supervisors) == 1
    assert executions[0].stdout == _WORKER_MARKER
    assert supervisors[0].attempt.state is WorkProcessState.TERMINATED
    assert await gateway.dispatch_registered_next() is None
    assert len(executions) == 1
    restarted = build_durable_launch_gateway(repository, backends={"test": backend})
    assert await restarted.dispatch_registered_next() is None
    assert len(executions) == 1
