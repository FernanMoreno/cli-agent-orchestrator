"""The resource controller is reusable without constructing the central Inbox."""

import importlib
import json
import subprocess
import sys
from test.services.test_admission_monitor import fixture
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize(
    "module",
    [
        "cli_agent_orchestrator.services.inbox_admission_runtime",
        "cli_agent_orchestrator.api.admission_routes",
    ],
)
def test_control_import_does_not_construct_inbox_singleton(module):
    program = """
import importlib,json,sys
importlib.import_module(json.load(sys.stdin)['module'])
assert 'cli_agent_orchestrator.services.inbox_service' not in sys.modules
assert 'cli_agent_orchestrator.api.main' not in sys.modules
"""
    subprocess.run(
        [sys.executable, "-I", "-c", program],
        input=json.dumps({"module": module}),
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )


class Participant:
    def __init__(self):
        self.owner = None
        self.accepting = False
        self.drained = False

    def bind_admission(self, owner, *, clock, retirement):
        self.owner, self.clock, self.retirement = owner, clock, retirement

    def notify_admission_changed(self, terminal_id=None):
        pass

    def start_accepting(self):
        self.accepting = True

    def stop_accepting(self):
        self.accepting = False

    async def drain(self, *, timeout_s):
        assert not self.accepting and timeout_s == 10
        self.drained = True


def test_environment_builder_uses_only_trusted_scoped_bootstrap(monkeypatch):
    api = importlib.import_module("cli_agent_orchestrator.services.inbox_admission_runtime")
    f = fixture()
    participant = Participant()
    calls = []

    def scoped():
        calls.append("bootstrap")
        return f.bootstrap

    monkeypatch.delenv("CAO_ADMISSION_POLICY_FILE", raising=False)
    assert api.runtime_from_environment(participant, bootstrap_factory=scoped) is None
    assert calls == []
    monkeypatch.setenv("CAO_ADMISSION_POLICY_FILE", "/private-test-policy.json")
    monkeypatch.setattr("cli_agent_orchestrator.security.auth.is_auth_enabled", lambda: False)
    with pytest.raises(ValueError):
        api.runtime_from_environment(participant, bootstrap_factory=scoped)
    assert calls == []
    monkeypatch.setattr("cli_agent_orchestrator.security.auth.is_auth_enabled", lambda: True)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.legacy_memory_audit_repository._legacy_memory_audit_manager",
        lambda: SimpleNamespace(admission_slots=lambda: 4),
    )
    value = api.runtime_from_environment(participant, bootstrap_factory=scoped)
    assert value.bootstrap is f.bootstrap
    assert calls == ["bootstrap"]
    assert hasattr(api, "AdmissionParticipant")


@pytest.mark.asyncio
async def test_generic_participant_preserves_controller_owner_and_retirement():
    api = importlib.import_module("cli_agent_orchestrator.services.inbox_admission_runtime")
    f = fixture()
    participant = Participant()
    value = api.InboxAdmissionRuntime(
        participant,
        monitor=f.monitor,
        bootstrap=f.bootstrap,
        clock=lambda: f.clock[0],
        drain_timeout_s=10,
    )
    await value.start()
    assert participant.owner is f.monitor.owner
    assert participant.clock is value.clock and participant.accepting
    assert not participant.retirement()
    await value.stop()
    assert participant.drained and participant.retirement() and value.retired
