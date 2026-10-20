"""Local admission/snapshot capacity with the real PostgreSQL ledger."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from test.postgres.interactions import (
    postgres_interaction_store,
    register_interaction_source,
)
from threading import Barrier, Lock, local

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.interactions.models import CreateGroupRequest, QuestionInput
from cli_agent_orchestrator.services.interactions.service import (
    InteractionCapacityExceeded,
)

pytestmark = pytest.mark.postgres


def _plain(value):
    if is_dataclass(value):
        return asdict(value)
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def test_twenty_sources_one_hundred_pending_are_visible_and_saturation_is_explicit(
    postgres_interaction_store,
):
    owner = auth._verified_principal(
        "https://test.issuer", "capacity-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    service = postgres_interaction_store.service
    identities = set()
    create_to_visible_latency = []
    confirmed_registration_to_visible_latency = []
    first_token = None
    for source_index in range(20):
        terminal_id = f"ac02{source_index:04x}"
        session_name = f"capacity-{source_index}"
        incarnation = f"capacity-incarnation-{source_index}"
        database.create_terminal(
            terminal_id,
            session_name,
            "worker",
            "codex",
            allowed_tools=["@cao-mcp-server"],
            session_incarnation_id=incarnation,
        )
        issued = service.register_source(
            terminal_id=terminal_id,
            incarnation_id=incarnation,
            session_name=session_name,
            provider="codex",
            principal=owner,
        )
        first_token = first_token or issued.token
        create_started_at = time.monotonic()
        created_group = service.create_group(
            issued.token,
            CreateGroupRequest(
                questions=[
                    QuestionInput(
                        kind="text",
                        prompt=f"capacity question {source_index}/{index}",
                        required=True,
                    )
                    for index in range(5)
                ]
            ),
            f"capacity-create-operation-{source_index:04d}",
        )
        registration_confirmed_at = time.monotonic()
        group = _plain(created_group)
        current = {question["question_id"] for question in group["questions"]}
        identities.update(current)
        # The public snapshot has a bounded default page, so request the stated
        # acceptance population explicitly rather than mistaking pagination for loss.
        metadata_page = service.list_metadata(owner, limit=100)
        visible_at = time.monotonic()
        metadata = _plain(metadata_page)
        visible = json.dumps(metadata)
        assert all(question_id in visible for question_id in current)
        create_to_visible_latency.append(visible_at - create_started_at)
        confirmed_registration_to_visible_latency.append(visible_at - registration_confirmed_at)
    assert len(identities) == 100
    confirmed_registration_p95 = sorted(confirmed_registration_to_visible_latency)[18]
    create_to_visible_p95 = sorted(create_to_visible_latency)[18]
    assert confirmed_registration_p95 < 2.0, {
        "confirmed_registration_to_visible_p95_s": confirmed_registration_p95,
        "create_to_visible_p95_s": create_to_visible_p95,
    }
    with pytest.raises(InteractionCapacityExceeded):
        service.create_group(
            first_token,
            CreateGroupRequest(
                questions=[QuestionInput(kind="text", prompt="visible saturation", required=True)]
            ),
            "capacity-create-operation-overflow",
        )
    remaining = json.dumps(_plain(service.list_metadata(owner, limit=100)))
    assert all(question_id in remaining for question_id in identities)


def test_concurrent_group_admission_cannot_overrun_global_capacity(
    postgres_interaction_store, monkeypatch
):
    from cli_agent_orchestrator.services.interactions import service as service_module

    store = postgres_interaction_store
    owner = auth._verified_principal(
        "https://test.issuer", "capacity-race-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    sources = [register_interaction_source(store, owner=owner) for _ in range(3)]
    service = store.service
    monkeypatch.setattr(service_module, "_MAX_ACTIVE", 2)
    service.create_group(
        sources[0].token,
        CreateGroupRequest(
            questions=[QuestionInput(kind="text", prompt="Base active question", required=True)]
        ),
        "capacity-race-base-operation-0001",
    )
    barrier = Barrier(2)

    def admit(index):
        barrier.wait(timeout=10)
        try:
            service.create_group(
                sources[index + 1].token,
                CreateGroupRequest(
                    questions=[
                        QuestionInput(
                            kind="text", prompt=f"Competing active question {index}", required=True
                        )
                    ]
                ),
                f"capacity-race-contender-operation-{index:04d}",
            )
            return "created"
        except InteractionCapacityExceeded:
            return "full"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(admit, range(2)))

    assert sorted(outcomes) == ["created", "full"]
    with store.repository.read_connection() as connection:
        active_count = connection.execute(
            "SELECT count(*) FROM interaction_questions WHERE lifecycle='open'"
        ).fetchone()[0]
    assert active_count == 2


def test_concurrent_capacity_profile_records_lock_wait_and_visible_latency(
    postgres_interaction_store,
):
    """Exercise the 20-source/100-question acceptance case with SQL timing evidence."""
    owner = auth._verified_principal(
        "https://test.issuer", "capacity-profile-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    store = postgres_interaction_store
    sources = [register_interaction_source(store, owner=owner) for _ in range(20)]
    service = store.service
    barrier = Barrier(len(sources))
    thread_state = local()
    trace_lock = Lock()
    traces = {
        index: {
            "query_count": {},
            "query_ms": {},
            "visibility_query_count": {},
            "visibility_query_ms": {},
            "visibility_source_queries": [],
            "lock_wait_ms": None,
            "lock_hold_ms": None,
            "setup_ms": None,
            "transaction_ms": None,
            "registration_confirmed_at": None,
            "visibility_expiry_ms": None,
            "visibility_read_setup_ms": None,
            "error": None,
        }
        for index in range(len(sources))
    }
    original_transaction = store.repository.transaction
    original_read_connection = store.repository.read_connection
    original_expire_before_read = service._expire_before_read

    def query_bucket(statement: object) -> str:
        query = " ".join(str(statement).lower().split())
        if "pg_advisory_xact_lock" in query:
            return "global_capacity_lock"
        if "from interaction_waits w join interaction_groups" in query:
            return "wait_cycle_scan"
        if query.startswith("insert into interaction_events"):
            return "event_insert"
        if query.startswith("insert into interaction_groups"):
            return "group_insert"
        if query.startswith("insert into interaction_questions("):
            return "question_insert"
        if query.startswith("insert into interaction_question_versions("):
            return "question_version_insert"
        if query.startswith("update interaction_groups"):
            return "group_update"
        if query.startswith("insert into interaction_waits"):
            return "wait_insert"
        if query.startswith("select * from interaction_groups"):
            return "group_result"
        if query.startswith("select question_id,version,required,lifecycle,expires_at"):
            return "created_questions_result"
        if "count(*)" in query and "interaction_questions" in query:
            return "capacity_count"
        if (
            "pg_catalog" in query
            or "information_schema" in query
            or "_cao_schema_migrations" in query
            or "_cao_store_identity" in query
            or "pg_roles" in query
            or "pg_namespace" in query
        ):
            return "schema_verification"
        if "terminals" in query or "session_incarnations" in query:
            return "terminal_binding"
        if "interaction_delegations" in query:
            return "delegation_lookup"
        if "interaction_sources" in query:
            return "source_validation"
        if "interaction_" in query:
            return "interaction_rows"
        if "work_" in query:
            return "work_validation"
        return "other"

    class TimedConnection:
        def __init__(self, connection, trace, phase):
            self.connection = connection
            self.trace = trace
            self.phase = phase
            self.lock_acquired_at = None

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, statement, params=(), *, prepare=None):
            bucket = query_bucket(statement)
            count_key = "query_count" if self.phase == "create" else "visibility_query_count"
            duration_key = "query_ms" if self.phase == "create" else "visibility_query_ms"
            started = time.perf_counter()
            try:
                if prepare is None:
                    result = self.connection.execute(statement, params)
                else:
                    result = self.connection.execute(statement, params, prepare=prepare)
            except BaseException as error:
                elapsed_ms = (time.perf_counter() - started) * 1000
                with trace_lock:
                    self.trace[count_key][bucket] = self.trace[count_key].get(bucket, 0) + 1
                    self.trace[duration_key][bucket] = (
                        self.trace[duration_key].get(bucket, 0.0) + elapsed_ms
                    )
                    if bucket == "global_capacity_lock":
                        self.trace["lock_wait_ms"] = elapsed_ms
                        self.trace["error"] = getattr(error, "sqlstate", type(error).__name__)
                raise
            elapsed_ms = (time.perf_counter() - started) * 1000
            with trace_lock:
                self.trace[count_key][bucket] = self.trace[count_key].get(bucket, 0) + 1
                self.trace[duration_key][bucket] = (
                    self.trace[duration_key].get(bucket, 0.0) + elapsed_ms
                )
                if (
                    self.phase == "visibility"
                    and bucket == "source_validation"
                    and len(self.trace["visibility_source_queries"]) < 3
                ):
                    self.trace["visibility_source_queries"].append(
                        " ".join(str(statement).split())[:300]
                    )
                if bucket == "global_capacity_lock":
                    self.trace["lock_wait_ms"] = elapsed_ms
                    self.lock_acquired_at = time.perf_counter()
            return result

        def executemany(self, statement, params):
            bucket = query_bucket(statement)
            started = time.perf_counter()
            try:
                result = self.connection.executemany(statement, params)
            except BaseException:
                elapsed_ms = (time.perf_counter() - started) * 1000
                with trace_lock:
                    self.trace["query_count"][bucket] = self.trace["query_count"].get(bucket, 0) + 1
                    self.trace["query_ms"][bucket] = (
                        self.trace["query_ms"].get(bucket, 0.0) + elapsed_ms
                    )
                raise
            elapsed_ms = (time.perf_counter() - started) * 1000
            with trace_lock:
                self.trace["query_count"][bucket] = self.trace["query_count"].get(bucket, 0) + 1
                self.trace["query_ms"][bucket] = (
                    self.trace["query_ms"].get(bucket, 0.0) + elapsed_ms
                )
            return result

    @contextmanager
    def traced_transaction():
        index = getattr(thread_state, "request_index", None)
        phase = getattr(thread_state, "phase", None)
        if index is None or phase != "create":
            with original_transaction() as connection:
                yield connection
            return

        trace = traces[index]
        started = time.perf_counter()
        measured = None
        try:
            with original_transaction() as connection:
                measured = TimedConnection(connection, trace, "create")
                with trace_lock:
                    trace["setup_ms"] = (time.perf_counter() - started) * 1000
                yield measured
            registration_confirmed_at = time.perf_counter()
            with trace_lock:
                trace["registration_confirmed_at"] = registration_confirmed_at
        except BaseException as error:
            with trace_lock:
                trace["error"] = trace["error"] or getattr(error, "sqlstate", type(error).__name__)
            raise
        finally:
            finished = time.perf_counter()
            with trace_lock:
                trace["transaction_ms"] = (finished - started) * 1000
                if measured is not None and measured.lock_acquired_at is not None:
                    trace["lock_hold_ms"] = (finished - measured.lock_acquired_at) * 1000

    store.repository.transaction = traced_transaction

    def traced_expire_before_read():
        index = getattr(thread_state, "request_index", None)
        phase = getattr(thread_state, "phase", None)
        started = time.perf_counter()
        try:
            return original_expire_before_read()
        finally:
            if index is not None and phase == "visibility":
                with trace_lock:
                    traces[index]["visibility_expiry_ms"] = (time.perf_counter() - started) * 1000

    service._expire_before_read = traced_expire_before_read

    @contextmanager
    def traced_read_connection():
        index = getattr(thread_state, "request_index", None)
        phase = getattr(thread_state, "phase", None)
        if index is None or phase != "visibility":
            with original_read_connection() as connection:
                yield connection
            return
        started = time.perf_counter()
        with original_read_connection() as connection:
            with trace_lock:
                traces[index]["visibility_read_setup_ms"] = (time.perf_counter() - started) * 1000
            yield TimedConnection(connection, traces[index], "visibility")

    store.repository.read_connection = traced_read_connection

    def create_and_check_visible(index):
        registered = sources[index]
        thread_state.request_index = index
        thread_state.phase = "create"
        barrier.wait(timeout=15)
        started = time.perf_counter()
        try:
            group = service.create_group(
                registered.token,
                CreateGroupRequest(
                    questions=[
                        QuestionInput(
                            kind="text",
                            prompt=f"Concurrent capacity question {index}/{question_index}",
                            required=True,
                        )
                        for question_index in range(5)
                    ]
                ),
                f"capacity-concurrent-operation-{index:04d}",
            )
            create_ms = (time.perf_counter() - started) * 1000
            registration_confirmed_at = traces[index]["registration_confirmed_at"]
            assert registration_confirmed_at is not None
            question_ids = {question["question_id"] for question in group.questions}
            thread_state.phase = "visibility"
            metadata_started = time.perf_counter()
            metadata_page = service.list_metadata(owner, limit=100)
            metadata_returned_at = time.perf_counter()
            metadata_ms = (metadata_returned_at - metadata_started) * 1000
            metadata = _plain(metadata_page)
            visible_ids = {item.get("question_id") for item in metadata.get("items", [])}
            missing_ids = sorted(question_ids - visible_ids)
            return {
                "index": index,
                "create_ms": create_ms,
                "metadata_ms": metadata_ms,
                "visible_ms": (metadata_returned_at - started) * 1000,
                "confirmed_registration_to_visible_ms": (
                    metadata_returned_at - registration_confirmed_at
                )
                * 1000,
                "missing_ids": missing_ids,
                "question_ids": question_ids,
                "error": None,
            }
        except BaseException as error:
            return {
                "index": index,
                "create_ms": None,
                "metadata_ms": None,
                "visible_ms": None,
                "confirmed_registration_to_visible_ms": None,
                "missing_ids": [],
                "question_ids": set(),
                "error": getattr(error, "sqlstate", type(error).__name__),
            }
        finally:
            thread_state.phase = None
            thread_state.request_index = None

    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        outcomes = list(pool.map(create_and_check_visible, range(len(sources))))

    successful = [outcome for outcome in outcomes if outcome["error"] is None]
    confirmed_registration_count = sum(
        trace["registration_confirmed_at"] is not None for trace in traces.values()
    )
    create_latencies = sorted(outcome["create_ms"] for outcome in successful)
    metadata_latencies = sorted(outcome["metadata_ms"] for outcome in successful)
    create_to_visible_latencies = sorted(outcome["visible_ms"] for outcome in successful)
    confirmed_registration_to_visible_latencies = sorted(
        outcome["confirmed_registration_to_visible_ms"] for outcome in successful
    )
    lock_waits = sorted(
        trace["lock_wait_ms"] for trace in traces.values() if trace["lock_wait_ms"] is not None
    )
    lock_holds = sorted(
        trace["lock_hold_ms"] for trace in traces.values() if trace["lock_hold_ms"] is not None
    )
    transactions = sorted(
        trace["transaction_ms"] for trace in traces.values() if trace["transaction_ms"] is not None
    )
    query_totals = {}
    query_counts = {}
    visibility_query_totals = {}
    visibility_query_counts = {}
    visibility_source_queries = []
    for trace in traces.values():
        for key, value in trace["query_ms"].items():
            query_totals[key] = query_totals.get(key, 0.0) + value
            query_counts[key] = query_counts.get(key, 0) + trace["query_count"][key]
        for key, value in trace["visibility_query_ms"].items():
            visibility_query_totals[key] = visibility_query_totals.get(key, 0.0) + value
            visibility_query_counts[key] = (
                visibility_query_counts.get(key, 0) + trace["visibility_query_count"][key]
            )
        visibility_source_queries.extend(trace["visibility_source_queries"])
    slowest = sorted(
        (
            {
                "index": outcome["index"],
                "create_ms": outcome["create_ms"],
                "metadata_ms": outcome["metadata_ms"],
                "visible_ms": outcome["visible_ms"],
                "confirmed_registration_to_visible_ms": outcome[
                    "confirmed_registration_to_visible_ms"
                ],
                "lock_wait_ms": traces[outcome["index"]]["lock_wait_ms"],
                "lock_hold_ms": traces[outcome["index"]]["lock_hold_ms"],
                "transaction_ms": traces[outcome["index"]]["transaction_ms"],
                "visibility_expiry_ms": traces[outcome["index"]]["visibility_expiry_ms"],
                "visibility_read_setup_ms": traces[outcome["index"]]["visibility_read_setup_ms"],
            }
            for outcome in outcomes
        ),
        key=lambda item: item["visible_ms"] or 0,
        reverse=True,
    )[:5]
    diagnostics = {
        "success_count": len(successful),
        "confirmed_registration_count": confirmed_registration_count,
        "create_p95_ms": round(create_latencies[18], 1) if len(create_latencies) >= 19 else None,
        "metadata_p95_ms": (
            round(metadata_latencies[18], 1) if len(metadata_latencies) >= 19 else None
        ),
        "create_to_visible_p95_ms": (
            round(create_to_visible_latencies[18], 1)
            if len(create_to_visible_latencies) >= 19
            else None
        ),
        "confirmed_registration_to_visible_p95_ms": (
            round(confirmed_registration_to_visible_latencies[18], 1)
            if len(confirmed_registration_to_visible_latencies) >= 19
            else None
        ),
        "lock_wait_ms": {
            "p50": round(lock_waits[len(lock_waits) // 2], 1) if lock_waits else None,
            "p95": round(lock_waits[min(18, len(lock_waits) - 1)], 1) if lock_waits else None,
            "max": round(lock_waits[-1], 1) if lock_waits else None,
        },
        "lock_hold_ms": {
            "p50": round(lock_holds[len(lock_holds) // 2], 1) if lock_holds else None,
            "p95": round(lock_holds[min(18, len(lock_holds) - 1)], 1) if lock_holds else None,
            "max": round(lock_holds[-1], 1) if lock_holds else None,
        },
        "transaction_ms": {
            "p50": round(transactions[len(transactions) // 2], 1) if transactions else None,
            "p95": round(transactions[min(18, len(transactions) - 1)], 1) if transactions else None,
            "max": round(transactions[-1], 1) if transactions else None,
        },
        "query_count": query_counts,
        "query_ms_total": {key: round(value, 1) for key, value in query_totals.items()},
        "visibility_query_count": visibility_query_counts,
        "visibility_query_ms_total": {
            key: round(value, 1) for key, value in visibility_query_totals.items()
        },
        "visibility_source_queries": visibility_source_queries[:3],
        "visibility_phase_p95_ms": {
            name: (
                round(sorted(trace[name] for trace in traces.values())[18], 1)
                if all(trace[name] is not None for trace in traces.values())
                else None
            )
            for name in ("visibility_expiry_ms", "visibility_read_setup_ms")
        },
        "slowest": [
            {
                key: round(value, 1) if isinstance(value, float) else value
                for key, value in item.items()
            }
            for item in slowest
        ],
    }
    print(f"CAPACITY_TRACE {json.dumps(diagnostics, sort_keys=True)}")
    assert len(successful) == len(sources), f"concurrent create/visibility failures: {diagnostics}"
    assert confirmed_registration_count == len(sources), diagnostics
    assert all(
        trace[name] is not None
        for trace in traces.values()
        for name in ("visibility_expiry_ms", "visibility_read_setup_ms")
    ), diagnostics
    assert all(not outcome["missing_ids"] for outcome in successful), diagnostics
    assert (
        len({question_id for outcome in successful for question_id in outcome["question_ids"]})
        == 100
    )
    assert confirmed_registration_to_visible_latencies[18] < 2000, diagnostics
