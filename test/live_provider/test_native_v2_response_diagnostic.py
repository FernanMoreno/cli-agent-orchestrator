"""Runner binding and private snapshot controls for the T128 diagnostic."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from test.live_provider import opencode_http_prompt_observer as prompt_observer
from test.live_provider import opencode_http_response_argument_observer as response_observer

import pytest

RUNNER_PATH = (
    Path(__file__).resolve().parents[2]
    / "specs/025-provider-mcp-profiles/fixtures/run_native_v2_acceptance.py"
)
SPEC = importlib.util.spec_from_file_location("native_v2_t128_response_runner", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)

NONCE = "1234567890abcdef1234567890abcdef"
SENDER_ID = "deadbeef"
RECEIVER_ID = "feedface"
PRIVATE_CALL_ID = "private-response-call-id"
PRIVATE_RECEIVER = "private-receiver-id"


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _private_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as writer:
        writer.write(content)
        writer.flush()
        os.fsync(writer.fileno())


def _write_json(path: Path, document: dict[str, object]) -> bytes:
    content = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _private_file(path, content)
    return content


def _source_bytes(relative: str) -> bytes:
    return (runner.REPO / relative).read_bytes()


def _source_hashes() -> dict[str, str]:
    paths = (
        "test/live_provider/opencode_http_prompt_observer.js",
        "test/live_provider/opencode_http_prompt_observer.py",
        "test/live_provider/opencode_http_response_argument_observer.js",
        "test/live_provider/opencode_http_response_argument_observer.py",
        "test/live_provider/opencode_http_response_protocol.js",
    )
    return {path: hashlib.sha256(_source_bytes(path)).hexdigest() for path in paths}


def _seed_response_run(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path, dict[str, object]]:
    root = _private_directory(tmp_path / "owned-run")
    work = _private_directory(root / "sender-workdir")
    evidence = _private_directory(root / "evidence")
    raw_dir = _private_directory(evidence / "raw")

    prompt_js_path = runner.REPO / "test/live_provider/opencode_http_prompt_observer.js"
    prompt_source = prompt_js_path.read_bytes()
    prompt_plugin = runner._http_prompt_plugin_bytes(prompt_source, nonce=NONCE)
    private_prompt_source = tmp_path / "prompt-source.js"
    _private_file(private_prompt_source, prompt_plugin)
    prompt_observer.install_project_plugin(work, private_prompt_source, NONCE)

    phase_prompts = tuple(
        runner._phase_prompt(index, RECEIVER_ID, message)
        for index, (_phase, message) in enumerate(runner.PHASES)
    )
    prompt_document = runner._http_prompt_expectations(
        sender_id=SENDER_ID,
        nonce=NONCE,
        phase_prompts=phase_prompts,
    )
    prompt_expectation_path = prompt_observer.write_expectations(work, prompt_document)
    prompt_expectation_bytes = runner._read_owned_private_file(
        prompt_expectation_path, maximum=prompt_observer.MAX_EXPECTATION_BYTES
    )

    response_relative = "test/live_provider/opencode_http_response_argument_observer.js"
    protocol_relative = "test/live_provider/opencode_http_response_protocol.js"
    response_source_path = runner.REPO / response_relative
    protocol_source_path = runner.REPO / protocol_relative
    response_source = response_source_path.read_bytes()
    protocol_source = protocol_source_path.read_bytes()
    response_plugin = response_observer.specialize_plugin_bytes(response_source, NONCE)
    response_ledger = response_observer.install_project_plugin(
        work, response_source_path, protocol_source_path, NONCE
    )
    response_document = runner._http_response_expectations(nonce=NONCE)
    response_expectation_path = response_observer.write_expectations(work, response_document)
    response_expectation_bytes = runner._read_owned_private_file(
        response_expectation_path, maximum=response_observer.MAX_EXPECTATION_BYTES
    )
    if json.loads(response_expectation_bytes) != response_document:
        raise AssertionError("response_expectation_readback_mismatch")

    response_ledger.mkdir(mode=0o700)
    for ordinal, (_phase, message) in enumerate(runner.PHASES, start=1):
        byte_count = len(message.encode("utf-8"))
        _write_json(
            response_ledger / f"response-{ordinal:04d}.json",
            {
                "schema_version": 1,
                "nonce": NONCE,
                "ordinal": ordinal,
                "phase_ordinal": ordinal,
                "request_bound": True,
                "status": "observed",
                "reason": "none",
                "target_tool_call_count": 1,
                "argument_exact": True,
                "known_punctuation_variant_exact": False,
                "expected_utf8_bytes": byte_count,
                "observed_utf8_bytes": byte_count,
                "clone_complete": True,
                "original_not_replaced": True,
            },
        )
    _write_json(
        response_ledger / "seal.json",
        {
            "schema_version": 1,
            "nonce": NONCE,
            "hook_disposed": True,
            "trace_complete": True,
            "request_count": 2,
            "response_count": 2,
            "durable_record_count": 2,
            "overflow": False,
        },
    )

    monkeypatch.setattr(runner, "EVIDENCE", evidence)
    monkeypatch.setattr(runner, "RUN_MANIFEST", {"source_sha256s": _source_hashes()})
    monkeypatch.setattr(runner, "_ACTIVE_PRIVATE_RUN_ROOT", root)
    context: dict[str, object] = {
        "working_directory": work,
        "directory": response_ledger,
        "nonce": NONCE,
        "plugin_bytes": response_plugin,
        "protocol_bytes": protocol_source,
        "prompt_plugin_bytes": prompt_plugin,
        "prompt_expectation_bytes": prompt_expectation_bytes,
        "expectation_bytes": response_expectation_bytes,
    }
    return root, raw_dir, response_ledger, context


def test_response_expectations_reuse_prompt_nonce_and_exact_phase_bodies() -> None:
    document = runner._http_response_expectations(nonce=NONCE)

    assert document == {
        "schema_version": 1,
        "nonce": NONCE,
        "phase_messages": [message for _phase, message in runner.PHASES],
    }
    with pytest.raises(runner.GateError, match="http_response_expectation_context_invalid"):
        runner._http_response_expectations(nonce="invalid")


def test_runtime_source_map_requires_the_response_reader() -> None:
    module_name = "test.live_provider.opencode_http_response_argument_observer"
    path = "test/live_provider/opencode_http_response_argument_observer.py"

    assert runner.RUN_SOURCE_RELATIVE_PATHS[module_name] == path
    assert module_name in runner.RUN_SOURCE_MODULES
    assert runner._runtime_source_modules_match(runner.RUN_SOURCE_RELATIVE_PATHS)
    missing = dict(runner.RUN_SOURCE_RELATIVE_PATHS)
    missing.pop(module_name)
    assert not runner._runtime_source_modules_match(missing)
    extra = dict(runner.RUN_SOURCE_RELATIVE_PATHS)
    extra["test.live_provider.unbound_response_reader"] = path
    assert not runner._runtime_source_modules_match(extra)


def test_source_inventory_uses_complete_pinned_head_paths_not_a_fixed_count(monkeypatch) -> None:
    head = "a" * 40
    expected = ["src/first.py", "test/live_provider/new_observer.py"]
    calls: list[list[str]] = []

    def check_output(command, **kwargs):
        calls.append(command)
        if command[:3] == ["git", "ls-tree", "-r"]:
            return b"\0".join(path.encode("utf-8") for path in expected) + b"\0"
        raise AssertionError("unexpected_git_command")

    monkeypatch.setattr(runner.subprocess, "check_output", check_output)

    assert runner._source_files(expected_head=head) == expected
    assert calls == [
        ["git", "ls-tree", "-r", "-z", "--name-only", head, "--", *runner.SOURCE_PATHS]
    ]


@pytest.mark.parametrize(
    ("current_head", "status"),
    [("b" * 40, b""), ("a" * 40, b" M test/live_provider/observer.py\0")],
)
def test_source_tree_guard_rejects_stale_head_or_uncommitted_scope(
    monkeypatch, current_head: str, status: bytes
) -> None:
    head = "a" * 40

    def check_output(command, **kwargs):
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return current_head
        if command[:3] == ["git", "status", "--porcelain"]:
            return status
        raise AssertionError("unexpected_git_command")

    monkeypatch.setattr(runner.subprocess, "check_output", check_output)
    monkeypatch.setattr(runner, "EXPECTED_HEAD", head)

    assert runner._source_head_tree_clean(head) is False


def test_runner_places_sender_observer_before_input_and_collects_in_finally() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    prompt_install = source.index("prompt_observer.install_project_plugin(")
    response_install = source.index("response_observer.install_project_plugin(")
    expectation_write = source.index("response_observer.write_expectations(")
    first_input = source.index('f"{server.url}/terminals/{sender_id}/input"')
    capture = source.index("_capture_http_response_argument_diagnostic_safely(", first_input)
    private_cleanup = source.index("_remove_private_run_root(", capture)
    run_node = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_enabled"
    )
    acceptance_node = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "_acceptance_contract"
    )

    def calls_response_capture(node: ast.AST) -> bool:
        return any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_capture_http_response_argument_diagnostic_safely"
            for child in ast.walk(node)
        )

    assert prompt_install < response_install < expectation_write < first_input
    assert capture < private_cleanup
    assert 'working_directory=role_workdirs["sender"]' in source[response_install:expectation_write]
    assert "api_processes_confirmed_dead is True" in source[capture:private_cleanup]
    assert "tmux_processes_confirmed_dead is True" in source[capture:private_cleanup]
    assert any(
        isinstance(node, ast.Try)
        and node.finalbody
        and any(calls_response_capture(statement) for statement in node.finalbody)
        for node in ast.walk(run_node)
    )
    assert "sender_http_response_argument_diagnostic" not in ast.get_source_segment(
        source, acceptance_node
    )


def test_response_collector_copies_private_layout_and_projects_no_nonce_or_payload(
    tmp_path: Path, monkeypatch
) -> None:
    root, raw_dir, response_ledger, context = _seed_response_run(tmp_path, monkeypatch)

    result = runner._capture_http_response_argument_diagnostic_safely(
        context=context,
        root=root,
        raw_dir=raw_dir,
        writers_dead=True,
    )

    assert result["status"] == "sealed_observation_available"
    assert result["sealed_raw_copy_verified"] is True
    assert result["acceptance_input_changed"] is False
    observation = result["observation"]
    assert observation["observer_status"] == "observed"
    assert observation["trace_complete"] is True
    assert [phase["status"] for phase in observation["phase_results"]] == ["exact", "exact"]
    assert "records" not in observation

    copied_root = raw_dir / "sender-http-response-argument-observer-copy"
    copied_opencode = copied_root / ".opencode"
    assert (
        response_observer.read_observation(copied_opencode / response_ledger.name, NONCE)[
            "observer_status"
        ]
        == "observed"
    )
    assert (copied_opencode / "http-prompt-expectations.json").is_file()
    assert (copied_opencode / "http-response-argument-expectations.json").is_file()
    assert (copied_opencode / "plugins/cao-http-prompt-observer.js").is_file()
    assert (copied_opencode / "plugins" / response_observer.PLUGIN_NAME).is_file()
    assert (
        copied_opencode / response_observer.SUPPORT_DIRECTORY_NAME / response_observer.PROTOCOL_NAME
    ).is_file()

    public = json.dumps(result, ensure_ascii=False, sort_keys=True)
    for private in (
        NONCE,
        PRIVATE_CALL_ID,
        PRIVATE_RECEIVER,
        *[message for _phase, message in runner.PHASES],
        "http-response-argument-observer-copy",
    ):
        assert private not in public


def test_response_collector_fails_closed_before_copy_and_suppresses_baseexception(
    tmp_path: Path, monkeypatch
) -> None:
    root, raw_dir, _ledger, context = _seed_response_run(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner,
        "_read_source_bound_response_file",
        lambda *_args, **_kwargs: pytest.fail("source_read_before_writer_shutdown"),
    )

    active = runner._capture_http_response_argument_diagnostic_safely(
        context=context, root=root, raw_dir=raw_dir, writers_dead=False
    )
    assert active["status"] == "unavailable"
    assert active["reason"] == "writers_not_confirmed_dead"
    assert not (raw_dir / "sender-http-response-argument-observer-copy").exists()

    def interrupted(**_kwargs):
        raise KeyboardInterrupt("private exception canary")

    monkeypatch.setattr(runner, "_capture_http_response_argument_diagnostic", interrupted)
    failed = runner._capture_http_response_argument_diagnostic_safely(
        context=context, root=root, raw_dir=raw_dir, writers_dead=True
    )
    assert failed["status"] == "unavailable"
    assert failed["reason"] == "collector_error"
    assert "private exception canary" not in json.dumps(failed)


def test_response_collector_rejects_changed_installed_support(tmp_path: Path, monkeypatch) -> None:
    root, raw_dir, _ledger, context = _seed_response_run(tmp_path, monkeypatch)
    support = (
        context["working_directory"]
        / ".opencode"
        / response_observer.SUPPORT_DIRECTORY_NAME
        / response_observer.PROTOCOL_NAME
    )
    support.write_bytes(b"changed protocol source")
    support.chmod(0o600)

    result = runner._capture_http_response_argument_diagnostic_safely(
        context=context, root=root, raw_dir=raw_dir, writers_dead=True
    )

    assert result["status"] == "unavailable"
    assert result["reason"] == "installed_source_mismatch"
    assert result["sealed_raw_copy_verified"] is False
