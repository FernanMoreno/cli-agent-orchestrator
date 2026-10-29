"""Single native task-query case contracts, distinct from message acceptance."""

import contextlib
import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "task_query_case_candidate", Path(__file__).with_name("run_native_v2_acceptance.py")
)
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)
CASE = "task_query"
TASK = "11111111-1111-4111-8111-111111111111"


def test_task_case_has_one_operation_and_current_coordination_profile():
    case = runner._case_definition(CASE)
    assert case.sender_calls == ("get_local_cao_task",)
    assert case.sender_catalog == runner.COORDINATION_SENDER_TOOLS
    assert runner._case_native_profile("sender", CASE) == "coordination"
    assert runner._case_observer_options(CASE) == {
        "expected_safe_result_tool": "get_local_cao_task"
    }
    assert runner._self_check(CASE)["success"] is True
    assert runner.RUN_ENABLED is False
    assert runner.TASK_QUERY_SECONDS == 180.0
    assert runner.MEMORY_FLOOR_MIB == 6144


def test_task_query_prompt_is_one_exact_operation():
    prompt = runner._task_query_prompt(TASK)
    assert TASK in prompt and "cao-mcp-server_get_local_cao_task" in prompt
    assert "send_message" not in prompt


@pytest.mark.parametrize("task_id", [None, True, "", "../secret", TASK.upper(), "0" * 64])
def test_task_query_prompt_refuses_invalid_task_identity(task_id):
    # This UUID has no letters; uppercase is consequently still canonical.
    if task_id == TASK:
        task_id = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    with pytest.raises(runner.GateError):
        runner._task_query_prompt(task_id)


def _sender():
    return {
        "mcp_tool_calls": [
            {
                "server": "cao-mcp-server",
                "tool": "get_local_cao_task",
                "status": "completed",
                "error_present": False,
                "result_present": True,
                "safe_result_success": True,
                "safe_result_task_binding_observed": True,
                "safe_result_task_argument_matches": True,
                "safe_result_task_id_matches": True,
                "safe_result_task_requester_matches": True,
                "safe_result_task_state": "succeeded",
                "safe_result_task_state_matches": True,
                "safe_result_task_result_state": "succeeded",
                "safe_result_task_result_state_matches": True,
            }
        ]
    }


def test_task_query_result_accepts_the_complete_bound_projection():
    assert runner._task_query_result_contract(_sender()) is True


@pytest.mark.parametrize("field", list(_sender()["mcp_tool_calls"][0]))
def test_task_query_result_refuses_each_missing_boundary(field):
    sender = _sender()
    del sender["mcp_tool_calls"][0][field]
    assert runner._task_query_result_contract(sender) is False


@pytest.mark.parametrize(
    "field", [k for k, v in _sender()["mcp_tool_calls"][0].items() if v is True]
)
@pytest.mark.parametrize("value", [False, None, 1])
def test_task_query_result_requires_literal_true(field, value):
    sender = _sender()
    sender["mcp_tool_calls"][0][field] = value
    assert runner._task_query_result_contract(sender) is False


def test_task_query_result_refuses_extra_or_incomplete_operations():
    sender = _sender()
    sender["mcp_tool_calls"].append(copy.deepcopy(sender["mcp_tool_calls"][0]))
    assert runner._task_query_result_contract(sender) is False
    sender["mcp_tool_calls"] = []
    assert runner._task_query_result_contract(sender) is False


