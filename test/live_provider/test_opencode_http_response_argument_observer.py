from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from test.live_provider import opencode_http_response_argument_observer as observer

import pytest

NONCE = "0123456789abcdef0123456789abcdef"
PHASE_MESSAGES = ("Phase one café.", "Phase two 東京.")
PROMPT_DOCUMENT = {
    "schema_version": 1,
    "nonce": NONCE,
    "phase_prompts": ["phase one prompt LAST", "phase two prompt LAST"],
    "receipt_prefix": "\n\nreceipt: ",
    "receipt_suffix": "\nLAST",
    "last_instruction": "LAST",
}
PLUGIN_SOURCE = (
    b'import { inspectRequest } from "./opencode_http_prompt_observer.js";\n'
    b'import { parseResponseArguments } from "./opencode_http_response_protocol.js";\n'
    b"export function createHttpResponseArgumentObserver(options = {}) { return options; }\n"
    b"export default createHttpResponseArgumentObserver()\n"
)
PROTOCOL_SOURCE = b"export function parseResponseArguments() { return {}; }\n"


def _private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _private_file(path: Path, raw: bytes) -> Path:
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


def _workdir(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    _private_directory(path)
    return path


def _prompt_expectations(nonce: str = NONCE) -> dict[str, object]:
    return {**PROMPT_DOCUMENT, "nonce": nonce}


def _response_expectations(nonce: str = NONCE) -> dict[str, object]:
    return {
        "schema_version": 1,
        "nonce": nonce,
        "phase_messages": list(PHASE_MESSAGES),
    }


def _seed_opencode(workdir: Path, *, nonce: str = NONCE) -> Path:
    opencode = _private_directory(workdir / ".opencode")
    plugins = _private_directory(opencode / "plugins")
    _private_file(plugins / "cao-http-prompt-observer.js", b"pinned prompt observer\n")
    _private_file(
        opencode / "http-prompt-expectations.json",
        json.dumps(_prompt_expectations(nonce), ensure_ascii=False, separators=(",", ":")).encode(),
    )
    return opencode


def _write_json(path: Path, value: object) -> None:
    _private_file(path, json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def _record(
    *,
    ordinal: int = 1,
    phase: int | None = 1,
    status: str = "observed",
    reason: str = "none",
    request_bound: bool = True,
    target_count: int | None = 1,
    argument_exact: bool = True,
    punctuation_variant: bool = False,
    expected_bytes: int | None = len(PHASE_MESSAGES[0].encode()),
    observed_bytes: int | None = len(PHASE_MESSAGES[0].encode()),
    clone_complete: bool = True,
    original_not_replaced: bool = True,
    nonce: str = NONCE,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "nonce": nonce,
        "ordinal": ordinal,
        "phase_ordinal": phase,
        "request_bound": request_bound,
        "status": status,
        "reason": reason,
        "target_tool_call_count": target_count,
        "argument_exact": argument_exact,
        "known_punctuation_variant_exact": punctuation_variant,
        "expected_utf8_bytes": expected_bytes,
        "observed_utf8_bytes": observed_bytes,
        "clone_complete": clone_complete,
        "original_not_replaced": original_not_replaced,
    }


def _seal(
    *,
    request_count: int = 1,
    response_count: int = 1,
    durable_record_count: int = 1,
    hook_disposed: bool = True,
    trace_complete: bool = True,
    overflow: bool = False,
    nonce: str = NONCE,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "nonce": nonce,
        "hook_disposed": hook_disposed,
        "trace_complete": trace_complete,
        "request_count": request_count,
        "response_count": response_count,
        "durable_record_count": durable_record_count,
        "overflow": overflow,
    }


def _ledger(
    workdir: Path,
    records: list[dict[str, object]],
    seal: dict[str, object] | None = None,
    *,
    expectations: dict[str, object] | None = None,
) -> Path:
    opencode = workdir / ".opencode"
    if not opencode.exists():
        _seed_opencode(workdir)
    if expectations is None:
        expectations = _response_expectations()
    observer.write_expectations(workdir, expectations)
    path = _private_directory(opencode / "http-response-argument-observation")
    for ordinal, record in enumerate(records, start=1):
        _write_json(path / f"response-{ordinal:04d}.json", record)
    _write_json(
        path / "seal.json",
        (
            seal
            if seal is not None
            else _seal(
                request_count=len(records),
                response_count=len(records),
                durable_record_count=len(records),
            )
        ),
    )
    return path


def test_specialize_plugin_bytes_rewrites_only_the_three_fixed_markers() -> None:
    specialized = observer.specialize_plugin_bytes(PLUGIN_SOURCE, NONCE)

    assert b'from "./cao-http-prompt-observer.js"' in specialized
    assert b'from "../cao-http-response-support/opencode_http_response_protocol.js"' in specialized
    assert f'createHttpResponseArgumentObserver({{ nonce: "{NONCE}" }})'.encode() in specialized
    assert b"./opencode_http_prompt_observer.js" not in specialized
    assert b"./opencode_http_response_protocol.js" not in specialized
    assert specialized.count(NONCE.encode()) == 1


@pytest.mark.parametrize(
    "source,nonce",
    [
        (PLUGIN_SOURCE.replace(b'from "./opencode_http_prompt_observer.js"', b""), NONCE),
        (PLUGIN_SOURCE + PLUGIN_SOURCE, NONCE),
        (PLUGIN_SOURCE + b"export default createHttpResponseArgumentObserver()", NONCE),
        (PLUGIN_SOURCE + b'from "./cao-http-prompt-observer.js"', NONCE),
        (PLUGIN_SOURCE, "A" * 32),
        ("not bytes", NONCE),
    ],
)
def test_specialize_rejects_bad_nonce_or_non_unique_source_markers(source, nonce) -> None:
    with pytest.raises(RuntimeError):
        observer.specialize_plugin_bytes(source, nonce)


def test_install_projects_private_plugin_support_and_fresh_ledger(tmp_path: Path) -> None:
    workdir = _workdir(tmp_path)
    opencode = _seed_opencode(workdir)
    t125 = opencode / "plugins" / "cao-http-prompt-observer.js"
    original_t125 = t125.read_bytes()
    source_path = _private_file(tmp_path / "observer.js", PLUGIN_SOURCE)
    protocol_path = _private_file(tmp_path / "protocol.js", PROTOCOL_SOURCE)

    ledger = observer.install_project_plugin(workdir, source_path, protocol_path, NONCE)

    assert ledger == opencode / "http-response-argument-observation"
    assert not ledger.exists()
    plugin = opencode / "plugins" / "cao-http-response-argument-observer.js"
    assert plugin.read_bytes() == observer.specialize_plugin_bytes(PLUGIN_SOURCE, NONCE)
    support = opencode / "cao-http-response-support" / "opencode_http_response_protocol.js"
    assert support.read_bytes() == PROTOCOL_SOURCE
    assert stat.S_IMODE(plugin.stat().st_mode) == 0o600
    assert stat.S_IMODE(support.stat().st_mode) == 0o600
    assert t125.read_bytes() == original_t125
    for path in (opencode, opencode / "plugins", support.parent):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert all(path.stat().st_nlink == 1 for path in (plugin, support, t125))


def test_install_requires_fresh_ledger_path_owned_by_runtime(tmp_path: Path) -> None:
    workdir = _workdir(tmp_path)
    opencode = _seed_opencode(workdir)
    source_path = _private_file(tmp_path / "observer.js", PLUGIN_SOURCE)
    protocol_path = _private_file(tmp_path / "protocol.js", PROTOCOL_SOURCE)
    ledger = _private_directory(opencode / "http-response-argument-observation")
    sentinel = _private_file(ledger / "runtime-owned", b"preserve")

    with pytest.raises(RuntimeError):
        observer.install_project_plugin(workdir, source_path, protocol_path, NONCE)

    assert sentinel.read_bytes() == b"preserve"
    assert not (opencode / "cao-http-response-support").exists()
    assert not (opencode / "plugins" / "cao-http-response-argument-observer.js").exists()


def test_install_requires_existing_private_single_link_t125_plugin(tmp_path: Path) -> None:
    workdir = _workdir(tmp_path)
    opencode = _private_directory(workdir / ".opencode")
    plugins = _private_directory(opencode / "plugins")
    source_path = _private_file(tmp_path / "observer.js", PLUGIN_SOURCE)
    protocol_path = _private_file(tmp_path / "protocol.js", PROTOCOL_SOURCE)

    with pytest.raises(RuntimeError):
        observer.install_project_plugin(workdir, source_path, protocol_path, NONCE)

    target = _private_file(plugins / "cao-http-prompt-observer.js", b"existing")
    linked = tmp_path / "linked-prompt.js"
    os.link(target, linked)
    with pytest.raises(RuntimeError):
        observer.install_project_plugin(workdir, source_path, protocol_path, NONCE)
    assert not (plugins / "cao-http-response-argument-observer.js").exists()


def test_install_never_overwrites_reserved_destination(tmp_path: Path) -> None:
    workdir = _workdir(tmp_path)
    opencode = _seed_opencode(workdir)
    source_path = _private_file(tmp_path / "observer.js", PLUGIN_SOURCE)
    protocol_path = _private_file(tmp_path / "protocol.js", PROTOCOL_SOURCE)
    destination = opencode / "plugins" / "cao-http-response-argument-observer.js"
    _private_file(destination, b"reserved-before-install")

    with pytest.raises(RuntimeError):
        observer.install_project_plugin(workdir, source_path, protocol_path, NONCE)

    assert destination.read_bytes() == b"reserved-before-install"
    assert not (opencode / "cao-http-response-support").exists()
    assert not (opencode / "http-response-argument-observation").exists()


def test_install_handles_a_destination_reservation_race_without_clobbering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = _workdir(tmp_path)
    opencode = _seed_opencode(workdir)
    source_path = _private_file(tmp_path / "observer.js", PLUGIN_SOURCE)
    protocol_path = _private_file(tmp_path / "protocol.js", PROTOCOL_SOURCE)
    destination = opencode / "plugins" / "cao-http-response-argument-observer.js"
    real_open = observer.os.open
    reserved = False

    def reserve_then_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal reserved
        if Path(path) == destination and flags & os.O_EXCL and not reserved:
            reserved = True
            _private_file(destination, b"race-reservation")
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(observer.os, "open", reserve_then_open)

    with pytest.raises(RuntimeError):
        observer.install_project_plugin(workdir, source_path, protocol_path, NONCE)

    assert reserved
    assert destination.read_bytes() == b"race-reservation"
    assert not (opencode / "cao-http-response-support").exists()
    assert not (opencode / "http-response-argument-observation").exists()


def test_write_expectations_enforces_t125_nonce_and_private_single_write(tmp_path: Path) -> None:
    workdir = _workdir(tmp_path)
    opencode = _seed_opencode(workdir)

    path = observer.write_expectations(workdir, _response_expectations())

    assert path == opencode / "http-response-argument-expectations.json"
    raw = path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.stat().st_nlink == 1
    assert json.loads(raw) == _response_expectations()
    with pytest.raises(RuntimeError):
        observer.write_expectations(workdir, _response_expectations())
    assert path.read_bytes() == raw


@pytest.mark.parametrize(
    "document",
    [
        {**_response_expectations(), "unexpected": True},
        {**_response_expectations(), "schema_version": True},
        {**_response_expectations(), "phase_messages": ["only one"]},
        {**_response_expectations(), "phase_messages": ["", "second"]},
        {**_response_expectations(), "phase_messages": ["\ud800", "second"]},
        {**_response_expectations(), "phase_messages": ["x" * 8193, "second"]},
        {**_response_expectations(), "nonce": "0" * 31},
    ],
)
def test_write_expectations_rejects_invalid_document_before_creating_file(
    tmp_path: Path, document: dict[str, object]
) -> None:
    workdir = _workdir(tmp_path)
    opencode = _seed_opencode(workdir)

    with pytest.raises(RuntimeError):
        observer.write_expectations(workdir, document)

    assert not (opencode / "http-response-argument-expectations.json").exists()


def test_write_expectations_rejects_wrong_t125_nonce(tmp_path: Path) -> None:
    workdir = _workdir(tmp_path)
    opencode = _seed_opencode(workdir, nonce="f" * 32)

    with pytest.raises(RuntimeError):
        observer.write_expectations(workdir, _response_expectations())

    assert not (opencode / "http-response-argument-expectations.json").exists()


def test_write_expectations_rejects_duplicated_t125_document_keys(tmp_path: Path) -> None:
    workdir = _workdir(tmp_path)
    opencode = _seed_opencode(workdir)
    prompt = opencode / "http-prompt-expectations.json"
    prompt.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    prompt.chmod(0o600)

    with pytest.raises(RuntimeError):
        observer.write_expectations(workdir, _response_expectations())

    assert not (opencode / "http-response-argument-expectations.json").exists()


def test_write_expectations_accepts_exact_utf8_phase_limit(tmp_path: Path) -> None:
    workdir = _workdir(tmp_path)
    opencode = _seed_opencode(workdir)
    messages = ["x" * 8192, "東" * 2730 + "x" * 2]
    assert len(messages[0].encode("utf-8")) == 8192
    assert len(messages[1].encode("utf-8")) == 8192

    path = observer.write_expectations(
        workdir,
        {"schema_version": 1, "nonce": NONCE, "phase_messages": messages},
    )

    assert path.parent == opencode
    assert path.stat().st_size <= observer.MAX_EXPECTATION_BYTES


def test_read_observation_reports_exact_and_known_punctuation_without_payload(
    tmp_path: Path,
) -> None:
    expected_second_bytes = len(PHASE_MESSAGES[1].encode("utf-8"))
    ledger = _ledger(
        _workdir(tmp_path),
        [
            _record(ordinal=1, phase=1),
            _record(
                ordinal=2,
                phase=2,
                argument_exact=False,
                punctuation_variant=True,
                expected_bytes=expected_second_bytes,
                observed_bytes=expected_second_bytes + 2,
            ),
        ],
        _seal(request_count=2, response_count=2, durable_record_count=2),
    )

    result = observer.read_observation(ledger, NONCE)

    assert result["observer_status"] == "observed"
    assert result["trace_complete"] is True
    assert result["request_count"] == 2
    assert result["response_count"] == 2
    assert result["durable_record_count"] == 2
    assert result["phase_results"][0]["status"] == "exact"
    assert result["phase_results"][1]["status"] == "known_punctuation_variant"
    assert result["phase_results"][1]["known_punctuation_variant_exact"] is True
    serialized = json.dumps(result, ensure_ascii=False)
    assert NONCE not in serialized
    assert PHASE_MESSAGES[0] not in serialized
    assert PHASE_MESSAGES[1] not in serialized
    assert "nonce" not in serialized


def test_read_observation_separates_trace_completion_from_missing_phase_two(
    tmp_path: Path,
) -> None:
    ledger = _ledger(
        _workdir(tmp_path),
        [_record()],
        _seal(request_count=1, response_count=1, durable_record_count=1),
    )

    result = observer.read_observation(ledger, NONCE)

    assert result["observer_status"] == "observed"
    assert result["trace_complete"] is True
    assert result["phase_results"][0]["status"] == "exact"
    assert result["phase_results"][1]["status"] == "not_observed"
    assert result["phase_results"][1]["target_tool_call_count"] is None
    assert result["phase_results"][1]["observed_utf8_bytes"] is None


def test_read_observation_does_not_claim_phase_result_from_incomplete_trace(
    tmp_path: Path,
) -> None:
    ledger = _ledger(
        _workdir(tmp_path),
        [_record()],
        _seal(
            request_count=2,
            response_count=1,
            durable_record_count=1,
            hook_disposed=False,
            trace_complete=False,
        ),
    )

    result = observer.read_observation(ledger, NONCE)

    assert result["observer_status"] == "incomplete"
    assert result["trace_complete"] is False
    assert result["request_count"] == 2
    assert result["response_count"] == 1
    assert result["phase_results"][0]["status"] == "incomplete"
    assert result["phase_results"][0]["argument_exact"] is None


def test_read_observation_treats_no_target_tool_as_no_body_observation(
    tmp_path: Path,
) -> None:
    record = _record(
        status="no_target_tool",
        target_count=0,
        argument_exact=False,
        expected_bytes=len(PHASE_MESSAGES[0].encode()),
        observed_bytes=None,
    )
    ledger = _ledger(_workdir(tmp_path), [record])

    result = observer.read_observation(ledger, NONCE)

    phase = result["phase_results"][0]
    assert result["observer_status"] == "observed"
    assert phase["status"] == "target_call_missing"
    assert phase["target_tool_call_count"] == 0
    assert phase["argument_exact"] is None
    assert phase["observed_utf8_bytes"] is None


def test_read_observation_marks_multiple_target_calls_ambiguous(tmp_path: Path) -> None:
    records = [_record(ordinal=1), _record(ordinal=2)]
    ledger = _ledger(
        _workdir(tmp_path),
        records,
        _seal(request_count=2, response_count=2, durable_record_count=2),
    )

    result = observer.read_observation(ledger, NONCE)

    phase = result["phase_results"][0]
    assert result["observer_status"] == "observed"
    assert phase["status"] == "ambiguous_target_calls"
    assert phase["target_tool_call_count"] == 2
    assert phase["argument_exact"] is None


def test_read_observation_keeps_body_mismatch_distinct_from_unavailable(
    tmp_path: Path,
) -> None:
    record = _record(argument_exact=False, observed_bytes=len(PHASE_MESSAGES[0].encode()) + 1)
    ledger = _ledger(_workdir(tmp_path), [record])

    result = observer.read_observation(ledger, NONCE)

    phase = result["phase_results"][0]
    assert result["observer_status"] == "observed"
    assert phase["status"] == "body_mismatch"
    assert phase["argument_exact"] is False
    assert phase["known_punctuation_variant_exact"] is False


def test_read_observation_accepts_complete_mechanical_trace_with_unavailable_body(
    tmp_path: Path,
) -> None:
    record = _record(
        status="unavailable",
        reason="unsupported_content_type",
        target_count=None,
        argument_exact=False,
        expected_bytes=len(PHASE_MESSAGES[0].encode()),
        observed_bytes=None,
    )
    ledger = _ledger(_workdir(tmp_path), [record])

    result = observer.read_observation(ledger, NONCE)

    assert result["observer_status"] == "observed"
    assert result["trace_complete"] is True
    assert result["phase_results"][0]["status"] == "unavailable"
    assert result["phase_results"][0]["target_tool_call_count"] is None
    assert result["phase_results"][0]["argument_exact"] is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(extra=True),
        lambda r: r.update(schema_version=True),
        lambda r: r.update(ordinal=True),
        lambda r: r.update(target_tool_call_count=True),
        lambda r: r.update(target_tool_call_count=None),
        lambda r: r.update(request_bound=1),
        lambda r: r.update(reason="unknown_reason"),
        lambda r: r.update(reason="none", status="unavailable"),
        lambda r: r.update(status="unknown_status"),
        lambda r: r.update(phase_ordinal=3),
        lambda r: r.update(phase_ordinal=None),
        lambda r: r.update(nonce="f" * 32),
        lambda r: r.update(expected_utf8_bytes=None),
        lambda r: r.update(known_punctuation_variant_exact=True),
        lambda r: r.update(original_not_replaced=False),
        lambda r: r.update(clone_complete=False),
    ],
)
def test_read_observation_rejects_invalid_success_record_relationships(
    tmp_path: Path, mutate
) -> None:
    record = _record()
    mutate(record)
    ledger = _ledger(_workdir(tmp_path), [record])

    result = observer.read_observation(ledger, NONCE)

    assert result["observer_status"] == "unavailable"
    assert result["request_count"] is None
    assert result["response_count"] is None


