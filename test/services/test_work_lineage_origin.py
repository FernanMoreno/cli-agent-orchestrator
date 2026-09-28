"""T019 v22 managed lineage is server-internal, exact and fail-closed."""

import importlib
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContract,
    FrozenContractModel,
)
from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
from cli_agent_orchestrator.security import auth
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    ResolvedSnapshot,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.work_authority import (
    AuthorityDenied,
    Permissions,
    WorkAuthority,
)
from cli_agent_orchestrator.services.work_delivery import DeliveryAdapter
from cli_agent_orchestrator.services.work_contract import ContractConflict, WorkContracts
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
from cli_agent_orchestrator.services.work_scheduler import WorkScheduler


class _InboxPayload(FrozenContractModel):
    message: str


class _NoEffectBackend(TmuxBackend):
    def preflight_work(self, _restriction):
        return None

    def create_session(self, *_args, **_kwargs):
        raise AssertionError("lineage admission must not create a native child")


def _delivery_adapter():
    async def send(*_args, **_kwargs):
        raise AssertionError("lineage admission must not send a delivery")

    return DeliveryAdapter(_InboxPayload, send)


def _modules():
    return (
        importlib.import_module("cli_agent_orchestrator.models.work_origin"),
        importlib.import_module("cli_agent_orchestrator.services.work_admission"),
        importlib.import_module("cli_agent_orchestrator.services.work_origin"),
    )


def _counts(repository):
    with repository.connection() as connection:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "work_items",
                "work_attempts",
                "work_dispatch_bindings",
                "work_child_origin_bindings",
                "work_delivery_orders",
                "work_scheduler_requests",
            )
        )


def _lineage_integrity(repository, attempt):
    with repository.read_snapshot() as connection:
        return connection.execute(
            "SELECT * FROM work_lineage_integrity WHERE child_attempt_id=? AND child_generation=?",
            (attempt["id"], attempt["generation"]),
        ).fetchone()


def _integrity_counts(repository):
    with repository.read_snapshot() as connection:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("work_lineage_integrity", "work_lineage_projection_intents")
        )


