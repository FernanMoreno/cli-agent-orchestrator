"""Two-phase root deployment composition without Popen or PostgreSQL."""

from __future__ import annotations

import os
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cli_agent_orchestrator.clients.admission_bootstrap_repository import ResourceEnvelopeRepository
from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget
from cli_agent_orchestrator.models.resource_envelope import (
    EnvelopeClaims,
    ResourceBinding,
    ResourceEnvelopeRecord,
)
from cli_agent_orchestrator.models.root_control_activation import (
    RootControlFootprint,
    _sign_root_control_activation,
)
from cli_agent_orchestrator.models.sql_root_genesis import (
    RootSqlOpeningIntent,
    _issue_confirmed_root_sql_opening,
)
from cli_agent_orchestrator.models.sql_startup_permission import (
    sql_startup_binding_sha256,
    sql_startup_destination_sha256,
    sql_startup_target_identity_sha256,
)
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services import root_control_deployment as deployment_module
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_policy import (
    AdmissionPolicy,
    CapacityObservation,
    ResourceAmounts,
)
from cli_agent_orchestrator.services.delivery_admission import (
    CommandProcess,
    CommandProcessStartUncertain,
    CommandReservation,
    DeliveryAdmission,
)
from cli_agent_orchestrator.services.root_control_activation import (
    RootControlActivationUnavailableAfterCommit,
)
from cli_agent_orchestrator.services.root_sql_control import RootSqlSubject
from cli_agent_orchestrator.services.sql_root_genesis import sign_root_opening


def _deployment_fixture():
    target = DatabaseTarget(
        host="127.0.0.1",
        port=5432,
        database="root-control-deployment-test",
        user="owner",
        schema="cao_root_control_deployment_test",
        store_id=str(uuid4()),
        secret_file=Path("/root-control-test/unused-secret.json"),
        sslmode="disable",
    )
    repository = ResourceEnvelopeRepository(target_supplier=lambda: target)
    bootstrap = ResourceBootstrap(repository=repository)
    channel = Connection.__new__(Connection)
    channel.target = target
    principal = auth._verified_principal("urn:caos:test", "admin", [auth.SCOPE_ADMIN], "jwt")

    policy = AdmissionPolicy(
        memory_per_delivery_bytes=1,
        cpu_per_delivery_millicores=1,
        memory_reserve_percent=0,
        cpu_reserve_percent=0,
        max_sample_age_s=5.0,
    )
    owner, _control = DeliveryAdmission.with_storage_control(policy)
    owner._budget = ResourceAmounts(16_384, 2_000, 0)
    accepted = owner.observe(
        CapacityObservation(
            observed_at=1.0,
            memory_available_bytes=16_384,
            memory_limit_bytes=16_384,
            cpu_available_millicores=2_000,
            cpu_limit_millicores=2_000,
            storage_available_slots=0,
            coverage="known",
        ),
        now=1.0,
        generation=owner.generation,
    )
    if not accepted:
        raise AssertionError("unit fixture capacity was rejected")
    reservation = owner.try_reserve_command(
        footprint=ResourceAmounts(1024, 100, 0), now=1.0
    ).reservation
    if reservation is None:
        raise AssertionError("unit fixture command reservation was rejected")

    target_digest = sql_startup_target_identity_sha256(target)
    binding = ResourceBinding(
        instance_id=str(uuid4()),
        process_generation=str(uuid4()),
        process_pid=os.getpid() + 123,
        process_started_at=100.0,
        store_id=target.store_id,
        target_identity_sha256=target_digest,
        mount_ns_device=0,
        mount_ns_inode=2,
        cgroup_ns_device=0,
        cgroup_ns_inode=3,
        resource_view_sha256="b" * 64,
    )
    subject = RootSqlSubject(binding, f"caos-sql-v1-{uuid4().hex}", str(uuid4()), 1, (1, 2))
    events: list[object] = []

    def launch_process(
        exact_reservation,
        argv,
        *,
        expected_store_id,
        expected_target_identity_sha256,
        env,
        clock,
        root_control_channel,
    ):
        events.append(("process_launch", root_control_channel))
        if exact_reservation is not reservation or not root_control_channel:
            raise AssertionError("deployment did not use the existing reservation/Root channel")
        process = CommandProcess._create(
            exact_reservation,
            store_id=expected_store_id,
            target_digest=expected_target_identity_sha256,
        )
        with owner._guard:
            object.__setattr__(exact_reservation, "_process_worker", process)
            exact_reservation._mark_started_locked(threading.current_thread(), None)
        return process

    return {
        "target": target,
        "bootstrap": bootstrap,
        "channel": channel,
        "principal": principal,
        "owner": owner,
        "reservation": reservation,
        "binding": binding,
        "subject": subject,
        "events": events,
        "launch_process": launch_process,
        "target_digest": target_digest,
    }


