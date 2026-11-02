"""Controlled issuer unit tests; real root funding/kernel/TCP are separate cases."""

import hashlib
import os
from dataclasses import replace
from test.utils.test_sql_startup_permission import issued
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cli_agent_orchestrator.models import sql_startup_permission as permission
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.utils import process_sql_origin as origin

pytestmark = pytest.mark.offline_database
ROOT_MODE = "CAO_INTERNAL_ROOT_SQL_GENESIS"


def api():
    assert getattr(permission, "SQL_ROOT_GENESIS_ENV", None) == ROOT_MODE
    assert callable(getattr(origin, "_install_root_sql_startup_permission", None))
    return origin._install_root_sql_startup_permission


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(origin, "_STATE", origin._State(os.getpid()))
    for name in (
        ROOT_MODE,
        permission.SQL_STARTUP_PERMISSION_ENV,
        permission.SQL_BROKER_FD_ENV,
        origin.RESOURCE_BINDING_FD_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


def candidate():
    target = SimpleNamespace(
        identity=("host", 5432, "root", "root", str(uuid4())),
        user="runtime",
        sslmode="disable",
        scope="main",
    )
    binding = ResourceBinding(
        str(uuid4()),
        str(uuid4()),
        os.getpid(),
        1.0,
        target.identity[-1],
        permission.sql_startup_target_identity_sha256(target),
        1,
        2,
        1,
        3,
        "a" * 64,
    )
    record = permission.SqlStartupPermissionRecord(
        session_id=str(uuid4()),
        journal_id=str(uuid4()),
        intent_id=str(uuid4()),
        subject_binding_sha256=permission.sql_startup_binding_sha256(binding),
        sql_application_name=origin.current_sql_application_name(),
        path="native",
        destination_sha256=permission.sql_startup_destination_sha256(target),
        destination_scope="main",
    )
    return target, binding, record


def test_root_mode_alone_never_grants_sql(fresh, monkeypatch):
    api()
    monkeypatch.setenv(ROOT_MODE, "1")
    assert origin.current_sql_application_name().startswith("caos-sql-v1-")
    with pytest.raises(origin.SqlStartupPermissionUnavailable):
        with origin.sql_process_opening():
            pytest.fail("unfunded driver entered")


def test_root_permission_is_single_use_and_no_rearm(fresh, monkeypatch):
    install = api()
    monkeypatch.setenv(ROOT_MODE, "1")
    target, binding, record = candidate()
    with issued(permission, binding, record) as cap:
        install(cap)
        with origin.sql_process_opening(destination=target):
            pass
        with pytest.raises(origin.SqlStartupPermissionUnavailable):
            with origin.sql_process_opening(destination=target):
                pytest.fail("second root driver")
    with issued(permission, binding, replace(record, intent_id=str(uuid4()))) as cap:
        with pytest.raises(origin.SqlStartupPermissionUnavailable):
            install(cap)


def test_root_cannot_adopt_a_process_with_prior_admitted_sql(fresh, monkeypatch):
    install = api()
    with origin.sql_process_opening():
        pass
    monkeypatch.setenv(ROOT_MODE, "1")
    _, binding, record = candidate()
    with issued(permission, binding, record) as cap:
        with pytest.raises(origin.SqlStartupPermissionUnavailable):
            install(cap)


@pytest.mark.parametrize("mode", ["0", "true", ""])
def test_invalid_root_marker_is_latched_closed(fresh, monkeypatch, mode):
    install = api()
    monkeypatch.setenv(ROOT_MODE, mode)
    _, binding, record = candidate()
    monkeypatch.delenv(ROOT_MODE)
    with issued(permission, binding, record) as cap:
        with pytest.raises(origin.SqlStartupPermissionUnavailable):
            install(cap)


def test_root_and_child_grant_modes_cannot_mix(fresh, monkeypatch):
    install = api()
    monkeypatch.setenv(ROOT_MODE, "1")
    monkeypatch.setenv(permission.SQL_STARTUP_PERMISSION_ENV, "1")
    _, binding, record = candidate()
    with issued(permission, binding, record) as cap:
        with pytest.raises(origin.SqlStartupPermissionUnavailable):
            install(cap)


def test_identity_channel_can_precede_root_certificate(fresh, monkeypatch):
    install = api()
    monkeypatch.setenv(ROOT_MODE, "1")
    monkeypatch.setenv(origin.RESOURCE_BINDING_FD_ENV, "123")
    target, binding, record = candidate()
    monkeypatch.delenv(origin.RESOURCE_BINDING_FD_ENV)
    with issued(permission, binding, record) as cap:
        install(cap)
        with origin.sql_process_opening(destination=target):
            pass


def test_naked_root_data_is_not_an_issuer(fresh, monkeypatch):
    install = api()
    monkeypatch.setenv(ROOT_MODE, "1")
    _, _, record = candidate()
    with pytest.raises(origin.SqlStartupPermissionUnavailable):
        install(record)