def _exercise_query_controller(tmp_path, monkeypatch, stale_generation, target_change):
    tmp_path.mkdir(parents=True, exist_ok=True)
    from dataclasses import replace

    from cli_agent_orchestrator.clients.postgres.config import DatabaseTarget
    from cli_agent_orchestrator.services import local_peer_service

    monkeypatch.syspath_prepend(str(runner.REPO / "test/live_provider"))
    from test.live_provider.message_api_restart_live import _target_fingerprint

    target = DatabaseTarget(
        host="127.0.0.1",
        port=5432,
        database="fixture",
        user="fixture",
        schema="fixture",
        store_id="11111111-1111-4111-8111-111111111111",
        secret_file=tmp_path / "unused.secret",
        sslmode="disable",
    )
    targets = SimpleNamespace(main=target, auth=target, peer=target)
    foreign = SimpleNamespace(main=replace(target, database="foreign"), auth=target, peer=target)
    server = SimpleNamespace(url="http://127.0.0.1", database_targets=targets)
    fingerprint = _target_fingerprint(targets, database_target_type=type(target))
    invalid = SimpleNamespace(main=object(), auth=target, peer=target)
    if target_change == "before":
        server.database_targets = foreign
    elif target_change == "invalid_before":
        server.database_targets = invalid

    clock = [0.0]
    monkeypatch.setattr(
        runner,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        ),
    )
    monkeypatch.setattr(
        local_peer_service, "project_binding", lambda path: {"project_id": "f" * 64}
    )
    monkeypatch.setattr(runner, "_admit", lambda receipt, phase: None)
    monkeypatch.setattr(runner, "_pg_rows", lambda *args: [])
    monkeypatch.setattr(runner, "_api_rows", lambda *args: [])
    stored = {}
    posts = []
    binding = tmp_path / "binding.json"

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, query, params):
            if query.startswith("INSERT INTO local_peer_tasks"):
                stored.update(
                    task_id=params[0],
                    source_instance_id=params[1],
                    requester_terminal_id=params[3],
                    project_id=params[5],
                    state="succeeded",
                    result=json.loads(params[8]),
                )
                return Cursor(None)
            assert query.startswith("SELECT generation") and params == ("a1b2c3d4",)
            return Cursor(
                {
                    "generation": "1" * 32 if stale_generation or not posts else "2" * 32,
                    "phase": "result_verified",
                }
            )

    @contextlib.contextmanager
    def connect(*args, **kwargs):
        yield Connection()

    def api(requests, method, url, **kwargs):
        if url.endswith("/local-coordination/identity"):
            return {"instance_id": "33333333-3333-4333-8333-333333333333"}
        if url.endswith("/projects/verify"):
            return {"matches": True}
        if "/agent/tasks/" in url:
            assert kwargs["params"] == {"requester_terminal_id": "a1b2c3d4"}
            return stored
        if method == "POST":
            assert url.endswith("/terminals/a1b2c3d4/input")
            assert json.loads(binding.read_text())["task_id"] == stored["task_id"]
            posts.append(kwargs["params"])
            clock[0] += 0.01
            if target_change == "after":
                server.database_targets = foreign
            elif target_change == "invalid_after":
                server.database_targets = invalid
            return {"success": True, "turn_sequence": 1}
        if url.endswith("/terminals/a1b2c3d4"):
            return {
                "turn_sequence": int(bool(posts)),
                "turn_completed": int(bool(posts)),
                "status": "completed",
            }
        assert url.endswith("/terminals/e5f6a7b8")
        return {"turn_sequence": 0, "turn_completed": 0, "status": "completed"}

    monkeypatch.setattr(runner, "_api_json", api)
    receipt = {"postgres_target_fingerprint_sha256": fingerprint}
    if target_change == "missing_expected":
        receipt.clear()
    options = dict(
        receipt=receipt,
        requests=None,
        server=server,
        terminals={"sender": {"id": "a1b2c3d4"}, "receiver": {"id": "e5f6a7b8"}},
        connect=connect,
        target=target,
        working_directory=tmp_path,
        binding_path=binding,
        nonce="b" * 32,
        principal=SimpleNamespace(id="fixture-principal"),
    )
    if target_change:
        stage = "before" if target_change == "before" else "during"
        code = (
            "task_query_postgres_targets_invalid"
            if target_change in {"invalid_before", "invalid_after", "missing_expected"}
            else f"postgres_target_changed_{stage}_task_query"
        )
        with pytest.raises(runner.GateError, match=code):
            runner._run_task_query(**options)
        assert receipt["postgres_target_identity_stable"] is False
    elif stale_generation:
        with pytest.raises(runner.GateError, match="task_query_model_deadline_expired"):
            runner._run_task_query(**options)
        assert "task_query_turn_verified" not in receipt
        assert receipt["postgres_target_identity_stable"] is False
    else:
        runner._run_task_query(**options)
        assert receipt["task_query_turn_verified"] is True
        assert receipt["task_query_receiver_unprompted"] is True
        assert receipt["task_query_no_messages"] is True
        assert receipt["postgres_target_identity_stable"] is True
    no_effects = target_change in {"before", "invalid_before", "missing_expected"}
    assert len(posts) == (0 if no_effects else 1)
    if no_effects:
        assert stored == {}
    return receipt


@pytest.mark.parametrize(
    "stale_generation,target_change",
    [
        (False, None),
        (True, None),
        (False, "before"),
        (False, "after"),
        (False, "invalid_before"),
        (False, "invalid_after"),
        (False, "missing_expected"),
    ],
)
def test_query_controller_submits_once_and_requires_a_new_durable_turn(
    tmp_path, monkeypatch, stale_generation, target_change
):
    _exercise_query_controller(tmp_path, monkeypatch, stale_generation, target_change)


