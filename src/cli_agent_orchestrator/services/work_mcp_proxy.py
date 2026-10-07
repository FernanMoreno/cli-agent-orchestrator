"""Attempt-bound Work MCP proxy with a host-acceptance gate still closed.

The proxy binds requests to a durable attempt/generation/revision/contract
issue and an ACK-bound runtime proof. It passes one unnamed AF_UNIX socketpair
endpoint to the worker; the server-side secret is loaded only after durable
setup evidence and proof validation. Effects use a durable intent/event journal
and are never automatically retried after an uncertain outcome. This runtime
slice remains unregistered until native-host and adversarial acceptance gates
pass.

Yama and a worker-local filter for PR_SET_PTRACER do not establish a sibling
boundary. Bubblewrap user namespaces grant their creator capabilities inside
the child namespace, so a same-UID host process could use ptrace and
pidfd_getfd against a worker outside that namespace. The Work broker must run
under a dedicated, non-root, non-login OS account with no unrelated processes;
the account that creates the namespaces is part of the trusted computing base.
Changing per-attempt UID mappings alone does not remove the creator's
capabilities. The legacy unbound creation API rejects before touching its
callbacks. The bound path reserves a one-shot issue before launch and activates
only after the exact worker FD and durable setup ACK are proven.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_contract import EffectiveWorkContractV2
from cli_agent_orchestrator.models.work_origin import MAX_WORKFLOW_STEP_RESULT_BYTES
from cli_agent_orchestrator.services.work_bubblewrap_isolation_proof import (
    WorkBubblewrapRuntimeIsolationProof,
)
from cli_agent_orchestrator.services.work_contract import WorkContracts
from cli_agent_orchestrator.services.work_docker_isolation_proof import (
    WorkDockerRuntimeIsolationProof,
)

_MAX_REQUEST_BYTES = 16384
_MAX_SUBMIT_RESULT_REQUEST_BYTES = 3 * MAX_WORKFLOW_STEP_RESULT_BYTES + 4096
_MAX_RESPONSE_BYTES = 65536
_SOCKET_TIMEOUT_SECONDS = 5.0


class WorkMcpProxyError(RuntimeError):
    """Base proxy gate error; messages contain no credentials or origin details."""


class WorkMcpProxyUnavailable(WorkMcpProxyError):
    """The backend cannot prove and implement proxy isolation for this attempt."""


class WorkMcpProxyRejected(WorkMcpProxyError):
    """Attempt binding or server-owned issuance prerequisites are invalid."""


class WorkMcpProxyUncertain(WorkMcpProxyError):
    """A future implementation must report and reconcile partial lifecycle outcomes."""


@dataclass(frozen=True)
class AttemptBinding:
    """The non-secret identity and lease fields required by a proxy protocol."""

    attempt_id: str
    generation: int
    expires_at: float


@dataclass(frozen=True, slots=True)
class WorkMcpEndpoint:
    """Broker-owned handoff for one unnamed worker socketpair endpoint."""

    worker_socket_identity: tuple[int, int]
    _owner: WorkMcpProxy

    @property
    def worker_fd(self) -> int:
        worker = self._owner._worker_socket
        return -1 if worker is None else worker.fileno()

    def stop(self) -> None:
        self._owner.close()


class WorkMcpProxy:
    """Own one attempt-bound proxy lifecycle; host acceptance remains separate.

    A future implementation must receive its upstream secret through the
    server-side ``server_secret_factory`` only after isolation preflight, and
    must require a durable ``claim_issue_once`` callback before creating an FD.
    The worker may receive only its anonymous socketpair FD through
    ``pass_fds``; the secret, origin socket, and secret-bearing callback remain
    server-side. Every request needs a fresh durable attempt/generation/grant/
    revocation/expiry guard immediately before the upstream effect. Partial
    forwarding or cleanup is uncertain, leaves reconciliation required, and
    blocks reissue/redelivery.

    The namespace creator receives capabilities inside its child namespace;
    per-attempt UID mappings alone do not isolate it from same-UID host
    processes. Require the Work broker to run under a dedicated, non-root,
    non-login OS account with no unrelated processes, and treat that account as
    trusted. The legacy unbound API remains disabled; the bound API exposes
    credentials only after an ACK-bound proof activates its reserved worker FD.
    """

    def __init__(
        self,
        repository: WorkRepository | None = None,
        *,
        endpoint_root: Path | None = None,
        server_secret_factory: Callable[[], bytes] | None = None,
        upstream: Callable[[dict, bytes], dict] | None = None,
    ) -> None:
        self._repository = repository
        self._endpoint_root = endpoint_root
        self._secret_factory = server_secret_factory
        self._upstream = upstream
        self._endpoint: WorkMcpEndpoint | None = None
        self._broker_socket: socket.socket | None = None
        self._worker_socket: socket.socket | None = None
        self._secret: bytes | None = None
        self._issue: tuple[str, int, int, str, float] | None = None
        self._worker_socket_identity: tuple[int, int] | None = None
        self._isolation_proof: (
            WorkBubblewrapRuntimeIsolationProof | WorkDockerRuntimeIsolationProof | None
        ) = None
        self._consumed = False
        self._request_timeout: float | None = _SOCKET_TIMEOUT_SECONDS
        self._lifecycle_lock = threading.RLock()
        self._worker_io_lock = threading.Lock()

    @staticmethod
    def _validate_binding(attempt_id: str, generation: int, expires_at: float) -> AttemptBinding:
        try:
            valid_expiry = math.isfinite(expires_at)
        except (OverflowError, TypeError):
            valid_expiry = False
        if (
            type(attempt_id) is not str
            or not attempt_id
            or len(attempt_id) > 256
            or "\x00" in attempt_id
            or type(generation) is not int
            or not 0 < generation <= 2**63 - 1
            or type(expires_at) not in {int, float}
            or not valid_expiry
            or expires_at <= time.time()
        ):
            raise WorkMcpProxyRejected("invalid attempt proxy binding")
        return AttemptBinding(attempt_id, generation, float(expires_at))

    def create_attempt(
        self,
        *,
        attempt_id: str,
        generation: int,
        expires_at: float,
        claim_issue_once: Callable[[str, int], None],
        server_secret_factory: Callable[[], bytes],
    ) -> None:
        """Reject before secret lookup, durable issue claim, or socket creation."""

        self._validate_binding(attempt_id, generation, expires_at)
        if not callable(claim_issue_once) or not callable(server_secret_factory):
            raise WorkMcpProxyRejected("server-side issue claim and secret source are required")
        raise self._unsupported()

    def create_bound_attempt(
        self,
        *,
        attempt_id: str,
        generation: int,
        expected_attempt_revision: int,
        contract_hash: str,
        expires_at: float,
        runtime_isolation_proof: object | None = None,
    ) -> WorkMcpEndpoint:
        """Consume one durable issue, then create an unnamed socketpair."""
        binding = self._validate_binding(attempt_id, generation, expires_at)
        if runtime_isolation_proof is not None:
            raise WorkMcpProxyRejected("isolation proof is bound after the worker setup ACK")
        if (
            not isinstance(self._repository, WorkRepository)
            or not callable(self._secret_factory)
            or not callable(self._upstream)
            or self._issue is not None
            or type(expected_attempt_revision) is not int
            or expected_attempt_revision <= 0
            or type(contract_hash) is not str
            or len(contract_hash) != 64
            or any(character not in "0123456789abcdef" for character in contract_hash)
        ):
            raise WorkMcpProxyRejected("invalid server-owned proxy issue context")
        now = time.time()
        try:
            with self._repository.transaction() as connection:
                self._repository._verify(connection)
                current = WorkContracts(self._repository)._revalidate_order(
                    connection, attempt_id, generation=generation
                )
                attempt = connection.execute(
                    "SELECT state,revision,lease_expires_at FROM work_attempts "
                    "WHERE id=? AND generation=?",
                    (attempt_id, generation),
                ).fetchone()
                if (
                    current.contract_hash != contract_hash
                    or not isinstance(current.contract, EffectiveWorkContractV2)
                    or attempt is None
                    or (attempt["state"], attempt["revision"])
                    != ("sent", expected_attempt_revision)
                    or not now < binding.expires_at <= attempt["lease_expires_at"]
                ):
                    raise WorkMcpProxyRejected("proxy issue differs from current sent attempt")
                connection.execute(
                    "INSERT INTO work_mcp_proxy_issues "
                    "(attempt_id,generation,attempt_revision,contract_hash,expires_at,issued_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        attempt_id,
                        generation,
                        expected_attempt_revision,
                        contract_hash,
                        binding.expires_at,
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO work_mcp_proxy_issue_events "
                    "(attempt_id,generation,sequence,state,occurred_at) "
                    "VALUES (?,?,1,'issued',?)",
                    (attempt_id, generation, now),
                )
        except sqlite3.IntegrityError as exc:
            raise WorkMcpProxyRejected("proxy endpoint has already been issued") from exc
        self._issue = (
            attempt_id,
            generation,
            expected_attempt_revision,
            contract_hash,
            binding.expires_at,
        )
        try:
            socket_type = socket.SOCK_STREAM | getattr(socket, "SOCK_CLOEXEC", 0)
            broker_socket, worker_socket = socket.socketpair(socket.AF_UNIX, socket_type)
            broker_socket.settimeout(_SOCKET_TIMEOUT_SECONDS)
            self._broker_socket = broker_socket
            self._worker_socket = worker_socket
            self._worker_socket_identity = self._file_identity(worker_socket.fileno())
            self._endpoint = WorkMcpEndpoint(self._worker_socket_identity, self)
            return self._endpoint
        except BaseException as exc:
            self.close()
            raise WorkMcpProxyUnavailable(
                "proxy issue was consumed but endpoint setup failed; reconcile before retry"
            ) from exc

    def activate_with_isolation_proof(
        self,
        endpoint: WorkMcpEndpoint,
        proof: WorkBubblewrapRuntimeIsolationProof | WorkDockerRuntimeIsolationProof,
    ) -> None:
        """Expose server-side credentials only after the worker setup ACK is durable."""
        with self._lifecycle_lock:
            self._activate_with_isolation_proof(endpoint, proof)

    def _activate_with_isolation_proof(
        self,
        endpoint: WorkMcpEndpoint,
        proof: WorkBubblewrapRuntimeIsolationProof | WorkDockerRuntimeIsolationProof,
    ) -> None:
        issue = self._issue
        if (
            endpoint is not self._endpoint
            or issue is None
            or self._broker_socket is None
            or self._worker_socket is None
            or self._secret is not None
            or not isinstance(
                proof, (WorkBubblewrapRuntimeIsolationProof, WorkDockerRuntimeIsolationProof)
            )
            or (proof.attempt_id, proof.generation, proof.attempt_revision, proof.contract_hash)
            != (issue[0], issue[1], issue[2], issue[3])
            or proof.worker_socket_identity != endpoint.worker_socket_identity
        ):
            raise WorkMcpProxyRejected("runtime isolation proof does not bind this endpoint")
        try:
            proof.require_current(issue[0], issue[1], issue[3])
        except RuntimeError as exc:
            raise WorkMcpProxyRejected(
                "launch isolation expired before endpoint activation"
            ) from exc
        assert self._secret_factory is not None
        secret = self._secret_factory()
        if type(secret) is not bytes or not secret or len(secret) > 65536:
            self.close()
            raise WorkMcpProxyUnavailable("server-side proxy credential is invalid")
        self._secret = secret
        self._isolation_proof = proof

    def forward_from_docker(self, endpoint: WorkMcpEndpoint, request: bytes) -> bytes:
        """Bridge one bounded Docker attach request through the private proxy socket."""
        if (
            endpoint is not self._endpoint
            or self._issue is None
            or self._secret is None
            or not isinstance(self._isolation_proof, WorkDockerRuntimeIsolationProof)
            or self._worker_socket is None
            or self._broker_socket is None
        ):
            raise WorkMcpProxyRejected("Docker proxy endpoint is not live for this owner")
        self._request(request)
        try:
            self._isolation_proof.require_current(*self._issue[:2], self._issue[3])
        except RuntimeError as error:
            raise WorkMcpProxyRejected("Docker isolation expired before proxy request") from error
        with self._worker_io_lock:
            worker_socket = self._worker_socket
            if worker_socket is None:
                raise WorkMcpProxyRejected("Docker proxy endpoint was revoked")
            previous_timeout = worker_socket.gettimeout()
            try:
                worker_socket.settimeout(_SOCKET_TIMEOUT_SECONDS)
                worker_socket.sendall(request)
                response = bytearray()
                while len(response) <= _MAX_RESPONSE_BYTES:
                    chunk = worker_socket.recv(min(4096, _MAX_RESPONSE_BYTES + 1 - len(response)))
                    if not chunk:
                        break
                    response.extend(chunk)
                    if b"\n" in chunk:
                        break
            except (OSError, TimeoutError) as error:
                raise WorkMcpProxyRejected("Docker proxy response is unavailable") from error
            finally:
                try:
                    worker_socket.settimeout(previous_timeout)
                except OSError:
                    pass
        if (
            not response.endswith(b"\n")
            or response.count(b"\n") != 1
            or len(response) > _MAX_RESPONSE_BYTES
        ):
            raise WorkMcpProxyRejected("Docker proxy response is outside its bound")
        try:
            value = json.loads(response[:-1].decode("utf-8"))
        except (UnicodeError, ValueError, TypeError) as error:
            raise WorkMcpProxyRejected("Docker proxy response is invalid") from error
        request_value = self._request(request)
        if (
            type(value) is not dict
            or value.get("jsonrpc") != "2.0"
            or type(value.get("id")) is not type(request_value["id"])
            or value.get("id") != request_value["id"]
            or set(value) not in ({"jsonrpc", "id", "result"}, {"jsonrpc", "id", "error"})
        ):
            raise WorkMcpProxyRejected("Docker proxy response does not match its request")
        return bytes(response)

    @staticmethod
    def _file_identity(path: Path | int) -> tuple[int, int]:
        item = os.fstat(path) if type(path) is int else os.stat(path, follow_symlinks=False)
        return item.st_dev, item.st_ino

    @staticmethod
    def _request(raw: bytes) -> dict:
        if not raw.endswith(b"\n") or len(raw) > _MAX_SUBMIT_RESULT_REQUEST_BYTES:
            raise WorkMcpProxyRejected("invalid bounded proxy request")

        def unique_pairs(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate JSON key")
                value[key] = item
            return value

        try:
            value = json.loads(
                raw[:-1].decode("utf-8"),
                object_pairs_hook=unique_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise WorkMcpProxyRejected("invalid JSON-RPC request") from exc
        if (
            type(value) is not dict
            or set(value) != {"jsonrpc", "id", "method", "params"}
            or value["jsonrpc"] != "2.0"
            or value["method"] != "tools/call"
            or type(value["id"]) not in {int, str}
            or (type(value["id"]) is str and (not value["id"] or len(value["id"]) > 128))
            or type(value["params"]) is not dict
            or set(value["params"]) != {"name", "arguments"}
            or type(value["params"]["name"]) is not str
            or not value["params"]["name"]
            or len(value["params"]["name"]) > 128
            or type(value["params"]["arguments"]) is not dict
        ):
            raise WorkMcpProxyRejected("unsupported or invalid JSON-RPC request")
        maximum = (
            _MAX_SUBMIT_RESULT_REQUEST_BYTES
            if value["params"]["name"] == "cao.work.submit_result"
            else _MAX_REQUEST_BYTES
        )
        if len(raw) > maximum:
            raise WorkMcpProxyRejected("proxy request exceeds its tool-specific byte bound")
        return value

    @staticmethod
    def _read_request(client: socket.socket) -> dict:
        chunks = bytearray()
        while len(chunks) <= _MAX_SUBMIT_RESULT_REQUEST_BYTES:
            chunk = client.recv(_MAX_SUBMIT_RESULT_REQUEST_BYTES + 1 - len(chunks))
            if not chunk:
                break
            chunks.extend(chunk)
            if b"\n" in chunk:
                break
        if chunks.count(b"\n") != 1 or not chunks.endswith(b"\n"):
            raise WorkMcpProxyRejected("proxy accepts one bounded request")
        return WorkMcpProxy._request(bytes(chunks))

    def serve_once(self, endpoint: WorkMcpEndpoint) -> None:
        """Journal one request, then call upstream without holding SQLite open."""
        if (
            endpoint is not self._endpoint
            or self._broker_socket is None
            or self._repository is None
            or self._issue is None
            or self._secret is None
            or self._consumed
        ):
            raise WorkMcpProxyRejected("proxy endpoint is not live for this owner")
        self._consumed = True
        try:
            self._serve_request(endpoint)
        finally:
            self.close()

    def serve(self, attempt_id: str, generation: int) -> None:
        """Serve sequential worker requests until its socket closes or authority fails."""
        self._validate_binding(attempt_id, generation, time.time() + 1)
        endpoint = self._endpoint
        if (
            endpoint is None
            or self._issue is None
            or (self._issue[0], self._issue[1]) != (attempt_id, generation)
            or self._consumed
            or self._secret is None
            or self._broker_socket is None
        ):
            raise WorkMcpProxyRejected("proxy endpoint is not live for this attempt")
        self._consumed = True
        self._request_timeout = None
        try:
            while True:
                try:
                    self._serve_request(endpoint)
                except WorkMcpProxyRejected:
                    break
        finally:
            self.close()

    def _serve_request(self, endpoint: WorkMcpEndpoint) -> None:
        attempted = False
        effect_id: str | None = None
        effect_finalized = False
        try:
            assert self._broker_socket is not None
            assert self._issue is not None
            assert self._repository is not None
            client = socket.socket(fileno=os.dup(self._broker_socket.fileno()))
            with client:
                client.settimeout(self._request_timeout)
                request = self._read_request(client)
                try:
                    if self._isolation_proof is None:
                        raise WorkMcpProxyRejected("proxy was revoked before proxy effect")
                    self._isolation_proof.require_current(*self._issue[:2], self._issue[3])
                except (AttributeError, RuntimeError) as exc:
                    raise WorkMcpProxyRejected(
                        "launch isolation expired before proxy effect"
                    ) from exc
                attempt_id, generation, revision, contract_hash, expires_at = self._issue
                request_json = json.dumps(
                    request, sort_keys=True, separators=(",", ":"), allow_nan=False
                )
                request_sha256 = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
                with self._repository.transaction() as connection:
                    self._repository._verify(connection)
                    current = WorkContracts(self._repository)._revalidate_order(
                        connection, attempt_id, generation=generation
                    )
                    attempt = connection.execute(
                        "SELECT state,revision,lease_expires_at FROM work_attempts "
                        "WHERE id=? AND generation=?",
                        (attempt_id, generation),
                    ).fetchone()
                    receiver_tool = request["params"]["name"] in {
                        "cao.work.task_received",
                        "cao.work.submit_result",
                    }
                    allowed_states = {"sent", "acknowledged", "running"}
                    if (
                        receiver_tool
                        and isinstance(current.contract, EffectiveWorkContractV2)
                        and current.contract.operation_kind == "agent_step"
                    ):
                        allowed_states.add("finished")
                    issue = connection.execute(
                        "SELECT generation,attempt_revision,contract_hash,expires_at "
                        "FROM work_mcp_proxy_issues WHERE attempt_id=?",
                        (attempt_id,),
                    ).fetchone()
                    if (
                        current.contract_hash != contract_hash
                        or attempt is None
                        or attempt["state"] not in allowed_states
                        or attempt["revision"] < revision
                        or issue is None
                        or tuple(issue) != (generation, revision, contract_hash, expires_at)
                        or min(attempt["lease_expires_at"], expires_at) <= time.time()
                        or request["params"]["name"] not in current.contract.permissions.tools
                    ):
                        raise WorkMcpProxyRejected("proxy request is no longer authorized")
                    effect_id = uuid4().hex
                    connection.execute(
                        "INSERT INTO work_mcp_proxy_effects "
                        "(effect_id,attempt_id,generation,request_sha256,created_at) "
                        "VALUES (?,?,?,?,?)",
                        (effect_id, attempt_id, generation, request_sha256, time.time()),
                    )
                    connection.execute(
                        "INSERT INTO work_mcp_proxy_effect_events "
                        "(effect_id,sequence,state,occurred_at) VALUES (?,1,'intent',?)",
                        (effect_id, time.time()),
                    )

                # Serialize the final guard, effect, durable result, and reply
                # with revoke. A returned revoke cannot race an active request.
                with self._lifecycle_lock:
                    try:
                        if self._isolation_proof is None or self._issue is None:
                            raise WorkMcpProxyRejected("proxy was revoked before proxy effect")
                        self._isolation_proof.require_current(*self._issue[:2], self._issue[3])
                    except (AttributeError, RuntimeError) as exc:
                        raise WorkMcpProxyRejected(
                            "launch isolation expired before proxy effect"
                        ) from exc
                    if self._secret is None or self._upstream is None:
                        raise WorkMcpProxyRejected("proxy was revoked before proxy effect")
                    attempted = True
                    response = self._upstream(request, self._secret)
                    if (
                        type(response) is not dict
                        or response.get("jsonrpc") != "2.0"
                        or type(response.get("id")) is not type(request["id"])
                        or response.get("id") != request["id"]
                        or (
                            set(response) != {"jsonrpc", "id", "result"}
                            and set(response) != {"jsonrpc", "id", "error"}
                        )
                    ):
                        raise ValueError("invalid upstream JSON-RPC response")
                    encoded = (
                        json.dumps(
                            response, sort_keys=True, separators=(",", ":"), allow_nan=False
                        ).encode("utf-8")
                        + b"\n"
                    )
                    if len(encoded) > _MAX_RESPONSE_BYTES:
                        raise ValueError("upstream response exceeds byte bound")
                    self._append_effect_event(
                        effect_id,
                        "completed",
                        response_sha256=hashlib.sha256(encoded).hexdigest(),
                    )
                    effect_finalized = True
                    client.sendall(encoded)
        except BaseException as exc:
            if effect_id is not None and not effect_finalized:
                try:
                    self._append_effect_event(
                        effect_id, "uncertain" if attempted else "failed_before_effect"
                    )
                except BaseException:
                    # A durable intent without a terminal event is reconciled as uncertain.
                    pass
            if attempted:
                message = (
                    "upstream effect completed durably but its response could not be delivered"
                    if effect_finalized
                    else "upstream effect may have occurred; reconcile this attempt"
                )
                raise WorkMcpProxyUncertain(message) from exc
            if isinstance(exc, WorkMcpProxyRejected):
                raise
            raise WorkMcpProxyRejected("proxy request could not be authorized") from exc

    def revoke(self, attempt_id: str, generation: int) -> None:
        """Close the matching live endpoint; repeated and stale revokes are safe."""
        self._validate_binding(attempt_id, generation, time.time() + 1)
        with self._lifecycle_lock:
            issue = self._issue
            if issue is not None and (issue[0], issue[1]) == (attempt_id, generation):
                self.close()

    def _append_effect_event(
        self,
        effect_id: str,
        state: str,
        *,
        response_sha256: str | None = None,
        resolution_sha256: str | None = None,
    ) -> None:
        if self._repository is None:
            raise WorkMcpProxyUnavailable("durable proxy effect journal is unavailable")
        with self._repository.transaction() as connection:
            latest = connection.execute(
                "SELECT sequence,state FROM work_mcp_proxy_effect_events "
                "WHERE effect_id=? ORDER BY sequence DESC LIMIT 1",
                (effect_id,),
            ).fetchone()
            sequence = 1 if latest is None else latest["sequence"] + 1
            connection.execute(
                "INSERT INTO work_mcp_proxy_effect_events "
                "(effect_id,sequence,state,response_sha256,resolution_sha256,occurred_at) "
                "VALUES (?,?,?,?,?,?)",
                (effect_id, sequence, state, response_sha256, resolution_sha256, time.time()),
            )

    def incomplete_effect_owners(self) -> tuple[tuple[str, int], ...]:
        """Return attempt generations whose latest durable effect is still an intent."""
        if self._repository is None:
            return ()
        with self._repository.read_snapshot() as connection:
            rows = connection.execute(
                "SELECT DISTINCT effect.attempt_id,effect.generation "
                "FROM work_mcp_proxy_effects AS effect "
                "JOIN work_mcp_proxy_effect_events AS event USING(effect_id) "
                "WHERE event.sequence=(SELECT max(latest.sequence) "
                "FROM work_mcp_proxy_effect_events AS latest "
                "WHERE latest.effect_id=effect.effect_id) AND event.state='intent' "
                "ORDER BY effect.attempt_id,effect.generation"
            ).fetchall()
        return tuple((row[0], row[1]) for row in rows)

    def incomplete_issue_owners(self) -> tuple[tuple[str, int], ...]:
        """Return consumed issues with no request journal that still need owner proof."""
        if self._repository is None:
            return ()
        with self._repository.read_snapshot() as connection:
            rows = connection.execute(
                "SELECT issue.attempt_id,issue.generation "
                "FROM work_mcp_proxy_issues AS issue "
                "JOIN work_mcp_proxy_issue_events AS event "
                "USING(attempt_id,generation) "
                "WHERE event.sequence=(SELECT max(latest.sequence) "
                "FROM work_mcp_proxy_issue_events AS latest "
                "WHERE latest.attempt_id=issue.attempt_id "
                "AND latest.generation=issue.generation) "
                "AND event.state='issued' "
                "AND NOT EXISTS (SELECT 1 FROM work_mcp_proxy_effects AS effect "
                "WHERE effect.attempt_id=issue.attempt_id "
                "AND effect.generation=issue.generation) "
                "ORDER BY issue.attempt_id,issue.generation"
            ).fetchall()
        return tuple((row[0], row[1]) for row in rows)

    def recover_incomplete_issues(self, attempt_id: str, generation: int) -> int:
        """Mark one stopped issue without effects abandoned, idempotently."""
        if (
            type(attempt_id) is not str
            or not attempt_id
            or len(attempt_id) > 256
            or "\x00" in attempt_id
            or type(generation) is not int
            or not 0 < generation <= 2**63 - 1
        ):
            raise WorkMcpProxyRejected("invalid proxy issue recovery owner")
        if self._repository is None:
            return 0
        with self._repository.transaction() as connection:
            row = connection.execute(
                "SELECT event.sequence FROM work_mcp_proxy_issues AS issue "
                "JOIN work_mcp_proxy_issue_events AS event USING(attempt_id,generation) "
                "WHERE issue.attempt_id=? AND issue.generation=? AND event.state='issued' "
                "AND event.sequence=(SELECT max(latest.sequence) "
                "FROM work_mcp_proxy_issue_events AS latest "
                "WHERE latest.attempt_id=issue.attempt_id "
                "AND latest.generation=issue.generation) "
                "AND NOT EXISTS (SELECT 1 FROM work_mcp_proxy_effects AS effect "
                "WHERE effect.attempt_id=issue.attempt_id "
                "AND effect.generation=issue.generation)",
                (attempt_id, generation),
            ).fetchone()
            if row is None:
                return 0
            connection.execute(
                "INSERT INTO work_mcp_proxy_issue_events "
                "(attempt_id,generation,sequence,state,occurred_at) "
                "VALUES (?,?,?,'abandoned',?)",
                (attempt_id, generation, row[0] + 1, time.time()),
            )
            return 1

    def unresolved_issues(self, *, limit: int = 100) -> tuple[dict, ...]:
        """List issued or abandoned endpoints that have no request effect journal."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise WorkMcpProxyRejected("invalid proxy issue query limit")
        if self._repository is None:
            raise WorkMcpProxyUnavailable("durable proxy issue journal is unavailable")
        with self._repository.read_snapshot() as connection:
            rows = connection.execute(
                "SELECT issue.attempt_id,issue.generation,issue.issued_at,event.state,event.occurred_at "
                "FROM work_mcp_proxy_issues AS issue "
                "JOIN work_mcp_proxy_issue_events AS event USING(attempt_id,generation) "
                "WHERE event.sequence=(SELECT max(latest.sequence) "
                "FROM work_mcp_proxy_issue_events AS latest "
                "WHERE latest.attempt_id=issue.attempt_id "
                "AND latest.generation=issue.generation) "
                "AND event.state IN ('issued','abandoned') "
                "AND NOT EXISTS (SELECT 1 FROM work_mcp_proxy_effects AS effect "
                "WHERE effect.attempt_id=issue.attempt_id "
                "AND effect.generation=issue.generation) "
                "ORDER BY issue.issued_at,issue.attempt_id LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(
            {
                "attempt_id": row[0],
                "generation": row[1],
                "issued_at": row[2],
                "state": row[3],
                "updated_at": row[4],
            }
            for row in rows
        )

    def reconcile_issue(self, attempt_id: str, generation: int, resolution: str) -> None:
        """Close an abandoned no-effect issue after an explicit operator decision."""
        if (
            type(attempt_id) is not str
            or not attempt_id
            or len(attempt_id) > 256
            or "\x00" in attempt_id
            or type(generation) is not int
            or not 0 < generation <= 2**63 - 1
            or type(resolution) is not str
            or not resolution.strip()
            or len(resolution.encode("utf-8")) > 4096
        ):
            raise WorkMcpProxyRejected("invalid proxy issue reconciliation")
        if self._repository is None:
            raise WorkMcpProxyUnavailable("durable proxy issue journal is unavailable")
        with self._repository.transaction() as connection:
            latest = connection.execute(
                "SELECT event.sequence,event.state FROM work_mcp_proxy_issues AS issue "
                "JOIN work_mcp_proxy_issue_events AS event USING(attempt_id,generation) "
                "WHERE issue.attempt_id=? AND issue.generation=? "
                "AND event.sequence=(SELECT max(current.sequence) "
                "FROM work_mcp_proxy_issue_events AS current "
                "WHERE current.attempt_id=issue.attempt_id "
                "AND current.generation=issue.generation)",
                (attempt_id, generation),
            ).fetchone()
            if latest is None:
                raise WorkMcpProxyRejected("unknown proxy issue")
            if latest[1] != "abandoned":
                raise WorkMcpProxyRejected("proxy issue is not abandoned")
            connection.execute(
                "INSERT INTO work_mcp_proxy_issue_events "
                "(attempt_id,generation,sequence,state,resolution_sha256,occurred_at) "
                "VALUES (?,?,?,'reconciled',?,?)",
                (
                    attempt_id,
                    generation,
                    latest[0] + 1,
                    hashlib.sha256(resolution.encode("utf-8")).hexdigest(),
                    time.time(),
                ),
            )

    def unresolved_effects(self, *, limit: int = 100) -> tuple[dict, ...]:
        """Expose bounded, redacted effect state for explicit operator recovery."""
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise WorkMcpProxyRejected("invalid proxy effect query limit")
        if self._repository is None:
            raise WorkMcpProxyUnavailable("durable proxy effect journal is unavailable")
        with self._repository.read_snapshot() as connection:
            rows = connection.execute(
                "SELECT effect.effect_id,effect.attempt_id,effect.generation,"
                "effect.request_sha256,effect.created_at,event.state,event.occurred_at "
                "FROM work_mcp_proxy_effects AS effect "
                "JOIN work_mcp_proxy_effect_events AS event USING(effect_id) "
                "WHERE event.sequence=(SELECT max(latest.sequence) "
                "FROM work_mcp_proxy_effect_events AS latest "
                "WHERE latest.effect_id=effect.effect_id) "
                "AND event.state IN ('intent','uncertain') "
                "ORDER BY effect.created_at,effect.effect_id LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(
            {
                "effect_id": row[0],
                "attempt_id": row[1],
                "generation": row[2],
                "request_sha256": row[3],
                "created_at": row[4],
                "state": row[5],
                "updated_at": row[6],
            }
            for row in rows
        )

    def recover_incomplete_effects(self, attempt_id: str, generation: int) -> int:
        """Mark one stopped attempt's still-open effects uncertain, idempotently.

        The caller must hold that attempt's cleanup-owner lock and prove its
        durable process identity has terminated before calling this method.
        """
        if (
            type(attempt_id) is not str
            or not attempt_id
            or len(attempt_id) > 256
            or "\x00" in attempt_id
            or type(generation) is not int
            or not 0 < generation <= 2**63 - 1
        ):
            raise WorkMcpProxyRejected("invalid proxy effect recovery owner")
        if self._repository is None:
            return 0
        recovered = 0
        with self._repository.transaction() as connection:
            intents = connection.execute(
                "SELECT effect.effect_id,max(event.sequence) AS sequence "
                "FROM work_mcp_proxy_effects AS effect "
                "JOIN work_mcp_proxy_effect_events AS event USING(effect_id) "
                "WHERE effect.attempt_id=? AND effect.generation=? "
                "GROUP BY effect.effect_id "
                "HAVING (SELECT state FROM work_mcp_proxy_effect_events "
                "WHERE effect_id=effect.effect_id ORDER BY sequence DESC LIMIT 1)='intent'",
                (attempt_id, generation),
            ).fetchall()
            for row in intents:
                connection.execute(
                    "INSERT INTO work_mcp_proxy_effect_events "
                    "(effect_id,sequence,state,occurred_at) VALUES (?,?,'uncertain',?)",
                    (row["effect_id"], row["sequence"] + 1, time.time()),
                )
                recovered += 1
        return recovered

    def reconcile_effect(self, effect_id: str, resolution: str) -> None:
        """Close an uncertain effect only after an explicit bounded reconciliation note."""
        if (
            type(effect_id) is not str
            or len(effect_id) != 32
            or any(character not in "0123456789abcdef" for character in effect_id)
            or type(resolution) is not str
            or not resolution.strip()
            or len(resolution.encode("utf-8")) > 4096
        ):
            raise WorkMcpProxyRejected("invalid proxy effect reconciliation")
        if self._repository is None:
            raise WorkMcpProxyUnavailable("durable proxy effect journal is unavailable")
        with self._repository.transaction() as connection:
            latest = connection.execute(
                "SELECT event.sequence,event.state FROM work_mcp_proxy_effects AS effect "
                "JOIN work_mcp_proxy_effect_events AS event USING(effect_id) "
                "WHERE effect.effect_id=? AND event.sequence=(SELECT max(current.sequence) "
                "FROM work_mcp_proxy_effect_events AS current "
                "WHERE current.effect_id=effect.effect_id)",
                (effect_id,),
            ).fetchone()
            if latest is None:
                raise WorkMcpProxyRejected("unknown proxy effect")
            if latest[1] != "uncertain":
                raise WorkMcpProxyRejected("proxy effect is not uncertain")
            connection.execute(
                "INSERT INTO work_mcp_proxy_effect_events "
                "(effect_id,sequence,state,resolution_sha256,occurred_at) "
                "VALUES (?,?,'reconciled',?,?)",
                (
                    effect_id,
                    latest[0] + 1,
                    hashlib.sha256(resolution.encode("utf-8")).hexdigest(),
                    time.time(),
                ),
            )

    def close(self) -> None:
        """Close both private socketpair ends and discard server-side authority."""
        with self._lifecycle_lock:
            proof, self._isolation_proof = self._isolation_proof, None
            if isinstance(
                proof, (WorkBubblewrapRuntimeIsolationProof, WorkDockerRuntimeIsolationProof)
            ):
                proof.close()
            broker, self._broker_socket = self._broker_socket, None
            worker, self._worker_socket = self._worker_socket, None
            if broker is not None:
                try:
                    # shutdown also wakes a reader holding a dup() of the
                    # broker endpoint; closing this object alone would not.
                    broker.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            for endpoint in (broker, worker):
                if endpoint is not None:
                    endpoint.close()
            self._secret = None
            self._endpoint = None

    @staticmethod
    def _unsupported() -> WorkMcpProxyUnavailable:
        return WorkMcpProxyUnavailable(
            "unbound proxy creation is disabled; use a durable attempt issue "
            "and an ACK-bound runtime isolation proof"
        )
