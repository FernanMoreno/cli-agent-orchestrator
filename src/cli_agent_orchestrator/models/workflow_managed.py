"""Immutable records for server-provisioned workflow steps and run credentials."""

import hashlib
import json
from typing import Annotated

from pydantic import Field, StringConstraints

from cli_agent_orchestrator.models.work_contract import (
    EffectiveWorkContract,
    EffectiveWorkContractV2,
)
from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
from cli_agent_orchestrator.models.work_origin import (
    Digest,
    FrozenOriginModel,
    Identity,
    OriginAuthorizationRef,
    OriginSubjectRef,
    Positive,
)


class WorkflowStepProvisionRef(FrozenOriginModel):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    id: Identity
    principal_id: Identity
    workflow_id: Identity
    step_id: Identity
    revision: Positive


class ProvisionedWorkflowStep(FrozenOriginModel):
    """Current server-owned selector after durable evidence was revalidated."""

    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    ref: WorkflowStepProvisionRef
    provision_fingerprint: Digest = "0" * 64
    issuer_id: Identity
    spec_hash: Digest
    workflow_subject_ref: OriginSubjectRef
    workflow_authorization_ref: OriginAuthorizationRef
    job_id: Identity
    grant_id: Identity
    grant_revision: Positive
    contract: EffectiveWorkContract | EffectiveWorkContractV2
    contract_hash: Digest
    snapshot_id: Identity
    snapshot_hash: Digest
    delivery_template: WorkDeliveryEnvelope
    delivery_template_hash: Digest
    adapter_version: Positive
    lease_seconds: Annotated[int, Field(strict=True, gt=0, le=3600)]
    output_schema_json: str | None
    output_schema_hash: Digest | None
    receiver_subject_ref: OriginSubjectRef
    receiver_authorization_ref: OriginAuthorizationRef
    receiver_grant_id: Identity
    receiver_grant_revision: Positive

    def fingerprint(self) -> str:
        payload = {
            "adapter_version": self.adapter_version,
            "contract_hash": self.contract_hash,
            "delivery_template_hash": self.delivery_template_hash,
            "grant_id": self.grant_id,
            "grant_revision": self.grant_revision,
            "job_id": self.job_id,
            "issuer_id": self.issuer_id,
            "lease_seconds": self.lease_seconds,
            "output_schema_hash": self.output_schema_hash,
            "output_schema_json": self.output_schema_json,
            "receiver_authorization_ref": self.receiver_authorization_ref.model_dump(mode="json"),
            "receiver_grant_id": self.receiver_grant_id,
            "receiver_grant_revision": self.receiver_grant_revision,
            "receiver_subject_ref": self.receiver_subject_ref.model_dump(mode="json"),
            "ref": self.ref.model_dump(mode="json"),
            "schema_version": self.schema_version,
            "snapshot_hash": self.snapshot_hash,
            "snapshot_id": self.snapshot_id,
            "spec_hash": self.spec_hash,
            "workflow_authorization_ref": self.workflow_authorization_ref.model_dump(mode="json"),
            "workflow_subject_ref": self.workflow_subject_ref.model_dump(mode="json"),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def has_valid_fingerprint(self) -> bool:
        return self.provision_fingerprint == self.fingerprint()


class WorkflowStepBinding(FrozenOriginModel):
    """Read-only immutable proof connecting one run step to one Work attempt."""

    binding_id: Identity
    binding_fingerprint: Digest
    tier: Annotated[str, StringConstraints(pattern=r"^(yaml|script)$")]
    run_id: Identity
    run_generation: Positive
    workflow_id: Identity
    spec_hash: Digest
    step_id: Identity
    workflow_step_attempt: Positive
    principal_id: Identity
    provision_id: Identity
    provision_revision: Positive
    provision_fingerprint: Digest
    workflow_subject_ref: OriginSubjectRef
    workflow_authorization_ref: OriginAuthorizationRef
    job_id: Identity
    grant_id: Identity
    grant_revision: Positive
    contract_id: Identity
    contract_hash: Digest
    snapshot_id: Identity
    snapshot_hash: Digest
    delivery_id: Identity
    delivery_hash: Digest
    work_item_id: Identity
    work_attempt_id: Identity
    work_generation: Positive
    request_hash: Digest
    idempotency_key: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    output_schema_json: str | None
    output_schema_hash: Digest | None
    receiver_subject_ref: OriginSubjectRef
    receiver_authorization_ref: OriginAuthorizationRef
    receiver_grant_id: Identity
    receiver_grant_revision: Positive
    receiver_received_action: Annotated[str, StringConstraints(pattern=r"^task_received$")]
    receiver_result_action: Annotated[str, StringConstraints(pattern=r"^task_result$")]
    created_at: float

    def fingerprint_payload(self) -> dict:
        value = self.model_dump(mode="json")
        value.pop("binding_fingerprint", None)
        return value

    def computed_fingerprint(self) -> str:
        encoded = json.dumps(
            self.fingerprint_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class WorkflowStepRetryAuthorization(FrozenOriginModel):
    """Durable permission for one exact failed workflow step to advance once."""

    authorization_id: Identity
    authorization_fingerprint: Digest
    binding_id: Identity
    binding_fingerprint: Digest
    tier: Annotated[str, StringConstraints(pattern=r"^(yaml|script)$")]
    run_id: Identity
    run_generation: Positive
    workflow_id: Identity
    spec_hash: Digest
    step_id: Identity
    workflow_step_attempt: Positive
    principal_id: Identity
    provision_id: Identity
    provision_revision: Positive
    provision_fingerprint: Digest
    work_item_id: Identity
    work_attempt_id: Identity
    work_generation: Positive
    authorized_at: float

    def fingerprint_payload(self) -> dict:
        payload = self.model_dump(mode="json")
        payload.pop("authorization_fingerprint", None)
        return payload

    def computed_fingerprint(self) -> str:
        encoded = json.dumps(
            self.fingerprint_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
