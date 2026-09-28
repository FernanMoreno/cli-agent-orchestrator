"""Create a bounded, private copy of mutable Bubblewrap runtime trees."""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_MAX_BYTES = 512 * 1024 * 1024
_DEFAULT_MAX_FILES = 100_000
_FILE_MODE = 0o444
_EXECUTABLE_FILE_MODE = 0o555
_DIRECTORY_MODE = 0o555
_PRIVATE_MODE = 0o700
_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW


class RuntimeSnapshotError(RuntimeError):
    """The runtime tree cannot be copied or its snapshot is no longer valid."""


@dataclass(frozen=True, slots=True)
class _Entry:
    kind: str
    mode: int
    digest: str | None = None
    target: str | None = None


@dataclass(slots=True)
class RuntimeSnapshot:
    """Own a private snapshot until child cleanup is confirmed.

    Call ``close(cleanup_confirmed=False)`` when a child may still be using the
    snapshot. The directory is retained so uncertain cleanup never destroys
    evidence or resources still visible to that child.
    """

    path: Path
    runtime_path: Path
    package_path: Path
    deps_path: Path
    _manifest: dict[str, _Entry] = field(repr=False)
    _root_identity: tuple[int, int] = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def __enter__(self) -> RuntimeSnapshot:
        if self._closed:
            raise RuntimeSnapshotError("runtime snapshot is already closed")
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close(cleanup_confirmed=True)

    @property
    def digest(self) -> str:
        """Return a stable digest of paths, types, modes, and file contents."""

        return _manifest_digest(self._manifest)

    def validate(self) -> None:
        """Reject replacement, added entries, mode drift, or content changes."""

        if self._closed:
            raise RuntimeSnapshotError("runtime snapshot is closed")
        try:
            root_info = os.stat(self.path, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeSnapshotError("runtime snapshot root changed") from exc
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or (root_info.st_dev, root_info.st_ino) != self._root_identity
            or root_info.st_uid != os.getuid()
            or stat.S_IMODE(root_info.st_mode) != _PRIVATE_MODE
        ):
            raise RuntimeSnapshotError("runtime snapshot root changed")

        observed: dict[str, _Entry] = {}
        try:
            for tree_name in ("runtime", "package", "deps"):
                tree = self.path / tree_name
                tree_info = os.stat(tree, follow_symlinks=False)
                if not stat.S_ISDIR(tree_info.st_mode):
                    raise RuntimeSnapshotError("runtime snapshot changed")
                _inspect_tree(tree, tree_name, observed)
        except RuntimeSnapshotError:
            raise
        except OSError as exc:
            raise RuntimeSnapshotError("runtime snapshot changed") from exc
        if observed != self._manifest:
            raise RuntimeSnapshotError("runtime snapshot changed")

    def close(self, cleanup_confirmed: bool) -> None:
        """Remove only after confirmed cleanup; otherwise retain the snapshot."""

        if type(cleanup_confirmed) is not bool:
            raise TypeError("cleanup_confirmed must be a bool")
        if self._closed:
            return
        if not cleanup_confirmed:
            return
        try:
            _remove_private_tree(self.path)
        except OSError as exc:
            raise RuntimeSnapshotError("runtime snapshot cleanup failed") from exc
        self._closed = True


