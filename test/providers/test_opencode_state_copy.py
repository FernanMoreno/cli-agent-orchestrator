"""Synthetic filesystem failures must not block launch or leak owned resources."""

import asyncio
import errno
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cli_agent_orchestrator.providers import opencode_cli


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFOs")
@pytest.mark.parametrize("boundary", ["metadata", "file", "bundle"])
def test_state_copy_rejects_fifo_without_waiting_for_writer(tmp_path, boundary):
    source = tmp_path / "state.db"
    fifo = source if boundary != "bundle" else Path(str(source) + "-wal")
    if boundary == "bundle":
        source.write_bytes(b"synthetic database")
    os.mkfifo(fifo)
    script = """
import sys
from pathlib import Path
from cli_agent_orchestrator.providers import opencode_cli as module
boundary, source, destination = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
try:
    if boundary == "metadata":
        module._state_file_metadata(source)
    elif boundary == "file":
        module._copy_private_state_file(source, destination / "state.db")
    else:
        module._copy_private_state_bundle(source, destination)
except RuntimeError as exc:
    assert str(exc) == "OpenCode credential state must be a regular file"
else:
    raise AssertionError("FIFO source was accepted")
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, boundary, str(source), str(tmp_path / "private")],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{boundary} blocked on a FIFO with no writer")
    assert result.returncode == 0, result.stderr
    assert not list((tmp_path / "private").glob("*"))
    if boundary == "bundle":
        assert source.read_bytes() == b"synthetic database"


@pytest.mark.parametrize("failure", ["parent", "permission", "copy", "cancel"])
def test_state_copy_closes_descriptors_after_setup_failure(tmp_path, monkeypatch, failure):
    source = tmp_path / "source"
    source.write_bytes(b"synthetic auth")
    parent = tmp_path / "private"
    if failure == "parent":
        parent.write_bytes(b"existing parent")
    destination = parent / "state"
    opened = []
    original_open = os.open

    def track_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def reject_permissions(*_args):
        raise PermissionError("synthetic chmod failure")

    def reject_copy(_reader, writer):
        writer.write(b"partial copy")
        if failure == "cancel":
            raise asyncio.CancelledError()
        raise OSError("synthetic write failure")

    monkeypatch.setattr(opencode_cli.os, "open", track_open)
    if failure == "permission":
        monkeypatch.setattr(opencode_cli.os, "fchmod", reject_permissions)
    if failure in {"copy", "cancel"}:
        monkeypatch.setattr(opencode_cli.shutil, "copyfileobj", reject_copy)
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else OSError):
        opencode_cli._copy_private_state_file(source, destination)
    leaked = []
    for descriptor in opened:
        try:
            os.fstat(descriptor)
        except OSError as exc:
            assert exc.errno == errno.EBADF
        else:
            leaked.append(descriptor)
            os.close(descriptor)  # Keep the regression itself leak-free on RED.
    assert not leaked, f"setup failure leaked {len(leaked)} descriptors"
    assert source.read_bytes() == b"synthetic auth"
    if failure == "parent":
        assert parent.read_bytes() == b"existing parent"
    else:
        assert not destination.exists()


def test_state_bundle_retries_a_changed_wal_as_one_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "custom.db"
    source.write_bytes(b"synthetic database")
    wal = Path(str(source) + "-wal")
    wal.write_bytes(b"first wal")
    destination = tmp_path / "private"
    copy = opencode_cli._copy_private_state_file
    copied = []

    def change_wal_after_first_copy(path, target):
        result = copy(path, target)
        copied.append(path)
        if len(copied) == 2:
            wal.write_bytes(b"new stable wal")
        return result

    monkeypatch.setattr(opencode_cli, "_copy_private_state_file", change_wal_after_first_copy)
    opencode_cli._copy_private_state_bundle(source, destination)
    assert copied == [source, wal, source, wal]
    assert (destination / "opencode.db").read_bytes() == source.read_bytes()
    assert (destination / "opencode.db-wal").read_bytes() == b"new stable wal"


def test_state_bundle_cleans_partial_copies_after_retry_exhaustion(tmp_path, monkeypatch):
    source = tmp_path / "custom.db"
    source.write_bytes(b"synthetic database")
    wal = Path(str(source) + "-wal")
    wal.write_bytes(b"synthetic wal")
    destination = tmp_path / "private"
    copy = opencode_cli._copy_private_state_file
    attempts = []

    def reject_wal(path, target):
        if path == wal:
            attempts.append(path)
            raise RuntimeError("synthetic WAL read failure")
        return copy(path, target)

    monkeypatch.setattr(opencode_cli, "_copy_private_state_file", reject_wal)
    with pytest.raises(RuntimeError, match="synthetic WAL read failure"):
        opencode_cli._copy_private_state_bundle(source, destination)
    assert len(attempts) == 3
    assert not list(destination.iterdir())
    assert source.read_bytes() == b"synthetic database"
    assert wal.read_bytes() == b"synthetic wal"


@pytest.mark.parametrize("boundary", ["file", "bundle"])
@pytest.mark.parametrize("destination_kind", ["regular", "symlink"])
def test_state_copy_preserves_preexisting_destination(tmp_path, boundary, destination_kind):
    source = tmp_path / "source"
    source.write_bytes(b"synthetic auth")
    private = tmp_path / "private"
    private.mkdir()
    destination = private / "opencode.db"
    existing = tmp_path / "existing"
    existing.write_bytes(b"existing destination")
    if destination_kind == "symlink":
        destination.symlink_to(existing)
    else:
        destination.write_bytes(b"existing destination")
    with pytest.raises(FileExistsError):
        if boundary == "bundle":
            opencode_cli._copy_private_state_bundle(source, private)
        else:
            opencode_cli._copy_private_state_file(source, destination)
    assert destination.read_bytes() == b"existing destination"
    assert destination.is_symlink() == (destination_kind == "symlink")
    assert existing.read_bytes() == b"existing destination"
    assert source.read_bytes() == b"synthetic auth"
