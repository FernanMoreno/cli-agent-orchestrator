"""Focused filesystem contracts for cross-process deferred-sidecar locks."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.services import terminal_service as module

pytestmark = pytest.mark.offline_database


def _stripe(terminal_id: str) -> int:
    digest = hashlib.sha256(terminal_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % module._DEFERRED_SIDECAR_FILE_LOCK_STRIPES


def _colliding_ids(prefix: str) -> tuple[str, str, str]:
    count = module._DEFERRED_SIDECAR_FILE_LOCK_STRIPES
    seen: dict[int, str] = {}
    collision: tuple[str, str, int] | None = None
    for index in range(count + 1):
        key = f"{prefix}-{index}"
        stripe = _stripe(key)
        if stripe in seen:
            collision = (seen[stripe], key, stripe)
            break
        seen[stripe] = key
    assert collision is not None
    first, second, shared = collision
    other = next(
        f"{prefix}-other-{index}"
        for index in range(count + 1)
        if _stripe(f"{prefix}-other-{index}") != shared
    )
    return first, second, other


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (
            parent / "src" / "cli_agent_orchestrator"
        ).is_dir():
            return parent
    raise AssertionError("cannot locate repository for the isolated child")


def test_distinct_id_churn_keeps_lock_files_bounded_and_idle_registries_empty(
    tmp_path, monkeypatch
):
    root = tmp_path / "terminal-logs"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(module, "TERMINAL_LOG_DIR", root)
    assert module._deferred_sidecar_locks == {}
    assert module._deferred_sidecar_file_locks == {}

    stripe_count = module._DEFERRED_SIDECAR_FILE_LOCK_STRIPES
    for index in range(2 * stripe_count + 1):
        with module._deferred_init_sidecar_lock(f"churn-{index}"):
            assert module._deferred_sidecar_file_locks
        assert module._deferred_sidecar_locks == {}
        assert module._deferred_sidecar_file_locks == {}

    lock_dir = root / ".deferred-init-locks-v1"
    info = lock_dir.lstat()
    assert stat.S_ISDIR(info.st_mode)
    assert info.st_uid == os.getuid()
    assert stat.S_IMODE(info.st_mode) == 0o700
    lock_files = list(lock_dir.glob("*.lock"))
    assert 0 < len(lock_files) <= stripe_count
    for path in lock_files:
        file_info = path.lstat()
        assert stat.S_ISREG(file_info.st_mode)
        assert file_info.st_uid == os.getuid()
        assert file_info.st_nlink == 1
        assert stat.S_IMODE(file_info.st_mode) == 0o600
        assert len(path.stem) == 2 and all(ch in "0123456789abcdef" for ch in path.stem)
    assert module._deferred_sidecar_locks == {}
    assert module._deferred_sidecar_file_locks == {}


def test_distinct_ids_on_one_stripe_are_reentrant_in_a_bounded_child(tmp_path):
    first, second, _other = _colliding_ids("nested")
    log_dir = tmp_path / "terminal-logs"
    log_dir.mkdir(mode=0o700)
    child_home = tmp_path / "child-home"
    child_home.mkdir(mode=0o700)
    code = r"""
import sys
from pathlib import Path
from cli_agent_orchestrator.services import terminal_service as ts
ts.TERMINAL_LOG_DIR = Path(sys.argv[1])
with ts._deferred_init_sidecar_lock(sys.argv[2]):
    with ts._deferred_init_sidecar_lock(sys.argv[3]):
        print("nested-ok", flush=True)
