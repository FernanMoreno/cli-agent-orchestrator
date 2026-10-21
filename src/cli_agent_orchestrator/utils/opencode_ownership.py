"""Private, exact-key ownership receipts for fresh OpenCode configuration trees.

These receipts contain no credentials or business/turn state. The PostgreSQL
registry remains authoritative for terminal lifecycle and recovery decisions.
"""

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterator, NoReturn

from cli_agent_orchestrator.utils.atomic_file import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    _atomic_publish,
    _file_lock,
)


class PrivateConfigOwnershipError(RuntimeError):
    """Ownership could not be established without guessing or following links."""


def _refuse() -> NoReturn:
    raise PrivateConfigOwnershipError("OpenCode private configuration ownership is unavailable")


def _private_directory(path: Path, *, create: bool = False) -> bool:
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        _refuse()
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        if not create:
            _refuse()
        # Match the existing native pool/CAO-home creation contract, without
        # following a substituted directory link while restricting its mode.
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                _refuse()
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino) or stat.S_IMODE(
            current.st_mode
        ) != 0o700:
            _refuse()
    return True


def _read_private(path: Path) -> bytes | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size > 4096
        ):
            _refuse()
        value = os.read(descriptor, 4097)
        if len(value) != metadata.st_size:
            _refuse()
        return value
    finally:
        os.close(descriptor)


