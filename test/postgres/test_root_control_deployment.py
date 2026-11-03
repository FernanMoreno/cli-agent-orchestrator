"""Actual Root CLI/private transport/PG composition; parent compute is declared synthetic."""

import base64
import importlib
import importlib.util
import json
import os
import signal
import sys
from dataclasses import asdict
from pathlib import Path
from test.postgres import fixtures as postgres_fixtures
from test.postgres.test_admission_metadata_credits import ADMIN, grant
from test.postgres.test_admission_reservations import domain, shared
from test.postgres.test_sql_root_genesis import funded, marked
from test.services.test_admission_bootstrap import principal
from test.services.test_admission_command_reservation import _command, _owner
from uuid import uuid4

import psutil
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg import sql

import cli_agent_orchestrator
from cli_agent_orchestrator.clients.admission_bootstrap_repository import ResourceEnvelopeRepository
from cli_agent_orchestrator.clients.admission_bootstrap_schema import (
    RESOURCE_ENVELOPE_MIGRATIONS,
    RESOURCE_ENVELOPE_TABLE,
)
from cli_agent_orchestrator.clients.admission_repository import ResourceAdmissionRepository
from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository
from cli_agent_orchestrator.clients.sql_opening_schema import SQL_OPENING_MIGRATIONS
from cli_agent_orchestrator.models.resource_envelope import EnvelopeClaims
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy, ResourceAmounts
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.services.resource_process_supervisor import ResourceProcessSupervisor

pytestmark = pytest.mark.postgres

NATIVE_ROOT_CLI = r"""
import os,runpy,sys
from pathlib import Path
import cli_agent_orchestrator
repository=Path(os.environ['ROOT_CONTROL_REPOSITORY'])
assert Path.cwd()==repository==Path(cli_agent_orchestrator.__file__).parents[2]
class PrivateProtocolOnly:
    encoding='utf-8'
    @property
    def buffer(self): return self
    def readline(self,*args): raise AssertionError('private Root read inherited stdin')
    def read(self,*args): raise AssertionError('private Root read inherited stdin')
    def write(self,*args): raise AssertionError('private Root wrote inherited stdout')
    def flush(self): pass
    def isatty(self): return False
sys.stdin=PrivateProtocolOnly()
sys.stdout=PrivateProtocolOnly()
sys.argv=['cli_agent_orchestrator.runtime_channel.root_control',*sys.argv[1:]]
runpy.run_module('cli_agent_orchestrator.runtime_channel.root_control',run_name='__main__')
"""


def deployment_api():
    name = "cli_agent_orchestrator.services.root_control_deployment"
    assert importlib.util.find_spec(name) is not None, "missing Root deployment composition"
    return importlib.import_module(name)


def test_root_deployment_api_exists():
    module = deployment_api()
    assert callable(module.RootControlDeployment.launch)
    assert isinstance(module.RootControlDeploymentUnavailable, type)


def cleanup_owned(owner):
    if owner is None or not owner.has_native_owner:
        return False
    if owner.cleanup(timeout_s=0):
        return True
    owner.send_signal(signal.SIGTERM)
    if not owner.cleanup(timeout_s=20):
        owner.send_signal(signal.SIGKILL)
        assert owner.cleanup(timeout_s=20), "exact Root child could not be closed"
    return True


def finalize_owned(deployment, reservation, primary_error):
    errors = []
    closed = False
    for owner in (deployment, reservation._process_worker):
        try:
            if cleanup_owned(owner):
                closed = True
                break
        except BaseException as error:
            errors.append(error)
    if not closed and any(owner is not None for owner in (deployment, reservation._process_worker)):
        errors.append(AssertionError("exact Root owner closure remained unconfirmed"))
    if not errors:
        return
    if primary_error is not None:
        add_note = getattr(primary_error, "add_note", None)
        if callable(add_note):
            add_note(
                "Root teardown encountered an error; exact reservation-owner fallback "
                + ("confirmed closure." if closed else "could not confirm closure.")
            )
        return
    raise errors[0]


