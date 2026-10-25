"""Cancellation before backend submission owns a returned temporary Git worktree."""

import asyncio
import subprocess
import threading
from pathlib import Path
from test.services.test_terminal_service_inbox_registration import (
    SESSION_NAME,
    TERMINAL_ID,
    create_mocks,
)
from unittest.mock import Mock, patch

import pytest

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.services import provider_lifecycle
from cli_agent_orchestrator.services import terminal_service as terminal
from cli_agent_orchestrator.services import worktree_service as worktrees


@pytest.fixture
def repo(create_mocks, monkeypatch, tmp_path):
    assert db.engine.dialect.name == "postgresql"
    monkeypatch.delenv("CAO_REGISTERED_PROJECTS", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in (
        "GIT_DIR",
        "GIT_COMMON_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(provider_lifecycle, "_startups", {})
    monkeypatch.setattr(provider_lifecycle, "_retirements", {})
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True, capture_output=True)
    (root / "README.md").write_text("temporary fixture\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CAOS test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-q",
            "-m",
            "temporary fixture",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    create_mocks.db_create_terminal.side_effect = db.create_terminal
    # Nested patch exits before create_mocks restores its original Path.
    # monkeypatch teardown would otherwise restore that fixture's MagicMock.
    with patch.object(terminal, "TERMINAL_LOG_DIR", tmp_path / "logs"):
        yield root


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat", [False, True])
@pytest.mark.parametrize("caller", [False, True])
async def test_cancelled_allocation_removes_its_returned_clean_worktree(
    create_mocks, repo, monkeypatch, repeat, caller
):
    entered, release, allocated = threading.Event(), threading.Event(), threading.Event()
    create = worktrees.create_worktree
    remove = Mock(wraps=worktrees.remove_worktree)

    def allocate(*args):
        entered.set()
        assert release.wait(10)
        path = create(*args)
        allocated.set()
        return path

    monkeypatch.setattr(worktrees, "create_worktree", allocate)
    monkeypatch.setattr(worktrees, "remove_worktree", remove)
    task = asyncio.create_task(
        terminal.create_terminal(
            "claude_code",
            "developer",
            session_name=SESSION_NAME,
            use_worktree=True,
            working_directory=str(repo),
            caller_id="parent-fixture" if caller else None,
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 10)
        task.cancel("original cancellation")
        await asyncio.sleep(0)
        if repeat:
            task.cancel("repeat cancellation")
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        assert raised.value.args == ("original cancellation",)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert allocated.is_set()
    assert not Path(worktrees.worktree_path_for(str(repo), TERMINAL_ID)).exists()
    assert (
        worktrees.branch_for(TERMINAL_ID)
        not in git(repo, "branch", "--format=%(refname:short)").splitlines()
    )
    remove.assert_called_once_with(str(repo), TERMINAL_ID)
    create_mocks.backend.create_session.assert_not_called()
    create_mocks.backend.create_window.assert_not_called()
    create_mocks.db_create_terminal.assert_not_called()
    assert db.get_terminal_metadata(TERMINAL_ID) is None
    child = db.get_native_child_for_terminal(TERMINAL_ID)
    if caller:
        assert child["state"] == "planned"
        assert child["parent_terminal_id"] == "parent-fixture"
    else:
        assert child is None


@pytest.mark.asyncio
@pytest.mark.parametrize("preexisting", [False, True])
async def test_cancelled_failed_allocation_never_removes_preexisting_checkout(
    create_mocks, repo, monkeypatch, preexisting
):
    entered, release = threading.Event(), threading.Event()
    if preexisting:
        worktrees.create_worktree(str(repo), TERMINAL_ID)
    remove = Mock(wraps=worktrees.remove_worktree)
    failure = worktrees.WorktreeError("controlled allocation refusal")

    def refuse(*_args):
        entered.set()
        assert release.wait(10)
        raise failure

    monkeypatch.setattr(worktrees, "create_worktree", refuse)
    monkeypatch.setattr(worktrees, "remove_worktree", remove)
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
        task.cancel("original cancellation")
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    remove.assert_not_called()
    assert Path(worktrees.worktree_path_for(str(repo), TERMINAL_ID)).exists() is preexisting
    create_mocks.backend.create_window.assert_not_called()
    create_mocks.db_create_terminal.assert_not_called()


@pytest.mark.asyncio
async def test_repeat_cancel_during_removal_drains_worker_and_keeps_original_cancel(
    create_mocks, repo, monkeypatch
):
    allocating, release_allocation = threading.Event(), threading.Event()
    removing, release_removal = threading.Event(), threading.Event()
    real_create, real_remove = worktrees.create_worktree, worktrees.remove_worktree
    removed = threading.Event()

    def allocate(*args):
        allocating.set()
        assert release_allocation.wait(10)
        return real_create(*args)

    def remove(*args):
        removing.set()
        assert release_removal.wait(10)
        result = real_remove(*args)
        removed.set()
        return result

    monkeypatch.setattr(worktrees, "create_worktree", allocate)
    monkeypatch.setattr(worktrees, "remove_worktree", remove)
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
        assert await asyncio.to_thread(allocating.wait, 10)
        task.cancel("original cancellation")
        release_allocation.set()
        assert await asyncio.to_thread(removing.wait, 10)
        task.cancel("repeat during removal")
        # The event loop still progresses while Git cleanup is held on its worker.
        await asyncio.sleep(0)
        assert not task.done() and not removed.is_set()
        release_removal.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        assert raised.value.args == ("original cancellation",)
    finally:
        release_allocation.set()
        release_removal.set()
        await asyncio.gather(task, return_exceptions=True)
    assert removed.is_set()
    assert not Path(worktrees.worktree_path_for(str(repo), TERMINAL_ID)).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("contents", ["modified", "untracked", "ignored", "committed"])
async def test_cancelled_cleanup_preserves_user_work_and_unmerged_commits(
    create_mocks, repo, monkeypatch, contents
):
    entered, release = threading.Event(), threading.Event()
    real_create = worktrees.create_worktree
    remove = Mock(wraps=worktrees.remove_worktree)
    payload = "preserved user work\n"

    def allocate(*args):
        entered.set()
        assert release.wait(10)
        checkout = Path(real_create(*args))
        name = "README.md" if contents == "modified" else "result.txt"
        if contents == "ignored":
            (repo / ".git/info/exclude").write_text("result.txt\n")
        (checkout / name).write_text(payload)
        if contents == "committed":
            git(checkout, "add", name)
            git(
                checkout,
                "-c",
                "user.name=CAOS test",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "-q",
                "-m",
                "user work",
            )
        return str(checkout)

    monkeypatch.setattr(worktrees, "create_worktree", allocate)
    monkeypatch.setattr(worktrees, "remove_worktree", remove)
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
        task.cancel("original cancellation")
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    remove.assert_called_once_with(str(repo), TERMINAL_ID)
    branch = worktrees.branch_for(TERMINAL_ID)
    assert branch in git(repo, "branch", "--format=%(refname:short)").splitlines()
    checkout = Path(worktrees.worktree_path_for(str(repo), TERMINAL_ID))
    if contents == "committed":
        assert not checkout.exists()
        assert git(repo, "show", f"{branch}:result.txt") == payload
    else:
        assert (
            checkout / ("README.md" if contents == "modified" else "result.txt")
        ).read_text() == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("ambiguous", [False, True])
async def test_cancelled_cleanup_failure_never_masks_cancel_or_invents_authority(
    create_mocks, repo, monkeypatch, ambiguous, caplog
):
    entered, release = threading.Event(), threading.Event()
    real_create = worktrees.create_worktree
    failure = RuntimeError("controlled cleanup unavailable")
    remove = Mock(side_effect=failure)

    def allocate(*args):
        entered.set()
        assert release.wait(10)
        path = real_create(*args)
        if ambiguous:
            raise worktrees.WorktreeError("allocation returned no witness")
        return path

    monkeypatch.setattr(worktrees, "create_worktree", allocate)
    monkeypatch.setattr(worktrees, "remove_worktree", remove)
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
        task.cancel("original cancellation")
        release.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        assert raised.value.args == ("original cancellation",)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert Path(worktrees.worktree_path_for(str(repo), TERMINAL_ID)).exists()
    if ambiguous:
        remove.assert_not_called()
    else:
        remove.assert_called_once_with(str(repo), TERMINAL_ID)
        assert "Cancelled worktree cleanup deferred" in caplog.text


@pytest.mark.asyncio
async def test_cancel_during_provider_initialization_retains_runtime_worktree(
    create_mocks, repo, monkeypatch
):
    entered = asyncio.Event()
    remove = Mock(wraps=worktrees.remove_worktree)
    monkeypatch.setattr(worktrees, "remove_worktree", remove)

    async def initialize():
        entered.set()
        await asyncio.Event().wait()

    create_mocks.provider_manager.create_provider.return_value.initialize.side_effect = initialize
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
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel("provider cancellation")
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    remove.assert_not_called()
    assert Path(worktrees.worktree_path_for(str(repo), TERMINAL_ID)).exists()
    assert db.get_terminal_metadata(TERMINAL_ID) is not None
    assert provider_lifecycle._startups[TERMINAL_ID].uncertain
    create_mocks.backend.kill_window.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_after_backend_submission_retains_worktree_for_existing_compensator(
    create_mocks, repo, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    remove = Mock(wraps=worktrees.remove_worktree)
    monkeypatch.setattr(worktrees, "remove_worktree", remove)
    window = create_mocks.backend.create_window.return_value
    before = set(terminal._create_compensations)

    def create_window(*_args, **_kwargs):
        entered.set()
        assert release.wait(10)
        return window

    create_mocks.backend.create_window.side_effect = create_window
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
        task.cancel("backend cancellation")
        release.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await task
        assert raised.value.args == ("backend cancellation",)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*(terminal._create_compensations - before), return_exceptions=True)
    remove.assert_not_called()
    assert Path(worktrees.worktree_path_for(str(repo), TERMINAL_ID)).exists()
    create_mocks.provider_manager.create_provider.assert_not_called()
    assert db.get_terminal_metadata(TERMINAL_ID) is None
