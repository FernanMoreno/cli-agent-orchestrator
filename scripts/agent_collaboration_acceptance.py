#!/usr/bin/env python3
"""Extra native acceptance scenarios, reusing validate_collaboration_demo.

All agents, login copies, JWKS and external MCP service belong to a disposable
runtime. The operator harness sends tasks; workers send their own CAO messages.
"""

import json
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests


def recovery_message(messages, sender, receiver, nonce, state):
    matches = [message for message in messages if message.get("sender_id") == sender
               and message.get("receiver_id") == receiver
               and "RECOVERY_PENDING:" + nonce in str(message.get("message", ""))]
    if len(matches) > 1:
        raise RuntimeError("duplicate recovery messages")
    return matches[0] if matches and matches[0].get("status") == state else None


def same_durable_turn(before, after):
    generation = (before.get("turn") or {}).get("generation")
    return bool(generation and before.get("id") == after.get("id")
                and (after.get("turn") or {}).get("generation") == generation)


def peer_messages_verified(messages, api_id, frontend_id, nonce):
    pairs = ((api_id, frontend_id, "CONTRACT_PROBE:"),
             (frontend_id, api_id, "CONTRACT_REPLY:"))
    counts = [sum(
        message.get("sender_id") == sender and message.get("receiver_id") == receiver
        and message.get("status") == "delivered"
        and marker + nonce in str(message.get("message", ""))
        for message in messages
    ) for sender, receiver, marker in pairs]
    if any(count > 1 for count in counts):
        raise RuntimeError("duplicate peer messages")
    return counts == [1, 1]


