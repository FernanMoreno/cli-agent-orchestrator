"""Resource participants name a process; they never prove hierarchy or authority."""

import importlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from types import SimpleNamespace
from uuid import UUID, uuid4, uuid5

import psutil
import pytest

pytestmark = pytest.mark.offline_database


def target():
    return SimpleNamespace(
        store_id="be2d952a-45e8-419b-998c-f1b9a4763954",
        identity=("host", 5432, "db", "schema", "store"),
    )


def module(monkeypatch):
    api = importlib.import_module("cli_agent_orchestrator.services.admission_bootstrap")
    profile = str(uuid4())
    monkeypatch.setattr(api, "get_persistent_instance_id", lambda: profile, raising=False)

    def forbidden():
        pytest.fail("resource process identity must not load/create peer credentials")

    monkeypatch.setattr(api, "current_process_identity", forbidden)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.local_peer_identity.public_key_text", forbidden
    )
    return api, profile


def test_scope_is_stable_per_process_distinct_from_profile_and_case_sensitive(monkeypatch):
    api, profile = module(monkeypatch)
    first = api.scoped_process_bootstrap("bridge-resource/Node-A")
    repeated = api.scoped_process_bootstrap("bridge-resource/Node-A")
    other = api.scoped_process_bootstrap("bridge-resource/node-a")
    a, b, c = first._capture(target()), repeated._capture(target()), other._capture(target())
    assert a == b
    assert a.instance_id == str(uuid5(UUID(profile), "bridge-resource/Node-A"))
    assert c.instance_id == str(uuid5(UUID(profile), "bridge-resource/node-a"))
    assert len({profile, a.instance_id, c.instance_id}) == 3
    assert first._server_instance() == a.instance_id
    assert a.process_pid == os.getpid()
    assert a.process_started_at == psutil.Process().create_time()
    assert UUID(a.process_generation).version == 4


def test_cold_scope_created_concurrently_has_one_process_generation(monkeypatch):
    api, profile = module(monkeypatch)
    ready, second, guard = Barrier(2), Event(), Lock()
    birth = psutil.Process().create_time()
    calls = []

    def persistent_profile():
        ready.wait(timeout=10)
        return profile

    def create_time():
        with guard:
            calls.append(os.getpid())
            number = len(calls)
        if number == 1:
            # Force overlapping cold misses if the factory lacks single-flight.
            second.wait(timeout=2)
        else:
            second.set()
        return birth

    monkeypatch.setattr(api, "get_persistent_instance_id", persistent_profile)
    monkeypatch.setattr(
        api,
        "psutil",
        SimpleNamespace(
            Process=lambda _: SimpleNamespace(create_time=create_time), Error=psutil.Error
        ),
    )
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [
            workers.submit(api.scoped_process_bootstrap, "bridge-resource/cold") for _ in range(2)
        ]
        bootstraps = [future.result(timeout=15) for future in futures]
    bindings = [bootstrap._capture(target()) for bootstrap in bootstraps]
    assert bindings[0] == bindings[1]
    assert len(calls) == 1


@pytest.mark.parametrize("scope", [None, "", " ", "bad\nname", "x" * 257])
def test_invalid_scope_cannot_alias_another_resource_participant(monkeypatch, scope):
    api, _ = module(monkeypatch)
    with pytest.raises(ValueError):
        api.scoped_process_bootstrap(scope)


def test_process_identity_failure_is_fixed_and_does_not_expose_private_error(monkeypatch):
    api, _ = module(monkeypatch)

    def fail(_):
        raise OSError("private-process-path-canary")

    monkeypatch.setattr(
        api, "psutil", SimpleNamespace(Process=fail, Error=psutil.Error), raising=False
    )
    with pytest.raises(ValueError) as error:
        api.scoped_process_bootstrap("bridge-resource/unavailable")
    assert "private-process-path-canary" not in str(error.value)


def test_fresh_process_keeps_logical_scope_but_changes_generation(monkeypatch):
    api, profile = module(monkeypatch)
    parent = api.scoped_process_bootstrap("bridge-resource/restart")._capture(target())
    program = """
import json,os,sys
from pathlib import Path
from types import SimpleNamespace
import cli_agent_orchestrator
from cli_agent_orchestrator.services import admission_bootstrap as api
assert Path.cwd() == Path('/mnt/c/Users/ferna/OneDrive/Escritorio/CAOS')
assert Path(cli_agent_orchestrator.__file__).parent.parent == Path.cwd()/'src'
profile=json.load(sys.stdin)['profile']
api.get_persistent_instance_id=lambda:profile
api.current_process_identity=lambda:(_ for _ in ()).throw(AssertionError('peer identity forbidden'))
target=SimpleNamespace(store_id='be2d952a-45e8-419b-998c-f1b9a4763954',identity=('host',5432,'db','schema','store'))
b=api.scoped_process_bootstrap('bridge-resource/restart')._capture(target)
print(json.dumps({'id':b.instance_id,'generation':b.process_generation,'pid':b.process_pid,'birth':b.process_started_at}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", program],
        input=json.dumps({"profile": profile}),
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )
    child = json.loads(result.stdout)
    assert child["id"] == parent.instance_id
    assert child["generation"] != parent.process_generation
    assert child["pid"] != parent.process_pid
    assert child["birth"] > 0
