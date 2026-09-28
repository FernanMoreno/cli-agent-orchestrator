"""Durable identity, transactional events and independent-connection races."""

import hashlib
import importlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from test.fixtures.work_store import work_store_paths  # noqa: F401

import pytest

from cli_agent_orchestrator.clients.work_repository import (
    SchemaMismatch,
    WorkConflict,
    WorkRepository,
)
from cli_agent_orchestrator.clients.work_process_identity_schema import (
    PROCESS_IDENTITY_SCHEMA,
)
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence


@pytest.fixture
def store(work_store_paths):
    repository = WorkRepository(work_store_paths.database)
    repository.initialize()
    return repository


def create_job(store):
    return store.create_job(
        project_id="project",
        principal_id="operator",
        allowed_providers=["mock_cli"],
        grant_id="grant",
    )


def _process_identity_v3(**overrides):
    payload = {
        "version": 3,
        "boot_id": "boot-identity-test",
        "monitor_pid": 4101,
        "monitor_start_time_ticks": 12001,
        "init_pid": 4102,
        "init_start_time_ticks": 12002,
        "init_parent_pid": 4101,
        "pid_namespace": [1, 41001],
        "net_namespace": [1, 41002],
        "ipc_namespace": [1, 41003],
        "monitor_argv_prefix_sha256": "a" * 64,
    }
    payload.update(overrides)
    identity_sha256 = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    return {**payload, "identity_sha256": identity_sha256}


def legacy_v21_store(path):
    """Build an actual v21 ledger, then let a test migrate that same file to v22."""
    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    store = WorkRepository(path)
    verifier = WorkRepository._verify
    with store.transaction() as connection:
        for version in range(1, 22):
            for statement in repository_module._MIGRATIONS[version]:
                connection.execute(statement)
            if version == 16:
                store._initialize_inbox_store_context(connection)
            connection.execute(
                "INSERT INTO work_migrations VALUES (?,?,?,?)",
                (version, repository_module._CHECKSUMS[version], time.time(), "verified"),
            )
        verifier(connection, version=21)

    def verify_v21(connection, *, version=None):
        return verifier(connection, version=21 if version is None else version)

    store.__dict__["_verify"] = verify_v21
    store.__dict__["_legacy_v21"] = True
    return store


def migrate_legacy_v21_store(store):
    """Restore the current verifier before applying the real v21-to-v22 migration."""
    store.__dict__.pop("_verify")
    store.__dict__.pop("_legacy_v21")
    store.initialize()
    with store.connection() as connection:
        WorkRepository._verify(connection)
    return store


def _admission_request(job, **overrides):
    request = dict(
        job_id=job["id"],
        operation_kind="launch",
        idempotency_key="request-1",
        request_hash=hashlib.sha256(b"request").hexdigest(),
        contract_id="contract",
        snapshot_id="snapshot",
        provider="mock_cli",
        actor_id="operator",
        lease_seconds=60,
    )
    request.update(overrides)
    return request


def admit(store, job, **overrides):
    request = _admission_request(job, **overrides)
    return store.admit_work(**request)


def legacy_admit(store, job, **overrides):
    """Historical fixture writer: only valid against the deliberately-v21 store."""
    assert store.__dict__.get("_legacy_v21") is True
    with store.transaction() as connection:
        store._verify(connection)
        return store._admit_work(connection, **_admission_request(job, **overrides))


def test_repeated_request_survives_restart_without_duplicate_attempt(store):
    job = create_job(store)
    first = admit(store, job)
    reopened = WorkRepository(store.path)
    assert admit(reopened, job) == first
    assert reopened.get_work(first["id"])["attempts"] == first["attempts"]
    assert len(first["attempts"]) == 1


