"""Owned off-loop execution for admitted Work process effects."""

from __future__ import annotations

import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Coroutine, TypeVar

from cli_agent_orchestrator.clients.storage_admission import (
    capture_storage_worker,
    storage_scope_present,
)

_Result = TypeVar("_Result")


async def run_owned_blocking(
    function: Callable[..., _Result], /, *args: Any, **kwargs: Any
) -> _Result:
    """Run one admitted blocking call outside asyncio's shared default executor.

    The caller must invoke this only after Work admission has committed. One
    single-use executor then owns that call through completion, without adding a
    separate production concurrency limit or queue for unadmitted work.
    """
    loop = asyncio.get_running_loop()
    handoff = capture_storage_worker()
    context = contextvars.copy_context()

    def run_in_context() -> _Result:
        if handoff is not None:
            return handoff.run(function, *args, **kwargs)
        return function(*args, **kwargs)

    def invoke() -> _Result:
        return context.run(run_in_context)

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cao-work-execution")
    try:
        future = executor.submit(invoke)
    except BaseException:
        # If thread startup failed after submission began, cancel pending work and
        # join any worker that did start before the caller can close its capability.
        executor.shutdown(wait=True, cancel_futures=True)
        raise

    try:
        wrapped = asyncio.wrap_future(future, loop=loop)
    except BaseException:
        # A live loop should always be able to wrap a submitted Future. Still join
        # the real operation if that invariant is broken; never report it stopped.
        executor.shutdown(wait=True, cancel_futures=True)
        raise

    try:
        return await asyncio.shield(wrapped)
    except asyncio.CancelledError as cancellation:
        while not future.done() or not wrapped.done():
            if wrapped.done() and not future.done():
                wrapped = asyncio.wrap_future(future, loop=loop)
            try:
                await asyncio.shield(wrapped)
            except asyncio.CancelledError:
                continue
            except BaseException:
                # A worker exception completes the concurrent Future as well.
                # If the wrapper failed independently, rebuild its awaitable from
                # the still-owned Future and keep draining instead of closing early.
                if not future.done():
                    wrapped = asyncio.wrap_future(future, loop=loop)

        # Cancellation remains the caller-visible outcome, but retrieve a worker
        # error from both Future layers and keep ownership through synchronous cleanup.
        try:
            wrapped.result()
        except BaseException:
            pass
        try:
            future.result()
        except BaseException:
            pass
        raise cancellation
    finally:
        # The concurrent Future is done on every normal/error/cancel path above.
        # shutdown(wait=True) joins the worker thread before adapter port cleanup.
        executor.shutdown(wait=True, cancel_futures=True)


async def run_storage_aware_blocking(
    function: Callable[..., _Result], /, *args: Any, **kwargs: Any
) -> _Result:
    """Preserve ordinary offloads; admitted work gets an explicitly owned worker."""
    if storage_scope_present():
        return await run_owned_blocking(function, *args, **kwargs)
    return await asyncio.to_thread(function, *args, **kwargs)


async def run_owned_async(operation: Coroutine[Any, Any, _Result]) -> _Result:
    """Keep callback ownership through completion, preserving the first failure.

    Cancelling a waiter does not revoke the admitted Work contract or prove its
    physical callback retired. Deliveries retain their deadlines/effect guards.
    """

    async def invoke() -> _Result:
        # Even an eager factory must return an owner before this callback starts.
        await asyncio.sleep(0)
        return await operation

    wrapper = invoke()
    try:
        worker = asyncio.create_task(wrapper)
    except BaseException:
        wrapper.close()
        operation.close()
        raise
    try:
        return await asyncio.shield(worker)
    except BaseException as failure:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except BaseException:
                # A failed await wrapper is not proof the callback finished.
                # Yield even when repeated wrapper failure has no suspension.
                if not worker.done():
                    try:
                        await asyncio.sleep(0)
                    except BaseException:
                        pass
        try:
            worker.result()
        except BaseException:
            pass
        raise failure
