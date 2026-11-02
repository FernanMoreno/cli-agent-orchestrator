"""Scoped unit issuer checks; kernel transport and funding are tested separately."""

import asyncio
import copy
import hashlib
import importlib
import importlib.util
import json
import os
import pickle
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.utils import process_sql_origin as origin

pytestmark = pytest.mark.offline_database


def api():
    name = "cli_agent_orchestrator.models.sql_startup_permission"
    assert importlib.util.find_spec(name) is not None, "missing SQL startup permission contract"
    module = importlib.import_module(name)
    assert callable(getattr(origin, "_install_sql_startup_permission", None))
    return module


def setup(monkeypatch, *, path="native"):
    module = api()
    monkeypatch.setattr(origin, "_STATE", origin._State(os.getpid()))
    monkeypatch.setenv(origin.RESOURCE_BINDING_FD_ENV, "123")
    monkeypatch.setenv(module.SQL_STARTUP_PERMISSION_ENV, "1")
    target = SimpleNamespace(
        identity=("localhost", 5432, "unit", "unit", str(uuid4())),
        user="unit",
        sslmode="disable",
        scope="main",
    )
    binding = ResourceBinding(
        str(uuid4()),
        str(uuid4()),
        os.getpid(),
        1.0,
        target.identity[-1],
        hashlib.sha256(json.dumps(target.identity, separators=(",", ":")).encode()).hexdigest(),
        1,
        2,
        1,
        3,
        "a" * 64,
    )
    record = module.SqlStartupPermissionRecord(
        session_id=str(uuid4()),
        journal_id=str(uuid4()),
        intent_id=str(uuid4()),
        subject_binding_sha256=module.sql_startup_binding_sha256(binding),
        sql_application_name=origin.current_sql_application_name(),
        path=path,
        destination_sha256=module.sql_startup_destination_sha256(target),
        destination_scope=target.scope,
    )
    return module, target, binding, record


@contextmanager
def issued(module, binding, record, *, validate_process=lambda: None, approved=True):
    active = [True]

    def validate():
        assert active[0], "expired controlled unit issuer"

    cap = module._issue_verified_sql_startup_permission(
        record, binding, validate=validate, validate_process=validate_process
    )
    if approved:
        module._approve_verified_sql_startup_permission(cap)
    try:
        yield cap
    finally:
        active[0] = False
        module._expire_verified_sql_startup_permission(cap)


def test_a_required_permission_is_not_a_legacy_opening(monkeypatch):
    module, target, _, _ = setup(monkeypatch)
    with pytest.raises(origin.SqlStartupPermissionUnavailable):
        with origin.sql_process_opening(destination=target):
            pytest.fail("SQL before kernel permission")
    assert origin._STATE.opening == origin._STATE.unconfirmed == 0


@pytest.mark.parametrize("path", ["native", "orm", "audit"])
def test_one_permission_covers_exactly_one_opening_and_survives_no_close_refund(monkeypatch, path):
    module, target, binding, record = setup(monkeypatch, path=path)
    with issued(module, binding, record) as cap:
        origin._install_sql_startup_permission(cap)
    with origin.sql_process_opening(path=path, destination=target):
        pass
    with pytest.raises(origin.SqlStartupPermissionUnavailable):
        with origin.sql_process_opening(path=path, destination=target):
            pytest.fail("permission reused after a successful physical call")
    assert origin._STATE.opening == origin._STATE.unconfirmed == 0


@pytest.mark.parametrize("data_only", ["record", "unapproved", "expired"])
def test_a_descriptor_candidate_or_expired_issuer_cannot_activate_authority(monkeypatch, data_only):
    module, _, binding, record = setup(monkeypatch)
    with issued(module, binding, record, approved=False) as cap:
        if data_only == "expired":
            module._expire_verified_sql_startup_permission(cap)
        with pytest.raises((ValueError, origin.SqlStartupPermissionUnavailable)):
            origin._install_sql_startup_permission(record if data_only == "record" else cap)


