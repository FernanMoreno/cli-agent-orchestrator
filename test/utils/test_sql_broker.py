"""Pure protocol and gate tests; controlled issuers are not financing proof."""

import importlib
from dataclasses import replace
from uuid import uuid4

import pytest

pytestmark = pytest.mark.offline_database


def api():
    module = importlib.import_module("cli_agent_orchestrator.models.sql_startup_permission")
    assert (
        getattr(module, "SqlBrokerRequest", None) is not None
    ), "missing persistent SQL broker contract"
    return module


def request():
    m = api()
    return m.SqlBrokerRequest(
        journal_id=str(uuid4()),
        startup_intent_id=str(uuid4()),
        intent_id=str(uuid4()),
        sequence=1,
        subject_binding_sha256="a" * 64,
        sql_application_name="caos-sql-v1-" + uuid4().hex,
        path="native",
        destination_sha256="b" * 64,
        destination_scope="main",
    )


def test_exact_versioned_request_roundtrips():
    value = request()
    m = api()
    assert m.SqlBrokerRequest.from_packet(value.to_packet()) == value
    assert value.to_packet()["version"] == 4


@pytest.mark.parametrize(
    "change",
    [
        {"version": 3},
        {"sequence": True},
        {"sequence": 0},
        {"extra": 1},
        {"intent_id": "invalid"},
        {"kind": "sql_startup"},
        {"destination_scope": ""},
    ],
)
def test_invalid_or_downgraded_requests_are_not_accepted(change):
    m = api()
    packet = request().to_packet()
    packet.update(change)
    with pytest.raises(ValueError):
        m.SqlBrokerRequest.from_packet(packet)


def test_server_and_install_are_explicit_apis():
    import inspect

    from cli_agent_orchestrator.services.resource_process_supervisor import (
        ResourceProcessSupervisor,
    )
    from cli_agent_orchestrator.utils import process_sql_origin as origin

    assert "sql_broker" in inspect.signature(ResourceProcessSupervisor.launch).parameters
    assert callable(getattr(ResourceProcessSupervisor, "serve_child_sql_request", None))
    assert callable(getattr(origin, "_install_sql_broker", None))


def test_broker_permission_is_a_distinct_kind_with_one_unit():
    m = api()
    r = request()
    fields = dict(
        journal_id=r.journal_id,
        startup_intent_id=r.startup_intent_id,
        intent_id=r.intent_id,
        sequence=1,
        session_id=str(uuid4()),
        subject_binding_sha256=r.subject_binding_sha256,
        sql_application_name=r.sql_application_name,
        path=r.path,
        destination_sha256=r.destination_sha256,
        destination_scope=r.destination_scope,
    )
    value = m.SqlBrokerPermissionRecord(**fields)
    assert value.to_packet()["kind"] == "sql_opening" and value.slots == 1
    assert m.SqlBrokerPermissionRecord.from_packet(value.to_packet()) == value
    with pytest.raises(ValueError):
        replace(value, slots=True)


def controlled_gate(monkeypatch, callback):
    from test.utils.test_sql_startup_permission import issued, setup

    from cli_agent_orchestrator.utils import process_sql_origin as origin

    m, target, binding, initial = setup(monkeypatch)
    closed = []
    with issued(m, binding, initial) as cap:
        origin._install_sql_startup_permission(cap)
        origin._install_sql_broker(
            cap,
            lambda path, dest: callback(m, initial, path, dest),
            close=lambda: closed.append(True),
        )
    return origin, m, target, initial, closed


def test_each_later_opening_gets_a_distinct_permission_without_rearming_startup(monkeypatch):
    calls = []

    def reply(m, initial, path, target):
        from cli_agent_orchestrator.utils import process_sql_origin as origin

        assert origin._STATE.guard.acquire(blocking=False), "broker called under gate mutex"
        origin._STATE.guard.release()
        calls.append(path)
        return m.SqlBrokerPermissionRecord(
            session_id=str(uuid4()),
            journal_id=initial.journal_id,
            intent_id=str(uuid4()),
            startup_intent_id=initial.intent_id,
            sequence=len(calls),
            subject_binding_sha256=initial.subject_binding_sha256,
            sql_application_name=initial.sql_application_name,
            path=path,
            destination_sha256=m.sql_startup_destination_sha256(target),
            destination_scope=target.scope,
        )

    origin, m, target, _, closed = controlled_gate(monkeypatch, reply)
    with origin.sql_process_opening(destination=target):
        pass
    for path in ("native", "orm", "audit", "native", "orm"):
        with origin.sql_process_opening(path=path, destination=target):
            pass
        assert origin._STATE.startup_spent is True
    assert len(calls) == 5 and not closed and origin._STATE.opening == 0
    assert origin.seal_sql_process_openings()
    with pytest.raises(origin.SqlProcessOpeningsSealed):
        with origin.sql_process_opening(destination=target):
            pytest.fail("fence bypassed")
    assert len(calls) == 5


