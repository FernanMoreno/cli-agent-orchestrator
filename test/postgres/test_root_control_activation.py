"""Owner-funded activation and pre-SQL Linux compute; not deployment or M1 acceptance."""

import base64
import importlib
import importlib.util
import json
import os
import select
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from test.postgres import fixtures as postgres_fixtures
from test.postgres.test_admission_metadata_credits import ADMIN, grant
from test.postgres.test_admission_reservations import domain, shared
from test.postgres.test_root_sql_control import CHILD as ROOT_CHILD
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
from cli_agent_orchestrator.services.admission_policy import ResourceAmounts
from cli_agent_orchestrator.services.resource_process_supervisor import _identity, _matches
from cli_agent_orchestrator.services.root_sql_control import RootSqlSubject

pytestmark = pytest.mark.postgres


def activation_api():
    name = "cli_agent_orchestrator.services.root_control_activation"
    assert importlib.util.find_spec(name) is not None, "missing pre-SQL root activation"
    return importlib.import_module(name)


def test_pre_sql_activation_api_exists():
    module = activation_api()
    assert callable(module.issue_root_control_activation)
    assert callable(module.prepare_original_control_owner)


BOUNDARY = "certificate=SignedRootSqlOpening.from_bytes"
assert ROOT_CHILD.count(BOUNDARY) == 1
CHILD = (
    r"""
import os
from pathlib import Path
import cli_agent_orchestrator,test.postgres.fixtures
repository=Path(os.environ['ROOT_ACTIVATION_REPOSITORY'])
assert Path.cwd()==repository==Path(test.postgres.fixtures.__file__).parents[2]
assert Path(cli_agent_orchestrator.__file__).parents[2]==repository
"""
    + ROOT_CHILD[: ROOT_CHILD.index(BOUNDARY)]
    + r"""
import asyncio,threading,time
from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.services.admission_settings import AdmissionPolicyLoader
from cli_agent_orchestrator.services.admission_resources import LinuxResourceSampler
from cli_agent_orchestrator.services.admission_monitor import AdmissionMonitor
from cli_agent_orchestrator.services.root_control_activation import (
    RootControlActivationUnavailable,prepare_original_control_owner,
)
packet=sys.stdin.buffer.readline().rstrip(b'\n')
public=Ed25519PublicKey.from_public_bytes(base64.b64decode(os.environ['ROOT_TEST_PUBLIC_KEY']))
mode=os.environ['ROOT_ACTIVATION_MODE']
if mode=='wrong_key':public=__import__('cryptography.hazmat.primitives.asymmetric.ed25519',fromlist=['Ed25519PrivateKey']).Ed25519PrivateKey.generate().public_key()
drivers=[]
attempts=[]
samples=[]
current_actor=[None]
main_thread=threading.current_thread()
original_connect=native.psycopg.connect
original_sample=LinuxResourceSampler.sample

def sample(self,*,storage):
    result=original_sample(self,storage=storage)
    samples.append(dict(issues=result.issues,memory=result.observation.memory_available_bytes is not None,cpu=result.observation.cpu_available_millicores is not None,storage=result.observation.storage_available_slots))
    return result

LinuxResourceSampler.sample=sample

def driver(*args,**kwargs):
    actor=current_actor[0]
    attempts.append(dict(actor_exists=actor is not None,thread_is_main=threading.current_thread() is main_thread))
    assert actor is not None,'SQL before startup actor exists'
    assert actor._startup.state=='started','SQL before startup charge'
    assert actor._config.owner._observation is None,'fabricated observation before first TCP'
    assert threading.current_thread() is not main_thread,'root SQL outside original worker'
    raw=original_connect(*args,**kwargs)
    drivers.append((raw.info.backend_pid,threading.get_ident()))
    return raw

native.psycopg.connect=driver
if mode=='late_revision':
    original_monitor_init=AdmissionMonitor.__init__
    def monitor_init(self,*args,**kwargs):
        assert threading.current_thread() is not main_thread
        assert current_actor[0]._session_entered
        assert not current_actor[0]._channel.in_transaction
        print(json.dumps(dict(phase='initial_readback_done')),flush=True)
        assert sys.stdin.buffer.readline()==b'continue\n','readback barrier not released'
        return original_monitor_init(self,*args,**kwargs)
    AdmissionMonitor.__init__=monitor_init

async def run():
    actor=None
    try:
        try:
            actor=await prepare_original_control_owner(
                packet=packet,trusted_keys={'test-root':public},bootstrap=bootstrap,
                policy_loader=AdmissionPolicyLoader(source=lambda:{'version':1,'policy':{}}),
                scope_id='root/activation',clock=time.monotonic,
            )
        except RootControlActivationUnavailable:
            assert mode=='wrong_key',(mode,attempts,samples)
            assert not drivers and not attempts and not samples
            return dict(mode=mode,drivers=0,pre_sql_refused=True)
        assert mode!='wrong_key'
        current_actor[0]=actor
        owner=actor._config.owner
        assert not drivers and owner._observation is None
        assert len(samples)==2 and samples[-1]['memory'] and samples[-1]['cpu'],samples
        assert all(s['storage'] is None for s in samples),samples
        before=owner.snapshot(now=time.monotonic())
        assert before.command_inflight==1 and before.command_started==0
        assert actor._startup.state=='reserved'
        assert owner.try_reserve('ordinary-data',now=time.monotonic()).reservation is None
        if mode=='replay':
            before_samples=len(samples)
            try:
                await prepare_original_control_owner(
                    packet=packet,trusted_keys={'test-root':public},bootstrap=bootstrap,
                    policy_loader=AdmissionPolicyLoader(source=lambda:{'version':1,'policy':{}}),
                    scope_id='root/activation',clock=time.monotonic,
                )
            except RootControlActivationUnavailable:pass
            else:raise AssertionError('pre-SQL activation replay accepted')
            assert not drivers and not attempts and len(samples)==before_samples
            await actor.aclose(timeout=5)
            assert actor.retired
            return dict(mode=mode,drivers=0,pre_sql_refused=True)
        actor.start()
        if mode in ('revoked','revision','late_revision'):
            try:await actor.wait_ready()
            except StorageAdmissionUnavailable:pass
            else:raise AssertionError('changed envelope accepted at first root readback')
            try:await actor.aclose(timeout=5)
            except StorageAdmissionUnavailable:pass
            assert actor.retired and owner._observation is None
            assert len(samples)==2,samples
        else:
            assert mode=='healthy'
            await actor.wait_ready()
            await actor.aclose(timeout=5)
            assert actor.retired
        after=owner.snapshot(now=time.monotonic())
        assert after.command_inflight==0 and after.storage_connection_loans==0
        assert len(drivers)==len({p for p,t in drivers})==len({t for p,t in drivers})==1
        return dict(mode=mode,drivers=1,pre_sql_charge=True,normal_unknown_before_sql=True,retired=True)
    finally:
        primary=sys.exc_info()[1]
        if actor is not None and not actor.retired:
            try:await actor.aclose(timeout=5)
            except BaseException:
                if primary is None:raise

print(json.dumps(asyncio.run(run())),flush=True)
"""
)