def _managed_context(tmp_path):
    models, admission_module, origin_module = _modules()
    repository = WorkRepository(tmp_path / "lineage.sqlite3")
    repository.initialize()
    WorkScheduler(repository).configure(
        capacity=4, max_queue=100, aging_seconds=10, expected_policy_revision=0
    )
    owner = auth._verified_principal(
        "https://issuer.test", "lineage-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    child = auth._verified_principal(
        "https://issuer.test", "lineage-child", [auth.SCOPE_WRITE], "jwt"
    )
    receiver = auth._verified_principal(
        "https://issuer.test", "lineage-receiver", [auth.SCOPE_WRITE], "jwt"
    )
    job = repository.create_job(
        project_id="lineage-project",
        principal_id=owner.id,
        allowed_providers=["mock_cli"],
        grant_id="lineage-root",
        budget={"scheduler_units": 100},
    )
    authority = WorkAuthority(repository)
    root = authority.issue_root(
        owner,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}, paths={str(tmp_path)}),
        expires_at=time.time() + 600,
    )
    child_grant = authority.delegate(
        owner,
        parent_grant_id=root.id,
        expected_parent_revision=root.revision,
        child_principal=child,
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}, paths={str(tmp_path)}),
        expires_at=time.time() + 300,
    )
    receiver_grant = authority.delegate(
        owner,
        parent_grant_id=root.id,
        expected_parent_revision=root.revision,
        child_principal=receiver,
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}, paths={str(tmp_path)}),
        expires_at=time.time() + 300,
    )
    snapshot = DelegationSnapshots(
        repository, policy=KnowledgePolicy(repository, job["id"], root.id, root.revision)
    ).freeze(
        principal=owner,
        job_id=job["id"],
        contract_id="lineage-parent-contract",
        binding_key="lineage-parent-snapshot",
        request_hash="a" * 64,
        scope="project",
        scope_id="lineage-project",
        resolver=lambda _connection, _principal: ResolvedSnapshot("lineage parent"),
    )
    parent_contract = EffectiveWorkContract(
        id="lineage-parent-contract",
        operation_kind="inbox",
        provider="mock_cli",
        backend="test",
        permissions=ContractPermissions(paths=(str(tmp_path),)),
        resources=ContractResources(
            checkout_root=str(tmp_path), write_paths=(str(tmp_path / "lineage"),), units=1
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )
    admission = admission_module.WorkAdmission(
        repository,
        backends={"test": _NoEffectBackend()},
        delivery_adapters={("inbox", 1): _delivery_adapter()},
    )
    parent = admission.admit(
        principal=owner,
        job_id=job["id"],
        idempotency_key="lineage-parent",
        request_hash="b" * 64,
        grant_id=root.id,
        expected_grant_revision=root.revision,
        contract=parent_contract,
        lease_seconds=300,
    )
    origin_authority = origin_module.WorkOriginAuthority(repository)
    child_subject = origin_authority.register_subject(
        owner,
        verified_subject=child,
        kind="child",
        issuer_id=owner.id,
        expected_revision=0,
    )
    child_authorization = origin_authority.authorize(
        owner,
        subject=child,
        origin_kind="child",
        grant_id=child_grant.id,
        grant_revision=child_grant.revision,
        actions={"admit_child", "execute"},
        expires_at=time.time() + 120,
        expected_revision=0,
    )
    receiver_subject = origin_authority.register_subject(
        owner,
        verified_subject=receiver,
        kind="receiver",
        issuer_id=owner.id,
        expected_revision=0,
    )
    receiver_authorization = origin_authority.authorize(
        owner,
        subject=receiver,
        origin_kind="receiver",
        grant_id=receiver_grant.id,
        grant_revision=receiver_grant.revision,
        actions={"task_received"},
        expires_at=time.time() + 120,
        expected_revision=0,
    )
    child_contract = parent_contract.model_copy(
        update={
            "id": "lineage-child-contract",
            "resources": parent_contract.resources.model_copy(
                update={"write_paths": (str(tmp_path / "lineage-child"),)}
            ),
        }
    )
    return SimpleNamespace(
        models=models,
        admission=admission,
        origin_module=origin_module,
        repository=repository,
        owner=owner,
        job=job,
        parent=parent,
        child=child,
        child_grant=child_grant,
        child_subject=child_subject,
        child_authorization=child_authorization,
        receiver=receiver,
        receiver_grant=receiver_grant,
        receiver_subject=receiver_subject,
        receiver_authorization=receiver_authorization,
        child_contract=child_contract,
    )


@pytest.fixture
def managed_context(tmp_path):
    return _managed_context(tmp_path)


def test_forged_context_cannot_admit_managed_lineage_or_queue_work(managed_context):
    """Deleting the trusted-context gate must leave an executable child behind."""
    context = managed_context
    parent_attempt = context.parent["attempts"][0]
    intent = context.models.ManagedLineageIntent(
        contract=context.child_contract,
        delivery=WorkDeliveryEnvelope(
            operation_kind="inbox",
            adapter_version=1,
            payload_json=json.dumps({"message": "managed child"}),
        ),
        lease_seconds=120,
    )
    before = _counts(context.repository)

    with pytest.raises(context.origin_module.OriginDenied):
        context.origin_module.WorkOrigins(context.repository).admit(
            authenticated_context=object(),
            parent_attempt_ref=context.models.WorkAttemptRef(
                work_item_id=context.parent["id"],
                attempt_id=parent_attempt["id"],
                generation=parent_attempt["generation"],
            ),
            child_subject_ref=context.child_subject,
            child_authorization_ref=context.child_authorization.ref,
            receiver_subject_ref=context.receiver_subject,
            receiver_authorization_ref=context.receiver_authorization.ref,
            intent=intent,
            idempotency_key="forged-lineage",
            kind="child",
        )

    assert _counts(context.repository) == before


def _managed_handoff(
    context,
    *,
    key="managed-lineage",
    message="managed child",
    origins=None,
    authenticated_context=None,
    parent_attempt_ref=None,
    child_subject_ref=None,
    child_authorization_ref=None,
    receiver_subject_ref=None,
    receiver_authorization_ref=None,
    contract=None,
    kind="child",
):
    parent_attempt = context.parent["attempts"][0]
    origins = origins or context.origin_module.WorkOrigins(context.repository)
    authenticated_context = authenticated_context or origins.authenticated_context(
        context.owner, context.child, context.receiver
    )
    return origins.admit(
        authenticated_context=authenticated_context,
        parent_attempt_ref=parent_attempt_ref
        or context.models.WorkAttemptRef(
            work_item_id=context.parent["id"],
            attempt_id=parent_attempt["id"],
            generation=parent_attempt["generation"],
        ),
        child_subject_ref=child_subject_ref or context.child_subject,
        child_authorization_ref=child_authorization_ref or context.child_authorization.ref,
        receiver_subject_ref=receiver_subject_ref or context.receiver_subject,
        receiver_authorization_ref=receiver_authorization_ref or context.receiver_authorization.ref,
        intent=context.models.ManagedLineageIntent(
            contract=contract or context.child_contract,
            delivery=WorkDeliveryEnvelope(
                operation_kind="inbox",
                adapter_version=1,
                payload_json=json.dumps({"message": message}),
            ),
            lease_seconds=120,
        ),
        idempotency_key=key,
        kind=kind,
    )


def test_authenticated_task_receipt_only_acknowledges_its_exact_managed_delivery(
    managed_context,
):
    """A sealed receiver receipt cannot be replayed for another hash or generation."""
    context = managed_context
    service_module = importlib.import_module("cli_agent_orchestrator.services.work_service")
    origins = context.origin_module.WorkOrigins(context.repository)
    child = context.admission.admit_managed_lineage(
        _managed_handoff(context, origins=origins, key="task-receipt-exact")
    )
    attempt = child["attempts"][0]
    admission = TransitionEvidence(
        generation=attempt["generation"],
        expected_generation=attempt["generation"],
        contract_confirmed=True,
        grant_confirmed=True,
        capacity_confirmed=True,
        reservations_confirmed=True,
    )
    sender = service_module.WorkService(context.repository)
    sent = sender.dispatch(
        child["id"],
        lambda: service_module.DeliveryObservation(),
        admission=admission,
        actor_id=context.owner.id,
    )
    assert sent["attempts"][0]["state"] == "sent"

    receipt = origins.issue_task_received_receipt(
        authenticated_context=origins.authenticated_context(
            context.owner, context.child, context.receiver
        ),
        attempt_ref=context.models.WorkAttemptRef(
            work_item_id=child["id"],
            attempt_id=attempt["id"],
            generation=attempt["generation"],
        ),
        receiver_subject_ref=context.receiver_subject,
        receiver_authorization_ref=context.receiver_authorization.ref,
        nonce="task-receipt-exact",
    )
    receiver = service_module.WorkService(context.repository, origins=origins)
    stale_generation = receipt.model_copy(
        update={"attempt": receipt.attempt.model_copy(update={"generation": 2})}
    )
    wrong_delivery = receipt.model_copy(update={"delivery_hash": "0" * 64})

    assert receiver.record_task_received(stale_generation)["attempts"][0]["state"] == "sent"
    assert receiver.record_task_received(wrong_delivery)["attempts"][0]["state"] == "sent"
    acknowledged = receiver.record_task_received(receipt)

    assert acknowledged["attempts"][0]["state"] == "acknowledged"
    with context.repository.read_snapshot() as connection:
        before_history = (
            connection.execute(
                "SELECT nonce,receipt_hash FROM work_task_received_receipts "
                "WHERE attempt_id=? AND generation=?",
                (attempt["id"], attempt["generation"]),
            ).fetchall(),
            connection.execute(
                "SELECT event_id,event_type,actor_id FROM work_events WHERE attempt_id=? "
                "ORDER BY sequence",
                (attempt["id"],),
            ).fetchall(),
        )
    assert receiver.record_task_received(wrong_delivery)["attempts"][0]["state"] == "acknowledged"
    with context.repository.read_snapshot() as connection:
        after_history = (
            connection.execute(
                "SELECT nonce,receipt_hash FROM work_task_received_receipts "
                "WHERE attempt_id=? AND generation=?",
                (attempt["id"], attempt["generation"]),
            ).fetchall(),
            connection.execute(
                "SELECT event_id,event_type,actor_id FROM work_events WHERE attempt_id=? "
                "ORDER BY sequence",
                (attempt["id"],),
            ).fetchall(),
        )
    assert after_history == before_history
    reopened = service_module.WorkService(WorkRepository(context.repository.path))
    assert reopened.record_task_received(receipt)["attempts"][0]["state"] == "acknowledged"
    redeliveries = []
    assert reopened.dispatch(
        child["id"],
        lambda: redeliveries.append("sent"),
        admission=admission,
        actor_id="untrusted-replay",
    )["attempts"][0]["state"] == "acknowledged"
    assert redeliveries == []


def test_task_receipt_issued_before_receiver_revocation_never_acknowledges(managed_context):
    """A receipt must revalidate its receiver at acceptance, not just issue time."""
    context = managed_context
    service_module = importlib.import_module("cli_agent_orchestrator.services.work_service")
    origins = context.origin_module.WorkOrigins(context.repository)
    child = context.admission.admit_managed_lineage(
        _managed_handoff(context, origins=origins, key="task-receipt-revoked")
    )
    attempt = child["attempts"][0]
    admission = TransitionEvidence(
        generation=attempt["generation"],
        expected_generation=attempt["generation"],
        contract_confirmed=True,
        grant_confirmed=True,
        capacity_confirmed=True,
        reservations_confirmed=True,
    )
    assert service_module.WorkService(context.repository).dispatch(
        child["id"],
        lambda: service_module.DeliveryObservation(),
        admission=admission,
        actor_id=context.owner.id,
    )["attempts"][0]["state"] == "sent"
    receipt = origins.issue_task_received_receipt(
        authenticated_context=origins.authenticated_context(
            context.owner, context.child, context.receiver
        ),
        attempt_ref=context.models.WorkAttemptRef(
            work_item_id=child["id"],
            attempt_id=attempt["id"],
            generation=attempt["generation"],
        ),
        receiver_subject_ref=context.receiver_subject,
        receiver_authorization_ref=context.receiver_authorization.ref,
        nonce="task-receipt-revoked",
    )
    context.origin_module.WorkOriginAuthority(context.repository).revoke(
        owner=context.owner,
        subject=context.receiver,
        origin_kind="receiver",
        expected_revision=context.receiver_authorization.ref.revision,
    )

    rejected = service_module.WorkService(context.repository, origins=origins).record_task_received(
        receipt
    )

    assert rejected["attempts"][0]["state"] == "sent"
    with context.repository.read_snapshot() as connection:
        assert connection.execute(
            "SELECT count(*) FROM work_task_received_receipts WHERE attempt_id=?",
            (attempt["id"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM work_events WHERE attempt_id=? "
            "AND event_type='attempt.acknowledged'",
            (attempt["id"],),
        ).fetchone()[0] == 0


def test_v21_legacy_child_migrates_to_v22_and_replays_through_public_admission(
    tmp_path, monkeypatch
):
    """A migrated legacy child replays exactly, even after its parent has ended."""
    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    verified_schema = repository_module.WorkRepository._verify

    def verify_v21(connection, *, version=None):
        return verified_schema(connection, version=21 if version is None else version)

    def verify_v22(connection, *, version=None):
        return verified_schema(connection, version=22 if version is None else version)

    monkeypatch.setattr(repository_module, "SCHEMA_VERSION", 21)
    monkeypatch.setattr(repository_module.WorkRepository, "_verify", staticmethod(verify_v21))
    repository = repository_module.WorkRepository(tmp_path / "legacy-v21.sqlite3")
    repository.initialize()
    WorkScheduler(repository).configure(
        capacity=4, max_queue=100, aging_seconds=10, expected_policy_revision=0
    )
    owner = auth._verified_principal(
        "https://issuer.test", "legacy-owner", [auth.SCOPE_ADMIN], "jwt"
    )
    job = repository.create_job(
        project_id="legacy-project",
        principal_id=owner.id,
        allowed_providers=["mock_cli"],
        grant_id="legacy-root",
        budget={"scheduler_units": 100},
    )
    grant = WorkAuthority(repository).issue_root(
        owner,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(tools={"knowledge.read"}, paths={str(tmp_path)}),
        expires_at=time.time() + 600,
    )
    snapshot = DelegationSnapshots(
        repository, policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision)
    ).freeze(
        principal=owner,
        job_id=job["id"],
        contract_id="legacy-parent-contract",
        binding_key="legacy-snapshot",
        request_hash="f" * 64,
        scope="project",
        scope_id="legacy-project",
        resolver=lambda _connection, _principal: ResolvedSnapshot("legacy parent"),
    )
    parent_contract = EffectiveWorkContract(
        id="legacy-parent-contract",
        operation_kind="inbox",
        provider="mock_cli",
        backend="test",
        permissions=ContractPermissions(paths=(str(tmp_path),)),
        resources=ContractResources(
            checkout_root=str(tmp_path),
            write_paths=(str(tmp_path / "legacy-parent"),),
            units=1,
        ),
        snapshot=ContractSnapshot(
            state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
        ),
    )
    child_contract = parent_contract.model_copy(
        update={
            "id": "legacy-child-contract",
            "resources": parent_contract.resources.model_copy(
                update={"write_paths": (str(tmp_path / "legacy-child"),)}
            ),
        }
    )
    backend = _NoEffectBackend()
    admission = importlib.import_module(
        "cli_agent_orchestrator.services.work_admission"
    ).WorkAdmission(
        repository,
        backends={"test": backend},
        delivery_adapters={("inbox", 1): _delivery_adapter()},
    )
    parent = admission.admit(
        principal=owner,
        job_id=job["id"],
        idempotency_key="legacy-parent",
        request_hash="e" * 64,
        grant_id=grant.id,
        expected_grant_revision=grant.revision,
        contract=parent_contract,
        lease_seconds=300,
    )
    delivery = admission.deliveries.envelope(
        WorkDeliveryEnvelope(
            operation_kind="inbox",
            adapter_version=1,
            payload_json=json.dumps({"message": "legacy"}),
        ),
        child_contract.operation_kind,
    )
    request = dict(
        job_id=job["id"],
        operation_kind="inbox",
        idempotency_key="legacy-child",
        request_hash="d" * 64,
        contract_id=child_contract.id,
        snapshot_id=snapshot.id,
        provider="mock_cli",
        actor_id=owner.id,
        lease_seconds=120,
        parent_work_item_id=parent["id"],
    )
    # This is a historical v21 write: no v22 lineage table or marker exists yet.
    with repository.transaction() as connection:
        child = repository._admit_work(connection, **request)
        attempt = child["attempts"][0]
        binding = admission.contracts._bind(
            connection,
            principal=owner,
            attempt_id=attempt["id"],
            generation=attempt["generation"],
            expected_attempt_revision=attempt["revision"],
            grant_id=grant.id,
            expected_grant_revision=grant.revision,
            contract=child_contract,
        )
        prepared = admission.deliveries.prepare(
            delivery, child_contract.operation_kind, contract=child_contract
        )
        admission.deliveries._bind(connection, binding, prepared, request=delivery)
        admission.scheduler._enqueue(
            connection,
            attempt_id=attempt["id"],
            generation=attempt["generation"],
            expected_attempt_revision=attempt["revision"],
            units=child_contract.resources.units,
            dependencies=child_contract.resources.dependencies,
            actor_id=owner.id,
        )
    with repository.transaction() as connection:
        connection.execute(
            "UPDATE work_attempts SET state='failed',revision=revision+1 WHERE id=?",
            (parent["attempts"][0]["id"],),
        )
        connection.execute(
            "UPDATE work_items SET state='failed',revision=revision+1 WHERE id=?",
            (parent["id"],),
        )
    with repository.connection() as connection:
        assert {row[1] for row in connection.execute("PRAGMA table_info(work_items)")}.isdisjoint(
            {"lineage_protocol"}
        )

    monkeypatch.setattr(repository_module, "SCHEMA_VERSION", 22)
    monkeypatch.setattr(repository_module.WorkRepository, "_verify", staticmethod(verify_v22))
    repository.initialize()
    with repository.read_snapshot() as connection:
        assert (
            connection.execute(
                "SELECT lineage_protocol FROM work_items WHERE id=?", (child["id"],)
            ).fetchone()[0]
            == "legacy"
        )
        assert (
            connection.execute("SELECT count(*) FROM work_child_origin_bindings").fetchone()[0] == 0
        )
    monkeypatch.setattr(repository_module, "SCHEMA_VERSION", 23)
    monkeypatch.setattr(repository_module.WorkRepository, "_verify", staticmethod(verified_schema))
    repository.initialize()
    with repository.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM work_lineage_integrity").fetchone()[0] == 0
    before = _counts(repository)
    backend.preflight_work = Mock()

    replay = admission.admit(
        principal=owner,
        job_id=job["id"],
        idempotency_key="legacy-child",
        request_hash="d" * 64,
        grant_id=grant.id,
        expected_grant_revision=grant.revision,
        contract=child_contract,
        lease_seconds=120,
        parent_work_item_id=parent["id"],
        delivery=delivery,
    )

    assert replay["id"] == child["id"]
    assert _counts(repository) == before
    backend.preflight_work.assert_not_called()


def _alternate_subject(context, *, name, kind, actions):
    """Create a second live role in the same root-grant tree for replay tests."""
    subject = auth._verified_principal("https://issuer.test", name, [auth.SCOPE_WRITE], "jwt")
    authority = WorkAuthority(context.repository)
    grant = authority.delegate(
        context.owner,
        parent_grant_id=context.child_grant.parent_grant_id,
        expected_parent_revision=context.child_grant.parent_revision,
        child_principal=subject,
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read"}, paths={context.child_contract.resources.checkout_root}
        ),
        expires_at=time.time() + 300,
    )
    origins = context.origin_module.WorkOriginAuthority(context.repository)
    subject_ref = origins.register_subject(
        context.owner,
        verified_subject=subject,
        kind=kind,
        issuer_id=context.owner.id,
        expected_revision=0,
    )
    authorization = origins.authorize(
        context.owner,
        subject=subject,
        origin_kind=kind,
        grant_id=grant.id,
        grant_revision=grant.revision,
        actions=actions,
        expires_at=time.time() + 120,
        expected_revision=0,
    )
    return subject, subject_ref, authorization


def test_untrusted_context_dto_or_tampered_seal_never_creates_lineage(managed_context):
    """Caller metadata or altered private seals must not become origin authority."""
    context = managed_context
    origins = context.origin_module.WorkOrigins(context.repository)
    before = _counts(context.repository)
    integrity_before = _integrity_counts(context.repository)

    caller_metadata = SimpleNamespace(
        id=context.owner.id,
        caller_id=context.owner.id,
        terminal_id="untrusted-terminal",
        banner="untrusted-banner",
    )
    with pytest.raises(context.origin_module.OriginDenied):
        origins.authenticated_context(caller_metadata, context.child, context.receiver)

    trusted_context = origins.authenticated_context(context.owner, context.child, context.receiver)
    with pytest.raises(context.origin_module.OriginDenied):
        _managed_handoff(
            context,
            origins=origins,
            authenticated_context=replace(trusted_context, _seal="forged-context-seal"),
        )

    sealed = _managed_handoff(context, origins=origins, authenticated_context=trusted_context)
    with pytest.raises(context.origin_module.OriginDenied):
        context.admission.admit_managed_lineage(replace(sealed, _seal="forged-handoff-seal"))

    assert _counts(context.repository) == before
    assert _integrity_counts(context.repository) == integrity_before


def test_managed_lineage_requires_authenticated_receiver_and_live_child_action(managed_context):
    """Removing receiver or admit-child action cannot leave a child order queued."""
    context = managed_context
    origins = context.origin_module.WorkOrigins(context.repository)
    before = _counts(context.repository)

    with pytest.raises(context.origin_module.OriginDenied):
        _managed_handoff(
            context,
            origins=origins,
            authenticated_context=origins.authenticated_context(context.owner, context.child),
        )

    authorization = context.origin_module.WorkOriginAuthority(context.repository).authorize(
        context.owner,
        subject=context.child,
        origin_kind="child",
        grant_id=context.child_grant.id,
        grant_revision=context.child_grant.revision,
        actions={"execute"},
        expires_at=time.time() + 120,
        expected_revision=context.child_authorization.ref.revision,
    )
    with pytest.raises(context.origin_module.OriginDenied):
        _managed_handoff(context, child_authorization_ref=authorization.ref)

    assert _counts(context.repository) == before


def test_managed_lineage_commits_exact_binding_delivery_queue_and_replays_one_identity(
    managed_context,
):
    """Removing atomic lineage consumption would split child, binding, delivery, or queue."""
    context = managed_context
    first = context.admission.admit_managed_lineage(_managed_handoff(context))
    replay = context.admission.admit_managed_lineage(_managed_handoff(context))

    assert replay["id"] == first["id"]
    assert _counts(context.repository) == (2, 2, 2, 1, 1, 2)
    with context.repository.read_snapshot() as connection:
        child = connection.execute(
            "SELECT lineage_protocol,parent_work_item_id FROM work_items WHERE id=?", (first["id"],)
        ).fetchone()
        binding = connection.execute("SELECT * FROM work_child_origin_bindings").fetchone()
        projection = connection.execute(
            "SELECT state FROM work_lineage_projection_intents"
        ).fetchone()
    assert tuple(child) == ("managed-v1", context.parent["id"])
    assert (
        binding["parent_work_item_id"],
        binding["parent_attempt_id"],
        binding["parent_generation"],
        binding["child_work_item_id"],
        binding["kind"],
        binding["idempotency_key"],
    ) == (
        context.parent["id"],
        context.parent["attempts"][0]["id"],
        context.parent["attempts"][0]["generation"],
        first["id"],
        "child",
        "managed-lineage",
    )
    assert projection["state"] == "planned"


def _assert_v23_lineage_integrity_is_installed(repository_module):
    assert repository_module.SCHEMA_VERSION >= 23
    assert any(
        statement.startswith("CREATE TABLE work_lineage_integrity")
        for statement in repository_module._MIGRATIONS[23]
    )


def test_v23_managed_lineage_persists_and_reopens_exact_integrity_companion(managed_context):
    """A new managed child is authoritative only with its immutable v23 companion."""
    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    _assert_v23_lineage_integrity_is_installed(repository_module)
    context = managed_context
    child = context.admission.admit_managed_lineage(_managed_handoff(context, key="v23-positive"))
    attempt = child["attempts"][0]
    integrity = _lineage_integrity(context.repository, attempt)
    assert integrity is not None
    assert (
        integrity["schema_version"],
        integrity["canonicalization_version"],
        integrity["child_attempt_id"],
        integrity["child_generation"],
    ) == (1, 1, attempt["id"], attempt["generation"])
    assert len(integrity["fingerprint"]) == 64
    assert (
        WorkContracts(context.repository)
        .revalidate_order(attempt["id"], generation=attempt["generation"])
        .attempt_id
        == attempt["id"]
    )

    reopened = WorkRepository(context.repository.path)
    reopened.initialize()
    assert (
        WorkContracts(reopened)
        .revalidate_order(attempt["id"], generation=attempt["generation"])
        .attempt_id
        == attempt["id"]
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "child_authorization_revoked",
        "receiver_authorization_revoked",
        "child_subject_replaced",
        "receiver_subject_replaced",
        "child_authorization_replaced",
        "receiver_authorization_replaced",
        "receiver_grant_revoked",
        "authorization_expired",
    ),
)
def test_v23_revalidation_requires_exact_live_child_and_receiver_authority(
    managed_context, monkeypatch, mutation
):
    """A fingerprint never makes stale child or receiver authority current again."""
    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    _assert_v23_lineage_integrity_is_installed(repository_module)
    context = managed_context
    child = context.admission.admit_managed_lineage(
        _managed_handoff(context, key=f"v23-authority-{mutation}")
    )
    attempt = child["attempts"][0]
    origins = context.origin_module.WorkOriginAuthority(context.repository)
    if mutation == "child_authorization_revoked":
        origins.revoke(
            owner=context.owner,
            subject=context.child,
            origin_kind="child",
            expected_revision=context.child_authorization.ref.revision,
        )
    elif mutation == "receiver_authorization_revoked":
        origins.revoke(
            owner=context.owner,
            subject=context.receiver,
            origin_kind="receiver",
            expected_revision=context.receiver_authorization.ref.revision,
        )
    elif mutation == "child_subject_replaced":
        origins.register_subject(
            context.owner,
            verified_subject=context.child,
            kind="child",
            issuer_id=context.owner.id,
            expected_revision=context.child_subject.revision,
        )
    elif mutation == "receiver_subject_replaced":
        origins.register_subject(
            context.owner,
            verified_subject=context.receiver,
            kind="receiver",
            issuer_id=context.owner.id,
            expected_revision=context.receiver_subject.revision,
        )
    elif mutation == "child_authorization_replaced":
        origins.authorize(
            context.owner,
            subject=context.child,
            origin_kind="child",
            grant_id=context.child_grant.id,
            grant_revision=context.child_grant.revision,
            actions={"admit_child", "execute"},
            expires_at=time.time() + 120,
            expected_revision=context.child_authorization.ref.revision,
        )
    elif mutation == "receiver_authorization_replaced":
        origins.authorize(
            context.owner,
            subject=context.receiver,
            origin_kind="receiver",
            grant_id=context.receiver_grant.id,
            grant_revision=context.receiver_grant.revision,
            actions={"task_received"},
            expires_at=time.time() + 120,
            expected_revision=context.receiver_authorization.ref.revision,
        )
    elif mutation == "receiver_grant_revoked":
        WorkAuthority(context.repository).revoke(
            context.owner,
            grant_id=context.receiver_grant.id,
            expected_grant_revision=context.receiver_grant.revision,
            reason="v23 receiver chain fence",
        )
    else:
        now = time.time()
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.work_authority.time.time", lambda: now + 121
        )

    with pytest.raises((AuthorityDenied, ContractConflict)):
        WorkContracts(context.repository).revalidate_order(
            attempt["id"], generation=attempt["generation"]
        )


