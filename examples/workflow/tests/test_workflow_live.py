"""Gated live-provider test for the workflow example (issue #591).

Exercises a REAL ``cao-server`` and a REAL, authenticated ``claude_code`` CLI
end to end (or CAO_LIVE_WORKFLOW_PROVIDER=codex/opencode_cli with the authorized
subscription/free model) — the one thing ``test_workflow_example.py`` deliberately does
NOT do (it fakes the ``/terminals/run-step`` transport so it can run without
credentials). This test is expensive, needs a real provider login, and must
never run in CI or by default — it is gated the same way the existing
provider integration suites are:

    CAO_RUN_LIVE_PROVIDER_TESTS=1 pytest examples/workflow/tests/test_workflow_live.py -v

Same env var and skip pattern as ``test/providers/test_kiro_cli_integration.py``
/ ``test/providers/test_codex_provider_unit.py``, plus the ``live_provider``
marker (registered in pyproject.toml) so it is identifiable independent of
the env-var gate.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from test.fixtures.live_workflow import live_workflow_server

import pytest

pytestmark = [
    pytest.mark.live_provider,
    pytest.mark.skipif(
        os.environ.get("CAO_RUN_LIVE_PROVIDER_TESTS", "") != "1",
        reason="Live provider tests disabled. Set CAO_RUN_LIVE_PROVIDER_TESTS=1 to enable.",
    ),
]

_WORKFLOW_PATH = Path(__file__).resolve().parents[1] / "workflow.py"
_RUN_TIMEOUT = 900.0  # a real plan step + up to 2 concurrent checks, headless


@pytest.fixture
def live_cao_server(live_workflow_server):
    server = live_workflow_server
    home = server.home_dir / ".aws" / "cli-agent-orchestrator"
    env = {
        **os.environ,
        "HOME": str(server.home_dir),
        "CAO_HOME_DIR": str(home),
        "CAO_API_HOST": "127.0.0.1",
        "CAO_API_PORT": str(server.port),
    }
    yield home, env


def test_workflow_runs_end_to_end_with_a_real_provider(live_cao_server):
    """validate -> run --wait against the real server + a real claude_code step."""
    home, cli_env = live_cao_server
    workflows_dir = home / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    installed_path = workflows_dir / "workflow.py"
    provider = os.environ.get("CAO_LIVE_WORKFLOW_PROVIDER", "claude_code")
    source = _WORKFLOW_PATH.read_text(encoding="utf-8")
    installed_path.write_text(
        source.replace('"claude_code"', json.dumps(provider)), encoding="utf-8"
    )

    validate = subprocess.run(
        ["cao", "workflow", "validate", str(installed_path)],
        env=cli_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert validate.returncode == 0, validate.stdout + validate.stderr

    run = subprocess.run(
        [
            "cao",
            "workflow",
            "run",
            "workflow",
            "--run-id",
            "live-workflow-example",
            "--input",
            "target=myapp",
            "--wait",
            "--json",
        ],
        env=cli_env,
        capture_output=True,
        text=True,
        timeout=_RUN_TIMEOUT,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads(run.stdout)
    assert result["state"] == "completed"
    assert result["output"]["target"] == "myapp"
    assert result["output"]["failed_checks"] == []
