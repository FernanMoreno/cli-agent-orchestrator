"""Git worktree provisioning for per-terminal isolation (issue #100, Phase 1).

When a supervisor spawns multiple workers via ``handoff``/``assign``, they
share the same git branch and working directory by default -- the exact
"merge conflicts, overwritten files, race conditions" gap issue #100 names.
Passing ``use_worktree=True`` on a spawn gives that one worker an isolated
``git worktree`` checkout on its own branch instead.

Scoped strictly to the maintainer's own suggested Phase 1 (this module +
``use_worktree`` on ``handoff``/``assign``) -- the ``--enable-worktrees``
global launch flag and the ``cao worktrees clean`` CLI command are Phase 2/3,
intentionally not built here to keep this PR reviewable-sized.

Legacy provisioning needs no CAO-side persistence: path and branch are derived
deterministically from the terminal_id CAO already generates for every
terminal (``generate_terminal_id()``, unique and server-controlled, never
user-supplied), so ``create_terminal``/``delete_terminal`` can locate a
worktree at teardown time from the terminal_id alone -- git's own
``.git/worktrees`` bookkeeping is the single source of truth, matching how
this project already treats git as authoritative elsewhere.

Protected evidence archival additionally records immutable artifact references
and audit events in the work repository. Archiving never authorizes deletion.
"""

import difflib
import hashlib
import json
import logging
import os
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from cli_agent_orchestrator.services.step_output_store import ArtifactRef, ImmutableResultStore
from cli_agent_orchestrator.services.work_authority import (
    AuthorityDenied,
    Permissions,
    WorkAuthority,
)
from cli_agent_orchestrator.services.work_reservations import StoppedWriter, WorkReservations

logger = logging.getLogger(__name__)

# Kept out of the repo's own working tree root and namespaced under one
# directory so a single `git worktree list`/`rm -rf` scopes cleanly to
# everything CAO has ever provisioned here.
WORKTREE_SUBDIR = ".cao/worktrees"
BRANCH_PREFIX = "cao/"

# Local-only git operations (add/remove/list); generous but bounded so a
# hung git process cannot hang terminal creation/deletion indefinitely.
_GIT_TIMEOUT_SECONDS = 30

_WORKTREE_PATH_MARKER = f"{os.sep}{WORKTREE_SUBDIR}{os.sep}"


class WorktreeError(Exception):
    """A git-worktree operation failed (repo resolution, add, or list)."""