def prepare_runtime_snapshot(
    runtime: str | os.PathLike[str],
    source: str | os.PathLike[str],
    deps: str | os.PathLike[str],
    *,
    parent: str | os.PathLike[str],
    max_bytes: int = _DEFAULT_MAX_BYTES,
    max_files: int = _DEFAULT_MAX_FILES,
    runtime_entries: Sequence[str] | None = None,
    source_entries: Sequence[str] | None = None,
    deps_entries: Sequence[str] | None = None,
) -> RuntimeSnapshot:
    """Copy three trees without following source links or special files.

    The parent must already be an owner-controlled private directory. Snapshot
    files become read-only (0444), retaining execute permission as read/execute
    only (0555) where the source had any execute bit. Directories become
    read/execute-only (0555), and the snapshot root remains private (0700).
    """

    if type(max_bytes) is not int or max_bytes < 0:
        raise RuntimeSnapshotError("byte limit must be a non-negative integer")
    if type(max_files) is not int or max_files < 0:
        raise RuntimeSnapshotError("file limit must be a non-negative integer")
    entry_filters = {
        "runtime": _validate_entry_filter(runtime_entries),
        "package": _validate_entry_filter(source_entries),
        "deps": _validate_entry_filter(deps_entries),
    }

    parent_path = Path(parent).absolute()
    _validate_private_parent(parent_path)
    snapshot_path = parent_path / f"cao-runtime-{secrets.token_hex(16)}"
    try:
        os.mkdir(snapshot_path, _PRIVATE_MODE)
    except OSError as exc:
        raise RuntimeSnapshotError("cannot create private runtime snapshot") from exc

    manifest: dict[str, _Entry] = {}
    totals = [0, 0]  # bytes, non-directory entries
    sources = (
        ("runtime", Path(runtime)),
        ("package", Path(source)),
        ("deps", Path(deps)),
    )
    try:
        for tree_name, source_path in sources:
            destination = snapshot_path / tree_name
            os.mkdir(destination, _PRIVATE_MODE)
            _copy_tree(
                source_path,
                destination,
                tree_name,
                manifest,
                totals,
                max_bytes,
                max_files,
                entry_filters[tree_name],
            )
            _set_tree_modes(destination)
        _validate_snapshot_links(snapshot_path, manifest)
        info = os.stat(snapshot_path, follow_symlinks=False)
        return RuntimeSnapshot(
            path=snapshot_path,
            runtime_path=snapshot_path / "runtime",
            package_path=snapshot_path / "package",
            deps_path=snapshot_path / "deps",
            _manifest=manifest,
            _root_identity=(info.st_dev, info.st_ino),
        )
    except RuntimeSnapshotError:
        _cleanup_partial(snapshot_path)
        raise
    except OSError as exc:
        _cleanup_partial(snapshot_path)
        raise RuntimeSnapshotError(
            f"cannot create runtime snapshot: {exc.strerror or exc}"
        ) from exc
    except BaseException:
        _cleanup_partial(snapshot_path)
        raise


def _validate_private_parent(parent: Path) -> None:
    try:
        info = os.stat(parent, follow_symlinks=False)
    except OSError as exc:
        raise RuntimeSnapshotError("snapshot parent is unavailable") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise RuntimeSnapshotError("snapshot parent must be an owner-controlled private directory")


def _validate_entry_filter(entries: Sequence[str] | None) -> frozenset[str] | None:
    if entries is None:
        return None
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        raise RuntimeSnapshotError("snapshot entries must be a sequence of top-level names")
    names = tuple(entries)
    if any(
        type(name) is not str
        or not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\0" in name
        for name in names
    ) or len(set(names)) != len(names):
        raise RuntimeSnapshotError("snapshot entry filter contains an invalid name")
    return frozenset(names)


def _copy_tree(
    source: Path,
    destination: Path,
    tree_name: str,
    manifest: dict[str, _Entry],
    totals: list[int],
    max_bytes: int,
    max_files: int,
    entry_filter: frozenset[str] | None,
) -> None:
    try:
        source_fd = os.open(source, _DIRECTORY_FLAGS)
    except OSError as exc:
        raise RuntimeSnapshotError(f"source tree is not a real directory: {source}") from exc
    try:
        root_info = os.fstat(source_fd)
        if not stat.S_ISDIR(root_info.st_mode):
            raise RuntimeSnapshotError("unsupported source root")
        _copy_directory_fd(
            source_fd,
            destination,
            tree_name,
            tree_name,
            manifest,
            totals,
            max_bytes,
            max_files,
            entry_filter,
        )
    finally:
        os.close(source_fd)