def test_read_observation_rejects_punctuation_variant_without_expected_ascii_period(
    tmp_path: Path,
) -> None:
    messages = ["No trailing period", PHASE_MESSAGES[1]]
    workdir = _workdir(tmp_path)
    _seed_opencode(workdir)
    expected = _response_expectations()
    expected["phase_messages"] = messages
    record = _record(
        punctuation_variant=True,
        argument_exact=False,
        expected_bytes=len(messages[0].encode()),
        observed_bytes=len(messages[0].encode()) + 2,
    )
    ledger = _ledger(workdir, [record], expectations=expected)

    result = observer.read_observation(ledger, NONCE)

    assert result["observer_status"] == "unavailable"


def test_read_observation_rejects_unbound_record_as_complete(tmp_path: Path) -> None:
    record = _record(
        phase=None,
        status="unavailable",
        reason="unbound_request",
        request_bound=False,
        target_count=None,
        argument_exact=False,
        expected_bytes=None,
        observed_bytes=None,
    )
    ledger = _ledger(_workdir(tmp_path), [record])

    result = observer.read_observation(ledger, NONCE)

    assert result["observer_status"] == "unavailable"
    assert result["phase_results"][0]["argument_exact"] is None


def test_read_observation_keeps_unbound_expectation_failure_incomplete(
    tmp_path: Path,
) -> None:
    record = _record(
        phase=None,
        status="unavailable",
        reason="expectations_unavailable",
        request_bound=False,
        target_count=None,
        argument_exact=False,
        expected_bytes=None,
        observed_bytes=None,
    )
    ledger = _ledger(
        _workdir(tmp_path),
        [record],
        _seal(
            request_count=1,
            response_count=1,
            durable_record_count=1,
            trace_complete=False,
        ),
    )

    result = observer.read_observation(ledger, NONCE)

    assert result["observer_status"] == "incomplete"
    assert result["trace_complete"] is False
    assert result["phase_results"][0]["status"] == "incomplete"
    assert result["phase_results"][0]["expected_utf8_bytes"] == len(PHASE_MESSAGES[0].encode())


