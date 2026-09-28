"""issue #100 Phase 1 -- worktree_service tests.

Covers:
- ``find_repo_root``: resolves from a subdirectory, raises outside a repo.
- ``create_worktree`` / ``remove_worktree``: real ``git worktree add``/
  ``remove``/branch-delete against a real local repo -- no subprocess mocking,
  same posture as ``test_project_identity.py``'s own real-git tests.
- ``remove_worktree`` quarantines modified, untracked and ignored content.
- ``list_worktrees`` reflects real ``git worktree`` state, including entries
  ``create_worktree`` did not itself create.
- ``parse_worktree_path`` round-trips against paths ``worktree_path_for``
  produces, and rejects unrelated paths.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

import pytest

from cli_agent_orchestrator.services.worktree_service import (
    WorktreeError,
    branch_for,
    create_worktree,
    find_repo_root,
    list_worktrees,
    parse_worktree_path,
    remove_worktree,
    worktree_path_for,
)


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True, timeout=2)
        return True
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(not _git_available(), reason="git executable required")


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "initial"], cwd=path, check=True, capture_output=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    _init_repo(repo_path)
    return repo_path


class TestFindRepoRoot:
    def test_resolves_from_repo_root_itself(self, repo: Path) -> None:
        assert find_repo_root(str(repo)) == str(repo.resolve())

    def test_resolves_from_a_subdirectory(self, repo: Path) -> None:
        subdir = repo / "src" / "pkg"
        subdir.mkdir(parents=True)
        assert find_repo_root(str(subdir)) == str(repo.resolve())

    def test_raises_outside_any_git_repository(self, tmp_path: Path) -> None:
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        with pytest.raises(WorktreeError, match="is not inside a git repository"):
            find_repo_root(str(non_repo))

    def test_raises_worktree_error_not_a_raw_os_error_for_a_nonexistent_path(
        self, tmp_path: Path
    ) -> None:
        """Regression: a nonexistent start_path (e.g. a typo'd
        working_directory) must surface as the same clean WorktreeError as
        'exists but isn't a repo' -- not an uncaught FileNotFoundError from
        subprocess.run's own cwd resolution, which would reach the API
        boundary as an unhandled 500 instead of the intended 400."""
        nonexistent = tmp_path / "does" / "not" / "exist"
        with pytest.raises(WorktreeError):
            find_repo_root(str(nonexistent))


class TestCreateAndRemoveWorktree:
    def test_create_worktree_produces_a_real_checkout_on_its_own_branch(self, repo: Path) -> None:
        terminal_id = "term_abc123"
        path = create_worktree(str(repo), terminal_id)

        assert Path(path) == Path(worktree_path_for(str(repo), terminal_id))
        assert (Path(path) / "README.md").is_file()  # real checkout of HEAD's tree

        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=path,
            capture_output=True,
            text=True,
            check=True,
        )
        assert branch_result.stdout.strip() == branch_for(terminal_id)

        # git itself agrees this is a real worktree of `repo`.
        list_result = subprocess.run(
            ["git", "worktree", "list"], cwd=repo, capture_output=True, text=True, check=True
        )
        assert path in list_result.stdout

    def test_create_worktree_raises_a_clear_error_outside_a_git_repository(
        self, tmp_path: Path
    ) -> None:
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        with pytest.raises(WorktreeError):
            create_worktree(str(non_repo), "term_xyz")

    def test_create_worktree_gitignores_the_worktrees_subdir(self, repo: Path) -> None:
        # Without this, every `git status` in the main checkout shows the
        # provisioned worktree as an untracked/embedded-repo gitlink, and
        # `git add -A` there (agents do this constantly) stages it.
        create_worktree(str(repo), "term_first")

        gitignore = repo / ".cao" / "worktrees" / ".gitignore"
        assert gitignore.is_file()
        assert gitignore.read_text() == "*\n"

        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
        )
        assert ".cao" not in status.stdout

    def test_create_worktree_does_not_clobber_an_existing_gitignore(self, repo: Path) -> None:
        create_worktree(str(repo), "term_first")
        gitignore = repo / ".cao" / "worktrees" / ".gitignore"
        gitignore.write_text("custom\n")

        create_worktree(str(repo), "term_second")

        assert gitignore.read_text() == "custom\n"

    def test_remove_worktree_deletes_the_directory_and_the_branch(self, repo: Path) -> None:
        terminal_id = "term_clean01"
        path = create_worktree(str(repo), terminal_id)

        remove_worktree(str(repo), terminal_id)

        assert not Path(path).exists()
        branch_result = subprocess.run(
            ["git", "branch", "--list", branch_for(terminal_id)],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        assert branch_result.stdout.strip() == ""

    def test_remove_worktree_retains_a_branch_with_unmerged_commits(self, repo: Path) -> None:
        """A worker that committed real work to its branch before completing
        must not have that history destroyed just because Phase 1 has no
        merge-back story -- this clean checkout can be removed, but the
        branch itself is only safe-deleted (``git branch
        -d``), which refuses when there are commits that would be lost."""
        terminal_id = "term_committed01"
        path = create_worktree(str(repo), terminal_id)
        (Path(path) / "result.txt").write_text("important output\n")
        subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "worker output"],
            cwd=path,
            check=True,
            capture_output=True,
        )

        remove_worktree(str(repo), terminal_id)  # must not raise

        assert not Path(path).exists()  # worktree checkout itself is gone
        branch_result = subprocess.run(
            ["git", "branch", "--list", branch_for(terminal_id)],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        # Branch survives -- a leak for Phase 3 to sweep, not data loss.
        assert branch_for(terminal_id) in branch_result.stdout

    def test_remove_worktree_quarantines_uncommitted_and_untracked_content(
        self, repo: Path
    ) -> None:
        """Agents commonly leave modified/untracked files behind -- a plain
        (non-force) ``git worktree remove`` refuses in that case; this must
        not surface as a failure to the caller (teardown paths call this
        best-effort and must never raise)."""
        terminal_id = "term_dirty01"
        path = create_worktree(str(repo), terminal_id)
        (Path(path) / "scratch.txt").write_text("uncommitted work\n")
        (Path(path) / "README.md").write_text("modified\n")

        remove_worktree(str(repo), terminal_id)  # must not raise

        assert (Path(path) / "scratch.txt").read_text() == "uncommitted work\n"
        assert (Path(path) / "README.md").read_text() == "modified\n"

    def test_ignored_files_are_quarantined(self, repo: Path) -> None:
        path = Path(create_worktree(str(repo), "term_ignored"))
        (path / ".gitignore").write_text("secret.txt\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-qm", "ignore"], cwd=path, check=True, capture_output=True
        )
        (path / "secret.txt").write_text("private")
        outcome = remove_worktree(str(repo), "term_ignored")
        assert (path / "secret.txt").read_text() == "private"
        assert outcome.state == "quarantined"

    def test_remove_worktree_on_an_already_removed_worktree_does_not_raise(
        self, repo: Path
    ) -> None:
        terminal_id = "term_gone01"
        create_worktree(str(repo), terminal_id)
        remove_worktree(str(repo), terminal_id)

        remove_worktree(str(repo), terminal_id)  # second call: must not raise

    def test_remove_worktree_on_a_nonexistent_repo_root_does_not_raise(self) -> None:
        """Regression: this function's own docstring promises 'never raises'
        (both terminal_service.delete_terminal's teardown path and
        create_terminal's own failure-cleanup path call it with no
        try/except, relying on that contract). A repo_root that no longer
        exists on disk (e.g. the parent clone was itself deleted between
        worktree creation and teardown) previously raised an uncaught
        FileNotFoundError from subprocess.run's own cwd resolution."""
        remove_worktree("/definitely/not/a/real/repo/root/anywhere", "term_x")  # must not raise


