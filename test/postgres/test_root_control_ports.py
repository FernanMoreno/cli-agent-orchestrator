"""Original root channel reused by runtime ledger and pressure ports, not actor acceptance."""

import base64
import importlib
import importlib.util
import inspect
import json
import os
import select
import subprocess
import sys
from dataclasses import asdict
from test.postgres.test_admission_metadata_credits import ADMIN, grant
from test.postgres.test_admission_reservations import domain, shared
from test.postgres.test_root_sql_control import CHILD as GENESIS_CHILD
from test.postgres.test_sql_root_genesis import funded, marked
from test.services.test_admission_bootstrap import principal
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cli_agent_orchestrator.clients.admission_bootstrap_repository import ResourceEnvelopeRepository
from cli_agent_orchestrator.clients.admission_repository import ResourceAdmissionRepository
from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository
from cli_agent_orchestrator.clients.sql_opening_schema import SQL_OPENING_MIGRATIONS
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.admission_postgres import PostgresPressureSampler
from cli_agent_orchestrator.services.resource_process_supervisor import _identity, _matches
from cli_agent_orchestrator.services.root_sql_control import (
    RootSqlSubject,
    issue_root_sql_certificate,
)

pytestmark = pytest.mark.postgres

# Keep the existing real genesis producer verbatim until its explicit ownership
# boundary. The suffix exercises new production ports, not another bootstrap.
BOUNDARY = "owner,control=DeliveryAdmission.with_storage_control(AdmissionPolicy())"
assert GENESIS_CHILD.count(BOUNDARY) == 1
CHILD = GENESIS_CHILD[: GENESIS_CHILD.index(BOUNDARY)] + r"""
from contextlib import contextmanager
from dataclasses import replace
from itertools import count
from cli_agent_orchestrator.clients import admission_repository as ledger_module
from cli_agent_orchestrator.clients.funded_control_session import FundedControlSession
from cli_agent_orchestrator.clients.postgres.connection import DatabaseError,FundedSqlCommitUncertain
from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.models.admission_domains import SharedResourceCost
from cli_agent_orchestrator.services.admission_postgres import PostgresPressureSampler
from cli_agent_orchestrator.services.shared_delivery_admission import authorize_shared_delivery,SharedDeliveryUnavailable
from cli_agent_orchestrator.security.auth import _verified_principal,SCOPE_ADMIN

mode=os.environ['ROOT_PORT_MODE']
owner,control=DeliveryAdmission.with_storage_control(AdmissionPolicy())
original=native.psycopg.connect
attempts=0
def driver(*args,**kwargs):
    global attempts
    attempts+=1
    return original(*args,**kwargs)
native.psycopg.connect=driver
backend_pids=[]
connect_calls=[]
def no_connect(*args,**kwargs):
    connect_calls.append(True)
    raise AssertionError('bound control port attempted another TCP')
with root_sql_control_channel(bootstrap,owner,control,certificate,trusted_keys={'test-root':public},clock=lambda:10.0) as channel:
    backend=channel.execute('SELECT pg_backend_pid()').fetchone()[0]
    old_timeout=channel.execute('SHOW statement_timeout').fetchone()[0]
    binding=subject.binding
    current=[binding]
    with FundedControlSession(channel) as session:
        ledger_module.connect=no_connect
        repo=ledger_module.ResourceAdmissionRepository(target,binding_supplier=lambda:current[0],control_session=session)
        ticks=count(10)
        sampler=PostgresPressureSampler(target_supplier=lambda:target,connect_factory=no_connect,clock=lambda:float(next(ticks)))
        sample_times=[]
        assert repo.audit_usage()==()
        for iteration in range(2):
            coverage=repo.metadata_coverage_current()
            assert coverage.binding==binding and set(coverage.credits)==set(repo.metadata_credits_current())
            assert all(repo.used(c.domain_id)==1 for c in coverage.credits)
            page=repo.outstanding_resources(limit=100)
            assert len(page.entries)==len(coverage.credits)
            observed=sampler.sample_control(session)
            assert observed.reason=='pressure_observed' and observed.observed_backends>=1
            sample_times.append(observed.observation.observed_at)
            assert sampler.retire_owned() is True
            backend_pids.append(channel.execute('SELECT pg_backend_pid()').fetchone()[0])
            assert channel.execute('SHOW statement_timeout').fetchone()[0]==old_timeout
            assert channel.execute('SHOW transaction_read_only').fetchone()[0]=='off'
        assert sample_times[1]>sample_times[0]
        # A returned coverage value and a reusable port do not authorize enabling
        # shared delivery over this original live loan.
        admin=_verified_principal('urn:root-control-test','admin',[SCOPE_ADMIN],'jwt')
        try:
            authorize_shared_delivery(principal=admin,repository=repo,owner=owner,control=control,retirement=lambda:False)
        except SharedDeliveryUnavailable:pass
        else:raise AssertionError('operational session became activation authority')
        assert owner._shared_coordinator is None
        try:repo.provision_domain(None,expected_revision=0,principal_id=admin.id)
        except (ValueError,ledger_module.SharedAdmissionDenied):pass
        else:raise AssertionError('owner writer accepted bound runtime session')
        # Call _owner directly to ensure refusal occurs before any connection or
        # migration credentials, rather than merely a DTO validation failure.
        try:
            with repo._owner(admin.id):raise AssertionError('owner scope entered')
        except ledger_module.SharedAdmissionDenied:pass
        current[0]=replace(binding,resource_view_sha256='f'*64)
        try:repo.used(coverage.credits[0].domain_id)
        except ledger_module.SharedAdmissionDenied:pass
        else:raise AssertionError('changed binding admitted')
        current[0]=binding
        if mode=='rollback':
            try:
                with session.transaction() as connection:
                    repo._query(connection,'UPDATE {usage} SET committed=committed+1')
                    raise ValueError('controlled body failure')
            except ValueError:pass
            assert repo.audit_usage()==()
            try:
                with session.transaction(readonly=True) as connection:
                    assert connection.read_only is True
                    repo._query(connection,'UPDATE {usage} SET committed=committed WHERE false')
            except DatabaseError:pass
            else:raise AssertionError('readonly transaction accepted a ledger write')
            assert repo.audit_usage()==()
            from cli_agent_orchestrator.services import admission_postgres as pressure_module
            from cli_agent_orchestrator.services.admission_postgres import PostgresPressurePolicy
            original_sql=pressure_module._PRESSURE_SQL
            original_policy=sampler._policy
            assert sampler.configure(PostgresPressurePolicy(statement_timeout_ms=100))
            pressure_module._PRESSURE_SQL='SELECT pg_sleep(1)'
            try:
                assert sampler.sample_control(session).reason=='postgres_unavailable'
            finally:
                pressure_module._PRESSURE_SQL=original_sql
                assert sampler.configure(original_policy)
            assert sampler.sample_control(session).reason=='pressure_observed'
            assert channel.execute('SHOW statement_timeout').fetchone()[0]==old_timeout
            assert sampler.retire_owned() is True
        operation=str(uuid4())
        cost=SharedResourceCost(storage_slots=1)
        if mode in ('commit_ack','postcheck'):
            original_transaction=channel.funded_transaction
            original_idle=session._check_idle
            committed=[False]
            def fail_postcheck():
                original_idle()
                if committed[0]:raise StorageAdmissionUnavailable()
            @contextmanager
            def lost_ack():
                with original_transaction() as connection:yield connection
                committed[0]=True
                if mode=='commit_ack':raise FundedSqlCommitUncertain('controlled acknowledged commit lost')
            channel.funded_transaction=lost_ack
            if mode=='postcheck':session._check_idle=fail_postcheck
            try:repo.reserve_for_cost(operation,cost)
            except ledger_module.SharedCommitUncertain:pass
            else:raise AssertionError('unconfirmed commit granted reservation')
            channel.funded_transaction=original_transaction
            session._check_idle=original_idle
            try:repo.read_operation(operation)
            except StorageAdmissionUnavailable:pass
            else:raise AssertionError('uncertain session reused')
        else:
            receipt=repo.reserve_for_cost(operation,cost)
            assert receipt.state=='preparing'
            receipt=repo.transition(operation,expected_revision=receipt.revision,state='active')
            receipt=repo.transition(operation,expected_revision=receipt.revision,state='uncertain')
            assert receipt.state=='uncertain'
            assert repo.read_operation(operation)==receipt
            assert all(repo.used(c.domain_id)==2 for c in coverage.credits)
        assert channel.raw.closed is False
    assert channel.raw.closed is False
    assert not connect_calls
    assert all(value==backend for value in backend_pids)
assert owner.snapshot(now=10.0).storage_connection_loans==0
print(json.dumps(dict(mode=mode,drivers=attempts,same_backend=True,operation=operation)),flush=True)
"""


