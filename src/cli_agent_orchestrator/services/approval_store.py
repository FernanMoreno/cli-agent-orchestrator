"""The durable plan-approval record (issue #583 Bolt 2, unit ``approval-store``).

FR-8's re-approval mechanism, and nothing more. One row per approved plan, keyed by the ``plan_id`` that
``plan_identifier.compute`` derives from a run's execution-affecting fields: a changed plan produces a
different ``plan_id``, finds no row, and is refused until approved in its own right.

THIS MODULE DECIDES NOTHING ABOUT EXECUTION. ``components.md`` is explicit that ``PlanApprovalBinding``
"consumes the manifest; decides nothing about execution" — it answers "does an approval exist for this
``plan_id``?" and stops. Whether that permits a run is ``approval-gate``'s call (unit 7). Two places deciding
whether a run may proceed means the second one drifts, and a drifted authorisation check is the kind that looks
correct while permitting something.

THERE IS NO UPDATE, NO DELETE AND NO REPLACING UPSERT, AND THAT ABSENCE IS THE CENTRAL CONTROL. An update path
would let a caller point an existing approval at a changed plan: the row would read as approved, its timestamp
would look recent, and **work nobody reviewed would execute with a genuine approval behind it** — not a bypass
of the gate, but a corruption of the thing the gate consults. ``grant`` therefore uses ``INSERT ... ON CONFLICT DO NOTHING``, so
write-once is a property of the statement rather than of a caller remembering to look first.

ABSENCE AND STORE FAULTS BOTH REFUSE EXECUTION. ``approval_state`` distinguishes a missing approval
from a database error so the gate can report the appropriate remedy. ``is_approved`` retains its
boolean contract: both absence and an unreadable store return False.

``plan_id`` IS OPAQUE: stored and compared verbatim, never parsed, never normalised. A normalisation is how two
distinct plans could come to share one approval, and ``plan-v1:``'s versioned prefix exists precisely so a
future scheme is DISTINGUISHABLE rather than silently comparable.

ONE CONSEQUENCE AN OPERATOR WILL MEET, so it is written down. ``plan_id`` does not exist until
``manifest-freeze`` computes it at run start, so a plan that has never been started cannot have been approved —
**the first run of any new plan is refused by design.** The loop is: run refused, ``plan_id`` reported, approve,
re-run. That friction is temporary rather than intrinsic: Bolt 3's authoring sequence presents the plan and
takes approval BEFORE running. ``approval-gate``'s refusal message MUST carry the ``plan_id``, or the operator
has nothing to act on.
"""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from cli_agent_orchestrator.clients.postgres import (
    ConfigurationError,
    Connection,
    DatabaseError,
    DatabaseTarget,
    connect,
)
from cli_agent_orchestrator.clients.postgres.migrations import SchemaMismatch, verify_component
from cli_agent_orchestrator.clients.private_plan_postgres_schema import PRIVATE_PLAN_MIGRATIONS


@dataclass(frozen=True)
class PlanApproval:
    """One ``workflow_plan_approval`` row. Frozen: an approval is a record, not a mutable state."""

    plan_id: str
    approved_at: str
    approved_by: str


def _now() -> str:
    """UTC ISO timestamp, matching ``workflow_run.started_at``'s convention so the two compare alike."""
    return datetime.now(timezone.utc).isoformat()


def _connect() -> Connection:
    """Open a verified native transaction without modifying schema."""
    connection = connect(DatabaseTarget.from_settings())
    try:
        connection.begin()
        verify_component(connection, PRIVATE_PLAN_MIGRATIONS)
        return connection
    except BaseException:
        connection.close()
        raise


def grant(plan_id: str, approved_by: str) -> None:
    """Record an approval for ``plan_id``. Write-once: a repeated grant is a no-op.

    ``INSERT ... ON CONFLICT DO NOTHING`` rather than a plain ``INSERT`` so a second grant for an already-approved plan neither
    errors nor overwrites the original ``approved_at`` / ``approved_by``. Harmless to repeat, impossible to
    rewrite.
    """
    with closing(_connect()) as _owned_conn, _owned_conn as conn:
        conn.execute(
            "INSERT INTO workflow_plan_approval (plan_id, approved_at, approved_by) "
            "VALUES (%s, %s, %s) ON CONFLICT (plan_id) DO NOTHING",
            (plan_id, _now(), approved_by),
        )


# Keep an unreadable store distinct from a plan with no approval (issue #696).
APPROVED = "approved"
ABSENT = "absent"
UNKNOWN = "unknown"


def approval_state(plan_id: str) -> str:
    """Return APPROVED, ABSENT, or UNKNOWN when the database cannot be read."""
    try:
        with closing(_connect()) as _owned_conn, _owned_conn as conn:
            row = conn.execute(
                "SELECT 1 FROM workflow_plan_approval WHERE plan_id = %s", (plan_id,)
            ).fetchone()
    except (DatabaseError, ConfigurationError, SchemaMismatch):
        # A database fault is not "no approval". UNKNOWN keeps the run fail-closed (the gate refuses)
        # while preserving that the cause was a store the gate could not read, not a missing approval.
        return UNKNOWN
    return APPROVED if row is not None else ABSENT


def is_approved(plan_id: str) -> bool:
    """Return whether the plan is approved; absence and database faults remain False."""
    return approval_state(plan_id) == APPROVED


def get_approval(plan_id: str) -> Optional[PlanApproval]:
    """The approval record for ``plan_id``, or ``None``. TOTAL — never raises.

    FOR DIAGNOSIS, NOT FOR DECIDING. "When, and by whom" is the first question a refused operator asks;
    ``approval-gate`` routes on :func:`approval_state`.
    """
    try:
        with closing(_connect()) as _owned_conn, _owned_conn as conn:
            row = conn.execute(
                "SELECT plan_id, approved_at, approved_by FROM workflow_plan_approval WHERE plan_id = %s",
                (plan_id,),
            ).fetchone()
    except (DatabaseError, ConfigurationError, SchemaMismatch):
        return None
    if row is None:
        return None
    return PlanApproval(plan_id=row[0], approved_at=row[1], approved_by=row[2])
