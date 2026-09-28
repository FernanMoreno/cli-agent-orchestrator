"""Durable operation routing with SQLite; no provider or backend process runs."""

import asyncio
import importlib
import json
import multiprocessing
import sqlite3

import pytest
from pydantic import BaseModel, ConfigDict, Field, field_serializer

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services.work_admission import WorkAdmission
from cli_agent_orchestrator.services.work_provisioning import WorkProvisioning
from cli_agent_orchestrator.services.work_service import DeliveryObservation, DeliveryUncertain
from test.integration.test_work_dispatch import (  # noqa: F401
    context,
    admit,
    accounting,
    AdmissionOnlyBackend,
)


class Message(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    message: str


class TargetedMessage(Message):
    """Test-only versioned payload with a sealed durable terminal target."""

    terminal_id: str
    session_name: str
    window_name: str


def target_adapter(send):
    module = importlib.import_module("cli_agent_orchestrator.services.work_delivery")
    return module.DeliveryAdapter(
        TargetedMessage,
        send,
        terminal_identity=lambda payload: payload.terminal_id,
        terminal_target=lambda payload: (
            payload.terminal_id,
            payload.session_name,
            payload.window_name,
        ),
    )


def target_envelope(operation, message, *, terminal_id, session_name, window_name):
    module = importlib.import_module("cli_agent_orchestrator.models.work_delivery")
    return module.WorkDeliveryEnvelope(
        operation_kind=operation,
        adapter_version=1,
        payload_json=json.dumps(
            {
                "message": message,
                "terminal_id": terminal_id,
                "session_name": session_name,
                "window_name": window_name,
            }
        ),
    )


def persist_terminal_target(terminal_id, session_name, window_name):
    database.create_terminal(terminal_id, session_name, window_name, "mock_cli")


def registry(context, observed, *, operations=("launch", "inbox")):
    module = importlib.import_module("cli_agent_orchestrator.services.work_delivery")
    adapters = {}
    for operation in operations:

        async def send(binding, payload, snapshot, port, operation=operation):
            await asyncio.sleep(0)
            observed.append((operation, payload.message, snapshot.content))
            return DeliveryObservation(task_received=True)

        adapters[(operation, 1)] = module.DeliveryAdapter(Message, send)
    context.service = WorkAdmission(
        context.repo,
        backends={"test": context.backend},
        delivery_adapters=adapters,
    )
    return adapters


def targeted_registry(context, observed, *, operations=("launch", "inbox")):
    adapters = {}
    for operation in operations:

        async def send(binding, payload, snapshot, port, operation=operation):
            await asyncio.sleep(0)
            observed.append((operation, payload.message, snapshot.content))
            port.bind_terminal_target(
                payload.terminal_id, payload.session_name, payload.window_name
            )
            port.send_keys(payload.session_name, payload.window_name, payload.message)
            return DeliveryObservation(task_received=True)

        adapters[(operation, 1)] = target_adapter(send)
    context.service = WorkAdmission(
        context.repo,
        backends={"test": context.backend},
        delivery_adapters=adapters,
    )
    return adapters


def envelope(operation, message):
    module = importlib.import_module("cli_agent_orchestrator.models.work_delivery")
    return module.WorkDeliveryEnvelope(
        operation_kind=operation,
        adapter_version=1,
        payload_json=json.dumps({"message": message}),
    )


@pytest.mark.asyncio
async def test_restart_routes_mixed_operations_with_exact_payload_and_snapshot(context):
    observed = []
    targets = [
        ("d311a001", "launch-session", "launch-window"),
        ("d311a002", "inbox-session", "inbox-window"),
    ]
    for target in targets:
        persist_terminal_target(*target)
    adapters = targeted_registry(context, observed)
    first = admit(
        context,
        "delivery-launch",
        delivery=target_envelope(
            "launch",
            "launch task",
            terminal_id=targets[0][0],
            session_name=targets[0][1],
            window_name=targets[0][2],
        ),
    )
    second = admit(
        context,
        "delivery-inbox",
        operation="inbox",
        job_index=1,
        delivery=target_envelope(
            "inbox",
            "inbox task",
            terminal_id=targets[1][0],
            session_name=targets[1][1],
            window_name=targets[1][2],
        ),
    )
    restarted = WorkAdmission(
        WorkRepository(context.repo.path),
        backends={"test": context.backend},
        delivery_adapters=adapters,
    )
    assert (await restarted.dispatch_registered_next())["id"] == first["id"]
    assert (await restarted.dispatch_registered_next())["id"] == second["id"]
    assert observed == [
        ("launch", "launch task", b"frozen context delivery-launch"),
        ("inbox", "inbox task", b"frozen context delivery-inbox"),
    ]
    assert context.backend.effects == [
        (targets[0][1], targets[0][2], "launch task"),
        (targets[1][1], targets[1][2], "inbox task"),
    ]
    assert await restarted.dispatch_registered_next() is None
    with context.repo.connection() as connection:
        events = " ".join(row[0] for row in connection.execute("SELECT metadata FROM work_events"))
        assert "launch task" not in events and "inbox task" not in events


@pytest.mark.asyncio
async def test_missing_adapter_does_not_spend_budget_or_block_another_operation(context):
    observed = []
    registry(context, observed)
    unknown = admit(context, "missing", operation="inbox", delivery=envelope("inbox", "waiting"))
    available = admit(context, "available", delivery=envelope("launch", "ready"))
    registry(context, observed, operations=("launch",))
    assert (await context.service.dispatch_registered_next())["id"] == available["id"]
    assert context.repo.get_work(unknown["id"])["state"] == "queued"
    assert accounting(context.repo)["spent"] == 1
    assert observed == [("launch", "ready", b"frozen context available")]


def test_replay_compares_delivery_even_when_request_hash_is_reused(context):
    registry(context, [])
    first = admit(context, "replay", delivery=envelope("launch", "first"))
    assert admit(context, "replay", delivery=envelope("launch", "first"))["id"] == first["id"]
    with pytest.raises(ValueError):
        admit(context, "replay", delivery=envelope("launch", "different"))
    with pytest.raises(ValueError):
        admit(context, "replay", operation="launch")


def test_delivery_replay_does_not_require_the_adapter_to_remain_installed(context):
    registry(context, [])
    first = admit(context, "offline-replay", delivery=envelope("launch", "task"))
    context.service = WorkAdmission(context.repo, backends={"test": context.backend})
    assert (
        admit(context, "offline-replay", delivery=envelope("launch", "task"))["id"] == first["id"]
    )


@pytest.mark.asyncio
async def test_retired_launch_origin_during_readiness_blocks_the_protected_effect(context):
    """A fresh target guard blocks a sealed, owned send after origin retirement."""
    target = ("d311a010", "launch-session", "launch-window")
    persist_terminal_target(*target)
    targeted_registry(context, [])
    key = "retire-during-readiness"
    work = admit(
        context,
        key,
        delivery=target_envelope(
            "launch",
            "guarded task",
            terminal_id=target[0],
            session_name=target[1],
            window_name=target[2],
        ),
    )
    origin = context.launch_origins[(0, key)]
    validated_target = []

    async def retire_then_effect(binding, payload, snapshot, port):
        port.bind_terminal_target(
            payload.terminal_id, payload.session_name, payload.window_name
        )
        validated_target.append((payload.terminal_id, payload.session_name, payload.window_name))
        WorkProvisioning(context.repo).retire_launch(
            context.actor,
            subject=context.actor,
            selector=origin.ref.selector,
            expected_revision=origin.ref.revision,
        )
        port.send_keys(payload.session_name, payload.window_name, payload.message)
        return DeliveryObservation(task_received=True)

    context.service = WorkAdmission(
        context.repo,
        backends={"test": context.backend},
        delivery_adapters={
            ("launch", 1): target_adapter(retire_then_effect),
        },
    )

    with pytest.raises(DeliveryUncertain) as failure:
        await context.service.dispatch_registered_next()

    assert validated_target == [target]
    assert "launch origin provision is no longer valid" in str(failure.value.__cause__)
    assert context.backend.effects == []
    state = context.repo.get_work(work["id"])
    assert state["state"] == "reconcile"
    assert state["attempts"][0]["terminal_id"] == target[0]


@pytest.mark.asyncio
async def test_delivery_integrity_covers_adapter_version(context):
    registry(context, [])
    work = admit(context, "version-tamper", delivery=envelope("launch", "task"))
    with context.repo.transaction() as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='work_delivery_orders_immutable_update'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER work_delivery_orders_immutable_update")
        connection.execute("UPDATE work_delivery_orders SET adapter_version=2")
        connection.execute(trigger)
    assert await context.service.dispatch_registered_next() is None
    assert context.repo.get_work(work["id"])["state"] == "queued"
    assert context.backend.effects == []


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz"},
        {"message": "task", "password": "short"},
        {"message": "task", "unexpected": True},
    ],
)
def test_invalid_or_secret_delivery_never_enters_database_or_errors(context, payload):
    registry(context, [])
    models = importlib.import_module("cli_agent_orchestrator.models.work_delivery")
    value = models.WorkDeliveryEnvelope(
        operation_kind="launch", adapter_version=1, payload_json=json.dumps(payload)
    )
    with pytest.raises(ValueError) as failure:
        admit(context, "invalid", delivery=value)
    assert "abcdefghijklmnopqrstuvwxyz" not in str(failure.value)
    with context.repo.connection() as connection:
        assert connection.execute("SELECT count(*) FROM work_items").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM work_delivery_orders").fetchone()[0] == 0


