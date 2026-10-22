"""Current-catalog admission must precede an authorized real journey."""

from __future__ import annotations

import json
import subprocess
import sys
from test.live_provider import opencode_interaction_journey as journey
from test.live_provider import opencode_model_catalog as catalog
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database


def test_cli_deprecated_catalog_stops_before_journey(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        sys,
        "argv",
        ["journey", "--run-model", "--model", journey.common.OPENCODE_INTERACTION_MODEL],
    )
    monkeypatch.setattr(
        journey,
        "model_catalog",
        SimpleNamespace(
            check_model=lambda model: {
                "eligible": False,
                "reason": "deprecated",
                "status": "deprecated",
            }
        ),
    )
    monkeypatch.setattr(
        journey,
        "run",
        lambda **kwargs: calls.append(kwargs)
        or {"provider": "opencode_cli", "result": "incomplete_journey"},
    )
    assert journey.main() == 1
    assert calls == []
    assert json.loads(capsys.readouterr().out)["model_catalog"]["reason"] == "deprecated"


import subprocess
from test.live_provider import opencode_model_catalog as catalog

MODEL = journey.common.OPENCODE_INTERACTION_MODEL
NAME = MODEL.split("/", 1)[1]


def document(**fields):
    return {"opencode-go": {"models": {NAME: {"tool_call": True, **fields}}}}


@pytest.mark.parametrize("status", ["active", "alpha", "beta", None])
def test_current_catalog_eligibility_is_not_an_inference_claim(status):
    result = catalog.assess_catalog(document(status=status), MODEL)
    assert result == {"eligible": True, "reason": "eligible", "status": status or "unspecified"}


@pytest.mark.parametrize(
    "payload,reason",
    [
        (document(status="deprecated"), "deprecated"),
        ({}, "not_found"),
        ({"opencode-go": {"models": {}}}, "not_found"),
        ([], "catalog_invalid"),
        ({"opencode-go": []}, "catalog_invalid"),
        ({"opencode-go": {"models": []}}, "catalog_invalid"),
        (document(status="SECRET_NEVER_EXPORT"), "catalog_invalid"),
        (document(status={}), "catalog_invalid"),
        (document(tool_call=False), "tool_calling_unavailable"),
        (document(tool_call=1), "tool_calling_unavailable"),
        (document(tool_call=None), "tool_calling_unavailable"),
    ],
)
def test_rejection_is_closed_and_does_not_echo_payload(payload, reason):
    result = catalog.assess_catalog(payload, MODEL)
    assert result["eligible"] is False
    assert result["reason"] == reason
    assert "SECRET_NEVER_EXPORT" not in json.dumps(result)


@pytest.mark.parametrize(
    "model", [None, "", "secret", "go/model?SECRET_NEVER_EXPORT", "../bad/model", "go/" + "a" * 121]
)
def test_invalid_model_never_fetches(monkeypatch, model):
    monkeypatch.setattr(
        catalog.subprocess, "run", lambda *a, **k: pytest.fail("catalog process must not start")
    )
    assert catalog.check_model(model)["reason"] == "invalid_model"


def test_catalog_child_has_fixed_destination_no_ambient_auth_and_hard_deadline(monkeypatch):
    calls = []
    monkeypatch.setenv("HTTPS_PROXY", "SECRET_NEVER_EXPORT")
    monkeypatch.setenv("OPENCODE_AUTH_CONTENT", "SECRET_NEVER_EXPORT")
    body = json.dumps(document(status="deprecated")).encode()

    def child(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"200\n" + body)

    monkeypatch.setattr(catalog.subprocess, "run", child)
    result = catalog.check_model(MODEL)
    assert result["reason"] == "deprecated"
    assert result["http_status"] == 200
    assert result["catalog_bytes"] == len(body)
    assert len(result["catalog_sha256"]) == 64
    args, options = calls.pop()
    assert calls == []
    assert args[1:3] == ["-I", "-c"]
    assert "HTTPSConnection('models.opencode.ai'" in args[3]
    assert options["env"] == {"PATH": catalog.os.defpath, "LANG": "C.UTF-8"}
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stderr"] == subprocess.DEVNULL
    assert options["timeout"] == 10
    assert "SECRET_NEVER_EXPORT" not in json.dumps(result)


