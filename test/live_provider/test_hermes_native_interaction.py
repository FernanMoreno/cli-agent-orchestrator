"""Opt-in native Hermes interaction proof with a loopback-only model stub.

Run with:

    CAO_RUN_HERMES_INTERACTION_TESTS=1 .venv/bin/python -m pytest \
        -o 'addopts=' -m '' test/live_provider/test_hermes_native_interaction.py -q

The test uses an already-installed Hermes 3.14 CLI and an isolated HERMES_HOME.
It never reads provider credentials or contacts a hosted model. It renders the
real CLI clarify dialog in a PTY, passes that live viewport through
HermesProvider and StatusMonitor, and verifies the interaction ledger records
only a manual notice and exact external closure.
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import re
import select
import shutil
import struct
import subprocess
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("CAO_RUN_HERMES_INTERACTION_TESTS") != "1",
        reason=(
            "Hermes native interaction acceptance is opt-in; set "
            "CAO_RUN_HERMES_INTERACTION_TESTS=1."
        ),
    ),
]


_PYTHON_314 = re.compile(r"\bPython:\s*3\.14(?:\.\d+)?\b")
_QUESTION = "Choose a color for the report cover"


def _minimal_hermes_env(hermes_home: Path) -> dict[str, str]:
    """Keep the provider process isolated from inherited API credentials/proxies."""
    return {
        "HERMES_HOME": str(hermes_home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TERM": "xterm-256color",
        "LC_ALL": "C.UTF-8",
        # The test invokes an installed CLI against its checkout, but must not
        # let startup repair/sync alter that external installation.
        "HERMES_DISABLE_LAZY_INSTALLS": "1",
    }


def _candidate_hermes_bins() -> list[Path]:
    explicit = os.environ.get("CAO_HERMES_TEST_BIN", "").strip()
    if explicit:
        return [Path(explicit).expanduser()]

    candidates: list[Path] = []
    managed_root = Path.home() / ".hermes" / "installs"
    if managed_root.is_dir():
        # Prefer a concrete installed venv entry point over PATH shims. A shim
        # can silently select another checkout or a stale interpreter while
        # still printing a supported Python version.
        candidates.extend(sorted(managed_root.glob("*/environments/*/venv/bin/hermes")))
    found = shutil.which("hermes")
    if found:
        candidates.append(Path(found))
    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _supported_hermes_bin(hermes_home: Path) -> Path:
    failures: list[str] = []
    for candidate in _candidate_hermes_bins():
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            failures.append(f"{candidate}: not executable")
            continue
        try:
            result = subprocess.run(
                [str(candidate), "--version"],
                env=_minimal_hermes_env(hermes_home),
                cwd=hermes_home,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            failures.append(f"{candidate}: {type(error).__name__}")
            continue
        version = f"{result.stdout}\n{result.stderr}"
        if result.returncode == 0 and _PYTHON_314.search(version):
            return candidate
        failures.append(
            f"{candidate}: exit={result.returncode}, supported Python 3.14 not reported"
        )
    detail = "; ".join(failures) if failures else "no Hermes executable found"
    pytest.skip(f"No runnable Hermes CLI using supported Python 3.14: {detail}")


class _LocalHermesModel:
    """Tiny OpenAI Chat Completions stub; every listener is loopback-only."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.tool_results: list[str] = []
        self.chat_requests = 0
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        model = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                return

            def _json(self, status: int, value: dict[str, Any]) -> None:
                raw = json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                if self.path.rstrip("/") == "/v1/models":
                    self._json(
                        200,
                        {
                            "object": "list",
                            "data": [
                                {
                                    "id": "cao-hermes-local-test",
                                    "object": "model",
                                    "created": 0,
                                    "owned_by": "test",
                                }
                            ],
                        },
                    )
                else:
                    self._json(
                        404, {"error": {"message": "local test endpoint only", "type": "not_found"}}
                    )

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 1_000_000:
                    self._json(
                        413,
                        {
                            "error": {
                                "message": "request too large",
                                "type": "invalid_request_error",
                            }
                        },
                    )
                    return
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path != "/v1/chat/completions":
                    self._json(
                        404, {"error": {"message": "local test endpoint only", "type": "not_found"}}
                    )
                    return

                tool_messages = [
                    message
                    for message in body.get("messages", [])
                    if isinstance(message, dict) and message.get("role") in {"tool", "function"}
                ]
                with model._lock:
                    model.chat_requests += 1
                    if tool_messages:
                        model.tool_results.extend(
                            str(message.get("content") or "") for message in tool_messages
                        )
                    request_no = model.chat_requests

                if not tool_messages:
                    arguments = json.dumps(
                        {
                            "questions": [{"question": _QUESTION, "choices": ["Blue", "Green"]}],
                        }
                    )
                    delta = {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_hermes_clarify_local_1",
                                "type": "function",
                                "function": {"name": "clarify", "arguments": arguments},
                            }
                        ],
                    }
                    finish_reason = "tool_calls"
                else:
                    delta = {"content": "Thanks. I will use the selected color."}
                    finish_reason = "stop"

                now = int(time.time())
                model_name = body.get("model") or "cao-hermes-local-test"
                if not body.get("stream"):
                    message = {"role": "assistant", **delta}
                    self._json(
                        200,
                        {
                            "id": f"chatcmpl-cao-hermes-{request_no}",
                            "object": "chat.completion",
                            "created": now,
                            "model": model_name,
                            "choices": [
                                {"index": 0, "message": message, "finish_reason": finish_reason}
                            ],
                            "usage": {
                                "prompt_tokens": 1,
                                "completion_tokens": 1,
                                "total_tokens": 2,
                            },
                        },
                    )
                    return

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                completion_id = f"chatcmpl-cao-hermes-{request_no}"
                for chunk in (
                    {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": now,
                        "model": model_name,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                    },
                    {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": now,
                        "model": model_name,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                    },
                ):
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        assert self.server is not None
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)