def provision_from_operator_worker(ledger, footprint, target, issuer_binding, child_binding):
    """Use a separately funded operator; never borrow the issuer's live loan."""
    reservation = _command(ledger, footprint).reservation
    assert reservation is not None

    def provision():
        assert reservation.state == "started"
        assert not ledger._guard._is_owned()
        operator = ResourceAdmissionRepository(target, binding_supplier=lambda: issuer_binding)
        operator_ledger, permit = DeliveryAdmission.with_storage_control(AdmissionPolicy())
        operator_shared = (
            target,
            operator,
            issuer_binding,
            importlib.import_module("cli_agent_orchestrator.clients.admission_repository"),
        )
        with permit.operation(clock=lambda: 10.0):
            records = (domain(operator_shared, 4), domain(operator_shared, 4))
            assert all(hasattr(record, "spec") for record in records)
            for record in records:
                operator.provision_member(
                    record.spec.domain_id, child_binding, expected_revision=0, principal_id=ADMIN
                )
            credits = tuple(
                grant(operator_shared, record, current=child_binding) for record in records
            )
        assert operator_ledger.snapshot(now=10.0).storage_connection_loans == 0
        return records, credits

    worker = reservation._launch_thread(provision, clock=lambda: 1.0)
    assert worker is not None
    assert worker.join(timeout=30), "funded operator worker did not close"
    assert reservation.state == "released"
    return worker.result(timeout=0)


