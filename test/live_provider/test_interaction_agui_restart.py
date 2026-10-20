"""Offline contracts for the real-provider AG-UI restart harness."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from test.live_provider import interaction_cli_journey as journey
from test.live_provider.interaction_agui_restart import (
    API_WORKER_READINESS_TIMEOUT_SECONDS,
    ApiWorkerController,
    WorkerSpec,
    ack_identity_diagnostics,
    append_observation,
    build_api_worker_spec,
    delivery_state_updates,
    open_private_log,
    parse_sse_frames,
    read_observations,
    snapshot_from_frame,
    snapshot_identity_map,
)

import pytest
import yaml

pytestmark = pytest.mark.offline_database


def _valid_interaction_item(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "question_id": "q-1",
        "version": 1,
        "group_id": "g-1",
        "lifecycle": "open",
        "delivery_state": None,
        "source": {
            "terminal_id": "t-1",
            "incarnation_id": "i-1",
            "provider": "codex",
            "provider_version": "codex-cli 1.2.3",
            "execution_mode": "interactive",
        },
        "task": None,
        "kind": "text",
        "required": True,
        "recipient": {"kind": "human", "label": "operator", "reason": "human"},
        "group_progress": {"required_total": 2, "required_answered": 0},
        "origin": "explicit",
        "can_answer": False,
        "actions": [],
        "manual_reason": None,
        "created_at": "2026-10-15T00:00:00Z",
        "expires_at": None,
        "answer_receipt": None,
    }
    item.update(overrides)
    return item


def _snapshot_frame(interaction_overrides: dict[str, object] | None = None):
    interactions: dict[str, object] = {
        "available": True,
        "items": [_valid_interaction_item()],
        "next_cursor": None,
        "truncated": False,
        "event_sequence": 1,
    }
    interactions.update(interaction_overrides or {})
    encoded = json.dumps({"snapshot": {"interactions": interactions}})
    return parse_sse_frames(f"event: STATE_SNAPSHOT\ndata: {encoded}\n\n")[0]


def _receipt_with_unknown_secret(interactions: dict[str, object]) -> None:
    receipt = {
        "question_id": "q-1",
        "version": 1,
        "answer_id": "a-1",
        "lifecycle": "answered",
        "delivery_state": "agent_acknowledged",
        "accepted_at": "2026-10-15T00:00:01Z",
        "private_secret": "synthetic-secret",
    }
    interactions["items"] = [_valid_interaction_item(answer_receipt=receipt, lifecycle="answered")]


def _task_with_unknown_secret(interactions: dict[str, object]) -> None:
    interactions["items"] = [
        _valid_interaction_item(
            task={
                "work_item_id": "w-1",
                "attempt_id": "a-1",
                "session_name": "session-1",
                "private_secret": "synthetic-secret",
            }
        )
    ]


def test_sse_parser_handles_named_multiline_frames_and_ignores_comments() -> None:
    frames = parse_sse_frames(
        ": keep-alive\n\n"
        "id: stream-7\n"
        "event: STATE_SNAPSHOT\n"
        'data: {"snapshot":\n'
        'data: {"interactions":{"available":true,"items":['
        + json.dumps(_valid_interaction_item())
        + '],"next_cursor":null,"truncated":false,"event_sequence":1}}}\n\n'
    )

    assert len(frames) == 1
    assert frames[0].event == "STATE_SNAPSHOT"
    assert frames[0].event_id == "stream-7"
    snapshot = snapshot_from_frame(frames[0])
    assert snapshot["interactions"]["items"][0]["question_id"] == "q-1"


def test_snapshot_parser_rejects_incomplete_or_content_bearing_projection() -> None:
    incomplete = parse_sse_frames('event: STATE_SNAPSHOT\ndata: {"snapshot":{}}')
    assert incomplete == ()

    prompt_item = _valid_interaction_item(prompt="private prompt")
    content = parse_sse_frames(
        "event: STATE_SNAPSHOT\ndata: "
        + json.dumps(
            {
                "snapshot": {
                    "interactions": {
                        "available": True,
                        "items": [prompt_item],
                        "next_cursor": None,
                        "truncated": False,
                        "event_sequence": 1,
                    }
                }
            }
        )
        + "\n\n"
    )
    with pytest.raises(ValueError, match="unsafe_interaction_projection"):
        snapshot_from_frame(content[0])


def test_delta_parser_tracks_only_safe_delivery_state_fields() -> None:
    frame = parse_sse_frames(
        'event: STATE_DELTA\ndata: {"delta":[{"op":"replace",'
        '"path":"/interactions/items/0/delivery_state","value":"agent_acknowledged"}]}\n\n'
    )[0]

    assert delivery_state_updates(
        frame,
        [{"question_id": "q-1"}],
        forbidden_content=("private answer",),
    ) == {"q-1": "agent_acknowledged"}

    unsafe = parse_sse_frames(
        'event: STATE_DELTA\ndata: {"delta":[{"op":"replace",'
        '"path":"/interactions/items/0/prompt","value":"private answer"}]}\n\n'
    )[0]
    with pytest.raises(ValueError, match="unsafe_interaction_projection"):
        delivery_state_updates(
            unsafe, [{"question_id": "q-1"}], forbidden_content=("private answer",)
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda interactions: interactions.update(private_secret="synthetic-secret"),
        lambda interactions: interactions["items"][0].update(private_summary="synthetic-secret"),
        lambda interactions: interactions["items"][0]["source"].update(
            internal_token="synthetic-secret"
        ),
        lambda interactions: interactions["items"][0]["recipient"].update(
            future_private_field="synthetic-secret"
        ),
        _task_with_unknown_secret,
        _receipt_with_unknown_secret,
    ],
)
def test_snapshot_parser_rejects_unknown_fields_recursively_without_echoing_values(mutate):
    frame = _snapshot_frame()
    payload = json.loads(frame.data)
    interactions = payload["snapshot"]["interactions"]
    mutate(interactions)
    unsafe_frame = type(frame)(frame.event, json.dumps(payload), frame.event_id)

    with pytest.raises(ValueError, match="unsafe_interaction_projection") as error:
        snapshot_from_frame(unsafe_frame)

    assert "synthetic-secret" not in str(error.value)


def test_snapshot_parser_rejects_nonempty_actions_projection() -> None:
    frame = _snapshot_frame({"items": [_valid_interaction_item(actions=["answer"])]})
    with pytest.raises(ValueError, match="unsafe_interaction_projection"):
        snapshot_from_frame(frame)


def test_delta_parser_rejects_nonempty_actions_projection() -> None:
    frame = parse_sse_frames(
        'event: STATE_DELTA\ndata: {"delta":[{"op":"replace",'
        '"path":"/interactions/items/0/actions","value":["answer"]}]}\n\n'
    )[0]
    with pytest.raises(ValueError, match="unsafe_interaction_projection"):
        delivery_state_updates(frame, [{"question_id": "q-1"}])


def test_snapshot_parser_accepts_closed_receipt_projection_without_receipt_token() -> None:
    receipt = {
        "question_id": "q-1",
        "version": 1,
        "answer_id": "answer-1",
        "lifecycle": "answered",
        "delivery_state": "agent_acknowledged",
        "accepted_at": "2026-10-15T00:00:01Z",
    }
    frame = _snapshot_frame(
        {
            "items": [
                _valid_interaction_item(
                    lifecycle="answered",
                    delivery_state="agent_acknowledged",
                    answer_receipt=receipt,
                )
            ]
        }
    )

    snapshot = snapshot_from_frame(frame)
    assert snapshot_identity_map(snapshot)["q-1"]["delivery_state"] == "agent_acknowledged"


@pytest.mark.parametrize("value", [[], {}])
def test_delta_parser_rejects_unhashable_delivery_state(value: object) -> None:
    frame = parse_sse_frames(
        "event: STATE_DELTA\ndata: "
        + json.dumps(
            {
                "delta": [
                    {
                        "op": "replace",
                        "path": "/interactions/items/0/delivery_state",
                        "value": value,
                    }
                ]
            }
        )
        + "\n\n"
    )[0]

    with pytest.raises(ValueError, match="invalid_agui_delta_delivery_state"):
        delivery_state_updates(frame, [{"question_id": "q-1"}])


def test_delta_parser_rejects_unknown_operation_and_interaction_paths() -> None:
    unknown_operation = parse_sse_frames(
        'event: STATE_DELTA\ndata: {"delta":[{"op":"replace",'
        '"path":"/interactions/items/0/delivery_state",'
        '"value":"agent_acknowledged","private_secret":"synthetic-secret"}]}\n\n'
    )[0]
    with pytest.raises(ValueError, match="invalid_agui_delta_operation"):
        delivery_state_updates(unknown_operation, [{"question_id": "q-1"}])

    unknown_path = parse_sse_frames(
        'event: STATE_DELTA\ndata: {"delta":[{"op":"add",'
        '"path":"/interactions/items/0/internal_summary",'
        '"value":"synthetic-secret"}]}\n\n'
    )[0]
    with pytest.raises(ValueError, match="unsafe_interaction_projection"):
        delivery_state_updates(unknown_path, [{"question_id": "q-1"}])


def test_snapshot_identity_map_exposes_only_safe_identity_and_lifecycle_fields() -> None:
    snapshot = snapshot_from_frame(_snapshot_frame())

    assert snapshot_identity_map(snapshot) == {
        "q-1": {
            "group_id": "g-1",
            "version": 1,
            "terminal_id": "t-1",
            "incarnation_id": "i-1",
            "lifecycle": "open",
            "delivery_state": None,
        }
    }


def test_agui_restart_is_narrowly_opt_in_and_default_prompt_is_unchanged() -> None:
    default_args = journey._parse_args(["codex"])
    opt_in_args = journey._parse_args(["claude_code", "--agui-restart"])

    assert default_args.agui_restart is False
    assert opt_in_args.agui_restart is True
    ordinary_prompt = journey._prompt("operation-1")
    assert "API connection may be interrupted" not in ordinary_prompt
    assert "error with code unavailable" not in ordinary_prompt
    restart_prompt = journey._prompt("operation-1", recover_api_restart=True)
    assert "API process has restarted" in restart_prompt
    assert "exact group_id returned by cao_ask_user" in restart_prompt
    assert "If state is pending, repeat that wait for the same group." in restart_prompt
    assert "do not call cao_ask_user again" in restart_prompt
    assert "error with code unavailable" in restart_prompt
    assert "repeat that same wait for the exact group_id with wait_seconds=20" in restart_prompt
    assert "within the existing 75-second provider-task budget" in restart_prompt
    assert (
        "Stop and fail on any terminal state, any other error, or when the bound expires"
        in restart_prompt
    )
    assert "every returned question_id, version, and receipt" in restart_prompt


def test_claude_agui_profile_model_is_opt_in_and_explicit(tmp_path, monkeypatch) -> None:
    from cli_agent_orchestrator import constants

    profile_dir = tmp_path / "profiles"
    monkeypatch.setattr(constants, "LOCAL_AGENT_STORE_DIR", profile_dir)
    monkeypatch.setattr(journey, "_mcp_child_environment", lambda *_args: {})
    run_root = tmp_path / "run"

    journey._install_profile(run_root, "claude-default", "claude_code", 1, "t-1")
    journey._install_profile(run_root, "claude-agui", "claude_code", 1, "t-1", agui_restart=True)

    default_metadata = yaml.safe_load(
        (profile_dir / "claude-default.md").read_text(encoding="utf-8").split("---")[1]
    )
    agui_metadata = yaml.safe_load(
        (profile_dir / "claude-agui.md").read_text(encoding="utf-8").split("---")[1]
    )
    assert "model" not in default_metadata
    assert agui_metadata["model"] == "sonnet"
    assert journey._profile_model("claude_code", agui_restart=False) is None
    assert journey._profile_model("claude_code", agui_restart=True) == "sonnet"
    assert journey._profile_model("codex", agui_restart=True) == "gpt-6-luna"


def test_claude_agui_receipt_reports_only_the_explicit_model_pin() -> None:
    allowed = journey._safe_provider_receipt(
        {
            "provider": "claude_code",
            "requested_model": "sonnet",
            "agui_restart_enabled": True,
        }
    )
    unpinned = journey._safe_provider_receipt(
        {
            "provider": "claude_code",
            "requested_model": "sonnet",
            "agui_restart_enabled": False,
        }
    )
    arbitrary = journey._safe_provider_receipt(
        {
            "provider": "claude_code",
            "requested_model": "private-model-name",
            "agui_restart_enabled": True,
        }
    )

    assert allowed["requested_model"] == "sonnet"
    assert "requested_model" not in unpinned
    assert "requested_model" not in arbitrary
    assert journey._command_has_exact_model(["claude", "--model", "sonnet"], "sonnet")
    assert not journey._command_has_exact_model(["claude", "--model", "opus"], "sonnet")
    assert not journey._command_has_exact_model(
        ["claude", "--model", "sonnet", "--model", "opus"], "sonnet"
    )


def test_worker_spec_uses_only_run_private_config_and_strips_operator_secrets(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    run_root = tmp_path / "run"
    run_root.mkdir(mode=0o700)
    parent_env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": "/operator/home",
        "CODEX_HOME": "/operator/codex",
        "OPENAI_API_KEY": "secret-value",
        "CAO_DATABASE_CONFIG_FILE": "/operator/database-config.json",
        "CAO_API_PORT": "9999",
    }

    spec = build_api_worker_spec(
        source_root=source_root,
        run_root=run_root,
        python_executable="/venv/bin/python",
        port=43127,
        parent_env=parent_env,
    )

    assert spec.command == (
        "/venv/bin/python",
        "-m",
        "test.live_provider.interaction_api_worker",
        "--port",
        "43127",
    )
    assert spec.env["PYTHONPATH"] == f"{source_root / 'src'}{__import__('os').pathsep}{source_root}"
    assert spec.env["CAO_HOME_DIR"] == str(run_root / "cao")
    assert spec.env["CAO_DATABASE_CONFIG_FILE"] == str(run_root / "cao" / "database-config.json")
    assert spec.env["CAO_API_HOST"] == "127.0.0.1"
    assert spec.env["CAO_API_PORT"] == "43127"
    assert spec.env["CAO_AUTH_MODE"] == "off"
    assert spec.env["CAO_AGUI_ENABLED"] == "true"
    assert spec.env["HOME"] == str(run_root / "home")
    assert spec.env["TMUX_TMPDIR"] == str(run_root / "tmux")
    assert "CODEX_HOME" not in spec.env
    assert "OPENAI_API_KEY" not in spec.env
    assert "/operator" not in repr(spec.env)


def test_ack_identity_diagnostics_compare_exact_returned_receipts_without_exposing_them() -> None:
    returned = [
        {"question_id": "q-1", "version": 1, "receipt": "returned-secret-receipt-1"},
        {"question_id": "q-2", "version": 2, "receipt": "returned-secret-receipt-2"},
    ]
    acknowledged = [dict(item) for item in returned]
    result = ack_identity_diagnostics(
        created_group_id="g-1",
        created_questions=[
            {"question_id": "q-1", "version": 1},
            {"question_id": "q-2", "version": 2},
        ],
        waited_group_id="g-1",
        waited_answers=returned,
        ack_group_id="g-1",
        acknowledgements=acknowledged,
    )

    assert result == {
        "group_matches_created": True,
        "group_matches_waited": True,
        "question_versions_match_created": True,
        "question_versions_match_returned": True,
        "receipt_hashes_match_returned": True,
        "ack_count": 2,
    }
    assert "returned-secret-receipt" not in repr(result)
    acknowledged[0]["receipt"] = "different-secret-receipt"
    assert not ack_identity_diagnostics(
        created_group_id="g-1",
        created_questions=[
            {"question_id": "q-1", "version": 1},
            {"question_id": "q-2", "version": 2},
        ],
        waited_group_id="g-1",
        waited_answers=returned,
        ack_group_id="g-1",
        acknowledgements=acknowledged,
    )["receipt_hashes_match_returned"]


def test_observation_file_is_private_and_cannot_store_receipt_values(tmp_path: Path) -> None:
    path = tmp_path / "interaction-observations.jsonl"
    observation = {
        "event": "group_created",
        "group_id": "g-1",
        "questions": [{"question_id": "q-1", "version": 1}],
    }
    append_observation(path, observation)
    append_observation(
        path,
        {
            "event": "wait_returned",
            "group_id": "g-1",
            "state": "ready",
            "question_versions": [{"question_id": "q-1", "version": 1}],
        },
    )
    assert path.stat().st_mode & 0o777 == 0o600
    assert len(read_observations(path)) == 2
    assert "receipt" not in path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="invalid_interaction_observation"):
        append_observation(path, {**observation, "receipt": "secret-receipt"})


def test_worker_binding_rejects_wrong_host_or_port_before_importing_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test.live_provider.interaction_api_worker import run_worker

    monkeypatch.setenv("CAO_API_HOST", "0.0.0.0")
    monkeypatch.setenv("CAO_API_PORT", "43127")
    with pytest.raises(RuntimeError, match="api_worker_bind_mismatch"):
        run_worker(43127)


def test_api_worker_log_is_created_with_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "api-worker.log"
    with open_private_log(path) as stream:
        stream.write("private unit fixture\n")
    assert path.stat().st_mode & 0o777 == 0o600


class _FakeProcess:
    def __init__(self, pid: int, events: list[str]) -> None:
        self.pid = pid
        self.events = events
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.events.append(f"terminate:{self.pid}")
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        self.events.append(f"wait:{self.pid}")
        assert timeout is not None and timeout > 0
        return self.returncode or 0

    def kill(self) -> None:
        self.events.append(f"kill:{self.pid}")
        self.returncode = -9


def test_worker_controller_restarts_only_the_process_it_created(tmp_path: Path) -> None:
    events: list[str] = []
    created: list[_FakeProcess] = []

    def popen(command, **kwargs):
        assert kwargs["shell"] is False
        assert kwargs["start_new_session"] is True
        assert kwargs["env"] == {"CAO_HOME_DIR": str(tmp_path)}
        process = _FakeProcess(len(created) + 10, events)
        created.append(process)
        events.append(f"start:{process.pid}")
        return process

    spec = WorkerSpec(
        command=("python", "-m", "worker"),
        cwd=tmp_path,
        env={"CAO_HOME_DIR": str(tmp_path)},
        port=43127,
    )
    controller = ApiWorkerController(
        spec,
        popen_factory=popen,
        readiness_probe=lambda process, timeout: process.poll() is None,
    )

    controller.start()
    controller.restart()
    controller.stop()

    assert events == [
        "start:10",
        "terminate:10",
        "wait:10",
        "start:11",
        "terminate:11",
        "wait:11",
    ]
    assert controller.process is None


def test_worker_controller_uses_bounded_cold_start_readiness_default(tmp_path: Path) -> None:
    spec = WorkerSpec(command=("python", "-m", "worker"), cwd=tmp_path, env={}, port=43127)
    controller = ApiWorkerController(spec, readiness_probe=lambda _process, _timeout: True)

    assert controller._readiness_timeout == API_WORKER_READINESS_TIMEOUT_SECONDS == 45.0


def test_http_readiness_rejects_success_received_after_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow 200 response must not extend the bounded readiness budget."""
    import httpx

    class _LateClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> "_LateClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, _url: str) -> object:
            return type("Response", (), {"status_code": 200})()

    clock = iter((0.0, 0.0, 2.0, 2.0))
    monkeypatch.setattr(httpx, "Client", _LateClient)
    monkeypatch.setattr(journey.time, "monotonic", lambda: next(clock))

    spec = WorkerSpec(command=("python",), cwd=tmp_path, env={}, port=43127)
    controller = ApiWorkerController(spec)

    assert controller._http_readiness_probe(_FakeProcess(42, []), timeout=1.0) is False


