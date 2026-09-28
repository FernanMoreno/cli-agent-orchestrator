"""The bootstrap mounts a private, socket-free copy of mutable runtime trees."""

from __future__ import annotations

import errno
import os
import socket
import stat
from pathlib import Path

import pytest

from cli_agent_orchestrator.services.work_bubblewrap_runtime_snapshot import (
    RuntimeSnapshotError,
    prepare_runtime_snapshot,
)


def _trees(tmp_path: Path) -> tuple[Path, Path, Path]:
    roots = tuple(tmp_path / name for name in ("runtime", "source", "deps"))
    for root in roots:
        root.mkdir()
        (root / "module.py").write_text("before")
    return roots


def test_snapshot_excludes_late_socket_and_source_mutation(tmp_path: Path) -> None:
    runtime, source, deps = _trees(tmp_path)
    with prepare_runtime_snapshot(runtime, source, deps, parent=tmp_path) as snapshot:
        listener = socket.socket(socket.AF_UNIX)
        try:
            listener.bind(str(runtime / "late.sock"))
            (source / "module.py").write_text("after")
            assert not (snapshot.runtime_path / "late.sock").exists()
            assert (snapshot.package_path / "module.py").read_text() == "before"
            assert snapshot.runtime_path != runtime
            assert snapshot.package_path != source
        finally:
            listener.close()
    assert not snapshot.path.exists()
    assert (source / "module.py").read_text() == "after"


def test_snapshot_can_copy_only_declared_top_level_runtime_and_dependency_entries(
    tmp_path: Path,
) -> None:
    runtime, source, deps = _trees(tmp_path)
    (runtime / "unused").write_text("not mounted")
    (deps / "unused.py").write_text("not imported")
    with prepare_runtime_snapshot(
        runtime,
        source,
        deps,
        parent=tmp_path,
        runtime_entries=("module.py",),
        deps_entries=("module.py",),
    ) as snapshot:
        assert (snapshot.runtime_path / "module.py").read_text() == "before"
        assert not (snapshot.runtime_path / "unused").exists()
        assert (snapshot.deps_path / "module.py").read_text() == "before"
        assert not (snapshot.deps_path / "unused.py").exists()


@pytest.mark.parametrize("kind", ["socket", "fifo"])
def test_snapshot_rejects_special_nodes_without_modifying_source(tmp_path: Path, kind: str) -> None:
    runtime, source, deps = _trees(tmp_path)
    special = runtime / "special"
    listener = None
    if kind == "socket":
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(special))
    else:
        os.mkfifo(special)
    try:
        with pytest.raises(RuntimeSnapshotError, match="unsupported"):
            prepare_runtime_snapshot(runtime, source, deps, parent=tmp_path)
        assert special.exists()
        assert (source / "module.py").read_text() == "before"
    finally:
        if listener:
            listener.close()


@pytest.mark.parametrize("kind", ["character", "block"])
def test_snapshot_rejects_device_nodes_when_runner_can_create_them(
    tmp_path: Path, kind: str
) -> None:
    runtime, source, deps = _trees(tmp_path)
    special = runtime / "device"
    mode, device = (
        (stat.S_IFCHR, os.makedev(1, 3))
        if kind == "character"
        else (stat.S_IFBLK, os.makedev(7, 0))
    )
    try:
        os.mknod(special, mode | 0o600, device)
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.EACCES, errno.EOPNOTSUPP}:
            pytest.skip(f"runner cannot create {kind} device nodes")
        raise
    try:
        with pytest.raises(RuntimeSnapshotError, match="unsupported"):
            prepare_runtime_snapshot(runtime, source, deps, parent=tmp_path)
        assert special.exists()
        assert (source / "module.py").read_text() == "before"
    finally:
        special.unlink()


def test_snapshot_preserves_relative_symlink_inside_tree(tmp_path: Path) -> None:
    runtime, source, deps = _trees(tmp_path)
    (runtime / "alias").symlink_to("module.py")
    with prepare_runtime_snapshot(runtime, source, deps, parent=tmp_path) as snapshot:
        assert (snapshot.runtime_path / "alias").is_symlink()
        assert os.readlink(snapshot.runtime_path / "alias") == "module.py"
        assert (snapshot.runtime_path / "alias").read_text() == "before"


def test_snapshot_preserves_execute_permission_without_write_permission(tmp_path: Path) -> None:
    runtime, source, deps = _trees(tmp_path)
    executable = runtime / "bin" / "python"
    executable.parent.mkdir()
    executable.write_text("runtime")
    executable.chmod(0o755)

    with prepare_runtime_snapshot(runtime, source, deps, parent=tmp_path) as snapshot:
        mode = os.stat(snapshot.runtime_path / "bin" / "python").st_mode & 0o777
        assert mode == 0o555
        assert snapshot.validate() is None


def test_snapshot_rejects_escaping_symlink_and_enforces_bounds(tmp_path: Path) -> None:
    runtime, source, deps = _trees(tmp_path)
    (runtime / "escape").symlink_to("../../outside")
    with pytest.raises(RuntimeSnapshotError, match="symlink"):
        prepare_runtime_snapshot(runtime, source, deps, parent=tmp_path)
    (runtime / "escape").unlink()
    with pytest.raises(RuntimeSnapshotError, match="byte limit"):
        prepare_runtime_snapshot(runtime, source, deps, parent=tmp_path, max_bytes=1)


def test_snapshot_enforces_file_limit_and_detects_tampering(tmp_path: Path) -> None:
    runtime, source, deps = _trees(tmp_path)
    with pytest.raises(RuntimeSnapshotError, match="file limit"):
        prepare_runtime_snapshot(runtime, source, deps, parent=tmp_path, max_files=1)
    with prepare_runtime_snapshot(runtime, source, deps, parent=tmp_path) as snapshot:
        snapshot.validate()
        (snapshot.runtime_path / "module.py").chmod(0o600)
        (snapshot.runtime_path / "module.py").write_text("tampered")
        with pytest.raises(RuntimeSnapshotError, match="changed"):
            snapshot.validate()


def test_snapshot_retained_when_child_cleanup_is_uncertain(tmp_path: Path) -> None:
    runtime, source, deps = _trees(tmp_path)
    snapshot = prepare_runtime_snapshot(runtime, source, deps, parent=tmp_path)
    snapshot.close(cleanup_confirmed=False)
    assert snapshot.path.is_dir()
    assert (snapshot.runtime_path / "module.py").read_text() == "before"
    snapshot.close(cleanup_confirmed=True)
    assert not snapshot.path.exists()
