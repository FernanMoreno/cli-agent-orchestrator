"""Logical admission cannot hold a mutex while callers await or observe."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from cli_agent_orchestrator.providers import manager as providers
from cli_agent_orchestrator.services import provider_lifecycle as module

pytestmark = pytest.mark.offline_database


@pytest.fixture(autouse=True)
def registry(monkeypatch):
    monkeypatch.setattr(module, "_startups", {})
    monkeypatch.setattr(module, "_retirements", {})


def test_idle_registry_has_no_historical_keys():
    for index in range(10000):
        owner = module.reserve_startup(str(index))
        module.finish_startup(owner)
        with module.retirement_scope(str(index)) as admitted:
            assert admitted
    assert module._startups == module._retirements == {}


def test_foreign_retirement_refused_and_owner_rollback_reenters():
    owner = module.reserve_startup("a")
    with module.retirement_scope("a") as admitted:
        assert not admitted
    with module.startup_context(owner):
        with module.retirement_scope("a") as admitted:
            assert admitted
            with module.retirement_scope("a") as nested:
                assert nested
    module.finish_startup(owner)
    assert module._startups == module._retirements == {}


def test_retirement_excludes_startup_but_allows_independent_key_and_releases_on_error():
    with pytest.raises(ValueError):
        with module.retirement_scope("a") as admitted:
            assert admitted
            assert module._guard.acquire(blocking=False)
            module._guard.release()
            with pytest.raises(RuntimeError):
                module.reserve_startup("a")
            with ThreadPoolExecutor(max_workers=1) as workers:
                other = workers.submit(module.reserve_startup, "b").result(timeout=2)
            module.finish_startup(other)
            raise ValueError("caller failed")
    assert module._retirements == {}


def test_late_release_cannot_clear_another_owner():
    old = module.reserve_startup("a")
    module.finish_startup(old)
    new = module.reserve_startup("a")
    module.finish_startup(old)
    assert module._startups["a"] is new
    module.finish_startup(new)


def test_uncertain_owner_is_not_proof_of_stopped_workers():
    owner = module.reserve_startup("a")
    module.retain_uncertain_startup(owner)
    with module.startup_context(owner), module.retirement_scope("a") as admitted:
        assert not admitted
    module.finish_startup(owner)
    assert module._startups["a"] is owner


@pytest.mark.parametrize(
    "changed", [None, "provider", "tmux_session", "tmux_window", "session_incarnation_id"]
)
def test_actual_publication_witness_refuses_deleted_or_changed_row(monkeypatch, changed):
    manager = providers.ProviderManager()
    expected = dict(
        provider="mock_cli", tmux_session="s", tmux_window="w", session_incarnation_id="generation"
    )
    current = dict(expected)
    if changed is not None:
        current[changed] = "replacement"
    monkeypatch.setattr(providers, "get_terminal_metadata", lambda _k: current if changed else None)
    with pytest.raises(ValueError, match="registration changed"):
        manager.create_provider("mock_cli", "a", "s", "w", expected_registration=expected)
    assert manager._providers == manager._restore_locks == {}


def test_generic_constructor_keeps_no_row_contract(monkeypatch):
    def forbidden(_key):
        raise AssertionError("generic construction required a DB row")

    monkeypatch.setattr(providers, "get_terminal_metadata", forbidden)
    manager = providers.ProviderManager()
    provider = manager.create_provider("mock_cli", "a", "s", "w")
    assert manager._providers["a"] is provider


def test_entered_copied_retirement_context_keeps_owner_until_last_exit():
    import contextvars

    entered, release = threading.Event(), threading.Event()

    def nested():
        with module.retirement_scope("a") as admitted:
            assert admitted
            entered.set()
            assert release.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=1) as workers:
        try:
            with module.retirement_scope("a"):
                copied = contextvars.copy_context()
                future = workers.submit(copied.run, nested)
                assert entered.wait(timeout=2)
            with pytest.raises(RuntimeError):
                module.reserve_startup("a")
        finally:
            release.set()
        future.result(timeout=2)
    assert module._retirements == {}


def test_only_owner_of_last_retirement_can_transfer_to_replacement_startup():
    with module.retirement_scope("a") as admitted:
        assert admitted
        with module.retirement_scope("a"):
            with pytest.raises(RuntimeError):
                module.reserve_startup("a", replace_retired=True)
        owner = module.reserve_startup("a", replace_retired=True)
        with module.retirement_scope("a") as foreign:
            assert not foreign
    assert module._retirements == {}
    assert module._startups["a"] is owner
    module.finish_startup(owner)
