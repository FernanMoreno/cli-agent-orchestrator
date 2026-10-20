"""Disposable API/PostgreSQL and MCP stdio runtime for Questions browser acceptance.

The fixture seeds through the production InteractionService and launches the
bundled MCP server against the same isolated API and private source capability.
It uses private main/auth/peer PostgreSQL targets, never starts a provider CLI,
and writes CAO state only inside its temporary home.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import queue
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from uuid import uuid4


def _assert_private_runtime_targets(targets) -> None:
    config_path = Path(targets.config_file)
    config_stat = config_path.lstat()
    if (
        not stat.S_ISREG(config_stat.st_mode)
        or config_stat.st_mode & 0o077
        or config_path.is_symlink()
    ):
        raise RuntimeError("isolated PostgreSQL runtime config is not private")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    configured = config.get("targets")
    if not isinstance(configured, dict) or set(configured) != {"main", "auth", "peer"}:
        raise RuntimeError("isolated PostgreSQL runtime config must contain all three scopes")
    for scope in ("main", "auth", "peer"):
        values = configured[scope]
        target = targets[scope]
        if not isinstance(values, dict) or any(
            key in values for key in ("migration_user", "migration_secret_file")
        ):
            raise RuntimeError("isolated PostgreSQL runtime config contains migration credentials")
        if target.migration_user is not None or target.migration_secret_file is not None:
            raise RuntimeError("isolated PostgreSQL runtime target contains migration credentials")
        secret_path = Path(target.secret_file)
        if secret_path.resolve().parent != config_path.parent.resolve():
            raise RuntimeError("isolated PostgreSQL runtime credential escaped its private home")
        secret_stat = secret_path.lstat()
        if (
            not stat.S_ISREG(secret_stat.st_mode)
            or secret_stat.st_mode & 0o077
            or secret_path.is_symlink()
        ):
            raise RuntimeError("isolated PostgreSQL runtime credential is not private")


def _tmux_child_environment(
    parent_environment: Mapping[str, str],
    *,
    cao_home: Path,
    private_tmp: Path,
    tmux_tmpdir: Path,
) -> dict[str, str]:
    """Build the minimum private environment needed by the test tmux server."""
    environment = {
        "PATH": parent_environment.get("PATH") or os.defpath,
        "CAO_HOME_DIR": str(cao_home),
        "TMPDIR": str(private_tmp),
        "TMUX_TMPDIR": str(tmux_tmpdir),
    }
    for key in ("LANG", "LC_ALL", "LC_CTYPE", "LANGUAGE", "TERM"):
        value = parent_environment.get(key)
        if value:
            environment[key] = value
    return environment


def _prepare_private_path_home(workspace: Path) -> Path:
    """Keep Path.home-derived app paths inside this disposable runtime."""
    private_home = workspace / "account-home"
    private_home.mkdir(mode=0o700)
    os.chmod(private_home, 0o700)
    resolved_private_home = private_home.resolve(strict=True)
    if resolved_private_home == Path.home().resolve():
        raise RuntimeError("private interaction fixture home must differ from account home")
    original_home = os.environ.get("HOME")
    original_codex_home = os.environ.get("CODEX_HOME")
    Path.home = classmethod(lambda _cls: resolved_private_home)
    if (
        Path.home().resolve() != resolved_private_home
        or os.environ.get("HOME") != original_home
        or os.environ.get("CODEX_HOME") != original_codex_home
    ):
        raise RuntimeError("private interaction fixture home was not applied safely")
    return resolved_private_home


def _mcp_child_environment(
    parent_environment: Mapping[str, str],
    *,
    private_account_home: Path,
    source_root: Path,
    cao_home: Path,
    database_config_file: Path,
    tmux_tmpdir: Path,
    api_port: int,
    capability_file: Path,
) -> dict[str, str]:
    """Pass only disposable runtime paths to the MCP child process."""
    return {
        "PATH": parent_environment.get("PATH", ""),
        "HOME": str(private_account_home),
        "PYTHONPATH": os.pathsep.join((str(source_root / "src"), str(source_root))),
        "LANG": parent_environment.get("LANG", "C.UTF-8"),
        "CAO_HOME_DIR": str(cao_home),
        "CAO_DATABASE_CONFIG_FILE": str(database_config_file),
        "TMUX_TMPDIR": str(tmux_tmpdir),
        "CAO_API_HOST": "127.0.0.1",
        "CAO_API_PORT": str(api_port),
        "CAO_INTERACTION_CAPABILITY_FILE": str(capability_file),
        "CAO_A2A_DISABLED": "true",
        "OTEL_SDK_DISABLED": "true",
    }


def main() -> None:
    workspace_context = tempfile.TemporaryDirectory(prefix="cao-interactions-e2e-")
    workspace = Path(workspace_context.name)
    os.chmod(workspace, 0o700)
    private_account_home = _prepare_private_path_home(workspace)
    cao_home = workspace / "cao"
    cao_home.mkdir(mode=0o700)
    private_tmp = workspace / "tmp"
    private_tmp.mkdir(mode=0o700)
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    # The isolated targets have explicit owner/runtime roles. Keep owner secrets
    # available only while the helper bootstraps component schemas; the yielded
    # config contains runtime credentials only.
    atexit.register(workspace_context.cleanup)
    target_stack = ExitStack()
    atexit.register(target_stack.close)
    os.environ.update(
        {
            "CAO_HOME_DIR": str(cao_home),
            "CAO_DATABASE_CONFIG_FILE": str(cao_home / "database-config.json"),
            "TMUX_TMPDIR": str(workspace / "tmux"),
            "CAO_A2A_DISABLED": "true",
            "OTEL_SDK_DISABLED": "true",
        }
    )
    (workspace / "tmux").mkdir(mode=0o700)
    for key in (
        "AUTH0_DOMAIN",
        "AUTH0_AUDIENCE",
        "CAO_AUTH_JWKS_URI",
        "CAO_AUTH_ISSUER",
        "CAO_AUTH_AUDIENCE",
        "CAO_BROWSER_LOGIN_ROOT",
        "CAO_AUTH_LOCAL_TOKEN",
        "TMUX",
    ):
        os.environ.pop(key, None)

    from test.postgres.fixtures import isolated_live_targets

    targets = target_stack.enter_context(isolated_live_targets(cao_home))
    _assert_private_runtime_targets(targets)
    os.environ["CAO_HOME_DIR"] = str(targets.home)
    os.environ["CAO_DATABASE_CONFIG_FILE"] = str(targets.config_file)
    tmux_environment = _tmux_child_environment(
        os.environ,
        cao_home=targets.home,
        private_tmp=private_tmp,
        tmux_tmpdir=workspace / "tmux",
    )

    # Keep the production API lifespan from registering/reconciling with the
    # user's durable local-peer registry. This process owns only its temp DB.
    import cli_agent_orchestrator.services.local_peer_registry as local_peer_registry
    import cli_agent_orchestrator.services.local_peer_service as local_peer_service
    from cli_agent_orchestrator.services.local_peer_identity import (
        LocalPeerUnavailableError,
    )

    def disable_local_peer_registry() -> None:
        raise LocalPeerUnavailableError("isolated browser acceptance runtime")

    local_peer_registry.register_instance = disable_local_peer_registry
    local_peer_service.reconcile_local_peer_tasks_at_startup = lambda: None

    import uvicorn

    from cli_agent_orchestrator.api.main import app
    from cli_agent_orchestrator.clients.database import create_terminal
    from cli_agent_orchestrator.providers.mcp_interactions import NativeInteractionObservation
    from cli_agent_orchestrator.security.auth import local_operator_principal
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        InteractionOption,
        QuestionInput,
    )

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 90
    while not server.started:
        if not thread.is_alive() or time.monotonic() >= deadline:
            raise RuntimeError("isolated interaction API did not start")
        time.sleep(0.05)

    print(json.dumps({"ready": True, "origin": origin}), flush=True)
    source_tokens: dict[int, str] = {}
    target_group_id: str | None = None
    target_question_id: str | None = None
    target_admitted_ms: int | None = None
    target_scenarios: list[dict[str, object]] = []
    source_metadata: dict[int, dict[str, str]] = {}
    population_snapshot: dict[str, object] | None = None
    capability_file = workspace / "source-capability"
    mcp_events: queue.Queue[dict[str, object]] = queue.Queue()
    mcp_thread: threading.Thread | None = None
    mcp_wait_started = False
    native_receipt: dict[str, object] | None = None
    credential_question_id: str | None = None

    try:
        service = app.state.interaction_service
        principal = local_operator_principal()

        def run_mcp_wait(expected_responses: dict[str, object]) -> None:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            def result_payload(result) -> dict[str, object]:
                if result.isError:
                    raise RuntimeError("MCP interaction tool returned an error")
                structured = result.structuredContent
                if isinstance(structured, dict):
                    value = structured.get("result", structured)
                    if isinstance(value, dict):
                        return value
                for item in result.content:
                    if item.type == "text":
                        value = json.loads(item.text)
                        if isinstance(value, dict):
                            return value
                raise RuntimeError("MCP interaction tool returned an invalid result")

            def canonical_response(value: dict[str, object]) -> dict[str, object]:
                normalized = dict(value)
                if isinstance(normalized.get("option_ids"), list):
                    normalized["option_ids"] = sorted(normalized["option_ids"])
                return normalized

            async def journey() -> None:
                env = _mcp_child_environment(
                    os.environ,
                    private_account_home=private_account_home,
                    source_root=repo_root,
                    cao_home=targets.home,
                    database_config_file=targets.config_file,
                    tmux_tmpdir=workspace / "tmux",
                    api_port=port,
                    capability_file=capability_file,
                )
                async with stdio_client(
                    StdioServerParameters(
                        command=sys.executable,
                        args=[
                            "-c",
                            "from cli_agent_orchestrator.mcp_server.server import main; main()",
                        ],
                        env=env,
                    )
                ) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = {item.name for item in (await session.list_tools()).tools}
                        required_tools = {
                            "cao_wait_for_user_answers",
                            "cao_acknowledge_user_answers",
                        }
                        if not required_tools <= tools:
                            raise RuntimeError(
                                "MCP process did not expose the interaction wait tools"
                            )
                        wait_task = asyncio.create_task(
                            session.call_tool(
                                "cao_wait_for_user_answers",
                                {"group_id": target_group_id, "wait_seconds": 25},
                                read_timeout_seconds=timedelta(seconds=35),
                            )
                        )
                        # Let the client send the tool call before exposing the
                        # ready event to Playwright. The HTTP request then stays
                        # blocked until the browser answers all required rows.
                        await asyncio.sleep(0.25)
                        mcp_events.put({"event": "mcp_ready"})
                        waited = await wait_task
                        ready = result_payload(waited)
                        answers = ready.get("answers", [])
                        actual_by_id = (
                            {
                                str(answer.get("question_id")): canonical_response(
                                    answer.get("response", {})
                                )
                                for answer in answers
                                if isinstance(answer, dict)
                                and isinstance(answer.get("response"), dict)
                            }
                            if isinstance(answers, list)
                            else {}
                        )
                        expected_by_id = {
                            str(question_id): canonical_response(value)
                            for question_id, value in expected_responses.items()
                            if isinstance(value, dict)
                        }
                        matches = ready.get("state") == "ready" and actual_by_id == expected_by_id
                        acknowledged = False
                        if matches and isinstance(answers, list) and answers:
                            receipts = [
                                {key: answer[key] for key in ("question_id", "version", "receipt")}
                                for answer in answers
                                if isinstance(answer, dict)
                            ]
                            ack_result = result_payload(
                                await session.call_tool(
                                    "cao_acknowledge_user_answers",
                                    {"group_id": target_group_id, "receipts": receipts},
                                    read_timeout_seconds=timedelta(seconds=35),
                                )
                            )
                            acknowledged = ack_result.get("success") is True and ack_result.get(
                                "acknowledged"
                            ) == len(receipts)
                        mcp_events.put(
                            {
                                "event": "mcp_done",
                                "state": ready.get("state"),
                                "answer_count": len(answers) if isinstance(answers, list) else 0,
                                "responses_match": matches,
                                "acknowledged": acknowledged,
                            }
                        )

            try:
                asyncio.run(journey())
            except Exception:
                mcp_events.put({"event": "mcp_error", "message": "MCP wait journey failed"})

        for raw in sys.stdin:
            command = json.loads(raw)
            action = command.get("action")
            if action == "populate":
                if population_snapshot is not None:
                    print(json.dumps({"ack": action, **population_snapshot}), flush=True)
                    continue
                visible_questions: list[dict[str, object]] = []
                for source_index in range(20):
                    # The public terminal API validates the production 8-hex
                    # terminal ID format, including exact-incarnation lookups.
                    terminal_id = f"{0xB0000000 + source_index:08x}"
                    session_name = f"cao-browser-session-{source_index:02d}"
                    incarnation_id = f"browser-incarnation-{source_index:02d}"
                    if source_index == 0:
                        subprocess.run(
                            [
                                "tmux",
                                "-f",
                                "/dev/null",
                                "new-session",
                                "-d",
                                "-s",
                                session_name,
                                "-n",
                                "agent",
                                "sleep",
                                "600",
                            ],
                            check=True,
                            env=tmux_environment,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    create_terminal(
                        terminal_id,
                        session_name,
                        "agent",
                        "codex",
                        allowed_tools=["@cao-mcp-server"],
                        session_incarnation_id=incarnation_id,
                        new_session_incarnation=(source_index == 0),
                    )
                    issued = service.register_source(
                        terminal_id=terminal_id,
                        incarnation_id=incarnation_id,
                        session_name=session_name,
                        provider="codex",
                        principal=principal,
                    )
                    source_tokens[source_index] = issued.token
                    if source_index == 0:
                        capability_file.write_text(issued.token, encoding="ascii")
                        os.chmod(capability_file, 0o600)
                    source_metadata[source_index] = {
                        "terminal_id": terminal_id,
                        "session_name": session_name,
                        "incarnation_id": incarnation_id,
                        "window_name": "agent",
                    }

                def create_group_for_source(source_index: int):
                    if source_index == 0:
                        definitions = [
                            {
                                "kind": "text",
                                "prompt": "Select the next safe action for source 0, request 0-0.",
                                "required": True,
                            },
                            {
                                "kind": "single_choice",
                                "prompt": "Choose one safe action.",
                                "options": (
                                    InteractionOption(id="inspect", label="Inspect recent changes"),
                                    InteractionOption(id="wait", label="Wait for more information"),
                                ),
                            },
                            {
                                "kind": "multi_choice",
                                "prompt": "Choose all safe checks to perform.",
                                "options": (
                                    InteractionOption(id="tests", label="Run contract tests"),
                                    InteractionOption(id="diff", label="Review the diff"),
                                    InteractionOption(id="deploy", label="Deploy immediately"),
                                ),
                            },
                            {
                                "kind": "choice_or_text",
                                "prompt": "Choose a response or write another safe option.",
                                "allow_free_text": True,
                                "options": (
                                    InteractionOption(id="review", label="Review the request"),
                                    InteractionOption(id="clarify", label="Ask for clarification"),
                                ),
                            },
                            {
                                "kind": "approval",
                                "prompt": "Choose an informational approval response.",
                                "options": (
                                    InteractionOption(
                                        id="approve", label="Approve read-only inspection"
                                    ),
                                    InteractionOption(id="decline", label="Do not approve"),
                                ),
                            },
                        ]
                    else:
                        definitions = [
                            {
                                "kind": "text",
                                "prompt": f"Select the next safe action for source {source_index}, request 0-{question_index}.",
                            }
                            for question_index in range(3 if source_index == 19 else 5)
                        ]
                    request = CreateGroupRequest(
                        questions=[QuestionInput(**definition) for definition in definitions]
                    )
                    group = service.create_group(
                        source_tokens[source_index],
                        request,
                        f"browser-e2e-operation-{source_index:02d}-00",
                    )
                    # Measure browser pickup from the committed admission
                    # boundary; the 20 independent sources are admitted concurrently.
                    admitted_ms = int(time.time() * 1000)
                    return source_index, admitted_ms, group, definitions

                with ThreadPoolExecutor(max_workers=20) as executor:
                    groups = list(executor.map(create_group_for_source, range(20)))
                target_scenarios.clear()
                for source_index, admitted_ms, group, definitions in groups:
                    for question in group.questions:
                        question_id = str(question["question_id"])
                        visible_questions.append(
                            {"question_id": question_id, "admitted_ms": admitted_ms}
                        )
                    if source_index == 0:
                        target_group_id = group.group_id
                        target_question_id = str(group.questions[0]["question_id"])
                        target_admitted_ms = admitted_ms
                        target_scenarios.extend(
                            {
                                "question_id": str(created["question_id"]),
                                "kind": definition["kind"],
                                "prompt": definition["prompt"],
                            }
                            for definition, created in zip(
                                definitions, group.questions, strict=True
                            )
                        )

                # Keep the capacity benchmark at exactly 100 real questions
                # while including the two manual-only cases in that same
                # browser-visible snapshot (98 typed questions + native +
                # credential). The HTTP list contract caps at 100 active rows.
                source = source_metadata[0]
                observation = NativeInteractionObservation.manual_signal(
                    screen_lines=["Waiting for manual input in the provider terminal."],
                    terminal_id=source["terminal_id"],
                    session_name=source["session_name"],
                    window_name=source["window_name"],
                )
                if observation is None:
                    raise RuntimeError("browser native manual test observation was not created")
                native_result = service.observe_native(
                    source["terminal_id"], source["incarnation_id"], 1, observation
                )
                native_receipt = {
                    "question_id": native_result.question_id,
                    "lifecycle": native_result.lifecycle,
                }
                visible_questions.append(
                    {
                        "question_id": str(native_result.question_id),
                        "admitted_ms": int(time.time() * 1000),
                    }
                )

                credential_group = service.create_group(
                    source_tokens[0],
                    CreateGroupRequest(
                        questions=[
                            QuestionInput(
                                kind="text",
                                prompt="What is your password?",
                                required=True,
                            )
                        ]
                    ),
                    "browser-e2e-explicit-credential-operation",
                )
                credential_question_id = str(credential_group.questions[0]["question_id"])
                visible_questions.append(
                    {
                        "question_id": credential_question_id,
                        "admitted_ms": int(time.time() * 1000),
                    }
                )
                if len(visible_questions) != 100:
                    raise RuntimeError("browser capacity seed did not create 100 questions")
                population_snapshot = {
                    "questions": visible_questions,
                    "target_question_id": target_question_id,
                    "target_prompt": "Select the next safe action for source 0, request 0-0.",
                    "target_admitted_ms": target_admitted_ms,
                    "scenarios": target_scenarios,
                }
                print(json.dumps({"ack": action, **population_snapshot}), flush=True)
            elif action == "mcp_start_wait":
                if target_group_id is None or not capability_file.exists():
                    raise RuntimeError("MCP wait requires the populated source capability")
                if mcp_wait_started:
                    raise RuntimeError("MCP wait already started for this fixture")
                expected_responses = command.get("expected_responses", {})
                if not isinstance(expected_responses, dict):
                    raise RuntimeError("MCP expected response set is invalid")
                mcp_thread = threading.Thread(
                    target=run_mcp_wait,
                    args=(expected_responses,),
                    name="interaction-browser-mcp-wait",
                    daemon=True,
                )
                mcp_thread.start()
                ready = mcp_events.get(timeout=90)
                if ready.get("event") != "mcp_ready":
                    raise RuntimeError(
                        str(ready.get("message", "MCP process did not become ready"))
                    )
                mcp_wait_started = True
                print(json.dumps({"ack": action, "started": True}), flush=True)
            elif action == "mcp_collect_result":
                if not mcp_wait_started:
                    raise RuntimeError("MCP wait has not started")
                result = mcp_events.get(timeout=60)
                if result.get("event") != "mcp_done":
                    raise RuntimeError(str(result.get("message", "MCP wait failed")))
                print(json.dumps({"ack": action, **result}), flush=True)
            elif action == "list_sessions":
                from cli_agent_orchestrator.services.session_service import list_sessions

                print(
                    json.dumps({"ack": action, "sessions": list_sessions()}),
                    flush=True,
                )
            elif action == "seed_native":
                if native_receipt is not None:
                    print(json.dumps({"ack": action, **native_receipt}), flush=True)
                    continue
                source = source_metadata[0]
                observation = NativeInteractionObservation.manual_signal(
                    screen_lines=["Waiting for manual input in the provider terminal."],
                    terminal_id=source["terminal_id"],
                    session_name=source["session_name"],
                    window_name=source["window_name"],
                )
                if observation is None:
                    raise RuntimeError("native manual test observation was not created")
                receipt = service.observe_native(
                    source["terminal_id"],
                    source["incarnation_id"],
                    1,
                    observation,
                )
                native_receipt = {
                    "question_id": receipt.question_id,
                    "lifecycle": receipt.lifecycle,
                }
                print(
                    json.dumps({"ack": action, **native_receipt}),
                    flush=True,
                )
            elif action == "seed_explicit_credential":
                if credential_question_id is None:
                    group = service.create_group(
                        source_tokens[0],
                        CreateGroupRequest(
                            questions=[
                                QuestionInput(
                                    # A typed text request containing an
                                    # authentication prompt is classified by
                                    # the production admission policy as
                                    # credential/manual-only.
                                    kind="text",
                                    prompt="What is your password?",
                                    required=True,
                                )
                            ]
                        ),
                        "browser-e2e-explicit-credential-operation",
                    )
                    credential_question_id = str(group.questions[0]["question_id"])
                print(
                    json.dumps({"ack": action, "question_id": credential_question_id}),
                    flush=True,
                )
            elif action == "stop":
                break
            else:
                raise ValueError("unknown browser acceptance command")
    finally:
        if mcp_thread is not None and mcp_thread.is_alive():
            mcp_thread.join(timeout=30)
        server.should_exit = True
        thread.join(timeout=30)
        listener.close()
        subprocess.run(
            ["tmux", "-f", "/dev/null", "kill-server"],
            env=tmux_environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        target_stack.close()
        workspace_context.cleanup()


if __name__ == "__main__":
    main()