class PrivateConfigOwnership:
    """Keep one terminal's launch receipt addressable without directory scans."""

    def __init__(self, base: Path, terminal_id: str) -> None:
        if (
            not isinstance(terminal_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", terminal_id) is None
        ):
            _refuse()
        self.base = base
        self.terminal_id = terminal_id
        self.directory = base / "opencode-ownership"
        key = hashlib.sha256(terminal_id.encode("utf-8")).hexdigest()
        self.path = self.directory / f"{key}.json"
        self._required = False
        self._snapshot = False
        self._bound: dict[str, Any] | None = None
        self._pending: dict[str, Any] | None = None
        self.managed = False

    @contextlib.contextmanager
    def _transaction(self, *, create: bool = False) -> Iterator[bool]:
        if not create and not self.directory.exists() and not self.directory.is_symlink():
            yield False
            return
        if not _private_directory(self.base, create=create):
            yield False
            return
        if not _private_directory(self.directory, create=create):
            yield False
            return
        lock = self.path.with_suffix(".lock")
        # Reuse the project's bounded interprocess lock and atomic publisher;
        # lock artifacts are private and permanent, avoiding inode replacement.
        if lock.exists() or lock.is_symlink():
            _read_private(lock)
        with _file_lock(lock, DEFAULT_LOCK_TIMEOUT_SECONDS):
            _read_private(lock)
            yield True

    def _document(self, claim: dict[str, Any] | None, *, phase: str = "active") -> dict[str, Any]:
        return {"version": 1, "terminal_id": self.terminal_id, "claim": claim, "phase": phase}

    def _decode_document(self, raw: bytes) -> dict[str, Any]:
        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    _refuse()
                result[key] = value
            return result

        try:
            document = json.loads(raw, object_pairs_hook=unique_object)
        except (ValueError, UnicodeError):
            _refuse()
        if (
            not isinstance(document, dict)
            or set(document)
            not in (
                {"version", "terminal_id", "claim"},
                {"version", "terminal_id", "claim", "phase"},
            )
            or type(document["version"]) is not int
            or document["version"] != 1
            or document["terminal_id"] != self.terminal_id
        ):
            _refuse()
        document.setdefault("phase", "active")
        if document["phase"] not in ("active", "retiring") or (
            document["claim"] is None and document["phase"] != "active"
        ):
            _refuse()
        claim = document["claim"]
        if claim is not None:
            if (
                not isinstance(claim, dict)
                or set(claim) != {"major", "name", "nonce", "device", "inode"}
                or type(claim["major"]) is not int
                or claim["major"] not in (1, 2)
                or not isinstance(claim["name"], str)
                or re.fullmatch(r"terminal-[A-Za-z0-9_-]{1,128}", claim["name"]) is None
                or not isinstance(claim["nonce"], str)
                or re.fullmatch(r"[0-9a-f]{32}", claim["nonce"]) is None
                or type(claim["device"]) is not int
                or claim["device"] < 0
                or type(claim["inode"]) is not int
                or claim["inode"] <= 0
            ):
                _refuse()
        return document

    def _read(self) -> dict[str, Any] | None:
        raw = _read_private(self.path)
        return None if raw is None else self._decode_document(raw)

    def _publish(self, claim: dict[str, Any] | None, *, phase: str = "active") -> None:
        _atomic_publish(
            self.path, json.dumps(self._document(claim, phase=phase)) + "\n", "utf-8", mode=0o600
        )

    def _root(
        self, claim: dict[str, Any], *, marker: bool = True, retiring: bool = False
    ) -> Path | None:
        parent = self.base / f"opencode-v{claim['major']}"
        if not _private_directory(parent):
            return None
        root = parent / str(claim["name"])
        if not _private_directory(root):
            return None
        metadata = root.lstat()
        if (metadata.st_dev, metadata.st_ino) != (claim["device"], claim["inode"]):
            _refuse()
        if marker:
            raw = _read_private(root / ".cao-ownership.json")
            if raw is None:
                if retiring:
                    return root
                _refuse()
            recorded = self._decode_document(raw)
            if recorded != self._document(claim):
                _refuse()
        return root

    def bind_restored(self) -> None:
        """Bind reconstruction to the observed launch, without probing a CLI."""
        self._required = True
        with self._transaction() as available:
            if not available:
                return
            document = self._read()
            if document is not None:
                claim = document["claim"]
                if self._snapshot and claim != self._bound:
                    _refuse()
                if claim is not None:
                    self._root(claim, retiring=document["phase"] == "retiring")
                self._bound = claim
                self._snapshot = True
                self.managed = True

    def allocate(self, major: int) -> Path:
        if type(major) is not int or major not in (1, 2):
            _refuse()
        with self._transaction(create=True):
            document = self._read()
            if document is not None and document["claim"] is not None:
                _refuse()  # callers must retire the previous launch first
            parent = self.base / f"opencode-v{major}"
            _private_directory(parent, create=True)
            root = Path(tempfile.mkdtemp(prefix="terminal-", dir=parent))
            metadata = root.lstat()
            claim = {
                "major": major,
                "name": root.name,
                "nonce": secrets.token_hex(16),
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
            }
            self._pending = claim
            try:
                marker = root / ".cao-ownership.json"
                descriptor = os.open(
                    marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(json.dumps(self._document(claim)) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                self._publish(claim)
            except BaseException:
                # No configuration/credentials have been staged yet. Retain a
                # failed local cleanup claim for the caller's normal retry.
                with contextlib.suppress(OSError, PrivateConfigOwnershipError):
                    self._rollback_allocation()
                raise
            self._pending = None
            self._bound = claim
            self._snapshot = True
            self._required = True
            self.managed = True
            return root

    def _rollback_allocation(self) -> None:
        """Compensate under the allocation lock, preserving published intent."""
        claim = self._pending
        if claim is None:
            return
        document = self._read()
        if document is not None and document["claim"] is not None:
            if document["claim"] != claim:
                _refuse()
            self._root(claim)
            self._publish(claim, phase="retiring")
            # Keep the exact local claim until clearing its durable index has
            # succeeded, including when directory fsync fails after replace.
            self._remove_pending(retain=True)
            self._publish(None)
            self._pending = None
        else:
            self._remove_pending()

    def _remove_pending(self, *, retain: bool = False) -> None:
        if self._pending is not None:
            root = self._root(self._pending, marker=False)
            if root is not None:
                shutil.rmtree(root)
            if not retain:
                self._pending = None

    def cleanup(self) -> bool:
        """Refuse uncertain ownership and keep evidence for a later retry."""
        try:
            with self._transaction(create=self._pending is not None) as available:
                if not available:
                    return not self._required
                document = self._read()
                if document is None:
                    if self._pending is not None:
                        self._remove_pending()
                        self._publish(None)
                        self.managed = True
                        return True
                    return not self._required
                claim = document["claim"]
                if claim is not None:
                    if not ((self._snapshot and claim == self._bound) or claim == self._pending):
                        return False  # stale objects cannot retire a replacement
                    retiring = document["phase"] == "retiring"
                    root = self._root(claim, retiring=retiring)
                    if not retiring:
                        # Persist validated intent before a recursive removal
                        # can delete its marker and then fail partway through.
                        self._publish(claim, phase="retiring")
                    if root is not None:
                        shutil.rmtree(root)
                    self._publish(None)
                elif self._bound is not None and self._root(self._bound) is not None:
                    return False  # a cleared receipt cannot hide a live old tree
                self._remove_pending()
                self.managed = True
                self._required = True
                return True
        except (OSError, ValueError, PrivateConfigOwnershipError):
            return False