def test_process_identity_v3_survives_repository_restart_and_is_idempotent(store):
    job = create_job(store)
    work = advance(store, admit(store, job))
    attempt_id = work["attempts"][0]["id"]
    identity = _process_identity_v3()

    persist = getattr(store, "persist_process_identity", None)
    read = getattr(store, "read_process_identity", None)
    assert callable(persist), "WorkRepository must durably store process identities"
    assert callable(read), "WorkRepository must load a process identity after restart"

    assert persist(attempt_id, 1, identity) is None
    assert persist(attempt_id, 1, identity) is None
    reopened = WorkRepository(store.path)
    assert reopened.read_process_identity(attempt_id, 1) == identity
    with reopened.connection() as connection:
        stored = connection.execute(
            "SELECT identity_json FROM work_process_identities WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
    assert json.loads(stored["identity_json"]) == identity
    assert "monitor_argv" not in json.loads(stored["identity_json"])


def test_process_identity_v3_rejects_replacement_and_wrong_generation(store):
    job = create_job(store)
    work = advance(store, admit(store, job))
    attempt_id = work["attempts"][0]["id"]
    original = _process_identity_v3()
    replacement = _process_identity_v3(monitor_pid=5101, init_pid=5102, init_parent_pid=5101)

    persist = getattr(store, "persist_process_identity", None)
    read = getattr(store, "read_process_identity", None)
    assert callable(persist), "WorkRepository must durably store process identities"
    assert callable(read), "WorkRepository must load a process identity after restart"

    persist(attempt_id, 1, original)
    with pytest.raises(WorkConflict, match="identity"):
        persist(attempt_id, 1, replacement)
    with pytest.raises(WorkConflict, match="generation"):
        persist(attempt_id, 2, replacement)
    with pytest.raises(WorkConflict, match="generation"):
        read(attempt_id, 2)
    assert read(attempt_id, 1) == original


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(version=2),
        lambda value: value.update(monitor_argv=["/worker", "secret-prompt"]),
        lambda value: value.update(monitor_argv_prefix_sha256="not-a-sha256"),
        lambda value: value.update(identity_sha256="0" * 64),
    ],
    ids=("wrong-protocol", "raw-argv", "bad-prefix-digest", "bad-identity-digest"),
)
def test_process_identity_v3_rejects_untrusted_identity_shapes(store, mutation):
    job = create_job(store)
    work = advance(store, admit(store, job))
    attempt_id = work["attempts"][0]["id"]
    identity = _process_identity_v3()
    mutation(identity)

    persist = getattr(store, "persist_process_identity", None)
    assert callable(persist), "WorkRepository must durably store process identities"
    with pytest.raises(ValueError, match="process identity"):
        persist(attempt_id, 1, identity)


def test_read_process_identity_rejects_tampered_persisted_digest(store):
    job = create_job(store)
    work = advance(store, admit(store, job))
    attempt_id = work["attempts"][0]["id"]
    identity = _process_identity_v3()

    persist = getattr(store, "persist_process_identity", None)
    read = getattr(store, "read_process_identity", None)
    assert callable(persist), "WorkRepository must durably store process identities"
    assert callable(read), "WorkRepository must load a process identity after restart"

    persist(attempt_id, 1, identity)
    tampered = dict(identity, monitor_pid=9999)
    with store.transaction() as connection:
        connection.execute("DROP TRIGGER work_process_identities_immutable_update")
        connection.execute(
            "UPDATE work_process_identities SET identity_json=? WHERE attempt_id=?",
            (json.dumps(tampered, sort_keys=True, separators=(",", ":")), attempt_id),
        )
        connection.execute(PROCESS_IDENTITY_SCHEMA[1])
    with pytest.raises(SchemaMismatch, match="process identity"):
        WorkRepository(store.path).read_process_identity(attempt_id, 1)


def test_process_identity_v3_only_persists_for_active_attempts(store):
    job = create_job(store)
    planned = admit(store, job)
    attempt_id = planned["attempts"][0]["id"]
    identity = _process_identity_v3()
    persist = getattr(store, "persist_process_identity", None)
    read = getattr(store, "read_process_identity", None)
    assert callable(persist), "WorkRepository must durably store process identities"
    assert callable(read), "WorkRepository must load a process identity after restart"

    with pytest.raises(WorkConflict, match="active"):
        persist(attempt_id, 1, identity)
    sent = advance(store, planned)
    persist(attempt_id, 1, identity)
    reconciled = advance(store, sent, target="reconcile", event_id="identity-reconcile")
    assert reconciled["attempts"][-1]["state"] == "reconcile"
    with pytest.raises(WorkConflict, match="active"):
        persist(attempt_id, 1, identity)
    assert read(attempt_id, 1) == identity


