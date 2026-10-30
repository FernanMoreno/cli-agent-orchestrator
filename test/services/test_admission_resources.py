"""Linux samples must honor effective limits and keep unknown data unknown."""

import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline_database
MIB = 1024 * 1024


def api():
    return importlib.import_module("cli_agent_orchestrator.services.admission_resources")


@pytest.fixture
def linux_files(tmp_path):
    proc = tmp_path / "proc"
    group = tmp_path / "cgroup"
    (proc / "self").mkdir(parents=True)
    (group / "worker").mkdir(parents=True)
    (proc / "self/cgroup").write_text("0::/worker\n")
    (proc / "self/mountinfo").write_text(f"20 19 0:25 / {group} rw - cgroup2 cgroup2 rw\n")
    (proc / "meminfo").write_text("MemTotal: 8388608 kB\nMemAvailable: 2097152 kB\n")
    for directory, values in (
        (group, (4096, 700, "max", "200000 100000", 1000000)),
        (group / "worker", (512, 100, 400, "50000 100000", 100000)),
    ):
        maximum, current, high, cpu, usage = values
        (directory / "memory.max").write_text(str(maximum * MIB))
        (directory / "memory.high").write_text(str(high * MIB) if high != "max" else high)
        (directory / "memory.current").write_text(str(current * MIB))
        (directory / "cpu.max").write_text(cpu)
        (directory / "cpu.stat").write_text(f"usage_usec {usage}\n")
    (proc / "stat").write_text("cpu0 10 0 10 80 0 0 0 0 0 0\ncpu1 10 0 10 80 0 0 0 0 0 0\n")
    return proc, group


def advance(proc, group):
    (proc / "stat").write_text("cpu0 20 0 20 160 0 0 0 0 0 0\ncpu1 20 0 20 160 0 0 0 0 0 0\n")
    (group / "cpu.stat").write_text("usage_usec 1300000\n")
    (group / "worker/cpu.stat").write_text("usage_usec 200000\n")


def sampler(linux_files, *, complete=True, host_root=False):
    proc, _ = linux_files
    clock = [10.0]
    value = api().LinuxResourceSampler(
        proc_root=proc,
        affinity=lambda: frozenset({0, 1}),
        clock=lambda: clock[0],
        complete_hierarchy=complete,
        root_is_host=host_root,
    )
    return value, clock


def storage(at=10.0, slots=8):
    return api().StorageObservation(observed_at=at, available_slots=slots)


def warmed(linux_files, **options):
    value, clock = sampler(linux_files, **options)
    first = value.sample(storage=storage())
    advance(*linux_files)
    clock[0] = 11.0
    return value, clock, first, value.sample(storage=storage(11.0))


def test_first_cpu_sample_does_not_invent_idle_capacity(linux_files):
    value, _ = sampler(linux_files)
    result = value.sample(storage=storage())
    assert "cpu_warming" in result.issues
    assert result.observation.coverage == "partial"
    assert result.observation.cpu_available_millicores is None


def test_effective_memory_and_cpu_include_ancestors_and_usage(linux_files):
    _, _, _, result = warmed(linux_files)
    sample = result.observation
    assert sample.coverage == "known"
    assert not result.issues
    assert sample.memory_limit_bytes == 400 * MIB
    assert sample.memory_available_bytes == 300 * MIB
    assert sample.cpu_limit_millicores == 500
    assert sample.cpu_available_millicores == 400
    assert sample.storage_available_slots == 8
    assert sample.observed_at == 11.0


def test_resources_can_grow_without_restarting_the_sampler(linux_files):
    value, clock = sampler(linux_files)
    value.sample(storage=storage())
    proc, group = linux_files
    advance(proc, group)
    (group / "worker/cpu.max").write_text("100000 100000")
    (group / "worker/memory.max").write_text(str(1024 * MIB))
    (group / "worker/memory.high").write_text("max")
    clock[0] = 11.0
    sample = value.sample(storage=storage(11.0)).observation
    assert sample.coverage == "known"
    assert sample.memory_available_bytes == 924 * MIB
    assert sample.memory_limit_bytes == 1024 * MIB
    assert sample.cpu_limit_millicores == 1000
    assert sample.cpu_available_millicores == 900


