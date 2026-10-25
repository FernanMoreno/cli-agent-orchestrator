"""The scheduler must match oldest-nonbusy event order, not grouped-key order."""

import random

import pytest

from cli_agent_orchestrator.services import inbox_service as module

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize("seed", range(8))
def test_pending_selection_matches_independent_event_list_oracle(seed):
    scheduler = module._PendingStatusEvents()
    pending = []
    busy = set()
    rng = random.Random(seed)

    def dispatch():
        expected = next((key for key in pending if key not in busy), None)
        actual = scheduler.pop()
        assert actual == expected
        if actual is not None:
            pending.remove(actual)
            busy.add(actual)

    def complete(key):
        busy.remove(key)
        scheduler.completed(key)

    for _ in range(10000):
        action = rng.randrange(3)
        if action == 0 and len(pending) < 64:
            key = f"terminal-{rng.randrange(16)}"
            pending.append(key)
            scheduler.append(key, busy=key in busy)
        elif action == 1 and len(busy) < 4:
            dispatch()
        elif busy:
            complete(rng.choice(sorted(busy)))
        assert len(scheduler) == len(pending)
        assert set(scheduler._events) == set(pending)
        heads = [key for _ordinal, key in scheduler._ready]
        assert len(heads) == len(set(heads))
        assert set(heads) == set(pending) - busy

    while pending or busy:
        if busy:
            complete(next(iter(busy)))
        while len(busy) < 4:
            old_size = len(pending)
            dispatch()
            if len(pending) == old_size:
                break
    assert len(scheduler) == 0
    assert scheduler._events == {}
    assert scheduler._ready == []


def test_historical_keys_retire_and_busy_key_handoff_keeps_its_events():
    scheduler = module._PendingStatusEvents()
    for i in range(10000):
        key = f"ephemeral-{i}"
        scheduler.append(key, busy=False)
        assert scheduler.pop() == key
        # While that key is in flight, preserve a new event without publishing
        # a second ready head; another key must still be selectable.
        scheduler.append(key, busy=True)
        scheduler.append("other", busy=False)
        assert scheduler.pop() == "other"
        assert scheduler.pop() is None
        scheduler.completed(key)
        assert scheduler.pop() == key
        scheduler.completed(key)
        scheduler.completed("other")
        assert len(scheduler) == 0
        assert scheduler._events == {}
        assert scheduler._ready == []
