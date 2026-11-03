"""Signed root and native PG for one funded actor; synthetic compute observations."""

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
from pathlib import Path
from test.postgres import fixtures as postgres_fixtures
from test.postgres.test_admission_metadata_credits import ADMIN, grant
from test.postgres.test_admission_reservations import domain, shared
from test.postgres.test_root_sql_control import CHILD as GENESIS_CHILD
from test.postgres.test_sql_root_genesis import funded, marked
from test.services.test_admission_bootstrap import principal
from uuid import uuid4

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
from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository
from cli_agent_orchestrator.clients.sql_opening_schema import SQL_OPENING_MIGRATIONS
from cli_agent_orchestrator.models.resource_envelope import EnvelopeClaims, ResourceBinding
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap
from cli_agent_orchestrator.services.resource_process_supervisor import _identity, _matches
from cli_agent_orchestrator.services.root_sql_control import (
    RootSqlSubject,
    issue_root_sql_certificate,
)

pytestmark = pytest.mark.postgres


def test_original_control_owner_api_exists():
    name = "cli_agent_orchestrator.services.original_control_owner"
    assert importlib.util.find_spec(name) is not None, "missing original funded control owner"
    module = importlib.import_module(name)
    assert "startup" in inspect.signature(module.OriginalControlOwner).parameters
    for method in ("wait_ready", "refresh", "aclose"):
        assert inspect.iscoroutinefunction(getattr(module.OriginalControlOwner, method))
    assert callable(module.OriginalControlOwner.start)
    assert callable(module.OriginalControlOwner.diagnostics)


