from __future__ import annotations

import hashlib
import json
import os
import select
import shutil
import subprocess
import time
from pathlib import Path
from test.live_provider import opencode_http_text_stream_observer as observer
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

pytestmark = pytest.mark.offline_database

NONCE = "a" * 32
DIGEST = "b" * 64
TERMINAL = "0123abcd"
PROMPTS = ["first END", "second END"]


def private(path):
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def install(tmp_path):
    return observer.install(
        tmp_path / "runtime",
        private(tmp_path / "project"),
        NONCE,
        PROMPTS,
        "\n\nreceipt: ",
        "\nYour own CAO terminal ID is {{CAO_TERMINAL_ID}}; END",
        "END",
    )


def record(ordinal, *, state="active", **changes):
    return {
        "schema_version": 1,
        "nonce": NONCE,
        "sequence": ordinal,
        "request_ordinal": 1,
        "request_bound": True,
        "phase_ordinal": 2,
        "receipt_sha256": DIGEST,
        "state": state,
        "reason": "stream_finished" if state == "finished" else "none",
        "wire_bytes": 200 + ordinal,
        "text_bytes": ordinal,
        "text_events": ordinal,
        "event_count": ordinal,
        "original_not_replaced": True,
        **changes,
    }


def ledger(tmp_path):
    runtime = private(tmp_path / "runtime")
    opencode = private(runtime / ".opencode")
    root = private(opencode / "http-text-stream-observation")
    return private(root / TERMINAL)


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf8")
    path.chmod(0o600)


def test_installer_freezes_complete_private_import_closure_without_writing_project(tmp_path):
    result = install(tmp_path)
    plugins = result.private_root / ".opencode/plugins"
    assert sorted(p.name for p in plugins.iterdir()) == ["opencode_http_text_stream_observer.js"]
    assert len(list(result.support_directory.iterdir())) == 3
    assert not (tmp_path / "project/.opencode").exists()
    for directory in [result.private_root, plugins.parent, plugins, result.support_directory]:
        assert directory.stat().st_mode & 0o777 == 0o700
    for path in [*plugins.iterdir(), *result.support_directory.iterdir(), result.expectation_path]:
        assert path.stat().st_mode & 0o777 == 0o600
    source = (plugins / "opencode_http_text_stream_observer.js").read_text()
    assert "../http-stream-support/opencode_http_prompt_observer.js" in source
    assert "terminalId: process.env.CAO_TERMINAL_ID" in source
    assert NONCE in source
    assert result.private_root.as_uri() in result.project_loader_bytes.decode()


def test_sparse_live_snapshot_exports_identity_and_monotonic_counts(tmp_path):
    directory = ledger(tmp_path)
    write(directory / "sample-0001.json", record(1))
    write(directory / "sample-0003.json", record(3))
    result = observer.read_live(directory, NONCE, DIGEST)
    assert result["active"] is True
    assert result["sequence"] == 3
    assert result["request_ordinal"] == 1
    assert result["directory_identity"] == [directory.stat().st_dev, directory.stat().st_ino]
    assert result["text_events"] == 3


@pytest.mark.parametrize(
    "change",
    [
        {"receipt_sha256": "c" * 64},
        {"request_bound": False},
        {"phase_ordinal": 1},
        {"original_not_replaced": False},
        {"text_bytes": 0},
        {"text_events": 0},
        {"state": "waiting"},
        {"state": "finished", "reason": "stream_finished"},
        {"sequence": True},
        {"unexpected": "field"},
    ],
)
def test_live_projection_refuses_invalid_or_nonactive_samples(tmp_path, change):
    directory = ledger(tmp_path)
    write(directory / "sample-0001.json", record(1, **change))
    assert observer.read_live(directory, NONCE, DIGEST)["active"] is False


@pytest.mark.parametrize("change", [{"request_ordinal": 2}, {"text_bytes": 0}])
def test_other_request_or_regressed_counters_refuse_complete_ledger(tmp_path, change):
    directory = ledger(tmp_path)
    write(directory / "sample-0001.json", record(1))
    write(directory / "sample-0002.json", record(2, **change))
    assert observer.read_live(directory, NONCE, DIGEST)["active"] is False


