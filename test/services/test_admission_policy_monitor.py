"""Persistent operator policy remains bound to the original funded control session."""

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.clients.admission_policy_repository import (
    PolicyNotFound,
    PolicyRevisionConflict,
)
from cli_agent_orchestrator.services.admission_policy import AdmissionPolicy
from cli_agent_orchestrator.services.admission_settings import (
    AdmissionConfiguration,
    PolicyLoadResult,
)

pytestmark = pytest.mark.offline_database


class _PolicySequence:
    persistent = True

    def __init__(self, results, events=None):
        self.results = list(results)
        self.calls = []
        self.events = events

    def load(self, *, scope_id, control_session=None):
        self.calls.append((scope_id, control_session))
        if self.events is not None:
            self.events.append("policy")
        return self.results.pop(0)


def _result(configuration, revision, *, status="current"):
    return PolicyLoadResult(
        configuration=configuration,
        status=status,
        reason="policy_valid" if configuration is not None else "policy_unreadable",
        revision=revision,
    )


def _bind_persistent_monitor(f, loader):
    session = object()
    f.monitor._control_session = session
    f.monitor._storage_control = object()
    f.monitor._check_control_session = lambda: None

    def load_authority():
        return SimpleNamespace(attestation=f.bootstrap.load_current().attestation)

    f.monitor._load_authority = load_authority
    f.monitor._sample_pressure = f.postgres.sample
    f.monitor._policy_loader = loader
    return session


def test_file_loader_keeps_the_legacy_load_call_shape():
    from test.services.test_admission_monitor import fixture

    f = fixture()

    class FileLoader:
        persistent = False

        def __init__(self):
            self.scopes = []

        def load(self, *, scope_id):
            self.scopes.append(scope_id)
            return _result(AdmissionConfiguration(), 1)

    loader = FileLoader()
    f.monitor._policy_loader = loader

    assert f.monitor.refresh().published
    assert loader.scopes == ["test-node/source"]


@pytest.mark.parametrize(
    "second_result",
    [
        _result(AdmissionConfiguration(), 8),
        _result(replace(AdmissionConfiguration(), sample_interval_s=1.5), 7),
    ],
    ids=["durable-revision-changed-only", "configuration-changed-only"],
)
def test_persistent_policy_change_after_sampling_is_not_published(second_result):
    from test.services.test_admission_monitor import fixture

    f = fixture()
    events = []
    configuration = AdmissionConfiguration()
    loader = _PolicySequence([_result(configuration, 7), second_result], events)
    session = _bind_persistent_monitor(f, loader)
    f.postgres.after = lambda: events.append("pressure")
    f.resources.after = lambda: events.append("resources")
    original_authority_load = f.monitor._load_authority

    def load_authority():
        events.append("authority")
        return original_authority_load()

    f.monitor._load_authority = load_authority
    observed = []
    f.owner.observe = lambda *args, **kwargs: observed.append((args, kwargs)) or True

    result = f.monitor.refresh()

    assert result.reason == "policy_unavailable"
    assert not result.published
    assert observed == []
    assert loader.calls == [
        ("test-node/source", session),
        ("test-node/source", session),
    ]
    assert events == ["policy", "authority", "pressure", "resources", "authority", "policy"]
    assert f.monitor.applied_policy_revision is None


def test_persistent_policy_revocation_after_sampling_invalidates_without_publication():
    from test.services.test_admission_monitor import fixture

    f = fixture()
    loader = _PolicySequence(
        [
            _result(AdmissionConfiguration(), 7),
            _result(None, 0, status="unavailable"),
        ]
    )
    _bind_persistent_monitor(f, loader)
    observed = []
    f.owner.observe = lambda *args, **kwargs: observed.append((args, kwargs)) or True

    result = f.monitor.refresh()

    assert result.reason == "policy_unavailable"
    assert not result.published
    assert observed == []


@pytest.mark.parametrize(
    "checked_policy",
    [_result(AdmissionConfiguration(), 8), _result(None, 0, status="unavailable")],
    ids=["changed", "unavailable"],
)
def test_failed_first_persistent_policy_recheck_keeps_bootstrap_unvalidated(checked_policy):
    from test.services.test_admission_monitor import fixture

    f = fixture()
    loader = _PolicySequence([_result(AdmissionConfiguration(), 7), checked_policy])
    _bind_persistent_monitor(f, loader)
    f.monitor._expected_envelope = f.record
    f.monitor._bootstrap_pin_active = True
    f.monitor._bootstrap_validated = False
    observed = []
    f.owner.observe = lambda *args, **kwargs: observed.append((args, kwargs)) or True

    result = f.monitor.refresh()

    assert result.reason == "policy_unavailable"
    assert not result.published
    assert observed == []
    assert f.monitor._bootstrap_pin_active
    assert not f.monitor.bootstrap_validated


