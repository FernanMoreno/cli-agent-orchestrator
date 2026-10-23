"""Synthetic polling schedules; no API, native CLI, model or operator data."""

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import native_phase_observer as phase

SECRET = "private-frame-never-export"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def completed(sequence=1):
    return dict(
        http_status=200, status="completed", turn_sequence=sequence, turn_completed=sequence
    )


def observed():
    return dict(
        schema="caos.t089.native-text-shapes.v1",
        diagnostic_only=True,
        status="observed",
        native_agent_matches=True,
        active_receipt_bound=True,
        exact_owned_root=True,
        within_three_seconds=True,
        within_original_deadline=True,
        raw_text_exported=False,
        acceptance_input_changed=False,
    )


class PhaseTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.events = []
        self.scopes = {
            role: phase.RoleScope(role, 1, "v1-" + role) for role in ("sender", "receiver")
        }

    def make(self, state=None, output=None, viewport=None, **kwargs):
        def read_state(terminal, stop):
            self.events.append(("state", terminal, stop))
            return completed() if state is None else state(terminal, stop)

        def read_output(terminal, stop, *, max_bytes):
            self.assertEqual(max_bytes, phase.probe.CAP + 1)
            self.events.append(("output", terminal, stop))
            return "ACK" if output is None else output(terminal, stop)

        def capture(terminal, stop, *, max_bytes):
            self.assertEqual(max_bytes, phase.probe.CAP + 1)
            self.events.append(("capture", terminal, stop))
            return SECRET if viewport is None else viewport(terminal, stop)

        return phase.NativePhaseObserver(
            run_root="/unused",
            cao_home="/unused/cao",
            target=None,
            scopes=self.scopes,
            phase_started=0.0,
            clock=self.clock,
            read_state=read_state,
            read_output=read_output,
            capture_viewport=capture,
            **kwargs,
        )

    def test_receiver_is_probed_before_sender_without_acceptance_input(self):
        observer = self.make()
        with patch.object(phase.probe, "observe", return_value=observed()) as probe:
            first = observer.tick()
            self.assertEqual(probe.call_count, 1)
            self.assertEqual(probe.call_args.args[2], "receiver")
            self.assertEqual(
                probe.call_args.kwargs, dict(expected="ACK", expected_agent="v1-receiver")
            )
            self.assertEqual(first["receiver_native_text_diagnostic"]["status"], "observed")
            self.clock.now = 0.3
            observer.tick()
            self.assertEqual(probe.call_count, 2)
            self.assertEqual(probe.call_args.args[2], "sender")
            self.assertEqual(probe.call_args.kwargs["expected"], phase.probe.MARKER)
            self.clock.now = 1.0
            observer.tick()
            self.assertEqual(probe.call_count, 2)

    def test_waiting_receiver_does_not_starve_sender(self):
        observer = self.make(
            state=lambda terminal, stop: completed(0 if terminal == "receiver" else 1)
        )
        with patch.object(phase.probe, "observe", return_value=observed()) as probe:
            observer.tick()
            probe.assert_not_called()
            self.clock.now = 0.25
            result = observer.tick()
        self.assertEqual(probe.call_args.args[2], "sender")
        self.assertEqual(result["native_observation_states"]["receiver"], "pending")

    def test_slow_pending_receiver_cannot_starve_sender_across_ticks(self):
        def state(terminal, stop):
            if terminal == "receiver":
                self.clock.now = stop
                return completed(0)
            return completed()

        observer = self.make(state=state)
        with patch.object(phase.probe, "observe", return_value=observed()) as probe:
            observer.tick()
            probe.assert_not_called()
            self.clock.now += 0.3
            observer.tick()
        self.assertEqual(probe.call_args.args[2], "sender")

    def test_slow_waiting_role_does_not_spend_other_roles_probe_budget(self):
        def state(terminal, stop):
            if terminal == "receiver":
                self.clock.now = stop - 0.1
                return completed(0)
            return completed()

        def needs_pg_margin(*args, **kwargs):
            if args[6] - self.clock.now < 1.0:
                raise RuntimeError("insufficient_pg_margin")
            return observed()

        observer = self.make(state=state)
        with patch.object(phase.probe, "observe", side_effect=needs_pg_margin):
            observer.tick()
            self.clock.now += 0.3
            result = observer.tick()
        self.assertEqual(result["native_text_diagnostic"]["status"], "observed")

    def test_backward_clock_fails_before_more_io(self):
        observer = self.make(state=lambda *_: completed(0))
        self.clock.now = 2.0
        observer.tick()
        before = list(self.events)
        self.clock.now = 1.0
        with self.assertRaisesRegex(ValueError, "invalid_clock"):
            observer.tick()
        self.assertEqual(self.events, before)

    def test_probe_own_late_witness_is_unavailable(self):
        observer = self.make()
        with patch.object(
            phase.probe, "observe", return_value={**observed(), "within_three_seconds": False}
        ):
            result = observer.tick()
        self.assertEqual(result["receiver_native_text_diagnostic"]["status"], "unavailable")
        self.assertEqual(
            result["receiver_native_text_diagnostic"]["reason"], "observation_deadline"
        )

    def test_probe_result_types_and_private_extra_fields_are_minimized(self):
        for value in (
            None,
            SECRET,
            [],
            {**observed(), "schema": SECRET},
            {**observed(), "text_parts": SECRET},
            {**observed(), "raw_text_exported": True},
        ):
            with self.subTest(kind=type(value).__name__):
                observer = self.make()
                with patch.object(phase.probe, "observe", return_value=value):
                    result = observer.tick()
                self.assertNotIn(SECRET, json.dumps(result))
                self.assertEqual(
                    result["receiver_native_text_diagnostic"]["reason"], "probe_result_invalid"
                )
        observer = self.make()
        extra = {
            **observed(),
            "raw_text": SECRET,
            "reason": SECRET,
            "classification": SECRET,
            "native_public": {"characters": 3, "contains_marker": False, "raw": SECRET},
        }
        with patch.object(phase.probe, "observe", return_value=extra):
            result = observer.tick()
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertFalse(
            result["receiver_native_text_diagnostic"]["native_public"]["contains_marker"]
        )

    def test_closing_releases_private_context_and_reader_references(self):
        for expire in (False, True):
            self.clock.now = 0.0
            with self.subTest(expire=expire):
                observer = self.make()
                if expire:
                    self.clock.now = 75.0
                    observer.finish()
                else:
                    with patch.object(phase.probe, "observe", return_value=observed()):
                        observer.tick()
                        self.clock.now = 0.25
                        observer.tick()
                    observer.finish()
                for name in ("_root", "_home", "_target", "_state", "_output", "_viewport"):
                    self.assertIsNone(getattr(observer, name))
                self.assertEqual(observer._scopes, {})

    def test_failed_sender_does_not_prevent_later_receiver(self):
        def state(terminal, stop):
            return completed(1 if terminal == "sender" or self.clock.now >= 1 else 0)

        observer = self.make(state=state)
        failed_sender = {**observed(), "native_public": {"contains_marker": False}}
        with patch.object(phase.probe, "observe", side_effect=[failed_sender, observed()]) as probe:
            observer.tick()
            self.clock.now = 1.0
            observer.tick()
            self.clock.now = 1.3
            result = observer.tick()
        self.assertEqual([call.args[2] for call in probe.call_args_list], ["sender", "receiver"])
        self.assertFalse(result["native_text_diagnostic"]["native_public"]["contains_marker"])
        self.assertEqual(result["receiver_native_text_diagnostic"]["status"], "observed")

    def test_polling_is_throttled_and_snapshots_are_copies(self):
        observer = self.make(state=lambda *_: completed(0))
        first = observer.tick()
        first["receiver_native_text_diagnostic"]["reason"] = SECRET
        observer.tick()
        self.assertEqual(len(self.events), 1)
        self.assertNotIn(SECRET, json.dumps(observer.snapshot()))
        self.clock.now = 0.25
        observer.tick()
        self.assertEqual(len(self.events), 2)

    def test_untriggered_deadline_does_not_mean_wrong_profile(self):
        observer = self.make(state=lambda *_: completed(0))
        self.clock.now = 75.0
        result = observer.tick()
        self.assertEqual(self.events, [])
        self.assertEqual(
            result["receiver_native_text_diagnostic"]["reason"], "not_triggered_by_deadline"
        )
        self.assertNotIn("native_agent_matches", result["receiver_native_text_diagnostic"])
        self.assertFalse(phase.probe.native_profiles_exact([result, result]))

    def test_stale_and_invalid_states_never_reach_capture(self):
        for bad in (
            completed(0),
            {**completed(), "turn_sequence": True},
            {**completed(), "turn_completed": False},
            {**completed(), "http_status": 500},
            {**completed(), "status": "processing"},
            {**completed(), "status": []},
            None,
            SECRET,
        ):
            with self.subTest(bad=bad):
                self.events = []
                observer = self.make(state=lambda *_: bad)
                with patch.object(phase.probe, "observe") as probe:
                    observer.tick()
                probe.assert_not_called()
                self.assertTrue(all(event[0] == "state" for event in self.events))

    def test_future_turn_is_unavailable_without_probe(self):
        observer = self.make(state=lambda *_: completed(2))
        with patch.object(phase.probe, "observe") as probe:
            result = observer.tick()
        probe.assert_not_called()
        self.assertEqual(result["receiver_native_text_diagnostic"]["reason"], "unexpected_turn")

    def test_state_exception_exports_fixed_code_only(self):
        def bad(*_):
            raise RuntimeError(SECRET)

        observer = self.make(state=bad)
        result = observer.tick()
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(result["receiver_native_text_diagnostic"]["reason"], "state_unavailable")

    def test_capture_exception_is_one_shot_and_does_not_leak(self):
        def bad(*_):
            raise RuntimeError(SECRET)

        observer = self.make(viewport=bad)
        with patch.object(phase.probe, "observe") as probe:
            result = observer.tick()
            self.clock.now = 1.0
            observer.tick()
        probe.assert_not_called()
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(sum(e[:2] == ("capture", "receiver") for e in self.events), 1)

    def test_oversize_capture_is_rejected_before_probe(self):
        observer = self.make(viewport=lambda *_: "界" * 22000)
        with patch.object(phase.probe, "observe") as probe:
            result = observer.tick()
        probe.assert_not_called()
        self.assertEqual(result["receiver_native_text_diagnostic"]["reason"], "frame_size_or_type")

    def test_exact_byte_boundaries_for_both_frames(self):
        for reader in ("output", "viewport"):
            for value, accepted in (
                ("x" * phase.probe.CAP, True),
                ("x" * (phase.probe.CAP + 1), False),
                ("界" * 22000, False),
                (b"bytes", False),
            ):
                with self.subTest(reader=reader, size=len(value)):
                    observer = self.make(**{reader: lambda *_, value=value: value})
                    with patch.object(phase.probe, "observe", return_value=observed()) as probe:
                        observer.tick()
                    self.assertEqual(probe.call_count, int(accepted))

    def test_output_overrun_prevents_capture_and_probe(self):
        def slow(*_):
            self.clock.now = 3.1
            return "ACK"

        observer = self.make(output=slow)
        with patch.object(phase.probe, "observe") as probe:
            observer.tick()
        probe.assert_not_called()
        self.assertFalse(any(event[0] == "capture" for event in self.events))

    def test_probe_exception_is_minimized_and_one_shot(self):
        observer = self.make()
        with patch.object(phase.probe, "observe", side_effect=RuntimeError(SECRET)) as probe:
            result = observer.tick()
            self.clock.now = 0.3
            observer.tick()
            self.clock.now = 1.0
            observer.tick()
        self.assertEqual(probe.call_count, 2)
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(result["receiver_native_text_diagnostic"]["reason"], "probe_unavailable")

    def test_finish_closes_successful_observers_without_more_reads(self):
        observer = self.make()
        with patch.object(phase.probe, "observe", return_value=observed()):
            observer.tick()
            self.clock.now = 0.25
            observer.tick()
        observer.finish()
        before = list(self.events)
        self.clock.now = 1.0
        observer.tick()
        self.assertEqual(self.events, before)

    def test_callback_overrun_stops_further_io(self):
        def slow(*_):
            self.clock.now = 4.0
            return completed()

        observer = self.make(state=slow)
        with patch.object(phase.probe, "observe") as probe:
            result = observer.tick()
        probe.assert_not_called()
        self.assertEqual(len(self.events), 1)
        self.assertEqual(
            result["receiver_native_text_diagnostic"]["reason"], "observation_deadline"
        )

    def test_probe_overrun_cannot_supply_timely_identity(self):
        observer = self.make()

        def slow(*args, **kwargs):
            self.clock.now = 4.0
            return observed()

        with patch.object(phase.probe, "observe", side_effect=slow):
            result = observer.tick()
        self.assertFalse(result["receiver_native_text_diagnostic"]["within_three_seconds"])
        self.assertFalse(phase.probe.native_profiles_exact([result, result]))

    def test_whole_tick_and_end_of_phase_share_original_budget(self):
        self.clock.now = 74.0
        observer = self.make()
        with patch.object(phase.probe, "observe", return_value=observed()):
            observer.tick()
        self.assertTrue(all(event[2] == 75.0 for event in self.events))

    def test_abort_releases_pending_context_without_io_or_profile_claim(self):
        observer = self.make()
        result = observer.abort()
        self.assertEqual(self.events, [])
        self.assertEqual(result["receiver_native_text_diagnostic"]["reason"], "phase_aborted")
        self.assertNotIn("native_agent_matches", result["receiver_native_text_diagnostic"])
        self.assertEqual(observer._scopes, {})
        self.assertFalse(phase.probe.native_profiles_exact([result, result]))

    def test_finish_cannot_hide_pending_receiver_before_deadline(self):
        observer = self.make()
        with self.assertRaisesRegex(ValueError, "phase_not_finished"):
            observer.finish()
        self.clock.now = 75.0
        self.assertEqual(
            observer.finish()["native_observation_states"]["receiver"], "not_triggered"
        )

    def test_invalid_scope_and_start_fail_before_io(self):
        for turn in (True, 0, -1, "1"):
            with self.subTest(turn=turn), self.assertRaises(ValueError):
                phase.RoleScope("receiver", turn, "v1-receiver")
        for start in (True, float("nan"), float("inf"), 1.0):
            with self.subTest(start=start), self.assertRaises(ValueError):
                phase.NativePhaseObserver(
                    run_root="/unused",
                    cao_home="/unused",
                    target=None,
                    scopes=self.scopes,
                    phase_started=start,
                    clock=self.clock,
                    read_state=None,
                    read_output=None,
                    capture_viewport=None,
                )
        self.assertEqual(self.events, [])