def test_worker_controller_resolves_default_popen_after_module_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    created: list[_FakeProcess] = []

    def injected_popen(command, **kwargs):
        assert command == ["python", "-m", "worker"]
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["shell"] is False
        assert kwargs["start_new_session"] is True
        process = _FakeProcess(42, events)
        created.append(process)
        events.append(f"start:{process.pid}")
        return process

    monkeypatch.setattr(subprocess, "Popen", injected_popen)
    spec = WorkerSpec(command=("python", "-m", "worker"), cwd=tmp_path, env={}, port=43127)
    controller = ApiWorkerController(
        spec, readiness_probe=lambda process, _timeout: process.poll() is None
    )

    controller.start()
    controller.stop()

    assert len(created) == 1
    assert events == ["start:42", "terminate:42", "wait:42"]
    assert controller.process is None


def test_worker_controller_stops_child_if_readiness_fails(tmp_path: Path) -> None:
    events: list[str] = []
    spec = WorkerSpec(
        command=("python", "-m", "worker"),
        cwd=tmp_path,
        env={},
        port=43127,
    )
    controller = ApiWorkerController(
        spec,
        popen_factory=lambda *_args, **_kwargs: _FakeProcess(42, events),
        readiness_probe=lambda _process, _timeout: False,
        readiness_timeout=0.01,
    )

    with pytest.raises(TimeoutError, match="api_worker_not_ready"):
        controller.start()

    assert events == ["terminate:42", "wait:42"]
    assert controller.process is None


