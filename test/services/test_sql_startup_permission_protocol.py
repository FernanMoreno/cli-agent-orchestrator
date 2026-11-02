"""Kernel transport controls with a controlled issuer, not financing evidence."""

import hashlib
import json
import os
import signal
import socket
import struct
import sys
from contextlib import contextmanager
from test.services.test_resource_process_supervisor import CHILD, STORE
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cli_agent_orchestrator.models.sql_startup_permission import (
    SQL_STARTUP_PERMISSION_ENV,
    SqlStartupPermissionRecord,
    sql_startup_binding_sha256,
    sql_startup_destination_sha256,
)
from cli_agent_orchestrator.services.resource_process_supervisor import (
    BINDING_FD_ENV,
    ResourceProcessSupervisor,
    ResourceProcessUnproven,
)

pytestmark = pytest.mark.offline_database
TARGET = SimpleNamespace(
    identity=("permission-unit", 5432, "unit", "unit", STORE),
    user="unit",
    sslmode="disable",
    scope="main",
)
DIGEST = hashlib.sha256(json.dumps(TARGET.identity, separators=(",", ":")).encode()).hexdigest()
PREFIX = CHILD[: CHILD.index("mode=os.environ")].replace("'a'*64", repr(DIGEST))
SCRIPT = PREFIX + r"""
from types import SimpleNamespace
from cli_agent_orchestrator.models.sql_startup_permission import SQL_STARTUP_PERMISSION_ENV
from cli_agent_orchestrator.utils import process_sql_origin as origin
result=Path(os.environ['PERMISSION_RESULT'])
target=SimpleNamespace(**json.loads(os.environ['PERMISSION_TARGET']))
target.identity=tuple(target.identity)
before_fds=len(os.listdir('/proc/self/fd'))
pre=False
mode=os.environ.get('PERMISSION_MODE','ok')
if mode=='early_cleanup':os.environ.pop(SQL_STARTUP_PERMISSION_ENV)
try:
    if mode!='early_cleanup':
        try:
            with origin.sql_process_opening(destination=target):
                raise AssertionError('driver before grant')
        except origin.SqlStartupPermissionUnavailable:pre=True
    if mode=='early_cleanup':publish_resource_binding(binding)
    elif mode=='version2':
        os.environ.pop(SQL_STARTUP_PERMISSION_ENV)
        publish_resource_binding(binding)
    elif mode=='identity_publisher':publish_resource_binding(binding)
    else:publish_resource_binding(binding,first_sql_grant=True)
    with origin.sql_process_opening(destination=target):pass
    try:
        with origin.sql_process_opening(destination=target):raise AssertionError('second driver')
    except origin.SqlStartupPermissionUnavailable:pass
    result.write_text(json.dumps(dict(pre=pre,first=True,second_denied=True)))
    os._exit(23)
except BaseException as failure:
    blocked=False
    try:
        with origin.sql_process_opening(destination=target):pass
    except origin.SqlStartupPermissionUnavailable:blocked=True
    result.write_text(json.dumps(dict(pre=pre,first=False,blocked=blocked,error=type(failure).__name__,before_fds=before_fds,after_fds=len(os.listdir('/proc/self/fd')))))
    os._exit(24)
"""
compile(SCRIPT, "sql-permission-protocol-child", "exec")


@contextmanager
def child_permission(tmp_path, *, mode="ok", wrapper=None):
    marker = tmp_path / "permission-result.json"
    environment = dict(os.environ)
    environment.pop(BINDING_FD_ENV, None)
    environment.pop(SQL_STARTUP_PERMISSION_ENV, None)
    environment.update(
        PERMISSION_RESULT=str(marker),
        PERMISSION_TARGET=json.dumps(vars(TARGET)),
        PERMISSION_MODE=mode,
    )
    argv = [sys.executable, "-c", SCRIPT]
    if wrapper == "exec":
        argv = ["/bin/sh", "-c", 'exec "$@"', "permission-wrapper", *argv]
    elif wrapper == "nonexec":
        argv = ["/bin/sh", "-c", '"$@"; code=$?; exit "$code"', "permission-wrapper", *argv]
    owner = ResourceProcessSupervisor.launch(
        argv,
        expected_store_id=STORE,
        expected_target_identity_sha256=DIGEST,
        env=environment,
        first_sql_grant=True,
    )
    try:
        yield owner, marker
    finally:
        if not owner._exited:
            owner.send_signal(signal.SIGTERM)
        owner.wait(timeout_s=30)
        owner.close()


def controlled_packet(subject):
    return SqlStartupPermissionRecord(
        session_id=str(uuid4()),
        journal_id=str(uuid4()),
        intent_id=str(uuid4()),
        subject_binding_sha256=sql_startup_binding_sha256(subject.binding),
        sql_application_name=subject.sql_application_name,
        path="native",
        destination_sha256=sql_startup_destination_sha256(TARGET),
        destination_scope="main",
    ).to_packet()


