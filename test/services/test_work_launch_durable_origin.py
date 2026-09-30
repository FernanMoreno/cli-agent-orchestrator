"""Durable launch origin binding must survive a runtime restart."""

import json
import sqlite3
from dataclasses import replace
from test.services.test_work_launch_runtime import (  # noqa: F401
    ProtectedFakeBackend,
    intent,
    make_runtime,
    provision,
    runtime_module,
    trusted_setup,
)

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkConflict
from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
from cli_agent_orchestrator.services.work_authority import AuthorityDenied
from cli_agent_orchestrator.services.work_contract import ContractConflict
from cli_agent_orchestrator.services.work_launch import launch_adapter
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning


def test_runtime_resolves_sqlite_provision_without_context_registry(trusted_setup):
    """A fresh runtime derives its launch authority only from durable evidence."""
    repository, principal, _, job, grant, contract = trusted_setup
    provision = WorkProvisioning(repository).provision_launch(
        principal,
        subject=principal,
        selector="opaque",
        expected_revision=0,
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        contract=contract,
        adapter_version=1,
        lease_seconds=300,
    )
    module = runtime_module()
    runtime = module.LaunchRuntime(
        repository,
        backends={"test": ProtectedFakeBackend()},
        delivery_adapters={("launch", 1): launch_adapter()},
    )

    resolved = runtime.resolve_launch(principal, "opaque", intent(module), "durable-key")

    assert resolved.provision_ref == provision


def test_tampered_launch_handoff_cannot_create_an_unbound_order(trusted_setup):
    """Changing an effective handoff field is rejected before preflight or writes."""
    repository, principal, _, job, grant, contract = trusted_setup
    WorkProvisioning(repository).provision_launch(
        principal,
        subject=principal,
        selector="opaque",
        expected_revision=0,
        job_id=job["id"],
        grant_id=grant.id,
        grant_revision=grant.revision,
        contract=contract,
        adapter_version=1,
        lease_seconds=300,
    )
    module = runtime_module()
    backend = ProtectedFakeBackend()
    runtime = module.LaunchRuntime(
        repository,
        backends={"test": backend},
        delivery_adapters={("launch", 1): launch_adapter()},
    )
    tampered = replace(
        runtime.resolve_launch(principal, "opaque", intent(module), "tampered-key"),
        lease_seconds=1,
    )

    with pytest.raises(module.LaunchRuntimeError):
        runtime.admit_launch(principal, tampered)

    with repository.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0
        assert (
            connection.execute("SELECT count(*) FROM work_launch_origin_bindings").fetchone()[0]
            == 0
        )
    assert backend.preflights == []


def test_launch_origin_marker_cannot_be_downgraded_to_legacy(trusted_setup):
    """A v21 launch marker is durable evidence, never a mutable legacy switch."""
    provision(trusted_setup)
    module, runtime, _ = make_runtime(trusted_setup)
    repository, principal, _, _, _, _ = trusted_setup
    receipt = runtime.admit_launch(
        principal, runtime.resolve_launch(principal, "opaque", intent(module), "marker-key")
    )

    with repository.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="origin protocol"):
            connection.execute(
                "UPDATE work_items SET origin_protocol='legacy' WHERE id=?",
                (receipt.work_item_id,),
            )


def test_new_launch_admission_requires_durable_origin(trusted_setup):
    """The admission owner cannot mint a new executable legacy launch."""
    provision(trusted_setup)
    module, runtime, backend = make_runtime(trusted_setup)
    repository, principal, _, _, _, _ = trusted_setup
    resolved = runtime.resolve_launch(principal, "opaque", intent(module), "missing-origin-key")

    with pytest.raises(WorkConflict):
        runtime._admission.admit(
            principal=principal,
            job_id=resolved.job_id,
            idempotency_key=resolved.idempotency_key,
            request_hash=resolved.request_hash,
            grant_id=resolved.grant_id,
            expected_grant_revision=resolved.grant_revision,
            contract=resolved.contract,
            lease_seconds=resolved.lease_seconds,
            delivery=resolved.envelope,
        )

    with repository.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0
    assert backend.preflights == []


def test_replaced_handoff_selection_or_payload_cannot_reach_queue(trusted_setup):
    """The private handoff seals selection, request hash, and canonical envelope."""
    provision(trusted_setup)
    module, runtime, backend = make_runtime(trusted_setup)
    repository, principal, _, _, _, _ = trusted_setup
    resolved = runtime.resolve_launch(principal, "opaque", intent(module), "handoff-key")
    payload = json.loads(resolved.envelope.payload_json)
    payload["message"] = "tampered"
    changed_envelope = WorkDeliveryEnvelope(
        operation_kind=resolved.envelope.operation_kind,
        adapter_version=resolved.envelope.adapter_version,
        payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )

    for tampered in (
        replace(resolved, selection="other-selector"),
        replace(resolved, envelope=changed_envelope),
        replace(resolved, request_hash="0" * 64),
    ):
        with pytest.raises(module.LaunchRuntimeError):
            runtime.admit_launch(principal, tampered)

    with repository.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0
    assert backend.preflights == []