class Acceptance:
    def __init__(self, root, *, recovery=False):
        self.recovery = recovery
        self.root = Path(root)
        self.nonce = uuid.uuid4().hex
        self.external = None
        self.external_thread = None
        self.jwks = None
        self.client = requests.Session()

    def __enter__(self):
        from test.fixtures.cao_server import _JWKSServer, _session_rsa_keys
        from test.conftest import mint_test_token

        private, public = _session_rsa_keys()
        self.private_key = private
        self.jwks = _JWKSServer(public)
        try:
            self.jwks.start()
            self.env = {
                "CAO_AUTH_JWKS_URI": self.jwks.url,
                "CAO_AUTH_ISSUER": "https://test.local/",
                "CAO_AUTH_AUDIENCE": "cao://test",
                "CAO_AUTH_LOCAL_TOKEN": mint_test_token(
                    private, exp_offset=3600, subject="collaboration-acceptance-" + self.nonce
                ),
            }
            self.expired = mint_test_token(
                private, exp_offset=-300, iat_offset=-600,
                subject="collaboration-acceptance-" + self.nonce,
            )
            self.client.headers["Authorization"] = "Bearer " + self.env["CAO_AUTH_LOCAL_TOKEN"]
            payload = json.dumps({"nonce": self.nonce}).encode()

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

                def log_message(self, *_args):
                    pass

            self.external = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            self.external_url = "http://127.0.0.1:" + str(self.external.server_port)
            self.external_thread = threading.Thread(target=self.external.serve_forever, daemon=True)
            self.external_thread.start()
            script = self.root / "external-mcp.py"
            script.write_text(
                "import json,requests\nfrom pathlib import Path\nfrom fastmcp import FastMCP\n"
                "mcp=FastMCP('local-probe')\n@mcp.tool()\ndef probe_service() -> dict:\n"
                "    try:\n        result={'state':'available',**requests.get(" + repr(self.external_url) + ",timeout=2).json()}\n"
                "    except requests.ConnectionError:\n        result={'state':'unavailable','error':'ConnectionError'}\n"
                "    with Path(" + repr(str(self.root / "external-probes.jsonl")) + ").open('a') as out:\n"
                "        out.write(json.dumps(result)+'\\n')\n    return result\n"
                "if __name__=='__main__':mcp.run()\n"
            )
            self.mcp_config = {"type": "stdio", "command": sys.executable, "args": [str(script)]}
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def stop_external(self):
        if self.external is not None:
            self.external.shutdown()
            self.external.server_close()
            self.external_thread.join(timeout=3)
            self.external = None

    def __exit__(self, *_args):
        self.stop_external()
        if self.jwks is not None:
            self.jwks.stop()
        self.client.close()

    def run_recovery(self, base, parent, api, frontend, terminal, verified_worker, runtime):
        """Fault only this run; genuine agents produce every task/result/receipt."""
        import os
        import sqlite3
        import subprocess
        from test.conftest import mint_test_token

        def wait(predicate, seconds, description):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                value = predicate()
                if value:
                    return value
                time.sleep(2)
            raise TimeoutError(description)

        def dispatch(tid, message):
            previous = terminal(tid)["turn"]["generation"]
            response = self.client.post(base + f"/terminals/{tid}/input", params={"message": message}, timeout=30)
            response.raise_for_status()
            if not response.json().get("success"):
                raise RuntimeError("recovery dispatch not accepted")
            return response.json()["turn_sequence"], previous

        def inbox():
            response = self.client.get(base + f"/terminals/{api}/inbox/messages", params={"limit": 100}, timeout=15)
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, list) else payload.get("messages", [])

        def gate(active):
            path = self.root / "receipt-gate.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"active": active, "terminal_id": api}))
            temporary.replace(path)

        # Renew the controller bearer while all native workers remain alive.
        # Existing MCP processes retain their still-valid token; this is not
        # automatic hot reload of an expired provider environment.
        rejected = requests.get(base + "/sessions", headers={"Authorization": "Bearer " + self.expired}, timeout=10)
        if rejected.status_code != 401:
            raise RuntimeError("expired renewal credential accepted")
        self.env["CAO_AUTH_LOCAL_TOKEN"] = mint_test_token(
            self.private_key, exp_offset=3600, subject="collaboration-acceptance-" + self.nonce
        )
        self.client.headers["Authorization"] = "Bearer " + self.env["CAO_AUTH_LOCAL_TOKEN"]
        response = self.client.get(base + f"/sessions/{runtime['session']}", timeout=15)
        response.raise_for_status()
        identities = {str(row["id"]) for row in response.json()["terminals"]}
        if identities != {parent, api, frontend}:
            raise RuntimeError("renewal changed live terminal membership")
        print("AUTH_RENEWAL=PASS controller=true automatic_mcp_reload=not_tested", flush=True)

        gate(True)
        try:
            sequence, previous = dispatch(api, "RECOVERY_REVIEW: Read CONTRACT.md and api/server.py. Return RECOVERY_READY with one endpoint fact and your current receipt. No callbacks, edits, shell commands or retries.")
            wait(lambda: terminal(api).get("turn", {}).get("state") == "reconcile", 180, "native receipt did not reach reconcile")
            witnesses = self.root / "receipt-witness.jsonl"
            if not witnesses.is_file() or not any(json.loads(line).get("native_receipt_witness") for line in witnesses.read_text().splitlines()):
                raise RuntimeError("refusal did not observe a genuine native receipt")
            before = terminal(api)
            front_sequence, front_previous = dispatch(frontend, "Use CAO send_message exactly once to receiver_id=" + api
                + " with message RECOVERY_PENDING:" + self.nonce + ". This recipient is temporarily reconciling. Do not retry. Finish with RECOVERY_SENDER and your current receipt; no callback to supervisor.")
            verified_worker(frontend, "RECOVERY_SENDER", 180, front_sequence, front_previous)
            pending = wait(lambda: recovery_message(inbox(), frontend, api, self.nonce, "pending"), 45, "native recovery message was not pending")
            runtime["restart"]()
            after = terminal(api)
            (self.root / "recovery-boundaries.json").write_text(json.dumps({
                "before": {"id": before.get("id"), "turn": before.get("turn"), "turn_sequence": before.get("turn_sequence")},
                "after": {"id": after.get("id"), "turn": after.get("turn"), "turn_sequence": after.get("turn_sequence")},
                "pending_message_id": pending["id"],
            }, indent=2))
            if not same_durable_turn(before, after):
                raise RuntimeError("restart changed the pending native turn identity")
            retained = recovery_message(inbox(), frontend, api, self.nonce, "pending")
            if not retained or retained["id"] != pending["id"]:
                raise RuntimeError("restart lost or replayed the pending message")
        finally:
            gate(False)
        verified_worker(api, "RECOVERY_DELIVERED", 240, previous_generation=previous)
        delivered = recovery_message(inbox(), frontend, api, self.nonce, "delivered")
        if not delivered or delivered["id"] != pending["id"]:
            raise RuntimeError("recovered native message not uniquely delivered")
        response = self.client.get(base + f"/sessions/{runtime['session']}", timeout=15)
        response.raise_for_status()
        if {str(row["id"]) for row in response.json()["terminals"]} != identities:
            raise RuntimeError("restart created replacement workers")
        print("RESTART=PASS native_receipt=true pending_retained=true same_ids=true no_input_replay=true", flush=True)

        def create_extra(profile, provider, model=None, message=None):
            response = self.client.post(base + f"/sessions/{runtime['session']}/terminals",
                params={"agent_profile": profile, "provider": provider, "working_directory": str(runtime["project"]),
                        "defer_init": True, **({"model": model} if model else {})},
                json={"initial_message": message} if message else None, timeout=90)
            response.raise_for_status()
            return str(response.json()["id"])

        bad = create_extra("demo-frontend", "opencode_cli", "opencode-go/cao-nonexistent-" + self.nonce,
                           "Reply FAILURE_PROBE only; no tools, edits, callbacks or retries.")
        wait(lambda: terminal(bad).get("status") == "error", 150, "bad native provider was not reported as error")
        for tid in identities:
            if terminal(tid).get("status") == "error":
                raise RuntimeError("isolated provider failure affected a sibling")
        response = self.client.delete(base + f"/terminals/{bad}", timeout=90)
        response.raise_for_status()
        print("PROVIDER_FAILURE=PASS native_opencode_bad_model=true siblings_preserved=true", flush=True)

        cancelled = create_extra("demo-handoff", "codex")
        response = self.client.delete(base + f"/terminals/{cancelled}", timeout=90)
        response.raise_for_status()
        time.sleep(5)
        if self.client.get(base + f"/terminals/{cancelled}", timeout=15).status_code != 404:
            raise RuntimeError("cancelled initialization resurrected a terminal")
        panes = subprocess.run(["tmux", "-S", str(runtime["socket"]), "list-panes", "-a", "-F", "#{@cao_terminal_id}"],
                               capture_output=True, text=True, timeout=10, check=True)
        if cancelled in panes.stdout.splitlines():
            raise RuntimeError("cancelled initialization retained its pane")
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                environment = (entry / "environ").read_bytes().split(b"\0")
            except (OSError, PermissionError):
                continue
            if b"CAO_TERMINAL_ID=" + cancelled.encode() in environment:
                raise RuntimeError("cancelled initialization retained a process")
        print("INIT_CANCEL=PASS native_codex=true no_resurrection=true no_owned_processes=true", flush=True)

        database = runtime["home"] / ".aws/cli-agent-orchestrator/db/cli-agent-orchestrator.db"
        with sqlite3.connect(database) as connection:
            old_jobs = {row[0] for row in connection.execute("SELECT job_id FROM handoff_results")}
            old_children = {row[0] for row in connection.execute("SELECT id FROM native_children")}
        sequence, previous = dispatch(parent, "ACCEPTANCE_TIMEOUT: Use CAO handoff exactly once with agent_profile=demo-handoff and timeout=1. Ask it to read CONTRACT.md and api/server.py carefully, then return TIMEOUT_RESULT:" + self.nonce
            + " with endpoint details. Report the actual result. On timeout report HANDOFF_TIMEOUT and its returned terminal_id/job_id. Do not retry, assign or cancel.")
        verified_worker(parent, "HANDOFF_TIMEOUT", 240, sequence, previous)
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            jobs = [dict(row) for row in connection.execute("SELECT * FROM handoff_results") if row["job_id"] not in old_jobs]
            children = [dict(row) for row in connection.execute("SELECT * FROM native_children") if row["id"] not in old_children]
        if len(jobs) != 1 or len(children) != 1 or jobs[0]["state"] != "error" or not jobs[0]["terminal_id"] or "within" not in str(jobs[0]["error_message"]):
            raise RuntimeError("handoff timeout lacked one durable recoverable job/worker")
        response = self.client.get(base + f"/handoff-results/{jobs[0]['job_id']}", timeout=15)
        response.raise_for_status()
        if response.json().get("state") != "error":
            raise RuntimeError("handoff timeout reference was not recoverable")
        print("HANDOFF_TIMEOUT=PASS one_job=true one_worker=true durable_reference=true no_retry=true", flush=True)
        (self.root / "recovery-results.json").write_text(json.dumps({
            "controller_renewal": "PASS", "automatic_mcp_reload": "NOT_TESTED", "restart": "PASS",
            "pending_message_id": pending["id"], "provider_failure": "PASS", "initialization_cancel": "PASS",
            "handoff_timeout": "PASS", "terminal_ids": sorted(identities),
        }, indent=2))

    def run(self, base, parent, api, frontend, terminal, verified_worker, runtime=None):
        for token in (None, self.expired, "invalid"):
            headers = {} if token is None else {"Authorization": "Bearer " + token}
            response = requests.get(base + "/sessions", headers=headers, timeout=10)
            if response.status_code != 401:
                raise RuntimeError("missing/expired/invalid bearer was not rejected")
        print("AUTH_NEGATIVE=PASS missing_expired_invalid=401", flush=True)

        def dispatch(tid, message):
            previous = terminal(tid)["turn"]["generation"]
            response = self.client.post(base + f"/terminals/{tid}/input", params={"message": message}, timeout=30)
            if response.status_code != 200 or not response.json().get("success"):
                raise RuntimeError("acceptance task dispatch rejected: HTTP " + str(response.status_code))
            return response.json()["turn_sequence"], previous

        sequence, previous = dispatch(api, "ACCEPTANCE_PEER: Use CAO send_message exactly once to receiver_id=" + frontend
            + " with message CONTRACT_PROBE:" + self.nonce + ". Ask them to read CONTRACT.md and answer you with CONTRACT_REPLY:"
            + self.nonce + ". Finish this turn with PEER_SENT and your current receipt; do not poll or wait.")
        # The reply can advance the API worker before the observer samples the
        # dispatch receipt. Its current final receipt and the two routed messages
        # prove this scenario without requiring a transient intermediate screen.
        deadline = time.monotonic() + 240
        messages = []
        while time.monotonic() < deadline:
            messages = []
            for tid in (api, frontend):
                response = self.client.get(base + f"/terminals/{tid}/inbox/messages", params={"limit": 100}, timeout=15)
                response.raise_for_status()
                payload = response.json()
                messages.extend(payload if isinstance(payload, list) else payload.get("messages", []))
            if peer_messages_verified(messages, api, frontend, self.nonce):
                break
            time.sleep(2)
        else:
            raise TimeoutError("native peer question and answer not delivered")
        verified_worker(api, "PEER_DONE", 180, previous_generation=previous)
        verified_worker(frontend, "FRONT_PEER_DONE", 180)
        print("PEERS=PASS own_sender_ids=true question_answer=true duplicates=0", flush=True)

        for available in (True, False):
            if not available:
                self.stop_external()
            marker = "EXTERNAL_OK:" + self.nonce if available else "EXTERNAL_UNAVAILABLE"
            sequence, previous = dispatch(api, "ACCEPTANCE_EXTERNAL: Call local-probe probe_service exactly once. "
                "Report its actual state. If available, include EXTERNAL_OK: followed by its nonce. "
                "If unavailable, report EXTERNAL_UNAVAILABLE and ConnectionError; do not invent success or retry. "
                "Do not send callbacks. End with the current receipt.")
            verified_worker(api, marker, 180, sequence, previous)
        probes = [json.loads(line) for line in (self.root / "external-probes.jsonl").read_text().splitlines()]
        if len(probes) != 2 or probes[0] != {"state": "available", "nonce": self.nonce} or probes[1].get("state") != "unavailable":
            raise RuntimeError("external integration did not record both real tool calls")
        print("EXTERNAL_MCP=PASS available_then_connection_error=true", flush=True)

        sequence, previous = dispatch(parent, "ACCEPTANCE_HANDOFF: Use CAO handoff once with agent_profile=demo-handoff. "
            "Ask it to read CONTRACT.md and return HANDOFF_PASS:" + self.nonce + " with one endpoint detail. "
            "After handoff returns include its result in your final answer and current receipt. No assign or retries.")
        verified_worker(parent, "HANDOFF_PASS:" + self.nonce, 300, sequence, previous)
        print("HANDOFF=PASS native_supervisor_received_result=true", flush=True)
        if self.recovery:
            self.run_recovery(base, parent, api, frontend, terminal, verified_worker, runtime)
        (self.root / "acceptance-results.json").write_text(json.dumps({
            "auth_negative": "PASS", "peers": "PASS", "external_mcp": "PASS", "handoff": "PASS",
            "api": api, "frontend": frontend, "supervisor": parent,
        }, indent=2))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.validate_collaboration_demo import main
    if "--full" not in sys.argv:
        sys.argv.append("--full")
    main()
