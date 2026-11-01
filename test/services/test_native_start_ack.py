"""Unobserved process starts cannot be discharged by artifact-only cleanup."""

import contextvars
import copy
import subprocess
import sys
import threading
from test.backends.test_docker_backend_contracts import native, result
from test.services.test_physical_effect_reservation import started

import pytest

from cli_agent_orchestrator.backends import docker_backend as docker
from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.services import work_process_effect as effects

_REAL_RUN = subprocess.run
_REAL_POPEN = subprocess.Popen
pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize("begin", [False, True])
def test_unknown_start_is_sticky_across_begin_confirm_and_manual_finish(begin):
    owner, lease = started()
    with effects.physical_effect_scope(lease) as effect:
        effects.observe_native_effect()
        if begin:
            effect.gate()
            effects.begin_native_effect()
        effects.mark_native_start_unobserved()
        effects.confirm_native_effect_closed()
        assert effect.unobserved_start and not effect.closed
        effects.begin_native_effect()
        effects.confirm_native_effect_closed()
        assert effect.unobserved_start and not effect.closed
    assert not lease.finish_after_storage()
    token = next(iter(lease._physical_effects))
    assert len(token._unobserved_starts) == 1 and not token.finish()
    token._register_late()  # A later artifact-only assertion is still insufficient.
    assert not owner._physical_closes and owner.retry_physical_closes().clean
    assert owner.snapshot(now=10).inflight == 1
    with pytest.raises(TypeError):
        copy.copy(token)


def test_retained_helper_marker_revokes_positive_late_eligibility():
    owner, lease = started()
    entered, mark, marked, release = (threading.Event() for _ in range(4))
    marker = []
    errors = []

    def run():
        entered.set()
        try:
            assert mark.wait(3)
            with effects.native_start_marker_scope(marker[0]):
                effects.mark_native_start_unobserved()
            marked.set()
            assert release.wait(3)
        except BaseException as error:
            errors.append(error)

    helper = threading.Thread(target=run)
    try:
        with effects.physical_effect_scope(lease):
            effects.observe_native_effect()
            effects.begin_native_effect()
            marker.append(effects.capture_native_start_marker())
            helper.start()
            assert entered.wait(3)
            effects.retain_native_effect_owner(helper, effects.observe_thread_close(helper))
            effects.confirm_native_effect_closed()
        assert not lease.finish_after_storage() and len(owner._physical_closes) == 1
        mark.set()
        assert marked.wait(3)
        assert not owner._physical_closes
        release.set()
        helper.join(3)
        assert not helper.is_alive() and not errors
        assert owner.retry_physical_closes().clean
        assert owner.snapshot(now=10).inflight == 1
        token = next(iter(lease._physical_effects))
        assert not token.finish()
        token._register_late()
        assert not owner._physical_closes
    finally:
        mark.set()
        release.set()
        if helper.ident is not None:
            helper.join(3)


def test_captured_marker_only_changes_its_original_owner():
    first, first_lease = started()
    second, second_lease = started()
    with effects.physical_effect_scope(first_lease):
        effects.begin_native_effect()
        marker = effects.capture_native_start_marker()
    with effects.physical_effect_scope(second_lease) as second_effect:
        effects.begin_native_effect()
        with effects.native_start_marker_scope(marker):
            effects.mark_native_start_unobserved()
        assert not second_effect.unobserved_start
        effects.confirm_native_effect_closed()
    assert second_lease.finish_after_storage()
    assert second.snapshot(now=10).inflight == 0
    assert not first_lease.finish_after_storage()
    assert first.snapshot(now=10).inflight == 1
    assert next(iter(first_lease._physical_effects))._unobserved_starts


def test_marker_rejects_foreign_pid_before_inherited_locks(monkeypatch):
    owner, lease = started()
    with effects.physical_effect_scope(lease) as effect:
        effects.begin_native_effect()
        marker = effects.capture_native_start_marker()
    actual_pid = effects.os.getpid()

    class Forbidden:
        def __enter__(self):
            pytest.fail("foreign marker touched an inherited ledger lock")

        def __exit__(self, *args):
            pass

    with monkeypatch.context() as patch:
        patch.setattr(owner, "_process_guard", Forbidden())
        patch.setattr(effects.os, "getpid", lambda: actual_pid + 1)
        with pytest.raises(StorageAdmissionUnavailable):
            marker()
    assert not effect.unobserved_start
    assert not lease.finish_after_storage()


