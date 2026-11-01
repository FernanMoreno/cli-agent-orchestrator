"""Late native helpers retire only after the original full native closure proof."""

import threading
from test.services.test_physical_effect_reservation import started

import pytest

from cli_agent_orchestrator.services import work_process_effect as effects

pytestmark = pytest.mark.offline_database


def retry(owner):
    return owner.retry_physical_closes()


def pending_helper(*, confirmed=True, observer=None, owner=None, now=10):
    if owner is None:
        owner, lease = started()
    else:
        lease = owner.try_reserve("late-native", now=now, storage_slots=2).reservation
        assert lease is not None and lease.start()
    entered, release = threading.Event(), threading.Event()

    def run():
        entered.set()
        assert release.wait(10)

    helper = threading.Thread(target=run)
    helper.start()
    assert entered.wait(3)
    with effects.physical_effect_scope(lease) as effect:
        effects.observe_native_effect()
        effect.gate()
        effects.begin_native_effect()
        effects.retain_native_effect_owner(helper, observer or effects.observe_thread_close(helper))
        if confirmed:
            effects.confirm_native_effect_closed()
    assert not lease.finish_after_storage()
    assert owner.snapshot(now=now).inflight == 1
    return owner, lease, helper, release


def cleanup(owner, lease, helper, release):
    release.set()
    helper.join(3)
    assert not helper.is_alive()
    for token in tuple(lease._physical_effects):
        token.finish()  # Fixture cleanup only after the exact helper really joined.
    if hasattr(owner, "retry_physical_closes"):
        retry(owner)


def test_confirmed_native_close_and_late_exact_helper_retire_without_manual_finish():
    owner, lease, helper, release = pending_helper()
    try:
        assert not retry(owner).clean
        assert owner.snapshot(now=10).inflight == 1
        release.set()
        helper.join(3)
        assert not helper.is_alive()
        result = retry(owner)
        assert result.clean and result.pending == 0
        assert owner.snapshot(now=10).inflight == 0
        assert retry(owner).clean
        replacement = owner.try_reserve("native", now=10, storage_slots=2).reservation
        assert replacement is not None and replacement.cancel_before_start()
    finally:
        cleanup(owner, lease, helper, release)


def test_closed_helper_without_full_native_proof_is_never_auto_retired():
    owner, lease, helper, release = pending_helper(confirmed=False)
    try:
        release.set()
        helper.join(3)
        assert not helper.is_alive()
        retry(owner)
        assert owner.snapshot(now=10).inflight == 1
        assert owner.try_reserve("native", now=10).reservation is None
    finally:
        cleanup(owner, lease, helper, release)


def test_closed_empty_owner_list_without_full_native_proof_is_not_registered():
    owner, lease = started()
    try:
        with effects.physical_effect_scope(lease) as effect:
            effects.observe_native_effect()
            effect.gate()
            effects.begin_native_effect()
        assert not lease.finish_after_storage()
        retry(owner)
        assert owner.snapshot(now=10).inflight == 1
    finally:
        for token in tuple(lease._physical_effects):
            token.finish()  # Controlled no-native-object fixture, never production observation.


def test_late_observer_error_preserves_the_charge_and_exact_record():
    broken = [False]
    closed = [False]

    def observer():
        if broken[0]:
            raise OSError("synthetic late observer failure")
        return closed[0]

    owner, lease, helper, release = pending_helper(observer=observer)
    try:
        broken[0] = True
        assert not retry(owner).clean
        assert owner.snapshot(now=10).inflight == 1
        release.set()
        helper.join(3)
        broken[0], closed[0] = False, True
        assert retry(owner).clean
        assert owner.snapshot(now=10).inflight == 0
    finally:
        cleanup(owner, lease, helper, release)


def test_late_close_is_scoped_to_exact_admission_owner():
    owner, lease, helper, release = pending_helper()
    other, other_lease = started()
    try:
        release.set()
        helper.join(3)
        assert retry(other).clean
        assert owner.snapshot(now=10).inflight == 1
        assert other.snapshot(now=10).inflight == 1
        assert retry(owner).clean and owner.snapshot(now=10).inflight == 0
    finally:
        other_lease.finish_after_storage()
        cleanup(owner, lease, helper, release)


def test_manually_retired_token_drops_late_record_without_second_release():
    owner, lease, helper, release = pending_helper()
    try:
        assert len(owner._physical_closes) == 1
        release.set()
        helper.join(3)
        token = next(iter(lease._physical_effects))
        assert token.finish()
        assert not owner._physical_closes
        assert retry(owner).clean
        assert owner.snapshot(now=10).inflight == 0
    finally:
        cleanup(owner, lease, helper, release)