@pytest.fixture
def task_receipt(monkeypatch, tmp_path):
    emitted = _exercise_query_controller(tmp_path / "consumer-emission", monkeypatch, False, None)
    case = runner._case_definition(CASE)
    pins = {
        "EXPECTED_HEAD": "1" * 40,
        "RUN_MANIFEST_SHA256": "2" * 64,
        "SOURCE_FINGERPRINT_SHA256": "3" * 64,
        "RUNNER_SHA256_AT_ADMISSION": "4" * 64,
        "REVIEW_DIGEST": "5" * 64,
        "PINNED_OPENCODE_SHA256": "6" * 64,
        "PINNED_POSTGRES_ID": "7" * 64,
        "PINNED_POSTGRES_IMAGE": "sha256:" + "8" * 64,
        "PINNED_POSTGRES_FIXTURE_SHA256": "9" * 64,
        "PINNED_POSTGRES_GUARD_DERIVATION_SHA256": "a" * 64,
    }
    for key, value in pins.items():
        monkeypatch.setattr(runner, key, value)
    manifest = {
        "schema": case.manifest_schema,
        "case_id": CASE,
        "artifact_label": case.label,
        "case_profile_fixture_path": case.profile_fixture_path,
        "case_profile_fixture_schema": case.profile_fixture_schema,
        "receipt_schema": case.receipt_schema,
        "profile_fixture_sha256": "b" * 64,
        "operator_auth_snapshot_sha256": "c" * 64,
    }
    monkeypatch.setattr(runner, "RUN_MANIFEST", manifest)
    receipt = {
        runner._case_receipt_key(key, CASE): True
        for key in runner._REQUIRED_TRUE_RECEIPT_FIELDS
        if key != "postgres_target_identity_stable"
    }
    receipt.update(
        {
            "schema": case.receipt_schema,
            "case_id": CASE,
            "artifact_label": case.label,
            "profile_fixture_path": case.profile_fixture_path,
            "profile_fixture_schema": case.profile_fixture_schema,
            "scope": runner._case_scope(CASE),
            "native_surface": case.native_surface,
            "model": runner.MODEL,
            "auth_lane": runner.MODEL,
            "cao_auth_mode": "off",
            "source_head": pins["EXPECTED_HEAD"],
            "source_head_expected": pins["EXPECTED_HEAD"],
            "run_manifest_sha256": pins["RUN_MANIFEST_SHA256"],
            "review_digest": pins["REVIEW_DIGEST"],
            "source_fingerprint_sha256": pins["SOURCE_FINGERPRINT_SHA256"],
            "source_fingerprint_sha256_expected": pins["SOURCE_FINGERPRINT_SHA256"],
            "runner_sha256": pins["RUNNER_SHA256_AT_ADMISSION"],
            "runner_sha256_expected": pins["RUNNER_SHA256_AT_ADMISSION"],
            "profile_fixture_sha256_expected": manifest["profile_fixture_sha256"],
            "operator_auth_snapshot_sha256": manifest["operator_auth_snapshot_sha256"],
            "binary_sha256_expected": pins["PINNED_OPENCODE_SHA256"],
            "postgres_container_id_expected": pins["PINNED_POSTGRES_ID"],
            "postgres_container_id": pins["PINNED_POSTGRES_ID"],
            "postgres_image_id_expected": pins["PINNED_POSTGRES_IMAGE"],
            "postgres_image_id": pins["PINNED_POSTGRES_IMAGE"],
            "postgres_fixture_cleanup_guard_source_sha256": pins["PINNED_POSTGRES_FIXTURE_SHA256"],
            "postgres_fixture_cleanup_guard_derived_sha256": pins[
                "PINNED_POSTGRES_GUARD_DERIVATION_SHA256"
            ],
            "sender_capability_source_owner_kind": "authenticated_creation",
            "sender_capability_binding_diagnostics": {
                name: True for name in runner.CAPABILITY_BINDING_DIAGNOSTIC_FIELDS
            },
            "native_model_offer_scope": "selected_model_primary_requests_only",
            "receiver_native_builtin_execution_observation": "not_observed",
            "api_a_pid": 123,
            "api_a_process_start": 456,
            "api_a_launch_attempted": True,
            "api_b_launch_attempted": False,
            "api_b_cleanup": False,
            "api_server_record_count": 1,
            "task_fixture_owned_preflight_passed": True,
            "task_query_turn_verified": True,
            "task_query_receiver_unprompted": True,
            "task_query_no_messages": True,
            "task_query_seconds": 20.0,
            "observer_seal_seconds": 1.0,
            "phases": [],
            "session_delete_http_status": 200,
            "native_provider_roots_immediately_after_delete": 0,
            "capability_files_immediately_after_delete": 0,
            "cleanup_failures": [],
            "postgres_cleanup_deferred": False,
            "credentials_cleanup_deferred": False,
            "actual_cancel_observed": False,
            "cross_model_or_oauth_claimed": False,
            "claude_used": False,
            "receipt_redacted_only": True,
            "private_raw_evidence_present": True,
            "retained_private_state": {"status": "removed", "credential_snapshot": "removed"},
            "memory_admissions_mib": {
                key: 6144 for key in runner._SUCCESS_MEMORY_ADMISSIONS[:7] + ("task_query",)
            },
        }
    )
    common = {
        "observer_status": "observed",
        "mcp_observer_shutdown_observed": True,
        "mcp_catalog_unexpected_tool_count": 0,
        "mcp_tool_unexpected_call_count": 0,
    }
    receipt["native_observations"] = {
        "sender": {
            **common,
            **_sender(),
            "mcp_catalog_tool_names": list(case.sender_catalog),
            "mcp_catalog_observation_count": 1,
            "mcp_catalog_observation_status": "observed",
            "mcp_tool_observation_status": "observed",
        },
        "receiver": {
            **common,
            "mcp_catalog_tool_names": [],
            "mcp_catalog_observation_count": 0,
            "mcp_catalog_observation_status": "unobserved",
            "mcp_tool_calls": [],
            "mcp_tool_observation_status": "unobserved",
        },
    }
    assert runner._acceptance_contract(receipt, CASE) is False
    receipt["postgres_target_identity_stable"] = emitted["postgres_target_identity_stable"]
    assert runner._acceptance_contract(receipt, CASE) is True
    return receipt


