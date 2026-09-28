"""Fail-closed checks for the Work MCP proxy isolation gate."""

import json
import sqlite3
import os
import platform
import subprocess
import sys
import time
import socket
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from cli_agent_orchestrator.services.work_mcp_proxy import (
    WorkMcpProxy,
    WorkMcpProxyRejected,
    WorkMcpProxyUnavailable,
    WorkMcpProxyUncertain,
)
from test.security.test_work_bubblewrap_bound_content import _bound_worker


@pytest.fixture
def short_proxy_root():
    with tempfile.TemporaryDirectory(prefix="wp-", dir="/tmp") as directory:
        yield Path(directory)


_OPT_IN_WORKER = r"""
import ctypes, json, os, socket, sys, time
libc = ctypes.CDLL(None, use_errno=True)
rc = libc.prctl(0x59616d61, ctypes.c_ulong(-1).value, 0, 0, 0)  # PR_SET_PTRACER_ANY
if rc != 0:
    print(json.dumps({"prctl_errno": ctypes.get_errno()}), flush=True)
    raise SystemExit(0)
sock = socket.socket(fileno=int(sys.argv[1]))
print(json.dumps({"pid": os.getpid(), "uid": os.getuid(), "address": sock.getsockname()}), flush=True)
time.sleep(20)
"""


_DUPLICATE_AND_READ = r"""
import ctypes, errno, json, os, socket, sys
pid, fd = map(int, sys.argv[1:])
pidfd = os.pidfd_open(pid)
libc = ctypes.CDLL(None, use_errno=True)
duplicate = libc.syscall(438, pidfd, fd, 0)  # pidfd_getfd on Linux
if duplicate < 0:
    print(json.dumps({"uid": os.getuid(), "errno": ctypes.get_errno()}), flush=True)
else:
    stolen = socket.socket(fileno=duplicate)
    print(json.dumps({"uid": os.getuid(), "data": stolen.recv(128).hex()}), flush=True)
    stolen.close()
os.close(pidfd)
"""