def test_process_identity_v3_rejects_cancelled_attempt(store):
    job = create_job(store)
    cancelled = advance(store, admit(store, job), target="cancelled")
    attempt_id = cancelled["attempts"][-1]["id"]

    persist = getattr(store, "persist_process_identity", None)
    assert callable(persist), "WorkRepository must durably store process identities"
    with pytest.raises(WorkConflict, match="active"):
        persist(attempt_id, 1, _process_identity_v3())


def test_caller_owned_admission_and_transition_roll_back_together(store):
    job = create_job(store)
    baseline = store.read_events(job["id"])
    with pytest.raises(RuntimeError, match="downstream rejection"):
        with store.transaction() as connection:
            store._verify(connection)
            work = store._admit_work(
                connection,
                job_id=job["id"],
                operation_kind="launch",
                idempotency_key="atomic",
                request_hash="a" * 64,
                contract_id="contract",
                snapshot_id=None,
                provider="mock_cli",
                actor_id="operator",
            )
            store._transition_attempt(
                connection,
                attempt_id=work["attempts"][0]["id"],
                generation=1,
                expected_revision=1,
                expected_state="planned",
                target="sent",
                actor_id="operator",
                event_id="atomic-sent",
                evidence=TransitionEvidence(
                    generation=1,
                    expected_generation=1,
                    contract_confirmed=True,
                    grant_confirmed=True,
                    capacity_confirmed=True,
                    reservations_confirmed=True,
                ),
            )
            assert connection.in_transaction
            raise RuntimeError("downstream rejection")
    assert store.read_events(job["id"]) == baseline
    with store.connection() as connection:
        for table in ("work_items", "work_attempts", "work_transition_receipts"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_changed_payload_conflicts_under_same_idempotency_key(store):
    job = create_job(store)
    first = admit(store, job)
    with pytest.raises(ValueError, match="idempotency"):
        admit(store, job, request_hash=hashlib.sha256(b"different").hexdigest())
    assert store.get_work(first["id"]) == first


def test_changed_contract_or_provider_cannot_hide_behind_payload_hash(store):
    job = create_job(store)
    admit(store, job)
    with pytest.raises(ValueError):
        admit(store, job, contract_id="different")


def test_concurrent_identical_admissions_have_single_identity(store):
    job = create_job(store)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: admit(WorkRepository(store.path), job), range(16)))
    assert len({item["id"] for item in results}) == 1
    assert len({item["attempts"][0]["id"] for item in results}) == 1
    page = store.read_events(job["id"])
    assert [event["event_type"] for event in page["events"]] == ["job.created", "work.admitted"]
    assert [event["sequence"] for event in page["events"]] == [1, 2]


