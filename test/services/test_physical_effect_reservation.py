"""Native ownership blocks physical retirement independently of SQL loans."""

import copy
import gc
import pickle
import threading
import weakref
from test.services.test_delivery_admission import create, observe

import pytest

from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
from cli_agent_orchestrator.services.delivery_admission import PhysicalEffectReservation
from cli_agent_orchestrator.services.work_process_effect import (
    begin_native_effect,
    confirm_native_effect_closed,
    observe_native_effect,
    observe_thread_close,
    physical_effect_scope,
    retain_native_effect_owner,
)

pytestmark = pytest.mark.offline_database


def started():
    owner = create()
    assert observe(owner, 2)
    lease = owner.try_reserve("native", now=10.0, storage_slots=2).reservation
    assert lease is not None and lease.start()
    return owner, lease


def test_native_owner_retained_until_positive_close_and_ready_notification():
    class Native:
        pass

    owner, lease = started()
    hold = lease.hold_retirement()
    native = Native()
    ref = weakref.ref(native)
    done = [False]
    token = lease.hold_physical_effect()
    token.retain(native, lambda: done[0])
    del native
    gc.collect()
    assert ref() is not None
    assert not lease.finish_after_storage()
    assert not hold.physical_ready and not token.finish()
    assert owner.try_reserve("native", now=10.0).reservation is None
    done[0] = True
    assert token.finish()
    assert hold.physical_ready
    assert owner.wait_retirement_ready(timeout_s=0) is hold
    assert hold.finish() and not token.finish()
    gc.collect()
    assert ref() is None
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.parametrize("lost_start_ack", [False, True])
def test_thread_witness_requires_positive_start_and_actual_termination(lost_start_ack):
    entered, release = threading.Event(), threading.Event()

    def run():
        entered.set()
        assert release.wait(3), "controlled thread was not released"

    thread = threading.Thread(target=run)
    closed = observe_thread_close(thread)
    try:
        assert closed() is False  # not alive before start does not prove closure.
        thread.start()
        assert entered.wait(3)
        if lost_start_ack:
            try:
                raise OSError("controlled lost Thread.start acknowledgement")
            except OSError:
                assert closed() is False
        assert closed() is False
        release.set()
        thread.join(3)
        assert not thread.is_alive() and closed() is True
    finally:
        release.set()
        if thread.ident is not None:
            thread.join(3)


def test_owner_observation_runs_outside_guard_and_failure_keeps_charge():
    owner, lease = started()
    token = lease.hold_physical_effect()
    done = [False]

    def closed():
        acquired = []

        def inspect():
            free = owner._guard.acquire(blocking=False)
            acquired.append(free)
            if free:
                owner._guard.release()

        inspector = threading.Thread(target=inspect)
        inspector.start()
        inspector.join(3)
        assert not inspector.is_alive() and acquired == [True], "observation held admission lock"
        if not done[0]:
            raise OSError("controlled unavailable witness")
        return True

    token.retain(object(), closed)
    assert not lease.finish_after_storage()
    assert not token.finish() and owner.snapshot(now=10.0).inflight == 1
    done[0] = True
    assert token.finish() and owner.snapshot(now=10.0).inflight == 0


def test_native_and_sql_owners_both_close_before_release():
    owner, lease = started()
    token = lease.hold_physical_effect()
    loan = lease.try_acquire_storage_connection(now=10.0)
    assert loan is not None
    assert not lease.finish_after_storage()
    assert token.finish()
    assert owner.snapshot(now=10.0).inflight == 1
    assert loan.finish()
    assert owner.snapshot(now=10.0).inflight == 0


@pytest.mark.parametrize("clone", [copy.copy, copy.deepcopy, pickle.dumps])
def test_native_token_cannot_be_copied_or_pickled(clone):
    owner, lease = started()
    token = lease.hold_physical_effect()
    with pytest.raises(TypeError):
        clone(token)
    assert not PhysicalEffectReservation(lease).finish()
    assert token.finish() and lease.finish()


def test_retired_or_foreign_process_cannot_attach_or_finish_native_owner(monkeypatch):
    owner, lease = started()
    token = lease.hold_physical_effect()
    assert not lease.finish_after_storage()
    with pytest.raises(StorageAdmissionUnavailable):
        lease.hold_physical_effect()
    with monkeypatch.context() as patch:
        patch.setattr(owner, "_pid", owner._pid + 10000)
        with pytest.raises(StorageAdmissionUnavailable):
            token.finish()
        with pytest.raises(StorageAdmissionUnavailable):
            token.retain(object(), lambda: True)
    assert token.finish()


def test_monitored_preflight_error_is_proven_no_start_but_attempt_failure_is_not():
    owner, lease = started()
    with pytest.raises(ValueError):
        with physical_effect_scope(lease) as effect:
            observe_native_effect()
            effect.gate()
            raise ValueError("preflight before first native attempt")
    assert not lease._physical_effects
    with pytest.raises(OSError):
        with physical_effect_scope(lease) as effect:
            observe_native_effect()
            effect.gate()
            begin_native_effect()
            raise OSError("start may have happened")
    assert len(lease._physical_effects) == 1
    assert not lease.finish_after_storage()
    assert owner.snapshot(now=10.0).inflight == 1


def test_copied_context_in_another_thread_cannot_confirm_the_native_owner():
    import contextvars

    owner, lease = started()
    errors = []
    with physical_effect_scope(lease) as effect:
        effect.gate()
        begin_native_effect()
        context = contextvars.copy_context()

        def foreign():
            try:
                context.run(confirm_native_effect_closed)
            except StorageAdmissionUnavailable:
                errors.append(True)

        worker = threading.Thread(target=foreign)
        worker.start()
        worker.join(3)
        assert not worker.is_alive() and errors == [True]
    assert not lease.finish_after_storage()
    assert owner.snapshot(now=10.0).inflight == 1


def test_positive_callback_does_not_override_a_live_native_handle():
    owner, lease = started()
    done = [False]
    with physical_effect_scope(lease) as effect:
        effect.gate()
        retain_native_effect_owner(object(), lambda: done[0])
        confirm_native_effect_closed()
    assert not lease.finish_after_storage()
    assert owner.snapshot(now=10.0).inflight == 1
    done[0] = True
    token = next(iter(lease._physical_effects))
    assert token.finish()
    assert owner.snapshot(now=10.0).inflight == 0
