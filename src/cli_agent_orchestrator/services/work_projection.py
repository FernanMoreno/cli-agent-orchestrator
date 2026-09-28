"""One read projection of durable work; terminal liveness never determines success."""

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work import EventPage, WorkView
from cli_agent_orchestrator.security.auth import Principal, SCOPE_ADMIN, SCOPE_READ, SCOPE_WRITE


def project_work(job: dict, work: dict) -> WorkView:
    attempt = work["attempts"][-1] if work["attempts"] else None
    state = attempt["state"] if attempt else None
    return WorkView(
        job_id=job["id"],
        work_item_id=work["id"],
        attempt_id=attempt["id"] if attempt else None,
        job_state=job["state"],
        work_state=work["state"],
        attempt_state=state,
        turn_state={
            "sent": "input_sent",
            "acknowledged": "acknowledged",
            "running": "processing",
        }.get(state),
        process_state="unknown",
        revision=work["revision"],
        result_ref=work["accepted_result_id"],
        cleanup_state=attempt["cleanup_state"] if attempt else "not_requested",
        required_action="reconcile_attempt" if work["state"] == "reconcile" else None,
    )


class WorkQueries:
    """Owner-only v1 reads. Delegated execution does not implicitly grant job-wide reads.

    Ownership and payload are evaluated in the same verified read transaction.
    Revoked jobs remain readable by their owner for audit and reconciliation.
    """

    def __init__(self, repository: WorkRepository):
        self.repository = repository

    @staticmethod
    def _scope(principal: Principal) -> None:
        if not isinstance(principal, Principal) or not getattr(principal, "scopes", ()):
            raise PermissionError("read scope required")
        if not principal.scopes & {SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN}:
            raise PermissionError("read scope required")

    @staticmethod
    def _owner(principal: Principal, job: dict) -> None:
        if job["principal_id"] != principal.id:
            raise KeyError("work resource not found")

    def work(self, principal: Principal, work_item_id: str) -> WorkView:
        self._scope(principal)
        with self.repository.read_snapshot() as connection:
            work = self.repository._work(connection, work_item_id)
            job = self.repository._job(connection, work["job_id"])
            self._owner(principal, job)
            return project_work(job, work)

    def events(
        self, principal: Principal, job_id: str, *, after_sequence=0, limit=100
    ) -> EventPage:
        self._scope(principal)
        with self.repository.read_snapshot() as connection:
            job = self.repository._job(connection, job_id)
            self._owner(principal, job)
            return EventPage.model_validate(
                self.repository._read_events(
                    connection,
                    job_id,
                    after_sequence=after_sequence,
                    limit=limit,
                )
            )
