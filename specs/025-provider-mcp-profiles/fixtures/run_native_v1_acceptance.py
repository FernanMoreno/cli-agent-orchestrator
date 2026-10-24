"""Default-closed major-1 native diagnostic; explicit private bindings required."""

import argparse
import ast
import contextlib
import datetime
import hashlib
import importlib.util
import inspect
import json
import math
import os
import re
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import requests

RUN_ENABLED = False
parser = argparse.ArgumentParser()
parser.add_argument("--manifest", type=Path, required=True)
parser.add_argument("--expected-head", required=True)
parser.add_argument("--go", action="store_true")
args = parser.parse_args()
if sys.flags.optimize:
    raise RuntimeError("optimized_python_not_supported")
if not RUN_ENABLED or args.go is not True:
    raise RuntimeError("execution_gate_closed")


def private_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        st = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or st.st_nlink != 1
            or st.st_mode & 0o077
            or st.st_size > 2097152
        ):
            raise RuntimeError("private_binding_file_invalid")
        value = json.load(stream)
    if type(value) is not dict:
        raise RuntimeError("private_binding_file_invalid")
    return value


m = private_json(args.manifest)
if m.get("execution_authorized") is not True:
    raise RuntimeError("execution_gate_closed")
repo = Path(m["workspace"])
cache = Path(m["evidence_dir"])
label = "t100-v1-messages"
expected_head = m["source_head"]
if args.expected_head != expected_head:
    raise RuntimeError("source_head_confirmation_mismatch")
if (
    cache.is_symlink()
    or not cache.is_dir()
    or cache.stat().st_mode & 0o777 != 0o700
    or cache.stat().st_uid != os.getuid()
):
    raise RuntimeError("private_evidence_directory_invalid")
if (
    subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    != expected_head
):
    raise RuntimeError("source_head_pin_mismatch")
if subprocess.check_output(
    ["git", "status", "--porcelain", "--untracked-files=all", "--", "src", "test"],
    cwd=repo,
    text=True,
).strip():
    raise RuntimeError("source_runtime_tree_dirty")
sourcefiles = {
    entry.decode("utf-8")
    for entry in subprocess.check_output(
        [
            "git",
            "ls-tree",
            "-r",
            "-z",
            "--name-only",
            expected_head,
            "--",
            "src",
            "test/live_provider",
            "test/fixtures/cao_server.py",
            "test/postgres/fixtures.py",
        ],
        cwd=repo,
    ).split(b"\0")
    if entry
}
if not sourcefiles or sourcefiles != set(m["source_files"]):
    raise RuntimeError("source_inventory_mismatch")
required_artifacts = {
    "specs/025-provider-mcp-profiles/fixtures/" + name
    for name in (
        "native_v1_probe.py",
        "native_phase_observer.py",
        "native_phase_readers.py",
        "native_acceptance_contract.py",
        "run_native_v1_acceptance.py",
        "native-message-acceptance-profiles.json",
    )
} | {"specs/025-provider-mcp-profiles/opencode-v1-pin.json"}
if set(m["artifacts"]) != required_artifacts:
    raise RuntimeError("artifact_inventory_mismatch")
sourcepins = {**m["source_files"], **m["artifacts"]}
for relative, digest in sourcepins.items():
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise RuntimeError("source_binding_path_invalid")
    content = (repo / relative).read_bytes()
    if hashlib.sha256(content).hexdigest() != digest or content != subprocess.check_output(
        ["git", "show", expected_head + ":" + relative], cwd=repo
    ):
        raise RuntimeError("source_file_binding_mismatch")
runtime_relative = "specs/025-provider-mcp-profiles/fixtures/run_native_v1_acceptance.py"
closed = (repo / runtime_relative).read_text()
executed = Path(__file__).read_text()
if (
    hashlib.sha256(closed.encode()).hexdigest() != m["closed_runner_sha256"]
    or executed.replace("RUN_ENABLED = True", "RUN_ENABLED = False", 1) != closed
    or hashlib.sha256(executed.encode()).hexdigest() != m["runner_sha256"]
):
    raise RuntimeError("runner_binding_mismatch")
