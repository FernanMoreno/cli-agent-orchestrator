"""Run the U6 examples through real prepared-plan approval and script children.

The public prepare, review, approve, and start routes run over an isolated
PostgreSQL Work fixture. The scripts themselves are real child processes; a
small local HTTP peer records ``/terminals/run-step`` requests so assertions
can verify the example's actual control flow without invoking a provider CLI.
The raw-HTTP example still ships without a ``cao_workflow`` import; a test-only
adapter passes its run capability from the inherited descriptor to urllib.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from test.e2e.prepared_script_helpers import run_prepared_script
from test.integration.test_work_dispatch import context  # noqa: F401

import pytest

from cli_agent_orchestrator.models.workflow_runtime import RunState

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.postgres]

_EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "docs" / "examples"


class _RunStepFakeHandler(BaseHTTPRequestHandler):
    """Records every POST body and answers 200 with a canned RunStepResponse."""

    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler's naming convention
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        body = json.loads(raw.decode("utf-8"))
        self.server.recorded_calls.append(body)  # type: ignore[attr-defined]
        capability = self.headers.get("X-CAO-Workflow-Run-Credential")
        self.server.capability_lengths.append(  # type: ignore[attr-defined]
            None if capability is None else len(capability)
        )

        step_id = body.get("env_vars", {}).get("CAO_WORKFLOW_STEP_ID", "unknown")
        response = json.dumps(
            {
                "terminal_id": f"term-{step_id}",
                "last_message": f"ack:{step_id}",
                "status": "COMPLETED",
                "replayed": False,
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format, *args):  # noqa: A002 — silence default stderr logging
        return


@pytest.fixture
def fake_run_step_server():
    server = HTTPServer(("127.0.0.1", 0), _RunStepFakeHandler)
    server.recorded_calls = []  # type: ignore[attr-defined]
    server.capability_lengths = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _reset_workflow_state():
    from cli_agent_orchestrator.services import workflow_service

    workflow_service.run_registry.clear()
    workflow_service._active_drives.clear()
    yield


@pytest.fixture
def _api_base_url(fake_run_step_server, monkeypatch):
    host, port = fake_run_step_server.server_address
    base_url = f"http://{host}:{port}"
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.script_runner.API_BASE_URL", base_url, raising=True
    )
    return base_url


def _assert_prepared_capabilities(server, expected_calls):
    assert len(server.capability_lengths) == expected_calls
    assert all(32 <= length <= 256 for length in server.capability_lengths)


def _assert_completed(result):
    failures = [
        (step.step_id, step.state.value, step.error)
        for step in result.steps
        if step.error is not None
    ]
    assert result.state == RunState.COMPLETED, {
        "kind": result.kind,
        "failed_steps": failures,
    }


async def test_loop_example_records_n_iterations(
    context, tmp_path, monkeypatch, _api_base_url, fake_run_step_server
):
    result = await run_prepared_script(
        context,
        tmp_path,
        monkeypatch,
        source=(_EXAMPLES_DIR / "loop_example.py").read_text(encoding="utf-8"),
        run_id="e2e-loop",
        agent_profile="reviewer",
    )

    _assert_completed(result)
    assert result.output["iterations"] == 3
    assert len(result.output["outputs"]) == 3
    step_ids = [c["env_vars"]["CAO_WORKFLOW_STEP_ID"] for c in fake_run_step_server.recorded_calls]
    assert step_ids == ["call-1", "call-2", "call-3"]
    _assert_prepared_capabilities(fake_run_step_server, 3)


async def test_conditional_example_runs_only_the_taken_branch(
    context, tmp_path, monkeypatch, _api_base_url, fake_run_step_server
):
    """IS_URGENT is a script-level constant (no env passthrough exists for
    this — U4's spawn env is a fixed identity-only allowlist), so this test
    exercises the file's real content (IS_URGENT = True) as shipped."""
    result = await run_prepared_script(
        context,
        tmp_path,
        monkeypatch,
        source=(_EXAMPLES_DIR / "conditional_example.py").read_text(encoding="utf-8"),
        run_id="e2e-conditional",
        agent_profile="reviewer",
    )

    _assert_completed(result)
    assert result.output["branch_taken"] == "urgent"
    step_ids = [c["env_vars"]["CAO_WORKFLOW_STEP_ID"] for c in fake_run_step_server.recorded_calls]
    assert step_ids == ["urgent-branch"]  # the routine branch's step never ran
    _assert_prepared_capabilities(fake_run_step_server, 1)


async def test_fanout_example_records_distinct_step_ids_per_shard(
    context, tmp_path, monkeypatch, _api_base_url, fake_run_step_server
):
    result = await run_prepared_script(
        context,
        tmp_path,
        monkeypatch,
        source=(_EXAMPLES_DIR / "fanout_example.py").read_text(encoding="utf-8"),
        run_id="e2e-fanout",
        agent_profile="reviewer",
    )

    _assert_completed(result)
    assert set(result.output["shards"].keys()) == {"alpha", "beta", "gamma"}
    step_ids = [c["env_vars"]["CAO_WORKFLOW_STEP_ID"] for c in fake_run_step_server.recorded_calls]
    assert sorted(step_ids) == ["shard-alpha", "shard-beta", "shard-gamma"]
    assert len(set(step_ids)) == 3  # pairwise distinct
    _assert_prepared_capabilities(fake_run_step_server, 3)


async def test_loop_raw_http_example_works_without_the_shim(
    context, tmp_path, monkeypatch, _api_base_url, fake_run_step_server
):
    """Q2=A shim-optionality proof: same shape, zero cao_workflow import."""
    original_source = (_EXAMPLES_DIR / "loop_raw_http_example.py").read_text(encoding="utf-8")
    result = await run_prepared_script(
        context,
        tmp_path,
        monkeypatch,
        source=original_source,
        run_id="e2e-loop-raw-http",
        agent_profile="reviewer",
        raw_http_capability_adapter=True,
    )

    _assert_completed(result)
    assert result.output["iterations"] == 3
    assert len(result.output["outputs"]) == 3
    assert "import cao_workflow" not in original_source
    _assert_prepared_capabilities(fake_run_step_server, 3)