def test_invalid_marker_blocks_earlier_positive_evidence(tmp_path):
    directory = ledger(tmp_path)
    write(directory / "sample-0001.json", record(1))
    write(
        directory / "invalid.json",
        {
            "schema_version": 1,
            "nonce": NONCE,
            "status": "unavailable",
            "reason": "observer_integrity",
        },
    )
    assert observer.read_live(directory, NONCE, DIGEST)["active"] is False


def test_duplicate_keys_and_nonprivate_files_are_refused(tmp_path):
    directory = ledger(tmp_path)
    path = directory / "sample-0001.json"
    path.write_text('{"state":"waiting","state":"active"}')
    path.chmod(0o600)
    assert observer.read_live(directory, NONCE, DIGEST)["active"] is False
    write(path, record(1))
    path.chmod(0o644)
    assert observer.read_live(directory, NONCE, DIGEST)["active"] is False


def test_installer_refuses_reuse_and_preserves_existing_bytes(tmp_path):
    result = install(tmp_path)
    before = result.expectation_path.read_bytes()
    with pytest.raises(RuntimeError):
        observer.install(
            result.private_root,
            tmp_path / "project",
            NONCE,
            PROMPTS,
            "\n\nreceipt: ",
            "\nEND",
            "END",
        )
    assert result.expectation_path.read_bytes() == before


@pytest.mark.parametrize(
    "suffix,prompts",
    [
        ("\nEND", PROMPTS),
        ("{{CAO_TERMINAL_ID}}{{CAO_TERMINAL_ID}} END", PROMPTS),
        ("{{CAO_TERMINAL_ID}} END", ["first END ", "second END"]),
    ],
)
def test_installer_refuses_ambiguous_or_nonproduction_templates_before_writing(
    tmp_path, suffix, prompts
):
    project = private(tmp_path / "project")
    runtime = tmp_path / "runtime"
    with pytest.raises(RuntimeError):
        observer.install(runtime, project, NONCE, prompts, "\n\nreceipt: ", suffix, "END")
    assert not runtime.exists()


