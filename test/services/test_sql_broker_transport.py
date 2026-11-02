"""Real kernel broker transport with controlled records, not financial proof."""

import json
import os
import signal
import socket
import sys
from contextlib import contextmanager
from test.services.test_sql_startup_permission_protocol import (
    DIGEST,
    PREFIX,
    STORE,
    TARGET,
    controlled_packet,
)

import pytest

from cli_agent_orchestrator.models.sql_startup_permission import (
    SQL_BROKER_FD_ENV,
    SqlBrokerPermissionRecord,
    SqlBrokerRequest,
)
from cli_agent_orchestrator.services.resource_process_supervisor import (
    ResourceProcessSupervisor,
    _broker_receive,
    _broker_send,
)

pytestmark = pytest.mark.offline_database
SCRIPT = PREFIX + r"""
from types import SimpleNamespace
from cli_agent_orchestrator.utils import process_sql_origin as origin
from cli_agent_orchestrator.models.sql_startup_permission import SQL_BROKER_FD_ENV
result=Path(os.environ['BROKER_RESULT'])
target=SimpleNamespace(**json.loads(os.environ['BROKER_TARGET']));target.identity=tuple(target.identity)
try:
    publish_resource_binding(binding,first_sql_grant=True,timeout_s=float(os.environ['BROKER_TIMEOUT']))
    with origin.sql_process_opening(destination=target):pass
    before=len(os.listdir('/proc/self/fd'))
    if os.environ['BROKER_MODE']=='fork':
        pid=os.fork()
        if pid==0:
            try:
                with origin.sql_process_opening(destination=target):os._exit(1)
            except origin.SqlStartupPermissionUnavailable:os._exit(0)
            os._exit(2)
        assert os.waitpid(pid,0)[1]==0
    if os.environ['BROKER_MODE']=='invalid_local':
        try:
            with origin.sql_process_opening(path='invalid',destination=target):raise AssertionError('invalid path')
        except origin.SqlStartupPermissionUnavailable:pass
    with origin.sql_process_opening(path='audit',destination=target):pass
    origin.close_sql_broker()
    result.write_text(json.dumps(dict(ok=True,spent=origin._STATE.startup_spent)))
    os._exit(23)
except origin.SqlStartupPermissionUnavailable as failure:
    blocked=False
    try:
        with origin.sql_process_opening(destination=target):pass
    except origin.SqlStartupPermissionUnavailable:blocked=True
    result.write_text(json.dumps(dict(ok=False,blocked=blocked,before=before,
        after=len(os.listdir('/proc/self/fd')),request_id=getattr(failure,'intent_id',None))))
    os._exit(24)
"""
compile(SCRIPT, "broker-transport-child", "exec")


@contextmanager
def child(tmp_path, mode="ok", timeout=10):
    from cli_agent_orchestrator.models.sql_startup_permission import SQL_STARTUP_PERMISSION_ENV
    from cli_agent_orchestrator.services.resource_process_supervisor import BINDING_FD_ENV

    env = dict(os.environ)
    for key in (BINDING_FD_ENV, SQL_STARTUP_PERMISSION_ENV, SQL_BROKER_FD_ENV):
        env.pop(key, None)
    marker = tmp_path / "result.json"
    env.update(
        BROKER_RESULT=str(marker),
        BROKER_TARGET=json.dumps(vars(TARGET)),
        BROKER_MODE=mode,
        BROKER_TIMEOUT=str(timeout),
    )
    owner = ResourceProcessSupervisor.launch(
        [sys.executable, "-c", SCRIPT],
        env=env,
        expected_store_id=STORE,
        expected_target_identity_sha256=DIGEST,
        first_sql_grant=True,
        sql_broker=True,
    )
    try:
        yield owner, marker
    finally:
        if not owner._exited:
            owner.send_signal(signal.SIGTERM)
        owner.wait(timeout_s=30)
        owner.close()


def handshake(owner):
    records = []

    def prepare(subject):
        records.append(controlled_packet(subject))

    owner._receive_binding(
        timeout_s=30, before_ack=prepare, first_sql_reply=lambda: json.dumps(records[0]).encode()
    )
    return records[0]


def response(request):
    from uuid import uuid4

    values = {k: v for k, v in vars(request).items()}
    return SqlBrokerPermissionRecord(**values, session_id=str(uuid4()))


@pytest.mark.parametrize("mode", ["ok", "fork", "invalid_local"])
def test_persistent_exchange_preserves_startup_and_refuses_fork_inheritance(tmp_path, mode):
    with child(tmp_path, mode) as (owner, marker):
        initial = handshake(owner)
        request = SqlBrokerRequest.from_packet(
            _broker_receive(owner._broker_channel, owner._process.pid)
        )
        assert request.startup_intent_id == initial["intent_id"] and request.path == "audit"
        _broker_send(owner._broker_channel, response(request).to_packet())
        assert owner.wait(timeout_s=30).returncode == 23
        assert json.loads(marker.read_text()) == dict(ok=True, spent=True)


@pytest.mark.parametrize(
    "fault", ["nonce", "sequence", "version", "duplicate", "rights", "foreign_parent"]
)
def test_invalid_reply_never_rearms_or_enters_the_driver(tmp_path, fault):
    with child(tmp_path) as (owner, marker):
        handshake(owner)
        request = SqlBrokerRequest.from_packet(
            _broker_receive(owner._broker_channel, owner._process.pid)
        )
        packet = response(request).to_packet()
        if fault == "nonce":
            packet["intent_id"] = packet["session_id"]
        elif fault == "sequence":
            packet["sequence"] += 1
        elif fault == "version":
            packet["version"] = 3
        raw = json.dumps(packet).encode()
        if fault == "duplicate":
            raw = raw.replace(b'"slots": 1', b'"slots": 1, "slots": 1')
        if fault == "rights":
            import struct

            fd = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
            try:
                owner._broker_channel.sendmsg(
                    [raw], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", fd))]
                )
            finally:
                os.close(fd)
        elif fault == "foreign_parent":
            pid = os.fork()
            if pid == 0:
                owner._broker_channel.send(raw)
                os._exit(0)
            assert os.waitpid(pid, 0)[1] == 0
        else:
            owner._broker_channel.send(raw)
        assert owner.wait(timeout_s=30).returncode == 24
        result = json.loads(marker.read_text())
        assert not result["ok"] and result["blocked"]
        assert result["request_id"] == request.intent_id
        assert result["after"] == result["before"] - 1


def test_timeout_is_terminal_and_a_late_reply_cannot_be_reused(tmp_path):
    with child(tmp_path, timeout=0.3) as (owner, marker):
        handshake(owner)
        request = SqlBrokerRequest.from_packet(
            _broker_receive(owner._broker_channel, owner._process.pid)
        )
        assert owner.wait(timeout_s=30).returncode == 24
        assert json.loads(marker.read_text())["blocked"]
        with pytest.raises(OSError):
            _broker_send(owner._broker_channel, response(request).to_packet())