binding = hashlib.sha256(
    json.dumps(
        {k: v for k, v in m.items() if k not in ("execution_authorized", "runner_sha256")},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()
review = private_json(Path(m["review_file"]))
if review.get("approved") is not True or review.get("bindings_sha256") != binding:
    raise RuntimeError("independent_review_binding_missing")
sys.path[:0] = [
    str(repo / "specs/025-provider-mcp-profiles/fixtures"),
    str(repo / "src"),
    str(repo),
    str(repo / "test/live_provider"),
]
import native_acceptance_contract as acceptance
import native_v1_probe as native_probe
from native_phase_observer import NativePhaseObserver, RoleScope
from native_phase_readers import HTTPStatusError, OwnedPhaseReaders, http_json_bytes

case = json.loads(
    (
        repo / "specs/025-provider-mcp-profiles/fixtures/native-message-acceptance-profiles.json"
    ).read_text()
)
if case["required_checks"] != {
    "receiver_public_ack_exact": "ACK",
    "active_standalone_turn_receipt": True,
    "sender_unicode_marker": "Validado ñ, 中文.",
    "phase_seconds": 75,
    "memory_floor_mib": 6144,
    "message_phases": 2,
    "api_restart": True,
    "owned_cleanup": True,
}:
    raise RuntimeError("native_case_contract_mismatch")
generation_path = Path(m["generation_receipt"])
generation = private_json(generation_path)
if hashlib.sha256(generation_path.read_bytes()).hexdigest() != m[
    "generation_receipt_sha256"
] or not acceptance.generation_exact(generation, expected_head, m["generation_bindings"]):
    raise RuntimeError("fresh_generation_gate_failed")
if case["model"] != generation["model"]:
    raise RuntimeError("native_case_contract_mismatch")
binary = Path(m["binary"])
expected_binary_sha256 = "77b2cfe4b97df6f15c3673b22100b9f79c711f25ecb9bf513bb82526b15d24fa"
expected_pg_container_id = m["postgres_container_id"]
expected_pg_image_id = "sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336"
expected_pg_fixture_sha256 = "7318d370ef67be8600804bc1f893bf51a33cd8933e36d465a4fcaa190f9da7b4"
expected_pg_guard_sha256 = "d8b7b81703e3331f5b0c69d1fc34e8d85b0aa5d1e82c9437e168c1d66766b35a"
metadata = Path(m["postgres_metadata"])
metadata_document = private_json(metadata)
if (
    metadata_document.get("container_id") != expected_pg_container_id
    or metadata_document.get("published_host") != "127.0.0.1"
    or metadata_document.get("published_port") != m["postgres_port"]
):
    raise RuntimeError("postgres_metadata_binding_mismatch")
if hashlib.sha256(metadata.read_bytes()).hexdigest() != m["postgres_metadata_sha256"]:
    raise RuntimeError("postgres_metadata_binding_mismatch")
if os.environ.get("CAO_TEST_POSTGRES_AUTOSTART") != "0" or os.environ.get(
    "CAO_TEST_POSTGRES_METADATA_FILE"
) != str(metadata):
    raise RuntimeError("postgres_explicit_service_required")
if any(name in os.environ for name in ("OPENCODE_DB", "XDG_DATA_HOME")):
    raise RuntimeError("inherited_native_state_override")
stat_binary = binary.lstat()
if (
    not stat.S_ISREG(stat_binary.st_mode)
    or stat_binary.st_nlink != 1
    or stat_binary.st_uid != os.getuid()
    or stat.S_IMODE(stat_binary.st_mode) != 0o700
):
    raise RuntimeError("isolated_binary_shape_invalid")
receipt_path = cache / (label + "-receipt.json")
raw = cache / (label + "-evidence")
if receipt_path.exists() or raw.exists():
    raise RuntimeError("output_already_exists")
manifest_rel = "specs/025-provider-mcp-profiles/opencode-v1-pin.json"
manifest_path = repo / manifest_rel
manifest_bytes = manifest_path.read_bytes()
pin = json.loads(manifest_bytes)
if (
    pin["binary_sha256"] != expected_binary_sha256
    or hashlib.sha256(binary.read_bytes()).hexdigest() != expected_binary_sha256
):
    raise RuntimeError("binary_pin_mismatch")
operator = Path.home()
source_db = operator / ".local/share/opencode/opencode.db"
legacy = operator / ".local/share/opencode/auth.json"


def fingerprints():
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [source_db, Path(str(source_db) + "-wal"), legacy]
        if p.is_file() and not p.is_symlink()
    }


http_session = requests.Session()
http_session.trust_env = False
_session_request = http_session.request


def _private_api_request(method, url, **kwargs):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise RuntimeError("private_api_route_invalid")
    kwargs["allow_redirects"] = False
    return _session_request(method, url, **kwargs)


http_session.request = _private_api_request
requests = http_session
before = fingerprints()
runner_path = Path(__file__).resolve()
runner_sha256 = hashlib.sha256(runner_path.read_bytes()).hexdigest()
root = Path(tempfile.mkdtemp(prefix="c60-msg-", dir="/tmp"))
root.chmod(0o700)
raw = cache / (label + "-evidence")
raw.mkdir(mode=0o700)
receipt = {
    "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "source_head": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip(),
    "source_files": sourcepins,
    "binary_sha256": pin["binary_sha256"],
    "runner_sha256": runner_sha256,
    "scope": "two actual production-installed managed-v1 native terminals; collaboration discovery/send_message, PostgreSQL/AG-UI/API restart; not external cao.work.message",
    "model": case["model"],
    "case": "T100",
    "diagnostic_only": True,
    "acceptance_claimed": False,
    "original_model_acceptance_claimed": False,
    "source_pin_file_count": len(sourcefiles),
    "generation_gate_bound": True,
    "generation_receipt_sha256": m["generation_receipt_sha256"],
    "Claude": "not used",
    "raw_output_printed": False,
    "explicit_go_confirmed": args.go,
    "phases": [],
}
observer_module = None
server = None
servers = []
server_stop_confirmed = set()
api_launch_attempts = []
pgcontext = None
pgentered = False
postgres_fixtures = None
tmux = None
tmux_start_attempted = False
tmux_cleanup_confirmed = False
sid = None
sockid = None
owned = set()
terminal_id = ""
failures = set()
start = time.monotonic()
oldenv = dict(os.environ)
captures = []
terminals = {}
_DEFERRED_POSTGRES_CONTEXTS = []


@contextlib.contextmanager
def _temporary_environment(values):
    previous = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(values)
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def admit(phase):
    n = next(
        int(x.split()[1]) // 1024
        for x in Path("/proc/meminfo").read_text().splitlines()
        if x.startswith("MemAvailable:")
    )
    receipt[phase + "_admission_mib"] = n
    if n < 6144:
        raise RuntimeError("memory_below_unchanged_6144_MiB_floor")
    print(phase + ": admitted " + str(n) + " MiB", flush=True)


def _bounded_count(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


_INBOX_FAILURE_SNAPSHOT_GRACE_SECONDS = 10.0
# Reserve two seconds from the ten-second grace for killing and reaping the child.
_INBOX_FAILURE_SNAPSHOT_WORK_SECONDS = 7.5
_INBOX_FAILURE_SNAPSHOT_REAP_SECONDS = 1.0
_INBOX_FAILURE_SNAPSHOT_MAX_BYTES = 4096
_INBOX_FAILURE_SNAPSHOT_ROW_LIMIT = 10
_INBOX_FAILURE_STATUSES = frozenset({"pending", "delivered", "reconcile", "failed"})
_INBOX_FAILURE_TERMINAL_STATES = frozenset(
    {
        "unknown",
        "idle",
        "processing",
        "completed",
        "waiting_user_answer",
        "waiting_quota",
        "reconcile",
        "error",
    }
)


def _failure_snapshot_rows_summary(rows, expected_messages, sender_id, receiver_id):
    """Reduce private inbox rows to bounded fixed-field diagnostic evidence."""
    summary = {
        "status": "invalid_payload",
        "row_count": 0,
        "valid_id_count": 0,
        "sender_receiver_match": False,
        "expected_messages_match": False,
        "statuses": [],
    }
    if type(rows) is not list or len(rows) > _INBOX_FAILURE_SNAPSHOT_ROW_LIMIT:
        summary["row_count"] = (
            min(len(rows), _INBOX_FAILURE_SNAPSHOT_ROW_LIMIT + 1) if type(rows) is list else 0
        )
        return summary
    summary["status"] = "observed"
    summary["row_count"] = len(rows)
    summary["valid_id_count"] = sum(
        1 for row in rows if type(row) is dict and type(row.get("id")) is int and row["id"] > 0
    )
    private_rows = []
    for row in rows:
        if (
            type(row) is not dict
            or set(row) != {"id", "sender_id", "receiver_id", "message", "status", "created_at"}
            or type(row.get("id")) is not int
            or row["id"] <= 0
            or type(row.get("sender_id")) is not str
            or type(row.get("receiver_id")) is not str
            or type(row.get("message")) is not str
            or type(row.get("status")) is not str
            or type(row.get("created_at")) is not str
        ):
            summary["status"] = "invalid_payload"
            continue
        try:
            created_at = datetime.datetime.fromisoformat(row["created_at"])
        except ValueError:
            summary["status"] = "invalid_payload"
            continue
        if created_at.isoformat() != row["created_at"]:
            summary["status"] = "invalid_payload"
            continue
        status = row["status"] if row["status"] in _INBOX_FAILURE_STATUSES else "other"
        summary["statuses"].append(status)
        private_rows.append(
            (
                row["id"],
                row["sender_id"],
                row["receiver_id"],
                row["message"],
                row["status"],
                row["created_at"],
            )
        )
    summary["sender_receiver_match"] = (
        summary["status"] == "observed"
        and len(rows) == len(expected_messages)
        and len(private_rows) == len(rows)
        and all(row[1] == sender_id and row[2] == receiver_id for row in private_rows)
    )
    summary["expected_messages_match"] = summary["sender_receiver_match"] and sorted(
        row[3] for row in private_rows
    ) == sorted(expected_messages)
    return summary


def _record_failure_receiver_observation(phase, state, *, phase_elapsed_seconds):
    """Cache only the latest identity-bound receiver counters already read in the loop."""
    status = state.get("status")
    phase["_failure_receiver_state"] = {
        "bound": True,
        "http_status": (
            state.get("http_status")
            if type(state.get("http_status")) is int and 100 <= state["http_status"] <= 599
            else None
        ),
        "status": status if status in _INBOX_FAILURE_TERMINAL_STATES else "other",
        "turn_sequence": _bounded_count(state.get("turn_sequence")),
        "turn_completed": _bounded_count(state.get("turn_completed")),
        "source": "phase_poll",
        "phase_elapsed_seconds": round(max(0.0, phase_elapsed_seconds), 3),
    }


def _run_bounded_private_child(argv, payload, stop, *, max_output_bytes):
    """Run a fixed diagnostic child with private stdin and incremental output bounds."""
    if (
        type(stop) not in (int, float)
        or not math.isfinite(stop)
        or type(max_output_bytes) is not int
        or not 1 <= max_output_bytes <= _INBOX_FAILURE_SNAPSHOT_MAX_BYTES
        or type(argv) is not list
        or len(argv) != 5
        or argv[0] != sys.executable
        or argv[1] != "-I"
        or argv[2] != "-B"
        or argv[3] != "-c"
        or type(argv[4]) is not str
    ):
        raise ValueError("failure_snapshot_context_invalid")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 65536:
        raise ValueError("failure_snapshot_input_limit")
    if stop - time.monotonic() <= 0:
        raise TimeoutError("failure_snapshot_deadline")
    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        bufsize=0,
    )
    selector = None
    output = bytearray()
    input_offset = 0
    stdin_open = False
    stdout_open = False
    try:
        stdin = process.stdin
        stdout = process.stdout
        if stdin is None or stdout is None:
            raise RuntimeError("failure_snapshot_child_pipes_unavailable")
        stdin_fd = stdin.fileno()
        stdout_fd = stdout.fileno()
        os.set_blocking(stdin_fd, False)
        os.set_blocking(stdout_fd, False)
        selector = selectors.DefaultSelector()
        selector.register(stdout_fd, selectors.EVENT_READ, "stdout")
        stdout_open = True
        if encoded:
            selector.register(stdin_fd, selectors.EVENT_WRITE, "stdin")
            stdin_open = True
        else:
            stdin.close()

        while stdout_open or stdin_open:
            remaining = stop - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("failure_snapshot_deadline")
            events = selector.select(remaining)
            if not events:
                raise TimeoutError("failure_snapshot_deadline")
            for key, _ in events:
                if time.monotonic() >= stop:
                    raise TimeoutError("failure_snapshot_deadline")
                if key.data == "stdin":
                    try:
                        written = os.write(
                            stdin_fd,
                            encoded[input_offset : input_offset + 4096],
                        )
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        raise RuntimeError("failure_snapshot_child_unavailable") from None
                    if written <= 0:
                        raise RuntimeError("failure_snapshot_child_unavailable")
                    input_offset += written
                    if input_offset == len(encoded):
                        selector.unregister(stdin_fd)
                        stdin_open = False
                        stdin.close()
                else:
                    read_limit = min(4096, max_output_bytes + 1 - len(output))
                    try:
                        chunk = os.read(stdout_fd, read_limit)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stdout_fd)
                        stdout_open = False
                        continue
                    if len(output) + len(chunk) > max_output_bytes:
                        raise ValueError("failure_snapshot_output_limit")
                    output.extend(chunk)

        remaining = stop - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("failure_snapshot_deadline")
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise TimeoutError("failure_snapshot_deadline") from None
        if time.monotonic() >= stop:
            raise TimeoutError("failure_snapshot_deadline")
        if process.returncode != 0:
            raise RuntimeError("failure_snapshot_child_unavailable")
        return bytes(output)
    finally:
        try:
            if process.poll() is None:
                cleanup_deadline = min(
                    stop + 2 * _INBOX_FAILURE_SNAPSHOT_REAP_SECONDS,
                    time.monotonic() + 2 * _INBOX_FAILURE_SNAPSHOT_REAP_SECONDS,
                )
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(
                        timeout=min(
                            _INBOX_FAILURE_SNAPSHOT_REAP_SECONDS,
                            max(0.0, cleanup_deadline - time.monotonic()),
                        )
                    )
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    remaining = cleanup_deadline - time.monotonic()
                    if remaining > 0:
                        process.wait(timeout=remaining)
                    else:
                        raise TimeoutError("failure_snapshot_child_reap_deadline") from None
        finally:
            if selector is not None:
                selector.close()
            for stream in (process.stdin, process.stdout):
                if stream is not None and not stream.closed:
                    stream.close()


def _read_owned_postgres_inbox_snapshot(context, stop):
    """Read the private inbox rows in a short-lived isolated Python child."""
    child_source = r"""import json, re, sys
from datetime import datetime

def emit(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")))

def unavailable(status="unavailable", identity_status="unavailable"):
    emit({"status": status, "target_identity_match": identity_status == "matched",
          "target_identity_status": identity_status, "row_count": 0,
          "sender_receiver_match": False, "expected_messages_match": False,
          "api_pg_rows_match": False, "provider_variant_is_opencode_v1": False,
          "statuses": []})

def main():
    identity_status = "unavailable"
    try:
        value = json.load(sys.stdin)
        schema = value["schema"]
        store_id = value["store_id"]
        scope = value["scope"]
        sender = value["sender_id"]
        receiver = value["receiver_id"]
        expected = value["expected_messages"]
        api_rows = value["api_rows"]
        api_rows_observed = value["api_rows_observed"]
        params = value["connection_parameters"]
        if (not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema)
                or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", store_id)
                or schema in {"public", "information_schema"}
                or schema.startswith("pg_")
                or scope != "main"
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sender)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", receiver)
                or not isinstance(expected, list) or not 1 <= len(expected) <= 2
                or any(not isinstance(item, str) or not item or len(item) > 4096 for item in expected)
                or not isinstance(api_rows, list) or len(api_rows) > 10
                or type(api_rows_observed) is not bool
                or not isinstance(params, dict)
                or not {"host", "port", "dbname", "user", "password", "sslmode"}.issubset(params)):
            unavailable("invalid_context")
            return
        import psycopg
        from psycopg import sql
        params.update(connect_timeout=1,
                      options="-c statement_timeout=500 -c lock_timeout=500 -c default_transaction_read_only=on",
                      application_name="t112-v1-inbox-snapshot-readonly")
        with psycopg.connect(**params) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL(
                    "SELECT scope,store_id FROM {}._cao_store_identity WHERE singleton=1"
                ).format(sql.Identifier(schema)))
                identity = cursor.fetchone()
                identity_match = identity == (scope, store_id)
                identity_status = "matched" if identity_match else "mismatch"
                if not identity_match:
                    unavailable("ownership_mismatch", identity_status)
                    return
                cursor.execute(sql.SQL(
                    "SELECT t.provider_variant,i.id,i.sender_id,i.receiver_id,"
                    "i.message,i.status,i.created_at "
                    "FROM {}.terminals AS t LEFT JOIN {}.inbox AS i ON i.receiver_id=t.id "
                    "WHERE t.id=%s ORDER BY i.created_at,i.id LIMIT 11"
                ).format(sql.Identifier(schema), sql.Identifier(schema)), (receiver,))
                joined_rows = cursor.fetchall()
        provider_variant_is_opencode_v1 = bool(joined_rows) and all(
            row[0] == "opencode-v1" for row in joined_rows
        )
        rows = [row[1:] for row in joined_rows if row[1] is not None]
        statuses = [
            row[4] if row[4] in {"pending", "delivered", "reconcile", "failed"} else "other"
            for row in rows
        ]
        private_rows = []
        shape = True
        for row in rows:
            if (len(row) != 6 or type(row[0]) is not int or row[0] <= 0
                    or any(type(item) is not str for item in row[1:5])
                    or not isinstance(row[5], datetime)):
                shape = False
                continue
            private_rows.append((row[0], *row[1:5], row[5].isoformat()))
        sender_receiver_match = (
            shape and len(private_rows) == len(rows) and len(rows) == len(expected)
            and all(row[1] == sender and row[2] == receiver for row in private_rows)
        )
        expected_messages_match = (sender_receiver_match
                                   and sorted(row[3] for row in private_rows) == sorted(expected))
        def api_signature(items):
            if api_rows_observed is not True or not isinstance(items, list):
                return None
            result = []
            for item in items:
                if (not isinstance(item, dict)
                        or set(item) != {"id", "sender_id", "receiver_id", "message",
                                         "status", "created_at"}
                        or type(item.get("id")) is not int or item["id"] <= 0
                        or type(item.get("sender_id")) is not str
                        or type(item.get("receiver_id")) is not str
                        or type(item.get("message")) is not str
                        or type(item.get("status")) is not str
                        or type(item.get("created_at")) is not str):
                    return None
                try:
                    created_at = datetime.fromisoformat(item["created_at"])
                except ValueError:
                    return None
                if created_at.isoformat() != item["created_at"]:
                    return None
                result.append((item["id"], item["sender_id"], item["receiver_id"],
                               item["message"], item["status"], item["created_at"]))
            return sorted(result)
        api_signature_rows = api_signature(api_rows)
        api_pg_rows_match = (
            shape and len(private_rows) == len(rows) and api_signature_rows is not None
            and api_signature_rows == sorted(private_rows)
        )
        emit({"status": "observed", "target_identity_match": True,
              "target_identity_status": "matched",
              "row_count": min(len(rows), 11),
              "sender_receiver_match": bool(sender_receiver_match),
              "expected_messages_match": bool(expected_messages_match),
              "api_pg_rows_match": bool(api_pg_rows_match),
              "provider_variant_is_opencode_v1": provider_variant_is_opencode_v1,
              "statuses": statuses[:11]})
    except Exception:
        unavailable(identity_status=identity_status)

main()
"""
    output = _run_bounded_private_child(
        [sys.executable, "-I", "-B", "-c", child_source],
        context,
        stop,
        max_output_bytes=_INBOX_FAILURE_SNAPSHOT_MAX_BYTES,
    )
    value = json.loads(output)
    required = {
        "status",
        "target_identity_match",
        "target_identity_status",
        "row_count",
        "sender_receiver_match",
        "expected_messages_match",
        "api_pg_rows_match",
        "provider_variant_is_opencode_v1",
        "statuses",
    }
    if type(value) is not dict or set(value) != required:
        raise ValueError("failure_snapshot_child_shape")
    if value["status"] not in {"observed", "unavailable", "ownership_mismatch", "invalid_context"}:
        raise ValueError("failure_snapshot_child_shape")
    if value["target_identity_status"] not in {"matched", "mismatch", "unavailable"}:
        raise ValueError("failure_snapshot_child_shape")
    if any(
        type(value[key]) is not bool
        for key in (
            "target_identity_match",
            "sender_receiver_match",
            "expected_messages_match",
            "api_pg_rows_match",
            "provider_variant_is_opencode_v1",
        )
    ):
        raise ValueError("failure_snapshot_child_shape")
    if type(value["row_count"]) is not int or not 0 <= value["row_count"] <= 11:
        raise ValueError("failure_snapshot_child_shape")
    if (
        type(value["statuses"]) is not list
        or len(value["statuses"]) > 11
        or any(item not in _INBOX_FAILURE_STATUSES | {"other"} for item in value["statuses"])
    ):
        raise ValueError("failure_snapshot_child_shape")
    return value


