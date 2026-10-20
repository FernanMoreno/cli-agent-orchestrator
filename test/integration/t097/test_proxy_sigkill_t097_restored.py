"""SIGKILL proxy crash windows against the current server-owned proxy design."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import threading
import time
from contextlib import suppress
from test.integration.t097.test_host_acceptance import (
    _host_backend_factory,
    accepted_linux_host,
)
from test.integration.t097.test_proxy_concurrency import _proxy_worker, _use_test_worker_timeout
from test.integration.t098.test_work_launch_dispatch import _setup
from typing import Any

import pytest

from cli_agent_orchestrator.services.work_mcp_proxy import WorkMcpProxy
from cli_agent_orchestrator.services.work_service import DeliveryUncertain, WorkService

pytestmark = [pytest.mark.integration, pytest.mark.t097_host]

_SETUP_ACK_TIMEOUT_SECONDS = 20.0


def _post_sigkill_cleanup_timeout_seconds() -> float:
    worker_timeout_seconds = float(os.environ.get("T097_TEST_WORKER_TIMEOUT_SECONDS", "10"))
    assert 0 < worker_timeout_seconds <= 300
    return _SETUP_ACK_TIMEOUT_SECONDS + worker_timeout_seconds


def _add_cleanup_note_if_supported(error: BaseException, note: str) -> None:
    """Attach best-effort cleanup context without masking the body failure.

    ``BaseException.add_note`` is available starting with Python 3.11, while
    this project supports Python 3.10. Cleanup diagnostics must remain
    optional on older interpreters and must never replace the primary error.
    """
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        try:
            add_note(note)
        except BaseException:
            # A diagnostic failure must not shadow the error being cleaned up.
            pass


async def _wait_for_upstream_or_dispatch(
    upstream_started: asyncio.Event,
    dispatch: asyncio.Future[Any],
) -> None:
    """Wait for the callback or the real dispatch outcome.

    Setup phases and worker execution carry their own production deadlines. A
    synthetic aggregate timeout measured from dispatch scheduling can expire
    before preparation reaches those phases, so the QEMU job supplies the outer
    hang guard for this rendezvous.
    """
    upstream_wait = asyncio.create_task(upstream_started.wait())
    try:
        done, _ = await asyncio.wait(
            (upstream_wait, dispatch),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if upstream_wait in done:
            return
        if dispatch in done:
            # Await the task so its original error remains observable.
            await dispatch
            raise AssertionError("dispatch completed before the upstream callback started")
    finally:
        if not upstream_wait.done():
            upstream_wait.cancel()
            with suppress(asyncio.CancelledError):
                await upstream_wait


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["before_upstream_effect", "after_upstream"])
async def test_sigkill_during_proxy_effect_window_is_uncertain_without_redelivery(
    tmp_path, monkeypatch, accepted_linux_host, stage
):
    worker = _proxy_worker(tmp_path)
    _use_test_worker_timeout(monkeypatch)
    post_sigkill_timeout_seconds = _post_sigkill_cleanup_timeout_seconds()
    upstream_started = asyncio.Event()
    continue_upstream = threading.Event()
    loop = asyncio.get_running_loop()
    started_at = time.monotonic()
    timeline: list[tuple[str, float]] = []
    upstream_callback_at: list[float] = []

    def mark(stage_name: str) -> None:
        timeline.append((stage_name, round(time.monotonic() - started_at, 3)))

    external_effect = tmp_path / f"{stage}.effect"
    upstream_calls: list[str] = []
    supervisors = []
    executions = []
    proxies = []

    def make_proxy(repository, _attempt_id, _generation):
        def upstream(request, _secret):
            upstream_calls.append(stage)
            upstream_callback_at.append(time.monotonic())
            if stage == "after_upstream":
                external_effect.write_text("synthetic effect reached upstream")
            loop.call_soon_threadsafe(upstream_started.set)
            if not continue_upstream.wait(timeout=15):
                raise TimeoutError("test did not release the controlled upstream")
            raise TimeoutError("simulated connection loss at the selected effect window")

        proxy = WorkMcpProxy(
            repository,
            server_secret_factory=lambda: b"synthetic-t097-secret",
            upstream=upstream,
        )
        proxies.append(proxy)
        return proxy

    repository, principal, gateway, _backend, _marker, request = _setup(
        tmp_path,
        backend_factory=lambda marker, repo: _host_backend_factory(
            supervisors,
            executions,
            mcp_proxy_factory=make_proxy,
        )(marker, repo),
        worker_binary=worker,
        contract_tools=("test.echo",),
        request_tools=("test.echo",),
    )
    receipt = gateway.admit(principal, request)
    dispatch = asyncio.create_task(gateway.dispatch_registered_next())
    mark("dispatch_started")
    body_error: BaseException | None = None
    try:
        await _wait_for_upstream_or_dispatch(
            upstream_started,
            dispatch,
        )
        mark("upstream_observed")
        if upstream_callback_at:
            timeline.append(
                (
                    "upstream_callback_elapsed",
                    round(upstream_callback_at[0] - started_at, 3),
                )
            )
        assert len(supervisors) == 1
        attempt = supervisors[0].attempt
        assert attempt is not None

        signal.pidfd_send_signal(attempt._pidfd, signal.SIGKILL)
        mark("sigkill_sent")
        from cli_agent_orchestrator.services.work_process_supervisor import WorkProcessState

        assert supervisors[0].wait(attempt, timeout=8) is WorkProcessState.TERMINATED
        mark("supervisor_reaped")
        continue_upstream.set()
        mark("upstream_released")

        try:
            await asyncio.wait_for(dispatch, timeout=post_sigkill_timeout_seconds)
        except DeliveryUncertain:
            mark("delivery_uncertain")
        else:
            pytest.fail("dispatch completed without an uncertain-delivery result")

        assert upstream_calls == [stage]
        assert external_effect.exists() is (stage == "after_upstream")
        current = repository.get_work(receipt.work_item_id)
        assert current["state"] == "reconcile"
        assert current["attempts"][0]["state"] == "reconcile"
        effects = WorkMcpProxy(repository).unresolved_effects()
        assert len(effects) == 1
        assert effects[0]["attempt_id"] == receipt.attempt_id
        assert effects[0]["state"] == "uncertain"
        assert await gateway.dispatch_registered_next() is None
        assert upstream_calls == [stage]
    except BaseException as error:
        body_error = error
        print(f"T097_STAGE_TIMELINE {json.dumps(timeline, separators=(',', ':'))}", flush=True)
        raise
    finally:
        continue_upstream.set()
        try:
            if not dispatch.done():
                try:
                    await asyncio.wait_for(dispatch, timeout=post_sigkill_timeout_seconds)
                    mark("cleanup_dispatch_completed")
                except BaseException as cleanup_error:
                    if body_error is None:
                        raise
                    _add_cleanup_note_if_supported(
                        body_error,
                        "T097 dispatch cleanup also raised "
                        f"{type(cleanup_error).__name__}; "
                        f"timeline={json.dumps(timeline, separators=(',', ':'))}",
                    )
        finally:
            for proxy in proxies:
                try:
                    proxy.close()
                except BaseException as cleanup_error:
                    if body_error is None:
                        raise
                    _add_cleanup_note_if_supported(
                        body_error, f"T097 proxy cleanup also raised {type(cleanup_error).__name__}"
                    )
            if supervisors and supervisors[0].attempt is not None:
                attempt = supervisors[0].attempt
                if attempt.state.value != "terminated":
                    try:
                        supervisors[0].terminate(attempt, timeout=3)
                    except BaseException as cleanup_error:
                        if body_error is None:
                            raise
                        _add_cleanup_note_if_supported(
                            body_error,
                            "T097 supervisor cleanup also raised "
                            f"{type(cleanup_error).__name__}",
                        )
