"""Adversarial offline tests for the bounded v1 human interaction contract."""

import copy
import json
import math
import unittest
from uuid import UUID

import native_human_contract as gate

TEXT_QUESTION = {
    "client_key": "test-color",
    "kind": "text",
    "prompt": "What color should I use for this live test?",
    "required": True,
    "options": [],
}
CHOICE_QUESTION = {
    "client_key": "test-shade",
    "kind": "single_choice",
    "prompt": "Choose the test shade.",
    "required": True,
    "options": [
        {"id": "sapphire", "label": "Sapphire"},
        {"id": "amber", "label": "Amber"},
    ],
}
QUESTIONS = [TEXT_QUESTION, CHOICE_QUESTION]
TERMINAL_ID = "a1b2c3d4"
INCARNATION_ID = UUID(int=201).hex
GROUP_ID = str(UUID(int=101))
QUESTION_IDS = [str(UUID(int=1)), str(UUID(int=2))]


def row(question, index, *, group_id=GROUP_ID, version=1, state=None, lifecycle="open"):
    return {
        "question_id": QUESTION_IDS[index],
        "version": version,
        "group_id": group_id,
        "terminal_id": TERMINAL_ID,
        "incarnation_id": INCARNATION_ID,
        "client_key": question["client_key"],
        "kind": question["kind"],
        "required": question["required"],
        "prompt_content": question["prompt"],
        "options_json": json.dumps(
            [{**option, "available": True} for option in question["options"]], ensure_ascii=False
        ),
        "lifecycle": lifecycle,
        "state": state,
    }


def rows(*, state=None, lifecycle="open"):
    return [
        row(TEXT_QUESTION, 0, state=state, lifecycle=lifecycle),
        row(CHOICE_QUESTION, 1, state=state, lifecycle=lifecycle),
    ]


def audit():
    return {
        "create_group_seen": True,
        "created_group_matches_database": True,
        "created_questions_match_database": True,
        "wait_call_count": 2,
        "ready_wait_count": 1,
        "ready_wait_matches_database": True,
        "ack_call_count": 1,
        "ack_checks": [
            {
                "group_matches_created": True,
                "group_matches_waited": True,
                "question_versions_match_created": True,
                "question_versions_match_returned": True,
                "receipt_hashes_match_returned": True,
                "ack_succeeded": True,
                "ack_count": 2,
            }
        ],
    }


def protocol():
    tools = ["cao_ask_user", "cao_wait_for_user_answers", "cao_acknowledge_user_answers"]
    return {
        "observer_status": "observed",
        "mcp_catalog_observation_status": "observed",
        "mcp_catalog_tool_names": sorted(tools),
        "mcp_catalog_unexpected_tool_count": 0,
        "mcp_catalog_observation_count": 1,
        "mcp_tool_observation_status": "observed",
        "mcp_tool_unexpected_call_count": 0,
        "observed_interaction_tools": sorted(tools),
        "mcp_observer_shutdown_observed": True,
        "mcp_tool_calls": [
            {
                "server": "cao-mcp-server",
                "tool": "cao_ask_user",
                "status": "completed",
                "error_present": False,
                "result_present": True,
            },
            {
                "server": "cao-mcp-server",
                "tool": "cao_wait_for_user_answers",
                "status": "completed",
                "error_present": False,
                "result_present": True,
            },
            {
                "server": "cao-mcp-server",
                "tool": "cao_wait_for_user_answers",
                "status": "completed",
                "error_present": False,
                "result_present": True,
            },
            {
                "server": "cao-mcp-server",
                "tool": "cao_acknowledge_user_answers",
                "status": "completed",
                "error_present": False,
                "result_present": True,
            },
        ],
    }


def final_receipt():
    receipt = {name: True for name in gate.FINAL_TRUE}
    receipt.update({name: False for name in gate.FINAL_FALSE})
    receipt.update(gate.FINAL_EXACT_VALUES)
    for name in gate.FINAL_EMPTY_LISTS:
        receipt[name] = []
    for name, value in gate.FINAL_EXACT_STRINGS.items():
        receipt[name] = value
    receipt.update(model_seconds=31.42, journey_seconds=62.31, minimum_admission_mib=6144)
    return receipt