def _capture_failure_inbox_snapshot(index, message, phase, readers):
    """Record queue and receiver facts before the ACK gate triggers cleanup."""
    started = time.monotonic()
    deadline = started + _INBOX_FAILURE_SNAPSHOT_WORK_SECONDS
    snapshot = {
        "diagnostic_only": True,
        "status": "unavailable",
        "elapsed_seconds": 0.0,
        "within_deadline": False,
        "receiver": {
            "status": "unavailable",
            "owned": False,
            "source": "unavailable",
            "http_status": None,
            "terminal_status": "unavailable",
            "turn_sequence": None,
            "turn_completed": None,
            "runtime_id_present": None,
            "runtime_incarnation_id_present": None,
            "native_swarm_pending": None,
            "fresh_read_status": "not_attempted",
            "fresh_http_status": None,
            "snapshot_elapsed_seconds": None,
            "phase_elapsed_seconds": None,
        },
        "api_inbox": {
            "status": "unavailable",
            "http_status": None,
            "row_count": 0,
            "valid_id_count": 0,
            "sender_receiver_match": False,
            "expected_messages_match": False,
            "statuses": [],
        },
        "postgres_inbox": {
            "status": "unavailable",
            "target_identity_match": False,
            "target_identity_status": "unavailable",
            "row_count": 0,
            "sender_receiver_match": False,
            "expected_messages_match": False,
            "api_pg_rows_match": False,
            "provider_variant_is_opencode_v1": False,
            "statuses": [],
        },
    }
    phase["failure_snapshot"] = snapshot
    api_rows = []
    api_rows_observed = False
    try:
        if (
            type(index) is not int
            or index not in (0, 1)
            or not isinstance(message, str)
            or pgentered is not True
            or not isinstance(terminals, dict)
            or set(terminals) != {"sender", "receiver"}
            or sender_id != terminals["sender"].get("id")
            or receiver_id != terminals["receiver"].get("id")
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sender_id) is None
            or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", receiver_id) is None
            or message != (PRE_RESTART_MESSAGE, POST_RESTART_MESSAGE)[index]
        ):
            snapshot["status"] = "invalid_context"
            return snapshot

        target = getattr(targets, "main", None)
        schema = getattr(target, "schema", None)
        store_id = getattr(target, "store_id", None)
        scope = getattr(target, "scope", None)
        target_identity = getattr(target, "identity", None)
        if (
            target is None
            or scope != "main"
            or not isinstance(schema, str)
            or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema) is None
            or schema in {"public", "information_schema"}
            or schema.startswith("pg_")
            or not isinstance(store_id, str)
            or re.fullmatch(
                r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
                store_id,
            )
            is None
            or target_identity
            != (
                getattr(target, "host", None),
                getattr(target, "port", None),
                getattr(target, "database", None),
                schema,
                store_id,
            )
        ):
            snapshot["status"] = "invalid_context"
            return snapshot
        api_url = server.url
        parsed = urlsplit(api_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or not parsed.port
        ):
            snapshot["status"] = "invalid_context"
            return snapshot

        try:
            reader_scope = readers._check(receiver_id, min(deadline, time.monotonic() + 1.0))
        except Exception:
            snapshot["status"] = "invalid_context"
            return snapshot

        cached = phase.get("_failure_receiver_state")
        if time.monotonic() < deadline:
            state_stop = min(deadline, time.monotonic() + 1.0)
            fresh_read_error = False
            fresh_http_status = None
            try:
                state = readers._json(receiver_id, state_stop, 65537)
                if time.monotonic() > state_stop:
                    raise TimeoutError("failure_snapshot_deadline")
                if type(reader_scope) is not dict or (
                    state.get("id"),
                    state.get("session_name"),
                    state.get("name"),
                ) != (receiver_id, reader_scope.get("session"), reader_scope.get("pane")):
                    read_status = "identity_mismatch"
                    fresh_read_error = True
                else:
                    snapshot["receiver"] = {
                        "status": "observed",
                        "owned": True,
                        "source": "failure_snapshot",
                        "http_status": 200,
                        "terminal_status": (
                            state.get("status")
                            if state.get("status") in _INBOX_FAILURE_TERMINAL_STATES
                            else "other"
                        ),
                        "turn_sequence": _bounded_count(state.get("turn_sequence")),
                        "turn_completed": _bounded_count(state.get("turn_completed")),
                        "runtime_id_present": type(state.get("runtime_id")) is str
                        and bool(state.get("runtime_id")),
                        "runtime_incarnation_id_present": type(state.get("runtime_incarnation_id"))
                        is str
                        and bool(state.get("runtime_incarnation_id")),
                        "native_swarm_pending": (
                            state.get("native_swarm_pending")
                            if type(state.get("native_swarm_pending")) is bool
                            else None
                        ),
                        "fresh_read_status": "observed",
                        "fresh_http_status": 200,
                        "snapshot_elapsed_seconds": round(time.monotonic() - started, 3),
                        "phase_elapsed_seconds": None,
                    }
            except HTTPStatusError as error:
                read_status = "http_error"
                fresh_http_status = error.status_code
                fresh_read_error = True
            except Exception:
                read_status = "deadline" if time.monotonic() >= state_stop else "unavailable"
                fresh_read_error = True
            if fresh_read_error:
                if type(cached) is dict and cached.get("bound") is True:
                    snapshot["receiver"] = {
                        "status": "observed",
                        "owned": True,
                        "source": "phase_poll_fallback",
                        "fresh_read_status": read_status,
                        "fresh_http_status": fresh_http_status,
                        "http_status": cached.get("http_status"),
                        "terminal_status": (
                            cached.get("status")
                            if cached.get("status") in _INBOX_FAILURE_TERMINAL_STATES
                            else "other"
                        ),
                        "turn_sequence": _bounded_count(cached.get("turn_sequence")),
                        "turn_completed": _bounded_count(cached.get("turn_completed")),
                        "runtime_id_present": None,
                        "runtime_incarnation_id_present": None,
                        "native_swarm_pending": None,
                        "snapshot_elapsed_seconds": round(time.monotonic() - started, 3),
                        "phase_elapsed_seconds": cached.get("phase_elapsed_seconds"),
                    }
                else:
                    snapshot["receiver"]["status"] = read_status
                    snapshot["receiver"]["fresh_read_status"] = read_status
                    snapshot["receiver"]["fresh_http_status"] = fresh_http_status

        api_stop = min(deadline, time.monotonic() + 2.0)
        try:
            readers._check(receiver_id, api_stop)
            body = http_json_bytes(
                api_url,
                "/terminals/" + receiver_id + "/inbox/messages?limit=10",
                api_stop,
                65537,
            )
            readers._check(receiver_id, api_stop)
            if time.monotonic() > api_stop:
                raise TimeoutError("failure_snapshot_deadline")
            snapshot["api_inbox"]["http_status"] = 200
            api_rows = json.loads(body)
            summary = _failure_snapshot_rows_summary(
                api_rows,
                [
                    expected_persisted_message(item, sender_id)
                    for item in (PRE_RESTART_MESSAGE, POST_RESTART_MESSAGE)[: index + 1]
                ],
                sender_id,
                receiver_id,
            )
            snapshot["api_inbox"].update(summary)
            api_rows_observed = summary["status"] == "observed"
        except HTTPStatusError as error:
            snapshot["api_inbox"]["status"] = "http_error"
            snapshot["api_inbox"]["http_status"] = error.status_code
        except Exception:
            snapshot["api_inbox"]["status"] = (
                "deadline" if time.monotonic() >= api_stop else "unavailable"
            )

        if time.monotonic() < deadline:
            params = target.connection_parameters()
            context = {
                "connection_parameters": params,
                "schema": schema,
                "store_id": store_id,
                "scope": scope,
                "sender_id": sender_id,
                "receiver_id": receiver_id,
                "expected_messages": [
                    expected_persisted_message(item, sender_id)
                    for item in (PRE_RESTART_MESSAGE, POST_RESTART_MESSAGE)[: index + 1]
                ],
                "api_rows": api_rows if type(api_rows) is list else [],
                "api_rows_observed": api_rows_observed,
            }
            pg_stop = min(deadline, time.monotonic() + 5.0)
            try:
                snapshot["postgres_inbox"] = _read_owned_postgres_inbox_snapshot(context, pg_stop)
            except TimeoutError:
                snapshot["postgres_inbox"]["status"] = "deadline"
            except Exception:
                snapshot["postgres_inbox"]["status"] = (
                    "deadline" if time.monotonic() >= pg_stop else "unavailable"
                )
        else:
            snapshot["postgres_inbox"]["status"] = "deadline"

        component_statuses = [
            snapshot[name]["status"] for name in ("receiver", "api_inbox", "postgres_inbox")
        ]
        observed = component_statuses.count("observed")
        receiver_is_current = (
            snapshot["receiver"]["status"] == "observed"
            and snapshot["receiver"]["fresh_read_status"] == "observed"
        )
        if observed == 3 and receiver_is_current:
            snapshot["status"] = "complete"
        elif time.monotonic() >= deadline or "deadline" in component_statuses:
            snapshot["status"] = "deadline"
        elif observed:
            snapshot["status"] = "partial"
        else:
            snapshot["status"] = "unavailable"
        return snapshot
    except Exception:
        snapshot["status"] = "deadline" if time.monotonic() >= deadline else "unavailable"
        return snapshot
    finally:
        elapsed = max(0.0, time.monotonic() - started)
        snapshot["elapsed_seconds"] = round(elapsed, 3)
        snapshot["within_deadline"] = elapsed <= _INBOX_FAILURE_SNAPSHOT_GRACE_SECONDS
        if not snapshot["within_deadline"]:
            snapshot["status"] = "deadline"


