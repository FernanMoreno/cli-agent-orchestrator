"""A wrapped marker never replaces current durable completion evidence."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "validate_collaboration_demo",
    Path(__file__).resolve().parents[2] / "scripts/validate_collaboration_demo.py",
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def response(status=200, output="RONDA2_DO\nNE — Sent findings."):
    return SimpleNamespace(status_code=status, json=lambda: {"output": output})


def row():
    return {
        "status": "completed",
        "turn_sequence": 2,
        "turn_completed": 2,
        "turn": {"state": "verified", "generation": "new"},
    }


def test_wrapped_marker_requires_current_sequence_and_new_generation():
    assert runner.verified_turn(row(), response(), "RONDA2_DONE", 2, "old")


@pytest.mark.parametrize(
    "key,value",
    [
        ("status", "processing"),
        ("turn_sequence", 1),
        ("turn_sequence", None),
        ("turn_sequence", True),
        ("turn_completed", 1),
    ],
)
def test_stale_or_unfinished_turn_cannot_pass(key, value):
    data = row()
    data[key] = value
    assert not runner.verified_turn(data, response(), "RONDA2_DONE", 2, "old")


@pytest.mark.parametrize(
    "turn",
    [
        None,
        {},
        {"state": "sent", "generation": "new"},
        {"state": "verified"},
        {"state": "verified", "generation": "old"},
    ],
)
def test_marker_cannot_bypass_pending_or_old_receipt(turn):
    data = row()
    data["turn"] = turn
    assert not runner.verified_turn(data, response(), "RONDA2_DONE", 2, "old")


@pytest.mark.parametrize(
    "result", [response(409), response(output=""), response(output="Previous findings")]
)
def test_only_verified_http_result_with_current_marker_can_pass(result):
    assert not runner.verified_turn(row(), result, "RONDA2_DONE", 2, "old")


def test_preflight_rejects_missing_auth_before_runtime_allocation(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "api").mkdir(parents=True)
    (project / "api/server.py").write_text("pass")
    with pytest.raises(SystemExit, match="missing reusable provider login"):
        runner.preflight(project, tmp_path / "empty-home")


def test_preflight_rejects_missing_provider_binary(tmp_path, monkeypatch):
    (tmp_path / "api").mkdir()
    (tmp_path / "api/server.py").write_text("pass")
    for relative in (
        ".claude/.credentials.json",
        ".claude.json",
        ".codex/auth.json",
        ".local/share/opencode/auth.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="missing provider executable"):
        runner.preflight(tmp_path, tmp_path)


def test_preflight_detects_expired_claude_login_without_reading_secrets(tmp_path, monkeypatch):
    import json

    (tmp_path / "api").mkdir()
    (tmp_path / "api/server.py").write_text("pass")
    for relative in (
        ".claude/.credentials.json",
        ".claude.json",
        ".codex/auth.json",
        ".local/share/opencode/auth.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"claudeAiOauth": {"expiresAt": 1, "accessToken": "do-not-show"}})
        )
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/bin/" + name)
    with pytest.raises(SystemExit, match="claude auth login") as error:
        runner.preflight(tmp_path, tmp_path)
    assert "do-not-show" not in str(error.value)


@pytest.mark.parametrize(
    "result",
    [
        response(202, "COLLABORATION_REVIEW_FINAL API Frontend"),
        response(output="Frontend received. API result still pending."),
    ],
)
def test_initial_review_waits_for_verified_final_synthesis(result):
    assert not runner.initial_review_verified(row(), result)


def test_initial_review_accepts_current_final_receipt():
    assert runner.initial_review_verified(
        row(), response(output="COLLABORATION_REVIEW_FINAL API and Frontend reviewed")
    )


def test_initial_review_rejects_marker_in_unfinished_turn():
    data = row()
    data["turn"] = {"state": "sent", "generation": "new"}
    assert not runner.initial_review_verified(
        data, response(output="COLLABORATION_REVIEW_FINAL API Frontend")
    )
