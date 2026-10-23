"""Cooperative, diagnostic-only polling; never launches or accepts a native turn."""

import copy
import math
import re
import time
from dataclasses import dataclass

import native_v1_probe as probe

ROLES = ("receiver", "sender")
KEYS = {"receiver": "receiver_native_text_diagnostic", "sender": "native_text_diagnostic"}


@dataclass(frozen=True)
class RoleScope:
    terminal_id: str
    expected_turn: int
    expected_agent: str

    def __post_init__(self):
        if (
            type(self.expected_turn) is not int
            or not 1 <= self.expected_turn <= 2**63 - 1
            or any(
                type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is None
                for value in (self.terminal_id, self.expected_agent)
            )
        ):
            raise ValueError("invalid_role_scope")


def _unavailable(reason):
    return dict(
        status="unavailable", reason=reason, raw_text_exported=False, acceptance_input_changed=False
    )


def _project_probe(result):
    """Copy only the native reader's bounded diagnostic vocabulary."""
    if (
        type(result) is not dict
        or result.get("schema") != "caos.t089.native-text-shapes.v1"
        or result.get("diagnostic_only") is not True
        or result.get("raw_text_exported") is not False
        or result.get("acceptance_input_changed") is not False
        or result.get("status") not in ("observed", "unavailable")
    ):
        raise ValueError("probe_result_invalid")
    safe = {
        key: result[key]
        for key in (
            "schema",
            "diagnostic_only",
            "raw_text_exported",
            "acceptance_input_changed",
            "status",
        )
    }
    for key in (
        "active_receipt_bound",
        "exact_owned_root",
        "native_equals_api",
        "native_agent_matches",
        "any_single_text_contains_exact_marker",
        "raw_model_response_observed",
        "within_three_seconds",
        "within_original_deadline",
    ):
        if key in result:
            value = result[key]
            if type(value) is not bool and not (key == "native_agent_matches" and value is None):
                raise ValueError("probe_result_invalid")
            safe[key] = value
    for key, limit in (("text_parts", probe.PARTS), ("elapsed_ms", 2**31 - 1)):
        if key in result:
            if type(result[key]) is not int or not 0 <= result[key] <= limit:
                raise ValueError("probe_result_invalid")
            safe[key] = result[key]

    def shape(value):
        if type(value) is not dict:
            raise ValueError("probe_result_invalid")
        output = {}
        for key in ("characters", "lines"):
            if key in value:
                if type(value[key]) is not int or not 0 <= value[key] <= probe.CAP:
                    raise ValueError("probe_result_invalid")
                output[key] = value[key]
        for key in (
            "contains_marker",
            "nfc_marker",
            "nfkc_marker",
            "exact_marker",
            "compact_marker",
        ):
            if key in value:
                if type(value[key]) is not bool:
                    raise ValueError("probe_result_invalid")
                output[key] = value[key]
        return output

    for key in ("native_public", "rendered_public"):
        if key in result:
            safe[key] = shape(result[key])
    if "text_part_shapes" in result:
        values = result["text_part_shapes"]
        if type(values) is not list or len(values) > probe.PARTS:
            raise ValueError("probe_result_invalid")
        safe["text_part_shapes"] = [shape(value) for value in values]
    if "classification" in result:
        value = result["classification"]
        safe["classification"] = (
            value
            if value
            in (
                "multiple_native_text_parts",
                "native_marker_api_missing",
                "native_text_contains_linebreak",
                "other_native_text_shape",
            )
            else "unclassified"
        )
    if "reason" in result:
        value = result["reason"]
        safe["reason"] = (
            value
            if value
            in (
                "native_agent_mismatch",
                "native_agent_metadata_invalid",
                "active_nonce_not_unique",
                "pg_receipt_missing_or_incomplete",
                "ownership_missing",
                "deadline",
            )
            else "native_probe_unavailable"
        )
    return safe