def test_actual_installed_node_hooks_private_writer_and_python_live_reader_compose(tmp_path):
    from cli_agent_orchestrator.providers.base import BaseProvider

    source_body, source_receipt = "__SOURCE_BODY__", "__SOURCE_RECEIPT__"
    template = BaseProvider._format_turn_receipt_input(
        SimpleNamespace(terminal_id="{{CAO_TERMINAL_ID}}"),
        source_body,
        source_receipt,
    )
    before, suffix = template.split(source_receipt)
    assert before.startswith(source_body)
    result = observer.install(
        tmp_path / "runtime",
        private(tmp_path / "project"),
        NONCE,
        PROMPTS,
        before[len(source_body) :],
        suffix,
        "END",
    )
    source_prompt = tmp_path / "source-prompt.txt"
    source_prompt.write_text(
        BaseProvider._format_turn_receipt_input(
            SimpleNamespace(terminal_id=TERMINAL),
            PROMPTS[1],
            "CAO-TURN-RECEIPT-" + "c" * 32,
        ),
        encoding="utf8",
    )
    source_prompt.chmod(0o600)
    project = tmp_path / "project"
    plugins = private(private(project / ".opencode") / "plugins")
    loader = plugins / "stream-loader.js"
    loader.write_bytes(result.project_loader_bytes)
    loader.chmod(0o644)  # Git worktrees preserve this mode, not private0600.
    script = tmp_path / "exercise.mjs"
    script.write_text(
        r"""
import fs from "node:fs/promises"
import readline from "node:readline"
import { pathToFileURL } from "node:url"
const { default: plugin } = await import(pathToFileURL(process.argv[2]).href)
const expectation = JSON.parse(await fs.readFile(process.argv[3]))
const hooks = new Map()
let disposed = 0
const cleanup = await plugin.setup({ session: { hook: async (name, callback) => {
  hooks.set(name, callback)
  return { dispose: async () => { disposed += 1 } }
} } })
if (typeof cleanup !== "function" || hooks.size !== 2) throw new Error("hook_setup_failed")
const request = new Request("https://example.invalid", { method: "POST", body: JSON.stringify({
  messages: [{ role: "user", content: await fs.readFile(process.argv[4], "utf8") }],
}) })
const model = { providerID: "opencode-go", id: "glm-5.3-flash" }
hooks.get("http.request")({ kind: "primary", model, request })
await request.text()
let source
const response = new Response(new ReadableStream({ start(controller) { source = controller } }), {
  headers: { "content-type": "text/event-stream" },
})
const event = { kind: "primary", model, request, response }
hooks.get("http.response")(event)
const original = response.text()
const sent = []
function frame(content, finish = null) {
  return Buffer.from(`data: ${JSON.stringify({ id: "response", model: "synthetic-wire-model", choices: [{ index: 0,
    delta: content === null ? {} : { content }, finish_reason: finish }] })}

`)
}
process.stdout.write(JSON.stringify({ schema: 1, state: "ready" }) + "\n")
for await (const line of readline.createInterface({ input: process.stdin })) {
  const command = JSON.parse(line)
  if (Object.keys(command).sort().join(",") !== "operation,schema" || command.schema !== 1) throw new Error("command_invalid")
  if (command.operation === "first" || command.operation === "second") {
    const bytes = frame(command.operation)
    sent.push(bytes); source.enqueue(bytes)
  } else if (command.operation === "finish") {
    const bytes = frame(null, "stop")
    sent.push(bytes); source.enqueue(bytes); source.close()
    const actual = await original
    await cleanup()
    process.stdout.write(JSON.stringify({ schema: 1, state: "finished", original_exact: actual === Buffer.concat(sent).toString(),
      original_identity: event.response === response, disposed }) + "\n")
    break
  } else throw new Error("command_invalid")
}
""",
        encoding="utf8",
    )
    script.chmod(0o600)
    node = shutil.which("node")
    assert node is not None
    process = subprocess.Popen(
        [node, str(script), str(loader), str(result.expectation_path), str(source_prompt)],
        cwd=project,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "CAO_TERMINAL_ID": TERMINAL},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )
    directory = result.ledger_root / TERMINAL
    digest = hashlib.sha256(("CAO-TURN-RECEIPT-" + "c" * 32).encode()).hexdigest()

    def read_message():
        assert select.select([process.stdout], [], [], 5)[0], "node control output timeout"
        raw = process.stdout.readline(1025)
        assert raw.endswith(b"\n") and len(raw) <= 1024
        return json.loads(raw)

    def send(operation):
        process.stdin.write((json.dumps({"schema": 1, "operation": operation}) + "\n").encode())

    def live_after(minimum):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            value = observer.read_live(directory, NONCE, digest)
            if value["active"] and value["text_events"] > minimum:
                return value
            time.sleep(0.01)
        pytest.fail("installed hook did not publish fresh live evidence")

    try:
        assert read_message() == {"schema": 1, "state": "ready"}
        send("first")
        first = live_after(0)
        send("second")
        second = live_after(first["text_events"])
        assert second["sequence"] > first["sequence"]
        assert second["text_bytes"] > first["text_bytes"]
        assert second["request_ordinal"] == first["request_ordinal"]
        assert second["directory_identity"] == first["directory_identity"]
        send("finish")
        process.stdin.close()
        assert read_message() == {
            "schema": 1,
            "state": "finished",
            "original_exact": True,
            "original_identity": True,
            "disposed": 2,
        }
        assert process.wait(timeout=2) == 0
        assert observer.read_live(directory, NONCE, digest)["active"] is False
        seal = json.loads((directory / "seal.json").read_text())
        assert seal["sample_count"] == len(list(directory.glob("sample-*.json")))
        schema_path = (
            Path(__file__).resolve().parents[2]
            / "specs/025-provider-mcp-profiles/contracts/http-text-stream-observation.schema.json"
        )
        schema = json.loads(schema_path.read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for path in directory.glob("*.json"):
            validator.validate(json.loads(path.read_text()))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for pipe in [process.stdin, process.stdout]:
            if pipe is not None:
                pipe.close()
