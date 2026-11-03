"""Strict signed activation wire contract and signature tests."""

from __future__ import annotations

import base64
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

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cli_agent_orchestrator.clients import (
    admission_bootstrap_repository as envelope_repository_module,
)
from cli_agent_orchestrator.clients.admission_bootstrap_repository import ResourceEnvelopeRepository
from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget
from cli_agent_orchestrator.clients.postgres.connection import FundedSqlCommitUncertain
from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository
from cli_agent_orchestrator.models import root_control_activation as activation_model
from cli_agent_orchestrator.models.resource_envelope import (
    EnvelopeClaims,
    ResourceBinding,
    ResourceEnvelopeRecord,
)
from cli_agent_orchestrator.models.root_control_activation import (
    ROOT_CONTROL_ACTIVATION_MAX_BYTES,
    RootControlActivation,
    RootControlFootprint,
    _sign_root_control_activation,
    verify_root_control_activation_signature,
)
from cli_agent_orchestrator.models.sql_process_origin import SQL_ORIGIN_PREFIX
from cli_agent_orchestrator.models.sql_root_genesis import (
    RootSqlOpeningIntent,
    SignedRootSqlOpening,
    _issue_confirmed_root_sql_opening,
)
from cli_agent_orchestrator.models.sql_startup_permission import (
    sql_startup_binding_sha256,
    sql_startup_destination_sha256,
)
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services import root_control_activation as activation_service
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_policy import ResourceAmounts
from cli_agent_orchestrator.services.root_sql_control import RootSqlSubject
from cli_agent_orchestrator.services.sql_root_genesis import sign_root_opening


def _activation() -> tuple[RootControlActivation, Ed25519PrivateKey]:
    private_key = Ed25519PrivateKey.generate()
    binding = ResourceBinding(
        instance_id=str(uuid4()),
        process_generation=str(uuid4()),
        process_pid=12345,
        process_started_at=1.0,
        store_id=str(uuid4()),
        target_identity_sha256="a" * 64,
        mount_ns_device=0,
        mount_ns_inode=1,
        cgroup_ns_device=0,
        cgroup_ns_inode=1,
        resource_view_sha256="b" * 64,
    )
    credit_ids = tuple(sorted((str(uuid4()), str(uuid4()))))
    root_record = RootSqlOpeningIntent(
        intent_id=str(uuid4()),
        journal_id=str(uuid4()),
        ordinal=1,
        credit_id=credit_ids[0],
        credit_ids=credit_ids,
        membership_sha256="c" * 64,
        session_id=str(uuid4()),
        subject=binding,
        subject_binding_sha256=sql_startup_binding_sha256(binding),
        subject_sql_application_name=SQL_ORIGIN_PREFIX + uuid4().hex,
        boot_id=str(uuid4()),
        start_time_ticks=1,
        pid_namespace=(1, 1),
        path="native",
        destination_sha256="d" * 64,
        destination_scope="main",
        issuer_principal_id="principal-" + "e" * 64,
    )
    committed = _issue_confirmed_root_sql_opening(root_record, validate=lambda: None)
    certificate = sign_root_opening(committed, key_id="control-key", private_key=private_key)
    envelope = ResourceEnvelopeRecord(
        binding,
        EnvelopeClaims(1_000_000, 2_000, 20, True, False),
        revision=1,
        active=True,
        actor_principal_id="principal-" + "f" * 64,
    )
    activation = _sign_root_control_activation(
        committed=committed,
        certificate=certificate,
        envelope=envelope,
        startup_footprint=RootControlFootprint(1024, 20),
        refresh_footprint=RootControlFootprint(512, 10),
        key_id="control-key",
        private_key=private_key,
    )
    return activation, private_key


