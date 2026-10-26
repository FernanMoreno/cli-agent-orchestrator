"""Default-closed, source-bound paired CAO journey with a real OpenCode worker.

The coordinator uses the actual stdio MCP protocol; it is not a native model.
An external verified PostgreSQL owner must outlive this runner and its borrowers.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import inspect
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import native_paired_protocol as protocol
import run_native_v2_acceptance as native

RUN_ENABLED = False
MODEL = "opencode-go/glm-5.3-flash"
BINARY_SHA256 = "10d405161d8b9595f4a2ec31254e961969e6ca3f8239ac9bd55b6ff6431dc24f"
MODEL_SECONDS = 75
MEMORY_MIB = 6144
EXPECTED = "T168 comprobado: ñ, 中文."
WORKER = "t168-native-worker"
CALLER = "t168-stdio-coordinator"
EXTRA_SOURCES = (
    "specs/025-provider-mcp-profiles/fixtures/native_paired_protocol.py",
    "specs/025-provider-mcp-profiles/fixtures/run_native_paired_worker.py",
    "specs/025-provider-mcp-profiles/fixtures/test_native_paired_protocol.py",
    "specs/025-provider-mcp-profiles/fixtures/test_peer_cli_json.py",
    "test/providers/fixtures/bin/mock_cli",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources(head: str) -> dict[str, str]:
    paths = sorted(set(native._source_files(expected_head=head)) | set(EXTRA_SOURCES))
    if subprocess.run(["git", "diff", "--quiet", head, "--", *paths], cwd=native.REPO).returncode:
        raise protocol.HarnessError("source_not_committed_or_changed")
    raw = subprocess.check_output(
        ["git", "cat-file", "--batch"],
        cwd=native.REPO,
        input="".join(f"{head}:{name}\n" for name in paths).encode(),
    )
    result, cursor = {}, 0
    for name in paths:
        end = raw.index(b"\n", cursor)
        header = raw[cursor:end].split()
        if len(header) != 3 or header[1] != b"blob":
            raise protocol.HarnessError("source_not_committed_or_changed")
        size = int(header[2])
        data = raw[end + 1 : end + 1 + size]
        cursor = end + size + 2
        path = native.REPO / name
        if path.is_symlink() or path.read_bytes() != data:
            raise protocol.HarnessError("source_not_committed_or_changed")
        result[name] = hashlib.sha256(data).hexdigest()
    if cursor != len(raw):
        raise protocol.HarnessError("source_inventory_invalid")
    return result


def admit_memory(receipt: dict, phase: str) -> None:
    available = native._available_memory_mib()
    receipt.setdefault("memory_admissions_mib", {})[phase] = available
    if available < MEMORY_MIB:
        raise protocol.HarnessError("memory_below_6144_mib")


def private_json(path: Path, value) -> None:
    native._write_private(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def api(server, method, route, *, expected=200, deadline=None, **kwargs):
    import requests

    return native._api_json(
        requests,
        method,
        server.url + route,
        expected=expected,
        deadline=deadline or time.monotonic() + MODEL_SECONDS,
        **kwargs,
    )


def runtime_configuration(targets, directory: Path):
    """Serialize the selected runtime targets, including an adopted peer store.

    The general fixture helper returns existing configs for runtime-only targets;
    replacing a target in that dataclass therefore requires a new descriptor.
    """
    selected = {scope: targets[scope] for scope in ("main", "auth", "peer")}
    if any(
        target.migration_user is not None or target.migration_secret_file is not None
        for target in selected.values()
    ):
        raise protocol.HarnessError("runtime_targets_required")
    directory.mkdir(mode=0o700)
    configuration = directory / "database-config.json"
    private_json(
        configuration,
        {
            "version": 1,
            "targets": {
                scope: {
                    "host": target.host,
                    "port": target.port,
                    "database": target.database,
                    "user": target.user,
                    "schema": target.schema,
                    "store_id": target.store_id,
                    "secret_file": str(target.secret_file.resolve()),
                    "sslmode": target.sslmode,
                }
                for scope, target in selected.items()
            },
        },
    )
    return dataclasses.replace(targets, config_file=configuration)


def record_mcp_closure(receipt: dict, *, processes_dead: bool) -> None:
    """Never equate a failed client exit with a client that was not created."""
    not_created = receipt.get("mcp_client_created") is False
    receipt["mcp_client_disposition"] = "not_created" if not_created else "created"
    receipt["mcp_client_closed"] = (
        not_created or receipt.get("mcp_client_closed") is True
    ) and processes_dead


def private_executable_path(binary: Path) -> str:
    return ":".join(
        (str(binary.parent), str(native.REPO / "test/providers/fixtures/bin"), "/usr/bin", "/bin")
    )


def worker_profile(environment: dict) -> str:
    import frontmatter

    return frontmatter.dumps(
        frontmatter.Post(
            "Follow the private diagnostic task exactly. Do not use tools or change files. "
            "The required active machine receipt is protocol metadata on its own final line.",
            name=WORKER,
            description="Private real paired-worker diagnostic",
            provider="opencode_cli",
            model=MODEL,
            allowedTools=[],
            mcpServers={
                "cao-mcp-server": {
                    "command": sys.executable,
                    "args": ["-m", "cli_agent_orchestrator.mcp_server.server"],
                    "env": environment,
                    "cao_tool_profile": "collaboration",
                    "enabled_tools": [],
                    "timeout": 60000,
                }
            },
        ),
        sort_keys=False,
    )


def owned_native_launch_selected(
    terminal_id: str, *, binary_path: Path, server_identity, socket_path: Path
) -> bool:
    """Observe the selected model on the exact owned native executable process."""
    from test.live_provider import opencode_interaction_journey as journey

    from cli_agent_orchestrator.utils.opencode_config import to_opencode_agent_id

    expected = binary_path.resolve(strict=True)
    matches = []
    for identity in journey._terminal_tagged_processes(
        terminal_id, socket_path, server_identity=server_identity
    ):
        process = Path("/proc") / str(identity.pid)
        try:
            if (process / "exe").resolve(strict=True) != expected:
                continue
            with (process / "cmdline").open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536 or not raw.endswith(b"\0"):
                raise protocol.HarnessError("native_launch_arguments_invalid")
            arguments = [part.decode("utf-8") for part in raw[:-1].split(b"\0")]
            if "--agent" not in arguments:
                continue  # A bounded --version probe is not the running worker.
            current = journey._process_info(identity.pid)
            if current is None or current[0] != identity or current[2] == "Z":
                continue
            if (
                arguments.count("--agent") != 1
                or arguments.count("--model") != 1
                or arguments[-1:] == ["--agent"]
                or arguments[-1:] == ["--model"]
                or arguments[arguments.index("--agent") + 1] != to_opencode_agent_id(WORKER)
                or arguments[arguments.index("--model") + 1] != MODEL
            ):
                raise protocol.HarnessError("native_worker_launch_model_mismatch")
            with (process / "cmdline").open("rb") as stream:
                repeated = stream.read(65537)
            if (process / "exe").resolve(strict=True) != expected or repeated != raw:
                raise protocol.HarnessError("native_worker_launch_changed_during_read")
            matches.append(identity)
        except (FileNotFoundError, ProcessLookupError):
            continue
    if len(matches) > 1:
        raise protocol.HarnessError("native_worker_launch_not_unique")
    return len(matches) == 1


def worker_configuration(
    cao_home: Path, terminal_id: str, *, launch_binding: dict, evidence: Path
) -> Path | None:
    """Validate actual owned v2 config, static model and zero-MCP native policy."""
    from cli_agent_orchestrator.constants import _CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV
    from cli_agent_orchestrator.utils.mcp_tool_policy import (
        MCP_TOOL_FILTER_ENV,
        MCP_TOOL_PROFILE_ENV,
    )
    from cli_agent_orchestrator.utils.opencode_v2 import validate_v2_mcp_configuration

    matches = []
    for root in native._provider_roots(cao_home):
        owner = json.loads((root / ".cao-ownership.json").read_text())
        if owner.get("terminal_id") != terminal_id:
            continue
        info = root.lstat()
        claim = owner.get("claim", {})
        if (
            root.is_symlink()
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.getuid()
            or claim.get("major") != 2
            or claim.get("device") != info.st_dev
            or claim.get("inode") != info.st_ino
            or claim.get("name") != root.name
        ):
            raise protocol.HarnessError("native_worker_owner_mismatch")
        configuration_bytes = (root / "opencode.json").read_bytes()
        config = json.loads(configuration_bytes)
        server = config.get("mcp", {}).get("servers", {}).get("cao-mcp-server", {})
        agent = config.get("agents", {}).get(WORKER, {})
        tool_contract = native._native_mcp_profile_contract(
            "receiver",
            server,
            server.get("environment"),
            agent.get("permissions"),
            capability_path=None,
            case_id="coordination",
            filter_env=MCP_TOOL_FILTER_ENV,
            profile_env=MCP_TOOL_PROFILE_ENV,
            legacy_marker=_CAO_MCP_INTERACTION_PULL_FLOW_ONLY_ENV,
        )
        checks = {
            "schema": "caos.spec025.t168.native-config-input-observation.v1",
            "configuration_sha256": hashlib.sha256(configuration_bytes).hexdigest(),
            "native_agent_model_present": "model" in agent,
            "explicit_model_override_valid": agent.get("model") in (None, MODEL),
            "mcp_contract_valid": tool_contract,
            "server_environment_object": isinstance(server.get("environment"), dict),
            "codemode_disabled": server.get("codemode") is False,
            "permissions_array": isinstance(agent.get("permissions"), list),
        }
        observation = evidence / f"native-config-input-{terminal_id}.json"
        if not observation.exists():
            private_json(observation, checks)
        if not checks["explicit_model_override_valid"] or not tool_contract:
            raise protocol.HarnessError("native_worker_model_or_tools_mismatch")
        validate_v2_mcp_configuration(
            root, agent_name=WORKER, server_name="cao-mcp-server", require_interaction=False
        )
        matches.append(root)
    if len(matches) > 1:
        raise protocol.HarnessError("native_worker_configuration_not_unique")
    if not matches or not owned_native_launch_selected(terminal_id, **launch_binding):
        return None
    observation = evidence / f"native-configuration-{terminal_id}.json"
    if not observation.exists():
        private_json(
            observation,
            {
                "schema": "caos.spec025.t168.native-launch-observation.v1",
                "owned_executable_model_argv_verified": True,
                "native_zero_mcp_configuration_verified": True,
                "agent_model_field_required": False,
            },
        )
    return matches[0]


def native_startup_observation(server, terminal: dict, tmux, *, deadline: float):
    """Use production typed readiness and the visible pane, as in the v2 journey."""
    import requests

    data, in_time = native._read_terminal_before_deadline(
        requests, server.url, terminal["id"], deadline=deadline
    )
    if not in_time or data is None:
        return None
    if (
        data.get("id") != terminal["id"]
        or data.get("provider") != "opencode_cli"
        or data.get("session_name") != terminal["session_name"]
        or data.get("name") != terminal["name"]
    ):
        raise protocol.HarnessError("native_preflight_terminal_binding_changed")
    viewport = tmux.get_history(
        data["session_name"], data["name"], strip_escapes=True, visible_only=True
    )
    if time.monotonic() > deadline:
        return None
    checks = {
        "schema": "caos.spec025.t168.native-startup-observation.v1",
        "status": data.get("status"),
        "model_label_present": "GLM-5.3-Flash" in viewport,
        "provider_label_present": "OpenCode Go" in viewport,
        "fallback_label_absent": "Big Pickle" not in viewport,
        "viewport": viewport,
    }
    checks["ready"] = (
        checks["status"] in {"idle", "completed"}
        and checks["model_label_present"]
        and checks["provider_label_present"]
        and checks["fallback_label_absent"]
    )
    return checks


def cleanup_safe(receipt: dict) -> bool:
    fields = (
        "api_processes_dead",
        "tmux_processes_dead",
        "mcp_client_closed",
        "private_provider_roots_removed",
        "worktrees_removed",
        "database_targets_closed",
        "private_state_removed",
        "operator_unchanged",
        "source_unchanged",
    )
    return all(receipt.get(k) is True for k in fields) and receipt.get("cleanup_failures") == []


def preflight_acceptance(receipt: dict) -> bool:
    memory = receipt.get("memory_admissions_mib")
    return (
        receipt.get("schema") == "caos.spec025.t168.native-paired.v1"
        and receipt.get("mode") == "preflight"
        and "error_code" not in receipt
        and receipt.get("model") == MODEL
        and receipt.get("binary_sha256") == BINARY_SHA256
        and all(
            receipt.get(key) is True
            for key in (
                "production_pairing_verified",
                "caller_controller_created",
                "native_preflight_passed",
                "mcp_catalog_exact_twelve",
                "binary_unchanged",
                "interpreter_unchanged",
            )
        )
        and isinstance(memory, dict)
        and bool(memory)
        and all(type(value) is int and value >= MEMORY_MIB for value in memory.values())
        and cleanup_safe(receipt)
    )


def acceptance(receipt: dict) -> bool:
    required = (
        "production_pairing_verified",
        "caller_controller_created",
        "mcp_catalog_exact_twelve",
        "native_preflight_passed",
        "native_success_exact",
        "same_key_replay_exact",
        "active_native_cancel_observed",
        "cancelled_worker_stopped",
        "git_baseline_unchanged",
        "binary_unchanged",
        "interpreter_unchanged",
    )
    memory = receipt.get("memory_admissions_mib", {})
    return (
        receipt.get("schema") == "caos.spec025.t168.native-paired.v1"
        and receipt.get("mode") == "execute"
        and "error_code" not in receipt
        and all(receipt.get(k) is True for k in required)
        and receipt.get("model") == MODEL
        and receipt.get("binary_sha256") == BINARY_SHA256
        and len(receipt.get("task_seconds", [])) == 2
        and all(
            type(t) in (int, float) and 0 <= t <= MODEL_SECONDS for t in receipt["task_seconds"]
        )
        and bool(memory)
        and all(type(n) is int and n >= MEMORY_MIB for n in memory.values())
        and cleanup_safe(receipt)
    )


async def _tasks(
    client, source, target, target_home, project, identity, caller_id, receipt, ids, launch_binding
):
    from cli_agent_orchestrator.services.worktree_service import worktree_path_for

    base = {
        "peer_instance_id": identity.target_instance_id,
        "agent_profile": WORKER,
    }
    for index in range(2):
        admit_memory(receipt, f"task_{index}")
        message = (
            f"Reply with exactly {EXPECTED} as the public body, no explanation."
            if index == 0
            else "Start your response with RESULT: followed by one space and the sum of "
            "270584 and 198327. Then output 10000 numbered lines explaining that arithmetic."
        )
        body = {**base, "operation_key": f"t168-operation-{index}", "message": message}
        started = time.monotonic()
        deadline = started + MODEL_SECONDS
        task = await client.call("assign_local_cao_task", body)
        task_id, terminal_id = task.get("task_id"), task.get("terminal_id")
        if not isinstance(task_id, str) or not native._safe_terminal_identity(terminal_id):
            raise protocol.HarnessError("paired_task_not_admitted")
        ids.append((target, terminal_id))
        replay = await client.call("assign_local_cao_task", body)
        if replay.get("task_id") != task_id or replay.get("terminal_id") != terminal_id:
            raise protocol.HarnessError("paired_replay_changed_worker")
        receipt["same_key_replay_exact"] = True
        expected_worktree = Path(worktree_path_for(str(project), terminal_id)).resolve()
        while time.monotonic() < deadline:
            status = await client.call("get_local_cao_task", {"task_id": task_id})
            metadata = await asyncio.to_thread(api, target, "GET", f"/terminals/{terminal_id}")
            cwd = await asyncio.to_thread(
                api, target, "GET", f"/terminals/{terminal_id}/working-directory"
            )
            if Path(cwd["working_directory"]).resolve() != expected_worktree:
                raise protocol.HarnessError("paired_worker_worktree_mismatch")
            if metadata.get("provider") != "opencode_cli":
                raise protocol.HarnessError("paired_worker_provider_mismatch")
            try:
                configuration = worker_configuration(
                    target_home,
                    terminal_id,
                    launch_binding=launch_binding,
                    evidence=Path(receipt["evidence_dir"]),
                )
            except FileNotFoundError:
                configuration = None
            if configuration is None:
                await asyncio.sleep(0.2)
                continue
            if index == 0 and status.get("state") in {"succeeded", "failed", "cancelled"}:
                result = status.get("result", {})
                if status["state"] != "succeeded" or result.get("output") != EXPECTED:
                    raise protocol.HarnessError("native_worker_result_not_exact")
                receipt["native_success_exact"] = True
                break
            if index == 1:
                turn = await asyncio.to_thread(api, target, "GET", f"/terminals/{terminal_id}/turn")
                output = await asyncio.to_thread(
                    api, target, "GET", f"/terminals/{terminal_id}/output", params={"mode": "full"}
                )
                if turn.get("status") == "processing" and "RESULT: 468911" in output.get(
                    "output", ""
                ):
                    receipt["active_native_cancel_observed"] = True
                    cancelled = await client.call("cancel_local_cao_task", {"task_id": task_id})
                    if (
                        cancelled.get("state") != "cancelled"
                        or cancelled.get("result", {}).get("worker_stopped") is not True
                    ):
                        raise protocol.HarnessError("native_cancel_stop_not_proven")
                    receipt["cancelled_worker_stopped"] = True
                    break
            await asyncio.sleep(0.2)
        else:
            raise protocol.HarnessError("native_model_75_second_deadline")
        elapsed = time.monotonic() - started
        if elapsed > MODEL_SECONDS:
            raise protocol.HarnessError("native_model_75_second_deadline")
        receipt.setdefault("task_seconds", []).append(round(elapsed, 3))
        # Successful workers remain owned terminals; cancelled workers were
        # already retired by the target's production cancellation boundary.
        if index == 0:
            await asyncio.to_thread(api, target, "DELETE", f"/terminals/{terminal_id}")
        if expected_worktree.exists():
            raise protocol.HarnessError("native_worker_worktree_retained")


def run(manifest: dict, *, preflight_only: bool = False) -> dict:
    """Execute only after admission; all uncertain teardown remains retained."""
    from test.fixtures.cao_server import (
        _pick_free_port,
        _start_cao_server,
        _subprocess_env,
    )
    from test.live_provider import opencode_interaction_journey as journey
    from test.postgres import fixtures as pg

    import frontmatter

    from cli_agent_orchestrator.clients.tmux import TmuxClient
    from cli_agent_orchestrator.providers.opencode_cli import _copy_private_state_bundle

    started = time.monotonic()
    receipt = {
        "schema": "caos.spec025.t168.native-paired.v1",
        "mode": "preflight" if preflight_only else "execute",
        "source_head": manifest["source_head"],
        "evidence_dir": manifest["evidence_dir"],
    }
    receipt.update(
        model=MODEL,
        binary_sha256=digest(Path(manifest["binary_path"])),
        execution_sha256=manifest["execution_sha256"],
        interpreter_sha256=digest(Path(sys.executable).resolve()),
    )
    source_before = sources(manifest["source_head"])
    if source_before != manifest["source_sha256"]:
        raise protocol.HarnessError("source_manifest_mismatch")
    service = pg._discover_postgres_service()
    if service.metadata["container_id"] != manifest["postgres_container_id"]:
        raise protocol.HarnessError("postgres_owner_mismatch")
    original_env, original_home = dict(os.environ), Path.home()
    auth_paths = native._operator_state_paths(original_home, original_env)
    operator_before = native._operator_fingerprints(auth_paths)
    if (
        hashlib.sha256(json.dumps(operator_before, sort_keys=True).encode()).hexdigest()
        != manifest["operator_fingerprint"]
    ):
        raise protocol.HarnessError("operator_state_changed_before_run")
    root = journey._private_opencode_root()
    contexts, targets, servers, ids = [], [], [], []
    failures, owned = set(), set()
    sid = socket_id = socket_path = None
    tmux = TmuxClient()
    previous_tmux = tmux.server
    native.PINNED_POSTGRES_FIXTURE_SHA256 = manifest["postgres_fixture_sha256"]
    native.PINNED_POSTGRES_GUARD_DERIVATION_SHA256 = manifest["postgres_guard_sha256"]
    receipt["mcp_client_closed"] = False
    receipt["mcp_client_created"] = False
    project = root / "project"
    tmux_dir = root / "tmux"
    homes = [root / "profile-0", root / "profile-1"]
    try:
        native._install_guarded_postgres_cleanup(pg)
        admit_memory(receipt, "initial")
        home = journey._private_directory(root / "home")
        project = journey._private_directory(root / "project")
        journey._initialize_private_git_repository(project)
        baseline = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project, text=True
        ).strip()
        data = journey._private_directory(home / ".local/share/opencode")
        _copy_private_state_bundle(auth_paths[0], data)
        raw = journey._private_directory(root / "raw")
        tmux_dir = journey._private_directory(root / "tmux")
        path_value = private_executable_path(Path(manifest["binary_path"]))
        common = {
            "HOME": str(home),
            "PATH": path_value,
            "LANG": "C.UTF-8",
            "TERM": "xterm-256color",
            "TMUX_TMPDIR": str(tmux_dir),
            "PYTHONPATH": str(native.REPO / "src") + ":" + str(native.REPO),
            "PYTHONDONTWRITEBYTECODE": "1",
            "CAO_AUTH_MODE": "off",
            "CAO_TERMINAL_BACKEND": "tmux",
            "CAO_WORK_LAUNCH_MODE": "legacy",
            "CAO_LOCAL_LISTENER_DISABLED": "true",
            "CAO_MCP_APPS_ENABLED": "0",
            "OPENCODE_DB": str(data / "opencode.db"),
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
        }
        os.environ.clear()
        os.environ.update(common)
        for index in range(2):
            context = pg.isolated_live_targets(root / f"profile-{index}", service=service)
            target = native._enter_guarded_postgres_context(context, pg)
            contexts.append(context)
            targets.append(target)
        targets[1] = dataclasses.replace(targets[1], peer=targets[0].peer)
        environments = []
        homes = [root / "profile-0", root / "profile-1"]
        for index in range(2):
            runtime = runtime_configuration(targets[index], root / f"runtime-{index}")
            port = _pick_free_port()
            extra = {
                **common,
                "CAO_HOME_DIR": str(homes[index]),
                "CAO_DATABASE_CONFIG_FILE": str(runtime.config_file),
                "CAO_INSTANCE_NAME": f"t168-{index}",
            }
            env = _subprocess_env(
                home,
                port,
                extra=extra,
                allowed_external_credentials=frozenset(
                    {"OPENCODE_DB", "OPENCODE_DISABLE_AUTOUPDATE"}
                ),
            )
            environments.append(env)
            store = journey._private_directory(homes[index] / "agent-store")
            if index == 0:
                (store / f"{CALLER}.md").write_text(
                    frontmatter.dumps(
                        frontmatter.Post(
                            "Private MCP coordinator.",
                            name=CALLER,
                            description="Private coordinator",
                            provider="mock_cli",
                            allowedTools=["@cao-mcp-server"],
                        )
                    )
                )
            else:
                profile = root / "worker.md"
                native._write_private(profile, worker_profile(env))
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "cli_agent_orchestrator.cli.main",
                        "install",
                        str(profile),
                        "--provider",
                        "opencode_cli",
                    ],
                    env=env,
                    cwd=native.REPO,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                native._write_private(raw / "worker-install.private", result.stdout + result.stderr)
                if result.returncode != 0:
                    raise protocol.HarnessError("worker_production_install_failed")
            before = set(
                __import__("test.fixtures.cao_server", fromlist=["_LIVE_SERVERS"])._LIVE_SERVERS
            )
            try:
                server = _start_cao_server(
                    home,
                    port,
                    database_targets=runtime,
                    extra_env=extra,
                    deadline=60,
                    allowed_external_credentials=frozenset(
                        {"OPENCODE_DB", "OPENCODE_DISABLE_AUTOUPDATE"}
                    ),
                )
                servers.append((server, None))
                servers[-1] = (server, native._capture_api_process(server, journey)[1])
            except BaseException:
                registry = __import__(
                    "test.fixtures.cao_server", fromlist=["_LIVE_SERVERS"]
                )._LIVE_SERVERS
                for handle in registry - before:
                    if any(known is handle for known, _ in servers):
                        continue
                    servers.append((handle, None))
                    servers[-1] = (handle, native._capture_api_process(handle, journey)[1])
                raise
        socket_path = journey._pin_tmux_client_to_private_server(tmux, tmux_dir)
        sid, socket_id = journey._start_and_pin_private_tmux_server(tmux, socket_path)
        source, target = servers[0][0], servers[1][0]
        launch_binding = {
            "binary_path": Path(manifest["binary_path"]),
            "server_identity": sid,
            "socket_path": socket_path,
        }
        pair = protocol.pair_profiles(
            environments[0], environments[1], source.url, target.url, project, raw
        )
        receipt["production_pairing_verified"] = True
        caller = api(
            source,
            "POST",
            "/sessions",
            expected=201,
            params={
                "provider": "mock_cli",
                "agent_profile": CALLER,
                "working_directory": str(project),
                "session_name": "t168-caller-" + os.urandom(5).hex(),
            },
        )
        ids.append((source, caller["id"]))
        receipt["caller_controller_created"] = True
        # An actual native controller initialization without a user turn.
        admit_memory(receipt, "native_preflight")
        preflight = api(
            target,
            "POST",
            "/sessions",
            expected=201,
            deadline=time.monotonic() + MODEL_SECONDS,
            params={
                "provider": "opencode_cli",
                "agent_profile": WORKER,
                "working_directory": str(project),
                "session_name": "t168-preflight-" + os.urandom(5).hex(),
            },
        )
        ids.append((target, preflight["id"]))
        ready_deadline = time.monotonic() + MODEL_SECONDS
        ready = False
        observation_number = 0
        while time.monotonic() < ready_deadline:
            try:
                configured = worker_configuration(
                    homes[1],
                    preflight["id"],
                    launch_binding=launch_binding,
                    evidence=Path(manifest["evidence_dir"]),
                )
            except FileNotFoundError:
                configured = None
            observation = native_startup_observation(
                target, preflight, tmux, deadline=ready_deadline
            )
            if observation is None:
                break
            private_json(
                Path(manifest["evidence_dir"])
                / f"native-startup-{observation_number:04d}.private.json",
                observation,
            )
            observation_number += 1
            if configured is not None and observation["ready"]:
                ready = True
                break
            time.sleep(0.2)
        if not ready:
            raise protocol.HarnessError("native_preflight_not_ready_within_75s")
        api(target, "DELETE", f'/terminals/{preflight["id"]}')
        receipt["native_preflight_passed"] = True

        async def coordinate():
            receipt["mcp_client_created"] = True
            try:
                async with protocol.open_coordination_client(
                    environments[0], caller["id"]
                ) as client:
                    receipt["mcp_catalog_exact_twelve"] = True
                    peers = await client.call("list_local_cao_peers", {})
                    if len(peers.get("peers", [])) != 1:
                        raise protocol.HarnessError("private_pair_catalog_not_exact")
                    if not preflight_only:
                        await _tasks(
                            client,
                            source,
                            target,
                            homes[1],
                            project,
                            pair,
                            caller["id"],
                            receipt,
                            ids,
                            launch_binding,
                        )
                receipt["mcp_client_closed"] = True
            finally:
                receipt.setdefault("mcp_client_closed", False)

        asyncio.run(coordinate())
        receipt["git_baseline_unchanged"] = (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()
            == baseline
        )
    except BaseException as error:
        receipt["error_code"] = (
            error.code if isinstance(error, protocol.HarnessError) else "paired_journey_failed"
        )
        try:
            private_json(
                Path(manifest["evidence_dir"]) / "exception-type.json",
                {"type": type(error).__name__},
            )
            import traceback

            native._write_private(
                Path(manifest["evidence_dir"]) / "traceback.private", traceback.format_exc()
            )
        except Exception:
            failures.add("exception_archive_failed")
    finally:
        try:
            private_json(
                Path(manifest["evidence_dir"]) / "owner-context.json",
                {
                    "root": str(root),
                    "terminals": [{"url": handle.url, "id": tid} for handle, tid in ids],
                    "api_identities": [
                        dataclasses.asdict(identity) if identity else None
                        for _, identity in servers
                    ],
                    "tmux_identity": dataclasses.asdict(sid) if sid else None,
                    "socket_identity": dataclasses.asdict(socket_id) if socket_id else None,
                    "socket_path": str(socket_path) if socket_path else None,
                },
            )
        except Exception:
            failures.add("owner_context_write_failed")
        for server, terminal_id in reversed(ids):
            try:
                # Missing means the production cancellation already retired it.
                import requests

                result = requests.delete(server.url + f"/terminals/{terminal_id}", timeout=60)
                if result.status_code not in (200, 404):
                    failures.add("terminal_retirement_not_confirmed")
            except Exception:
                failures.add("terminal_retirement_failed")
        for server, identity in reversed(servers):
            try:
                server.stop()
                if identity is None or journey._process_identity_alive(identity):
                    failures.add("api_process_alive")
            except Exception:
                failures.add("api_cleanup_failed")
        receipt["api_processes_dead"] = (
            all(
                identity is not None and not journey._process_identity_alive(identity)
                for _, identity in servers
            )
            and "api_cleanup_failed" not in failures
        )
        if sid is not None:
            cleanup_ids = [tid for _, tid in ids] or ["t168-no-returned-terminal-id"]
            for terminal_id in cleanup_ids:
                try:
                    owned |= journey._owned_process_snapshot_for_cleanup(
                        sid, terminal_id, socket_path
                    )
                except Exception:
                    failures.add("process_snapshot_failed")
            try:
                stopped = journey._cleanup_private_tmux_server(
                    tmux,
                    tmux_dir,
                    failures,
                    terminal_id=cleanup_ids[-1],
                    server_identity=sid,
                    owned_processes=owned,
                    socket_identity=socket_id,
                )
            except Exception:
                stopped = False
                failures.add("tmux_cleanup_failed")
            for _, terminal_id in ids:
                try:
                    if journey._terminal_tagged_processes(
                        terminal_id, socket_path, server_identity=sid
                    ):
                        stopped = False
                except Exception:
                    stopped = False
            receipt["tmux_processes_dead"] = stopped
        else:
            receipt["tmux_processes_dead"] = not ids
        record_mcp_closure(receipt, processes_dead=receipt["tmux_processes_dead"])
        try:
            receipt["private_provider_roots_removed"] = all(
                not native._provider_roots(home)
                for home in (root / "profile-0", root / "profile-1")
            )
            if (project / ".git").exists():
                remaining = subprocess.run(
                    ["git", "worktree", "list", "--porcelain"],
                    cwd=project,
                    capture_output=True,
                    text=True,
                )
                receipt["worktrees_removed"] = (
                    remaining.returncode == 0 and remaining.stdout.count("worktree ") == 1
                )
            else:
                receipt["worktrees_removed"] = not ids
        except Exception:
            receipt["private_provider_roots_removed"] = False
            receipt["worktrees_removed"] = False
            failures.add("private_root_verification_failed")
        safe = (
            receipt["api_processes_dead"]
            and receipt["tmux_processes_dead"]
            and receipt["private_provider_roots_removed"]
            and receipt["worktrees_removed"]
            and not failures
        )
        try:
            for folder in (root / "raw", root / "home"):
                if folder.exists():
                    for item in folder.iterdir():
                        if (
                            item.is_file()
                            and not item.is_symlink()
                            and (folder.name == "raw" or item.name == "server.log")
                        ):
                            native._write_private(
                                Path(manifest["evidence_dir"]) / ("raw-" + item.name),
                                item.read_bytes(),
                            )
        except Exception:
            failures.add("diagnostic_archive_failed")
            safe = False
        if safe:
            try:
                for context in reversed(contexts):
                    native._exit_guarded_postgres_context(context, pg)
                receipt["database_targets_closed"] = True
                shutil.rmtree(root)
                receipt["private_state_removed"] = not root.exists()
            except Exception:
                receipt["database_targets_closed"] = False
                receipt["private_state_removed"] = False
                failures.add("private_teardown_failed")
        else:
            receipt["database_targets_closed"] = False
            receipt["private_state_removed"] = False
            failures.add("private_resources_retained")
        try:
            receipt["operator_unchanged"] = (
                native._operator_fingerprints(auth_paths) == operator_before
            )
            receipt["source_unchanged"] = sources(manifest["source_head"]) == source_before
            receipt["binary_unchanged"] = (
                digest(Path(manifest["binary_path"])) == receipt["binary_sha256"]
            )
            receipt["interpreter_unchanged"] = (
                digest(Path(sys.executable).resolve()) == receipt["interpreter_sha256"]
            )
        except Exception:
            receipt["operator_unchanged"] = False
            receipt["source_unchanged"] = False
            failures.add("preservation_verification_failed")
        os.environ.clear()
        os.environ.update(original_env)
        tmux.server = previous_tmux
        receipt["cleanup_failures"] = sorted(failures)
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
        receipt["success"] = (
            acceptance(receipt) if not preflight_only else preflight_acceptance(receipt)
        )
        private_json(Path(manifest["evidence_dir"]) / "receipt.json", receipt)
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--go", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)
    if not RUN_ENABLED or not args.go:
        print(json.dumps({"status": "closed", "model_started": False}))
        return 2
    closed = native.REPO / "specs/025-provider-mcp-profiles/fixtures/run_native_paired_worker.py"
    if not native._execution_source_matches(closed.read_bytes(), Path(__file__).read_bytes()):
        raise protocol.HarnessError("execution_is_not_flag_only_derivation")
    if args.manifest is None:
        raise protocol.HarnessError("manifest_required")
    manifest = json.loads(
        native._read_owned_private_file(args.manifest, maximum=2 * 1024 * 1024).decode()
    )
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=native.REPO, text=True).strip()
    if (
        manifest.get("approved") is not True
        or manifest.get("mode") != ("preflight" if args.preflight_only else "execute")
        or manifest.get("source_head") != head
        or manifest.get("model") != MODEL
    ):
        raise protocol.HarnessError("private_admission_invalid")
    if (
        digest(Path(__file__)) != manifest.get("execution_sha256")
        or digest(Path(manifest["binary_path"])) != BINARY_SHA256
    ):
        raise protocol.HarnessError("execution_or_binary_changed")
    if (
        manifest.get("python_sha256") != digest(Path(sys.executable).resolve())
        or manifest.get("model_seconds") != MODEL_SECONDS
        or manifest.get("memory_mib") != MEMORY_MIB
        or manifest.get("source_head")
        != subprocess.check_output(
            ["git", "rev-parse", "origin/main"], cwd=native.REPO, text=True
        ).strip()
    ):
        raise protocol.HarnessError("interpreter_limits_or_origin_mismatch")
    evidence = Path(manifest["evidence_dir"])
    info = evidence.lstat()
    if (
        evidence.is_symlink()
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.getuid()
        or (evidence / "receipt.json").exists()
    ):
        raise protocol.HarnessError("private_one_shot_evidence_required")
    return 0 if run(manifest, preflight_only=args.preflight_only)["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
