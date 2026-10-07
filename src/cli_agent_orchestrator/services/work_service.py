"""Own the durable intent/external-effect boundary for work execution.

Repository methods only persist facts. This service invokes a delivery adapter
after its intent commits, and never infers delivery from terminal allocation.
Public entrypoints must supply server-verified admission evidence.
"""

import asyncio
import hashlib
import json
import math
import os
import select
import signal
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Callable, Literal, Mapping
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from cli_agent_orchestrator.clients.work_repository import (
    BubblewrapProcessIdentity,
    WorkConflict,
    WorkRepository,
)
from cli_agent_orchestrator.models.work_origin import (
    MAX_WORKFLOW_STEP_RESULT_BYTES,
    WorkAttemptRef,
    WorkflowStepResultV1,
)
from cli_agent_orchestrator.services.step_output_store import ImmutableResultStore
from cli_agent_orchestrator.services.work_origin import (
    OriginConflict,
    OriginDenied,
    TaskReceivedReceiptV1,
    WorkOrigins,
)
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence


def validate_workflow_step_result(binding, content: bytes) -> dict:
    """Validate canonical result bytes against one immutable workflow binding."""
    if type(content) is not bytes or len(content) > MAX_WORKFLOW_STEP_RESULT_BYTES:
        raise ValueError("workflow result envelope exceeds 1 MiB")
    result = WorkflowStepResultV1.from_json_bytes(content)
    if result.canonical_bytes() != content:
        raise WorkConflict("workflow result envelope is not canonically encoded")

    schema_json = getattr(binding, "output_schema_json", None)
    schema_hash = getattr(binding, "output_schema_hash", None)
    if (schema_json is None) != (schema_hash is None):
        raise WorkConflict("workflow output schema binding is incomplete")
    if schema_json is not None:
        if type(schema_json) is not str or type(schema_hash) is not str:
            raise WorkConflict("workflow output schema binding is invalid")

        def unique_pairs(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("workflow output schema contains a duplicate key")
                value[key] = item
            return value

        def reject_constant(_value):
            raise ValueError("workflow output schema numbers must be finite")

        try:
            schema = json.loads(
                schema_json,
                object_pairs_hook=unique_pairs,
                parse_constant=reject_constant,
            )
            canonical_schema = json.dumps(
                schema,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError) as error:
            raise WorkConflict("workflow output schema is not strict JSON") from error
        if (
            type(schema) is not dict
            or canonical_schema != schema_json
            or hashlib.sha256(schema_json.encode("utf-8")).hexdigest() != schema_hash
        ):
            raise WorkConflict("workflow output schema hash or canonical form changed")
        try:
            import jsonschema

            validator = jsonschema.Draft202012Validator(schema)
            validator.validate(result.output)
        except jsonschema.SchemaError as error:
            raise WorkConflict("frozen workflow output schema is invalid") from error
        except jsonschema.ValidationError as error:
            raise WorkConflict("workflow result output does not match its frozen schema") from error

    binding_id = getattr(binding, "binding_id", None)
    provision_fingerprint = getattr(binding, "provision_fingerprint", None)
    if (
        type(binding_id) is not str
        or not binding_id
        or type(provision_fingerprint) is not str
        or len(provision_fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in provision_fingerprint)
    ):
        raise WorkConflict("workflow result has no immutable binding identity")
    return {
        "valid": True,
        "content_hash": hashlib.sha256(content).hexdigest(),
        "output_schema_hash": schema_hash,
        "provision_fingerprint": provision_fingerprint,
        "validator_id": "workflow-step-result-v1",
        "workflow_binding_id": binding_id,
    }


@dataclass(frozen=True, slots=True)
class AcceptedWorkflowStepResult:
    """Restart-readable proof for one accepted workflow result artifact."""

    binding_id: str
    binding_fingerprint: str
    tier: str
    run_id: str
    run_generation: int
    step_id: str
    workflow_step_attempt: int
    work_item_id: str
    work_attempt_id: str
    work_generation: int
    delivery_id: str
    delivery_hash: str
    accepted_result_id: str
    content_hash: str
    byte_length: int
    canonical_bytes: bytes = field(repr=False)
    result: WorkflowStepResultV1


@dataclass(frozen=True, slots=True)
class WorkflowStepWorkState:
    """Exact durable Work and attempt states for one immutable workflow binding."""

    binding_id: str
    work_item_id: str
    work_attempt_id: str
    work_generation: int
    work_state: str
    work_revision: int
    attempt_state: str
    attempt_revision: int
    current_attempt_id: str
    current_generation: int
    current_attempt_state: str
    accepted_result_id: str | None
    cleanup_state: str


class ProcessFailureEvidence(BaseModel):
    """Server-owned proof that one Docker process exited unsuccessfully and was removed."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    attempt_id: StrictStr
    generation: Annotated[StrictInt, Field(gt=0)]
    exit_code: Annotated[StrictInt, Field(gt=0, le=255)]
    process_stopped: Literal[True]
    container_removed: Literal[True]
    image_removed: Literal[True]

    @model_validator(mode="after")
    def validate_identity(self):
        if not self.attempt_id or len(self.attempt_id) > 128:
            raise ValueError("process failure attempt identity is invalid")
        return self


class DeliveryObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # Kept only to deserialize legacy adapter observations.  These unauthenticated
    # booleans are never used as durable acknowledgement evidence.
    task_received: StrictBool = False
    execution_started: StrictBool = False
    task_received_receipt: TaskReceivedReceiptV1 | None = None
    process_failure: ProcessFailureEvidence | None = None

    @model_validator(mode="after")
    def execution_implies_receipt(self):
        if self.execution_started and not (self.task_received or self.task_received_receipt):
            raise ValueError("execution evidence must include task receipt")
        return self


class DeliveryUncertain(RuntimeError):
    def __init__(self, work_item_id: str):
        self.work_item_id = work_item_id
        super().__init__("delivery is uncertain; reconcile the existing operation before retrying")


_BWRAP_MONITOR_PATH = b"/tmp/caos-exec/T097/bubblewrap-build/bwrap"
_BWRAP_REQUIRED_OPTIONS = {
    b"--unshare-pid",
    b"--unshare-net",
    b"--unshare-ipc",
    b"--die-with-parent",
    b"--as-pid-1",
}
_BWRAP_IDENTITY_KEYS = (
    "boot_id",
    "monitor_pid",
    "monitor_start_time_ticks",
    "init_pid",
    "init_start_time_ticks",
    "init_parent_pid",
    "pid_namespace",
    "net_namespace",
    "ipc_namespace",
    "monitor_argv_sha256",
    "monitor_executable_sha256",
)


def _read_bubblewrap_proc_stat(pid: int) -> tuple[int, int, str]:
    value = Path(f"/proc/{pid}/stat").read_text()
    boundary = value.rfind(")")
    if boundary < 0:
        raise ValueError("Bubblewrap proc stat has no command boundary")
    fields = value[boundary + 2 :].split()
    if len(fields) <= 19:
        raise ValueError("Bubblewrap proc stat omits process identity")
    return int(fields[1]), int(fields[19]), fields[0]


def _hash_bubblewrap_executable(pid: int) -> str:
    descriptor = os.open(f"/proc/{pid}/exe", os.O_RDONLY | os.O_CLOEXEC)
    try:
        source = os.fstat(descriptor)
        if not stat.S_ISREG(source.st_mode) or not 0 < source.st_size <= 64 * 1024 * 1024:
            raise ValueError("Bubblewrap monitor executable is not a bounded regular file")
        digest = hashlib.sha256()
        size = 0
        while size <= 64 * 1024 * 1024:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
        if size != source.st_size:
            raise ValueError("Bubblewrap monitor executable changed while reading")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _read_bubblewrap_live_identity(monitor_pid: int, init_pid: int) -> dict[str, object]:
    """Read a fresh process pair without trusting the persisted PID alone."""
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    _, monitor_start, monitor_state = _read_bubblewrap_proc_stat(monitor_pid)
    init_parent, init_start, init_state = _read_bubblewrap_proc_stat(init_pid)
    children = Path(f"/proc/{monitor_pid}/task/{monitor_pid}/children").read_text().split()
    argv = Path(f"/proc/{monitor_pid}/cmdline").read_bytes()
    args = argv[:-1].split(b"\x00") if argv.endswith(b"\x00") else []
    if (
        not boot_id
        or monitor_state in {"Z", "X", "x"}
        or init_state in {"Z", "X", "x"}
        or monitor_start <= 0
        or init_start <= 0
        or init_parent != monitor_pid
        or children != [str(init_pid)]
        or not args
        or args[0] != _BWRAP_MONITOR_PATH
        or not _BWRAP_REQUIRED_OPTIONS.issubset(args)
    ):
        raise ValueError("live Bubblewrap monitor and namespace init cannot be verified")
    namespaces = {
        f"{kind}_namespace": _bubblewrap_namespace_identity(init_pid, kind)
        for kind in ("pid", "net", "ipc")
    }
    return {
        "boot_id": boot_id,
        "monitor_pid": monitor_pid,
        "monitor_start_time_ticks": monitor_start,
        "init_pid": init_pid,
        "init_start_time_ticks": init_start,
        "init_parent_pid": init_parent,
        **namespaces,
        "monitor_argv_sha256": hashlib.sha256(argv).hexdigest(),
        "monitor_executable_sha256": _hash_bubblewrap_executable(monitor_pid),
    }


def _bubblewrap_namespace_identity(pid: int, kind: str) -> list[int]:
    namespace = os.stat(f"/proc/{pid}/ns/{kind}")
    return [namespace.st_dev, namespace.st_ino]


def _original_bubblewrap_pair_gone(identity: BubblewrapProcessIdentity) -> bool:
    """Prove both original PIDs exited without treating a live mismatch as exit."""
    try:
        current_boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if not current_boot:
            return False
        if current_boot != identity["boot_id"]:
            return True
        for pid, original_start_time in (
            (identity["monitor_pid"], identity["monitor_start_time_ticks"]),
            (identity["init_pid"], identity["init_start_time_ticks"]),
        ):
            try:
                _, start_time, state = _read_bubblewrap_proc_stat(pid)
            except FileNotFoundError:
                continue
            if start_time == original_start_time and state not in {"Z", "X", "x"}:
                return False
        return True
    except (OSError, TypeError, ValueError):
        return False


def _bubblewrap_pidfd_target(descriptor: int) -> int:
    fields = Path(f"/proc/self/fdinfo/{descriptor}").read_text().splitlines()
    pid_fields = [line.split(":", 1)[1].strip() for line in fields if line.startswith("Pid:")]
    if len(pid_fields) != 1:
        raise ValueError("Bubblewrap pidfd target is unavailable")
    return int(pid_fields[0])


def _bubblewrap_pidfd_exited(descriptor: int, timeout_seconds: float) -> bool | None:
    poller = select.poll()
    poller.register(descriptor, select.POLLIN | select.POLLHUP | select.POLLERR)
    events = poller.poll(round(timeout_seconds * 1000))
    if any(flags & (select.POLLERR | select.POLLNVAL) for _, flags in events):
        return None
    return any(flags & (select.POLLIN | select.POLLHUP) for _, flags in events)


def _orphaned_bubblewrap_init_matches(identity: BubblewrapProcessIdentity) -> bool:
    """Require the original monitor gone and its exact namespace init live."""
    if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != identity["boot_id"]:
        return False
    try:
        _, monitor_start, monitor_state = _read_bubblewrap_proc_stat(identity["monitor_pid"])
    except FileNotFoundError:
        monitor_gone = True
    else:
        monitor_gone = monitor_start != identity["monitor_start_time_ticks"] or monitor_state in {
            "Z",
            "X",
            "x",
        }
    if not monitor_gone:
        return False
    _, init_start, init_state = _read_bubblewrap_proc_stat(identity["init_pid"])
    if init_start != identity["init_start_time_ticks"] or init_state in {"Z", "X", "x"}:
        return False
    fields: Mapping[str, object] = identity
    return all(
        _bubblewrap_namespace_identity(identity["init_pid"], kind) == fields[f"{kind}_namespace"]
        for kind in ("pid", "net", "ipc")
    )


def _cleanup_orphaned_bubblewrap_init(identity: BubblewrapProcessIdentity) -> bool:
    """Kill only a surviving exact init pinned after its monitor has ended."""
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        return False
    init_fd = None
    try:
        if not _orphaned_bubblewrap_init_matches(identity):
            return False
        init_fd = os.pidfd_open(identity["init_pid"])
        if (
            _bubblewrap_pidfd_target(init_fd) != identity["init_pid"]
            or not _orphaned_bubblewrap_init_matches(identity)
            or _bubblewrap_pidfd_exited(init_fd, 0) is not False
        ):
            return False
        signal.pidfd_send_signal(init_fd, signal.SIGKILL)
        return _bubblewrap_pidfd_exited(init_fd, 2.0) is True
    except (OSError, KeyError, TypeError, ValueError):
        return False
    finally:
        if init_fd is not None:
            os.close(init_fd)


def _cleanup_bubblewrap_identity(identity: BubblewrapProcessIdentity) -> bool:
    """Signal only a twice-validated live pair through pinned pidfds."""
    if identity.get("version") != 1 or identity.get("kind") != "bubblewrap":
        return False
    monitor_pid = identity["monitor_pid"]
    init_pid = identity["init_pid"]
    fields: Mapping[str, object] = identity
    expected = {key: fields[key] for key in _BWRAP_IDENTITY_KEYS}
    try:
        before = _read_bubblewrap_live_identity(monitor_pid, init_pid)
    except (OSError, ValueError):
        before = None
    if before != expected:
        return _original_bubblewrap_pair_gone(identity) or _cleanup_orphaned_bubblewrap_init(
            identity
        )
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        return False
    monitor_fd = None
    init_fd = None
    try:
        monitor_fd = os.pidfd_open(monitor_pid)
        init_fd = os.pidfd_open(init_pid)
        if (
            _bubblewrap_pidfd_target(monitor_fd) != monitor_pid
            or _bubblewrap_pidfd_target(init_fd) != init_pid
            or _read_bubblewrap_live_identity(monitor_pid, init_pid) != expected
            or _bubblewrap_pidfd_exited(monitor_fd, 0) is not False
            or _bubblewrap_pidfd_exited(init_fd, 0) is not False
        ):
            return False
        signal.pidfd_send_signal(init_fd, signal.SIGKILL)
        return (
            _bubblewrap_pidfd_exited(init_fd, 2.0) is True
            and _bubblewrap_pidfd_exited(monitor_fd, 2.0) is True
        )
    finally:
        if init_fd is not None:
            os.close(init_fd)
        if monitor_fd is not None:
            os.close(monitor_fd)


class WorkService:
    def __init__(
        self,
        repository: WorkRepository,
        *,
        origins: WorkOrigins | None = None,
        artifacts=None,
        workflow_origins=None,
    ):
        self.repository = repository
        if origins is not None and (
            not isinstance(origins, WorkOrigins)
            or Path(origins.repository.path).resolve() != Path(repository.path).resolve()
        ):
            raise ValueError("task receipt origin must use this work repository")
        self.origins = origins
        from cli_agent_orchestrator.services.work_workflow import WorkWorkflowOrigins

        if workflow_origins is not None and (
            not isinstance(workflow_origins, WorkWorkflowOrigins)
            or workflow_origins.repository is not repository
        ):
            raise ValueError("workflow result resolver must use this exact Work repository")
        self.workflow_origins = (
            workflow_origins
            or (getattr(origins, "_workflow_origins", None) if origins is not None else None)
            or WorkWorkflowOrigins(repository)
        )

        if artifacts is not None and not all(
            callable(getattr(artifacts, method, None)) for method in ("publish", "read")
        ):
            raise ValueError("workflow results require a durable artifact store")
        self.artifacts = artifacts
        if origins is not None and artifacts is not None:
            origins._bind_result_service(self)

    def retry_work(
        self,
        work_item_id: str,
        *,
        scheduler,
        expected_revision: int,
        provider: str,
        evidence: TransitionEvidence,
        actor_id: str,
        lease_seconds: float,
    ) -> dict:
        """Replace exactly one reconciled writer after durable cessation evidence.

        The scheduler owns the external stop verifier and its durable release;
        the repository remains the sole owner of the retry-generation SQL.  The
        release snapshot is deliberately passed directly to ``retry_work`` as
        the CAS fence rather than rereading a newer work revision.
        """
        from cli_agent_orchestrator.services.work_scheduler import SchedulerConflict, WorkScheduler

        if (
            not isinstance(scheduler, WorkScheduler)
            or Path(scheduler.repository.path).resolve() != Path(self.repository.path).resolve()
        ):
            raise WorkConflict("scheduler must use this work repository")
        if type(expected_revision) is not int or expected_revision <= 0:
            raise WorkConflict("work revision fence is required")
        if not isinstance(provider, str) or not provider.strip():
            raise WorkConflict("replacement provider is required")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, (int, float))
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
        ):
            raise ValueError("lease_seconds must be finite and positive")
        if not isinstance(evidence, TransitionEvidence) or not evidence.reconciliation_authorized:
            raise WorkConflict("replacement requires authorized reconciliation")
        with self.repository.read_snapshot() as connection:
            work = self.repository._work(connection, work_item_id)
            prior = work["attempts"][-1]
            job = self.repository._job(connection, work["job_id"])
            if work["revision"] != expected_revision:
                raise WorkConflict("work revision changed")
            if work["state"] != "reconcile" or prior["state"] != "reconcile":
                raise WorkConflict("only the current reconciled writer may be replaced")
            if prior["cleanup_state"] == "pending":
                raise SchedulerConflict("pending cleanup blocks replacement")
            if (
                evidence.generation != prior["generation"]
                or evidence.expected_generation != prior["generation"]
            ):
                raise WorkConflict("stale reconciliation generation")
            if provider not in job["allowed_providers"] or job["state"] in {
                "revoked",
                "completed",
                "failed",
            }:
                raise WorkConflict("job does not permit replacement provider")
        reservation = scheduler.get_by_attempt(prior["id"])
        if (
            reservation.job_id != work["job_id"]
            or reservation.work_item_id != work_item_id
            or reservation.generation != prior["generation"]
        ):
            raise WorkConflict("scheduler reservation does not own reconciled writer")
        released = scheduler.release_for_replacement(
            reservation.id,
            generation=prior["generation"],
            expected_revision=reservation.revision,
            expected_attempt_revision=prior["revision"],
            expected_work_revision=work["revision"],
            actor_id=actor_id,
        )
        retry_evidence = evidence.model_copy(update={"prior_stopped": True})
        return self.repository.retry_work(
            work_item_id,
            expected_revision=released["revision"],
            provider=provider,
            evidence=retry_evidence,
            actor_id=actor_id,
            lease_seconds=lease_seconds,
        )

    def dispatch(
        self,
        work_item_id: str,
        send: Callable[[], DeliveryObservation],
        *,
        admission: TransitionEvidence,
        actor_id: str,
    ) -> dict:
        work = self.repository.get_work(work_item_id)
        attempt = work["attempts"][-1]
        if work["state"] != "queued" or attempt["state"] != "planned":
            # Includes an uncertain prior send. Query/replay never means redelivery.
            return work
        try:
            sent = self.repository.transition_attempt(
                attempt_id=attempt["id"],
                generation=attempt["generation"],
                expected_revision=attempt["revision"],
                expected_state="planned",
                target="sent",
                actor_id=actor_id,
                event_id=uuid4().hex,
                evidence=admission,
            )
        except WorkConflict:
            # Another dispatcher may have committed the intent; only its winner sends.
            current = self.repository.get_work(work_item_id)
            if current["attempts"][-1]["state"] != "planned":
                return current
            raise
        return self._send_committed(sent, send, actor_id=actor_id)

    def _send_committed(
        self, sent: dict, send: Callable[[], DeliveryObservation], *, actor_id: str
    ) -> dict:
        """Send only for the trusted caller that just won and committed the sent CAS.

        The transaction must already be closed. Never call this helper for a
        recovered intent: its existence is not permission to resend the task.
        """
        work_item_id = sent["id"]
        writer_id = sent["attempts"][-1]["id"]
        registered = False
        try:
            with self.repository.transaction() as connection:
                self.repository._verify(connection)
                self.repository._register_writer_effect(connection, writer_id=writer_id)
                registered = True
            try:
                observation = send()
                if not isinstance(observation, DeliveryObservation):
                    raise TypeError("delivery adapter must return DeliveryObservation")
            finally:
                if registered:
                    with self.repository.transaction() as connection:
                        self.repository._verify(connection)
                        self.repository._release_writer_effect(connection, writer_id=writer_id)
        except Exception as error:
            self._reconcile(sent, actor_id=actor_id)
            raise DeliveryUncertain(work_item_id) from error
        return self._record_delivery(sent, observation, actor_id=actor_id)

    async def _send_committed_async(self, sent: dict, send, *, actor_id: str) -> dict:
        """Async delivery for the same commit winner; cancellation never permits replay."""
        writer_id = sent["attempts"][-1]["id"]
        registered = False
        try:
            with self.repository.transaction() as connection:
                self.repository._verify(connection)
                self.repository._register_writer_effect(connection, writer_id=writer_id)
                registered = True
            try:
                observation = await send()
                if not isinstance(observation, DeliveryObservation):
                    raise TypeError("delivery adapter must return DeliveryObservation")
            finally:
                if registered:
                    with self.repository.transaction() as connection:
                        self.repository._verify(connection)
                        self.repository._release_writer_effect(connection, writer_id=writer_id)
        except asyncio.CancelledError:
            self._reconcile(sent, actor_id=actor_id)
            raise
        except Exception as error:
            self._reconcile(sent, actor_id=actor_id)
            raise DeliveryUncertain(sent["id"]) from error
        return self._record_delivery(sent, observation, actor_id=actor_id)

    def _record_delivery(
        self, sent: dict, observation: DeliveryObservation, *, actor_id: str
    ) -> dict:
        """Record only authenticated receipts or the server's proven process failure."""
        work_item_id = sent["id"]
        current = self.repository.get_work(work_item_id)
        if observation.task_received_receipt is not None:
            current = self.record_task_received(observation.task_received_receipt)
        if observation.process_failure is not None:
            current = self._record_process_failure(
                sent, observation.process_failure, actor_id=actor_id
            )
        return current

    def _record_process_failure(
        self, sent: dict, failure: ProcessFailureEvidence, *, actor_id: str
    ) -> dict:
        """Fail only the exact still-live attempt after Docker proves stop and cleanup."""
        dispatched = sent["attempts"][-1]
        current = self.repository.get_work(sent["id"])
        latest = current["attempts"][-1]
        if (
            failure.attempt_id != dispatched["id"]
            or failure.generation != dispatched["generation"]
            or current["id"] != sent["id"]
            or latest["id"] != dispatched["id"]
            or latest["generation"] != dispatched["generation"]
            or latest["state"] not in {"sent", "acknowledged", "running"}
            or current["accepted_result_id"] is not None
        ):
            # Result acceptance, cancellation, a newer attempt, or any stale
            # observation wins. It cannot be overwritten by an old process exit.
            return current
        evidence = TransitionEvidence(
            generation=failure.generation,
            expected_generation=failure.generation,
            process_exit_code=failure.exit_code,
            process_stopped=failure.process_stopped,
            container_removed=failure.container_removed,
            image_removed=failure.image_removed,
        )
        try:
            return self.repository.transition_attempt(
                attempt_id=failure.attempt_id,
                generation=failure.generation,
                expected_revision=latest["revision"],
                expected_state=latest["state"],
                target="failed",
                actor_id=actor_id,
                event_id=uuid4().hex,
                evidence=evidence,
            )
        except WorkConflict:
            # A result or another terminal observation may have won the CAS.
            return self.repository.get_work(sent["id"])

    def record_task_received(self, receipt: TaskReceivedReceiptV1) -> dict:
        """Consume one server-issued receipt without accepting an adapter actor.

        The exact durable replay path is intentionally available across a restart;
        it only returns existing history.  A new acknowledgement additionally
        requires this service's paired ``WorkOrigins`` runtime to validate the
        live receiver grant and lineage inside the same SQLite transaction.
        """
        if not isinstance(receipt, TaskReceivedReceiptV1):
            raise TypeError("authenticated task receipt required")
        try:
            with self.repository.transaction() as connection:
                self.repository._verify(connection)
                replay = self.repository._task_received_receipt_replay(
                    connection,
                    attempt_id=receipt.attempt.attempt_id,
                    generation=receipt.attempt.generation,
                    nonce=receipt.nonce,
                    receipt_hash=receipt.fingerprint(),
                )
                if replay is not None:
                    return replay
                if self.origins is None:
                    raise WorkConflict("authenticated task receipt owner is unavailable")
                validated = self.origins.validate_task_received_receipt(connection, receipt)
                return self.repository._record_task_received_receipt(connection, **validated)
        except (OriginDenied, OriginConflict, WorkConflict):
            # Spoofed, stale, revoked, and contradictory receipts never overwrite Work.
            # Returning the current durable row gives async adapters an idempotent,
            # fail-closed observation without turning rejection into a redelivery.
            return self.repository.get_work(receipt.attempt.work_item_id)

    def _reconcile(self, work: dict, *, actor_id: str) -> dict:
        attempt = work["attempts"][-1]
        try:
            return self.repository.transition_attempt(
                attempt_id=attempt["id"],
                generation=attempt["generation"],
                expected_revision=attempt["revision"],
                expected_state=attempt["state"],
                target="reconcile",
                actor_id=actor_id,
                event_id=uuid4().hex,
                evidence=TransitionEvidence(
                    generation=attempt["generation"], expected_generation=attempt["generation"]
                ),
            )
        except WorkConflict:
            return self.repository.get_work(work["id"])

    def settle_attempt(
        self,
        work_item_id: str,
        *,
        generation: int,
        content: bytes,
        artifacts,
        validate: Callable[[bytes], dict],
        validator_id: str,
        actor_id: str,
        before_register: Callable[[object], None] | None = None,
    ) -> dict:
        """Accept bytes only after explicit validation and durable publication.

        ``validate`` is the frozen contract's server-side validator. It must
        return evidence with ``valid is True`` or raise; a truthy error/string
        is not success. No validator or validity assertion comes from a worker.
        """
        work = self.repository.get_work(work_item_id)
        attempt = next(
            (item for item in work["attempts"] if item["generation"] == generation), None
        )
        if attempt is None:
            raise WorkConflict("unknown result generation")
        validation = validate(content)
        if not isinstance(validation, dict) or validation.get("valid") is not True:
            raise ValueError("result did not pass its contract validator")

        def accept(reference):
            # The artifact lock spans this DB reference, so GC cannot race it.
            return self.repository.register_result(
                attempt_id=attempt["id"],
                generation=generation,
                content_hash=reference.content_hash,
                immutable_location=reference.immutable_location,
                byte_length=reference.byte_length,
                validator_id=validator_id,
                validation_evidence=validation,
                actor_id=actor_id,
                before_register=before_register,
            )

        current = self.repository.get_work(work_item_id)
        latest = current["attempts"][-1]
        if latest["id"] == attempt["id"] and latest["state"] == "acknowledged":
            # Result validation never manufactures receipt evidence.  Only an
            # already durable authenticated receipt may precede execution.
            try:
                self.repository.transition_attempt(
                    attempt_id=attempt["id"],
                    generation=generation,
                    expected_revision=latest["revision"],
                    expected_state=latest["state"],
                    target="running",
                    actor_id=actor_id,
                    event_id=uuid4().hex,
                    evidence=TransitionEvidence(
                        generation=generation,
                        expected_generation=generation,
                        execution_started=True,
                    ),
                )
            except WorkConflict:
                pass  # Retain the artifact even if cancellation/expiry already won.
        result = artifacts.publish(content, accept)
        current = self.repository.get_work(work_item_id)
        latest = current["attempts"][-1]
        if (
            latest["id"] != attempt["id"]
            or latest["state"] != "running"
            or latest["result_id"] != result["id"]
        ):
            return current
        # Re-read durable bytes before the acceptance CAS, including a retry that
        # reuses an existing content hash. Hash alone never proves retrievability.
        from cli_agent_orchestrator.services.step_output_store import ArtifactRef

        artifacts.read(
            ArtifactRef(result["content_hash"], result["immutable_location"], result["byte_length"])
        )
        return self.repository.transition_attempt(
            attempt_id=attempt["id"],
            generation=generation,
            expected_revision=latest["revision"],
            expected_state="running",
            target="finished",
            actor_id=actor_id,
            event_id=uuid4().hex,
            evidence=TransitionEvidence(
                generation=generation,
                expected_generation=generation,
                result_durable=True,
                result_validated=True,
                children_settled=True,
            ),
        )

    def _require_workflow_task_received(
        self, connection, binding, *, require_live_authority: bool = True
    ) -> dict:
        if self.origins is None:
            raise WorkConflict("workflow receiver authority is unavailable")
        if require_live_authority:
            self.origins._require_workflow_receiver_action(
                connection, binding, action="task_received"
            )
        receipt = self.repository._workflow_step_task_received_receipt(
            connection, binding.work_attempt_id, binding.work_generation
        )
        acceptance = connection.execute(
            "SELECT * FROM work_workflow_step_receiver_acceptances "
            "WHERE attempt_id=? AND generation=?",
            (binding.work_attempt_id, binding.work_generation),
        ).fetchone()
        expected_acceptance = self.origins._workflow_receiver_acceptance_hash(
            binding_id=binding.binding_id,
            attempt_ref=WorkAttemptRef(
                work_item_id=binding.work_item_id,
                attempt_id=binding.work_attempt_id,
                generation=binding.work_generation,
            ),
            receiver_subject_ref=binding.receiver_subject_ref,
            receiver_authorization_ref=binding.receiver_authorization_ref,
            delivery_id=binding.delivery_id,
            delivery_hash=binding.delivery_hash,
        )
        if receipt is None or acceptance is None:
            raise WorkConflict("workflow result requires a durable authenticated task receipt")
        expected_receipt = (
            binding.binding_id,
            binding.work_attempt_id,
            binding.work_generation,
            binding.work_item_id,
            binding.job_id,
            binding.receiver_subject_ref.subject_id,
            binding.receiver_subject_ref.revision,
            "receiver",
            binding.receiver_authorization_ref.revision,
            binding.receiver_grant_id,
            binding.receiver_grant_revision,
            binding.delivery_id,
            binding.delivery_hash,
        )
        actual_receipt = (
            receipt["binding_id"],
            receipt["attempt_id"],
            receipt["generation"],
            receipt["work_item_id"],
            receipt["job_id"],
            receipt["receiver_subject_id"],
            receipt["receiver_subject_revision"],
            receipt["receiver_authorization_kind"],
            receipt["receiver_authorization_revision"],
            receipt["receiver_grant_id"],
            receipt["receiver_grant_revision"],
            receipt["delivery_id"],
            receipt["delivery_hash"],
        )
        if (
            actual_receipt != expected_receipt
            or not isinstance(receipt["nonce"], str)
            or not receipt["nonce"]
            or not isinstance(receipt["receipt_hash"], str)
            or len(receipt["receipt_hash"]) != 64
            or any(ch not in "0123456789abcdef" for ch in receipt["receipt_hash"])
            or acceptance["binding_id"] != binding.binding_id
            or acceptance["acceptance_sha256"] != expected_acceptance
        ):
            raise WorkConflict("durable workflow task receipt does not match its binding")
        return dict(receipt)

    def _authenticate_workflow_result(self, connection, *, attempt_credential, receiver_credential):
        from cli_agent_orchestrator.services.work_attempt_credential import (
            WorkAttemptCredentialRejected,
            WorkAttemptCredentials,
        )

        credentials = WorkAttemptCredentials(self.repository, origins=self.origins)
        try:
            attempt = credentials.authenticate_in_transaction(
                connection, attempt_credential, allow_finished=True  # gitleaks:allow
            )
            receiver = credentials.authenticate_receiver_in_transaction(
                connection, receiver_credential, allow_finished=True  # gitleaks:allow
            )
        except WorkAttemptCredentialRejected as error:
            raise WorkConflict(
                "workflow result credentials are expired, revoked, or stale"
            ) from error
        if (attempt.attempt_id, attempt.generation, attempt.work_item_id) != (
            receiver.attempt_id,
            receiver.generation,
            receiver.work_item_id,
        ) or receiver.binding_id is None:
            raise WorkConflict("workflow result credentials do not identify one managed binding")
        binding = self.origins._workflow_binding_for_attempt(
            connection,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            work_item_id=attempt.work_item_id,
        )
        if binding is None or binding.binding_id != receiver.binding_id:
            raise WorkConflict("workflow result credentials have no exact immutable binding")
        self.origins._require_workflow_receiver_action(connection, binding, action="task_result")
        if (
            attempt.job_id != binding.job_id
            or attempt.grant_id != binding.grant_id
            or attempt.grant_revision != binding.grant_revision
            or attempt.contract_hash != binding.contract_hash
            or receiver.receiver_subject_id != binding.receiver_subject_ref.subject_id
            or receiver.receiver_subject_revision != binding.receiver_subject_ref.revision
            or receiver.receiver_authorization_revision
            != binding.receiver_authorization_ref.revision
            or receiver.receiver_grant_id != binding.receiver_grant_id
            or receiver.receiver_grant_revision != binding.receiver_grant_revision
            or receiver.delivery_id != binding.delivery_id
            or receiver.delivery_hash != binding.delivery_hash
        ):
            raise WorkConflict("workflow result credential differs from its immutable binding")
        self._require_workflow_task_received(connection, binding)
        return attempt, receiver, binding, credentials

    def submit_workflow_step_result(
        self,
        *,
        attempt_credential: bytes,
        receiver_credential: bytes,
        result: WorkflowStepResultV1,
    ) -> dict:
        """Accept only receiver-authenticated result bytes for one ACKed workflow attempt."""
        if self.origins is None or self.artifacts is None:
            raise WorkConflict("managed workflow result owner is not configured")
        if not isinstance(result, WorkflowStepResultV1):
            raise ValueError("typed workflow result envelope required")
        content = result.canonical_bytes()
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            attempt, receiver, binding, _credentials = self._authenticate_workflow_result(
                connection,
                attempt_credential=attempt_credential,
                receiver_credential=receiver_credential,
            )
            work = self.repository._work(connection, binding.work_item_id)
            if work["attempts"][-1]["id"] != binding.work_attempt_id:
                raise WorkConflict("workflow result attempt is no longer current")
            if work["accepted_result_id"] is not None:
                accepted = self.read_accepted_workflow_result(binding)
                if accepted is None or accepted.canonical_bytes != content:
                    raise WorkConflict("workflow result conflicts with the accepted bytes")
                return work
            if (
                attempt.attempt_id != binding.work_attempt_id
                or attempt.generation != binding.work_generation
            ):
                raise WorkConflict("workflow result attempt generation changed")
            actor_id = self.repository._job(connection, binding.job_id)["principal_id"]

        validation = validate_workflow_step_result(binding, content)

        def before_register(connection):
            self.repository._verify(connection)
            _attempt, _receiver, current_binding, _credentials = self._authenticate_workflow_result(
                connection,
                attempt_credential=attempt_credential,
                receiver_credential=receiver_credential,
            )
            if current_binding != binding:
                raise WorkConflict("workflow binding changed before result acceptance")

        settled = self.settle_attempt(
            binding.work_item_id,
            generation=binding.work_generation,
            content=content,
            artifacts=self.artifacts,
            validate=lambda raw: validate_workflow_step_result(binding, raw),
            validator_id="workflow-step-result-v1",
            actor_id=actor_id,
            before_register=before_register,
        )
        latest = settled["attempts"][-1]
        if (
            settled["accepted_result_id"] is None
            or latest["id"] != binding.work_attempt_id
            or latest["generation"] != binding.work_generation
            or latest["state"] != "finished"
        ):
            raise WorkConflict("workflow result was not durably accepted by Work")
        accepted = self.read_accepted_workflow_result(binding)
        if (
            accepted is None
            or accepted.accepted_result_id != settled["accepted_result_id"]
            or accepted.content_hash != hashlib.sha256(content).hexdigest()
            or accepted.canonical_bytes != content
        ):
            raise WorkConflict("workflow result was not durably accepted by Work")
        return settled

    def read_accepted_workflow_result(self, binding) -> AcceptedWorkflowStepResult | None:
        """Rehydrate and revalidate one durable accepted result after process restart."""
        if self.artifacts is None or self.origins is None:
            raise WorkConflict("managed workflow result reader is not configured")
        from cli_agent_orchestrator.services.step_output_store import ArtifactRef
        from cli_agent_orchestrator.services.work_workflow import WorkWorkflowOrigins

        try:
            exact = self.workflow_origins.read_step_binding(
                tier=binding.tier,
                run_id=binding.run_id,
                run_generation=binding.run_generation,
                step_id=binding.step_id,
                workflow_step_attempt=binding.workflow_step_attempt,
                historical=True,
            )
        except Exception as error:
            raise WorkConflict("workflow result binding could not be revalidated") from error
        if (
            exact is None
            or exact != binding
            or exact.computed_fingerprint() != exact.binding_fingerprint
        ):
            raise WorkConflict("workflow result binding is absent, changed, or corrupt")
        with self.repository.read_snapshot() as connection:
            self.repository._verify(connection)
            work = self.repository._work(connection, exact.work_item_id)
            attempt = connection.execute(
                "SELECT * FROM work_attempts WHERE id=? AND generation=?",
                (exact.work_attempt_id, exact.work_generation),
            ).fetchone()
            if work["accepted_result_id"] is None:
                return None
            # The private submit endpoint requires both receiver actions to be
            # live when it accepts bytes. Once accepted, projection relies on
            # the immutable receipt/acceptance proof and artifact; later grant
            # expiry or revocation cannot erase that historical fact.
            self._require_workflow_task_received(connection, exact, require_live_authority=False)
            result_row = connection.execute(
                "SELECT * FROM work_results WHERE id=?",
                (work["accepted_result_id"],),
            ).fetchone()
            if (
                result_row is None
                or attempt is None
                or work["id"] != exact.work_item_id
                or work["job_id"] != exact.job_id
                or work["attempts"][-1]["id"] != exact.work_attempt_id
                or work["attempts"][-1]["generation"] != exact.work_generation
                or result_row["attempt_id"] != exact.work_attempt_id
                or attempt["result_id"] != result_row["id"]
                or attempt["state"] != "finished"
                or work["state"] != "succeeded"
                or result_row["validation_state"] != "verified"
                or result_row["validator_id"] != "workflow-step-result-v1"
            ):
                raise WorkConflict("accepted workflow result reference is inconsistent")

            def unique_pairs(pairs):
                decoded = {}
                for key, value in pairs:
                    if key in decoded:
                        raise ValueError("duplicate validation evidence key")
                    decoded[key] = value
                return decoded

            def reject_constant(_value):
                raise ValueError("non-finite validation evidence number")

            try:
                evidence = json.loads(
                    result_row["validation_evidence"],
                    object_pairs_hook=unique_pairs,
                    parse_constant=reject_constant,
                )
            except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as error:
                raise WorkConflict(
                    "accepted workflow result validation evidence is corrupt"
                ) from error
            ref = ArtifactRef(
                result_row["content_hash"],
                result_row["immutable_location"],
                result_row["byte_length"],
            )
            raw = self.artifacts.read(ref)
        validation = validate_workflow_step_result(exact, raw)
        if evidence != validation:
            raise WorkConflict("accepted workflow result validation evidence changed")
        return AcceptedWorkflowStepResult(
            binding_id=exact.binding_id,
            binding_fingerprint=exact.provision_fingerprint,
            tier=exact.tier,
            run_id=exact.run_id,
            run_generation=exact.run_generation,
            step_id=exact.step_id,
            workflow_step_attempt=exact.workflow_step_attempt,
            work_item_id=exact.work_item_id,
            work_attempt_id=exact.work_attempt_id,
            work_generation=exact.work_generation,
            delivery_id=exact.delivery_id,
            delivery_hash=exact.delivery_hash,
            accepted_result_id=result_row["id"],
            content_hash=result_row["content_hash"],
            byte_length=result_row["byte_length"],
            canonical_bytes=raw,
            result=WorkflowStepResultV1.from_json_bytes(raw),
        )

    def read_workflow_step_state(self, binding, *, connection=None) -> WorkflowStepWorkState:
        """Read raw durable Work states for the exact immutable workflow binding.

        This reader does not interpret terminal observations or authorize retry.
        It uses one SQLite snapshot for the binding, bound attempt, current
        attempt, and item state so callers can distinguish a current terminal
        attempt from a stale binding after an explicit Work replacement.
        """
        from cli_agent_orchestrator.services.work_workflow import WorkWorkflowOrigins

        try:
            attempt_id = binding.work_attempt_id
            generation = binding.work_generation
            work_item_id = binding.work_item_id
            binding_id = binding.binding_id
        except AttributeError as error:
            raise WorkConflict("workflow Work state requires an immutable binding") from error
        if (
            not isinstance(binding_id, str)
            or not binding_id
            or not isinstance(attempt_id, str)
            or not attempt_id
            or not isinstance(work_item_id, str)
            or not work_item_id
            or type(generation) is not int
            or generation <= 0
        ):
            raise WorkConflict("workflow Work state binding identity is invalid")

        def read(snapshot) -> WorkflowStepWorkState:
            if not snapshot.in_transaction:
                raise WorkConflict("workflow Work state reads require a stable SQLite snapshot")
            self.repository._verify(snapshot)
            exact = self.workflow_origins.read_binding_for_attempt(
                attempt_id,
                generation,
                work_item_id,
                connection=snapshot,
                historical=True,
            )
            if exact is None or exact != binding:
                raise WorkConflict("workflow Work state binding is absent or changed")
            work = self.repository._work(snapshot, work_item_id)
            if work["job_id"] != exact.job_id or not work["attempts"]:
                raise WorkConflict("workflow binding does not identify a durable Work item")
            attempt = next(
                (
                    item
                    for item in work["attempts"]
                    if item["id"] == attempt_id and item["generation"] == generation
                ),
                None,
            )
            if attempt is None:
                raise WorkConflict("workflow binding does not identify a durable Work attempt")
            current = work["attempts"][-1]
            return WorkflowStepWorkState(
                binding_id=exact.binding_id,
                work_item_id=work["id"],
                work_attempt_id=attempt["id"],
                work_generation=attempt["generation"],
                work_state=work["state"],
                work_revision=work["revision"],
                attempt_state=attempt["state"],
                attempt_revision=attempt["revision"],
                current_attempt_id=current["id"],
                current_generation=current["generation"],
                current_attempt_state=current["state"],
                accepted_result_id=work["accepted_result_id"],
                cleanup_state=attempt["cleanup_state"],
            )

        if connection is not None:
            return read(connection)
        with self.repository.read_snapshot() as snapshot:
            return read(snapshot)

    def read_result(self, work_item_id: str, *, artifacts: ImmutableResultStore) -> bytes:
        from cli_agent_orchestrator.services.step_output_store import ArtifactRef

        work = self.repository.get_work(work_item_id)
        if work["accepted_result_id"] is None:
            raise KeyError("work has no accepted result")
        result = self.repository.get_result(work["accepted_result_id"])
        return artifacts.read(
            ArtifactRef(result["content_hash"], result["immutable_location"], result["byte_length"])
        )

    def join_children(
        self, work_item_id: str, *, actor_id: str, timeout_seconds: float = 0
    ) -> dict:
        """Bounded, restartable join; uncertain children are visible, never success."""
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 0 <= timeout_seconds <= 60
        ):
            raise ValueError("join timeout must be between 0 and 60 seconds")
        self.repository.get_work(work_item_id)
        deadline = time.monotonic() + timeout_seconds
        while True:
            self.repository.reconcile_expired(actor_id=actor_id, parent_work_item_id=work_item_id)
            children = self.repository.children(work_item_id)
            settled = all(
                child["state"] in {"succeeded", "failed", "reconcile", "cancelled"}
                for child in children
            )
            successful = all(child["state"] == "succeeded" for child in children)
            remaining = deadline - time.monotonic()
            if settled or remaining <= 0:
                return dict(
                    work_item_id=work_item_id,
                    children=children,
                    settled=settled,
                    successful=successful,
                    required_action=(
                        "reconcile_children"
                        if any(child["state"] == "reconcile" for child in children)
                        else None
                    ),
                )
            # Repository snapshots have closed; never hold SQLite locks while waiting.
            time.sleep(min(0.1, remaining))

    def recover_incomplete_proxy_effects(self) -> int:
        """Recover open MCP effects only after each exact process owner has exited.

        Called at supervisor startup. A missing or unverifiable process identity
        is not evidence of exit, so its intent stays open and replay remains
        blocked by the durable request uniqueness constraint.
        """
        from cli_agent_orchestrator.services.work_mcp_proxy import WorkMcpProxy

        proxy = WorkMcpProxy(self.repository)
        recovered = 0
        for attempt_id, generation in proxy.incomplete_effect_owners():
            with self.repository.cleanup_owner_lock(attempt_id, generation):
                if self._proxy_process_owner_stopped(attempt_id, generation):
                    recovered += proxy.recover_incomplete_effects(attempt_id, generation)
        for attempt_id, generation in proxy.incomplete_issue_owners():
            with self.repository.cleanup_owner_lock(attempt_id, generation):
                if self._proxy_process_owner_stopped(attempt_id, generation):
                    recovered += proxy.recover_incomplete_issues(attempt_id, generation)
        return recovered

    def _proxy_process_owner_stopped(self, attempt_id: str, generation: int) -> bool:
        """Observe the durable process pair without signaling or guessing."""
        from cli_agent_orchestrator.services.work_process_supervisor import WorkProcessSupervisor

        try:
            bwrap_identity = self.repository.read_bubblewrap_process_identity(
                attempt_id, generation
            )
            if bwrap_identity is not None:
                return _original_bubblewrap_pair_gone(bwrap_identity) is True
            identity = self.repository.read_process_identity(attempt_id, generation)
            if identity is None:
                return False
            return WorkProcessSupervisor().original_pair_terminated(identity) is True
        except Exception:
            return False

    def recover_process_cleanup(
        self, attempt_id: str, *, supervisor=None, backend_reconciler=None, actor_id: str
    ) -> dict:
        """Reattach and clean only the current, durably held reconcile process."""
        from cli_agent_orchestrator.services.work_process_supervisor import (
            WorkProcessState,
            WorkProcessSupervisor,
        )

        attempt = self.repository.get_attempt(attempt_id)
        with self.repository.cleanup_owner_lock(attempt_id, attempt["generation"]):
            attempt = self.repository.get_attempt(attempt_id)
            claimed = self.repository.claim_reconciled_cleanup(
                attempt_id,
                generation=attempt["generation"],
                expected_revision=attempt["revision"],
                actor_id=actor_id,
            )
            current = claimed["attempts"][-1]
            if current["cleanup_state"] == "complete":
                return claimed
            try:
                bwrap_identity = self.repository.read_bubblewrap_process_identity(
                    attempt_id, current["generation"]
                )
                if bwrap_identity is not None:
                    confirmed = (
                        supervisor.cleanup_bubblewrap_identity(bwrap_identity) is True
                        if supervisor is not None
                        else _original_bubblewrap_pair_gone(bwrap_identity)
                    )
                else:
                    identity = self.repository.read_process_identity(
                        attempt_id, current["generation"]
                    )
                    confirmed = False
                    if identity is not None:
                        if supervisor is not None:
                            try:
                                confirmed = (
                                    supervisor.terminate(supervisor.reattach(identity))
                                    is WorkProcessState.TERMINATED
                                )
                            except Exception:
                                pass
                        if not confirmed:
                            observer = (
                                supervisor if supervisor is not None else WorkProcessSupervisor()
                            )
                            confirmed = observer.original_pair_terminated(identity) is True
                    elif backend_reconciler is not None:
                        reconcile_attempt = getattr(backend_reconciler, "reconcile_attempt", None)
                        if callable(reconcile_attempt):
                            cleanup = reconcile_attempt(attempt_id, current["generation"])
                            confirmed = (
                                type(cleanup) is dict
                                and cleanup.get("container_removed") is True
                                and cleanup.get("image_removed") is True
                            )
            except Exception:
                confirmed = False
            if confirmed:
                try:
                    from cli_agent_orchestrator.services.work_mcp_proxy import WorkMcpProxy

                    proxy = WorkMcpProxy(self.repository)
                    proxy.recover_incomplete_effects(attempt_id, current["generation"])
                    proxy.recover_incomplete_issues(attempt_id, current["generation"])
                except Exception:
                    # Do not report recovery complete while an open effect could
                    # not be durably recorded as uncertain.
                    confirmed = False
            return self.repository.record_cleanup(
                attempt_id,
                generation=current["generation"],
                expected_revision=current["revision"],
                state="complete" if confirmed else "failed",
                actor_id=actor_id,
            )

    def cleanup_attempt(
        self, attempt_id: str, cleanup: Callable[[], bool], *, actor_id: str
    ) -> dict:
        attempt = self.repository.get_attempt(attempt_id)
        with self.repository.cleanup_owner_lock(attempt_id, attempt["generation"]):
            attempt = self.repository.get_attempt(attempt_id)
            if attempt["cleanup_state"] in {"pending", "complete"}:
                # Pending after a process crash requires observation, not blind replay.
                return self.repository.get_work(attempt["work_item_id"])
            self.repository.record_cleanup(
                attempt_id,
                generation=attempt["generation"],
                expected_revision=attempt["revision"],
                state="pending",
                actor_id=actor_id,
            )
            try:
                confirmed = cleanup() is True
            except Exception:
                confirmed = False
            current = self.repository.get_attempt(attempt_id)
            return self.repository.record_cleanup(
                attempt_id,
                generation=attempt["generation"],
                expected_revision=current["revision"],
                state="complete" if confirmed else "failed",
                actor_id=actor_id,
            )
