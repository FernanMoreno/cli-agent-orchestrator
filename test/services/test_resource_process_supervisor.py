"""Owned child/kernel exit is narrower than whole-generation resource closure."""

import copy
import importlib
import importlib.util
import os
import pickle
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import UUID

import pytest

pytestmark = pytest.mark.offline_database

STORE = "d18bcbac-9c97-4408-baf0-24f7e2331486"
TARGET = "a" * 64
CHILD = r"""
import hashlib, json, os, psutil, time, socket
from dataclasses import asdict
from dataclasses import replace
from pathlib import Path
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.services.resource_process_supervisor import publish_resource_binding
from cli_agent_orchestrator.utils.process_sql_origin import current_sql_application_name
pid=os.getpid()
ns=[os.stat('/proc/self/ns/'+name) for name in ('mnt','cgroup')]
c=Path('/proc/self/cgroup').read_bytes();m=Path('/proc/self/mountinfo').read_bytes()
binding=ResourceBinding('fa909c12-4645-4605-ab7c-9ac5f4c8125a','395141ce-9910-43f6-b4c4-9fe21790309e',pid,psutil.Process().create_time(),'d18bcbac-9c97-4408-baf0-24f7e2331486','a'*64,ns[0].st_dev,ns[0].st_ino,ns[1].st_dev,ns[1].st_ino,hashlib.sha256(len(c).to_bytes(8,'big')+c+len(m).to_bytes(8,'big')+m).hexdigest())
mode=os.environ.get('CAOS_EXIT_TEST_MODE','ok')
if mode=='die': os._exit(17)
if mode=='birth': binding=replace(binding,process_started_at=binding.process_started_at-1)
if mode=='pid': binding=replace(binding,process_pid=pid+1)
if mode=='store': binding=replace(binding,store_id='d18bcbac-9c97-4408-baf0-24f7e2331487')
if mode=='namespace': binding=replace(binding,mount_ns_inode=binding.mount_ns_inode+1)
if mode=='descendant_sender':
    from cli_agent_orchestrator.services.resource_process_supervisor import BINDING_FD_ENV, _identity
    descendant=os.fork()
    if descendant:
        os.waitpid(descendant,0)
        os._exit(19)
    current_sql_application_name()
    with socket.socket(fileno=int(os.environ.pop(BINDING_FD_ENV))) as channel:
        channel.send(json.dumps({'version':2,'binding':asdict(binding),'kernel':asdict(_identity(pid)),'sql_application_name':current_sql_application_name()}).encode())
        channel.recv(2)
    os._exit(0)
if mode=='descendant_alive':
    descendant=os.fork()
    if not descendant: time.sleep(60);os._exit(0)
    Path(os.environ['CAOS_EXIT_TEST_DESCENDANT']).write_text(str(descendant))
if mode=='inherited_pgappname':
    from cli_agent_orchestrator.services.legacy_memory_audit_repository import _check_libpq_environment
    _check_libpq_environment(dict(host='localhost',port=1,dbname='private',user='runtime',sslmode='disable',connect_timeout=10,application_name=current_sql_application_name()))
if mode.startswith('packet_'):
    from cli_agent_orchestrator.services.resource_process_supervisor import BINDING_FD_ENV, _identity
    packet={'version':2,'binding':asdict(binding),'kernel':asdict(_identity(pid)),'sql_application_name':current_sql_application_name()}
    if mode=='packet_version': packet['version']=True
    if mode=='packet_legacy': packet['version']=1
    if mode=='packet_sql_legacy': packet['sql_application_name']='caos'
    if mode=='packet_sql_missing': packet.pop('sql_application_name')
    if mode=='packet_sql_type': packet['sql_application_name']=[]
    if mode=='packet_sql_forged': packet['sql_application_name']='caos-sql-v1-'+'0'*32
    if mode=='packet_extra': packet['binding']['extra']=1
    if mode=='packet_kernel': packet['kernel']['ticks']+=1
    payload=json.dumps(packet).encode()
    if mode=='packet_duplicate': payload=b'{"version":2,'+payload[1:]
    if mode=='packet_oversized': payload=b'x'*16384
    with socket.socket(fileno=int(os.environ.pop(BINDING_FD_ENV))) as channel:
        channel.send(payload)
        channel.recv(2)
    os._exit(0)
if mode=='bridge_factory':
    from types import SimpleNamespace
    from cli_agent_orchestrator.services import admission_bootstrap
    from cli_agent_orchestrator.runtime_channel.bridge import _resource_process_bootstrap
    admission_bootstrap.scoped_process_bootstrap=lambda *a,**kw: SimpleNamespace(shared_repository=lambda:SimpleNamespace(current_binding=lambda:binding))
    _resource_process_bootstrap('supervised-test')
else:
    publish_resource_binding(binding, timeout_s=0.01 if mode=='ack_timeout' else 10)
if mode=='exit23': os._exit(23)
time.sleep(600)
"""