@pytest.mark.parametrize(
    "output,returncode,reason",
    [
        (b"200\nSECRET_NEVER_EXPORT", 0, "catalog_invalid"),
        (b"302\nSECRET_NEVER_EXPORT", 0, "catalog_http_error"),
        (b"503\nSECRET_NEVER_EXPORT", 0, "catalog_http_error"),
        (b"oversized", 0, "catalog_oversized"),
        (b"SECRET_NEVER_EXPORT", 1, "catalog_unavailable"),
        (b"SECRET_NEVER_EXPORT", 0, "catalog_unavailable"),
    ],
)
def test_reader_failures_do_not_retry_or_leak(monkeypatch, output, returncode, reason):
    calls = []
    monkeypatch.setattr(
        catalog.subprocess,
        "run",
        lambda *a, **k: calls.append(1) or SimpleNamespace(returncode=returncode, stdout=output),
    )
    result = catalog.check_model(MODEL)
    assert result["eligible"] is False
    assert result["reason"] == reason
    assert calls == [1]
    assert "SECRET_NEVER_EXPORT" not in json.dumps(result)


def test_child_timeout_is_not_model_unavailability(monkeypatch):
    calls = []

    def timeout(*args, **kwargs):
        calls.append(1)
        raise subprocess.TimeoutExpired("SECRET_NEVER_EXPORT", 10, output=b"SECRET_NEVER_EXPORT")

    monkeypatch.setattr(catalog.subprocess, "run", timeout)
    result = catalog.check_model(MODEL)
    assert result["reason"] == "catalog_unavailable"
    assert calls == [1]
    assert "SECRET_NEVER_EXPORT" not in json.dumps(result)


@pytest.mark.parametrize("argv", [[], ["--run-model"], ["--run-model", "--model", "unknown/model"]])
def test_unapproved_or_invalid_invocation_does_not_read_catalog(monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", ["journey", *argv])
    monkeypatch.setattr(
        journey.model_catalog, "check_model", lambda model: pytest.fail("unexpected catalog access")
    )
    monkeypatch.setattr(
        journey, "_private_opencode_root", lambda: pytest.fail("unexpected runtime allocation")
    )
    assert journey.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["result"] in {"model_run_not_approved", "explicit_model_required"}


def test_eligible_catalog_delegates_to_unchanged_journey(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(sys, "argv", ["journey", "--run-model", "--model", MODEL])
    monkeypatch.setattr(
        journey.model_catalog,
        "check_model",
        lambda model: {"eligible": True, "reason": "eligible", "status": "active"},
    )
    monkeypatch.setattr(
        journey,
        "run",
        lambda **kwargs: calls.append(kwargs)
        or {"provider": "opencode_cli", "result": "incomplete_journey"},
    )
    assert journey.main() == 1
    assert calls == [{"model": MODEL, "run_model": True}]
    assert json.loads(capsys.readouterr().out)["result"] == "incomplete_journey"


def test_catalog_size_limit_is_checked_before_json(monkeypatch):
    monkeypatch.setattr(catalog, "CATALOG_MAX_BYTES", 8)
    monkeypatch.setattr(
        catalog.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"200\n" + b"x" * 9),
    )
    assert catalog.check_model(MODEL)["reason"] == "catalog_oversized"


def test_catalog_deadline_reaps_the_actual_reader_process(monkeypatch):
    processes = []
    original = subprocess.Popen

    def track(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(catalog.subprocess, "Popen", track)
    monkeypatch.setattr(catalog, "_CATALOG_READER", "import time; time.sleep(60)")
    monkeypatch.setattr(catalog, "CATALOG_DEADLINE_SECONDS", 0.25)
    assert catalog.check_model(MODEL)["reason"] == "catalog_unavailable"
    assert len(processes) == 1
    assert processes[0].poll() is not None