def test_retired_scope_marker_cannot_resurrect_a_lease():
    owner, lease = started()
    with effects.physical_effect_scope(lease):
        marker = effects.capture_native_start_marker()
        effects.confirm_native_effect_closed()
    assert lease.finish_after_storage()
    marker()
    assert owner.snapshot(now=10).inflight == 0
    assert not owner._physical_closes


def test_observed_nonzero_cli_result_does_not_invent_unknown_start(native):
    backend, seam = native
    seam.response = lambda *args: result(code=1, stderr="controlled daemon rejection")
    owner, lease = started()
    with effects.physical_effect_scope(lease) as effect:
        effects.observe_native_effect()
        effects.begin_native_effect()
        with pytest.raises(docker.DockerWorkBackendUnavailable):
            backend._run(["info"])
        assert not effect.unobserved_start
        effects.confirm_native_effect_closed()
    assert lease.finish_after_storage() and owner.snapshot(now=10).inflight == 0


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, MemoryError])
def test_cli_base_exception_keeps_its_type_and_the_unknown_obligation(native, error_type):
    backend, seam = native

    def interrupted(*args):
        raise error_type("controlled unobserved CLI interruption")

    seam.response = interrupted
    owner, lease = started()
    with effects.physical_effect_scope(lease) as effect:
        effects.observe_native_effect()
        with pytest.raises(error_type):
            backend._run(["info"])
        effects.confirm_native_effect_closed()
        assert effect.unobserved_start and not effect.closed
    assert not lease.finish_after_storage()
    assert owner.snapshot(now=10).inflight == 1


def test_stdlib_timeout_has_a_real_reaped_child_and_is_not_an_unknown_start(native, monkeypatch):
    backend, _ = native
    children = []

    def observed_popen(*args, **kwargs):
        child = _REAL_POPEN(*args, **kwargs)
        children.append(child)
        return child

    owner, lease = started()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(docker.subprocess, "run", _REAL_RUN)
            patch.setattr(docker.subprocess, "Popen", observed_popen)
            patch.setattr(
                backend,
                "_docker_arguments",
                lambda _: [sys.executable, "-I", "-c", "import time;time.sleep(10)"],
            )
            patch.setattr(backend, "_docker_environment", {"LC_ALL": "C"})
            with effects.physical_effect_scope(lease) as effect:
                effects.begin_native_effect()
                with pytest.raises(docker.DockerWorkBackendUnavailable):
                    backend._run(["ignored"], timeout=0.05)
                assert len(children) == 1 and children[0].poll() is not None
                assert not effect.unobserved_start
                effects.confirm_native_effect_closed()
        assert lease.finish_after_storage()
        assert owner.snapshot(now=10).inflight == 0
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


def test_confirm_cannot_overtake_a_helpers_installed_unknown_obligation(monkeypatch):
    from cli_agent_orchestrator.services.delivery_admission import PhysicalEffectReservation

    owner, lease = started()
    installed, allow_scope_flags = threading.Event(), threading.Event()
    original = PhysicalEffectReservation._mark_unobserved_start
    errors = []

    def paused(token):
        result = original(token)
        installed.set()
        assert allow_scope_flags.wait(3)
        return result

    marker = []

    def mark():
        try:
            marker[0]()
        except BaseException as error:
            errors.append(error)

    helper = threading.Thread(target=mark)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(PhysicalEffectReservation, "_mark_unobserved_start", paused)
            with effects.physical_effect_scope(lease) as effect:
                effects.begin_native_effect()
                marker.append(effects.capture_native_start_marker())
                helper.start()
                assert installed.wait(3)
                assert not effect.unobserved_start  # Pause between token and scope writes.
                effects.confirm_native_effect_closed()
                assert not effect.closed
                allow_scope_flags.set()
                helper.join(3)
                assert not helper.is_alive() and not errors
                assert effect.unobserved_start
        assert not lease.finish_after_storage()
        assert owner.snapshot(now=10).inflight == 1
        assert not owner._physical_closes
    finally:
        allow_scope_flags.set()
        if helper.ident is not None:
            helper.join(3)


