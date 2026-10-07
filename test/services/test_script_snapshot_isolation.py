"""Owned script snapshots must remain independent even for the same run ID."""

import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cli_agent_orchestrator.services import script_runner


def test_same_run_snapshots_keep_distinct_sources_and_independent_cleanup(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(script_runner, "WORKFLOW_SCRIPT_SCRATCH_DIR", scratch)
    first = Path(script_runner._materialize_snapshot("same-run", "print('first')\n"))
    second = Path(script_runner._materialize_snapshot("same-run", "print('second')\n"))
    try:
        assert first != second, "same run ID overwrote another owner's snapshot"
        assert (
            subprocess.check_output([sys.executable, str(first)], text=True, timeout=5).strip()
            == "first"
        )
        assert (
            subprocess.check_output([sys.executable, str(second)], text=True, timeout=5).strip()
            == "second"
        )
        if os.name == "posix":
            assert stat.S_IMODE(first.stat().st_mode) == 0o600
            assert stat.S_IMODE(scratch.stat().st_mode) == 0o700
        script_runner._delete_temp_file(str(first))
        assert second.read_text() == "print('second')\n"
    finally:
        script_runner._delete_temp_file(str(first))
        script_runner._delete_temp_file(str(second))


def test_concurrent_same_run_materializations_are_exclusive(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(script_runner, "WORKFLOW_SCRIPT_SCRATCH_DIR", scratch)

    def materialize(index):
        source = f"print({index})\n"
        return Path(script_runner._materialize_snapshot("shared-run", source)), source

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(materialize, range(16)))
    try:
        assert len({str(path) for path, _ in rows}) == 16
        assert all(path.read_text() == source for path, source in rows)
    finally:
        for path, _ in rows:
            script_runner._delete_temp_file(str(path))


def test_failed_fd_wrapper_leaves_no_snapshot_or_open_descriptor(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(script_runner, "WORKFLOW_SCRIPT_SCRATCH_DIR", scratch)
    captured = []

    def refused(fd, *_args, **_kwargs):
        captured.append(fd)
        raise OSError("test wrapping refusal")

    monkeypatch.setattr(script_runner.os, "fdopen", refused)
    with pytest.raises(OSError, match="test wrapping refusal"):
        script_runner._materialize_snapshot("failed-run", "print('never')\n")
    assert not list(scratch.iterdir())
    with pytest.raises(OSError):
        os.fstat(captured[0])
