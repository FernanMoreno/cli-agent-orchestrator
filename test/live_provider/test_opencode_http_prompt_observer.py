from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from test.live_provider import opencode_http_prompt_observer as observer

import pytest

NONCE = "0123456789abcdef0123456789abcdef"
PROMPTS = ("phase one source body LAST_SENTINEL", "phase two source body LAST_SENTINEL")


def _expectations(nonce: str = NONCE) -> dict[str, object]:
    return {
        "schema_version": 1,
        "nonce": nonce,
        "phase_prompts": list(PROMPTS),
        "receipt_prefix": "\n\nreceipt: ",
        "receipt_suffix": "\nLAST_SENTINEL",
        "last_instruction": "LAST_SENTINEL",
    }


def _record(
    *,
    nonce: str = NONCE,
    ordinal: int = 1,
    phase_ordinal: int | None = 1,
    source_prompt_exact: bool = True,
    last_instruction_present: bool = True,
    receipt_envelope_shape_valid: bool = True,
    status: str = "observed",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "nonce": nonce,
        "ordinal": ordinal,
        "status": status,
        "phase_ordinal": phase_ordinal,
        "source_prompt_exact": source_prompt_exact,
        "last_instruction_present": last_instruction_present,
        "receipt_envelope_shape_valid": receipt_envelope_shape_valid,
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    path.chmod(0o600)


def _write_ledger(
    path: Path,
    *,
    records: list[dict[str, object]] | None = None,
    nonce: str = NONCE,
    hook_disposed: bool = True,
    trace_complete: bool = True,
    overflow: bool = False,
    seal: bool = True,
) -> None:
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    rows = [] if records is None else records
    for index, record in enumerate(rows, start=1):
        _write_json(path / f"request-{index:04d}.json", record)
    if seal:
        _write_json(
            path / "seal.json",
            {
                "schema_version": 1,
                "nonce": nonce,
                "hook_disposed": hook_disposed,
                "trace_complete": trace_complete,
                "request_count": len(rows),
                "durable_record_count": len(rows),
                "overflow": overflow,
            },
        )


def test_install_and_write_expectations_create_only_private_source_copy(tmp_path: Path) -> None:
    working = tmp_path / "work"
    working.mkdir(mode=0o700)
    working.chmod(0o700)
    source = tmp_path / "source.js"
    source.write_text("export default 'SOURCE_SENTINEL'\n", encoding="utf-8")
    source.chmod(0o600)

    ledger = observer.install_project_plugin(working, source, NONCE)
    plugin = working / ".opencode" / "plugins" / "cao-http-prompt-observer.js"
    assert ledger == working / ".opencode" / "http-prompt-observation"
    assert plugin.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(plugin.stat().st_mode) == 0o600
    assert plugin.stat().st_nlink == 1
    assert stat.S_IMODE(plugin.parent.stat().st_mode) == 0o700
    assert not ledger.exists()

    expectation_path = observer.write_expectations(working, _expectations())
    assert expectation_path == working / ".opencode" / "http-prompt-expectations.json"
    assert stat.S_IMODE(expectation_path.stat().st_mode) == 0o600
    assert expectation_path.stat().st_nlink == 1
    assert json.loads(expectation_path.read_text(encoding="utf-8")) == _expectations()
    with pytest.raises(RuntimeError):
        observer.write_expectations(working, _expectations())


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"phase_prompts": [PROMPTS[0]]},
        {"phase_prompts": [PROMPTS[0], PROMPTS[0]]},
        {"phase_prompts": [PROMPTS[0], 7]},
        {"receipt_prefix": ""},
        {"receipt_suffix": 9},
        {"last_instruction": ""},
        {"surprise": "must fail closed"},
    ],
)
def test_expectation_writer_rejects_invalid_shape_and_never_creates_file(
    tmp_path: Path, change: dict[str, object]
) -> None:
    working = tmp_path / "work"
    working.mkdir(mode=0o700)
    working.chmod(0o700)
    (working / ".opencode").mkdir(mode=0o700)
    value = _expectations()
    value.update(change)

    with pytest.raises(RuntimeError):
        observer.write_expectations(working, value)

    assert not (working / ".opencode" / "http-prompt-expectations.json").exists()


