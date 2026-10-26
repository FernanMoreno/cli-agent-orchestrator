"""A cancelled create keeps its row until native absence is positively established."""

import asyncio
import threading
from pathlib import Path
from test.integration.test_cancelled_worktree_creation import repo
from test.services.test_terminal_service_inbox_registration import (
    SESSION_NAME,
    TERMINAL_ID,
    create_mocks,
)
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult
from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.services import provider_lifecycle
from cli_agent_orchestrator.services import terminal_service as terminal
from cli_agent_orchestrator.services import worktree_service as worktrees


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [False, True])
async def test_cancelled_old_attempt_never_retires_changed_incarnation(
    create_mocks, repo, monkeypatch, replacement
):
    entered, release = threading.Event(), threading.Event()
    backend = create_mocks.backend
    window = backend.create_window.return_value
    original = terminal._finish_and_roll_back_cancelled_create
    before = set(terminal._create_compensations)
    preserved = []

    def create_window(*_args, **_kwargs):
        entered.set()
        assert release.wait(10)
        return window

    def compensate(outcome, *args, **kwargs):
        outcome.result()  # Actual worker has committed its own row/binding.
        with db.SessionLocal() as session:
            pointer = session.get(db.SessionIncarnationModel, SESSION_NAME)
            pointer.incarnation_id = "replacement-incarnation"
            if replacement:
                row = session.get(db.TerminalModel, TERMINAL_ID)
                row.session_incarnation_id = "replacement-incarnation"
                row.tmux_window = "replacement-window"
            session.commit()
        preserved.append(db.get_terminal_metadata(TERMINAL_ID))
        return original(outcome, *args, **kwargs)

    backend.create_window.side_effect = create_window
    backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.ABSENT
    )
    monkeypatch.setattr(terminal, "_finish_and_roll_back_cancelled_create", compensate)
    task = asyncio.create_task(
        terminal.create_terminal(
            "claude_code",
            "developer",
            session_name=SESSION_NAME,
            use_worktree=True,
            working_directory=str(repo),
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel("original backend cancellation")
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*(terminal._create_compensations - before), return_exceptions=True)
    assert len(preserved) == 1
    assert db.get_terminal_metadata(TERMINAL_ID) == preserved[0]
    backend.kill_window.assert_not_called()
    backend.cleanup_terminal_exact.assert_not_called()
    assert Path(worktrees.worktree_path_for(str(repo), TERMINAL_ID)).is_dir()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["false", "exception", "absent", "absent_false"])