def _seed_pre_v21_legacy_launch(setup, *, idempotency_key):
    """Create migration evidence directly, never through a new legacy admission."""
    provision(setup, selection="legacy-replay")
    module, runtime, backend = make_runtime(setup)
    repository, principal, _, _, _, _ = setup
    resolved = runtime.resolve_launch(principal, "legacy-replay", intent(module), idempotency_key)
    admission = runtime._admission
    prepared = admission.deliveries.prepare(
        resolved.envelope, resolved.contract.operation_kind, contract=resolved.contract
    )
    with repository.transaction() as connection:
        work = repository._admit_work(
            connection,
            job_id=resolved.job_id,
            operation_kind=resolved.contract.operation_kind,
            idempotency_key=resolved.idempotency_key,
            request_hash=resolved.request_hash,
            contract_id=resolved.contract.id,
            snapshot_id=resolved.contract.snapshot.id,
            provider=resolved.contract.provider,
            actor_id=principal.id,
            lease_seconds=resolved.lease_seconds,
        )
        attempt = work["attempts"][0]
        binding = admission.contracts._bind(
            connection,
            principal=principal,
            attempt_id=attempt["id"],
            generation=attempt["generation"],
            expected_attempt_revision=attempt["revision"],
            grant_id=resolved.grant_id,
            expected_grant_revision=resolved.grant_revision,
            contract=resolved.contract,
        )
        admission.deliveries._bind(connection, binding, prepared, request=resolved.envelope)
        admission.scheduler._enqueue(
            connection,
            attempt_id=attempt["id"],
            generation=attempt["generation"],
            expected_attempt_revision=attempt["revision"],
            units=resolved.contract.resources.units,
            dependencies=resolved.contract.resources.dependencies,
            actor_id=principal.id,
        )
    return module, runtime, backend, resolved, work


def _legacy_replay_counts(repository):
    with repository.connection() as connection:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "work_items",
                "work_attempts",
                "work_dispatch_bindings",
                "work_delivery_orders",
                "work_scheduler_requests",
                "work_launch_origin_bindings",
                "work_events",
            )
        )


def test_only_a_preexisting_exact_legacy_launch_can_replay(trusted_setup):
    """Normal admission never reopens legacy creation; replay only reads durable history."""
    _, runtime, backend, resolved, seeded = _seed_pre_v21_legacy_launch(
        trusted_setup, idempotency_key="legacy-replay-key"
    )
    repository, principal, _, _, _, _ = trusted_setup
    replay_args = dict(
        principal=principal,
        job_id=resolved.job_id,
        idempotency_key=resolved.idempotency_key,
        request_hash=resolved.request_hash,
        grant_id=resolved.grant_id,
        expected_grant_revision=resolved.grant_revision,
        contract=resolved.contract,
        lease_seconds=resolved.lease_seconds,
        delivery=resolved.envelope,
    )
    before = _legacy_replay_counts(repository)

    with pytest.raises(WorkConflict, match="durable origin"):
        runtime._admission.admit(**replay_args)
    replayed = runtime._admission.replay_legacy_launch(**replay_args)

    assert replayed["id"] == seeded["id"]
    assert _legacy_replay_counts(repository) == before
    assert backend.preflights == []

    with pytest.raises(WorkConflict, match="idempotency key"):
        runtime._admission.replay_legacy_launch(**dict(replay_args, request_hash="0" * 64))
    assert _legacy_replay_counts(repository) == before

    with pytest.raises(WorkConflict, match="legacy launch"):
        runtime._admission.replay_legacy_launch(
            **dict(replay_args, idempotency_key="legacy-replay-new-key")
        )
    assert _legacy_replay_counts(repository) == before
    assert backend.preflights == []


def test_legacy_replay_rejects_incomplete_or_revoked_evidence(trusted_setup):
    """A history row cannot turn a missing binding or revoked grant into authority."""
    repository, principal, authority, job, grant, contract = trusted_setup
    _, runtime, backend, resolved, _ = _seed_pre_v21_legacy_launch(
        trusted_setup, idempotency_key="legacy-revoked-key"
    )
    replay_args = dict(
        principal=principal,
        job_id=resolved.job_id,
        idempotency_key=resolved.idempotency_key,
        request_hash=resolved.request_hash,
        grant_id=resolved.grant_id,
        expected_grant_revision=resolved.grant_revision,
        contract=resolved.contract,
        lease_seconds=resolved.lease_seconds,
        delivery=resolved.envelope,
    )
    authority.revoke(
        principal,
        grant_id=grant.id,
        expected_grant_revision=grant.revision,
        reason="legacy replay revoked",
    )
    before = _legacy_replay_counts(repository)

    with pytest.raises(AuthorityDenied):
        runtime._admission.replay_legacy_launch(**replay_args)
    assert _legacy_replay_counts(repository) == before
    assert backend.preflights == []


