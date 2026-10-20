"""Attempt-bound Work message dispatch contracts (Spec024 T004)."""

import hashlib
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cli_agent_orchestrator.backends.tmux_backend import TmuxBackend
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_contract import (
    ContractPermissions,
    ContractResources,
    ContractSnapshot,
    EffectiveWorkContract,
)
from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
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
from cli_agent_orchestrator.services.work_scheduler import WorkScheduler
from cli_agent_orchestrator.services.work_service import DeliveryObservation, WorkService

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _provision_work_postgres_schema(postgres_targets):
    from cli_agent_orchestrator.clients.postgres.registry import bootstrap_scope

    bootstrap_scope(postgres_targets.main)


class _NoEffectBackend(TmuxBackend):
    def preflight_work(self, _restriction):
        return None

    def create_session(self, *_args, **_kwargs):
        raise AssertionError("message dispatch setup must not create a native session")


def _message_world(tmp_path, postgres_targets):
    repository = WorkRepository(postgres_targets.main, filesystem_home=tmp_path)
    repository.initialize()
    WorkScheduler(repository).configure(
        capacity=8, max_queue=100, aging_seconds=10, expected_policy_revision=0
    )
    owner = auth._verified_principal(
        "https://issuer.test", f"message-owner-{uuid4().hex}", [auth.SCOPE_ADMIN], "jwt"
    )
    project_id = f"message-project-{uuid4().hex}"
    job = repository.create_job(
        project_id=project_id,
        principal_id=owner.id,
        allowed_providers=["mock_cli"],
        grant_id=f"message-root-{uuid4().hex}",
        budget={"scheduler_units": 32},
    )
    authority = WorkAuthority(repository)
    grant = authority.issue_root(
        owner,
        job_id=job["id"],
        providers={"mock_cli"},
        permissions=Permissions(
            tools={"cao.work.message", "knowledge.read"}, paths={str(tmp_path)}
        ),
        expires_at=time.time() + 600,
    )
    origins = WorkOrigins(repository)
    admission = WorkAdmission(repository, backends={"test": _NoEffectBackend()}, origins=origins)
    credentials = WorkAttemptCredentials(repository, origins=origins)
    service = WorkService(repository, origins=origins)

    def live_attempt(key, *, tools=("cao.work.message",)):
        contract_id = f"message-contract-{key}-{uuid4().hex}"
        snapshot = DelegationSnapshots(
            repository,
            policy=KnowledgePolicy(repository, job["id"], grant.id, grant.revision),
        ).freeze(
            principal=owner,
            job_id=job["id"],
            contract_id=contract_id,
            binding_key=f"message-binding-{contract_id}",
            request_hash=hashlib.sha256(contract_id.encode("utf-8")).hexdigest(),
            scope="project",
            scope_id=project_id,
            resolver=lambda _connection, _principal: ResolvedSnapshot("message dispatch test"),
        )
        contract = EffectiveWorkContract(
            id=contract_id,
            operation_kind="inbox",
            provider="mock_cli",
            backend="test",
            permissions=ContractPermissions(tools=tuple(tools), paths=(str(tmp_path),)),
            resources=ContractResources(checkout_root=str(tmp_path), units=1),
            snapshot=ContractSnapshot(
                state="present", id=snapshot.id, delivered_hash=snapshot.delivered_hash
            ),
        )
        work = admission.admit(
            principal=owner,
            job_id=job["id"],
            idempotency_key=f"message-{key}-{uuid4().hex}",
            request_hash=hashlib.sha256(key.encode("utf-8")).hexdigest(),
            grant_id=grant.id,
            expected_grant_revision=grant.revision,
            contract=contract,
            lease_seconds=300,
        )
        planned_attempt = work["attempts"][-1]
        work = service.dispatch(
            work["id"],
            lambda: DeliveryObservation(),
            admission=TransitionEvidence(
                generation=planned_attempt["generation"],
                expected_generation=planned_attempt["generation"],
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
                connection,
                binding,
                expected_attempt_revision=attempt["revision"],
            )
        return SimpleNamespace(work=work, attempt=attempt, credential=issued.secret)

    return SimpleNamespace(
        repository=repository,
        owner=owner,
        job=job,
        grant=grant,
        origins=origins,
        sender=live_attempt("sender"),
        receiver=live_attempt("receiver"),
        sender_without_tool=live_attempt("sender-without-tool", tools=()),
        receiver_without_tool=live_attempt("receiver-without-tool", tools=()),
    )


@pytest.fixture
def message_world(tmp_path, postgres_targets):
    return _message_world(tmp_path, postgres_targets)


def _request(request_id, arguments, *, name="cao.work.message"):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _result_text(response, request_id):
    assert response.get("jsonrpc") == "2.0"
    assert response.get("id") == request_id
    assert "result" in response, response
    content = response["result"]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"
    return json.loads(content[0]["text"])


def _assert_rejected(response, request_id):
    assert response.get("jsonrpc") == "2.0"
    assert response.get("id") == request_id
    assert "error" in response
    assert "result" not in response


def _message_effect_counts(repository):
    with repository.read_snapshot() as connection:
        has_message_table = connection.execute("SELECT to_regclass('work_messages')").fetchone()[0]
        message_count = (
            connection.execute("SELECT count(*) FROM work_messages").fetchone()[0]
            if has_message_table is not None
            else None
        )
        event_count = connection.execute(
            "SELECT count(*) FROM work_events WHERE event_type LIKE 'work.message.%'"
        ).fetchone()[0]
    return message_count, event_count


def test_message_send_receive_and_ack_use_live_attempt_credentials(message_world):
    world = message_world
    body = "¡Hola, 世界! 👋"

    sent_response = world.origins.handle_mcp_request(
        _request(
            "send-1",
            {
                "operation": "send",
                "receiver_work_item_id": world.receiver.work["id"],
                "idempotency_key": "message.send-1",
                "body": body,
            },
        ),
        world.sender.credential,
    )
    sent = _result_text(sent_response, "send-1")
    assert set(sent) == {"message_id", "status"}
    assert sent["status"] == "sent"
    assert isinstance(sent["message_id"], str) and sent["message_id"]

    received_response = world.origins.handle_mcp_request(
        _request("receive-1", {"operation": "receive"}),
        world.receiver.credential,
    )
    received = _result_text(received_response, "receive-1")
    assert set(received) == {"messages"}
    assert len(received["messages"]) == 1
    message = received["messages"][0]
    assert set(message) == {
        "message_id",
        "sender_work_item_id",
        "body",
        "created_at",
    }
    assert message["message_id"] == sent["message_id"]
    assert message["sender_work_item_id"] == world.sender.work["id"]
    assert message["body"] == body

    ack_response = world.origins.handle_mcp_request(
        _request("ack-1", {"operation": "ack", "message_id": sent["message_id"]}),
        world.receiver.credential,
    )
    assert _result_text(ack_response, "ack-1") == {
        "message_id": sent["message_id"],
        "status": "acknowledged",
    }
    replayed_ack = world.origins.handle_mcp_request(
        _request("ack-2", {"operation": "ack", "message_id": sent["message_id"]}),
        world.receiver.credential,
    )
    assert _result_text(replayed_ack, "ack-2") == {
        "message_id": sent["message_id"],
        "status": "already_acknowledged",
    }
    pending = world.origins.handle_mcp_request(
        _request("receive-2", {"operation": "receive"}),
        world.receiver.credential,
    )
    assert _result_text(pending, "receive-2") == {"messages": []}

    assert _message_effect_counts(world.repository) == (1, 2)
    with world.repository.read_snapshot() as connection:
        event = connection.execute(
            "SELECT metadata FROM work_events WHERE event_type='work.message.sent'"
        ).fetchone()
    assert event is not None
    metadata = event["metadata"]
    assert body not in metadata
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() not in metadata


def test_sender_and_receiver_effective_contracts_must_grant_message_before_persistence(
    message_world,
):
    world = message_world
    before = _message_effect_counts(world.repository)
    denied_sender = world.origins.handle_mcp_request(
        _request(
            "sender-denied",
            {
                "operation": "send",
                "receiver_work_item_id": world.receiver.work["id"],
                "idempotency_key": "sender-denied",
                "body": "hello",
            },
        ),
        world.sender_without_tool.credential,
    )
    _assert_rejected(denied_sender, "sender-denied")
    assert _message_effect_counts(world.repository) == before

    denied_receiver_send = world.origins.handle_mcp_request(
        _request(
            "receiver-send-denied",
            {
                "operation": "send",
                "receiver_work_item_id": world.receiver_without_tool.work["id"],
                "idempotency_key": "receiver-send-denied",
                "body": "hello",
            },
        ),
        world.sender.credential,
    )
    _assert_rejected(denied_receiver_send, "receiver-send-denied")
    assert _message_effect_counts(world.repository) == before

    denied_receive = world.origins.handle_mcp_request(
        _request("receiver-read-denied", {"operation": "receive"}),
        world.receiver_without_tool.credential,
    )
    _assert_rejected(denied_receive, "receiver-read-denied")
    denied_ack = world.origins.handle_mcp_request(
        _request("receiver-ack-denied", {"operation": "ack", "message_id": "unknown"}),
        world.receiver_without_tool.credential,
    )
    _assert_rejected(denied_ack, "receiver-ack-denied")
    assert _message_effect_counts(world.repository) == before


def test_message_tool_name_and_closed_action_shapes_reject_before_persistence(message_world):
    world = message_world
    valid_send = {
        "operation": "send",
        "receiver_work_item_id": world.receiver.work["id"],
        "idempotency_key": "closed-shape",
        "body": "hello",
    }
    invalid_calls = [
        *[
            ("cao.work.message", {**valid_send, field: "forged"}, False)
            for field in (
                "sender_work_item_id",
                "sender_principal_id",
                "job_id",
                "attempt_id",
                "generation",
                "credential",
            )
        ],
        ("cao.work.message", {"operation": "receive", "receiver_work_item_id": "forged"}, True),
        (
            "cao.work.message",
            {
                "operation": "ack",
                "message_id": "message-1",
                "receiver_work_item_id": "forged",
            },
            True,
        ),
        ("cao.work.message", {**valid_send, "body": ""}, False),
        ("cao.work.message", {**valid_send, "body": "x\x00y"}, False),
        ("cao.work.message", {**valid_send, "body": "x" * 4097}, False),
        ("cao.work.message", {**valid_send, "body": "\ud800"}, False),
        ("cao.work.message", {**valid_send, "body": 7}, False),
        (
            "cao.work.message",
            {
                "operation": "SEND",
                **{key: value for key, value in valid_send.items() if key != "operation"},
            },
            False,
        ),
        ("cao.work.message", {"operation": "receive", "limit": True}, True),
        ("cao.work.message", {"operation": "receive", "limit": 3}, True),
        ("cao.work.message", {"operation": "ack", "message_id": ""}, True),
        ("send_message", valid_send, False),
    ]

    before = _message_effect_counts(world.repository)
    for index, (name, arguments, receiver_call) in enumerate(invalid_calls):
        request_id = f"malformed-{index}"
        response = world.origins.handle_mcp_request(
            _request(request_id, arguments, name=name),
            world.receiver.credential if receiver_call else world.sender.credential,
        )
        _assert_rejected(response, request_id)

    extra_params = {
        "jsonrpc": "2.0",
        "id": "extra-params",
        "method": "tools/call",
        "params": {"name": "cao.work.message", "arguments": valid_send, "extra": True},
    }
    _assert_rejected(
        world.origins.handle_mcp_request(extra_params, world.sender.credential),
        "extra-params",
    )
    invalid_credential = world.origins.handle_mcp_request(
        _request("invalid-credential", valid_send), b"z" * 32
    )
    _assert_rejected(invalid_credential, "invalid-credential")
    assert _message_effect_counts(world.repository) == before


def test_existing_task_result_child_and_handoff_dispatch_routes_remain_available():
    origins = WorkOrigins(WorkRepository())
    credential = b"c" * 32
    receiver_credential = b"r" * 32
    work = {
        "id": "work-item",
        "state": "sent",
        "attempts": [{"id": "attempt", "generation": 3, "state": "sent"}],
    }
    calls = []

    def accept_task_received(**kwargs):
        calls.append(("task_received", kwargs))
        return work

    def submit_result(**kwargs):
        calls.append(("submit_result", kwargs))
        return work

    def admit_from_credential(attempt_credential, **kwargs):
        calls.append((kwargs["kind"], {"credential": attempt_credential, **kwargs}))
        return ("handoff", kwargs["kind"])

    origins.accept_task_received_with_credentials = accept_task_received
    origins._result_service = SimpleNamespace(submit_workflow_step_result=submit_result)
    origins.admit_from_attempt_credential = admit_from_credential
    origins._admission = SimpleNamespace(admit_managed_lineage=lambda _handoff: work)

    contract = EffectiveWorkContract(
        id="dispatch-compatibility",
        operation_kind="inbox",
        provider="mock_cli",
        backend="test",
        permissions=ContractPermissions(),
        resources=ContractResources(checkout_root="/tmp", units=1),
        snapshot=ContractSnapshot(state="absent", absence_reason="legacy_parent_has_no_snapshot"),
    )
    intent = {
        "schema_version": 1,
        "contract": contract.model_dump(mode="json"),
        "delivery": WorkDeliveryEnvelope(
            operation_kind="inbox", adapter_version=1, payload_json="{}"
        ).model_dump(mode="json"),
        "lease_seconds": 60,
        "native_child_id": None,
        "terminal_id": None,
        "handoff_id": None,
    }

    task_response = origins.handle_mcp_request(
        _request("old-task", {}, name="cao.work.task_received"),
        credential,
        receiver_credential=receiver_credential,
    )
    assert _result_text(task_response, "old-task") == {
        "attempt_id": "attempt",
        "generation": 3,
        "state": "sent",
        "work_item_id": "work-item",
    }

    result_response = origins.handle_mcp_request(
        _request(
            "old-result",
            {"schema_version": 1, "status": "completed", "output": {}},
            name="cao.work.submit_result",
        ),
        credential,
        receiver_credential=receiver_credential,
    )
    assert _result_text(result_response, "old-result")["work_item_id"] == "work-item"

    for tool_name, kind in (("cao.work.child", "child"), ("cao.work.handoff", "handoff")):
        response = origins.handle_mcp_request(
            _request(
                f"old-{kind}",
                {
                    "child_subject_id": "child-subject",
                    "receiver_subject_id": "receiver-subject",
                    "intent": intent,
                    "idempotency_key": f"old-{kind}",
                },
                name=tool_name,
            ),
            credential,
        )
        assert _result_text(response, f"old-{kind}")["work_item_id"] == "work-item"

    assert [name for name, _ in calls] == ["task_received", "submit_result", "child", "handoff"]