def test_parent_cpu_pressure_can_close_admission_despite_idle_child(linux_files):
    value, clock = sampler(linux_files)
    value.sample(storage=storage())
    advance(*linux_files)
    (linux_files[1] / "cpu.max").write_text("25000 100000")
    clock[0] = 11.0
    sample = value.sample(storage=storage(11.0)).observation
    assert sample.coverage == "known"
    assert sample.cpu_limit_millicores == 250
    assert sample.cpu_available_millicores == 0


def test_unlimited_cgroup_still_respects_finite_host_and_affinity(linux_files):
    proc, group = linux_files
    for directory in (group, group / "worker"):
        (directory / "memory.max").write_text("max")
        (directory / "memory.high").write_text("max")
        (directory / "cpu.max").write_text("max 100000")
    _, _, _, result = warmed(linux_files)
    sample = result.observation
    assert sample.memory_limit_bytes == 8192 * MIB
    assert sample.memory_available_bytes == 2048 * MIB
    assert sample.cpu_limit_millicores == 2000
    assert sample.cpu_available_millicores == 1600


def test_missing_storage_visibility_preserves_known_local_values(linux_files):
    value, clock = sampler(linux_files)
    value.sample(storage=storage(slots=None))
    advance(*linux_files)
    clock[0] = 11.0
    result = value.sample(storage=storage(11.0, slots=None))
    assert result.observation.coverage == "partial"
    assert result.observation.memory_available_bytes == 300 * MIB
    assert result.observation.cpu_available_millicores == 400
    assert result.observation.storage_available_slots is None
    assert "storage_unknown" in result.issues


def test_combined_sample_retains_age_of_oldest_resource(linux_files):
    value, clock = sampler(linux_files)
    value.sample(storage=storage(0.0))
    advance(*linux_files)
    clock[0] = 11.0
    result = value.sample(storage=storage(0.0))
    assert result.observation.observed_at == 0.0
    policy = importlib.import_module("cli_agent_orchestrator.services.admission_policy")
    decision = policy.evaluate_capacity(policy.AdmissionPolicy(), result.observation, now=11.0)
    assert decision.reason == "stale_observation"
    assert decision.available_slots == 0


def test_future_storage_sample_cannot_hide_behind_local_timestamp(linux_files):
    value, clock = sampler(linux_files)
    value.sample(storage=storage())
    advance(*linux_files)
    clock[0] = 11.0
    result = value.sample(storage=storage(1000.0))
    assert result.observation.coverage == "partial"
    assert result.observation.storage_available_slots is None
    assert "future_storage" in result.issues


def test_unknown_layout_never_falls_back_to_unrestricted_host_resources(linux_files):
    proc, _ = linux_files
    (proc / "self/cgroup").write_text("4:cpu:/worker\n5:memory:/worker\n")
    value, _ = sampler(linux_files)
    result = value.sample(storage=storage())
    assert result.observation.coverage == "unknown"
    assert "unsupported_cgroup" in result.issues


def test_hidden_ancestors_are_explicit_partial_coverage(linux_files):
    proc, group = linux_files
    (proc / "self/cgroup").write_text("0::/parent/worker\n")
    (proc / "self/mountinfo").write_text(f"20 19 0:25 /parent {group} rw - cgroup2 cgroup2 rw\n")
    _, _, _, result = warmed(linux_files)
    assert result.observation.coverage == "partial"
    assert "hidden_ancestors" in result.issues


def test_invalid_path_does_not_escape_its_controller_mount(linux_files):
    proc, _ = linux_files
    (proc / "self/cgroup").write_text("0::/../private-control-canary\n")
    value, _ = sampler(linux_files)
    result = value.sample(storage=storage())
    assert result.observation.coverage == "unknown"
    assert "invalid_cgroup_path" in result.issues
    assert "private-control-canary" not in repr(result)


@pytest.mark.parametrize("bad_value", ["usage_usec -1", "usage_usec nan", "other 10"])
def test_invalid_usage_is_unknown_and_never_logged_raw(linux_files, bad_value):
    (linux_files[1] / "worker/cpu.stat").write_text(bad_value)
    value, _ = sampler(linux_files)
    result = value.sample(storage=storage())
    assert result.observation.coverage == "unknown"
    assert "invalid_resource_data" in result.issues


