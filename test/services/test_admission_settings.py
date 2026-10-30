"""Operator policy reload is atomic, scoped and independent of ordinary config."""

import importlib
import json
import os
import threading
from dataclasses import FrozenInstanceError

import pytest

pytestmark = pytest.mark.offline_database


def api():
    return importlib.import_module("cli_agent_orchestrator.services.admission_settings")


def document(**values):
    return {"version": 1, "policy": values}


def test_explicit_versioned_policy_has_typed_consumption_settings():
    result = api().parse_operator_policy(
        document(memory_per_delivery_bytes=128 * 1024 * 1024, cpu_per_delivery_millicores=500)
    )
    assert result.policy.memory_per_delivery_bytes == 128 * 1024 * 1024
    assert result.policy.cpu_per_delivery_millicores == 500
    assert result.postgres.reserve_percent == 20
    assert result.sample_interval_s == 1.0
    assert result.healthy_samples_before_growth == 3


@pytest.mark.parametrize(
    "candidate",
    [
        {},
        {"version": True, "policy": {}},
        {"version": 2, "policy": {}},
        {"version": 1, "policy": []},
        {"version": 1, "policy": {}, "complete_hierarchy": True},
        document(root_is_host=True),
        document(complete_hierarchy=True),
        document(max_workers=8),
        document(cpu_per_delivery_millicores=True),
        document(cpu_per_delivery_millicores=1.5),
        document(memory_per_delivery_bytes=0),
        document(memory_reserve_percent=100),
        document(postgres_reserve_percent=-1),
        document(postgres_statement_timeout_ms=False),
        document(max_sample_age_s=float("nan")),
        document(sample_interval_s=float("inf")),
        document(sample_interval_s=0),
        document(max_sample_age_s=1, sample_interval_s=1),
        document(healthy_samples_before_growth=False),
        document(healthy_samples_before_growth=0),
    ],
)
def test_invalid_or_authority_bearing_documents_do_not_become_configuration(candidate):
    with pytest.raises(ValueError):
        api().parse_operator_policy(candidate)


def test_invalid_reload_retains_the_whole_last_valid_policy():
    current = [document(cpu_per_delivery_millicores=500)]
    loader = api().AdmissionPolicyLoader(source=lambda: current[0])
    first = loader.load(scope_id="node-epoch-a")
    assert first.status == "current"
    assert first.configuration.policy.cpu_per_delivery_millicores == 500
    current[0] = document(cpu_per_delivery_millicores=1000, memory_per_delivery_bytes=0)
    failed = loader.load(scope_id="node-epoch-a")
    assert failed.status == "retained"
    assert failed.reason == "policy_invalid"
    assert failed.configuration == first.configuration
    assert failed.revision == first.revision
    current[0] = document(cpu_per_delivery_millicores=1000)
    recovered = loader.load(scope_id="node-epoch-a")
    assert recovered.status == "current"
    assert recovered.configuration.policy.cpu_per_delivery_millicores == 1000
    assert recovered.revision == first.revision + 1


def test_same_values_do_not_create_a_new_policy_revision():
    loader = api().AdmissionPolicyLoader(source=lambda: document())
    first = loader.load(scope_id="node-epoch-a")
    second = loader.load(scope_id="node-epoch-a")
    assert second.revision == first.revision
    assert second.configuration == first.configuration


def test_invalid_boot_has_no_previous_policy_or_magic_fallback():
    loader = api().AdmissionPolicyLoader(source=lambda: document(memory_per_delivery_bytes=0))
    result = loader.load(scope_id="node-epoch-a")
    assert result.status == "unavailable"
    assert result.reason == "policy_invalid"
    assert result.configuration is None


def test_new_source_scope_cannot_inherit_another_nodes_last_policy():
    current = [document(cpu_per_delivery_millicores=500)]
    loader = api().AdmissionPolicyLoader(source=lambda: current[0])
    assert loader.load(scope_id="node-epoch-a").configuration is not None
    current[0] = document(memory_per_delivery_bytes=0)
    result = loader.load(scope_id="node-epoch-b")
    assert result.status == "unavailable"
    assert result.configuration is None


