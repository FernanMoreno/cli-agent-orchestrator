"""Durable owner-scoped AG-UI streaming for Work message events."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable, Iterator
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cli_agent_orchestrator.api import work_routes
from cli_agent_orchestrator.clients.postgres import ConfigurationError, connect
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.work_projection import WorkQueries

_STREAM_HEADERS = {"Accept": "text/event-stream"}


def test_work_message_poll_backoff_is_bounded_and_resets_for_catch_up():
    empty = SimpleNamespace(events=[], gaps=[])
    delay = work_routes._WORK_MESSAGE_MIN_POLL_SECONDS
    sleeps = []
    for _ in range(8):
        sleep_delay, delay = work_routes._work_message_poll_schedule(empty, delay)
        sleeps.append(sleep_delay)

    assert sleeps == [0.1, 0.2, 0.4, 0.8, 1.6, 2.0, 2.0, 2.0]
    assert delay == work_routes._WORK_MESSAGE_MAX_POLL_SECONDS

    active = SimpleNamespace(events=[{"sequence": 1}], gaps=[])
    assert work_routes._work_message_poll_schedule(active, delay) == (0.1, 0.1)
    gap = SimpleNamespace(events=[], gaps=[{"from_sequence": 1, "through_sequence": 1}])
    assert work_routes._work_message_poll_schedule(gap, delay) == (0.1, 0.1)


class _SSEReader:
    """Read complete SSE blocks while preserving any trailing response chunk."""

    def __init__(self, chunks: Iterator[str]):
        self._chunks = iter(chunks)
        self._buffer = ""

    def next_frame(self) -> dict:
        while True:
            while "\n\n" not in self._buffer:
                self._buffer += next(self._chunks)
            block, self._buffer = self._buffer.split("\n\n", 1)
            frame: dict = {}
            data_lines: list[str] = []
            for line in block.splitlines():
                if line.startswith("id:"):
                    frame["id"] = line[3:].strip()
                elif line.startswith("event:"):
                    frame["event"] = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
            if data_lines:
                frame["data"] = json.loads("\n".join(data_lines))
            # Ignore optional heartbeat comments and empty blocks.
            if frame:
                return frame


@dataclass
class _WorkStreamContext:
    repository: WorkRepository
    owner: auth.Principal
    job: dict
    pages: list
    query_threads: list[int]
    factory_threads: list[int]
    loop_threads: list[int]
    client_for: Callable

    def append_event(self, event_type: str, metadata: dict | None = None) -> dict:
        with self.repository.transaction() as connection:
            return self.repository._append_event(
                connection,
                job_id=self.job["id"],
                event_type=event_type,
                actor_id=self.owner.id,
                metadata=metadata or {},
            )


@pytest.fixture
def work_stream(isolated_memory_db, monkeypatch):
    """Use the ordinary isolated PostgreSQL target for Work event reads and writes."""

    target = isolated_memory_db
    repository = WorkRepository(target)
    repository.initialize()
    owner = auth._verified_principal(
        "https://issuer.test/", "work-message-stream-owner", [auth.SCOPE_READ], "jwt"
    )
    job = repository.create_job(
        project_id="work-message-stream-project",
        principal_id=owner.id,
        allowed_providers=["mock_cli"],
        grant_id="work-message-stream-root",
    )
    pages = []
    query_threads = []
    factory_threads = []
    loop_threads = []

    def client_for(store=repository, principal=owner, *, fail_on_query=None):
        class _RecordingQueries(WorkQueries):
            def events(self, actor, job_id, *, after_sequence=0, limit=100):
                query_threads.append(threading.get_ident())
                if len(query_threads) == fail_on_query:
                    raise ConfigurationError("private poll configuration detail")
                page = super().events(actor, job_id, after_sequence=after_sequence, limit=limit)
                pages.append(page)
                return page

        def query_factory():
            factory_threads.append(threading.get_ident())
            return _RecordingQueries(store)

        monkeypatch.setattr(work_routes, "queries", query_factory)
        api = FastAPI()
        api.include_router(work_routes.router)
        api.dependency_overrides[auth.get_current_principal] = lambda: principal
        api.dependency_overrides[work_routes.repository] = lambda: store

        @api.middleware("http")
        async def record_loop_thread(request, call_next):
            loop_threads.append(threading.get_ident())
            return await call_next(request)

        @contextmanager
        def serve_client():
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            server = uvicorn.Server(
                uvicorn.Config(
                    api,
                    lifespan="off",
                    log_level="error",
                    access_log=False,
                    ws="none",
                )
            )
            server_thread = threading.Thread(
                target=server.run, kwargs={"sockets": [listener]}, daemon=True
            )
            server_thread.start()
            try:
                deadline = time.monotonic() + 5
                while (
                    not server.started and server_thread.is_alive() and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                if not server.started:
                    raise RuntimeError("AG-UI test server did not start")
                with httpx.Client(
                    base_url=f"http://127.0.0.1:{port}",
                    timeout=httpx.Timeout(10.0),
                ) as client:
                    yield client
            finally:
                server.should_exit = True
                server_thread.join(timeout=5)
                listener.close()
                if server_thread.is_alive():
                    raise AssertionError("AG-UI test server did not stop")

        return serve_client()

    return _WorkStreamContext(
        repository,
        owner,
        job,
        pages,
        query_threads,
        factory_threads,
        loop_threads,
        client_for,
    )


def _message_metadata(message_id: str) -> dict:
    return {
        "message_id": message_id,
        "sender_work_item_id": "sender-work-item",
        "receiver_work_item_id": "receiver-work-item",
        # Even malformed or newer event metadata must never reach the stream.
        "body": "private message body",
        "body_digest": "private body digest",
    }


def _open_stream(client, job_id: str, *, params: dict | None = None, headers: dict | None = None):
    return client.stream(
        "GET",
        f"/jobs/{job_id}/messages/stream",
        params=params,
        headers={**_STREAM_HEADERS, **(headers or {})},
    )


def test_stream_requires_job_owner_and_read_scope(work_stream):
    outsider = auth._verified_principal(
        "https://issuer.test/", "work-message-stream-outsider", [auth.SCOPE_ADMIN], "jwt"
    )
    with work_stream.client_for(principal=outsider) as client:
        response = client.get(
            f"/jobs/{work_stream.job['id']}/messages/stream", headers=_STREAM_HEADERS
        )
    assert response.status_code == 404

    unscoped_owner = auth._verified_principal(
        work_stream.owner.issuer, work_stream.owner.subject, [], "jwt"
    )
    with work_stream.client_for(principal=unscoped_owner) as client:
        response = client.get(
            f"/jobs/{work_stream.job['id']}/messages/stream", headers=_STREAM_HEADERS
        )
    assert response.status_code == 403


def test_stream_maps_database_configuration_failure_to_safe_503(monkeypatch):
    owner = auth._verified_principal(
        "https://issuer.test/", "work-message-config-owner", [auth.SCOPE_READ], "jwt"
    )

    def unavailable_queries():
        raise ConfigurationError("private database configuration detail")

    monkeypatch.setattr(work_routes, "queries", unavailable_queries)
    api = FastAPI()
    api.include_router(work_routes.router)
    api.dependency_overrides[auth.get_current_principal] = lambda: owner

    with TestClient(api, raise_server_exceptions=False) as client:
        response = client.get("/jobs/config-failure/messages/stream", headers=_STREAM_HEADERS)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "work_store_unavailable"
    assert "private database configuration detail" not in response.text


def test_stream_replays_after_sequence_and_suppresses_ack_events(work_stream):
    sent_before = work_stream.append_event(
        "work.message.sent", _message_metadata("message-before-cursor")
    )
    work_stream.append_event(
        "work.message.acknowledged",
        {"message_id": "message-before-cursor", "receiver_work_item_id": "receiver-work-item"},
    )
    sent_after = work_stream.append_event(
        "work.message.sent", _message_metadata("message-after-cursor")
    )

    with work_stream.client_for() as client:
        with _open_stream(
            client,
            work_stream.job["id"],
            params={"after_sequence": sent_before["sequence"]},
        ) as response:
            assert response.status_code == 200
            frame = _SSEReader(response.iter_text()).next_frame()

    assert frame["id"] == str(sent_after["sequence"])
    assert frame["event"] == "TEXT_MESSAGE_CONTENT"
    assert frame["data"]["message_id"] == "message-after-cursor"
    assert frame["data"]["delta"] == ""
    assert frame["data"]["metadata"] == {
        "sender_work_item_id": "sender-work-item",
        "receiver_work_item_id": "receiver-work-item",
    }
    assert "private message body" not in json.dumps(frame["data"])
    assert "private body digest" not in json.dumps(frame["data"])


def test_last_event_id_takes_precedence_over_after_sequence(work_stream):
    first = work_stream.append_event("work.message.sent", _message_metadata("message-first"))
    work_stream.append_event("work.item.updated", {"work_item_id": "another-item"})
    last = work_stream.append_event("work.message.sent", _message_metadata("message-last"))

    with work_stream.client_for() as client:
        with _open_stream(
            client,
            work_stream.job["id"],
            params={"after_sequence": 0},
            headers={"Last-Event-ID": str(first["sequence"] + 1)},
        ) as response:
            assert response.status_code == 200
            frame = _SSEReader(response.iter_text()).next_frame()

    assert frame["id"] == str(last["sequence"])
    assert frame["data"]["message_id"] == "message-last"


def test_retention_gap_is_raw_at_floor_without_skipping_later_events(work_stream):
    pruned = work_stream.append_event("work.message.sent", _message_metadata("pruned-message"))
    retained = work_stream.append_event("work.message.sent", _message_metadata("retained-message"))
    page = WorkQueries(work_stream.repository).events(
        work_stream.owner, work_stream.job["id"], after_sequence=0, limit=100
    )
    assert page.events
    work_stream.repository.prune_events(work_stream.job["id"], through_sequence=pruned["sequence"])
    gap_page = WorkQueries(work_stream.repository).events(
        work_stream.owner, work_stream.job["id"], after_sequence=0, limit=100
    )

    with work_stream.client_for() as client:
        with _open_stream(client, work_stream.job["id"], params={"after_sequence": 0}) as response:
            assert response.status_code == 200
            reader = _SSEReader(response.iter_text())
            gap = reader.next_frame()
            event = reader.next_frame()

    assert gap_page.gaps == [{"from_sequence": 1, "through_sequence": pruned["sequence"]}]
    assert gap_page.next_cursor == retained["sequence"]
    assert gap["event"] == "RAW"
    assert gap["id"] == str(gap_page.gaps[0]["through_sequence"])
    assert gap["data"] == gap_page.gaps[0]
    assert event["event"] == "TEXT_MESSAGE_CONTENT"
    assert event["id"] == str(retained["sequence"])
    assert event["data"]["message_id"] == "retained-message"


def test_open_stream_revalidates_owner_on_each_poll(work_stream):
    first = work_stream.append_event(
        "work.message.sent", _message_metadata("message-before-owner-change")
    )
    with work_stream.client_for() as client:
        with _open_stream(
            client,
            work_stream.job["id"],
            params={"after_sequence": first["sequence"] - 1},
        ) as response:
            assert response.status_code == 200
            reader = _SSEReader(response.iter_text())
            initial = reader.next_frame()
            assert initial["id"] == str(first["sequence"])

            replacement_owner = auth._verified_principal(
                "https://issuer.test/", "work-message-stream-new-owner", [auth.SCOPE_READ], "jwt"
            )
            with connect(work_stream.repository.target) as connection:
                with connection.transaction():
                    connection.execute(
                        "INSERT INTO work_principals (id,issuer,subject,kind,created_at) "
                        "VALUES (%s,%s,%s,%s,%s)",
                        (
                            replacement_owner.id,
                            replacement_owner.issuer,
                            replacement_owner.subject,
                            replacement_owner.kind,
                            time.time(),
                        ),
                    )
                    connection.execute(
                        "UPDATE work_jobs SET principal_id=%s WHERE id=%s",
                        (replacement_owner.id, work_stream.job["id"]),
                    )
            work_stream.append_event(
                "work.message.sent", _message_metadata("message-after-owner-change")
            )

            with pytest.raises(StopIteration):
                reader.next_frame()


def test_open_stream_closes_safely_when_configuration_fails_during_poll(work_stream):
    with work_stream.client_for(fail_on_query=2) as client:
        with _open_stream(client, work_stream.job["id"]) as response:
            assert response.status_code == 200
            with pytest.raises(StopIteration):
                _SSEReader(response.iter_text()).next_frame()

    assert len(work_stream.query_threads) == 2
    assert len(work_stream.factory_threads) == 2
    assert set(work_stream.query_threads).isdisjoint(work_stream.loop_threads)
    assert set(work_stream.factory_threads).isdisjoint(work_stream.loop_threads)


def test_live_poll_delivers_a_message_appended_after_stream_opens(work_stream):
    cursor = work_stream.repository.read_events(work_stream.job["id"])["high_water"]
    with work_stream.client_for() as client:
        with _open_stream(
            client,
            work_stream.job["id"],
            params={"after_sequence": cursor},
        ) as response:
            assert response.status_code == 200
            assert work_stream.pages
            assert work_stream.pages[0].events == []
            live = work_stream.append_event("work.message.sent", _message_metadata("message-live"))
            frame = _SSEReader(response.iter_text()).next_frame()

    assert frame["event"] == "TEXT_MESSAGE_CONTENT"
    assert frame["id"] == str(live["sequence"])
    assert frame["data"]["message_id"] == "message-live"
    assert len(work_stream.pages) >= 2
    assert len(work_stream.query_threads) >= 2
    assert len(work_stream.factory_threads) >= 2
    assert work_stream.loop_threads
    assert set(work_stream.query_threads).isdisjoint(work_stream.loop_threads)
    assert set(work_stream.factory_threads).isdisjoint(work_stream.loop_threads)


def test_replacement_api_replays_from_the_same_postgresql_work_store(work_stream):
    sent = work_stream.append_event(
        "work.message.sent", _message_metadata("message-survives-api-replacement")
    )
    replacement_repository = WorkRepository(work_stream.repository.target)
    replacement_repository.initialize()

    with work_stream.client_for() as api_a:
        with _open_stream(
            api_a,
            work_stream.job["id"],
            params={"after_sequence": sent["sequence"] - 1},
        ) as response_a:
            assert response_a.status_code == 200
            frame_a = _SSEReader(response_a.iter_text()).next_frame()

    with work_stream.client_for(store=replacement_repository) as api_b:
        with _open_stream(
            api_b,
            work_stream.job["id"],
            params={"after_sequence": sent["sequence"] - 1},
        ) as response_b:
            assert response_b.status_code == 200
            frame_b = _SSEReader(response_b.iter_text()).next_frame()

    assert frame_a["id"] == frame_b["id"] == str(sent["sequence"])
    assert frame_a["data"] == frame_b["data"]
    assert frame_b["data"]["message_id"] == "message-survives-api-replacement"


class _LiveTestIsolationProof:
    """Keep the proxy's host-acceptance gate out of this API/service composition test."""

    def require_current(self, *_args):
        return None


