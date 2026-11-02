"""Real isolated root process and first control TCP; no production issuer acceptance."""

import base64
import json
import os
import select
import subprocess
import sys
from dataclasses import asdict, replace
from test.postgres.test_admission_metadata_credits import ADMIN, grant
from test.postgres.test_admission_reservations import domain, shared
from test.postgres.test_sql_root_genesis import funded, marked
from test.services.test_admission_bootstrap import principal
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cli_agent_orchestrator.clients.admission_bootstrap_repository import ResourceEnvelopeRepository
from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.resource_process_supervisor import _identity, _matches

pytestmark = pytest.mark.postgres

CHILD = r"""
import base64,json,os,sys
from dataclasses import asdict
from uuid import uuid4
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cli_agent_orchestrator.clients.admission_bootstrap_repository import ResourceEnvelopeRepository
from cli_agent_orchestrator.clients.postgres import DatabaseTarget,connect
from cli_agent_orchestrator.clients.postgres import connection as native
from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository
from cli_agent_orchestrator.models.sql_root_genesis import SignedRootSqlOpening
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission
from cli_agent_orchestrator.services.root_sql_control import current_root_sql_subject,root_sql_control_channel
from cli_agent_orchestrator.utils.process_sql_origin import SqlStartupPermissionUnavailable

target=DatabaseTarget.from_settings()
bootstrap=ResourceBootstrap(repository=ResourceEnvelopeRepository(target_supplier=lambda:target))
subject=current_root_sql_subject(bootstrap)
print(json.dumps(asdict(subject)),flush=True)
certificate=SignedRootSqlOpening.from_bytes(sys.stdin.buffer.readline().rstrip(b'\n'))
public=Ed25519PublicKey.from_public_bytes(base64.b64decode(os.environ['ROOT_TEST_PUBLIC_KEY']))
owner,control=DeliveryAdmission.with_storage_control(AdmissionPolicy())
original_read=SqlIntentRepository.read_root_opening
if os.environ.get('ROOT_TEST_READBACK_LOST')=='1':
    def unavailable_read(self,*args,**kwargs):
        assert original_read(self,*args,**kwargs)==certificate.record
        return None
    SqlIntentRepository.read_root_opening=unavailable_read
original=native.psycopg.connect
attempts=0
def driver(*args,**kwargs):
    global attempts
    attempts+=1
    return original(*args,**kwargs)
native.psycopg.connect=driver
yielded=False
try:
    with root_sql_control_channel(bootstrap,owner,control,certificate,trusted_keys={'test-root':public},clock=lambda:10.0) as channel:
        yielded=True
        row=channel.execute("SELECT 42,current_setting('application_name')").fetchone()
        assert row[0]==42 and row[1]==subject.sql_application_name
        receipt=SqlIntentRepository(target,binding_supplier=bootstrap.shared_repository().current_binding).record(channel,journal_id=certificate.record.journal_id,intent_id=str(uuid4()),path='native',destination=target)
        assert receipt.ordinal==2
    with control.operation(clock=lambda:10.0):
        try:
            with connect(target):raise AssertionError('second root TCP accepted')
        except SqlStartupPermissionUnavailable:pass
    status='accepted'
except RuntimeError:
    status='denied'
assert owner.snapshot(now=10.0).storage_connection_loans==0
print(json.dumps(dict(status=status,drivers=attempts,yielded=yielded)),flush=True)
"""


def api():
    import importlib
    import importlib.util

    name = "cli_agent_orchestrator.services.root_sql_control"
    assert importlib.util.find_spec(name) is not None, "missing root control composition"
    return importlib.import_module(name)