def test_an_inert_descriptor_cannot_install_a_persistent_broker(monkeypatch):
    from test.utils.test_sql_startup_permission import setup

    from cli_agent_orchestrator.utils import process_sql_origin as origin

    _, _, _, record = setup(monkeypatch)
    with pytest.raises(origin.SqlStartupPermissionUnavailable):
        origin._install_sql_broker(record, lambda *a: None, close=lambda: None)


def test_broker_failure_does_not_restore_the_initial_permission(monkeypatch):
    def failed(*a):
        from cli_agent_orchestrator.utils.process_sql_origin import SqlBrokerUnavailable

        raise SqlBrokerUnavailable("controlled exchange failure")

    origin, _, target, _, closed = controlled_gate(monkeypatch, failed)
    with origin.sql_process_opening(destination=target):
        pass
    with pytest.raises(origin.SqlBrokerUnavailable):
        with origin.sql_process_opening(destination=target):
            pytest.fail("driver after broker failure")
    assert origin._STATE.startup_spent and origin._STATE.opening == 0
    assert origin._STATE.registration_unconfirmed == 1 and closed == [True]


def test_a_fork_cannot_use_the_original_broker(monkeypatch):
    import os

    origin, _, target, _, _ = controlled_gate(
        monkeypatch, lambda *a: pytest.fail("inherited broker callback")
    )
    with origin.sql_process_opening(destination=target):
        pass
    pid = os.fork()
    if pid == 0:
        try:
            with origin.sql_process_opening(destination=target):
                os._exit(1)
        except origin.SqlStartupPermissionUnavailable:
            os._exit(0)
        os._exit(2)
    assert os.waitpid(pid, 0)[1] == 0


@pytest.mark.parametrize("argument", [True, 1, "yes"])
def test_broker_mode_cannot_skip_first_permission(argument):
    from cli_agent_orchestrator.services.resource_process_supervisor import (
        ResourceProcessSupervisor,
    )

    with pytest.raises(ValueError):
        ResourceProcessSupervisor.launch(
            ["unused"],
            expected_store_id=str(uuid4()),
            expected_target_identity_sha256="a" * 64,
            sql_broker=argument,
        )


def test_failed_broker_rejects_locally_without_inventing_another_uncertain_request(monkeypatch):
    def failed(*a):
        from cli_agent_orchestrator.utils.process_sql_origin import SqlBrokerUnavailable

        raise SqlBrokerUnavailable("controlled sent request uncertainty")

    origin, _, target, _, _ = controlled_gate(monkeypatch, failed)
    with origin.sql_process_opening(destination=target):
        pass
    with pytest.raises(origin.SqlBrokerUnavailable):
        with origin.sql_process_opening(destination=target):
            pytest.fail("driver after failure")
    assert origin._STATE.registration_unconfirmed == 1
    with pytest.raises(origin.SqlStartupPermissionUnavailable) as error:
        with origin.sql_process_opening(destination=target):
            pytest.fail("dead broker reused")
    assert not isinstance(error.value, origin.SqlBrokerUnavailable)
    assert origin._STATE.registration_unconfirmed == 1


def test_broker_install_is_single_use_even_if_closed_before_first_sql(monkeypatch):
    from test.utils.test_sql_startup_permission import issued, setup

    from cli_agent_orchestrator.utils import process_sql_origin as origin

    m, _, binding, initial = setup(monkeypatch)
    with issued(m, binding, initial) as cap:
        origin._install_sql_startup_permission(cap)
        origin._install_sql_broker(cap, lambda *a: None, close=lambda: None)
        origin.close_sql_broker()
        with pytest.raises(origin.SqlStartupPermissionUnavailable):
            origin._install_sql_broker(cap, lambda *a: None, close=lambda: None)


def test_sql4_preserves_all_three_prior_migration_checksums():
    from cli_agent_orchestrator.clients.sql_opening_schema import SQL_OPENING_MIGRATIONS

    assert tuple(m.checksum for m in SQL_OPENING_MIGRATIONS[:3]) == (
        "71678a014d4780947d4c093152eee9d4e57e995fcfeaa2dfdd44d9945b82f7ed",
        "c6e27af1094d494a3884e753e72210ea4dd22758d41db3c2fdccfbb56d65e2ad",
        "21ee6e386ccd774c1171bcb17d82082bcce33f27de91dec6b69156619df00074",
    )
