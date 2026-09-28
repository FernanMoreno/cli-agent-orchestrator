"""Crash windows around external sends, using real temporary SQLite."""

import importlib
import importlib.util
import sqlite3
import threading

import pytest

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
from test.clients.test_work_repository import (
    admit,
    create_job,
    legacy_admit,
    legacy_v21_store,
    migrate_legacy_v21_store,
)
from test.fixtures.work_store import work_store_paths  # noqa: F401


def service_module():
    name = "cli_agent_orchestrator.services.work_service"
    assert importlib.util.find_spec(name) is not None, "durable dispatch owner missing"
    return importlib.import_module(name)


@pytest.fixture
def prepared(work_store_paths):
    store = WorkRepository(work_store_paths.database)
    store.initialize()
    job = create_job(store)
    work = admit(store, job)
    proof = TransitionEvidence(
        generation=1,
        expected_generation=1,
        contract_confirmed=True,
        grant_confirmed=True,
        capacity_confirmed=True,
        reservations_confirmed=True,
    )
    return store, work, proof


def _running_for_settlement(module, store, work, proof):
    """Seed pre-T094 execution state for settlement-only behavior tests.

    The receipt protocol itself is exercised through real managed lineage below;
    these result/cleanup tests deliberately use the repository's trusted test
    setup so they do not recreate an unauthenticated adapter acknowledgement.
    """
    sent = module.WorkService(store).dispatch(
        work["id"],
        lambda: module.DeliveryObservation(),
        admission=proof,
        actor_id="operator",
    )
    attempt = sent["attempts"][0]
    acknowledged = store.transition_attempt(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_revision=attempt["revision"],
        expected_state="sent",
        target="acknowledged",
        actor_id="trusted-test-receiver",
        event_id="trusted-test-receipt",
        evidence=TransitionEvidence(
            generation=attempt["generation"],
            expected_generation=attempt["generation"],
            task_received=True,
        ),
    )
    attempt = acknowledged["attempts"][0]
    return store.transition_attempt(
        attempt_id=attempt["id"],
        generation=attempt["generation"],
        expected_revision=attempt["revision"],
        expected_state="acknowledged",
        target="running",
        actor_id="trusted-test-receiver",
        event_id="trusted-test-execution",
        evidence=TransitionEvidence(
            generation=attempt["generation"],
            expected_generation=attempt["generation"],
            execution_started=True,
        ),
    )


def test_intent_is_committed_before_send_without_holding_database_lock(prepared):
    module = service_module()
    store, work, proof = prepared
    calls = []

    def send():
        with sqlite3.connect(store.path, timeout=0.1) as connection:
            connection.execute("BEGIN IMMEDIATE")
            assert connection.execute("SELECT state FROM work_attempts").fetchone()[0] == "sent"
        calls.append("sent")
        return module.DeliveryObservation(task_received=True, execution_started=True)

    result = module.WorkService(store).dispatch(
        work["id"], send, admission=proof, actor_id="operator"
    )
    assert calls == ["sent"]
    assert result["attempts"][0]["state"] == "sent"


def test_direct_work_service_dispatch_registers_its_writer_for_the_effect(prepared):
    module = service_module()
    store, work, proof = prepared

    def send():
        with sqlite3.connect(store.path) as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM work_registered_writers WHERE state='active'"
                ).fetchone()[0]
                == 1
            )
        return module.DeliveryObservation()

    module.WorkService(store).dispatch(work["id"], send, admission=proof, actor_id="operator")
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT state FROM work_registered_writers").fetchall() == [
            ("released",)
        ]


def test_sent_intent_cannot_cross_a_real_offline_cut_before_writer_registration(
    prepared, monkeypatch
):
    """A cut opened after sent makes the real registry reject before any delivery or ACK."""
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.work_authority import WorkAuthority

    module = service_module()
    store, work, proof = prepared
    service = module.WorkService(store)
    operator = auth._verified_principal("https://cut.test", "sent-race", [auth.SCOPE_ADMIN], "jwt")
    authority = WorkAuthority(WorkRepository(store.path))
    entered_after_sent = threading.Event()
    release_registration = threading.Event()
    dispatch_result = {}
    observed = []
    calls = []
    original_send_committed = service._send_committed

    def pause_after_sent(sent, send, *, actor_id):
        observed.append("sent-before-register")
        entered_after_sent.set()
        if not release_registration.wait(timeout=5):
            raise TimeoutError("test did not release writer registration")
        return original_send_committed(sent, send, actor_id=actor_id)

    def dispatch_from_real_api():
        try:
            dispatch_result["work"] = service.dispatch(
                work["id"],
                lambda: calls.append("send") or module.DeliveryObservation(),
                admission=proof,
                actor_id="operator",
            )
        except BaseException as error:  # retain errors across the deterministic thread boundary
            dispatch_result["error"] = error

    monkeypatch.setattr(service, "_send_committed", pause_after_sent)
    dispatch_thread = threading.Thread(target=dispatch_from_real_api, name="sent-registration")
    try:
        dispatch_thread.start()
        assert entered_after_sent.wait(timeout=5)
        lease = authority.create_offline_cut(operator, ttl_seconds=60)
        observed.append("cut-open")
        assert observed == ["sent-before-register", "cut-open"]
    finally:
        release_registration.set()
        dispatch_thread.join(timeout=5)

    assert not dispatch_thread.is_alive()
    assert isinstance(dispatch_result.get("error"), module.DeliveryUncertain)
    assert calls == []
    current = store.get_work(work["id"])
    assert current["state"] == "reconcile"
    assert current["attempts"][-1]["state"] == "reconcile"
    assert "attempt.acknowledged" not in [
        event["event_type"] for event in store.read_events(work["job_id"])["events"]
    ]
    assert lease.id


