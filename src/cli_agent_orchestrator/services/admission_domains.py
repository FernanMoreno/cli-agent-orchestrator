"""Verified administrative enrollment, separate from delivery authorization."""

from cli_agent_orchestrator.clients.admission_repository import (
    ResourceAdmissionRepository,
    SharedCommitUncertain,
)
from cli_agent_orchestrator.models.admission_domains import (
    DomainRecord,
    DomainSpec,
    MetadataCredit,
    UsageMismatch,
)
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap


class ResourceDomainAuthority:
    def __init__(self, repository: ResourceAdmissionRepository):
        self._repository = repository

    def provision_domain(
        self,
        *,
        principal: object,
        spec: DomainSpec,
        expected_revision: int,
        active: bool = True,
    ) -> DomainRecord:
        actor = ResourceBootstrap._administrator(principal)
        return self._repository.provision_domain(
            spec,
            expected_revision=expected_revision,
            principal_id=actor,
            active=active,
        )

    def grant_current_member(
        self,
        *,
        principal: object,
        domain_id: str,
        expected_revision: int,
        active: bool = True,
        metadata_credit_id: str | None = None,
    ) -> int:
        actor = ResourceBootstrap._administrator(principal)
        binding = self._repository.current_binding()
        return self._repository.provision_member(
            domain_id,
            binding,
            expected_revision=expected_revision,
            principal_id=actor,
            active=active,
            **(
                {"metadata_credit_id": metadata_credit_id} if metadata_credit_id is not None else {}
            ),
        )

    def grant_current_metadata_credit(
        self, *, principal: object, credit_id: str, domain_id: str
    ) -> MetadataCredit:
        """Grant before feature activation; it never activates delivery itself."""
        actor = ResourceBootstrap._administrator(principal)
        binding = self._repository.current_binding()
        credit = self._repository.grant_metadata_credit(
            credit_id, domain_id, binding, principal_id=actor
        )
        try:
            current = self._repository.current_binding() == binding
        except Exception:
            current = False
        if not current:
            # The grant may be durable. Retain its exact identity and charge;
            # a changed/unavailable view is not a current activation allowance.
            raise SharedCommitUncertain("Metadata credit binding requires reconciliation")
        return credit

    def audit_usage(self, *, principal: object) -> tuple[UsageMismatch, ...]:
        ResourceBootstrap._administrator(principal)
        return self._repository.audit_usage()