def test_missing_resource_file_reports_unknown(linux_files):
    (linux_files[1] / "worker/memory.max").unlink()
    value, _ = sampler(linux_files)
    result = value.sample(storage=storage())
    assert result.observation.coverage == "unknown"
    assert "resource_unavailable" in result.issues


def test_counter_reset_requires_another_warm_sample(linux_files):
    value, clock = sampler(linux_files)
    value.sample(storage=storage())
    advance(*linux_files)
    (linux_files[1] / "worker/cpu.stat").write_text("usage_usec 1\n")
    clock[0] = 11.0
    result = value.sample(storage=storage(11.0))
    assert result.observation.coverage == "partial"
    assert "cpu_warming" in result.issues


@pytest.mark.parametrize("slots", [True, -1, 1.5])
def test_storage_quantities_are_strict_units(slots):
    with pytest.raises(ValueError):
        storage(slots=slots)


def test_true_host_root_does_not_require_nonroot_controller_files(linux_files):
    proc, group = linux_files
    for name in ("memory.max", "memory.high", "memory.current", "cpu.max"):
        (group / name).unlink()
    _, _, _, result = warmed(linux_files, host_root=True)
    assert result.observation.coverage == "known"
    assert result.observation.memory_limit_bytes == 400 * MIB
    assert result.observation.cpu_limit_millicores == 500


def test_virtual_namespace_root_is_not_automatically_complete(linux_files):
    proc, _ = linux_files
    (proc / "self/cgroup").write_text("0::/\n")
    _, _, _, result = warmed(linux_files, complete=False)
    assert result.observation.coverage != "known"
    assert "unverified_hierarchy" in result.issues


def test_host_memory_reserve_survives_a_smaller_cgroup_limit(linux_files):
    proc, group = linux_files
    (proc / "meminfo").write_text("MemTotal: 8388608 kB\nMemAvailable: 1048576 kB\n")
    (group / "memory.current").write_text(str(1024 * MIB))
    for name in ("memory.max", "memory.high"):
        (group / "worker" / name).write_text("max")
    _, _, _, result = warmed(linux_files)
    assert result.observation.coverage == "known"
    policy = importlib.import_module("cli_agent_orchestrator.services.admission_policy")
    decision = policy.evaluate_capacity(policy.AdmissionPolicy(), result.observation, now=11.0)
    assert decision.available_slots == 0
    assert decision.limiting_resources == ("memory",)


def test_host_cpu_reserve_survives_a_smaller_cgroup_quota(linux_files):
    value, clock = sampler(linux_files)
    value.sample(storage=storage())
    advance(*linux_files)
    (linux_files[0] / "stat").write_text(
        "cpu0 55 0 55 90 0 0 0 0 0 0\ncpu1 55 0 55 90 0 0 0 0 0 0\n"
    )
    clock[0] = 11.0
    result = value.sample(storage=storage(11.0))
    policy = importlib.import_module("cli_agent_orchestrator.services.admission_policy")
    decision = policy.evaluate_capacity(
        policy.AdmissionPolicy(cpu_per_delivery_millicores=50), result.observation, now=11.0
    )
    assert decision.available_slots == 0
    assert decision.limiting_resources == ("cpu",)


def test_default_sampler_never_attests_its_own_namespace_view(linux_files):
    proc, group = linux_files
    clock = [10.0]
    value = api().LinuxResourceSampler(
        proc_root=proc, affinity=lambda: frozenset({0, 1}), clock=lambda: clock[0]
    )
    value.sample(storage=storage())
    advance(proc, group)
    clock[0] = 11.0
    result = value.sample(storage=storage(11.0))
    assert result.observation.coverage == "partial"
    assert "unverified_hierarchy" in result.issues
    assert result.observation.cpu_domains
    assert len(result.observation.memory_domains) == 3


@pytest.mark.parametrize(
    "options",
    [
        {"complete_hierarchy": "yes"},
        {"root_is_host": 1},
        {"root_is_host": True, "complete_hierarchy": False},
    ],
)
def test_hierarchy_assertions_cannot_be_implicit_truthy_values(options):
    with pytest.raises(ValueError):
        api().LinuxResourceSampler(**options)
