"""The deployment adapter chooses a fixed Bridge command and canonical realm."""

import hashlib
import importlib
import importlib.util
import json
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database


def api():
    name = "cli_agent_orchestrator.runtime_channel.bridge_supervisor"
    assert (
        importlib.util.find_spec(name) is not None
    ), "supervised Bridge deployment adapter is missing"
    return importlib.import_module(name)


def test_bridge_launcher_uses_fixed_module_and_canonical_parent_target(monkeypatch):
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.services.resource_process_supervisor import (
        ResourceProcessSupervisor,
    )

    module = api()
    target = SimpleNamespace(
        store_id="d18bcbac-9c97-4408-baf0-24f7e2331486", identity=("host", 123, "main")
    )
    monkeypatch.setattr(database, "main_target", lambda: target)
    calls = []
    sentinel = object()

    def launch(argv, **kwargs):
        calls.append((argv, kwargs))
        return sentinel

    monkeypatch.setattr(ResourceProcessSupervisor, "launch", launch)
    environment = {
        "CAO_ADMISSION_MODE": "shared",
        "CAO_ADMISSION_POLICY_FILE": "/private/policy.json",
    }
    assert module.launch_bridge(env=environment) is sentinel
    argv, options = calls[0]
    assert argv == [module.sys.executable, "-m", "cli_agent_orchestrator.runtime_channel.bridge"]
    assert options == dict(
        expected_store_id=target.store_id,
        expected_target_identity_sha256=hashlib.sha256(
            json.dumps(target.identity, separators=(",", ":")).encode()
        ).hexdigest(),
        env=environment,
    )


@pytest.mark.parametrize(
    "environment", [{}, {"CAO_ADMISSION_MODE": "local"}, {"CAO_ADMISSION_MODE": "shared"}]
)
def test_missing_shared_policy_is_rejected_before_target_or_child(monkeypatch, environment):
    from cli_agent_orchestrator.clients import database

    module = api()

    def forbidden():
        pytest.fail("invalid deployment must not resolve credentials or create a child")

    monkeypatch.setattr(database, "main_target", forbidden)
    with pytest.raises(ValueError):
        module.launch_bridge(env=environment)


def test_main_joins_the_owner_attached_to_a_post_launch_failure(monkeypatch):
    import signal
    import sys

    module = api()
    owner = module.ResourceProcessSupervisor.launch(
        [sys.executable, "-c", "import time; time.sleep(600)"],
        expected_store_id="d18bcbac-9c97-4408-baf0-24f7e2331486",
        expected_target_identity_sha256="a" * 64,
    )
    original = MemoryError("controlled launch handoff failure")
    original.resource_process_owner = owner

    def failed_launch():
        raise original

    monkeypatch.setattr(module, "launch_bridge", failed_launch)
    try:
        with pytest.raises(MemoryError) as caught:
            module.main()
        assert caught.value is original
        assert owner._exited, "launcher adapter abandoned a started child on exceptional handoff"
        assert owner._closed
        assert owner.observation is None
    finally:
        owner.send_signal(signal.SIGTERM)
        owner.wait(timeout_s=10)
        owner.close()


@pytest.mark.parametrize("requested", ["SIGTERM", "SIGHUP", "SIGINT"])
def test_main_joins_before_propagating_a_shutdown_signal(monkeypatch, requested):
    import signal

    module = api()
    chosen = getattr(signal, requested)
    previous = signal.getsignal(chosen)
    events = []

    class Owner:
        def receive_binding(self):
            handler = signal.getsignal(chosen)
            assert (
                callable(handler) and handler is not previous
            ), "supervisor has no managed shutdown signal handler"
            handler(chosen, None)

        def send_signal(self, signum):
            events.append("signal-child")

        def wait(self):
            events.append("joined")

        def close(self):
            events.append("closed")

    monkeypatch.setattr(module, "launch_bridge", Owner)
    with pytest.raises(SystemExit) as stopped:
        module.main()
    assert stopped.value.code == 128 + chosen
    assert events == ["signal-child", "joined", "closed"]
    assert signal.getsignal(chosen) is previous
