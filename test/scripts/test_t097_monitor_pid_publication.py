"""Publish the QEMU fixture monitor PID completely before exposing its path."""

from __future__ import annotations

import ast
import builtins
import threading
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline_database


class _StopControllerLoop(Exception):
    pass


class _FakeProcess:
    pid = 424242


def _embedded_controller_code() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    fixture = repo_root / "test/services/test_work_bubblewrap_cleanup_recovery.py"
    tree = ast.parse(fixture.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_dead_monitor_recovery_cleans_surviving_exact_init"
    )
    assignment = next(
        node
        for node in function.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "controller_code"
            for target in node.targets
        )
    )
    return ast.literal_eval(assignment.value)


def _execute_fake_controller(code: str, pid_file: Path, *, gate: str):
    opened = threading.Event()
    before_replace = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    errors: list[str] = []
    fake_calls: list[str] = []
    path_base = type(Path())

    if gate == "write":

        class GatedPath(path_base):
            __slots__ = ()

            def write_text(self, data, *args, **kwargs):
                with self.open("w", *args, **kwargs) as stream:
                    opened.set()
                    if not release.wait(3):
                        raise RuntimeError("fake writer barrier timed out")
                    return stream.write(data)

    elif gate == "replace":

        class GatedPath(path_base):
            __slots__ = ()

            def replace(self, target):
                before_replace.set()
                if not release.wait(3):
                    raise RuntimeError("fake replace barrier timed out")
                return super().replace(target)

    else:
        raise AssertionError("unknown fake-controller gate")

    def fake_popen(*_args, **_kwargs):
        fake_calls.append("fake Popen")
        return _FakeProcess()

    def stop_after_publication(_seconds):
        raise _StopControllerLoop()

    fake_modules = {
        "pathlib": types.SimpleNamespace(Path=GatedPath),
        "subprocess": types.SimpleNamespace(DEVNULL=object(), Popen=fake_popen),
        "sys": types.SimpleNamespace(argv=["controller", "/fake/bwrap", str(pid_file)]),
        "time": types.SimpleNamespace(sleep=stop_after_publication),
    }
    real_import = builtins.__import__

    def import_hook(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name in fake_modules:
            return fake_modules[name]
        return real_import(name, globals, locals, fromlist, level)

    scope = {"__builtins__": {**builtins.__dict__, "__import__": import_hook}}

    def run():
        try:
            exec(compile(code, "<embedded-controller-code>", "exec"), scope, scope)
        except _StopControllerLoop:
            pass
        except BaseException as exc:
            errors.append(type(exc).__name__)
        finally:
            finished.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return opened, before_replace, release, finished, errors, fake_calls, thread


def test_monitor_pid_publication_has_no_visible_empty_file(tmp_path):
    controller = _embedded_controller_code()
    atomic_publish = (
        "pid_file = pathlib.Path(sys.argv[2])\n"
        "temporary_file = pid_file.with_name(pid_file.name + '.tmp')\n"
        "temporary_file.write_text(str(process.pid))\n"
        "temporary_file.replace(pid_file)"
    )
    assert controller.count(atomic_publish) == 1
    legacy_controller = controller.replace(
        atomic_publish,
        "pathlib.Path(sys.argv[2]).write_text(str(process.pid))",
        1,
    )

    red_path = tmp_path / "red" / "monitor-pid"
    red_path.parent.mkdir()
    opened, _, release, finished, errors, fake_calls, thread = _execute_fake_controller(
        legacy_controller, red_path, gate="write"
    )
    try:
        assert opened.wait(3), "legacy producer did not reach the opened target"
        assert red_path.exists()
        assert red_path.read_text(encoding="utf-8") == ""
        with pytest.raises(ValueError):
            int(red_path.read_text(encoding="utf-8"))
    finally:
        release.set()
    assert finished.wait(3)
    thread.join(timeout=1)
    assert not errors
    assert fake_calls == ["fake Popen"]

    green_path = tmp_path / "green" / "monitor-pid"
    green_path.parent.mkdir()
    _, before_replace, release, finished, errors, fake_calls, thread = _execute_fake_controller(
        controller, green_path, gate="replace"
    )
    try:
        assert before_replace.wait(3), "atomic producer did not reach same-directory replace"
        assert not green_path.exists(), "final PID path appeared before publication"
    finally:
        release.set()
    assert finished.wait(3)
    thread.join(timeout=1)
    assert not errors
    assert fake_calls == ["fake Popen"]
    assert green_path.exists()
    assert int(green_path.read_text(encoding="utf-8")) == _FakeProcess.pid