def test_worker_controller_does_not_wait_on_an_unowned_process(tmp_path: Path) -> None:
    spec = WorkerSpec(command=("python",), cwd=tmp_path, env={}, port=43127)
    controller = ApiWorkerController(spec, popen_factory=lambda *_a, **_k: pytest.fail("spawned"))

    controller.stop()

    assert controller.process is None


def test_agui_private_directory_preparation_reuses_base_cli_tmp(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir(mode=0o700)
    journey._base_cli_env(root, "codex")
    tmp_dir = root / "tmp"

    journey._ensure_private_directory(tmp_dir)

    metadata = tmp_dir.lstat()
    assert stat.S_ISDIR(metadata.st_mode)
    assert not stat.S_ISLNK(metadata.st_mode)
    assert metadata.st_uid == os.geteuid()
    assert stat.S_IMODE(metadata.st_mode) == 0o700


def test_agui_private_directory_preparation_creates_missing_directories(
    tmp_path: Path,
) -> None:
    root = tmp_path / "run"
    root.mkdir(mode=0o700)
    directories = tuple(root / name for name in ("home", "tmp", "tmux", "xdg-cache", "xdg-config"))

    for directory in directories:
        journey._ensure_private_directory(directory)
        metadata = directory.lstat()
        assert stat.S_ISDIR(metadata.st_mode)
        assert metadata.st_uid == os.geteuid()
        assert stat.S_IMODE(metadata.st_mode) == 0o700


def test_agui_private_directory_preparation_rejects_symlinks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "run"
    root.mkdir(mode=0o700)
    target = root / "target"
    target.mkdir(mode=0o700)
    link = root / "home"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(RuntimeError, match="unsafe_private_directory"):
        journey._ensure_private_directory(link)

    assert stat.S_ISLNK(link.lstat().st_mode)


def test_agui_private_directory_preparation_rejects_non_directories(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home"
    path.write_text("private fixture", encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(RuntimeError, match="unsafe_private_directory"):
        journey._ensure_private_directory(path)

    assert stat.S_ISREG(path.lstat().st_mode)


def test_agui_private_directory_preparation_rejects_wrong_mode(
    tmp_path: Path,
) -> None:
    path = tmp_path / "home"
    path.mkdir(mode=0o700)
    path.chmod(0o755)

    with pytest.raises(RuntimeError, match="unsafe_private_directory"):
        journey._ensure_private_directory(path)


def test_agui_private_directory_preparation_checks_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "home"
    path.mkdir(mode=0o700)
    current_uid = os.geteuid()
    monkeypatch.setattr(journey.os, "geteuid", lambda: current_uid + 1)

    with pytest.raises(RuntimeError, match="unsafe_private_directory"):
        journey._ensure_private_directory(path)


def test_agui_source_owner_matches_verified_loopback_worker_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.interactions.models import InteractionForbidden
    from cli_agent_orchestrator.services.interactions.policy import InteractionPolicy

    monkeypatch.setattr(auth, "is_auth_enabled", lambda: False)
    agui_owner = journey._source_owner_for_journey(auth, agui_restart=True)
    api_worker_principal = auth.local_operator_principal()
    assert agui_owner.kind == "local_operator"
    assert agui_owner.id == api_worker_principal.id
    source = {"owner_principal_id": agui_owner.id}
    assert InteractionPolicy.require_owner(api_worker_principal, source) is api_worker_principal

    default_owner = journey._source_owner_for_journey(auth, agui_restart=False)
    assert default_owner.kind == "jwt"
    assert default_owner.issuer == "https://live-interaction.invalid"
    assert default_owner.subject == "live-acceptance-owner"
    assert (
        InteractionPolicy.require_owner(default_owner, {"owner_principal_id": default_owner.id})
        is default_owner
    )
    with pytest.raises(InteractionForbidden):
        InteractionPolicy.require_owner(
            api_worker_principal, {"owner_principal_id": default_owner.id}
        )

    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    with pytest.raises(PermissionError):
        journey._source_owner_for_journey(auth, agui_restart=True)
