"""Controlled EOF admission and real stdio closure, without Claude or auth."""

import json
import os
import subprocess
import sys
import time
from test.live_provider.claude_requested_eof import RequestedEof, close_mcp_then_cli, request_eof

import pytest

pytestmark = pytest.mark.offline_database
TOKEN = "caos-eof-control-nonce"


def test_only_nonce_bound_private_request_is_admitted(tmp_path):
    path = tmp_path / "request.json"
    control = RequestedEof(path, TOKEN, tmp_path)
    assert not control.requested()
    request_eof(path, TOKEN)
    assert control.requested()
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        request_eof(path, TOKEN)


@pytest.mark.parametrize(
    "body",
    [
        {"token": "foreign", "schema": "caos.mcp-eof-request.v1"},
        {"token": TOKEN, "schema": "foreign"},
        {"token": TOKEN, "schema": "caos.mcp-eof-request.v1", "extra": True},
    ],
)
def test_invalid_control_never_becomes_eof(tmp_path, body):
    path = tmp_path / "request.json"
    control = RequestedEof(path, TOKEN, tmp_path)
    path.write_text(json.dumps(body))
    path.chmod(0o600)
    with pytest.raises(ValueError):
        control.requested()


def test_both_rpc_directions_must_settle(tmp_path):
    control = RequestedEof(tmp_path / "request.json", TOKEN, tmp_path)
    control.observe({"id": 1, "method": "ping"}, outbound=False)
    assert not control.quiet()
    control.observe({"id": 1, "method": "roots/list"}, outbound=True)
    control.observe({"id": 1, "result": {}}, outbound=True)
    assert not control.quiet()
    control.observe({"id": 1, "result": {}}, outbound=False)
    assert control.quiet()


def wait_for(predicate):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("controlled_process_observation_deadline")


@pytest.mark.parametrize(
    "case", ["quiet", "pending", "partial", "late", "signal", "early_eof", "helper", "server_rpc"]
)
def test_real_proxy_eof_requires_quiescence_and_rejects_late_data(tmp_path, case):
    request = tmp_path / "mcp-eof-request.json"
    witness = tmp_path / "mcp-witness.json"
    ledger = tmp_path / "proxy-lifecycle.jsonl"
    seen = tmp_path / "seen"
    body = """
import json,sys,time
from pathlib import Path
for line in sys.stdin:
    v=json.loads(line)
    Path(sys.argv[1]).write_text('seen')
    if v['method']=='hold':time.sleep(10)
    else:print(json.dumps({'id':v['id'],'result':{}}),flush=True)
if sys.argv[2]=="server_rpc":print(json.dumps({"id":"late-server","method":"ping"}),flush=True)
time.sleep(0.4)
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "test.live_provider.claude_denial_mcp_proxy",
            sys.executable,
            "-c",
            body,
            str(seen),
            case,
        ],
        env={
            **os.environ,
            "CAO_SPEC025_DENIAL_PROXY": "1",
            "CAO_SPEC025_DENIAL_ROOT": str(tmp_path),
            "CAO_SPEC025_DENIAL_WITNESS": str(witness),
            "CAO_SPEC025_DENIAL_LIFECYCLE": str(ledger),
            "CAO_SPEC025_DENIAL_EOF_FILE": str(request),
            "CAO_SPEC025_DENIAL_EOF_TOKEN": TOKEN,
        },
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        wait_for(lambda: ledger.exists() and b"child_spawned" in ledger.read_bytes())
        if case == "pending":
            process.stdin.write(b'{"id":1,"method":"hold"}\n')
            process.stdin.flush()
            wait_for(seen.exists)
        elif case == "partial":
            process.stdin.write(b"{")
            process.stdin.flush()
            time.sleep(0.1)
        if case == "helper":
            result = close_mcp_then_cli(process, tmp_path, TOKEN)
            assert result["closed_cleanly"] is True
            assert process.stdin.closed
            return
        request_eof(request, TOKEN)
        if case in {"late", "signal", "early_eof"}:
            wait_for(lambda: b"requested_eof" in ledger.read_bytes())
            if case == "late":
                process.stdin.write(b'{"method":"late"}\n')
                process.stdin.flush()
            elif case == "early_eof":
                process.stdin.close()
            else:
                import signal

                process.send_signal(signal.SIGINT)
        process.wait(timeout=10)
        result = json.loads(witness.read_text())
        if case == "quiet":
            assert process.returncode == 0
            assert result["closed_cleanly"] is True
            assert b"requested_eof" in ledger.read_bytes()
            assert b"normal_closed" in ledger.read_bytes()
            assert not process.stdin.closed
        else:
            assert process.returncode != 0
            assert result["closed_cleanly"] is False
            assert b"normal_closed" not in ledger.read_bytes()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in [process.stdin, process.stdout, process.stderr]:
            if stream and not stream.closed:
                stream.close()