class TestListWorktrees:
    def test_lists_the_main_checkout_and_every_created_worktree(self, repo: Path) -> None:
        create_worktree(str(repo), "term_one")
        create_worktree(str(repo), "term_two")

        entries = list_worktrees(str(repo))

        paths = {e["worktree"] for e in entries if "worktree" in e}
        assert str(repo.resolve()) in paths
        assert worktree_path_for(str(repo), "term_one") in paths
        assert worktree_path_for(str(repo), "term_two") in paths

    def test_raises_outside_a_git_repository(self, tmp_path: Path) -> None:
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        with pytest.raises(WorktreeError):
            list_worktrees(str(non_repo))


class TestParseWorktreePath:
    def test_round_trips_against_worktree_path_for(self) -> None:
        repo_root = "/home/user/myrepo"
        terminal_id = "term_9f8e7d"
        path = worktree_path_for(repo_root, terminal_id)

        parsed = parse_worktree_path(path)

        assert parsed == (repo_root, terminal_id)

    def test_returns_none_for_a_path_outside_any_worktree_subdir(self) -> None:
        assert parse_worktree_path("/home/user/myrepo/src/pkg") is None

    def test_returns_none_for_none(self) -> None:
        assert parse_worktree_path(None) is None

    def test_returns_none_for_the_worktrees_dir_itself_with_no_terminal_segment(self) -> None:
        assert parse_worktree_path("/home/user/myrepo/.cao/worktrees/") is None

    def test_accepts_a_subdirectory_of_the_worktree(self) -> None:
        # tmux reports the pane's CURRENT directory (pane_current_path); a
        # worker that `cd`s into a subdirectory of its own worktree (very
        # likely) must still be recognized as worktree-backed at teardown,
        # or the worktree/branch leaks silently.
        assert parse_worktree_path("/home/user/myrepo/.cao/worktrees/term_x/extra") == (
            "/home/user/myrepo",
            "term_x",
        )
        assert parse_worktree_path("/home/user/myrepo/.cao/worktrees/term_x/deeply/nested/dir") == (
            "/home/user/myrepo",
            "term_x",
        )

    def test_resolves_the_innermost_worktree_under_nesting(self) -> None:
        # A worktree-backed supervisor (terminal A) spawning a worktree-backed
        # worker (terminal B) nests B's worktree under A's:
        # <repo_root>/.cao/worktrees/A/.cao/worktrees/B. `rfind` (last
        # marker occurrence) must resolve to B's own (repo_root, terminal_id)
        # -- repo_root being A's worktree root -- not fail to parse (`find`,
        # first occurrence, would yield terminal_id "A/.cao/worktrees/B",
        # rejected for containing a path separator, leaking B).
        nested = "/home/user/myrepo/.cao/worktrees/term_a/.cao/worktrees/term_b"
        assert parse_worktree_path(nested) == (
            "/home/user/myrepo/.cao/worktrees/term_a",
            "term_b",
        )