@pytest.mark.parametrize(
    "seal",
    [
        _seal(request_count=2),
        _seal(response_count=0),
        _seal(durable_record_count=0),
        _seal(hook_disposed=False),
        _seal(overflow=True),
        {**_seal(), "extra": 1},
        {**_seal(), "request_count": True},
        {**_seal(), "response_count": 17},
    ],
)
def test_read_observation_rejects_invalid_seal_counts_and_flags(
    tmp_path: Path, seal: dict[str, object]
) -> None:
    ledger = _ledger(_workdir(tmp_path), [_record()], seal)

    result = observer.read_observation(ledger, NONCE)

    assert result["observer_status"] == "unavailable"
    assert result["trace_complete"] is False
    assert result["request_count"] is None


def test_read_observation_does_not_invent_counts_when_ledger_is_missing(tmp_path: Path) -> None:
    result = observer.read_observation(tmp_path / "missing", NONCE)

    assert result["observer_status"] == "unavailable"
    assert result["request_count"] is None
    assert result["response_count"] is None
    assert result["durable_record_count"] is None
    assert result["overflow"] is None
    assert result["records"] is None


def test_read_observation_rejects_duplicate_keys_and_unknown_ledger_entries(
    tmp_path: Path,
) -> None:
    ledger = _ledger(_workdir(tmp_path), [_record()])
    record = ledger / "response-0001.json"
    record.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    record.chmod(0o600)
    assert observer.read_observation(ledger, NONCE)["observer_status"] == "unavailable"

    record.write_text(json.dumps(_record(), separators=(",", ":")), encoding="utf-8")
    record.chmod(0o600)
    _private_file(ledger / "unexpected.json", b"{}")
    assert observer.read_observation(ledger, NONCE)["observer_status"] == "unavailable"