def test_persistent_revision_is_applied_only_after_publication_and_is_generation_scoped():
    from test.services.test_admission_monitor import fixture

    f = fixture()
    loader = _PolicySequence(
        [_result(AdmissionConfiguration(), 11), _result(AdmissionConfiguration(), 11)]
    )
    _bind_persistent_monitor(f, loader)

    result = f.monitor.refresh()

    assert result.published
    assert f.monitor.applied_policy_revision == 11
    f.owner.reconfigure(
        AdmissionPolicy(memory_per_delivery_bytes=32 * 1024 * 1024),
        expected_generation=f.owner.generation,
    )
    assert f.monitor.applied_policy_revision is None


def test_persistent_unchanged_revision_and_configuration_publish():
    from test.services.test_admission_monitor import fixture

    f = fixture()
    configuration = AdmissionConfiguration()
    loader = _PolicySequence([_result(configuration, 11), _result(configuration, 11)])
    _bind_persistent_monitor(f, loader)

    result = f.monitor.refresh()

    assert result.published
    assert f.monitor.applied_policy_revision == 11


def test_durable_revision_change_resets_growth_even_when_policy_values_match():
    from test.services.test_admission_monitor import fixture

    f = fixture()
    configuration = AdmissionConfiguration()
    loader = _PolicySequence(
        [
            _result(configuration, 1),
            _result(configuration, 1),
            _result(configuration, 1),
            _result(configuration, 1),
            _result(configuration, 2),
            _result(configuration, 2),
        ]
    )
    _bind_persistent_monitor(f, loader)

    assert f.monitor.refresh().reason == "recovering"
    f.clock[0] += 1
    assert f.monitor.refresh().reason == "recovering"
    f.clock[0] += 1

    result = f.monitor.refresh()

    assert result.reason == "recovering"
    assert result.published
    assert f.owner.snapshot(now=f.clock[0]).available_slots == 0


def test_persistent_unbound_monitor_uses_permit_control_call_with_no_session():
    from test.services.test_admission_monitor import fixture

    f = fixture()
    configuration = AdmissionConfiguration()
    loader = _PolicySequence([_result(configuration, 21), _result(configuration, 21)])
    f.monitor._policy_loader = loader
    entered_control_call = [0]
    original_control_call = f.monitor.control_call
    operations = [0]

    class Permit:
        @contextmanager
        def operation(self, *, clock):
            operations[0] += 1
            yield

    f.monitor._storage_control = Permit()

    def control_call(callback):
        entered_control_call[0] += 1
        return original_control_call(callback)

    f.monitor.control_call = control_call

    result = f.monitor.refresh()

    assert result.published
    assert loader.calls == [
        ("test-node/source", None),
        ("test-node/source", None),
    ]
    assert entered_control_call[0] >= 4
    assert operations[0] >= 4
    assert f.monitor.applied_policy_revision == 21


def test_persistent_revision_is_cleared_when_authority_mutation_begins():
    from test.services.test_admission_monitor import fixture

    f = fixture()
    configuration = AdmissionConfiguration()
    loader = _PolicySequence([_result(configuration, 31), _result(configuration, 31)])
    f.monitor._policy_loader = loader

    @contextmanager
    def operation(*, clock):
        yield

    f.monitor._storage_control = SimpleNamespace(operation=operation)
    assert f.monitor.refresh().published
    assert f.monitor.applied_policy_revision == 31

    token = f.monitor.begin_authority_change()

    assert f.monitor.applied_policy_revision is None
    f.monitor.cancel_authority_change(token)


@pytest.mark.parametrize(
    "known_policy_outcome",
    [PolicyRevisionConflict, PolicyNotFound],
    ids=["cas-conflict", "revoked-policy-missing"],
)
def test_known_policy_cas_outcome_does_not_poison_authority_writer(known_policy_outcome):
    from test.services.test_admission_monitor import fixture

    f = fixture()

    @contextmanager
    def operation(*, clock):
        yield

    f.monitor._storage_control = SimpleNamespace(operation=operation)
    token = f.monitor.begin_authority_change()

    with pytest.raises(known_policy_outcome):
        with f.monitor.authority_change(token):
            raise known_policy_outcome("known-no-commit")

    assert not f.monitor._authority_state.uncertain


