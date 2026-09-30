"""Durable launch dispatch reaches only the bound Work process capability."""

import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from test.security.test_work_bubblewrap_composition import (
    _TRUSTED_BWRAP_SHA256,
    _WORKER_MARKER,
    _minimal_static_worker,
    _real_bubblewrap,
)

import pytest

from cli_agent_orchestrator.backends.bubblewrap_backend import BubblewrapWorkBackend
from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContractV2,
    ExecutableIdentity,
)
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_bubblewrap_setup_intent import WorkBubblewrapSetupIntent
from cli_agent_orchestrator.services.work_elf_identity import identify_static_executable
from cli_agent_orchestrator.services.work_executable_content import WorkExecutableContent
from cli_agent_orchestrator.services.work_launch_gateway import (
    DurableLaunchGatewayError,
    DurableLaunchRequest,
    build_durable_launch_gateway,
)
from cli_agent_orchestrator.services.work_process_supervisor import (
    WorkProcessState,
    WorkProcessSupervisor,
)
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning
from cli_agent_orchestrator.services.work_scheduler import WorkScheduler
from cli_agent_orchestrator.services.work_service import DeliveryUncertain


def _adversarial_static_worker(tmp_path):
    compiler = shutil.which("cc")
    readelf = shutil.which("readelf")
    dynamic_target = Path("/usr/bin/false")
    if compiler is None or readelf is None or not dynamic_target.is_file():
        pytest.skip("static C compiler, readelf, or the scratch dynamic target is unavailable")

    headers = subprocess.run(
        [readelf, "-l", str(dynamic_target)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    match = re.search(r"Requesting program interpreter:\s*([^\]]+)\]", headers.stdout)
    if headers.returncode != 0 or match is None:
        pytest.skip("/usr/bin/false has no inspectable dynamic loader for the exec probe")
    loader = Path(match.group(1).strip())
    if not loader.is_absolute() or not loader.is_file():
        pytest.skip(f"dynamic loader is unavailable in the test host: {loader}")

    fixture = (
        Path(__file__).resolve().parents[2] / "fixtures" / ("work_exec_policy_adversarial_worker.c")
    )
    source = tmp_path / "work_exec_policy_adversarial_worker.c"
    source.write_text(
        fixture.read_text(encoding="utf-8").replace(
            "T098_LOADER_PATH", json.dumps(str(loader.resolve()))
        ),
        encoding="utf-8",
    )
    executable = tmp_path / "work-exec-policy-adversarial-worker"
    build = subprocess.run(
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
    assert build.returncode == 0, f"could not compile adversarial worker:\n{build.stderr}"
    return executable.read_bytes()


class ProcessOnlyBackend(TmuxBackend):
    """Test backend exposes the process capability and records any terminal fallback."""

    def __init__(self, marker, repository, *, before_effect=None):
        self.marker = marker
        self.repository = repository
        self.before_effect_hook = before_effect
        self.process_calls = []
        self.terminal_calls = []

    def preflight_work(self, _restriction):
        return None

    def execute_bound_process(
        self,
        restriction,
        *,
        binding,
        command_token,
        worker_input,
        expected_attempt_revision,
        attempt_credential_fd,
        before_effect,
        authorize_setup,
        authorize_go,
        receiver_credential_fd=None,
    ):
        assert type(attempt_credential_fd) is int
        assert receiver_credential_fd is None or type(receiver_credential_fd) is int
        if self.before_effect_hook is not None:
            self.before_effect_hook()
        before_effect()
        with self.repository.transaction() as connection:
            authorize_setup(connection)
        with self.repository.transaction() as connection:
            authorize_go(connection)
        self.process_calls.append(
            {
                "attempt_id": binding.attempt_id,
                "generation": binding.generation,
                "attempt_revision": expected_attempt_revision,
                "work_item_id": binding.work_item_id,
                "contract_hash": binding.contract_hash,
                "command_token": command_token,
                "restriction": restriction,
            }
        )
        self.marker.write_bytes(worker_input)

    def create_session(self, *args, **kwargs):
        self.terminal_calls.append((args, kwargs))
        raise AssertionError("Work launch must not fall back to terminal creation")

    def send_keys(self, *args, **kwargs):
        self.terminal_calls.append((args, kwargs))
        raise AssertionError("Work launch must not fall back to terminal input")


class TerminalOnlyBackend(TmuxBackend):
    """Proves that a registered legacy backend cannot impersonate process execution."""

    def __init__(self, marker, _repository):
        self.marker = marker
        self.terminal_calls = []

    def preflight_work(self, _restriction):
        return None

    def create_session(self, *args, **kwargs):
        self.terminal_calls.append((args, kwargs))
        return "unexpected-terminal"


def _setup(
    tmp_path,
    *,
    backend_factory=ProcessOnlyBackend,
    worker_binary=None,
    contract_tools=(),
    request_tools=(),
    capacity=1,
    contract_paths=None,
    contract_write_paths=None,
    lease_seconds=300,
):
    repository = WorkRepository(tmp_path / "t098.sqlite3")
    repository.initialize()
    WorkScheduler(repository).configure(
        capacity=capacity, max_queue=20, aging_seconds=5, expected_policy_revision=0
    )
    principal = auth._verified_principal(
        "https://issuer.test", "t098-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    job = repository.create_job(
        project_id="t098-project",
        principal_id=principal.id,
        allowed_providers=["scratch_worker"],
        grant_id="t098-root",
        budget={"scheduler_units": 20},
    )
    grant = WorkAuthority(repository).issue_root(
        principal,
        job_id=job["id"],
        providers={"scratch_worker"},
        permissions=Permissions(
            tools={"knowledge.read", "tool.read", *contract_tools},
            paths={str(tmp_path)},
            commands={"/worker"},
        ),
        expires_at=time.time() + 300,
    )
    contract_id = "t098-contract"
    snapshot = DelegationSnapshots(
        repository,
        policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision),
    ).freeze(
        principal=principal,
        job_id=job["id"],
        contract_id=contract_id,
        binding_key="t098-snapshot",
        request_hash=hashlib.sha256(b"t098-snapshot").hexdigest(),
        scope="project",
        scope_id="t098-project",
        resolver=lambda _connection, _actor: ResolvedSnapshot("frozen task context"),
    )
    if worker_binary is None:
        digest = "a" * 64
        executable = ExecutableIdentity(
            command_token="/worker",
            content_reference=f"sha256:{digest}",
            sha256_digest=digest,
            elf_machine="x86_64",
            elf_class="ELF64",
            endianness="little",
            static=True,
        )
    else:
        executable = identify_static_executable("/worker", worker_binary)
        WorkExecutableContent(repository).publish(executable, worker_binary)
    contract = EffectiveWorkContractV2(
        id=contract_id,
        operation_kind="launch",
        provider="scratch_worker",
        backend="test",
        permissions=ContractPermissions(
            paths=(str(tmp_path),) if contract_paths is None else tuple(contract_paths),
            commands=("/worker",),
            tools=contract_tools,
        ),
        resources=ContractResources(
            checkout_root=str(tmp_path),
            write_paths=(
                (str(tmp_path / "worker-output"),)
                if contract_write_paths is None
                else tuple(contract_write_paths)
            ),
            units=1,
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
        executable_identities=(executable,),
    )
    WorkProvisioning(repository).provision_launch(
        principal,
        subject=principal,
        selector="t098-selection",
        expected_revision=0,
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        contract=contract,
        adapter_version=2,
        lease_seconds=lease_seconds,
    )
    marker = tmp_path / "worker-input.bin"
    backend = backend_factory(marker, repository)
    gateway = build_durable_launch_gateway(repository, backends={"test": backend})
    assert set(gateway._launch_runtime_provider._runtime._admission.deliveries.adapters) == {
        ("launch", 2),
        ("agent_step", 1),
    }
    request = DurableLaunchRequest(
        selection="t098-selection",
        agent_profile="developer",
        session_name="unused-by-process-launch",
        message="payload for exact worker",
        allowed_tools=request_tools,
    )
    return repository, principal, gateway, backend, marker, request


@pytest.mark.asyncio
async def test_durable_work_contract_can_dispatch_only_its_allowed_mcp_tools(tmp_path):
    repository, principal, gateway, backend, marker, request = _setup(
        tmp_path,
        contract_tools=("test.echo",),
        request_tools=("test.echo",),
    )

    receipt = gateway.admit(principal, request)
    result = await gateway.dispatch_registered_next()

    assert result["id"] == receipt.work_item_id
    assert len(backend.process_calls) == 1
    assert marker.read_bytes() == b"frozen task context\n\npayload for exact worker"


def _scratch_backend(
    bwrap, repository, supervisors, worker_outputs, *, lose_process_result_ack=False
):
    def new_supervisor():
        supervisor = WorkProcessSupervisor()
        supervisors.append(supervisor)
        return supervisor

    backend = BubblewrapWorkBackend(
        repository=repository,
        bwrap_executable=bwrap,
        bwrap_sha256_digest=_TRUSTED_BWRAP_SHA256,
        supervisor_factory=new_supervisor,
    )
    # Scratch only: do not treat this preflight bypass as production host proof.
    backend.preflight_work = lambda _restriction: None
    original_execute = backend.execute_bound_process

    def capture_scratch_result(*args, **kwargs):
        result = original_execute(*args, **kwargs)
        worker_outputs.append(result.stdout)
        if lose_process_result_ack:
            from cli_agent_orchestrator.services.work_bubblewrap_composition import (
                WorkBubblewrapExecutionUncertain,
            )

            raise WorkBubblewrapExecutionUncertain(
                "injected loss of the cleanup/result acknowledgement",
                cleanup_confirmed=False,
            )
        return result

    backend.execute_bound_process = capture_scratch_result
    backend.create_session = lambda *_args, **_kwargs: pytest.fail(
        "process launch fell back to terminal creation"
    )
    backend.send_keys = lambda *_args, **_kwargs: pytest.fail(
        "process launch fell back to terminal input"
    )
    return backend


@pytest.mark.asyncio
async def test_gateway_dispatches_v2_to_bound_process_effect_without_terminal_fallback(tmp_path):
    repository, principal, gateway, backend, marker, request = _setup(tmp_path)

    receipt = gateway.admit(principal, request)
    assert receipt.state == "queued"
    assert marker.exists() is False

    result = await gateway.dispatch_registered_next()

    assert result["id"] == receipt.work_item_id
    assert result["state"] == "running"
    assert result["attempts"][0]["state"] == "sent"
    assert marker.read_bytes() == b"frozen task context\n\npayload for exact worker"
    assert backend.terminal_calls == []
    assert len(backend.process_calls) == 1
    call = backend.process_calls[0]
    assert call["attempt_id"] == receipt.attempt_id
    assert call["work_item_id"] == receipt.work_item_id
    assert call["generation"] == receipt.generation
    assert call["attempt_revision"] == result["attempts"][0]["revision"]
    assert call["contract_hash"]
    assert call["command_token"] == "/worker"
    assert marker.read_bytes() == b"frozen task context\n\npayload for exact worker"
    assert result["attempts"][0]["terminal_id"] is None

    restarted_gateway = build_durable_launch_gateway(repository, backends={"test": backend})
    assert await restarted_gateway.dispatch_registered_next() is None
    assert len(backend.process_calls) == 1


@pytest.mark.asyncio
async def test_gateway_runs_scratch_worker_through_bubblewrap_and_supervisor(tmp_path):
    """Scratch execution proves composition only; it does not register/accept the host backend."""
    bwrap = _real_bubblewrap()
    worker = _minimal_static_worker()
    supervisors = []
    worker_outputs = []

    def create_backend(_marker, repository):
        return _scratch_backend(bwrap, repository, supervisors, worker_outputs)

    repository, principal, gateway, backend, _marker, request = _setup(
        tmp_path, backend_factory=create_backend, worker_binary=worker
    )
    receipt = gateway.admit(principal, request)

    try:
        result = await gateway.dispatch_registered_next()
    except Exception as error:
        from cli_agent_orchestrator.services.work_bubblewrap_composition import (
            WorkBubblewrapCompositionError,
        )

        if isinstance(error, WorkBubblewrapCompositionError) and error.cleanup_confirmed:
            pytest.skip(f"host cannot run the scratch composition: {error}")
        raise

    assert result["id"] == receipt.work_item_id
    assert result["state"] == "running"
    assert worker_outputs == [_WORKER_MARKER]
    assert len(supervisors) == 1
    assert supervisors[0].attempt is not None
    assert supervisors[0].attempt.state is WorkProcessState.TERMINATED
    evidence = WorkBubblewrapSetupIntent(repository).read_historical(
        receipt.attempt_id, receipt.generation
    )
    assert evidence.release_intent == "pending"
    assert supervisors[0].attempt._external_process_identity["identity_sha256"] == (
        evidence.process_identity_sha256
    )
    assert await gateway.dispatch_registered_next() is None


@pytest.mark.asyncio
async def test_double_fork_descendants_can_exec_only_the_bound_static_elf(tmp_path):
    bwrap = _real_bubblewrap()
    worker = _adversarial_static_worker(tmp_path)
    supervisors = []
    worker_outputs = []

    repository, principal, gateway, _backend, _marker, request = _setup(
        tmp_path,
        backend_factory=lambda _marker, repository: _scratch_backend(
            bwrap, repository, supervisors, worker_outputs
        ),
        worker_binary=worker,
    )
    receipt = gateway.admit(principal, request)

    result = await gateway.dispatch_registered_next()

    assert result["id"] == receipt.work_item_id
    assert result["state"] == "running"
    assert worker_outputs == [
        b"memfd-create-denied\n"
        b"execveat-denied\n"
        b"mapped-descendant-exec-allowed\n"
        b"unmapped-exec-denied\n"
        b"direct-loader-denied\n"
        b"double-fork-descendant-reaped\n"
    ]
    assert len(supervisors) == 1
    assert supervisors[0].attempt.state is WorkProcessState.TERMINATED
    evidence = WorkBubblewrapSetupIntent(repository).read_historical(
        receipt.attempt_id, receipt.generation
    )
    assert evidence.ack_sha256
    assert evidence.process_identity_sha256
    assert await gateway.dispatch_registered_next() is None


def test_shebang_cannot_be_published_as_bound_static_executable(tmp_path):
    script = b"#!/bin/sh\nprintf should-not-run\\n\n"
    digest = hashlib.sha256(script).hexdigest()
    repository = WorkRepository(tmp_path / "shebang.sqlite3")
    repository.initialize()
    identity = ExecutableIdentity(
        command_token="/worker",
        content_reference=f"sha256:{digest}",
        sha256_digest=digest,
        elf_machine="x86_64",
        elf_class="ELF64",
        endianness="little",
        static=True,
    )

    with pytest.raises(ValueError, match="invalid or truncated ELF"):
        WorkExecutableContent(repository).publish(identity, script)

    with repository.read_snapshot() as connection:
        assert (
            connection.execute("SELECT count(*) FROM work_executable_contents").fetchone()[0] == 0
        )


@pytest.mark.asyncio
async def test_go_authority_revocation_leaves_no_worker_effect_or_redelivery(tmp_path, monkeypatch):
    bwrap = _real_bubblewrap()
    worker = _minimal_static_worker()
    supervisors = []
    worker_outputs = []

    repository, principal, gateway, _backend, _marker, request = _setup(
        tmp_path,
        backend_factory=lambda _marker, repository: _scratch_backend(
            bwrap, repository, supervisors, worker_outputs
        ),
        worker_binary=worker,
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
            reason="T098 authority change after ACK, before GO",
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
    assert worker_outputs == []
    assert len(supervisors) == 1
    assert supervisors[0].attempt.state is WorkProcessState.TERMINATED
    evidence = WorkBubblewrapSetupIntent(repository).read_historical(
        receipt.attempt_id, receipt.generation
    )
    assert evidence.ack_sha256
    assert evidence.process_identity_sha256
    assert evidence.release_intent == "pending"
    assert await gateway.dispatch_registered_next() is None


@pytest.mark.asyncio
async def test_uncertain_cleanup_is_reconciled_without_process_redelivery(tmp_path):
    bwrap = _real_bubblewrap()
    worker = _minimal_static_worker()
    supervisors = []
    worker_outputs = []
    repository, principal, gateway, _backend, _marker, request = _setup(
        tmp_path,
        backend_factory=lambda _marker, repository: _scratch_backend(
            bwrap,
            repository,
            supervisors,
            worker_outputs,
            lose_process_result_ack=True,
        ),
        worker_binary=worker,
    )
    receipt = gateway.admit(principal, request)

    with pytest.raises(DeliveryUncertain):
        await gateway.dispatch_registered_next()

    current = repository.get_work(receipt.work_item_id)
    assert current["state"] == "reconcile"
    assert current["attempts"][0]["state"] == "reconcile"
    assert worker_outputs == [_WORKER_MARKER]
    assert len(supervisors) == 1
    assert supervisors[0].attempt.state is WorkProcessState.TERMINATED
    assert await gateway.dispatch_registered_next() is None
    assert worker_outputs == [_WORKER_MARKER]


@pytest.mark.asyncio
async def test_missing_process_capability_never_substitutes_terminal_backend(tmp_path):
    repository, principal, gateway, backend, marker, request = _setup(
        tmp_path, backend_factory=TerminalOnlyBackend
    )
    with pytest.raises(DurableLaunchGatewayError) as rejected:
        gateway.admit(principal, request)

    assert rejected.value.code == "launch_runtime_unavailable"
    with repository.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0
    assert backend.terminal_calls == []
    assert marker.exists() is False
    restarted_gateway = build_durable_launch_gateway(repository, backends={"test": backend})
    assert await restarted_gateway.dispatch_registered_next() is None
    assert backend.terminal_calls == []


@pytest.mark.asyncio
async def test_authority_change_at_process_effect_boundary_leaves_no_worker_marker(tmp_path):
    state = {}

    def revoke_before_effect():
        WorkAuthority(state["repository"]).revoke(
            state["principal"],
            grant_id=state["grant"],
            expected_grant_revision=1,
            reason="integration boundary revocation",
        )

    repository, principal, gateway, backend, marker, request = _setup(
        tmp_path,
        backend_factory=lambda path, repository: ProcessOnlyBackend(
            path, repository, before_effect=revoke_before_effect
        ),
    )
    state["repository"] = repository
    state["principal"] = principal
    with repository.read_snapshot() as connection:
        state["grant"] = connection.execute(
            "SELECT id FROM work_grants WHERE principal_id=? ORDER BY created_at LIMIT 1",
            (principal.id,),
        ).fetchone()["id"]

    receipt = gateway.admit(principal, request)

    with pytest.raises(DeliveryUncertain):
        await gateway.dispatch_registered_next()

    current = repository.get_work(receipt.work_item_id)
    assert current["state"] == "reconcile"
    assert current["attempts"][0]["state"] == "reconcile"
    assert marker.exists() is False
    assert backend.process_calls == []
    assert backend.terminal_calls == []
    assert await gateway.dispatch_registered_next() is None