@pytest.mark.parametrize("mode", [0o644, 0o400])
def test_read_observation_rejects_non_private_record_modes(tmp_path: Path, mode: int) -> None:
    ledger = _ledger(_workdir(tmp_path), [_record()])
    (ledger / "response-0001.json").chmod(mode)

    result = observer.read_observation(ledger, NONCE)

    assert result["observer_status"] == "unavailable"


def test_read_observation_rejects_hardlinks_and_symlinks(tmp_path: Path) -> None:
    ledger = _ledger(_workdir(tmp_path), [_record()])
    record = ledger / "response-0001.json"
    external = tmp_path / "external.json"
    os.link(record, external)
    assert observer.read_observation(ledger, NONCE)["observer_status"] == "unavailable"

    record.unlink()
    record.symlink_to(external)
    assert observer.read_observation(ledger, NONCE)["observer_status"] == "unavailable"


def test_read_observation_rejects_overfull_directory_without_unbounded_listing(
    tmp_path: Path,
) -> None:
    ledger = _ledger(_workdir(tmp_path), [_record()])
    for ordinal in range(2, 19):
        _write_json(ledger / f"response-{ordinal:04d}.json", _record(ordinal=ordinal))

    result = observer.read_observation(ledger, NONCE)

    assert result["observer_status"] == "unavailable"


def test_read_observation_detects_ledger_mutation_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _ledger(_workdir(tmp_path), [_record()])
    real_read = observer._read_private_file_with_identity
    changed = False

    def read_then_mutate(path: Path, *, maximum: int):
        nonlocal changed
        result = real_read(path, maximum=maximum)
        if path == ledger / "response-0001.json" and not changed:
            changed = True
            path.write_bytes(path.read_bytes() + b" ")
            path.chmod(0o600)
        return result

    monkeypatch.setattr(observer, "_read_private_file_with_identity", read_then_mutate)

    result = observer.read_observation(ledger, NONCE)

    assert changed
    assert result["observer_status"] == "unavailable"
    assert result["request_count"] is None
