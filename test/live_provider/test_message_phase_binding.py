"""Current-turn evidence must not be replaced by cached terminal readiness."""

from pathlib import Path
from types import SimpleNamespace

import message_api_restart_live as live
import pytest
from message_api_restart import PRE_RESTART_MESSAGE

pytestmark = pytest.mark.offline_database


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"success": False, "turn_sequence": 1},
        {"success": True, "turn_sequence": True},
        {"success": True, "turn_sequence": 0},
        {"success": True, "turn_sequence": "1"},
        {"success": True},
    ],
)
def test_trigger_refuses_ambiguous_ack_without_resending(monkeypatch, body):
    calls = []
    monkeypatch.setattr(live, "_api_request", lambda *a, **k: calls.append(True) or (200, body))
    with pytest.raises(live.AcceptanceFailure):
        live._submit_trigger("http://private.test", "a" * 8, "before_restart")
    assert calls == [True]


def test_trigger_returns_actual_accepted_dispatch_sequence(monkeypatch):
    monkeypatch.setattr(
        live, "_api_request", lambda *a, **k: (200, {"success": True, "turn_sequence": 7})
    )
    assert live._submit_trigger("http://private.test", "a" * 8, "before_restart") == 7


def test_missing_terminal_stays_an_explicit_fixed_failure(monkeypatch):
    monkeypatch.setattr(live, "_api_request", lambda *a, **k: (404, {}))
    observation = live._terminal_turn_observation(
        "http://private.test",
        "a" * 8,
        provider="codex",
        failure_stage="phase_a",
        deadline=live.time.monotonic() + 1,
    )
    assert observation.status == "missing"
    assert not observation.verified


@pytest.mark.parametrize(
    "change", [None, "identity", "provider", "sequence", "counter", "generation", "pending"]
)
def test_phase_observation_requires_owned_typed_verified_turn(monkeypatch, change):
    function = getattr(live, "_terminal_turn_observation", None)
    assert callable(function), "causal terminal observation is missing"
    data = {
        "id": "a" * 8,
        "provider": "codex",
        "status": "completed",
        "turn_sequence": 2,
        "turn_completed": 1,
        "turn": {
            "terminal_id": "a" * 8,
            "provider": "codex",
            "state": "verified",
            "generation": "c" * 32,
        },
        "private_extra": "PRIVATE_RESPONSE_SENTINEL",
    }
    if change == "identity":
        data["id"] = "b" * 8
    if change == "provider":
        data["turn"]["provider"] = "claude_code"
    if change == "sequence":
        data["turn_sequence"] = True
    if change == "counter":
        data["turn_completed"] = True
    if change == "generation":
        data["turn"]["generation"] = "not-a-generation"
    if change == "pending":
        data["turn"]["state"] = "pending"
    monkeypatch.setattr(live, "_api_request", lambda *a, **k: (200, data))
    observation = function(
        "http://private.test",
        "a" * 8,
        provider="codex",
        failure_stage="phase_a",
        deadline=live.time.monotonic() + 1,
    )
    assert observation.verified is (change is None)
    assert "PRIVATE_RESPONSE_SENTINEL" not in repr(observation)


