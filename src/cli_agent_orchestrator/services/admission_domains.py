"""Verified administrative operations, separate from delivery authorization."""

from typing import Protocol

from cli_agent_orchestrator.clients.admission_repository import (
    ResourceAdmissionRepository,
    SharedCommitUncertain,
)
from cli_agent_orchestrator.models.admission_domains import (
    DomainRecord,
    DomainSpec,
    MetadataCredit,
    SharedAbortReceipt,
    SharedDebtCursor,
    SharedDebtPage,
    SharedReleaseReceipt,
    UsageMismatch,
)
from cli_agent_orchestrator.models.resource_envelope import ResourceBinding
from cli_agent_orchestrator.services.admission_bootstrap import ResourceBootstrap


class ResourceRecoveryRepository(Protocol):
    def admin_release_witnessed(
        self,
        operation_id: str,
        *,
        action_id: str,
        expected_revision: int,
        principal_id: str,
        expected_binding: ResourceBinding | None = None,
    ) -> SharedReleaseReceipt: ...

    def admin_abort_preparing(
        self,
        operation_id: str,
        *,
        action_id: str,
        expected_revision: int,
        principal_id: str,
        expected_binding: ResourceBinding | None = None,
    ) -> SharedAbortReceipt: ...

    def outstanding_resources(
        self,
        *,
        after: SharedDebtCursor | None = None,
        limit: int = 100,
        expected_binding: ResourceBinding | None = None,
    ) -> SharedDebtPage: ...


def abort_preparing_for_admin(
    repository: ResourceRecoveryRepository,
    *,
    principal: object,
    action_id: str,
    operation_id: str,
    expected_revision: int,
    expected_binding: ResourceBinding | None = None,
) -> SharedAbortReceipt:
    actor = ResourceBootstrap._administrator(principal)
    return repository.admin_abort_preparing(
        operation_id,
        action_id=action_id,
        expected_revision=expected_revision,
        principal_id=actor,
        **({"expected_binding": expected_binding} if expected_binding is not None else {}),
    )


def release_witnessed_for_admin(
    repository: ResourceRecoveryRepository,
    *,
    principal: object,
    action_id: str,
    operation_id: str,
    expected_revision: int,
    expected_binding: ResourceBinding | None = None,
) -> SharedReleaseReceipt:
    actor = ResourceBootstrap._administrator(principal)
    return repository.admin_release_witnessed(
        operation_id,
        action_id=action_id,
        expected_revision=expected_revision,
        principal_id=actor,
        **({"expected_binding": expected_binding} if expected_binding is not None else {}),
    )


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

    def outstanding_resources(
        self, *, principal: object, after: SharedDebtCursor | None = None, limit: int = 100
    ) -> SharedDebtPage:
        ResourceBootstrap._administrator(principal)
        return self._repository.outstanding_resources(after=after, limit=limit)

    def abort_preparing(
        self, *, principal: object, action_id: str, operation_id: str, expected_revision: int
    ) -> SharedAbortReceipt:
        return abort_preparing_for_admin(
            self._repository,
            principal=principal,
            action_id=action_id,
            operation_id=operation_id,
            expected_revision=expected_revision,
        )

    def release_witnessed(
        self, *, principal: object, action_id: str, operation_id: str, expected_revision: int
    ) -> SharedReleaseReceipt:
        return release_witnessed_for_admin(
            self._repository,
            principal=principal,
            action_id=action_id,
            operation_id=operation_id,
            expected_revision=expected_revision,
        )