def test_event_insert_failure_rolls_back_admission(store):
    job = create_job(store)
    with sqlite3.connect(store.path) as connection:
        # A constraint failure after INSERT proves transaction rollback, not a mocked call.
        connection.execute("CREATE TEMP TABLE unused (id INTEGER)")
        connection.execute(
            "CREATE TRIGGER fail_event BEFORE INSERT ON work_events BEGIN SELECT RAISE(ABORT,'event unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="event unavailable"):
        admit(store, job)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM work_attempts").fetchone()[0] == 0
        assert connection.execute("SELECT high_water FROM work_event_sequences").fetchone()[0] == 1


def test_foreign_parent_and_disallowed_provider_rejected(store):
    first_job = create_job(store)
    parent = admit(store, first_job)
    other_job = create_job(store)
    with pytest.raises(WorkConflict, match="managed lineage origin required"):
        admit(store, other_job, parent_work_item_id=parent["id"])
    with pytest.raises(ValueError, match="provider"):
        admit(store, other_job, provider="not-allowed")


def test_cursor_pages_and_declared_retention_gap(store):
    job = create_job(store)
    admit(store, job)
    page = store.read_events(job["id"], limit=1)
    assert page["high_water"] == 2 and page["next_cursor"] == 1
    assert page["gaps"] == []
    second = store.read_events(job["id"], after_sequence=1)
    assert [event["sequence"] for event in second["events"]] == [2]
    store.prune_events(job["id"], through_sequence=1)
    after = store.read_events(job["id"])
    assert after["gaps"] == [{"from_sequence": 1, "through_sequence": 1}]
    assert after["next_cursor"] == 2


def test_admission_reverifies_corrupt_schema(store):
    job = create_job(store)
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE work_migrations SET checksum='tampered'")
    with pytest.raises(RuntimeError, match="ledger"):
        admit(store, job)


def advance(store, work, target="sent", **overrides):
    attempt = work["attempts"][-1]
    request = dict(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_revision=attempt["revision"],
        expected_state=attempt["state"],
        target=target,
        actor_id="operator",
        event_id="event-delivery",
        evidence=TransitionEvidence(
            generation=attempt["generation"],
            expected_generation=attempt["generation"],
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
        ),
    )
    request.update(overrides)
    return store.transition_attempt(**request)


def admit_descendant(store, job, parent):
    """Create a real trusted child for repository-only cascade coverage."""
    with store.transaction() as connection:
        store._verify(connection)
        return store._admit_work(
            connection,
            job_id=job["id"],
            operation_kind="child",
            idempotency_key="child-request",
            request_hash=hashlib.sha256(b"child-request").hexdigest(),
            contract_id="child-contract",
            snapshot_id="child-snapshot",
            provider="mock_cli",
            actor_id="operator",
            parent_work_item_id=parent["id"],
        )


def test_transition_and_descendant_cancellation_share_the_durable_commit(store, monkeypatch):
    job = create_job(store)
    root = admit(store, job)
    commits = []
    original = WorkRepository._commit_checked_transition

    def record_commit(self, connection, *, work, attempt, **kwargs):
        commits.append((work["id"], attempt["id"], kwargs["event_type"]))
        return original(self, connection, work=work, attempt=attempt, **kwargs)

    monkeypatch.setattr(WorkRepository, "_commit_checked_transition", record_commit)

    sent = advance(store, root, event_id="root-sent")
    child = admit_descendant(store, job, sent)
    cancelled = advance(store, sent, target="cancelled", event_id="root-cancelled")

    assert cancelled["state"] == "cancelled"
    assert store.get_work(child["id"])["state"] == "cancelled"
    assert commits == [
        (root["id"], root["attempts"][-1]["id"], "attempt.sent"),
        (root["id"], root["attempts"][-1]["id"], "attempt.cancelled"),
        (child["id"], child["attempts"][-1]["id"], "attempt.cancelled"),
    ]


def test_descendant_receipt_failure_rolls_back_the_entire_cancellation(store):
    job = create_job(store)
    root = admit(store, job)
    child = admit_descendant(store, job, root)
    baseline_events = store.read_events(job["id"])
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_descendant_receipt BEFORE INSERT ON "
            "work_transition_receipts WHEN NEW.work_item_id='"
            f"{child['id']}' BEGIN SELECT RAISE(ABORT,'descendant receipt unavailable'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="descendant receipt unavailable"):
        advance(store, root, target="cancelled", event_id="root-cancelled")

    assert store.get_work(root["id"]) == root
    assert store.get_work(child["id"]) == child
    assert store.read_events(job["id"]) == baseline_events
    with store.connection() as connection:
        assert (
            connection.execute("SELECT count(*) FROM work_transition_receipts").fetchone()[0] == 0
        )


def test_attempt_transition_cas_and_event_are_atomic(store):
    job = create_job(store)
    work = admit(store, job)
    sent = advance(store, work)
    assert sent["state"] == "running"
    assert sent["attempts"][0]["state"] == "sent"
    assert sent["attempts"][0]["revision"] == 2
    with pytest.raises(ValueError, match="revision"):
        advance(store, work, event_id="different-event")
    assert [event["event_type"] for event in store.read_events(job["id"])["events"]] == [
        "job.created",
        "work.admitted",
        "attempt.sent",
    ]


def test_event_retry_deduplicates_without_reapplying_state_even_after_retention(store):
    job = create_job(store)
    work = admit(store, job)
    sent = advance(store, work)
    assert advance(store, work) == sent
    store.prune_events(job["id"], through_sequence=3)
    assert advance(store, work) == sent
    assert store.read_events(job["id"])["high_water"] == 3


def test_event_id_reuse_with_changed_transition_conflicts(store):
    job = create_job(store)
    work = admit(store, job)
    advance(store, work)
    with pytest.raises(ValueError, match="event_id"):
        advance(store, work, target="failed")


def test_late_generation_and_expired_lease_do_not_dispatch(store):
    job = create_job(store)
    work = admit(store, job)
    with pytest.raises(ValueError, match="generation"):
        advance(store, work, generation=2)
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE work_attempts SET lease_expires_at=1")
    with pytest.raises(ValueError, match="lease"):
        advance(store, work)
    cancelled = advance(store, work, target="cancelled", event_id="cancel-event")
    assert cancelled["state"] == "cancelled"


def test_cancelled_winner_cannot_be_revived(store):
    job = create_job(store)
    work = admit(store, job)
    cancelled = advance(store, work, target="cancelled")
    with pytest.raises(ValueError):
        advance(store, cancelled, target="sent", event_id="late-event")
    assert store.get_work(work["id"])["state"] == "cancelled"


def test_execution_observation_records_receipt_before_running(store):
    job = create_job(store)
    sent = advance(store, admit(store, job))
    advance(
        store,
        sent,
        target="running",
        event_id="event-running",
        evidence=TransitionEvidence(
            generation=1, expected_generation=1, task_received=True, execution_started=True
        ),
    )
    types = [event["event_type"] for event in store.read_events(job["id"])["events"]]
    assert types[-2:] == ["attempt.acknowledged", "attempt.running"]


def test_transition_failure_rolls_back_both_states_and_event_sequence(store):
    job = create_job(store)
    work = admit(store, job)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_event BEFORE INSERT ON work_events BEGIN SELECT RAISE(ABORT,'event unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        advance(store, work)
    assert store.get_work(work["id"]) == work
    assert store.read_events(job["id"])["high_water"] == 2


def running_work(store, job, **kwargs):
    work = admit(store, job, **kwargs)
    sent = advance(store, work, event_id=f"sent-{work['id']}")
    return advance(
        store,
        sent,
        target="running",
        event_id=f"running-{work['id']}",
        evidence=TransitionEvidence(
            generation=1, expected_generation=1, task_received=True, execution_started=True
        ),
    )


def register_result(store, work, **overrides):
    attempt = work["attempts"][-1]
    digest = hashlib.sha256(b"verified output").hexdigest()
    arguments = dict(
        attempt_id=attempt["id"],
        generation=1,
        content_hash=digest,
        immutable_location=digest,
        byte_length=len(b"verified output"),
        validator_id="contract-validator",
        validation_evidence={"contract_id": work["contract_id"]},
        actor_id="operator",
    )
    arguments.update(overrides)
    return store.register_result(**arguments)


def finish(store, work):
    return advance(
        store,
        work,
        target="finished",
        event_id=f"finished-{work['id']}",
        evidence=TransitionEvidence(
            generation=1,
            expected_generation=1,
            result_durable=True,
            result_validated=True,
            children_settled=True,
        ),
    )


def test_success_atomically_assigns_accepted_result(store):
    job = create_job(store)
    work = running_work(store, job)
    with pytest.raises(ValueError, match="result"):
        finish(store, work)
    result = register_result(store, work)
    assert store.get_work(work["id"])["state"] == "running"
    completed = finish(store, store.get_work(work["id"]))
    assert completed["state"] == "succeeded"
    assert completed["accepted_result_id"] == result["id"]
    assert completed["attempts"][-1]["result_id"] == result["id"]
    assert store.get_result(result["id"]) == result


def test_accepted_result_cannot_be_replaced_and_late_evidence_is_retained(store):
    job = create_job(store)
    work = running_work(store, job)
    first = register_result(store, work)
    finish(store, store.get_work(work["id"]))
    digest = hashlib.sha256(b"late output").hexdigest()
    late = register_result(
        store, work, content_hash=digest, immutable_location=digest, byte_length=11
    )
    assert late["id"] != first["id"]
    assert store.get_result(late["id"])["content_hash"] == digest
    assert store.get_work(work["id"])["accepted_result_id"] == first["id"]
    assert store.get_work(work["id"])["attempts"][-1]["result_id"] == first["id"]


def test_join_checks_actual_children_not_only_callers_assertion(work_store_paths):
    store = legacy_v21_store(work_store_paths.database)
    job = create_job(store)
    parent = legacy_admit(store, job)
    sent = advance(store, parent, event_id=f"sent-{parent['id']}")
    parent = advance(
        store,
        sent,
        target="running",
        event_id=f"running-{parent['id']}",
        evidence=TransitionEvidence(
            generation=1, expected_generation=1, task_received=True, execution_started=True
        ),
    )
    child = legacy_admit(store, job, idempotency_key="child", parent_work_item_id=parent["id"])
    migrate_legacy_v21_store(store)
    register_result(store, parent)
    with pytest.raises(ValueError, match="children"):
        finish(store, store.get_work(parent["id"]))
    assert store.get_work(child["id"])["state"] == "queued"


def test_result_reference_failure_does_not_claim_success(store):
    job = create_job(store)
    work = running_work(store, job)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_event BEFORE INSERT ON work_events BEGIN SELECT RAISE(ABORT,'event unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        register_result(store, work)
    assert store.get_work(work["id"]) == work
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT count(*) FROM work_results").fetchone()[0] == 0


def test_renewal_is_fenced_and_never_revives_expired_or_cancelled_attempt(store):
    job = create_job(store)
    work = running_work(store, job)
    attempt = work["attempts"][-1]
    assert hasattr(store, "renew_attempt"), "lease renewal missing"
    renewed = store.renew_attempt(
        attempt["id"],
        generation=1,
        expected_revision=attempt["revision"],
        lease_seconds=120,
        actor_id="operator",
    )
    assert renewed["attempts"][-1]["lease_expires_at"] > attempt["lease_expires_at"]
    with pytest.raises(ValueError, match="revision"):
        store.renew_attempt(
            attempt["id"],
            generation=1,
            expected_revision=attempt["revision"],
            lease_seconds=120,
            actor_id="operator",
        )
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE work_attempts SET lease_expires_at=1")
    with pytest.raises(ValueError, match="lease"):
        store.renew_attempt(
            attempt["id"],
            generation=1,
            expected_revision=renewed["attempts"][-1]["revision"],
            lease_seconds=120,
            actor_id="operator",
        )


def test_retry_requires_reconciliation_and_preserves_old_generation(store):
    job = create_job(store)
    running = running_work(store, job)
    uncertain = advance(store, running, target="reconcile", event_id="uncertain")
    assert hasattr(store, "retry_work"), "retained attempt history missing"
    with pytest.raises(ValueError):
        store.retry_work(
            uncertain["id"],
            expected_revision=uncertain["revision"],
            provider="mock_cli",
            evidence=TransitionEvidence(generation=1, expected_generation=1),
            actor_id="operator",
            lease_seconds=60,
        )
    retried = store.retry_work(
        uncertain["id"],
        expected_revision=uncertain["revision"],
        provider="mock_cli",
        evidence=TransitionEvidence(
            generation=1, expected_generation=1, prior_stopped=True, reconciliation_authorized=True
        ),
        actor_id="operator",
        lease_seconds=60,
    )
    assert retried["state"] == "queued"
    assert [attempt["generation"] for attempt in retried["attempts"]] == [1, 2]
    assert [attempt["state"] for attempt in retried["attempts"]] == ["reconcile", "planned"]
    with pytest.raises(ValueError, match="generation"):
        advance(store, uncertain, target="cancelled", event_id="old-worker")


def test_expiry_declares_uncertainty_without_auto_replacement(store):
    job = create_job(store)
    running = running_work(store, job)
    assert hasattr(store, "reconcile_expired"), "lease sweep missing"
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE work_attempts SET lease_expires_at=1")
    assert store.reconcile_expired(actor_id="supervisor") == [running["id"]]
    work = store.get_work(running["id"])
    assert work["state"] == "reconcile"
    assert len(work["attempts"]) == 1
    assert store.reconcile_expired(actor_id="supervisor") == []
