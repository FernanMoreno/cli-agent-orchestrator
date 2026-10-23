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
label = "t102-v1-human"
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
sourcefiles = set(
    subprocess.check_output(
        [
            "git",
            "ls-files",
            "src",
            "test/live_provider",
            "test/fixtures/cao_server.py",
            "test/postgres/fixtures.py",
        ],
        cwd=repo,
        text=True,
    ).splitlines()
)
if len(sourcefiles) != 507 or sourcefiles != set(m["source_files"]):
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
from native_phase_readers import OwnedPhaseReaders, http_json_bytes

case = json.loads(
    (
        repo / "specs/025-provider-mcp-profiles/fixtures/native-human-interaction-profile.json"
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
    "scope": "current managed-v1 human Q&A; committed-response pre-wait API restart; not expanded profiles or global acceptance",
    "model": case["model"],
    "case": "T102",
    "diagnostic_only": True,
    "acceptance_claimed": False,
    "original_model_acceptance_claimed": False,
    "global_acceptance_claimed": False,
    "source_pin_file_count": 507,
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
    childenv = {
        "HOME": str(root / "mcp-home"),
        "XDG_DATA_HOME": str(root / "mcp-data"),
        "TMPDIR": str(root),
        "CAO_HOME_DIR": str(caohome),
        "CAO_API_HOST": "127.0.0.1",
        "CAO_API_PORT": str(api_port),
        "CAO_MCP_APPS_ENABLED": "0",
        "PYTHONPATH": os.pathsep.join(
            [str(repo / "test/live_provider"), str(repo / "src"), str(repo)]
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
    profile = case["profile_name"]
    source = root / (profile + ".md")
    source.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                case["prompt"],
                name=profile,
                description="Private current native-v1 human interaction",
                provider="opencode_cli",
                role="developer",
                allowedTools=case["allowed_tools"],
                mcpServers={
                    "cao-mcp-server": {
                        "command": sys.executable,
                        "args": ["-m", "cli_agent_orchestrator.mcp_server.server"],
                        "env": childenv,
                        "cao_tool_profile": "interaction",
                        "enabled_tools": case["tools"],
                        "timeout": 60000,
                    }
                },
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
    session_name = "t102-" + root.name[-8:]
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
        and native_env.get("CAO_INTERNAL_MCP_TOOL_PROFILE") == "interaction"
        and set(json.loads(native_env["CAO_INTERNAL_MCP_ENABLED_TOOLS"])) == set(human.TOOLS)
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
        and native_env.get("CAO_INTERNAL_INTERACTION_PULL_FLOW_ONLY") == "1"
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
    forbidden = tuple(q["prompt"] for q in case["questions"]) + (
        case["human_answers"]["test-color"]["text"],
        "Sapphire",
        "Amber",
    )
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
    if any(
        receipt.get(key) is not True
        for key in (
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
        body = http_json_bytes(
            server.url,
            "/terminals/" + terminal_id + "/output?mode=last",
            min(deadline, start + 180, time.monotonic() + 2),
            6 * 65537 + 4096,
        )
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
            },
            stream,
        )
    observed = requests.get(
        server.url + "/__v1/human-status",
        headers={"X-CAOS-Probe": probe_nonce},
        timeout=remaining_timeout(2),
    )
    check_deadline()
    probe = observed.json() if observed.status_code == 200 else {}
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
    receipt["native_provider_status"] = probe.get("provider_status", "UNKNOWN")
    receipt["native_turn_completed_within_75_seconds"] = (
        time.monotonic() < deadline
        and state.get("status") == "completed"
        and receipt["last_active_receipt_exact"]
    )
    receipt["model_seconds"] = time.monotonic() - model_start
    outer_remaining = start + 180 - time.monotonic()
    if outer_remaining <= 0:
        raise RuntimeError("human_journey_deadline")
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
        "created_terminal_target_invalid",
        "protocol_sidecar_invalid",
        "invalid_native_send_phase",
        "postgres_identity_mismatch",
        "private_api_route_invalid",
        "production_install_failed",
        "production_terminal_creation_failed",
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
    receipt["success"] = human.final_exact(receipt)
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