@pytest.mark.parametrize("copying", [copy.copy, copy.deepcopy, pickle.dumps])
def test_live_permission_is_not_copyable_or_serializable(monkeypatch, copying):
    module, _, binding, record = setup(monkeypatch)
    with issued(module, binding, record) as cap:
        with pytest.raises(TypeError):
            copying(cap)


@pytest.mark.parametrize("change", ["path", "role", "sslmode", "target", "scope"])
def test_wrong_path_or_destination_consumes_the_attempt_without_driver_entry(monkeypatch, change):
    module, target, binding, record = setup(monkeypatch)
    with issued(module, binding, record) as cap:
        origin._install_sql_startup_permission(cap)
    wrong = SimpleNamespace(**vars(target))
    path = "native"
    if change == "path":
        path = "orm"
    elif change == "target":
        wrong.identity = (*target.identity[:2], "another", *target.identity[3:])
    else:
        setattr(wrong, "user" if change == "role" else change, "other")
    if change in {"role", "sslmode", "target"}:
        assert module.sql_startup_destination_sha256(wrong) != record.destination_sha256
    elif change == "scope":
        assert wrong.scope != record.destination_scope
    else:
        assert path != record.path
    with pytest.raises(origin.SqlStartupPermissionUnavailable):
        with origin.sql_process_opening(path=path, destination=wrong):
            pytest.fail("wrong target/path was authorized")
    with pytest.raises(origin.SqlStartupPermissionUnavailable):
        with origin.sql_process_opening(destination=target):
            pytest.fail("refusal refunded permission")
    assert origin._STATE.unconfirmed == 0


@pytest.mark.parametrize("failure_kind", ["recorder", "driver", "memory", "cancel"])
def test_failure_after_admission_never_refunds_permission(monkeypatch, failure_kind):
    module, target, binding, record = setup(monkeypatch)
    with issued(module, binding, record) as cap:
        origin._install_sql_startup_permission(cap)
    failure = {
        "recorder": ValueError,
        "driver": RuntimeError,
        "memory": MemoryError,
        "cancel": asyncio.CancelledError,
    }[failure_kind]("controlled")

    def record_attempt(path, destination):
        if failure_kind == "recorder":
            raise failure

    with origin.sql_opening_recorder(record_attempt):
        with pytest.raises(type(failure)):
            with origin.sql_process_opening(destination=target):
                raise failure
    with pytest.raises(origin.SqlStartupPermissionUnavailable):
        with origin.sql_process_opening(destination=target):
            pytest.fail("failed attempt reused permission")
    assert origin._STATE.unconfirmed == (0 if failure_kind == "recorder" else 1)


def test_foreign_worker_cannot_steal_or_consume_the_original_workers_permission(monkeypatch):
    module, target, binding, record = setup(monkeypatch)
    with issued(module, binding, record) as cap:
        origin._install_sql_startup_permission(cap)

    def foreign():
        with pytest.raises(origin.SqlStartupPermissionUnavailable):
            with origin.sql_process_opening(destination=target):
                pytest.fail("foreign thread stole permission")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(foreign).result(timeout=10)
    with origin.sql_process_opening(destination=target):
        pass


def test_a_forked_successor_does_not_inherit_the_parent_permission(monkeypatch):
    module, target, binding, record = setup(monkeypatch)
    with issued(module, binding, record) as cap:
        origin._install_sql_startup_permission(cap)
    pid = os.fork()
    if pid == 0:
        try:
            with origin.sql_process_opening(destination=target):
                os._exit(1)
        except origin.SqlStartupPermissionUnavailable:
            os._exit(0)
        os._exit(2)
    assert os.waitpid(pid, 0)[1] == 0
    with origin.sql_process_opening(destination=target):
        pass