@pytest.mark.parametrize("mode", ("healthy", "wrong_key", "eof_before_activation"))
def test_real_root_cli_private_owner_and_issuer(shared, monkeypatch, tmp_path, mode):
    module = deployment_api()
    repository = Path(cli_agent_orchestrator.__file__).parents[2]
    assert Path.cwd() == repository == Path(postgres_fixtures.__file__).parents[2]
    assert psutil.virtual_memory().available >= 6144 * 1024**2
    target, admission, issuer_binding, _ = shared
    signing_key = Ed25519PrivateKey.generate()
    trusted_key = (
        Ed25519PrivateKey.generate().public_key()
        if mode == "wrong_key"
        else signing_key.public_key()
    )
    trust_file = tmp_path / "private-root-trust.json"
    policy_file = tmp_path / "private-root-policy.json"
    trust_file.write_text(
        json.dumps(
            {
                "version": 1,
                "keys": {
                    "test-root": base64.b64encode(
                        trusted_key.public_bytes(
                            serialization.Encoding.Raw, serialization.PublicFormat.Raw
                        )
                    ).decode("ascii")
                },
            }
        )
    )
    policy_file.write_text(json.dumps({"version": 1, "policy": {}}))
    trust_file.chmod(0o600)
    policy_file.chmod(0o600)
    environment = dict(os.environ)
    for name in (
        "CAO_INTERNAL_RESOURCE_BINDING_FD",
        "CAO_INTERNAL_SQL_STARTUP_PERMISSION",
        "CAO_INTERNAL_SQL_BROKER_FD",
        "CAO_INTERNAL_ROOT_CONTROL_FD",
    ):
        environment.pop(name, None)
    environment.update(
        CAO_INTERNAL_ROOT_SQL_GENESIS="1",
        ROOT_CONTROL_REPOSITORY=str(repository),
        PYTHONPATH=os.pathsep.join((str(repository / "src"), str(repository))),
    )
    argv = [
        sys.executable,
        "-c",
        NATIVE_ROOT_CLI,
        "--trust-file",
        str(trust_file),
        "--policy-file",
        str(policy_file),
        "--scope",
        "root/private/" + mode,
        "--close-timeout",
        "5",
    ]
    ledger = _owner(memory=64 * 1024**2, cpu=4000, storage=8)
    footprint = ResourceAmounts(1024**2, 10, 0)
    reservation = _command(ledger, footprint).reservation
    assert reservation is not None
    native_launch = ResourceProcessSupervisor.launch
    starts = []

    def observe_native_launch(cls, *arguments, **keywords):
        assert reservation.state == "started"
        assert ledger.snapshot(now=1.0).committed == footprint
        assert not ledger._guard._is_owned()
        assert keywords["root_control_channel"] is True
        starts.append("started")
        return native_launch(*arguments, **keywords)

    monkeypatch.setattr(ResourceProcessSupervisor, "launch", classmethod(observe_native_launch))
    deployment = None
    try:
        with marked(monkeypatch):
            migrate_component(target, SQL_OPENING_MIGRATIONS)
            migrate_component(target, RESOURCE_ENVELOPE_MIGRATIONS)
            issuer = ResourceBootstrap(
                repository=ResourceEnvelopeRepository(target_supplier=lambda: target),
                binding_supplier=lambda _: issuer_binding,
                instance_supplier=lambda: issuer_binding.instance_id,
            )
            with funded(target.migration_target()) as channel:
                try:
                    deployment = module.RootControlDeployment.launch(
                        bootstrap=issuer,
                        channel=channel,
                        principal=principal(),
                        reservation=reservation,
                        argv=argv,
                        env=environment,
                        clock=lambda: 1.0,
                        binding_timeout_s=30,
                    )
                except module.RootControlDeploymentUnavailable as error:
                    deployment = error.deployment
                    raise
                subject = deployment.subject
                assert starts == ["started"]
                bound = subject.binding
                assert reservation.state == "started"
                assert ledger.snapshot(now=1.0).committed == footprint
                assert not reservation.finish()
                assert (
                    channel.execute(
                        "SELECT count(*) FROM pg_stat_activity WHERE application_name=%s",
                        (subject.sql_application_name,),
                    ).fetchone()[0]
                    == 0
                )
                if mode == "eof_before_activation":
                    assert deployment.record is None
                    assert deployment.cleanup(timeout_s=30)
                    assert not deployment.join(timeout_s=0)
                    assert ledger.snapshot(now=1.0).committed == footprint
                    assert (
                        channel.execute(
                            "SELECT count(*) FROM pg_stat_activity WHERE application_name=%s",
                            (subject.sql_application_name,),
                        ).fetchone()[0]
                        == 0
                    )
                    return

                # Operator provisioning is explicit, outside the deployment service.
                records, credits = provision_from_operator_worker(
                    ledger, footprint, target, issuer_binding, bound
                )
                assert ledger.snapshot(now=1.0).committed == footprint
                values = {
                    **asdict(bound),
                    **asdict(EnvelopeClaims(4 * 1024**3, 16000, 64, True, False)),
                    "revision": 1,
                    "active": True,
                    "protocol_version": 1,
                    "actor_principal_id": ADMIN,
                }
                with channel.funded_transaction():
                    channel.execute(
                        sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                            sql.Identifier(target.schema, RESOURCE_ENVELOPE_TABLE),
                            sql.SQL(",").join(map(sql.Identifier, values)),
                            sql.SQL(",").join(sql.Placeholder() for _ in values),
                        ),
                        tuple(values.values()),
                    )
                journal, intent, session = (str(uuid4()) for _ in range(3))
                intents = SqlIntentRepository(target, binding_supplier=lambda: issuer_binding)
                intents.provision(
                    channel,
                    journal_id=journal,
                    credit_id=credits[0].credit_id,
                    max_records=3,
                    principal_id=ADMIN,
                    participant_binding=bound,
                )
                key_calls = []

                def key_supplier():
                    assert channel.in_transaction is False
                    assert not ledger._guard._is_owned()
                    key_calls.append("key")
                    if mode == "healthy":
                        # The real issuer calls this after commit, before the
                        # packet reaches the child. Same-task callback reentry
                        # must not operate or retire that waiting process.
                        controls = (
                            lambda: deployment.refresh(timeout_s=0),
                            lambda: deployment.stop(timeout_s=0),
                            lambda: deployment.join(timeout_s=0),
                            lambda: deployment.cleanup(timeout_s=0),
                            lambda: deployment.send_signal(signal.SIGTERM),
                        )
                        for control in controls:
                            with pytest.raises(module.RootControlDeploymentUnavailable):
                                control()
                        assert ledger.snapshot(now=1.0).committed == footprint
                    return "test-root", signing_key

                arguments = dict(
                    journal_id=journal,
                    intent_id=intent,
                    session_id=session,
                    key_supplier=key_supplier,
                    startup_footprint=footprint,
                    refresh_footprint=footprint,
                    timeout_s=30,
                )
                if mode == "wrong_key":
                    with pytest.raises(module.RootControlDeploymentUnavailable):
                        deployment.activate(**arguments)
                    assert ledger.snapshot(now=1.0).command_started == 1
                    assert ledger.snapshot(now=1.0).committed == footprint
                    assert (
                        channel.execute(
                            "SELECT count(*) FROM pg_stat_activity WHERE application_name=%s",
                            (subject.sql_application_name,),
                        ).fetchone()[0]
                        == 0
                    )
                else:
                    deployment.activate(**arguments)
                    assert (
                        channel.execute(
                            "SELECT count(*) FROM pg_stat_activity WHERE application_name=%s",
                            (subject.sql_application_name,),
                        ).fetchone()[0]
                        == 1
                    )
                    with pytest.raises(module.RootControlDeploymentUnavailable):
                        deployment.activate(**arguments)
                    reason = deployment.refresh(timeout_s=30)
                    assert isinstance(reason, str) and reason
                    deployment.stop(timeout_s=30)
                    assert ledger.snapshot(now=1.0).command_started == 1
                assert key_calls == ["key"]
                assert deployment.record is not None
                assert (
                    intents.read_root_opening(
                        channel, journal_id=journal, intent_id=intent, owner=True
                    )
                    == deployment.record
                )
                assert deployment.join(timeout_s=30)
                assert not reservation.finish()
                assert ledger.snapshot(now=1.0).committed == ResourceAmounts()
                assert all(
                    admission._used(channel, record.spec.domain_id) == 1 for record in records
                )
    finally:
        finalize_owned(deployment, reservation, sys.exc_info()[1])