class _ProxyClientSession:
    """Drive multiple JSON-RPC requests through one authorized proxy generation."""

    def __init__(self, proxy, endpoint, attempt_id, generation):
        self._proxy = proxy
        self._client = socket.socket(fileno=os.dup(endpoint.worker_fd))
        self._reader = self._client.makefile("rb")
        self._errors = []

        def serve():
            try:
                proxy.serve(attempt_id, generation)
            except BaseException as error:  # surfaced synchronously by close()
                self._errors.append(error)

        self._server = threading.Thread(target=serve, daemon=True)
        self._server.start()

    def call(self, request: dict) -> dict:
        encoded = (
            json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
        )
        self._client.sendall(encoded)
        line = self._reader.readline()
        if not line:
            raise AssertionError("MCP proxy closed before returning a response")
        return json.loads(line.decode("utf-8"))

    def close(self):
        try:
            self._client.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        self._reader.close()
        self._client.close()
        self._server.join(timeout=3)
        if self._server.is_alive():
            raise AssertionError("MCP proxy did not stop after the client closed")
        if self._errors:
            raise AssertionError("MCP proxy session failed") from self._errors[0]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


def _work_message_composition_world(repository, filesystem_home):
    """Build real message authority over the same store used by the SSE clients."""
    import time as clock

    from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
    from cli_agent_orchestrator.models.work_contract import (
        ContractPermissions,
        ContractResources,
        ContractSnapshot,
        EffectiveWorkContractV2,
    )
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.delegation_snapshot import (
        DelegationSnapshots,
        ResolvedSnapshot,
    )
    from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
    from cli_agent_orchestrator.services.work_admission import WorkAdmission
    from cli_agent_orchestrator.services.work_attempt_credential import WorkAttemptCredentials
    from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
    from cli_agent_orchestrator.services.work_contract import WorkContracts
    from cli_agent_orchestrator.services.work_origin import WorkOrigins
    from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
    from cli_agent_orchestrator.services.work_reservations import StoppedWriter
    from cli_agent_orchestrator.services.work_scheduler import WorkScheduler
    from cli_agent_orchestrator.services.work_service import DeliveryObservation, WorkService

    checkout_root = str(filesystem_home.resolve())

    class NoEffectBackend(TmuxBackend):
        def preflight_work(self, _restriction):
            return None

        def create_session(self, *_args, **_kwargs):
            raise AssertionError("message composition setup must not start a worker")

    owner = auth._verified_principal(
        "https://issuer.test/",
        f"work-message-agui-{uuid4().hex}",
        [auth.SCOPE_ADMIN, auth.SCOPE_READ],
        "jwt",
    )
    project_id = f"work-message-agui-project-{uuid4().hex}"
    job = repository.create_job(
        project_id=project_id,
        principal_id=owner.id,
        allowed_providers=["mock_cli"],
        grant_id=f"work-message-agui-root-{uuid4().hex}",
        budget={"scheduler_units": 16},
    )
    grant = WorkAuthority(repository).issue_root(
        owner,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"cao.work.message", "knowledge.read"}, paths={checkout_root}
        ),
        expires_at=clock.time() + 1800,
    )
    origins = WorkOrigins(repository)
    scheduler = WorkScheduler(
        repository,
        stop_verifier=lambda writer: StoppedWriter(
            writer.attempt_id,
            writer.generation,
            writer.revision,
            "test-confirmed-stop",
        ),
    )
    scheduler.configure(capacity=8, max_queue=100, aging_seconds=10, expected_policy_revision=0)
    admission = WorkAdmission(
        repository,
        backends={"message-composition-test": NoEffectBackend()},
        origins=origins,
    )
    credentials = WorkAttemptCredentials(repository, origins=origins)
    service = WorkService(repository, origins=origins)

    def make_work(label: str):
        contract_id = f"work-message-agui-contract-{label}-{uuid4().hex}"
        snapshot = DelegationSnapshots(
            repository,
            policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision),
        ).freeze(
            principal=owner,
            job_id=job["id"],
            contract_id=contract_id,
            binding_key=f"work-message-agui-binding-{contract_id}",
            request_hash=hashlib.sha256(contract_id.encode("utf-8")).hexdigest(),
            scope="project",
            scope_id=project_id,
            resolver=lambda _connection, _principal: ResolvedSnapshot(
                "Work message AG-UI composition"
            ),
        )
        contract = EffectiveWorkContractV2(
            id=contract_id,
            operation_kind="inbox",
            provider="mock_cli",
            backend="message-composition-test",
            permissions=ContractPermissions(tools=("cao.work.message",), paths=(checkout_root,)),
            resources=ContractResources(checkout_root=checkout_root, units=1),
            snapshot=ContractSnapshot(
                state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
            ),
        )
        work = admission.admit(
            principal=owner,
            job_id=job["id"],
            idempotency_key=f"work-message-agui-{label}-{uuid4().hex}",
            request_hash=hashlib.sha256(label.encode("utf-8")).hexdigest(),
            grant_id=grant.id,
            expected_grant_revision=grant.revision,
            contract=contract,
            lease_seconds=900,
        )
        attempt = work["attempts"][-1]
        reservation = scheduler.claim_next(actor_id=owner.id)
        if reservation is None or reservation.attempt_id != attempt["id"]:
            raise AssertionError("the composition fixture could not claim its Work attempt")
        work = service.dispatch(
            work["id"],
            lambda: DeliveryObservation(),
            admission=TransitionEvidence(
                generation=attempt["generation"],
                expected_generation=attempt["generation"],
                contract_confirmed=True,
                grant_confirmed=True,
                capacity_confirmed=True,
                reservations_confirmed=True,
            ),
            actor_id=owner.id,
        )
        attempt = work["attempts"][-1]
        with repository.transaction() as connection:
            binding = WorkContracts(repository)._revalidate_order(
                connection, attempt["id"], generation=attempt["generation"]
            )
            issued = credentials.issue_in_transaction(
                connection, binding, expected_attempt_revision=attempt["revision"]
            )
        return SimpleNamespace(
            work=work,
            attempt=attempt,
            credential=issued.secret,
            contract=contract,
        )

    return SimpleNamespace(
        repository=repository,
        owner=owner,
        job=job,
        grant=grant,
        origins=origins,
        scheduler=scheduler,
        credentials=credentials,
        service=service,
        make_work=make_work,
    )