@pytest.mark.parametrize(
    "mode", ["healthy", "revoked", "revision", "late_revision", "wrong_key", "replay", "cli"]
)
def test_signed_activation_uses_linux_before_first_tcp_and_exact_envelope_readback(
    shared, monkeypatch, tmp_path, mode
):
    module = activation_api()
    repository_root = Path(cli_agent_orchestrator.__file__).parents[2]
    assert Path.cwd() == repository_root == Path(postgres_fixtures.__file__).parents[2]
    assert Path(os.environ["PYTHONPATH"].split(os.pathsep)[0]) == repository_root / "src"
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
        ROOT_ACTIVATION_MODE=mode,
        ROOT_ACTIVATION_REPOSITORY=str(repository_root),
        PYTHONPATH=os.pathsep.join((str(repository_root / "src"), str(repository_root))),
        ROOT_TEST_PUBLIC_KEY=base64.b64encode(
            private.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        ).decode("ascii"),
    )
    argv = [sys.executable, "-c", CHILD]
    if mode == "cli":
        trust_file = tmp_path / "root-trust.json"
        policy_file = tmp_path / "root-policy.json"
        trust_file.write_text(
            json.dumps({"version": 1, "keys": {"test-root": environment["ROOT_TEST_PUBLIC_KEY"]}})
        )
        policy_file.write_text(json.dumps({"version": 1, "policy": {}}))
        trust_file.chmod(0o600)
        policy_file.chmod(0o600)
        argv = [
            sys.executable,
            "-m",
            "cli_agent_orchestrator.runtime_channel.root_control",
            "--trust-file",
            str(trust_file),
            "--policy-file",
            str(policy_file),
            "--scope",
            "root/activation-cli",
            "--close-timeout",
            "5",
        ]
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        cwd=repository_root,
    )
    try:
        assert select.select([process.stdout], [], [], 30)[0], "root subject timeout"
        packet = json.loads(process.stdout.readline())
        if mode == "cli":
            assert set(packet) == {"type", "value"} and packet["type"] == "subject"
            packet = packet["value"]
        bound = ResourceBinding(**packet.pop("binding"))
        packet["pid_namespace"] = tuple(packet["pid_namespace"])
        subject = RootSqlSubject(binding=bound, **packet)
        live = _identity(process.pid)
        assert bound.process_pid == process.pid and _matches(bound, live)
        assert (subject.boot_id, subject.start_time_ticks, subject.pid_namespace) == (
            live.boot_id,
            live.ticks,
            live.pid_ns,
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
            intents = SqlIntentRepository(target, binding_supplier=lambda: issuer_binding)
            with funded(target.migration_target()) as channel:
                values = {
                    **asdict(bound),
                    **asdict(EnvelopeClaims(4 * 1024**3, 16000, 64, True, False)),
                    "revision": 1,
                    "active": True,
                    "protocol_version": 1,
                    "actor_principal_id": ADMIN,
                }

                def append_envelope(writer=channel):
                    with writer.funded_transaction():
                        writer.execute(
                            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                                sql.Identifier(target.schema, RESOURCE_ENVELOPE_TABLE),
                                sql.SQL(",").join(map(sql.Identifier, values)),
                                sql.SQL(",").join(sql.Placeholder() for _ in values),
                            ),
                            tuple(values.values()),
                        )

                append_envelope()
                intents.provision(
                    channel,
                    journal_id=journal,
                    credit_id=credits[0].credit_id,
                    max_records=3,
                    principal_id=ADMIN,
                    participant_binding=bound,
                )
                activation = module.issue_root_control_activation(
                    issuer,
                    channel,
                    principal=principal(),
                    journal_id=journal,
                    intent_id=str(uuid4()),
                    session_id=str(uuid4()),
                    subject=subject,
                    key_supplier=lambda: ("test-root", private),
                    startup_footprint=ResourceAmounts(1024**2, 10, 0),
                    refresh_footprint=ResourceAmounts(1024**2, 10, 0),
                )
                assert type(activation) is bytes
                if mode in ("revoked", "revision"):
                    values["revision"] = 2
                    values["active"] = mode != "revoked"
                    append_envelope()
            assert all(admission.used(record.spec.domain_id) == 1 for record in records)
            if mode == "cli":
                deadline = time.monotonic() + 45
                process.stdin.write(activation.decode("ascii") + "\n")
                process.stdin.flush()
                assert select.select(
                    [process.stdout], [], [], max(0.0, deadline - time.monotonic())
                )[0], "CLI ready timeout"
                line = process.stdout.readline()
                if not line:
                    _, errors = process.communicate(timeout=max(0.0, deadline - time.monotonic()))
                    raise AssertionError(errors)
                assert json.loads(line) == {"type": "ready"}
                with funded(target.migration_target()) as observer:
                    rows = observer.execute(
                        "SELECT pid FROM pg_stat_activity WHERE application_name=%s",
                        (subject.sql_application_name,),
                    ).fetchall()
                    assert len(rows) == 1
                output, errors = process.communicate(
                    '{"type":"stop"}\n', timeout=max(0.0, deadline - time.monotonic())
                )
                assert process.returncode == 0, errors
                assert [json.loads(line) for line in output.splitlines()] == [{"type": "stopping"}]
                assert all(admission.used(record.spec.domain_id) == 1 for record in records)
                return
            elif mode == "late_revision":
                deadline = time.monotonic() + 45
                process.stdin.write(activation.decode("ascii") + "\n")
                process.stdin.flush()
                assert select.select(
                    [process.stdout], [], [], max(0.0, deadline - time.monotonic())
                )[0], "first-readback barrier timeout"
                line = process.stdout.readline()
                if not line:
                    _, errors = process.communicate(timeout=max(0.0, deadline - time.monotonic()))
                    raise AssertionError(errors)
                assert json.loads(line) == {"phase": "initial_readback_done"}
                values["revision"] = 2
                with funded(target.migration_target()) as changed:
                    append_envelope(changed)
                output, errors = process.communicate(
                    "continue\n", timeout=max(0.0, deadline - time.monotonic())
                )
            else:
                output, errors = process.communicate(activation.decode("ascii") + "\n", timeout=45)
            assert process.returncode == 0, errors
            result = json.loads(output)
            assert result["mode"] == mode
            assert result["drivers"] == (0 if mode in ("wrong_key", "replay") else 1)
            if mode in ("wrong_key", "replay"):
                assert result["pre_sql_refused"] is True
            else:
                assert result["retired"] is True
                assert result["pre_sql_charge"] is True
                assert result["normal_unknown_before_sql"] is True
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