@pytest.mark.parametrize("role", ("child", "receiver"))
def test_v23_replay_rejects_authorization_grant_contradiction(managed_context, role):
    """Replay cannot reuse a binding after its selected authorization changes grant."""
    context = managed_context
    child = context.admission.admit_managed_lineage(
        _managed_handoff(context, key=f"v23-replay-grant-{role}")
    )
    subject = context.child if role == "child" else context.receiver
    original_grant = context.child_grant if role == "child" else context.receiver_grant
    authorization = (
        context.child_authorization if role == "child" else context.receiver_authorization
    )
    alternate = WorkAuthority(context.repository).delegate(
        context.owner,
        parent_grant_id=original_grant.parent_grant_id,
        expected_parent_revision=original_grant.parent_revision,
        child_principal=subject,
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read"},
            paths={context.child_contract.resources.checkout_root},
        ),
        expires_at=time.time() + 300,
    )
    with context.repository.transaction() as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='work_origin_authorizations_immutable_update'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER work_origin_authorizations_immutable_update")
        connection.execute(
            "UPDATE work_origin_authorizations SET grant_id=?, grant_revision=? "
            "WHERE subject_id=? AND origin_kind=? AND revision=?",
            (
                alternate.id,
                alternate.revision,
                subject.id,
                role,
                authorization.ref.revision,
            ),
        )
        connection.execute(trigger)
    context.repository.verify_schema()
    before = _counts(context.repository)
    with context.repository.read_snapshot() as connection:
        events_before = connection.execute("SELECT count(*) FROM work_events").fetchone()[0]

    with pytest.raises(context.origin_module.OriginConflict, match="grant"):
        context.admission.admit_managed_lineage(
            _managed_handoff(context, key=f"v23-replay-grant-{role}")
        )

    assert _counts(context.repository) == before
    with context.repository.read_snapshot() as connection:
        assert connection.execute("SELECT count(*) FROM work_events").fetchone()[0] == events_before
    assert child["id"] == context.repository.get_work(child["id"])["id"]


