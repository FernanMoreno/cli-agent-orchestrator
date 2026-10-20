from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import stat
import subprocess
from pathlib import Path
from test.live_provider import interaction_cli_journey as common
from test.live_provider import opencode_http_tool_offer_observer as observer
from test.live_provider import opencode_interaction_journey as journey

import pytest

NONCE = "0123456789abcdef0123456789abcdef"
TOOLS = observer.TOOL_NAMES


def _record(
    *,
    nonce: str = NONCE,
    ordinal: int = 1,
    kind: str = "primary",
    model_match: bool = True,
    status: str = "valid_empty",
    names: dict[str, int] | None = None,
    tool_count: int = 0,
    other_count: int = 0,
) -> dict[str, object]:
    if status not in {"observed", "valid_empty"}:
        return {
            "schema_version": 1,
            "nonce": nonce,
            "ordinal": ordinal,
            "kind": kind,
            "model_match": model_match,
            "status": status,
            "tool_count": None,
            "name_counts": None,
            "other_name_count": None,
        }
    name_counts = names or {name: 0 for name in TOOLS}
    return {
        "schema_version": 1,
        "nonce": nonce,
        "ordinal": ordinal,
        "kind": kind,
        "model_match": model_match,
        "status": status,
        "tool_count": tool_count,
        "name_counts": name_counts,
        "other_name_count": other_count,
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")
    path.chmod(0o600)


def _write_ledger(
    path: Path,
    *,
    nonce: str = NONCE,
    records: list[dict[str, object]] | None = None,
    hook_disposed: bool = True,
    trace_complete: bool = True,
    overflow: bool = False,
    record_count: int | None = None,
    durable_record_count: int | None = None,
    seal: bool = True,
    extra_record_fields: dict[str, object] | None = None,
) -> None:
    path.mkdir(mode=0o700, parents=True)
    path.chmod(0o700)
    record_values = [] if records is None else records
    for index, record in enumerate(record_values, start=1):
        value = dict(record)
        if extra_record_fields:
            value.update(extra_record_fields)
        _write_json(path / f"request-{index:04d}.json", value)
    if seal:
        _write_json(
            path / "seal.json",
            {
                "schema_version": 1,
                "nonce": nonce,
                "hook_disposed": hook_disposed,
                "trace_complete": trace_complete,
                "record_count": len(record_values) if record_count is None else record_count,
                "durable_record_count": (
                    len(record_values) if durable_record_count is None else durable_record_count
                ),
                "overflow": overflow,
            },
        )


def test_reads_durable_request_counts_and_only_allowlisted_names(tmp_path: Path) -> None:
    path = tmp_path / "ledger"
    counts = {TOOLS[0]: 1, TOOLS[1]: 1, TOOLS[2]: 0}
    record = _record(status="observed", names=counts, tool_count=3, other_count=1)
    _write_ledger(path, records=[record])

    value = observer.read_observation(path, expected_nonce=NONCE)

    assert value["observer_status"] == "observed"
    assert value["tool_offer_status"] == "observed"
    assert value["nonce"] == NONCE
    assert value["hook_disposed"] is True
    assert value["trace_complete"] is True
    assert value["request_count"] == 1
    assert value["durable_record_count"] == 1
    assert value["target_primary_request_count"] == 1
    assert value["valid_target_primary_request_count"] == 1
    assert value["tool_name_counts"] == counts
    assert value["other_tool_name_count"] == 1
    assert value["requests"][0]["ordinal"] == 1


def test_request_fact_survives_missing_global_seal_but_overall_stays_incomplete(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger"
    _write_ledger(
        path,
        records=[
            _record(status="observed", tool_count=1, names={TOOLS[0]: 1, TOOLS[1]: 0, TOOLS[2]: 0})
        ],
        seal=False,
    )

    value = observer.read_observation(path, expected_nonce=NONCE)

    assert value["observer_status"] == "incomplete"
    assert value["tool_offer_status"] == "incomplete"
    assert value["hook_disposed"] is False
    assert value["trace_complete"] is False
    assert value["requests"][0]["status"] == "observed"
    assert value["requests"][0]["tool_count"] == 1
    assert value["valid_target_primary_request_count"] == 1


def test_forced_child_termination_preserves_request_fact_without_global_success(
    tmp_path: Path,
) -> None:
    node = shutil.which("node")
    assert node is not None
    working_directory = tmp_path / "work"
    working_directory.mkdir(mode=0o700)
    working_directory.chmod(0o700)
    ledger = observer.install_project_plugin(
        working_directory=working_directory,
        source_path=Path(__file__).with_name("opencode_http_tool_offer_observer.js"),
        nonce=NONCE,
    )
    plugin = working_directory / ".opencode" / "plugins" / "cao-http-tool-offer-observer.js"
    plugin_literal = json.dumps(str(plugin))
    nonce_literal = json.dumps(NONCE)
    child_source = f"""
import {{ pathToFileURL }} from "node:url"
import {{ readFile }} from "node:fs/promises"
const pluginPath = {plugin_literal}
const nonce = {nonce_literal}
const source = await readFile(pluginPath)
const module = await import("data:text/javascript;base64," + source.toString("base64"))
const observer = module.createToolOfferObserver({{ nonce, moduleUrl: pathToFileURL(pluginPath).href }})
let callback
await observer.setup({{ session: {{ hook: async (_name, registered) => {{
  callback = registered
  return {{ dispose: async () => undefined }}
}} }} }})
const request = new Request("https://example.invalid/", {{
  method: "POST",
  body: JSON.stringify({{ tools: [{{ type: "function", function: {{
    name: "cao-mcp-server_cao_ask_user",
    description: "PRIVATE_DESCRIPTION_SENTINEL",
    parameters: {{ type: "object", properties: {{ secret: {{ const: "PRIVATE_SCHEMA_SENTINEL" }} }} }},
  }} }}] }}),
}})
const originalBody = await request.clone().text()
await callback({{ kind: "primary", model: {{ providerID: "opencode-go", id: "longcat-2.5-preview-free", variant: "balanced" }}, request }})
if (await request.text() !== originalBody) process.exit(21)
process.stdout.write("READY\\n")
setInterval(() => undefined, 60000)
"""
    child_environment = {
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
    }
    child = subprocess.Popen(
        [node, "--input-type=module", "-e", child_source],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=child_environment,
        close_fds=True,
    )
    try:
        assert child.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            ready = selector.select(timeout=10)
            assert ready, "observer child did not finish request publication"
            marker = child.stdout.readline(32)
        assert marker == b"READY\n"
        child.send_signal(signal.SIGKILL)
        assert child.wait(timeout=5) == -signal.SIGKILL
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        if child.stdout is not None:
            child.stdout.close()

    record_path = ledger / "request-0001.json"
    assert record_path.is_file()
    assert not (ledger / "seal.json").exists()
    assert not any(name.name.endswith(".pending") for name in ledger.iterdir())
    value = observer.read_observation(ledger, expected_nonce=NONCE)
    assert value["observer_status"] == "incomplete"
    assert value["tool_offer_status"] == "incomplete"
    assert value["hook_disposed"] is False
    assert value["trace_complete"] is False
    assert value["request_count"] == 1
    assert value["valid_target_primary_request_count"] == 1
    request = value["requests"][0]
    assert request["status"] == "observed"
    assert request["tool_count"] == 1
    assert request["name_counts"] == {TOOLS[0]: 1, TOOLS[1]: 0, TOOLS[2]: 0}
    assert "PRIVATE_DESCRIPTION_SENTINEL" not in record_path.read_text(encoding="utf-8")
    assert "PRIVATE_SCHEMA_SENTINEL" not in record_path.read_text(encoding="utf-8")


def test_shared_receipt_preserves_only_revalidated_per_request_evidence(tmp_path: Path) -> None:
    path = tmp_path / "ledger"
    _write_ledger(path, records=[_record(status="valid_empty")], seal=False)
    offer = observer.read_observation(path, expected_nonce=NONCE)

    unsafe = common._safe_provider_receipt(
        {
            "provider": "opencode_cli",
            "opencode_http_tool_offer": {
                **offer,
                "prompt": "PROMPT_SENTINEL",
                "authorization": "AUTH_SENTINEL",
            },
        }
    )

    projected = unsafe["opencode_http_tool_offer"]
    assert projected["observer_status"] == "incomplete"
    assert projected["tool_offer_status"] == "incomplete"
    assert projected["requests"][0]["status"] == "valid_empty"
    assert "PROMPT_SENTINEL" not in json.dumps(unsafe)
    assert "AUTH_SENTINEL" not in json.dumps(unsafe)

    valid = common._safe_provider_receipt(
        {"provider": "opencode_cli", "opencode_http_tool_offer": offer}
    )
    assert valid["opencode_http_tool_offer"] == offer


def test_projection_keeps_gapped_request_fact_but_rejects_complete_trace(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger"
    _write_ledger(
        path,
        records=[
            _record(
                status="observed",
                names={TOOLS[0]: 1, TOOLS[1]: 0, TOOLS[2]: 0},
                tool_count=1,
            )
        ],
    )
    source_summary = observer.read_observation(path, expected_nonce=NONCE)
    forged = dict(source_summary)
    forged["requests"] = [{**source_summary["requests"][0], "ordinal": 2}]

    projected = observer.project_safe_summary(forged)

    assert projected["observer_status"] == "incomplete"
    assert projected["tool_offer_status"] == "incomplete"
    assert projected["trace_complete"] is False
    assert projected["requests"][0]["ordinal"] == 2
    assert projected["requests"][0]["status"] == "observed"


def test_projection_requires_valid_nonce_for_complete_empty_trace() -> None:
    forged = observer._build_summary(
        nonce=None,
        records=[],
        hook_disposed=True,
        trace_complete=True,
        overflow=False,
        evidence_present=True,
        durable_record_count=0,
    )

    projected = observer.project_safe_summary(forged)

    assert projected["observer_status"] == "incomplete"
    assert projected["tool_offer_status"] == "incomplete"
    assert projected["trace_complete"] is False
    assert projected["nonce"] is None


def test_unconfirmed_record_is_not_projected_when_seal_count_disagrees(tmp_path: Path) -> None:
    path = tmp_path / "ledger"
    _write_ledger(
        path,
        records=[
            _record(status="observed", tool_count=1, names={TOOLS[0]: 1, TOOLS[1]: 0, TOOLS[2]: 0})
        ],
        trace_complete=False,
        durable_record_count=0,
    )

    value = observer.read_observation(path, expected_nonce=NONCE)

    assert value["observer_status"] == "incomplete"
    assert value["tool_offer_status"] == "incomplete"
    assert value["trace_complete"] is False
    assert value["requests"] == []
    assert value["valid_target_primary_request_count"] == 0


def test_valid_empty_array_is_distinct_from_no_matching_request(tmp_path: Path) -> None:
    path = tmp_path / "ledger"
    _write_ledger(path, records=[_record(status="valid_empty")])
    empty_offer = observer.read_observation(path, expected_nonce=NONCE)
    assert empty_offer["tool_offer_status"] == "observed"
    assert empty_offer["tool_name_counts"] == {name: 0 for name in TOOLS}

    no_target_path = tmp_path / "no-target"
    _write_ledger(
        no_target_path,
        records=[_record(kind="title", status="non_primary")],
    )
    no_target = observer.read_observation(no_target_path, expected_nonce=NONCE)
    assert no_target["observer_status"] == "observed"
    assert no_target["tool_offer_status"] == "unobserved"
    assert no_target["target_primary_request_count"] == 0


def test_unknown_kind_or_invalid_target_body_is_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "ledger"
    records = [
        _record(kind="unknown", status="request_kind_unknown"),
        _record(ordinal=2, status="tools_absent"),
    ]
    _write_ledger(path, records=records)

    value = observer.read_observation(path, expected_nonce=NONCE)

    assert value["observer_status"] == "observed"
    assert value["tool_offer_status"] == "incomplete"
    assert value["request_status_counts"]["request_kind_unknown"] == 1
    assert value["request_status_counts"]["tools_absent"] == 1
    assert value["target_primary_request_count"] == 1
    assert value["valid_target_primary_request_count"] == 0


def test_hook_disposal_and_trace_integrity_are_separate_from_request_facts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger"
    _write_ledger(
        path,
        records=[_record(status="valid_empty")],
        hook_disposed=False,
        trace_complete=False,
    )

    value = observer.read_observation(path, expected_nonce=NONCE)

    assert value["observer_status"] == "incomplete"
    assert value["tool_offer_status"] == "incomplete"
    assert value["hook_disposed"] is False
    assert value["trace_complete"] is False
    assert value["requests"][0]["status"] == "valid_empty"


def test_foreign_nonce_record_is_not_exposed(tmp_path: Path) -> None:
    path = tmp_path / "ledger"
    _write_ledger(path, nonce=NONCE, records=[_record(nonce="f" * 32)])

    value = observer.read_observation(path, expected_nonce=NONCE)

    assert value["observer_status"] == "incomplete"
    assert value["tool_offer_status"] == "incomplete"
    assert value["requests"] == []
    assert "f" * 32 not in json.dumps(value)


@pytest.mark.parametrize(
    "extra_record_fields",
    [
        {"prompt": "PROMPT_SENTINEL"},
        {"headers": {"Authorization": "AUTH_SENTINEL"}},
        {"request_body": "BODY_SENTINEL"},
    ],
)
def test_unrecognized_record_fields_fail_closed_without_echo(
    tmp_path: Path, extra_record_fields: dict[str, object]
) -> None:
    path = tmp_path / "ledger"
    _write_ledger(
        path,
        records=[_record(status="valid_empty")],
        extra_record_fields=extra_record_fields,
    )

    value = observer.read_observation(path, expected_nonce=NONCE)

    assert value["observer_status"] == "incomplete"
    assert value["tool_offer_status"] == "incomplete"
    assert "PROMPT_SENTINEL" not in json.dumps(value)
    assert "AUTH_SENTINEL" not in json.dumps(value)
    assert "BODY_SENTINEL" not in json.dumps(value)


def test_duplicate_keys_gaps_overflow_and_unexpected_files_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "duplicate"
    _write_ledger(path, records=[_record(status="valid_empty")])
    record_path = path / "request-0001.json"
    record_path.write_text(
        '{"schema_version":1,"schema_version":1,"nonce":"' + NONCE + '"}',
        encoding="utf-8",
    )
    record_path.chmod(0o600)
    assert observer.read_observation(path, expected_nonce=NONCE)["observer_status"] == "incomplete"

    gap_path = tmp_path / "gap"
    _write_ledger(gap_path, records=[_record(ordinal=2, status="valid_empty")])
    assert (
        observer.read_observation(gap_path, expected_nonce=NONCE)["observer_status"] == "incomplete"
    )

    overflow_path = tmp_path / "overflow"
    _write_ledger(
        overflow_path,
        records=[_record(status="valid_empty")],
        overflow=True,
        trace_complete=False,
    )
    assert (
        observer.read_observation(overflow_path, expected_nonce=NONCE)["observer_status"]
        == "incomplete"
    )

    pending_path = tmp_path / "pending"
    _write_ledger(pending_path, records=[_record(status="valid_empty")])
    (pending_path / ".request-0002.json.pending").write_text("partial", encoding="utf-8")
    assert (
        observer.read_observation(pending_path, expected_nonce=NONCE)["observer_status"]
        == "incomplete"
    )


def test_missing_seal_with_no_request_is_unobserved(tmp_path: Path) -> None:
    path = tmp_path / "empty"
    path.mkdir(mode=0o700)
    path.chmod(0o700)

    value = observer.read_observation(path, expected_nonce=NONCE)

    assert value["observer_status"] == "unobserved"
    assert value["tool_offer_status"] == "unobserved"
    assert value["trace_complete"] is False


def test_non_private_ledger_or_file_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "ledger"
    _write_ledger(path, records=[_record(status="valid_empty")])
    path.chmod(0o750)
    assert observer.read_observation(path, expected_nonce=NONCE)["observer_status"] == "incomplete"

    path.chmod(0o700)
    record_path = path / "request-0001.json"
    record_path.chmod(0o640)
    assert observer.read_observation(path, expected_nonce=NONCE)["observer_status"] == "incomplete"


def test_symlink_ledger_and_oversized_records_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    source.chmod(0o700)
    link = tmp_path / "link"
    link.symlink_to(source, target_is_directory=True)
    assert observer.read_observation(link, expected_nonce=NONCE)["observer_status"] == "incomplete"

    oversized = tmp_path / "oversized"
    _write_ledger(oversized, records=[_record(status="valid_empty")])
    record_path = oversized / "request-0001.json"
    record_path.write_bytes(b"x" * (observer.MAX_RECORD_BYTES + 1))
    record_path.chmod(0o600)
    assert (
        observer.read_observation(oversized, expected_nonce=NONCE)["observer_status"]
        == "incomplete"
    )


def test_plugin_install_is_create_only_private_and_byte_exact(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir(mode=0o700)
    source = tmp_path / "observer.js"
    source.write_bytes(b"export default { id: 'sentinel', setup() {} }\n")

    ledger_directory = observer.install_project_plugin(
        working_directory=workdir,
        source_path=source,
        nonce=NONCE,
    )
    installed = workdir / ".opencode" / "plugins" / "cao-http-tool-offer-observer.js"

    assert installed.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(installed.stat().st_mode) == 0o600
    assert stat.S_IMODE(installed.parent.stat().st_mode) == 0o700
    assert ledger_directory == workdir / ".opencode" / "http-tool-offer-observation"
    assert not ledger_directory.exists()
    with pytest.raises(FileExistsError):
        observer.install_project_plugin(
            working_directory=workdir,
            source_path=source,
            nonce=NONCE,
        )
    with pytest.raises(RuntimeError, match="nonce_invalid"):
        observer.install_project_plugin(
            working_directory=tmp_path / "unused",
            source_path=source,
            nonce="not-a-nonce",
        )


def test_plugin_install_fails_closed_if_parent_directory_sync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir(mode=0o700)
    source = tmp_path / "observer.js"
    source.write_bytes(b"export default { id: 'sentinel', setup() {} }\n")
    sync_directory = observer._fsync_directory

    def fail_plugin_directory(path: Path) -> None:
        if path.name == "plugins":
            raise OSError("DIRECTORY_SYNC_SENTINEL")
        sync_directory(path)

    monkeypatch.setattr(observer, "_fsync_directory", fail_plugin_directory)
    with pytest.raises(OSError, match="DIRECTORY_SYNC_SENTINEL"):
        observer.install_project_plugin(
            working_directory=workdir,
            source_path=source,
            nonce=NONCE,
        )
    assert not (workdir / ".opencode" / "plugins" / "cao-http-tool-offer-observer.js").exists()


def test_nonce_is_added_only_for_the_explicit_observer_lane(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir(mode=0o700)
    tmux = root / "tmux"
    tmux.mkdir(mode=0o700)
    base = {
        "root": root,
        "cao_home": root / "cao",
        "config_dir": root / "config",
        "data_home": root / "data",
        "api_port": 12345,
        "source_pythonpath": "/source",
        "path_value": "/usr/bin:/bin",
        "tmux_tmpdir": tmux,
    }

    ordinary = journey._private_cli_environment(**base)
    assert "CAO_OPENCODE_HTTP_TOOL_OFFER_NONCE" not in ordinary
    agui = journey._private_cli_environment(**base, http_tool_offer_nonce=NONCE)
    assert agui["CAO_OPENCODE_HTTP_TOOL_OFFER_NONCE"] == NONCE
    with pytest.raises(RuntimeError, match="nonce_invalid"):
        journey._private_cli_environment(**base, http_tool_offer_nonce="invalid")
