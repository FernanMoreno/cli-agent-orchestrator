"""Cold deployment selection never grants authority or silently falls back."""

from test.services.test_delivery_admission import observe
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services import inbox_admission_runtime as module
from cli_agent_orchestrator.services.inbox_service import InboxService

pytestmark = pytest.mark.offline_database


@pytest.fixture
def factory(monkeypatch, tmp_path):
    from cli_agent_orchestrator.security import auth

    monkeypatch.setenv("CAO_ADMISSION_POLICY_FILE", str(tmp_path / "private-policy.json"))
    monkeypatch.delenv("CAO_ADMISSION_MODE", raising=False)
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    calls = []
    repository = object()
    bootstrap = SimpleNamespace(shared_repository=lambda: calls.append("repository") or repository)
    return (
        lambda: module.runtime_from_environment(
            InboxService(), bootstrap_factory=lambda: bootstrap
        ),
        calls,
        repository,
    )


@pytest.mark.parametrize("mode", ["", "SHARED", "true", "0", "shared "])
def test_invalid_mode_is_rejected_even_without_policy(factory, monkeypatch, mode):
    create, calls, _ = factory
    monkeypatch.delenv("CAO_ADMISSION_POLICY_FILE")
    monkeypatch.setenv("CAO_ADMISSION_MODE", mode)
    with pytest.raises(ValueError):
        create()
    assert calls == []


def test_shared_mode_requires_policy_before_resolving_repository(factory, monkeypatch):
    create, calls, _ = factory
    monkeypatch.delenv("CAO_ADMISSION_POLICY_FILE")
    monkeypatch.setenv("CAO_ADMISSION_MODE", "shared")
    with pytest.raises(ValueError):
        create()
    assert calls == []


@pytest.mark.parametrize("mode", [None, "local"])
def test_local_mode_preserves_compatibility_without_resolving_repository(
    factory, monkeypatch, mode
):
    create, calls, _ = factory
    if mode is not None:
        monkeypatch.setenv("CAO_ADMISSION_MODE", mode)
    value = create()
    assert calls == [] and not value.monitor.owner.shared_required


def test_shared_mode_requires_confirmation_before_normal_data(factory, monkeypatch):
    create, calls, repository = factory
    monkeypatch.setenv("CAO_ADMISSION_MODE", "shared")
    value = create()
    assert calls == ["repository"]
    assert value._shared_repository is repository
    assert value.monitor.owner.shared_required and not value.monitor.owner.shared_ready
    assert observe(value.monitor.owner, 20)
    assert value.monitor.owner.try_reserve("receiver", now=10).reservation is None


def test_repository_construction_failure_never_returns_local_runtime(factory, monkeypatch):
    create, calls, _ = factory
    monkeypatch.setenv("CAO_ADMISSION_MODE", "shared")

    def broken():
        calls.append("repository")
        raise OSError("private-construction-canary")

    bootstrap = SimpleNamespace(shared_repository=broken)
    with pytest.raises(OSError):
        module.runtime_from_environment(InboxService(), bootstrap_factory=lambda: bootstrap)
    assert calls == ["repository"]