def test_v23_integrity_fingerprint_rejects_coherent_active_receiver_swap(managed_context):
    """Another active same-job receiver cannot replace the receiver originally admitted."""
    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    _assert_v23_lineage_integrity_is_installed(repository_module)
    context = managed_context
    child = context.admission.admit_managed_lineage(_managed_handoff(context, key="v23-swap"))
    attempt = child["attempts"][0]
    receiver, subject, authorization = _alternate_subject(
        context, name="v23-other-receiver", kind="receiver", actions={"task_received"}
    )
    with context.repository.transaction() as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='work_child_origin_bindings_immutable_update'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER work_child_origin_bindings_immutable_update")
        connection.execute(
            "UPDATE work_child_origin_bindings SET receiver_subject_id=?, "
            "receiver_subject_revision=?, receiver_authorization_revision=?, "
            "receiver_grant_id=?, receiver_grant_revision=? "
            "WHERE child_attempt_id=? AND child_generation=?",
            (
                receiver.id,
                subject.revision,
                authorization.ref.revision,
                authorization.grant_id,
                authorization.grant_revision,
                attempt["id"],
                attempt["generation"],
            ),
        )
        connection.execute(trigger)
    context.repository.verify_schema()
    with pytest.raises(ContractConflict, match="integrity"):
        WorkContracts(context.repository).revalidate_order(
            attempt["id"], generation=attempt["generation"]
        )


