"""Pure evidence-gated transitions for durable work."""

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

from cli_agent_orchestrator.models.work import AttemptState, PositiveInt, WorkState


class TransitionConflict(ValueError):
    """The supplied state or evidence cannot authorize the transition."""


class TransitionEvidence(BaseModel):
    """Verified facts supplied by the authority; observations default to unproven.

    ``result_durable`` means the content is retrievable, not merely that a digest
    exists. The caller checks these facts and commits the transition with CAS.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    generation: PositiveInt
    expected_generation: PositiveInt
    contract_confirmed: StrictBool = False
    grant_confirmed: StrictBool = False
    capacity_confirmed: StrictBool = False
    reservations_confirmed: StrictBool = False
    result_durable: StrictBool = False
    result_validated: StrictBool = False
    children_settled: StrictBool = False
    prior_stopped: StrictBool = False
    reconciliation_authorized: StrictBool = False
    task_received: StrictBool = False
    execution_started: StrictBool = False
    process_exit_code: StrictInt | None = None
    process_stopped: StrictBool = False
    container_removed: StrictBool = False
    image_removed: StrictBool = False


def _fence(evidence: TransitionEvidence) -> None:
    if evidence.generation != evidence.expected_generation:
        raise TransitionConflict("stale attempt generation")


def transition(
    state: WorkState | str, target: WorkState | str, *, evidence: TransitionEvidence
) -> WorkState:
    """Reduce an expected work state; the repository must CAS that same state.

    Terminal states never transition, including to themselves. Retry creates a
    new attempt after reconciliation; it never rewrites an attempt's history.
    """
    state, target = WorkState(state), WorkState(target)
    _fence(evidence)
    allowed = {
        WorkState.QUEUED: {WorkState.RUNNING, WorkState.FAILED, WorkState.CANCELLED},
        WorkState.RUNNING: {
            WorkState.WAITING_CHILDREN,
            WorkState.SUCCEEDED,
            WorkState.FAILED,
            WorkState.RECONCILE,
            WorkState.CANCELLED,
        },
        WorkState.WAITING_CHILDREN: {
            WorkState.SUCCEEDED,
            WorkState.FAILED,
            WorkState.RECONCILE,
            WorkState.CANCELLED,
        },
        WorkState.RECONCILE: {WorkState.QUEUED, WorkState.FAILED, WorkState.CANCELLED},
    }
    if target not in allowed.get(state, set()):
        raise TransitionConflict(f"work cannot transition from {state.value} to {target.value}")
    if target == WorkState.RUNNING and not all(
        (
            evidence.contract_confirmed,
            evidence.grant_confirmed,
            evidence.capacity_confirmed,
            evidence.reservations_confirmed,
        )
    ):
        raise TransitionConflict("admission requires contract, grant, capacity and reservations")
    if target == WorkState.QUEUED and not (
        evidence.prior_stopped and evidence.reconciliation_authorized
    ):
        raise TransitionConflict(
            "retry requires stopped prior execution and authorized reconciliation"
        )
    if target == WorkState.SUCCEEDED and not (
        evidence.result_durable and evidence.result_validated and evidence.children_settled
    ):
        raise TransitionConflict(
            "success requires a retrievable validated result and completed join"
        )
    return target


def delivery_transition(
    state: AttemptState | str, target: AttemptState | str, *, evidence: TransitionEvidence
) -> AttemptState:
    """Validate delivery evidence without producing events or executing effects.

    A direct sent-to-running observation requires task receipt too. The caller
    persists receipt and execution events in that order in the same transaction.
    """
    state, target = AttemptState(state), AttemptState(target)
    _fence(evidence)
    allowed = {
        AttemptState.PLANNED: {AttemptState.SENT, AttemptState.FAILED, AttemptState.CANCELLED},
        AttemptState.SENT: {
            AttemptState.ACKNOWLEDGED,
            AttemptState.RUNNING,
            AttemptState.FAILED,
            AttemptState.RECONCILE,
            AttemptState.CANCELLED,
        },
        AttemptState.ACKNOWLEDGED: {
            AttemptState.RUNNING,
            AttemptState.FAILED,
            AttemptState.RECONCILE,
            AttemptState.CANCELLED,
        },
        AttemptState.RUNNING: {
            AttemptState.FINISHED,
            AttemptState.FAILED,
            AttemptState.RECONCILE,
            AttemptState.CANCELLED,
        },
        AttemptState.RECONCILE: {AttemptState.CANCELLED},
    }
    if target not in allowed.get(state, set()):
        raise TransitionConflict(f"attempt cannot transition from {state.value} to {target.value}")
    if target == AttemptState.ACKNOWLEDGED and not evidence.task_received:
        raise TransitionConflict("acknowledgement requires a task receipt")
    if target == AttemptState.RUNNING and not (
        evidence.execution_started
        and (evidence.task_received or state == AttemptState.ACKNOWLEDGED)
    ):
        raise TransitionConflict("running requires task receipt and execution evidence")
    if target == AttemptState.FINISHED and not (
        evidence.result_durable and evidence.result_validated
    ):
        raise TransitionConflict("finished requires a retrievable validated result")
    if target == AttemptState.FAILED and state != AttemptState.PLANNED:
        if not (
            evidence.process_exit_code is not None
            and 1 <= evidence.process_exit_code <= 255
            and evidence.process_stopped
            and evidence.container_removed
            and evidence.image_removed
        ):
            raise TransitionConflict(
                "post-dispatch process failure requires a nonzero exit and verified cleanup"
            )
    return target