class QuestionIdentityTests(unittest.TestCase):
    def test_question_options_match_real_production_defaults_and_reject_unavailable(self):
        from cli_agent_orchestrator.services.interactions.models import InteractionOption

        actual = rows()
        normalized = [
            InteractionOption.model_validate(option).model_dump(mode="json")
            for option in CHOICE_QUESTION["options"]
        ]
        actual[1]["options_json"] = json.dumps(normalized)
        self.assertTrue(gate.questions_exact(actual, QUESTIONS, TERMINAL_ID, INCARNATION_ID))
        normalized[0]["available"] = False
        actual[1]["options_json"] = json.dumps(normalized)
        self.assertFalse(gate.questions_exact(actual, QUESTIONS, TERMINAL_ID, INCARNATION_ID))

    def test_each_catalog_must_be_exact_even_when_union_is_complete(self):
        self.assertTrue(gate.protocol_exact(protocol()))
        complete = dict(
            event="tools_list", names=sorted(gate.TOOLS), unexpected_count=0, tool_count=3
        )
        self.assertTrue(gate.catalogs_exact([complete, dict(complete)]))
        partial = [
            {**complete, "names": [gate.TOOLS[0]], "tool_count": 1},
            {**complete, "names": sorted(gate.TOOLS[1:]), "tool_count": 2},
        ]
        self.assertEqual(set().union(*(set(v["names"]) for v in partial)), set(gate.TOOLS))
        self.assertFalse(gate.catalogs_exact(partial))
        for bad in (
            {**complete, "tool_count": 4},
            {**complete, "unexpected_count": True},
            {**complete, "names": []},
        ):
            self.assertFalse(gate.catalogs_exact([bad]))

    def test_all_waits_match_group_and_pending_then_ready_history(self):
        expected = gate.identity(rows())
        pending = dict(
            event="wait_returned", group_id=GROUP_ID, state="pending", question_versions=[]
        )
        ready = {
            **pending,
            "state": "ready",
            "question_versions": [{"question_id": q, "version": 1} for q in QUESTION_IDS],
        }
        self.assertTrue(gate.wait_history_exact([pending, ready, dict(ready)], GROUP_ID, expected))
        other = str(UUID(int=999))
        self.assertFalse(gate.wait_history_exact([{**ready, "group_id": other}], other, expected))
        for events in (
            [{**pending, "group_id": str(UUID(int=999))}, ready],
            [ready, pending],
            [{**ready, "state": "cancelled"}],
            [pending],
            [
                {
                    **ready,
                    "question_versions": [
                        {"question_id": q, "version": True} for q in QUESTION_IDS
                    ],
                }
            ],
        ):
            self.assertFalse(gate.wait_history_exact(events, GROUP_ID, expected))

    def test_questions_require_exact_typed_text_and_choice_rows(self):
        self.assertTrue(gate.questions_exact(rows(), QUESTIONS, TERMINAL_ID, INCARNATION_ID))

    def test_questions_reject_count_duplicates_and_stale_source_identity(self):
        good = rows()
        for candidate in ([], good[:1], good + [copy.deepcopy(good[0])]):
            self.assertFalse(
                gate.questions_exact(candidate, QUESTIONS, TERMINAL_ID, INCARNATION_ID)
            )

        duplicated_id = copy.deepcopy(good)
        duplicated_id[1]["question_id"] = duplicated_id[0]["question_id"]
        self.assertFalse(
            gate.questions_exact(duplicated_id, QUESTIONS, TERMINAL_ID, INCARNATION_ID)
        )

        for field, value in (("terminal_id", "stale000"), ("incarnation_id", str(UUID(int=202)))):
            changed = copy.deepcopy(good)
            changed[1][field] = value
            self.assertFalse(gate.questions_exact(changed, QUESTIONS, TERMINAL_ID, INCARNATION_ID))

    def test_questions_reject_invalid_group_uuid_or_mixed_groups(self):
        for value in (None, "", "not-a-uuid"):
            changed = copy.deepcopy(rows())
            changed[0]["group_id"] = value
            self.assertFalse(gate.questions_exact(changed, QUESTIONS, TERMINAL_ID, INCARNATION_ID))

        changed = copy.deepcopy(rows())
        changed[1]["group_id"] = str(UUID(int=102))
        self.assertFalse(gate.questions_exact(changed, QUESTIONS, TERMINAL_ID, INCARNATION_ID))

    def test_questions_reject_bool_zero_and_out_of_range_versions(self):
        for value in (True, False, 0, -1, 2**63, "1"):
            changed = copy.deepcopy(rows())
            changed[0]["version"] = value
            self.assertFalse(gate.questions_exact(changed, QUESTIONS, TERMINAL_ID, INCARNATION_ID))

    def test_questions_reject_each_changed_question_contract_field(self):
        changes = (
            ("client_key", "unexpected"),
            ("kind", "multi_choice"),
            ("required", 1),
            ("prompt_content", "different prompt"),
            ("options_json", "not-json"),
            (
                "options_json",
                '[{"id":"sapphire","label":"Sapphire","extra":true},{"id":"amber","label":"Amber"}]',
            ),
        )
        for field, value in changes:
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(rows())
                changed[1][field] = value
                self.assertFalse(
                    gate.questions_exact(changed, QUESTIONS, TERMINAL_ID, INCARNATION_ID)
                )

        text_options = copy.deepcopy(rows())
        text_options[0]["options_json"] = '[{"id":"x","label":"not-empty"}]'
        self.assertFalse(gate.questions_exact(text_options, QUESTIONS, TERMINAL_ID, INCARNATION_ID))

    def test_questions_reject_noncanonical_option_json_duplicate_keys_and_bool_coercion(self):
        for value in (
            '[{"id":"sapphire","id":"other","label":"Sapphire"},{"id":"amber","label":"Amber"}]',
            '[{"id":"sapphire","label":"Sapphire"},{"id":"amber","label":"Amber","rank":1}]',
            '[{"id":"sapphire","label":"Sapphire"},{"id":"amber","label":"Amber"},true]',
        ):
            changed = copy.deepcopy(rows())
            changed[1]["options_json"] = value
            self.assertFalse(gate.questions_exact(changed, QUESTIONS, TERMINAL_ID, INCARNATION_ID))

    def test_identity_returns_only_exact_source_identity_or_raises(self):
        expected = {
            QUESTION_IDS[0]: {
                "group_id": GROUP_ID,
                "version": 1,
                "terminal_id": TERMINAL_ID,
                "incarnation_id": INCARNATION_ID,
            },
            QUESTION_IDS[1]: {
                "group_id": GROUP_ID,
                "version": 1,
                "terminal_id": TERMINAL_ID,
                "incarnation_id": INCARNATION_ID,
            },
        }
        self.assertEqual(gate.identity(rows()), expected)
        for candidate in ([], rows()[:1]):
            with self.assertRaises(ValueError):
                gate.identity(candidate)
        duplicate = copy.deepcopy(rows())
        duplicate[1]["question_id"] = duplicate[0]["question_id"]
        with self.assertRaises(ValueError):
            gate.identity(duplicate)
        invalid_version = copy.deepcopy(rows())
        invalid_version[0]["version"] = True
        with self.assertRaises(ValueError):
            gate.identity(invalid_version)

    def test_ledger_matches_only_exact_identity_lifecycle_and_delivery_state(self):
        expected = gate.identity(rows())
        self.assertTrue(gate.ledger_exact(rows(), expected, "open", None))
        acknowledged = rows(state="agent_acknowledged")
        self.assertTrue(gate.ledger_exact(acknowledged, expected, "open", "agent_acknowledged"))

        changed = copy.deepcopy(acknowledged)
        changed[0]["question_id"] = str(UUID(int=9))
        self.assertFalse(gate.ledger_exact(changed, expected, "open", "agent_acknowledged"))
        for field, value in (("lifecycle", "cancelled"), ("state", "delivered"), ("version", True)):
            changed = copy.deepcopy(acknowledged)
            changed[1][field] = value
            self.assertFalse(gate.ledger_exact(changed, expected, "open", "agent_acknowledged"))


