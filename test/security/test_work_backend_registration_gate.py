"""Unregistered Work transports stay behind the real enforcement preflight."""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import time
from pathlib import Path

import pytest

from cli_agent_orchestrator.backends import bubblewrap_backend, work_registry
from cli_agent_orchestrator.backends.base import (
    ProcessRestrictionContract,
    UnsupportedWorkEnforcement,
)
from cli_agent_orchestrator.backends.bubblewrap_backend import BubblewrapWorkBackend
from cli_agent_orchestrator.backends.herdr_backend import HerdrBackend
from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    ExecutableIdentity,
    EffectiveWorkContract,
)


@pytest.fixture(autouse=True)
def test_candidate_artifact_checks_with_valid_broker(monkeypatch):
    """Keep these tests focused on artifact trust, not OS account setup."""
    monkeypatch.setattr(bubblewrap_backend, "_require_work_broker_identity", lambda _name: None)


from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.work_admission import WorkAdmission
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_scheduler import WorkScheduler


class _EffectRecorder:
    """Fake transport that records forbidden terminal mutations."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith(("create_", "send_", "kill_", "pipe_", "stop_pipe_")):

            def record(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                return "unexpected-effect"

            return record
        raise AttributeError(name)


def _candidate_backend(candidate, tmp_path, monkeypatch):
    transport = _EffectRecorder()

    if candidate == "tmux":
        return TmuxBackend(client=transport), transport, None

    if candidate == "herdr":
        # Herdr starts a local daemon in __init__; replace only that constructor
        # side effect and its terminal command seam, while keeping real methods.
        monkeypatch.setattr(HerdrBackend, "_ensure_session_running", lambda _self: None)
        backend = HerdrBackend()
        backend._run_herdr = lambda *args, **kwargs: transport.calls.append(("herdr", args, kwargs))
        return backend, transport, None

    if candidate.startswith("bubblewrap-"):
        return BubblewrapWorkBackend(client=transport), transport, None

    raise AssertionError(f"unhandled test backend {candidate}")


def _admission_context(tmp_path, candidate, backend):
    root = tmp_path.resolve()
    repository = WorkRepository(tmp_path / f"{candidate}.sqlite3")
    repository.initialize()
    WorkScheduler(repository).configure(
        capacity=1, max_queue=4, aging_seconds=10, expected_policy_revision=0
    )

    principal = auth._verified_principal(
        "https://work-gate.test", f"owner-{candidate}", [auth.SCOPE_ADMIN], "jwt"
    )
    job = repository.create_job(
        project_id="work-gate-project",
        principal_id=principal.id,
        allowed_providers=["mock_cli"],
        grant_id=f"grant-{candidate}",
        budget={"scheduler_units": 4},
    )
    grant = WorkAuthority(repository).issue_root(
        principal,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read"}, paths={str(root)}, commands={"/usr/bin/bash"}
        ),
        expires_at=time.time() + 600,
    )
    snapshot = DelegationSnapshots(
        repository,
        policy=KnowledgePolicy(repository, job["id"], grant.id, 1),
    ).freeze(
        principal=principal,
        job_id=job["id"],
        contract_id=f"contract-{candidate}",
        binding_key=f"snapshot-{candidate}",
        request_hash=hashlib.sha256(candidate.encode()).hexdigest(),
        scope="project",
        scope_id=job["project_id"],
        resolver=lambda _connection, _actor: ResolvedSnapshot("frozen test context"),
    )
    contract = EffectiveWorkContract(
        id=f"contract-{candidate}",
        operation_kind="inbox",
        provider="mock_cli",
        backend=candidate,
        permissions=ContractPermissions(
            tools=("knowledge.read",),
            paths=(str(root),),
            commands=("/usr/bin/bash",),
        ),
        resources=ContractResources(checkout_root=str(root), units=1),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )
    admission = WorkAdmission(repository, backends={candidate: backend})
    return repository, admission, principal, job, grant, contract


def _durable_dispatch_counts(repository):
    with repository.connection() as connection:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "work_items",
                "work_attempts",
                "work_dispatch_bindings",
                "work_delivery_orders",
                "work_scheduler_requests",
            )
        )


def test_work_backend_registry_is_empty_until_an_enforcement_backend_passes_its_gate():
    """The application must not opt a terminal transport into Work implicitly."""
    assert work_registry.WORK_BACKENDS == {}


@pytest.mark.parametrize("candidate", ["tmux", "herdr", "bubblewrap-unbound-contract"])
def test_rejected_work_backend_cannot_enqueue_or_deliver_terminal_effects(
    tmp_path, monkeypatch, candidate
):
    """Real admission and protected methods reject before create/window/send."""
    backend, transport, probe_log = _candidate_backend(candidate, tmp_path, monkeypatch)
    repository, admission, principal, job, grant, contract = _admission_context(
        tmp_path, candidate, backend
    )

    with pytest.raises(UnsupportedWorkEnforcement) as rejected:
        admission.admit(
            principal=principal,
            job_id=job["id"],
            idempotency_key=f"select-{candidate}",
            request_hash=hashlib.sha256(f"request-{candidate}".encode()).hexdigest(),
            grant_id=grant.id,
            expected_grant_revision=1,
            contract=contract,
        )
    if candidate == "bubblewrap-unbound-contract":
        assert "contract tools require an attempt-bound MCP proxy factory" in rejected.value.reason

    restriction = admission._restriction(contract)
    with pytest.raises(UnsupportedWorkEnforcement):
        backend.create_work_session(restriction, "s", "w", "terminal")
    with pytest.raises(UnsupportedWorkEnforcement):
        backend.create_work_window(restriction, "s", "w", "terminal")
    with pytest.raises(UnsupportedWorkEnforcement):
        backend.send_work_keys(restriction, "s", "w", "input")

    deliveries = []
    assert admission.dispatch_next(lambda binding, port: deliveries.append((binding, port))) is None
    assert deliveries == []
    assert transport.calls == []
    assert _durable_dispatch_counts(repository) == (0, 0, 0, 0, 0)
    assert work_registry.WORK_BACKENDS == {}

    assert probe_log is None


def _valid_restriction(tmp_path):
    root = str(tmp_path.resolve())
    digest = hashlib.sha256(b"synthetic static ELF identity").hexdigest()
    identity = ExecutableIdentity(
        command_token="/usr/bin/bash",
        content_reference=f"sha256:{digest}",
        sha256_digest=digest,
        elf_machine="x86_64",
        elf_class="ELF64",
        endianness="little",
        static=True,
    )
    return ProcessRestrictionContract(
        paths=(),
        commands=("/usr/bin/bash",),
        network=(),
        read_paths=(root,),
        checkout_root=root,
        executable_identities=(identity,),
    )


def test_root_owned_non_bubblewrap_utility_cannot_be_selected(tmp_path, monkeypatch):
    utility = Path("/usr/bin/ls")
    if not utility.exists():
        pytest.skip("the standard root-owned /usr/bin/ls utility is unavailable")
    assert utility.stat().st_uid == 0

    probe_calls = []
    monkeypatch.setattr(bubblewrap_backend.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bubblewrap_backend, "_query_abi_version", lambda: 9)
    monkeypatch.setattr(
        bubblewrap_backend.subprocess,
        "run",
        lambda *args, **kwargs: probe_calls.append((args, kwargs)),
    )
    backend = BubblewrapWorkBackend(bwrap_executable=utility)

    with pytest.raises(UnsupportedWorkEnforcement, match="canonical trusted path"):
        backend.preflight_work(_valid_restriction(tmp_path))

    assert probe_calls == []


def test_euid_owned_read_only_bubblewrap_candidate_is_never_executed(tmp_path, monkeypatch):
    marker = tmp_path / "executed"
    executable = tmp_path / "bwrap"
    executable.write_text(
        "#!/bin/sh\n"
        f"printf 'ran' > {shlex.quote(str(marker))}\n"
        "printf 'bubblewrap 0.13.0\\n'\n",
        encoding="utf-8",
    )
    executable.chmod(0o555)
    assert executable.stat().st_uid == os.geteuid()

    probe_calls = []

    def record_probe(args, **kwargs):
        probe_calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="bubblewrap 0.13.0\n", stderr="")

    monkeypatch.setattr(bubblewrap_backend.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bubblewrap_backend.subprocess, "run", record_probe)
    backend = BubblewrapWorkBackend(bwrap_executable=executable)

    with pytest.raises(UnsupportedWorkEnforcement):
        backend.preflight_work(_valid_restriction(tmp_path))

    assert probe_calls == []
    assert not marker.exists()
