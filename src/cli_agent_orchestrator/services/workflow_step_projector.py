"""Restartable projection of durable Work outcomes into workflow state.

The projector reads immutable bindings and exact Work state. The workflow
journal owns the atomic CAS that completes or fails a pending step.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cli_agent_orchestrator.models.work_origin import WorkflowStepResultV1
from cli_agent_orchestrator.services import workflow_journal

logger = logging.getLogger(__name__)


class WorkflowProjectionError(RuntimeError):
    """A durable workflow binding or accepted-result proof failed verification."""


@dataclass(frozen=True, slots=True)
class ProjectionOutcome:
    step_id: str
    status: Literal[
        "projected",
        "already_projected",
        "failed",
        "already_failed",
        "binding_missing",
        "result_pending",
        "work_reconcile",
        "work_cancelled",
        "work_stale",
    ]


def _repository_path(repository) -> Path:
    try:
        return Path(repository.path).resolve()
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("workflow projection requires a durable Work repository") from error


def _required(value, field: str):
    try:
        return getattr(value, field)
    except AttributeError as error:
        raise WorkflowProjectionError(f"durable workflow proof is missing {field}") from error


def _binding_fingerprint(binding) -> str:
    fingerprint = getattr(binding, "provision_fingerprint", None)
    if fingerprint is None:
        fingerprint = getattr(binding, "binding_fingerprint", None)
    if not isinstance(fingerprint, str) or not fingerprint:
        raise WorkflowProjectionError("durable workflow binding fingerprint is missing")
    return fingerprint


class WorkflowStepProjector:
    """Project Work results for durable ``work_pending`` steps after restart.

    ``repository``, ``workflow_origins`` and ``work_service`` must all belong to
    the same durable SQLite store.  Every invocation re-reads journal state,
    the exact immutable binding, and the accepted result; no process-local
    result cache participates in recovery.
    """

    def __init__(self, repository, workflow_origins, work_service):
        path = _repository_path(repository)
        if (
            _repository_path(getattr(workflow_origins, "repository", None)) != path
            or _repository_path(getattr(work_service, "repository", None)) != path
        ):
            raise ValueError("workflow projector dependencies must use the same Work repository")
        if not callable(getattr(workflow_origins, "read_step_binding", None)):
            raise ValueError("workflow step binding reader is required")
        if not callable(getattr(work_service, "read_workflow_step_state", None)):
            raise ValueError("durable workflow step state reader is required")
        if not callable(getattr(work_service, "read_accepted_workflow_result", None)):
            raise ValueError("accepted workflow result reader is required")
        self.repository = repository
        self.workflow_origins = workflow_origins
        self.work_service = work_service

    def project_pending_for_run(self, run_id: str) -> tuple[ProjectionOutcome, ...]:
        """Project every currently pending managed step for ``run_id``.

        A missing binding or an accepted result that has not arrived yet is a
        normal recovery state and remains pending.  Corrupt or mismatched proof
        raises, so the caller cannot continue the workflow on uncertain data.
        """
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("workflow run ID is required")
        run = workflow_journal.get_run(run_id)
        if run is None:
            raise WorkflowProjectionError("workflow run does not exist")

        outcomes: list[ProjectionOutcome] = []
        for step in workflow_journal.get_steps(run_id):
            if step.state != "work_pending":
                continue
            try:
                generation = int(run.generation)
            except (TypeError, ValueError) as error:
                raise WorkflowProjectionError("workflow run generation is invalid") from error
            if generation <= 0 or str(generation) != run.generation:
                raise WorkflowProjectionError("workflow run generation is invalid")

            binding = self.workflow_origins.read_step_binding(
                tier=run.tier,
                run_id=run.run_id,
                run_generation=generation,
                step_id=step.step_id,
                workflow_step_attempt=step.attempts,
                historical=True,
            )
            if binding is None:
                outcomes.append(ProjectionOutcome(step.step_id, "binding_missing"))
                continue
            self._verify_binding_identity(
                binding,
                tier=run.tier,
                run_id=run.run_id,
                run_generation=generation,
                step_id=step.step_id,
                step_attempt=step.attempts,
            )

            work_state = self.work_service.read_workflow_step_state(binding)
            work_status = self._classify_work_state(binding, work_state)
            if (
                work_status == "work_stale"
                or work_status == "work_reconcile"
                or work_status == "work_cancelled"
            ):
                outcomes.append(ProjectionOutcome(step.step_id, work_status))
                continue
            if work_status == "failed":
                status = workflow_journal.project_work_failure(
                    repository=self.repository,
                    run_id=run.run_id,
                    run_generation=generation,
                    tier=run.tier,
                    step_id=step.step_id,
                    step_attempt=step.attempts,
                    binding=binding,
                    work_state=work_state,
                    updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                )
                if status not in {"failed", "already_failed"}:
                    raise WorkflowProjectionError(
                        "workflow journal compare-and-set did not project the durable failure"
                    )
                outcomes.append(
                    ProjectionOutcome(
                        step.step_id, "failed" if status == "failed" else "already_failed"
                    )
                )
                continue

            accepted = self.work_service.read_accepted_workflow_result(binding)
            if work_status == "live":
                if accepted is not None:
                    raise WorkflowProjectionError(
                        "live Work state unexpectedly has an accepted result reference"
                    )
                outcomes.append(ProjectionOutcome(step.step_id, "result_pending"))
                continue
            if accepted is None:
                raise WorkflowProjectionError(
                    "durable successful Work state has no accepted result proof"
                )
            self._verify_accepted_result(binding, accepted)
            if _required(accepted, "accepted_result_id") != _required(
                work_state, "accepted_result_id"
            ):
                raise WorkflowProjectionError(
                    "accepted result reference differs from durable Work state"
                )

            status = workflow_journal.project_work_result(
                repository=self.repository,
                run_id=run.run_id,
                run_generation=generation,
                tier=run.tier,
                step_id=step.step_id,
                step_attempt=step.attempts,
                binding=binding,
                accepted_result=accepted,
                updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            if status not in {"projected", "already_projected"}:
                raise WorkflowProjectionError(
                    "workflow journal compare-and-set did not project the accepted result"
                )
            outcomes.append(
                ProjectionOutcome(
                    step.step_id, "projected" if status == "projected" else "already_projected"
                )
            )
        return tuple(outcomes)

    def project_pending_at_startup(self) -> tuple[ProjectionOutcome, ...]:
        """Rehydrate all pending managed results before the service accepts work."""
        outcomes: list[ProjectionOutcome] = []
        for run_id in workflow_journal.list_work_pending_run_ids():
            try:
                outcomes.extend(self.project_pending_for_run(run_id))
            except Exception:
                logger.exception(
                    "managed workflow startup projection failed for run %s; leaving it pending",
                    run_id,
                )
        return tuple(outcomes)

    @staticmethod
    def _verify_binding_identity(
        binding,
        *,
        tier: str,
        run_id: str,
        run_generation: int,
        step_id: str,
        step_attempt: int,
    ) -> None:
        expected = {
            "tier": tier,
            "run_id": run_id,
            "run_generation": run_generation,
            "step_id": step_id,
            "workflow_step_attempt": step_attempt,
        }
        for field, expected_value in expected.items():
            if _required(binding, field) != expected_value:
                raise WorkflowProjectionError(
                    f"durable workflow binding does not match journal {field}"
                )
        for field in ("run_generation", "workflow_step_attempt", "work_generation"):
            if type(_required(binding, field)) is not int or _required(binding, field) <= 0:
                raise WorkflowProjectionError(f"durable workflow binding has invalid {field}")
        for field in (
            "binding_id",
            "provision_fingerprint",
            "work_item_id",
            "work_attempt_id",
            "delivery_id",
            "delivery_hash",
        ):
            value = _required(binding, field)
            if value is None or value == "":
                raise WorkflowProjectionError(f"durable workflow binding has invalid {field}")
        _binding_fingerprint(binding)

    @staticmethod
    def _classify_work_state(
        binding, work_state
    ) -> Literal["live", "succeeded", "failed", "work_reconcile", "work_cancelled", "work_stale"]:
        """Require a coherent item/attempt pair before projecting any terminal outcome."""
        expected = {
            "binding_id": _required(binding, "binding_id"),
            "work_item_id": _required(binding, "work_item_id"),
            "work_attempt_id": _required(binding, "work_attempt_id"),
            "work_generation": _required(binding, "work_generation"),
        }
        for field, value in expected.items():
            if _required(work_state, field) != value:
                raise WorkflowProjectionError(f"durable Work state does not match binding {field}")

        for field in ("work_revision", "attempt_revision"):
            revision = _required(work_state, field)
            if type(revision) is not int or revision <= 0:
                raise WorkflowProjectionError(f"durable Work state has invalid {field}")

        current_attempt_id = _required(work_state, "current_attempt_id")
        current_generation = _required(work_state, "current_generation")
        current_state = _required(work_state, "current_attempt_state")
        if (
            not isinstance(current_attempt_id, str)
            or not current_attempt_id
            or type(current_generation) is not int
            or current_generation <= 0
            or not isinstance(current_state, str)
        ):
            raise WorkflowProjectionError("durable Work current-attempt identity is invalid")
        if (
            current_attempt_id != expected["work_attempt_id"]
            or current_generation != expected["work_generation"]
        ):
            return "work_stale"

        item_state = _required(work_state, "work_state")
        attempt_state = _required(work_state, "attempt_state")
        accepted_result_id = _required(work_state, "accepted_result_id")
        if not isinstance(item_state, str) or not isinstance(attempt_state, str):
            raise WorkflowProjectionError("durable Work state value is invalid")
        if accepted_result_id is not None and (
            not isinstance(accepted_result_id, str) or not accepted_result_id
        ):
            raise WorkflowProjectionError("durable Work accepted-result reference is invalid")

        if "reconcile" in {item_state, attempt_state, current_state}:
            if (item_state, attempt_state, current_state) != (
                "reconcile",
                "reconcile",
                "reconcile",
            ):
                raise WorkflowProjectionError("durable Work reconcile state is inconsistent")
            return "work_reconcile"
        if "cancelled" in {item_state, attempt_state, current_state}:
            if (item_state, attempt_state, current_state) != (
                "cancelled",
                "cancelled",
                "cancelled",
            ) or accepted_result_id is not None:
                raise WorkflowProjectionError("durable Work cancellation state is inconsistent")
            return "work_cancelled"
        if "failed" in {item_state, attempt_state, current_state}:
            if (item_state, attempt_state, current_state) != ("failed", "failed", "failed"):
                raise WorkflowProjectionError("durable Work failure state is inconsistent")
            if accepted_result_id is not None:
                raise WorkflowProjectionError("failed Work state has an accepted result reference")
            return "failed"
        if "succeeded" in {item_state, attempt_state, current_state} or "finished" in {
            item_state,
            attempt_state,
            current_state,
        }:
            if (item_state, attempt_state, current_state) != (
                "succeeded",
                "finished",
                "finished",
            ) or accepted_result_id is None:
                raise WorkflowProjectionError("durable Work success state is inconsistent")
            return "succeeded"

        live_attempt_states = {"planned", "sent", "acknowledged", "running"}
        if (
            item_state not in {"queued", "running", "waiting_children"}
            or attempt_state not in live_attempt_states
            or current_state != attempt_state
            or accepted_result_id is not None
        ):
            raise WorkflowProjectionError("durable Work state is unknown or inconsistent")
        return "live"

    @staticmethod
    def _verify_accepted_result(binding, accepted) -> None:
        expected = {
            "binding_id": _required(binding, "binding_id"),
            "binding_fingerprint": _binding_fingerprint(binding),
            "tier": _required(binding, "tier"),
            "run_id": _required(binding, "run_id"),
            "run_generation": _required(binding, "run_generation"),
            "step_id": _required(binding, "step_id"),
            "workflow_step_attempt": _required(binding, "workflow_step_attempt"),
            "work_item_id": _required(binding, "work_item_id"),
            "work_attempt_id": _required(binding, "work_attempt_id"),
            "work_generation": _required(binding, "work_generation"),
            "delivery_id": _required(binding, "delivery_id"),
            "delivery_hash": _required(binding, "delivery_hash"),
        }
        for field, expected_value in expected.items():
            if _required(accepted, field) != expected_value:
                raise WorkflowProjectionError(
                    f"accepted Work result does not match workflow binding {field}"
                )

        raw = _required(accepted, "canonical_bytes")
        digest = _required(accepted, "content_hash")
        if type(raw) is not bytes or not isinstance(digest, str):
            raise WorkflowProjectionError("accepted Work result bytes or hash are invalid")
        if hashlib.sha256(raw).hexdigest() != digest:
            raise WorkflowProjectionError("accepted Work result content hash does not match")
        byte_length = _required(accepted, "byte_length")
        if type(byte_length) is not int or byte_length != len(raw):
            raise WorkflowProjectionError("accepted Work result byte length does not match")

        try:
            parsed = WorkflowStepResultV1.from_json_bytes(raw)
            supplied = _required(accepted, "result")
            if parsed != supplied or parsed.canonical_bytes() != raw:
                raise ValueError("accepted envelope is not canonical")
        except (TypeError, ValueError) as error:
            raise WorkflowProjectionError("accepted Work result envelope is invalid") from error
        result_id = _required(accepted, "accepted_result_id")
        if not isinstance(result_id, str) or not result_id:
            raise WorkflowProjectionError("accepted Work result reference is invalid")
