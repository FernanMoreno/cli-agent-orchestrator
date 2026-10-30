"""PostgreSQL pressure is fresh telemetry, never a cluster-wide reservation."""

import importlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database


def api():
    return importlib.import_module("cli_agent_orchestrator.services.admission_postgres")


def target():
    return SimpleNamespace(database="database-private-canary", user="role-private-canary")


def row(**changes):
    values = {
        "maximum": 100,
        "superuser_reserved": 3,
        "reserved": 2,
        "backends": 10,
        "database": target().database,
        "role": target().user,
    }
    values.update(changes)
    return values


def probe(values=None, *, error=None, supplier=target):
    events = []

    @contextmanager
    def connect(selected, *, readonly):
        events.append(("open", readonly))
        assert selected.database == target().database

        class Connection:
            raw = SimpleNamespace(closed=False)

            def execute(self, statement, params=()):
                if statement.startswith("SET LOCAL"):
                    events.append(("configure",))
                else:
                    events.append(("query",))
                    if error is not None:
                        raise error
                return self

            def fetchone(self):
                return values

        connection = Connection()
        try:
            yield connection
        finally:
            connection.raw.closed = True
            events.append(("closed",))

    value = api().PostgresPressureSampler(
        target_supplier=supplier, connect_factory=connect, clock=lambda: 10.0
    )
    return value.sample(), events


def test_pressure_preserves_both_server_and_policy_reserves():
    result, events = probe(row())
    assert result.reason == "pressure_observed"
    assert result.maximum_connections == 100
    assert result.ordinary_connection_budget == 95
    assert result.policy_reserved_slots == 19
    assert result.observed_backends == 10
    assert result.observation.available_slots == 66
    assert result.observation.observed_at == 10.0
    assert events[0] == ("open", True)
    assert events[-1] == ("closed",)
    assert any(event[0] == "configure" for event in events)


def test_overloaded_storage_is_known_zero_not_unrestricted_capacity():
    result, _ = probe(row(backends=100))
    assert result.reason == "pressure_observed"
    assert result.observation.available_slots == 0


def test_missing_inventory_cannot_mean_an_idle_server():
    result, _ = probe(row(backends=0))
    assert result.reason == "insufficient_visibility"
    assert result.observation.available_slots is None


@pytest.mark.parametrize("values", [None, row(role="wrong"), row(database="wrong")])
def test_incorrect_identity_or_missing_row_does_not_publish_capacity(values):
    result, events = probe(values)
    assert result.observation.available_slots is None
    assert result.reason in {"identity_mismatch", "invalid_pressure_data"}
    assert events[-1] == ("closed",)


@pytest.mark.parametrize(
    "values",
    [
        row(maximum=True),
        row(maximum=0),
        row(maximum="100"),
        row(superuser_reserved=-1),
        row(reserved=101),
        row(backends=-1),
        row(backends=False),
    ],
)
def test_invalid_inventory_is_not_coerced_into_slots(values):
    result, events = probe(values)
    assert result.reason == "invalid_pressure_data"
    assert result.observation.available_slots is None
    assert events[-1] == ("closed",)


def test_failure_is_redacted_and_connection_is_closed():
    result, events = probe(row(), error=OSError("private-pressure-error-canary"))
    assert result.reason == "postgres_unavailable"
    assert result.observation.available_slots is None
    assert "private" not in repr(result)
    assert events[-1] == ("closed",)


def test_supplier_failure_returns_unknown_without_opening_a_connection():
    def unavailable():
        raise OSError("private-target-canary")

    result, events = probe(row(), supplier=unavailable)
    assert not events
    assert result.reason == "postgres_unavailable"
    assert result.observation.available_slots is None
    assert "private" not in repr(result)


@pytest.mark.parametrize("reserve", [True, -1, 100, 1.5])
def test_invalid_pressure_policy_is_rejected(reserve):
    with pytest.raises(ValueError):
        api().PostgresPressurePolicy(reserve_percent=reserve)


def test_each_sample_resolves_the_target_again_and_excludes_private_identity():
    calls = []
    times = iter((10.0, 11.0))

    def supplier():
        calls.append(len(calls))
        return target()

    @contextmanager
    def connect(selected, *, readonly):
        class Connection:
            raw = SimpleNamespace(closed=False)

            def execute(self, statement):
                return self

            def fetchone(self):
                return row()

        connection = Connection()
        try:
            yield connection
        finally:
            connection.raw.closed = True

    sampler = api().PostgresPressureSampler(
        target_supplier=supplier, connect_factory=connect, clock=lambda: next(times)
    )
    first, second = sampler.sample(), sampler.sample()
    assert calls == [0, 1]
    assert (first.observation.observed_at, second.observation.observed_at) == (10.0, 11.0)
    assert "private" not in repr(second)