def test_persistence_failure_prevents_external_effect(prepared):
    module = service_module()
    store, work, proof = prepared
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_event BEFORE INSERT ON work_events BEGIN SELECT RAISE(ABORT,'event unavailable'); END"
        )
    calls = []
    with pytest.raises(sqlite3.IntegrityError):
        module.WorkService(store).dispatch(
            work["id"], lambda: calls.append("sent"), admission=proof, actor_id="operator"
        )
    assert calls == []
    assert store.get_work(work["id"])["attempts"][0]["state"] == "planned"


def test_uncertain_send_is_not_repeated_after_restart(prepared):
    module = service_module()
    store, work, proof = prepared
    calls = []

    def send():
        calls.append("sent")
        raise TimeoutError("provider may have received input")

    with pytest.raises(module.DeliveryUncertain):
        module.WorkService(store).dispatch(work["id"], send, admission=proof, actor_id="operator")
    assert store.get_work(work["id"])["state"] == "reconcile"
    module.WorkService(WorkRepository(store.path)).dispatch(
        work["id"], send, admission=proof, actor_id="operator"
    )
    assert calls == ["sent"]


def test_process_death_after_intent_is_not_replayed(prepared):
    module = service_module()
    store, work, proof = prepared
    calls = []

    def killed_send():
        calls.append("possibly sent")
        raise SystemExit("simulated process death")

    with pytest.raises(SystemExit):
        module.WorkService(store).dispatch(
            work["id"], killed_send, admission=proof, actor_id="operator"
        )
    assert store.get_work(work["id"])["attempts"][0]["state"] == "sent"
    module.WorkService(WorkRepository(store.path)).dispatch(
        work["id"], killed_send, admission=proof, actor_id="operator"
    )
    assert calls == ["possibly sent"]


def test_allocation_without_task_receipt_stays_sent(prepared):
    module = service_module()
    store, work, proof = prepared
    result = module.WorkService(store).dispatch(
        work["id"], lambda: module.DeliveryObservation(), admission=proof, actor_id="operator"
    )
    assert result["attempts"][0]["state"] == "sent"
    assert "attempt.acknowledged" not in [
        e["event_type"] for e in store.read_events(work["job_id"])["events"]
    ]


def test_boolean_task_received_observation_never_acknowledges_work(prepared):
    """An adapter's unauthenticated observation cannot advance durable Work."""
    module = service_module()
    store, work, proof = prepared

    result = module.WorkService(store).dispatch(
        work["id"],
        lambda: module.DeliveryObservation(task_received=True, execution_started=True),
        admission=proof,
        actor_id="untrusted-adapter",
    )

    assert result["attempts"][0]["state"] == "sent"
    assert "attempt.acknowledged" not in [
        event["event_type"] for event in store.read_events(work["job_id"])["events"]
    ]


def test_failed_admission_does_not_send(prepared):
    module = service_module()
    store, work, _ = prepared
    calls = []
    with pytest.raises(ValueError):
        module.WorkService(store).dispatch(
            work["id"],
            lambda: calls.append("sent"),
            admission=TransitionEvidence(generation=1, expected_generation=1),
            actor_id="operator",
        )
    assert calls == []


def test_settlement_persists_retrievable_result_before_success(prepared, work_store_paths):
    module = service_module()
    assert hasattr(module.WorkService, "settle_attempt"), "durable settlement missing"
    from cli_agent_orchestrator.services.step_output_store import ImmutableResultStore

    store, work, proof = prepared
    artifacts = ImmutableResultStore(work_store_paths.artifacts)
    service = module.WorkService(store)
    _running_for_settlement(module, store, work, proof)
    completed = service.settle_attempt(
        work["id"],
        generation=1,
        content=b"verified output",
        artifacts=artifacts,
        validate=lambda content: {"valid": content == b"verified output"},
        validator_id="test-validator",
        actor_id="operator",
    )
    assert completed["state"] == "succeeded"
    reopened = module.WorkService(WorkRepository(store.path))
    assert reopened.read_result(work["id"], artifacts=artifacts) == b"verified output"