def test_delivery_failure_rolls_back_binding_item_queue_and_events(context):
    registry(context, [])
    with context.repo.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER reject_delivery BEFORE INSERT ON work_delivery_orders "
            "BEGIN SELECT RAISE(ABORT,'delivery unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="delivery unavailable"):
        admit(context, "rollback-delivery", delivery=envelope("launch", "task"))
    with context.repo.connection() as connection:
        for table in (
            "work_items",
            "work_attempts",
            "work_dispatch_bindings",
            "work_scheduler_requests",
        ):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_registered_timeout_never_resends_after_restart(context):
    observed = []
    target = ("d311a020", "launch-session", "launch-window")
    persist_terminal_target(*target)

    async def uncertain(binding, payload, snapshot, port):
        port.bind_terminal_target(
            payload.terminal_id, payload.session_name, payload.window_name
        )
        observed.append(("launch", payload.message, snapshot.content))
        port.send_keys(payload.session_name, payload.window_name, payload.message)
        raise TimeoutError("no receipt")

    adapters = {("launch", 1): target_adapter(uncertain)}
    context.service = WorkAdmission(
        context.repo, backends={"test": context.backend}, delivery_adapters=adapters
    )
    work = admit(
        context,
        "uncertain",
        delivery=target_envelope(
            "launch",
            "task",
            terminal_id=target[0],
            session_name=target[1],
            window_name=target[2],
        ),
    )
    with pytest.raises(DeliveryUncertain):
        await context.service.dispatch_registered_next()
    restarted = WorkAdmission(
        WorkRepository(context.repo.path),
        backends={"test": context.backend},
        delivery_adapters=adapters,
    )
    assert await restarted.dispatch_registered_next() is None
    assert context.repo.get_work(work["id"])["state"] == "reconcile"
    assert len(context.backend.effects) == 1
    assert context.backend.effects == [(target[1], target[2], "task")]