BOUNDARY = "owner,control=DeliveryAdmission.with_storage_control(AdmissionPolicy())"
assert GENESIS_CHILD.count(BOUNDARY) == 1
# Preserve the existing subject/certificate producer up to its original channel boundary.
CHILD_PREFLIGHT = r"""
import os
from pathlib import Path
import cli_agent_orchestrator
import test.postgres.fixtures
repository=Path(os.environ['ROOT_ACTOR_REPOSITORY'])
assert Path.cwd()==repository==Path(test.postgres.fixtures.__file__).parents[2]
assert Path(cli_agent_orchestrator.__file__).parents[2]==repository
"""
CHILD = CHILD_PREFLIGHT + GENESIS_CHILD[: GENESIS_CHILD.index(BOUNDARY)] + r"""
import asyncio,threading,time
from cli_agent_orchestrator.clients.funded_control_session import FundedControlSession
from cli_agent_orchestrator.clients.postgres.connection import FundedSqlCommitUncertain
from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.services.admission_policy import CapacityObservation,ResourceAmounts
from cli_agent_orchestrator.services.admission_resources import ResourceSample
from cli_agent_orchestrator.services.admission_settings import AdmissionPolicyLoader
from cli_agent_orchestrator.services.admission_postgres import PostgresPressureSampler
from cli_agent_orchestrator.services.admission_monitor import AdmissionMonitor
from cli_agent_orchestrator.services.original_control_owner import OriginalControlConfig,OriginalControlOwner

mode=os.environ['ROOT_ACTOR_MODE']
main_thread=threading.current_thread()
drivers=[]
query_threads=[]
sample_threads=[]
unexpected_connects=[]
poison_seen=threading.Event()
post_poison_queries=[]
poison_next=[False]
block_next=[False]
release=threading.Event()
notify_entered=[None]
notify_capacity=[None]
published_cycles=[]
original_connect=native.psycopg.connect
original_execute=native.Connection.execute
original_idle=FundedControlSession._check_idle
original_refresh=AdmissionMonitor.refresh
original_run_refresh=OriginalControlOwner._run_refresh
protected_refreshes=[]

def protected_refresh(self,*args):
    protected_refreshes.append(True)
    return original_run_refresh(self,*args)

def refresh_cycle(self):
    result=original_refresh(self)
    if result.published:published_cycles.append(result.reason)
    if notify_capacity[0] is not None and self.owner.snapshot(now=time.monotonic()).reason=='capacity_available':
        notify_capacity[0]()
    return result

def driver(*args,**kwargs):
    assert threading.current_thread() is not main_thread,'SQL opened outside funded actor'
    raw=original_connect(*args,**kwargs)
    drivers.append((raw.info.backend_pid,threading.get_ident()))
    return raw
def execute(self,*args,**kwargs):
    assert threading.current_thread() is not main_thread,'SQL outside original worker'
    if poison_seen.is_set():post_poison_queries.append(True)
    assert not poison_seen.is_set(),'poisoned actor attempted another SQL operation'
    query_threads.append(threading.get_ident())
    return original_execute(self,*args,**kwargs)
def no_connect(*args,**kwargs):
    unexpected_connects.append(True)
    raise AssertionError('bound actor attempted fallback connection')
def no_unbound_sample(self):
    raise AssertionError('bound actor used a new pressure connection')
def idle(self):
    original_idle(self)
    if poison_next[0] and self._busy:
        poison_next[0]=False
        poison_seen.set()
        raise StorageAdmissionUnavailable()
native.psycopg.connect=driver
native.Connection.execute=execute
FundedControlSession._check_idle=idle
PostgresPressureSampler.sample=no_unbound_sample
AdmissionMonitor.refresh=refresh_cycle
OriginalControlOwner._run_refresh=protected_refresh
bootstrap=ResourceBootstrap(repository=ResourceEnvelopeRepository(target_supplier=lambda:target,connect_factory=no_connect),clock=time.monotonic)
owner,control=DeliveryAdmission.with_storage_control(AdmissionPolicy())

def prepare(admission):
    now=time.monotonic()
    assert admission.observe(CapacityObservation(now,1024*1024*1024,1024*1024*1024,16000,16000,0,'known'),now=now,generation=admission.generation)
    token=admission.try_reserve_command(footprint=ResourceAmounts(1024*1024,10,0),now=time.monotonic()).reservation
    assert token is not None
    return token

startup=prepare(owner)
if mode=='wrong_owner':
    other,other_control=DeliveryAdmission.with_storage_control(AdmissionPolicy())
    assert startup.cancel_before_start()
    startup=prepare(other)
if mode=='shared_unready':owner.require_shared_admission()

class Resources:
    def reset_history(self):pass
    def sample(self,*,storage):
        assert threading.current_thread() is not main_thread
        try:task=asyncio.current_task()
        except RuntimeError:task=None
        assert task is None,'actor changed asyncio task'
        sample_threads.append(threading.get_ident())
        if block_next[0]:
            block_next[0]=False
            notify_entered[0]()
            assert release.wait(10),'owned test barrier did not release'
        return ResourceSample(CapacityObservation(time.monotonic(),4096*1024*1024,4096*1024*1024,16000,16000,storage.available_slots,'known'),())

config=OriginalControlConfig(bootstrap=bootstrap,owner=owner,control=control,certificate=certificate,trusted_keys={'test-root':public},policy_loader=AdmissionPolicyLoader(source=lambda:{'version':1,'policy':{}}),scope_id='root/actor',command_footprint=ResourceAmounts(1024*1024,10,0),clock=time.monotonic,resource_factory=lambda _:Resources())
if mode in ('missing_startup','wrong_owner','shared_unready'):
    denied=False
    try:
        actor=OriginalControlOwner(config,startup=None if mode=='missing_startup' else startup)
        actor.start()
    except StorageAdmissionUnavailable:denied=True
    assert denied and not drivers and not query_threads
    startup.cancel_before_start()
    print(json.dumps(dict(mode=mode,drivers=0,backend_count=0,thread_count=0,reply_count=0)),flush=True)
elif mode=='start_ack_lost':
    actor=OriginalControlOwner(config,startup=startup)
    native_start=threading.Thread.start
    def lost_ack(thread):
        native_start(thread)
        raise RuntimeError('lost native start acknowledgement')
    threading.Thread.start=lost_ack
    try:
        try:actor.start()
        except StorageAdmissionUnavailable:pass
        else:raise AssertionError('lost start acknowledgement was accepted')
    finally:threading.Thread.start=native_start
    asyncio.run(actor.aclose(timeout=5))
    assert actor.retired and not drivers and not query_threads
    assert startup.state=='released'
    print(json.dumps(dict(mode=mode,drivers=0,backend_count=0,thread_count=0,reply_count=0,startup_retired=True)),flush=True)
else:
    actor=OriginalControlOwner(config,startup=startup)
    actor.start()
    async def run():
        await actor.wait_ready()
        assert len(drivers)==1 and len(set(query_threads))==1
        diagnostic=actor.diagnostics(version=2)
        assert diagnostic.operations.commands.inflight==1
        # Ready proves the first cycle, while the unchanged production policy
        # requires sustained healthy samples before allowing resource growth.
        capacity_ready=asyncio.Event()
        loop=asyncio.get_running_loop()
        notify_capacity[0]=lambda:loop.call_soon_threadsafe(capacity_ready.set)
        if owner.snapshot(now=time.monotonic()).reason=='capacity_available':capacity_ready.set()
        await asyncio.wait_for(capacity_ready.wait(),5)
        assert len(published_cycles)>=actor._monitor._configuration.healthy_samples_before_growth
        replies=0
        if mode=='healthy':
            for _ in range(3):
                result=await actor.refresh()
                assert result.published,(result,owner.snapshot(now=time.monotonic()))
                replies+=1
            results=await asyncio.gather(*(actor.refresh() for _ in range(25)))
            assert all(value.published for value in results)
            replies+=len(results)
            assert len(sample_threads)>=replies+1
            # Consume the actual remaining declared compute quantities, rather
            # than introducing a numeric queue/worker limit. The original SQL
            # channel remains live; a refused message never reaches its worker.
            with owner._guard:
                capacity=owner._current_decision(time.monotonic(),charged=False).resource_capacity
                remaining=capacity.excess_over(owner._committed)
                footprint=ResourceAmounts(remaining.memory_bytes,remaining.cpu_millicores,0)
                external=owner.try_reserve_command(footprint=footprint,now=time.monotonic()).reservation
                assert external is not None and external.start(now=time.monotonic())
            try:
                before=len(protected_refreshes)
                refused=await actor.refresh()
                assert not refused.published and refused.reason=='resource_exhausted',refused
                assert len(protected_refreshes)==before==replies
                assert len(drivers)==1 and owner.snapshot(now=time.monotonic()).command_started==2
            finally:
                assert external.finish()
            await actor.aclose(timeout=5)
        elif mode=='cancel':
            entered=asyncio.Event()
            loop=asyncio.get_running_loop()
            notify_entered[0]=lambda:loop.call_soon_threadsafe(entered.set)
            block_next[0]=True
            waiting=asyncio.create_task(actor.refresh())
            try:
                await asyncio.wait_for(entered.wait(),5)
                waiting.cancel()
                try:await waiting
                except asyncio.CancelledError:pass
                else:raise AssertionError('cancelled waiter returned')
                assert owner.snapshot(now=time.monotonic()).command_started==2
                assert actor.diagnostics(version=2).operations.commands.inflight==2
                try:await actor.aclose(timeout=0.01)
                except TimeoutError:pass
                else:raise AssertionError('close ignored running callback')
                assert not actor.retired
                assert owner.snapshot(now=time.monotonic()).command_started==2
            finally:release.set()
            await actor.aclose(timeout=5)
        elif mode=='poison':
            poison_next[0]=True
            try:refused=await actor.refresh()
            except (FundedSqlCommitUncertain,StorageAdmissionUnavailable):pass
            else:assert not refused.published and refused.reason=='stopping'
            assert poison_seen.is_set()
            before=len(query_threads)
            try:refused=await actor.refresh()
            except StorageAdmissionUnavailable:pass
            else:assert not refused.published and refused.reason=='stopping'
            try:await actor.aclose(timeout=5)
            except (FundedSqlCommitUncertain,StorageAdmissionUnavailable):pass
            assert len(query_threads)==before
            assert not post_poison_queries
        else:raise AssertionError('unknown owned mode')
        assert actor.retired
        status=owner.snapshot(now=time.monotonic())
        assert status.command_inflight==status.command_started==0
        assert status.storage_connection_loans==status.control_connection_loans==0
        closed=actor.diagnostics(version=2)
        assert not closed.accepting and closed.operations.commands.inflight==0
        assert len(drivers)==1 and len({p for p,t in drivers})==1
        assert set(query_threads)==set(sample_threads)=={drivers[0][1]}
        assert not unexpected_connects
        return replies
    try:
        replies=asyncio.run(run())
    finally:
        primary=sys.exc_info()[1]
        if not actor.retired:
            try:asyncio.run(actor.aclose(timeout=5))
            except BaseException:
                if primary is None:raise
    print(json.dumps(dict(mode=mode,drivers=len(drivers),backend_count=len({p for p,t in drivers}),thread_count=len(set(query_threads)),reply_count=replies,saturation_checked=mode=='healthy')),flush=True)
"""