def _replace_composition_generation(world, prior):
    """Stop and reconcile one writer, then authorize and dispatch its next generation."""
    from cli_agent_orchestrator.services.work_contract import WorkContracts
    from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
    from cli_agent_orchestrator.services.work_service import DeliveryObservation, WorkService

    repository = world.repository
    owner = world.owner
    attempt = prior.attempt
    running = repository.transition_attempt(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_revision=attempt["revision"],
        expected_state="sent",
        target="running",
        actor_id=owner.id,
        event_id=uuid4().hex,
        evidence=TransitionEvidence(
            generation=attempt["generation"],
            expected_generation=attempt["generation"],
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
            task_received=True,
            execution_started=True,
        ),
    )
    current = running["attempts"][-1]
    reconciled = repository.transition_attempt(
        attempt_id=current["id"],
        generation=current["generation"],
        expected_revision=current["revision"],
        expected_state="running",
        target="reconcile",
        actor_id=owner.id,
        event_id=uuid4().hex,
        evidence=TransitionEvidence(
            generation=current["generation"],
            expected_generation=current["generation"],
        ),
    )
    replacement = WorkService(repository, origins=world.origins).retry_work(
        prior.work["id"],
        scheduler=world.scheduler,
        expected_revision=reconciled["revision"],
        provider="mock_cli",
        evidence=TransitionEvidence(
            generation=current["generation"],
            expected_generation=current["generation"],
            reconciliation_authorized=True,
        ),
        actor_id=owner.id,
        lease_seconds=900,
    )
    next_attempt = replacement["attempts"][-1]
    with repository.transaction() as connection:
        WorkContracts(repository)._bind(
            connection,
            principal=owner,
            attempt_id=next_attempt["id"],
            generation=next_attempt["generation"],
            expected_attempt_revision=next_attempt["revision"],
            grant_id=world.grant.id,
            expected_grant_revision=world.grant.revision,
            contract=prior.contract,
        )
    world.scheduler.enqueue(
        attempt_id=next_attempt["id"],
        generation=next_attempt["generation"],
        expected_attempt_revision=next_attempt["revision"],
        units=prior.contract.resources.units,
        actor_id=owner.id,
    )
    reservation = world.scheduler.claim_next(actor_id=owner.id)
    if reservation is None or reservation.attempt_id != next_attempt["id"]:
        raise AssertionError("the replacement generation could not claim its reservation")
    work = WorkService(repository, origins=world.origins).dispatch(
        replacement["id"],
        lambda: DeliveryObservation(),
        admission=TransitionEvidence(
            generation=next_attempt["generation"],
            expected_generation=next_attempt["generation"],
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
        actor_id=owner.id,
    )
    next_attempt = work["attempts"][-1]
    with repository.transaction() as connection:
        binding = WorkContracts(repository)._revalidate_order(
            connection, next_attempt["id"], generation=next_attempt["generation"]
        )
        issued = world.credentials.issue_in_transaction(
            connection, binding, expected_attempt_revision=next_attempt["revision"]
        )
    return SimpleNamespace(
        work=work,
        attempt=next_attempt,
        credential=issued.secret,
        contract=prior.contract,
    )


def _message_proxy(repository, work, upstream):
    from cli_agent_orchestrator.services.work_mcp_proxy import WorkMcpProxy

    proxy = WorkMcpProxy(
        repository,
        server_secret_factory=lambda: b"test-only-server-secret",
        upstream=upstream,
    )
    attempt = work.attempt
    endpoint = proxy.create_bound_attempt(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_attempt_revision=attempt["revision"],
        contract_hash=work.contract.canonical_hash(),
        expires_at=min(attempt["lease_expires_at"] - 1, time.time() + 60),
    )
    # Existing proxy unit coverage separately enforces the host isolation proof.
    proxy._isolation_proof = _LiveTestIsolationProof()
    proxy._secret = b"test-only-server-secret"
    return proxy, endpoint


def _message_call(request_id: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": "cao.work.message", "arguments": arguments},
    }


def _message_result(response: dict, request_id: str) -> dict:
    assert response.get("jsonrpc") == "2.0"
    assert response.get("id") == request_id
    assert "result" in response, response
    return json.loads(response["result"]["content"][0]["text"])


def _proxy_call_once(repository, work, upstream, request):
    proxy, endpoint = _message_proxy(repository, work, upstream)
    client = socket.socket(fileno=os.dup(endpoint.worker_fd))
    encoded = (
        json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
    )
    try:
        client.sendall(encoded)
        proxy.serve_once(endpoint)
        with client.makefile("rb") as reader:
            line = reader.readline()
        if not line:
            raise AssertionError("MCP proxy closed before returning a response")
        return json.loads(line.decode("utf-8"))
    finally:
        client.close()


def test_api_replacement_replays_proxy_send_after_generation_reconciliation(work_stream, tmp_path):
    """Exercise uncertain send, API replacement, new-generation replay, and SSE restart."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.services.work_mcp_proxy import WorkMcpProxy, WorkMcpProxyUncertain
    from cli_agent_orchestrator.services.work_origin import WorkOrigins

    # The original API repository is shared with the real Work message service.
    api_a_repository = work_stream.repository
    world = _work_message_composition_world(api_a_repository, tmp_path)
    sender = world.make_work("sender")
    receiver = world.make_work("receiver")
    body = "private cross-generation message 🧭"
    idempotency_key = "api-restart-send-1"
    first_request = _message_call(
        "jsonrpc-send-before-lost-response",
        {
            "operation": "send",
            "receiver_work_item_id": receiver.work["id"],
            "idempotency_key": idempotency_key,
            "body": body,
        },
    )
    performed = []

    def commit_then_lose_response(request, _secret):
        performed.append(world.origins.handle_mcp_request(request, sender.credential))
        raise OSError("simulated lost response after the durable Work commit")

    sender_proxy, sender_endpoint = _message_proxy(
        api_a_repository, sender, commit_then_lose_response
    )
    sender_socket = socket.socket(fileno=os.dup(sender_endpoint.worker_fd))
    sender_socket.sendall(
        json.dumps(first_request, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    )
    try:
        with pytest.raises(WorkMcpProxyUncertain):
            sender_proxy.serve_once(sender_endpoint)
    finally:
        sender_socket.close()

    original_result = _message_result(performed[0], first_request["id"])
    message_id = original_result["message_id"]
    assert original_result["status"] == "sent"
    uncertain = sender_proxy.unresolved_effects()
    assert len(uncertain) == 1
    effect_id = uncertain[0]["effect_id"]
    assert uncertain[0]["state"] == "uncertain"
    assert body not in json.dumps(uncertain)
    with api_a_repository.read_snapshot() as connection:
        committed_before_reconciliation = connection.execute(
            "SELECT message_id,body FROM work_messages WHERE job_id=%s",
            (world.job["id"],),
        ).fetchall()
        sent_before_reconciliation = connection.execute(
            "SELECT count(*) FROM work_events WHERE job_id=%s "
            "AND event_type='work.message.sent'",
            (world.job["id"],),
        ).fetchone()[0]
    assert [tuple(row) for row in committed_before_reconciliation] == [(message_id, body)]
    assert sent_before_reconciliation == 1

    # API B starts from the same PostgreSQL target and constructs its own
    # origin/proxy services. Any post-restart Work message operation must go
    # through this replacement service graph, not the still-referenced API A
    # object in the test process.
    replacement_repository = WorkRepository(api_a_repository.target, filesystem_home=tmp_path)
    replacement_repository.initialize()
    api_b_origins = WorkOrigins(replacement_repository)
    api_b_recovery = WorkMcpProxy(replacement_repository)
    api_a_message_calls = []

    def reject_api_a_message_call(*_args, **_kwargs):
        api_a_message_calls.append("unexpected API A call")
        raise AssertionError("post-restart Work message operation used API A")

    world.origins.handle_mcp_request = reject_api_a_message_call

    # The original receiver generation is explicitly stopped/reconciled before
    # a newly authorized generation consumes and acknowledges this message via
    # API B's own credential resolver and Work message service.
    receiver_v2 = _replace_composition_generation(world, receiver)
    assert receiver_v2.attempt["generation"] == receiver.attempt["generation"] + 1

    def new_receiver_upstream(request, _secret):
        return api_b_origins.handle_mcp_request(request, receiver_v2.credential)

    receiver_proxy, receiver_endpoint = _message_proxy(
        replacement_repository, receiver_v2, new_receiver_upstream
    )
    with _ProxyClientSession(
        receiver_proxy,
        receiver_endpoint,
        receiver_v2.attempt["id"],
        receiver_v2.attempt["generation"],
    ) as receiver_client:
        received_response = receiver_client.call(
            _message_call("receiver-receive-new-generation", {"operation": "receive"})
        )
        received = _message_result(received_response, "receiver-receive-new-generation")
        assert len(received["messages"]) == 1
        assert received["messages"][0]["message_id"] == message_id
        assert received["messages"][0]["body"] == body
        acknowledged_response = receiver_client.call(
            _message_call(
                "receiver-ack-new-generation",
                {"operation": "ack", "message_id": message_id},
            )
        )
        acknowledged = _message_result(acknowledged_response, "receiver-ack-new-generation")
        assert acknowledged["status"] == "acknowledged"

    # Reconcile the uncertain effect against its committed message/event before
    # replacing the old sender, then replay the identical business operation
    # with a fresh JSON-RPC ID under the new sender-generation credential.
    api_b_recovery.reconcile_effect(
        effect_id, "message row and sent event confirm commit; only the response was lost"
    )
    assert sender_proxy.unresolved_effects() == ()
    sender_v2 = _replace_composition_generation(world, sender)
    assert sender_v2.attempt["generation"] == sender.attempt["generation"] + 1

    def replay_upstream(request, _secret):
        return api_b_origins.handle_mcp_request(request, sender_v2.credential)

    retry_request = _message_call(
        "jsonrpc-send-retry-new-id",
        {
            "operation": "send",
            "receiver_work_item_id": receiver.work["id"],
            "idempotency_key": idempotency_key,
            "body": body,
        },
    )
    replay_response = _proxy_call_once(
        replacement_repository, sender_v2, replay_upstream, retry_request
    )
    replay = _message_result(replay_response, retry_request["id"])
    assert replay["status"] == "replayed"
    assert replay["message_id"] == message_id

    with api_a_repository.read_snapshot() as connection:
        durable_messages = connection.execute(
            "SELECT message_id,body,sender_generation,acknowledged_generation "
            "FROM work_messages WHERE job_id=%s",
            (world.job["id"],),
        ).fetchall()
        durable_events = connection.execute(
            "SELECT sequence,event_type,metadata FROM work_events "
            "WHERE job_id=%s AND event_type IN "
            "('work.message.sent','work.message.acknowledged') ORDER BY sequence",
            (world.job["id"],),
        ).fetchall()
    assert len(durable_messages) == 1
    assert tuple(durable_messages[0]) == (
        message_id,
        body,
        sender.attempt["generation"],
        receiver_v2.attempt["generation"],
    )
    assert [event["event_type"] for event in durable_events] == [
        "work.message.sent",
        "work.message.acknowledged",
    ]
    event_metadata = json.dumps([event["metadata"] for event in durable_events], ensure_ascii=False)
    body_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert body not in event_metadata
    assert body_digest not in event_metadata

    # Both API generations read the same retained event, while AG-UI exposes
    # routing metadata only and never the message or its digest.
    sent_event = durable_events[0]
    with work_stream.client_for(store=api_a_repository, principal=world.owner) as api_a:
        with _open_stream(
            api_a,
            world.job["id"],
            params={"after_sequence": sent_event["sequence"] - 1},
        ) as response_a:
            assert response_a.status_code == 200
            frame_a = _SSEReader(response_a.iter_text()).next_frame()
    with work_stream.client_for(store=replacement_repository, principal=world.owner) as api_b:
        with _open_stream(
            api_b,
            world.job["id"],
            params={"after_sequence": sent_event["sequence"] - 1},
        ) as response_b:
            assert response_b.status_code == 200
            frame_b = _SSEReader(response_b.iter_text()).next_frame()

    assert frame_a == frame_b
    assert frame_b["id"] == str(sent_event["sequence"])
    assert frame_b["event"] == "TEXT_MESSAGE_CONTENT"
    assert frame_b["data"]["message_id"] == message_id
    assert frame_b["data"]["delta"] == ""
    assert frame_b["data"]["metadata"] == {
        "sender_work_item_id": sender.work["id"],
        "receiver_work_item_id": receiver.work["id"],
    }
    streamed_payload = json.dumps(frame_b["data"], ensure_ascii=False)
    assert body not in streamed_payload
    assert body_digest not in streamed_payload
    assert api_a_message_calls == []
