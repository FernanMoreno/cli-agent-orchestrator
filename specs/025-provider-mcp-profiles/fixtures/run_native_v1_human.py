"""Default-closed native-v1 human interaction acceptance with explicit private bindings."""

import argparse
import ast
import contextlib
import datetime
import hashlib
import importlib.util
import inspect
import json
import os
import re
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
parser.add_argument("--case", choices=("interaction", "collaboration"), default="interaction")
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
mixed = args.case == "collaboration"
if m.get("case", "interaction") != args.case:
    raise RuntimeError("case_binding_mismatch")
if m.get("execution_authorized") is not True:
    raise RuntimeError("execution_gate_closed")
repo = Path(m["workspace"])
cache = Path(m["evidence_dir"])
label = "t103-v1-collaboration" if mixed else "t102-v1-human"
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
        "native_phase_readers.py",
        "native_acceptance_contract.py",
        "run_native_v1_acceptance.py",
        "native-human-interaction-profile.json",
        "run_native_v1_human.py",
        "native_human_contract.py",
        "native_human_api_worker.py",
    )
} | {"specs/025-provider-mcp-profiles/opencode-v1-pin.json"}
if mixed:
    required_artifacts |= {
        "specs/025-provider-mcp-profiles/fixtures/" + name
        for name in (
            "native-collaboration-interaction-profile.json",
            "native_collaboration_contract.py",
            "native-message-acceptance-profiles.json",
        )
    }
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
runtime_relative = "specs/025-provider-mcp-profiles/fixtures/run_native_v1_human.py"
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
from native_phase_readers import HTTPStatusError, OwnedPhaseReaders, capture_bytes, http_json_bytes

case = json.loads(
    (
        repo
        / "specs/025-provider-mcp-profiles/fixtures"
        / (
            "native-collaboration-interaction-profile.json"
            if mixed
            else "native-human-interaction-profile.json"
        )
    ).read_text()
)
if case["required_checks"] != {
    "question_count": 2,
    "agui_snapshot_count": 3,
    "model_seconds": 75,
    "outer_seconds": 180,
    "memory_floor_mib": 6144,
    "restart_boundary": "committed_create_response_before_return_to_mcp",
    "unicode_marker": "Validado ñ, 中文.",
    "owned_cleanup": True,
}:
    raise RuntimeError("native_case_contract_mismatch")
import native_human_contract as human

if mixed:
    import native_collaboration_contract as mixed_contract

    restricted_case = json.loads(
        (
            repo / "specs/025-provider-mcp-profiles/fixtures/native-human-interaction-profile.json"
        ).read_text()
    )
    if any(
        case.get(key) != restricted_case.get(key)
        for key in ("model", "questions", "human_answers", "required_checks", "allowed_tools")
    ):
        raise RuntimeError("native_case_contract_mismatch")

    if case["mcp_profile"] != "collaboration" or sorted(case["tools"]) != sorted(
        mixed_contract.TOOLS
    ):
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
root = Path(tempfile.mkdtemp(prefix="t102-human-", dir="/tmp"))
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
    "scope": (
        "current native-v1 full-collaboration human+message case; not all profiles or global acceptance"
        if mixed
        else "current managed-v1 human Q&A; committed-response pre-wait API restart; not expanded profiles or global acceptance"
    ),
    "model": case["model"],
    "case": "T103" if mixed else "T102",
    "mcp_profile": "collaboration" if mixed else "interaction",
    "diagnostic_only": True,
    "acceptance_claimed": False,
    "original_model_acceptance_claimed": False,
    "global_acceptance_claimed": False,
    "source_pin_file_count": len(sourcefiles),
    "generation_gate_bound": True,
    "generation_receipt_sha256": m["generation_receipt_sha256"],
    "Claude": "read review only; no Claude provider execution",
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
receiver_id = ""
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


def _mixed_private_observer_source(observer_source, tools):
    """Bind the copied collaboration observer to its terminal-specific ledger."""
    if (
        type(observer_source) is not str
        or type(tools) not in (list, tuple)
        or any(type(tool) is not str or not tool for tool in tools)
    ):
        raise RuntimeError("mcp_observer_source_invalid")
    original = "    return install_protocol_observer(low_level_server, Path(raw_path), nonce)"
    if observer_source.count(original) != 1:
        raise RuntimeError("mcp_observer_source_mismatch")
    replacement = (
        "    terminal = selected_environment.get('CAO_TERMINAL_ID', '')\n"
        "    if not re.fullmatch(r'[0-9a-f]{8}', terminal):\n"
        "        raise RuntimeError('mcp_observer_terminal_binding_invalid')\n"
        "    return install_protocol_observer(\n"
        "        low_level_server,\n"
        "        Path(raw_path).with_name(Path(raw_path).name + '-' + terminal),\n"
        "        nonce,\n"
        "    )"
    )
    adapted = observer_source.replace(original, replacement, 1)
    return adapted + "\nINTERACTION_TOOLS = frozenset(" + repr(tuple(sorted(tools))) + ")\n"


def _observer_path_for_terminal(base_path, terminal, *, mixed):
    if not mixed:
        return base_path
    if type(terminal) is not str or re.fullmatch(r"[0-9a-f]{8}", terminal) is None:
        raise RuntimeError("mcp_observer_terminal_binding_invalid")
    return base_path.with_name(base_path.name + "-" + terminal)


def _private_protocol_rows(path, expected_nonce):
    """Read one private observer ledger without exposing its raw event contents."""
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 131072
        ):
            raise RuntimeError("protocol_sidecar_invalid")
        data = stream.read(131073)
    rows = [json.loads(line) for line in data.splitlines()]
    if (
        len(data) > 131072
        or not data.endswith(b"\n")
        or not all(type(row) is dict and row.get("nonce") == expected_nonce for row in rows)
    ):
        raise RuntimeError("protocol_sidecar_invalid")
    return rows


def _observer_sidecar_identity(path):
    try:
        info = path.lstat()
    except OSError:
        return None
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        return None
    return info.st_dev, info.st_ino


def _receiver_interaction_capability_path(caohome, receiver_id, incarnation_id):
    if (
        not isinstance(caohome, Path)
        or not caohome.is_absolute()
        or type(receiver_id) is not str
        or re.fullmatch(r"[0-9a-f]{8}", receiver_id) is None
        or type(incarnation_id) is not str
        or re.fullmatch(r"[0-9a-f]{32}", incarnation_id) is None
    ):
        raise RuntimeError("receiver_identity_failed")
    terminal_key = hashlib.sha256(receiver_id.encode("utf-8")).hexdigest()
    incarnation_key = hashlib.sha256(incarnation_id.encode("utf-8")).hexdigest()
    return caohome / "tmp" / "interaction-capabilities" / f"{terminal_key}.{incarnation_key}.cap"