class ObservationTests(unittest.TestCase):
    def test_audit_requires_database_matched_create_ready_wait_and_exact_ack(self):
        self.assertTrue(gate.audit_exact(audit()))
        for field in (
            "create_group_seen",
            "created_group_matches_database",
            "created_questions_match_database",
            "ready_wait_matches_database",
        ):
            changed = audit()
            changed[field] = 1
            self.assertFalse(gate.audit_exact(changed))
        for field, value in (
            ("ready_wait_count", 0),
            ("ready_wait_count", True),
            ("wait_call_count", 0),
            ("wait_call_count", True),
            ("ack_call_count", 2),
            ("ack_call_count", True),
        ):
            changed = audit()
            changed[field] = value
            self.assertFalse(gate.audit_exact(changed))

    def test_audit_rejects_missing_or_inexact_receipt_ack_check(self):
        for check_field, value in (
            ("group_matches_created", False),
            ("group_matches_waited", None),
            ("question_versions_match_created", 1),
            ("question_versions_match_returned", False),
            ("receipt_hashes_match_returned", False),
            ("ack_succeeded", False),
            ("ack_count", True),
            ("ack_count", 1),
        ):
            changed = audit()
            changed["ack_checks"][0][check_field] = value
            self.assertFalse(gate.audit_exact(changed))
        for value in ([], None, [audit()["ack_checks"][0], audit()["ack_checks"][0]]):
            changed = audit()
            changed["ack_checks"] = value
            self.assertFalse(gate.audit_exact(changed))

    def test_protocol_requires_exact_catalog_real_calls_order_and_graceful_seal(self):
        self.assertTrue(gate.protocol_exact(protocol()))
        changes = (
            ("observer_status", "incomplete"),
            ("mcp_catalog_observation_status", "unobserved"),
            ("mcp_catalog_tool_names", ["cao_ask_user", "cao_wait_for_user_answers"]),
            ("mcp_catalog_unexpected_tool_count", True),
            ("mcp_tool_unexpected_call_count", 1),
            ("mcp_observer_shutdown_observed", False),
        )
        for field, value in changes:
            with self.subTest(field=field):
                changed = protocol()
                changed[field] = value
                self.assertFalse(gate.protocol_exact(changed))

    def test_protocol_rejects_wrong_order_extra_calls_and_uncompleted_or_error_calls(self):
        good = protocol()
        for calls in (
            [good["mcp_tool_calls"][1], good["mcp_tool_calls"][0], good["mcp_tool_calls"][-1]],
            [good["mcp_tool_calls"][0], good["mcp_tool_calls"][-1]],
            good["mcp_tool_calls"] + [good["mcp_tool_calls"][1]],
        ):
            changed = protocol()
            changed["mcp_tool_calls"] = calls
            self.assertFalse(gate.protocol_exact(changed))

        for field, value in (
            ("status", "failed"),
            ("error_present", True),
            ("result_present", False),
            ("result_present", 1),
            ("tool", "other"),
        ):
            changed = protocol()
            changed["mcp_tool_calls"][1][field] = value
            self.assertFalse(gate.protocol_exact(changed))


