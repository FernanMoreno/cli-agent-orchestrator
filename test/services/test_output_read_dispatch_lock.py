"""Plain output reads must not wait for input; authority and receipts stay fenced."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services import terminal_service as terminal
from cli_agent_orchestrator.services import work_terminal


class _DispatchContended(RuntimeError):
    pass


@pytest.fixture
def contended_dispatch(monkeypatch):
    @contextmanager
    def busy(*_args, **_kwargs):
        raise _DispatchContended("input dispatch is held")
        yield

    monkeypatch.setattr(work_terminal, "terminal_dispatch_lock", busy)
    monkeypatch.setattr(
        work_terminal.DatabaseTarget,
        "from_settings",
        classmethod(lambda _cls, **_kwargs: None),
    )


@pytest.mark.parametrize("capability", [False, None, 0, 1, "true"])
def test_plain_output_read_does_not_wait_for_input(contended_dispatch, capability):
    provider = SimpleNamespace(requires_turn_receipt=capability)
    assert (
        terminal._verify_receipt_bearing_result(
            "terminal", provider, "captured transcript", "extracted result"
        )
        == "extracted result"
    )


@pytest.mark.parametrize(
    ("capability", "ordinary_observation"), [(True, False), (True, True), (False, True)]
)
def test_receipt_and_ownership_checks_keep_dispatch_fence(
    contended_dispatch, capability, ordinary_observation
):
    provider = SimpleNamespace(requires_turn_receipt=capability)
    with pytest.raises(_DispatchContended, match="input dispatch is held"):
        terminal._verify_receipt_bearing_result(
            "terminal",
            provider,
            "captured transcript",
            "extracted result",
            ordinary_observation=ordinary_observation,
        )


def test_ordinary_observation_checks_work_owner_inside_fence(monkeypatch):
    active = []
    events = []

    @contextmanager
    def fenced(*_args, **_kwargs):
        active.append(True)
        events.append("locked")
        try:
            yield None
        finally:
            active.pop()
            events.append("released")

    def owned(terminal_id):
        assert terminal_id == "terminal" and active
        events.append("checked")
        raise ValueError("owned by Work")

    monkeypatch.setattr(work_terminal, "terminal_dispatch_lock", fenced)
    monkeypatch.setattr(
        work_terminal.DatabaseTarget,
        "from_settings",
        classmethod(lambda _cls, **_kwargs: None),
    )
    monkeypatch.setattr(terminal, "ensure_terminal_is_not_work_owned", owned)
    with pytest.raises(ValueError, match="owned by Work"):
        terminal._verify_receipt_bearing_result(
            "terminal",
            SimpleNamespace(requires_turn_receipt=False),
            "transcript",
            "result",
            ordinary_observation=True,
        )
    assert events == ["locked", "checked", "released"]


def test_receipt_observer_unwrapped_surface_is_preserved(contended_dispatch):
    assert (
        terminal._verify_receipt_bearing_result.__wrapped__(
            "terminal", SimpleNamespace(requires_turn_receipt=False), "transcript", "result"
        )
        == "result"
    )
