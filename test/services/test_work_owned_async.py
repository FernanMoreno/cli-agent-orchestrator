"""A failed await wrapper is not physical callback retirement."""

import asyncio

import pytest

from cli_agent_orchestrator.services import work_blocking

pytestmark = pytest.mark.offline_database


@pytest.mark.asyncio
async def test_await_wrapper_failure_retains_admitted_callback_until_completion(monkeypatch):
    entered, release, retired = asyncio.Event(), asyncio.Event(), asyncio.Event()
    physical = []
    original = asyncio.shield
    calls = 0

    def shield(awaitable):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("controlled-await-wrapper-failure")
        return original(awaitable)

    async def operation():
        physical.append(asyncio.current_task())
        entered.set()
        try:
            await release.wait()
        finally:
            retired.set()

    with monkeypatch.context() as patch:
        patch.setattr(work_blocking.asyncio, "shield", shield)
        task = asyncio.create_task(work_blocking.run_owned_async(operation()))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.sleep(0)
            assert not task.done()
            assert not retired.is_set()
        finally:
            release.set()
            with pytest.raises(RuntimeError, match="controlled-await-wrapper-failure"):
                await task
            await asyncio.gather(*physical, return_exceptions=True)
    assert retired.is_set()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not hasattr(asyncio, "eager_task_factory"), reason="stdlib eager factory requires Python3.12+"
)
async def test_eager_factory_failure_cannot_begin_callback_before_owner_handoff(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    tasks = []

    async def operation():
        started.set()
        await release.wait()

    def create(coroutine, **kwargs):
        task = asyncio.eager_task_factory(asyncio.get_running_loop(), coroutine, **kwargs)
        tasks.append(task)
        raise RuntimeError("controlled-task-handoff-failure")

    with monkeypatch.context() as patch:
        patch.setattr(work_blocking.asyncio, "create_task", create)
        try:
            with pytest.raises(RuntimeError, match="controlled-task-handoff-failure"):
                await work_blocking.run_owned_async(operation())
            assert not started.is_set()
        finally:
            release.set()
            # These are exactly the fake-factory tasks captured by this test;
            # a closed coroutine may already have removed its Event waiter.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def test_resource_eligibility_peek_is_advisory_and_does_not_spend_capacity():
    from test.services.test_inbox_resource_admission import service

    _, owner = service(1)
    before = owner.snapshot(now=10.0)
    assert owner.can_reserve("one", now=10.0)
    assert owner.can_reserve("one", now=10.0)
    assert owner.snapshot(now=10.0) == before
    lease = owner.try_reserve("one", now=10.0).reservation
    assert lease is not None and lease.start()
    assert not owner.can_reserve("one", now=10.0)
    assert not owner.can_reserve("two", now=10.0)
    lease.finish()
    assert owner.can_reserve("two", now=10.0)
    owner.stop_accepting()
    assert not owner.can_reserve("two", now=10.0)


def test_zero_resource_budget_never_enters_work_storage():
    from test.services.test_inbox_resource_admission import service

    from cli_agent_orchestrator.services.work_admission import WorkAdmission

    _, owner = service(0)
    # Isolate the pre-storage boundary; no authority/repository is fabricated.
    admission = object.__new__(WorkAdmission)
    admission._resource_admission = owner
    admission._resource_clock = lambda: 10.0

    def preview(**kwargs):
        pytest.fail("zero resource headroom must not probe Work storage")

    admission._preview = preview
    assert admission._prepare_dispatch(registered_only=True) is None
