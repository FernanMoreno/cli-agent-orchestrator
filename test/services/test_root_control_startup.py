"""Pre-SQL signature, one-use claim, and compute-only startup tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cli_agent_orchestrator.clients.admission_bootstrap_repository import ResourceEnvelopeRepository
from cli_agent_orchestrator.clients.postgres import DatabaseTarget
from cli_agent_orchestrator.models.resource_envelope import (
    EnvelopeClaims,
    ResourceBinding,
    ResourceEnvelopeRecord,
)
from cli_agent_orchestrator.models.root_control_activation import (
    RootControlFootprint,
    _issue_verified_compute_startup_sample,
    _sign_root_control_activation,
    is_verified_compute_startup_sample,
)
from cli_agent_orchestrator.models.sql_root_genesis import (
    RootSqlOpeningIntent,
    _issue_confirmed_root_sql_opening,
)
from cli_agent_orchestrator.models.sql_startup_permission import (
    SQL_ROOT_GENESIS_ENV,
    sql_startup_binding_sha256,
    sql_startup_destination_sha256,
)
from cli_agent_orchestrator.services import original_control_owner as actor_module
from cli_agent_orchestrator.services import root_control_activation as service
from cli_agent_orchestrator.services.admission_monitor import AdmissionMonitor
from cli_agent_orchestrator.services.admission_policy import (
    AdmissionPolicy,
    CapacityObservation,
    ComputeObservation,
    ResourceAmounts,
    ResourceHeadroom,
    compute_capacity,
)
from cli_agent_orchestrator.services.admission_postgres import PostgresPressureSample
from cli_agent_orchestrator.services.admission_resources import ResourceSample, StorageObservation
from cli_agent_orchestrator.services.admission_settings import AdmissionPolicyLoader
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.services.resource_process_supervisor import _identity
from cli_agent_orchestrator.services.root_sql_control import RootSqlSubject
from cli_agent_orchestrator.utils import process_sql_origin as sql_origin


def _target() -> DatabaseTarget:
    return DatabaseTarget(
        host="127.0.0.1",
        port=5432,
        database="root-control-activation-test",
        user="runtime",
        schema="cao_root_activation_test",
        store_id=str(uuid4()),
        secret_file=Path("/root-control-test/unused-secret.json"),
        sslmode="disable",
    )


def _test_context():
    target = _target()
    live = _identity(os.getpid())
    target_digest = hashlib.sha256(
        json.dumps(target.identity, separators=(",", ":")).encode()
    ).hexdigest()
    binding = ResourceBinding(
        instance_id=str(uuid4()),
        process_generation=str(uuid4()),
        process_pid=os.getpid(),
        process_started_at=live.birth,
        store_id=target.store_id,
        target_identity_sha256=target_digest,
        mount_ns_device=live.mount_ns[0],
        mount_ns_inode=live.mount_ns[1],
        cgroup_ns_device=live.cgroup_ns[0],
        cgroup_ns_inode=live.cgroup_ns[1],
        resource_view_sha256=live.view,
    )
    calls: list[object] = []

    @contextmanager
    def no_connect(selected, **kwargs):
        calls.append((selected, kwargs))
        raise AssertionError("pre-SQL control consumer opened a repository connection")
        yield  # pragma: no cover

    repository = ResourceEnvelopeRepository(
        target_supplier=lambda: target,
        connect_factory=no_connect,
    )
    bootstrap = service.ResourceBootstrap(
        repository=repository,
        binding_supplier=lambda selected: binding,
        instance_supplier=lambda: binding.instance_id,
    )
    label = sql_origin.current_sql_application_name()
    subject = RootSqlSubject(
        binding,
        label,
        live.boot_id,
        live.ticks,
        live.pid_ns,
    )
    private_key = Ed25519PrivateKey.generate()
    credits = tuple(sorted((str(uuid4()), str(uuid4()))))
    record = RootSqlOpeningIntent(
        intent_id=str(uuid4()),
        journal_id=str(uuid4()),
        ordinal=1,
        credit_id=credits[0],
        credit_ids=credits,
        membership_sha256="a" * 64,
        session_id=str(uuid4()),
        subject=binding,
        subject_binding_sha256=sql_startup_binding_sha256(binding),
        subject_sql_application_name=label,
        boot_id=live.boot_id,
        start_time_ticks=live.ticks,
        pid_namespace=live.pid_ns,
        path="native",
        destination_sha256=sql_startup_destination_sha256(target),
        destination_scope="main",
        issuer_principal_id="principal-" + "c" * 64,
    )
    capability = _issue_confirmed_root_sql_opening(record, validate=lambda: None)
    from cli_agent_orchestrator.services.sql_root_genesis import sign_root_opening

    certificate = sign_root_opening(capability, key_id="root-control-test", private_key=private_key)
    envelope = ResourceEnvelopeRecord(
        binding,
        EnvelopeClaims(1_000_000, 2_000, 10, True, False),
        revision=3,
        active=True,
        actor_principal_id="principal-" + "d" * 64,
    )
    activation_cap = _issue_confirmed_root_sql_opening(record, validate=lambda: None)
    packet = _sign_root_control_activation(
        committed=activation_cap,
        certificate=certificate,
        envelope=envelope,
        startup_footprint=RootControlFootprint(2048, 100),
        refresh_footprint=RootControlFootprint(1024, 50),
        key_id="root-control-test",
        private_key=private_key,
    ).to_bytes()
    return target, binding, bootstrap, envelope, packet, private_key, calls


def _samples() -> tuple[ResourceSample, ResourceSample]:
    first = ResourceSample(
        CapacityObservation(
            10.0,
            800_000,
            1_000_000,
            None,
            2_000,
            None,
            "partial",
            memory_domains=(ResourceHeadroom(800_000, 1_000_000),),
            cpu_domains=(),
        ),
        ("storage_unknown", "cpu_warming"),
    )
    second = ResourceSample(
        CapacityObservation(
            11.0,
            800_000,
            1_000_000,
            1_500,
            2_000,
            None,
            "partial",
            memory_domains=(ResourceHeadroom(800_000, 1_000_000),),
            cpu_domains=(ResourceHeadroom(1_500, 2_000),),
        ),
        ("storage_unknown",),
    )
    return first, second


class _ControlledLinuxSampler:
    def __init__(self, *, clock, complete_hierarchy: bool, root_is_host: bool):
        self.clock = clock
        self.complete_hierarchy = complete_hierarchy
        self.root_is_host = root_is_host
        self.samples = list(_samples())
        self.storages: list[StorageObservation] = []

    def sample(self, *, storage: StorageObservation) -> ResourceSample:
        self.storages.append(storage)
        return self.samples.pop(0)


class RootControlStartupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(os.environ, {SQL_ROOT_GENESIS_ENV: "1"}, clear=False)
        self.environment.start()
        self.state = patch.object(sql_origin, "_STATE", sql_origin._State(os.getpid()))
        self.state.start()

    def tearDown(self) -> None:
        self.state.stop()
        self.environment.stop()

    def _loader(self) -> AdmissionPolicyLoader:
        return AdmissionPolicyLoader(
            source=lambda: {
                "version": 1,
                "policy": {"sample_interval_s": 0.5, "max_sample_age_s": 3.0},
            }
        )

    def _pinned_monitor(self, post_readback: list[ResourceEnvelopeRecord | None]):
        target, binding, bootstrap, expected, _, _, _ = _test_context()
        bootstrap._clock = lambda: 10.0
        reads = [expected]
        read_calls: list[object] = []
        load_kwargs: list[dict[str, object]] = []

        def read_latest(selected, instance_id, *, control_session=None):
            read_calls.append((selected, instance_id, control_session))
            source = reads if reads else post_readback
            if not source:
                raise AssertionError("unexpected envelope authority read")
            return source.pop(0)

        bootstrap._repository.read_latest = read_latest
        original_load_current = bootstrap.load_current

        def load_current(**kwargs):
            load_kwargs.append(dict(kwargs))
            return original_load_current(**kwargs)

        bootstrap.load_current = load_current
        owner = DeliveryAdmission(AdmissionPolicy())
        resource_calls: list[StorageObservation] = []

        class Postgres:
            def configure(self, _policy):
                return True

            def sample(self):
                return PostgresPressureSample(StorageObservation(10.0, 10), "pressure_observed")

        class Resources:
            def reset_history(self):
                return None

            def sample(self, *, storage):
                resource_calls.append(storage)
                return ResourceSample(
                    CapacityObservation(
                        10.0, None, None, None, None, storage.available_slots, "unknown"
                    ),
                    (),
                )

        resources = Resources()
        monitor = AdmissionMonitor(
            owner,
            policy_loader=self._loader(),
            bootstrap=bootstrap,
            postgres=Postgres(),
            scope_id="unit",
            clock=lambda: 10.0,
            resource_factory=lambda _attestation: resources,
            expected_envelope=expected,
        )
        initial = bootstrap._repository.read_latest(target, binding.instance_id)
        self.assertEqual(initial, expected)
        return SimpleNamespace(**locals())

    def _assert_monitor_rejects_post_readback_authority_change(
        self,
        monitor_fixture,
    ) -> None:
        result = monitor_fixture.monitor.refresh()
        self.assertEqual(result.reason, "probe_busy")
        self.assertFalse(monitor_fixture.monitor.bootstrap_validated)
        self.assertEqual(monitor_fixture.resource_calls, [])
        self.assertIsNone(monitor_fixture.owner._observation)
        self.assertIsNone(monitor_fixture.owner._budget)
        self.assertEqual(
            monitor_fixture.owner.snapshot(now=10.0).reason,
            "unknown_capacity",
        )

    async def _assert_revalidation_refuses_interval_mutation(self, mutation: str) -> None:
        target, binding, bootstrap, _, packet, private_key, sql_calls = _test_context()
        state = {"changed": False}
        sampler_instances: list[_ControlledLinuxSampler] = []
        original_capture = bootstrap._capture
        original_resolve_target = bootstrap._repository.resolve_target
        original_matches_primary = bootstrap.matches_primary_target
        original_identity = service._identity
        changed_target = replace(target, database=target.database + "-changed")

        def sampler_factory(**kwargs):
            instance = _ControlledLinuxSampler(**kwargs)
            sampler_instances.append(instance)
            return instance

        def capture(selected):
            if state["changed"] and mutation == "binding":
                return replace(binding, process_generation=str(uuid4()))
            return original_capture(selected)

        def resolve_target():
            if state["changed"] and mutation == "target":
                return changed_target
            return original_resolve_target()

        def matches_primary(selected):
            if state["changed"] and mutation == "target":
                return False
            return original_matches_primary(selected)

        def current_identity(pid: int):
            value = original_identity(pid)
            if state["changed"] and mutation == "kernel":
                return replace(
                    value,
                    boot_id=value.boot_id + "-changed",
                    ticks=value.ticks + 1,
                    pid_ns=(value.pid_ns[0], value.pid_ns[1] + 1),
                )
            return value

        async def change_during_interval(delay: float) -> None:
            self.assertEqual(delay, 0.5)
            state["changed"] = True

        with (
            patch.object(service, "LinuxResourceSampler", sampler_factory),
            patch.object(service.asyncio, "sleep", change_during_interval),
            patch.object(bootstrap, "_capture", side_effect=capture),
            patch.object(bootstrap._repository, "resolve_target", side_effect=resolve_target),
            patch.object(bootstrap, "matches_primary_target", side_effect=matches_primary),
            patch.object(service, "_identity", side_effect=current_identity),
            patch.object(
                service,
                "_issue_verified_compute_startup_sample",
                wraps=service._issue_verified_compute_startup_sample,
            ) as sample_issuer,
            patch.object(
                DeliveryAdmission, "_reserve_root_control_startup", autospec=True
            ) as reserve_startup,
            patch.object(service, "OriginalControlOwner") as actor_constructor,
        ):
            with self.assertRaises(service.RootControlActivationUnavailable):
                await service.prepare_original_control_owner(
                    packet=packet,
                    trusted_keys={"root-control-test": private_key.public_key()},
                    bootstrap=bootstrap,
                    policy_loader=self._loader(),
                    scope_id="unit",
                )

            self.assertTrue(sql_origin._STATE.root_control_activation_spent)
            self.assertEqual(len(sampler_instances), 1)
            self.assertEqual(len(sampler_instances[0].storages), 2)
            sample_issuer.assert_not_called()
            reserve_startup.assert_not_called()
            actor_constructor.assert_not_called()
            self.assertEqual(sql_calls, [])

            # Restore the local view to prove the failed activation stayed spent,
            # rather than relying on the same changed binding to reject replay.
            state["changed"] = False
            with self.assertRaises(service.RootControlActivationUnavailable):
                await service.prepare_original_control_owner(
                    packet=packet,
                    trusted_keys={"root-control-test": private_key.public_key()},
                    bootstrap=bootstrap,
                    policy_loader=self._loader(),
                    scope_id="unit",
                )
            self.assertEqual(len(sampler_instances), 1)
            self.assertEqual(len(sampler_instances[0].storages), 2)
            self.assertEqual(sql_calls, [])

    async def test_binding_change_during_sampling_refuses_startup_and_keeps_replay_spent(
        self,
    ) -> None:
        await self._assert_revalidation_refuses_interval_mutation("binding")

    async def test_kernel_identity_change_during_sampling_refuses_startup_and_keeps_replay_spent(
        self,
    ) -> None:
        await self._assert_revalidation_refuses_interval_mutation("kernel")

    async def test_primary_target_change_during_sampling_refuses_startup_and_keeps_replay_spent(
        self,
    ) -> None:
        await self._assert_revalidation_refuses_interval_mutation("target")

    def test_expected_envelope_change_before_first_monitor_authority_read_fails_before_sampler(
        self,
    ) -> None:
        f = self._pinned_monitor([])
        f.post_readback.append(replace(f.expected, revision=f.expected.revision + 1))
        self._assert_monitor_rejects_post_readback_authority_change(f)

    def test_expected_envelope_change_before_second_monitor_authority_read_fails_before_sampler(
        self,
    ) -> None:
        f = self._pinned_monitor([])
        f.post_readback.extend(
            (
                f.expected,
                replace(f.expected, revision=f.expected.revision + 1),
            )
        )
        self._assert_monitor_rejects_post_readback_authority_change(f)

    def test_unknown_first_probe_is_ready_only_after_expected_envelope_reads_validate(self) -> None:
        f = self._pinned_monitor([])
        f.post_readback.extend((f.expected, f.expected, f.expected))

        result = f.monitor.refresh()

        self.assertEqual(result.reason, "unknown_capacity")
        self.assertFalse(result.published)
        self.assertTrue(f.monitor.bootstrap_validated)
        self.assertEqual(len(f.resource_calls), 1)
        self.assertIsNone(f.owner._observation)
        self.assertEqual(len(f.read_calls), 4)  # Actor readback plus three cycle checks.
        self.assertEqual(
            f.load_kwargs,
            [{"expected_envelope": f.expected}] * 3,
        )

        updated = replace(
            f.expected,
            revision=f.expected.revision + 1,
            claims=replace(
                f.expected.claims,
                memory_budget_bytes=f.expected.claims.memory_budget_bytes + 1,
            ),
        )
        f.post_readback.extend((updated, updated))
        next_cycle = f.monitor.refresh()
        self.assertEqual(next_cycle.reason, "unknown_capacity")
        self.assertTrue(next_cycle.published is False)
        self.assertEqual(f.load_kwargs[-2:], [{}, {}])
        self.assertEqual(f.owner._budget.memory_bytes, updated.claims.memory_budget_bytes)

    async def test_wrong_deployment_key_fails_before_sampler_or_sql(self) -> None:
        _, _, bootstrap, _, packet, _, sql_calls = _test_context()
        sampler_calls: list[object] = []
        wrong = Ed25519PrivateKey.generate().public_key()
        with patch.object(
            service,
            "LinuxResourceSampler",
            lambda **kwargs: sampler_calls.append(kwargs),
        ):
            with self.assertRaises(service.RootControlActivationUnavailable):
                await service.prepare_original_control_owner(
                    packet=packet,
                    trusted_keys={"root-control-test": wrong},
                    bootstrap=bootstrap,
                    policy_loader=self._loader(),
                    scope_id="unit",
                )
        self.assertEqual(sampler_calls, [])
        self.assertEqual(sql_calls, [])
        self.assertFalse(sql_origin._STATE.root_control_activation_spent)

    async def test_valid_prepare_charges_only_startup_compute_and_keeps_storage_unknown(
        self,
    ) -> None:
        _, _, bootstrap, envelope, packet, private_key, sql_calls = _test_context()
        sampler_instances: list[_ControlledLinuxSampler] = []
        sleeps: list[float] = []

        def sampler_factory(**kwargs):
            instance = _ControlledLinuxSampler(**kwargs)
            sampler_instances.append(instance)
            return instance

        async def controlled_sleep(delay: float) -> None:
            sleeps.append(delay)

        clock_values = iter((10.0, 11.0, 11.1, 11.2))
        with (
            patch.object(service, "LinuxResourceSampler", sampler_factory),
            patch.object(service.asyncio, "sleep", controlled_sleep),
        ):
            actor = await service.prepare_original_control_owner(
                packet=packet,
                trusted_keys={"root-control-test": private_key.public_key()},
                bootstrap=bootstrap,
                policy_loader=self._loader(),
                scope_id="unit",
                clock=lambda: next(clock_values),
            )
        owner = actor._config.owner
        self.assertIsNone(owner._observation)
        self.assertEqual(actor._config.expected_envelope, envelope)
        self.assertEqual(sleeps, [0.5])
        self.assertEqual(len(sampler_instances), 1)
        self.assertTrue(
            all(sample.available_slots is None for sample in sampler_instances[0].storages)
        )
        self.assertEqual(len(sampler_instances[0].storages), 2)
        self.assertTrue(owner.snapshot(now=11.2).reason == "unknown_capacity")
        self.assertEqual(actor._startup.footprint, ResourceAmounts(2048, 100, 0))
        self.assertEqual(actor._startup.state, "reserved")
        normal = owner.try_reserve_command(footprint=ResourceAmounts(1, 1, 0), now=11.2)
        self.assertIsNone(normal.reservation)
        self.assertTrue(sql_origin._STATE.root_control_activation_spent)
        self.assertEqual(sql_calls, [])
        await actor.aclose()

    async def test_hidden_ancestor_or_unknown_compute_never_creates_startup_charge(self) -> None:
        _, _, bootstrap, _, packet, private_key, _ = _test_context()
        sampler_instances: list[_ControlledLinuxSampler] = []

        class BadSampler(_ControlledLinuxSampler):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                sample = self.samples[1]
                self.samples[1] = ResourceSample(
                    sample.observation,
                    ("storage_unknown", "hidden_ancestors"),
                )
                sampler_instances.append(self)

        clock_values = iter((20.0, 21.0, 21.1))

        async def controlled_sleep(delay: float) -> None:
            return None

        with (
            patch.object(service, "LinuxResourceSampler", BadSampler),
            patch.object(service.asyncio, "sleep", controlled_sleep),
        ):
            with self.assertRaises(service.RootControlActivationUnavailable):
                await service.prepare_original_control_owner(
                    packet=packet,
                    trusted_keys={"root-control-test": private_key.public_key()},
                    bootstrap=bootstrap,
                    policy_loader=self._loader(),
                    scope_id="unit",
                    clock=lambda: next(clock_values),
                )
        self.assertEqual(len(sampler_instances), 1)
        self.assertTrue(sql_origin._STATE.root_control_activation_spent)

    async def test_activation_replay_is_rejected_before_second_sampler(self) -> None:
        _, _, bootstrap, _, packet, private_key, _ = _test_context()
        factories: list[object] = []
        sleeps: list[float] = []

        def sampler_factory(**kwargs):
            value = _ControlledLinuxSampler(**kwargs)
            factories.append(value)
            return value

        async def controlled_sleep(delay: float) -> None:
            sleeps.append(delay)

        clock_values = iter((10.0, 11.0, 11.1))
        with (
            patch.object(service, "LinuxResourceSampler", sampler_factory),
            patch.object(service.asyncio, "sleep", controlled_sleep),
        ):
            actor = await service.prepare_original_control_owner(
                packet=packet,
                trusted_keys={"root-control-test": private_key.public_key()},
                bootstrap=bootstrap,
                policy_loader=self._loader(),
                scope_id="unit",
                clock=lambda: next(clock_values),
            )
            with self.assertRaises(service.RootControlActivationUnavailable):
                await service.prepare_original_control_owner(
                    packet=packet,
                    trusted_keys={"root-control-test": private_key.public_key()},
                    bootstrap=bootstrap,
                    policy_loader=self._loader(),
                    scope_id="unit",
                    clock=lambda: next(clock_values),
                )
        self.assertEqual(len(factories), 1)
        self.assertEqual(sleeps, [0.5])
        await actor.aclose()

    async def test_stale_compute_basis_refuses_native_start_without_sql(self) -> None:
        _, _, bootstrap, _, packet, private_key, sql_calls = _test_context()

        async def controlled_sleep(delay: float) -> None:
            return None

        clock_values = iter((10.0, 11.0, 11.1, 14.1))
        with (
            patch.object(service, "LinuxResourceSampler", _ControlledLinuxSampler),
            patch.object(service.asyncio, "sleep", controlled_sleep),
        ):
            actor = await service.prepare_original_control_owner(
                packet=packet,
                trusted_keys={"root-control-test": private_key.public_key()},
                bootstrap=bootstrap,
                policy_loader=self._loader(),
                scope_id="unit",
                clock=lambda: next(clock_values),
            )

        with self.assertRaises(actor_module.OriginalControlUnavailable):
            actor.start()
        self.assertIsNone(actor._worker)
        self.assertEqual(actor._startup.state, "released")
        self.assertEqual(sql_calls, [])
        await actor.aclose()

    def test_raw_or_cross_activation_compute_data_cannot_reserve_startup(self) -> None:
        _, _, bootstrap, _, packet, private_key, _ = _test_context()
        raw_observation = ComputeObservation(
            10.0,
            100,
            200,
            100,
            200,
            (ResourceHeadroom(100, 200),),
            (ResourceHeadroom(100, 200),),
        )
        raw_owner, _ = DeliveryAdmission.with_storage_control(AdmissionPolicy())
        raw_attempt = raw_owner._reserve_root_control_startup(
            object(),
            compute_sample=raw_observation,
            now=10.0,
        )
        self.assertIsNone(raw_attempt.reservation)
        self.assertEqual(raw_owner.diagnostic(now=10.0).snapshot.command_inflight, 0)

        first_activation, first_proof = service._verify_activation(
            packet,
            trusted_keys={"root-control-test": private_key.public_key()},
            bootstrap=bootstrap,
        )
        second_activation, second_proof = service._verify_activation(
            packet,
            trusted_keys={"root-control-test": private_key.public_key()},
            bootstrap=bootstrap,
        )
        self.assertIsNot(first_activation, second_activation)
        self.assertTrue(first_proof._claim_startup())
        self.assertTrue(second_proof._claim_startup())
        compute_sample = _issue_verified_compute_startup_sample(
            first_proof,
            observation=raw_observation,
        )
        self.assertTrue(is_verified_compute_startup_sample(compute_sample))
        owner, _ = DeliveryAdmission.with_storage_control(AdmissionPolicy())
        attempt = owner._reserve_root_control_startup(
            second_proof,
            compute_sample=compute_sample,
            now=10.0,
        )
        self.assertIsNone(attempt.reservation)
        self.assertEqual(owner.diagnostic(now=10.0).snapshot.command_inflight, 0)

        if not hasattr(os, "fork"):
            self.skipTest("process-local proof fork check requires POSIX fork")
        read_fd, write_fd = os.pipe()
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            os.write(write_fd, b"1" if is_verified_compute_startup_sample(compute_sample) else b"0")
            os.close(write_fd)
            os._exit(0)
        os.close(write_fd)
        child_result = os.read(read_fd, 1)
        os.close(read_fd)
        _, status = os.waitpid(child, 0)
        self.assertEqual(status, 0)
        self.assertEqual(child_result, b"0")

    async def test_reconfigure_generation_cancels_startup_basis_without_refund_rearm(self) -> None:
        _, _, bootstrap, _, packet, private_key, _ = _test_context()

        async def controlled_sleep(delay: float) -> None:
            return None

        clock_values = iter((10.0, 11.0, 11.1, 12.0))
        with (
            patch.object(service, "LinuxResourceSampler", _ControlledLinuxSampler),
            patch.object(service.asyncio, "sleep", controlled_sleep),
        ):
            actor = await service.prepare_original_control_owner(
                packet=packet,
                trusted_keys={"root-control-test": private_key.public_key()},
                bootstrap=bootstrap,
                policy_loader=self._loader(),
                scope_id="unit",
                clock=lambda: next(clock_values),
            )
        owner = actor._config.owner
        generation = owner.generation
        changed = AdmissionPolicy(max_sample_age_s=2.5)
        self.assertEqual(owner.reconfigure(changed, expected_generation=generation), generation + 1)
        self.assertEqual(actor._startup.state, "released")
        with self.assertRaises(actor_module.OriginalControlUnavailable):
            actor.start()
        self.assertEqual(actor._startup.state, "released")
        await actor.aclose()

    async def test_expected_envelope_is_read_on_first_session_before_monitor_creation(self) -> None:
        _, _, bootstrap, envelope, packet, private_key, _ = _test_context()

        async def controlled_sleep(delay: float) -> None:
            return None

        clock_values = iter((10.0, 11.0, 11.1))
        with (
            patch.object(service, "LinuxResourceSampler", _ControlledLinuxSampler),
            patch.object(service.asyncio, "sleep", controlled_sleep),
        ):
            actor = await service.prepare_original_control_owner(
                packet=packet,
                trusted_keys={"root-control-test": private_key.public_key()},
                bootstrap=bootstrap,
                policy_loader=self._loader(),
                scope_id="unit",
                clock=lambda: next(clock_values),
            )

        events: list[object] = []
        changed = ResourceEnvelopeRecord(
            envelope.binding,
            envelope.claims,
            envelope.revision + 1,
            envelope.active,
            envelope.actor_principal_id,
        )

        class Session:
            def __init__(self, channel):
                self.channel = channel

            def __enter__(self):
                events.append(("session_enter", self.channel))
                return self

            def __exit__(self, exc_type, exc, traceback):
                events.append(("session_exit", self.channel))
                return False

        def read_latest(target, instance_id, *, control_session):
            events.append(("read", target, instance_id, control_session))
            return changed

        with (
            patch.object(actor_module, "FundedControlSession", Session),
            patch.object(bootstrap._repository, "read_latest", read_latest),
            patch.object(bootstrap, "_capture", return_value=envelope.binding),
            patch.object(actor_module, "AdmissionMonitor", side_effect=AssertionError),
        ):
            with self.assertRaises(actor_module.OriginalControlUnavailable):
                actor._run_session(object())
        self.assertEqual([entry[0] for entry in events], ["session_enter", "read", "session_exit"])
        self.assertIs(events[1][3].channel, events[0][1])
        await actor.aclose()

    async def test_owner_does_not_report_ready_before_monitor_bootstrap_validation(self) -> None:
        _, _, bootstrap, envelope, packet, private_key, _ = _test_context()

        async def controlled_sleep(_delay: float) -> None:
            return None

        with (
            patch.object(service, "LinuxResourceSampler", _ControlledLinuxSampler),
            patch.object(service.asyncio, "sleep", controlled_sleep),
        ):
            actor = await service.prepare_original_control_owner(
                packet=packet,
                trusted_keys={"root-control-test": private_key.public_key()},
                bootstrap=bootstrap,
                policy_loader=self._loader(),
                scope_id="unit",
                clock=lambda: 11.0,
            )

        class Session:
            def __init__(self, channel):
                self.channel = channel

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        class UnvalidatedMonitor:
            bootstrap_validated = False

            def refresh(self):
                return None

        def read_latest(target, instance_id, *, control_session):
            self.assertEqual(instance_id, envelope.binding.instance_id)
            self.assertIsNotNone(control_session)
            return envelope

        with (
            patch.object(actor_module, "FundedControlSession", Session),
            patch.object(bootstrap._repository, "read_latest", read_latest),
            patch.object(bootstrap, "_capture", return_value=envelope.binding),
            patch.object(actor_module, "AdmissionMonitor", return_value=UnvalidatedMonitor()),
        ):
            with self.assertRaises(actor_module.OriginalControlUnavailable):
                actor._run_session(object())
        self.assertFalse(actor._ready.done())
        await actor.aclose()

    async def test_unknown_capacity_can_report_ready_after_validated_bootstrap_cycle(self) -> None:
        _, _, bootstrap, envelope, packet, private_key, _ = _test_context()

        async def controlled_sleep(_delay: float) -> None:
            return None

        with (
            patch.object(service, "LinuxResourceSampler", _ControlledLinuxSampler),
            patch.object(service.asyncio, "sleep", controlled_sleep),
        ):
            actor = await service.prepare_original_control_owner(
                packet=packet,
                trusted_keys={"root-control-test": private_key.public_key()},
                bootstrap=bootstrap,
                policy_loader=self._loader(),
                scope_id="unit",
                clock=lambda: 11.0,
            )

        class Session:
            def __init__(self, channel):
                self.channel = channel

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        class ValidatedUnknownMonitor:
            bootstrap_validated = True

            def refresh(self):
                return SimpleNamespace(reason="unknown_capacity", published=False)

        def read_latest(target, instance_id, *, control_session):
            self.assertEqual(instance_id, envelope.binding.instance_id)
            self.assertIsNotNone(control_session)
            return envelope

        with (
            patch.object(actor_module, "FundedControlSession", Session),
            patch.object(bootstrap._repository, "read_latest", read_latest),
            patch.object(bootstrap, "_capture", return_value=envelope.binding),
            patch.object(actor_module, "AdmissionMonitor", return_value=ValidatedUnknownMonitor()),
            patch.object(actor, "_serve", lambda _monitor: None),
        ):
            actor._run_session(object())
        self.assertTrue(actor._ready.done())
        self.assertIsNone(actor._ready.exception())
        await actor.aclose()

    async def test_startup_basis_requires_exact_expected_envelope_configuration(self) -> None:
        _, _, bootstrap, envelope, packet, private_key, _ = _test_context()

        async def controlled_sleep(delay: float) -> None:
            return None

        clock_values = iter((10.0, 11.0, 11.1))
        with (
            patch.object(service, "LinuxResourceSampler", _ControlledLinuxSampler),
            patch.object(service.asyncio, "sleep", controlled_sleep),
        ):
            actor = await service.prepare_original_control_owner(
                packet=packet,
                trusted_keys={"root-control-test": private_key.public_key()},
                bootstrap=bootstrap,
                policy_loader=self._loader(),
                scope_id="unit",
                clock=lambda: next(clock_values),
            )

        candidates = (
            None,
            replace(envelope, revision=envelope.revision + 1),
        )
        for expected in candidates:
            with self.subTest(expected=expected):
                config = replace(actor._config, expected_envelope=expected)
                with self.assertRaises(actor_module.OriginalControlUnavailable):
                    actor_module.OriginalControlOwner(config, startup=actor._startup)

        wrong_cost = replace(
            actor._config,
            command_footprint=ResourceAmounts(1, 1, 0),
        )
        changed_record = replace(
            actor._config.certificate.record,
            session_id=str(uuid4()),
        )
        changed_certificate = service.sign_root_opening(
            _issue_confirmed_root_sql_opening(changed_record, validate=lambda: None),
            key_id="root-control-test",
            private_key=private_key,
        )
        wrong_certificate = replace(actor._config, certificate=changed_certificate)
        for config in (wrong_cost, wrong_certificate):
            with self.subTest(config=config):
                with self.assertRaises(actor_module.OriginalControlUnavailable):
                    actor_module.OriginalControlOwner(config, startup=actor._startup)
        original_startup_footprint = actor._startup._footprint
        object.__setattr__(actor._startup, "_footprint", ResourceAmounts(1, 1, 0))
        try:
            with self.assertRaises(actor_module.OriginalControlUnavailable):
                actor_module.OriginalControlOwner(actor._config, startup=actor._startup)
        finally:
            object.__setattr__(actor._startup, "_footprint", original_startup_footprint)
        self.assertEqual(actor._startup.state, "reserved")
        await actor.aclose()

    async def test_fork_successor_cannot_rearm_root_control_activation(self) -> None:
        successor = sql_origin._successor(sql_origin._State(os.getpid()), os.getpid() + 100)
        self.assertTrue(successor.root_control_activation_spent)


class ComputeOnlyPolicyTests(unittest.TestCase):
    def test_compute_margin_and_signed_ceiling_do_not_add_storage(self) -> None:
        observation = ComputeObservation(
            10.0,
            700,
            900,
            1_500,
            2_000,
            (ResourceHeadroom(800, 1_000), ResourceHeadroom(700, 900)),
            (ResourceHeadroom(1_500, 2_000),),
        )
        result = compute_capacity(
            AdmissionPolicy(
                memory_reserve_percent=10,
                cpu_reserve_percent=20,
                max_sample_age_s=2.0,
            ),
            observation,
            now=11.0,
            budget_memory_bytes=650,
            budget_cpu_millicores=1_000,
        )
        self.assertEqual(result, ResourceAmounts(610, 1_000, 0))
        self.assertIsNone(
            compute_capacity(
                AdmissionPolicy(max_sample_age_s=1.0),
                observation,
                now=11.1,
                budget_memory_bytes=1_000,
                budget_cpu_millicores=2_000,
            )
        )


if __name__ == "__main__":
    unittest.main()
