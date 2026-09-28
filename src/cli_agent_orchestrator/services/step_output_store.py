"""In-memory structured-return store (issue #312, Bolt 2 / N4, ADR-4).

A process-local, capped, oldest-first-evicting collection keyed by
``(run_id, step_id)`` that holds the worker-emitted output of one workflow step
plus its schema-validation verdict. It decouples worker-emit timing from
engine-consume timing; the engine (N5, Bolt 3) reads it when sequencing the next
step. The N6 run journal swaps in behind this same interface with no contract
change.

The store is **transient** and **best-effort**: a process restart loses it (the
explicit pre-N6 gap, ADR-8), and cap eviction honors the non-blocking promise —
it NEVER raises. ``record_step_output`` validates the output against an
``output_schema`` (passed WITH the request for the synthetic-key MVP, since there
is no run record in Bolt 2 — F2) using the SAME ``jsonschema`` Draft 2020-12
validator family Bolt 1 used to check schema well-formedness, so authoring-time
and runtime checks agree (B2-BR-7). A schema failure is RECORDED (validated=False,
state COMPLETED_UNVALIDATED), never raised.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import stat
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, TypeVar

import jsonschema  # type: ignore[import-untyped]  # stable Draft 2020-12 API, matches N1

from cli_agent_orchestrator.constants import (
    WORKFLOW_NAME_RE,
    WORKFLOW_OUTPUT_STORE_MAX_ENTRIES,
)
from cli_agent_orchestrator.models.workflow import StepOutputRecord, StepState

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(WORKFLOW_NAME_RE)


def _validate_key_part(value: str, label: str) -> str:
    """Validate a ``run_id`` / ``step_id`` against the anchored name regex (B2-BR-1).

    Rejects traversal tokens and any value not matching ``WORKFLOW_NAME_RE``.
    Raises ``ValueError`` -> HTTPException 400 at the boundary.
    """
    if value in (".", ".."):
        raise ValueError(f"{label} '{value}' is not allowed (traversal token)")
    if not _NAME_RE.match(value):
        raise ValueError(f"{label} '{value}' is invalid (must match {WORKFLOW_NAME_RE})")
    return value


class StepOutputStore:
    """A capped, insertion-ordered ``(run_id, step_id)`` -> ``StepOutputRecord`` map.

    Oldest-first soft eviction at ``WORKFLOW_OUTPUT_STORE_MAX_ENTRIES`` (Q1=A): a
    plain ``OrderedDict`` with ``popitem(last=False)``, no LRU dependency.
    Last-write-wins on the same key (a reprompt overwrites in place rather than
    growing the store), and re-inserting an existing key refreshes its position
    so it is not the eviction victim.
    """

    def __init__(self, max_entries: int = WORKFLOW_OUTPUT_STORE_MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._store: "OrderedDict[Tuple[str, str], StepOutputRecord]" = OrderedDict()

    def put(self, run_id: str, step_id: str, record: StepOutputRecord) -> None:
        """Store a record, evicting the oldest entry first if over the cap.

        Eviction is best-effort and NEVER raises (the store is transient): any
        unexpected error during eviction is logged and swallowed so a worker's
        structured return is never blocked by store bookkeeping.
        """
        key = (run_id, step_id)
        # Last-write-wins: drop any existing entry so the re-insert lands at the
        # newest position (and the count is correct for the cap check).
        if key in self._store:
            del self._store[key]
        self._store[key] = record
        try:
            while len(self._store) > self._max_entries:
                evicted_key, _ = self._store.popitem(last=False)
                logger.debug("StepOutputStore: evicted oldest entry %s (cap reached)", evicted_key)
        except Exception as e:  # noqa: BLE001 — transient store; eviction must never block a put
            logger.warning("StepOutputStore: eviction skipped after error: %s", e)

    def get(self, run_id: str, step_id: str) -> Optional[StepOutputRecord]:
        """Return the record for ``(run_id, step_id)``, or None if absent."""
        return self._store.get((run_id, step_id))

    def delete(self, run_id: str, step_id: str) -> None:
        """Drop the record for ``(run_id, step_id)``; a no-op when absent.

        A caller that re-runs a step under a REUSED key (the single-step replay,
        issue #640) must clear the slot first, or a "this step emitted nothing"
        read silently returns the PREVIOUS occupant's output.
        """
        self._store.pop((run_id, step_id), None)

    def __len__(self) -> int:
        return len(self._store)


# Module-level singleton — process-local, shared by the API endpoint.
step_output_store = StepOutputStore()


def _collapse_schema_error(exc: jsonschema.ValidationError) -> str:
    """Collapse a (possibly multi-line) jsonschema error to a single line."""
    message = str(exc).splitlines()[0] if str(exc) else "output failed schema validation"
    return message


def record_step_output(
    run_id: str,
    step_id: str,
    output: Dict[str, Any],
    output_schema: Optional[Dict[str, Any]] = None,
) -> StepOutputRecord:
    """Validate a worker output against its schema and store the record (C5, FR-4.1).

    For the synthetic-key MVP the ``output_schema`` arrives WITH the request
    (there is no run record to re-resolve it from — F2). The validation locus is
    the seam, not the engine (ADR-4).

    - No ``output_schema`` -> ``validated=True`` (trivially valid), state
      ``COMPLETED``.
    - Schema **pass** -> ``validated=True``, ``errors=[]``, state ``COMPLETED``.
    - Schema **fail** -> ``validated=False``, ``errors=[<collapsed reason>]``,
      state ``COMPLETED_UNVALIDATED``. This does NOT raise — the flag is recorded
      for the engine to act on (FR-4.3 / B2-BR-8).

    ``run_id`` / ``step_id`` are validated per B2-BR-1 before any store write; a
    malformed key raises ``ValueError`` -> HTTPException 400.

    Returns the stored ``StepOutputRecord``.
    """
    _validate_key_part(run_id, "run_id")
    _validate_key_part(step_id, "step_id")

    validated = True
    errors: List[str] = []
    if output_schema is not None:
        try:
            jsonschema.validate(output, output_schema, cls=jsonschema.Draft202012Validator)
        except jsonschema.ValidationError as exc:
            validated = False
            errors = [_collapse_schema_error(exc)]
        except jsonschema.SchemaError as exc:
            # A malformed schema arriving at runtime is a caller error (the
            # authoring-time check should have caught it); treat it as a 400.
            raise ValueError(f"output_schema is not valid JSON-Schema: {exc}")

    record = StepOutputRecord(
        run_id=run_id,
        step_id=step_id,
        output=output,
        validated=validated,
        errors=errors,
        state=StepState.COMPLETED if validated else StepState.COMPLETED_UNVALIDATED,
    )
    step_output_store.put(run_id, step_id, record)
    return record


@dataclass(frozen=True)
class ArtifactRef:
    """Immutable reference to exact bytes in one result store, never an arbitrary path."""

    content_hash: str
    immutable_location: str
    byte_length: int

    def __post_init__(self) -> None:
        if not isinstance(self.content_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.content_hash
        ):
            raise ValueError("artifact hash must be a lowercase SHA-256 digest")
        if (
            not isinstance(self.immutable_location, str)
            or self.immutable_location != self.content_hash
        ):
            raise ValueError("artifact location must be its hash filename")
        if type(self.byte_length) is not int or self.byte_length < 0:
            raise ValueError("artifact byte length must be a nonnegative integer")


_ResultT = TypeVar("_ResultT")


class ImmutableResultStore:
    """Durable, content-addressed artifacts with serialized reference acceptance.

    A failed acceptance leaves a recoverable orphan. ``accept`` must synchronously
    commit the durable reference before returning; it may read artifacts, but must
    not recursively publish or collect from this store. All reference writers and
    orphan collectors must use this protocol. POSIX locking and fsync are required;
    unavailable durability or locking never silently degrades to best effort.
    """

    def __init__(self, root: Path, *, max_bytes: int = 8 * 1024 * 1024) -> None:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        root = Path(root)
        if ".." in root.parts:
            raise ValueError("result root cannot contain traversal components")
        self.root = root.absolute()
        if self.root == Path(self.root.anchor):
            raise ValueError("result root must be a dedicated directory")
        self.max_bytes = max_bytes
        with self._directory(create=True):
            pass

    @contextmanager
    def _directory(self, *, create: bool = False) -> Iterator[int]:
        """Walk without following even ancestor symlinks; use anchored directory FDs."""
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = os.open(self.root.anchor, flags)
        try:
            for part in self.root.parts[1:]:
                try:
                    child = os.open(part, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=descriptor)
                        os.fsync(descriptor)
                    except FileExistsError:
                        pass
                    child = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            info = os.fstat(descriptor)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
                raise ValueError("result root must be owned by this user and not publicly writable")
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def _private_file(descriptor: int) -> os.stat_result:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ValueError("result files must be owner-only regular files")
        return info

    @contextmanager
    def _locked(self) -> Iterator[int]:
        import fcntl

        with self._directory() as directory:
            lock = os.open(
                ".result-store.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory,
            )
            try:
                self._private_file(lock)
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    yield directory
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
            finally:
                os.close(lock)

    def _read_artifact(self, directory: int, ref: ArtifactRef, *, flush: bool = False) -> bytes:
        if not isinstance(ref, ArtifactRef) or ref.byte_length > self.max_bytes:
            raise ValueError("artifact reference exceeds the store's size bound")
        descriptor = os.open(
            ref.immutable_location,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "rb") as source:
            info = self._private_file(source.fileno())
            if info.st_size != ref.byte_length:
                raise ValueError("artifact size does not match its reference")
            content = source.read(self.max_bytes + 1)
            if (
                len(content) != ref.byte_length
                or hashlib.sha256(content).hexdigest() != ref.content_hash
            ):
                raise ValueError("artifact bytes do not match their content hash")
            if flush:
                os.fsync(source.fileno())
            return content

    def read(self, ref: ArtifactRef) -> bytes:
        """Read and verify bounded bytes; an open descriptor survives concurrent unlink."""
        with self._directory() as directory:
            return self._read_artifact(directory, ref)

    def publish(self, content: bytes, accept: Callable[[ArtifactRef], _ResultT]) -> _ResultT:
        """Flush immutable bytes before accepting their durable reference under the lock."""
        if not isinstance(content, bytes) or len(content) > self.max_bytes:
            raise ValueError("result must be bounded bytes")
        content_hash = hashlib.sha256(content).hexdigest()
        ref = ArtifactRef(content_hash, content_hash, len(content))
        with self._locked() as directory:
            try:
                self._read_artifact(directory, ref, flush=True)
            except FileNotFoundError:
                temporary = ".publish-" + uuid.uuid4().hex
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=directory,
                )
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        os.fchmod(output.fileno(), 0o600)
                        output.write(content)
                        output.flush()
                        os.fsync(output.fileno())
                    try:
                        os.link(
                            temporary,
                            content_hash,
                            src_dir_fd=directory,
                            dst_dir_fd=directory,
                            follow_symlinks=False,
                        )
                    except FileExistsError:
                        # Never overwrite a published inode, even if a non-cooperating
                        # writer raced the lock. Verification below must still succeed.
                        pass
                finally:
                    os.unlink(temporary, dir_fd=directory)
                self._read_artifact(directory, ref, flush=True)
            os.fsync(directory)
            return accept(ref)

    def collect_orphans(
        self, referenced_hashes: Callable[[], set[str]], older_than: float
    ) -> list[str]:
        """Remove private unreferenced artifacts and crash staging files older than a cutoff.

        Reference lookup happens under the publication lock. Unknown filenames,
        symlinks, public/foreign-owned files and recent files are preserved. The
        cutoff is an absolute UNIX timestamp. Errors obtaining references fail
        before deletion. Returns removed filenames, including eligible staging names.
        """
        if (
            isinstance(older_than, bool)
            or not isinstance(older_than, (int, float))
            or not math.isfinite(older_than)
        ):
            raise ValueError("older_than must be a finite UNIX timestamp")
        removed = []
        with self._locked() as directory:
            referenced = referenced_hashes()
            if not isinstance(referenced, set) or any(
                not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in referenced
            ):
                raise ValueError("reference lookup must return a set of content hashes")
            for name in sorted(os.listdir(directory)):
                if (
                    not re.fullmatch(r"(?:[0-9a-f]{64}|\.publish-[0-9a-f]{32})", name)
                    or name in referenced
                ):
                    continue
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) & 0o077
                    or info.st_mtime >= older_than
                ):
                    continue
                os.unlink(name, dir_fd=directory)
                removed.append(name)
            if removed:
                os.fsync(directory)
        return removed