def receive_controlled(owner, transform=lambda packet: packet):
    payload = []

    def inspect(subject):
        packet = transform(controlled_packet(subject))
        payload.append(packet if type(packet) is bytes else json.dumps(packet).encode())

    owner._receive_binding(timeout_s=30, before_ack=inspect, first_sql_reply=lambda: payload[0])


def test_exact_kernel_parent_can_install_one_permission_before_any_driver(tmp_path):
    with child_permission(tmp_path) as (owner, marker):
        receive_controlled(owner)
        assert owner.wait(timeout_s=30).returncode == 23
        assert json.loads(marker.read_text()) == dict(pre=True, first=True, second_denied=True)


@pytest.mark.parametrize(
    "fault", ["legacy_ack", "extra", "slots_bool", "version_bool", "subject", "label", "duplicate"]
)
def test_bad_or_downgraded_reply_never_arms_a_permission(tmp_path, fault):
    def changed(packet):
        if fault == "legacy_ack":
            return b"1"
        if fault == "extra":
            packet["extra"] = 1
        elif fault == "slots_bool":
            packet["slots"] = True
        elif fault == "version_bool":
            packet["version"] = True
        elif fault == "subject":
            packet["subject_binding_sha256"] = "0" * 64
        elif fault == "label":
            packet["sql_application_name"] = "caos"
        elif fault == "duplicate":
            return json.dumps(packet).replace('"slots": 1', '"slots": 1, "slots": 1').encode()
        return packet

    with child_permission(tmp_path) as (owner, marker):
        receive_controlled(owner, changed)
        assert owner.wait(timeout_s=30).returncode == 24
        result = json.loads(marker.read_text())
        assert result["pre"] and not result["first"] and result["blocked"]


@pytest.mark.parametrize(
    "method", ["identity_receiver", "version2_request", "identity_publisher", "early_cleanup"]
)
def test_protocol_mismatch_cannot_silently_fall_back_to_legacy_sql(tmp_path, method):
    mode = {
        "identity_receiver": "ok",
        "version2_request": "version2",
        "identity_publisher": "identity_publisher",
        "early_cleanup": "early_cleanup",
    }[method]
    with child_permission(tmp_path, mode=mode) as (owner, marker):
        with pytest.raises(ResourceProcessUnproven):
            if method == "identity_receiver":
                owner.receive_binding(timeout_s=30)
            else:
                receive_controlled(owner)
        assert owner.wait(timeout_s=30) is None
        assert owner._wait_status == (os.CLD_EXITED, 24)
        result = json.loads(marker.read_text())
        assert not result["first"] and result["blocked"]


def test_descriptor_reply_from_a_forked_writer_is_not_the_original_parent(tmp_path, monkeypatch):
    with child_permission(tmp_path) as (owner, marker):
        original = socket.socket.send

        def forked(sock, payload, *args, **kwargs):
            if sock is not owner._channel:
                return original(sock, payload, *args, **kwargs)
            pid = os.fork()
            if pid == 0:
                original(sock, payload, *args, **kwargs)
                os._exit(0)
            assert os.waitpid(pid, 0)[1] == 0
            return len(payload)

        with monkeypatch.context() as patch:
            patch.setattr(socket.socket, "send", forked)
            receive_controlled(owner)
        assert owner.wait(timeout_s=30).returncode == 24
        result = json.loads(marker.read_text())
        assert not result["first"] and result["blocked"]


def test_rejected_reply_closes_received_rights_as_well_as_the_original_channel(
    tmp_path, monkeypatch
):
    fd = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
    try:
        with child_permission(tmp_path) as (owner, marker):
            original = socket.socket.send

            def rights(sock, payload, *args, **kwargs):
                if sock is owner._channel:
                    return sock.sendmsg(
                        [payload], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", fd))]
                    )
                return original(sock, payload, *args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(socket.socket, "send", rights)
                receive_controlled(owner)
            assert owner.wait(timeout_s=30).returncode == 24
            result = json.loads(marker.read_text())
            assert not result["first"] and result["blocked"]
            assert result["after_fds"] == result["before_fds"] - 1
    finally:
        os.close(fd)


@pytest.mark.parametrize("wrapper", ["exec", "nonexec"])
def test_argv_must_preserve_the_exact_owned_child_lineage(tmp_path, wrapper):
    with child_permission(tmp_path, wrapper=wrapper) as (owner, marker):
        if wrapper == "exec":
            receive_controlled(owner)
            assert owner.wait(timeout_s=30).returncode == 23
            assert json.loads(marker.read_text())["first"]
        else:
            with pytest.raises(ResourceProcessUnproven):
                receive_controlled(owner)
            assert owner.wait(timeout_s=30) is None
            assert owner._wait_status == (os.CLD_EXITED, 24)
            result = json.loads(marker.read_text())
            assert not result["first"] and result["blocked"]