def _copy_directory_fd(
    source_fd: int,
    destination: Path,
    tree_name: str,
    relative: str,
    manifest: dict[str, _Entry],
    totals: list[int],
    max_bytes: int,
    max_files: int,
    entry_filter: frozenset[str] | None,
) -> None:
    manifest[relative] = _Entry("directory", _DIRECTORY_MODE)
    try:
        with os.scandir(source_fd) as entries:
            all_names = {entry.name for entry in entries}
            if relative == tree_name and entry_filter is not None:
                missing = entry_filter - all_names
                if missing:
                    raise RuntimeSnapshotError("snapshot entry filter names a missing path")
                names = sorted(entry_filter)
            else:
                names = sorted(all_names)
    except OSError as exc:
        raise RuntimeSnapshotError("cannot inspect source directory") from exc

    for name in names:
        if name in {".", ".."} or "/" in name or "\0" in name:
            raise RuntimeSnapshotError("unsupported source entry name")
        child_rel = f"{relative}/{name}"
        output = destination / name
        try:
            source_info = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeSnapshotError("source tree changed during snapshot") from exc

        if stat.S_ISDIR(source_info.st_mode):
            child_fd = _open_child_directory(source_fd, name, source_info)
            try:
                os.mkdir(output, _PRIVATE_MODE)
                _copy_directory_fd(
                    child_fd,
                    output,
                    tree_name,
                    child_rel,
                    manifest,
                    totals,
                    max_bytes,
                    max_files,
                    entry_filter,
                )
            finally:
                os.close(child_fd)
            continue

        totals[1] += 1
        if totals[1] > max_files:
            raise RuntimeSnapshotError("file limit exceeded")

        if stat.S_ISREG(source_info.st_mode):
            output_mode = _EXECUTABLE_FILE_MODE if source_info.st_mode & 0o111 else _FILE_MODE
            digest = _copy_regular_file(
                source_fd, name, output, source_info, output_mode, totals, max_bytes
            )
            manifest[child_rel] = _Entry("file", output_mode, digest=digest)
        elif stat.S_ISLNK(source_info.st_mode):
            target = _read_symlink(source_fd, name, source_info)
            if os.path.isabs(target):
                raise RuntimeSnapshotError(f"absolute symlink is not allowed: {child_rel}")
            try:
                os.symlink(target, output)
            except OSError as exc:
                raise RuntimeSnapshotError("cannot preserve source symlink") from exc
            manifest[child_rel] = _Entry("symlink", 0o777, target=target)
        else:
            raise RuntimeSnapshotError(f"unsupported source node: {child_rel}")


def _open_child_directory(parent_fd: int, name: str, expected: os.stat_result) -> int:
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise RuntimeSnapshotError("source tree changed during snapshot") from exc
    observed = os.fstat(descriptor)
    if not stat.S_ISDIR(observed.st_mode) or (observed.st_dev, observed.st_ino) != (
        expected.st_dev,
        expected.st_ino,
    ):
        os.close(descriptor)
        raise RuntimeSnapshotError("source tree changed during snapshot")
    return descriptor