def api():
    name = "cli_agent_orchestrator.services.resource_process_supervisor"
    assert (
        importlib.util.find_spec(name) is not None
    ), "owned resource process supervisor is missing"
    return importlib.import_module(name)


@contextmanager
def child(mode="ok", **environment):
    module = api()
    owner = module.ResourceProcessSupervisor.launch(
        [sys.executable, "-c", CHILD],
        expected_store_id=STORE,
        expected_target_identity_sha256=TARGET,
        env={**os.environ, "CAOS_EXIT_TEST_MODE": mode, **environment},
    )
    try:
        yield owner
    finally:
        if owner.observation is None:
            try:
                owner.send_signal(signal.SIGTERM)
            except module.ResourceProcessUnproven:
                pass
        owner.wait(timeout_s=10)
        owner.close()


@pytest.mark.parametrize("mode", ["ok", "bridge_factory"])
def test_live_child_timeout_then_exact_owned_reap_produces_stable_observation(mode):
    with child(mode) as owner:
        original = owner.receive_binding(timeout_s=10)
        assert str(UUID(original.store_id)) == STORE
        assert owner.observation is None
        with pytest.raises(subprocess.TimeoutExpired):
            owner.wait(timeout_s=0.01)
        assert owner.observation is None
        owner.send_signal(signal.SIGTERM)
        observed = owner.wait(timeout_s=10)
        assert api().is_process_exit_observation(observed)
        assert observed.binding is original
        assert observed.returncode == -signal.SIGTERM
        assert owner.wait(timeout_s=0) is observed
        assert owner.observation is observed
        owner.close()
        assert owner.observation is observed


@pytest.mark.parametrize("mode", ["birth", "pid", "store", "namespace", "die"])
def test_wrong_or_absent_binding_never_observes_an_admitted_generation(mode):
    with child(mode) as owner:
        with pytest.raises(api().ResourceProcessUnproven):
            owner.receive_binding(timeout_s=10)
        owner.send_signal(signal.SIGTERM)
        assert owner.wait(timeout_s=10) is None
        assert owner.observation is None


@pytest.mark.parametrize("operation", [copy.copy, copy.deepcopy, pickle.dumps])
def test_supervisor_and_exit_observation_cannot_be_transferred(operation):
    with child() as owner:
        owner.receive_binding(timeout_s=10)
        with pytest.raises(TypeError):
            operation(owner)
        owner.send_signal(signal.SIGTERM)
        observation = owner.wait(timeout_s=10)
        with pytest.raises(TypeError):
            operation(observation)


def test_foreign_thread_cannot_obtain_a_witness_or_signal_the_child():
    with child() as owner:
        owner.receive_binding(timeout_s=10)
        with ThreadPoolExecutor(max_workers=1) as pool:
            for operation in (owner.wait, owner.close, lambda: owner.send_signal(signal.SIGTERM)):
                with pytest.raises(PermissionError):
                    pool.submit(operation).result(timeout=5)
        assert owner.observation is None


def test_closed_channel_is_not_process_exit_and_live_owner_cannot_be_discarded():
    with child() as owner:
        owner.receive_binding(timeout_s=10)
        with pytest.raises(api().ResourceProcessUnproven):
            owner.close()
        assert owner.observation is None
        with pytest.raises(subprocess.TimeoutExpired):
            owner.wait(timeout_s=0.01)


