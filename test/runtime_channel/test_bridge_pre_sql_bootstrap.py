"""Authenticated startup ordering, not funded genesis or provider acceptance."""

import asyncio
import json
import os
import select
import signal
import sys
from test.services.test_resource_process_supervisor import CHILD, STORE, TARGET

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.runtime_channel import bridge
from cli_agent_orchestrator.services.resource_process_supervisor import (
    BINDING_FD_ENV,
    ResourceProcessSupervisor,
)

pytestmark = pytest.mark.offline_database


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("CAO_BRIDGE_SERVER_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("CAO_BRIDGE_RUNTIME_ID", "pre-sql-test")
    monkeypatch.setenv(BINDING_FD_ENV, "123")
    monkeypatch.setattr(bridge, "runtime_token", lambda: "synthetic-test-only")


@pytest.mark.parametrize("error", [RuntimeError, MemoryError, BaseException])
@pytest.mark.asyncio
async def test_rejected_supervisor_prevents_the_first_startup_sql(configured, monkeypatch, error):
    original = error("controlled publication failure")
    reached = []

    def rejected(runtime_id):
        reached.append("publish")
        raise original

    def forbidden():
        pytest.fail("startup SQL ran before authenticated supervisor acknowledgement")

    monkeypatch.setattr(bridge, "_resource_process_bootstrap", rejected)
    monkeypatch.setattr(database, "init_runtime_db", forbidden)
    with pytest.raises(error) as caught:
        await bridge._amain()
    assert caught.value is original and reached == ["publish"]


@pytest.mark.parametrize("supervised", [True, False])
@pytest.mark.asyncio
async def test_exact_bootstrap_is_reused_and_legacy_remains_lazy(
    configured, monkeypatch, supervised
):
    from cli_agent_orchestrator.backends import registry
    from cli_agent_orchestrator.runtime_channel import store
    from cli_agent_orchestrator.services import inbox_admission_runtime

    if not supervised:
        monkeypatch.delenv(BINDING_FD_ENV)
    events = []
    bootstrap = object()

    def publish(runtime_id):
        events.append("bootstrap")
        monkeypatch.delenv(BINDING_FD_ENV, raising=False)
        return bootstrap

    def first_sql():
        if supervised:
            assert events == ["bootstrap"], "SQL preceded the supervisor ACK"
        else:
            assert events == [], "legacy construction became eager"
        events.append("sql")

    class ReachedAdmission(Exception):
        pass

    def runtime(participant, *, bootstrap_factory):
        assert bootstrap_factory() is bootstrap
        assert events.count("bootstrap") == 1
        raise ReachedAdmission

    class LocalBridge:
        def __init__(self, *args, **kwargs):
            pass

        def stop(self):
            events.append("stop")

        async def drain(self):
            events.append("drain")

    def restore(*args):
        return "incarnation"

    monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda *args: None)
    monkeypatch.setattr(bridge, "_resource_process_bootstrap", publish)
    monkeypatch.setattr(database, "init_runtime_db", first_sql)
    monkeypatch.setattr(bridge, "_drop_stale_rows", lambda: None)
    monkeypatch.setattr(bridge.bus, "set_loop", lambda loop: None)
    monkeypatch.setattr(registry, "get_backend", lambda: object())
    monkeypatch.setattr(database, "main_target", lambda: object())
    monkeypatch.setattr(store, "RemoteOperationStore", lambda target: object())
    monkeypatch.setattr(bridge, "restore_incarnation", restore)
    monkeypatch.setattr(bridge, "Bridge", LocalBridge)
    monkeypatch.setattr(inbox_admission_runtime, "runtime_from_environment", runtime)
    with pytest.raises(ReachedAdmission):
        await bridge._amain()
    assert events.count("bootstrap") == 1 and events[-1] == "drain"


@pytest.mark.parametrize("invalid", ["missing_server", "invalid_runtime"])
@pytest.mark.asyncio
async def test_required_configuration_is_checked_before_publication(
    configured, monkeypatch, invalid
):
    if invalid == "missing_server":
        monkeypatch.delenv("CAO_BRIDGE_SERVER_URL")
    else:
        monkeypatch.setenv("CAO_BRIDGE_RUNTIME_ID", "invalid runtime")
    monkeypatch.setattr(
        bridge,
        "_resource_process_bootstrap",
        lambda runtime: pytest.fail("published invalid configuration"),
    )
    monkeypatch.setattr(
        database, "init_runtime_db", lambda: pytest.fail("SQL for invalid configuration")
    )
    with pytest.raises(SystemExit):
        await bridge._amain()


REAL_CHILD = CHILD[: CHILD.index("mode=os.environ")] + r"""
import asyncio
from types import SimpleNamespace
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.runtime_channel import bridge
from cli_agent_orchestrator.services import admission_bootstrap
from cli_agent_orchestrator.services.resource_process_supervisor import ResourceProcessUnproven
bridge.runtime_token=lambda:'synthetic-test-only'
admission_bootstrap.scoped_process_bootstrap=lambda *a,**kw:SimpleNamespace(shared_repository=lambda:SimpleNamespace(current_binding=lambda:binding))
class FirstSQL(Exception):pass
def first_sql():
    Path(os.environ['SQL_BOUNDARY_MARKER']).write_text('entered')
    raise FirstSQL()
database.init_runtime_db=first_sql
try:
    asyncio.run(bridge._amain())
except FirstSQL:
    os._exit(23)
except ResourceProcessUnproven:
    Path(os.environ['ACK_REJECTION_MARKER']).write_text('denied')
    os._exit(24)
os._exit(25)
"""


@pytest.mark.parametrize("acknowledged", [True, False])
def test_real_owned_child_cannot_cross_first_sql_until_parent_ack(tmp_path, acknowledged):
    marker = tmp_path / "first-sql"
    denied = tmp_path / "ack-rejected"
    owner = ResourceProcessSupervisor.launch(
        [sys.executable, "-c", REAL_CHILD],
        expected_store_id=STORE,
        expected_target_identity_sha256=TARGET,
        env={
            **os.environ,
            "CAO_BRIDGE_SERVER_URL": "http://127.0.0.1:1",
            "CAO_BRIDGE_RUNTIME_ID": "pre-sql-test",
            "SQL_BOUNDARY_MARKER": str(marker),
            "ACK_REJECTION_MARKER": str(denied),
        },
    )
    try:
        ready, _, _ = select.select([owner._channel], [], [], 30)
        assert ready, "child did not reach the authenticated publication boundary"
        assert not marker.exists(), "child crossed first SQL before the parent ACK"
        if acknowledged:
            original = owner.receive_binding(timeout_s=30)
            observation = owner.wait(timeout_s=30)
            assert observation.binding is original and observation.returncode == 23
            assert marker.read_text() == "entered" and not denied.exists()
        else:
            # Reject the actual packet explicitly. Closing a socket with an
            # unread packet can produce ECONNRESET instead of an ACK refusal.
            packet = json.loads(owner._channel.recv(8192))
            assert packet["binding"]["process_pid"] == owner._process.pid
            assert owner._channel.send(b"0") == 1
            assert owner.wait(timeout_s=30) is None
            assert owner._wait_status == (os.CLD_EXITED, 24)
            assert not marker.exists() and denied.read_text() == "denied"
    finally:
        owner.send_signal(signal.SIGTERM)
        owner.wait(timeout_s=30)
        owner.close()