def archive_context(repo, tmp_path, monkeypatch):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.security.auth import local_operator_principal
    from cli_agent_orchestrator.services import worktree_service as service
    from cli_agent_orchestrator.services.step_output_store import ImmutableResultStore
    from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
    from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
    from cli_agent_orchestrator.services.work_reservations import StoppedWriter

    assert hasattr(service, "archive_worktree"), "protected archival missing"
    monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
    monkeypatch.delenv("CAO_AUTH_JWKS_URI", raising=False)
    actor = local_operator_principal()
    db = WorkRepository(tmp_path / "work.sqlite3")
    db.initialize()
    path = Path(create_worktree(str(repo), "archived"))
    job = db.create_job(
        project_id="project",
        principal_id=actor.id,
        allowed_providers=["mock_cli"],
        grant_id="grant",
    )
    WorkAuthority(db).issue_root(
        actor,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(paths={str(path)}, artifacts={"worktree_evidence"}),
        expires_at=time.time() + 300,
    )
    work = db.admit_work(
        job_id=job["id"],
        operation_kind="test",
        idempotency_key="one",
        request_hash=hashlib.sha256(b"one").hexdigest(),
        contract_id="contract",
        snapshot_id=None,
        provider="mock_cli",
        actor_id=actor.id,
    )
    attempt = work["attempts"][-1]
    # Trusted fixture arrangement; production binding belongs to dispatch T017.
    with db.transaction() as connection:
        connection.execute(
            "UPDATE work_attempts SET terminal_id='archived' WHERE id=?", (attempt["id"],)
        )
    db.transition_attempt(
        attempt_id=attempt["id"],
        generation=1,
        expected_revision=1,
        expected_state="planned",
        target="cancelled",
        actor_id=actor.id,
        event_id="cancel",
        evidence=TransitionEvidence(generation=1, expected_generation=1),
    )
    arguments = dict(
        repo_root=str(repo),
        terminal_id="archived",
        repository=db,
        artifact_store=ImmutableResultStore(tmp_path / "artifacts"),
        principal=actor,
        job_id=job["id"],
        work_item_id=work["id"],
        attempt_id=attempt["id"],
        generation=1,
        expected_attempt_revision=2,
        grant_id="grant",
        expected_grant_revision=1,
        authorized_paths=("README.md", "scratch.txt"),
        stop_verifier=lambda owner: StoppedWriter(
            owner.attempt_id, owner.generation, owner.revision, "backend-stopped"
        ),
    )
    return service, db, path, arguments