def _dispatch_in_fresh_process(database, output):
    async def send(binding, payload, snapshot, port):
        port.bind_terminal_target(
            payload.terminal_id, payload.session_name, payload.window_name
        )
        port.send_keys(payload.session_name, payload.window_name, payload.message)
        output.put((binding.work_item_id, payload.message, snapshot.content))
        return DeliveryObservation(task_received=True)

    service = WorkAdmission(
        WorkRepository(database),
        backends={"test": AdmissionOnlyBackend()},
        delivery_adapters={("launch", 1): target_adapter(send)},
    )
    result = asyncio.run(service.dispatch_registered_next())
    if result is None:
        output.put(None)


def test_new_process_recovers_delivery_without_original_request_objects(context):
    target = ("d311a030", "launch-session", "launch-window")
    persist_terminal_target(*target)
    targeted_registry(context, [])
    work = admit(
        context,
        "process-delivery",
        delivery=target_envelope(
            "launch",
            "durable message",
            terminal_id=target[0],
            session_name=target[1],
            window_name=target[2],
        ),
    )
    processes = multiprocessing.get_context("spawn")
    output = processes.Queue()
    try:
        for expected in [(work["id"], "durable message", b"frozen context process-delivery"), None]:
            worker = processes.Process(
                target=_dispatch_in_fresh_process, args=(str(context.repo.path), output)
            )
            worker.start()
            try:
                # Spawn imports the checkout afresh; mounted filesystems can take
                # over 30 seconds before test code runs. This is not a dispatch SLA.
                assert output.get(timeout=120) == expected
                worker.join(timeout=10)
                assert worker.exitcode == 0
            finally:
                if worker.is_alive():
                    worker.terminate()
                worker.join(timeout=5)
    finally:
        output.close()
        output.join_thread()
    assert context.backend.effects == []  # The backend instance lived in the child process.
    assert context.repo.get_work(work["id"])["attempts"][0]["terminal_id"] == target[0]