def test_expectation_writer_rejects_symlink_and_hardlinked_destination(tmp_path: Path) -> None:
    working = tmp_path / "work"
    working.mkdir(mode=0o700)
    working.chmod(0o700)
    opencode = working / ".opencode"
    opencode.mkdir(mode=0o700)
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("sentinel", encoding="utf-8")
    os.link(elsewhere, opencode / "http-prompt-expectations.json")
    with pytest.raises(RuntimeError):
        observer.write_expectations(working, _expectations())

    (opencode / "http-prompt-expectations.json").unlink()
    (opencode / "http-prompt-expectations.json").symlink_to(elsewhere)
    with pytest.raises(RuntimeError):
        observer.write_expectations(working, _expectations())


def test_reader_returns_only_bounded_redacted_fields_and_preserves_one_phase_prefix(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "ledger"
    _write_ledger(ledger, records=[_record()])

    result = observer.read_observation(ledger, NONCE)

    assert result == {
        "observer_status": "observed",
        "trace_complete": True,
        "overflow": False,
        "request_count": 1,
        "durable_record_count": 1,
        "phase_observation": "prefix",
        "records": [
            {
                "ordinal": 1,
                "status": "observed",
                "phase_ordinal": 1,
                "source_prompt_exact": True,
                "last_instruction_present": True,
                "receipt_envelope_shape_valid": True,
            }
        ],
    }
    serialized = json.dumps(result)
    assert NONCE not in serialized
    assert "CAO-TURN-RECEIPT" not in serialized
    assert "LAST_SENTINEL" not in serialized
    assert "identity" not in serialized


def test_two_phase_observation_is_diagnostic_and_not_an_acceptance_field(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    _write_ledger(
        ledger,
        records=[_record(ordinal=1, phase_ordinal=1), _record(ordinal=2, phase_ordinal=2)],
    )

    result = observer.read_observation(ledger, NONCE)

    assert result["observer_status"] == "observed"
    assert result["phase_observation"] == "both"
    assert "accepted" not in result
    assert "active_receipt_identity_attested" not in result


def test_phase_summary_aggregates_repeated_requests_without_dropping_rows(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    _write_ledger(
        ledger,
        records=[
            _record(ordinal=1, phase_ordinal=1),
            _record(ordinal=2, phase_ordinal=1),
            _record(ordinal=3, phase_ordinal=2),
            _record(ordinal=4, phase_ordinal=2),
        ],
    )

    result = observer.read_observation(ledger, NONCE)

    assert result["observer_status"] == "observed"
    assert result["phase_observation"] == "both"
    assert [record["phase_ordinal"] for record in result["records"]] == [1, 1, 2, 2]


@pytest.mark.parametrize(
    "record",
    [
        _record(ordinal=True),
        {**_record(), "schema_version": True},
        _record(phase_ordinal=True),
        _record(source_prompt_exact=1),
        _record(last_instruction_present="true"),
        _record(receipt_envelope_shape_valid=1),
        _record(last_instruction_present=False),
        _record(status="unrecognized"),
        {**_record(), "nonce": "foreignnonceforeignnonceforeignn"},
        {**_record(), "private_prompt": "PRIVATE_PROMPT_SENTINEL"},
    ],
)
def test_reader_rejects_malformed_record_without_echoing_any_values(
    tmp_path: Path, record: dict[str, object]
) -> None:
    ledger = tmp_path / "ledger"
    _write_ledger(ledger, records=[record])

    result = observer.read_observation(ledger, NONCE)

    assert result["observer_status"] == "unavailable"
    assert result["records"] == []
    assert "PRIVATE_PROMPT_SENTINEL" not in json.dumps(result)


def test_reader_rejects_duplicate_json_keys_and_boolean_seal_counts(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate"
    duplicate.mkdir(mode=0o700)
    duplicate.chmod(0o700)
    (duplicate / "request-0001.json").write_text(
        '{"schema_version":1,"schema_version":1}', encoding="utf-8"
    )
    (duplicate / "request-0001.json").chmod(0o600)
    _write_json(
        duplicate / "seal.json",
        {
            "schema_version": 1,
            "nonce": NONCE,
            "hook_disposed": True,
            "trace_complete": True,
            "request_count": 1,
            "durable_record_count": 1,
            "overflow": False,
        },
    )
    assert observer.read_observation(duplicate, NONCE)["observer_status"] == "unavailable"

    boolean_count = tmp_path / "boolean-count"
    _write_ledger(boolean_count, records=[_record()])
    seal_path = boolean_count / "seal.json"
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    seal["request_count"] = True
    _write_json(seal_path, seal)
    assert observer.read_observation(boolean_count, NONCE)["observer_status"] == "unavailable"

    boolean_version = tmp_path / "boolean-version"
    _write_ledger(boolean_version, records=[_record()])
    version_path = boolean_version / "seal.json"
    version = json.loads(version_path.read_text(encoding="utf-8"))
    version["schema_version"] = True
    _write_json(version_path, version)
    assert observer.read_observation(boolean_version, NONCE)["observer_status"] == "unavailable"


@pytest.mark.parametrize("mode", ["missing", "incomplete", "foreign_nonce", "overflow", "gap"])
def test_reader_never_promotes_missing_partial_or_foreign_seals(tmp_path: Path, mode: str) -> None:
    ledger = tmp_path / mode
    if mode == "missing":
        _write_ledger(ledger, records=[_record()], seal=False)
    elif mode == "incomplete":
        _write_ledger(ledger, records=[_record()], trace_complete=False)
    elif mode == "foreign_nonce":
        _write_ledger(ledger, records=[_record()], nonce="f" * 32)
    elif mode == "overflow":
        _write_ledger(ledger, records=[_record()], overflow=True)
    else:
        ledger.mkdir(mode=0o700)
        ledger.chmod(0o700)
        _write_json(ledger / "request-0002.json", _record(ordinal=2))
        _write_json(
            ledger / "seal.json",
            {
                "schema_version": 1,
                "nonce": NONCE,
                "hook_disposed": True,
                "trace_complete": True,
                "request_count": 1,
                "durable_record_count": 1,
                "overflow": False,
            },
        )

    result = observer.read_observation(ledger, NONCE)
    assert result["observer_status"] in {"unavailable", "incomplete"}
    assert result["phase_observation"] in {"unavailable", "none"}
    assert result["records"] == []


def test_reader_rejects_hardlinks_symlink_directories_and_public_permissions(
    tmp_path: Path,
) -> None:
    hardlink = tmp_path / "hardlink"
    _write_ledger(hardlink, records=[_record()])
    os.link(hardlink / "request-0001.json", tmp_path / "outside.json")
    assert observer.read_observation(hardlink, NONCE)["observer_status"] == "unavailable"

    target = tmp_path / "target"
    _write_ledger(target, records=[_record()])
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    assert observer.read_observation(alias, NONCE)["observer_status"] == "unavailable"

    public = tmp_path / "public"
    _write_ledger(public, records=[_record()])
    public.chmod(0o755)
    assert observer.read_observation(public, NONCE)["observer_status"] == "unavailable"


def test_reader_rejects_unknown_files_and_invalid_nonce(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger"
    _write_ledger(ledger, records=[_record()])
    _write_json(ledger / "unexpected.json", {"data": "PRIVATE_SENTINEL"})
    assert observer.read_observation(ledger, NONCE)["observer_status"] == "unavailable"
    assert observer.read_observation(ledger, "bad")["observer_status"] == "unavailable"


def test_reader_stops_directory_enumeration_at_the_record_cap(tmp_path: Path, monkeypatch) -> None:
    ledger = tmp_path / "bounded-ledger"
    ledger.mkdir(mode=0o700)
    steps = []

    class EndlessEntries:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            from types import SimpleNamespace

            steps.append(None)
            if len(steps) > observer.MAX_REQUESTS + 2:
                raise AssertionError("unbounded directory traversal")
            return SimpleNamespace(name=f"request-{len(steps):04d}.json")

    with monkeypatch.context() as scope:
        scope.setattr(observer.os, "scandir", lambda _path: EndlessEntries())
        result = observer.read_observation(ledger, expected_nonce=NONCE)
    assert result["observer_status"] == "unavailable"
    assert len(steps) == observer.MAX_REQUESTS + 2