def test_v22_managed_history_has_no_v23_companion_and_cannot_replay(tmp_path, monkeypatch):
    """Migrating a managed v22 row preserves history but never promotes its authority."""
    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    verified_schema = repository_module.WorkRepository._verify

    def verify_v22(connection, *, version=None):
        return verified_schema(connection, version=22 if version is None else version)

    monkeypatch.setattr(repository_module, "SCHEMA_VERSION", 22)
    monkeypatch.setattr(repository_module.WorkRepository, "_verify", staticmethod(verify_v22))
    context = _managed_context(tmp_path)
    origins = context.origin_module.WorkOrigins(context.repository)
    monkeypatch.setattr(origins, "_persist_integrity", lambda *_args: None)
    child = context.admission.admit_managed_lineage(
        _managed_handoff(context, key="v22-history", origins=origins)
    )
    attempt = child["attempts"][0]

    monkeypatch.setattr(repository_module, "SCHEMA_VERSION", 23)
    monkeypatch.setattr(repository_module.WorkRepository, "_verify", staticmethod(verified_schema))
    context.repository.initialize()
    assert _lineage_integrity(context.repository, attempt) is None
    with pytest.raises(ContractConflict, match="integrity"):
        WorkContracts(context.repository).revalidate_order(
            attempt["id"], generation=attempt["generation"]
        )
    with pytest.raises(context.origin_module.OriginConflict, match="integrity"):
        context.admission.admit_managed_lineage(
            _managed_handoff(context, key="v22-history", origins=origins)
        )