@pytest.mark.parametrize(
    "sequence,completed,verified", [(2, 1, False), (2, 2, False), (1, 1, True)]
)
def test_cached_ready_cannot_classify_unverified_current_turn_as_no_tool(
    monkeypatch, tmp_path, sequence, completed, verified
):
    state = live.RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=tmp_path / "workspace",
        home=tmp_path / "home",
        source_root=tmp_path,
        audit_path=tmp_path / "audit.jsonl",
    )
    state.sender_id, state.receiver_id = "a" * 8, "b" * 8
    state.active_server = SimpleNamespace(url="http://private.test")
    state.expected_turn_sequence = 2
    clock = [100.0]
    monkeypatch.setattr(live.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        live.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    monkeypatch.setattr(live, "_audit_failure_present", lambda state: False)
    monkeypatch.setattr(live, "read_dispatch_audit", lambda path: [])
    monkeypatch.setattr(live, "_get_rows", lambda *a, **k: [])
    monkeypatch.setattr(live, "_terminal_status", lambda *a, **k: "completed")
    observation = SimpleNamespace(
        status="completed",
        sequence=sequence,
        completed=completed,
        generation="c" * 32,
        verified=verified,
    )
    monkeypatch.setattr(
        live, "_terminal_turn_observation", lambda *a, **k: observation, raising=False
    )

    class Capture:
        def take_until(self, deadline):
            return []

    with pytest.raises(live.AcceptanceFailure):
        live._await_phase(
            state,
            capture=Capture(),
            expected_bodies=(PRE_RESTART_MESSAGE,),
            phase_body=PRE_RESTART_MESSAGE,
            expected_call_count=1,
            phase="before_restart",
        )
    assert state.failure_kind == "phase_timeout", "cached ready is not current-turn completion"
    assert clock[0] >= 280.0


@pytest.mark.parametrize("generation_changes", [False, True])
def test_effects_require_completion_of_the_bound_generation(
    monkeypatch, tmp_path, generation_changes
):
    from message_api_restart import expected_persisted_message

    state = live.RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=tmp_path / "workspace",
        home=tmp_path / "home",
        source_root=tmp_path,
        audit_path=tmp_path / "audit.jsonl",
    )
    state.sender_id, state.receiver_id = "a" * 8, "b" * 8
    state.active_server = SimpleNamespace(url="http://private.test")
    state.expected_turn_sequence = 2
    clock = [100.0]
    monkeypatch.setattr(live.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        live.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    monkeypatch.setattr(live, "_audit_failure_present", lambda state: False)
    monkeypatch.setattr(
        live,
        "read_dispatch_audit",
        lambda path: [{"tool": "send_message", "status": "completed", "outcome": "success"}],
    )
    row = {
        "id": 1,
        "sender_id": state.sender_id,
        "receiver_id": state.receiver_id,
        "message": expected_persisted_message(PRE_RESTART_MESSAGE, state.sender_id),
        "created_at": "2026-10-28T12:00:00Z",
        "status": "delivered",
    }
    monkeypatch.setattr(live, "_get_rows", lambda *a, **k: [row])
    monkeypatch.setattr(live, "_terminal_status", lambda *a, **k: "completed")
    monkeypatch.setattr(live, "_wait_ready", lambda *a, **k: "completed")

    def observation(*args, **kwargs):
        verified = clock[0] >= 102
        generation = "d" * 32 if verified and generation_changes else "c" * 32
        return live._PhaseTurnObservation("completed", 2, 1, generation, verified)

    monkeypatch.setattr(live, "_terminal_turn_observation", observation)

    class Capture:
        emitted = False

        def take_until(self, deadline):
            if self.emitted:
                return []
            self.emitted = True
            return [True]

    def run():
        return live._await_phase(
            state,
            capture=Capture(),
            expected_bodies=(PRE_RESTART_MESSAGE,),
            phase_body=PRE_RESTART_MESSAGE,
            expected_call_count=1,
            phase="before_restart",
        )

    if generation_changes:
        with pytest.raises(live.AcceptanceFailure):
            run()
        assert state.failure_kind == "mismatch"
    else:
        assert run() == [row]
        assert clock[0] >= 102, "effects do not prove the current model turn completed"
    assert "c" * 32 not in str(state.turn_observations)


@pytest.mark.parametrize("generation", [None, "c" * 32])
def test_trigger_latches_pre_dispatch_receipt_generation(monkeypatch, tmp_path, generation):
    state = live.RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=tmp_path / "workspace",
        home=tmp_path / "home",
        source_root=tmp_path,
        audit_path=tmp_path / "audit.jsonl",
    )
    prior = {
        "terminal_id": "a" * 8,
        "provider": "codex",
        "state": "none" if generation is None else "verified",
        "generation": generation,
    }
    methods = []

    def request(method, *args, **kwargs):
        methods.append(method)
        if method == "GET":
            assert args[0].endswith("/turn"), "baseline must survive live-counter reset"
        return (200, prior) if method == "GET" else (200, {"success": True, "turn_sequence": 1})

    monkeypatch.setattr(live, "_api_request", request)
    assert live._submit_trigger("http://private.test", "a" * 8, "after_restart", state=state) == 1
    assert methods == ["GET", "POST"]
    assert state.previous_turn_generation == generation


def test_input_is_not_sent_without_a_readable_baseline(monkeypatch, tmp_path):
    state = live.RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=tmp_path / "workspace",
        home=tmp_path / "home",
        source_root=tmp_path,
        audit_path=tmp_path / "audit.jsonl",
    )
    methods = []

    def request(method, *args, **kwargs):
        methods.append(method)
        return (503, {}) if method == "GET" else (200, {"success": True, "turn_sequence": 1})

    monkeypatch.setattr(live, "_api_request", request)
    with pytest.raises(live.AcceptanceFailure):
        live._submit_trigger("http://private.test", "a" * 8, "after_restart", state=state)
    assert methods == ["GET"]


def test_phase_b_refuses_previous_receipt_when_sequence_resets(monkeypatch, tmp_path):
    from message_api_restart import POST_RESTART_MESSAGE

    state = live.RunState(
        provider="codex",
        run_root=tmp_path,
        workspace=tmp_path / "workspace",
        home=tmp_path / "home",
        source_root=tmp_path,
        audit_path=tmp_path / "audit.jsonl",
    )
    state.sender_id, state.receiver_id = "a" * 8, "b" * 8
    state.active_server = SimpleNamespace(url="http://private.test")
    state.expected_turn_sequence, state.previous_turn_generation = 1, "c" * 32
    clock = [100.0]
    monkeypatch.setattr(live.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        live.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    monkeypatch.setattr(live, "_audit_failure_present", lambda state: False)
    monkeypatch.setattr(live, "read_dispatch_audit", lambda path: [])
    monkeypatch.setattr(live, "_get_rows", lambda *a, **k: [])
    monkeypatch.setattr(
        live,
        "_terminal_turn_observation",
        lambda *a, **k: live._PhaseTurnObservation("completed", 1, 1, "c" * 32, True),
    )

    class Capture:
        def take_until(self, deadline):
            return []

    with pytest.raises(live.AcceptanceFailure):
        live._await_phase(
            state,
            capture=Capture(),
            expected_bodies=(PRE_RESTART_MESSAGE, POST_RESTART_MESSAGE),
            phase_body=POST_RESTART_MESSAGE,
            expected_call_count=2,
            phase="after_restart",
        )
    assert state.failure_kind == "mismatch"
    assert clock[0] == 100.0
