"""Offline tests for the owner-funded Root deployment runtime."""

from __future__ import annotations

import asyncio
import copy
import math
import pickle
import threading
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from cli_agent_orchestrator.clients.admission_bootstrap_repository import ResourceEnvelopeRepository
from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.models.sql_root_subject import RootSqlSubject
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services import root_deployment_runtime as module
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_policy import (
    AdmissionPolicy,
    CapacityObservation,
    ResourceAmounts,
)
from cli_agent_orchestrator.services.delivery_admission import (
    CommandReservation,
    DeliveryAdmission,
)

_FOOTPRINT = ResourceAmounts(1024, 10, 0)
pytestmark = pytest.mark.offline_database


def _fixture(*, key_supplier=None, clock=None):
    target = DatabaseTarget(
        host="127.0.0.1",
        port=5432,
        database="root-deployment-runtime-test",
        user="runtime",
        schema="cao_root_deployment_runtime_test",
        store_id=str(uuid4()),
        secret_file=Path("/unused/runtime-secret.json"),
        sslmode="disable",
        migration_user="bootstrap",
        migration_secret_file=Path("/unused/bootstrap-secret.json"),
    )
    repository = ResourceEnvelopeRepository(target_supplier=lambda: target)
    bootstrap = ResourceBootstrap(repository=repository)
    policy = AdmissionPolicy(
        memory_per_delivery_bytes=1,
        cpu_per_delivery_millicores=1,
        memory_reserve_percent=0,
        cpu_reserve_percent=0,
        max_sample_age_s=5.0,
    )
    owner, control = DeliveryAdmission.with_storage_control(policy)
    assert owner.observe(
        CapacityObservation(
            observed_at=1.0,
            memory_available_bytes=100_000,
            memory_limit_bytes=100_000,
            cpu_available_millicores=10_000,
            cpu_limit_millicores=10_000,
            storage_available_slots=0,
            coverage="known",
        ),
        now=1.0,
        generation=owner.generation,
    )
    principal = auth._verified_principal(
        "urn:caos:runtime-test", "operator-a", [auth.SCOPE_ADMIN], "jwt"
    )
    subject = RootSqlSubject(
        ResourceBinding(
            instance_id=str(uuid4()),
            process_generation=str(uuid4()),
            process_pid=1,
            process_started_at=1.0,
            store_id=target.store_id,
            target_identity_sha256="a" * 64,
            mount_ns_device=0,
            mount_ns_inode=1,
            cgroup_ns_device=0,
            cgroup_ns_inode=2,
            resource_view_sha256="b" * 64,
        ),
        "caos-sql-test",
        "boot-id",
        1,
        (0, 1),
    )
    config = module.RootDeploymentConfig(
        bootstrap=bootstrap,
        owner=owner,
        control=control,
        target=target,
        argv=("trusted-root", "--private-control"),
        env={"CAO_TEST_PRIVATE": "fixed"},
        key_supplier=key_supplier or (lambda: ("test-key", object())),
        worker_footprint=_FOOTPRINT,
        child_footprint=_FOOTPRINT,
        command_footprint=_FOOTPRINT,
        startup_footprint=_FOOTPRINT,
        refresh_footprint=_FOOTPRINT,
        clock=clock or (lambda: 1.0),
        timeout_s=2.0,
    )
    return config, principal, subject, owner


def _connection(config, *, close_hook=None):
    connection = Connection.__new__(Connection)
    connection.target = config.target.migration_target()
    connection._funded_target = connection.target
    connection._storage_pid = __import__("os").getpid()
    connection._owns_raw = True
    connection._storage_loan = object()
    connection.raw = SimpleNamespace(closed=False)
    connection.close_calls = 0

    def check_funded_worker():
        config.control.check_current_operation()

    def close():
        config.control.check_current_operation()
        connection.close_calls += 1
        if close_hook is not None and close_hook(connection):
            return
        connection.raw.closed = True
        connection._storage_loan = None

    connection.close = close
    connection._check_funded_worker = check_funded_worker
    return connection