def test_v23_managed_without_companion_fails_closed_but_legacy_stays_readable(managed_context):
    """No v22 managed row is promoted merely because the v23 table now exists."""
    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    _assert_v23_lineage_integrity_is_installed(repository_module)
    context = managed_context
    child = context.admission.admit_managed_lineage(_managed_handoff(context, key="v23-missing"))
    attempt = child["attempts"][0]
    with context.repository.transaction() as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='work_lineage_integrity_immutable_delete'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER work_lineage_integrity_immutable_delete")
        connection.execute(
            "DELETE FROM work_lineage_integrity WHERE child_attempt_id=? AND child_generation=?",
            (attempt["id"], attempt["generation"]),
        )
        connection.execute(trigger)
    context.repository.verify_schema()
    with pytest.raises(ContractConflict, match="integrity"):
        WorkContracts(context.repository).revalidate_order(
            attempt["id"], generation=attempt["generation"]
        )
    assert context.repository.get_work(context.parent["id"])["lineage_protocol"] == "legacy"


def test_v23_integrity_insert_failure_rolls_back_every_managed_row(managed_context, monkeypatch):
    """The companion is in the same SQLite commit as binding, delivery, projection, and queue."""
    repository_module = importlib.import_module("cli_agent_orchestrator.clients.work_repository")
    _assert_v23_lineage_integrity_is_installed(repository_module)
    context = managed_context
    origins = context.origin_module.WorkOrigins(context.repository)
    before = _counts(context.repository)
    original = origins._persist_integrity

    def insert_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected lineage integrity failure")

    monkeypatch.setattr(origins, "_persist_integrity", insert_then_fail)
    with pytest.raises(RuntimeError, match="injected lineage integrity failure"):
        context.admission.admit_managed_lineage(
            _managed_handoff(context, key="v23-integrity-rollback", origins=origins)
        )
    assert _counts(context.repository) == before


def test_managed_lineage_never_substitutes_the_latest_parent_attempt(managed_context):
    """A caller-supplied old tuple must not silently bind to a replacement attempt."""
    context = managed_context
    parent_attempt = context.parent["attempts"][0]
    with context.repository.transaction() as connection:
        connection.execute(
            "INSERT INTO work_attempts "
            "(id,work_item_id,attempt_number,generation,provider,lease_expires_at,created_at) "
            "VALUES ('lineage-parent-replacement',?,2,2,'mock_cli',?,?)",
            (context.parent["id"], time.time() + 300, time.time()),
        )
    before = _counts(context.repository)

    with pytest.raises(ContractConflict):
        _managed_handoff(
            context,
            parent_attempt_ref=context.models.WorkAttemptRef(
                work_item_id=context.parent["id"],
                attempt_id=parent_attempt["id"],
                generation=parent_attempt["generation"],
            ),
        )

    assert _counts(context.repository) == before