def test_legacy_replay_rejects_incomplete_order_without_writes(trusted_setup):
    """A marker alone is insufficient: binding and queue must preexist together."""
    repository, principal, _, job, grant, contract = trusted_setup
    request_hash = "a" * 64
    with repository.transaction() as connection:
        repository._admit_work(
            connection,
            job_id=job["id"],
            operation_kind="launch",
            idempotency_key="legacy-incomplete-key",
            request_hash=request_hash,
            contract_id=contract.id,
            snapshot_id=contract.snapshot.id,
            provider=contract.provider,
            actor_id=principal.id,
            lease_seconds=300,
        )
    _, runtime, backend = make_runtime(trusted_setup)
    before = _legacy_replay_counts(repository)

    with pytest.raises(WorkConflict, match="complete admitted order"):
        runtime._admission.replay_legacy_launch(
            principal=principal,
            job_id=job["id"],
            idempotency_key="legacy-incomplete-key",
            request_hash=request_hash,
            grant_id=grant.id,
            expected_grant_revision=grant.revision,
            contract=contract,
            lease_seconds=300,
        )
    assert _legacy_replay_counts(repository) == before
    assert backend.preflights == []


def _origin_durable_counts(repository):
    with repository.connection() as connection:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "work_items",
                "work_attempts",
                "work_dispatch_bindings",
                "work_launch_origin_bindings",
                "work_delivery_orders",
                "work_scheduler_requests",
            )
        )


def _admitted_durable_launch(setup, *, idempotency_key, selection):
    provision(setup, selection=selection)
    module, runtime, backend = make_runtime(setup)
    repository, principal, _, _, _, _ = setup
    receipt = runtime.admit_launch(
        principal, runtime.resolve_launch(principal, selection, intent(module), idempotency_key)
    )
    return module, runtime, backend, receipt


def test_missing_or_contradictory_launch_origin_fails_before_any_effect(trusted_setup):
    """WorkContracts never treats absent/contradictory v21 origin evidence as legacy."""
    _, runtime, backend, receipt = _admitted_durable_launch(
        trusted_setup, idempotency_key="missing-origin-binding", selection="missing-origin"
    )
    repository, _, _, _, _, _ = trusted_setup
    with repository.transaction() as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='work_launch_origin_bindings_immutable_delete'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER work_launch_origin_bindings_immutable_delete")
        connection.execute(
            "DELETE FROM work_launch_origin_bindings WHERE attempt_id=? AND generation=?",
            (receipt.attempt_id, receipt.generation),
        )
        connection.execute(trigger)

    with pytest.raises(ContractConflict, match="durable origin binding"):
        runtime._admission.contracts.revalidate_order(
            receipt.attempt_id, generation=receipt.generation
        )
    assert _origin_durable_counts(repository) == (1, 1, 1, 0, 1, 1)
    assert backend.effects == []

    _, runtime, backend, receipt = _admitted_durable_launch(
        trusted_setup,
        idempotency_key="contradictory-origin-marker",
        selection="contradictory-origin",
    )
    with repository.transaction() as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='work_items_origin_protocol_monotonic'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER work_items_origin_protocol_monotonic")
        connection.execute(
            "UPDATE work_items SET origin_protocol='legacy' WHERE id=?", (receipt.work_item_id,)
        )
        connection.execute(trigger)

    with pytest.raises(ContractConflict, match="contradicts its launch origin"):
        runtime._admission.contracts.revalidate_order(
            receipt.attempt_id, generation=receipt.generation
        )
    assert _origin_durable_counts(repository) == (2, 2, 2, 1, 2, 2)
    assert backend.effects == []


@pytest.mark.parametrize("stage", ("work", "origin", "delivery", "enqueue"))
def test_exception_after_each_launch_persistence_step_rolls_back_everything(
    trusted_setup, monkeypatch, stage
):
    """The final admission transaction cannot leave a partially executable launch."""
    provision(trusted_setup)
    module, runtime, backend = make_runtime(trusted_setup)
    repository, principal, _, _, _, _ = trusted_setup
    resolved = runtime.resolve_launch(principal, "opaque", intent(module), f"rollback-{stage}")
    admission = runtime._admission

    if stage == "work":
        original = repository._admit_work

        def fail_after_work(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError(stage)

        monkeypatch.setattr(repository, "_admit_work", fail_after_work)
    elif stage == "origin":
        original = admission._bind_launch_origin

        def fail_after_origin(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError(stage)

        monkeypatch.setattr(admission, "_bind_launch_origin", fail_after_origin)
    elif stage == "delivery":
        original = admission.deliveries._bind

        def fail_after_delivery(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError(stage)

        monkeypatch.setattr(admission.deliveries, "_bind", fail_after_delivery)
    else:
        original = admission.scheduler._enqueue

        def fail_after_enqueue(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError(stage)

        monkeypatch.setattr(admission.scheduler, "_enqueue", fail_after_enqueue)

    with pytest.raises(RuntimeError, match=stage):
        runtime.admit_launch(principal, resolved)

    assert _origin_durable_counts(repository) == (0, 0, 0, 0, 0, 0)
    assert backend.effects == []
