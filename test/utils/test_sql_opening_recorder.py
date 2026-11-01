"""Registration refusal is not evidence that a TCP attempt ran."""

import os

import pytest

from cli_agent_orchestrator.utils import process_sql_origin as origin

pytestmark = pytest.mark.offline_database


def fresh(monkeypatch):
    monkeypatch.setattr(origin, "_STATE", origin._State(os.getpid()))
    monkeypatch.setenv(origin.RESOURCE_BINDING_FD_ENV, "123")


def test_registration_refusal_does_not_manufacture_an_unconfirmed_tcp_attempt(monkeypatch):
    fresh(monkeypatch)

    def refused(path, destination):
        raise ValueError("controlled registration refusal")

    with origin.sql_opening_recorder(refused):
        with pytest.raises(ValueError, match="controlled registration refusal"):
            with origin.sql_process_opening():
                pytest.fail("driver ran after registration refusal")
    assert origin._STATE.opening == origin._STATE.unconfirmed == 0


def test_recursive_registration_cannot_skip_recording_or_create_tcp_uncertainty(monkeypatch):
    fresh(monkeypatch)

    def recursive(path, destination):
        with origin.sql_process_opening():
            pytest.fail("recursive recorder reached a physical driver")

    with origin.sql_opening_recorder(recursive):
        with pytest.raises(origin.SqlOpeningRecorderUnavailable, match="Recursive"):
            with origin.sql_process_opening():
                pytest.fail("outer driver ran after recursive recording")
    assert origin._STATE.opening == origin._STATE.unconfirmed == 0


def test_acknowledged_recording_precedes_driver_and_restores_its_scope(monkeypatch):
    fresh(monkeypatch)
    order = []
    destination = object()

    def recorded(path, selected):
        assert path == "audit" and selected is destination
        order.append("committed")

    with origin.sql_opening_recorder(recorded):
        with origin.sql_process_opening(path="audit", destination=destination):
            order.append("driver")
    with origin.sql_process_opening():
        order.append("legacy")
    assert order == ["committed", "driver", "legacy"]


def test_unacknowledged_preparation_is_retained_separately_from_tcp(monkeypatch):
    from cli_agent_orchestrator.models.sql_opening_intent import SqlOpeningPreparationUncertain

    fresh(monkeypatch)

    def unknown(path, destination):
        raise SqlOpeningPreparationUncertain("controlled lost registration acknowledgement")

    with origin.sql_opening_recorder(unknown):
        with pytest.raises(SqlOpeningPreparationUncertain):
            with origin.sql_process_opening():
                pytest.fail("driver ran with an unacknowledged registration")
    assert origin._STATE.unconfirmed == 0
    assert origin._STATE.registration_unconfirmed == 1 and origin._STATE.opening == 0


def test_worker_identity_is_checked_again_after_registration_callback(monkeypatch):
    fresh(monkeypatch)
    task = [object()]
    monkeypatch.setattr(origin, "_recording_task", lambda: task[0])

    def transferred(path, destination):
        task[0] = object()

    with origin.sql_opening_recorder(transferred):
        with pytest.raises(origin.SqlOpeningRecorderUnavailable, match="physical worker"):
            with origin.sql_process_opening():
                pytest.fail("a callback transferred its acknowledgement to another task")
    assert origin._STATE.opening == origin._STATE.unconfirmed == 0
