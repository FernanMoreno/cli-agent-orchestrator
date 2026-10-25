"""Provider operations must not overwrite or remove another operation's owner."""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.providers import manager as module
from cli_agent_orchestrator.services import remote_terminal_service

pytestmark = pytest.mark.offline_database


@pytest.fixture(autouse=True)
def local_provider(monkeypatch):
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _key: None)
    monkeypatch.setattr(module, "registered_provider_descriptor", lambda _key: None)


def metadata(_key):
    return dict(provider="mock_cli", tmux_session="s", tmux_window="w", agent_profile=None)


def publication_boundary(manager, monkeypatch, contender):
    """Signal either registered lease admission or the old unguarded publish."""
    reached = threading.Event()
    guard = manager._restore_locks_guard

    class Guard:
        def __enter__(self):
            return guard.__enter__()

        def __exit__(self, *args):
            result = guard.__exit__(*args)
            if threading.current_thread().name == contender:
                reached.set()
            return result

    monkeypatch.setattr(manager, "_restore_locks_guard", Guard())
    return reached


def test_restore_cannot_overwrite_explicit_creation(monkeypatch):
    manager = module.ProviderManager()
    entered, release = threading.Event(), threading.Event()
    published = threading.Event()
    boundary = publication_boundary(manager, monkeypatch, "explicit-create")
    restored = SimpleNamespace(requires_turn_receipt=False, cleanup=Mock())
    live = SimpleNamespace(requires_turn_receipt=False)

    def read(key):
        entered.set()
        assert release.wait(timeout=5)
        return metadata(key)

    def build(*_args, **_kwargs):
        return live if threading.current_thread().name == "explicit-create" else restored

    def create():
        threading.current_thread().name = "explicit-create"
        result = manager.create_provider("mock_cli", "same", "s", "w")
        published.set()
        boundary.set()
        return result

    monkeypatch.setattr(module, "get_terminal_metadata", read)
    monkeypatch.setattr(module, "MockCliProvider", build)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(manager.get_provider, "same")
        try:
            assert entered.wait(timeout=5)
            second = workers.submit(create)
            assert boundary.wait(timeout=5)
            assert not published.is_set(), "explicit publication bypassed an ongoing restore"
        finally:
            release.set()
        assert first.result(timeout=5) is restored
        assert second.result(timeout=5) is live
    assert manager.get_provider("same") is live
    restored.cleanup.assert_not_called()
    assert manager._restore_locks == {}


@pytest.mark.parametrize("cached", [True, False])
def test_cleanup_cannot_pop_newly_published_provider(monkeypatch, cached):
    manager = module.ProviderManager()
    entered, release = threading.Event(), threading.Event()
    published = threading.Event()
    boundary = publication_boundary(manager, monkeypatch, "explicit-create")
    new = SimpleNamespace(cleanup=Mock())

    def clean():
        entered.set()
        assert release.wait(timeout=5)
        return True

    old = SimpleNamespace(cleanup=Mock(side_effect=clean))
    if cached:
        manager._providers["same"] = old
    else:
        monkeypatch.setattr(
            module, "get_terminal_metadata", lambda _key: dict(metadata(_key), provider="grok_cli")
        )
        monkeypatch.setattr(module, "GrokCliProvider", lambda *_args, **_kwargs: old)

    def create():
        threading.current_thread().name = "explicit-create"
        result = manager.create_provider("mock_cli", "same", "s", "w")
        published.set()
        boundary.set()
        return result

    monkeypatch.setattr(module, "MockCliProvider", lambda *_args, **_kwargs: new)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(manager.cleanup_provider, "same")
        try:
            assert entered.wait(timeout=5)
            second = workers.submit(create)
            assert boundary.wait(timeout=5)
            assert not published.is_set(), "new publication overlapped old private cleanup"
        finally:
            release.set()
        assert first.result(timeout=5) is True
        assert second.result(timeout=5) is new
    assert manager._providers["same"] is new
    old.cleanup.assert_called_once()
    new.cleanup.assert_not_called()
    assert manager._restore_locks == {}


