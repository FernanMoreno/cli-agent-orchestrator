"""Private diagnostic inputs must not change render guard behavior."""

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from test.live_provider import receipt_verification_observer as observer
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database


def _provider():
    return SimpleNamespace(_render_guard_signature=lambda text: "".join(text.split()))


def test_private_pair_preserves_returns_and_restores_exact_hook(tmp_path):
    module = _provider()
    original = module._render_guard_signature
    capture = observer._RenderInputCapture(tmp_path / "capture", module, lambda: 1)
    assert module._render_guard_signature("**Estado** ñ") == original("**Estado** ñ")
    assert module._render_guard_signature("Estado ñ") == original("Estado ñ")
    capture.close()
    assert module._render_guard_signature is original
    files = sorted((tmp_path / "capture").glob("input-*.json"))
    assert [json.loads(p.read_text())["text"] for p in files] == ["**Estado** ñ", "Estado ñ"]
    assert [json.loads(p.read_text())["part_ordinal"] for p in files] == [1, 2]
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in files)
    assert stat.S_IMODE((tmp_path / "capture").stat().st_mode) == 0o700


def test_capture_never_swallows_original_exception(tmp_path):
    error = RuntimeError("private exception")

    def original(text):
        raise error

    module = SimpleNamespace(_render_guard_signature=original)
    capture = observer._RenderInputCapture(tmp_path / "capture", module, lambda: 0)
    try:
        with pytest.raises(RuntimeError) as caught:
            module._render_guard_signature("secret")
        assert caught.value is error
    finally:
        capture.close()
    assert module._render_guard_signature is original
    assert not list((tmp_path / "capture").glob("input-*.json"))


def test_capture_has_fixed_file_and_input_limits(tmp_path):
    module = _provider()
    capture = observer._RenderInputCapture(tmp_path / "capture", module, lambda: 0)
    for index in range(12):
        assert module._render_guard_signature(str(index)) == str(index)
    capture.close()
    assert len(list((tmp_path / "capture").glob("input-*.json"))) == 8
    assert json.loads((tmp_path / "capture/seal.json").read_text())["overflow"] is True
    second = observer._RenderInputCapture(tmp_path / "large", module, lambda: 0)
    text = "x" * (65536 + 1)
    assert module._render_guard_signature(text) == text
    second.close()
    assert not list((tmp_path / "large").glob("input-*.json"))


def test_capture_write_failure_and_late_callback_preserve_result(tmp_path, monkeypatch):
    module = _provider()
    original = module._render_guard_signature
    capture = observer._RenderInputCapture(tmp_path / "capture", module, lambda: 0)
    wrapped = module._render_guard_signature

    def fail(text):
        raise KeyboardInterrupt("private capture failure")

    monkeypatch.setattr(capture, "_record_input", fail)
    assert wrapped("a b") == "ab"
    capture.close()
    assert module._render_guard_signature is original
    assert wrapped("c d") == "cd"
    assert not list((tmp_path / "capture").glob("input-*.json"))


def test_capture_does_not_restore_over_a_replacement_hook(tmp_path):
    module = _provider()
    capture = observer._RenderInputCapture(tmp_path / "capture", module, lambda: 0)
    replacement = lambda text: "replacement"
    module._render_guard_signature = replacement
    capture.close()
    assert module._render_guard_signature is replacement
    assert json.loads((tmp_path / "capture/seal.json").read_text())["failed"] is True


def test_installed_api_bootstrap_captures_separately_from_fixed_stage_ledger(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    hashes = {
        relative: hashlib.sha256((repo / relative).read_bytes()).hexdigest()
        for relative in observer.SOURCE_MODULES.values()
    }
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    pythonpath = observer.prepare_api_observer(
        tmp_path / "bootstrap", raw, repo, hashes, "api_a", capture_render_inputs=True
    )
    code = r"""
from cli_agent_orchestrator.providers import opencode_cli as provider
from cli_agent_orchestrator.utils import receipt_verification_diagnostics as diagnostics
assert getattr(provider._render_guard_signature, "__wrapped__", None) is not None
with diagnostics.receipt_verification_attempt(1):
    with diagnostics.receipt_verification_diagnostic_stage("native_render_guard") as finish:
        assert provider._render_guard_signature("**private-render-canary** ñ") == "**private-render-canary**ñ"
        assert provider._render_guard_signature("private-render-canary ñ") == "private-render-canaryñ"
        finish("rejected")
print("capture-control-complete")
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo,
        env={
            "HOME": str(tmp_path / "home"),
            "PATH": os.defpath,
            "PYTHONPATH": pythonpath,
            "PYTHONDONTWRITEBYTECODE": "1",
            "CAO_TEST_POSTGRES_AUTOSTART": "0",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "private-render-canary" not in completed.stdout + completed.stderr
    summary = observer.read_api_observation(raw, role="api_a", writers_dead=True)
    assert summary["observer_status"] == "complete"
    assert summary["stage_outcome_counts"]["native_render_guard"]["rejected"] == 1
    assert "private-render-canary" not in json.dumps(summary)
    files = sorted((raw / "render-inputs/api_a").glob("input-*.json"))
    assert len(files) == 2
    assert all("private-render-canary" in json.loads(p.read_text())["text"] for p in files)
    assert json.loads((raw / "render-inputs/api_a/seal.json").read_text())["failed"] is False
    default_raw = tmp_path / "default-raw"
    default_raw.mkdir(mode=0o700)
    observer.prepare_api_observer(
        tmp_path / "default-bootstrap", default_raw, repo, hashes, "api_b"
    )
    default_config = json.loads(
        (tmp_path / "default-bootstrap" / observer.CONFIG_FILENAME).read_text()
    )
    assert "capture_render_inputs" not in default_config
    assert not (default_raw / "render-inputs").exists()