def _activation_packet(target: DatabaseTarget, subject: RootSqlSubject):
    private_key = Ed25519PrivateKey.generate()
    credit_ids = tuple(sorted((str(uuid4()), str(uuid4()))))
    root_record = RootSqlOpeningIntent(
        intent_id=str(uuid4()),
        journal_id=str(uuid4()),
        ordinal=1,
        credit_id=credit_ids[0],
        credit_ids=credit_ids,
        membership_sha256="c" * 64,
        session_id=str(uuid4()),
        subject=subject.binding,
        subject_binding_sha256=sql_startup_binding_sha256(subject.binding),
        subject_sql_application_name=subject.sql_application_name,
        boot_id=subject.boot_id,
        start_time_ticks=subject.start_time_ticks,
        pid_namespace=subject.pid_namespace,
        path="native",
        destination_sha256=sql_startup_destination_sha256(target),
        destination_scope="main",
        issuer_principal_id="principal-" + "d" * 64,
    )
    committed = _issue_confirmed_root_sql_opening(root_record, validate=lambda: None)
    certificate = sign_root_opening(committed, key_id="deployment-key", private_key=private_key)
    envelope = ResourceEnvelopeRecord(
        subject.binding,
        EnvelopeClaims(10_000, 1_000, 8, True, False),
        revision=2,
        active=True,
        actor_principal_id="principal-" + "e" * 64,
    )
    packet = _sign_root_control_activation(
        committed=committed,
        certificate=certificate,
        envelope=envelope,
        startup_footprint=RootControlFootprint(128, 20),
        refresh_footprint=RootControlFootprint(64, 10),
        key_id="deployment-key",
        private_key=private_key,
    ).to_bytes()
    return packet, root_record


