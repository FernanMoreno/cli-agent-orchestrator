"""T162: a publisher and row retirement share PostgreSQL and sidecar files."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.services import terminal_service as ts


def test_default_sidecar_io_uses_the_private_test_log_root(tmp_path_factory):
    assert ts.TERMINAL_LOG_DIR == constants.TERMINAL_LOG_DIR
    assert ts.TERMINAL_LOG_DIR.is_relative_to(tmp_path_factory.getbasetemp())
    key = "t162-isolated-logs"
    db.create_terminal(key, "cao-t162-isolated", "worker", "kimi_cli")
    assert ts._publish_deferred_failure_fallback(key, {"kind": "controlled"})
    assert ts._deferred_failure_fallback_path(key).is_file()


_PUBLISHER = r"""
import json, os, sys
from pathlib import Path
from cli_agent_orchestrator.services import terminal_service as ts

terminal_id, kind, log_dir = sys.argv[1:4]
ts.TERMINAL_LOG_DIR = Path(log_dir)

def emit(event, **fields):
    print(json.dumps({"event": event, "pid": os.getpid(), **fields}), flush=True)

real_get = ts.get_terminal_metadata
def observed_get(key):
    row = real_get(key)
    emit("metadata_check", present=row is not None)
    return row
ts.get_terminal_metadata = observed_get

def wait_then_write(write):
    def paused(*args):
        emit("write_callback_entered")
        if sys.stdin.buffer.readline() != b"resume\n":
            raise RuntimeError("parent did not release publisher")
        emit("write_callback_resuming")
        result = write(*args)
        emit("write_callback_returned", written=bool(result))
        return result
    return paused

try:
    if kind == "failure":
        real_write = ts._write_deferred_failure_fallback
        ts._write_deferred_failure_fallback = wait_then_write(real_write)
        published = ts._publish_deferred_failure_fallback(
            terminal_id,
            {"phase": "deferred_init", "kind": "provider_init_error", "message": "t162"},
        )
    elif kind == "complete":
        real_write = ts._write_deferred_init_complete_fallback
        ts._write_deferred_init_complete_fallback = wait_then_write(real_write)
        published = ts._publish_deferred_init_complete_fallback(terminal_id)
    else:
        raise ValueError("unknown sidecar kind")
    emit("publish_returned", published=bool(published))
except BaseException as exc:
    emit("worker_error", error_type=type(exc).__name__)
    raise SystemExit(2)
"""

_DELETER = r"""
import fcntl, json, os, sys
from pathlib import Path
from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.services import terminal_service as ts

terminal_id, log_dir, retirement = sys.argv[1:4]
ts.TERMINAL_LOG_DIR = Path(log_dir)

def emit(event, **fields):
    print(json.dumps({"event": event, "pid": os.getpid(), **fields}), flush=True)

try:
    emit("delete_started")
    real_flock = fcntl.flock
    def observe_contention(fd, operation):
        if operation == fcntl.LOCK_EX:
            try:
                return real_flock(fd, operation | fcntl.LOCK_NB)
            except BlockingIOError:
                emit("coordination_blocked")
        return real_flock(fd, operation)
    fcntl.flock = observe_contention
    if retirement == "single":
        deleted = ts.delete_terminal_row(terminal_id, None, registry=None)
    elif retirement == "bulk":
        deleted = ts._delete_terminal_rows_with_sidecars(
            [terminal_id],
            delete_rows=lambda ids: sum(db.delete_terminal(key) for key in ids),
        )
    else:
        raise ValueError("unknown retirement path")
    emit("delete_returned", deleted=bool(deleted))
except BaseException as exc:
    emit("worker_error", error_type=type(exc).__name__)
    raise SystemExit(2)
