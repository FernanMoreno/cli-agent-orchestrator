"""Pure mandatory native acceptance checks, separate from diagnostic identity."""

import copy
import unittest
from datetime import datetime

import native_acceptance_contract as gate


def phase(before):
    return dict(
        receiver_ack_exact=True,
        receiver_ack_within_deadline=True,
        receiver_turn_completed_once=True,
        receiver_sequence_before=before,
        receiver_sequence_after=before + 1,
        receiver_completed_after=before + 1,
    )


class ContractTests(unittest.TestCase):
    def test_ack_is_literal(self):
        self.assertTrue(gate.ack_exact("ACK"))
        for value in (" ACK", "ACK\n", "ack", "A\nCK", "ACK extra", None, True):
            self.assertFalse(gate.ack_exact(value))

    def test_completed_turn_rejects_noninteger_or_late_state(self):
        state = dict(http_status=200, status="completed", turn_sequence=1, turn_completed=1)
        self.assertTrue(gate.turn_exact(state, 1))
        for field, value in (
            ("turn_sequence", True),
            ("turn_completed", False),
            ("turn_completed", 0),
            ("status", "processing"),
            ("http_status", 500),
        ):
            self.assertFalse(gate.turn_exact({**state, field: value}, 1))
        for value in (0, True, None, "1"):
            self.assertFalse(gate.turn_exact(state, value))

    def test_receiver_contract_requires_all_phases_and_counter_continuity(self):
        good = [phase(0), phase(1)]
        self.assertTrue(gate.receiver_phases_exact(good))
        for value in (None, [], [phase(0)], [phase(0), phase(0)], [phase(0), phase(2)]):
            self.assertFalse(gate.receiver_phases_exact(value))
        for index in (0, 1):
            for key in (
                "receiver_ack_exact",
                "receiver_ack_within_deadline",
                "receiver_turn_completed_once",
            ):
                for bad in (False, None, 1):
                    changed = copy.deepcopy(good)
                    changed[index][key] = bad
                    self.assertFalse(gate.receiver_phases_exact(changed))

    def test_profile_evidence_never_substitutes_for_inherited_gates(self):
        data = dict(
            inherited_acceptance_passed=True,
            receiver_ack_acceptance_passed=True,
            native_profile_bindings_exact=True,
            native_profile_configuration_exact=True,
            generation_gate_bound=True,
            sender_unicode_acceptance_passed=True,
            api_restart_identity_exact=True,
            inbox_pairs_exact=True,
            terminal_groups_exact=True,
        )
        self.assertTrue(gate.final_exact(data))
        for key in data:
            for bad in (False, None, 1):
                self.assertFalse(gate.final_exact({**data, key: bad}))

    def test_sender_unicode_is_a_mandatory_final_phase_predicate(self):
        good = dict(
            last_receipt_unicode_verified=True,
            sender_completed=True,
            input_turn_sequence=1,
            sender_sequence_after=1,
            sender_completed_after=1,
        )
        self.assertTrue(gate.sender_phases_exact([good, good]))
        for key in good:
            for bad in (False, None, "missing"):
                self.assertFalse(gate.sender_phases_exact([good, {**good, key: bad}]))

    def test_inbox_pair_requires_full_delivered_identity_for_both_views(self):
        row = dict(
            id=1,
            sender_id="abc12345",
            receiver_id="def12345",
            message="unicode ñ 中文",
            created_at="2026-10-23T00:00:00Z",
            status="delivered",
        )
        self.assertTrue(
            gate.inbox_pair_exact(row, dict(row), "abc12345", "def12345", "unicode ñ 中文")
        )
        for key in row:
            changed = {**row, key: False if key == "id" else "different"}
            self.assertFalse(
                gate.inbox_pair_exact(row, changed, "abc12345", "def12345", "unicode ñ 中文")
            )
        self.assertFalse(
            gate.inbox_pair_exact(
                {**row, "status": "pending"},
                {**row, "status": "pending"},
                "abc12345",
                "def12345",
                "unicode ñ 中文",
            )
        )

    def test_private_pg_timestamp_matches_api_iso_without_changing_row(self):
        created = datetime(2026, 10, 23, 1, 2, 3, 123456)
        pg = dict(
            id=1,
            sender_id="abc12345",
            receiver_id="def12345",
            message="exact",
            created_at=created,
            status="delivered",
        )
        api = {**pg, "created_at": created.isoformat()}
        self.assertTrue(gate.inbox_pair_exact(pg, api, "abc12345", "def12345", "exact"))
        self.assertIs(pg["created_at"], created)
        self.assertFalse(
            gate.inbox_pair_exact(
                pg, {**api, "created_at": "different"}, "abc12345", "def12345", "exact"
            )
        )

    def test_generation_requires_completion_source_and_cleanup(self):
        report = {key: True for key in gate.GENERATION_TRUE}
        report.update(
            binary_sha256=gate.BINARY_SHA,
            runner_sha256="b" * 64,
            plugin_sha256="c" * 64,
            source_head="a" * 40,
            model="opencode-go/glm-5.3-flash",
            native_exit_code=0,
            outcome="native_process_exit",
            model_deadline_seconds=75,
            model_admission_mib=6144,
            events={"step_finish": 1},
            acceptance_claimed=False,
            original_model_acceptance_claimed=False,
            raw_stdout_persisted=False,
            headers_or_bodies_recorded=False,
            error_body_persisted=False,
            mcp_enabled=False,
        )
        bindings = {key: report[key] for key in ("binary_sha256", "runner_sha256", "plugin_sha256")}
        self.assertTrue(gate.generation_exact(report, "a" * 40, bindings))
        for key in bindings:
            self.assertFalse(gate.generation_exact({**report, key: "d" * 64}, "a" * 40, bindings))
        for key in gate.GENERATION_TRUE:
            self.assertFalse(gate.generation_exact({**report, key: False}, "a" * 40, bindings))
        self.assertFalse(
            gate.generation_exact({**report, "model_admission_mib": 6143}, "a" * 40, bindings)
        )
        self.assertFalse(
            gate.generation_exact({**report, "source_head": "b" * 40}, "a" * 40, bindings)
        )


if __name__ == "__main__":
    unittest.main()