def test_terminal_fence_still_denies_an_unused_permission(monkeypatch):
    module, target, binding, record = setup(monkeypatch)
    with issued(module, binding, record) as cap:
        origin._install_sql_startup_permission(cap)
    assert origin.seal_sql_process_openings()
    with pytest.raises(origin.SqlProcessOpeningsSealed):
        with origin.sql_process_opening(destination=target):
            pytest.fail("startup permission bypassed terminal fence")


@pytest.mark.parametrize(
    "field,value",
    [
        ("slots", True),
        ("slots", 2),
        ("path", "sql"),
        ("destination_scope", ""),
        ("session_id", "invalid"),
        ("subject_binding_sha256", "A" * 64),
    ],
)
def test_wire_record_rejects_ambiguous_or_unbounded_values(monkeypatch, field, value):
    module, _, _, record = setup(monkeypatch)
    with pytest.raises(ValueError):
        replace(record, **{field: value})


def test_repeated_install_does_not_replace_an_original_permission(monkeypatch):
    module, target, binding, record = setup(monkeypatch)
    with issued(module, binding, record) as cap:
        origin._install_sql_startup_permission(cap)
        with pytest.raises((ValueError, origin.SqlStartupPermissionUnavailable)):
            origin._install_sql_startup_permission(cap)
    with origin.sql_process_opening(destination=target):
        pass


@pytest.mark.parametrize("transition", ["latch", "denial"])
def test_fork_during_mode_publication_cannot_lose_required_state(monkeypatch, transition):
    module, target, _, _ = setup(monkeypatch)
    statuses = []

    class ForkAtEnabled(origin._State):
        def __setattr__(self, name, value):
            object.__setattr__(self, name, value)
            if name != "enabled" or value is not True or getattr(self, "forked", False):
                return
            object.__setattr__(self, "forked", True)
            pid = os.fork()
            if pid == 0:
                os.environ.pop(module.SQL_STARTUP_PERMISSION_ENV, None)
                os.environ.pop(origin.RESOURCE_BINDING_FD_ENV, None)
                try:
                    with origin.sql_process_opening(destination=target):
                        os._exit(1)
                except origin.SqlStartupPermissionUnavailable:
                    os._exit(0)
                os._exit(2)
            statuses.append(os.waitpid(pid, 0)[1])

    monkeypatch.setattr(origin, "_STATE", ForkAtEnabled(os.getpid()))
    if transition == "latch":
        origin.current_sql_application_name()
    else:
        origin._deny_sql_startup_permission()
    assert statuses == [0], "fork lost the strict pending/denied mode"


def test_process_view_refusal_has_one_typed_error_and_never_refunds(monkeypatch):
    module, target, binding, record = setup(monkeypatch)
    valid = [True]

    def validate_process():
        if not valid[0]:
            raise OSError("controlled unavailable kernel view")

    with issued(module, binding, record, validate_process=validate_process) as cap:
        origin._install_sql_startup_permission(cap)
    valid[0] = False
    with pytest.raises(origin.SqlStartupPermissionUnavailable):
        with origin.sql_process_opening(destination=target):
            pytest.fail("driver after process view failure")
    valid[0] = True
    with pytest.raises(origin.SqlStartupPermissionUnavailable):
        with origin.sql_process_opening(destination=target):
            pytest.fail("process view recovery refunded the opening")
    assert origin._STATE.opening == origin._STATE.unconfirmed == 0


def test_another_async_task_cannot_steal_the_sync_workers_permission(monkeypatch):
    module, target, binding, record = setup(monkeypatch)
    with issued(module, binding, record) as cap:
        origin._install_sql_startup_permission(cap)

    async def foreign():
        with pytest.raises(origin.SqlStartupPermissionUnavailable):
            with origin.sql_process_opening(destination=target):
                pytest.fail("another task stole the original worker's permission")

    asyncio.run(foreign())
    with origin.sql_process_opening(destination=target):
        pass