def _interaction_capability_file_absent(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    except Exception:
        return False
    return False


def _receiver_capability_absence_exact(
    server_environment,
    capability_file_absent,
    config_binding,
    current_config_binding,
):
    from cli_agent_orchestrator.providers.mcp_interactions import CAO_INTERACTION_CAPABILITY_FILE

    if (
        type(server_environment) is not dict
        or CAO_INTERACTION_CAPABILITY_FILE in server_environment
        or capability_file_absent is not True
        or type(config_binding) is not tuple
        or type(current_config_binding) is not tuple
        or len(config_binding) != 4
        or len(current_config_binding) != 4
        or any(type(value) is not int for value in config_binding[:2])
        or any(type(value) is not int for value in current_config_binding[:2])
        or any(type(value) is not str for value in config_binding[2:])
        or any(type(value) is not str for value in current_config_binding[2:])
    ):
        return False
    return config_binding == current_config_binding


def _native_v1_passive_receiver_profile(receiver_case, sender_mcp_definition):
    """Reproduce T101's explicit CAO collaboration server with an empty filter."""
    allowed = receiver_case.get("allowed_tools")
    enabled_tools = receiver_case.get("enabled_mcp_tools")
    if type(allowed) is not list or allowed or type(enabled_tools) is not list or enabled_tools:
        raise RuntimeError("native_case_contract_mismatch")
    receiver_mcp_definition = {**sender_mcp_definition, "enabled_tools": []}
    return frontmatter.Post(
        receiver_case["prompt"],
        name="v1-receiver",
        description="Private native passive receiver",
        provider="opencode_cli",
        role="developer",
        allowedTools=allowed,
        mcpServers={"cao-mcp-server": receiver_mcp_definition},
    )


def _read_deferred_receiver_variant(
    capture,
    *,
    python,
    source_root,
    config_file,
    receiver_id,
    stop,
    monotonic,
):
    """Read only the owned receiver variant in a bounded isolated Python child."""
    if (
        type(python) is not str
        or not python
        or type(source_root) is not str
        or not source_root
        or type(config_file) is not str
        or not config_file
        or type(receiver_id) is not str
        or re.fullmatch(r"[0-9a-f]{8}", receiver_id) is None
    ):
        raise ValueError("receiver_readiness_database_binding")
    child_source = """import json, os, re, sys
os.environ["CAO_DATABASE_CONFIG_FILE"] = sys.argv[2]
sys.path.insert(0, sys.argv[1])
from cli_agent_orchestrator.clients.postgres.config import DatabaseTarget
from cli_agent_orchestrator.clients.postgres.connection import connect
terminal_id = sys.argv[3]
if re.fullmatch(r"[0-9a-f]{8}", terminal_id) is None:
    raise SystemExit(2)
target = DatabaseTarget.from_settings("main")
with connect(target, readonly=True) as con:
    row = con.execute("SELECT provider_variant FROM terminals WHERE id=%s", (terminal_id,)).fetchone()
ready = row is not None and type(row["provider_variant"]) is str and row["provider_variant"] == "opencode-v1"
sys.stdout.write('{"ready":' + ("true" if ready else "false") + "}\\n")
"""
    argv = [python, "-I", "-c", child_source, source_root, config_file, receiver_id]
    try:
        output = capture(argv, stop, 64)
    except Exception:
        raise ValueError("receiver_readiness_database_failed") from None
    if monotonic() > stop:
        raise ValueError("receiver_readiness_database_deadline")
    if output == b'{"ready":true}\n':
        return True
    if output == b'{"ready":false}\n':
        return False
    raise ValueError("receiver_readiness_database_response")


def _wait_for_deferred_receiver_ready(
    get_terminal,
    read_provider_variant,
    *,
    receipt,
    journey_start,
    outer_deadline,
    monotonic,
    sleep,
):
    """Wait for deferred receiver initialization without persisting its content."""
    observations = []
    receipt["receiver_startup_observations"] = observations
    receipt["receiver_initialization_complete"] = False
    receipt["receiver_initialization_complete_exact"] = False
    receipt["receiver_initialization_deadline_reached"] = False
    receipt["receiver_deferred_failure_present"] = False
    receipt["receiver_initialization_settled_samples"] = 0
    receipt["receiver_startup_poll_count"] = 0
    settled = 0
    fixed_statuses = {
        "unknown",
        "idle",
        "processing",
        "completed",
        "waiting_user_answer",
        "waiting_quota",
        "reconcile",
        "error",
    }

    while True:
        poll_started = monotonic()
        remaining = outer_deadline - poll_started
        if remaining <= 0:
            receipt["receiver_initialization_deadline_reached"] = True
            raise RuntimeError("receiver_initialization_deadline")

        try:
            response = get_terminal(timeout=min(2.0, remaining))
        except Exception:
            request_finished = monotonic()
            observations.append(
                {
                    "elapsed_seconds": round(max(0.0, request_finished - journey_start), 3),
                    "status": "request_error",
                    "http_status": None,
                    "turn_sequence": None,
                    "turn_completed": None,
                    "deferred_failure_present": False,
                    "provider_variant_ready": False,
                    "provider_variant_read_error": False,
                }
            )
            receipt["receiver_startup_poll_count"] = len(observations)
            if request_finished >= outer_deadline:
                receipt["receiver_initialization_deadline_reached"] = True
                raise RuntimeError("receiver_initialization_deadline") from None
            raise RuntimeError("receiver_readiness_request_failed") from None

        if monotonic() >= outer_deadline:
            receipt["receiver_initialization_deadline_reached"] = True
            raise RuntimeError("receiver_initialization_deadline")

        status_code = getattr(response, "status_code", None)
        if type(status_code) is not int or status_code != 200:
            observations.append(
                {
                    "elapsed_seconds": round(max(0.0, monotonic() - journey_start), 3),
                    "status": "http_error",
                    "http_status": status_code if type(status_code) is int else None,
                    "turn_sequence": None,
                    "turn_completed": None,
                    "deferred_failure_present": False,
                    "provider_variant_ready": False,
                    "provider_variant_read_error": False,
                }
            )
            receipt["receiver_startup_poll_count"] = len(observations)
            raise RuntimeError("receiver_readiness_http_failed")

        try:
            state = response.json()
        except Exception:
            state = None
        if type(state) is not dict:
            observations.append(
                {
                    "elapsed_seconds": round(max(0.0, monotonic() - journey_start), 3),
                    "status": "invalid_response",
                    "http_status": status_code,
                    "turn_sequence": None,
                    "turn_completed": None,
                    "deferred_failure_present": False,
                    "provider_variant_ready": False,
                    "provider_variant_read_error": False,
                }
            )
            receipt["receiver_startup_poll_count"] = len(observations)
            raise RuntimeError("receiver_readiness_response_invalid")

        raw_status = state.get("status")
        status = raw_status if type(raw_status) is str and raw_status in fixed_statuses else "other"
        sequence = _bounded_count(state.get("turn_sequence"))
        completed = _bounded_count(state.get("turn_completed"))
        deferred_failure_present = type(state.get("deferred_init_failure")) is dict
        provider_variant_read_error = False
        if deferred_failure_present:
            provider_variant_ready = False
        else:
            try:
                provider_variant_ready = read_provider_variant(min(outer_deadline, monotonic() + 3))
                if type(provider_variant_ready) is not bool:
                    raise ValueError("receiver_readiness_database_response")
            except Exception:
                provider_variant_ready = False
                provider_variant_read_error = True
        receipt["receiver_deferred_failure_present"] = deferred_failure_present
        observations.append(
            {
                "elapsed_seconds": round(max(0.0, monotonic() - journey_start), 3),
                "status": status,
                "http_status": status_code,
                "turn_sequence": sequence,
                "turn_completed": completed,
                "deferred_failure_present": deferred_failure_present,
                "provider_variant_ready": provider_variant_ready,
                "provider_variant_read_error": provider_variant_read_error,
            }
        )
        receipt["receiver_startup_poll_count"] = len(observations)

        if deferred_failure_present:
            raise RuntimeError("receiver_deferred_initialization_failed")
        if monotonic() >= outer_deadline:
            receipt["receiver_initialization_deadline_reached"] = True
            raise RuntimeError("receiver_initialization_deadline")
        if provider_variant_read_error:
            raise RuntimeError("receiver_readiness_database_failed")

        if (
            provider_variant_ready
            and status in {"idle", "completed"}
            and sequence == 0
            and completed == 0
        ):
            settled += 1
        else:
            settled = 0
        receipt["receiver_initialization_settled_samples"] = settled
        if settled >= 3:
            receipt["receiver_initialization_complete"] = True
            receipt["receiver_initialization_complete_exact"] = True
            receipt["receiver_initialization_ready_status"] = status
            return state

        remaining = outer_deadline - monotonic()
        if remaining <= 0:
            receipt["receiver_initialization_deadline_reached"] = True
            raise RuntimeError("receiver_initialization_deadline")
        sleep(min(0.25, remaining))


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


def _created_terminal_target(data):
    if (
        type(data) is not dict
        or any(
            type(data.get(key)) is not str or not data[key] or len(data[key]) > 128
            for key in ("id", "session_name", "name")
        )
        or len(data["id"]) != 8
        or any(char not in "0123456789abcdef" for char in data["id"])
    ):
        raise ValueError("created_terminal_target_invalid")
    return data["id"], data["session_name"], data["name"]


barrier_resolved = False

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
    postgres_restart_count_before = int(
        subprocess.check_output(
            ["docker", "inspect", "--format", "{{.RestartCount}}", expected_pg_container_id],
            text=True,
            timeout=10,
        )
    )
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
    barrier = root / "mcp-response-barrier"
    barrier.mkdir(mode=0o700)
    scope_path = root / "human-probe-scope.json"
    audit_path = root / "interaction-observations.jsonl"
    observation = raw / "mcp.events"
    nonce = os.urandom(16).hex()
    probe_nonce = os.urandom(16).hex()
    from test.live_provider import interaction_cli_journey as common
    from test.live_provider.interaction_agui_restart import (
        ApiWorkerController,
        WorkerSpec,
        open_private_log,
        snapshot_identity_map,
    )
    from test.live_provider.interaction_opencode_mcp_gate import barrier_reached, release_barrier

    import opencode_mcp_observer as observer_module

    from cli_agent_orchestrator.utils.mcp_readiness import (
        READY_FILE_ENV,
        READY_TOKEN_ENV,
        has_ready_signal,
    )
    from cli_agent_orchestrator.utils.opencode_policy import parse_native_agent
    from cli_agent_orchestrator.utils.opencode_v1 import validate_v1_mcp_configuration

    for name in ("mcp-home", "mcp-data"):
        (root / name).mkdir(mode=0o700)
    observer_directory = repo / "test/live_provider"
    if mixed:
        observer_directory = root / "mixed-observer"
        observer_directory.mkdir(mode=0o700)
        observer_source = (repo / "test/live_provider/opencode_mcp_observer.py").read_text()
        observer_source = _mixed_private_observer_source(observer_source, case["tools"])
        observer_file = observer_directory / "opencode_mcp_observer.py"
        observer_file.write_text(observer_source)
        observer_file.chmod(0o600)
        spec = importlib.util.spec_from_file_location("mixed_opencode_mcp_observer", observer_file)
        observer_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(observer_module)
    childenv = {
        "HOME": str(root / "mcp-home"),
        "XDG_DATA_HOME": str(root / "mcp-data"),
        "TMPDIR": str(root),
        "CAO_HOME_DIR": str(caohome),
        "CAO_API_HOST": "127.0.0.1",
        "CAO_API_PORT": str(api_port),
        "CAO_MCP_APPS_ENABLED": "0",
        "PYTHONPATH": os.pathsep.join(
            [
                str(observer_directory),
                str(repo / "test/live_provider"),
                str(repo / "src"),
                str(repo),
            ]
        ),
        "CAO_OPENCODE_MCP_OBSERVER": "1",
        "CAO_OPENCODE_MCP_OBSERVER_PATH": str(observation),
        "CAO_OPENCODE_MCP_OBSERVER_NONCE": nonce,
        "CAO_OPENCODE_MCP_SOURCE_ROOT": str(repo),
        "CAO_MCP_RESTART_BARRIER_DIR": str(barrier),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    path = str(binary.parent) + ":" + str(Path(sys.executable).parent) + ":/usr/bin:/bin"
    installenv = {
        "HOME": str(home),
        "CAO_HOME_DIR": str(caohome),
        "PATH": path,
        "LANG": "C.UTF-8",
        "PYTHONPATH": os.pathsep.join([str(repo / "src"), str(repo)]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "XDG_DATA_HOME": str(home / ".local/share"),
        "TMPDIR": str(root),
        "CAO_MCP_APPS_ENABLED": "0",
    }
    if mixed:
        childenv["CAO_ENABLE_SENDER_ID_INJECTION"] = "true"
    profile = case["profile_name"]
    source = root / (profile + ".md")
    mcp_definition = {
        "command": sys.executable,
        "args": ["-m", "cli_agent_orchestrator.mcp_server.server"],
        "env": childenv,
        "cao_tool_profile": "collaboration" if mixed else "interaction",
        "timeout": 60000,
    }
    if not mixed:
        mcp_definition["enabled_tools"] = case["tools"]
    source.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                case["prompt"],
                name=profile,
                description="Private current native-v1 human interaction",
                provider="opencode_cli",
                role="developer",
                allowedTools=case["allowed_tools"],
                mcpServers={"cao-mcp-server": mcp_definition},
            ),
            sort_keys=False,
        )
    )
    admit("install")
    installed = subprocess.run(
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
        timeout=60,
    )
    if installed.returncode != 0:
        raise RuntimeError("production_install_failed")
    if mixed:
        receiver_case = json.loads(
            (
                repo
                / "specs/025-provider-mcp-profiles/fixtures/native-message-acceptance-profiles.json"
            ).read_text()
        )["profiles"]["receiver"]
        receiver_source = root / "v1-receiver.md"
        receiver_source.write_text(
            frontmatter.dumps(
                _native_v1_passive_receiver_profile(receiver_case, mcp_definition),
                sort_keys=False,
            )
        )
        receiver_install = subprocess.run(
            [
                sys.executable,
                "-m",
                "cli_agent_orchestrator.cli.main",
                "install",
                str(receiver_source),
                "--provider",
                "opencode_cli",
            ],
            env=installenv,
            cwd=repo,
            capture_output=True,
            timeout=60,
        )
        if receiver_install.returncode != 0:
            raise RuntimeError("production_install_failed")
    receipt["production_install_exact"] = True
    _copy_private_state_bundle(source_db, home / ".local/share/opencode")
    if legacy.exists():
        _copy_private_state_file(legacy, home / ".local/share/opencode/auth.json")
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
    cao_server_fixture._seed_packaged_skills(home)
    cao_server_fixture._seed_omp_e2e_state(home)
    extra = {
        "PATH": path,
        "TMUX_TMPDIR": str(tmuxdir),
        "CAO_DATABASE_CONFIG_FILE": str(targets.config_file),
        "CAO_TERMINAL_BACKEND": "tmux",
        "CAO_AUTH_MODE": "off",
        "CAO_AGUI_ENABLED": "true",
        "CAO_RUN_ROOT": str(root),
        "CAO_INTERACTION_AUDIT_FILE": str(audit_path),
        "CAO_V1_HUMAN_SCOPE_FILE": str(scope_path),
        "TERM": "xterm-256color",
        "PYTHONPATH": os.pathsep.join(
            [str(repo / "specs/025-provider-mcp-profiles/fixtures"), str(repo / "src"), str(repo)]
        ),
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
    }
    if mixed:
        extra["CAO_ENABLE_SENDER_ID_INJECTION"] = "true"
    approved = frozenset(
        {
            "OPENCODE_DISABLE_AUTOUPDATE",
            "OPENCODE_DISABLE_PROJECT_CONFIG",
            "OPENCODE_DISABLE_MODELS_FETCH",
        }
    )

    def start_api(role, stop=None):
        env = cao_server_fixture._subprocess_env(
            home, api_port, extra=extra, allowed_external_credentials=approved
        )
        log_path = raw / (role + ".private")
        log = open_private_log(log_path)
        worker = ApiWorkerController(
            WorkerSpec(
                (sys.executable, "-m", "native_human_api_worker", "--port", str(api_port)),
                home,
                env,
                api_port,
            ),
            stdout=log,
            stderr=log,
            readiness_timeout=min(45, stop - time.monotonic()) if stop else 45,
        )
        wrapped = SimpleNamespace(
            stop=worker.stop,
            url=f"http://127.0.0.1:{api_port}",
            log_path=log_path,
            worker=worker,
            log=log,
        )
        servers.append(wrapped)
        attempt = {"role": role, "server": wrapped, "identity": None, "cleanup_servers": [wrapped]}
        api_launch_attempts.append(attempt)
        worker.start()
        info = journey._process_info(worker.process.pid)
        if info is None or info[0].uid != os.getuid() or info[2] == "Z":
            raise RuntimeError("api_process_identity_unavailable")
        attempt["identity"] = info[0]
        return wrapped

    admit("api_start")
    server = start_api("api_a", stop=start + 180)
    receipt["api_healthy"] = True
    admit("terminal")
    session_name = ("t103-" if mixed else "t102-") + root.name[-8:]
    created = requests.post(
        server.url + "/sessions",
        params={
            "provider": "opencode_cli",
            "agent_profile": profile,
            "session_name": session_name,
            "working_directory": str(home),
            "model": model,
        },
        timeout=min(120, start + 180 - time.monotonic()),
    )
    if created.status_code != 201:
        raise RuntimeError("production_terminal_creation_failed")
    data = created.json()
    terminal_id, session_name, pane_name = _created_terminal_target(data)
    terminals["human"] = data
    owned |= journey._snapshot_and_recheck_private_tmux_server(
        sid, sockid, socket_path, terminal_id=terminal_id
    )
    roots = list((caohome / "opencode-v1").glob("terminal-*"))
    if len(roots) != 1:
        raise RuntimeError("private_native_root_mismatch")
    private = roots[0]
    generated = json.loads((private / "opencode.json").read_text())
    config_stat = (private / "opencode.json").stat()
    native_config_binding = {
        "config_sha256": _sha256(private / "opencode.json"),
        "config_device": config_stat.st_dev,
        "config_inode": config_stat.st_ino,
        "ownership_sha256": _sha256(private / ".cao-ownership.json"),
        "launch_model": model,
    }
    validate_v1_mcp_configuration(
        private, agent_name=profile, server_name="cao-mcp-server", require_interaction=True
    )
    native_agent = generated["agent"][profile]
    native_env = generated["mcp"]["cao-mcp-server"]["environment"]
    requested = frontmatter.loads(source.read_text()).content.strip()
    installed_prompt = parse_native_agent(
        (config / "agents" / (profile + ".md")).read_text(), profile
    )[0].content
    receipt["profile_configuration_exact"] = (
        requested == installed_prompt == native_agent["prompt"]
        and generated["model"] == model
        and set(generated["agent"]) == {profile}
    )
    receipt["native_permissions_exact"] = (
        set(data.get("allowed_tools") or []) == set(case["allowed_tools"])
        and native_env.get("CAO_INTERNAL_MCP_TOOL_PROFILE")
        == ("collaboration" if mixed else "interaction")
        and (
            ("CAO_INTERNAL_MCP_ENABLED_TOOLS" not in native_env)
            if mixed
            else set(json.loads(native_env["CAO_INTERNAL_MCP_ENABLED_TOOLS"])) == set(human.TOOLS)
        )
    )
    receipt["mcp_ready_exact"] = (
        has_ready_signal(Path(native_env[READY_FILE_ENV]), native_env[READY_TOKEN_ENV])
        and Path(native_env[READY_FILE_ENV]).parent == private / "mcp-ready"
    )
    cap = Path(native_env["CAO_INTERACTION_CAPABILITY_FILE"])
    cap.resolve().relative_to(caohome.resolve())
    receipt["runtime_capability_exact"] = (
        cap.is_file()
        and not cap.is_symlink()
        and (
            ("CAO_INTERNAL_INTERACTION_PULL_FLOW_ONLY" not in native_env)
            if mixed
            else native_env.get("CAO_INTERNAL_INTERACTION_PULL_FLOW_ONLY") == "1"
        )
    )
    authfile = private / "data/opencode/auth.json"
    receipt["credentials_isolated"] = (
        authfile.is_file()
        and not authfile.is_symlink()
        and authfile.stat().st_mode & 0o777 == 0o600
        and authfile.stat().st_nlink == 1
    )
    viewport = tmux.get_history(session_name, pane_name, strip_escapes=True, visible_only=True)
    receipt["native_model_ready"] = (
        "GLM-5.3-Flash" in viewport and "OpenCode Go" in viewport and "Big Pickle" not in viewport
    )
    receipt["private_http_boundary_exact"] = http_session.trust_env is False
    if any(
        receipt.get(key) is not True
        for key in (
            "profile_configuration_exact",
            "native_permissions_exact",
            "mcp_ready_exact",
            "runtime_capability_exact",
            "credentials_isolated",
            "native_model_ready",
        )
    ):
        raise RuntimeError("native_profile_preflight_failed")
    data = requests.get(server.url + "/terminals/" + terminal_id, timeout=3).json()
    incarnation = data["session_incarnation_id"]
    if mixed:
        receipt["mixed_profile_configuration_exact"] = all(
            receipt.get(k) is True
            for k in (
                "profile_configuration_exact",
                "native_permissions_exact",
                "runtime_capability_exact",
            )
        )
        admit("receiver_terminal")
        receiver_create_remaining = start + 180 - time.monotonic()
        if receiver_create_remaining <= 0:
            receipt["receiver_initialization_deadline_reached"] = True
            raise RuntimeError("human_journey_deadline")
        receipt["receiver_create_http_status"] = None
        receipt["receiver_create_transport_error"] = False
        receiver_create_started = time.monotonic()
        try:
            receiver_created = requests.post(
                server.url + "/sessions/" + session_name + "/terminals",
                params={
                    "provider": "opencode_cli",
                    "agent_profile": "v1-receiver",
                    "working_directory": str(home),
                    "model": model,
                    "defer_init": "true",
                },
                timeout=min(120, receiver_create_remaining),
            )
        except requests.RequestException:
            receipt["receiver_create_transport_error"] = True
            raise
        finally:
            receipt["receiver_create_elapsed_seconds"] = round(
                max(0.0, time.monotonic() - receiver_create_started), 3
            )
        receiver_created_status = getattr(receiver_created, "status_code", None)
        receipt["receiver_create_http_status"] = (
            receiver_created_status if type(receiver_created_status) is int else None
        )
        if receiver_created.status_code != 201:
            raise RuntimeError("production_terminal_creation_failed")
        receiver = receiver_created.json()
        receiver_id, receiver_session, receiver_pane = _created_terminal_target(receiver)
        if receiver_session != session_name or receiver_id == terminal_id:
            raise RuntimeError("receiver_identity_failed")
        terminals["receiver"] = receiver
        owned |= journey._snapshot_and_recheck_private_tmux_server(
            sid, sockid, socket_path, terminal_id=receiver_id
        )

        def receiver_provider_variant(stop):
            return _read_deferred_receiver_variant(
                capture_bytes,
                python=sys.executable,
                source_root=str(repo / "src"),
                config_file=str(targets.config_file),
                receiver_id=receiver_id,
                stop=stop,
                monotonic=time.monotonic,
            )

        def receiver_terminal(timeout):
            stop = min(start + 180, time.monotonic() + min(3, timeout))
            try:
                payload = http_json_bytes(
                    server.url,
                    "/terminals/" + receiver_id,
                    stop,
                    65536,
                )
            except HTTPStatusError as exc:
                return SimpleNamespace(status_code=exc.status_code, json=lambda: None)
            try:
                state = json.loads(payload)
            except (ValueError, UnicodeError):
                state = None
            return SimpleNamespace(status_code=200, json=lambda: state)

        rs = _wait_for_deferred_receiver_ready(
            receiver_terminal,
            receiver_provider_variant,
            receipt=receipt,
            journey_start=start,
            outer_deadline=start + 180,
            monotonic=time.monotonic,
            sleep=time.sleep,
        )
        receiver_roots = set((caohome / "opencode-v1").glob("terminal-*")) - {private}
        if len(receiver_roots) != 1:
            raise RuntimeError("private_native_root_mismatch")
        receiver_root = next(iter(receiver_roots))
        receiver_config = json.loads((receiver_root / "opencode.json").read_text())
        receiver_config_info = (receiver_root / "opencode.json").stat()
        receiver_config_binding = (
            receiver_config_info.st_dev,
            receiver_config_info.st_ino,
            _sha256(receiver_root / "opencode.json"),
            _sha256(receiver_root / ".cao-ownership.json"),
        )
        from cli_agent_orchestrator.utils.mcp_tool_policy import (
            MCP_TOOL_FILTER_ENV,
            MCP_TOOL_PROFILE_ENV,
        )

        receiver_mcp = receiver_config.get("mcp")
        if type(receiver_mcp) is not dict or set(receiver_mcp) != {"cao-mcp-server"}:
            raise RuntimeError("receiver_identity_failed")
        receiver_mcp_server = receiver_mcp["cao-mcp-server"]
        if type(receiver_mcp_server) is not dict:
            raise RuntimeError("receiver_identity_failed")
        receiver_mcp_environment = receiver_mcp_server.get("environment")
        if type(receiver_mcp_environment) is not dict:
            raise RuntimeError("receiver_identity_failed")
        receiver_capability_path = _receiver_interaction_capability_path(
            caohome, receiver_id, rs.get("session_incarnation_id")
        )
        receiver_capability_absent_at_config = _interaction_capability_file_absent(
            receiver_capability_path
        )
        receiver_capability_absence_exact_at_config = _receiver_capability_absence_exact(
            receiver_mcp_environment,
            receiver_capability_absent_at_config,
            receiver_config_binding,
            receiver_config_binding,
        )
        validate_v1_mcp_configuration(
            receiver_root,
            agent_name="v1-receiver",
            server_name="cao-mcp-server",
            require_interaction=False,
        )
        receipt["receiver_profile_configuration_exact"] = (
            receiver_config.get("model") == model
            and set(receiver_config.get("agent", {})) == {"v1-receiver"}
            and receiver_config["agent"]["v1-receiver"]["prompt"] == receiver_case["prompt"]
            and receiver_mcp_server.get("type") == "local"
            and receiver_mcp_server.get("enabled") is True
            and receiver_mcp_environment.get(MCP_TOOL_PROFILE_ENV) == "collaboration"
            and receiver_mcp_environment.get(MCP_TOOL_FILTER_ENV) == "[]"
            and receiver_capability_absence_exact_at_config is True
            and receiver_config.get("permission", {}).get("*") == "deny"
            and receiver.get("allowed_tools") == []
        )
        if not receipt["receiver_profile_configuration_exact"]:
            raise RuntimeError("receiver_identity_failed")
        receiver_initial_counts = (rs["turn_sequence"], rs["turn_completed"])
        if receiver_initial_counts != (0, 0):
            raise RuntimeError("receiver_identity_failed")
        from message_api_restart import PRE_RESTART_MESSAGE, expected_persisted_message

        if case["message"] != PRE_RESTART_MESSAGE:
            raise RuntimeError("native_case_contract_mismatch")
        message_expected = expected_persisted_message(case["message"], terminal_id)

        def message_rows():
            with connect(targets.main, readonly=True) as con:
                rows = con.execute(
                    "SELECT id,sender_id,receiver_id,message,status,created_at FROM inbox WHERE receiver_id=%s ORDER BY created_at,id",
                    (receiver_id,),
                ).fetchall()
                result = [dict(zip(row.keys(), row)) for row in rows]
            for row in result:
                row["created_at"] = row["created_at"].isoformat()
            response = requests.get(
                server.url + "/terminals/" + receiver_id + "/inbox/messages",
                params={"limit": 10},
                timeout=remaining_timeout(3) if deadline is not None else 3,
            )
            if response.status_code != 200:
                raise RuntimeError("mixed_message_failed")
            return result, response.json()

        def receiver_ack_snapshot(expected_identity, expected_counts, phase):
            if phase not in {"before_restart", "final"}:
                raise RuntimeError("mixed_receiver_ack_failed")
            status_field = "receiver_" + phase + "_terminal_status"
            ack_field = "receiver_" + phase + "_ack_text_exact"
            receipt_check_field = "receiver_" + phase + "_receipt_check_valid"
            allowed_statuses = {
                "idle",
                "completed",
                "processing",
                "error",
                "waiting_user_answer",
                "waiting_quota",
                "reconcile",
            }
            while time.monotonic() < min(deadline, start + 180):
                try:
                    body = json.loads(
                        http_json_bytes(
                            server.url,
                            "/terminals/" + receiver_id + "/output?mode=last",
                            min(deadline, start + 180, time.monotonic() + 2),
                            6 * 65537 + 4096,
                        )
                    )
                except HTTPStatusError as exc:
                    if exc.status_code != 202:
                        raise
                else:
                    response = requests.get(
                        server.url + "/terminals/" + receiver_id, timeout=remaining_timeout(2)
                    )
                    if response.status_code != 200:
                        raise RuntimeError("mixed_receiver_ack_failed")
                    state = response.json()
                    status = state.get("status")
                    receipt[status_field] = (
                        status if type(status) is str and status in allowed_statuses else "UNKNOWN"
                    )
                    receipt[ack_field] = body.get("mode") == "last" and body.get("output") == "ACK"
                    counts = (state.get("turn_sequence"), state.get("turn_completed"))
                    if any(type(value) is not int or value < 0 for value in counts) or any(
                        value > limit for value, limit in zip(counts, expected_counts)
                    ):
                        raise RuntimeError("mixed_receiver_ack_failed")
                    receipt["receiver_" + phase + "_sequence"] = counts[0]
                    receipt["receiver_" + phase + "_completed"] = counts[1]
                    if (
                        body.get("mode") == "last"
                        and body.get("output") == "ACK"
                        and state.get("status") == "completed"
                        and counts == expected_counts
                    ):
                        receipt[receipt_check_field] = False
                        with connect(targets.main, readonly=True) as con:
                            rows = con.execute(
                                "SELECT generation,receipt_sha256,result_sha256,phase FROM terminal_turn_receipts WHERE terminal_id=%s",
                                (receiver_id,),
                            ).fetchall()
                        valid = (
                            len(rows) == 1
                            and rows[0]["phase"] == "result_verified"
                            and (
                                expected_identity is None
                                or (rows[0]["generation"], rows[0]["receipt_sha256"])
                                == expected_identity
                            )
                            and all(
                                type(rows[0][key]) is str
                                and re.fullmatch(pattern, rows[0][key]) is not None
                                for key, pattern in (
                                    ("generation", r"[0-9a-f]{32}"),
                                    ("receipt_sha256", r"[0-9a-f]{64}"),
                                    ("result_sha256", r"[0-9a-f]{64}"),
                                )
                            )
                        )
                        receipt[receipt_check_field] = valid is True
                        if not valid:
                            raise RuntimeError("mixed_receiver_ack_failed")
                        check_deadline()
                        if expected_identity is None:
                            receipt["receiver_before_restart_identity"] = (
                                rows[0]["generation"],
                                rows[0]["receipt_sha256"],
                            )
                        return counts
                time.sleep(min(0.1, max(0, min(deadline, start + 180) - time.monotonic())))
            raise RuntimeError("mixed_receiver_ack_failed")

        deadline = None
        if any(message_rows()):
            raise RuntimeError("mixed_message_failed")
        import message_api_restart_live as message_live

        namespace = dict(vars(message_live))
        namespace["requests"] = SimpleNamespace(
            get=http_session.get,
            RequestException=message_live.requests.RequestException,
            Response=message_live.requests.Response,
        )
        exec(
            compile(
                inspect.getsource(message_live.AguiCapture), "<source-bound-private-agui>", "exec"
            ),
            namespace,
        )
        message_capture = namespace["AguiCapture"](server.url, terminal_id, receiver_id)
        captures.append(message_capture)
        message_capture.start(timeout=min(20, start + 180 - time.monotonic()))
    receipt["initialization_complete"] = True

    def ledger():
        with connect(targets.main, readonly=True) as con:
            rows = con.execute(
                "SELECT q.question_id,q.version,q.group_id,q.terminal_id,q.incarnation_id,q.lifecycle,q.client_key,q.kind,q.required,q.prompt_content,q.options_json,a.payload_json,a.question_version,a.actor_kind,a.idempotency_key,d.state FROM interaction_questions q LEFT JOIN interaction_answers a ON a.question_id=q.question_id LEFT JOIN interaction_deliveries d ON d.answer_id=a.answer_id WHERE q.terminal_id=%s AND q.source_kind='explicit' ORDER BY q.created_at,q.question_id",
                (terminal_id,),
            ).fetchall()
            return [dict(zip(row.keys(), row)) for row in rows]

    def turn_row():
        with connect(targets.main, readonly=True) as con:
            row = con.execute(
                "SELECT generation,receipt_sha256,result_sha256,phase FROM terminal_turn_receipts WHERE terminal_id=%s",
                (terminal_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("durable_turn_unavailable")
            result = dict(zip(row.keys(), row))
            if (
                type(result["generation"]) is not str
                or re.fullmatch(r"[0-9a-f]{32}", result["generation"]) is None
                or type(result["receipt_sha256"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", result["receipt_sha256"]) is None
                or result["phase"] not in {"prepared", "sent", "result_verified"}
            ):
                raise RuntimeError("durable_turn_unavailable")
            return result

    def check_deadline():
        if time.monotonic() >= deadline or time.monotonic() >= start + 180:
            raise RuntimeError("human_journey_deadline")

    snapshot_count = 0

    def remaining_timeout(limit):
        remaining = min(deadline, start + 180) - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("human_journey_deadline")
        return min(limit, remaining)

    def snapshot(expected, lifecycle, delivery):
        global snapshot_count
        check_deadline()
        result = common._read_agui_snapshot(
            server.url,
            forbidden_content=forbidden,
            timeout_seconds=remaining_timeout(8),
        )
        check_deadline()
        if not common._snapshot_matches_questions(
            result, expected, lifecycle=lifecycle, delivery_state=delivery
        ):
            raise RuntimeError("human_snapshot_mismatch")
        snapshot_count += 1
        return snapshot_identity_map(result)

    operation = "t102-" + os.urandom(12).hex()
    names = {name: "cao_mcp_server_" + name for name in human.TOOLS}
    prompt = (
        "Use only the three CAO interaction tools. Call "
        + names["cao_ask_user"]
        + " exactly once with operation_key="
        + repr(operation)
        + ", wait_for_required=true, and these exact questions: "
        + json.dumps(case["questions"], ensure_ascii=False)
        + ". After it returns, call "
        + names["cao_wait_for_user_answers"]
        + " with the exact group_id and wait_seconds=20. Repeat only pending or unavailable waits for that same group within75 seconds; fail on any other error. When ready, acknowledge both answers' exact question_id, version and receipt using "
        + names["cao_acknowledge_user_answers"]
        + ". Never answer on behalf of the human. Finish with the exact line: Validado ñ, 中文."
    )
    if mixed:
        prompt = (
            "First call cao_mcp_server_send_message exactly once with receiver_id="
            + repr(receiver_id)
            + " and exact message="
            + repr(case["message"])
            + ". Require success; do not retry or call discovery. Then "
            + prompt.replace("Use only the three CAO interaction tools. ", "")
            + " Use no other offered tools."
        )
    forbidden = tuple(q["prompt"] for q in case["questions"]) + (
        case["human_answers"]["test-color"]["text"],
        "Sapphire",
        "Amber",
    )
    if mixed:
        forbidden += (case["message"], message_expected)
    admit("model")
    deadline = None
    model_start = None

    def mark_native_phase(phase):
        global deadline, model_start
        if phase != "native_send_input":
            raise RuntimeError("invalid_native_send_phase")
        deadline = journey._publish_agui_native_deadline_before_send(
            barrier_directory=barrier, existing_deadline=deadline
        )
        model_start = deadline - 75
        receipt["deadline_published_once"] = True

    def send_native_input():
        mark_native_phase("native_send_input")
        return requests.post(
            server.url + "/terminals/" + terminal_id + "/input",
            params={"message": prompt},
            timeout=min(15, deadline - time.monotonic(), start + 180 - time.monotonic()),
        )

    response = send_native_input()
    check_deadline()
    if response.status_code != 200 or type(response.json().get("turn_sequence")) is not int:
        raise RuntimeError("human_input_failed")
    while time.monotonic() < min(deadline, start + 180):
        pending = ledger()
        if len(pending) > 2:
            raise RuntimeError("duplicate_questions")
        if len(pending) == 2 and barrier_reached(barrier):
            break
        time.sleep(min(0.1, max(0, min(deadline, start + 180) - time.monotonic())))
    else:
        raise RuntimeError("human_questions_not_observed")
    receipt["questions_exact"] = human.questions_exact(
        pending, case["questions"], terminal_id, incarnation
    )
    if not receipt["questions_exact"]:
        raise RuntimeError("human_question_contract_failed")
    expected = human.identity(pending)
    group_id = pending[0]["group_id"]
    receipt["pending_ledger_before_restart_exact"] = human.ledger_exact(
        pending, expected, "open", None
    )
    before_snapshot = snapshot(expected, "open", None)
    receipt["agui_pending_before_restart_exact"] = True
    before_turn = turn_row()
    before_database = journey._private_file_identity(targets.config_file)
    before_native = journey._terminal_tagged_processes(
        terminal_id, socket_path, server_identity=sid
    )
    if not before_native or not all(journey._process_identity_alive(p) for p in before_native):
        raise RuntimeError("native_identity_unavailable")
    from native_human_api_worker import native_launch_model_exact

    receipt["native_model_argv_before_restart_exact"] = native_launch_model_exact(
        before_native,
        binary,
        model,
        profile,
        journey._process_identity_alive,
        peer_agents=frozenset({profile, "v1-receiver"}) if mixed else None,
    )
    if not receipt["native_model_argv_before_restart_exact"]:
        raise RuntimeError("native_model_argv_failed")
    if mixed:
        receiver_native_before = journey._terminal_tagged_processes(
            receiver_id, socket_path, server_identity=sid
        )
        receipt["receiver_model_argv_exact"] = native_launch_model_exact(
            receiver_native_before,
            binary,
            model,
            "v1-receiver",
            journey._process_identity_alive,
            peer_agents=frozenset({profile, "v1-receiver"}),
        )
        receiver_ack_snapshot(
            None,
            tuple(value + 1 for value in receiver_initial_counts),
            "before_restart",
        )
        before_receiver_turn = receipt["receiver_before_restart_identity"]
        before_message_pg, before_message_api = message_rows()
        receipt["receiver_one_turn_before_restart_exact"] = True
        receipt["message_pair_before_restart_exact"] = mixed_contract.message_pair_exact(
            before_message_pg, before_message_api, terminal_id, receiver_id, message_expected
        )
        notifications = []
        while time.monotonic() < min(deadline, start + 180):
            notifications.extend(
                message_capture.take_until(min(deadline, start + 180, time.monotonic() + 0.1))
            )
            if notifications:
                break
        duplicate_window_stop = time.monotonic() + 1
        if duplicate_window_stop >= min(deadline, start + 180):
            raise RuntimeError("mixed_message_failed")
        notifications.extend(message_capture.take_until(duplicate_window_stop))
        message_capture.stop()
        notifications.extend(message_capture.take_until(time.monotonic()))
        check_deadline()
        receipt["message_agui_notification_exact"] = (
            notifications == [True]
            and not message_capture._failed
            and not message_capture._thread.is_alive()
        )
        if not all(
            receipt.get(k) is True
            for k in (
                "receiver_model_argv_exact",
                "message_pair_before_restart_exact",
                "message_agui_notification_exact",
            )
        ):
            raise RuntimeError("mixed_message_failed")
    preaudit = common._agui_observation_diagnostics(audit_path, group_id, expected)
    receipt["restart_before_wait_exact"] = (
        barrier_reached(barrier)
        and preaudit["create_group_seen"] is True
        and preaudit["created_group_matches_database"] is True
        and preaudit["created_questions_match_database"] is True
        and preaudit["wait_call_count"] == 0
        and preaudit["ack_call_count"] == 0
    )
    if not receipt["restart_before_wait_exact"]:
        raise RuntimeError("restart_not_before_wait")
    admit("api_restart")
    old_identity = api_launch_attempts[-1]["identity"]
    previous = server
    previous.stop()
    server = start_api("api_b", stop=min(deadline, start + 180))
    check_deadline()
    receipt["old_api_worker_dead"] = not journey._process_identity_alive(old_identity)
    receipt["new_api_worker_identity_exact"] = api_launch_attempts[-1]["identity"] != old_identity
    after = ledger()
    receipt["pending_ledger_after_restart_exact"] = human.ledger_exact(
        after, expected, "open", None
    )
    receipt["database_config_identity_stable"] = (
        journey._private_file_identity(targets.config_file) == before_database
        and turn_row() == before_turn
    )
    after_snapshot = snapshot(expected, "open", None)
    receipt["agui_pending_after_restart_exact"] = after_snapshot == before_snapshot
    after_native = journey._terminal_tagged_processes(terminal_id, socket_path, server_identity=sid)
    receipt["native_provider_mcp_identities_stable"] = after_native == before_native and all(
        journey._process_identity_alive(p) for p in after_native
    )
    receipt["native_model_argv_after_restart_exact"] = native_launch_model_exact(
        after_native,
        binary,
        model,
        profile,
        journey._process_identity_alive,
        peer_agents=frozenset({profile, "v1-receiver"}) if mixed else None,
    )
    native_config_binding["launch_model_observed"] = (
        receipt["native_model_argv_before_restart_exact"] is True
        and receipt["native_model_argv_after_restart_exact"] is True
    )
    if any(
        receipt.get(key) is not True
        for key in (
            "native_model_argv_after_restart_exact",
            "old_api_worker_dead",
            "new_api_worker_identity_exact",
            "pending_ledger_before_restart_exact",
            "pending_ledger_after_restart_exact",
            "agui_pending_after_restart_exact",
            "native_provider_mcp_identities_stable",
            "database_config_identity_stable",
        )
    ):
        raise RuntimeError("human_restart_identity_failed")
    if mixed:
        after_message_pg, after_message_api = message_rows()
        receipt["message_pair_after_restart_exact"] = mixed_contract.message_pair_exact(
            after_message_pg, after_message_api, terminal_id, receiver_id, message_expected
        )
        receipt["message_identity_survives_restart"] = mixed_contract.message_survives_restart(
            before_message_pg, after_message_pg
        ) and mixed_contract.message_survives_restart(before_message_api, after_message_api)
        receiver_native_after = journey._terminal_tagged_processes(
            receiver_id, socket_path, server_identity=sid
        )
        receiver_after_info = (receiver_root / "opencode.json").stat()
        receiver_binding_stable = receiver_config_binding == (
            receiver_after_info.st_dev,
            receiver_after_info.st_ino,
            _sha256(receiver_root / "opencode.json"),
            _sha256(receiver_root / ".cao-ownership.json"),
        )
        receipt["receiver_model_argv_exact"] = receipt[
            "receiver_model_argv_exact"
        ] and native_launch_model_exact(
            receiver_native_after,
            binary,
            model,
            "v1-receiver",
            journey._process_identity_alive,
            peer_agents=frozenset({profile, "v1-receiver"}),
        )
        receipt["receiver_native_identity_stable"] = (
            bool(receiver_native_before)
            and receiver_binding_stable
            and receiver_native_before == receiver_native_after
            and all(journey._process_identity_alive(p) for p in receiver_native_after)
        )
        if not all(
            receipt.get(k) is True
            for k in (
                "message_pair_after_restart_exact",
                "receiver_model_argv_exact",
                "message_identity_survives_restart",
                "receiver_native_identity_stable",
            )
        ):
            raise RuntimeError("mixed_message_failed")
    if mixed:
        receiver_settled = 0
        receiver_last_counts = None
        while time.monotonic() < min(deadline, start + 180):
            response = requests.get(
                server.url + "/terminals/" + receiver_id, timeout=remaining_timeout(2)
            )
            if response.status_code != 200:
                raise RuntimeError("receiver_identity_failed")
            value = response.json()
            counts = (value.get("turn_sequence"), value.get("turn_completed"))
            valid = (
                all(type(v) is int and v >= 0 for v in counts)
                and counts[0] == counts[1]
                and value.get("status") in {"idle", "completed"}
            )
            receiver_settled = (
                receiver_settled + 1
                if valid and counts == receiver_last_counts
                else (1 if valid else 0)
            )
            receiver_last_counts = counts
            if receiver_settled == 3:
                receiver_restart_counts = counts
                break
            time.sleep(min(0.1, max(0, min(deadline, start + 180) - time.monotonic())))
        else:
            raise RuntimeError("receiver_identity_failed")
    release_barrier(barrier)
    barrier_resolved = True
    receipt["create_response_released"] = True
    for row in after:
        check_deadline()
        key = row["client_key"]
        answer = case["human_answers"][key]
        response = requests.post(
            server.url + "/interactions/" + row["question_id"] + "/answers",
            headers={
                "If-Match": '"' + str(row["version"]) + '"',
                "Idempotency-Key": operation + "-" + key,
            },
            json={"expected_version": row["version"], "response": answer},
            timeout=remaining_timeout(8),
        )
        check_deadline()
        if response.status_code not in (200, 201):
            raise RuntimeError("human_answer_failed")
        receipt["human_answers_posted_count"] = receipt.get("human_answers_posted_count", 0) + 1
    while time.monotonic() < min(deadline, start + 180):
        final = ledger()
        if len(final) > 2:
            raise RuntimeError("duplicate_questions")
        if human.ledger_exact(final, expected, "answered", "agent_acknowledged"):
            break
        time.sleep(min(0.1, max(0, min(deadline, start + 180) - time.monotonic())))
    else:
        raise RuntimeError("human_ack_missing")
    from cli_agent_orchestrator.services.interactions.models import InteractionAnswerPayload

    for row in final:
        check_deadline()
        answer = case["human_answers"][row["client_key"]]
        normalized = InteractionAnswerPayload.model_validate(answer).model_dump(
            mode="json", exclude_none=True
        )
        exact = (
            json.loads(row["payload_json"]) == normalized
            and type(row["question_version"]) is int
            and row["question_version"] == row["version"]
            and row["actor_kind"] == "principal"
            and row["idempotency_key"] == operation + "-" + row["client_key"]
        )
        receipt[
            (
                "human_text_answer_exact"
                if row["client_key"] == "test-color"
                else "human_choice_answer_exact"
            )
        ] = exact
    receipt["exact_ack"] = human.ledger_exact(final, expected, "answered", "agent_acknowledged")
    final_snapshot = snapshot(expected, "answered", "agent_acknowledged")
    receipt["agui_final_snapshot_exact"] = set(final_snapshot) == set(expected)
    receipt["pending_snapshot_redacted_three"] = snapshot_count == 3
    diag = common._agui_observation_diagnostics(audit_path, group_id, expected)
    receipt["ack_audit_exact"] = human.audit_exact(diag)
    from test.live_provider.interaction_agui_restart import read_observations

    receipt["wait_history_exact"] = human.wait_history_exact(
        read_observations(audit_path), group_id, expected
    )
    receipt["ack_audit"] = diag
    while time.monotonic() < min(deadline, start + 180):
        try:
            body = http_json_bytes(
                server.url,
                "/terminals/" + terminal_id + "/output?mode=last",
                min(deadline, start + 180, time.monotonic() + 2),
                6 * 65537 + 4096,
            )
        except HTTPStatusError as exc:
            receipt["last_output_http_status"] = exc.status_code
            if exc.status_code != 202:
                raise
            receipt["last_output_pending_observations"] = (
                receipt.get("last_output_pending_observations", 0) + 1
            )
            time.sleep(min(0.1, max(0, min(deadline, start + 180) - time.monotonic())))
            continue
        receipt["last_output_http_status"] = 200
        value = json.loads(body)
        state = requests.get(
            server.url + "/terminals/" + terminal_id, timeout=remaining_timeout(2)
        ).json()
        last = value.get("output", "")
        if (
            type(last) is str
            and len(last.encode("utf-8")) <= 65537
            and value.get("mode") == "last"
            and "Validado ñ, 中文." in last
            and state.get("status") == "completed"
        ):
            break
        time.sleep(min(0.1, max(0, min(deadline, start + 180) - time.monotonic())))
    else:
        raise RuntimeError("human_final_output_missing")
    check_deadline()
    final_turn = turn_row()
    receipt["last_active_receipt_exact"] = (
        final_turn["phase"] == "result_verified"
        and final_turn["generation"] == before_turn["generation"]
        and final_turn["receipt_sha256"] == before_turn["receipt_sha256"]
        and type(final_turn["result_sha256"]) is str
        and re.fullmatch(r"[0-9a-f]{64}", final_turn["result_sha256"]) is not None
    )
    receipt["unicode_response_exact"] = (
        type(last) is str and "Validado ñ, 中文." in last and value.get("mode") == "last"
    )
    with scope_path.open("x") as stream:
        scope_path.chmod(0o600)
        json.dump(
            {
                "terminal_id": terminal_id,
                "model": model,
                "deadline": deadline,
                "nonce": probe_nonce,
                **native_config_binding,
            },
            stream,
        )
    receipt["native_provider_status"] = "NOT_OBSERVED"
    observed = requests.get(
        server.url + "/__v1/human-status",
        headers={"X-CAOS-Probe": probe_nonce},
        timeout=remaining_timeout(2),
    )
    check_deadline()
    receipt["native_provider_probe_http_status"] = observed.status_code
    if observed.status_code != 200:
        raise RuntimeError("native_probe_http_failed")
    probe = observed.json()
    if type(probe) is not dict:
        raise RuntimeError("native_probe_shape_invalid")
    receipt["native_provider_probe_flags"] = {
        key: probe.get(key) is True
        for key in (
            "provider_instance_stable",
            "provider_major_exact",
            "provider_model_exact",
            "deadline_met",
        )
    }
    receipt["native_provider_probe_redacted"] = probe.get("raw_text_exported") is False
    receipt["native_provider_final_state_exact"] = (
        all(
            probe.get(k) is True
            for k in (
                "provider_instance_stable",
                "provider_major_exact",
                "provider_model_exact",
                "deadline_met",
            )
        )
        and probe.get("provider_status") in {"IDLE", "COMPLETED"}
        and probe.get("raw_text_exported") is False
    )
    receipt["native_provider_status"] = (
        probe.get("provider_status")
        if probe.get("provider_status")
        in {
            "IDLE",
            "COMPLETED",
            "PROCESSING",
            "ERROR",
            "WAITING_USER_ANSWER",
            "WAITING_QUOTA",
            "RECONCILE",
        }
        else "UNKNOWN"
    )
    receipt["native_turn_completed_within_75_seconds"] = (
        time.monotonic() < deadline
        and state.get("status") == "completed"
        and receipt["last_active_receipt_exact"]
    )
    if mixed:
        receiver_ack_snapshot(before_receiver_turn, receiver_restart_counts, "final")
        receipt["receiver_ack_exact"] = True
        receipt["receiver_verified_receipt_exact"] = True
        receipt["receiver_no_new_turn_after_restart_exact"] = True
        check_deadline()
    receipt["model_seconds"] = time.monotonic() - model_start
    outer_remaining = start + 180 - time.monotonic()
    if outer_remaining <= 0:
        raise RuntimeError("human_journey_deadline")
    if mixed:
        sender_observation = _observer_path_for_terminal(observation, terminal_id, mixed=True)
        receiver_observation = _observer_path_for_terminal(observation, receiver_id, mixed=True)
        response = requests.post(
            server.url + "/terminals/" + terminal_id + "/exit",
            timeout=min(15, outer_remaining),
        )
        receipt["graceful_exit_http_status"] = response.status_code
        outer_remaining = start + 180 - time.monotonic()
        if outer_remaining <= 0:
            raise RuntimeError("human_journey_deadline")
        receiver_response = requests.post(
            server.url + "/terminals/" + receiver_id + "/exit",
            timeout=min(15, outer_remaining),
        )
        receipt["receiver_graceful_exit_http_status"] = receiver_response.status_code
        seal_stop = min(time.monotonic() + 10, start + 180)
        sender_seal = sender_observation.with_name(sender_observation.name + ".complete")
        receiver_seal = receiver_observation.with_name(receiver_observation.name + ".complete")
        while time.monotonic() < seal_stop and not (
            sender_seal.exists() and receiver_seal.exists()
        ):
            time.sleep(0.1)
        observed_protocol = observer_module.read_protocol_observations(
            sender_observation, expected_nonce=nonce
        )
        receiver_protocol = observer_module.read_protocol_observations(
            receiver_observation, expected_nonce=nonce
        )
        seal_observed_at = time.monotonic()
        receipt["observer_graceful_seal"] = (
            response.status_code == 200
            and _graceful_seals_seen_by_deadline(
                observed_protocol.get("mcp_observer_shutdown_observed") is True,
                seal_observed_at,
                seal_stop,
            )
        )
        receipt["receiver_observer_graceful_seal_exact"] = (
            receiver_response.status_code == 200
            and _graceful_seals_seen_by_deadline(
                receiver_protocol.get("mcp_observer_shutdown_observed") is True,
                seal_observed_at,
                seal_stop,
            )
        )
        receipt["protocol_exact"] = mixed_contract.protocol_exact(observed_protocol)
        sender_rows = _private_protocol_rows(sender_observation, nonce)
        receiver_rows = _private_protocol_rows(receiver_observation, nonce)
        receipt["every_catalog_exact"] = mixed_contract.catalogs_exact(sender_rows)
        receipt["receiver_protocol_exact"] = mixed_contract.receiver_protocol_exact(
            receiver_protocol
        )
        receipt["receiver_catalogs_empty_exact"] = mixed_contract.receiver_catalogs_empty_exact(
            receiver_rows, receiver_protocol
        )
        sender_identity = _observer_sidecar_identity(sender_observation)
        receiver_identity = _observer_sidecar_identity(receiver_observation)
        receipt["receiver_observer_isolation_exact"] = (
            mixed_contract.observer_paths_exact(
                str(observation),
                str(sender_observation),
                str(receiver_observation),
                terminal_id,
                receiver_id,
            )
            and not observation.exists()
            and sender_identity is not None
            and receiver_identity is not None
            and sender_identity != receiver_identity
        )
        receiver_final_config_info = (receiver_root / "opencode.json").stat()
        receiver_config_current_binding = (
            receiver_final_config_info.st_dev,
            receiver_final_config_info.st_ino,
            _sha256(receiver_root / "opencode.json"),
            _sha256(receiver_root / ".cao-ownership.json"),
        )
        receiver_capability_absent_at_final = _interaction_capability_file_absent(
            receiver_capability_path
        )
        receipt["receiver_no_runtime_human_capability_exact"] = (
            receipt.get("receiver_profile_configuration_exact") is True
            and _receiver_capability_absence_exact(
                receiver_mcp_environment,
                receiver_capability_absent_at_config is True
                and receiver_capability_absent_at_final is True,
                receiver_config_binding,
                receiver_config_current_binding,
            )
            and receipt["receiver_protocol_exact"] is True
            and receipt["receiver_catalogs_empty_exact"] is True
        )
        receipt["protocol_observation"] = observed_protocol
        receipt["receiver_protocol_observation"] = receiver_protocol
    else:
        response = requests.post(
            server.url + "/terminals/" + terminal_id + "/exit", timeout=min(15, outer_remaining)
        )
        receipt["graceful_exit_http_status"] = response.status_code
        seal_stop = min(time.monotonic() + 10, start + 180)
        while (
            time.monotonic() < seal_stop
            and not observation.with_name(observation.name + ".complete").exists()
        ):
            time.sleep(0.1)
        observed_protocol = observer_module.read_protocol_observations(
            observation, expected_nonce=nonce
        )
        receipt["observer_graceful_seal"] = (
            response.status_code == 200
            and _graceful_seals_seen_by_deadline(
                observed_protocol.get("mcp_observer_shutdown_observed") is True,
                time.monotonic(),
                seal_stop,
            )
        )
        receipt["protocol_exact"] = human.protocol_exact(observed_protocol)
        with os.fdopen(
            os.open(observation, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb"
        ) as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > 131072
            ):
                raise RuntimeError("protocol_sidecar_invalid")
            data = stream.read(131073)
        rows = [json.loads(line) for line in data.splitlines()]
        if (
            len(data) > 131072
            or not data.endswith(b"\n")
            or not all(type(row) is dict and row.get("nonce") == nonce for row in rows)
        ):
            raise RuntimeError("protocol_sidecar_invalid")
        receipt["every_catalog_exact"] = human.catalogs_exact(rows)
        receipt["protocol_observation"] = observed_protocol
    receipt["journey_seconds"] = time.monotonic() - start
    receipt["journey_within_180_seconds"] = receipt["journey_seconds"] <= 180

except Exception as exc:
    receipt["error_type"] = type(exc).__name__
    allowed_errors = {
        "receiver_identity_failed",
        "mixed_message_failed",
        "mixed_receiver_ack_failed",
        "native_model_argv_failed",
        "native_probe_http_failed",
        "native_probe_shape_invalid",
        "http_status",
        "http_transport",
        "reader_deadline",
        "http_framing",
        "http_eof",
        "http_body_limit",
        "http_header_limit",
        "created_terminal_target_invalid",
        "protocol_sidecar_invalid",
        "mcp_observer_source_invalid",
        "mcp_observer_source_mismatch",
        "mcp_observer_terminal_binding_invalid",
        "native_case_contract_mismatch",
        "invalid_native_send_phase",
        "postgres_identity_mismatch",
        "private_api_route_invalid",
        "production_install_failed",
        "production_terminal_creation_failed",
        "receiver_deferred_initialization_failed",
        "receiver_initialization_deadline",
        "receiver_readiness_request_failed",
        "receiver_readiness_http_failed",
        "receiver_readiness_response_invalid",
        "receiver_readiness_database_failed",
        "private_native_root_mismatch",
        "native_profile_preflight_failed",
        "durable_turn_unavailable",
        "human_journey_deadline",
        "human_snapshot_mismatch",
        "human_input_failed",
        "duplicate_questions",
        "human_questions_not_observed",
        "human_question_contract_failed",
        "native_identity_unavailable",
        "restart_not_before_wait",
        "human_restart_identity_failed",
        "human_answer_failed",
        "human_ack_missing",
        "human_final_output_missing",
        "memory_below_unchanged_6144_MiB_floor",
        "api_process_identity_unavailable",
    }
    receipt["error_code"] = str(exc) if str(exc) in allowed_errors else "local_diagnostic_error"
finally:
    if "barrier" in globals() and not barrier_resolved:
        try:
            from test.live_provider.interaction_opencode_mcp_gate import release_barrier

            release_barrier(barrier, abort=True)
        except BaseException:
            failures.add("barrier_abort_failed")
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
    for candidate in servers:
        with contextlib.suppress(Exception):
            candidate.log.close()
    with contextlib.suppress(Exception):
        current_pg = subprocess.check_output(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Id}} {{.Image}} {{.State.Running}}",
                expected_pg_container_id,
            ],
            text=True,
            timeout=10,
        )
        receipt["shared_postgres_untouched"] = (
            _postgres_runtime_identity_matches(
                service.metadata, expected_pg_container_id, expected_pg_image_id, current_pg
            )
            and int(
                subprocess.check_output(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        "{{.RestartCount}}",
                        expected_pg_container_id,
                    ],
                    text=True,
                    timeout=10,
                )
            )
            == postgres_restart_count_before
        )
    if "observation" in globals() and observer_module is not None:
        with contextlib.suppress(Exception):
            if mixed and terminal_id:
                sender_observation = _observer_path_for_terminal(
                    observation, terminal_id, mixed=True
                )
                receipt["protocol_observation"] = observer_module.read_protocol_observations(
                    sender_observation, expected_nonce=nonce
                )
                if receiver_id:
                    receiver_observation = _observer_path_for_terminal(
                        observation, receiver_id, mixed=True
                    )
                    receipt["receiver_protocol_observation"] = (
                        observer_module.read_protocol_observations(
                            receiver_observation, expected_nonce=nonce
                        )
                    )
            else:
                receipt["protocol_observation"] = observer_module.read_protocol_observations(
                    observation, expected_nonce=nonce
                )
    receipt["tmux_cleanup"] = receipt.get("tmux_processes_clean") is True
    receipt["cleanup_status"] = (
        "complete"
        if receipt.get("api_cleanup") is True
        and receipt.get("postgres_cleanup") is True
        and receipt["tmux_cleanup"]
        and not receipt["cleanup_failure_codes"]
        else "incomplete"
    )
    values = [v for k, v in receipt.items() if k.endswith("_admission_mib") and type(v) is int]
    receipt["minimum_admission_mib"] = min(values) if values else 0
    receipt["success"] = (mixed_contract if mixed else human).final_exact(receipt)
    for child in raw.rglob("*"):
        if child.is_file() and not child.is_symlink():
            child.chmod(0o600)
    output = cache / (label + "-receipt.json")
    with output.open("x") as stream:
        output.chmod(0o600)
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in receipt.items()
                if type(v) is bool
                or type(v) in (int, float)
                or k in ("error_code", "error_type", "cleanup_failure_codes", "cleanup_status")
            }
        ),
        flush=True,
    )
raise SystemExit(0 if receipt["success"] else 1)
