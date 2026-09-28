"""Admission commits authorization, immutable order and queue as one SQLite unit."""

from concurrent.futures import ThreadPoolExecutor
import importlib
import importlib.util
import json
import multiprocessing
import os
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.backends.base import UnsupportedWorkEnforcement
from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator import constants
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContract,
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
from cli_agent_orchestrator.services.work_scheduler import WorkScheduler


class AdmissionOnlyBackend(TmuxBackend):
    """Test preflight only; never claims tmux actually enforces restrictions."""

    def __init__(self, hook=lambda: None):
        self.hook = hook

    def preflight_work(self, contract):
        self.hook()

    def create_session(self, *args, **kwargs):
        raise AssertionError("admission must never create a process")


def _process_commit_dispatch_intent_then_die(database, committed):
    """Crash after durable reservation/intent and before any delivery callback."""
    repository = WorkRepository(database)
    service = coordinator(SimpleNamespace(repo=repository), AdmissionOnlyBackend())
    if service._prepare_dispatch() is None:
        os._exit(2)
    committed.set()
    os._exit(23)


def _process_restart_without_redelivery(database, outcomes):
    """A restarted dispatcher must not infer send permission from a durable intent."""
    repository = WorkRepository(database)
    service = coordinator(SimpleNamespace(repo=repository), AdmissionOnlyBackend())

    def forbidden_effect(*_):
        raise AssertionError("restart must not redeliver a sent attempt")

    try:
        outcomes.put(("ok", service.dispatch_next(forbidden_effect) is None))
    except BaseException as error:
        outcomes.put(("error", type(error).__name__, str(error)))


def _process_admit_after_blocked_preflight(
    database, actor, job_id, grant_id, contract, entered, release, outcomes
):
    """Use a new SQLite client while another process changes authority."""
    repository = WorkRepository(database)

    def block_preflight():
        entered.set()
        if not release.wait(timeout=20):
            raise TimeoutError("preflight release timed out")

    service = coordinator(SimpleNamespace(repo=repository), AdmissionOnlyBackend(block_preflight))
    try:
        service.admit(
            principal=actor,
            job_id=job_id,
            idempotency_key="process-revoked",
            request_hash="c" * 64,
            grant_id=grant_id,
            expected_grant_revision=1,
            contract=contract,
            lease_seconds=300,
        )
    except BaseException as error:
        outcomes.put(("raised", type(error).__name__))
    else:
        outcomes.put(("admitted",))


def _process_revoke_grant(database, actor, grant_id, outcomes):
    try:
        WorkAuthority(WorkRepository(database)).revoke(
            actor,
            grant_id=grant_id,
            expected_grant_revision=1,
            reason="process preflight revocation",
        )
    except BaseException as error:
        outcomes.put(("error", type(error).__name__, str(error)))
    else:
        outcomes.put(("revoked",))


def _cleanup_process(process):
    if process.pid is None:
        return
    if process.is_alive():
        process.terminate()
    process.join(timeout=5)
    assert not process.is_alive(), "test process did not terminate during cleanup"
    process.close()


