"""PostgreSQL ledger regressions for authenticated Work agent messages."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = pytest.mark.postgres


@pytest.fixture
def work_message_context(postgres_targets, tmp_path):
    """A native Work store with a root-authorized pair of live attempt credentials."""
    from cli_agent_orchestrator.clients.database import core_migrations
    from cli_agent_orchestrator.clients.postgres.migrations import migrate_component
    from cli_agent_orchestrator.clients.private_plan_postgres_schema import PRIVATE_PLAN_MIGRATIONS
    from cli_agent_orchestrator.clients.work_continuation_postgres_schema import (
        WORKFLOW_CONTINUATION_MIGRATIONS,
    )
    from cli_agent_orchestrator.clients.work_postgres_schema import WORK_POSTGRES_MIGRATIONS
    from cli_agent_orchestrator.clients.work_repository import WorkRepository, bootstrap_work_store
    from cli_agent_orchestrator.clients.workflows_postgres_schema import WORKFLOW_MIGRATIONS
    from cli_agent_orchestrator.models.work_contract import (
        ContractPermissions,
        ContractResources,
        ContractSnapshot,
        EffectiveWorkContract,
    )
    from cli_agent_orchestrator.security import auth
    from cli_agent_orchestrator.services.delegation_snapshot import (
        DelegationSnapshots,
        ResolvedSnapshot,
    )
    from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
    from cli_agent_orchestrator.services.work_attempt_credential import WorkAttemptCredentials
    from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
    from cli_agent_orchestrator.services.work_contract import WorkContracts
    from cli_agent_orchestrator.services.work_origin import WorkOrigins
    from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
    from cli_agent_orchestrator.services.work_scheduler import WorkScheduler

    target = postgres_targets.main
    migrate_component(target, core_migrations())
    migrate_component(target, WORK_POSTGRES_MIGRATIONS[:1])
    migrate_component(target, WORKFLOW_MIGRATIONS)
    migrate_component(target, PRIVATE_PLAN_MIGRATIONS)
    migrate_component(target, WORKFLOW_CONTINUATION_MIGRATIONS)
    migrate_component(target, WORK_POSTGRES_MIGRATIONS)
    bootstrap_work_store(target)

    repository = WorkRepository(target, filesystem_home=postgres_targets.home)
    repository.initialize()
    owner = auth._verified_principal(
        "https://issuer.test", "work-message-ledger-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    authority = WorkAuthority(repository)
    job = repository.create_job(
        project_id="work-message-ledger-project",
        principal_id=owner.id,
        allowed_providers=["mock_cli"],
        grant_id="work-message-ledger-root",
        budget={"scheduler_units": 100},
    )
    grant = authority.issue_root(
        owner,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"cao.work.message", "knowledge.read"}, paths={str(tmp_path)}
        ),
        expires_at=time.time() + 1800,
    )
    scheduler = WorkScheduler(repository)
    scheduler.configure(capacity=8, max_queue=100, aging_seconds=10, expected_policy_revision=0)

    def freeze_snapshot(contract_id: str, work_grant):
        return DelegationSnapshots(
            repository,
            policy=KnowledgePolicy(repository, job["id"], work_grant.id, work_grant.revision),
        ).freeze(
            principal=owner,
            job_id=job["id"],
            contract_id=contract_id,
            binding_key=f"{contract_id}-snapshot",
            request_hash=hashlib.sha256(contract_id.encode("utf-8")).hexdigest(),
            scope="project",
            scope_id="work-message-ledger-project",
            resolver=lambda _connection, _principal: ResolvedSnapshot("message ledger context"),
        )

    from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
    from cli_agent_orchestrator.services.work_admission import WorkAdmission
    from cli_agent_orchestrator.services.work_service import DeliveryObservation, WorkService

    class _LedgerBackend(TmuxBackend):
        def preflight_work(self, _restriction):
            return None

        def create_session(self, *_args, **_kwargs):  # pragma: no cover - failure guard
            raise AssertionError("message ledger setup must not start a worker")

    origins = WorkOrigins(repository)
    admission = WorkAdmission(
        repository,
        backends={"ledger-test": _LedgerBackend()},
        origins=origins,
    )
    credentials = WorkAttemptCredentials(repository, origins=origins)
    service_module = __import__(
        "cli_agent_orchestrator.services.work_message_service",
        fromlist=["WorkMessageService"],
    )
    messages = service_module.WorkMessageService(repository, origins=origins)

    def make_work(label: str, *, message_permission: bool = True, work_grant=None):
        selected_grant = grant if work_grant is None else work_grant
        contract_id = f"work-message-ledger-{label}"
        snapshot = freeze_snapshot(contract_id, selected_grant)
        contract = EffectiveWorkContract(
            id=contract_id,
            operation_kind="inbox",
            provider="mock_cli",
            backend="ledger-test",
            permissions=ContractPermissions(
                tools=("cao.work.message",) if message_permission else (),
                paths=(str(tmp_path),),
            ),
            resources=ContractResources(
                checkout_root=str(tmp_path), write_paths=(str(tmp_path),), units=1
            ),
            snapshot=ContractSnapshot(
                state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
            ),
        )
        work = admission.admit(
            principal=owner,
            job_id=job["id"],
            idempotency_key=f"work-message-ledger-{label}",
            request_hash=(label.encode("utf-8").hex() + "0" * 64)[:64],
            grant_id=selected_grant.id,
            expected_grant_revision=selected_grant.revision,
            contract=contract,
            lease_seconds=900,
        )
        attempt = work["attempts"][-1]
        reservation = scheduler.claim_next(actor_id=owner.id)
        if reservation is None or reservation.attempt_id != attempt["id"]:
            raise AssertionError("the ledger fixture could not claim its exact Work attempt")
        sent = WorkService(repository, origins=origins).dispatch(
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
        sent_attempt = sent["attempts"][-1]
        with repository.transaction() as connection:
            binding = WorkContracts(repository)._revalidate_order(
                connection, sent_attempt["id"], generation=sent_attempt["generation"]
            )
            issued = credentials.issue_in_transaction(
                connection,
                binding,
                expected_attempt_revision=sent_attempt["revision"],
            )
        return SimpleNamespace(
            work=sent,
            attempt=sent_attempt,
            credential=issued.secret,
            receiver_credential=issued.receiver_secret,
            contract=contract,
        )

    def plan_work(label: str, *, work_grant=None):
        selected_grant = grant if work_grant is None else work_grant
        contract_id = f"work-message-ledger-{label}"
        snapshot = freeze_snapshot(contract_id, selected_grant)
        contract = EffectiveWorkContract(
            id=contract_id,
            operation_kind="inbox",
            provider="mock_cli",
            backend="ledger-test",
            permissions=ContractPermissions(
                tools=("cao.work.message",),
                paths=(str(tmp_path),),
            ),
            resources=ContractResources(
                checkout_root=str(tmp_path), write_paths=(str(tmp_path),), units=1
            ),
            snapshot=ContractSnapshot(
                state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
            ),
        )
        work = admission.admit(
            principal=owner,
            job_id=job["id"],
            idempotency_key=f"work-message-ledger-{label}",
            request_hash=(label.encode("utf-8").hex() + "0" * 64)[:64],
            grant_id=selected_grant.id,
            expected_grant_revision=selected_grant.revision,
            contract=contract,
            lease_seconds=900,
        )
        attempt = work["attempts"][-1]
        return SimpleNamespace(work=work, attempt=attempt, contract=contract)

    return SimpleNamespace(
        repository=repository,
        target=target,
        owner=owner,
        job=job,
        grant=grant,
        tmp_path=tmp_path,
        origins=origins,
        credentials=credentials,
        messages=messages,
        admission=admission,
        make_work=make_work,
        plan_work=plan_work,
    )


def _value(result, name):
    """Read a strict result model without weakening its asserted fields."""
    return getattr(result, name)


def _domain_rejection():
    """Existing Work boundary errors are ValueError or PermissionError subclasses."""
    return pytest.raises((ValueError, PermissionError))


def _message_rows(context):
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.clients.work_postgres_schema import WORK_POSTGRES_MIGRATIONS

    message_tables = {
        table
        for migration in WORK_POSTGRES_MIGRATIONS
        for table in migration.tables
        if "message" in table
    }
    with connect(context.target, readonly=True) as connection:
        return {
            table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            for table in sorted(message_tables)
        }


def _message_row_count(context):
    return _message_rows(context)[_message_data_table(context)]


def _message_data_table(context):
    from cli_agent_orchestrator.clients.work_postgres_schema import WORK_POSTGRES_MIGRATIONS

    candidates = [
        table
        for migration in WORK_POSTGRES_MIGRATIONS
        for table in migration.tables
        if "message" in table and "idempotency" not in table and "ack" not in table
    ]
    assert len(candidates) == 1, "expected one durable Work message table"
    return candidates[0]


def _message_event_rows(context):
    with context.repository.read_snapshot() as connection:
        return connection.execute(
            "SELECT event_id,event_type,actor_id,work_item_id,attempt_id,metadata "
            "FROM work_events WHERE job_id=%s AND event_type LIKE 'work.message.%%' "
            "ORDER BY sequence",
            (context.job["id"],),
        ).fetchall()


def _send(context, sender, receiver, key, body):
    return context.messages.execute(
        sender.credential,
        {
            "operation": "send",
            "receiver_work_item_id": receiver.work["id"],
            "idempotency_key": key,
            "body": body,
        },
    )


def _receive(context, receiver, *, limit=1):
    return context.messages.execute(
        receiver.credential,
        {"operation": "receive", "limit": limit},
    )


def _ack(context, receiver, message_id):
    return context.messages.execute(
        receiver.credential,
        {"operation": "ack", "message_id": message_id},
    )


def test_same_job_send_receive_ack_is_durable_and_body_free_in_work_events(work_message_context):
    context = work_message_context
    sender = context.make_work("sender")
    receiver = context.make_work("receiver")
    body = "Análisis del agente: mañana 🧭"

    sent = _send(context, sender, receiver, "utf8-ledger-1", body)
    received = _receive(context, receiver)

    assert _value(sent, "status") == "sent"
    message = _value(received, "messages")[0]
    assert message.message_id == _value(sent, "message_id")
    assert message.sender_work_item_id == sender.work["id"]
    assert message.body == body
    assert len(_message_event_rows(context)) == 1
    assert _message_row_count(context) == 1
    event_metadata = json.dumps(_message_event_rows(context)[0]["metadata"], ensure_ascii=False)
    assert body not in event_metadata
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() not in event_metadata

    acknowledged = _ack(context, receiver, message.message_id)
    repeated = _ack(context, receiver, message.message_id)
    assert _value(acknowledged, "status") == "acknowledged"
    assert _value(repeated, "status") == "already_acknowledged"
    assert len(_message_event_rows(context)) == 2
    assert _message_row_count(context) == 1
    assert _value(_receive(context, receiver), "messages") == []


def test_exact_send_replay_by_new_sender_generation_precedes_receiver_liveness(
    work_message_context,
):
    from cli_agent_orchestrator.clients.postgres import connect
    from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
    from cli_agent_orchestrator.services.work_reservations import StoppedWriter
    from cli_agent_orchestrator.services.work_scheduler import WorkScheduler
    from cli_agent_orchestrator.services.work_service import DeliveryObservation, WorkService

    context = work_message_context
    sender = context.make_work("replay-sender")
    receiver = context.make_work("replay-receiver")
    original = _send(context, sender, receiver, "stable-send-1", "exact body")
    rows_before = _message_rows(context)
    events_before = _message_event_rows(context)

    # The original receiver credential loses its lease while the committed message remains.
    with connect(context.target) as connection:
        with connection.transaction():
            connection.execute(
                "UPDATE work_attempts SET lease_expires_at=%s WHERE id=%s AND generation=%s",
                (time.time() - 1, receiver.attempt["id"], receiver.attempt["generation"]),
            )

    scheduler = WorkScheduler(
        context.repository,
        stop_verifier=lambda writer: StoppedWriter(
            writer.attempt_id,
            writer.generation,
            writer.revision,
            "test-confirmed-stop",
        ),
    )
    running = context.repository.transition_attempt(
        attempt_id=sender.attempt["id"],
        generation=sender.attempt["generation"],
        expected_revision=sender.attempt["revision"],
        expected_state="sent",
        target="running",
        actor_id=context.owner.id,
        event_id=uuid4().hex,
        evidence=TransitionEvidence(
            generation=sender.attempt["generation"],
            expected_generation=sender.attempt["generation"],
            contract_confirmed=True,
            grant_confirmed=True,
            capacity_confirmed=True,
            reservations_confirmed=True,
            task_received=True,
            execution_started=True,
        ),
    )
    current_attempt = running["attempts"][-1]
    reconciled = context.repository.transition_attempt(
        attempt_id=current_attempt["id"],
        generation=current_attempt["generation"],
        expected_revision=current_attempt["revision"],
        expected_state="running",
        target="reconcile",
        actor_id=context.owner.id,
        event_id=uuid4().hex,
        evidence=TransitionEvidence(
            generation=current_attempt["generation"],
            expected_generation=current_attempt["generation"],
        ),
    )
    replacement = WorkService(context.repository).retry_work(
        sender.work["id"],
        scheduler=scheduler,
        expected_revision=reconciled["revision"],
        provider="mock_cli",
        evidence=TransitionEvidence(
            generation=current_attempt["generation"],
            expected_generation=current_attempt["generation"],
            reconciliation_authorized=True,
        ),
        actor_id=context.owner.id,
        lease_seconds=900,
    )
    next_attempt = replacement["attempts"][-1]
    assert next_attempt["generation"] == sender.attempt["generation"] + 1

    from cli_agent_orchestrator.services.work_contract import WorkContracts

    with context.repository.transaction() as connection:
        WorkContracts(context.repository)._bind(
            connection,
            principal=context.owner,
            attempt_id=next_attempt["id"],
            generation=next_attempt["generation"],
            expected_attempt_revision=next_attempt["revision"],
            grant_id=context.grant.id,
            expected_grant_revision=context.grant.revision,
            contract=sender.contract,
        )
    scheduler.enqueue(
        attempt_id=next_attempt["id"],
        generation=next_attempt["generation"],
        expected_attempt_revision=next_attempt["revision"],
        units=sender.contract.resources.units,
        actor_id=context.owner.id,
    )
    reservation = scheduler.claim_next(actor_id=context.owner.id)
    assert reservation is not None and reservation.attempt_id == next_attempt["id"]

    # The replacement attempt gets an independent current authorization.
    WorkService(context.repository).dispatch(
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
        actor_id=context.owner.id,
    )
    next_attempt = context.repository.get_work(sender.work["id"])["attempts"][-1]
    with context.repository.transaction() as connection:
        binding = WorkContracts(context.repository)._revalidate_order(
            connection, next_attempt["id"], generation=next_attempt["generation"]
        )
        issued = context.credentials.issue_in_transaction(
            connection, binding, expected_attempt_revision=next_attempt["revision"]
        )

    replay = context.messages.execute(
        issued.secret,
        {
            "operation": "send",
            "receiver_work_item_id": receiver.work["id"],
            "idempotency_key": "stable-send-1",
            "body": "exact body",
        },
    )
    assert _value(replay, "status") == "replayed"
    assert _value(replay, "message_id") == _value(original, "message_id")
    assert _message_rows(context) == rows_before
    assert _message_event_rows(context) == events_before

    from cli_agent_orchestrator.services.work_attempt_credential import (
        WorkAttemptCredentialRejected,
    )

    with pytest.raises(WorkAttemptCredentialRejected):
        _send(context, sender, receiver, "stale-generation-key", "must be denied")
    assert _message_rows(context) == rows_before
    assert _message_event_rows(context) == events_before


@pytest.mark.parametrize(
    "replacement",
    [
        {"receiver_work_item_id": "changed-target"},
        {"body": "changed body"},
    ],
    ids=("changed-receiver", "changed-body"),
)
def test_same_sender_key_conflicts_when_receiver_or_body_changes(work_message_context, replacement):
    context = work_message_context
    sender = context.make_work("conflict-sender")
    receiver = context.make_work("conflict-receiver")
    _send(context, sender, receiver, "stable-conflict-1", "original")
    before_rows = _message_rows(context)
    before_events = _message_event_rows(context)
    args = {
        "operation": "send",
        "receiver_work_item_id": receiver.work["id"],
        "idempotency_key": "stable-conflict-1",
        "body": "original",
    }
    args.update(replacement)

    with _domain_rejection():
        context.messages.execute(sender.credential, args)

    assert _message_rows(context) == before_rows
    assert _message_event_rows(context) == before_events


@pytest.mark.parametrize("body", ["bad\x00body", "x" * 4097], ids=("nul", "oversize"))
def test_invalid_message_body_never_writes_a_message_or_event(work_message_context, body):
    context = work_message_context
    sender = context.make_work("invalid-sender")
    receiver = context.make_work("invalid-receiver")
    before_rows = _message_rows(context)
    before_events = _message_event_rows(context)

    with _domain_rejection():
        _send(context, sender, receiver, "invalid-body-1", body)

    assert _message_rows(context) == before_rows
    assert _message_event_rows(context) == before_events


def test_message_row_and_work_event_roll_back_together(work_message_context, monkeypatch):
    context = work_message_context
    sender = context.make_work("atomic-sender")
    receiver = context.make_work("atomic-receiver")
    before_rows = _message_rows(context)
    before_events = _message_event_rows(context)
    original_append = context.repository._append_event

    def fail_message_event(connection, **kwargs):
        if kwargs.get("event_type") == "work.message.sent":
            raise RuntimeError("injected event append failure")
        return original_append(connection, **kwargs)

    monkeypatch.setattr(context.repository, "_append_event", fail_message_event)
    with pytest.raises(RuntimeError, match="injected event append failure"):
        _send(context, sender, receiver, "atomic-send-1", "atomic body")

    assert _message_rows(context) == before_rows
    assert _message_event_rows(context) == before_events


def test_receive_returns_oldest_pending_messages_by_created_at_then_message_id(
    work_message_context, monkeypatch
):
    context = work_message_context
    sender = context.make_work("order-sender")
    receiver = context.make_work("order-receiver")
    timestamp = time.time()
    created_at = iter((timestamp + 0.002, timestamp, timestamp))
    monkeypatch.setattr(context.messages, "_clock", lambda: next(created_at))
    first = _send(context, sender, receiver, "order-1", "first")
    second = _send(context, sender, receiver, "order-2", "second")
    third = _send(context, sender, receiver, "order-3", "third")

    sent_ids = [
        _value(first, "message_id"),
        _value(second, "message_id"),
        _value(third, "message_id"),
    ]

    page = _value(_receive(context, receiver, limit=2), "messages")
    expected_tie_order = sorted(sent_ids[1:])
    assert [item.message_id for item in page] == expected_tie_order
    assert [item.body for item in page] == [
        "second" if item_id == sent_ids[1] else "third" for item_id in expected_tie_order
    ]


def test_stale_sender_and_wrong_receiver_cannot_read_or_ack_mailbox(
    work_message_context,
):
    from cli_agent_orchestrator.services.work_attempt_credential import (
        WorkAttemptCredentialRejected,
    )
    from cli_agent_orchestrator.services.work_reducer import TransitionEvidence

    context = work_message_context
    sender = context.make_work("scope-sender")
    receiver = context.make_work("scope-receiver")
    unrelated_receiver = context.make_work("scope-other-receiver")
    sent = _send(context, sender, receiver, "scope-send-1", "receiver only")
    message_id = _value(sent, "message_id")

    wrong_page = _value(_receive(context, unrelated_receiver), "messages")
    assert wrong_page == []
    before_events = _message_event_rows(context)
    with _domain_rejection():
        _ack(context, unrelated_receiver, message_id)
    assert _message_event_rows(context) == before_events

    stale_sender = sender.attempt
    context.repository.transition_attempt(
        attempt_id=stale_sender["id"],
        generation=stale_sender["generation"],
        expected_revision=stale_sender["revision"],
        expected_state="sent",
        target="failed",
        actor_id=context.owner.id,
        event_id=uuid4().hex,
        evidence=TransitionEvidence(
            generation=stale_sender["generation"],
            expected_generation=stale_sender["generation"],
            process_exit_code=1,
            process_stopped=True,
            container_removed=True,
            image_removed=True,
        ),
    )
    with pytest.raises(WorkAttemptCredentialRejected):
        context.messages.execute(
            sender.credential,
            {
                "operation": "send",
                "receiver_work_item_id": receiver.work["id"],
                "idempotency_key": "scope-stale-1",
                "body": "stale sender",
            },
        )
    assert _message_rows(context)


def test_sender_and_receiver_contract_permission_is_required(work_message_context):
    context = work_message_context
    unauthorized_sender = context.make_work("no-send-permission", message_permission=False)
    authorized_sender = context.make_work("permission-sender")
    unauthorized_receiver = context.make_work("no-receive-permission", message_permission=False)
    authorized_receiver = context.make_work("permission-receiver")

    before_rows = _message_rows(context)
    before_events = _message_event_rows(context)
    with _domain_rejection():
        _send(context, unauthorized_sender, authorized_receiver, "no-sender-permission", "x")
    with _domain_rejection():
        _send(context, authorized_sender, unauthorized_receiver, "no-receiver-permission", "x")
    assert _message_rows(context) == before_rows
    assert _message_event_rows(context) == before_events


def test_concurrent_exact_sends_create_one_message_and_event(work_message_context):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.services.work_message_service import WorkMessageService
    from cli_agent_orchestrator.services.work_origin import WorkOrigins

    context = work_message_context
    sender = context.make_work("concurrent-sender")
    receiver = context.make_work("concurrent-receiver")
    second_repository = WorkRepository(
        context.target, filesystem_home=context.repository.filesystem_home
    )
    second_repository.initialize()
    second_service = WorkMessageService(second_repository, origins=WorkOrigins(second_repository))
    barrier = threading.Barrier(2)

    def send_once(service):
        barrier.wait(timeout=10)
        return service.execute(
            sender.credential,
            {
                "operation": "send",
                "receiver_work_item_id": receiver.work["id"],
                "idempotency_key": "concurrent-stable-key",
                "body": "same body",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send_once, (context.messages, second_service)))

    assert len({_value(result, "message_id") for result in results}) == 1
    assert sorted(_value(result, "status") for result in results) == ["replayed", "sent"]
    assert _message_row_count(context) == 1
    assert len(_message_event_rows(context)) == 1


def test_concurrent_conflicting_sends_share_one_key_but_one_body_wins(work_message_context):
    from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
    from cli_agent_orchestrator.services.work_message_service import WorkMessageService
    from cli_agent_orchestrator.services.work_origin import WorkOrigins

    context = work_message_context
    sender = context.make_work("concurrent-conflict-sender")
    receiver = context.make_work("concurrent-conflict-receiver")
    second_repository = WorkRepository(
        context.target, filesystem_home=context.repository.filesystem_home
    )
    second_repository.initialize()
    second_service = WorkMessageService(second_repository, origins=WorkOrigins(second_repository))
    services = (context.messages, second_service)
    bodies = ("first competing body", "second competing body")
    barrier = threading.Barrier(2)

    def send_variant(index):
        barrier.wait(timeout=10)
        try:
            return services[index].execute(
                sender.credential,
                {
                    "operation": "send",
                    "receiver_work_item_id": receiver.work["id"],
                    "idempotency_key": "concurrent-conflicting-key",
                    "body": bodies[index],
                },
            )
        except WorkConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send_variant, range(2)))

    successes = [result for result in results if not isinstance(result, WorkConflict)]
    conflicts = [result for result in results if isinstance(result, WorkConflict)]
    assert len(successes) == len(conflicts) == 1
    assert _value(successes[0], "status") == "sent"
    assert _message_row_count(context) == 1
    events = _message_event_rows(context)
    assert [event["event_type"] for event in events] == ["work.message.sent"]


def test_simultaneous_sends_across_sibling_grants_do_not_deadlock(work_message_context):
    from cli_agent_orchestrator.clients.work_repository import WorkRepository
    from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
    from cli_agent_orchestrator.services.work_message_service import WorkMessageService
    from cli_agent_orchestrator.services.work_origin import WorkOrigins

    context = work_message_context
    authority = WorkAuthority(context.repository)
    permissions = Permissions(
        tools={"cao.work.message", "knowledge.read"}, paths={str(context.tmp_path)}
    )
    sender_grant = authority.delegate(
        context.owner,
        parent_grant_id=context.grant.id,
        expected_parent_revision=context.grant.revision,
        child_principal=context.owner,
        providers={"mock_cli"},
        permissions=permissions,
        expires_at=context.grant.expires_at,
    )
    receiver_grant = authority.delegate(
        context.owner,
        parent_grant_id=context.grant.id,
        expected_parent_revision=context.grant.revision,
        child_principal=context.owner,
        providers={"mock_cli"},
        permissions=permissions,
        expires_at=context.grant.expires_at,
    )
    sender = context.make_work("sibling-grant-sender", work_grant=sender_grant)
    receiver = context.make_work("sibling-grant-receiver", work_grant=receiver_grant)
    assert sender.attempt["id"] != receiver.attempt["id"]

    second_repository = WorkRepository(
        context.target, filesystem_home=context.repository.filesystem_home
    )
    second_repository.initialize()
    second_service = WorkMessageService(second_repository, origins=WorkOrigins(second_repository))
    services = (context.messages, second_service)
    sends = (
        (sender, receiver, "sibling-send-a", "from sender"),
        (receiver, sender, "sibling-send-b", "from receiver"),
    )
    barrier = threading.Barrier(2)

    def send_once(index):
        source, target, key, body = sends[index]
        barrier.wait(timeout=10)
        return services[index].execute(
            source.credential,
            {
                "operation": "send",
                "receiver_work_item_id": target.work["id"],
                "idempotency_key": key,
                "body": body,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send_once, index) for index in range(2)]
        results = [future.result(timeout=20) for future in futures]

    assert sorted(_value(result, "status") for result in results) == ["sent", "sent"]
    assert len({_value(result, "message_id") for result in results}) == 2
    assert _message_row_count(context) == 2
    assert [event["event_type"] for event in _message_event_rows(context)] == [
        "work.message.sent",
        "work.message.sent",
    ]


def test_send_waits_for_plain_receiver_revalidation_before_grant_locks(
    work_message_context, monkeypatch
):
    from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
    from cli_agent_orchestrator.services.work_contract import WorkContracts

    context = work_message_context
    authority = WorkAuthority(context.repository)
    permissions = Permissions(
        tools={"cao.work.message", "knowledge.read"}, paths={str(context.tmp_path)}
    )
    sender_grant = authority.delegate(
        context.owner,
        parent_grant_id=context.grant.id,
        expected_parent_revision=context.grant.revision,
        child_principal=context.owner,
        providers={"mock_cli"},
        permissions=permissions,
        expires_at=context.grant.expires_at,
    )
    receiver_grant = authority.delegate(
        context.owner,
        parent_grant_id=context.grant.id,
        expected_parent_revision=context.grant.revision,
        child_principal=context.owner,
        providers={"mock_cli"},
        permissions=permissions,
        expires_at=context.grant.expires_at,
    )
    sender = context.make_work("plain-auth-sender", work_grant=sender_grant)
    receiver = context.make_work("plain-auth-receiver", work_grant=receiver_grant)

    receiver_leaf_locked = threading.Event()
    message_authority_lane_requested = threading.Event()
    release_plain_auth = threading.Event()
    original_load = WorkAuthority._load
    original_lane = WorkAuthority.lock_job_authority_lane

    def pause_after_receiver_leaf(connection, grant_id, expected_revision):
        grant = original_load(connection, grant_id, expected_revision)
        if (
            threading.current_thread().name.startswith("plain-revalidate")
            and grant_id == receiver_grant.id
            and not receiver_leaf_locked.is_set()
        ):
            receiver_leaf_locked.set()
            if not release_plain_auth.wait(timeout=10):
                raise TimeoutError("plain receiver revalidation was not released")
        return grant

    def observe_message_lane(connection, job_id):
        if threading.current_thread().name.startswith("message-send"):
            message_authority_lane_requested.set()
        return original_lane(connection, job_id)

    monkeypatch.setattr(WorkAuthority, "_load", staticmethod(pause_after_receiver_leaf))
    monkeypatch.setattr(
        WorkAuthority, "lock_job_authority_lane", staticmethod(observe_message_lane)
    )

    def revalidate_receiver():
        with context.repository.transaction() as connection:
            context.repository._verify(connection)
            return WorkContracts(context.repository)._revalidate_order(
                connection,
                receiver.attempt["id"],
                generation=receiver.attempt["generation"],
            )

    with (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="plain-revalidate") as plain_pool,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="message-send") as message_pool,
    ):
        plain = plain_pool.submit(revalidate_receiver)
        assert receiver_leaf_locked.wait(timeout=10)
        send = message_pool.submit(
            _send, context, sender, receiver, "plain-auth-race", "authorized after receiver"
        )
        try:
            assert message_authority_lane_requested.wait(timeout=10)
        finally:
            release_plain_auth.set()
        assert plain.result(timeout=20).grant_id == receiver_grant.id
        assert _value(send.result(timeout=20), "status") == "sent"

    assert _message_row_count(context) == 1
    assert [event["event_type"] for event in _message_event_rows(context)] == ["work.message.sent"]


def test_send_and_dispatch_retirement_take_authority_lane_before_work_rows(
    work_message_context, monkeypatch
):
    from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority

    context = work_message_context
    sender = context.make_work("retirement-race-sender")
    receiver = context.make_work("retirement-race-receiver")
    authority = WorkAuthority(context.repository)
    child_permissions = Permissions(
        tools={"cao.work.message", "knowledge.read"}, paths={str(context.tmp_path)}
    )
    invalid_grant = authority.delegate(
        context.owner,
        parent_grant_id=context.grant.id,
        expected_parent_revision=context.grant.revision,
        child_principal=context.owner,
        providers={"mock_cli"},
        permissions=child_permissions,
        expires_at=context.grant.expires_at,
    )
    valid_grant = authority.delegate(
        context.owner,
        parent_grant_id=context.grant.id,
        expected_parent_revision=context.grant.revision,
        child_principal=context.owner,
        providers={"mock_cli"},
        permissions=child_permissions,
        expires_at=context.grant.expires_at,
    )
    retired = context.plan_work("retirement-race-revoked", work_grant=invalid_grant)
    eligible = context.plan_work("retirement-race-live", work_grant=valid_grant)
    authority.revoke(
        context.owner,
        grant_id=invalid_grant.id,
        expected_grant_revision=invalid_grant.revision,
        reason="exercise scheduler retirement lock order",
    )

    retired_attempt_locked = threading.Event()
    retirement_lane_acquired = threading.Event()
    send_lane_requested = threading.Event()
    release_retirement = threading.Event()
    dispatcher_lane_calls = 0
    original_lane = WorkAuthority.lock_job_authority_lane
    original_attempt_lock = context.repository._lock_attempt_for_write

    def observe_lane(connection, job_id):
        nonlocal dispatcher_lane_calls
        thread_name = threading.current_thread().name
        if thread_name.startswith("retirement-preview"):
            dispatcher_lane_calls += 1
            result = original_lane(connection, job_id)
            # The first acquisition is the failed candidate probe inside the
            # savepoint; the second must happen before retirement locks rows.
            if dispatcher_lane_calls == 2:
                retirement_lane_acquired.set()
            return result
        if thread_name.startswith("retirement-send"):
            send_lane_requested.set()
        return original_lane(connection, job_id)

    def pause_retirement_lock(connection, attempt_id):
        attempt = original_attempt_lock(connection, attempt_id)
        if (
            threading.current_thread().name.startswith("retirement-preview")
            and attempt_id == retired.attempt["id"]
        ):
            retired_attempt_locked.set()
            if not release_retirement.wait(timeout=10):
                raise TimeoutError("dispatch retirement was not released")
        return attempt

    monkeypatch.setattr(WorkAuthority, "lock_job_authority_lane", staticmethod(observe_lane))
    monkeypatch.setattr(context.repository, "_lock_attempt_for_write", pause_retirement_lock)

    with (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="retirement-preview") as preview_pool,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="retirement-send") as send_pool,
    ):
        preview = preview_pool.submit(context.admission._preview)
        assert retired_attempt_locked.wait(timeout=10)
        if not retirement_lane_acquired.is_set():
            release_retirement.set()
            preview.result(timeout=20)
            pytest.fail("dispatcher locked the retired attempt before the authority lane")

        send = send_pool.submit(
            _send, context, sender, receiver, "retirement-race-send", "send through retirement"
        )
        try:
            assert send_lane_requested.wait(timeout=10)
        finally:
            release_retirement.set()
        assert preview.result(timeout=20).attempt_id == eligible.attempt["id"]
        assert _value(send.result(timeout=20), "status") == "sent"

    assert _message_row_count(context) == 1
    assert [event["event_type"] for event in _message_event_rows(context)] == ["work.message.sent"]


def test_concurrent_acknowledgements_append_only_one_ack_event(work_message_context):
    context = work_message_context
    sender = context.make_work("concurrent-ack-sender")
    receiver = context.make_work("concurrent-ack-receiver")
    sent = _send(context, sender, receiver, "concurrent-ack-send", "ack once")
    message_id = _value(sent, "message_id")
    barrier = threading.Barrier(2)

    def ack_once():
        barrier.wait(timeout=10)
        return _ack(context, receiver, message_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: ack_once(), range(2)))

    assert sorted(_value(result, "status") for result in results) == [
        "acknowledged",
        "already_acknowledged",
    ]
    assert [row["event_type"] for row in _message_event_rows(context)] == [
        "work.message.sent",
        "work.message.acknowledged",
    ]


def test_cross_job_target_is_rejected_without_creating_a_message_or_event(
    work_message_context,
):
    context = work_message_context
    sender = context.make_work("cross-job-sender")
    before_rows = _message_rows(context)
    before_events = _message_event_rows(context)

    with _domain_rejection():
        context.messages.execute(
            sender.credential,
            {
                "operation": "send",
                "receiver_work_item_id": "not-in-sender-job",
                "idempotency_key": "cross-job-key",
                "body": "hidden target",
            },
        )

    assert _message_rows(context) == before_rows
    assert _message_event_rows(context) == before_events