def _issuer_setup(envelope_override: ResourceEnvelopeRecord | None = None):
    target = DatabaseTarget(
        host="127.0.0.1",
        port=5432,
        database="root-control-issuer-test",
        user="owner",
        schema="cao_root_control_issuer_test",
        store_id=str(uuid4()),
        secret_file=Path("/root-control-test/unused-secret.json"),
        sslmode="disable",
    )
    target_digest = hashlib.sha256(
        json.dumps(target.identity, separators=(",", ":")).encode()
    ).hexdigest()
    live = SimpleNamespace(boot_id=str(uuid4()), ticks=19, pid_ns=(7, 8))
    subject_binding = ResourceBinding(
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
        resource_view_sha256="9" * 64,
    )
    subject = RootSqlSubject(
        subject_binding,
        SQL_ORIGIN_PREFIX + uuid4().hex,
        live.boot_id,
        live.ticks,
        live.pid_ns,
    )
    issuer_binding = replace(subject_binding, process_pid=os.getpid())
    repository = ResourceEnvelopeRepository(target_supplier=lambda: target)
    bootstrap = ResourceBootstrap(
        repository=repository,
        binding_supplier=lambda _target: issuer_binding,
        instance_supplier=lambda: issuer_binding.instance_id,
    )
    channel = Connection.__new__(Connection)
    channel.target = target
    credits = tuple(sorted((str(uuid4()), str(uuid4()))))
    root_record = RootSqlOpeningIntent(
        intent_id=str(uuid4()),
        journal_id=str(uuid4()),
        ordinal=1,
        credit_id=credits[0],
        credit_ids=credits,
        membership_sha256="1" * 64,
        session_id=str(uuid4()),
        subject=subject_binding,
        subject_binding_sha256=sql_startup_binding_sha256(subject_binding),
        subject_sql_application_name=subject.sql_application_name,
        boot_id=subject.boot_id,
        start_time_ticks=subject.start_time_ticks,
        pid_namespace=subject.pid_namespace,
        path="native",
        destination_sha256=sql_startup_destination_sha256(target),
        destination_scope="main",
        issuer_principal_id="principal-" + "2" * 64,
    )
    cap = _issue_confirmed_root_sql_opening(root_record, validate=lambda: None)
    claims = EnvelopeClaims(10_000, 1_000, 8, True, False)
    envelope = ResourceEnvelopeRecord(
        subject_binding,
        claims,
        revision=2,
        active=True,
        actor_principal_id="principal-" + "3" * 64,
    )
    principal = auth._verified_principal("urn:caos:test", "admin", [auth.SCOPE_ADMIN], "jwt")
    events: list[str] = []
    private_key = Ed25519PrivateKey.generate()

    @contextmanager
    def no_sql_connect(*args, **kwargs):
        raise AssertionError("issuer opened a fallback connection")
        yield  # pragma: no cover

    # The repository is intentionally populated with a connect factory that
    # would fail if issuance attempted an unrelated runtime read.
    repository._connect = no_sql_connect
    return (
        target,
        bootstrap,
        channel,
        principal,
        subject,
        root_record,
        cap,
        envelope_override or envelope,
        events,
        private_key,
    )


class RootControlActivationTests(unittest.TestCase):
    def test_round_trip_is_canonical_and_outer_signature_uses_external_key(self) -> None:
        activation, private_key = _activation()
        packet = activation.to_bytes()

        decoded = RootControlActivation.from_bytes(packet)

        self.assertEqual(decoded, activation)
        verify_root_control_activation_signature(
            decoded,
            trusted_keys={"control-key": private_key.public_key()},
        )
        with self.assertRaises(InvalidSignature):
            verify_root_control_activation_signature(
                decoded,
                trusted_keys={"control-key": Ed25519PrivateKey.generate().public_key()},
            )


