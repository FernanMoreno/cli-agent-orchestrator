"""Exercise the shipped shell client without a server or real credential."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_showcase_authenticates_stream_by_header_without_token_in_url(tmp_path):
    curl = tmp_path / "curl"
    calls = tmp_path / "calls.jsonl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['CAO_TEST_CURL_CALLS'], 'a') as out:\n"
        "    out.write(json.dumps(args) + '\\n')\n"
        "url = next(arg for arg in args if arg.startswith('http://'))\n"
        "if '/stream' in url:\n"
        "    print('event: GENERATIVE_UI\\ndata: {}\\n\\n' * 6)\n"
        "elif '/emit_ui' in url:\n"
        "    Path(args[args.index('-o') + 1]).write_text('{}')\n"
        "    print('400' if '\"component\":\"iframe\"' in args[-1] else '200', end='')\n"
    )
    curl.chmod(0o700)
    env = dict(
        os.environ,
        PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        TMPDIR=str(tmp_path),
        CAO_AGUI_BASE="http://127.0.0.1:9889",
        CAO_TOKEN="fictional-test-bearer",
        CAO_TEST_CURL_CALLS=str(calls),
    )
    script = Path(__file__).resolve().parents[1] / "examples/ag-ui/ag-ui-dashboard/showcase.sh"
    result = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stdout + result.stderr
    requests = [json.loads(line) for line in calls.read_text().splitlines()]
    stream = next(args for args in requests if any("/stream" in arg for arg in args))
    stream_url = next(arg for arg in stream if arg.startswith("http://"))
    assert stream_url == "http://127.0.0.1:9889/agui/v1/stream"
    assert "Authorization: Bearer fictional-test-bearer" in stream
    assert all(
        "fictional-test-bearer" not in arg
        for args in requests
        for arg in args
        if arg.startswith("http://")
    )