def test_copied_context_adds_unknown_without_granting_positive_thread_authority(native):
    backend, seam = native
    failure = OSError("controlled unknown constructor in copied helper context")

    def unknown(*args):
        raise failure

    seam.response = unknown
    owner, lease = started()
    errors, positive_denials = [], []

    def run():
        try:
            effects.confirm_native_effect_closed()
        except StorageAdmissionUnavailable:
            positive_denials.append(True)
        try:
            backend._run(["info"])
        except BaseException as error:
            errors.append(error)

    with effects.physical_effect_scope(lease) as effect:
        effects.observe_native_effect()
        context = contextvars.copy_context()
        helper = threading.Thread(target=lambda: context.run(run))
        helper.start()
        helper.join(3)
        assert not helper.is_alive() and positive_denials == [True]
        assert len(errors) == 1 and isinstance(errors[0], docker.DockerWorkBackendUnavailable)
        assert errors[0].__cause__ is failure
        assert effect.unobserved_start
        effects.confirm_native_effect_closed()
        assert not effect.closed
    assert not lease.finish_after_storage()
    assert owner.snapshot(now=10).inflight == 1


@pytest.mark.parametrize("error_type", [OSError, MemoryError])
def test_bubblewrap_lost_start_ack_keeps_live_child_charged(tmp_path, monkeypatch, error_type):
    import select
    from test.services.test_work_bubblewrap_staging_args import _minimal_static_elf
    from types import SimpleNamespace

    from cli_agent_orchestrator.services import work_bubblewrap_composition as composition
    from cli_agent_orchestrator.services.work_elf_identity import identify_static_executable
    from cli_agent_orchestrator.services.work_executable_staging import stage_static_executable

    data = _minimal_static_elf()
    identity = identify_static_executable("/usr/bin/example", data)
    staged = stage_static_executable(identity, data)
    owner, lease = started()
    children = []
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    closed = []
    pinned = SimpleNamespace(path="unused", proc_path="unused", execution_descriptor=-1)
    runtime = SimpleNamespace(
        validate=lambda: None, close=lambda **kw: closed.append(kw), path=snapshot
    )
    monkeypatch.setattr(composition, "_require_accepted_bwrap", lambda *args: pinned)
    monkeypatch.setattr(composition, "_prepare_runtime_snapshot", lambda: (snapshot, runtime))
    monkeypatch.setattr(composition, "_build_bwrap_argv", lambda *args, **kw: ["unused"])
    monkeypatch.setattr(composition, "_revalidate_pinned_bwrap", lambda *args: None)
    monkeypatch.setattr(composition, "_close_pinned_bwrap", lambda *args: None)

    def lost_ack(*args, **kwargs):
        child = _REAL_POPEN(
            [
                sys.executable,
                "-I",
                "-c",
                "import sys;sys.stdout.buffer.write(b'R');sys.stdout.buffer.flush();sys.stdin.buffer.read()",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env={"LC_ALL": "C"},
        )
        children.append(child)
        assert select.select([child.stdout], [], [], 5)[0]
        assert child.stdout.read(1) == b"R" and child.poll() is None
        raise error_type("controlled Bubblewrap start acknowledgement loss")

    monkeypatch.setattr(composition.subprocess, "Popen", lost_ack)
    expected = composition.WorkBubblewrapSetupFailed if error_type is OSError else error_type
    try:
        with effects.physical_effect_scope(lease):
            effects.observe_native_effect()
            with pytest.raises(expected) as caught:
                composition._launch_owned_stage(
                    staged,
                    identity,
                    persist_and_authorize=lambda *args: None,
                    bwrap_path="unused",
                    bwrap_sha256_digest="unused",
                    timeout_seconds=2,
                    setup_timeout_seconds=2,
                )
            # Even an erroneous later positive assertion cannot clear uncertainty.
            effects.confirm_native_effect_closed()
        assert closed == [{"cleanup_confirmed": False}] and snapshot.exists()
        assert len(children) == 1 and children[0].poll() is None
        assert not lease.finish_after_storage()
        assert owner.snapshot(now=10).inflight == 1
        token = next(iter(lease._physical_effects))
        assert token._unobserved_starts and not token.finish()
        if error_type is OSError:
            assert caught.value.cleanup_confirmed is False
            assert caught.value.monitor_pid is None
            assert isinstance(caught.value.__cause__, OSError)
        assert not owner._physical_closes
    finally:
        for child in children:
            child.stdin.close()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            child.stdout.close()
        staged.close()
    assert owner.snapshot(now=10).inflight == 1