@pytest.mark.parametrize("cached", [True, False])
def test_reentrant_replacement_is_retained_and_cleanup_is_incomplete(monkeypatch, cached):
    manager = module.ProviderManager()
    new = SimpleNamespace(cleanup=Mock(return_value=True))
    monkeypatch.setattr(module, "MockCliProvider", lambda *_args, **_kwargs: new)

    def cleanup():
        assert manager.create_provider("mock_cli", "same", "s", "w") is new
        return True

    old = SimpleNamespace(cleanup=Mock(side_effect=cleanup))
    if cached:
        manager._providers["same"] = old
    else:
        monkeypatch.setattr(
            module, "get_terminal_metadata", lambda _key: dict(metadata(_key), provider="grok_cli")
        )
        monkeypatch.setattr(module, "GrokCliProvider", lambda *_args, **_kwargs: old)
    assert manager.cleanup_provider("same") is False
    assert manager._providers["same"] is new
    new.cleanup.assert_not_called()
    assert manager.cleanup_provider("same") is True
    assert manager._providers == {}
    assert manager._restore_locks == {}


def test_listing_uses_snapshot_and_does_not_block_other_publication(monkeypatch):
    manager = module.ProviderManager()
    entered, release = threading.Event(), threading.Event()

    class PausedProvider:
        def __getattribute__(self, name):
            if name == "__class__":
                entered.set()
                assert release.wait(timeout=5)
            return object.__getattribute__(self, name)

    manager._providers["old"] = PausedProvider()
    new = SimpleNamespace()
    monkeypatch.setattr(module, "MockCliProvider", lambda *_args, **_kwargs: new)
    with ThreadPoolExecutor(max_workers=1) as workers:
        future = workers.submit(manager.list_providers)
        try:
            assert entered.wait(timeout=5)
            assert manager.create_provider("mock_cli", "new", "s", "w") is new
        finally:
            release.set()
        assert future.result(timeout=5) == {"old": "PausedProvider"}
    assert manager._providers["new"] is new


def test_cleanup_does_not_hold_the_map_guard_during_provider_io(monkeypatch):
    manager = module.ProviderManager()
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()

    def cleanup():
        entered.set()
        assert release.wait(timeout=5)
        return True

    manager._providers["a"] = SimpleNamespace(cleanup=cleanup)
    b = SimpleNamespace(requires_turn_receipt=False)
    manager._providers["b"] = b

    def get_b():
        result = manager.get_provider("b")
        returned.set()
        return result

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(manager.cleanup_provider, "a")
        try:
            assert entered.wait(timeout=5)
            second = workers.submit(get_b)
            assert returned.wait(timeout=1), "A's cleanup held B's registry read"
            assert second.result(timeout=5) is b
        finally:
            release.set()
        assert first.result(timeout=5) is True
    assert manager._restore_locks == {}


def test_explicit_create_retains_launch_options_without_cleaning_restored_adapter(monkeypatch):
    manager = module.ProviderManager()
    old = SimpleNamespace(cleanup=Mock())
    manager._providers["same"] = old
    new = SimpleNamespace()
    build = Mock(return_value=new)
    monkeypatch.setattr(module, "CodexProvider", build)
    assert (
        manager.create_provider(
            "codex",
            "same",
            "s",
            "w",
            "reviewer",
            ["@cao-mcp-server"],
            skill_prompt="private skills",
            model="selected-model",
        )
        is new
    )
    build.assert_called_once_with(
        "same",
        "s",
        "w",
        "reviewer",
        ["@cao-mcp-server"],
        skill_prompt="private skills",
        model="selected-model",
    )
    old.cleanup.assert_not_called()
    assert manager._providers["same"] is new
    assert manager._restore_locks == {}


def test_cleanup_waits_for_inflight_restore_and_removes_that_owner(monkeypatch):
    manager = module.ProviderManager()
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    boundary = publication_boundary(manager, monkeypatch, "cleanup")
    restored = SimpleNamespace(requires_turn_receipt=False, cleanup=Mock(return_value=True))

    def read(key):
        if threading.current_thread().name == "restore":
            entered.set()
            assert release.wait(timeout=5)
        return metadata(key)

    def restore():
        threading.current_thread().name = "restore"
        return manager.get_provider("same")

    def cleanup():
        threading.current_thread().name = "cleanup"
        result = manager.cleanup_provider("same")
        completed.set()
        boundary.set()
        return result

    monkeypatch.setattr(module, "get_terminal_metadata", read)
    monkeypatch.setattr(module, "MockCliProvider", lambda *_args, **_kwargs: restored)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(restore)
        try:
            assert entered.wait(timeout=5)
            second = workers.submit(cleanup)
            assert boundary.wait(timeout=5)
            assert not completed.is_set(), "cleanup bypassed the in-flight reconstruction"
        finally:
            release.set()
        assert first.result(timeout=5) is restored
        assert second.result(timeout=5) is True
    restored.cleanup.assert_called_once()
    assert manager._providers == {}
    assert manager._restore_locks == {}
