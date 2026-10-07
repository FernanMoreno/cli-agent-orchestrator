"""Real SQLite snapshot verification must not block the API event loop."""

import asyncio
import threading
from contextlib import contextmanager
from test.services.test_integration_008_scoped_results import (
    context,
    plan_context,
    scoped_result_context,
)

import pytest

from cli_agent_orchestrator.services.workflow_continuation_driver import WorkflowContinuationDriver


def test_slow_real_snapshot_does_not_block_event_loop(scoped_result_context, monkeypatch):
    f = scoped_result_context
    driver = WorkflowContinuationDriver(f.plans, f.projector, instance_id="slow-snapshot-test")
    original = f.plans.repository.read_snapshot
    entered, release = threading.Event(), threading.Event()

    @contextmanager
    def slow_snapshot():
        entered.set()
        release.wait(2)
        with original() as conn:
            yield conn

    monkeypatch.setattr(f.plans.repository, "read_snapshot", slow_snapshot)

    async def scenario():
        task = asyncio.create_task(driver.tick())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            responsive = not task.done()
        finally:
            release.set()
            await task
        assert responsive, "continuation SQLite verification blocked the shared event loop"

    asyncio.run(scenario())


from test.services.test_integration_008_coordinator import coordinator_context, start_controller


def test_coordinator_stop_keeps_real_sqlite_off_event_loop(coordinator_context, monkeypatch):
    f = coordinator_context
    started = start_controller(f)
    main_thread = threading.get_ident()
    for name in ("read_snapshot", "transaction"):
        original = getattr(f.service.repository, name)

        @contextmanager
        def checked(original=original):
            assert threading.get_ident() != main_thread, "stop touched SQLite on the event loop"
            with original() as conn:
                yield conn

        monkeypatch.setattr(f.service.repository, name, checked)
    result = asyncio.run(f.service.stop(f.subject, started["coordinator_id"]))
    assert result["state"] == "stopped"


def test_real_http_health_remains_available_during_sqlite_verification(
    scoped_result_context, monkeypatch
):
    import socket
    from contextlib import asynccontextmanager

    import requests
    import uvicorn
    from fastapi import FastAPI

    from cli_agent_orchestrator.api import main

    f = scoped_result_context
    driver = WorkflowContinuationDriver(f.plans, f.projector, instance_id="real-http-test")
    entered, release = threading.Event(), threading.Event()
    original = f.plans.repository.read_snapshot

    @contextmanager
    def slow_snapshot():
        entered.set()
        release.wait(5)
        with original() as conn:
            yield conn

    monkeypatch.setattr(f.plans.repository, "read_snapshot", slow_snapshot)
    monkeypatch.setattr(main, "get_backend", lambda: object())
    loops = []

    @asynccontextmanager
    async def lifespan(_app):
        loops.append(asyncio.get_running_loop())
        yield

    app = FastAPI(lifespan=lifespan)
    app.add_api_route("/health", main.health_check)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = "http://127.0.0.1:" + str(sock.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", ws="none"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    pending = None
    try:
        import time

        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        pending = asyncio.run_coroutine_threadsafe(driver.tick(), loops[0])
        assert entered.wait(3)
        response = requests.get(url + "/health", timeout=1)
        assert response.status_code == 200
        assert not pending.done(), "health ran only after SQLite stopped blocking"
    finally:
        release.set()
        if pending is not None:
            pending.result(timeout=5)
        server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()


@pytest.mark.parametrize("stopping", [False, True])
def test_successive_rounds_reach_work_behind_128_waiting_rows(
    scoped_result_context, monkeypatch, stopping
):
    f = scoped_result_context
    driver = WorkflowContinuationDriver(f.plans, f.projector, instance_id="fair-pages")
    driver.enable(f.subject, "scoped-result")
    with f.context.repo.transaction() as conn:
        for index in range(260):
            run_id = f"a-waiting-{index:03}"
            for table in ("workflow_run", "workflow_driver"):
                row = dict(
                    conn.execute(f"SELECT * FROM {table} WHERE run_id='scoped-result'").fetchone()
                )
                row["run_id"] = run_id
                if table == "workflow_driver":
                    row["state"] = "waiting"
                columns = ",".join(row)
                placeholders = ",".join("?" for _ in row)
                conn.execute(
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(row.values())
                )
    observed = []
    monkeypatch.setattr(f.projector, "project_pending_for_run", observed.append)
    if stopping:
        from types import SimpleNamespace

        with f.context.repo.transaction() as conn:
            conn.execute("UPDATE workflow_driver SET state='stopping'")
        monkeypatch.setattr(driver, "owner", lambda run_id: f.subject)
        driver.coordinator = SimpleNamespace(
            reconcile_stop=lambda principal, run_id: observed.append(run_id)
        )

    async def scenario():
        for _ in range(4):
            await driver.tick()

    asyncio.run(scenario())
    assert "scoped-result" in observed
    assert len(set(observed)) == 261