class _GateError(RuntimeError):
    pass


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _guard_contextmanager_finalbody(
    function_source, *, function_name, guarded_name, guard_name, namespace, filename
):
    try:
        parsed = ast.parse(function_source, filename=filename)
    except SyntaxError as error:
        raise _GateError("postgres_cleanup_helper_source_invalid") from error
    functions = [
        node
        for node in parsed.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    ]
    if len(functions) != 1 or not isinstance(functions[0], ast.FunctionDef):
        raise _GateError("postgres_cleanup_helper_function_shape_changed")
    function = functions[0]
    finalizers = [
        node for node in ast.walk(function) if isinstance(node, ast.Try) and node.finalbody
    ]
    if len(finalizers) != 1:
        raise _GateError("postgres_cleanup_helper_finalizer_shape_changed")
    finalizer = finalizers[0]
    if any(
        isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == guard_name
        for node in finalizer.finalbody
    ):
        raise _GateError("postgres_cleanup_helper_already_guarded")
    finalizer.finalbody = [
        ast.If(test=ast.Name(id=guard_name, ctx=ast.Load()), body=finalizer.finalbody, orelse=[])
    ]
    function.name = guarded_name
    derived = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(derived)
    derived_source = ast.unparse(derived)
    derived_sha256 = hashlib.sha256(derived_source.encode("utf-8")).hexdigest()
    try:
        exec(compile(derived, filename, "exec"), namespace)
        guarded_function = namespace.pop(guarded_name)
    except Exception as error:
        raise _GateError("postgres_cleanup_helper_derivation_failed") from error
    if not callable(guarded_function):
        raise _GateError("postgres_cleanup_helper_derivation_failed")
    return guarded_function, derived_sha256


def _install_guarded_postgres_cleanup(fixtures_module):
    expected_path = (repo / "test/postgres/fixtures.py").resolve(strict=True)
    fixture_path = Path(getattr(fixtures_module, "__file__", "")).resolve(strict=True)
    if fixture_path != expected_path:
        raise _GateError("postgres_fixture_source_binding_invalid")
    fixture_sha256 = _sha256(fixture_path)
    if fixture_sha256 != expected_pg_fixture_sha256:
        raise _GateError("postgres_fixture_source_pin_mismatch")
    source_function = getattr(fixtures_module, "_isolated_live_targets", None)
    if source_function is None:
        raise _GateError("postgres_fixture_cleanup_helper_missing")
    try:
        source = inspect.getsource(source_function)
    except (OSError, TypeError) as error:
        raise _GateError("postgres_fixture_cleanup_helper_unavailable") from error
    namespace = fixtures_module.__dict__
    namespace["_cao_private_pg_teardown_permitted"] = False
    guarded, derived_sha256 = _guard_contextmanager_finalbody(
        source,
        function_name="_isolated_live_targets",
        guarded_name="_isolated_live_targets_private_guarded",
        guard_name="_cao_private_pg_teardown_permitted",
        namespace=namespace,
        filename=str(fixture_path),
    )
    if derived_sha256 != expected_pg_guard_sha256:
        raise _GateError("postgres_fixture_guard_derivation_pin_mismatch")
    wrapped = getattr(guarded, "__wrapped__", None)
    if wrapped is None or wrapped.__globals__ is not namespace:
        raise _GateError("postgres_fixture_guard_namespace_mismatch")
    fixtures_module._isolated_live_targets = guarded
    return fixture_sha256, derived_sha256


def _enter_guarded_postgres_context(context, fixtures_module):
    fixtures_module._cao_private_pg_teardown_permitted = True
    try:
        return context.__enter__()
    finally:
        fixtures_module._cao_private_pg_teardown_permitted = False


def _exit_guarded_postgres_context(context, fixtures_module):
    fixtures_module._cao_private_pg_teardown_permitted = True
    try:
        return context.__exit__(None, None, None)
    finally:
        fixtures_module._cao_private_pg_teardown_permitted = False


def _api_launches_confirmed_dead(records, *, attempted_roles, journey):
    roles = [record.get("role") for record in records]
    if set(roles) != attempted_roles or len(roles) != len(set(roles)):
        return False
    for record in records:
        identity = record.get("identity")
        if identity is None:
            return False
        try:
            if journey._process_identity_alive(identity):
                return False
        except Exception:
            return False
    return True


def _capture_api_identity(api_server, message_live, journey):
    process = message_live._api_process(api_server)
    info = journey._process_info(process.pid)
    if info is None or info[0].uid != os.getuid() or info[2] == "Z":
        raise _GateError("api_process_identity_unavailable")
    return info[0]


def _accept_last_receipt(
    status, output, response_returned_at, deadline, state_observation, expected_sequence
):
    return (
        status == 200
        and isinstance(output, str)
        and "Validado ñ, 中文." in output
        and response_returned_at <= deadline
        and type(expected_sequence) is int
        and expected_sequence > 0
        and isinstance(state_observation, dict)
        and state_observation.get("terminal_http_status") == 200
        and state_observation.get("terminal_status") == "completed"
        and state_observation.get("terminal_turn_sequence") == expected_sequence
        and state_observation.get("terminal_turn_completed") == expected_sequence
        and state_observation.get("terminal_sample_within_deadline") is True
    )


def _graceful_seals_seen_by_deadline(seals_present, observed_at, deadline):
    return seals_present is True and type(observed_at) in (int, float) and observed_at <= deadline


def _postgres_runtime_identity_matches(
    metadata, expected_container_id, expected_image_id, inspect_output
):
    if not isinstance(metadata, dict) or not isinstance(inspect_output, str):
        return False
    container_id = metadata.get("container_id")
    if not isinstance(container_id, str) or container_id != expected_container_id:
        return False
    return inspect_output.split() == [expected_container_id, expected_image_id, "true"]


def _final_success(receipt):
    required_true = [
        "explicit_go_confirmed",
        "postgres_guard_binding_exact",
        "postgres_identity_verified",
        "postgres_container_id_matches",
        "postgres_image_id_matches",
        "both_phases_passed",
        "agui_captures_stopped_cleanly",
        "agui_capture_threads_confirmed_dead_before_postgres_teardown",
        "agui_capture_phases_clean",
        "graceful_exit_http_success",
        "graceful_observer_seals_within_10s",
        "sender_operation_permissions_exact",
        "receiver_operation_permissions_exact",
        "two_isolated_native_roots",
        "credentials_isolated",
        "filters_exact",
        "native_identities_survive_restart",
        "pg_message_identity_survives_restart",
        "api_message_identity_survives_restart",
        "exact_native_catalogs",
        "exact_native_successful_calls",
        "observers_complete",
        "api_cleanup",
        "api_launches_confirmed_dead_before_postgres_teardown",
        "postgres_cleanup",
        "tmux_processes_clean",
        "tmux_processes_confirmed_dead_before_postgres_teardown",
        "private_state_removed",
        "operator_auth_unchanged",
        "source_unchanged",
        "runner_unchanged",
        "binary_unchanged",
        "source_head_pinned_at_end",
        "source_runtime_tree_clean_at_end",
    ]
    return (
        "error_type" not in receipt
        and type(receipt.get("private_roots_after_session_delete")) is int
        and receipt.get("private_roots_after_session_delete") == 0
        and receipt.get("cleanup_session_delete_http_status") == 200
        and receipt.get("postgres_autostarted") is False
        and receipt.get("postgres_cleanup_deferred") is False
        and receipt.get("cleanup_failure_codes") == []
        and all(receipt.get(key) is True for key in required_true)
    )


def _observe_state(api_url, terminal_id, previous_generation, deadline, phase_started):
    sample = {
        "elapsed_seconds": round(time.monotonic() - phase_started, 2),
        "turn_http_status": None,
        "turn_state": "unavailable",
        "generation_present": False,
        "generation_stable": False,
        "reason_present": False,
        "turn_attempts": None,
        "allowed_actions": [],
        "turn_error_present": False,
        "terminal_http_status": None,
        "terminal_status": "unavailable",
        "terminal_turn_sequence": None,
        "terminal_turn_completed": None,
        "terminal_sample_within_deadline": False,
        "terminal_error_present": False,
    }
    current_generation = None
    try:
        remaining = deadline - time.monotonic()
        if remaining > 0.05:
            response = requests.get(
                api_url + "/terminals/" + terminal_id + "/turn", timeout=min(1.0, remaining)
            )
            sample["turn_http_status"] = response.status_code
            if response.status_code == 200:
                value = response.json()
                if isinstance(value, dict):
                    state = value.get("state")
                    sample["turn_state"] = (
                        state
                        if isinstance(state, str)
                        and state
                        in {"none", "pending", "verifying", "verified", "reconcile", "cancelling"}
                        else "other"
                    )
                    reason = value.get("reason")
                    sample["reason_present"] = isinstance(reason, str) and bool(reason)
                    sample["turn_attempts"] = _bounded_count(value.get("attempts"))
                    actions = value.get("allowed_actions")
                    sample["allowed_actions"] = [
                        action
                        for action in ("verify", "cancel")
                        if isinstance(actions, list) and action in actions
                    ]
                    generation = value.get("generation")
                    if isinstance(generation, str) and re.fullmatch(r"[0-9a-f]{32}", generation):
                        sample["generation_present"] = True
                        sample["generation_stable"] = (
                            previous_generation is not None and generation == previous_generation
                        )
                        current_generation = generation
                else:
                    sample["turn_error_present"] = True
    except Exception:
        sample["turn_error_present"] = True
    try:
        remaining = deadline - time.monotonic()
        if remaining > 0.05:
            response = requests.get(
                api_url + "/terminals/" + terminal_id, timeout=min(1.0, remaining)
            )
            sample["terminal_http_status"] = response.status_code
            terminal_response_returned_at = time.monotonic()
            sample["terminal_sample_within_deadline"] = terminal_response_returned_at <= deadline
            if response.status_code == 200:
                value = response.json()
                if isinstance(value, dict):
                    status = value.get("status")
                    sample["terminal_status"] = (
                        status
                        if status
                        in {
                            "unknown",
                            "idle",
                            "processing",
                            "completed",
                            "waiting_user_answer",
                            "waiting_quota",
                            "reconcile",
                            "error",
                        }
                        else "other"
                    )
                    sample["terminal_turn_sequence"] = _bounded_count(value.get("turn_sequence"))
                    sample["terminal_turn_completed"] = _bounded_count(value.get("turn_completed"))
                else:
                    sample["terminal_error_present"] = True
    except Exception:
        sample["terminal_error_present"] = True
    return sample, current_generation


