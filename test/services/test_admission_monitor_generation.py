"""A failed cycle fences its own reconfiguration, never a newer owner update."""

from test.services.test_admission_monitor import fixture

import pytest

from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy, CapacityObservation

pytestmark = pytest.mark.offline_database
MIB = 1024 * 1024


def test_cycle_failure_invalidates_its_reconfigured_generation():
    f = fixture()
    original = f.owner.generation

    def fail():
        raise RuntimeError("controlled cycle failure")

    f.postgres.after = fail
    with pytest.raises(RuntimeError, match="controlled cycle failure"):
        f.monitor.refresh()
    assert f.owner.generation == original + 2  # Own configure, then own invalidation.
    assert f.owner.diagnostic(now=f.clock[0]).observation is None
    assert f.owner.snapshot(now=f.clock[0]).limit == 0


def test_cycle_failure_preserves_a_later_external_generation():
    f = fixture()
    original = f.owner.generation
    installed = []

    def supersede_and_fail():
        newer = f.owner.reconfigure(
            AdmissionPolicy(memory_per_delivery_bytes=32 * MIB),
            expected_generation=f.owner.generation,
        )
        assert newer == original + 2
        observation = CapacityObservation(f.clock[0], 4096 * MIB, 4096 * MIB, 16000, 16000, 12)
        assert f.owner.observe(observation, now=f.clock[0], generation=newer)
        installed.append(observation)
        raise RuntimeError("controlled newer generation")

    f.postgres.after = supersede_and_fail
    with pytest.raises(RuntimeError, match="controlled newer generation"):
        f.monitor.refresh()
    assert f.owner.generation == original + 2
    assert f.owner.diagnostic(now=f.clock[0]).observation == installed[0]
    assert f.owner.snapshot(now=f.clock[0]).limit > 0
