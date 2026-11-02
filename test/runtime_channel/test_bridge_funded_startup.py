"""Adapter ordering and lifetime; physical funding is tested against PostgreSQL."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from cli_agent_orchestrator.clients.postgres import Connection, DatabaseTarget
from cli_agent_orchestrator.clients.sql_intent_repository import SqlIntentRepository
from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.runtime_channel import bridge, bridge_supervisor
from cli_agent_orchestrator.services.resource_process_supervisor import ResourceProcessSupervisor

pytestmark = pytest.mark.offline_database


def test_strict_bridge_publisher_requests_the_authenticated_first_permission(monkeypatch):
    from cli_agent_orchestrator.models.sql_startup_permission import SQL_STARTUP_PERMISSION_ENV
    from cli_agent_orchestrator.services import admission_bootstrap, resource_process_supervisor

    binding = object()
    bootstrap = SimpleNamespace(
        shared_repository=lambda: SimpleNamespace(current_binding=lambda: binding)
    )
    monkeypatch.setenv(resource_process_supervisor.BINDING_FD_ENV, "123")
    monkeypatch.setenv(SQL_STARTUP_PERMISSION_ENV, "1")
    monkeypatch.setattr(admission_bootstrap, "scoped_process_bootstrap", lambda *a, **kw: bootstrap)
    calls = []
    monkeypatch.setattr(
        resource_process_supervisor,
        "publish_resource_binding",
        lambda value, **kwargs: calls.append((value, kwargs)),
    )
    assert bridge._resource_process_bootstrap("strict-test") is bootstrap
    assert calls == [(binding, {"first_sql_grant": True})]


def test_opt_in_launcher_passes_both_strict_modes(monkeypatch):
    from cli_agent_orchestrator.clients import database

    monkeypatch.setattr(
        database, "main_target", lambda: SimpleNamespace(store_id=str(uuid4()), identity=("x",))
    )
    calls = []
    monkeypatch.setattr(ResourceProcessSupervisor, "launch", lambda *a, **kw: calls.append(kw))
    bridge_supervisor.launch_bridge(
        env={"CAO_ADMISSION_MODE": "shared", "CAO_ADMISSION_POLICY_FILE": "/private/policy"},
        sql_broker=True,
    )
    assert calls[0]["first_sql_grant"] is True and calls[0]["sql_broker"] is True


@pytest.mark.parametrize("value", [1, None, "true"])
def test_launcher_rejects_ambiguous_mode_before_target(monkeypatch, value):
    from cli_agent_orchestrator.clients import database

    monkeypatch.setattr(
        database, "main_target", lambda: pytest.fail("resolved target before mode validation")
    )
    with pytest.raises(ValueError):
        bridge_supervisor.launch_bridge(sql_broker=value)


@pytest.fixture
def controlled(monkeypatch, tmp_path):
    from cli_agent_orchestrator.clients import database

    target = DatabaseTarget(
        "localhost",
        1234,
        "fixture",
        "runtime",
        "fixture",
        str(uuid4()),
        tmp_path / "secret",
        sslmode="disable",
    )
    channel = object.__new__(Connection)
    channel.target = target
    monkeypatch.setattr(Connection, "_check_funded_worker", lambda self: None)
    monkeypatch.setattr(database, "main_target", lambda: target)
    repository = SqlIntentRepository(target, binding_supplier=lambda: None)
    options = dict(
        channel=channel,
        repository=repository,
        journal_id=str(uuid4()),
        intent_id=str(uuid4()),
        session_reservation=object(),
    )
    events = []

    class Owner:
        def receive_binding_with_child_grant(self, **kwargs):
            events.append("grant")
            assert kwargs["channel"] is channel
            assert kwargs["session_reservation"] is options["session_reservation"]

        def send_signal(self, value):
            events.append("signal")

        def wait(self):
            events.append("wait")
            return object()

        def close(self):
            events.append("close")

    owner = Owner()

    def launch(**kwargs):
        events.append("launch")
        assert kwargs["sql_broker"] is True
        return owner

    monkeypatch.setattr(bridge_supervisor, "launch_bridge", launch)
    return options, owner, events


@pytest.mark.parametrize("failure", [None, MemoryError, BaseException])
def test_context_grants_before_body_and_always_joins(controlled, failure):
    options, owner, events = controlled
    error = failure("original") if failure else None

    def use():
        with bridge_supervisor.funded_bridge(**options) as active:
            assert active is owner and events == ["launch", "grant"]
            events.append("body")
            if error:
                raise error

    if error:
        with pytest.raises(failure) as caught:
            use()
        assert caught.value is error
    else:
        use()
    assert events == ["launch", "grant", "body", "signal", "wait", "close"]


def test_foreign_funding_fails_before_launch(controlled, monkeypatch):
    options, _, events = controlled

    def deny(self):
        raise StorageAdmissionUnavailable()

    monkeypatch.setattr(Connection, "_check_funded_worker", deny)
    with pytest.raises(StorageAdmissionUnavailable):
        with bridge_supervisor.funded_bridge(**options):
            pytest.fail("unfunded body")
    assert events == []


def test_different_canonical_target_fails_before_launch(controlled, monkeypatch):
    from dataclasses import replace

    from cli_agent_orchestrator.clients import database

    options, _, events = controlled
    monkeypatch.setattr(
        database, "main_target", lambda: replace(options["channel"].target, store_id=str(uuid4()))
    )
    with pytest.raises(ValueError):
        with bridge_supervisor.funded_bridge(**options):
            pytest.fail("wrong store body")
    assert events == []


def test_invalid_intent_fails_before_launch(controlled):
    options, _, events = controlled
    options["intent_id"] = "not-a-canonical-id"
    with pytest.raises(ValueError):
        with bridge_supervisor.funded_bridge(**options):
            pytest.fail("invalid request body")
    assert events == []


def test_failure_before_yield_retains_exact_owner_and_primary_error(controlled, monkeypatch):
    options, owner, events = controlled
    original = MemoryError("grant failure")

    def failed_grant(**kwargs):
        events.append("grant-failed")
        raise original

    def failed_wait():
        events.append("wait-failed")
        raise RuntimeError("join failure")

    monkeypatch.setattr(owner, "receive_binding_with_child_grant", failed_grant)
    monkeypatch.setattr(owner, "wait", failed_wait)
    with pytest.raises(MemoryError) as caught:
        with bridge_supervisor.funded_bridge(**options):
            pytest.fail("yielded without confirmed initial grant")
    assert caught.value is original and original.resource_process_owner is owner
    assert events == ["launch", "grant-failed", "signal", "wait-failed"]
    assert any("cleanup is unconfirmed" in note for note in original.__notes__)


def test_cleanup_failure_after_body_retains_owner(controlled, monkeypatch):
    options, owner, events = controlled
    original = MemoryError("close failure")

    def failed_close():
        events.append("close-failed")
        raise original

    monkeypatch.setattr(owner, "close", failed_close)
    with pytest.raises(MemoryError) as caught:
        with bridge_supervisor.funded_bridge(**options):
            events.append("body")
    assert caught.value is original and original.resource_process_owner is owner
    assert events == ["launch", "grant", "body", "signal", "wait", "close-failed"]


def test_missing_reservation_is_rejected_before_launch(controlled):
    options, _, events = controlled
    options["session_reservation"] = None
    with pytest.raises(ValueError):
        with bridge_supervisor.funded_bridge(**options):
            pytest.fail("launched without reservation")
    assert events == []


def test_missing_exit_observation_never_reports_success_or_discards_handle(controlled, monkeypatch):
    options, owner, events = controlled

    def unproven_exit():
        events.append("wait-unproven")
        return None

    monkeypatch.setattr(owner, "wait", unproven_exit)
    with pytest.raises(bridge_supervisor.ResourceProcessUnproven) as caught:
        with bridge_supervisor.funded_bridge(**options):
            events.append("body")
    assert caught.value.resource_process_owner is owner
    assert events == ["launch", "grant", "body", "signal", "wait-unproven"]