class SQLiteCompositionTests(unittest.TestCase):
    def test_two_phases_actual_owned_receipt_bound_probe_and_profile_gate(self):
        from cli_agent_orchestrator.utils.opencode_ownership import PrivateConfigOwnership

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            os.chmod(root, 0o700)
            home = root / "home/.aws/cli-agent-orchestrator"
            home.mkdir(parents=True, mode=0o700)
            terminal_ids = {"receiver": "a1b2c3d4", "sender": "e5f60718"}
            role_for = {terminal: role for role, terminal in terminal_ids.items()}
            paths = {}
            for role in ("receiver", "sender"):
                owned = PrivateConfigOwnership(home, terminal_ids[role]).allocate(1)
                dest = owned / "data/opencode"
                dest.mkdir(parents=True)
                paths[role] = dest / "opencode.db"
                with closing(sqlite3.connect(paths[role])) as db:
                    db.executescript(
                        "CREATE TABLE message(id TEXT,session_id TEXT,data TEXT); CREATE TABLE part(id TEXT,message_id TEXT,session_id TEXT,data TEXT);"
                    )
            phases = []
            for sequence in (1, 2):
                nonces = {
                    role: "CAO-TURN-RECEIPT-"
                    + hashlib.sha256((role + str(sequence)).encode()).hexdigest()[:32]
                    for role in paths
                }
                bodies = {"receiver": "ACK", "sender": phase.probe.MARKER}
                for role, path in paths.items():
                    with closing(sqlite3.connect(path)) as db:
                        user, assistant = "u" + str(sequence), "a" + str(sequence)
                        for mid, data in (
                            (user, dict(role="user")),
                            (
                                assistant,
                                dict(
                                    role="assistant",
                                    parentID=user,
                                    time={"completed": sequence},
                                    agent="v1-" + role,
                                ),
                            ),
                        ):
                            db.execute(
                                "INSERT INTO message VALUES(?,?,?)",
                                (mid, "session", json.dumps(data)),
                            )
                        for mid, text in (
                            (user, nonces[role]),
                            (assistant, bodies[role] + "\n" + nonces[role]),
                        ):
                            db.execute(
                                "INSERT INTO part VALUES(?,?,?,?)",
                                (mid, mid, "session", json.dumps(dict(type="text", text=text))),
                            )
                        db.commit()
                before = {role: path.read_bytes() for role, path in paths.items()}
                observer = phase.NativePhaseObserver(
                    run_root=root,
                    cao_home=home,
                    target=None,
                    scopes={
                        role: phase.RoleScope(terminal_ids[role], sequence, "v1-" + role)
                        for role in paths
                    },
                    phase_started=time.monotonic(),
                    read_state=lambda *_: completed(sequence),
                    read_output=lambda terminal, stop, max_bytes: bodies[role_for[terminal]],
                    capture_viewport=lambda terminal, stop, max_bytes: nonces[role_for[terminal]],
                )
                with patch.object(
                    phase.probe,
                    "_digest",
                    side_effect=lambda target, terminal, stop: hashlib.sha256(
                        nonces[role_for[terminal]].encode()
                    ).hexdigest(),
                ):
                    observer.tick()
                    # Use the same real monotonic clock as the actual native probe.
                    time.sleep(0.25)
                    observer.tick()
                    result = observer.finish()
                phases.append(result)
                self.assertEqual(before, {role: path.read_bytes() for role, path in paths.items()})
                serialized = json.dumps(result)
                for private in (
                    *nonces.values(),
                    *terminal_ids.values(),
                    str(root),
                    phase.probe.MARKER,
                ):
                    self.assertNotIn(private, serialized)
                for role in paths:
                    self.assertIs(result[phase.KEYS[role]]["native_equals_api"], True)
            self.assertTrue(phase.probe.native_profiles_exact(phases))


if __name__ == "__main__":
    unittest.main()