def test_result_validation_failure_never_accepts_result(prepared, work_store_paths):
    module = service_module()
    assert hasattr(module.WorkService, "settle_attempt"), "durable settlement missing"
    from cli_agent_orchestrator.services.step_output_store import ImmutableResultStore

    store, work, proof = prepared
    service = module.WorkService(store)
    _running_for_settlement(module, store, work, proof)

    def reject(content):
        raise ValueError("result does not match contract")

    with pytest.raises(ValueError, match="does not match"):
        service.settle_attempt(
            work["id"],
            generation=1,
            content=b"invalid",
            artifacts=ImmutableResultStore(work_store_paths.artifacts),
            validate=reject,
            validator_id="test-validator",
            actor_id="operator",
        )
    assert store.get_work(work["id"])["accepted_result_id"] is None


def test_result_persistence_failure_leaves_orphan_not_success(prepared, work_store_paths):
    module = service_module()
    assert hasattr(module.WorkService, "settle_attempt"), "durable settlement missing"
    from cli_agent_orchestrator.services.step_output_store import ImmutableResultStore

    store, work, proof = prepared
    service = module.WorkService(store)
    _running_for_settlement(module, store, work, proof)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_event BEFORE INSERT ON work_events BEGIN SELECT RAISE(ABORT,'event unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        service.settle_attempt(
            work["id"],
            generation=1,
            content=b"verified output",
            artifacts=ImmutableResultStore(work_store_paths.artifacts),
            validate=lambda content: {"valid": True},
            validator_id="test-validator",
            actor_id="operator",
        )
    assert store.get_work(work["id"])["state"] == "running"
    assert store.referenced_artifact_hashes() == set()
    assert any(
        path.name == "eecf56ab92af6fc765c7564f294a6f24b1cd2b4852b0c42d636d77d1501fb664"
        for path in work_store_paths.artifacts.iterdir()
    )


def test_cancelled_work_retains_late_result_without_claiming_success(prepared, work_store_paths):
    module = service_module()
    assert hasattr(module.WorkService, "settle_attempt"), "durable settlement missing"
    from cli_agent_orchestrator.services.step_output_store import ImmutableResultStore
    from test.clients.test_work_repository import advance

    store, work, proof = prepared
    service = module.WorkService(store)
    running = _running_for_settlement(module, store, work, proof)
    advance(store, running, target="cancelled", event_id="cancel")
    result = service.settle_attempt(
        work["id"],
        generation=1,
        content=b"late",
        artifacts=ImmutableResultStore(work_store_paths.artifacts),
        validate=lambda content: {"valid": True},
        validator_id="test-validator",
        actor_id="operator",
    )
    assert result["state"] == "cancelled" and result["accepted_result_id"] is None
    assert len(store.referenced_artifact_hashes()) == 1


def test_join_reports_expired_child_reconcile_instead_of_waiting_forever(work_store_paths):
    module = service_module()
    store = legacy_v21_store(work_store_paths.database)
    job = create_job(store)
    work = legacy_admit(store, job)
    child = legacy_admit(store, job, idempotency_key="child", parent_work_item_id=work["id"])
    migrate_legacy_v21_store(store)
    from test.clients.test_work_repository import advance

    advance(store, child, event_id="child-sent")
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE work_attempts SET lease_expires_at=1 WHERE work_item_id=?", (child["id"],)
        )
    assert hasattr(module.WorkService, "join_children"), "durable join missing"
    joined = module.WorkService(store).join_children(
        work["id"], actor_id="operator", timeout_seconds=0
    )
    assert joined["settled"] is True
    assert joined["successful"] is False
    assert joined["children"][0]["state"] == "reconcile"
    assert joined["required_action"] == "reconcile_children"
    assert store.get_work(work["id"])["state"] == "queued"


def test_cleanup_failure_is_separate_from_cancelled_work(prepared):
    module = service_module()
    store, work, proof = prepared
    service = module.WorkService(store)
    running = _running_for_settlement(module, store, work, proof)
    from test.clients.test_work_repository import advance

    cancelled = advance(store, running, target="cancelled", event_id="cancel")
    assert hasattr(service, "cleanup_attempt"), "separate cleanup lifecycle missing"
    observed = []

    def fail_cleanup():
        observed.append(store.get_work(work["id"])["attempts"][-1]["cleanup_state"])
        raise RuntimeError("backend unavailable")

    result = service.cleanup_attempt(
        cancelled["attempts"][-1]["id"], fail_cleanup, actor_id="operator"
    )
    assert observed == ["pending"]
    assert result["state"] == "cancelled"
    assert result["attempts"][-1]["cleanup_state"] == "failed"
    recovered = service.cleanup_attempt(
        cancelled["attempts"][-1]["id"], lambda: True, actor_id="operator"
    )
    assert (
        recovered["state"] == "cancelled"
        and recovered["attempts"][-1]["cleanup_state"] == "complete"
    )


def test_cleanup_does_not_destroy_an_active_attempt_without_cancellation(prepared):
    module = service_module()
    store, work, _ = prepared
    service = module.WorkService(store)
    assert hasattr(service, "cleanup_attempt"), "separate cleanup lifecycle missing"
    calls = []
    with pytest.raises(ValueError, match="active"):
        service.cleanup_attempt(
            work["attempts"][-1]["id"], lambda: calls.append("destroyed"), actor_id="operator"
        )
    assert calls == []