def _read_terminal_completion(api_url, terminal_id, deadline, phase_started):
    sample = {
        "elapsed_seconds": round(time.monotonic() - phase_started, 2),
        "terminal_http_status": None,
        "terminal_status": "unavailable",
        "terminal_turn_sequence": None,
        "terminal_turn_completed": None,
        "terminal_sample_within_deadline": False,
        "terminal_error_present": False,
    }
    remaining = deadline - time.monotonic()
    if remaining <= 0.01:
        return sample
    try:
        response = requests.get(api_url + "/terminals/" + terminal_id, timeout=min(1.0, remaining))
        sample["terminal_http_status"] = response.status_code
        returned_at = time.monotonic()
        sample["terminal_sample_within_deadline"] = returned_at <= deadline
        if response.status_code == 200:
            value = response.json()
            if isinstance(value, dict):
                status = value.get("status")
                sample["terminal_status"] = (
                    status
                    if status
                    in {
                        "unknown",
                        "idle",
                        "processing",
                        "completed",
                        "waiting_user_answer",
                        "waiting_quota",
                        "reconcile",
                        "error",
                    }
                    else "other"
                )
                sample["terminal_turn_sequence"] = _bounded_count(value.get("turn_sequence"))
                sample["terminal_turn_completed"] = _bounded_count(value.get("turn_completed"))
            else:
                sample["terminal_error_present"] = True
    except Exception:
        sample["terminal_error_present"] = True
    return sample


def _private_agui_type(module, requester):
    namespace = dict(vars(module))
    request_module = namespace["requests"]
    namespace["requests"] = SimpleNamespace(
        get=requester.get,
        RequestException=request_module.RequestException,
        Response=request_module.Response,
    )
    exec(
        compile(inspect.getsource(module.AguiCapture), "<source-bound-private-agui>", "exec"),
        namespace,
    )
    return namespace["AguiCapture"]


def _read_durable_turns(stop):
    import psycopg
    from psycopg import sql

    target = targets.main
    if stop - time.monotonic() < 2 or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", target.schema):
        raise RuntimeError("restart_durable_receipt_unavailable")
    params = target.connection_parameters()
    params.update(
        connect_timeout=1,
        options="-c statement_timeout=500 -c lock_timeout=500 -c default_transaction_read_only=on",
        application_name="t101-restart-readonly",
    )
    with psycopg.connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "SELECT terminal_id,generation,receipt_sha256,result_sha256,phase "
                    "FROM {}.terminal_turn_receipts WHERE terminal_id=ANY(%s) LIMIT 3"
                ).format(sql.Identifier(target.schema)),
                ([terminals[role]["id"] for role in ("sender", "receiver")],),
            )
            rows = cursor.fetchall()
    by_id = {
        row[0]: dict(zip(("generation", "receipt_sha256", "result_sha256", "phase"), row[1:]))
        for row in rows
    }
    if (
        len(rows) != 2
        or set(by_id) != {terminals[role]["id"] for role in ("sender", "receiver")}
        or time.monotonic() >= stop
    ):
        raise RuntimeError("restart_durable_receipt_unavailable")
    result = {role: by_id[terminals[role]["id"]] for role in ("sender", "receiver")}
    if not acceptance.durable_turns_exact(result, result):
        raise RuntimeError("restart_durable_receipt_unavailable")
    return result


def _wait_native_ready(readers, stop, phase):
    started = time.monotonic()
    streaks = {role: 0 for role in ("receiver", "sender")}
    previous = {}
    while time.monotonic() < stop:
        states = {}
        for role in streaks:
            read_stop = min(stop, time.monotonic() + 1)
            if time.monotonic() >= read_stop:
                raise RuntimeError("receiver_baseline_unavailable")
            state = readers.read_state(terminals[role]["id"], read_stop)
            if time.monotonic() >= read_stop:
                raise RuntimeError("receiver_baseline_unavailable")
            valid = time.monotonic() < stop and acceptance.settled_local_state(state)
            counters = (
                state.get("status"),
                state.get("turn_sequence"),
                state.get("turn_completed"),
            )
            streaks[role] = (
                streaks[role] + 1 if valid and previous.get(role) == counters else 1 if valid else 0
            )
            previous[role] = counters
            states[role] = state
            phase[role + "_readiness_samples"] = streaks[role]
            phase[role + "_baseline_ready"] = valid
            phase[role + "_baseline_status"] = (
                state.get("status")
                if state.get("status")
                in (
                    "idle",
                    "completed",
                    "unknown",
                    "processing",
                    "reconcile",
                    "error",
                    "waiting_user_answer",
                    "waiting_quota",
                )
                else "other"
            )
        phase["readiness_seconds"] = time.monotonic() - started
        if time.monotonic() < stop and all(count >= 3 for count in streaks.values()):
            return states
        remaining = stop - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.4, remaining))
    raise RuntimeError("receiver_baseline_unavailable")


def _collect_message_phase(index, message, phase, readers):
    coordinator = None
    try:
        readiness_stop = restart_readiness_stop if index else time.monotonic() + 2
        ready = _wait_native_ready(readers, readiness_stop, phase)
        baseline = ready["receiver"]
        receiver_before = baseline.get("turn_sequence")
        assert (
            type(receiver_before) is int
            and receiver_before >= 0
            and baseline.get("status") in {"idle", "completed"}
        ), "receiver_baseline_unavailable"
        if index:
            durable_after = _read_durable_turns(min(readiness_stop, time.monotonic() + 3))
            exact = acceptance.durable_turns_exact(restart_receipts_before, durable_after)
            for role in ("receiver", "sender"):
                turn = readers.read_turn(
                    terminals[role]["id"], min(readiness_stop, time.monotonic() + 1)
                )
                exact = exact and (
                    turn.get("generation") == durable_after[role]["generation"]
                    and turn.get("state") == "verified"
                    and type(turn.get("attempts")) is int
                    and turn["attempts"] == 0
                    and turn.get("allowed_actions") == []
                )
            exact = exact and time.monotonic() < readiness_stop
            phase["restart_durable_receipts_exact"] = exact
            receipt["durable_turn_identity_survives_restart"] = exact
            if exact is not True:
                raise RuntimeError("restart_durable_receipt_mismatch")
        phase["receiver_sequence_before"] = receiver_before
        t = time.monotonic()
        deadline = t + 75.0
        phase["deadline_seconds_from_input_start"] = 75.0
        discovery = (
            "Primero llama exactamente una vez a cao_mcp_server_list_siblings para descubrir tu compañero. "
            if index == 0
            else ""
        )
        prompt = (
            discovery
            + "Llama exactamente una vez a cao_mcp_server_send_message con receiver_id "
            + receiver_id
            + " y message "
            + json.dumps(message, ensure_ascii=False)
            + ". No dupliques ni cambies el mensaje. Tras éxito responde con la línea exacta: Validado ñ, 中文. Después cumple el contrato de recibo de este turno."
        )
        response = requests.post(
            server.url + "/terminals/" + sender_id + "/input",
            params={"message": prompt},
            timeout=min(30, deadline - time.monotonic()),
        )
        assert response.status_code == 200
        phase["input_returned_elapsed_seconds"] = round(time.monotonic() - t, 3)
        input_turn_sequence = response.json().get("turn_sequence")
        assert (
            type(input_turn_sequence) is int and input_turn_sequence > 0
        ), "input_turn_sequence_unavailable"
        phase["input_turn_sequence"] = input_turn_sequence
        scopes = {
            "sender": RoleScope(sender_id, input_turn_sequence, "v1-sender"),
            "receiver": RoleScope(receiver_id, receiver_before + 1, "v1-receiver"),
        }
        coordinator = NativePhaseObserver(
            run_root=root,
            cao_home=caohome,
            target=targets.main,
            scopes=scopes,
            phase_started=t,
            read_state=readers.read_state,
            read_output=readers.read_output,
            capture_viewport=readers.capture_viewport,
        )
        phase.update(
            receiver_ack_exact=False,
            receiver_ack_within_deadline=False,
            receiver_turn_completed_once=False,
            last_receipt_unicode_verified=False,
            sender_completed=False,
        )
        while time.monotonic() < deadline:
            phase.update(coordinator.tick())
            for role, data in terminals.items():
                if time.monotonic() >= deadline:
                    break
                try:
                    state = readers.read_state(data["id"], min(deadline, time.monotonic() + 1.0))
                    if role == "receiver":
                        _record_failure_receiver_observation(
                            phase, state, phase_elapsed_seconds=time.monotonic() - t
                        )
                    if not acceptance.turn_exact(state, scopes[role].expected_turn):
                        continue
                    text = readers.read_output(
                        data["id"], min(deadline, time.monotonic() + 1.0), max_bytes=65537
                    )
                    returned = time.monotonic()
                    if returned > deadline:
                        continue
                    if role == "sender":
                        phase.update(
                            sender_completed=True,
                            sender_sequence_after=state["turn_sequence"],
                            sender_completed_after=state["turn_completed"],
                        )
                        observation = {
                            "terminal_http_status": 200,
                            "terminal_status": state["status"],
                            "terminal_turn_sequence": state["turn_sequence"],
                            "terminal_turn_completed": state["turn_completed"],
                            "terminal_sample_within_deadline": True,
                        }
                        if _accept_last_receipt(
                            200, text, returned, deadline, observation, input_turn_sequence
                        ):
                            phase["last_receipt_unicode_verified"] = True
                    elif acceptance.ack_exact(text):
                        after = readers.read_state(
                            data["id"], min(deadline, time.monotonic() + 1.0)
                        )
                        if time.monotonic() <= deadline and acceptance.turn_exact(
                            after, receiver_before + 1
                        ):
                            phase.update(
                                receiver_ack_exact=True,
                                receiver_ack_within_deadline=True,
                                receiver_turn_completed_once=True,
                                receiver_sequence_after=after["turn_sequence"],
                                receiver_completed_after=after["turn_completed"],
                            )
                except Exception:
                    phase[role + "_acceptance_read_unavailable"] = True
            snapshot = coordinator.snapshot()
            if (
                all(value != "pending" for value in snapshot["native_observation_states"].values())
                and phase["sender_completed"]
                and phase["receiver_turn_completed_once"]
            ):
                break
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.25, remaining))
        phase["model_seconds"] = time.monotonic() - t
        phase.update(coordinator.finish())
        if phase.get("receiver_ack_exact") is not True:
            _capture_failure_inbox_snapshot(index, message, phase, readers)
    finally:
        if coordinator is not None:
            phase.update(coordinator.abort())
        phase.pop("_failure_receiver_state", None)
        readers.close()