@pytest.mark.asyncio
async def test_registered_dispatch_does_not_guess_payload_for_legacy_binding(context):
    registry(context, [])
    work = admit(context, "legacy-order")
    assert await context.service.dispatch_registered_next() is None
    assert context.repo.get_work(work["id"])["state"] == "queued"
    assert accounting(context.repo)["spent"] == 0


@pytest.mark.asyncio
async def test_same_version_drifted_order_does_not_block_a_healthy_registered_operation(context):
    """A blocked adapter must lose only its own eligibility, not the fair scan."""
    observed = []
    registry(context, observed)
    drifted = admit(context, "drifted-launch", delivery=envelope("launch", "launch task"))
    healthy = admit(
        context,
        "healthy-inbox",
        operation="inbox",
        job_index=1,
        delivery=envelope("inbox", "inbox task"),
    )

    class DriftedMessage(Message):
        schema_drift: str = "new-default"

    module = importlib.import_module("cli_agent_orchestrator.services.work_delivery")

    async def launch_send(*args):
        pytest.fail("a same-version drifted adapter must not deliver")

    async def inbox_send(binding, payload, snapshot, port):
        observed.append(("inbox", payload.message, snapshot.content))
        return DeliveryObservation(task_received=True)

    restarted = WorkAdmission(
        WorkRepository(context.repo.path),
        backends={"test": context.backend},
        delivery_adapters={
            ("launch", 1): module.DeliveryAdapter(DriftedMessage, launch_send),
            ("inbox", 1): module.DeliveryAdapter(Message, inbox_send),
        },
    )
    result = await restarted.dispatch_registered_next()
    assert result["id"] == healthy["id"]
    assert context.repo.get_work(drifted["id"])["state"] == "queued"
    assert accounting(context.repo)["spent"] == 1
    assert observed == [("inbox", "inbox task", b"frozen context healthy-inbox")]
    assert context.backend.effects == []