class _PtyHermes:
    def __init__(self, executable: Path, home: Path, cwd: Path) -> None:
        import pyte

        self.screen = pyte.Screen(100, 30)
        self.stream = pyte.Stream(self.screen)
        self.transcript = ""
        master_fd, slave_fd = pty.openpty()
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        self.master_fd = master_fd
        self.process = subprocess.Popen(
            [
                str(executable),
                "chat",
                "--cli",
                "--provider",
                "custom",
                "--toolsets",
                "clarify",
                "--max-turns",
                "2",
                "--run-budget",
                "45",
                "--yolo",
                "--source",
                "cao",
                "-q",
                "Ask me to choose a color for a report cover, then use my answer.",
            ],
            cwd=cwd,
            env=_minimal_hermes_env(home),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)

    @property
    def viewport(self) -> list[str]:
        return [line.rstrip() for line in self.screen.display]

    @property
    def viewport_text(self) -> str:
        return "\n".join(self.viewport)

    def pump(self, timeout: float = 0.2) -> None:
        ready, _, _ = select.select([self.master_fd], [], [], timeout)
        if not ready:
            return
        try:
            chunk = os.read(self.master_fd, 65536)
        except OSError:
            return
        if not chunk:
            return
        rendered = chunk.decode("utf-8", errors="replace")
        self.transcript = (self.transcript + rendered)[-1_000_000:]
        self.stream.feed(rendered)

    def wait_for(self, predicate, timeout: float, what: str) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump(0.1)
            if predicate():
                return
            if self.process.poll() is not None:
                break
        raise AssertionError(
            f"Timed out waiting for {what}; exit={self.process.poll()}, viewport={self.viewport_text[-2000:]}"
        )

    def send_enter(self) -> None:
        os.write(self.master_fd, b"\r")

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        try:
            os.close(self.master_fd)
        except OSError:
            pass


@pytest.fixture
def hermes_native_interaction_context(tmp_path, monkeypatch, postgres_interaction_store):
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.security import auth

    terminal_id = "ac010099"
    session_name = "hermes-live-native-clarify"
    incarnation_id = "hermes-live-clarify-incarnation"
    database.create_terminal(
        terminal_id,
        session_name,
        "0",
        "hermes",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id=incarnation_id,
        new_session_incarnation=True,
    )
    owner = auth._verified_principal(
        "https://hermes.live.integration",
        "hermes-native-owner",
        [auth.SCOPE_READ, auth.SCOPE_WRITE, auth.SCOPE_ADMIN],
        "jwt",
    )
    service = postgres_interaction_store.service
    # The isolated Hermes home below deliberately has no CAO MCP entry. Keep
    # the ledger source registered but uninstalled so this test covers only
    # native manual observation, not an explicit answer channel.
    service.register_source(
        terminal_id=terminal_id,
        incarnation_id=incarnation_id,
        session_name=session_name,
        provider="hermes",
        principal=owner,
        capability_available=False,
    )
    from cli_agent_orchestrator.services import terminal_service

    monkeypatch.setattr(terminal_service, "_interaction_service", lambda: service)
    return {
        "service": service,
        "owner": owner,
        "terminal_id": terminal_id,
        "session_name": session_name,
        "incarnation_id": incarnation_id,
    }