@pytest.mark.asyncio
async def test_persistent_revision_is_cleared_on_start_and_stop():
    from test.services.test_admission_monitor import fixture

    f = fixture()
    configuration = AdmissionConfiguration()
    loader = _PolicySequence([_result(configuration, 41), _result(configuration, 41)])
    f.monitor._policy_loader = loader

    @contextmanager
    def operation(**kwargs):
        yield

    f.monitor._storage_control = SimpleNamespace(operation=operation)
    assert f.monitor.refresh().published
    assert f.monitor.applied_policy_revision == 41

    async def idle():
        return None

    f.monitor._run = idle
    await f.monitor.start()
    assert f.monitor.applied_policy_revision is None
    await f.monitor.stop()
    assert f.monitor.applied_policy_revision is None


@pytest.mark.asyncio
@pytest.mark.parametrize("persistent", [False, True], ids=["file", "database"])
async def test_root_startup_uses_separate_bootstrap_loader_for_persistent_policy(
    monkeypatch, persistent
):
    from test.services.test_admission_monitor import fixture

    from cli_agent_orchestrator.services import root_control_activation as activation_module

    f = fixture()
    configuration = AdmissionConfiguration()

    class Loader:
        def __init__(self):
            self.persistent = persistent
            self.calls = []

        def load(self, *, scope_id):
            self.calls.append(("load", scope_id))
            return _result(configuration, 13)

        def load_bootstrap(self, *, scope_id):
            self.calls.append(("load_bootstrap", scope_id))
            return _result(configuration, 0)

    loader = Loader()
    owner = SimpleNamespace(_budget=None)
    startup = object()
    owner._reserve_root_control_startup = lambda proof, **kwargs: SimpleNamespace(
        reservation=startup
    )
    control = object()
    monkeypatch.setattr(
        activation_module,
        "AdmissionPolicyLoader",
        Loader,
    )
    activation = SimpleNamespace(
        certificate=SimpleNamespace(record=f.record),
        envelope=f.record,
        refresh_footprint=SimpleNamespace(memory_bytes=1, cpu_millicores=1, storage_slots=0),
    )

    class Proof:
        def _claim_startup(self):
            return True

    proof = Proof()
    monkeypatch.setattr(
        activation_module,
        "OriginalControlConfig",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        activation_module, "_verify_activation", lambda *a, **k: (activation, proof)
    )
    monkeypatch.setattr(activation_module, "_claim_root_control_activation", lambda: None)
    monkeypatch.setattr(activation_module, "_validate_activation_local_binding", lambda *a: None)
    monkeypatch.setattr(
        activation_module.DeliveryAdmission,
        "with_storage_control",
        classmethod(lambda cls, policy: (owner, control)),
    )
    monkeypatch.setattr(
        activation_module,
        "LinuxResourceSampler",
        lambda **kwargs: SimpleNamespace(sample=lambda **sample_kwargs: object()),
    )
    monkeypatch.setattr(activation_module, "_validated_compute_sample", lambda *a, **k: object())
    monkeypatch.setattr(
        activation_module,
        "_issue_verified_compute_startup_sample",
        lambda proof, **kwargs: object(),
    )
    monkeypatch.setattr(activation_module.asyncio, "sleep", lambda _delay: _async_noop())
    captured = []
    monkeypatch.setattr(
        activation_module,
        "OriginalControlOwner",
        lambda config, *, startup: captured.append((config, startup)) or "actor",
    )

    result = await activation_module.prepare_original_control_owner(
        packet=b"signed",
        trusted_keys={},
        bootstrap=f.bootstrap,
        policy_loader=loader,
        scope_id="root/profile",
        clock=lambda: 10.0,
    )

    assert result == "actor"
    assert loader.calls == [("load_bootstrap" if persistent else "load", "root/profile")]
    assert len(captured) == 1
    assert captured[0][1] is startup
    assert captured[0][0].policy_loader is loader


async def _async_noop():
    return None


@pytest.mark.parametrize("version", [1, 2])
def test_changed_policy_preserves_existing_diagnostic_reason_contract(version):
    from test.services.test_admission_monitor import fixture

    from cli_agent_orchestrator.services.admission_diagnostics import diagnostics_response

    f = fixture()
    loader = _PolicySequence(
        [_result(AdmissionConfiguration(), 7), _result(AdmissionConfiguration(), 8)]
    )
    _bind_persistent_monitor(f, loader)
    assert f.monitor.refresh().reason == "policy_unavailable"
    value = diagnostics_response(
        f.monitor.diagnostic(now=f.clock[0]), now=f.clock[0], version=version
    )
    assert value.monitor.last_cycle_reason == "policy_unavailable"
    assert value.capacity is None and f.monitor.applied_policy_revision is None
