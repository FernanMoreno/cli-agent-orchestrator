"""Offline regression for the T097 partial-line ready handshake."""

from __future__ import annotations

import importlib.util
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline_database

_HELPER_PATH = Path(__file__).with_name("bounded_ready_line.py")
_SPEC = importlib.util.spec_from_file_location("t097_bounded_ready_line", _HELPER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_HELPER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_HELPER)
read_newline_frame = _HELPER.read_newline_frame


def test_partial_ready_line_times_out_while_child_keeps_pipe_open() -> None:
    child_code = "import os,time; os.write(1,b'{\"ready\":'); time.sleep(30)"
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", child_code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    assert child.stdout is not None
    finished = threading.Event()
    result: list[bytes | None] = []
    worker_errors: list[str] = []

    def read_in_owned_worker() -> None:
        try:
            result.append(read_newline_frame(child.stdout, timeout=0.2))
        except Exception as error:
            worker_errors.append(type(error).__name__)
        finally:
            finished.set()

    worker = threading.Thread(target=read_in_owned_worker, daemon=True)
    try:
        ready, _, _ = select.select([child.stdout], [], [], 3)
        assert ready, "owned child did not publish the partial line"
        assert child.poll() is None, "owned child exited before the deadline check"
        started = time.monotonic()
        worker.start()
        assert finished.wait(timeout=2.0), "newline reader exceeded its explicit test bound"
        elapsed = time.monotonic() - started
        assert elapsed < 2.0
        assert not worker_errors
        assert result == [None]
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=2)
        if worker.ident is not None:
            worker.join(timeout=2)
        child.stdout.close()
    assert not worker.is_alive(), "owned reader worker did not stop after child cleanup"