def test_unreadable_reload_is_redacted_and_retains_valid_configuration():
    unreadable = [False]

    def source():
        if unreadable[0]:
            raise OSError("private-policy-path-and-content-canary")
        return document()

    loader = api().AdmissionPolicyLoader(source=source)
    first = loader.load(scope_id="node-a")
    unreadable[0] = True
    result = loader.load(scope_id="node-a")
    assert result.status == "retained"
    assert result.reason == "policy_unreadable"
    assert result.configuration == first.configuration
    assert "private" not in repr(result)


def test_source_mutation_cannot_change_an_accepted_snapshot():
    raw = document(cpu_per_delivery_millicores=500)
    loader = api().AdmissionPolicyLoader(source=lambda: raw)
    accepted = loader.load(scope_id="node-a")
    raw["policy"]["cpu_per_delivery_millicores"] = 1
    assert accepted.configuration.policy.cpu_per_delivery_millicores == 500
    with pytest.raises(FrozenInstanceError):
        accepted.configuration.sample_interval_s = 0


def test_private_operator_file_is_parsed_without_changing_permissions(tmp_path):
    path = tmp_path / "operator-policy.json"
    path.write_text(json.dumps(document(cpu_per_delivery_millicores=500)))
    path.chmod(0o600)
    raw = api().read_private_operator_policy(path)
    result = api().parse_operator_policy(raw)
    assert result.policy.cpu_per_delivery_millicores == 500
    assert path.stat().st_mode & 0o777 == 0o600


def test_operator_policy_never_follows_a_symlink(tmp_path):
    original = tmp_path / "original.json"
    original.write_text(json.dumps(document()))
    original.chmod(0o600)
    alias = tmp_path / "alias.json"
    alias.symlink_to(original)
    with pytest.raises(OSError):
        api().read_private_operator_policy(alias)


def test_group_or_world_writable_policy_is_not_operator_authority(tmp_path):
    path = tmp_path / "operator-policy.json"
    path.write_text(json.dumps(document()))
    path.chmod(0o644)
    with pytest.raises(PermissionError):
        api().read_private_operator_policy(path)


def test_oversized_policy_is_rejected_before_loading_unbounded_input(tmp_path):
    path = tmp_path / "operator-policy.json"
    path.write_bytes(b" " * (64 * 1024 + 1))
    path.chmod(0o600)
    with pytest.raises(ValueError):
        api().read_private_operator_policy(path)


def test_fifo_policy_is_rejected_without_waiting_for_a_writer(tmp_path):
    path = tmp_path / "operator-policy.fifo"
    os.mkfifo(path, 0o600)
    finished = threading.Event()
    failures = []

    def read_policy():
        try:
            api().read_private_operator_policy(path)
        except Exception as failure:
            failures.append(failure)
        finally:
            finished.set()

    reader = threading.Thread(target=read_policy)
    reader.start()
    rejected_without_writer = finished.wait(timeout=1)
    # Release a regressed blocking open so a failing test cannot leave a thread.
    release_descriptor = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    try:
        reader.join(timeout=2)
    finally:
        os.close(release_descriptor)
    assert not reader.is_alive()
    assert rejected_without_writer, "policy reader waited for a FIFO writer"
    assert len(failures) == 1 and isinstance(failures[0], PermissionError)


@pytest.mark.parametrize(
    "invalid_payload",
    [
        '{"version":1,"policy":{"cpu_per_delivery_millicores":500,'
        '"cpu_per_delivery_millicores":1}}',
        '{"version":1,"policy":{"unknown":' + "[" * 20000 + "0" + "]" * 20000 + "}}",
    ],
    ids=["duplicate-policy-field", "excessive-nesting"],
)
def test_invalid_json_reload_retains_the_whole_previous_policy(tmp_path, invalid_payload):
    path = tmp_path / "operator-policy.json"
    path.write_text(json.dumps(document(cpu_per_delivery_millicores=500)))
    path.chmod(0o600)
    loader = api().AdmissionPolicyLoader(source=lambda: api().read_private_operator_policy(path))
    first = loader.load(scope_id="node-a")
    path.write_text(invalid_payload)
    result = loader.load(scope_id="node-a")
    assert result.status == "retained"
    assert result.reason == "policy_invalid"
    assert result.configuration == first.configuration
    assert result.revision == first.revision


@pytest.mark.parametrize("scope", ["", "  ", None, 1])
def test_scope_identity_is_required_before_source_io(scope):
    calls = []
    loader = api().AdmissionPolicyLoader(source=lambda: calls.append(True))
    with pytest.raises(ValueError):
        loader.load(scope_id=scope)
    assert not calls
