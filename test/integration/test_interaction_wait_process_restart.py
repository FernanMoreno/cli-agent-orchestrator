"""Real HTTP wait interruption/recovery; no provider model or TestClient hop."""

import asyncio
import json
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from test.live_provider.interaction_agui_restart import (
    ApiWorkerController,
    build_api_worker_spec,
    open_private_log,
    read_observations,
)
from test.live_provider.interaction_cli_journey import _free_loopback_port
from test.postgres.interactions import postgres_interaction_store, register_interaction_source

import httpx
import pytest
import requests

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def _until(predicate, *, deadline):
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("wait_interruption_barrier_not_observed")


def test_inflight_wait_recovers_same_group_after_owned_api_crash(
    tmp_path,
    monkeypatch,
    postgres_interaction_store,
    postgres_targets,
):
    from cli_agent_orchestrator.mcp_server import server, utils
    from cli_agent_orchestrator.security import auth

    monkeypatch.setenv("CAO_AUTH_MODE", "off")
    source = register_interaction_source(
        postgres_interaction_store, owner=auth.local_operator_principal()
    )
    root = tmp_path / "worker"
    root.mkdir(mode=0o700)
    for name in ("cao", "work", "home", "tmp", "tmux", "xdg-cache", "xdg-config"):
        (root / name).mkdir(mode=0o700)
    config = json.loads(postgres_targets.config_file.read_text())
    target = config["targets"]["main"]
    for key in ("migration_user", "migration_secret_file"):
        target.pop(key, None)
    secret_name = target["secret_file"]
    shutil.copyfile(postgres_targets.home / secret_name, root / "cao" / secret_name)
    (root / "cao" / secret_name).chmod(0o600)
    config_path = root / "cao" / "database-config.json"
    config_path.write_text(json.dumps({"version": 1, "targets": {"main": target}}))
    config_path.chmod(0o600)
    capability = root / "source-capability"
    capability.write_text(source.token)
    capability.chmod(0o600)
    monkeypatch.setenv("CAO_INTERACTION_CAPABILITY_FILE", str(capability))
    spec = build_api_worker_spec(
        source_root=Path(__file__).resolve().parents[2],
        run_root=root,
        python_executable=sys.executable,
        port=_free_loopback_port(),
        parent_env={},
    )
    monkeypatch.setattr(utils, "API_BASE_URL", spec.base_url)
    monkeypatch.setattr(utils, "_auth_headers", lambda: {})
    audit = root / "interaction-observations.jsonl"
    transport_error = threading.Event()
    original_post = utils.interaction_post_json
    wait_paths = []

    def observed_post(path, body, **kwargs):
        if path.endswith("/wait"):
            wait_paths.append((path, body["after_event_sequence"]))
        try:
            return original_post(path, body, **kwargs)
        except requests.ConnectionError as error:
            if path.endswith("/wait") and not isinstance(error, requests.Timeout):
                transport_error.set()
            raise

    monkeypatch.setattr(utils, "interaction_post_json", observed_post)
    with open_private_log(root / "worker.log") as log:
        controller = ApiWorkerController(spec, stdout=log, stderr=log)
        with ThreadPoolExecutor(max_workers=1) as executor:
            waiting = None
            try:
                controller.start()
                original_process = controller.process
                created = asyncio.run(
                    server.cao_ask_user(
                        "inflight-process-question-0001",
                        [
                            server._InteractionQuestionInput(
                                client_key=f"q-{i}",
                                kind="text",
                                prompt=f"Synthetic question {i}",
                                required=True,
                            )
                            for i in range(2)
                        ],
                    )
                ).model_dump(mode="json")
                group_id = created["group_id"]
                questions = created["questions"]
                started = time.monotonic()
                waiting = executor.submit(
                    lambda: asyncio.run(
                        server.cao_wait_for_user_answers(
                            group_id, wait_seconds=30, after_event_sequence=0
                        )
                    )
                )
                _until(
                    lambda: any(
                        row["event"] == "wait_started"
                        and row["group_id"] == group_id
                        and row["worker_pid"] == original_process.pid
                        for row in read_observations(audit)
                    ),
                    deadline=started + 5,
                )
                assert not waiting.done()
                assert not any(row["event"] == "wait_returned" for row in read_observations(audit))
                controller.crash()
                assert original_process.poll() is not None
                assert not any(row["event"] == "wait_returned" for row in read_observations(audit))
                assert transport_error.wait(5), "actual_wait_connection_error_missing"
                assert not waiting.done()
                controller.start()
                replacement = controller.process
                assert replacement.pid != original_process.pid
                _until(
                    lambda: any(
                        row["event"] == "wait_started"
                        and row["group_id"] == group_id
                        and row["worker_pid"] == replacement.pid
                        for row in read_observations(audit)
                    ),
                    deadline=started + 35,
                )
                for question in questions:
                    response = httpx.post(
                        f"{spec.base_url}/interactions/{question['question_id']}/answers",
                        headers={
                            "If-Match": f'"{question["version"]}"',
                            "Idempotency-Key": f"inflight-answer-{question['question_id']}",
                        },
                        json={
                            "expected_version": question["version"],
                            "response": {"kind": "text", "text": "Synthetic operator answer"},
                        },
                        trust_env=False,
                        timeout=5,
                    )
                    assert response.status_code in {200, 201}
                result = waiting.result(
                    timeout=max(0.1, started + 35 - time.monotonic())
                ).model_dump(mode="json")
                assert result["state"] == "ready"
                assert result["group_id"] == group_id
                assert {a["question_id"] for a in result["answers"]} == {
                    q["question_id"] for q in questions
                }
                receipts = [
                    server._InteractionAckInput(
                        **{k: answer[k] for k in ("question_id", "version", "receipt")}
                    )
                    for answer in result["answers"]
                ]
                ack = asyncio.run(
                    server.cao_acknowledge_user_answers(group_id, receipts)
                ).model_dump(mode="json")
                assert ack == {"success": True, "acknowledged": 2}
                assert len(wait_paths) >= 2 and set(wait_paths) == {
                    (f"/agent/interactions/groups/{group_id}/wait", 0)
                }
                with source.service.repository.read_connection() as connection:
                    for table, expected in (
                        ("interaction_groups", 1),
                        ("interaction_questions", 2),
                        ("interaction_answers", 2),
                        ("interaction_deliveries", 2),
                    ):
                        assert (
                            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                            == expected
                        )
                    assert (
                        connection.execute(
                            "SELECT count(*) FROM interaction_deliveries WHERE state='agent_acknowledged'"
                        ).fetchone()[0]
                        == 2
                    )
                    counts = dict(
                        (row[0], row[1])
                        for row in connection.execute(
                            "SELECT event_type,count(*) FROM interaction_events WHERE group_id=%s GROUP BY event_type",
                            (group_id,),
                        ).fetchall()
                    )
                assert counts["interaction.answer_returned"] == 2
                assert counts["interaction.answer_acknowledged"] == 2
                observations = read_observations(audit)
                entries = [row for row in observations if row["event"] == "wait_started"]
                assert [row["worker_pid"] for row in entries] == [
                    original_process.pid,
                    replacement.pid,
                ]
                assert 0 <= entries[1]["wait_seconds"] < entries[0]["wait_seconds"] <= 30
                returned = [row for row in observations if row["event"] == "wait_returned"]
                assert len(returned) == 1 and returned[0]["state"] == "ready"
            finally:
                controller.stop()
                if waiting is not None:
                    waiting.result(timeout=35)
