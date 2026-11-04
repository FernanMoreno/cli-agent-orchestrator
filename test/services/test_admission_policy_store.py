"""Persistent selection cannot inherit bootstrap-file or stale database authority."""

from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services.admission_settings import AdmissionPolicyLoader

pytestmark = pytest.mark.offline_database


class Repository:
    def __init__(self):
        self.current = None
        self.calls = []
        self.error = None

    def resolve_target(self):
        return self

    def read_latest(self, target, *, control_session=None):
        self.calls.append(control_session)
        if self.error:
            raise self.error
        return self.current


def record(revision=1, active=True):
    return SimpleNamespace(
        revision=revision, active=active, policy=SimpleNamespace(model_dump=lambda: {})
    )


def test_database_missing_does_not_use_valid_bootstrap_file():
    repo = Repository()
    loader = AdmissionPolicyLoader(source=lambda: {"version": 1, "policy": {}}, repository=repo)
    assert loader.persistent
    assert loader.load_bootstrap(scope_id="a").configuration is not None
    assert loader.load(scope_id="a").configuration is None


def test_database_revision_does_not_follow_file_or_value_counter():
    repo = Repository()
    repo.current = record(revision=11)
    loader = AdmissionPolicyLoader(repository=repo)
    session = object()
    first = loader.load(scope_id="a", control_session=session)
    assert first.revision == 11
    assert first.configuration is not None
    assert repo.calls == [session]
    repo.current = record(revision=12)
    assert loader.load(scope_id="a").revision == 12


def test_revocation_and_failure_invalidate_last_valid_database_configuration():
    repo = Repository()
    repo.current = record()
    loader = AdmissionPolicyLoader(repository=repo)
    assert loader.load(scope_id="a").configuration is not None
    repo.current = record(revision=2, active=False)
    assert loader.load(scope_id="a").configuration is None
    repo.current = record(revision=3)
    assert loader.load(scope_id="a").configuration is not None
    repo.error = OSError("private-secret-canary")
    failed = loader.load(scope_id="a")
    assert failed.configuration is None
    assert failed.status == "unavailable"
    assert "canary" not in repr(failed)


def test_database_bootstrap_has_its_own_file_revision():
    repo = Repository()
    repo.current = record(revision=57)
    loader = AdmissionPolicyLoader(source=lambda: {"version": 1, "policy": {}}, repository=repo)
    assert loader.load(scope_id="a").revision == 57
    assert loader.load_bootstrap(scope_id="a").revision == 1
    assert loader.load(scope_id="a").revision == 57


def test_missing_bootstrap_never_reads_database():
    repo = Repository()
    loader = AdmissionPolicyLoader(repository=repo)
    assert loader.load_bootstrap(scope_id="a").configuration is None
    assert repo.calls == []


@pytest.mark.parametrize("selector", ["", "sqlite", "automatic", "Postgres"])
def test_unknown_selector_cannot_enable_another_source(monkeypatch, selector):
    from cli_agent_orchestrator.services.admission_settings import configured_policy_loader

    monkeypatch.setenv("CAO_ADMISSION_POLICY_STORE", selector)
    with pytest.raises(ValueError):
        configured_policy_loader(None)


def test_file_selector_requires_explicit_source(monkeypatch):
    from cli_agent_orchestrator.services.admission_settings import configured_policy_loader

    monkeypatch.delenv("CAO_ADMISSION_POLICY_STORE", raising=False)
    with pytest.raises(ValueError):
        configured_policy_loader(None)


def test_bound_session_authority_failure_is_not_swallowed():
    from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable

    repo = Repository()
    repo.error = StorageAdmissionUnavailable()
    loader = AdmissionPolicyLoader(repository=repo)
    with pytest.raises(StorageAdmissionUnavailable):
        loader.load(scope_id="a", control_session=object())
    assert loader.load(scope_id="a").configuration is None


def test_postgres_selector_resolves_trusted_profile_without_sql(monkeypatch):
    from cli_agent_orchestrator.clients import admission_policy_repository
    from cli_agent_orchestrator.services import local_peer_identity
    from cli_agent_orchestrator.services.admission_settings import configured_policy_loader

    repository = Repository()
    profiles = []
    monkeypatch.setenv("CAO_ADMISSION_POLICY_STORE", "postgres")
    monkeypatch.setattr(
        local_peer_identity,
        "get_persistent_instance_id",
        lambda: "0957cf8e-e246-4a64-95e0-262038f973c9",
    )

    def create(*, profile_id):
        profiles.append(profile_id)
        return repository

    monkeypatch.setattr(admission_policy_repository, "AdmissionPolicyRepository", create)
    loader = configured_policy_loader(None)
    assert loader.repository is repository
    assert profiles == ["0957cf8e-e246-4a64-95e0-262038f973c9"]
    assert repository.calls == []
    assert loader.load(scope_id="transient-participant").configuration is None


def test_postgres_runtime_without_file_starts_control_unknown(monkeypatch):
    from test.services.test_admission_monitor import fixture

    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services import inbox_admission_runtime as runtime_module
    from cli_agent_orchestrator.services.inbox_service import InboxService

    f = fixture()
    repository = Repository()
    loader = AdmissionPolicyLoader(repository=repository)
    monkeypatch.setenv("CAO_ADMISSION_POLICY_STORE", "postgres")
    monkeypatch.delenv("CAO_ADMISSION_POLICY_FILE", raising=False)
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(runtime_module, "_PROCESS_PRESSURE", f.postgres)
    monkeypatch.setattr(runtime_module, "configured_policy_loader", lambda path: loader)
    value = runtime_module.runtime_from_environment(
        InboxService(), bootstrap_factory=lambda: f.bootstrap
    )
    assert value is not None and value._policy_repository is repository
    assert value.monitor.refresh().reason == "policy_unavailable"
    assert value.monitor.owner.snapshot(now=value.clock()).available_slots == 0