class RootControlIssuerTests(unittest.TestCase):
    def _issue(
        self,
        setup,
        *,
        envelope_reads,
        root_error: BaseException | None = None,
        principal: object | None = None,
        key_calls: list[str] | None = None,
    ):
        (
            target,
            bootstrap,
            channel,
            administrator,
            subject,
            root_record,
            cap,
            envelope,
            events,
            private_key,
        ) = setup
        key_calls = [] if key_calls is None else key_calls
        live = SimpleNamespace(
            boot_id=subject.boot_id,
            ticks=subject.start_time_ticks,
            pid_ns=subject.pid_namespace,
        )

        def record_root_opening(_repository, _channel, **kwargs):
            events.append("commit")
            if root_error is not None:
                raise root_error
            self.assertEqual(kwargs["subject"], root_record.subject)
            return cap

        reads = iter(envelope_reads)

        def read_envelope(_repository, *_args):
            events.append("read")
            return next(reads)

        def key_supplier():
            key_calls.append("key")
            events.append("key")
            return "control-key", private_key

        with (
            patch.object(Connection, "_check_funded_worker", lambda _channel: None),
            patch.object(activation_service, "_identity", return_value=live),
            patch.object(activation_service, "_matches", return_value=True),
            patch.object(SqlIntentRepository, "record_root_opening", record_root_opening),
            patch.object(ResourceEnvelopeRepository, "read_latest_owner_funded", read_envelope),
        ):
            result = activation_service.issue_root_control_activation(
                bootstrap,
                channel,
                principal=administrator if principal is None else principal,
                journal_id=root_record.journal_id,
                intent_id=root_record.intent_id,
                session_id=root_record.session_id,
                subject=subject,
                key_supplier=key_supplier,
                startup_footprint=ResourceAmounts(100, 10, 0),
                refresh_footprint=ResourceAmounts(50, 5, 0),
            )
        return result, events, key_calls

    def test_issuer_reads_owner_envelope_before_and_after_commit_then_calls_key(self) -> None:
        setup = _issuer_setup()
        _, _, _, _, _, _, _, envelope, _, _ = setup

        packet, events, key_calls = self._issue(
            setup,
            envelope_reads=(envelope, envelope),
        )

        self.assertEqual(events, ["read", "commit", "read", "key"])
        self.assertEqual(key_calls, ["key"])
        decoded = RootControlActivation.from_bytes(packet)
        self.assertEqual(decoded.envelope, envelope)

    def test_missing_or_inactive_preexisting_envelope_writes_no_sql_or_key(self) -> None:
        setup = _issuer_setup()
        key_calls: list[str] = []

        with self.assertRaises(activation_service.RootControlActivationUnavailable):
            self._issue(setup, envelope_reads=(None,), key_calls=key_calls)

        self.assertEqual(key_calls, [])

    def test_owner_funded_read_checks_component_and_table_owner_in_same_transaction(self) -> None:
        target = DatabaseTarget(
            host="127.0.0.1",
            port=5432,
            database="root-control-owner-read-test",
            user="owner",
            schema="cao_root_control_owner_read_test",
            store_id=str(uuid4()),
            secret_file=Path("/root-control-test/unused-secret.json"),
            sslmode="disable",
        )
        channel = Connection.__new__(Connection)
        channel.target = target
        events: list[str] = []
        statements: list[object] = []

        class Cursor:
            def __init__(self, result):
                self.result = result

            def fetchone(self):
                return self.result

        @contextmanager
        def funded_transaction(_channel):
            events.append("transaction_enter")
            try:
                yield channel
            finally:
                events.append("transaction_exit")

        def execute(_channel, statement, params=(), **kwargs):
            statements.append(statement)
            if len(statements) == 2:
                return Cursor((True,))
            if len(statements) == 3:
                return Cursor(None)
            return Cursor(None)

        def verify_component(_channel, component):
            events.append("verify_component")
            self.assertEqual(_channel, channel)
            self.assertTrue(component)

        repository = ResourceEnvelopeRepository(
            target_supplier=lambda: target,
            connect_factory=lambda *args, **kwargs: self.fail("new connection attempted"),
        )
        with (
            patch.object(Connection, "_check_funded_worker", lambda _channel: None),
            patch.object(Connection, "funded_transaction", funded_transaction),
            patch.object(Connection, "execute", execute),
            patch.object(envelope_repository_module, "verify_component", verify_component),
        ):
            latest = repository.read_latest_owner_funded(channel, target, str(uuid4()))

        self.assertIsNone(latest)
        self.assertEqual(events, ["transaction_enter", "verify_component", "transaction_exit"])
        self.assertEqual(len(statements), 3)
        self.assertTrue(all("CREATE" not in str(statement) for statement in statements))

    def test_owner_funded_read_rejects_cross_target_before_channel_access(self) -> None:
        target = DatabaseTarget(
            host="127.0.0.1",
            port=5432,
            database="root-control-owner-read-test",
            user="owner",
            schema="cao_root_control_owner_read_test",
            store_id=str(uuid4()),
            secret_file=Path("/root-control-test/unused-secret.json"),
            sslmode="disable",
        )
        other = replace(target, database="other-root-control-target")
        channel = Connection.__new__(Connection)
        channel.target = target
        repository = ResourceEnvelopeRepository(target_supplier=lambda: target)
        with patch.object(Connection, "_check_funded_worker", side_effect=AssertionError):
            with self.assertRaises(Exception):
                repository.read_latest_owner_funded(channel, other, str(uuid4()))

    def test_commit_uncertainty_never_calls_signing_key(self) -> None:
        setup = _issuer_setup()
        _, _, _, _, _, _, _, envelope, events, _ = setup
        key_calls: list[str] = []

        with self.assertRaises(FundedSqlCommitUncertain):
            self._issue(
                setup,
                envelope_reads=(envelope,),
                root_error=FundedSqlCommitUncertain(),
                key_calls=key_calls,
            )

        self.assertEqual(events, ["read", "commit"])
        self.assertEqual(key_calls, [])

    def test_envelope_revision_change_after_commit_returns_no_packet(self) -> None:
        setup = _issuer_setup()
        _, _, _, _, _, _, _, envelope, _, _ = setup
        key_calls: list[str] = []

        with self.assertRaises(activation_service.RootControlActivationUnavailableAfterCommit):
            self._issue(
                setup,
                envelope_reads=(envelope, replace(envelope, revision=envelope.revision + 1)),
                key_calls=key_calls,
            )

        self.assertEqual(key_calls, [])

    def test_activation_signature_claim_is_separate_and_single_use_after_inner_signing(
        self,
    ) -> None:
        setup = _issuer_setup()
        _, _, _, _, _, _, cap, envelope, _, private_key = setup
        certificate = sign_root_opening(cap, key_id="control-key", private_key=private_key)
        first = _sign_root_control_activation(
            committed=cap,
            certificate=certificate,
            envelope=envelope,
            startup_footprint=RootControlFootprint(100, 10),
            refresh_footprint=RootControlFootprint(50, 5),
            key_id="control-key",
            private_key=private_key,
        )
        self.assertIsInstance(first, RootControlActivation)

        changed_envelope = replace(
            envelope,
            claims=replace(
                envelope.claims, memory_budget_bytes=envelope.claims.memory_budget_bytes + 1
            ),
        )
        with self.assertRaises(ValueError):
            _sign_root_control_activation(
                committed=cap,
                certificate=certificate,
                envelope=changed_envelope,
                startup_footprint=RootControlFootprint(200, 20),
                refresh_footprint=RootControlFootprint(75, 8),
                key_id="control-key",
                private_key=private_key,
            )

    def test_activation_signing_failure_consumes_claim_without_retry(self) -> None:
        setup = _issuer_setup()
        _, _, _, _, _, _, cap, envelope, _, private_key = setup
        certificate = sign_root_opening(cap, key_id="control-key", private_key=private_key)
        kwargs = {
            "committed": cap,
            "certificate": certificate,
            "envelope": envelope,
            "startup_footprint": RootControlFootprint(100, 10),
            "refresh_footprint": RootControlFootprint(50, 5),
            "key_id": "control-key",
            "private_key": private_key,
        }
        with patch.object(
            activation_model,
            "_sign_activation_bytes",
            side_effect=RuntimeError("signing failed"),
            create=True,
        ) as failed_sign:
            with self.assertRaises(RuntimeError):
                _sign_root_control_activation(**kwargs)
            failed_sign.assert_called_once()

        with patch.object(
            activation_model,
            "_sign_activation_bytes",
            wraps=lambda key, payload: key.sign(payload),
            create=True,
        ) as retry_sign:
            with self.assertRaises(ValueError):
                _sign_root_control_activation(**kwargs)
            retry_sign.assert_not_called()

    def test_nonadmin_is_rejected_before_target_resolution_or_key(self) -> None:
        setup = _issuer_setup()
        _, bootstrap, _, _, _, _, _, _, _, _ = setup
        target_calls: list[str] = []
        bootstrap._repository._target_supplier = lambda: target_calls.append("target")
        key_calls: list[str] = []
        non_admin = auth._verified_principal("urn:caos:test", "reader", [auth.SCOPE_READ], "jwt")

        with self.assertRaises(PermissionError):
            self._issue(setup, envelope_reads=(), principal=non_admin, key_calls=key_calls)

        self.assertEqual(target_calls, [])
        self.assertEqual(key_calls, [])

    def test_wire_rejects_duplicate_unknown_boolean_nan_and_oversized_values(self) -> None:
        activation, _ = _activation()
        packet = activation.to_bytes()
        with self.assertRaises(ValueError):
            RootControlActivation.from_bytes(packet[:-1] + b',"kind":"duplicate"}')

        obj = json.loads(packet)
        obj["untrusted"] = True
        with self.assertRaises(ValueError):
            RootControlActivation.from_bytes(json.dumps(obj, separators=(",", ":")).encode())

        obj = json.loads(packet)
        obj["startup_footprint"]["memory_bytes"] = True
        with self.assertRaises(ValueError):
            RootControlActivation.from_bytes(json.dumps(obj, separators=(",", ":")).encode())

        with self.assertRaises(ValueError):
            RootControlActivation.from_bytes(b'{"bad":NaN}')
        with self.assertRaises(ValueError):
            RootControlActivation.from_bytes(b" " * (ROOT_CONTROL_ACTIVATION_MAX_BYTES + 1))

    def test_outer_signature_cannot_be_reused_after_changing_envelope_revision(self) -> None:
        activation, private_key = _activation()
        altered_envelope = replace(activation.envelope, revision=activation.envelope.revision + 1)
        altered = replace(activation, envelope=altered_envelope)

        with self.assertRaises(InvalidSignature):
            verify_root_control_activation_signature(
                altered,
                trusted_keys={"control-key": private_key.public_key()},
            )

    def test_invalid_footprints_and_incomplete_envelope_are_rejected(self) -> None:
        activation, _ = _activation()
        with self.assertRaises(ValueError):
            RootControlFootprint(True, 1)
        with self.assertRaises(ValueError):
            RootControlFootprint(1, 1, 1)
        with self.assertRaises(ValueError):
            replace(activation, envelope=replace(activation.envelope, active=False))
        with self.assertRaises(ValueError):
            replace(
                activation,
                envelope=replace(
                    activation.envelope,
                    claims=replace(activation.envelope.claims, complete_hierarchy=False),
                ),
            )


if __name__ == "__main__":
    unittest.main()