"""


class _JsonPipe:
    """Read flushed child events without text-wrapper read-ahead hiding lines."""

    def __init__(self, stream):
        self.fd = stream.fileno()
        self.buffer = bytearray()

    def read(self, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([self.fd], [], [], remaining)
            if not ready:
                return None
            chunk = os.read(self.fd, 4096)
            if not chunk:
                return None
            self.buffer.extend(chunk)
        line, _, rest = self.buffer.partition(b"\n")
        self.buffer = bytearray(rest)
        return json.loads(line)


def _expect_event(reader: _JsonPipe, name: str, *, timeout: float = 10) -> dict:
    event = reader.read(timeout)
    assert event is not None, f"timed out waiting for child event {name}"
    assert event.get("event") != "worker_error", f"child raised {event.get('error_type')}"
    assert event.get("event") == name, f"expected {name}, got {event.get('event')}"
    return event


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (
            parent / "src" / "cli_agent_orchestrator"
        ).is_dir():
            return parent
    raise AssertionError("cannot locate the repository for isolated workers")


def _start_worker(code: str, args: list[str], *, repo: Path, env: dict[str, str]):
    return subprocess.Popen(
        [sys.executable, "-u", "-c", code, *args],
        cwd=repo,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )


def _release_and_reap(processes: list[subprocess.Popen], publisher) -> None:
    if publisher is not None and publisher.poll() is None and publisher.stdin is not None:
        try:
            publisher.stdin.write(b"resume\n")
            publisher.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
    for process in processes:
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


@pytest.mark.parametrize("kind", ["failure", "complete"])
@pytest.mark.parametrize("retirement", ["single", "bulk"])
def test_cross_process_late_fallback_cannot_survive_acknowledged_delete(
    kind, retirement, tmp_path, monkeypatch, postgres_worker_targets
):
    """Serialize actual check/write and actual delete/unlink across processes.

    The existing sequential deleted-row control remains in
    test_deferred_restart_recovery.py. Its read-outage preservation control is
    test_bulk_deferred_sidecar_retirement.py; this regression only adds the
    missing shared-filesystem/process boundary.
    """
    assert db.engine.dialect.name == "postgresql"
    targets = postgres_worker_targets
    assert targets.config_file.is_file() and targets.home.is_dir()
    monkeypatch.setenv("CAO_DATABASE_CONFIG_FILE", str(targets.config_file))
    monkeypatch.setenv("CAO_HOME_DIR", str(targets.home))
    monkeypatch.setenv("CAO_TEST_POSTGRES_AUTOSTART", "0")

    repo = _repo_root()
    terminal_id = f"t162-{os.getpid()}-{time.monotonic_ns():x}"
    log_dir = tmp_path / "shared-terminal-logs"
    log_dir.mkdir(mode=0o700)
    os.chmod(log_dir, 0o700)
    monkeypatch.setattr(ts, "TERMINAL_LOG_DIR", log_dir)

    db.create_terminal(
        terminal_id,
        "cao-t162-sidecar",
        f"window-{terminal_id}",
        "kimi_cli",
        deferred_init_external_owner=True,
    )

    failure_path = ts._deferred_failure_fallback_path(terminal_id)
    complete_path = ts._deferred_init_complete_fallback_path(terminal_id)
    assert not failure_path.exists() and not complete_path.exists()

    worker_env = os.environ.copy()
    worker_env["CAO_DATABASE_CONFIG_FILE"] = str(targets.config_file)
    worker_env["CAO_HOME_DIR"] = str(targets.home)
    worker_env["CAO_TEST_POSTGRES_AUTOSTART"] = "0"
    worker_env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(repo / "src"), worker_env.get("PYTHONPATH", "")) if value
    )

    publisher = deleter = None
    processes: list[subprocess.Popen] = []
    delete_completed_before_release = False
    try:
        publisher = _start_worker(
            _PUBLISHER, [terminal_id, kind, str(log_dir)], repo=repo, env=worker_env
        )
        processes.append(publisher)
        publisher_events = _JsonPipe(publisher.stdout)
        metadata_check = _expect_event(publisher_events, "metadata_check")
        assert metadata_check["present"] is True
        callback = _expect_event(publisher_events, "write_callback_entered")
        assert callback["pid"] == metadata_check["pid"]
        assert db.get_terminal_metadata(terminal_id) is not None

        deleter = _start_worker(
            _DELETER, [terminal_id, str(log_dir), retirement], repo=repo, env=worker_env
        )
        processes.append(deleter)
        deleter_events = _JsonPipe(deleter.stdout)
        started = _expect_event(deleter_events, "delete_started")
        assert started["pid"] != callback["pid"]

        # Observe actual kernel contention before releasing the publisher.
        # The unfenced baseline returns from DELETE instead; no scheduling
        # timeout may masquerade as proof of serialization.
        deletion_event = deleter_events.read(timeout=10)
        assert deletion_event is not None, "deleter made no observable progress"
        assert deletion_event.get("event") in {"coordination_blocked", "delete_returned"}
        if deletion_event["event"] == "delete_returned":
            assert deletion_event["deleted"] is True
            delete_completed_before_release = True
        else:
            assert deleter.poll() is None, "blocked deleter unexpectedly exited"

        assert publisher.stdin is not None
        publisher.stdin.write(b"resume\n")
        publisher.stdin.flush()
        _expect_event(publisher_events, "write_callback_resuming")
        written = _expect_event(publisher_events, "write_callback_returned")
        assert written["written"] is True
        published = _expect_event(publisher_events, "publish_returned")
        assert published["published"] is True

        if not delete_completed_before_release:
            deletion_event = _expect_event(deleter_events, "delete_returned")
            assert deletion_event["deleted"] is True

        assert publisher.wait(timeout=10) == 0
        assert deleter.wait(timeout=10) == 0
        assert db.get_terminal_metadata(terminal_id) is None
        assert not failure_path.exists() and not complete_path.exists(), (
            f"{kind} sidecar survived acknowledged deletion; "
            f"delete_completed_before_release={delete_completed_before_release}"
        )
        assert (
            not delete_completed_before_release
        ), "retirement must wait for the publisher's positive-check critical section"
    finally:
        _release_and_reap(processes, publisher)
        if db.get_terminal_metadata(terminal_id) is not None:
            ts.delete_terminal_row(terminal_id, None, registry=None)
        failure_path.unlink(missing_ok=True)
        complete_path.unlink(missing_ok=True)