def test_simultaneous_retry_is_nonblocking_and_observes_once():
    entered, finish_observation = threading.Event(), threading.Event()
    blocking = [False]
    observed = []
    helper_cell = []

    def observer():
        if not blocking[0]:
            return False
        observed.append(True)
        entered.set()
        assert finish_observation.wait(3)
        return not helper_cell[0].is_alive()

    owner, lease, helper, release = pending_helper(observer=observer)
    helper_cell.append(helper)
    results, errors = [], []

    def run_retry():
        try:
            results.append(retry(owner))
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=run_retry)
    try:
        blocking[0] = True
        worker.start()
        assert entered.wait(3)
        second = retry(owner)
        assert second.busy and not second.clean and second.pending == 1
        assert observed == [True]
        release.set()
        helper.join(3)
        finish_observation.set()
        worker.join(3)
        assert not worker.is_alive() and not errors
        assert len(results) == 1 and results[0].clean
        assert owner.snapshot(now=10).inflight == 0
        assert retry(owner).clean
    finally:
        finish_observation.set()
        if worker.ident is not None:
            worker.join(3)
        cleanup(owner, lease, helper, release)


def test_retry_observer_runs_outside_admission_guard():
    owner_cell = []
    acquired = []

    def observer():
        if not owner_cell:
            return False

        def inspect():
            lock = owner_cell[0]._guard
            free = lock.acquire(blocking=False)
            acquired.append(free)
            if free:
                lock.release()

        inspect_thread = threading.Thread(target=inspect)
        inspect_thread.start()
        inspect_thread.join(3)
        assert not inspect_thread.is_alive()
        return acquired[-1]

    owner, lease, helper, release = pending_helper(observer=observer)
    try:
        owner_cell.append(owner)
        release.set()
        helper.join(3)
        assert retry(owner).clean and acquired == [True]
        assert owner.snapshot(now=10).inflight == 0
    finally:
        cleanup(owner, lease, helper, release)


def test_foreign_pid_is_rejected_before_inherited_retry_lock(monkeypatch):
    from cli_agent_orchestrator.clients.storage_admission import StorageAdmissionUnavailable
    from cli_agent_orchestrator.services import delivery_admission

    owner, lease, helper, release = pending_helper()

    class Forbidden:
        def acquire(self, **kwargs):
            pytest.fail("foreign process touched inherited retry lock")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(owner, "_physical_retry_guard", Forbidden())
            actual_pid = delivery_admission.os.getpid()
            patch.setattr(delivery_admission.os, "getpid", lambda: actual_pid + 1)
            with pytest.raises(StorageAdmissionUnavailable):
                retry(owner)
        assert owner.snapshot(now=10).inflight == 1
    finally:
        cleanup(owner, lease, helper, release)


def test_monitor_refresh_observes_late_close_even_after_admission_stops():
    from test.services.test_admission_monitor import fixture, warm

    state = fixture()
    warm(state)
    owner, lease, helper, release = pending_helper(owner=state.owner, now=state.clock[0])
    try:
        state.monitor.stop_accepting()
        release.set()
        helper.join(3)
        assert state.monitor.refresh().reason == "stopping"
        assert owner.snapshot(now=state.clock[0]).inflight == 0
        assert not owner.snapshot(now=state.clock[0]).accepting
    finally:
        cleanup(owner, lease, helper, release)


@pytest.mark.asyncio
async def test_runtime_stop_retains_live_helper_then_retries_after_monitor_has_stopped():
    from test.services.test_admission_monitor import warm
    from test.services.test_inbox_admission_runtime import runtime

    from cli_agent_orchestrator.services.inbox_admission_runtime import AdmissionRuntimeUnavailable

    value, state, _ = runtime()
    await value.start()
    warm(state)
    owner, lease, helper, release = pending_helper(owner=state.owner, now=state.clock[0])
    try:
        with pytest.raises(AdmissionRuntimeUnavailable):
            await value.stop()
        assert not value.retired
        assert owner.snapshot(now=state.clock[0]).inflight == 1
        release.set()
        helper.join(3)
        assert not helper.is_alive()
        await value.stop()
        assert value.retired
        assert owner.snapshot(now=state.clock[0]).inflight == 0
        assert not owner._physical_closes
    finally:
        cleanup(owner, lease, helper, release)
        await value.stop()
