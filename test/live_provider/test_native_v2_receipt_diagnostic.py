"""Native runner's API-only stage observer preparation and failure preservation."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH = (
    Path(__file__).resolve().parents[2]
    / "specs/025-provider-mcp-profiles/fixtures/run_native_v2_acceptance.py"
)
SPEC = importlib.util.spec_from_file_location("t133_native_runner", PATH)
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def test_api_bootstrap_uses_only_current_epoch_and_pinned_core_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "REPO", tmp_path / "repo")
    calls = []

    def prepare(*args):
        calls.append(args)
        return ":".join(map(str, [args[0], runner.REPO / "src", runner.REPO]))

    sources = {key: "a" * 64 for key in runner.RECEIPT_STAGE_SOURCE_FILES}
    sources["PRIVATE_UNRELATED_PATH"] = "PRIVATE_CANARY"
    result = runner._prepare_receipt_stage_api_pythonpath(
        observer=SimpleNamespace(prepare_api_observer=prepare),
        bootstrap_root=tmp_path / "bootstrap",
        raw_dir=tmp_path / "raw",
        source_hashes=sources,
        role="api_b",
    )
    assert result.split(":")[0] == str(tmp_path / "bootstrap/api_b")
    assert calls[0][0] == tmp_path / "bootstrap/api_b"
    assert set(calls[0][3]) == set(runner.RECEIPT_STAGE_SOURCE_FILES)
    assert calls[0][-1] == "api_b"
    assert "PRIVATE" not in json.dumps(calls[0][3])


def test_alive_api_writers_are_never_read():
    def forbidden(*args, **kwargs):
        pytest.fail("live-writer observer must not read evidence")

    result = runner._capture_receipt_stage_diagnostic_safely(
        observer=SimpleNamespace(read_api_observation=forbidden),
        raw_dir=None,
        writers_dead=False,
    )
    assert result["status"] == "unavailable"
    assert result["acceptance_input_changed"] is False


def test_collection_failure_cannot_export_exception_or_replace_original_failure(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(runner, "EVIDENCE", tmp_path)

    def broken(*args, **kwargs):
        raise KeyboardInterrupt("PRIVATE_CALLBACK_CANARY")

    result = runner._capture_receipt_stage_diagnostic_safely(
        observer=SimpleNamespace(read_api_observation=broken),
        raw_dir=tmp_path / "raw/receipt-verification",
        writers_dead=True,
    )
    assert result["status"] == "unavailable"
    assert result["acceptance_input_changed"] is False
    assert "PRIVATE_CALLBACK_CANARY" not in json.dumps(result)


def test_actual_private_ledgers_reach_runner_collector(tmp_path, monkeypatch):
    from test.live_provider import receipt_verification_observer as observer

    monkeypatch.setattr(runner, "EVIDENCE", tmp_path)
    (tmp_path / "raw").mkdir(mode=0o700)
    raw = tmp_path / "raw/receipt-verification"
    raw.mkdir(mode=0o700)
    for role in ("api_a", "api_b"):
        writer = observer._LedgerWriter(raw, role)
        writer.emit(1, "attempt", "started")
        writer.close()
    result = runner._capture_receipt_stage_diagnostic_safely(
        observer=observer,
        raw_dir=raw,
        writers_dead=True,
    )
    assert result["status"] == "complete"
    assert result["acceptance_input_changed"] is False
    for row in result["epochs"].values():
        assert row["observer_status"] == "complete"
        assert row["event_count"] == 1
        assert row["stage_outcome_counts"]["attempt"]["started"] == 1


def test_api_observer_ready_guard_checks_owned_startup_artifact(tmp_path):
    role = tmp_path / "api_a"
    role.mkdir(mode=0o700)
    assert runner._receipt_stage_api_ready(tmp_path, "api_a") is False
    event = role / "events.jsonl"
    event.write_bytes(b"")
    event.chmod(0o600)
    assert runner._receipt_stage_api_ready(tmp_path, "api_a") is True
    event.chmod(0o644)
    assert runner._receipt_stage_api_ready(tmp_path, "api_a") is False


@pytest.mark.parametrize("install_bootstrap", [True, False])
def test_real_api_controller_checks_observer_initialization(
    tmp_path, postgres_worker_targets, install_bootstrap
):
    import ast
    import hashlib
    import inspect
    import os
    from test.fixtures import cao_server
    from test.live_provider import opencode_interaction_journey as journey
    from test.live_provider import receipt_verification_observer as observer

    tree = ast.parse(inspect.getsource(runner._run_enabled))
    node = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "start_api"
    )
    for name in ("home", "bootstrap", "raw"):
        (tmp_path / name).mkdir(mode=0o700)
    context = {"bootstrap_root": tmp_path / "bootstrap", "raw_dir": tmp_path / "raw"}
    source_hashes = {
        name: hashlib.sha256((runner.REPO / name).read_bytes()).hexdigest()
        for name in runner.RECEIPT_STAGE_SOURCE_FILES
    }
    fake = SimpleNamespace(
        prepare_api_observer=lambda boot, raw, repo, hashes, role: os.pathsep.join(
            map(str, (boot, repo / "src", repo))
        )
    )
    namespace = dict(vars(runner))
    namespace.update(
        case=runner._case_definition(),
        api_b_launch_attempted=False,
        receipt_stage_observer=observer if install_bootstrap else fake,
        receipt_stage_context=context,
        runner_manifest={"source_sha256s": source_hashes},
        api_extra={"CAO_AUTH_MODE": "off"},
        home=tmp_path / "home",
        api_port=cao_server._pick_free_port(),
        targets=postgres_worker_targets,
        api_allowlist=frozenset(),
        _start_cao_server=cao_server._start_cao_server,
        api_servers=[],
        failures=set(),
        journey=journey,
    )
    before = set(cao_server._LIVE_SERVERS)
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(PATH), "exec"
        ),
        namespace,
    )
    try:
        if install_bootstrap:
            server = namespace["start_api"]()
            assert server is not None
            assert runner._receipt_stage_api_ready(context["raw_dir"], "api_a") is True
        else:
            with pytest.raises(runner.GateError, match="receipt_stage_api_bootstrap_not_ready"):
                namespace["start_api"]()
    finally:
        for server in set(cao_server._LIVE_SERVERS) - before:
            server.stop()
    if install_bootstrap:
        summary = observer.read_api_observation(context["raw_dir"], role="api_a", writers_dead=True)
        assert summary["observer_status"] == "complete"