def require_api():
    name = "cli_agent_orchestrator.clients.funded_control_session"
    assert importlib.util.find_spec(name) is not None, "missing funded control session"
    assert "control_session" in inspect.signature(ResourceAdmissionRepository).parameters
    assert callable(getattr(PostgresPressureSampler, "sample_control", None))


@pytest.mark.parametrize("mode", ["normal", "rollback", "commit_ack", "postcheck"])
def test_root_runtime_ports_reuse_one_actual_backend(shared, monkeypatch, mode):
    require_api()
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
        ROOT_PORT_MODE=mode,
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
        assert select.select([process.stdout], [], [], 30)[0], "root subject timeout"
        packet = json.loads(process.stdout.readline())
        bound = ResourceBinding(**packet.pop("binding"))
        packet["pid_namespace"] = tuple(packet["pid_namespace"])
        subject = RootSqlSubject(binding=bound, **packet)
        identity = _identity(process.pid)
        assert bound.process_pid == process.pid and _matches(bound, identity)
        assert (subject.boot_id, subject.start_time_ticks, subject.pid_namespace) == (
            identity.boot_id,
            identity.ticks,
            identity.pid_ns,
        )
        with marked(monkeypatch):
            migrate_component(target, SQL_OPENING_MIGRATIONS)
            records = (domain(shared, 4), domain(shared, 4))
            for record in records:
                admission.provision_member(
                    record.spec.domain_id, bound, expected_revision=0, principal_id=ADMIN
                )
            credits = tuple(grant(shared, record, current=bound) for record in records)
            bootstrap = ResourceBootstrap(
                repository=ResourceEnvelopeRepository(target_supplier=lambda: target),
                binding_supplier=lambda _: issuer_binding,
                instance_supplier=lambda: issuer_binding.instance_id,
            )
            repository = SqlIntentRepository(target, binding_supplier=lambda: issuer_binding)
            journal = str(uuid4())
            with funded(target.migration_target()) as channel:
                repository.provision(
                    channel,
                    journal_id=journal,
                    credit_id=credits[0].credit_id,
                    max_records=3,
                    principal_id=ADMIN,
                    participant_binding=bound,
                )
                certificate = issue_root_sql_certificate(
                    bootstrap,
                    channel,
                    principal=principal(),
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    session_id=str(uuid4()),
                    subject=subject,
                    key_supplier=lambda: ("test-root", private),
                )
            output, errors = process.communicate(
                certificate.to_bytes().decode("ascii") + "\n", timeout=45
            )
            assert process.returncode == 0, errors
            result = json.loads(output)
            assert result["mode"] == mode and result["drivers"] == 1 and result["same_backend"]
            # A successful wrapper close and a failed commit acknowledgement
            # both retain the durable metadata and operation debt.
            assert all(admission.used(record.spec.domain_id) == 2 for record in records)
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
