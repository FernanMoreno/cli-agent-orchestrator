"""End-to-end proofs for approved U4 ScriptRunner child-process behavior.

These exercise the REAL prepare/review/approve/start routes and
``asyncio.create_subprocess_exec`` path — the unit suite
(``test/services/test_script_runner.py``) mocks the subprocess, so these are
the practical proofs the code-generation test plan calls for:

- M1 fan-out shape: a real script that prints a ``CAO_WORKFLOW_OUTPUT:`` sentinel
  completes with the parsed output (loop/conditional/fan-out M1 shapes reduce to
  "a real Python process runs, prints a sentinel, exits 0").
- M2 chatty-child > 1 MiB: a real process writing more than the OS pipe buffer to
  stdout drains concurrently WITHOUT deadlock (the whole reason both pipes are
  pumped by dedicated reader tasks).
- real hang-then-reap: a real ``time.sleep`` child is reaped at the wall-clock
  bound and returns ``FAILED, kind=timeout`` within the bound + grace.

Excluded from the default matrix (``-m 'not e2e'``). A local HTTP peer stands
in for the provider's ``/terminals/run-step`` route, while the scripts remain
real subprocesses. Run with:
``uv run pytest -m e2e test/e2e/script_runner/test_script_runner_e2e.py``.
"""

from __future__ import annotations

from test.e2e.prepared_script_helpers import run_prepared_script
from test.integration.test_work_dispatch import context  # noqa: F401

import pytest

from cli_agent_orchestrator.models.workflow_runtime import RunState
from cli_agent_orchestrator.services import script_runner, workflow_journal

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.postgres]


@pytest.fixture(autouse=True)
def _reset_workflow_state():
    from cli_agent_orchestrator.services import workflow_service

    workflow_service.run_registry.clear()
    workflow_service._active_drives.clear()
    yield


async def test_real_spawn_completes_with_sentinel(context, tmp_path, monkeypatch):
    """M1: a real Python process prints a sentinel and exits 0 -> COMPLETED."""
    result = await run_prepared_script(
        context,
        tmp_path,
        monkeypatch,
        source='import json\nprint("CAO_WORKFLOW_OUTPUT:" + json.dumps({"ok": True}))\n',
        run_id="e2e-ok",
    )
    assert result.state == RunState.COMPLETED
    assert result.output == {"ok": True}


async def test_real_chatty_child_over_1mib_no_deadlock(context, tmp_path, monkeypatch):
    """M2: a real child writing > 1 MiB to stdout drains without deadlock."""
    result = await run_prepared_script(
        context,
        tmp_path,
        monkeypatch,
        source=(
            "import sys, json\n"
            "sys.stdout.write('x' * (1024 * 1024 + 4096))\n"
            "print()\n"
            'print("CAO_WORKFLOW_OUTPUT:" + json.dumps({"drained": True}))\n'
        ),
        run_id="e2e-chatty",
    )
    assert result.state == RunState.COMPLETED
    assert result.output == {"drained": True}


async def test_real_hang_is_reaped_within_bound(context, tmp_path, monkeypatch):
    """A real sleeping child is reaped at the wall-clock bound -> FAILED,kind=timeout."""
    monkeypatch.setattr(script_runner, "WORKFLOW_SCRIPT_TIMEOUT", 0.5)
    monkeypatch.setattr(script_runner, "WORKFLOW_SCRIPT_TERM_GRACE", 0.5)
    result = await run_prepared_script(
        context,
        tmp_path,
        monkeypatch,
        source="import time\ntime.sleep(30)\n",
        run_id="e2e-hang",
    )
    assert result.state == RunState.FAILED
    assert result.kind == "timeout"


async def test_real_nonzero_exit_is_failed(context, tmp_path, monkeypatch):
    """A real nonzero script persists stderr after the child process exits."""
    result = await run_prepared_script(
        context,
        tmp_path,
        monkeypatch,
        source='import sys\nsys.stderr.write("boom\\n")\nsys.exit(3)\n',
        run_id="e2e-crash",
    )
    assert result.state == RunState.FAILED
    assert result.kind == "error"
    row = workflow_journal.get_run("e2e-crash")
    assert row is not None
    assert row.error == "boom"