@pytest.mark.parametrize("mode", ["accepted", "wrong_key", "wrong_kernel", "readback_lost"])
def test_one_real_root_control_tcp_requires_committed_signature_and_exact_kernel(
    shared, monkeypatch, mode
):
    module = api()
    from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository
    from cli_agent_orchestrator.clients.sql_opening_schema import SQL_OPENING_MIGRATIONS

    target, admission, issuer_binding, _ = shared
    private = Ed25519PrivateKey.generate()
    environment = dict(os.environ)
    for name in (
        "CAO_INTERNAL_RESOURCE_BINDING_FD",
        "CAO_INTERNAL_SQL_STARTUP_PERMISSION",
        "CAO_INTERNAL_SQL_BROKER_FD",
    ):
        environment.pop(name, None)
    environment.update(
        CAO_INTERNAL_ROOT_SQL_GENESIS="1",
        ROOT_TEST_READBACK_LOST="1" if mode == "readback_lost" else "0",
        ROOT_TEST_PUBLIC_KEY=base64.b64encode(
            private.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        ).decode("ascii"),
    )
    process = subprocess.Popen(
        [sys.executable, "-c", CHILD],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        text=True,
    )
    try:
        assert select.select([process.stdout], [], [], 30)[0], "root identity request timed out"
        line = process.stdout.readline()
        assert line, "root exited before identity request"
        packet = json.loads(line)
        subject_binding = ResourceBinding(**packet.pop("binding"))
        packet["pid_namespace"] = tuple(packet["pid_namespace"])
        subject = module.RootSqlSubject(binding=subject_binding, **packet)
        observed = _identity(process.pid)
        assert subject_binding.process_pid == process.pid and _matches(subject_binding, observed)
        assert (subject.boot_id, subject.start_time_ticks, subject.pid_namespace) == (
            observed.boot_id,
            observed.ticks,
            observed.pid_ns,
        )
        with marked(monkeypatch):
            migrate_component(target, SQL_OPENING_MIGRATIONS)
            records = (domain(shared, 4), domain(shared, 4))
            for record in records:
                admission.provision_member(
                    record.spec.domain_id, subject_binding, expected_revision=0, principal_id=ADMIN
                )
            credits = tuple(grant(shared, record, current=subject_binding) for record in records)
            bootstrap = ResourceBootstrap(
                repository=ResourceEnvelopeRepository(target_supplier=lambda: target),
                binding_supplier=lambda _: issuer_binding,
                instance_supplier=lambda: issuer_binding.instance_id,
            )
            repository = SqlIntentRepository(target, binding_supplier=lambda: issuer_binding)
            journal = str(uuid4())
            if mode == "wrong_kernel":
                subject = replace(subject, start_time_ticks=subject.start_time_ticks + 1)
            signer = Ed25519PrivateKey.generate() if mode == "wrong_key" else private
            with funded(target.migration_target()) as channel:
                repository.provision(
                    channel,
                    journal_id=journal,
                    credit_id=credits[0].credit_id,
                    max_records=3,
                    principal_id=ADMIN,
                    participant_binding=subject_binding,
                )
                certificate = module.issue_root_sql_certificate(
                    bootstrap,
                    channel,
                    principal=principal(),
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    session_id=str(uuid4()),
                    subject=subject,
                    key_supplier=lambda: ("test-root", signer),
                )
            output, errors = process.communicate(
                certificate.to_bytes().decode("ascii") + "\n", timeout=45
            )
            assert process.returncode == 0, errors
            result = json.loads(output)
            expected = (
                dict(status="accepted", drivers=1, yielded=True)
                if mode == "accepted"
                else dict(
                    status="denied", drivers=1 if mode == "readback_lost" else 0, yielded=False
                )
            )
            assert result == expected
            assert all(admission.used(record.spec.domain_id) == 1 for record in records)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def test_signing_key_failure_after_commit_retains_record_and_credit_without_retry(
    shared, monkeypatch
):
    module = api()
    from test.postgres.test_sql_root_genesis import prepare

    from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentConflict

    target, admission, bound, _ = shared
    from cli_agent_orchestrator.clients import sql_intent_repository as repository_module

    bootstrap = ResourceBootstrap(
        repository=ResourceEnvelopeRepository(target_supplier=lambda: target),
        binding_supplier=lambda _: bound,
        instance_supplier=lambda: bound.instance_id,
    )
    live = _identity(os.getpid())
    calls = []

    def unavailable_key():
        calls.append("key")
        raise RuntimeError("controlled private key backend failure")

    with marked(monkeypatch):
        from cli_agent_orchestrator.utils.process_sql_origin import current_sql_application_name

        repository, journal, credits = prepare(shared, repository_module)
        subject = module.RootSqlSubject(
            bound, current_sql_application_name(), live.boot_id, live.ticks, live.pid_ns
        )
        intent, session = str(uuid4()), str(uuid4())
        with funded(target.migration_target()) as channel:
            with pytest.raises(module.RootCertificateUnavailableAfterCommit) as error:
                module.issue_root_sql_certificate(
                    bootstrap,
                    channel,
                    principal=principal(),
                    journal_id=journal,
                    intent_id=intent,
                    session_id=session,
                    subject=subject,
                    key_supplier=unavailable_key,
                )
            retained = repository.read_root_opening(
                channel, journal_id=journal, intent_id=intent, owner=True
            )
            assert retained == error.value.record
            assert retained.intent_id == intent and retained.session_id == session
            assert str(error.value) == "root SQL genesis unavailable"
            with pytest.raises(SqlIntentConflict, match="retained SQL intent"):
                module.issue_root_sql_certificate(
                    bootstrap,
                    channel,
                    principal=principal(),
                    journal_id=journal,
                    intent_id=intent,
                    session_id=session,
                    subject=subject,
                    key_supplier=unavailable_key,
                )
        assert calls == ["key"]
        assert all(admission.used(credit.domain_id) == 1 for credit in credits)