@pytest.fixture
def context(tmp_path):
    repository = WorkRepository(tmp_path / "admission.db")
    repository.initialize()
    WorkScheduler(repository).configure(
        capacity=2, max_queue=100, aging_seconds=10, expected_policy_revision=0
    )
    actor = auth._verified_principal("issuer", "owner", [auth.SCOPE_ADMIN], "jwt")
    job = repository.create_job(
        project_id="project",
        principal_id=actor.id,
        allowed_providers=["mock_cli"],
        grant_id="root",
        budget={"scheduler_units": 100},
    )
    control = WorkAuthority(repository)
    grant = control.issue_root(
        actor,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read"}, paths={str(tmp_path)}, commands={"/bin/alpha"}
        ),
        expires_at=time.time() + 600,
    )
    snapshot = DelegationSnapshots(
        repository,
        policy=KnowledgePolicy(repository, job["id"], grant.id, 1),
    ).freeze(
        principal=actor,
        job_id=job["id"],
        contract_id="contract",
        binding_key="snapshot",
        request_hash="a" * 64,
        scope="project",
        scope_id="project",
        resolver=lambda connection, principal: ResolvedSnapshot(""),
    )
    contract = EffectiveWorkContract(
        id="contract",
        operation_kind="inbox",
        provider="mock_cli",
        backend="test",
        permissions=ContractPermissions(paths=(str(tmp_path),)),
        resources=ContractResources(
            checkout_root=str(tmp_path), write_paths=(str(tmp_path / "work"),), units=1
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )
    return SimpleNamespace(
        repo=repository, actor=actor, job=job, control=control, grant=grant, contract=contract
    )


def coordinator(context, backend=None):
    name = "cli_agent_orchestrator.services.work_admission"
    assert importlib.util.find_spec(name), "atomic admission coordinator missing"
    return importlib.import_module(name).WorkAdmission(
        context.repo,
        backends={"test": backend or AdmissionOnlyBackend()},
    )


def admit(context, service, key="request", **changes):
    args = dict(
        principal=context.actor,
        job_id=context.job["id"],
        idempotency_key=key,
        request_hash="b" * 64,
        grant_id=context.grant.id,
        expected_grant_revision=1,
        contract=context.contract,
        lease_seconds=300,
    )
    args.update(changes)
    return service.admit(**args)


def counts(context):
    with context.repo.connection() as connection:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "work_items",
                "work_attempts",
                "work_dispatch_bindings",
                "work_scheduler_requests",
            )
        )


def test_concurrent_admission_has_one_item_binding_and_queue_entry(context):
    service = coordinator(context)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: admit(context, service), range(12)))
    assert len({item["id"] for item in results}) == 1
    assert counts(context) == (1, 1, 1, 1)
    with context.repo.connection() as connection:
        assert (
            connection.execute("SELECT state FROM work_scheduler_requests").fetchone()[0]
            == "queued"
        )
        assert connection.execute("SELECT count(*) FROM work_reservation_sets").fetchone()[0] == 0
    assert results[0]["state"] == "queued"


def test_unsupported_backend_never_persists_admission(context):
    service = coordinator(context, object.__new__(TmuxBackend))
    with pytest.raises(UnsupportedWorkEnforcement):
        admit(context, service)
    assert counts(context) == (0, 0, 0, 0)


@pytest.mark.parametrize("empty", [False, True])
def test_backend_namespace_never_exceeds_frozen_reservation_request(context, empty):
    backend = AdmissionOnlyBackend()
    backend.preflight_work = Mock()
    contract = context.contract
    if empty:
        contract = contract.model_copy(
            update={"resources": contract.resources.model_copy(update={"write_paths": ()})}
        )
    admit(context, coordinator(context, backend), contract=contract)
    restriction = backend.preflight_work.call_args.args[0]
    assert restriction.paths == contract.resources.write_paths


def test_revocation_during_preflight_blocks_admission(context):
    def revoke():
        context.control.revoke(
            context.actor, grant_id=context.grant.id, expected_grant_revision=1, reason="test"
        )

    service = coordinator(context, AdmissionOnlyBackend(revoke))
    with pytest.raises(PermissionError):
        admit(context, service)
    assert counts(context) == (0, 0, 0, 0)


def test_full_queue_rolls_back_item_contract_and_events(context):
    WorkScheduler(context.repo).configure(
        capacity=2, max_queue=1, aging_seconds=10, expected_policy_revision=1
    )
    service = coordinator(context)
    admit(context, service)
    before = context.repo.read_events(context.job["id"])
    with pytest.raises(ValueError):
        admit(context, service, key="second")
    assert counts(context) == (1, 1, 1, 1)
    assert context.repo.read_events(context.job["id"]) == before


