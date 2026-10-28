"""Actual diagnostic proxy EOF and signal outcomes, without native providers."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from test.live_provider.claude_proxy_lifecycle import LifecycleAudit, read_lifecycle

import pytest

pytestmark = pytest.mark.offline_database


def test_ledger_is_exclusive_private_and_rejects_free_form_content(tmp_path):
    path = tmp_path / "lifecycle.jsonl"
    with LifecycleAudit(path, tmp_path) as audit:
        audit.event("proxy_started")
        with pytest.raises(ValueError):
            audit.event("PRIVATE PROMPT")
        with pytest.raises(ValueError):
            audit.event("signal", status=True)
    rows = read_lifecycle(path)
    assert [r["event"] for r in rows] == ["proxy_started"]
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        LifecycleAudit(path, tmp_path)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"event":"signal","at_ns":1,"status":true}\n',
        b'{"event":"PRIVATE","at_ns":1}\n',
        b'{"event":"proxy_started","at_ns":1,"secret":"PRIVATE"}\n',
        b'{"event":"proxy_started","at_ns":1}\n{"event":"stdin_eof","at_ns":0}\n',
    ],
)
def test_reader_rejects_untyped_or_untrusted_projection(tmp_path, raw):
    path = tmp_path / "lifecycle.jsonl"
    path.write_bytes(raw)
    path.chmod(0o600)
    with pytest.raises(ValueError) as error:
        read_lifecycle(path)
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("ending", ["eof", "sigint", "sigterm", "partial"])
def test_actual_proxy_records_first_boundary_and_actual_child_wait(tmp_path, ending):
    ledger = tmp_path / "lifecycle.jsonl"
    witness = tmp_path / "witness.json"
    child_pid = tmp_path / "child.pid"
    body = """
import os,sys
from pathlib import Path
Path(sys.argv[1]).write_text(str(os.getpid()))
for line in sys.stdin: pass
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "test.live_provider.claude_denial_mcp_proxy",
            sys.executable,
            "-c",
            body,
            str(child_pid),
        ],
        env={
            **os.environ,
            "CAO_SPEC025_DENIAL_PROXY": "1",
            "CAO_SPEC025_DENIAL_ROOT": str(tmp_path),
            "CAO_SPEC025_DENIAL_WITNESS": str(witness),
            "CAO_SPEC025_DENIAL_LIFECYCLE": str(ledger),
        },
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while not child_pid.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert child_pid.exists()
        if ending in {"sigint", "sigterm"}:
            process.send_signal(signal.SIGINT if ending == "sigint" else signal.SIGTERM)
            process.wait(timeout=10)
        else:
            process.communicate(b"{" if ending == "partial" else b"", timeout=10)
        rows = read_lifecycle(ledger)
        names = [r["event"] for r in rows]
        if ending == "eof":
            assert process.returncode == 0
            assert "stdin_eof" in names and "normal_closed" in names
            assert next(r["status"] for r in rows if r["event"] == "child_wait_normal") == 0
            assert json.loads(witness.read_text())["closed_cleanly"] is True
        else:
            assert process.returncode != 0
            assert "normal_closed" not in names
            assert json.loads(witness.read_text())["closed_cleanly"] is False
            if ending != "partial":
                expected = signal.SIGINT if ending == "sigint" else signal.SIGTERM
                assert next(r["status"] for r in rows if r["event"] == "signal") == expected
            assert any(r["event"] == "child_wait_cleanup" for r in rows)
        assert not Path(f"/proc/{child_pid.read_text()}").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in [process.stdin, process.stdout, process.stderr]:
            if stream and not stream.closed:
                stream.close()