@pytest.mark.asyncio
async def test_plaintext_task_content_is_opaque_in_delivery_rows_and_restores_after_restart(
    context,
):
    """Raw task prose belongs only in the owner-only content store."""
    message = "password is hunter2"
    observed = []
    adapters = registry(context, observed)
    work = admit(context, "opaque-message", delivery=envelope("launch", message))

    with context.repo.connection() as connection:
        row = connection.execute(
            "SELECT payload_json FROM work_delivery_orders WHERE attempt_id=?",
            (work["attempts"][0]["id"],),
        ).fetchone()
        delivery_events = connection.execute(
            "SELECT metadata FROM work_events WHERE event_type LIKE 'delivery.%'"
        ).fetchall()
    assert message not in row["payload_json"]
    assert all(message not in event["metadata"] for event in delivery_events)

    restarted = WorkAdmission(
        WorkRepository(context.repo.path),
        backends={"test": context.backend},
        delivery_adapters=adapters,
    )
    assert (await restarted.dispatch_registered_next())["id"] == work["id"]
    assert observed == [("launch", message, b"frozen context opaque-message")]


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["missing", "changed"])
async def test_missing_or_tampered_content_reference_never_delivers_or_guesses_payload(
    context, tamper
):
    """A content failure cannot reconstruct from arbitrary queued delivery input."""
    observed = []
    adapters = registry(context, observed)
    work = admit(context, "content-integrity", delivery=envelope("launch", "only this payload"))
    with context.repo.connection() as connection:
        encoded = json.loads(
            connection.execute(
                "SELECT payload_json FROM work_delivery_orders WHERE attempt_id=?",
                (work["attempts"][0]["id"],),
            ).fetchone()["payload_json"]
        )
    assert "content_ref" in encoded, "new delivery orders must hold an opaque content reference"
    content = context.repo.delivery_content_root / encoded["content_ref"]
    assert content.is_file()
    if tamper == "missing":
        content.unlink()
    else:
        content.write_bytes(b'{"message":"another order payload"}')

    restarted = WorkAdmission(
        WorkRepository(context.repo.path),
        backends={"test": context.backend},
        delivery_adapters=adapters,
    )
    assert await restarted.dispatch_registered_next() is None
    assert context.repo.get_work(work["id"])["state"] == "queued"
    assert accounting(context.repo)["spent"] == 0
    assert context.backend.effects == []
    assert observed == []


@pytest.mark.asyncio
async def test_legacy_plaintext_order_is_not_dispatched_without_a_content_reference(context):
    """A migrated v13 row stays immutable history, never a source for raw replay."""
    observed = []
    registry(context, observed)
    work = admit(context, "legacy-plaintext", delivery=envelope("launch", "new order payload"))
    delivery = importlib.import_module("cli_agent_orchestrator.services.work_delivery")
    models = importlib.import_module("cli_agent_orchestrator.models.work_delivery")
    with context.repo.transaction() as connection:
        order_trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='work_delivery_orders_immutable_update'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER work_delivery_orders_immutable_update")
        content_trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='work_delivery_content_refs_immutable_delete'"
        ).fetchone()
        if content_trigger is not None:
            connection.execute("DROP TRIGGER work_delivery_content_refs_immutable_delete")
            connection.execute(
                "DELETE FROM work_delivery_content_refs WHERE attempt_id=? AND generation=?",
                (work["attempts"][0]["id"], work["attempts"][0]["generation"]),
            )
            connection.execute(content_trigger[0])
        row = connection.execute(
            "SELECT request_hash,contract_hash FROM work_delivery_orders WHERE attempt_id=?",
            (work["attempts"][0]["id"],),
        ).fetchone()
        legacy = models.WorkDeliveryEnvelope(
            operation_kind="launch",
            adapter_version=1,
            payload_json='{"message":"legacy plaintext"}',
        )
        connection.execute(
            "UPDATE work_delivery_orders SET payload_json=?,delivery_hash=? WHERE attempt_id=?",
            (
                legacy.payload_json,
                delivery._digest(legacy, row["contract_hash"], row["request_hash"]),
                work["attempts"][0]["id"],
            ),
        )
        connection.execute(order_trigger)

    assert await context.service.dispatch_registered_next() is None
    assert context.repo.get_work(work["id"])["state"] == "queued"
    assert accounting(context.repo)["spent"] == 0
    assert context.backend.effects == []
    assert observed == []