def test_managed_lineage_rejects_a_real_parent_from_another_job(managed_context):
    """A valid parent tuple from another job cannot borrow this job's origin authority."""
    context = managed_context
    repository = context.repository
    authority = WorkAuthority(repository)
    job = repository.create_job(
        project_id="foreign-lineage-project",
        principal_id=context.owner.id,
        allowed_providers=["mock_cli"],
        grant_id="foreign-lineage-root",
        budget={"scheduler_units": 100},
    )
    root = authority.issue_root(
        context.owner,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read"}, paths={context.child_contract.resources.checkout_root}
        ),
        expires_at=time.time() + 600,
    )
    snapshot = DelegationSnapshots(
        repository, policy=KnowledgePolicy(repository, job["id"], root.id, root.revision)
    ).freeze(
        principal=context.owner,
        job_id=job["id"],
        contract_id="foreign-lineage-contract",
        binding_key="foreign-lineage-snapshot",
        request_hash="d" * 64,
        scope="project",
        scope_id="foreign-lineage-project",
        resolver=lambda _connection, _principal: ResolvedSnapshot("foreign lineage"),
    )
    contract = context.child_contract.model_copy(
        update={
            "id": "foreign-lineage-contract",
            "snapshot": ContractSnapshot(
                state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
            ),
        }
    )
    foreign = context.admission.admit(
        principal=context.owner,
        job_id=job["id"],
        idempotency_key="foreign-lineage-parent",
        request_hash="e" * 64,
        grant_id=root.id,
        expected_grant_revision=root.revision,
        contract=contract,
        lease_seconds=300,
    )
    attempt = foreign["attempts"][0]
    before = _counts(repository)
    with pytest.raises(context.origin_module.OriginDenied):
        _managed_handoff(
            context,
            parent_attempt_ref=context.models.WorkAttemptRef(
                work_item_id=foreign["id"],
                attempt_id=attempt["id"],
                generation=attempt["generation"],
            ),
        )
    assert _counts(repository) == before


@pytest.mark.parametrize(
    "contract",
    (
        lambda context: context.child_contract.model_copy(
            update={
                "permissions": ContractPermissions(
                    paths=(context.child_contract.resources.checkout_root, "/tmp")
                )
            }
        ),
        lambda context: context.child_contract.model_copy(
            update={
                "snapshot": ContractSnapshot(
                    state="present", id="different-snapshot", delivered_hash="c" * 64
                )
            }
        ),
    ),
)
def test_managed_lineage_rejects_expanded_permissions_or_replaced_snapshot(
    managed_context, contract
):
    """The internal resolver cannot widen the parent ceiling or replace its snapshot."""
    context = managed_context
    before = _counts(context.repository)
    with pytest.raises(context.origin_module.OriginDenied):
        _managed_handoff(context, contract=contract(context))
    assert _counts(context.repository) == before


def test_managed_lineage_rechecks_revocation_after_delivery_preparation(
    managed_context, monkeypatch
):
    """The final transaction must reject an authorization revoked after resolution."""
    context = managed_context
    handoff = _managed_handoff(context)
    before = _counts(context.repository)
    prepare = context.admission.deliveries.prepare

    def revoke_then_prepare(*args, **kwargs):
        WorkAuthority(context.repository).revoke(
            context.owner,
            grant_id=context.child_grant.id,
            expected_grant_revision=context.child_grant.revision,
            reason="lineage final-admit fence",
        )
        return prepare(*args, **kwargs)

    monkeypatch.setattr(context.admission.deliveries, "prepare", revoke_then_prepare)
    with pytest.raises(context.origin_module.OriginDenied):
        context.admission.admit_managed_lineage(handoff)
    assert _counts(context.repository) == before


def test_managed_lineage_rejects_expired_authorization_during_resolution(
    managed_context, monkeypatch
):
    """The resolver checks the live authorization expiry before it can seal a handoff."""
    context = managed_context
    now = time.time()
    authorization = context.origin_module.WorkOriginAuthority(context.repository).authorize(
        context.owner,
        subject=context.child,
        origin_kind="child",
        grant_id=context.child_grant.id,
        grant_revision=context.child_grant.revision,
        actions={"admit_child", "execute"},
        expires_at=now + 1,
        expected_revision=context.child_authorization.ref.revision,
    )
    before = _counts(context.repository)
    monkeypatch.setattr(context.origin_module.time, "time", lambda: now + 2)
    with pytest.raises(context.origin_module.OriginDenied):
        _managed_handoff(context, child_authorization_ref=authorization.ref)
    assert _counts(context.repository) == before


def test_managed_lineage_same_key_rejects_changed_payload_without_new_work(managed_context):
    """A replay key is bound to payload-derived request evidence, not merely its text key."""
    context = managed_context
    context.admission.admit_managed_lineage(_managed_handoff(context, key="same-payload"))
    before = _counts(context.repository)
    with pytest.raises(context.origin_module.OriginConflict):
        context.admission.admit_managed_lineage(
            _managed_handoff(context, key="same-payload", message="changed payload")
        )
    assert _counts(context.repository) == before


def test_managed_lineage_same_key_rejects_changed_child_without_new_work(managed_context):
    """A valid but different child identity cannot reuse a durable lineage key."""
    context = managed_context
    context.admission.admit_managed_lineage(_managed_handoff(context, key="same-child"))
    child, subject_ref, authorization = _alternate_subject(
        context,
        name="lineage-other-child",
        kind="child",
        actions={"admit_child", "execute"},
    )
    origins = context.origin_module.WorkOrigins(context.repository)
    before = _counts(context.repository)
    with pytest.raises(context.origin_module.OriginConflict):
        context.admission.admit_managed_lineage(
            _managed_handoff(
                context,
                key="same-child",
                origins=origins,
                authenticated_context=origins.authenticated_context(
                    context.owner, child, context.receiver
                ),
                child_subject_ref=subject_ref,
                child_authorization_ref=authorization.ref,
            )
        )
    assert _counts(context.repository) == before


def test_managed_lineage_same_key_rejects_changed_receiver_without_new_work(managed_context):
    """A valid but different receiver cannot reuse a durable lineage key."""
    context = managed_context
    context.admission.admit_managed_lineage(_managed_handoff(context, key="same-receiver"))
    receiver, subject_ref, authorization = _alternate_subject(
        context,
        name="lineage-other-receiver",
        kind="receiver",
        actions={"task_received"},
    )
    origins = context.origin_module.WorkOrigins(context.repository)
    before = _counts(context.repository)
    with pytest.raises(context.origin_module.OriginConflict):
        context.admission.admit_managed_lineage(
            _managed_handoff(
                context,
                key="same-receiver",
                origins=origins,
                authenticated_context=origins.authenticated_context(
                    context.owner, context.child, receiver
                ),
                receiver_subject_ref=subject_ref,
                receiver_authorization_ref=authorization.ref,
            )
        )
    assert _counts(context.repository) == before