@pytest.mark.parametrize(
    "mode",
    [
        "healthy",
        "cancel",
        "poison",
        "missing_startup",
        "wrong_owner",
        "shared_unready",
        "start_ack_lost",
    ],
)
def test_signed_original_control_owner_uses_one_actual_worker_and_backend(
    shared, monkeypatch, mode
):
    test_original_control_owner_api_exists()
    repository = Path(cli_agent_orchestrator.__file__).parents[2]
    assert Path.cwd() == repository == Path(postgres_fixtures.__file__).parents[2]
    assert Path(os.environ["PYTHONPATH"].split(os.pathsep)[0]) == repository / "src"
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
        ROOT_ACTOR_MODE=mode,
        ROOT_ACTOR_REPOSITORY=str(repository),
        PYTHONPATH=os.pathsep.join((str(repository / "src"), str(repository))),
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
        cwd=repository,
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
            migrate_component(target, RESOURCE_ENVELOPE_MIGRATIONS)
            records = (domain(shared, 4), domain(shared, 4))
            assert all(hasattr(record, "spec") for record in records)
            for record in records:
                admission.provision_member(
                    record.spec.domain_id, bound, expected_revision=0, principal_id=ADMIN
                )
            credits = tuple(grant(shared, record, current=bound) for record in records)
            issuer = ResourceBootstrap(
                repository=ResourceEnvelopeRepository(target_supplier=lambda: target),
                binding_supplier=lambda _: issuer_binding,
                instance_supplier=lambda: issuer_binding.instance_id,
            )
            journal = str(uuid4())
            repository = SqlIntentRepository(target, binding_supplier=lambda: issuer_binding)
            with funded(target.migration_target()) as channel:
                values = {
                    **asdict(bound),
                    **asdict(EnvelopeClaims(4096 * 1024 * 1024, 16000, 64, True, False)),
                    "revision": 1,
                    "protocol_version": 1,
                    "active": True,
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
                repository.provision(
                    channel,
                    journal_id=journal,
                    credit_id=credits[0].credit_id,
                    max_records=3,
                    principal_id=ADMIN,
                    participant_binding=bound,
                )
                certificate = issue_root_sql_certificate(
                    issuer,
                    channel,
                    principal=principal(),
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    session_id=str(uuid4()),
                    subject=subject,
                    key_supplier=lambda: ("test-root", private),
                )
            assert all(admission.used(record.spec.domain_id) == 1 for record in records)
            output, errors = process.communicate(
                certificate.to_bytes().decode("ascii") + "\n", timeout=45
            )
            assert process.returncode == 0, errors
            result = json.loads(output)
            expected = (
                0
                if mode in ("missing_startup", "wrong_owner", "shared_unready", "start_ack_lost")
                else 1
            )
            assert result["mode"] == mode and result["drivers"] == expected
            assert result["backend_count"] == result["thread_count"] == expected
            assert result["reply_count"] == (28 if mode == "healthy" else 0)
            if mode == "healthy":
                assert result["saturation_checked"] is True
            if mode == "start_ack_lost":
                assert result["startup_retired"] is True
            # Read-only actor work must not return the original durable root SQL credits.
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
