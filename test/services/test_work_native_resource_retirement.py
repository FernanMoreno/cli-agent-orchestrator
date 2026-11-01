"""Real owned child closure, independent of Bubblewrap/Docker acceptance."""

import subprocess
import sys
from test.services.test_physical_effect_reservation import started

import pytest

from cli_agent_orchestrator.services.work_process_effect import (
    begin_native_effect,
    confirm_native_effect_closed,
    observe_native_effect,
    observe_process_close,
    physical_effect_scope,
    retain_native_effect_owner,
)

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize("late", [False, True])
def test_exact_owned_child_keeps_its_footprint_until_wait_and_pipe_close(late):
    owner, lease = started()
    process = None
    try:
        with pytest.raises(OSError, match="controlled response loss"):
            with physical_effect_scope(lease) as effect:
                observe_native_effect()
                effect.gate()
                begin_native_effect()
                process = subprocess.Popen(
                    [sys.executable, "-I", "-c", "import sys; sys.stdin.buffer.read(1)"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    env={"LC_ALL": "C"},
                )
                retain_native_effect_owner(process, observe_process_close(process))
                retain_native_effect_owner(process.stdin, lambda: process.stdin.closed)
                assert process.poll() is None
                if not late:
                    process.stdin.write(b"x")
                    process.stdin.close()
                    assert process.wait(timeout=5) == 0
                    confirm_native_effect_closed()
                raise OSError("controlled response loss")
        lease.finish_after_storage()
        assert owner.snapshot(now=10.0).inflight == int(late)
        if late:
            token = next(iter(lease._physical_effects))
            assert not token.finish()
            process.stdin.write(b"x")
            process.stdin.close()
            assert process.wait(timeout=5) == 0
            assert token.finish() and not token.finish()
            assert owner.snapshot(now=10.0).inflight == 0
    finally:
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                process.kill()  # Only this exact test-owned child.
            process.wait(timeout=5)
        for token in tuple(lease._physical_effects):
            token.finish()