def test_managed_lineage_same_key_rejects_changed_grant_without_new_work(managed_context):
    """A newly authorized child grant is still a different immutable replay identity."""
    context = managed_context
    context.admission.admit_managed_lineage(_managed_handoff(context, key="same-grant"))
    grant = WorkAuthority(context.repository).delegate(
        context.owner,
        parent_grant_id=context.child_grant.parent_grant_id,
        expected_parent_revision=context.child_grant.parent_revision,
        child_principal=context.child,
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"knowledge.read"}, paths={context.child_contract.resources.checkout_root}
        ),
        expires_at=time.time() + 300,
    )
    authorization = context.origin_module.WorkOriginAuthority(context.repository).authorize(
        context.owner,
        subject=context.child,
        origin_kind="child",
        grant_id=grant.id,
        grant_revision=grant.revision,
        actions={"admit_child", "execute"},
        expires_at=time.time() + 120,
        expected_revision=context.child_authorization.ref.revision,
    )
    before = _counts(context.repository)
    with pytest.raises(context.origin_module.OriginConflict):
        context.admission.admit_managed_lineage(
            _managed_handoff(
                context,
                key="same-grant",
                child_authorization_ref=authorization.ref,
            )
        )
    assert _counts(context.repository) == before


def test_managed_lineage_contradictory_binding_and_cycle_fail_closed(managed_context):
    """Corrupted v22 evidence must not dispatch a child or repair a parent cycle."""
    context = managed_context
    child = context.admission.admit_managed_lineage(_managed_handoff(context))
    attempt = child["attempts"][0]
    with context.repository.transaction() as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='work_child_origin_bindings_immutable_update'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER work_child_origin_bindings_immutable_update")
        connection.execute(
            "UPDATE work_child_origin_bindings SET child_contract_hash=?",
            ("0" * 64,),
        )
        connection.execute(trigger)
    with pytest.raises(ContractConflict):
        WorkContracts(context.repository).revalidate_order(
            attempt["id"], generation=attempt["generation"]
        )

    with context.repository.transaction() as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='work_child_origin_bindings_immutable_update'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER work_child_origin_bindings_immutable_update")
        connection.execute(
            "UPDATE work_child_origin_bindings SET child_contract_hash=("
            "SELECT contract_hash FROM work_dispatch_bindings "
            "WHERE attempt_id=? AND generation=?)",
            (attempt["id"], attempt["generation"]),
        )
        connection.execute(trigger)
        connection.execute(
            "UPDATE work_items SET parent_work_item_id=? WHERE id=?",
            (child["id"], context.parent["id"]),
        )
    with pytest.raises(ContractConflict):
        WorkContracts(context.repository).revalidate_order(
            attempt["id"], generation=attempt["generation"]
        )


@pytest.mark.parametrize(
    ("target", "method"),
    (
        ("repository", "_admit_work"),
        ("contracts", "_bind"),
        ("deliveries", "_bind"),
        ("scheduler", "_enqueue"),
    ),
)
def test_managed_lineage_write_boundary_failure_rolls_back_every_row(
    managed_context, monkeypatch, target, method
):
    """A failure after each writable service boundary leaves no partial child state."""
    context = managed_context
    handoff = _managed_handoff(context, key=f"rollback-{target}")
    before = _counts(context.repository)
    service = getattr(context.admission, target, context.repository)
    original = getattr(service, method)

    def write_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError(f"injected {target}.{method} failure")

    monkeypatch.setattr(service, method, write_then_fail)
    with pytest.raises(RuntimeError, match="injected"):
        context.admission.admit_managed_lineage(handoff)
    assert _counts(context.repository) == before


def test_managed_lineage_reopen_preserves_planned_evidence_without_sending(managed_context):
    """Reopening only reads planned lineage/delivery evidence; it never runs the adapter."""
    context = managed_context
    child = context.admission.admit_managed_lineage(_managed_handoff(context, key="reopen"))
    reopened = WorkRepository(context.repository.path)
    reopened.initialize()
    recovered = reopened.get_work(child["id"])
    assert recovered["attempts"][0]["state"] == "planned"
    with reopened.read_snapshot() as connection:
        assert (
            connection.execute("SELECT state FROM work_lineage_projection_intents").fetchone()[0]
            == "planned"
        )
        assert connection.execute("SELECT count(*) FROM work_delivery_orders").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("trigger_name", "mutation"),
    (
        (
            "work_child_origin_bindings_immutable_delete",
            "DELETE FROM work_child_origin_bindings",
        ),
        (
            "work_items_lineage_protocol_monotonic",
            "UPDATE work_items SET lineage_protocol='legacy' WHERE lineage_protocol='managed-v1'",
        ),
    ),
)
def test_managed_lineage_missing_or_contradictory_marker_fails_contract_revalidation(
    managed_context, trigger_name, mutation
):
    """Deleting the v22 binding guard must let a tampered child reach dispatch."""
    context = managed_context
    child = context.admission.admit_managed_lineage(_managed_handoff(context))
    attempt = child["attempts"][0]
    with context.repository.transaction() as connection:
        if "bindings" in mutation:
            integrity_trigger = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='work_lineage_integrity_immutable_delete'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER work_lineage_integrity_immutable_delete")
            connection.execute("DELETE FROM work_lineage_integrity")
            connection.execute(integrity_trigger)
            projection_trigger = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='work_lineage_projection_intents_immutable_delete'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER work_lineage_projection_intents_immutable_delete")
            connection.execute("DELETE FROM work_lineage_projection_intents")
            connection.execute(projection_trigger)
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (trigger_name,)
        ).fetchone()[0]
        connection.execute(f"DROP TRIGGER {trigger_name}")
        connection.execute(mutation)
        connection.execute(trigger_sql)

    context.repository.verify_schema()
    with pytest.raises(ContractConflict):
        WorkContracts(context.repository).revalidate_order(
            attempt["id"], generation=attempt["generation"]
        )
    assert _counts(context.repository) == (2, 2, 2, 0 if "bindings" in mutation else 1, 1, 2)