def _copy_regular_file(
    parent_fd: int,
    name: str,
    output: Path,
    before: os.stat_result,
    output_mode: int,
    totals: list[int],
    max_bytes: int,
) -> str:
    try:
        source_fd = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise RuntimeSnapshotError("source file changed during snapshot") from exc
    output_fd = -1
    try:
        opened = os.fstat(source_fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise RuntimeSnapshotError("source file changed during snapshot")
        if totals[0] + opened.st_size > max_bytes:
            raise RuntimeSnapshotError("byte limit exceeded")
        output_fd = os.open(
            output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        digest = hashlib.sha256()
        copied = 0
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if totals[0] + copied > max_bytes:
                raise RuntimeSnapshotError("byte limit exceeded")
            digest.update(chunk)
            _write_all(output_fd, chunk)
        after = os.fstat(source_fd)
        if copied != opened.st_size or _source_version(opened) != _source_version(after):
            raise RuntimeSnapshotError("source file changed during snapshot")
        os.fchmod(output_fd, output_mode)
        totals[0] += copied
        return digest.hexdigest()
    except OSError as exc:
        raise RuntimeSnapshotError("cannot copy source file") from exc
    finally:
        if output_fd >= 0:
            os.close(output_fd)
        os.close(source_fd)


def _source_version(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        stat.S_IMODE(info.st_mode),
    )


def _read_symlink(parent_fd: int, name: str, expected: os.stat_result) -> str:
    try:
        target = os.readlink(name, dir_fd=parent_fd)
        observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise RuntimeSnapshotError("source symlink changed during snapshot") from exc
    if (observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino):
        raise RuntimeSnapshotError("source symlink changed during snapshot")
    return target


def _write_all(descriptor: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("short write while creating snapshot")
        remaining = remaining[written:]


def _set_tree_modes(directory: Path) -> None:
    """Apply bottom-up read-only modes without following any copied symlinks."""

    for current, dirnames, filenames in os.walk(directory, topdown=False, followlinks=False):
        current_path = Path(current)
        del filenames  # File modes were fixed at their no-follow copy point.
        for dirname in dirnames:
            entry = current_path / dirname
            if not entry.is_symlink():
                os.chmod(entry, _DIRECTORY_MODE, follow_symlinks=False)
        os.chmod(current_path, _DIRECTORY_MODE, follow_symlinks=False)


def _validate_snapshot_links(snapshot: Path, manifest: dict[str, _Entry]) -> None:
    for relative, entry in manifest.items():
        if entry.kind != "symlink":
            continue
        link = snapshot / relative
        tree_root = snapshot / relative.split("/", maxsplit=1)[0]
        try:
            resolved = link.resolve(strict=True)
            resolved.relative_to(tree_root.resolve(strict=True))
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeSnapshotError(f"symlink escapes or is unresolved: {relative}") from exc


def _inspect_tree(tree: Path, tree_name: str, observed: dict[str, _Entry]) -> None:
    def inspect(directory: Path, relative: str) -> None:
        info = os.stat(directory, follow_symlinks=False)
        mode = stat.S_IMODE(info.st_mode)
        observed[relative] = _Entry("directory", mode)
        if mode != _DIRECTORY_MODE:
            return
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
        for child in children:
            child_rel = f"{relative}/{child.name}"
            child_path = directory / child.name
            child_info = child.stat(follow_symlinks=False)
            child_mode = stat.S_IMODE(child_info.st_mode)
            if stat.S_ISDIR(child_info.st_mode):
                observed[child_rel] = _Entry("directory", child_mode)
                inspect(child_path, child_rel)
            elif stat.S_ISREG(child_info.st_mode):
                digest = _hash_regular_file(child_path)
                observed[child_rel] = _Entry("file", child_mode, digest=digest)
            elif stat.S_ISLNK(child_info.st_mode):
                observed[child_rel] = _Entry("symlink", child_mode, target=os.readlink(child_path))
            else:
                raise RuntimeSnapshotError("runtime snapshot changed")

    inspect(tree, tree_name)


def _hash_regular_file(path: Path) -> str:
    try:
        descriptor = os.open(path, _READ_FLAGS)
    except OSError as exc:
        raise RuntimeSnapshotError("runtime snapshot changed") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeSnapshotError("runtime snapshot changed")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        if _source_version(before) != _source_version(after):
            raise RuntimeSnapshotError("runtime snapshot changed")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _manifest_digest(manifest: dict[str, _Entry]) -> str:
    digest = hashlib.sha256()
    for path, entry in sorted(manifest.items()):
        fields = (path, entry.kind, f"{entry.mode:04o}", entry.digest or "", entry.target or "")
        digest.update("\0".join(fields).encode("utf-8", errors="surrogateescape"))
        digest.update(b"\n")
    return digest.hexdigest()


def _cleanup_partial(path: Path) -> None:
    try:
        _remove_private_tree(path)
    except OSError:
        # Failed partial cleanup is safer retained than traversed unsafely.
        pass


def _remove_private_tree(path: Path) -> None:
    """Remove the owned snapshot using directory descriptors and no-follow opens."""

    parent_fd = os.open(path.parent, _DIRECTORY_FLAGS)
    try:
        root_fd = os.open(path.name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        try:
            _remove_directory_contents(root_fd)
            os.fchmod(root_fd, _PRIVATE_MODE)
        finally:
            os.close(root_fd)
        os.rmdir(path.name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _remove_directory_contents(directory_fd: int) -> None:
    # Deleting entries requires write permission on the directory itself.
    os.fchmod(directory_fd, _PRIVATE_MODE)
    with os.scandir(directory_fd) as entries:
        names = [entry.name for entry in entries]
    for name in names:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            try:
                observed = os.fstat(child_fd)
                if (observed.st_dev, observed.st_ino) != (info.st_dev, info.st_ino):
                    raise OSError("snapshot changed during cleanup")
                _remove_directory_contents(child_fd)
                os.fchmod(child_fd, _PRIVATE_MODE)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)