@pytest.mark.parametrize(
    "key,value",
    [
        ("source_head", "foreign"),
        ("task_query_turn_verified", False),
        ("task_query_receiver_unprompted", False),
        ("task_query_no_messages", False),
        ("postgres_target_identity_stable", False),
        ("postgres_target_identity_stable", None),
        ("api_b_launch_attempted", True),
        ("api_server_record_count", 2),
        ("sender_capability_source_owner_kind", "unverified"),
        ("api_a_pid", True),
        ("task_query_seconds", 180.001),
        ("observer_seal_seconds", 10.001),
        ("native_provider_roots_immediately_after_delete", 1),
        ("capability_files_immediately_after_delete", 1),
        ("cleanup_failures", ["failed"]),
        ("phases", [{"name": "old_message_phase"}]),
        ("source_unchanged", False),
    ],
)
def test_task_acceptance_preserves_provenance_budget_and_cleanup(task_receipt, key, value):
    task_receipt[key] = value
    assert runner._acceptance_contract(task_receipt, CASE) is False


def test_task_acceptance_requires_native_bound_result_and_memory(task_receipt):
    bad = copy.deepcopy(task_receipt)
    bad["native_observations"]["sender"]["mcp_tool_calls"][0][
        "safe_result_task_requester_matches"
    ] = False
    assert runner._acceptance_contract(bad, CASE) is False
    task_receipt["memory_admissions_mib"]["task_query"] = 6143
    assert runner._acceptance_contract(task_receipt, CASE) is False


def test_passive_offer_requires_the_actual_complete_zero_request_reader(tmp_path):
    from test.live_provider import opencode_http_tool_offer_observer as observer

    ledger = tmp_path / "offer"
    ledger.mkdir(mode=0o700)
    seal = ledger / "seal.json"
    seal.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "nonce": "b" * 32,
                "hook_disposed": True,
                "trace_complete": True,
                "record_count": 0,
                "durable_record_count": 0,
                "overflow": False,
            }
        )
    )
    seal.chmod(0o600)
    actual = runner._read_http_tool_offer_observation(
        observer,
        ledger,
        expected_nonce="b" * 32,
        tracked_tool_names=runner._case_http_offer_names(CASE),
    )
    assert actual["observer_status"] == "observed"
    assert actual["tool_offer_status"] == "unobserved"
    assert runner._task_query_passive_offer_contract(actual, expected_nonce="b" * 32) is True
    for key in (
        "observer_status",
        "tool_offer_status",
        "tool_name_counts",
        "valid_target_primary_request_count",
        "other_tool_name_count",
    ):
        bad = copy.deepcopy(actual)
        del bad[key]
        assert runner._task_query_passive_offer_contract(bad, expected_nonce="b" * 32) is False
    bad = copy.deepcopy(actual)
    bad["tool_name_counts"][next(iter(bad["tool_name_counts"]))] = 1
    assert runner._task_query_passive_offer_contract(bad, expected_nonce="b" * 32) is False


def test_task_acceptance_refuses_deleted_target_witness(task_receipt):
    task_receipt.pop("postgres_target_identity_stable")
    assert runner._acceptance_contract(task_receipt, CASE) is False
