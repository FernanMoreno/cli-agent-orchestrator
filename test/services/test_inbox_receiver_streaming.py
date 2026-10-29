"""Discovery must not exhaust the SQL result before delivering its first keys."""

import threading

import pytest

from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.services import inbox_service as module

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize(
    "method", ["poll_opencode_pending_messages", "reconcile_orphaned_messages"]
)
def test_sweep_does_not_materialize_every_receiver_before_dispatch(monkeypatch, method):
    total = 1000
    read = 0
    seen = []
    errors = []
    lock = threading.Lock()
    initial = threading.Event()
    release = threading.Event()
    service = module.InboxService()
    cap = module._SWEEP_DELIVERY_CONCURRENCY

    class Result:
        closed = False

        def __iter__(self):
            nonlocal read
            for number in range(total):
                read += 1
                yield (f"receiver-{number:05d}",)

        def close(self):
            self.closed = True

    class Query:
        def join(self, *_):
            return self

        def filter(self, *_):
            return self

        def distinct(self):
            return self

        def all(self):
            return list(Result())

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def query(self, *_):
            return Query()

        def execute(self, *_args, **_kwargs):
            return Result()

    def deliver(tid, *_):
        with lock:
            seen.append((tid, read))
            if len(seen) == cap:
                initial.set()
        if int(tid.rsplit("-", 1)[1]) < cap:
            assert release.wait(10)

    def run():
        try:
            getattr(service, method)()
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(db, "SessionLocal", Session)
    monkeypatch.setattr(service, "_deliver_pending_locked", deliver)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert initial.wait(5)
        assert all(
            count <= 128 + cap for _tid, count in seen
        ), "SQL discovery was exhausted before initial delivery"
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive() and not errors
    assert len(seen) == total
    assert service._active_operations == 0 and service._delivery_locks == {}