def test_unconfirmed_close_blocks_new_probes_until_physical_retirement():
    opened = []
    fail_close = [True]

    @contextmanager
    def connect(selected, *, readonly):
        class Connection:
            def __init__(self):
                self.raw = SimpleNamespace(closed=False)

            def execute(self, statement):
                return self

            def fetchone(self):
                return row()

        connection = Connection()
        opened.append(connection)
        try:
            yield connection
        finally:
            if fail_close[0]:
                raise OSError("private-close-canary")
            connection.raw.closed = True

    sampler = api().PostgresPressureSampler(
        target_supplier=target, connect_factory=connect, clock=lambda: 10.0
    )
    first = sampler.sample()
    assert first.reason == "probe_cleanup_unconfirmed"
    assert first.observation.available_slots is None
    assert sampler.configure(api().PostgresPressurePolicy(reserve_percent=30))
    assert sampler.sample().reason == "probe_cleanup_unconfirmed"
    assert len(opened) == 1
    opened[0].raw.closed = True
    fail_close[0] = False
    assert sampler.sample().reason == "pressure_observed"
    assert len(opened) == 2 and opened[1].raw.closed


def test_parallel_probe_does_not_open_another_session():
    import threading
    from concurrent.futures import ThreadPoolExecutor

    entered = threading.Event()
    release = threading.Event()
    opened = []

    @contextmanager
    def connect(selected, *, readonly):
        class Connection:
            raw = SimpleNamespace(closed=False)

            def execute(self, statement):
                if not statement.startswith("SET LOCAL"):
                    entered.set()
                    assert release.wait(3), "probe controller did not release query"
                return self

            def fetchone(self):
                return row()

        connection = Connection()
        opened.append(connection)
        try:
            yield connection
        finally:
            connection.raw.closed = True

    sampler = api().PostgresPressureSampler(
        target_supplier=target, connect_factory=connect, clock=lambda: 10.0
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(sampler.sample)
        try:
            assert entered.wait(2)
            second = sampler.sample()
            assert second.reason == "probe_busy"
            assert second.observation.available_slots is None
            assert len(opened) == 1
        finally:
            release.set()
        assert first.result(timeout=2).reason == "pressure_observed"
    assert opened[0].raw.closed


@pytest.mark.parametrize("failure_stage", ["factory", "enter"])
def test_failed_open_without_a_close_witness_cannot_multiply_probes(failure_stage):
    opened = []

    class UnconfirmedOpen:
        def __enter__(self):
            opened.append(SimpleNamespace(closed=False))
            raise OSError("private-enter-cleanup-canary")

        def __exit__(self, *args):
            raise AssertionError("failed entry must not invoke exit")

    def connect(selected, *, readonly):
        if failure_stage == "factory":
            opened.append(SimpleNamespace(closed=False))
            raise OSError("private-factory-cleanup-canary")
        return UnconfirmedOpen()

    sampler = api().PostgresPressureSampler(
        target_supplier=target, connect_factory=connect, clock=lambda: 10.0
    )
    first = sampler.sample()
    assert first.observation.available_slots is None
    assert sampler.configure(api().PostgresPressurePolicy(reserve_percent=30))
    for _ in range(3):
        result = sampler.sample()
        assert result.reason == "probe_cleanup_unconfirmed"
        assert result.observation.available_slots is None
    assert len(opened) == 1


def test_supplier_failure_before_open_can_recover_without_uncertain_physical_state():
    resolved = []
    opened = []

    def supplier():
        resolved.append(True)
        if len(resolved) == 1:
            raise OSError("private-supplier-canary")
        return target()

    @contextmanager
    def connect(selected, *, readonly):
        connection = SimpleNamespace(raw=SimpleNamespace(closed=False))
        connection.execute = lambda statement: connection
        connection.fetchone = row
        opened.append(connection)
        try:
            yield connection
        finally:
            connection.raw.closed = True

    sampler = api().PostgresPressureSampler(
        target_supplier=supplier, connect_factory=connect, clock=lambda: 10.0
    )
    assert sampler.sample().reason == "postgres_unavailable"
    assert not opened
    assert sampler.sample().reason == "pressure_observed"
    assert len(opened) == 1 and opened[0].raw.closed
