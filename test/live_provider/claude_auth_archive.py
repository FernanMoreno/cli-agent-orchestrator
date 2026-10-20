"""Preserve the private Claude credential copy before deleting a run root.

The helper wraps the existing journey cleanup function. It never writes to the
configured source profile or auth stage. A failed or uncertain archive
operation prevents run-root removal.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Literal

MAX_CREDENTIAL_BYTES = 1024 * 1024
_RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_ARCHIVE_CATEGORY = "claude_auth_archive_incomplete"
_LAYOUT_COMPONENTS: dict[str, tuple[str, ...]] = {
    "interaction": ("claude-home",),
    "message": ("home", ".claude"),
}


class CredentialArchiveError(RuntimeError):
    """The per-run Claude credential copy could not be preserved safely."""


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _directory_identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid


def _path_without_symlinks(path: Path) -> None:
    if not path.is_absolute():
        raise CredentialArchiveError("archive path must be absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise CredentialArchiveError("archive path component unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise CredentialArchiveError("archive path contains symlink")
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise CredentialArchiveError("archive path ancestor is not a directory")


def _private_directory(path: Path) -> os.stat_result:
    _path_without_symlinks(path)
    try:
        info = path.lstat()
    except OSError as exc:
        raise CredentialArchiveError("private directory unavailable") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise CredentialArchiveError("private directory ownership or mode invalid")
    return info


def _open_directory(path: Path) -> tuple[int, os.stat_result]:
    before = _private_directory(path)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if not getattr(os, "O_DIRECTORY", 0) or not getattr(os, "O_NOFOLLOW", 0):
        raise CredentialArchiveError("safe directory open unavailable")
    try:
        descriptor = os.open(path, flags | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        raise CredentialArchiveError("private directory open failed") from exc
    opened = os.fstat(descriptor)
    if _identity(opened) != _identity(before):
        os.close(descriptor)
        raise CredentialArchiveError("private directory changed while opening")
    return descriptor, opened


def _open_child_directory(parent_fd: int, name: str) -> tuple[int, os.stat_result]:
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise CredentialArchiveError("private child directory unavailable") from exc
    if stat.S_ISLNK(before.st_mode):
        raise CredentialArchiveError("private child directory symlink rejected")
    if (
        not stat.S_ISDIR(before.st_mode)
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) != 0o700
    ):
        raise CredentialArchiveError("private child directory ownership or mode invalid")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags | getattr(os, "O_CLOEXEC", 0), dir_fd=parent_fd)
    except OSError as exc:
        raise CredentialArchiveError("private child directory open failed") from exc
    opened = os.fstat(descriptor)
    try:
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        os.close(descriptor)
        raise CredentialArchiveError("private child directory changed while opening") from exc
    if _identity(opened) != _identity(before) or _identity(after) != _identity(before):
        os.close(descriptor)
        raise CredentialArchiveError("private child directory changed while opening")
    return descriptor, opened


def prepare_archive_root(path: Path) -> Path:
    """Create one private archive directory below an existing private parent."""
    path = Path(path)
    if not path.is_absolute():
        raise CredentialArchiveError("archive root must be absolute")
    parent = path.parent
    parent_fd, parent_info = _open_directory(parent)
    child_fd = None
    created = False
    try:
        os.mkdir(path.name, mode=0o700, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        os.close(parent_fd)
        raise CredentialArchiveError("archive root creation failed") from exc
    try:
        child_fd, child_info = _open_child_directory(parent_fd, path.name)
        if created and _directory_identity(os.fstat(parent_fd)) != _directory_identity(parent_info):
            raise CredentialArchiveError("archive parent changed during creation")
        try:
            os.fsync(parent_fd)
        except OSError as exc:
            raise CredentialArchiveError("archive parent sync failed") from exc
        if _directory_identity(_private_directory(path)) != _directory_identity(child_info):
            raise CredentialArchiveError("archive root changed during creation")
        return path.resolve(strict=True)
    finally:
        if child_fd is not None:
            os.close(child_fd)
        os.close(parent_fd)


def _read_stable_credential(directory_fd: int) -> tuple[bytes, tuple[int, ...]]:
    try:
        before = os.stat(".credentials.json", dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise CredentialArchiveError("credential file unavailable") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) != 0o600
        or before.st_nlink != 1
        or not 0 < before.st_size <= MAX_CREDENTIAL_BYTES
    ):
        raise CredentialArchiveError("credential file ownership, mode, or size invalid")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise CredentialArchiveError("no-follow file open unavailable")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(".credentials.json", flags, dir_fd=directory_fd)
    except OSError as exc:
        raise CredentialArchiveError("credential open failed") from exc
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            raise CredentialArchiveError("credential identity changed before read")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, MAX_CREDENTIAL_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_CREDENTIAL_BYTES:
                raise CredentialArchiveError("credential exceeds size limit")
            chunks.append(chunk)
        after_fd = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        after_path = os.stat(".credentials.json", dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise CredentialArchiveError("credential path changed during read") from exc
    if (
        _identity(after_fd) != _identity(before)
        or _identity(after_path) != _identity(before)
        or total != before.st_size
    ):
        raise CredentialArchiveError("credential changed during read")
    return b"".join(chunks), _identity(before)


def _write_new_archive_file(parent_fd: int, destination: Path, content: bytes) -> None:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise CredentialArchiveError("no-follow file creation unavailable")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(destination.name, flags, 0o600, dir_fd=parent_fd)
    except OSError as exc:
        raise CredentialArchiveError("archive destination creation failed") from exc
    try:
        os.fchmod(descriptor, 0o600)
        initial = os.fstat(descriptor)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_uid != os.geteuid()
            or stat.S_IMODE(initial.st_mode) != 0o600
            or initial.st_nlink != 1
        ):
            raise CredentialArchiveError("archive file ownership or mode invalid")
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise CredentialArchiveError("archive write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        final_fd = os.fstat(descriptor)
    except OSError as exc:
        raise CredentialArchiveError("archive write failed") from exc
    finally:
        os.close(descriptor)
    try:
        final_path = os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
        path_info = destination.lstat()
        parent_info = os.fstat(parent_fd)
    except OSError as exc:
        raise CredentialArchiveError("archive file verification failed") from exc
    if (
        not stat.S_ISREG(final_path.st_mode)
        or final_path.st_uid != os.geteuid()
        or stat.S_IMODE(final_path.st_mode) != 0o600
        or final_path.st_nlink != 1
        or final_path.st_size != len(content)
        or _identity(final_path) != _identity(final_fd)
        or _identity(path_info) != _identity(final_fd)
        or not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or stat.S_IMODE(parent_info.st_mode) != 0o700
    ):
        raise CredentialArchiveError("archive file verification failed")
    try:
        os.fsync(parent_fd)
    except OSError as exc:
        raise CredentialArchiveError("archive directory sync failed") from exc


def archive_private_credential_copy(
    root: Path,
    archive_root: Path,
    *,
    process_stopped: bool,
    layout: Literal["interaction", "message"] = "interaction",
    archive_key: str | None = None,
) -> Path:
    """Archive a fixed-layout run credential without altering its source."""
    if process_stopped is not True:
        raise CredentialArchiveError("provider process is not confirmed stopped")
    if not isinstance(layout, str) or layout not in _LAYOUT_COMPONENTS:
        raise CredentialArchiveError("credential layout invalid")
    root = Path(root)
    archive_root = Path(archive_root)
    if archive_key is None:
        archive_key = root.name
    if not isinstance(archive_key, str) or not _RUN_NAME.fullmatch(archive_key):
        raise CredentialArchiveError("archive key invalid")
    root_fd = archive_fd = run_fd = None
    layout_fds: list[int] = []
    try:
        root_fd, root_info = _open_directory(root)
        archive_fd, archive_info = _open_directory(archive_root)
        root_resolved = root.resolve(strict=True)
        archive_resolved = archive_root.resolve(strict=True)
        if archive_resolved == root_resolved or archive_resolved.is_relative_to(root_resolved):
            raise CredentialArchiveError("archive root must be outside run root")
        if root_info.st_uid != archive_info.st_uid:
            raise CredentialArchiveError("archive and run root owners differ")

        layout_paths: list[Path] = []
        layout_infos: list[os.stat_result] = []
        directory_fd = root_fd
        layout_path = root
        for component in _LAYOUT_COMPONENTS[layout]:
            child_fd, child_info = _open_child_directory(directory_fd, component)
            layout_fds.append(child_fd)
            layout_infos.append(child_info)
            layout_path = layout_path / component
            layout_paths.append(layout_path)
            directory_fd = child_fd
        credential_directory_fd = layout_fds[-1]
        content, credential_identity = _read_stable_credential(credential_directory_fd)
        run_archive = archive_root / archive_key
        try:
            os.mkdir(archive_key, mode=0o700, dir_fd=archive_fd)
        except OSError as exc:
            raise CredentialArchiveError("run archive already exists or cannot be created") from exc
        run_fd, run_info = _open_child_directory(archive_fd, archive_key)
        try:
            os.fsync(archive_fd)
        except OSError as exc:
            raise CredentialArchiveError("archive root sync failed") from exc
        destination = run_archive / "credentials.json"
        _write_new_archive_file(run_fd, destination, content)
        try:
            os.fsync(archive_fd)
        except OSError as exc:
            raise CredentialArchiveError("archive root sync failed") from exc
        try:
            final_credential = os.stat(
                ".credentials.json",
                dir_fd=credential_directory_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise CredentialArchiveError("credential changed before cleanup") from exc
        if _identity(final_credential) != credential_identity:
            raise CredentialArchiveError("credential changed before cleanup")
        if (
            _directory_identity(_private_directory(root)) != _directory_identity(root_info)
            or _directory_identity(_private_directory(archive_root))
            != _directory_identity(archive_info)
            or _directory_identity(_private_directory(run_archive)) != _directory_identity(run_info)
            or any(
                _directory_identity(_private_directory(path)) != _directory_identity(info)
                for path, info in zip(layout_paths, layout_infos, strict=True)
            )
        ):
            raise CredentialArchiveError("archive path changed before completion")
        return destination
    finally:
        for descriptor in (run_fd, *reversed(layout_fds), archive_fd, root_fd):
            if descriptor is not None:
                os.close(descriptor)


def install_cleanup_archive_hook(journey: ModuleType, archive_root: Path) -> Callable[..., Any]:
    """Wrap journey cleanup so run-root removal follows credential archival."""
    original = getattr(journey, "_remove_private_run_root", None)
    if not callable(original):
        raise CredentialArchiveError("journey cleanup function unavailable")
    if getattr(original, "_claude_auth_archive_hook", False):
        raise CredentialArchiveError("journey cleanup hook already installed")

    def wrapped(root: Path, *, process_stopped: bool, failures: set[str]) -> Any:
        if process_stopped is not True:
            return original(root, process_stopped=False, failures=failures)
        if not failures:
            try:
                archive_private_credential_copy(root, archive_root, process_stopped=process_stopped)
            except Exception:
                failures.add(_ARCHIVE_CATEGORY)
                return original(root, process_stopped=False, failures=failures)
        return original(root, process_stopped=process_stopped, failures=failures)

    wrapped._claude_auth_archive_hook = True  # type: ignore[attr-defined]
    wrapped.__wrapped__ = original  # type: ignore[attr-defined]
    setattr(journey, "_remove_private_run_root", wrapped)
    return wrapped
