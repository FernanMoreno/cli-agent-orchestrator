from __future__ import annotations
import hashlib, json, os, re, shutil, socket, subprocess, sys, tempfile, time, threading
from pathlib import Path
import requests
import argparse


def verified_turn(row, response, marker=None, expected_sequence=None, previous_generation=None):
    """Markers locate answers; only current durable receipts prove completion."""
    if response.status_code != 200 or row.get("status") not in {"idle", "completed"}:
        return False
    turn = row.get("turn") or {}
    sequence = row.get("turn_sequence")
    if turn.get("state") != "verified" or not turn.get("generation"):
        return False
    if type(sequence) is not int or sequence <= 0 or row.get("turn_completed") != sequence:
        return False
    if expected_sequence is not None and sequence != expected_sequence:
        return False
    if previous_generation is not None and turn["generation"] == previous_generation:
        return False
    answer = str(response.json().get("output", ""))
    return bool(answer.strip()) and (marker is None or marker in "".join(answer.split()))


def initial_review_verified(row, response):
    """Delivered callbacks do not prove their final supervisor turn is complete."""
    return verified_turn(row, response, "COLLABORATION_REVIEW_FINAL")


def preflight(source, auth_root):
    """Fail before allocating state; never print login file contents."""
    source = source.resolve()
    if not (source / "api/server.py").is_file():
        raise SystemExit("--project must point to the collaboration demo")
    auth_paths = {relative: auth_root / relative for relative in (
        ".claude/.credentials.json", ".claude.json", ".codex/auth.json",
        ".local/share/opencode/auth.json",
    )}
    missing = [str(path) for path in auth_paths.values() if not path.is_file()]
    if missing:
        raise SystemExit("missing reusable provider login record(s): " + ", ".join(missing))
    try:
        login = json.loads(auth_paths[".claude/.credentials.json"].read_text())
        expires = login.get("claudeAiOauth", {}).get("expiresAt")
    except (ValueError, AttributeError):
        raise SystemExit("invalid Claude login record; run claude auth login") from None
    if isinstance(expires, (int, float)) and expires <= time.time() * 1000:
        raise SystemExit("Claude login expired; run claude auth login")
    missing_tools = [name for name in ("claude", "codex", "opencode", "tmux") if not shutil.which(name)]
    if missing_tools:
        raise SystemExit("missing provider executable(s): " + ", ".join(missing_tools))
    return source, auth_paths


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    parser = argparse.ArgumentParser(description="Real two-round Claude/Codex/OpenCode demo validation")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--full", action="store_true", help="also verify authenticated peer messaging, handoff and external MCP")
    parser.add_argument("--recovery", action="store_true", help="also verify owned restart, pending inbox and native failure/timeout")
    args = parser.parse_args()
    source, auth_paths = preflight(args.project, Path.home())
    with tempfile.TemporaryDirectory(prefix="cao-real-audit-20261005-") as root:
        if args.full or args.recovery:
            from scripts.agent_collaboration_acceptance import Acceptance
            with Acceptance(Path(root), recovery=args.recovery) as acceptance:
                _run(source, auth_paths, Path(root), acceptance=acceptance)
        else:
            _run(source, auth_paths, Path(root))