def test_archive_preserves_authorized_diff_base_and_untracked_durably(repo, tmp_path, monkeypatch):
    service, db, path, args = archive_context(repo, tmp_path, monkeypatch)
    (path / "README.md").write_text("changed\n")
    (path / "scratch.txt").write_text("new evidence\n")
    outcome = service.archive_worktree(**args)
    assert outcome.state == "quarantined"
    assert path.exists()
    data = json.loads(args["artifact_store"].read(outcome.artifact))
    assert data["files"]["README.md"]["base"] == "hello\n"
    assert "+changed" in data["files"]["README.md"]["diff"]
    assert data["files"]["scratch.txt"]["current"] == "new evidence\n"
    assert outcome.artifact.content_hash in db.referenced_artifact_hashes()
    with db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_worktree_evidence").fetchone()[0] == 1


@pytest.mark.parametrize("kind", ["binary", "symlink", "secret", "unapproved"])
def test_archive_rejects_unsafe_files_without_deleting_checkout(repo, tmp_path, monkeypatch, kind):
    service, db, path, args = archive_context(repo, tmp_path, monkeypatch)
    if kind == "binary":
        (path / "scratch.txt").write_bytes(b"secret\x00binary")
    elif kind == "symlink":
        (path / "scratch.txt").symlink_to(tmp_path / "outside")
    elif kind == "secret":
        (path / ".env").write_text("PASSWORD=secret")
        args["authorized_paths"] = (".env",)
    else:
        args["authorized_paths"] = ("../outside",)
    with pytest.raises(service.WorktreeError):
        service.archive_worktree(**args)
    assert path.exists()
    with db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_worktree_evidence").fetchone()[0] == 0


def test_archive_rejects_live_writer_without_copying(repo, tmp_path, monkeypatch):
    service, db, path, args = archive_context(repo, tmp_path, monkeypatch)
    args["stop_verifier"] = lambda owner: None
    with pytest.raises(service.WorktreeError):
        service.archive_worktree(**args)
    assert path.exists()


@pytest.mark.parametrize("terminal_binding", [None, "different-terminal"])
def test_archive_requires_exact_durable_terminal_binding(
    repo, tmp_path, monkeypatch, terminal_binding
):
    service, db, path, args = archive_context(repo, tmp_path, monkeypatch)
    with db.transaction() as connection:
        connection.execute(
            "UPDATE work_attempts SET terminal_id=? WHERE id=?",
            (terminal_binding, args["attempt_id"]),
        )
    with pytest.raises(service.WorktreeError, match="terminal"):
        service.archive_worktree(**args)
    assert path.exists()
    with db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_worktree_evidence").fetchone()[0] == 0


def test_revocation_during_stop_check_prevents_file_read(repo, tmp_path, monkeypatch):
    from cli_agent_orchestrator.services.work_authority import AuthorityDenied, WorkAuthority

    service, db, path, args = archive_context(repo, tmp_path, monkeypatch)
    original = args["stop_verifier"]

    def revoke(owner):
        WorkAuthority(db).revoke(
            args["principal"], grant_id="grant", expected_grant_revision=1, reason="stop archive"
        )
        return original(owner)

    def forbidden_read(*unused):
        pytest.fail("file read after grant revoked")

    args["stop_verifier"] = revoke
    monkeypatch.setattr(service, "_archive_current", forbidden_read)
    with pytest.raises(AuthorityDenied):
        service.archive_worktree(**args)
    assert path.exists()


def test_archive_event_failure_leaves_no_reference_and_preserves_files(repo, tmp_path, monkeypatch):
    import sqlite3

    service, db, path, args = archive_context(repo, tmp_path, monkeypatch)
    (path / "scratch.txt").write_text("retained")
    with db.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER fail_archive BEFORE INSERT ON work_events WHEN NEW.event_type='worktree.archived' BEGIN SELECT RAISE(ABORT,'no archive event'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="no archive event"):
        service.archive_worktree(**args)
    assert (path / "scratch.txt").read_text() == "retained"
    with db.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_worktree_evidence").fetchone()[0] == 0


def test_archive_never_reads_unapproved_untracked_content(repo, tmp_path, monkeypatch):
    service, db, path, args = archive_context(repo, tmp_path, monkeypatch)
    (path / "unauthorized.txt").write_bytes(b"DO NOT COPY\x00")
    outcome = service.archive_worktree(**args)
    content = args["artifact_store"].read(outcome.artifact)
    assert b"DO NOT COPY" not in content
    assert (path / "unauthorized.txt").exists()