def test_real_hermes_clarify_viewport_records_manual_notice_and_exact_external_closure(
    tmp_path, hermes_native_interaction_context
):
    from cli_agent_orchestrator.models.terminal import TerminalStatus
    from cli_agent_orchestrator.providers.hermes import HermesProvider
    from cli_agent_orchestrator.services.status_monitor import StatusMonitor

    home = tmp_path / "isolated-hermes-home"
    home.mkdir(mode=0o700)
    model = _LocalHermesModel()
    model.start()
    config = home / "config.yaml"
    config.write_text(
        "model:\n"
        "  default: cao-hermes-local-test\n"
        "  provider: custom\n"
        f"  base_url: {model.base_url}\n"
        "  api_key: local-test-only\n"
        "agent:\n"
        "  max_turns: 2\n",
        encoding="utf-8",
    )
    config.chmod(0o600)
    executable = _supported_hermes_bin(home)
    runner: _PtyHermes | None = None
    context = hermes_native_interaction_context
    provider = HermesProvider(
        context["terminal_id"],
        context["session_name"],
        "0",
    )
    monitor = StatusMonitor()

    try:
        runner = _PtyHermes(executable, home, tmp_path)
        runner.wait_for(
            lambda: _QUESTION in runner.viewport_text
            and "Blue" in runner.viewport_text
            and "Green" in runner.viewport_text,
            timeout=30,
            what="the actual Hermes clarify picker",
        )

        # This is the real, currently rendered Hermes PTY viewport. The
        # provider reports only an opaque manual signal; it does not extract
        # the prompt, choices, or an answer target.
        native_status = provider.get_status(runner.viewport_text)
        assert native_status == TerminalStatus.WAITING_USER_ANSWER, (
            f"Hermes did not classify its live clarify UI: {native_status}; "
            f"viewport={runner.viewport_text!r}"
        )
        observation = provider.observe_native_interaction(native_status, runner.viewport)
        assert observation is not None
        assert observation.observation == "manual_signal"
        assert observation.prompt is None
        assert observation.options == ()
        assert observation.answer_capability == "none"
        assert provider.interaction_capabilities["explicit_channel"] == "unsupported"
        assert provider.interaction_source_configured({}) is False

        monitor._observe_native_interaction(
            context["terminal_id"], provider, native_status, runner.viewport
        )
        with context["service"].repository.read_connection() as connection:
            question = connection.execute(
                "SELECT * FROM interaction_questions WHERE terminal_id=%s AND source_kind='native'",
                (context["terminal_id"],),
            ).fetchone()
        assert question is not None
        question_id = question["question_id"]
        assert question["lifecycle"] == "manual_only"
        assert question["response_capability"] == "manual_only"
        assert (
            question["prompt_content"]
            == "A native provider dialog requires manual action in its terminal."
        )
        assert question["options_json"] == "[]"
        assert _QUESTION not in question["prompt_content"]

        # Model the normal direct-human-input ordering: record the source turn
        # advance, then send a key to the actual native Hermes UI. It must
        # reconcile the old exact observation, without synthesizing/storing a
        # human answer in the ledger.
        from cli_agent_orchestrator.services import terminal_service

        advanced_generation = terminal_service.record_direct_interaction_input(
            context["terminal_id"],
            expected_incarnation_id=context["incarnation_id"],
            reason="websocket_input",
            idempotency_key="hermes-native-direct-input-0001",
        )
        assert advanced_generation == 2
        runner.send_enter()
        runner.wait_for(
            lambda: model.tool_results
            and "Thanks. I will use the selected color." in runner.viewport_text,
            timeout=30,
            what="the Hermes tool result and following model turn",
        )
        assert any("Blue" in result for result in model.tool_results)
        closed_status = provider.get_status(runner.viewport_text)
        assert closed_status not in {
            TerminalStatus.WAITING_USER_ANSWER,
            TerminalStatus.UNKNOWN,
            TerminalStatus.ERROR,
        }

        # StatusMonitor's real pin/reconcile path needs two stable post-dialog
        # observations. Reusing the same captured real viewport models the
        # monitor's two reads while avoiding sleeps that could cross a timer.
        post_dialog_viewport = runner.viewport
        monitor._observe_native_interaction(
            context["terminal_id"], provider, closed_status, post_dialog_viewport
        )
        monitor._observe_native_interaction(
            context["terminal_id"], provider, closed_status, post_dialog_viewport
        )
        detail = context["service"].get_detail(context["owner"], question_id)
        assert detail["lifecycle"] == "reconciled_external"
        assert detail["manual_reason"]
        assert detail["can_answer"] is False
        assert detail["delivery_state"] is None
        assert detail["answer_receipt"] is None
        assert model.chat_requests >= 2
    finally:
        if runner is not None:
            runner.close()
        model.stop()
