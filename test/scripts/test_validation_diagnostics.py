"""A diagnostic must not race with interpreter-frame metadata lifetime."""

import ast
import os
import subprocess
import sys
from pathlib import Path


def diagnostic_source():
    tree = ast.parse(
        (Path(__file__).resolve().parents[2] / "scripts/validate_collaboration_demo.py").read_text()
    )
    return next(
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "write_text"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and "CAO_DIAGNOSTIC_STACKS" in node.args[0].value
    )


def test_generated_diagnostic_never_uses_native_watchdog():
    source = diagnostic_source()
    assert "dump_traceback_later" not in source
    assert "faulthandler.enable()" in source
    assert "faulthandler.dump_traceback(" in source


def test_diagnostic_survives_freed_frame_metadata_with_debug_allocator(tmp_path):
    source = diagnostic_source().replace(
        "dump_traceback_later(40,", "dump_traceback_later(0.00001,"
    )
    source = source.replace(".wait(40)", ".wait(0.001)")
    child = tmp_path / "diagnostic-race.py"
    child.write_text(source + """
import types,threading,time
stop=threading.Event()
def template():return 1
def churn():
    while not stop.is_set():
        code=template.__code__.replace()
        fn=types.FunctionType(code,{})
        fn();fn=None;code=None
worker=threading.Thread(target=churn)
worker.start()
time.sleep(1)
stop.set();worker.join()
print("diagnostic survived")
""")
    env = dict(
        os.environ,
        PYTHONMALLOC="debug",
        CAO_DIAGNOSTIC_STACKS="1",
        CAO_TEST_SOURCE_MANIFEST=str(tmp_path / "manifest.jsonl"),
    )
    completed = subprocess.run(
        [sys.executable, str(child)], env=env, capture_output=True, text=True, timeout=8
    )
    assert completed.returncode == 0, completed.stderr[-1500:]
    assert "diagnostic survived" in completed.stdout
    assert "Thread" in completed.stderr
