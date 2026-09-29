"""Own the durable intent/external-effect boundary for work execution.

Repository methods only persist facts. This service invokes a delivery adapter
after its intent commits, and never infers delivery from terminal allocation.
Public entrypoints must supply server-verified admission evidence.
"""

import asyncio
import hashlib
import math
import os
import select
import signal
import stat
import time
from pathlib import Path
from typing import Callable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, StrictBool, model_validator

from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator.services.work_origin import (
    OriginDenied,
    OriginConflict,
    TaskReceivedReceiptV1,
    WorkOrigins,
)
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence


class DeliveryObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # Kept only to deserialize legacy adapter observations.  These unauthenticated
    # booleans are never used as durable acknowledgement evidence.
    task_received: StrictBool = False
    execution_started: StrictBool = False
    task_received_receipt: TaskReceivedReceiptV1 | None = None

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


def _original_bubblewrap_pair_gone(identity: dict[str, object]) -> bool:
    """Prove both original PIDs exited without treating a live mismatch as exit."""
    try:
        current_boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if not current_boot:
            return False
        if current_boot != identity["boot_id"]:
            return True
        for role in ("monitor", "init"):
            try:
                _, start_time, state = _read_bubblewrap_proc_stat(identity[f"{role}_pid"])
            except FileNotFoundError:
                continue
            if start_time == identity[f"{role}_start_time_ticks"] and state not in {"Z", "X", "x"}:
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


def _orphaned_bubblewrap_init_matches(identity: dict[str, object]) -> bool:
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
    return all(
        _bubblewrap_namespace_identity(identity["init_pid"], kind) == identity[f"{kind}_namespace"]
        for kind in ("pid", "net", "ipc")
    )


def _cleanup_orphaned_bubblewrap_init(identity: dict[str, object]) -> bool:
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


def _cleanup_bubblewrap_identity(identity: dict[str, object]) -> bool:
    """Signal only a twice-validated live pair through pinned pidfds."""
    if identity.get("version") != 1 or identity.get("kind") != "bubblewrap":
        return False
    monitor_pid = identity["monitor_pid"]
    init_pid = identity["init_pid"]
    expected = {key: identity[key] for key in _BWRAP_IDENTITY_KEYS}
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
    def __init__(self, repository: WorkRepository, *, origins: WorkOrigins | None = None):
        self.repository = repository
        if origins is not None and (
            not isinstance(origins, WorkOrigins)
            or Path(origins.repository.path).resolve() != Path(repository.path).resolve()
        ):
            raise ValueError("task receipt origin must use this work repository")
        self.origins = origins

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

    def _record_delivery(self, sent, observation, *, actor_id):
        """Only an authenticated receiver receipt can acknowledge delivery."""
        work_item_id = sent["id"]
        del actor_id
        if observation.task_received_receipt is None:
            # Legacy adapter telemetry remains readable but cannot promote Work.
            return self.repository.get_work(work_item_id)
        return self.record_task_received(observation.task_received_receipt)

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

    def read_result(self, work_item_id: str, *, artifacts) -> bytes:
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
                        supervisor.cleanup_bubblewrap_identity(bwrap_identity)
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
                        reconcile_attempt = getattr(
                            backend_reconciler, "reconcile_attempt", None
                        )
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