class RootControlDeploymentTests(unittest.TestCase):
    def test_unverified_admin_is_rejected_before_target_or_reservation_reads(self) -> None:
        target_reads: list[object] = []
        repository = ResourceEnvelopeRepository(
            target_supplier=lambda: target_reads.append("target")
            or (_ for _ in ()).throw(
                AssertionError("target read before administrator verification")
            )
        )
        bootstrap = ResourceBootstrap(repository=repository)

        with self.assertRaises(PermissionError):
            deployment_module.RootControlDeployment.launch(
                bootstrap=bootstrap,
                channel=object(),
                principal=object(),
                reservation=object(),
                argv=("unused",),
                env={},
                clock=lambda: 1.0,
            )

        self.assertEqual(target_reads, [])

    def test_launch_uses_authenticated_subject_and_root_channel_reservation(self) -> None:
        fixture = _deployment_fixture()
        events = fixture["events"]
        subject = fixture["subject"]
        binding = fixture["binding"]

        def receive_binding(_process, *, timeout_s):
            events.append(("binding", timeout_s))
            return binding

        with (
            patch.object(
                Connection, "_check_funded_worker", lambda _channel: events.append("funded")
            ),
            patch.object(CommandReservation, "_launch_process", fixture["launch_process"]),
            patch.object(CommandProcess, "receive_binding", receive_binding),
            patch.object(CommandProcess, "root_subject", lambda _process: subject),
        ):
            deployment = deployment_module.RootControlDeployment.launch(
                bootstrap=fixture["bootstrap"],
                channel=fixture["channel"],
                principal=fixture["principal"],
                reservation=fixture["reservation"],
                argv=("python3", "-m", "cli_agent_orchestrator.runtime_channel.root_control"),
                env={"CONTROLLED": "1"},
                clock=lambda: 1.0,
                binding_timeout_s=4.0,
            )

        self.assertIs(deployment.subject, subject)
        self.assertEqual(fixture["reservation"].state, "started")
        self.assertLess(events.index("funded"), events.index(("process_launch", True)))
        self.assertIn(("binding", 4.0), events)
        self.assertEqual(deployment.has_native_owner, False)

    def test_postcommit_rpc_failure_retains_record_handle_and_forbids_reissue(self) -> None:
        fixture = _deployment_fixture()
        packet, root_record = _activation_packet(fixture["target"], fixture["subject"])
        deployment = self._launch(fixture)
        rpc_error = deployment_module.RootControlDeploymentUnavailable()
        issuer_calls: list[object] = []
        abort_calls: list[object] = []

        def issue(*args, **kwargs):
            issuer_calls.append((args, kwargs))
            return packet

        with (
            patch.object(Connection, "_check_funded_worker", lambda _channel: None),
            patch.object(CommandProcess, "root_subject", return_value=replace(fixture["subject"])),
            patch.object(deployment_module, "issue_root_control_activation", side_effect=issue),
            patch.object(CommandProcess, "root_activate", side_effect=rpc_error),
            patch.object(
                CommandProcess, "root_abort", side_effect=lambda *_args: abort_calls.append(True)
            ),
        ):
            with self.assertRaises(deployment_module.RootControlDeploymentUnavailable) as raised:
                deployment.activate(
                    journal_id=root_record.journal_id,
                    intent_id=root_record.intent_id,
                    session_id=root_record.session_id,
                    key_supplier=lambda: (_ for _ in ()).throw(
                        AssertionError("deployment must pass the key supplier to the issuer")
                    ),
                    startup_footprint=ResourceAmounts(128, 20, 0),
                    refresh_footprint=ResourceAmounts(64, 10, 0),
                )

            self.assertIs(raised.exception.deployment, deployment)
            self.assertIs(raised.exception.cause, rpc_error)
            retained_record = deployment.record
            self.assertEqual(retained_record, root_record)
            self.assertEqual(len(issuer_calls), 1)
            self.assertEqual(abort_calls, [True])

            with self.assertRaises(deployment_module.RootControlDeploymentUnavailable):
                deployment.activate(
                    journal_id=root_record.journal_id,
                    intent_id=root_record.intent_id,
                    session_id=root_record.session_id,
                    key_supplier=lambda: ("unused", Ed25519PrivateKey.generate()),
                    startup_footprint=ResourceAmounts(128, 20, 0),
                    refresh_footprint=ResourceAmounts(64, 10, 0),
                )

            self.assertIs(deployment.record, retained_record)

        self.assertEqual(len(issuer_calls), 1)

    def test_changed_live_subject_is_denied_before_issuer_or_key_supplier(self) -> None:
        fixture = _deployment_fixture()
        deployment = self._launch(fixture)
        changed_subject = replace(fixture["subject"], boot_id=str(uuid4()))
        issuer_calls: list[object] = []
        key_calls: list[object] = []

        with (
            patch.object(Connection, "_check_funded_worker", lambda _channel: None),
            patch.object(CommandProcess, "root_subject", return_value=changed_subject),
            patch.object(
                deployment_module,
                "issue_root_control_activation",
                side_effect=lambda *args, **kwargs: issuer_calls.append((args, kwargs)),
            ),
        ):
            with self.assertRaises(deployment_module.RootControlDeploymentUnavailable):
                deployment.activate(
                    journal_id=str(uuid4()),
                    intent_id=str(uuid4()),
                    session_id=str(uuid4()),
                    key_supplier=lambda: key_calls.append(True)
                    or ("unused", Ed25519PrivateKey.generate()),
                    startup_footprint=ResourceAmounts(128, 20, 0),
                    refresh_footprint=ResourceAmounts(64, 10, 0),
                )

        self.assertEqual(issuer_calls, [])
        self.assertEqual(key_calls, [])

    def test_uncertain_launch_keeps_handle_even_when_abort_fails(self) -> None:
        fixture = _deployment_fixture()
        process_error = RuntimeError("native acknowledgement is uncertain")
        cleanup_error = RuntimeError("best effort abort failed")
        abort_calls: list[object] = []

        def uncertain_launch(*args, **kwargs):
            process = fixture["launch_process"](*args, **kwargs)
            raise CommandProcessStartUncertain(process, process_error)

        with (
            patch.object(Connection, "_check_funded_worker", lambda _channel: None),
            patch.object(CommandReservation, "_launch_process", uncertain_launch),
            patch.object(
                CommandProcess,
                "root_abort",
                side_effect=lambda *_args: abort_calls.append(True)
                or (_ for _ in ()).throw(cleanup_error),
            ),
            patch.object(CommandProcess, "join", return_value=True),
        ):
            with self.assertRaises(deployment_module.RootControlDeploymentUnavailable) as raised:
                deployment_module.RootControlDeployment.launch(
                    bootstrap=fixture["bootstrap"],
                    channel=fixture["channel"],
                    principal=fixture["principal"],
                    reservation=fixture["reservation"],
                    argv=("python3", "-m", "cli_agent_orchestrator.runtime_channel.root_control"),
                    env={},
                    clock=lambda: 1.0,
                )

            self.assertIsNotNone(raised.exception.deployment)
            self.assertIs(raised.exception.cause, process_error)
            self.assertEqual(raised.exception.deployment.join(), True)

        self.assertEqual(abort_calls, [True])
        self.assertEqual(fixture["reservation"].state, "started")

    def test_late_launch_exception_recovers_registered_worker_and_preserves_primary(self) -> None:
        fixture = _deployment_fixture()
        primary = KeyboardInterrupt("late launch interruption")
        cleanup_error = RuntimeError("best effort abort failed")
        abort_calls: list[object] = []

        def late_failure(*args, **kwargs):
            fixture["launch_process"](*args, **kwargs)
            raise primary

        with (
            patch.object(Connection, "_check_funded_worker", lambda _channel: None),
            patch.object(CommandReservation, "_launch_process", late_failure),
            patch.object(
                CommandProcess,
                "root_abort",
                side_effect=lambda *_args: abort_calls.append(True)
                or (_ for _ in ()).throw(cleanup_error),
            ),
            patch.object(CommandProcess, "join", return_value=True),
        ):
            with self.assertRaises(deployment_module.RootControlDeploymentUnavailable) as raised:
                deployment_module.RootControlDeployment.launch(
                    bootstrap=fixture["bootstrap"],
                    channel=fixture["channel"],
                    principal=fixture["principal"],
                    reservation=fixture["reservation"],
                    argv=("python3", "-m", "cli_agent_orchestrator.runtime_channel.root_control"),
                    env={},
                    clock=lambda: 1.0,
                )

            self.assertIs(raised.exception.cause, primary)
            self.assertEqual(raised.exception.deployment.join(), True)

        self.assertEqual(abort_calls, [True])
        self.assertEqual(fixture["reservation"].state, "started")

    def test_binding_failure_retains_handle_and_original_error(self) -> None:
        fixture = _deployment_fixture()
        primary = RuntimeError("binding acknowledgement failed")
        cleanup_error = RuntimeError("best effort abort failed")

        with (
            patch.object(Connection, "_check_funded_worker", lambda _channel: None),
            patch.object(CommandReservation, "_launch_process", fixture["launch_process"]),
            patch.object(CommandProcess, "receive_binding", side_effect=primary),
            patch.object(CommandProcess, "root_abort", side_effect=cleanup_error),
            patch.object(CommandProcess, "join", return_value=True),
        ):
            with self.assertRaises(deployment_module.RootControlDeploymentUnavailable) as raised:
                deployment_module.RootControlDeployment.launch(
                    bootstrap=fixture["bootstrap"],
                    channel=fixture["channel"],
                    principal=fixture["principal"],
                    reservation=fixture["reservation"],
                    argv=("python3", "-m", "cli_agent_orchestrator.runtime_channel.root_control"),
                    env={},
                    clock=lambda: 1.0,
                )

            self.assertIs(raised.exception.cause, primary)
            self.assertEqual(raised.exception.deployment.join(), True)

        self.assertEqual(fixture["reservation"].state, "started")

    def test_postcommit_issuer_error_retains_record_and_aborts_without_replay(self) -> None:
        fixture = _deployment_fixture()
        _packet, root_record = _activation_packet(fixture["target"], fixture["subject"])
        deployment = self._launch(fixture)
        issuer_calls: list[object] = []
        abort_calls: list[object] = []
        issuer_error = RootControlActivationUnavailableAfterCommit(root_record)

        def issue(*_args, **_kwargs):
            issuer_calls.append(True)
            raise issuer_error

        with (
            patch.object(Connection, "_check_funded_worker", lambda _channel: None),
            patch.object(CommandProcess, "root_subject", return_value=replace(fixture["subject"])),
            patch.object(deployment_module, "issue_root_control_activation", side_effect=issue),
            patch.object(
                CommandProcess, "root_abort", side_effect=lambda *_args: abort_calls.append(True)
            ),
        ):
            with self.assertRaises(deployment_module.RootControlDeploymentUnavailable) as raised:
                deployment.activate(
                    journal_id=root_record.journal_id,
                    intent_id=root_record.intent_id,
                    session_id=root_record.session_id,
                    key_supplier=lambda: ("unused", Ed25519PrivateKey.generate()),
                    startup_footprint=ResourceAmounts(128, 20, 0),
                    refresh_footprint=ResourceAmounts(64, 10, 0),
                )

            self.assertIs(raised.exception.cause, issuer_error)
            retained_record = deployment.record
            self.assertEqual(retained_record, root_record)
            self.assertEqual(issuer_calls, [True])
            self.assertEqual(abort_calls, [True])

            with self.assertRaises(deployment_module.RootControlDeploymentUnavailable):
                deployment.activate(
                    journal_id=root_record.journal_id,
                    intent_id=root_record.intent_id,
                    session_id=root_record.session_id,
                    key_supplier=lambda: ("unused", Ed25519PrivateKey.generate()),
                    startup_footprint=ResourceAmounts(128, 20, 0),
                    refresh_footprint=ResourceAmounts(64, 10, 0),
                )

            self.assertIs(deployment.record, retained_record)

        self.assertEqual(issuer_calls, [True])

    def test_activate_rechecks_admin_before_funded_channel_or_issuer(self) -> None:
        fixture = _deployment_fixture()
        deployment = self._launch(fixture)
        key_calls: list[object] = []

        with (
            patch.object(
                ResourceBootstrap,
                "_administrator",
                side_effect=PermissionError("revoked administrator"),
            ),
            patch.object(
                Connection,
                "_check_funded_worker",
                side_effect=AssertionError("funded channel checked before administrator"),
            ),
            patch.object(
                deployment_module,
                "issue_root_control_activation",
                side_effect=AssertionError("issuer entered after rejected administrator"),
            ),
        ):
            with self.assertRaises(deployment_module.RootControlDeploymentUnavailable):
                deployment.activate(
                    journal_id=str(uuid4()),
                    intent_id=str(uuid4()),
                    session_id=str(uuid4()),
                    key_supplier=lambda: key_calls.append(True)
                    or ("unused", Ed25519PrivateKey.generate()),
                    startup_footprint=ResourceAmounts(128, 20, 0),
                    refresh_footprint=ResourceAmounts(64, 10, 0),
                )

        self.assertEqual(key_calls, [])

    def test_pre_activation_refresh_and_stop_do_not_touch_child(self) -> None:
        fixture = _deployment_fixture()
        deployment = self._launch(fixture)
        child_calls: list[object] = []

        with (
            patch.object(
                CommandProcess,
                "root_refresh",
                side_effect=lambda *_args, **_kwargs: child_calls.append("refresh"),
            ),
            patch.object(
                CommandProcess,
                "root_stop",
                side_effect=lambda *_args, **_kwargs: child_calls.append("stop"),
            ),
            patch.object(
                CommandProcess,
                "root_abort",
                side_effect=lambda *_args, **_kwargs: child_calls.append("abort"),
            ),
        ):
            with self.assertRaises(deployment_module.RootControlDeploymentUnavailable):
                deployment.refresh()
            with self.assertRaises(deployment_module.RootControlDeploymentUnavailable):
                deployment.stop()

        self.assertEqual(child_calls, [])
        self.assertIsNone(deployment.record)
        self.assertFalse(deployment._active)

    def test_activation_callbacks_cannot_reenter_controls_or_retirement(self) -> None:
        fixture = _deployment_fixture()
        packet, root_record = _activation_packet(fixture["target"], fixture["subject"])
        deployment = self._launch(fixture)
        phases: list[str] = []
        child_calls: list[object] = []

        def reject_reentrant_controls(phase: str) -> None:
            controls = (
                ("refresh", lambda: deployment.refresh()),
                ("stop", lambda: deployment.stop()),
                ("cleanup", lambda: deployment.cleanup()),
                ("join", lambda: deployment.join()),
                ("send_signal", lambda: deployment.send_signal(15)),
            )
            for name, call in controls:
                with self.assertRaises(
                    deployment_module.RootControlDeploymentUnavailable
                ) as raised:
                    call()
                self.assertIs(raised.exception.deployment, deployment)
                phases.append(f"{phase}:{name}")

        def issue(*_args, **kwargs):
            phases.append("committed")
            kwargs["key_supplier"]()
            return packet

        def supply_key():
            reject_reentrant_controls("key_supplier")
            return "deployment-key", Ed25519PrivateKey.generate()

        def deliver(*_args, timeout_s):
            self.assertEqual(timeout_s, 30)
            phases.append("delivery")
            reject_reentrant_controls("delivery")

        with (
            patch.object(Connection, "_check_funded_worker", lambda _channel: None),
            patch.object(CommandProcess, "root_subject", return_value=replace(fixture["subject"])),
            patch.object(deployment_module, "issue_root_control_activation", side_effect=issue),
            patch.object(CommandProcess, "root_activate", deliver),
            patch.object(
                CommandProcess,
                "root_refresh",
                side_effect=lambda *_args, **_kwargs: child_calls.append("refresh"),
            ),
            patch.object(
                CommandProcess,
                "root_stop",
                side_effect=lambda *_args, **_kwargs: child_calls.append("stop"),
            ),
            patch.object(
                CommandProcess,
                "cleanup",
                side_effect=lambda *_args, **_kwargs: child_calls.append("cleanup"),
            ),
            patch.object(
                CommandProcess,
                "join",
                side_effect=lambda *_args, **_kwargs: child_calls.append("join"),
            ),
            patch.object(
                CommandProcess,
                "send_signal",
                side_effect=lambda *_args, **_kwargs: child_calls.append("signal"),
            ),
        ):
            deployment.activate(
                journal_id=root_record.journal_id,
                intent_id=root_record.intent_id,
                session_id=root_record.session_id,
                key_supplier=supply_key,
                startup_footprint=ResourceAmounts(128, 20, 0),
                refresh_footprint=ResourceAmounts(64, 10, 0),
            )

        self.assertEqual(phases[0], "committed")
        self.assertIn("key_supplier:cleanup", phases)
        self.assertIn("delivery:send_signal", phases)
        self.assertEqual(child_calls, [])
        self.assertEqual(deployment.record, root_record)
        self.assertTrue(deployment._active)

    def test_refresh_gate_blocks_reentrant_retirement_and_failure_deactivates(self) -> None:
        fixture = _deployment_fixture()
        deployment, root_record = self._activate_ready(fixture)
        retained_record = deployment.record
        self.assertEqual(retained_record, root_record)
        child_calls: list[object] = []

        def refresh(_process, *, timeout_s):
            self.assertEqual(timeout_s, 30)
            with self.assertRaises(deployment_module.RootControlDeploymentUnavailable):
                deployment.cleanup()
            child_calls.append("refresh")
            return "capacity_available"

        with (
            patch.object(CommandProcess, "root_refresh", refresh),
            patch.object(
                CommandProcess,
                "cleanup",
                side_effect=lambda *_args, **_kwargs: child_calls.append("cleanup"),
            ),
        ):
            self.assertEqual(deployment.refresh(), "capacity_available")

        self.assertEqual(child_calls, ["refresh"])
        primary = RuntimeError("refresh reply lost")
        with patch.object(CommandProcess, "root_refresh", side_effect=primary):
            with self.assertRaises(deployment_module.RootControlDeploymentUnavailable) as raised:
                deployment.refresh()

        self.assertIs(raised.exception.deployment, deployment)
        self.assertIs(raised.exception.cause, primary)
        self.assertFalse(deployment._active)
        self.assertIs(deployment.record, retained_record)
        self.assertEqual(deployment.record, root_record)

        retirement_calls: list[object] = []
        with (
            patch.object(
                CommandProcess,
                "cleanup",
                side_effect=lambda *_args, **_kwargs: retirement_calls.append("cleanup") or True,
            ),
            patch.object(
                CommandProcess,
                "join",
                side_effect=lambda *_args, **_kwargs: retirement_calls.append("join") or True,
            ),
            patch.object(
                CommandProcess,
                "send_signal",
                side_effect=lambda *_args, **_kwargs: retirement_calls.append("signal"),
            ),
        ):
            self.assertTrue(deployment.cleanup())
            self.assertTrue(deployment.join())
            deployment.send_signal(15)

        self.assertEqual(retirement_calls, ["cleanup", "join", "signal"])

    def test_stop_failure_deactivates_but_retains_handle_and_record(self) -> None:
        fixture = _deployment_fixture()
        deployment, root_record = self._activate_ready(fixture)
        retained_record = deployment.record
        self.assertEqual(retained_record, root_record)
        primary = RuntimeError("stop reply lost")

        with patch.object(CommandProcess, "root_stop", side_effect=primary):
            with self.assertRaises(deployment_module.RootControlDeploymentUnavailable) as raised:
                deployment.stop()

        self.assertIs(raised.exception.deployment, deployment)
        self.assertIs(raised.exception.cause, primary)
        self.assertFalse(deployment._active)
        self.assertIs(deployment.record, retained_record)
        self.assertEqual(deployment.record, root_record)

    def _activate_ready(self, fixture):
        packet, root_record = _activation_packet(fixture["target"], fixture["subject"])
        deployment = self._launch(fixture)
        with (
            patch.object(Connection, "_check_funded_worker", lambda _channel: None),
            patch.object(CommandProcess, "root_subject", return_value=replace(fixture["subject"])),
            patch.object(deployment_module, "issue_root_control_activation", return_value=packet),
            patch.object(CommandProcess, "root_activate", return_value=None),
        ):
            deployment.activate(
                journal_id=root_record.journal_id,
                intent_id=root_record.intent_id,
                session_id=root_record.session_id,
                key_supplier=lambda: ("deployment-key", Ed25519PrivateKey.generate()),
                startup_footprint=ResourceAmounts(128, 20, 0),
                refresh_footprint=ResourceAmounts(64, 10, 0),
            )
        return deployment, root_record

    def _launch(self, fixture):
        with (
            patch.object(Connection, "_check_funded_worker", lambda _channel: None),
            patch.object(CommandReservation, "_launch_process", fixture["launch_process"]),
            patch.object(
                CommandProcess, "receive_binding", lambda _process, **kwargs: fixture["binding"]
            ),
            patch.object(CommandProcess, "root_subject", lambda _process: fixture["subject"]),
        ):
            return deployment_module.RootControlDeployment.launch(
                bootstrap=fixture["bootstrap"],
                channel=fixture["channel"],
                principal=fixture["principal"],
                reservation=fixture["reservation"],
                argv=("python3", "-m", "cli_agent_orchestrator.runtime_channel.root_control"),
                env={},
                clock=lambda: 1.0,
            )


if __name__ == "__main__":
    unittest.main()
