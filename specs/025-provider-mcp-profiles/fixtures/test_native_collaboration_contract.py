"""Adversarial offline tests for the native-v1 collaboration + human contract."""

import copy
import sys
import unittest
from datetime import datetime
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE_DIR.parents[2]
for _path in (REPO_ROOT / "src", FIXTURE_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import native_collaboration_contract as gate
import native_human_contract as human
from cli_agent_orchestrator.utils.mcp_tool_policy import TOOL_PROFILES

SENDER_ID = "a1b2c3d4"
RECEIVER_ID = "e5f60718"
MESSAGE = "Colaboración: validado ñ, 中文."
PERSISTED_MESSAGE = (
    MESSAGE
    + "\n\n[Message from terminal "
    + SENDER_ID
    + ". Use send_message MCP tool for any follow-up work.]"
)
CREATED = datetime(2026, 10, 23, 14, 15, 16, 123456)


def call(tool):
    return {
        "server": "cao-mcp-server",
        "tool": tool,
        "status": "completed",
        "error_present": False,
        "result_present": True,
    }


def observations(calls=None):
    calls = (
        calls
        if calls is not None
        else [
            call("send_message"),
            call("cao_ask_user"),
            call("cao_wait_for_user_answers"),
            call("cao_wait_for_user_answers"),
            call("cao_acknowledge_user_answers"),
        ]
    )
    return {
        "observer_status": "observed",
        "mcp_catalog_observation_status": "observed",
        "mcp_catalog_tool_names": list(gate.TOOLS),
        "mcp_catalog_unexpected_tool_count": 0,
        "mcp_catalog_observation_count": 2,
        "mcp_tool_observation_status": "observed",
        "mcp_tool_unexpected_call_count": 0,
        "mcp_observer_shutdown_observed": True,
        "mcp_tool_calls": calls,
        "observed_interaction_tools": sorted({row["tool"] for row in calls}),
    }


def catalog_row(names=None, **overrides):
    row = {
        "event": "tools_list",
        "names": list(gate.TOOLS) if names is None else names,
        "unexpected_count": 0,
        "tool_count": len(gate.TOOLS),
    }
    row.update(overrides)
    return row


def pg_row(**overrides):
    row = {
        "id": 17,
        "sender_id": SENDER_ID,
        "receiver_id": RECEIVER_ID,
        "message": PERSISTED_MESSAGE,
        "created_at": CREATED,
        "status": "delivered",
    }
    row.update(overrides)
    return row


def api_row(**overrides):
    row = {**pg_row(), "created_at": CREATED.isoformat()}
    row.update(overrides)
    return row


def normalized_row(**overrides):
    row = {**api_row()}
    row.update(overrides)
    return row


def receipt():
    result = {name: True for name in human.FINAL_TRUE}
    result.update({name: False for name in human.FINAL_FALSE})
    result.update(human.FINAL_EXACT_VALUES)
    result.update(human.FINAL_EXACT_STRINGS)
    result.update({name: [] for name in human.FINAL_EMPTY_LISTS})
    result.update(model_seconds=30.0, journey_seconds=65.0, minimum_admission_mib=6144)
    result.update(
        {
            "mcp_profile": "collaboration",
            "original_model_acceptance_claimed": False,
            "mixed_profile_configuration_exact": True,
            "receiver_profile_configuration_exact": True,
            "message_pair_before_restart_exact": True,
            "message_pair_after_restart_exact": True,
            "message_identity_survives_restart": True,
            "message_agui_notification_exact": True,
            "receiver_one_turn_before_restart_exact": True,
            "receiver_no_new_turn_after_restart_exact": True,
            "receiver_ack_exact": True,
            "receiver_verified_receipt_exact": True,
            "receiver_native_identity_stable": True,
            "receiver_model_argv_exact": True,
        }
    )
    return result


class SourceAndProtocolTests(unittest.TestCase):
    def test_fixed_catalog_is_sorted_eight_and_matches_product_source(self):
        self.assertEqual(gate.TOOLS, tuple(sorted(gate.TOOLS)))
        self.assertEqual(len(gate.TOOLS), 8)
        self.assertEqual(len(set(gate.TOOLS)), 8)
        self.assertEqual(frozenset(gate.TOOLS), TOOL_PROFILES["collaboration"])

    def test_protocol_accepts_exact_full_catalog_and_send_then_human_flow(self):
        self.assertTrue(gate.protocol_exact(observations()))

    def test_protocol_rejects_wrong_call_order_or_extra_calls(self):
        valid_names = [
            "send_message",
            "cao_ask_user",
            "cao_wait_for_user_answers",
            "cao_acknowledge_user_answers",
        ]
        invalid_orders = (
            [
                "cao_ask_user",
                "send_message",
                "cao_wait_for_user_answers",
                "cao_acknowledge_user_answers",
            ],
            [
                "send_message",
                "cao_ask_user",
                "cao_acknowledge_user_answers",
                "cao_wait_for_user_answers",
            ],
            ["send_message", "cao_ask_user", "cao_acknowledge_user_answers"],
            [*valid_names, "send_message"],
            [
                "send_message",
                "cao_ask_user",
                "cao_wait_for_user_answers",
                "cao_wait_for_user_answers",
                "other",
                "cao_acknowledge_user_answers",
            ],
        )
        for names in invalid_orders:
            with self.subTest(names=names):
                self.assertFalse(gate.protocol_exact(observations([call(name) for name in names])))

    def test_protocol_rejects_unexpected_or_untyped_call_results(self):
        good = observations()
        for field, value in (
            ("server", "other-server"),
            ("status", "failed"),
            ("error_present", True),
            ("error_present", 0),
            ("result_present", False),
            ("result_present", 1),
        ):
            changed = copy.deepcopy(good)
            changed["mcp_tool_calls"][0][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(gate.protocol_exact(changed))

        unknown = copy.deepcopy(good)
        unknown["mcp_tool_calls"][2]["tool"] = "cao_escalate_interaction"
        self.assertFalse(gate.protocol_exact(unknown))

    def test_protocol_rejects_partial_catalogs_and_malformed_counts(self):
        good = observations()
        for field, value in (
            ("mcp_catalog_tool_names", list(gate.TOOLS[:-1])),
            ("mcp_catalog_tool_names", list(gate.TOOLS) + [gate.TOOLS[-1]]),
            ("mcp_catalog_unexpected_tool_count", True),
            ("mcp_catalog_unexpected_tool_count", 1),
            ("mcp_catalog_observation_count", True),
            ("mcp_catalog_observation_count", 0),
            ("mcp_tool_unexpected_call_count", True),
            ("mcp_observer_shutdown_observed", 1),
        ):
            changed = copy.deepcopy(good)
            changed[field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(gate.protocol_exact(changed))

    def test_protocol_requires_sorted_tools_derived_from_actual_calls(self):
        expected = sorted(
            {
                "send_message",
                "cao_ask_user",
                "cao_wait_for_user_answers",
                "cao_acknowledge_user_answers",
            }
        )
        self.assertEqual(observations()["observed_interaction_tools"], expected)
        for value in (
            None,
            " ".join(expected),
            expected[:-1],
            ["cao_ask_user", "send_message", *expected[2:]],
            [*expected, "cao_escalate_interaction"],
        ):
            changed = observations()
            changed["observed_interaction_tools"] = value
            with self.subTest(value=value):
                self.assertFalse(gate.protocol_exact(changed))
        missing = observations()
        del missing["observed_interaction_tools"]
        self.assertFalse(gate.protocol_exact(missing))

    def test_every_catalog_response_must_independently_be_exact(self):
        good = [catalog_row(), catalog_row()]
        self.assertTrue(gate.catalogs_exact(good))

        partial = [
            catalog_row(names=list(gate.TOOLS[:4]), tool_count=4),
            catalog_row(names=list(gate.TOOLS[4:]), tool_count=4),
        ]
        self.assertEqual(set().union(*(set(row["names"]) for row in partial)), set(gate.TOOLS))
        self.assertFalse(gate.catalogs_exact(partial))

        malformed = (
            [catalog_row(names=list(reversed(gate.TOOLS)))],
            [catalog_row(names=list(gate.TOOLS[:-1]))],
            [catalog_row(tool_count=True)],
            [catalog_row(unexpected_count=True)],
            [catalog_row(unexpected_count=1)],
            [catalog_row(tool_count=9)],
            [catalog_row(), catalog_row(names=list(gate.TOOLS[:-1]))],
            [catalog_row(), None],
            [],
        )
        for rows in malformed:
            with self.subTest(rows=rows):
                self.assertFalse(gate.catalogs_exact(rows))


class MessagePersistenceTests(unittest.TestCase):
    def test_message_pair_accepts_exact_pg_and_api_projection(self):
        self.assertTrue(
            gate.message_pair_exact(
                [pg_row()], [api_row()], SENDER_ID, RECEIVER_ID, PERSISTED_MESSAGE
            )
        )

    def test_message_pair_accepts_a_pre_normalized_pg_timestamp(self):
        self.assertTrue(
            gate.message_pair_exact(
                [{**pg_row(), "created_at": CREATED.isoformat()}],
                [api_row()],
                SENDER_ID,
                RECEIVER_ID,
                PERSISTED_MESSAGE,
            )
        )

    def test_message_pair_requires_one_exact_row_per_store(self):
        good_pg, good_api = [pg_row()], [api_row()]
        bad_inputs = (
            ([], good_api),
            (good_pg, []),
            (good_pg + [pg_row(id=18)], good_api),
            (good_pg, good_api + [api_row(id=18)]),
            (pg_row(), good_api),
            (good_pg, api_row()),
        )
        for pg_rows, api_rows in bad_inputs:
            with self.subTest(pg_rows=pg_rows, api_rows=api_rows):
                self.assertFalse(
                    gate.message_pair_exact(
                        pg_rows, api_rows, SENDER_ID, RECEIVER_ID, PERSISTED_MESSAGE
                    )
                )

    def test_message_pair_rejects_each_changed_field_and_wrong_status(self):
        changes = (
            ("id", 18),
            ("sender_id", "11111111"),
            ("receiver_id", "22222222"),
            ("message", "different"),
            ("created_at", "2026-10-23T14:15:16.123457"),
            ("status", "pending"),
        )
        for field, value in changes:
            with self.subTest(field=field, value=value):
                self.assertFalse(
                    gate.message_pair_exact(
                        [pg_row()],
                        [api_row(**{field: value})],
                        SENDER_ID,
                        RECEIVER_ID,
                        PERSISTED_MESSAGE,
                    )
                )
        for status in ("pending", "reconcile", "failed", "DELIVERED"):
            with self.subTest(status=status):
                self.assertFalse(
                    gate.message_pair_exact(
                        [pg_row(status=status)],
                        [api_row(status=status)],
                        SENDER_ID,
                        RECEIVER_ID,
                        PERSISTED_MESSAGE,
                    )
                )

    def test_message_pair_rejects_bad_typed_ids_and_foreign_or_self_receiver(self):
        bad_ids = (True, False, 0, -1, "17")
        for value in bad_ids:
            with self.subTest(value=value):
                self.assertFalse(
                    gate.message_pair_exact(
                        [pg_row(id=value)],
                        [api_row(id=value)],
                        SENDER_ID,
                        RECEIVER_ID,
                        PERSISTED_MESSAGE,
                    )
                )
        for sender, receiver in (
            ("A1B2C3D4", RECEIVER_ID),
            (SENDER_ID, "foreign"),
            (SENDER_ID, SENDER_ID),
        ):
            with self.subTest(sender=sender, receiver=receiver):
                self.assertFalse(
                    gate.message_pair_exact(
                        [pg_row(sender_id=sender, receiver_id=receiver)],
                        [api_row(sender_id=sender, receiver_id=receiver)],
                        sender,
                        receiver,
                        PERSISTED_MESSAGE,
                    )
                )

    def test_message_pair_rejects_invalid_or_noncanonical_api_dates(self):
        for api_created in (
            datetime(2026, 10, 23, 14, 15, 16, 123456),
            "",
            "not-a-date",
            "2026-10-23",
            "2026-10-23 14:15:16.123456",
            CREATED.isoformat() + "Z",
            CREATED.isoformat() + "+00",
        ):
            with self.subTest(api_created=api_created):
                self.assertFalse(
                    gate.message_pair_exact(
                        [pg_row()],
                        [api_row(created_at=api_created)],
                        SENDER_ID,
                        RECEIVER_ID,
                        PERSISTED_MESSAGE,
                    )
                )
        self.assertFalse(
            gate.message_pair_exact(
                [pg_row(created_at="bad-date")],
                [api_row(created_at="bad-date")],
                SENDER_ID,
                RECEIVER_ID,
                PERSISTED_MESSAGE,
            )
        )

    def test_message_pair_rejects_missing_extra_fields_and_wrong_body_argument(self):
        for pg, api, expected in (
            (
                {key: value for key, value in pg_row().items() if key != "status"},
                api_row(),
                PERSISTED_MESSAGE,
            ),
            (pg_row(extra="unexpected"), api_row(), PERSISTED_MESSAGE),
            (pg_row(), {**api_row(), "extra": "unexpected"}, PERSISTED_MESSAGE),
            (pg_row(), api_row(), "different expected body"),
            (pg_row(), api_row(), True),
        ):
            with self.subTest(expected=expected, pg=pg, api=api):
                self.assertFalse(
                    gate.message_pair_exact([pg], [api], SENDER_ID, RECEIVER_ID, expected)
                )

    def test_message_identity_survives_restart_requires_same_full_normalized_row(self):
        before = [normalized_row()]
        self.assertTrue(gate.message_survives_restart(before, copy.deepcopy(before)))

        changes = (
            ("id", 18),
            ("sender_id", "11111111"),
            ("receiver_id", "22222222"),
            ("message", "changed"),
            ("created_at", "2026-10-23T14:15:16.123457"),
            ("status", "pending"),
        )
        for field, value in changes:
            with self.subTest(field=field, value=value):
                self.assertFalse(
                    gate.message_survives_restart(before, [normalized_row(**{field: value})])
                )

    def test_message_identity_survives_restart_rejects_bad_inputs_and_bool_coercion(self):
        row = normalized_row()
        for before, after in (
            ([], [row]),
            ([row], []),
            ([row, row], [row]),
            ([row], [row, row]),
            ([{**row, "id": True}], [row]),
            ([{**row, "id": "17"}], [row]),
            ([{**row, "status": "reconcile"}], [{**row, "status": "reconcile"}]),
            ([{**row, "created_at": CREATED}], [row]),
            ([{**row, "created_at": "bad"}], [{**row, "created_at": "bad"}]),
            ([{**row, "extra": 1}], [{**row, "extra": 1}]),
        ):
            with self.subTest(before=before, after=after):
                self.assertFalse(gate.message_survives_restart(before, after))


class FinalReceiptTests(unittest.TestCase):
    def test_final_receipt_reuses_all_human_gates_and_requires_new_literal_bools(self):
        self.assertTrue(gate.final_exact(receipt()))
        for key in gate.FINAL_TRUE:
            for bad in (False, None, 1):
                changed = receipt()
                changed[key] = bad
                with self.subTest(key=key, bad=bad):
                    self.assertFalse(gate.final_exact(changed))

    def test_final_receipt_rejects_missing_or_broad_profile_claims(self):
        for key in gate.FINAL_TRUE:
            changed = receipt()
            changed.pop(key)
            with self.subTest(missing=key):
                self.assertFalse(gate.final_exact(changed))
        for key in (
            "acceptance_claimed",
            "global_acceptance_claimed",
            "original_model_acceptance_claimed",
        ):
            for bad in (True, 0, None):
                changed = receipt()
                changed[key] = bad
                with self.subTest(key=key, bad=bad):
                    self.assertFalse(gate.final_exact(changed))
        for value in ("interaction", "coordination", None, True):
            changed = receipt()
            changed["mcp_profile"] = value
            with self.subTest(profile=value):
                self.assertFalse(gate.final_exact(changed))

    def test_final_receipt_keeps_inherited_human_deadlines_and_cleanup_mandatory(self):
        for key, value in (
            ("model_seconds", 75.001),
            ("journey_seconds", 180.001),
            ("minimum_admission_mib", 6143),
            ("api_cleanup", False),
            ("source_unchanged", False),
        ):
            changed = receipt()
            changed[key] = value
            with self.subTest(key=key, value=value):
                self.assertFalse(gate.final_exact(changed))


if __name__ == "__main__":
    unittest.main()