def test_kernel_sender_credentials_reject_a_forked_descendant():
    # The original leader still owns the unreaped descendant in this case. Its
    # protocol exception exits the descendant after the inherited channel closes.
    with child("descendant_sender") as owner:
        with pytest.raises(api().ResourceProcessUnproven) as failed:
            owner.receive_binding(timeout_s=10)
        assert (
            str(failed.value.__context__) == "resource binding sender differs from the owned child"
        )
        assert owner.wait(timeout_s=10) is None


@pytest.mark.parametrize("invalid", [None, {}, object()])
def test_plain_values_are_not_exit_observations(invalid):
    assert not api().is_process_exit_observation(invalid)


def test_reaped_child_with_unknown_pidfd_state_does_not_produce_exit_evidence(monkeypatch):
    with child() as owner:
        owner.receive_binding(timeout_s=10)
        monkeypatch.setattr(api(), "_exit_ready", lambda fd: None)
        owner.send_signal(signal.SIGTERM)
        assert owner.wait(timeout_s=10) is None
        assert owner.observation is None


def test_fork_cannot_use_parent_owner_or_inherited_observation():
    with child() as owner:
        owner.receive_binding(timeout_s=10)
        owner.send_signal(signal.SIGTERM)
        observation = owner.wait(timeout_s=10)
        descendant = os.fork()
        if descendant == 0:
            try:
                assert not api().is_process_exit_observation(observation)
                for operation in (
                    owner.wait,
                    owner.close,
                    lambda: owner.send_signal(signal.SIGTERM),
                ):
                    try:
                        operation()
                    except PermissionError:
                        pass
                    else:
                        os._exit(31)
                os._exit(0)
            except BaseException:
                os._exit(32)
        assert os.waitpid(descendant, 0) == (descendant, 0)


def test_pin_failure_keeps_own_child_joinable_but_cannot_produce_a_witness(monkeypatch):
    def unavailable(*args):
        raise OSError("controlled pidfd failure")

    monkeypatch.setattr(api().os, "pidfd_open", unavailable)
    with child() as owner:
        with pytest.raises(api().ResourceProcessUnproven):
            owner.receive_binding(timeout_s=10)
        with pytest.raises(api().ResourceProcessUnproven):
            owner.send_signal(signal.SIGTERM)
        assert owner.wait(timeout_s=10) is None