def _run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    """Run ``git <args>`` in ``cwd``, never raising -- a nonexistent ``cwd``
    (``OSError``/``FileNotFoundError``) or a hung git process
    (``subprocess.TimeoutExpired``) is reported the SAME way a nonzero exit
    code is: a synthetic failed ``CompletedProcess`` with the exception text
    in ``stderr``. Every caller below already branches on ``returncode != 0``
    for a normal git failure -- routing infra failures through that same
    path (instead of letting them escape as a raw, uncaught exception) is
    what makes ``remove_worktree``'s "never raises" contract, and
    ``find_repo_root``/``create_worktree``/``list_worktrees``'s own
    ``WorktreeError`` contract, both actually hold rather than being
    docstring claims that a missing/unreadable directory quietly breaks."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=1, stdout="", stderr=str(e)
        )


def find_repo_root(start_path: str) -> str:
    """The git repository root containing ``start_path``.

    ``git worktree add`` must run from inside a real repo's own working
    tree; ``start_path`` may be any subdirectory of it (a supervisor's own
    working directory is not necessarily the repo root).

    Raises:
        WorktreeError: ``start_path`` is not inside a git repository.
    """
    result = _run_git(["rev-parse", "--show-toplevel"], cwd=start_path)
    if result.returncode != 0:
        raise WorktreeError(
            f"{start_path!r} is not inside a git repository -- use_worktree requires "
            f"a real git repo ('git rev-parse --show-toplevel' failed: {result.stderr.strip()})"
        )
    stdout: str = result.stdout
    return stdout.strip()


def worktree_path_for(repo_root: str, terminal_id: str) -> str:
    return os.path.join(repo_root, WORKTREE_SUBDIR, terminal_id)


def branch_for(terminal_id: str) -> str:
    return f"{BRANCH_PREFIX}{terminal_id}"


def create_worktree(repo_root: str, terminal_id: str) -> str:
    """``git worktree add`` a fresh checkout on its own branch, based on the
    repo's current HEAD. Returns the new worktree's absolute path.

    ``terminal_id`` is server-generated (never user-supplied), so the
    derived path/branch need no additional sanitization beyond what CAO's
    own terminal-id generator already guarantees (a fixed-alphabet,
    fixed-length id -- see ``generate_terminal_id``).

    Raises:
        WorktreeError: ``git worktree add`` failed (e.g. a stale directory
            or branch from an earlier crashed attempt under the same id --
            unreachable in practice since terminal_id is always fresh, but
            surfaced as a clear error rather than a confusing git failure).
    """
    path = worktree_path_for(repo_root, terminal_id)
    branch = branch_for(terminal_id)
    result = _run_git(["worktree", "add", "-b", branch, path], cwd=repo_root)
    if result.returncode != 0:
        raise WorktreeError(
            f"'git worktree add' failed for terminal {terminal_id}: {result.stderr.strip()}"
        )
    _ensure_worktree_subdir_gitignored(repo_root)
    return path


def _ensure_worktree_subdir_gitignored(repo_root: str) -> None:
    """Best-effort: write ``<repo_root>/.cao/worktrees/.gitignore`` (``*``) the
    first time a worktree is created there, so the main checkout's own
    ``git status``/``git add -A`` never sees -- and never stages as an
    embedded-repo gitlink -- the worktrees CAO provisions inside it. The
    target repo is arbitrary user code; we cannot assume its own
    ``.gitignore`` already excludes ``.cao/worktrees`` (this repo's own
    ``.gitignore`` only excludes its unrelated ``.worktrees``), so CAO must
    write its own ignore file rather than rely on one being present.

    Never raises: a failure here (e.g. read-only filesystem) must not fail
    the worktree creation that already succeeded.
    """
    gitignore_path = os.path.join(repo_root, WORKTREE_SUBDIR, ".gitignore")
    try:
        if not os.path.exists(gitignore_path):
            with open(gitignore_path, "w", encoding="utf-8") as f:
                f.write("*\n")
    except OSError as e:
        logger.warning("worktree setup: failed to write %s: %s", gitignore_path, e)


@dataclass(frozen=True)
class CleanupOutcome:
    state: str
    reason: str
    artifact: ArtifactRef | None = None


def remove_worktree(repo_root: str, terminal_id: str) -> CleanupOutcome:
    """Best-effort legacy cleanup; uncertain or dirty work is quarantined in place.

    Includes ignored files because plain Git removal may otherwise discard them.
    No force/reset/clean is permitted. Branch deletion follows successful removal
    only, and uses -d so unmerged commits survive. No writer-stop claim is made.
    """
    path = worktree_path_for(repo_root, terminal_id)
    branch = branch_for(terminal_id)
    status = _run_git(
        ["status", "--porcelain", "-z", "--untracked-files=all", "--ignored"], cwd=path
    )
    if status.returncode or status.stdout:
        logger.warning("worktree cleanup quarantined %s: dirty, ignored or uncertain state", path)
        return CleanupOutcome("quarantined", "dirty_or_uncertain")
    result = _run_git(["worktree", "remove", path], cwd=repo_root)
    if result.returncode != 0:
        logger.warning(
            "worktree cleanup: 'git worktree remove %s' failed: %s",
            path,
            result.stderr.strip(),
        )
        return CleanupOutcome("quarantined", "git_remove_refused")
    result = _run_git(["branch", "-d", branch], cwd=repo_root)
    if result.returncode != 0:
        logger.warning(
            "worktree cleanup: 'git branch -d %s' failed (left in place -- likely has "
            "unmerged commits; merge/push the work, then delete it manually): %s",
            branch,
            result.stderr.strip(),
        )
    return CleanupOutcome("removed", "clean_checkout")


def _archive_text(content: bytes, limit: int) -> str:
    if len(content) > limit or b"\x00" in content:
        raise WorktreeError("binary or oversized evidence requires manual preservation")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorktreeError("non-UTF8 evidence requires manual preservation") from exc


def _archive_current(root: Path, relative: Path, limit: int) -> str | None:
    """Anchored no-follow reads; never dereference a checkout symlink or hardlink."""
    descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in (*root.parts[1:], *relative.parts[:-1]):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        try:
            child = os.open(
                relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
            )
        except FileNotFoundError:
            return None
        with os.fdopen(child, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise WorktreeError("only private regular files may be archived")
            return _archive_text(source.read(limit + 1), limit)
    finally:
        os.close(descriptor)


def _archive_blob(root: Path, revision: str, path: str, limit: int) -> str | None:
    reference = _run_git(["rev-parse", "--verify", f"{revision}:{path}"], str(root))
    if reference.returncode:
        return None
    object_id = reference.stdout.strip()
    size = _run_git(["cat-file", "-s", object_id], str(root))
    if size.returncode or not size.stdout.strip().isdigit() or int(size.stdout) > limit:
        raise WorktreeError("base/index evidence cannot be read within its bound")
    try:
        value = subprocess.run(
            ["git", "cat-file", "blob", object_id],
            cwd=root,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError("cannot read immutable Git evidence") from exc
    if value.returncode:
        raise WorktreeError("expected a Git blob for authorized evidence")
    return _archive_text(value.stdout, limit)


def archive_worktree(
    *,
    repo_root: str,
    terminal_id: str,
    repository,
    artifact_store: ImmutableResultStore,
    principal,
    job_id: str,
    work_item_id: str,
    attempt_id: str,
    generation: int,
    expected_attempt_revision: int,
    grant_id: str,
    expected_grant_revision: int,
    authorized_paths: tuple[str, ...],
    stop_verifier,
    max_file_bytes: int = 256 * 1024,
) -> CleanupOutcome:
    """Preserve explicitly authorized text evidence; ALWAYS quarantine the checkout.

    This is an internal server API, not a client-supplied stop assertion. The
    injected backend verifier must attest irreversible cessation. Actual durable
    grants are checked before reading and again in the publication transaction.
    Secret-name filtering is conservative, not a content-redaction guarantee;
    callers must only authorize files approved for evidence retention. No prompts,
    environment, broad recursive discovery, force removal, reset or clean occur.
    """
    if type(max_file_bytes) is not int or not 0 < max_file_bytes <= 1024 * 1024:
        raise WorktreeError("invalid evidence size bound")
    if type(authorized_paths) is not tuple or not authorized_paths or len(authorized_paths) > 64:
        raise WorktreeError("explicit bounded file allowlist required")
    if not terminal_id or Path(terminal_id).name != terminal_id or terminal_id in {".", ".."}:
        raise WorktreeError("invalid terminal identity")
    root = Path(worktree_path_for(repo_root, terminal_id)).absolute()
    paths = []
    for name in authorized_paths:
        if type(name) is not str or not name or "\x00" in name:
            raise WorktreeError("invalid archive path")
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise WorktreeError("archive path escapes checkout")
        lowered = [part.lower() for part in path.parts]
        if any(
            part.startswith(".env")
            or part in {".git", ".ssh", ".aws", "credentials", "secrets", "id_rsa", "id_ed25519"}
            or part.endswith((".pem", ".key", ".p12"))
            for part in lowered
        ):
            raise WorktreeError("secret or metadata paths require manual preservation")
        # Resolve only to reject aliases; permission bounds remain frozen strings.
        if (root / path).resolve() != root / path:
            raise WorktreeError("symlinked evidence paths are not allowed")
        paths.append(path)
    requested = Permissions(
        paths=frozenset(str(root / path) for path in paths),
        artifacts=frozenset({"worktree_evidence"}),
    )
    authority = WorkAuthority(repository)
    authority._principal(principal)

    def verify(connection):
        repository._verify(connection)
        owner = WorkReservations._owner(
            connection,
            job_id=job_id,
            work_item_id=work_item_id,
            attempt_id=attempt_id,
            generation=generation,
            expected_attempt_revision=expected_attempt_revision,
            active=False,
        )
        chain, job = authority._chain(connection, grant_id, expected_grant_revision)
        attempt = connection.execute(
            "SELECT provider,terminal_id FROM work_attempts WHERE id=?", (attempt_id,)
        ).fetchone()
        if attempt["terminal_id"] != terminal_id:
            raise WorktreeError("archive terminal does not match the durable attempt binding")
        provider = attempt["provider"]
        if (
            job["id"] != job_id
            or chain[0].principal_id != principal.id
            or provider not in job["allowed_providers"]
            or not all(
                provider in grant.providers and requested.is_subset_of(grant.permissions)
                for grant in chain
            )
        ):
            raise AuthorityDenied("worktree evidence exceeds durable grant")
        return owner

    with repository.transaction() as connection:
        owner = verify(connection)
    proof = stop_verifier(owner)
    if not isinstance(proof, StoppedWriter) or (
        proof.attempt_id,
        proof.generation,
        proof.attempt_revision,
    ) != (attempt_id, generation, expected_attempt_revision):
        raise WorktreeError("server-verified irreversible writer cessation required")
    head = _run_git(["rev-parse", "--verify", "HEAD"], str(root))
    if head.returncode:
        raise WorktreeError("checkout base cannot be established")
    base_head = head.stdout.strip()
    files = {}
    try:
        for path in sorted(set(paths)):
            # Read only the exact authorized path; no directory walks or symlink follows.
            with repository.transaction() as connection:
                verify(connection)
            current = _archive_current(root, path, max_file_bytes)
            base = _archive_blob(root, base_head, path.as_posix(), max_file_bytes)
            index = _archive_blob(root, "", path.as_posix(), max_file_bytes)
            files[path.as_posix()] = {
                "base": base,
                "index": index,
                "current": current,
                "diff": "".join(
                    difflib.unified_diff(
                        (base or "").splitlines(True),
                        (current or "").splitlines(True),
                        fromfile="base",
                        tofile="current",
                    )
                ),
            }
    except AuthorityDenied:
        raise
    except OSError as exc:
        raise WorktreeError("evidence file cannot be read safely; checkout retained") from exc
    payload = json.dumps(
        {"version": 1, "base_head": base_head, "files": files},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    def accept(ref) -> CleanupOutcome:
        with repository.transaction() as connection:
            verify(connection)
            identifier = hashlib.sha256(f"{attempt_id}:{ref.content_hash}".encode()).hexdigest()
            prior = connection.execute(
                "SELECT id FROM work_worktree_evidence WHERE id=?", (identifier,)
            ).fetchone()
            if not prior:
                connection.execute(
                    "INSERT INTO work_worktree_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        identifier,
                        job_id,
                        work_item_id,
                        attempt_id,
                        generation,
                        expected_attempt_revision,
                        ref.content_hash,
                        ref.immutable_location,
                        ref.byte_length,
                        base_head,
                        proof.evidence_ref,
                        grant_id,
                        expected_grant_revision,
                        time.time(),
                    ),
                )
                connection.execute(
                    "UPDATE work_items SET revision=revision+1 WHERE id=?", (work_item_id,)
                )
                repository._append_event(
                    connection,
                    job_id=job_id,
                    work_item_id=work_item_id,
                    attempt_id=attempt_id,
                    actor_id=principal.id,
                    event_type="worktree.archived",
                    metadata={"evidence_id": identifier, "content_hash": ref.content_hash},
                )
        return CleanupOutcome("quarantined", "authorized_evidence_archived", ref)

    return artifact_store.publish(payload, accept)


def list_worktrees(repo_root: str) -> list[dict[str, str | bool]]:
    """Parsed ``git worktree list --porcelain`` for ``repo_root`` -- the AC's
    'list' operation. No CAO-side persistence to query: git's own
    bookkeeping is authoritative, so this always reflects reality even if a
    worktree was added/removed outside CAO.

    Raises:
        WorktreeError: ``repo_root`` is not a git repository, or the list
            command otherwise failed.
    """
    result = _run_git(["worktree", "list", "--porcelain"], cwd=repo_root)
    if result.returncode != 0:
        raise WorktreeError(f"'git worktree list' failed: {result.stderr.strip()}")
    worktrees: list[dict[str, str | bool]] = []
    current: dict[str, str | bool] = {}
    for line in result.stdout.splitlines():
        if not line:
            if current:
                worktrees.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value if value else True
    if current:
        worktrees.append(current)
    return worktrees


def parse_worktree_path(path: object) -> tuple[str, str] | None:
    """If ``path`` looks like a CAO-managed worktree, or a subdirectory of
    one (``<repo_root>/.cao/worktrees/<terminal_id>[/...]``), return
    ``(repo_root, terminal_id)``; otherwise ``None``.

    Used at teardown time to recognize a worktree-provisioned terminal from
    its own live pane working directory alone -- no separate CAO-side
    tracking of "which terminals are worktree-backed" is needed, since the
    path shape itself is the marker.

    Two deliberate choices beyond a naive split:

    - Subdirectories of the worktree are accepted, not just the worktree
      root exactly. tmux reports the pane's CURRENT directory
      (``pane_current_path``): a worker that ``cd``s into a subdirectory
      (very likely) would otherwise no longer be recognized as
      worktree-backed at teardown, leaking the worktree/branch.
    - ``rfind`` (last occurrence), not ``find`` (first), so a
      worktree-backed supervisor spawning a worktree-backed worker --
      nesting ``<repo_root>/.cao/worktrees/A/.cao/worktrees/B`` -- resolves
      to B's own ``(repo_root, terminal_id)`` (repo_root = A's worktree
      root, terminal_id = B) instead of failing to parse and leaking B.

    Accepts ``object`` (not just ``str | None``) and returns ``None`` for
    anything that isn't a real string, deliberately: the caller
    (``delete_terminal``) reads this from a backend call whose real contract
    is ``str | None``, but its actual value at any given call site can be
    something else entirely under test doubles/mocks -- this must degrade to
    "not a worktree" rather than raise, since it feeds a real ``git``
    subprocess call two steps downstream.
    """
    if not isinstance(path, str) or not path:
        return None
    idx = path.rfind(_WORKTREE_PATH_MARKER)
    if idx == -1:
        return None
    repo_root = path[:idx]
    remainder = path[idx + len(_WORKTREE_PATH_MARKER) :]
    terminal_id = remainder.split(os.sep, 1)[0]
    if not repo_root or not terminal_id:
        return None
    return repo_root, terminal_id
