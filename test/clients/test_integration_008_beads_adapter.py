"""Disposable subprocess proofs for the optional external metadata adapter."""

import sys
from pathlib import Path

import pytest


def client(tmp_path, source, *, timeout=1, limit=4096):
    from cli_agent_orchestrator.clients.beads import BeadsClient

    script = tmp_path / "fake-bd"
    script.write_text("#!" + sys.executable + "\n" + source)
    script.chmod(0o700)
    (tmp_path / ".beads").mkdir(exist_ok=True)
    return BeadsClient(tmp_path, binary=str(script), timeout=timeout, output_limit=limit)


def test_nonzero_empty_stderr_is_visible_error(tmp_path):
    from cli_agent_orchestrator.clients.beads import BeadsError

    adapter = client(tmp_path, "import sys;sys.exit(7)\n")
    with pytest.raises(BeadsError) as error:
        adapter.list()
    assert error.value.kind == "beads_cli_failed"


def test_malformed_json_is_not_empty_queue(tmp_path):
    from cli_agent_orchestrator.clients.beads import BeadsError

    adapter = client(tmp_path, "print('not json')\n")
    with pytest.raises(BeadsError) as error:
        adapter.list()
    assert error.value.kind == "beads_invalid_json"


def test_deadline_kills_and_reaps_child(tmp_path):
    from cli_agent_orchestrator.clients.beads import BeadsError

    adapter = client(tmp_path, "import time;time.sleep(30)\n", timeout=0.05)
    with pytest.raises(BeadsError) as error:
        adapter.list()
    assert error.value.kind == "beads_timeout" and not error.value.uncertain
    with pytest.raises(BeadsError) as error:
        adapter.add("one write")
    assert error.value.uncertain


def test_output_bound_and_utf8_error_are_visible(tmp_path):
    from cli_agent_orchestrator.clients.beads import BeadsError

    adapter = client(tmp_path, "import sys;sys.stdout.write('x'*100000)\n", limit=512)
    with pytest.raises(BeadsError) as error:
        adapter.list()
    assert error.value.kind == "beads_output_limit"
    adapter = client(tmp_path, "import sys;sys.stdout.buffer.write(b'\\xff')\n")
    with pytest.raises(BeadsError) as error:
        adapter.list()
    assert error.value.kind == "beads_invalid_utf8"


def test_explicit_cwd_argv_values_and_priority_zero(tmp_path):
    adapter = client(
        tmp_path,
        "import os,json,sys;print(json.dumps([{'id':'task-1','title':os.getcwd(),'priority':0,'status':'open','labels':sys.argv[1:]}]))\n",
    )
    task = adapter.list(priority=0)[0]
    assert task.priority == 0 and task.title == str(tmp_path)
    assert "--no-daemon" in task.labels
    with pytest.raises(ValueError):
        adapter.get("--unsafe")


def test_parent_context_cycle_and_path_escape_refused(tmp_path):
    from cli_agent_orchestrator.clients.beads import Task, resolve_context_files

    tasks = {
        "a": Task(id="a", title="a", parent_id="b", labels=["context:../outside"]),
        "b": Task(id="b", title="b", parent_id="a"),
    }

    class Catalog:
        working_dir = tmp_path

        def get(self, key):
            return tasks[key]

    with pytest.raises(ValueError):
        resolve_context_files(tasks["a"], Catalog())
