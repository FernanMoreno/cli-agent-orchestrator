"""Safety and cleanup of the opt-in real workflow fixture, without model calls."""

import json
from pathlib import Path
from test.fixtures import live_workflow as fixture
from types import SimpleNamespace

import pytest


@pytest.fixture
def subscription(tmp_path, monkeypatch):
    auth = tmp_path / "operator" / ".codex" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None}))
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv("CAO_LIVE_WORKFLOW_PROVIDER", "codex")
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_AUTH_HOME", str(auth.parent.parent))
    monkeypatch.setattr(fixture.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(fixture.subprocess, "run", lambda *args, **kwargs: None)
    return auth


def test_subscription_only_and_isolated_config(subscription, tmp_path, monkeypatch):
    captured = {}
    stopped = []
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-forbidden-key")
    monkeypatch.setenv("CODEX_HOME", "/operator/private-profile")

    def start(home, port, *, extra_env):
        captured.update(extra_env)
        return SimpleNamespace(stop=lambda: stopped.append(True))

    monkeypatch.setattr(fixture, "_start_cao_server", start)
    original = subscription.read_bytes()
    run = fixture.live_workflow_server.__wrapped__(tmp_path)
    next(run)
    assert captured["OPENAI_API_KEY"] == ""
    assert captured["CODEX_HOME"].startswith(str(tmp_path))
    assert Path(captured["TMUX_TMPDIR"]).is_dir()
    run.close()
    assert stopped == [True]
    assert not Path(captured["TMUX_TMPDIR"]).exists()
    assert not (tmp_path / "live-workflow-home/.codex/auth.json").exists()
    assert subscription.read_bytes() == original


def test_startup_failure_cleans_private_auth(subscription, tmp_path, monkeypatch):
    captured = {}

    def fail(home, port, *, extra_env):
        captured.update(extra_env)
        raise RuntimeError("injected startup failure")

    monkeypatch.setattr(fixture, "_start_cao_server", fail)
    run = fixture.live_workflow_server.__wrapped__(tmp_path)
    with pytest.raises(RuntimeError, match="injected startup"):
        next(run)
    assert not Path(captured["TMUX_TMPDIR"]).exists()
    assert not (tmp_path / "live-workflow-home/.codex/auth.json").exists()
    assert subscription.exists()


def test_api_billing_receipt_is_rejected(subscription, tmp_path):
    subscription.write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "synthetic"}))
    run = fixture.live_workflow_server.__wrapped__(tmp_path)
    with pytest.raises(AssertionError, match="subscription login"):
        next(run)
    assert not (tmp_path / "live-workflow-home/.codex/auth.json").exists()