try:
    admit("initial")
    sys.path.insert(0, str(repo))
    sys.path.insert(0, str(repo / "src"))
    import test.fixtures.cao_server as cao_server_fixture

    fixture_requests_module = cao_server_fixture.requests
    cao_server_fixture.requests = SimpleNamespace(
        get=http_session.get,
        ConnectionError=fixture_requests_module.ConnectionError,
        Timeout=fixture_requests_module.Timeout,
    )
    from test.fixtures.cao_server import _pick_free_port, _start_cao_server
    from test.live_provider import opencode_interaction_journey as journey
    from test.postgres import fixtures as postgres_fixtures
    from test.postgres.fixtures import _discover_postgres_service, isolated_live_targets

    import frontmatter

    from cli_agent_orchestrator.clients.postgres.connection import connect
    from cli_agent_orchestrator.clients.tmux import TmuxClient
    from cli_agent_orchestrator.providers.opencode_cli import (
        _copy_private_state_bundle,
        _copy_private_state_file,
    )

    sys.path.insert(0, str(repo / "test/live_provider"))
    import message_api_restart_live as message_live

    AguiCapture = _private_agui_type(message_live, http_session)
    from message_api_restart import (
        POST_RESTART_MESSAGE,
        PRE_RESTART_MESSAGE,
        compare_inbox_row,
        expected_persisted_message,
    )

    receipt["postgres_guard_fixture_sha256"], receipt["postgres_guard_derived_sha256"] = (
        _install_guarded_postgres_cleanup(postgres_fixtures)
    )
    service = _discover_postgres_service()
    receipt["postgres_autostarted"] = False
    postgres_container_id = service.metadata.get("container_id")
    receipt["postgres_container_id_matches"] = postgres_container_id == expected_pg_container_id
    if not receipt["postgres_container_id_matches"]:
        raise RuntimeError("postgres_identity_mismatch")
    postgres_inspect_output = subprocess.check_output(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Id}} {{.Image}} {{.State.Running}}",
            postgres_container_id,
        ],
        text=True,
        timeout=10,
    )
    postgres_inspect_tokens = postgres_inspect_output.split()
    receipt["postgres_image_id_matches"] = (
        len(postgres_inspect_tokens) == 3
        and postgres_inspect_tokens[0] == expected_pg_container_id
        and postgres_inspect_tokens[1] == expected_pg_image_id
    )
    receipt["postgres_identity_verified"] = _postgres_runtime_identity_matches(
        service.metadata, expected_pg_container_id, expected_pg_image_id, postgres_inspect_output
    )
    if not (receipt["postgres_image_id_matches"] and receipt["postgres_identity_verified"]):
        raise RuntimeError("postgres_identity_mismatch")
    home = root / "home"
    home.mkdir(mode=0o700)
    caohome = home / ".aws/cli-agent-orchestrator"
    config = home / ".aws/opencode"
    config.mkdir(mode=0o700, parents=True)
    model = receipt["model"]
    (config / "opencode.json").write_text(
        json.dumps({"model": model, "permission": {"*": "deny"}, "mcp": {}}) + "\n"
    )
    api_port = _pick_free_port()
    observer = raw / "observer"
    observer.mkdir(mode=0o700)
    original = (repo / "test/live_provider/opencode_mcp_observer.py").read_text()
    a = original.index("INTERACTION_TOOLS =")
    b = original.index("_NONCE_RE =", a)
    adapted = (
        original[:a]
        + "INTERACTION_TOOLS = frozenset({'send_message','list_siblings'})\n"
        + original[b:]
    )
    adapted = adapted.replace(
        "return install_protocol_observer(low_level_server, Path(raw_path), nonce)",
        "terminal = selected_environment.get('CAO_TERMINAL_ID', '')\n    if not re.fullmatch(r'[0-9a-f]{8}', terminal):\n        raise RuntimeError('mcp_observer_terminal_binding_invalid')\n    return install_protocol_observer(low_level_server, Path(raw_path).with_name(Path(raw_path).name+'-'+terminal), nonce)",
    )
    (observer / "opencode_mcp_observer.py").write_text(adapted)
    shutil.copyfile(repo / "test/live_provider/sitecustomize.py", observer / "sitecustomize.py")
    receipt["private_observer_sha256"] = hashlib.sha256(adapted.encode()).hexdigest()
    nonce = os.urandom(16).hex()
    baseobservation = raw / "mcp.events"
    observer_spec = importlib.util.spec_from_file_location(
        "t060_private_observer", observer / "opencode_mcp_observer.py"
    )
    if observer_spec is None or observer_spec.loader is None:
        raise RuntimeError("mcp_observer_import_failed")
    observer_module = importlib.util.module_from_spec(observer_spec)
    observer_spec.loader.exec_module(observer_module)
    childenv = {
        "HOME": str(home),
        "CAO_HOME_DIR": str(caohome),
        "CAO_API_HOST": "127.0.0.1",
        "CAO_API_PORT": str(api_port),
        "CAO_MCP_APPS_ENABLED": "0",
        "CAO_ENABLE_SENDER_ID_INJECTION": "true",
        "PYTHONPATH": os.pathsep.join(
            [str(observer), str(repo / "src"), str(repo / "test/live_provider"), str(repo)]
        ),
        "CAO_OPENCODE_MCP_OBSERVER": "1",
        "CAO_OPENCODE_MCP_OBSERVER_PATH": str(baseobservation),
        "CAO_OPENCODE_MCP_OBSERVER_NONCE": nonce,
        "CAO_OPENCODE_MCP_SOURCE_ROOT": str(repo),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    path = str(binary.parent) + ":" + str(Path(sys.executable).parent) + ":/usr/bin:/bin"
    installenv = {
        "HOME": str(home),
        "CAO_HOME_DIR": str(caohome),
        "PATH": path,
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(repo / "src") + os.pathsep + str(repo),
        "PYTHONDONTWRITEBYTECODE": "1",
        "XDG_DATA_HOME": str(home / ".local/share"),
        "TMPDIR": str(root),
        "CAO_MCP_APPS_ENABLED": "0",
    }
    for role in ["receiver", "sender"]:
        allowed = case["profiles"][role]["allowed_tools"]
        tools = case["profiles"][role]["enabled_mcp_tools"]
        source = root / ("v1-" + role + ".md")
        source.write_text(
            frontmatter.dumps(
                frontmatter.Post(
                    case["profiles"][role]["prompt"],
                    name="v1-" + role,
                    description="Private actual multiagent collaboration",
                    provider="opencode_cli",
                    role="developer",
                    allowedTools=allowed,
                    mcpServers={
                        "cao-mcp-server": {
                            "command": sys.executable,
                            "args": ["-m", "cli_agent_orchestrator.mcp_server.server"],
                            "env": childenv,
                            "cao_tool_profile": "collaboration",
                            "enabled_tools": tools,
                            "timeout": 60000,
                        }
                    },
                ),
                sort_keys=False,
            )
        )
        admit("install_" + role)
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "cli_agent_orchestrator.cli.main",
                "install",
                str(source),
                "--provider",
                "opencode_cli",
            ],
            env=installenv,
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=60,
        )
        (raw / ("install-" + role + ".private")).write_text(r.stdout + r.stderr)
        receipt[role + "_production_install_passed"] = r.returncode == 0
        assert r.returncode == 0, "production_install_failed"
    _copy_private_state_bundle(source_db, home / ".local/share/opencode")
    if legacy.exists():
        _copy_private_state_file(legacy, home / ".local/share/opencode/auth.json")
    admit("api_start")
    pgcontext = isolated_live_targets(root / "pg", service=service)
    targets = _enter_guarded_postgres_context(pgcontext, postgres_fixtures)
    pgentered = True
    tmuxdir = root / "tmux"
    tmuxdir.mkdir(mode=0o700)
    tmux_environment = {
        key: oldenv[key]
        for key in ("PATH", "SHELL", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TERM")
        if key in oldenv
    }
    tmux_environment.update({"HOME": str(home), "PATH": path, "TMUX_TMPDIR": str(tmuxdir)})
    with _temporary_environment(tmux_environment):
        tmux = TmuxClient()
        socket_path = journey._pin_tmux_client_to_private_server(tmux, tmuxdir)
        tmux_start_attempted = True
        sid, sockid = journey._start_and_pin_private_tmux_server(tmux, socket_path)
    extra = {
        "PATH": path,
        "TMUX_TMPDIR": str(tmuxdir),
        "CAO_TERMINAL_BACKEND": "tmux",
        "CAO_AUTH_MODE": "off",
        "CAO_AGUI_ENABLED": "true",
        "CAO_ENABLE_SENDER_ID_INJECTION": "true",
        "TERM": "xterm-256color",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
    }
    approved = frozenset(
        {
            "OPENCODE_DISABLE_AUTOUPDATE",
            "OPENCODE_DISABLE_PROJECT_CONFIG",
            "OPENCODE_DISABLE_MODELS_FETCH",
        }
    )

    def start_api(role):
        before = set(cao_server_fixture._LIVE_SERVERS)
        attempt = {"role": role, "server": None, "identity": None, "cleanup_servers": []}
        api_launch_attempts.append(attempt)
        try:
            created = _start_cao_server(
                home,
                api_port,
                deadline=60,
                database_targets=targets,
                extra_env=extra,
                allowed_external_credentials=approved,
            )
        except BaseException:
            captured = set(cao_server_fixture._LIVE_SERVERS) - before
            attempt["cleanup_servers"] = list(captured)
            servers.extend(item for item in captured if item not in servers)
            # A raise leaves the role's process identity unproven even if the fixture
            # attempted startup rollback.  Keep any retained owner for a stop retry;
            # the missing identity makes the PG release predicate fail closed.
            raise
        attempt["server"] = created
        attempt["cleanup_servers"] = [created]
        servers.append(created)
        attempt["identity"] = _capture_api_identity(created, message_live, journey)
        return created

    server = start_api("api_a")
    receipt["api_healthy"] = True
    group = ["spec025", os.urandom(6).hex()]
    for role in ["receiver", "sender"]:
        admit("terminal_" + role)
        t = time.monotonic()
        route = "/sessions" if role == "receiver" else "/sessions/" + session_name + "/terminals"
        params = {
            "provider": "opencode_cli",
            "agent_profile": "v1-" + role,
            "working_directory": str(home),
            "model": model,
        }
        if role == "receiver":
            session_name = "c60-" + root.name[-8:]
            params["session_name"] = session_name
        else:
            params["caller_id"] = terminals["receiver"]["id"]
        r = requests.post(server.url + route, params=params, json={"group": group}, timeout=200)
        (raw / ("create-" + role + ".private")).write_text(r.text)
        receipt[role + "_create_http_status"] = r.status_code
        receipt[role + "_initialize_seconds"] = time.monotonic() - t
        assert r.status_code == 201, "production_terminal_creation_failed"
        data = r.json()
        terminals[role] = data
        terminal_id = data["id"]
        session_name = data["session_name"]
        owned |= journey._snapshot_and_recheck_private_tmux_server(
            sid, sockid, socket_path, terminal_id=terminal_id
        )
        ready_deadline = time.monotonic() + 60
        while time.monotonic() < ready_deadline:
            data = requests.get(server.url + "/terminals/" + terminal_id, timeout=10).json()
            if data.get("status") in ["idle", "completed"]:
                break
            time.sleep(0.5)
        terminals[role] = data
        receipt[role + "_operation_permissions_exact"] = set(
            data.get("allowed_tools") or []
        ) == set([] if role == "receiver" else ["@cao-mcp-server", "discovery"])
        assert receipt[role + "_operation_permissions_exact"], "operation_permissions_mismatch"
        viewport = tmux.get_history(
            session_name, data["name"], strip_escapes=True, visible_only=True
        )
        receipt[role + "_model_ready"] = (
            "GLM-5.3-Flash" in viewport
            and "OpenCode Go" in viewport
            and "Big Pickle" not in viewport
            and data["status"] in ["idle", "completed"]
        )
        assert receipt[role + "_model_ready"], "native_model_not_ready"
    receipt["terminal_groups_exact"] = all(
        data.get("group") == group for data in terminals.values()
    )
    assert receipt["terminal_groups_exact"], "terminal_group_mismatch"
    receiver_id = terminals["receiver"]["id"]
    sender_id = terminals["sender"]["id"]
    roots = list((caohome / "opencode-v1").glob("terminal-*"))
    assert len(roots) == 2
    authfiles = [p / "data/opencode/auth.json" for p in roots]
    receipt["credentials_isolated"] = (
        all(
            p.is_file()
            and not p.is_symlink()
            and p.stat().st_mode & 0o777 == 0o600
            and p.stat().st_nlink == 1
            for p in authfiles
        )
        and len({(p.stat().st_dev, p.stat().st_ino) for p in authfiles}) == 2
    )
    assert receipt["credentials_isolated"], "private_credentials_not_isolated"
    configs = [json.loads((p / "opencode.json").read_text()) for p in roots]
    receipt["two_isolated_native_roots"] = (
        len({x["agent"].keys().__iter__().__next__() for x in configs}) == 2
    )
    from cli_agent_orchestrator.utils.opencode_policy import parse_native_agent

    names = {"v1-sender", "v1-receiver"}
    requested = {
        name: frontmatter.loads((root / (name + ".md")).read_text()).content.strip()
        for name in names
    }
    installed = {
        name: parse_native_agent((config / "agents" / (name + ".md")).read_text(), name)[0].content
        for name in names
    }
    receipt["native_profile_configuration_exact"] = (
        requested == installed
        and {next(iter(x["agent"])) for x in configs} == names
        and all(
            len(x["agent"]) == 1
            and x["agent"][next(iter(x["agent"]))]["prompt"] == installed[next(iter(x["agent"]))]
            and x["model"] == case["model"]
            for x in configs
        )
    )
    assert receipt["native_profile_configuration_exact"], "native_profile_configuration_mismatch"
    receipt["filters_exact"] = sorted(
        json.loads(x["mcp"]["cao-mcp-server"]["environment"]["CAO_INTERNAL_MCP_ENABLED_TOOLS"])
        for x in configs
    ) == [[], ["list_siblings", "send_message"]]

    def pgrows():
        with connect(targets.main, readonly=True) as con:
            rows = con.execute(
                "SELECT id,sender_id,receiver_id,message,status,created_at FROM inbox WHERE receiver_id=%s ORDER BY created_at,id",
                (receiver_id,),
            ).fetchall()
            return [{key: row[key] for key in row.keys()} for row in rows]

    def apirows():
        r = requests.get(
            server.url + "/terminals/" + receiver_id + "/inbox/messages",
            params={"limit": 10},
            timeout=10,
        )
        assert r.status_code == 200
        return r.json()

    assert not pgrows() and not apirows()
    first_pg = None
    first_api = None
    for index, message in enumerate([PRE_RESTART_MESSAGE, POST_RESTART_MESSAGE]):
        capture = AguiCapture(server.url, sender_id, receiver_id)
        captures.append(capture)
        capture.start()
        admit("model_phase_" + str(index))
        phase = {"phase": index}
        receipt["phases"].append(phase)

        def verify_readers():
            return (
                journey._capture_private_tmux_socket_identity(socket_path) == sockid
                and journey._process_identity_alive(sid)
                and journey._process_identity_alive(api_launch_attempts[-1]["identity"])
            )

        readers = OwnedPhaseReaders(
            server.url,
            socket_path,
            {d["id"]: {"session": session_name, "pane": d["name"]} for d in terminals.values()},
            verify_readers,
        )
        _collect_message_phase(index, message, phase, readers)
        assert (
            phase.get("last_receipt_unicode_verified") is True
            and phase.get("sender_completed") is True
        ), "model_receipt_not_observed"
        assert (
            phase.get("receiver_ack_exact") is True
            and phase.get("receiver_turn_completed_once") is True
        ), "receiver_ack_not_observed"
        rows = pgrows()
        arows = apirows()
        phase["one_exact_new_persisted_message"] = (
            len(rows) == index + 1
            and len(arows) == index + 1
            and rows[-1]["sender_id"] == sender_id
            and rows[-1]["receiver_id"] == receiver_id
            and rows[-1]["message"] == expected_persisted_message(message, sender_id)
            and arows[-1]["id"] == rows[-1]["id"]
            and arows[-1]["message"] == expected_persisted_message(message, sender_id)
        )
        assert phase["one_exact_new_persisted_message"], "inbox_contract_mismatch"
        phase["inbox_pair_exact"] = acceptance.inbox_pair_exact(
            rows[-1],
            arows[-1],
            sender_id,
            receiver_id,
            expected_persisted_message(message, sender_id),
        )
        assert phase["inbox_pair_exact"], "inbox_contract_mismatch"

        events = capture.take_until(time.monotonic() + 1)
        phase["agui_one_redacted_notification"] = events == [True]
        capture.stop()
        phase["agui_capture_thread_stopped"] = not capture._thread.is_alive()
        phase["agui_capture_clean"] = (
            phase["agui_capture_thread_stopped"] and capture._failed is False
        )
        assert phase["agui_one_redacted_notification"], "agui_notification_mismatch"
        assert phase["agui_capture_clean"], "agui_capture_thread_leaked"
        if index == 0:
            restart_receipts_before = _read_durable_turns(time.monotonic() + 3)
            first_pg = {k: v for k, v in rows[0].items() if k != "status"}
            first_api = {k: v for k, v in arows[0].items() if k != "status"}
            native_before = {
                role: journey._terminal_tagged_processes(d["id"], socket_path, server_identity=sid)
                for role, d in terminals.items()
            }
            assert all(native_before.values())
            admit("api_restart")
            t = time.monotonic()
            previous_server = server
            previous_api_identity = api_launch_attempts[-1]["identity"]
            previous_server.stop()
            server_stop_confirmed.add(id(previous_server))
            server = start_api("api_b")
            restart_readiness_stop = time.monotonic() + 75
            receipt["api_restart_identity_exact"] = (
                not journey._process_identity_alive(previous_api_identity)
                and api_launch_attempts[-1]["identity"] != previous_api_identity
            )
            assert receipt["api_restart_identity_exact"], "restart_identity_mismatch"
            receipt["restart_seconds"] = time.monotonic() - t
            receipt["native_identities_survive_restart"] = all(
                journey._terminal_tagged_processes(
                    terminals[role]["id"], socket_path, server_identity=sid
                )
                == ids
                and all(journey._process_identity_alive(p) for p in ids)
                for role, ids in native_before.items()
            )
            reread = pgrows()
            receipt["pg_message_identity_survives_restart"] = len(reread) == 1 and all(
                reread[0][k] == v for k, v in first_pg.items()
            )
            receipt["api_message_identity_survives_restart"] = compare_inbox_row(
                first_api, apirows()[0]
            )
            assert all(
                receipt[k]
                for k in [
                    "native_identities_survive_restart",
                    "pg_message_identity_survives_restart",
                    "api_message_identity_survives_restart",
                ]
            ), "restart_identity_mismatch"
    receipt["both_phases_passed"] = len(receipt["phases"]) == 2
    # Preserve exact protocol evidence through graceful native exits before pane deletion.
    for role, data in terminals.items():
        owned |= journey._owned_process_snapshot_for_cleanup(sid, data["id"], socket_path)
        r = requests.post(server.url + "/terminals/" + data["id"] + "/exit", timeout=15)
        receipt[role + "_graceful_exit_http_status"] = r.status_code
    receipt["graceful_exit_http_success"] = all(
        receipt.get(role + "_graceful_exit_http_status") == 200 for role in terminals
    )
    until = time.monotonic() + 10
    graceful_seals_seen = False
    graceful_seals_observed_at = None
    while time.monotonic() < until:
        seals_present = all(
            baseobservation.with_name(baseobservation.name + "-" + d["id"] + ".complete").exists()
            for d in terminals.values()
        )
        graceful_seals_observed_at = time.monotonic()
        graceful_seals_seen = _graceful_seals_seen_by_deadline(
            seals_present, graceful_seals_observed_at, until
        )
        if graceful_seals_seen:
            break
        time.sleep(0.1)
    receipt["graceful_observer_seals_within_10s"] = graceful_seals_seen
    if not (
        receipt["graceful_exit_http_success"] and receipt["graceful_observer_seals_within_10s"]
    ):
        raise RuntimeError("graceful_exit_or_observer_timeout")

except Exception as exc:
    receipt["error_type"] = type(exc).__name__
    receipt["error_code"] = (
        str(exc)
        if str(exc)
        in [
            "native_profile_configuration_mismatch",
            "terminal_group_mismatch",
            "receiver_baseline_unavailable",
            "restart_durable_receipt_unavailable",
            "restart_durable_receipt_mismatch",
            "receiver_counter_changed_on_restart",
            "receiver_ack_not_observed",
            "memory_below_unchanged_6144_MiB_floor",
            "production_install_failed",
            "production_terminal_creation_failed",
            "native_model_not_ready",
            "model_receipt_not_observed",
            "inbox_contract_mismatch",
            "agui_notification_mismatch",
            "restart_identity_mismatch",
            "private_credentials_not_isolated",
            "operation_permissions_mismatch",
            "postgres_identity_mismatch",
            "graceful_exit_or_observer_timeout",
            "agui_capture_thread_leaked",
            "input_turn_sequence_unavailable",
            "api_process_identity_unavailable",
            "postgres_cleanup_helper_source_invalid",
            "postgres_cleanup_helper_function_shape_changed",
            "postgres_cleanup_helper_finalizer_shape_changed",
            "postgres_cleanup_helper_already_guarded",
            "postgres_cleanup_helper_derivation_failed",
            "postgres_fixture_source_binding_invalid",
            "postgres_fixture_source_pin_mismatch",
            "postgres_fixture_cleanup_helper_missing",
            "postgres_fixture_cleanup_helper_unavailable",
            "postgres_fixture_guard_derivation_pin_mismatch",
            "postgres_fixture_guard_namespace_mismatch",
        ]
        else "local_diagnostic_error"
    )
finally:
    capture_threads_stopped = True
    capture_threads_dead = True
    for capture in captures:
        with contextlib.suppress(BaseException):
            capture.stop()
        try:
            dead = not capture._thread.is_alive()
            clean = capture._failed is False
        except BaseException:
            dead = False
            clean = False
        capture_threads_dead = capture_threads_dead and dead
        capture_threads_stopped = capture_threads_stopped and dead and clean
    receipt["agui_captures_stopped_cleanly"] = capture_threads_stopped
    receipt["agui_capture_threads_confirmed_dead_before_postgres_teardown"] = capture_threads_dead
    receipt["agui_capture_phases_clean"] = all(
        phase.get("agui_capture_clean") is True for phase in receipt.get("phases", [])
    )
    cleanup_server = None
    if "journey" in globals():
        for record in reversed(api_launch_attempts):
            candidate = record.get("server")
            identity = record.get("identity")
            try:
                if (
                    candidate is not None
                    and identity is not None
                    and journey._process_identity_alive(identity)
                ):
                    cleanup_server = candidate
                    break
            except BaseException:
                continue
    if cleanup_server:
        for role, data in terminals.items():
            try:
                snapshot_bytes = http_json_bytes(
                    cleanup_server.url,
                    "/terminals/" + data["id"] + "/output?mode=full",
                    time.monotonic() + 10,
                    6 * 65537 + 4096,
                )
                snapshot_body = json.loads(snapshot_bytes)
                snapshot_text = snapshot_body.get("output")
                if type(snapshot_text) is not str or len(snapshot_text.encode("utf-8")) > 65537:
                    raise RuntimeError("cleanup_snapshot_bound")
                (raw / ("full-shape-" + role + ".json")).write_text(
                    json.dumps(
                        {
                            "characters": len(snapshot_text),
                            "lines": len(snapshot_text.splitlines()),
                            "raw_text_exported": False,
                        }
                    )
                )
            except BaseException:
                failures.add("api_output_snapshot_failed")
            try:
                owned |= journey._owned_process_snapshot_for_cleanup(sid, data["id"], socket_path)
            except BaseException:
                failures.add("terminal_process_snapshot_failed")
        if "session_name" in globals():
            try:
                response = requests.delete(
                    cleanup_server.url + "/sessions/" + session_name, timeout=60
                )
                receipt["cleanup_session_delete_http_status"] = response.status_code
                if response.status_code == 200:
                    receipt["private_roots_after_session_delete"] = len(
                        list((caohome / "opencode-v1").glob("terminal-*"))
                    )
                    (raw / "private-root-count-after-delete.private").write_text(
                        str(receipt["private_roots_after_session_delete"])
                    )
                else:
                    failures.add("session_delete_failed")
            except BaseException:
                failures.add("session_delete_failed")
    else:
        receipt["cleanup_session_delete_http_status"] = None
        if terminals:
            failures.add("session_delete_failed")
    distinct_servers = []
    distinct_server_ids = set()
    for candidate in servers + [
        item for attempt in api_launch_attempts for item in attempt.get("cleanup_servers", [])
    ]:
        if id(candidate) not in distinct_server_ids:
            distinct_server_ids.add(id(candidate))
            distinct_servers.append(candidate)
    api_stop_calls_succeeded = True
    for candidate in reversed(distinct_servers):
        try:
            candidate.stop()
        except BaseException:
            api_stop_calls_succeeded = False
            failures.add("api_cleanup_failed")
    api_attempted_roles = {
        record.get("role") for record in api_launch_attempts if isinstance(record.get("role"), str)
    }
    api_processes_dead = (
        _api_launches_confirmed_dead(
            api_launch_attempts, attempted_roles=api_attempted_roles, journey=journey
        )
        if "journey" in globals()
        else not api_launch_attempts
    )
    receipt["api_launches_confirmed_dead_before_postgres_teardown"] = api_processes_dead
    receipt["api_cleanup"] = api_stop_calls_succeeded and api_processes_dead
    if not api_processes_dead:
        failures.add("api_process_identity_unconfirmed")
    for index, candidate in enumerate(distinct_servers):
        try:
            log_path = candidate.log_path
            if log_path.exists():
                shutil.copyfile(log_path, raw / ("server-" + str(index) + ".private"))
        except BaseException:
            failures.add("api_log_snapshot_failed")
    if tmux_start_attempted:
        try:
            tmux_cleanup_confirmed = bool(
                tmux
                and sid
                and sockid
                and journey._cleanup_private_tmux_server(
                    tmux,
                    tmuxdir,
                    failures,
                    terminal_id=terminal_id or "",
                    server_identity=sid,
                    owned_processes=owned,
                    socket_identity=sockid,
                )
            )
        except BaseException:
            tmux_cleanup_confirmed = False
        if not tmux_cleanup_confirmed:
            failures.add("tmux_cleanup_unconfirmed")
    else:
        tmux_cleanup_confirmed = True
    receipt["tmux_processes_clean"] = tmux_cleanup_confirmed
    receipt["tmux_processes_confirmed_dead_before_postgres_teardown"] = tmux_cleanup_confirmed
    safe_postgres_release = api_processes_dead and capture_threads_dead and tmux_cleanup_confirmed
    if pgentered:
        if safe_postgres_release:
            try:
                _exit_guarded_postgres_context(pgcontext, postgres_fixtures)
                receipt["postgres_cleanup"] = True
                receipt["postgres_cleanup_deferred"] = False
            except BaseException:
                receipt["postgres_cleanup"] = False
                receipt["postgres_cleanup_deferred"] = True
                _DEFERRED_POSTGRES_CONTEXTS.append(pgcontext)
                failures.add("postgres_cleanup_deferred_service_unconfirmed")
        else:
            receipt["postgres_cleanup"] = False
            receipt["postgres_cleanup_deferred"] = True
            _DEFERRED_POSTGRES_CONTEXTS.append(pgcontext)
            failures.add("postgres_cleanup_deferred_service_unconfirmed")
    else:
        receipt["postgres_cleanup"] = True
        receipt["postgres_cleanup_deferred"] = False
    receipt["cleanup_failure_codes"] = sorted(failures)
    with contextlib.suppress(Exception):
        if observer_module is None:
            raise RuntimeError("mcp_observer_import_unavailable")
        observations = {
            role: observer_module.read_protocol_observations(
                baseobservation.with_name(baseobservation.name + "-" + data["id"]),
                expected_nonce=nonce,
            )
            for role, data in terminals.items()
        }
        receipt["protocol_observations"] = observations
        senderobs = observations["sender"]
        receiverobs = observations["receiver"]
        calls = senderobs["mcp_tool_calls"]
        receipt["exact_native_catalogs"] = (
            set(senderobs["mcp_catalog_tool_names"]) == {"send_message", "list_siblings"}
            and receiverobs["mcp_catalog_tool_names"] == []
            and all(o["mcp_catalog_unexpected_tool_count"] == 0 for o in observations.values())
        )
        receipt["exact_native_successful_calls"] = (
            [x["tool"] for x in calls] == ["list_siblings", "send_message", "send_message"]
            and all(x["status"] == "completed" and x["error_present"] is False for x in calls)
            and not receiverobs["mcp_tool_calls"]
            and all(o["mcp_tool_unexpected_call_count"] == 0 for o in observations.values())
        )
        receipt["observers_complete"] = all(
            o["observer_status"] == "observed" for o in observations.values()
        )
    with contextlib.suppress(BaseException):
        os.environ.clear()
        os.environ.update(oldenv)
    try:
        receipt["operator_auth_unchanged"] = fingerprints() == before
    except BaseException:
        receipt["operator_auth_unchanged"] = False
    try:
        receipt["source_unchanged"] = all(
            hashlib.sha256((repo / f).read_bytes()).hexdigest() == h for f, h in sourcepins.items()
        )
    except BaseException:
        receipt["source_unchanged"] = False
    try:
        receipt["binary_unchanged"] = (
            hashlib.sha256(binary.read_bytes()).hexdigest() == pin["binary_sha256"]
        )
    except BaseException:
        receipt["binary_unchanged"] = False
    try:
        receipt["runner_unchanged"] = (
            hashlib.sha256(runner_path.read_bytes()).hexdigest() == runner_sha256
        )
    except BaseException:
        receipt["runner_unchanged"] = False
    try:
        receipt["source_head_pinned_at_end"] = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            == expected_head
        )
        receipt["source_runtime_tree_clean_at_end"] = not subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", "src", "test"],
            cwd=repo,
            text=True,
        ).strip()
    except Exception:
        receipt["source_head_pinned_at_end"] = False
        receipt["source_runtime_tree_clean_at_end"] = False
    if (
        receipt.get("tmux_processes_clean")
        and receipt.get("api_cleanup")
        and receipt.get("postgres_cleanup")
        and receipt.get("private_roots_after_session_delete") == 0
    ):
        try:
            shutil.rmtree(root)
            receipt["private_state_removed"] = not root.exists()
        except BaseException:
            receipt["private_state_removed"] = False
            receipt["retained_private_root"] = str(root)
    else:
        receipt["private_state_removed"] = False
        receipt["retained_private_root"] = str(root)
    if "fixture_requests_module" in globals():
        cao_server_fixture.requests = fixture_requests_module
    http_session.close()
    receipt["postgres_guard_binding_exact"] = (
        receipt.get("postgres_guard_fixture_sha256") == expected_pg_fixture_sha256
        and receipt.get("postgres_guard_derived_sha256") == expected_pg_guard_sha256
    )
    receipt["elapsed_seconds"] = time.monotonic() - start
    receipt["inherited_acceptance_passed"] = _final_success(receipt)
    receipt["receiver_ack_acceptance_passed"] = acceptance.receiver_phases_exact(
        receipt.get("phases")
    )
    receipt["native_profile_bindings_exact"] = receipt.get(
        "native_profile_configuration_exact"
    ) is True and native_probe.native_profiles_exact(receipt.get("phases"))
    receipt["sender_unicode_acceptance_passed"] = acceptance.sender_phases_exact(
        receipt.get("phases")
    )
    receipt["inbox_pairs_exact"] = len(receipt.get("phases", [])) == 2 and all(
        phase.get("inbox_pair_exact") is True for phase in receipt["phases"]
    )
    receipt["success"] = acceptance.final_exact(receipt)
    for p in raw.rglob("*"):
        if p.is_file() and not p.is_symlink():
            p.chmod(0o600)
    p = cache / (label + "-receipt.json")
    p.write_text(json.dumps(receipt, indent=2) + "\n")
    p.chmod(0o600)
    print(
        json.dumps(
            {
                k: v
                for k, v in receipt.items()
                if k not in ["source_files", "protocol_observations"]
            },
            indent=2,
        ),
        flush=True,
    )
raise SystemExit(0 if receipt["success"] else 1)
