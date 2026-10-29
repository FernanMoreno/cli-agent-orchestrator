"""Bounded native events are evidence, never independent task authority."""

import json
import os

import pytest

from cli_agent_orchestrator.utils.native_completion_signal import (
    NativeCompletionChannel,
    publish_event,
)

pytestmark = pytest.mark.offline_database


def channel(tmp_path):
    return NativeCompletionChannel.create(tmp_path, "claude_code", "terminal")


def test_requires_start_current_dispatch_and_new_stop(tmp_path):
    c = channel(tmp_path)
    assert c.result("gen", "hash") is None
    publish_event(c.directory, c.binding, "stop", "s", "early")
    publish_event(c.directory, c.binding, "start", "s")
    publish_event(c.directory, c.binding, "stop", "s", "before dispatch")
    c.arm("gen", "hash")
    assert c.result("gen", "hash") is None
    publish_event(c.directory, c.binding, "stop", "s", "current")
    assert c.result("gen", "hash") == "current"
    assert c.result("other", "hash") is None
    assert c.result("gen", "other") is None


def test_foreign_and_rotated_writers_cannot_supply_result(tmp_path):
    c = channel(tmp_path)
    publish_event(c.directory, c.binding, "start", "s")
    c.arm("gen", "hash")
    for binding, event, sid in [
        ({**c.binding, "terminal": "foreign"}, "stop", "s"),
        (c.binding, "stop", "foreign-session"),
        (c.binding, "subagent", "s"),
        (c.binding, "start", "foreign-session"),
    ]:
        publish_event(c.directory, binding, event, sid, "foreign")
        assert c.result("gen", "hash") is None
    assert c.close()
    publish_event(c.directory, c.binding, "stop", "s", "late")
    d = channel(tmp_path)
    publish_event(d.directory, d.binding, "start", "new")
    d.arm("gen", "hash")
    assert d.result("gen", "hash") is None


@pytest.mark.parametrize("tamper", ["symlink", "mode", "oversize", "duplicate", "nonfinite"])
def test_unsafe_snapshots_fail_closed(tmp_path, tamper):
    c = channel(tmp_path)
    publish_event(c.directory, c.binding, "start", "s")
    c.arm("gen", "hash")
    publish_event(c.directory, c.binding, "stop", "s", "current")
    p = c.directory / "state.json"
    if tamper == "symlink":
        target = tmp_path / "foreign"
        target.write_bytes(p.read_bytes())
        p.unlink()
        p.symlink_to(target)
    elif tamper == "mode":
        p.chmod(0o644)
    elif tamper == "oversize":
        p.write_bytes(b"x" * (1024 * 1024 + 1))
    elif tamper == "duplicate":
        p.write_text('{"schema":1,"schema":1}')
    else:
        p.write_text('{"value":NaN}')
    assert c.result("gen", "hash") is None


def test_storage_remains_constant_and_close_preserves_unknown_files(tmp_path):
    c = channel(tmp_path)
    publish_event(c.directory, c.binding, "start", "s")
    for i in range(100):
        publish_event(c.directory, c.binding, "stop", "s", str(i))
    assert sorted(p.name for p in c.directory.iterdir()) == ["channel.lock", "state.json"]
    assert json.loads((c.directory / "state.json").read_text())["sequence"] == 100
    (c.directory / "foreign").write_text("preserve")
    assert c.close() is False
    assert (c.directory / "foreign").read_text() == "preserve"
    assert os.stat(c.directory).st_mode & 0o777 == 0o700


def test_dispatch_before_start_still_requires_new_stop(tmp_path):
    c = channel(tmp_path)
    c.arm("gen", "hash")
    assert c.result("gen", "hash") is None
    publish_event(c.directory, c.binding, "start", "s")
    assert c.result("gen", "hash") is None
    publish_event(c.directory, c.binding, "stop", "s", "current")
    assert c.result("gen", "hash") == "current"


@pytest.mark.parametrize("operation", ["publish", "arm", "close"])
def test_short_lock_contention_does_not_drop_event_or_epoch(tmp_path, operation):
    import fcntl
    import threading
    from concurrent.futures import ThreadPoolExecutor

    c = channel(tmp_path)
    publish_event(c.directory, c.binding, "start", "s")
    c.arm("gen", "hash")
    lock = os.open(c.directory / "channel.lock", os.O_RDONLY)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    entered = threading.Event()

    def work():
        entered.set()
        if operation == "publish":
            return publish_event(c.directory, c.binding, "stop", "s", "current")
        if operation == "arm":
            return c.arm("next", "hash")
        return c.close()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(work)
            assert entered.wait(1)
            # The operation is blocked by our actual flock, not a mocked lock.
            with pytest.raises(__import__("concurrent.futures").futures.TimeoutError):
                future.result(timeout=0.04)
            fcntl.flock(lock, fcntl.LOCK_UN)
            outcome = future.result(timeout=2)
            if operation == "publish":
                assert c.result("gen", "hash") == "current"
            elif operation == "arm":
                publish_event(c.directory, c.binding, "stop", "s", "current")
                assert c.result("next", "hash") == "current"
            else:
                assert outcome is True and not c.directory.exists()
    finally:
        os.close(lock)


def test_abandoned_fixed_temporary_does_not_poison_future_events(tmp_path):
    c = channel(tmp_path)
    publish_event(c.directory, c.binding, "start", "s")
    c.arm("gen", "hash")
    foreign = tmp_path / "foreign"
    foreign.write_text("preserve")
    (c.directory / ".snapshot.tmp").symlink_to(foreign)
    publish_event(c.directory, c.binding, "stop", "s", "current")
    assert c.result("gen", "hash") == "current"
    assert foreign.read_text() == "preserve"
    assert not (c.directory / ".snapshot.tmp").exists()


def test_partial_initialization_failure_cleans_known_files(tmp_path, monkeypatch):
    from cli_agent_orchestrator.utils import native_completion_signal as module

    def fail(*_args):
        raise OSError("injected snapshot failure")

    monkeypatch.setattr(module, "_write", fail)
    with pytest.raises(OSError, match="injected"):
        channel(tmp_path)
    assert not list(tmp_path.iterdir())


def test_epoch_binding_failure_does_not_leave_directory(tmp_path, monkeypatch):
    from cli_agent_orchestrator.utils import native_completion_signal as module

    def fail(*_args):
        raise OSError("injected epoch failure")

    monkeypatch.setattr(module.secrets, "token_hex", fail)
    with pytest.raises(OSError, match="injected epoch"):
        channel(tmp_path)
    assert not list(tmp_path.iterdir())
