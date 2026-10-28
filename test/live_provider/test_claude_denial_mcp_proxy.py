"""Request/response correlation, never interpreting missing transport as zero."""

import json
import os
import subprocess
import sys
from test.live_provider.claude_denial_mcp_proxy import McpWitness, _write

import pytest

pytestmark = pytest.mark.offline_database


def feed(witness, value, outbound=False):
    witness.frame(json.dumps(value).encode(), outbound=outbound)


def test_witness_counts_successful_responses_and_actual_tool_dispatch():
    witness = McpWitness()
    feed(witness, {"id": 1, "method": "initialize"})
    assert witness.counts["initialize"] == 0
    feed(witness, {"id": 9, "result": {"serverInfo": {}}}, True)
    assert witness.counts["initialize"] == 0
    feed(witness, {"id": 1, "result": {"serverInfo": {"name": "control"}}}, True)
    feed(witness, {"id": 2, "method": "tools/list"})
    feed(witness, {"id": 2, "result": {"tools": []}}, True)
    feed(witness, {"id": 3, "method": "tools/call", "params": {"name": "PRIVATE"}})
    assert witness.counts == {
        "initialize": 1,
        "tools/list": 1,
        "tools/call": 1,
        "closed_cleanly": False,
    }
    assert "PRIVATE" not in json.dumps(witness.counts)


@pytest.mark.parametrize("result", [{"error": {}}, {"result": []}, {"result": {"tools": 1}}])
def test_failed_or_malformed_list_response_does_not_count_as_connection(result):
    witness = McpWitness()
    feed(witness, {"id": 1, "method": "tools/list"})
    with pytest.raises(ValueError):
        feed(witness, {"id": 1, **result}, True)
    assert witness.counts["tools/list"] == 0


def test_witness_writer_updates_actual_private_file_without_leaking_arguments(tmp_path):
    path = tmp_path / "witness.json"
    _write(path, {"tools/call": 0})
    _write(path, {"tools/call": 1})
    assert json.loads(path.read_text()) == {"tools/call": 1}
    assert path.stat().st_mode & 0o777 == 0o600
    assert not path.with_suffix(".pending").exists()


def test_actual_proxy_forwards_owned_child_frames_and_counts_dispatch(tmp_path):
    path = tmp_path / "witness.json"
    body = """
import json, sys
for line in sys.stdin:
    request=json.loads(line)
    method=request['method']
    result={'serverInfo':{'name':'control'}} if method=='initialize' else {'tools':[]}
    print(json.dumps({'id':request['id'],'result':result}),flush=True)
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "test.live_provider.claude_denial_mcp_proxy",
            sys.executable,
            "-c",
            body,
        ],
        env={
            **os.environ,
            "CAO_SPEC025_DENIAL_PROXY": "1",
            "CAO_SPEC025_DENIAL_ROOT": str(tmp_path),
            "CAO_SPEC025_DENIAL_WITNESS": str(path),
        },
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    requests = [
        {"id": i, "method": method}
        for i, method in enumerate(["initialize", "tools/list", "tools/call"], 1)
    ]
    try:
        stdout, stderr = child.communicate(
            b"".join((json.dumps(r) + "\n").encode() for r in requests), timeout=15
        )
        assert child.returncode == 0, stderr.decode()
        assert [json.loads(line)["id"] for line in stdout.splitlines()] == [1, 2, 3]
        assert json.loads(path.read_text()) == {
            "initialize": 1,
            "tools/list": 1,
            "tools/call": 1,
            "closed_cleanly": True,
        }
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


def test_bidirectional_server_request_does_not_consume_pending_client_id():
    witness = McpWitness()
    feed(witness, {"id": 1, "method": "initialize"})
    feed(witness, {"id": 1, "method": "roots/list"}, True)
    assert witness.pending == {1: "initialize"}
    feed(witness, {"id": 1, "result": {"serverInfo": {"name": "control"}}}, True)
    assert witness.counts["initialize"] == 1
