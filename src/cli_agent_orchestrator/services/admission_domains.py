"""Verified administrative enrollment, separate from delivery authorization."""

from cli_agent_orchestrator.clients.admission_repository import ResourceAdmissionRepository
from cli_agent_orchestrator.models.admission_domains import DomainRecord, DomainSpec, UsageMismatch
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
    ) -> int:
        actor = ResourceBootstrap._administrator(principal)
        binding = self._repository.current_binding()
        return self._repository.provision_member(
            domain_id,
            binding,
            expected_revision=expected_revision,
            principal_id=actor,
            active=active,
        )

    def audit_usage(self, *, principal: object) -> tuple[UsageMismatch, ...]:
        ResourceBootstrap._administrator(principal)
        return self._repository.audit_usage()