def test_changed_contract_cannot_hide_under_same_idempotency_hash(context):
    service = coordinator(context)
    admit(context, service)
    changed = context.contract.model_copy(
        update={"resources": context.contract.resources.model_copy(update={"units": 2})}
    )
    with pytest.raises(ValueError):
        admit(context, service, contract=changed)
    assert counts(context) == (1, 1, 1, 1)


def test_v2_admission_preflight_receives_exact_executable_identity_and_replay_is_immutable(context):
    identity = ExecutableIdentity(
        command_token="/bin/alpha",
        content_reference="sha256:" + "a" * 64,
        sha256_digest="a" * 64,
        elf_machine="x86_64",
        elf_class="ELF64",
        endianness="little",
    )
    contract = EffectiveWorkContractV2(
        **{
            **context.contract.model_dump(),
            "schema_version": 2,
            "permissions": context.contract.permissions.model_copy(
                update={"commands": ("/bin/alpha",)}
            ),
            "executable_identities": (identity,),
        }
    )
    backend = AdmissionOnlyBackend()
    backend.preflight_work = Mock()
    service = coordinator(context, backend)
    first = admit(context, service, contract=contract)
    restriction = backend.preflight_work.call_args.args[0]
    assert restriction.executable_identities == (identity,)
    assert admit(context, service, contract=contract)["id"] == first["id"]
    assert backend.preflight_work.call_count == 1
    changed_identity = identity.model_copy(
        update={"sha256_digest": "b" * 64, "content_reference": "sha256:" + "b" * 64}
    )
    with pytest.raises(ValueError):
        admit(
            context,
            service,
            contract=contract.model_copy(update={"executable_identities": (changed_identity,)}),
        )
    assert backend.preflight_work.call_count == 1
    assert counts(context) == (1, 1, 1, 1)


@pytest.mark.parametrize("corruption", ["json", "hash", "projection", "missing"])
def test_v2_replay_rejects_corrupt_or_missing_identity_before_preflight(context, corruption):
    from cli_agent_orchestrator.clients.work_dispatch_schema import (
        DISPATCH_SCHEMA,
        DISPATCH_V2_EVIDENCE_SCHEMA,
    )

    identity = ExecutableIdentity(
        command_token="/bin/alpha",
        content_reference="sha256:" + "a" * 64,
        sha256_digest="a" * 64,
        elf_machine="x86_64",
        elf_class="ELF64",
        endianness="little",
    )
    contract = EffectiveWorkContractV2(
        **{
            **context.contract.model_dump(),
            "schema_version": 2,
            "permissions": context.contract.permissions.model_copy(
                update={"commands": ("/bin/alpha",)}
            ),
            "executable_identities": (identity,),
        }
    )
    backend = AdmissionOnlyBackend()
    backend.preflight_work = Mock()
    service = coordinator(context, backend)
    admit(context, service, contract=contract)

    with sqlite3.connect(context.repo.path) as connection:
        table = "work_dispatch_bindings" if corruption == "projection" else "work_dispatch_v2_evidence"
        action = "delete" if corruption == "missing" else "update"
        trigger = f"{table}_immutable_{action}"
        schema = DISPATCH_SCHEMA if corruption == "projection" else DISPATCH_V2_EVIDENCE_SCHEMA
        connection.execute(f"DROP TRIGGER {trigger}")
        if corruption == "missing":
            connection.execute("DELETE FROM work_dispatch_v2_evidence")
        elif corruption == "hash":
            connection.execute(
                "UPDATE work_dispatch_v2_evidence SET contract_hash=?", ("f" * 64,)
            )
        else:
            payload = json.loads(connection.execute(
                f"SELECT contract_json FROM {table}"
            ).fetchone()[0])
            if corruption == "json":
                payload["executable_identities"][0]["sha256_digest"] = "b" * 64
                payload["executable_identities"][0]["content_reference"] = "sha256:" + "b" * 64
            else:
                payload["backend"] = "changed"
            connection.execute(
                f"UPDATE {table} SET contract_json=?",
                (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),),
            )
        connection.execute(next(statement for statement in schema if f"CREATE TRIGGER {trigger}" in statement))

    with pytest.raises(ValueError):
        admit(context, service, contract=contract)
    assert backend.preflight_work.call_count == 1
    assert counts(context) == (1, 1, 1, 1)