def _run(SOURCE, auth_paths, ROOT, *, acceptance=None):
    client = acceptance.client if acceptance is not None else requests
    REPO = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(REPO))
    PROJECT = ROOT / "project"
    HOME = ROOT / "cao-home"
    TMUX_TMP = ROOT / "tmux"
    TMUX_TMP.mkdir()
    def ignore(_directory, names):
        return {n for n in names if n == "__pycache__" or n.endswith(".pyc")}
    shutil.copytree(SOURCE, PROJECT, ignore=ignore)

    def manifest(root):
        return {
            p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".pyc")
        }
    source_before = manifest(SOURCE)
    project_before = manifest(PROJECT)
    implementation_files = [REPO / "src/cli_agent_orchestrator" / name for name in (
        "api/main.py", "providers/opencode_cli.py", "services/workflow_continuation_driver.py",
        "services/work_coordinator.py", "services/script_runner.py",
    )]
    implementation_before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in implementation_files}
    if source_before != project_before:
        raise SystemExit("temporary project copy does not match source")

    # Keep every CAO, provider, config, and tmux artifact in this temporary home.
    extra_env = {
        "CAO_HOME_DIR": str(HOME / ".aws/cli-agent-orchestrator"),
        "CAO_ENABLE_WORKING_DIRECTORY": "true",
        "CAO_TERMINAL_BACKEND": "tmux",
        "TMUX_TMPDIR": str(TMUX_TMP),
        "PATH": f"{REPO}/.venv/bin:{os.environ.get('PATH','')}",
        "CODEX_HOME": str(HOME / ".codex"),
        "CLAUDE_CONFIG_DIR": str(HOME / ".claude"),
        "XDG_DATA_HOME": str(HOME / ".local/share"),
        "XDG_CONFIG_HOME": str(HOME / ".config"),
        "OPENCODE_CONFIG_DIR": str(HOME / ".config/opencode"),
        "OPENCODE_CONFIG": "",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
    }
    if acceptance is not None:
        extra_env.update(acceptance.env)
        extra_env["CAO_REGISTERED_PROJECTS"] = json.dumps([str(PROJECT)])
    # Use the isolated subscription/login files, never ambient API-key or token variables.
    for key in os.environ:
        if key.endswith(("API_KEY", "API_TOKEN", "BASE_URL")) or key in {
            "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN", "OPENAI_ACCESS_TOKEN"
        }:
            extra_env[key] = ""

    def port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def write_profile(name, provider, model, role, body):
        store = HOME / ".aws/cli-agent-orchestrator/agent-store"
        store.mkdir(parents=True, exist_ok=True)
        content = (
            "---\n"
            f"name: {name}\n"
            f"description: Temporary read-only collaboration profile for {provider}\n"
            f"provider: {provider}\n"
            f"model: {model}\n"
            f"role: {role}\n"
            "allowedTools:\n"
            '  - "@cao-mcp-server"\n'
            + ('  - "@local-probe"\n' if acceptance is not None and name == "demo-api" else "")
            + "  - fs_read\n"
            "  - fs_list\n"
            "mcpServers:\n"
            "  cao-mcp-server:\n"
            "    type: stdio\n"
            f"    command: {REPO}/.venv/bin/{'cao-mcp-server' if acceptance is not None else 'python'}\n"
            + ("    args: []\n" if acceptance is not None else f"    args: [{json.dumps(str(wrapper))}]\n")
            + ("  local-probe: " + json.dumps(acceptance.mcp_config) + "\n"
               if acceptance is not None and name == "demo-api" else "")
            +
            "---\n\n"
            + body.strip() + "\n"
        )
        (store / f"{name}.md").write_text(content, encoding="utf-8")

    profiles = {
        "demo-supervisor": (
            "claude_code", "sonnet", "supervisor",
            """You coordinate a read-only review of this CAO demo project.
    You MUST use the CAO MCP tools assign and send_message. Never substitute another delegation tool.
    If assign is absent, stop and report that cao-mcp-server did not load.

    When the initial review task arrives, assign demo-api exactly once and demo-frontend exactly once. Use only CAO assign. Never repeat an assignment or create replacement workers. After both assignments are accepted, finish the dispatch phase with a concise summary of which results remain pending. Do not poll or join workers and do not wait for callbacks inside this turn. Follow the attached current-turn receipt contract. If acceptance is uncertain, report the failure instead of retrying.
    Ask demo-api to inspect api/server.py, tests/test_server.py, and CONTRACT.md; check endpoint and validation behavior against the contract; do not edit files; then report findings to you with CAO send_message, omitting receiver_id.
    Ask demo-frontend to inspect frontend/app.js, tests/test_frontend.js, and CONTRACT.md; check requests, response handling, and contract alignment; do not edit files; then report findings to you with CAO send_message, omitting receiver_id.
    Each later CAO callback is a separate review stage. Acknowledge the first callback concisely and complete that stage with its attached receipt. After the second callback, compare both findings already in the conversation and return one combined report beginning COLLABORATION_REVIEW_FINAL with headings API and Frontend and a sentence saying whether they agree on CONTRACT.md. Complete this final stage with its attached receipt. For a later RONDA2 callback, acknowledge the first and after both workers reply give a combined API and Frontend report containing RONDA2_FINAL. End each callback turn with its receipt. Do not edit files or run shell commands."""
        ),
        "demo-api": (
            "codex", "gpt-6.1-sol", "reviewer",
            """Review only the API portion of this shared temporary demo project.
    Inspect api/server.py, tests/test_server.py, and CONTRACT.md. Report whether routes, validation, persistence, and error codes match the contract, with file and line references. Use read-only file-reading commands if the provider needs them. Do not run tests or change files.
    You MUST use CAO send_message to return your findings to the terminal that assigned you; omit receiver_id. If send_message is absent, stop and report that cao-mcp-server did not load."""
        ),
        "demo-frontend": (
            "opencode_cli", "opencode-go/gpt-6-luna", "reviewer",
            """Review only the frontend portion of this shared temporary demo project.
    Inspect frontend/app.js, tests/test_frontend.js, and CONTRACT.md. Report whether request paths, payloads, response handling, and visible errors match the contract, with file and line references. Use read-only file-reading commands if the provider needs them. Do not run tests or change files.
    You MUST use CAO send_message to return your findings to the terminal that assigned you; omit receiver_id. If send_message is absent, stop and report that cao-mcp-server did not load."""
        ),
    }
    if acceptance is not None:
        provider, model, role, body = profiles["demo-supervisor"]
        profiles["demo-supervisor"] = (provider, model, role, body + "\nOn a later ACCEPTANCE_HANDOFF task, perform the single requested handoff and report its returned result with the current receipt. The initial review assignments have already been completed.")
        provider, model, role, body = profiles["demo-api"]
        profiles["demo-api"] = (provider, model, role, body + "\nIf a later task begins ACCEPTANCE_PEER, send the requested direct CAO message and finish with PEER_SENT. If a later CAO message contains CONTRACT_REPLY, acknowledge the actual answer with PEER_DONE and your current receipt; do not send another message. If a task begins ACCEPTANCE_EXTERNAL, use local-probe and return its actual result without sending callbacks.")
        provider, model, role, body = profiles["demo-frontend"]
        profiles["demo-frontend"] = (provider, model, role, body + "\nIf a later CAO message contains CONTRACT_PROBE, read CONTRACT.md, then send_message exactly once back to its real sender ID with CONTRACT_REPLY: followed by the supplied nonce and one endpoint detail. End with FRONT_PEER_DONE and your current receipt; do not notify the supervisor.")
        profiles["demo-handoff"] = ("codex", "gpt-6.1-sol", "reviewer", "Read CONTRACT.md and answer the short handoff task. Do not send_message or assign; return the requested marker and endpoint detail, then the attached current receipt.")
    if acceptance is not None and acceptance.recovery:
        provider, model, role, body = profiles["demo-api"]
        profiles["demo-api"] = (provider, model, role, body + "\nOn RECOVERY_REVIEW read the requested files and return RECOVERY_READY with your current receipt, no callbacks. On a later RECOVERY_PENDING message, acknowledge it with RECOVERY_DELIVERED and your current receipt, no callbacks.")
        provider, model, role, body = profiles["demo-supervisor"]
        profiles["demo-supervisor"] = (provider, model, role, body + "\nOn ACCEPTANCE_TIMEOUT call handoff exactly once with the requested timeout. If it times out, report HANDOFF_TIMEOUT and the actual returned terminal_id/job_id. Never retry or assign. If it succeeds, report UNEXPECTED_SUCCESS honestly.")
    instrument = ROOT / "instrument"
    instrument.mkdir()
    (instrument / "sitecustomize.py").write_text("import faulthandler, os, hashlib, json, importlib.machinery, threading, atexit\nfrom pathlib import Path\nif os.environ.get('CAO_DIAGNOSTIC_STACKS') == '1':\n    faulthandler.enable()\n    _stack_samples_stop = threading.Event()\n    def _sample_stacks():\n        while not _stack_samples_stop.wait(40):\n            faulthandler.dump_traceback(all_threads=True)\n    atexit.register(_stack_samples_stop.set)\n    threading.Thread(target=_sample_stacks, name='cao-stack-sampler', daemon=True).start()\n_original = importlib.machinery.SourceFileLoader.get_code\n_watched = {'cli_agent_orchestrator.api.main', 'cli_agent_orchestrator.providers.opencode_cli', 'cli_agent_orchestrator.services.workflow_continuation_driver', 'cli_agent_orchestrator.services.work_coordinator', 'cli_agent_orchestrator.services.script_runner'}\ndef _record_code(self, fullname):\n    code = _original(self, fullname)\n    if fullname in _watched:\n        with open(os.environ['CAO_TEST_SOURCE_MANIFEST'], 'a') as output:\n            output.write(json.dumps({'module': fullname, 'source': self.path, 'sha256': hashlib.sha256(Path(self.path).read_bytes()).hexdigest()}) + '\\n')\n    return code\nimportlib.machinery.SourceFileLoader.get_code = _record_code\n")
    if acceptance is not None and acceptance.recovery:
        (ROOT / "receipt-gate.json").write_text("{}")
        with (instrument / "sitecustomize.py").open("a") as output:
            gate_code = (
                "from cli_agent_orchestrator.services import terminal_service as _ts\n"
                "_verify_original=_ts._verify_receipt_bearing_result\n"
                "_gate=Path(" + repr(str(ROOT / "receipt-gate.json")) + ")\n"
                "_witness=Path(" + repr(str(ROOT / "receipt-witness.jsonl")) + ")\n"
                "def _hide_receipt(terminal_id,provider,transcript,result,**kwargs):\n"
                "    config=json.loads(_gate.read_text())\n"
                "    if config.get('active') and config.get('terminal_id')==terminal_id:\n"
                "        witness=(provider.receipt_result_viewport_terminal_status if kwargs.get('rendered_viewport') else provider.receipt_result_terminal_status)(transcript,result)\n"
                "        if witness is not None:\n"
                "            with _witness.open('a') as out:out.write(json.dumps({'terminal_id':terminal_id,'native_receipt_witness':True})+'\\n')\n"
                "        raise _ts.TurnResultUnavailableError('Controlled receipt visibility refusal')\n"
                "    return _verify_original(terminal_id,provider,transcript,result,**kwargs)\n"
                "_ts._verify_receipt_bearing_result=_hide_receipt\n"
            )
            output.write("if 'cli_agent_orchestrator.api.main' in __import__('sys').orig_argv:\n"
                         + "".join("    " + line + "\n" for line in gate_code.splitlines()))
    extra_env["CAO_TEST_SOURCE_MANIFEST"] = str(ROOT / "imported-source-manifest.jsonl")
    extra_env["PYTHONPATH"] = str(instrument) + ":" + str(REPO / "src") + ":" + str(REPO)
    extra_env["CAO_DIAGNOSTIC_STACKS"] = "1"
    wrapper = ROOT / "mcp-diagnostic.py"
    wrapper.write_text("import os, subprocess, time\nfrom pathlib import Path\np=Path(" + repr(str(ROOT)) + ") / ('mcp-' + str(os.getpid()))\nf=open(str(p)+'.stderr','w')\nos.chmod(str(p)+'.stderr',0o600)\nt=time.monotonic()\nc=subprocess.Popen([" + repr(str(REPO / '.venv/bin/cao-mcp-server')) + "],stderr=f)\nrc=c.wait()\nf.write('\\nDIAGNOSTIC_EXIT='+str(rc)+' elapsed='+str(time.monotonic()-t)+'\\n')\nf.flush()\nraise SystemExit(rc if rc>=0 else 128-rc)\n")
    stop_watch = threading.Event()
    def watchdog():
        while not stop_watch.wait(10):
            t = time.monotonic()
            try:
                r = client.get(server.url + "/health", timeout=3)
                outcome = str(r.status_code)
            except Exception as e:
                outcome = type(e).__name__
            print("WATCH health=" + outcome + " seconds=" + str(round(time.monotonic()-t,2)), flush=True)
            with open(ROOT / "memory.log", "a") as f:
                f.write(str(time.time()) + " " + " ".join(x.strip() for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith(('MemAvailable:', 'SwapFree:'))) + "\n")
    server = None
    session_name = None
    parent_id = None
    result = "FAIL"
    server_log_tail = ""
    try:
        # The CAO test helper gives this run an isolated HOME, port, CAO database, and process group.
        from test.fixtures.cao_server import _start_cao_server
        selected_port = port()
        server = _start_cao_server(HOME, selected_port, extra_env=extra_env, deadline=90)
        base = server.url
        threading.Thread(target=watchdog, daemon=True).start()
        print(f"CAO_SERVER=ready URL={base} HOME={HOME}", flush=True)

        # Make the four selected authentication files visible only inside the disposable HOME.
        for rel, src in auth_paths.items():
            dest = HOME / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if rel == ".claude.json":
                shutil.copy2(src, dest)
                dest.chmod(0o600)
            else:
                dest.symlink_to(src)
        for name, (provider, model, role, body) in profiles.items():
            write_profile(name, provider, model, role, body)

        for name, (provider, _model, _role, _body) in profiles.items():
            response = client.post(
                f"{base}/agents/profiles/install",
                json={"source": name, "provider": provider},
                timeout=30,
            )
            if response.status_code != 200:
                raise RuntimeError(f"profile install failed for {name}: HTTP {response.status_code} {response.text[:500]}")
            print(f"PROFILE_INSTALLED={name}:{provider}", flush=True)
        mcp_configs = [str(p.relative_to(HOME)) for p in HOME.rglob("*") if p.is_file() and p.suffix in {".json", ".toml", ".yaml", ".yml"} and "cao-mcp-server" in p.read_text(errors="replace")]
        print("MCP_CONFIG_FILES=" + json.dumps(mcp_configs), flush=True)

        session_name = f"demo-collab-{os.urandom(4).hex()}"
        parent_model = profiles["demo-supervisor"][1]
        task = (
            "Run a read-only cross-provider review of this project. Coordinate two workers through CAO: "
            "assign demo-api and demo-frontend asynchronously, then return control so their callbacks can arrive. "
            "Each worker must send findings back to you through CAO send_message. Do not edit project files. "
            "Dispatch both workers through CAO and acknowledge which results remain pending. "
            "End the dispatch turn after acceptance. "
            "The subsequent callbacks are new stages; after the second callback, return the combined API and Frontend report."
        )
        response = client.post(
            f"{base}/sessions",
            params={
                "provider": "claude_code",
                "agent_profile": "demo-supervisor",
                "session_name": session_name,
                "working_directory": str(PROJECT),
                "model": parent_model,
            },
            json={"initial_message": task},
            timeout=240,
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(f"supervisor session create failed: HTTP {response.status_code} {response.text[:800]}")
        parent_id = str(response.json()["id"])
        session_name = response.json().get("session_name", session_name)
        print(f"SUPERVISOR_CREATED provider=claude_code profile=demo-supervisor terminal={parent_id}", flush=True)

        def terminal(tid):
            r = client.get(f"{base}/terminals/{tid}", timeout=45)
            if r.status_code != 200:
                return {"status": f"http_{r.status_code}"}
            return r.json()

        def wait_ready(tid, seconds=180):
            until = time.monotonic() + seconds
            last = "unknown"
            while time.monotonic() < until:
                row = terminal(tid)
                last = row.get("status", "unknown")
                if last in {"idle", "completed"}:
                    return row
                if last == "error":
                    out = client.get(f"{base}/terminals/{tid}/output", params={"mode":"last"}, timeout=45).json()
                    raise RuntimeError(f"terminal {tid} init error: {str(out.get('output',''))[-1200:]}")
                time.sleep(1)
            raise TimeoutError(f"terminal {tid} not ready after {seconds}s; last_status={last}")

        print(
            "TASK_SENT=Claude initial task uses CAO deferred-init pickup confirmation; it must assign Codex and OpenCode",
            flush=True,
        )

        started = time.monotonic()
        timeout = 480
        last_log = 0
        final_status = "unknown"
        final_terms = []
        final_messages = []
        final_output = ""
        while time.monotonic() - started < timeout:
            elapsed = int(time.monotonic() - started)
            s = client.get(f"{base}/sessions/{session_name}", timeout=45)
            if s.status_code == 200:
                sd = s.json()
                final_terms = sd.get("terminals", []) or []
            else:
                final_terms = []
            details = []
            for item in final_terms:
                tid = str(item.get("id", ""))
                full = terminal(tid) if tid else {}
                details.append({
                    "id": tid,
                    "provider": full.get("provider", item.get("provider")),
                    "agent_profile": full.get("agent_profile", item.get("agent_profile")),
                    "status": full.get("status", item.get("status")),
                })
            final_status = next((x["status"] for x in details if x["id"] == parent_id), "unknown")
            inbox = client.get(
                f"{base}/terminals/{parent_id}/inbox/messages",
                params={"status": "delivered", "limit": 50},
                timeout=45,
            )
            if inbox.status_code == 200:
                payload = inbox.json()
                final_messages = payload if isinstance(payload, list) else payload.get("messages", [])
            else:
                final_messages = []
            senders = {str(m.get("sender_id")) for m in final_messages}
            child_ids = {x["id"] for x in details if x["id"] and x["id"] != parent_id}
            api_term = next((x for x in details if x["agent_profile"] == "demo-api"), None)
            front_term = next((x for x in details if x["agent_profile"] == "demo-frontend"), None)
            output_response = client.get(
                f"{base}/terminals/{parent_id}/output",
                params={"mode": "full"},
                timeout=45,
            )
            if output_response.status_code == 200:
                final_output = str(output_response.json().get("output", ""))
            if elapsed - last_log >= 15:
                print(
                    f"WAIT elapsed={elapsed}s parent={final_status} children={json.dumps(details, ensure_ascii=False)} delivered={len(final_messages)}",
                    flush=True,
                )
                last_log = elapsed
            both_children = bool(api_term and front_term)
            both_messages = bool(api_term and front_term and {api_term["id"], front_term["id"]} <= senders)
            if both_children and both_messages and final_status in {"idle", "completed"}:
                synthesis = client.get(
                    f"{base}/terminals/{parent_id}/output", params={"mode": "last"}, timeout=45
                )
                if initial_review_verified(terminal(parent_id), synthesis):
                    result = "CALLBACKS_RECEIVED"
                    break
            # Fail early if coordinator explicitly reports that the CAO tool surface is absent.
            if elapsed > 60 and not child_ids and final_status in {"idle", "completed"}:
                lower = final_output.lower()
                if "assign" in lower and ("not available" in lower or "not present" in lower or "mcp" in lower):
                    raise RuntimeError("Claude did not have CAO assign tools; no worker terminal was created.")
            time.sleep(2)

        if result != "CALLBACKS_RECEIVED":
            raise TimeoutError(f"collaboration did not complete within {timeout}s; children/messages/status captured in wait logs")
        sanitized = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", final_output)
        print("TERMINALS=" + json.dumps(details, ensure_ascii=False), flush=True)
        print("DELIVERED_MESSAGES=" + json.dumps([
            {
                "sender_id": m.get("sender_id"),
                "receiver_id": m.get("receiver_id"),
                "status": m.get("status"),
                "message": str(m.get("message", ""))[:1200],
            } for m in final_messages
        ], ensure_ascii=False), flush=True)
        print("SUPERVISOR_FINAL=" + sanitized[-2400:], flush=True)
        last_response = client.get(
            f"{base}/terminals/{parent_id}/output", params={"mode": "last"}, timeout=30
        )
        if last_response.status_code != 200:
            raise RuntimeError("Supervisor final result is not verified: HTTP " + str(last_response.status_code))
        final_answer = str(last_response.json().get("output", ""))
        print("VERIFIED_SUPERVISOR_FINAL=" + final_answer[-3500:], flush=True)
        if "API" not in final_answer or "Frontend" not in final_answer:
            raise RuntimeError("CAO messages arrived but supervisor final synthesis did not include both workstreams.")
        # Require verified completion from both actual workers before reusing them.
        def verified_worker(tid, marker=None, budget=150, expected_sequence=None, previous_generation=None):
            deadline = time.monotonic() + budget
            while time.monotonic() < deadline:
                row = terminal(tid)
                r = client.get(f"{base}/terminals/{tid}/output", params={"mode":"last"}, timeout=15)
                answer = str(r.json().get("output", "")) if r.status_code == 200 else ""
                if verified_turn(row, r, marker, expected_sequence, previous_generation):
                    if "CAO completion receipt requirement" in answer:
                        raise RuntimeError("Worker LAST includes echoed delivery contract: " + tid)
                    print("WORKER_VERIFIED=" + tid + " marker=" + str(marker), flush=True)
                    return answer
                time.sleep(2)
            raise TimeoutError("Worker receipt not verified: " + tid + " marker=" + str(marker))
        for worker in (api_term, front_term):
            verified_worker(worker["id"])
        old_ids = {str(m.get("id")) for m in final_messages}
        round2_sequences = {}
        prior_generations = {w["id"]: terminal(w["id"])["turn"]["generation"] for w in (api_term, front_term)}
        for worker, focus in ((api_term, "API validation and persistence"), (front_term, "Frontend error handling and escaped task titles")):
            message = ("RONDA2: Reuse this same terminal. Review " + focus + " against actual project source, read-only. "
                       "Return a concise finding beginning RONDA2 via CAO send_message with receiver_id=" + parent_id + ". "
                       "Do not create workers, edit files or run tests. After sending, your final answer must include RONDA2_DONE and your attached receipt.")
            r = client.post(f"{base}/terminals/{worker['id']}/input", params={"message":message}, timeout=30)
            if r.status_code != 200 or not r.json().get("success"):
                raise RuntimeError("Round2 dispatch rejected: " + str(r.status_code) + " " + r.text[:500])
            round2_sequences[worker["id"]] = r.json()["turn_sequence"]
            print("ROUND2_DISPATCH=" + worker["id"] + " " + r.text, flush=True)
        for worker in (api_term, front_term):
            verified_worker(worker["id"], "RONDA2_DONE", 240, round2_sequences[worker["id"]], prior_generations[worker["id"]])
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            r = client.get(f"{base}/terminals/{parent_id}/inbox/messages", params={"status":"delivered", "limit":50}, timeout=15)
            payload = r.json()
            messages = payload if isinstance(payload, list) else payload.get("messages", [])
            fresh = [m for m in messages if str(m.get("id")) not in old_ids and "RONDA2" in str(m.get("message", ""))]
            r = client.get(f"{base}/terminals/{parent_id}/output", params={"mode":"last"}, timeout=15)
            answer = str(r.json().get("output", "")) if r.status_code == 200 else ""
            if {api_term["id"], front_term["id"]} <= {str(m.get("sender_id")) for m in fresh} and verified_turn(terminal(parent_id), r, "RONDA2_FINAL"):
                if len(fresh) != 2:
                    raise RuntimeError("Round2 produced duplicate callbacks")
                print("ROUND2_VERIFIED_FINAL=" + answer[-2500:], flush=True)
                break
            time.sleep(2)
        else:
            raise TimeoutError("Round2 fresh callbacks and supervisor synthesis not verified")
        terms = client.get(f"{base}/sessions/{session_name}", timeout=15).json().get("terminals", [])
        if {str(t["id"]) for t in terms} != {parent_id, api_term["id"], front_term["id"]}:
            raise RuntimeError("Round2 unexpectedly changed terminal membership")
        print("ROUND2=PASS same_three_terminals=true fresh_callbacks=" + str(len(fresh)), flush=True)
        if acceptance is not None:
            def restart_runtime():
                nonlocal server
                server.stop()
                extra_env.update(acceptance.env)
                server = _start_cao_server(HOME, selected_port, extra_env=extra_env, deadline=90)

            acceptance.run(base, parent_id, api_term["id"], front_term["id"], terminal, verified_worker,
                           runtime={"restart": restart_runtime, "home": HOME,
                                    "socket": TMUX_TMP / f"tmux-{os.getuid()}" / "default",
                                    "session": session_name, "project": PROJECT})
        if manifest(PROJECT) != project_before:
            raise RuntimeError("temporary demo project changed during read-only collaboration.")
        if manifest(SOURCE) != source_before:
            raise RuntimeError("original demo source changed during provider collaboration.")
        if {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in implementation_files} != implementation_before:
            raise RuntimeError("CAO implementation changed during validation; proof is invalidated")
        print("CAO_IMPLEMENTATION_UNCHANGED=true", flush=True)
        print("PROJECT_FILES_UNCHANGED=true", flush=True)
        result = "PASS"
        print("COLLABORATION=PASS", flush=True)
    except BaseException as exc:
        result = "FAIL"
        print(f"COLLABORATION=FAIL error={type(exc).__name__}: {exc}", flush=True)
        if parent_id and server is not None:
            try:
                for mode in ("last", "full"):
                    payload = client.get(
                        f"{server.url}/terminals/{parent_id}/output",
                        params={"mode": mode}, timeout=10,
                    ).json()
                    diagnostic = re.sub(
                        r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", str(payload.get("output", ""))
                    )
                    print(f"FINAL_PARENT_DIAGNOSTIC_{mode.upper()}=" + diagnostic[-5000:], flush=True)
            except Exception as diagnostic_error:
                print("PARENT_DIAGNOSTIC_UNAVAILABLE=" + type(diagnostic_error).__name__, flush=True)
            try:
                tmux_socket = TMUX_TMP / f"tmux-{os.getuid()}" / "default"
                panes = subprocess.run(
                    ["tmux", "-S", str(tmux_socket), "list-panes", "-a", "-F",
                     "#{session_name}:#{window_index}.#{pane_index} #{pane_current_command}"],
                    text=True, capture_output=True, timeout=10, check=False,
                )
                print("TMUX_PANES=" + panes.stdout[-2000:], flush=True)
                if session_name:
                    capture = subprocess.run(
                        ["tmux", "-S", str(tmux_socket), "capture-pane", "-p", "-S", "-1500",
                         "-t", f"{session_name}:0.0"],
                        text=True, capture_output=True, timeout=10, check=False,
                    )
                    print("PARENT_TMUX_SCREEN=" + capture.stdout[-5000:], flush=True)
            except Exception as screen_error:
                print("TMUX_DIAGNOSTIC_UNAVAILABLE=" + type(screen_error).__name__, flush=True)
        if server is not None:
            try:
                server_log_tail = "\n".join(server.log_path.read_text(errors="replace").splitlines()[-45:])
            except Exception:
                server_log_tail = "<unavailable>"
            if server_log_tail:
                print("SERVER_LOG_TAIL_BEGIN\n" + server_log_tail + "\nSERVER_LOG_TAIL_END", flush=True)
    finally:
        stop_watch.set()
        archive = Path("/tmp/cao-investigation-evidence-20261005") / ROOT.name
        archive.mkdir(parents=True, exist_ok=True, mode=0o700)
        for source in [HOME / "server.log", ROOT / "memory.log", ROOT / "imported-source-manifest.jsonl", ROOT / "acceptance-results.json", ROOT / "external-probes.jsonl", ROOT / "recovery-results.json", ROOT / "receipt-witness.jsonl", ROOT / "recovery-boundaries.json", *ROOT.glob("mcp-*.stderr")]:
            if source.is_file():
                shutil.copy2(source, archive / source.name)
        for source in (HOME / ".aws/cli-agent-orchestrator/opencode-v2").glob("terminal-*/data/mcp-startup.log"):
            shutil.copy2(source, archive / (source.parent.parent.name + ".log"))
        print("DIAGNOSTIC_ARCHIVE=" + str(archive), flush=True)
        if server is not None and session_name:
            try:
                client.delete(f"{server.url}/sessions/{session_name}", timeout=30)
            except Exception:
                pass
        if server is not None:
            try:
                server.stop()
            except Exception:
                pass
        tmux_socket = TMUX_TMP / f"tmux-{os.getuid()}" / "default"
        subprocess.run(
            ["tmux", "-S", str(tmux_socket), "kill-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=45,
            check=False,
        )
        # Scrub only the disposable workspace, including provider auth symlinks.
        os.chdir("/tmp")
        shutil.rmtree(ROOT, ignore_errors=True)
        print("TEMPORARY_CA0_RUNTIME=removed", flush=True)
    if result != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