def _fake_deployment(subject):
    class FakeDeployment:
        events: list[tuple[object, ...]] = []
        join_result = True
        cleanup_result = True
        call_hook = None

        @classmethod
        def reset(cls):
            cls.events = []
            cls.join_result = True
            cls.cleanup_result = True
            cls.call_hook = None

        @classmethod
        def launch(cls, **kwargs):
            kwargs["channel"]._check_funded_worker()
            reservation = kwargs["reservation"]
            if not reservation.start(now=kwargs["clock"]()):
                raise AssertionError("child reservation did not start on its owner thread")
            instance = cls()
            instance.subject = subject
            instance.record = None
            instance.channel = kwargs["channel"]
            instance.reservation = reservation
            cls.events.append(("launch", threading.current_thread(), kwargs))
            return instance

        def activate(
            self,
            *,
            journal_id,
            intent_id,
            session_id,
            key_supplier,
            startup_footprint,
            refresh_footprint,
            timeout_s,
        ):
            self.channel._check_funded_worker()
            type(self).events.append(
                ("activate", threading.current_thread(), journal_id, intent_id, session_id)
            )
            key_supplier()
            hook = type(self).call_hook
            if hook is not None:
                hook()

        def refresh(self, *, timeout_s):
            self.channel._check_funded_worker()
            type(self).events.append(("refresh", threading.current_thread()))
            return "capacity_available"

        def stop(self, *, timeout_s):
            self.channel._check_funded_worker()
            type(self).events.append(("stop", threading.current_thread()))

        def join(self, timeout_s=None):
            self.channel._check_funded_worker()
            type(self).events.append(("join", threading.current_thread()))
            if not type(self).join_result:
                return False
            return self.reservation.finish()

        def cleanup(self, timeout_s=None):
            type(self).events.append(("cleanup", threading.current_thread()))
            return type(self).cleanup_result

    return FakeDeployment


class RootDeploymentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_config_is_frozen_snapshotted_and_footprints_are_positive_ram_cpu_only(self):
        config, _, _, _ = _fixture()
        self.assertEqual(config.argv, ("trusted-root", "--private-control"))
        self.assertIsInstance(config.env, MappingProxyType)
        self.assertEqual(dict(config.env), {"CAO_TEST_PRIVATE": "fixed"})
        with self.assertRaises(TypeError):
            config.env["CAO_TEST_PRIVATE"] = "changed"
        with self.assertRaises(FrozenInstanceError):
            config.timeout_s = 1.0
        for invalid in (0.0, -1.0, 30.1, math.inf, math.nan):
            with self.subTest(timeout=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    replace(config, timeout_s=invalid)
        with self.assertRaises(ValueError):
            replace(config, child_footprint=ResourceAmounts(1024, 10, 1))

    def test_runtime_is_untransferable_and_install_claim_is_one_shot(self):
        config, principal, _, _ = _fixture()
        runtime = module.RootDeploymentRuntime(config)
        self.assertTrue(runtime.installable)
        self.assertEqual(runtime.status(principal=principal).phase, "idle")
        with self.assertRaises(TypeError):
            copy.copy(runtime)
        with self.assertRaises(TypeError):
            copy.deepcopy(runtime)
        with self.assertRaises(TypeError):
            pickle.dumps(runtime)
        runtime.claim_installation()
        self.assertFalse(runtime.installable)
        with self.assertRaises(module.RootDeploymentRuntimeUnavailable):
            runtime.claim_installation()

    async def test_unverified_admin_fails_before_clock_or_sql(self):
        clock_calls = []
        config, _, _, _ = _fixture(clock=lambda: clock_calls.append("clock") or 1.0)
        runtime = module.RootDeploymentRuntime(config)
        with patch.object(module, "connect", side_effect=AssertionError("SQL opened")):
            with self.assertRaises(module.RootDeploymentOperatorMismatch):
                await runtime.launch(principal=object())
        self.assertEqual(clock_calls, [])
        self.assertTrue(runtime.installable)

    async def test_single_worker_original_connection_and_pre_activation_controls(self):
        config, principal, subject, _ = _fixture()
        runtime = module.RootDeploymentRuntime(config)
        connection = _connection(config)
        Fake = _fake_deployment(subject)
        Fake.reset()
        opened: list[tuple[threading.Thread, DatabaseTarget]] = []

        def connect_once(target):
            opened.append((threading.current_thread(), target))
            return connection

        with (
            patch.object(module, "connect", side_effect=connect_once),
            patch.object(module, "RootControlDeployment", Fake),
        ):
            launched = await runtime.launch(principal=principal)
            self.assertEqual(launched.phase, "awaiting_authority")
            self.assertIs(launched.subject, subject)
            with self.assertRaises(module.RootDeploymentRuntimeUnavailable):
                await runtime.refresh(principal=principal)
            with self.assertRaises(module.RootDeploymentRuntimeUnavailable):
                await runtime.stop(principal=principal)
            self.assertEqual([event[0] for event in Fake.events], ["launch"])
            ready = await runtime.activate(
                principal=principal,
                journal_id=str(uuid4()),
                intent_id=str(uuid4()),
                session_id=str(uuid4()),
            )
            self.assertEqual(ready.phase, "ready")
            refreshed = await runtime.refresh(principal=principal)
            self.assertEqual(refreshed.phase, "ready")
            stopped = await runtime.stop(principal=principal)
            self.assertEqual(stopped.phase, "stopping")
            await runtime.aclose(timeout=2.0)
            self.assertEqual(runtime.status(principal=principal).phase, "closed")

        self.assertEqual(len(opened), 1)
        worker_thread = opened[0][0]
        self.assertIsNot(worker_thread, threading.current_thread())
        self.assertFalse(worker_thread.daemon)
        self.assertTrue(all(event[1] is worker_thread for event in Fake.events))
        self.assertEqual(opened[0][1].identity, config.target.migration_target().identity)
        self.assertTrue(connection.raw.closed)
        self.assertEqual(connection.close_calls, 1)

    async def test_deployment_handle_is_retained_before_subject_accessor(self):
        config, principal, subject, _ = _fixture()
        runtime = module.RootDeploymentRuntime(config)
        connection = _connection(config)
        BaseFake = _fake_deployment(subject)

        class SubjectAccessorFailure(BaseFake):
            created = None

            @property
            def subject(self):
                raise RuntimeError("subject accessor failed")

            @subject.setter
            def subject(self, value):
                self._subject_value = value

            @property
            def record(self):
                return None

            @record.setter
            def record(self, value):
                self._record_value = value

            @classmethod
            def launch(cls, **kwargs):
                deployment = super().launch(**kwargs)
                cls.created = deployment
                return deployment

        with (
            patch.object(module, "connect", return_value=connection),
            patch.object(module, "RootControlDeployment", SubjectAccessorFailure),
        ):
            with self.assertRaises(module.RootDeploymentRuntimeUnavailable):
                await runtime.launch(principal=principal)
            self.assertIs(runtime._deployment, SubjectAccessorFailure.created)
            self.assertFalse(runtime._child_closed)
            await runtime.aclose(timeout=2.0)
        self.assertTrue(connection.raw.closed)
        self.assertEqual(runtime.status(principal=principal).phase, "closed")

    async def test_reentrant_key_callback_and_other_operator_cannot_touch_worker(self):
        nested_rejections: list[threading.Thread] = []
        runtime_ref = []
        principal_holder = []

        def key_supplier():
            runtime = runtime_ref[0]
            principal = principal_holder[0]
            try:
                asyncio.run(runtime.refresh(principal=principal))
            except module.RootDeploymentRuntimeUnavailable:
                nested_rejections.append(threading.current_thread())
            return "test-key", object()

        config, principal, subject, _ = _fixture(key_supplier=key_supplier)
        runtime = module.RootDeploymentRuntime(config)
        runtime_ref.append(runtime)
        principal_holder.append(principal)
        connection = _connection(config)
        Fake = _fake_deployment(subject)
        Fake.reset()
        other = auth._verified_principal(
            "urn:caos:runtime-test", "operator-b", [auth.SCOPE_ADMIN], "jwt"
        )
        with (
            patch.object(module, "connect", return_value=connection),
            patch.object(module, "RootControlDeployment", Fake),
        ):
            await runtime.launch(principal=principal)
            with self.assertRaises(module.RootDeploymentOperatorMismatch):
                await runtime.refresh(principal=other)
            await runtime.activate(
                principal=principal,
                journal_id=str(uuid4()),
                intent_id=str(uuid4()),
                session_id=str(uuid4()),
            )
            self.assertEqual(len(nested_rejections), 1)
            self.assertIsNot(nested_rejections[0], threading.current_thread())
            self.assertNotIn("refresh", [event[0] for event in Fake.events])
            await runtime.aclose(timeout=2.0)

    async def test_aclose_latches_while_active_command_finishes_without_new_controls(self):
        config, principal, subject, _ = _fixture()
        runtime = module.RootDeploymentRuntime(config)
        connection = _connection(config)
        Fake = _fake_deployment(subject)
        Fake.reset()
        entered = threading.Event()
        release = threading.Event()

        def hook():
            entered.set()
            release.wait(timeout=3.0)

        Fake.call_hook = hook
        with (
            patch.object(module, "connect", return_value=connection),
            patch.object(module, "RootControlDeployment", Fake),
        ):
            await runtime.launch(principal=principal)
            activate = asyncio.create_task(
                runtime.activate(
                    principal=principal,
                    journal_id=str(uuid4()),
                    intent_id=str(uuid4()),
                    session_id=str(uuid4()),
                )
            )
            self.assertTrue(await asyncio.to_thread(entered.wait, 2.0))
            closing = asyncio.create_task(runtime.aclose(timeout=2.0))
            await asyncio.sleep(0)
            with self.assertRaises(module.RootDeploymentRuntimeUnavailable):
                await runtime.refresh(principal=principal)
            release.set()
            self.assertEqual((await activate).phase, "ready")
            await closing
            self.assertEqual(runtime.status(principal=principal).phase, "closed")
        self.assertEqual([event[0] for event in Fake.events].count("launch"), 1)
        self.assertEqual([event[0] for event in Fake.events].count("refresh"), 0)

    async def test_canceled_waiter_retains_command_and_trusted_close_retries(self):
        config, principal, subject, owner = _fixture()
        runtime = module.RootDeploymentRuntime(config)
        connection = _connection(config)
        Fake = _fake_deployment(subject)
        Fake.reset()
        entered = threading.Event()
        release = threading.Event()

        def hook():
            entered.set()
            release.wait(timeout=3.0)

        Fake.call_hook = hook
        with (
            patch.object(module, "connect", return_value=connection),
            patch.object(module, "RootControlDeployment", Fake),
        ):
            await runtime.launch(principal=principal)
            activate = asyncio.create_task(
                runtime.activate(
                    principal=principal,
                    journal_id=str(uuid4()),
                    intent_id=str(uuid4()),
                    session_id=str(uuid4()),
                )
            )
            self.assertTrue(await asyncio.to_thread(entered.wait, 2.0))
            before = owner.snapshot(now=1.0).command_inflight
            activate.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await activate
            self.assertEqual(owner.snapshot(now=1.0).command_inflight, before)
            with self.assertRaises(module.RootDeploymentRuntimeUnavailable):
                await runtime.refresh(principal=principal)
            closing = asyncio.create_task(runtime.aclose(timeout=2.0))
            release.set()
            await closing
            self.assertEqual(runtime.status(principal=principal).phase, "closed")

    async def test_shutdown_uses_existing_worker_credit_after_owner_stops_accepting(self):
        config, principal, subject, owner = _fixture()
        runtime = module.RootDeploymentRuntime(config)
        connection = _connection(config)
        Fake = _fake_deployment(subject)
        Fake.reset()
        with (
            patch.object(module, "connect", return_value=connection),
            patch.object(module, "RootControlDeployment", Fake),
            patch.object(owner, "try_reserve_command", wraps=owner.try_reserve_command) as reserve,
        ):
            await runtime.launch(principal=principal)
            await runtime.activate(
                principal=principal,
                journal_id=str(uuid4()),
                intent_id=str(uuid4()),
                session_id=str(uuid4()),
            )
            reservations_before_close = reserve.call_count
            owner.stop_accepting()
            await runtime.aclose(timeout=2.0)
        self.assertEqual(runtime.status(principal=principal).phase, "closed")
        self.assertEqual(reserve.call_count, reservations_before_close)
        self.assertEqual(owner.snapshot(now=1.0).command_started, 0)
        self.assertEqual(owner.snapshot(now=1.0).command_inflight, 0)

    async def test_child_cleanup_success_without_join_keeps_runtime_and_charges(self):
        config, principal, subject, owner = _fixture()
        runtime = module.RootDeploymentRuntime(config)
        connection = _connection(config)
        Fake = _fake_deployment(subject)
        Fake.reset()
        Fake.join_result = False
        Fake.cleanup_result = True
        with (
            patch.object(module, "connect", return_value=connection),
            patch.object(module, "RootControlDeployment", Fake),
        ):
            await runtime.launch(principal=principal)
            charged_before_close = owner.snapshot(now=1.0).command_started
            with self.assertRaises(module.RootDeploymentRuntimeUnavailable):
                await runtime.aclose(timeout=1.0)
            self.assertEqual(runtime.status(principal=principal).phase, "unavailable")
            worker = runtime._worker
            self.assertIsNotNone(worker)
            await runtime._await_worker_return(
                worker,
                asyncio.get_running_loop().time() + 1.0,
            )
            runtime._refresh_control_lease_retired()
            self.assertTrue(connection.raw.closed)
            self.assertIsNone(connection._storage_loan)
            self.assertTrue(runtime._control_lease_retired)
            self.assertFalse(runtime._worker_retired)
            self.assertTrue(worker._returned.is_set())
            self.assertFalse(worker._thread.is_alive())
            self.assertEqual(owner.snapshot(now=1.0).command_started, charged_before_close)
            self.assertEqual(owner.snapshot(now=1.0).command_inflight, charged_before_close)
            self.assertTrue(any(event[0] == "cleanup" for event in Fake.events))
            with self.assertRaises(module.RootDeploymentRuntimeUnavailable):
                await runtime.aclose(timeout=0.1)
            self.assertEqual(owner.snapshot(now=1.0).command_inflight, charged_before_close)

    async def test_unconfirmed_sql_close_retains_worker_until_positive_retry(self):
        config, principal, subject, owner = _fixture()
        close_attempts = []

        def close_once(connection):
            close_attempts.append(connection.close_calls)
            return connection.close_calls == 1

        connection = _connection(config, close_hook=close_once)
        runtime = module.RootDeploymentRuntime(config)
        Fake = _fake_deployment(subject)
        Fake.reset()
        with (
            patch.object(module, "connect", return_value=connection),
            patch.object(module, "RootControlDeployment", Fake),
        ):
            await runtime.launch(principal=principal)
            await runtime.activate(
                principal=principal,
                journal_id=str(uuid4()),
                intent_id=str(uuid4()),
                session_id=str(uuid4()),
            )
            with self.assertRaises(module.RootDeploymentRuntimeUnavailable):
                await runtime.aclose(timeout=1.0)
            self.assertFalse(connection.raw.closed)
            self.assertNotEqual(runtime.status(principal=principal).phase, "closed")
            self.assertGreater(owner.snapshot(now=1.0).command_inflight, 0)
            await runtime.aclose(timeout=1.0)
            self.assertEqual(runtime.status(principal=principal).phase, "closed")
        self.assertTrue(connection.raw.closed)
        self.assertEqual(connection.close_calls, 2)
        self.assertEqual(owner.snapshot(now=1.0).command_inflight, 0)

    async def test_failed_command_finish_retains_payload_and_blocks_ready_or_join(self):
        config, principal, subject, owner = _fixture()
        runtime = module.RootDeploymentRuntime(config)
        connection = _connection(config)
        Fake = _fake_deployment(subject)
        Fake.reset()
        native_thread = threading.Thread

        # This case deliberately retains a live worker after finish fails. Keep
        # its test-only thread daemonized so the suite can verify the debt
        # without hanging interpreter shutdown.
        class RetainedChargeThread(native_thread):
            def __init__(self, *args, **kwargs):
                kwargs["daemon"] = True
                super().__init__(*args, **kwargs)

        with (
            patch.object(module, "connect", return_value=connection),
            patch.object(module, "RootControlDeployment", Fake),
            patch.object(threading, "Thread", RetainedChargeThread),
        ):
            await runtime.launch(principal=principal)
            with patch.object(CommandReservation, "finish", return_value=False):
                with self.assertRaises(module.RootDeploymentRuntimeUnavailable):
                    await runtime.activate(
                        principal=principal,
                        journal_id=str(uuid4()),
                        intent_id=str(uuid4()),
                        session_id=str(uuid4()),
                    )
            self.assertEqual(runtime.status(principal=principal).phase, "unavailable")
            self.assertIsNotNone(runtime._active_command)
            self.assertTrue(runtime._busy)
            before = owner.snapshot(now=1.0).command_inflight
            with self.assertRaises(module.RootDeploymentRuntimeUnavailable):
                await runtime.aclose(timeout=0.1)
            self.assertEqual(owner.snapshot(now=1.0).command_inflight, before)
            self.assertFalse(connection.raw.closed)