class FinalReceiptTests(unittest.TestCase):
    def test_final_receipt_requires_every_positive_and_negative_predicate(self):
        self.assertTrue(gate.final_exact(final_receipt()))
        for name in gate.FINAL_TRUE:
            changed = final_receipt()
            del changed[name]
            self.assertFalse(gate.final_exact(changed), name)
        for name in gate.FINAL_TRUE:
            for value in (False, None, 1):
                changed = final_receipt()
                changed[name] = value
                self.assertFalse(gate.final_exact(changed), name)
        for name in gate.FINAL_FALSE:
            for value in (True, None, 0):
                changed = final_receipt()
                changed[name] = value
                self.assertFalse(gate.final_exact(changed), name)

    def test_final_receipt_requires_exact_cleanup_numbers_empty_failures_and_status(self):
        for name, value in gate.FINAL_EXACT_VALUES.items():
            wrong = True if type(value) is int else "wrong"
            changed = final_receipt()
            changed[name] = wrong
            self.assertFalse(gate.final_exact(changed), name)
        for name in gate.FINAL_EMPTY_LISTS:
            for value in (None, ["cleanup_failed"], ()):
                changed = final_receipt()
                changed[name] = value
                self.assertFalse(gate.final_exact(changed), name)
        for name, value in gate.FINAL_EXACT_STRINGS.items():
            changed = final_receipt()
            changed[name] = value + "_wrong"
            self.assertFalse(gate.final_exact(changed), name)

    def test_final_receipt_enforces_finite_time_budgets_and_memory_floor(self):
        for field, value in (
            ("model_seconds", True),
            ("model_seconds", math.nan),
            ("model_seconds", math.inf),
            ("model_seconds", -0.1),
            ("model_seconds", 75.0001),
            ("journey_seconds", "62.31"),
            ("journey_seconds", math.nan),
            ("journey_seconds", 180.001),
            ("minimum_admission_mib", True),
            ("minimum_admission_mib", 6143),
            ("minimum_admission_mib", 6144.0),
        ):
            with self.subTest(field=field, value=value):
                changed = final_receipt()
                changed[field] = value
                self.assertFalse(gate.final_exact(changed))

        self.assertTrue(gate.final_exact({**final_receipt(), "model_seconds": 75.0}))
        self.assertTrue(gate.final_exact({**final_receipt(), "journey_seconds": 180.0}))


if __name__ == "__main__":
    unittest.main()