@pytest.mark.parametrize("malformation", ["bool_version", "missing_identity", "digest_mismatch"])
def test_malformed_v2_identity_fails_before_preflight_or_persistence(context, malformation):
    identity = ExecutableIdentity(
        command_token="/bin/alpha",
        content_reference="sha256:" + "a" * 64,
        sha256_digest="a" * 64,
        elf_machine="x86_64",
        elf_class="ELF64",
        endianness="little",
    )
    contract = EffectiveWorkContractV2(
        **{
            **context.contract.model_dump(),
            "schema_version": 2,
            "permissions": context.contract.permissions.model_copy(
                update={"commands": ("/bin/alpha",)}
            ),
            "executable_identities": (identity,),
        }
    )
    if malformation == "bool_version":
        contract = contract.model_copy(update={"schema_version": True})
    elif malformation == "missing_identity":
        contract = contract.model_copy(update={"executable_identities": ()})
    else:
        contract = contract.model_copy(update={
            "executable_identities": (identity.model_copy(update={"sha256_digest": "b" * 64}),)
        })
    backend = AdmissionOnlyBackend()
    backend.preflight_work = Mock()
    with pytest.raises(ValueError):
        admit(context, coordinator(context, backend), contract=contract)
    assert backend.preflight_work.call_count == 0
    assert counts(context) == (0, 0, 0, 0)


def test_snapshot_corruption_fails_before_commit(context):
    changed = context.contract.model_copy(
        update={
            "snapshot": context.contract.snapshot.model_copy(update={"delivered_hash": "f" * 64})
        }
    )
    with pytest.raises(ValueError):
        admit(context, coordinator(context), contract=changed)
    assert counts(context) == (0, 0, 0, 0)


def test_preflight_does_not_hold_database_write_transaction(context):
    def inspect():
        with context.repo.transaction() as connection:
            connection.execute("SELECT count(*) FROM work_jobs")

    assert admit(context, coordinator(context, AdmissionOnlyBackend(inspect)))["state"] == "queued"


def test_identical_replay_after_lease_expiry_does_not_rebind_or_renew(context):
    service = coordinator(context)
    work = admit(context, service)
    with context.repo.transaction() as connection:
        connection.execute(
            "UPDATE work_attempts SET lease_expires_at=1 WHERE id=?", (work["attempts"][0]["id"],)
        )
    before = context.repo.read_events(context.job["id"])
    repeated = admit(context, service)
    assert repeated["id"] == work["id"]
    assert repeated["attempts"][0]["lease_expires_at"] == 1
    assert context.repo.read_events(context.job["id"]) == before
    assert counts(context) == (1, 1, 1, 1)


def test_replay_does_not_require_backend_preflight_again(context):
    backend = AdmissionOnlyBackend()
    service = coordinator(context, backend)
    work = admit(context, service)

    def unavailable():
        raise RuntimeError("backend currently unavailable")

    backend.hook = unavailable
    repeated = admit(context, service)
    assert repeated["id"] == work["id"]
    assert counts(context) == (1, 1, 1, 1)


