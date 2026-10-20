"""Deterministic regressions for private service receipt publication after HTTP readiness."""

import json
from test.cli.commands import test_install_opencode as catalog

import pytest

pytestmark = pytest.mark.offline_database


class Clock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []
        self.on_sleep = lambda: None

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds
        self.on_sleep()


class Service:
    def __init__(self, exit_code=None):
        self.exit_code = exit_code

    def poll(self):
        return self.exit_code


@pytest.mark.parametrize("initial", [None, '{"password":'])
def test_waits_for_delayed_or_partial_private_auth_receipt(tmp_path, initial):
    clock = Clock()
    path = tmp_path / "service.json"
    if initial is not None:
        path.write_text(initial)

    def publish():
        if clock.value >= 0.2:
            path.write_text(json.dumps({"password": "synthetic-private-password"}))

    clock.on_sleep = publish
    result = catalog._wait_for_service_password(
        tmp_path, Service(), deadline=0.5, now=clock.now, sleep=clock.sleep
    )
    assert result == "synthetic-private-password"
    assert 0.2 <= clock.value <= 0.5
    assert len(clock.sleeps) == 2


@pytest.mark.parametrize("payload", [{}, {"password": ""}, {"password": None}, {"password": 1}, []])
def test_invalid_receipt_uses_only_remaining_deadline(tmp_path, payload):
    (tmp_path / "service.json").write_text(json.dumps(payload))
    clock = Clock()
    assert (
        catalog._wait_for_service_password(
            tmp_path, Service(), deadline=0.25, now=clock.now, sleep=clock.sleep
        )
        is None
    )
    assert clock.value == pytest.approx(0.25)
    assert clock.sleeps == pytest.approx([0.1, 0.1, 0.05])


def test_exited_service_does_not_poll_or_sleep(tmp_path):
    clock = Clock()
    (tmp_path / "service.json").write_text('{"password":"stale"}')
    assert (
        catalog._wait_for_service_password(
            tmp_path, Service(1), deadline=10, now=clock.now, sleep=clock.sleep
        )
        is None
    )
    assert clock.sleeps == []


def test_expired_deadline_never_reads_an_existing_receipt(tmp_path):
    clock = Clock()
    (tmp_path / "service.json").write_text('{"password":"stale"}')
    assert (
        catalog._wait_for_service_password(
            tmp_path, Service(), deadline=0, now=clock.now, sleep=clock.sleep
        )
        is None
    )
    assert clock.sleeps == []


def test_receipt_finishing_at_deadline_is_not_accepted():
    clock = Clock()

    class Receipt:
        def read_text(self, **kwargs):
            clock.value = 0.25
            return '{"password":"late"}'

    class Profile:
        def rglob(self, pattern):
            return [Receipt()]

    assert (
        catalog._wait_for_service_password(
            Profile(), Service(), deadline=0.25, now=clock.now, sleep=clock.sleep
        )
        is None
    )
    assert clock.sleeps == []


def test_transient_directory_scan_error_stays_within_existing_deadline():
    clock = Clock()

    class Receipt:
        def read_text(self, **kwargs):
            return '{"password":"synthetic-private-password"}'

    class Profile:
        def __init__(self):
            self.calls = 0

        def rglob(self, pattern):
            self.calls += 1
            if self.calls == 1:
                raise FileNotFoundError("synthetic startup directory churn")
            return [Receipt()]

    assert (
        catalog._wait_for_service_password(
            Profile(), Service(), deadline=0.25, now=clock.now, sleep=clock.sleep
        )
        == "synthetic-private-password"
    )
    assert clock.sleeps == [0.1]