@pytest.mark.parametrize("unknown", [False, True])
@pytest.mark.parametrize("new_session", [False, True])
async def test_cancelled_backend_requires_exact_stop_before_row_retirement(
    create_mocks, repo, stop, unknown, new_session
):
    assert db.engine.dialect.name == "postgresql"
    entered, release = threading.Event(), threading.Event()
    backend = create_mocks.backend
    backend.session_exists.return_value = not new_session
    window = (
        create_mocks.generate_window_name.return_value
        if new_session
        else backend.create_window.return_value
    )
    live = {"window": False}
    before = set(terminal._create_compensations)

    def create_window(*_args, **_kwargs):
        live["window"] = True
        entered.set()
        assert release.wait(10)
        return window

    def kill(*_args):
        if stop == "exception":
            raise RuntimeError("controlled native stop failure")
        if stop in {"absent", "absent_false"}:
            live["window"] = False
        return stop == "absent"

    def probe(*_args, **_kwargs):
        return TerminalCleanupResult(
            TerminalCleanupOutcome.ABSENT
            if not live["window"]
            else TerminalCleanupOutcome.UNKNOWN if unknown else TerminalCleanupOutcome.STILL_PRESENT
        )

    if new_session:
        backend.create_session.side_effect = create_window
        backend.kill_session.side_effect = kill
    else:
        backend.create_window.side_effect = create_window
        backend.kill_window.side_effect = kill
    backend.cleanup_terminal_exact.side_effect = probe
    task = asyncio.create_task(
        terminal.create_terminal(
            "claude_code",
            "developer",
            session_name=SESSION_NAME,
            use_worktree=True,
            working_directory=str(repo),
            new_session=new_session,
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel("original backend cancellation")
        release.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        assert raised.value.args == ("original backend cancellation",)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*(terminal._create_compensations - before), return_exceptions=True)
    if stop in {"false", "exception"}:
        assert live["window"] is True
        assert db.get_terminal_metadata(TERMINAL_ID) is not None
    else:
        assert live["window"] is False
        assert db.get_terminal_metadata(TERMINAL_ID) is None
    assert Path(worktrees.worktree_path_for(str(repo), TERMINAL_ID)).is_dir()
    create_mocks.provider_manager.create_provider.assert_not_called()
    backend.cleanup_terminal_exact.assert_called_once_with(
        TERMINAL_ID, SESSION_NAME, window, close=False
    )


async def _cancel_with_probe(create_mocks, repo, probe, *, defer_init=False, expect_probe=True):
    entered, release = threading.Event(), threading.Event()
    backend = create_mocks.backend
    window = backend.create_window.return_value
    before = set(terminal._create_compensations)

    def create_window(*_args, **_kwargs):
        entered.set()
        assert release.wait(10)
        return window

    backend.create_window.side_effect = create_window
    backend.kill_window.return_value = True
    backend.cleanup_terminal_exact.side_effect = probe
    task = asyncio.create_task(
        terminal.create_terminal(
            "claude_code",
            "developer",
            session_name=SESSION_NAME,
            use_worktree=True,
            working_directory=str(repo),
            defer_init=defer_init,
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel("original backend cancellation")
        release.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        assert raised.value.args == ("original backend cancellation",)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*(terminal._create_compensations - before), return_exceptions=True)
    assert Path(worktrees.worktree_path_for(str(repo), TERMINAL_ID)).is_dir()
    create_mocks.provider_manager.create_provider.assert_not_called()
    if expect_probe:
        backend.cleanup_terminal_exact.assert_called_once_with(
            TERMINAL_ID, SESSION_NAME, window, close=False
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", ["unknown", "present", "exception"])
async def test_true_label_kill_is_not_exact_absence(create_mocks, repo, proof):
    def probe(*_args, **_kwargs):
        if proof == "exception":
            raise RuntimeError("controlled exact observation unavailable")
        return TerminalCleanupResult(
            TerminalCleanupOutcome.UNKNOWN
            if proof == "unknown"
            else TerminalCleanupOutcome.STILL_PRESENT
        )

    await _cancel_with_probe(create_mocks, repo, probe)
    assert db.get_terminal_metadata(TERMINAL_ID) is not None
    create_mocks.backend.kill_window.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["false", "exception"])
async def test_cancelled_row_retirement_refusal_keeps_evidence_and_cancel(
    create_mocks, repo, monkeypatch, failure
):
    delete = (
        Mock(return_value=False)
        if failure == "false"
        else Mock(side_effect=RuntimeError("controlled row retirement unavailable"))
    )
    monkeypatch.setattr(terminal, "delete_terminal_row", delete)
    await _cancel_with_probe(
        create_mocks, repo, lambda *_a, **_k: TerminalCleanupResult(TerminalCleanupOutcome.ABSENT)
    )
    delete.assert_called_once()
    assert db.get_terminal_metadata(TERMINAL_ID) is not None


@pytest.mark.asyncio
async def test_binding_change_during_native_probe_preserves_replacement_row(create_mocks, repo):
    replacement = []

    def probe(*_args, **_kwargs):
        with db.SessionLocal() as session:
            row = session.get(db.TerminalModel, TERMINAL_ID)
            row.tmux_window = "replacement-window"
            session.commit()
        replacement.append(db.get_terminal_metadata(TERMINAL_ID))
        return TerminalCleanupResult(TerminalCleanupOutcome.ABSENT)

    await _cancel_with_probe(create_mocks, repo, probe)
    assert len(replacement) == 1
    assert db.get_terminal_metadata(TERMINAL_ID) == replacement[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("authority", ["missing_binding", "work_view", "work_owned"])
async def test_cancelled_creation_retains_row_without_cleanup_authority(
    create_mocks, repo, monkeypatch, authority
):
    from cli_agent_orchestrator.backends.work_backend import WorkBackendView

    original = terminal._finish_and_roll_back_cancelled_create
    rollback = terminal._roll_back_cancelled_create
    results, releases, views = [], [], []
    finish = Mock(wraps=terminal.finish_startup)
    monkeypatch.setattr(terminal, "finish_startup", finish)
    monkeypatch.setattr(terminal, "_initial_task_pending_ids", set())
    monkeypatch.setattr(terminal, "_active_deferred_init_external_owner_ids", set())

    def record_retirement(*args, **kwargs):
        result = rollback(*args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(terminal, "_roll_back_cancelled_create", record_retirement)

    def compensate(outcome, *args, **kwargs):
        outcome.result()
        release = Mock(wraps=kwargs["release_startup_marks"])
        releases.append(release)
        kwargs["release_startup_marks"] = release
        if authority == "missing_binding":
            kwargs["expected_binding_getter"] = None
        elif authority == "work_view":
            view = WorkBackendView(
                create_mocks.backend,
                None,
                lambda *_a: None,
                terminal_id=TERMINAL_ID,
                expected_target=(SESSION_NAME, create_mocks.backend.create_window.return_value),
            )
            # Successful spies make the explicit view refusal observable:
            # removing that guard would otherwise retire this real row.
            view.kill_window = Mock(return_value=True)
            view.cleanup_terminal_exact = Mock(
                return_value=TerminalCleanupResult(TerminalCleanupOutcome.ABSENT)
            )
            views.append(view)
            create_mocks.get_backend.return_value = view
        else:
            monkeypatch.setattr(
                terminal,
                "ensure_terminal_is_not_work_owned",
                Mock(side_effect=terminal.WorkOwnedTerminalError("controlled Work owner")),
            )
        return original(outcome, *args, **kwargs)

    monkeypatch.setattr(terminal, "_finish_and_roll_back_cancelled_create", compensate)
    await _cancel_with_probe(
        create_mocks,
        repo,
        lambda *_a, **_k: TerminalCleanupResult(TerminalCleanupOutcome.ABSENT),
        defer_init=True,
        expect_probe=False,
    )
    assert results == [False]
    assert len(releases) == 1
    releases[0].assert_called_once_with()
    finish.assert_called_once()
    assert finish.call_args.args[0] is not None
    assert not provider_lifecycle._startups
    assert terminal._initial_task_pending_ids == set()
    assert terminal._active_deferred_init_external_owner_ids == set()
    assert db.get_terminal_metadata(TERMINAL_ID) is not None
    assert db.get_terminal_metadata(TERMINAL_ID)["deferred_init_failure"] is None
    create_mocks.backend.kill_window.assert_not_called()
    create_mocks.backend.cleanup_terminal_exact.assert_not_called()
    for view in views:
        view.kill_window.assert_not_called()
        view.cleanup_terminal_exact.assert_not_called()
