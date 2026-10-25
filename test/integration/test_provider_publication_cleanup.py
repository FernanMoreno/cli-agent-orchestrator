"""PostgreSQL startup metadata must not let restoration replace a live launch."""

import threading
from concurrent.futures import ThreadPoolExecutor

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.providers import manager as module
from cli_agent_orchestrator.providers.codex import CodexProvider


def test_postgres_explicit_launch_owns_its_options_after_concurrent_restore(
    isolated_memory_db,
    monkeypatch,
):
    assert db.engine.dialect.name == "postgresql"
    terminal_id = "publication-race"
    db.create_terminal(terminal_id, "publish-session", "worker", "codex", "reviewer")
    manager = module.ProviderManager()
    entered, release, registered = threading.Event(), threading.Event(), threading.Event()
    original_read = module.get_terminal_metadata
    original_guard = manager._restore_locks_guard

    class Guard:
        def __enter__(self):
            return original_guard.__enter__()

        def __exit__(self, *args):
            result = original_guard.__exit__(*args)
            if threading.current_thread().name == "live-launch":
                registered.set()
            return result

    def read(key):
        value = original_read(key)
        if threading.current_thread().name == "cold-restore":
            entered.set()
            assert release.wait(timeout=10)
        return value

    def restore():
        threading.current_thread().name = "cold-restore"
        return manager.get_provider(terminal_id)

    def create():
        threading.current_thread().name = "live-launch"
        return manager.create_provider(
            "codex",
            terminal_id,
            "publish-session",
            "worker",
            "reviewer",
            skill_prompt="selected launch skills",
            model="selected-launch-model",
        )

    monkeypatch.setattr(module, "get_terminal_metadata", read)
    monkeypatch.setattr(manager, "_restore_locks_guard", Guard())
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(restore)
        try:
            assert entered.wait(timeout=5)
            second = workers.submit(create)
            assert registered.wait(timeout=5)
            assert not second.done()
        finally:
            release.set()
        restored = first.result(timeout=5)
        live = second.result(timeout=5)

    assert isinstance(restored, CodexProvider)
    assert isinstance(live, CodexProvider)
    assert restored is not live
    assert manager.get_provider(terminal_id) is live
    assert live._model == "selected-launch-model"
    assert live._skill_prompt == "selected launch skills"
    assert db.get_terminal_metadata(terminal_id) is not None
    assert db.get_terminal_turn_receipt(terminal_id) is None
    assert manager._restore_locks == {}


def test_postgres_cleanup_reentry_keeps_replacement_and_metadata_for_retry(
    isolated_memory_db,
    monkeypatch,
):
    terminal_id = "cleanup-reentry"
    db.create_terminal(terminal_id, "publish-session", "worker", "codex", "reviewer")
    manager = module.ProviderManager()
    old = manager.get_provider(terminal_id)
    replacement = None

    def cleanup():
        nonlocal replacement
        replacement = manager.create_provider(
            "codex", terminal_id, "publish-session", "worker", model="replacement-model"
        )
        return True

    monkeypatch.setattr(old, "cleanup", cleanup)
    assert manager.cleanup_provider(terminal_id) is False
    assert manager.get_provider(terminal_id) is replacement
    assert replacement._model == "replacement-model"
    assert db.get_terminal_metadata(terminal_id) is not None
    assert manager._restore_locks == {}
