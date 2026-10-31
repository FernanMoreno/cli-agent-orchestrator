"""Authorized cached diagnostics must never trigger another resource probe."""

import asyncio
import importlib
from test.api.test_admission_control import control, headers
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.offline_database


def pause_sampler(client, f):
    """Join the existing sampler, keeping the runtime active for cached reads."""

    async def pause():
        runner = f.monitor._runner
        if runner is not None:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
            f.monitor._runner = None
        physical = f.monitor._physical
        if physical is not None:
            await asyncio.gather(physical, return_exceptions=True)
            f.monitor._physical = None

    client.portal.call(pause)
    for _ in range(3):
        f.clock[0] += 1.0
        f.monitor.refresh()
    assert f.owner.snapshot(now=f.clock[0]).reason == "capacity_available"


def test_admin_reads_local_quantities_and_cached_postgres(control):
    client, f, key = control
    pause_sampler(client, f)
    lease = f.owner.try_reserve(
        "private-receiver-canary", now=f.clock[0], storage_slots=2
    ).reservation
    assert lease is not None and lease.start()
    try:
        response = client.get("/admission/diagnostics", headers=headers(key))
        assert response.status_code == 200
        body = response.json()
        assert body["version"] == 1 and body["scope"] == "process_local"
        assert body["distributed_observed"] is False
        assert body["generation"] == f.owner.generation
        assert body["policy"]["memory_per_delivery_bytes"] == 64 * 1024 * 1024
        assert body["committed"]["storage_slots"] == 2
        assert body["operations"]["started_deliveries"] == 1
        assert body["operations"]["inflight_storage_only"] == 0
        assert body["postgres"]["available_slots"] == 12
        assert body["postgres"]["reason"] == "pressure_observed"
        assert response.headers["cache-control"] == "no-store"
        for private in ["private-receiver-canary", f.binding.store_id, f.binding.instance_id]:
            assert private not in response.text
    finally:
        assert lease.finish_after_storage()


@pytest.mark.parametrize("scopes", [None, "cao:read", "cao:write"])
def test_diagnostics_auth_precedes_runtime_lookup(control, monkeypatch, scopes):
    client, f, key = control
    routes = importlib.import_module("cli_agent_orchestrator.api.admission_routes")
    lookup = Mock(side_effect=AssertionError("runtime resolved without authority"))
    monkeypatch.setattr(routes, "runtime", lookup)
    response = client.get("/admission/diagnostics", headers=headers(key, scopes) if scopes else {})
    assert response.status_code in (401, 403)
    lookup.assert_not_called()


def test_diagnostics_cached_reads_do_not_perform_resource_io(control, monkeypatch):
    client, f, key = control
    pause_sampler(client, f)
    runtime = client.app.state.inbox_admission_runtime
    forbidden = Mock(side_effect=AssertionError("private-probe-canary"))
    for target, name in [
        (runtime, "_execute"),
        (runtime, "registered"),
        (f.monitor, "refresh"),
        (f.bootstrap, "load_current"),
        (f.postgres, "sample"),
        (f.resources, "sample"),
        (f.monitor._policy_loader, "load"),
    ]:
        monkeypatch.setattr(target, name, forbidden)
    for _ in range(5):
        response = client.get("/admission/diagnostics", headers=headers(key))
        assert response.status_code == 200
        assert "private-probe-canary" not in response.text
    forbidden.assert_not_called()


def test_diagnostics_staleness_and_generation_do_not_retire_started_work(control):
    client, f, key = control
    pause_sampler(client, f)
    lease = f.owner.try_reserve("busy", now=f.clock[0], storage_slots=2).reservation
    assert lease is not None and lease.start()
    try:
        f.clock[0] += 4.0
        response = client.get("/admission/diagnostics", headers=headers(key))
        assert response.status_code == 200
        body = response.json()
        assert body["reason"] == "stale_observation"
        assert body["capacity"] is None
        assert body["operations"]["started_deliveries"] == 1
        assert body["debt"] == body["committed"]
        old_generation = body["postgres"]["generation"]
        assert f.owner.invalidate(expected_generation=f.owner.generation)
        body = client.get("/admission/diagnostics", headers=headers(key)).json()
        assert body["reason"] == "unknown_capacity" and body["capacity"] is None
        assert body["generation"] != old_generation
        assert body["postgres"]["current_generation"] is False
        assert body["operations"]["started_deliveries"] == 1
    finally:
        assert lease.finish_after_storage()


def test_diagnostics_implicit_local_admin_does_not_authorize(control, monkeypatch):
    client, f, key = control
    routes = importlib.import_module("cli_agent_orchestrator.api.admission_routes")
    lookup = Mock(side_effect=AssertionError("runtime resolved without JWT admin"))
    monkeypatch.setattr(routes, "runtime", lookup)
    for name in [
        "AUTH0_DOMAIN",
        "AUTH0_AUDIENCE",
        "CAO_AUTH_AUDIENCE",
        "CAO_AUTH_ISSUER",
        "CAO_AUTH_JWKS_URI",
    ]:
        monkeypatch.delenv(name, raising=False)
    assert client.get("/admission/diagnostics").status_code in (401, 403)
    lookup.assert_not_called()


def test_diagnostics_error_does_not_echo_private_exception(control, monkeypatch):
    client, f, key = control
    runtime = client.app.state.inbox_admission_runtime
    monkeypatch.setattr(
        runtime, "diagnostics", Mock(side_effect=OSError("private-diagnostic-canary"))
    )
    response = client.get("/admission/diagnostics", headers=headers(key))
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "resource_control_unavailable"}}
    assert "private-diagnostic-canary" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_first_diagnostic_read_does_not_import_projection_from_request(control, monkeypatch):
    import builtins

    client, f, key = control
    pause_sampler(client, f)
    original_import = builtins.__import__

    def no_lazy_projection(name, *args, **kwargs):
        if name == "cli_agent_orchestrator.services.admission_diagnostics":
            raise AssertionError("diagnostic projection loaded in request")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_lazy_projection)
    response = client.get("/admission/diagnostics", headers=headers(key))
    assert response.status_code == 200
