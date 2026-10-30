"""Pressure queries use the ordinary runtime role and retire their own sessions."""

import importlib
import json
from contextlib import contextmanager
from dataclasses import asdict

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


def test_pressure_changes_when_another_real_backend_is_held(postgres_targets, request):
    api = importlib.import_module("cli_agent_orchestrator.services.admission_postgres")
    from cli_agent_orchestrator.clients.postgres import connect

    target = postgres_targets.main
    held_pid = [None]
    snapshots = []

    @contextmanager
    def observed_connection(selected, *, readonly):
        with connect(selected, readonly=readonly) as probe:
            probe_pid = probe.execute("SELECT pg_catalog.pg_backend_pid()").fetchone()[0]
            # Both views use this transaction's cached backend-status array.
            # Restrict to the same visible database OIDs as pg_stat_database.
            pids = {
                row[0]
                for row in probe.execute(
                    "SELECT pid FROM pg_catalog.pg_stat_activity "
                    "WHERE datid IN (SELECT oid FROM pg_catalog.pg_database)"
                ).fetchall()
            }
            participants = {probe_pid}
            if held_pid[0] is not None:
                participants.add(held_pid[0])
            assert participants <= pids
            snapshots.append({"participants": sorted(participants), "all_pids": sorted(pids)})
            yield probe

    sampler = api.PostgresPressureSampler(
        target_supplier=lambda: target, connect_factory=observed_connection
    )
    first = sampler.sample()
    assert first.observation.available_slots is not None
    with connect(target, readonly=True) as held:
        held_pid[0] = held.execute("SELECT pg_catalog.pg_backend_pid()").fetchone()[0]
        loaded = sampler.sample()
        assert loaded.observation.available_slots is not None
    request.node.user_properties.append(
        (
            "pressure_snapshots",
            json.dumps({"snapshots": snapshots, "baseline": asdict(first), "held": asdict(loaded)}),
        )
    )
    adjusted_headroom = []
    for sample, snapshot in zip((first, loaded), snapshots):
        # Count every backend independently, including other workers and
        # sockets already closed locally but still registered on the server.
        assert sample.observed_backends == len(snapshot["all_pids"])
        external = len(set(snapshot["all_pids"]) - set(snapshot["participants"]))
        budget = sample.ordinary_connection_budget - sample.policy_reserved_slots
        raw_headroom = budget - sample.observed_backends
        assert sample.observation.available_slots == max(0, raw_headroom)
        adjusted_headroom.append(raw_headroom + external)
    assert [len(snapshot["participants"]) for snapshot in snapshots] == [1, 2]
    assert first.ordinary_connection_budget == loaded.ordinary_connection_budget
    assert first.policy_reserved_slots == loaded.policy_reserved_slots
    assert adjusted_headroom[1] == adjusted_headroom[0] - 1