"""
    repo = _repo_root()
    env = os.environ.copy()
    env.pop("CAO_DATABASE_CONFIG_FILE", None)
    env.pop("CAO_TEST_POSTGRES_METADATA_FILE", None)
    env["CAO_HOME_DIR"] = str(child_home)
    env["CAO_TEST_POSTGRES_AUTOSTART"] = "0"
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(repo / "src"), env.get("PYTHONPATH", "")) if value
    )
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", code, str(log_dir), first, second],
        cwd=repo,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        try:
            stdout, stderr = process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            pytest.fail("same-stripe nested lock hung its owning child")
        assert process.returncode == 0, f"child failed: {stderr[-1000:]}"
        assert stdout.strip() == "nested-ok"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_bulk_acquires_real_unique_stripes_in_sorted_order_and_coalesces_collisions(
    tmp_path, monkeypatch
):
    root = tmp_path / "terminal-logs"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(module, "TERMINAL_LOG_DIR", root)
    first, second, other = _colliding_ids("bulk")
    requested = [other, second, first, second, other]
    expected = sorted({_stripe(key) for key in requested})
    assert _stripe(first) == _stripe(second)
    assert len(expected) < len(set(requested))

    acquired: list[int] = []
    real_file_lock = module._deferred_sidecar_file_lock

    @contextmanager
    def observed_file_lock(directory, identity, stripe):
        with real_file_lock(directory, identity, stripe):
            acquired.append(stripe)
            yield

    monkeypatch.setattr(module, "_deferred_sidecar_file_lock", observed_file_lock)
    delete_rows = Mock(return_value=7)
    assert module._delete_terminal_rows_with_sidecars(requested, delete_rows=delete_rows) == 7
    assert acquired == expected
    delete_rows.assert_called_once_with(requested)
    assert module._deferred_sidecar_locks == {}
    assert module._deferred_sidecar_file_locks == {}


@pytest.mark.parametrize("failure_point", ["open", "flock"])
@pytest.mark.parametrize("operation", ["publish_failure", "publish_complete", "delete", "bulk"])
def test_guard_failure_refuses_mutation_and_preserves_existing_evidence(
    failure_point, operation, tmp_path, monkeypatch
):
    root = tmp_path / "terminal-logs"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(module, "TERMINAL_LOG_DIR", root)
    terminal_id = f"guard-{failure_point}-{operation}"
    failure_path = module._deferred_failure_fallback_path(terminal_id)
    complete_path = module._deferred_init_complete_fallback_path(terminal_id)
    failure_path.write_bytes(b"existing-failure")
    complete_path.write_bytes(b"existing-completion\n")
    before = failure_path.read_bytes(), complete_path.read_bytes()
    lock_dir = root / ".deferred-init-locks-v1"
    lock_dir.mkdir(mode=0o700)
    os.chmod(lock_dir, 0o700)
    lock_file = lock_dir / f"{_stripe(terminal_id):02x}.lock"
    lock_file.touch(mode=0o600)
    os.chmod(lock_file, 0o600)

    if failure_point == "open":
        real_os = module.os

        class OpenFailure:
            def __getattr__(self, name):
                return getattr(real_os, name)

            def open(self, path, *args, **kwargs):
                if Path(path).parent == lock_dir:
                    raise OSError("controlled stripe open failure")
                return real_os.open(path, *args, **kwargs)

        monkeypatch.setattr(module, "os", OpenFailure())
    else:
        real_fcntl = module.fcntl

        class FlockFailure:
            LOCK_EX = real_fcntl.LOCK_EX

            @staticmethod
            def flock(_fd, _operation):
                raise OSError("controlled stripe flock failure")

        monkeypatch.setattr(module, "fcntl", FlockFailure())

    assert module._deferred_sidecar_locks == {}
    assert module._deferred_sidecar_file_locks == {}
    if operation.startswith("publish_"):
        monkeypatch.setattr(module, "get_terminal_metadata", lambda _key: {"id": terminal_id})
        write = Mock(return_value=True)
        if operation == "publish_failure":
            monkeypatch.setattr(module, "_write_deferred_failure_fallback", write)
            assert (
                module._publish_deferred_failure_fallback(
                    terminal_id, {"phase": "deferred_init", "kind": "controlled"}
                )
                is False
            )
        else:
            monkeypatch.setattr(module, "_write_deferred_init_complete_fallback", write)
            assert module._publish_deferred_init_complete_fallback(terminal_id) is False
        write.assert_not_called()
    elif operation == "delete":
        monkeypatch.setattr(
            module,
            "provider_manager",
            SimpleNamespace(retirement_fence=lambda _key: nullcontext(True)),
        )
        delete_row = Mock(return_value=True)
        monkeypatch.setattr(module, "db_delete_terminal", delete_row)
        with pytest.raises(OSError, match="controlled stripe"):
            module.delete_terminal_row(terminal_id, {"engine": "mock_cli"}, registry=None)
        delete_row.assert_not_called()
    else:
        delete_rows = Mock(return_value=1)
        with pytest.raises(OSError, match="controlled stripe"):
            module._delete_terminal_rows_with_sidecars([terminal_id], delete_rows=delete_rows)
        delete_rows.assert_not_called()

    assert (failure_path.read_bytes(), complete_path.read_bytes()) == before
    assert module._deferred_sidecar_locks == {}
    assert module._deferred_sidecar_file_locks == {}


def test_acl_filesystem_mode_reporting_keeps_real_kernel_coordination(tmp_path, monkeypatch):
    root = tmp_path / "acl-terminal-logs"
    root.mkdir(mode=0o700)
    lock_dir = root / ".deferred-init-locks-v1"
    monkeypatch.setattr(module, "TERMINAL_LOG_DIR", root)
    real_os = module.os
    real_lstat = Path.lstat
    real_flock = module.fcntl.flock
    acquired = []

    def reported_mode(info):
        fields = list(info)
        fields[0] = (info.st_mode & ~0o777) | 0o777
        return real_os.stat_result(fields)

    def acl_lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        return reported_mode(info) if path == lock_dir else info

    class AclReportedOs:
        def __getattr__(self, name):
            return getattr(real_os, name)

        def fstat(self, fd):
            return reported_mode(real_os.fstat(fd))

    def observed_flock(fd, operation):
        result = real_flock(fd, operation)
        acquired.append(operation)
        return result

    monkeypatch.setattr(Path, "lstat", acl_lstat)
    monkeypatch.setattr(module, "os", AclReportedOs())
    monkeypatch.setattr(module.fcntl, "flock", observed_flock)
    with module._deferred_init_sidecar_lock("acl-reporting"):
        assert len(module._deferred_sidecar_file_locks) == 1
    assert acquired == [module.fcntl.LOCK_EX]
    assert module._deferred_sidecar_file_locks == {}
    assert module._deferred_sidecar_locks == {}