def test_unexpected_failure_after_popen_preserves_a_joinable_owner(monkeypatch):
    module = api()

    def fail(*args):
        raise MemoryError("controlled post-start observation failure")

    started = []
    real_popen = module.subprocess.Popen

    def tracked(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", tracked)
    monkeypatch.setattr(module, "_identity", fail)
    owner = None
    try:
        with pytest.raises(MemoryError) as caught:
            module.ResourceProcessSupervisor.launch(
                [sys.executable, "-c", "import time; time.sleep(600)"],
                expected_store_id=STORE,
                expected_target_identity_sha256=TARGET,
            )
        owner = getattr(caught.value, "resource_process_owner", None)
        assert owner is not None, "post-Popen failure lost the actual child's cleanup owner"
        owner.send_signal(signal.SIGTERM)
        assert owner.wait(timeout_s=10) is None
    finally:
        # Fixture always owns the actual Popen even while testing a missing
        # product handoff. Never leave a real RED child for another session.
        for process in started:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
        if owner is not None:
            owner.close()


def test_bridge_scoped_bootstrap_publishes_before_returning(monkeypatch):
    from types import SimpleNamespace

    from cli_agent_orchestrator.runtime_channel import bridge
    from cli_agent_orchestrator.services import admission_bootstrap

    factory = getattr(bridge, "_resource_process_bootstrap", None)
    assert callable(factory), "Bridge must publish its exact scoped binding for supervision"
    calls = []
    original = object()
    bootstrap = SimpleNamespace(
        shared_repository=lambda: SimpleNamespace(current_binding=lambda: original)
    )

    def scoped(scope, *, terminal_generation):
        calls.append((scope, terminal_generation))
        return bootstrap

    monkeypatch.setattr(admission_bootstrap, "scoped_process_bootstrap", scoped)
    monkeypatch.setenv(api().BINDING_FD_ENV, "123")
    monkeypatch.setattr(api(), "publish_resource_binding", lambda binding: calls.append(binding))
    assert factory("node-A") is bootstrap
    assert calls == [("bridge-resource/node-A", True), original]


def test_bridge_scoped_bootstrap_without_opt_in_does_not_read_an_extra_binding(monkeypatch):
    from types import SimpleNamespace

    from cli_agent_orchestrator.runtime_channel import bridge
    from cli_agent_orchestrator.services import admission_bootstrap

    factory = getattr(bridge, "_resource_process_bootstrap", None)
    assert callable(factory), "Bridge supervision bootstrap is missing"
    bootstrap = SimpleNamespace()
    monkeypatch.delenv(api().BINDING_FD_ENV, raising=False)
    monkeypatch.setattr(admission_bootstrap, "scoped_process_bootstrap", lambda *a, **kw: bootstrap)
    assert factory("node-A") is bootstrap


def test_external_reaper_cannot_be_reported_as_this_owners_observed_exit():
    with child() as owner:
        owner.receive_binding(timeout_s=10)
        owner.send_signal(signal.SIGTERM)
        pid = owner._process.pid
        waited, status = os.waitpid(pid, 0)
        assert waited == pid and os.WIFSIGNALED(status)
        assert owner.wait(timeout_s=10) is None, "Popen synthesized a success after another reaper"
        assert owner.observation is None


def test_rejected_ancillary_rights_are_closed_even_when_the_packet_is_invalid():
    import array
    import socket

    with child() as owner:
        inherited = os.open("/dev/null", os.O_RDONLY)
        original = owner._channel
        closed = False

        class InjectedChannel:
            def settimeout(self, value):
                pass

            def recvmsg(self, *args):
                return (
                    b"{}",
                    [
                        (
                            socket.SOL_SOCKET,
                            socket.SCM_RIGHTS,
                            array.array("i", [inherited]).tobytes(),
                        )
                    ],
                    socket.MSG_CTRUNC,
                    None,
                )

            def close(self):
                original.close()

        owner._channel = InjectedChannel()
        try:
            with pytest.raises(api().ResourceProcessUnproven):
                owner.receive_binding(timeout_s=10)
            with pytest.raises(OSError):
                os.fstat(inherited)
            closed = True
        finally:
            if not closed:
                os.close(inherited)


@pytest.mark.parametrize(
    "mode",
    ["packet_version", "packet_extra", "packet_kernel", "packet_duplicate", "packet_oversized"],
)
def test_closed_structured_binding_schema_rejects_invalid_packets(mode):
    with child(mode) as owner:
        with pytest.raises(api().ResourceProcessUnproven):
            owner.receive_binding(timeout_s=10)
        assert owner.wait(timeout_s=10) is None


def test_child_ack_timeout_never_produces_a_binding_or_exit_observation():
    with child("ack_timeout") as owner:
        assert owner.wait(timeout_s=10) is None
        with pytest.raises(api().ResourceProcessUnproven):
            owner.receive_binding(timeout_s=10)
        assert owner.observation is None


def test_actual_wait_status_is_structured_and_preserves_a_nonzero_exit():
    with child("exit23") as owner:
        original = owner.receive_binding(timeout_s=10)
        observation = owner.wait(timeout_s=10)
        assert observation.binding is original
        assert observation.exit_kind == "exited" and observation.exit_status == 23
        assert observation.returncode == 23
        assert str(UUID(observation.boot_id)) == observation.boot_id
        assert observation.start_time_ticks > 0
        assert len(observation.pid_namespace) == 2


def test_process_exit_does_not_claim_a_live_descendant_is_closed(tmp_path):
    import json

    # The isolated fixture is a subreaper solely for its own descendants. It
    # actually waits for both children and never changes the pytest host's role.
    script = r"""
import ctypes,json,os,signal,sys
from pathlib import Path
from cli_agent_orchestrator.services.resource_process_supervisor import ResourceProcessSupervisor
assert ctypes.CDLL(None).prctl(36,1,0,0,0)==0
owner=ResourceProcessSupervisor.launch([sys.executable,'-c',CHILD_CODE],expected_store_id=STORE_ID,expected_target_identity_sha256=TARGET_DIGEST,env={**os.environ,'CAOS_EXIT_TEST_MODE':'descendant_alive','CAOS_EXIT_TEST_DESCENDANT':DESCENDANT_FILE})
descendant=None;pin=None
try:
    original=owner.receive_binding(timeout_s=10)
    descendant=int(Path(DESCENDANT_FILE).read_text())
    pin=os.pidfd_open(descendant)
    owner.send_signal(signal.SIGTERM)
    observation=owner.wait(timeout_s=10)
    assert observation.binding is original
    assert os.waitpid(descendant,os.WNOHANG)==(0,0)
    print(json.dumps({'owned_process_exit':True,'descendant_still_alive':True,'generation_closure':False,'credit_return':False}))
finally:
    if pin is not None:
        try:signal.pidfd_send_signal(pin,signal.SIGKILL)
        except ProcessLookupError:pass
        os.close(pin)
    if descendant is not None:os.waitpid(descendant,0)
    owner.send_signal(signal.SIGTERM)
    owner.wait(timeout_s=10)
    owner.close()
"""
    values = dict(
        CHILD_CODE=CHILD,
        STORE_ID=STORE,
        TARGET_DIGEST=TARGET,
        DESCENDANT_FILE=str(tmp_path / "descendant"),
    )
    prefix = "\n".join(f"{key}={value!r}" for key, value in values.items())
    result = subprocess.run(
        [sys.executable, "-c", prefix + script], capture_output=True, text=True, timeout=40
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == dict(
        owned_process_exit=True,
        descendant_still_alive=True,
        generation_closure=False,
        credit_return=False,
    )


def test_failed_bridge_publication_has_no_sql_generation_or_closeable_bootstrap(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    import psutil

    from cli_agent_orchestrator.clients.admission_bootstrap_repository import (
        ResourceEnvelopeRepository,
    )
    from cli_agent_orchestrator.clients.postgres import DatabaseTarget
    from cli_agent_orchestrator.runtime_channel import bridge
    from cli_agent_orchestrator.services import admission_bootstrap

    target = DatabaseTarget(
        "localhost",
        1,
        "never_connected",
        "runtime",
        "private_test",
        STORE,
        tmp_path / "must-not-read",
    )

    def forbidden(*args, **kwargs):
        pytest.fail("constructing/publishing this bootstrap must not connect or register SQL")

    identity = SimpleNamespace(
        instance_id="fa909c12-4645-4605-ab7c-9ac5f4c8125a",
        process_generation="395141ce-9910-43f6-b4c4-9fe21790309e",
        pid=os.getpid(),
        process_started_at=psutil.Process().create_time(),
    )
    bootstrap = admission_bootstrap.ResourceBootstrap(
        repository=ResourceEnvelopeRepository(
            target_supplier=lambda: target, connect_factory=forbidden
        ),
        binding_supplier=lambda value: admission_bootstrap.capture_server_binding(
            value, identity_supplier=lambda: identity
        ),
        instance_supplier=lambda: identity.instance_id,
        terminal_generation=True,
    )
    original = RuntimeError("controlled missing ACK")

    def failed_publish(binding):
        assert binding.instance_id == identity.instance_id
        raise original

    monkeypatch.setattr(admission_bootstrap, "scoped_process_bootstrap", lambda *a, **kw: bootstrap)
    monkeypatch.setenv(api().BINDING_FD_ENV, "123")
    monkeypatch.setattr(api(), "publish_resource_binding", failed_publish)
    with pytest.raises(RuntimeError) as failed:
        bridge._resource_process_bootstrap("test")
    assert failed.value is original
    assert not hasattr(bootstrap, "close")
    assert not target.secret_file.exists()


@pytest.mark.parametrize(
    "mode",
    [
        "packet_legacy",
        "packet_sql_legacy",
        "packet_sql_missing",
        "packet_sql_type",
        "packet_sql_forged",
    ],
)
def test_process_exit_requires_protocol_two_and_a_valid_sql_origin(mode):
    with child(mode) as owner:
        with pytest.raises(api().ResourceProcessUnproven):
            owner.receive_binding(timeout_s=10)
        assert owner.wait(timeout_s=10) is None
        assert owner.sql_application_name is None


def test_supervised_child_canonicalizes_sql_name_without_mutating_operator_environment(monkeypatch):
    monkeypatch.setenv("PGAPPNAME", "caos")
    with child("inherited_pgappname") as owner:
        owner.receive_binding(timeout_s=30)
        assert owner.sql_application_name.startswith("caos-sql-v1-")
        assert os.environ["PGAPPNAME"] == "caos"
