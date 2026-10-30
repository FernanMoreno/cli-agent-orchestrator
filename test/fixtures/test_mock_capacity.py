"""Capacity evidence must reject serial activity and unsafe ramp admission."""

import os
from test.fixtures import mock_capacity as capacity
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.offline_database


def sample(pid=10, birth="100", state="S"):
    return capacity.Process(pid, birth, os.geteuid(), state, 1, 1024)


def test_overlap_requires_distinct_worker_processes():
    value = sample()
    with pytest.raises(AssertionError, match="multiple workers"):
        capacity.confirmed_overlap({"one": value, "two": value}, lambda _: value)


def test_serial_or_reused_processes_do_not_prove_twenty_workers():
    values = {str(i): sample(pid=100 + i) for i in range(20)}
    assert capacity.confirmed_overlap(values, lambda pid: sample(pid=pid, state="Z")) == 0
    assert capacity.confirmed_overlap(values, lambda pid: sample(pid=pid, birth="101")) == 0
    assert capacity.confirmed_overlap(values, lambda pid: None) == 0
    assert capacity.confirmed_overlap(values, lambda pid: sample(pid=pid)) == 20


@pytest.mark.parametrize("memory", [0, 6143])
def test_low_memory_never_admits_another_rung(memory):
    with pytest.raises(AssertionError, match="memory"):
        capacity.admit_rung(2, None, memory)


@pytest.mark.parametrize(
    "previous",
    [
        None,
        {"workers": 20, "passed": False, "cleanup_confirmed": True},
        {"workers": 20, "passed": True, "cleanup_confirmed": False},
        {"workers": 12, "passed": True, "cleanup_confirmed": True},
    ],
)
def test_thirty_two_requires_success_and_cleanup_at_twenty(previous):
    with pytest.raises(AssertionError):
        capacity.admit_rung(32, previous, 6144)


def test_validated_ramp_admission_keeps_twenty_and_thirty_two_distinct():
    capacity.admit_rung(2, None, 6144)
    capacity.admit_rung(20, {"workers": 12, "passed": True, "cleanup_confirmed": True}, 6144)
    capacity.admit_rung(32, {"workers": 20, "passed": True, "cleanup_confirmed": True}, 6144)


def test_extra_pane_cannot_be_collapsed_into_one_worker():
    from test.live_provider.test_mock_cli_capacity import tmux_panes

    result = SimpleNamespace(stdout="owned\t123\nowned\t456\n")
    with patch("test.live_provider.test_mock_cli_capacity.subprocess.run", return_value=result):
        with pytest.raises(AssertionError, match="exactly one pane"):
            tmux_panes("/unused-private-socket", ["owned"])