def test_yama_scope_one_can_be_bypassed_by_same_uid_worker_opt_in():
    if platform.system() != "Linux" or os.geteuid() == 0 or not hasattr(os, "pidfd_open"):
        pytest.skip("requires unprivileged Linux pidfd_getfd support")
    try:
        ptrace_scope = int(open("/proc/sys/kernel/yama/ptrace_scope").read().strip())
    except (OSError, ValueError):
        pytest.skip("Yama policy is unavailable")
    if ptrace_scope != 1:
        pytest.skip("the adversarial case is specifically Yama ptrace_scope=1")

    import socket

    server_socket, worker_socket = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    worker_fd = worker_socket.fileno()
    worker = subprocess.Popen(
        [sys.executable, "-c", _OPT_IN_WORKER, str(worker_fd)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        close_fds=True,
        pass_fds=(worker_fd,),
    )
    attacker = None
    try:
        worker_socket.close()
        ready = json.loads(worker.stdout.readline())
        if "prctl_errno" in ready:
            pytest.skip(f"worker could not opt into sibling ptrace: errno {ready['prctl_errno']}")
        assert ready["uid"] == os.getuid()
        assert ready["address"] == ""

        payload = b"mcp-proxy-fd-private-message"
        server_socket.sendall(payload)
        attacker = subprocess.run(
            [sys.executable, "-c", _DUPLICATE_AND_READ, str(ready["pid"]), str(worker_fd)],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
            close_fds=True,
        )
        result = json.loads(attacker.stdout)
        if "errno" in result:
            pytest.skip(f"pidfd_getfd was unavailable after worker opt-in: errno {result['errno']}")
        assert result["uid"] == ready["uid"] == os.getuid()
        assert bytes.fromhex(result["data"]) == payload
    finally:
        server_socket.close()
        worker_socket.close()
        if worker.poll() is None:
            worker.kill()
        worker.communicate(timeout=5)


def test_unproven_user_namespace_candidate_is_rejected_before_secret_or_claim():
    calls = []
    secret = b"server-secret-that-must-stay-private"
    proxy = WorkMcpProxy()

    def claim_issue_once(_attempt_id, _generation):
        calls.append("claim")

    def server_secret_factory():
        calls.append("secret")
        return secret

    with pytest.raises(WorkMcpProxyUnavailable) as rejected:
        proxy.create_attempt(
            attempt_id="attempt-a",
            generation=3,
            expires_at=time.time() + 60,
            claim_issue_once=claim_issue_once,
            server_secret_factory=server_secret_factory,
        )

    assert calls == []
    message = str(rejected.value)
    assert "unbound proxy creation is disabled" in message
    assert "durable attempt issue" in message
    assert secret.decode() not in str(rejected.value)
    proxy.close()


def test_restart_or_replacement_cannot_reissue_any_attempt_without_worker_guard():
    calls = []

    def claim_issue_once(*args):
        calls.append(("claim", args))

    def server_secret_factory():
        calls.append(("secret",))
        return b"replacement-secret-must-not-be-issued"

    for attempt_id, generation in (("attempt-a", 3), ("attempt-b", 8)):
        with pytest.raises(WorkMcpProxyUnavailable):
            WorkMcpProxy().create_attempt(
                attempt_id=attempt_id,
                generation=generation,
                expires_at=time.time() + 60,
                claim_issue_once=claim_issue_once,
                server_secret_factory=server_secret_factory,
            )

    replacement = WorkMcpProxy()
    with pytest.raises(WorkMcpProxyUnavailable):
        replacement.create_attempt(
            attempt_id="attempt-a",
            generation=3,
            expires_at=time.time() + 60,
            claim_issue_once=claim_issue_once,
            server_secret_factory=server_secret_factory,
        )
    replacement.close()
    assert calls == []


def test_invalid_attempt_binding_is_rejected_before_environment_or_issue_claim():
    calls = []
    proxy = WorkMcpProxy()

    with pytest.raises(WorkMcpProxyRejected):
        proxy.create_attempt(
            attempt_id="",
            generation=0,
            expires_at=0,
            claim_issue_once=lambda *_args: calls.append("claim"),
            server_secret_factory=lambda: calls.append("secret"),
        )

    assert calls == []
    proxy.close()


def test_serve_revoke_and_close_reject_without_an_issued_endpoint():
    proxy = WorkMcpProxy()
    with pytest.raises(WorkMcpProxyRejected):
        proxy.serve("attempt-a", 3)
    proxy.revoke("attempt-a", 3)
    proxy.close()
    proxy.close()


def test_revoke_closes_reserved_worker_endpoint_and_is_idempotent(tmp_path, short_proxy_root):
    repository, binding, _, _ = _bound_worker(tmp_path)
    proxy = WorkMcpProxy(
        repository,
        endpoint_root=short_proxy_root,
        server_secret_factory=lambda: b"private",
        upstream=lambda request, secret: {},
    )
    endpoint = proxy.create_bound_attempt(
        attempt_id=binding.attempt_id,
        generation=1,
        expected_attempt_revision=1,
        contract_hash=binding.contract_hash,
        expires_at=time.time() + 30,
    )
    class LiveProof:
        def require_current(self, *_args):
            return None

    proxy._isolation_proof = LiveProof()
    proxy._secret = b"private"
    worker = socket.socket(fileno=os.dup(endpoint.worker_fd))
    server = threading.Thread(
        target=lambda: proxy.serve(binding.attempt_id, 1), daemon=True
    )
    server.start()
    try:
        proxy.revoke(binding.attempt_id, 1)
        assert endpoint.worker_fd == -1
        assert worker.recv(1) == b""
        server.join(timeout=2)
        assert not server.is_alive()
        proxy.revoke(binding.attempt_id, 1)
    finally:
        worker.close()


def test_revoke_waits_for_inflight_effect_and_prevents_later_effects(tmp_path, short_proxy_root):
    repository, binding, _, _ = _bound_worker(tmp_path)
    entered_upstream = threading.Event()
    finish_upstream = threading.Event()
    revoke_finished = threading.Event()
    calls = []

    class LiveProof:
        def require_current(self, *_args):
            return None

    def upstream(request, _secret):
        calls.append(request)
        entered_upstream.set()
        assert finish_upstream.wait(timeout=5)
        return {"jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}}

    proxy = WorkMcpProxy(
        repository,
        endpoint_root=short_proxy_root,
        server_secret_factory=lambda: b"private",
        upstream=upstream,
    )
    endpoint = proxy.create_bound_attempt(
        attempt_id=binding.attempt_id, generation=1, expected_attempt_revision=1,
        contract_hash=binding.contract_hash, expires_at=time.time() + 30,
    )
    proxy._isolation_proof = LiveProof()
    proxy._secret = b"private"
    client = socket.socket(fileno=os.dup(endpoint.worker_fd))
    reader = client.makefile("rb")
    server = threading.Thread(target=lambda: proxy.serve(binding.attempt_id, 1), daemon=True)
    server.start()
    try:
        client.sendall(
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call",'
            b'"params":{"name":"Read","arguments":{}}}\n'
        )
        assert entered_upstream.wait(timeout=5)
        revoker = threading.Thread(
            target=lambda: (proxy.revoke(binding.attempt_id, 1), revoke_finished.set()),
            daemon=True,
        )
        revoker.start()
        assert not revoke_finished.wait(timeout=0.05)
        finish_upstream.set()
        assert revoke_finished.wait(timeout=5)
        assert json.loads(reader.readline())["result"] == {"ok": True}
        server.join(timeout=2)
        revoker.join(timeout=2)
        assert not server.is_alive()
        assert not revoker.is_alive()
    finally:
        finish_upstream.set()
        reader.close()
        client.close()
        proxy.close()
    assert len(calls) == 1
    with repository.read_snapshot() as connection:
        assert [row[0] for row in connection.execute(
            "SELECT state FROM work_mcp_proxy_effect_events ORDER BY sequence"
        )] == ["intent", "completed"]


def test_bound_proxy_issue_reserves_endpoint_without_secret_and_cannot_be_reissued(tmp_path, short_proxy_root):
    repository, binding, _, _ = _bound_worker(tmp_path)
    root = short_proxy_root
    calls = []
    proxy = WorkMcpProxy(
        repository,
        endpoint_root=root,
        server_secret_factory=lambda: calls.append("secret") or b"upstream-secret",
        upstream=lambda request, secret: {"jsonrpc": "2.0", "id": request["id"], "result": {}},
    )
    endpoint = proxy.create_bound_attempt(
        attempt_id=binding.attempt_id,
        generation=1,
        expected_attempt_revision=1,
        contract_hash=binding.contract_hash,
        expires_at=time.time() + 30,
    )
    assert endpoint.worker_fd >= 0
    assert not hasattr(endpoint, "host_path")
    assert list(root.iterdir()) == []
    worker_socket = socket.socket(fileno=os.dup(endpoint.worker_fd))
    try:
        worker_socket.sendall(b"PING")
        assert proxy._broker_socket.recv(4) == b"PING"
    finally:
        worker_socket.close()
    assert calls == []
    with repository.read_snapshot() as connection:
        assert connection.execute(
            "SELECT generation,attempt_revision,contract_hash FROM work_mcp_proxy_issues "
            "WHERE attempt_id=?", (binding.attempt_id,)
        ).fetchone()[:] == (1, 1, binding.contract_hash)
    proxy.close()
    assert endpoint.worker_fd == -1
    assert list(root.iterdir()) == []
    replacement = WorkMcpProxy(
        repository, endpoint_root=root,
        server_secret_factory=lambda: calls.append("replacement-secret") or b"other",
        upstream=lambda request, secret: {},
    )
    with pytest.raises(WorkMcpProxyRejected):
        replacement.create_bound_attempt(
            attempt_id=binding.attempt_id, generation=1, expected_attempt_revision=1,
            contract_hash=binding.contract_hash, expires_at=time.time() + 30,
        )
    assert calls == []


def test_bound_proxy_without_activated_isolation_proof_never_calls_upstream(tmp_path, short_proxy_root):
    repository, binding, _, _ = _bound_worker(tmp_path)
    root = short_proxy_root
    calls = []
    proxy = WorkMcpProxy(
        repository, endpoint_root=root,
        server_secret_factory=lambda: b"private",
        upstream=lambda request, secret: calls.append((request, secret)) or {
            "jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}
        },
    )
    endpoint = proxy.create_bound_attempt(
        attempt_id=binding.attempt_id, generation=1, expected_attempt_revision=1,
        contract_hash=binding.contract_hash, expires_at=time.time() + 30,
    )
    client = socket.socket(fileno=os.dup(endpoint.worker_fd))
    try:
        client.sendall(b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"Read","arguments":{}}}\n')
        with pytest.raises(WorkMcpProxyRejected):
            proxy.serve_once(endpoint)
    finally:
        client.close()
    assert calls == []
    proxy.close()


def test_proxy_commits_effect_intent_before_upstream_without_holding_sqlite_writer(
    tmp_path, short_proxy_root
):
    repository, binding, _, _ = _bound_worker(tmp_path)
    transaction_depth = 0
    original_transaction = repository.transaction

    @contextmanager
    def tracked_transaction():
        nonlocal transaction_depth
        transaction_depth += 1
        try:
            with original_transaction() as connection:
                yield connection
        finally:
            transaction_depth -= 1

    repository.transaction = tracked_transaction
    calls = []

    class LiveProof:
        def require_current(self, *_args):
            return None

    def upstream(request, secret):
        assert transaction_depth == 0
        assert secret == b"private"
        with repository.read_snapshot() as connection:
            assert connection.execute(
                "SELECT state FROM work_mcp_proxy_effect_events "
                "WHERE effect_id=(SELECT effect_id FROM work_mcp_proxy_effects "
                "WHERE attempt_id=? ORDER BY created_at DESC LIMIT 1) ORDER BY sequence DESC LIMIT 1",
                (binding.attempt_id,),
            ).fetchone()[0] == "intent"
        calls.append(request)
        return {"jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}}

    proxy = WorkMcpProxy(
        repository,
        endpoint_root=short_proxy_root,
        server_secret_factory=lambda: b"private",
        upstream=upstream,
    )
    endpoint = proxy.create_bound_attempt(
        attempt_id=binding.attempt_id, generation=1, expected_attempt_revision=1,
        contract_hash=binding.contract_hash, expires_at=time.time() + 30,
    )
    proxy._isolation_proof = LiveProof()
    proxy._secret = b"private"
    client = socket.socket(fileno=os.dup(endpoint.worker_fd))
    try:
        client.sendall(
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call",'
            b'"params":{"name":"Read","arguments":{}}}\n'
        )
        proxy.serve_once(endpoint)
        response = json.loads(client.recv(4096))
    finally:
        client.close()
    assert response["result"] == {"ok": True}
    assert len(calls) == 1
    with repository.read_snapshot() as connection:
        events = connection.execute(
            "SELECT state FROM work_mcp_proxy_effect_events ORDER BY sequence"
        ).fetchall()
        assert [row[0] for row in events] == ["intent", "completed"]
        effect = connection.execute(
            "SELECT effect_id,request_sha256 FROM work_mcp_proxy_effects WHERE attempt_id=?",
            (binding.attempt_id,),
        ).fetchone()
    assert len(effect["request_sha256"]) == 64
    with repository.transaction() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO work_mcp_proxy_effects "
                "(effect_id,attempt_id,generation,request_sha256,created_at) VALUES (?,?,?,?,?)",
                ("b" * 32, binding.attempt_id, 1, effect["request_sha256"], time.time()),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE work_mcp_proxy_effect_events SET state='uncertain' WHERE effect_id=?",
                (effect["effect_id"],),
            )
    with pytest.raises(WorkMcpProxyRejected):
        proxy.serve_once(endpoint)
    assert len(calls) == 1
    assert transaction_depth == 0


def test_proxy_marks_ambiguous_upstream_failure_uncertain_and_recovery_does_not_replay(
    tmp_path, short_proxy_root
):
    repository, binding, _, _ = _bound_worker(tmp_path)
    calls = []

    class LiveProof:
        def require_current(self, *_args):
            return None

    def upstream(_request, _secret):
        calls.append("effect may have happened")
        raise OSError("connection lost after upstream accepted request")

    proxy = WorkMcpProxy(
        repository,
        endpoint_root=short_proxy_root,
        server_secret_factory=lambda: b"private",
        upstream=upstream,
    )
    endpoint = proxy.create_bound_attempt(
        attempt_id=binding.attempt_id, generation=1, expected_attempt_revision=1,
        contract_hash=binding.contract_hash, expires_at=time.time() + 30,
    )
    proxy._isolation_proof = LiveProof()
    proxy._secret = b"private"
    client = socket.socket(fileno=os.dup(endpoint.worker_fd))
    try:
        client.sendall(
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call",'
            b'"params":{"name":"Read","arguments":{}}}\n'
        )
        with pytest.raises(WorkMcpProxyUncertain):
            proxy.serve_once(endpoint)
    finally:
        client.close()
    with repository.read_snapshot() as connection:
        assert [row[0] for row in connection.execute(
            "SELECT state FROM work_mcp_proxy_effect_events ORDER BY sequence"
        )] == ["intent", "uncertain"]
    assert calls == ["effect may have happened"]


def test_proxy_serves_sequential_calls_and_does_not_replay_identical_request(
    tmp_path, short_proxy_root
):
    repository, binding, _, _ = _bound_worker(tmp_path)
    calls = []

    class LiveProof:
        def require_current(self, *_args):
            return None

    proxy = WorkMcpProxy(
        repository,
        endpoint_root=short_proxy_root,
        server_secret_factory=lambda: b"private",
        upstream=lambda request, _secret: calls.append(request) or {
            "jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}
        },
    )
    endpoint = proxy.create_bound_attempt(
        attempt_id=binding.attempt_id, generation=1, expected_attempt_revision=1,
        contract_hash=binding.contract_hash, expires_at=time.time() + 30,
    )
    proxy._isolation_proof = LiveProof()
    proxy._secret = b"private"
    client = socket.socket(fileno=os.dup(endpoint.worker_fd))
    request = (
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call",'
        b'"params":{"name":"Read","arguments":{}}}\n'
    )
    server = threading.Thread(
        target=lambda: proxy.serve(binding.attempt_id, 1), daemon=True
    )
    reader = client.makefile("rb")
    server.start()
    try:
        client.sendall(request)
        assert json.loads(reader.readline())["result"] == {"ok": True}
        client.sendall(request)
        assert client.recv(4096) == b""
    finally:
        reader.close()
        client.close()
    server.join(timeout=2)
    assert not server.is_alive()
    assert len(calls) == 1
    with repository.read_snapshot() as connection:
        assert connection.execute(
            "SELECT count(*) FROM work_mcp_proxy_effects WHERE attempt_id=?",
            (binding.attempt_id,),
        ).fetchone()[0] == 1


def test_proxy_recovery_turns_open_intent_into_uncertain(tmp_path):
    repository, binding, _, _ = _bound_worker(tmp_path)
    proxy = WorkMcpProxy(repository)
    with repository.transaction() as connection:
        connection.execute(
            "INSERT INTO work_mcp_proxy_issues "
            "(attempt_id,generation,attempt_revision,contract_hash,expires_at,issued_at) "
            "VALUES (?,1,1,?,?,?)",
            (binding.attempt_id, binding.contract_hash, time.time() + 30, time.time()),
        )
        connection.execute(
            "INSERT INTO work_mcp_proxy_effects "
            "(effect_id,attempt_id,generation,request_sha256,created_at) "
            "VALUES ('e' || '1' || '000000000000000000000000000000',?,1,?,?)",
            (binding.attempt_id, "f" * 64, time.time()),
        )
        connection.execute(
            "INSERT INTO work_mcp_proxy_effect_events "
            "(effect_id,sequence,state,occurred_at) "
            "VALUES ('e1000000000000000000000000000000',1,'intent',?)",
            (time.time(),),
        )
    assert proxy.recover_incomplete_effects() == 1
    assert proxy.recover_incomplete_effects() == 0
    with repository.read_snapshot() as connection:
        assert [row[0] for row in connection.execute(
            "SELECT state FROM work_mcp_proxy_effect_events "
            "WHERE effect_id='e1000000000000000000000000000000' ORDER BY sequence"
        )] == ["intent", "uncertain"]
    proxy.reconcile_effect("e1000000000000000000000000000000", "operator confirmed no replay")
    with repository.read_snapshot() as connection:
        assert connection.execute(
            "SELECT state,resolution_sha256 FROM work_mcp_proxy_effect_events "
            "WHERE effect_id='e1000000000000000000000000000000' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()[0] == "reconciled"
