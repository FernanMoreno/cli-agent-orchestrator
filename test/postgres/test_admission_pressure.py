"""Pressure queries use the ordinary runtime role and retire their own sessions."""

import importlib
from contextlib import contextmanager

import pytest

pytestmark = pytest.mark.postgres


def test_pressure_query_is_runtime_readonly_and_closes_its_session(postgres_targets):
    api = importlib.import_module("cli_agent_orchestrator.services.admission_postgres")
    from cli_agent_orchestrator.clients.postgres import connect

    target = postgres_targets.main
    seen = []

    @contextmanager
    def observed_connection(selected, *, readonly):
        with connect(selected, readonly=readonly) as connection:
            seen.append(connection)
            assert connection.read_only
            assert (
                connection.execute(
                    "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
                ).fetchone()[0]
                is False
            )
            yield connection

    sampler = api.PostgresPressureSampler(
        target_supplier=lambda: target, connect_factory=observed_connection
    )
    result = sampler.sample()
    assert result.reason == "pressure_observed"
    assert result.observation.available_slots is not None
    assert result.maximum_connections > result.ordinary_connection_budget
    assert result.observed_backends >= 1
    assert len(seen) == 1 and seen[0].raw.closed
    assert target.user not in repr(result)
    assert target.database not in repr(result)


def test_pressure_changes_when_another_real_backend_is_held(postgres_targets):
    api = importlib.import_module("cli_agent_orchestrator.services.admission_postgres")
    from cli_agent_orchestrator.clients.postgres import connect

    target = postgres_targets.main
    sampler = api.PostgresPressureSampler(target_supplier=lambda: target)
    first = sampler.sample()
    assert first.observation.available_slots is not None
    with connect(target, readonly=True):
        loaded = sampler.sample()
        assert loaded.observation.available_slots is not None
        assert loaded.observed_backends >= first.observed_backends + 1
        assert loaded.observation.available_slots <= first.observation.available_slots - 1