@pytest.mark.parametrize(
    ("operation_kind", "uses_parent"),
    (
        ("inbox", True),
        ("child", True),
        ("child", False),
        ("handoff", False),
    ),
)
def test_generic_new_child_routing_fails_before_preflight_or_persistence(
    context, operation_kind, uses_parent
):
    """Removing the managed-origin guard would create a child from generic metadata."""
    parent = admit(context, coordinator(context), key="generic-parent") if uses_parent else None
    backend = AdmissionOnlyBackend()
    backend.preflight_work = Mock()
    service = coordinator(context, backend)
    contract = context.contract.model_copy(
        update={"id": f"generic-{operation_kind}", "operation_kind": operation_kind}
    )
    before = counts(context)
    events = context.repo.read_events(context.job["id"])

    with pytest.raises(WorkConflict, match="managed lineage origin required"):
        admit(
            context,
            service,
            key=f"generic-{operation_kind}-{uses_parent}",
            contract=contract,
            parent_work_item_id=parent["id"] if parent else None,
        )

    assert counts(context) == before
    assert context.repo.read_events(context.job["id"]) == events
    backend.preflight_work.assert_not_called()


@pytest.mark.parametrize(
    ("operation_kind", "uses_parent"),
    (("inbox", True), ("child", False), ("handoff", False)),
)
def test_public_repository_cannot_create_a_new_legacy_child_route(
    context, operation_kind, uses_parent
):
    """The public persistence boundary cannot substitute for WorkOrigins."""
    parent = admit(context, coordinator(context), key="repository-parent") if uses_parent else None
    before = counts(context)
    events = context.repo.read_events(context.job["id"])

    with pytest.raises(WorkConflict, match="managed lineage origin required"):
        context.repo.admit_work(
            job_id=context.job["id"],
            operation_kind=operation_kind,
            idempotency_key=f"repository-{operation_kind}-{uses_parent}",
            request_hash="f" * 64,
            contract_id=f"repository-{operation_kind}",
            snapshot_id=context.contract.snapshot.id,
            provider=context.contract.provider,
            actor_id=context.actor.id,
            parent_work_item_id=parent["id"] if parent else None,
        )

    assert counts(context) == before
    assert context.repo.read_events(context.job["id"]) == events


def test_inbox_staging_cannot_create_a_new_child_from_a_generic_parent(context, monkeypatch):
    """A staged inbox order must not bypass the same managed lineage entrance."""
    monkeypatch.setattr(constants, "DATABASE_FILE", context.repo.path)
    parent = admit(context, coordinator(context), key="staging-parent")
    backend = AdmissionOnlyBackend()
    backend.preflight_work = Mock()
    service = coordinator(context, backend)
    before = counts(context)
    events = context.repo.read_events(context.job["id"])

    with pytest.raises(WorkConflict, match="managed lineage origin required"):
        service.reserve_managed_inbox(
            principal=context.actor,
            job_id=context.job["id"],
            idempotency_key="staging-child",
            request_hash="e" * 64,
            grant_id=context.grant.id,
            expected_grant_revision=context.grant.revision,
            contract=context.contract,
            managed_store_context=context.repo.managed_inbox_store_context(),
            parent_work_item_id=parent["id"],
        )

    assert counts(context) == before
    assert context.repo.read_events(context.job["id"]) == events
    backend.preflight_work.assert_not_called()