@pytest.mark.parametrize(
    "payload_json",
    [
        '{"message":"one","message":"two"}',
        '{"message":NaN}',
        "[]",
    ],
)
def test_ambiguous_delivery_json_is_rejected_before_admission(context, payload_json):
    registry(context, [])
    models = importlib.import_module("cli_agent_orchestrator.models.work_delivery")
    with pytest.raises(ValueError):
        admit(
            context,
            "ambiguous",
            delivery=models.WorkDeliveryEnvelope(
                operation_kind="launch",
                adapter_version=1,
                payload_json=payload_json,
            ),
        )
    assert accounting(context.repo)["queued"] == 0


def test_delivery_history_cannot_be_updated_or_deleted(context):
    registry(context, [])
    admit(context, "immutable", delivery=envelope("launch", "task"))
    for sql in [
        "DELETE FROM work_delivery_orders",
        "UPDATE work_delivery_orders SET adapter_version=2",
    ]:
        with context.repo.transaction() as connection:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(sql)


@pytest.mark.asyncio
async def test_aliased_delivery_fields_remain_executable_after_persistence(context):
    from cli_agent_orchestrator.services.work_delivery import DeliveryAdapter
    from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope

    class AliasedMessage(Message):
        message: str = Field(alias="text")

    async def send(binding, payload, snapshot, port):
        return DeliveryObservation(task_received=True)

    context.service = WorkAdmission(
        context.repo,
        backends={"test": context.backend},
        delivery_adapters={("launch", 1): DeliveryAdapter(AliasedMessage, send)},
    )
    work = admit(
        context,
        "aliased",
        delivery=WorkDeliveryEnvelope(
            operation_kind="launch", adapter_version=1, payload_json='{"text":"task"}'
        ),
    )
    assert (await context.service.dispatch_registered_next())["id"] == work["id"]
    assert context.backend.effects == []


def test_non_roundtrip_serializer_is_rejected_before_queueing(context):
    from cli_agent_orchestrator.services.work_delivery import DeliveryAdapter

    class UnreadableMessage(Message):
        @field_serializer("message")
        def serialize_message(self, value):
            return {"text": value}

    async def send(*args):
        pytest.fail("nonrecoverable payload must not be admitted")

    context.service = WorkAdmission(
        context.repo,
        backends={"test": context.backend},
        delivery_adapters={("launch", 1): DeliveryAdapter(UnreadableMessage, send)},
    )
    with pytest.raises(ValueError):
        admit(context, "unreadable", delivery=envelope("launch", "task"))
    assert accounting(context.repo)["queued"] == 0


@pytest.mark.parametrize("credential", ["refresh_token", "pwd", "id_token", "private_key"])
def test_nested_credential_fields_never_persist_even_when_payload_model_accepts_them(
    context, credential
):
    from cli_agent_orchestrator.services.work_delivery import DeliveryAdapter
    from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope

    class MetadataMessage(Message):
        metadata: dict[str, str]

    async def send(*args):
        pytest.fail("credential payload must not be admitted")

    context.service = WorkAdmission(
        context.repo,
        backends={"test": context.backend},
        delivery_adapters={("launch", 1): DeliveryAdapter(MetadataMessage, send)},
    )
    with pytest.raises(ValueError):
        admit(
            context,
            "credential",
            delivery=WorkDeliveryEnvelope(
                operation_kind="launch",
                adapter_version=1,
                payload_json=json.dumps(
                    {"message": "task", "metadata": {credential: "opaque-value"}}
                ),
            ),
        )
    assert accounting(context.repo)["queued"] == 0