class NativePhaseObserver:
    """Call tick independently of sender acceptance from the existing phase loop.

    Readers must be read-only and use the supplied absolute stop time for every
    I/O operation. Time checks reject late returns, but cannot preempt a callback.
    Each tick polls one pending role; one budget covers its state, frames and probe.
    No sleep, thread, state writer, dispatch or acceptance callback is used.
    """

    def __init__(
        self,
        *,
        run_root,
        cao_home,
        target,
        scopes,
        phase_started,
        read_state,
        read_output,
        capture_viewport,
        clock=None,
    ):
        self._clock = clock if clock is not None else time.monotonic
        now = self._now()
        if (
            type(phase_started) not in (int, float)
            or not math.isfinite(phase_started)
            or phase_started > now
            or not isinstance(scopes, dict)
            or set(scopes) != set(ROLES)
            or any(not isinstance(scopes[role], RoleScope) for role in ROLES)
            or len({scope.terminal_id for scope in scopes.values()}) != 2
            or any(not callable(reader) for reader in (read_state, read_output, capture_viewport))
        ):
            raise ValueError("invalid_phase_scope")
        self._deadline = phase_started + 75.0
        self._scopes = dict(scopes)
        self._root, self._home, self._target = run_root, cao_home, target
        self._state, self._output, self._viewport = read_state, read_output, capture_viewport
        self._next_poll = now
        self._priority = "receiver"
        self._closed = False
        self._states = {role: "pending" for role in ROLES}
        self._results = {role: _unavailable("not_observed_yet") for role in ROLES}

    def _now(self):
        value = self._clock()
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or value < getattr(self, "_last_now", value)
        ):
            raise ValueError("invalid_clock")
        self._last_now = value
        return value

    def _fail(self, role, reason):
        self._states[role] = "unavailable"
        self._results[role] = _unavailable(reason)

    def _expire(self):
        for role in ROLES:
            if self._states[role] == "pending":
                self._states[role] = "not_triggered"
                self._results[role] = _unavailable("not_triggered_by_deadline")
        self._close()

    def _close(self):
        self._closed = True
        self._scopes = {}
        self._root = self._home = self._target = None
        self._state = self._output = self._viewport = None
        self._clock = time.monotonic

    def snapshot(self):
        return {
            **{KEYS[role]: copy.deepcopy(self._results[role]) for role in ROLES},
            "native_observation_states": dict(self._states),
        }

    def abort(self):
        """Close an interrupted phase without observing or accepting missing work."""
        for role in ROLES:
            if self._states[role] == "pending":
                self._fail(role, "phase_aborted")
        self._close()
        return self.snapshot()

    def finish(self):
        if self._closed:
            return self.snapshot()
        if self._now() >= self._deadline:
            self._expire()
        elif any(value == "pending" for value in self._states.values()):
            raise ValueError("phase_not_finished")
        self._close()
        return self.snapshot()

    def tick(self):
        if self._closed:
            return self.snapshot()
        now = self._now()
        if now >= self._deadline:
            self._expire()
        if self._closed or now < self._next_poll:
            return self.snapshot()
        self._next_poll = now + 0.25
        stop = min(self._deadline, now + 3.0)
        order = (self._priority, "sender" if self._priority == "receiver" else "receiver")
        for role in order:
            if self._states[role] != "pending":
                continue
            if self._now() >= stop:
                break
            scope = self._scopes[role]
            self._priority = "sender" if role == "receiver" else "receiver"
            try:
                state = self._state(scope.terminal_id, stop)
            except Exception:
                self._fail(role, "state_unavailable")
                break
            if self._now() > stop:
                self._fail(role, "observation_deadline")
                break
            if (
                type(state) is not dict
                or type(state.get("http_status")) is not int
                or state["http_status"] != 200
                or any(
                    type(state.get(key)) is not int or not 0 <= state[key] <= 2**63 - 1
                    for key in ("turn_sequence", "turn_completed")
                )
                or type(state.get("status")) is not str
                or state["status"] not in {"idle", "processing", "completed"}
            ):
                self._fail(role, "state_unavailable")
                break
            if (
                state["turn_sequence"] > scope.expected_turn
                or state["turn_completed"] > scope.expected_turn
            ):
                self._fail(role, "unexpected_turn")
                break
            if (
                state["status"] != "completed"
                or state["turn_sequence"] != scope.expected_turn
                or state["turn_completed"] != scope.expected_turn
            ):
                break
            self._observe(role, scope, stop)
            break  # At most one frame/probe attempt per tick, even after failure.
        if self._now() >= self._deadline:
            self._expire()
        elif all(value != "pending" for value in self._states.values()):
            self._close()
        return self.snapshot()

    def _observe(self, role, scope, stop):
        self._states[role] = "unavailable"  # One-shot, including failed reads/captures.
        frames = []
        try:
            for reader in (self._output, self._viewport):
                if self._now() >= stop:
                    self._fail(role, "observation_deadline")
                    return
                frame = reader(scope.terminal_id, stop, max_bytes=probe.CAP + 1)
                if self._now() > stop:
                    self._fail(role, "observation_deadline")
                    return
                if type(frame) is not str or len(frame.encode("utf-8")) > probe.CAP:
                    self._fail(role, "frame_size_or_type")
                    return
                frames.append(frame)
        except Exception:
            self._fail(role, "capture_or_output_unavailable")
            return
        if self._now() >= stop:
            self._fail(role, "observation_deadline")
            return
        try:
            result = probe.observe(
                self._root,
                self._home,
                scope.terminal_id,
                self._target,
                frames[1],
                frames[0],
                stop,
                expected="ACK" if role == "receiver" else probe.MARKER,
                expected_agent=scope.expected_agent,
            )
        except Exception:
            self._fail(role, "probe_unavailable")
            return
        try:
            result = _project_probe(result)
        except Exception:
            self._fail(role, "probe_result_invalid")
            return
        finished = self._now()
        result["within_three_seconds"] = (
            result.get("within_three_seconds") is True and finished <= stop
        )
        result["within_original_deadline"] = (
            result.get("within_original_deadline") is True and finished <= self._deadline
        )
        if (
            finished > stop
            or result["within_three_seconds"] is not True
            or result["within_original_deadline"] is not True
        ):
            result.update(status="unavailable", reason="observation_deadline")
        self._results[role] = result
        self._states[role] = "observed" if result.get("status") == "observed" else "unavailable"