def test_process_crash_after_committed_intent_never_credits_or_redelivers(context):
    """Removing the sent fence would let a restarted dispatcher duplicate delivery."""
    work = admit(context, coordinator(context), key="process-crash")
    before = context.repo.read_events(context.job["id"])["events"]
    processes = multiprocessing.get_context("fork")
    committed, outcomes = processes.Event(), processes.Queue()
    crash = processes.Process(
        target=_process_commit_dispatch_intent_then_die,
        args=(context.repo.path, committed),
    )
    restart = None
    try:
        crash.start()
        assert committed.wait(timeout=20), "dispatcher never committed its intent"
        crash.join(timeout=10)
        assert not crash.is_alive(), "crashed dispatcher did not terminate"
        assert crash.exitcode == 23

        persisted = context.repo.get_work(work["id"])
        assert persisted["state"] == "running"
        assert persisted["attempts"][-1]["state"] == "sent"
        with context.repo.connection() as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM work_scheduler_requests WHERE state='held'"
                ).fetchone()[0]
                == 1
            )
            assert (
                connection.execute(
                    "SELECT count(*) FROM work_reservation_sets WHERE state='active'"
                ).fetchone()[0]
                == 1
            )
        event_types = [
            event["event_type"]
            for event in context.repo.read_events(context.job["id"])["events"][len(before) :]
        ]
        assert {"scheduler.claimed", "paths.reserved", "attempt.sent"} <= set(event_types)
        assert "attempt.acknowledged" not in event_types
        assert "attempt.running" not in event_types

        restart = processes.Process(
            target=_process_restart_without_redelivery,
            args=(context.repo.path, outcomes),
        )
        restart.start()
        assert outcomes.get(timeout=20) == ("ok", True)
        restart.join(timeout=10)
        assert not restart.is_alive(), "restarted dispatcher did not terminate"
        assert restart.exitcode == 0
        assert context.repo.get_work(work["id"])["attempts"][-1]["state"] == "sent"
    finally:
        _cleanup_process(crash)
        if restart is not None:
            _cleanup_process(restart)
        outcomes.close()
        outcomes.join_thread()


def test_admission_delivery_port_uses_the_shared_writer_registry_once(context):
    """The delivery callback sees one active writer, then dispatch releases it once."""
    from cli_agent_orchestrator.services.work_service import DeliveryObservation

    admit(context, coordinator(context), key="registry-port")
    active_counts = []

    def send(_binding, _port):
        with sqlite3.connect(context.repo.path) as connection:
            active_counts.append(
                connection.execute(
                    "SELECT count(*) FROM work_registered_writers WHERE state='active'"
                ).fetchone()[0]
            )
        return DeliveryObservation()

    assert coordinator(context).dispatch_next(send) is not None
    assert active_counts == [1]
    with sqlite3.connect(context.repo.path) as connection:
        assert connection.execute("SELECT state FROM work_registered_writers").fetchall() == [
            ("released",)
        ]


def test_process_revocation_during_preflight_leaves_no_admission(context):
    """Removing the live admission authorization fences would persist a revoked order."""
    before = context.repo.read_events(context.job["id"])["events"]
    processes = multiprocessing.get_context("fork")
    entered, release = processes.Event(), processes.Event()
    admission_outcomes, revocation_outcomes = processes.Queue(), processes.Queue()
    admission = processes.Process(
        target=_process_admit_after_blocked_preflight,
        args=(
            context.repo.path,
            context.actor,
            context.job["id"],
            context.grant.id,
            context.contract,
            entered,
            release,
            admission_outcomes,
        ),
    )
    revocation = None
    try:
        admission.start()
        assert entered.wait(timeout=20), "admission never reached blocked preflight"
        revocation = processes.Process(
            target=_process_revoke_grant,
            args=(context.repo.path, context.actor, context.grant.id, revocation_outcomes),
        )
        revocation.start()
        assert revocation_outcomes.get(timeout=20) == ("revoked",)
        revocation.join(timeout=10)
        assert not revocation.is_alive(), "revocation process did not terminate"
        assert revocation.exitcode == 0

        release.set()
        assert admission_outcomes.get(timeout=20) == ("raised", "AuthorityDenied")
        admission.join(timeout=10)
        assert not admission.is_alive(), "admission process did not terminate"
        assert admission.exitcode == 0
        assert counts(context) == (0, 0, 0, 0)
        events = context.repo.read_events(context.job["id"])["events"]
        assert events[:-1] == before
        assert events[-1]["event_type"] == "grant.revoked"
        assert events[-1]["work_item_id"] is None
        assert events[-1]["attempt_id"] is None
    finally:
        release.set()
        _cleanup_process(admission)
        if revocation is not None:
            _cleanup_process(revocation)
        admission_outcomes.close()
        admission_outcomes.join_thread()
        revocation_outcomes.close()
        revocation_outcomes.join_thread()
