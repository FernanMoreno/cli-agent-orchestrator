"""Bind a server-resolved order to one attempt; never manufacture authenticated identities.

Binding freezes evidence, not backend support, resource ownership or permission to
send twice. The coordinator still owns reservations, intent CAS and the protected
backend effect boundary. Revalidation authorizes only this admitted order against
its current durable grant; it does not reconstruct JWT scopes from stored rows.
"""

from pathlib import Path
import json
import time

from pydantic import ValidationError

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.work_contract import (
    EffectiveWorkContract,
    EffectiveWorkContractV2,
    WorkContractBinding,
)
from cli_agent_orchestrator.models.work_origin import (
    ProvisionRef,
    lineage_integrity_fingerprint,
)
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    SnapshotConflict,
)
from cli_agent_orchestrator.services.knowledge_policy import KnowledgePolicy
from cli_agent_orchestrator.services.secret_gate import scan_for_secrets
from cli_agent_orchestrator.services.work_authority import (
    AuthorityDenied,
    Permissions,
    WorkAuthority,
)
from cli_agent_orchestrator.services.work_reservations import WorkReservations


class ContractConflict(ValueError):
    """A frozen order, resource request or attempt identity cannot be replaced."""


def _positive(value):
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise ContractConflict("positive bounded revision or generation required")


def _nonsecret(value):
    if isinstance(value, str):
        if scan_for_secrets(value):
            raise ContractConflict("credential-shaped values cannot enter a work contract")
    elif isinstance(value, dict):
        for key, item in value.items():
            _nonsecret(key)
            _nonsecret(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _nonsecret(item)


class WorkContracts:
    def __init__(self, repository: WorkRepository):
        self.repository = repository

    def _transaction(self, connection):
        if not connection.in_transaction:
            raise ContractConflict("caller-owned transaction required")
        main = next(
            (row[2] for row in connection.execute("PRAGMA database_list") if row[1] == "main"), None
        )
        if not main or Path(main).resolve() != self.repository.path.resolve():
            raise ContractConflict("contract transaction belongs to another store")
        self.repository._verify(connection)

    @staticmethod
    def _contract(value):
        try:
            # Revalidate even model_copy/model_construct outputs, not just dicts.
            payload = value.model_dump() if isinstance(
                value, (EffectiveWorkContract, EffectiveWorkContractV2)
            ) else value
            if not isinstance(payload, dict):
                raise ValueError("contract must be an object")
            version = payload.get("schema_version", 1)
            if type(version) is not int or version not in (1, 2):
                raise ValueError("contract schema version must be an exact supported integer")
            model = EffectiveWorkContract if version == 1 else EffectiveWorkContractV2
            contract = model.model_validate(payload)
        except (ValidationError, TypeError, ValueError):
            raise ContractConflict("invalid effective work contract") from None
        _nonsecret(contract.model_dump())
        if len(contract.canonical_json().encode("utf-8")) > 32768:
            raise ContractConflict("effective work contract exceeds byte bound")
        if contract.snapshot.state != "present":
            raise ContractConflict(
                "new executable bindings require a persisted snapshot, including empty context"
            )
        return contract

    @classmethod
    def _from_json(cls, encoded):
        try:
            payload = json.loads(encoded)
            if not isinstance(payload, dict):
                raise ValueError("stored contract must be an object")
            version = payload.get("schema_version")
            if type(version) is not int or version not in (1, 2):
                raise ValueError("stored contract schema version is invalid")
            model = EffectiveWorkContract if version == 1 else EffectiveWorkContractV2
            contract = cls._contract(model.model_validate_json(encoded))
        except (ContractConflict, ValidationError, TypeError, ValueError):
            raise ContractConflict("stored effective contract is invalid") from None
        if encoded != contract.canonical_json():
            raise ContractConflict("stored effective contract is not canonical")
        return contract

    @staticmethod
    def _projection(contract):
        if isinstance(contract, EffectiveWorkContract):
            return contract.canonical_json()
        payload = contract.model_dump()
        payload.pop("executable_identities")
        payload["schema_version"] = 1
        return EffectiveWorkContract.model_validate(payload).canonical_json()

    @classmethod
    def _stored_contract(cls, connection, row):
        evidence = None
        if connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' "
            "AND name='work_dispatch_v2_evidence'"
        ).fetchone():
            evidence = connection.execute(
                "SELECT contract_json,contract_hash FROM work_dispatch_v2_evidence "
                "WHERE attempt_id=? AND generation=?",
                (row["attempt_id"], row["generation"]),
            ).fetchone()
        contract = cls._from_json(
            evidence["contract_json"] if evidence is not None else row["contract_json"]
        )
        if (
            (evidence is None) != isinstance(contract, EffectiveWorkContract)
            or row["contract_json"] != cls._projection(contract)
            or row["contract_hash"] != contract.canonical_hash()
            or (evidence is not None and evidence["contract_hash"] != contract.canonical_hash())
            or row["contract_id"] != contract.id
            or row["snapshot_id"] != contract.snapshot.id
        ):
            raise ContractConflict("stored contract integrity check failed")
        return contract

    @staticmethod
    def _permissions(contract):
        return Permissions(**contract.permissions.model_dump())

    @staticmethod
    def _attempt(connection, attempt_id, generation):
        _positive(generation)
        row = connection.execute(
            "SELECT a.*,w.job_id,w.contract_id,w.snapshot_id,w.operation_kind,w.state AS work_state,"
            "j.project_id,j.state AS job_state FROM work_attempts a "
            "JOIN work_items w ON w.id=a.work_item_id JOIN work_jobs j ON j.id=w.job_id WHERE a.id=?",
            (attempt_id,),
        ).fetchone()
        if row is None or row["generation"] != generation:
            raise ContractConflict("work attempt identity changed")
        latest = connection.execute(
            "SELECT id FROM work_attempts WHERE work_item_id=? ORDER BY generation DESC LIMIT 1",
            (row["work_item_id"],),
        ).fetchone()[0]
        if (
            latest != attempt_id
            or row["state"] not in {"planned", "sent", "acknowledged", "running"}
            or row["work_state"] in {"succeeded", "failed", "cancelled", "reconcile"}
            or row["job_state"] in {"revoked", "completed", "failed"}
            or row["lease_expires_at"] <= time.time()
        ):
            raise ContractConflict("attempt no longer permits new effects")
        return row

    @staticmethod
    def _continuation_attempt(connection, attempt_id, generation):
        """Return a live bound attempt that is safe to describe, never dispatch.

        A completed result is only readable when its current generation remains
        fenced and its job/grant will be rechecked below.  The retained lease is
        a freshness limit for this descriptive lookup; it never authorizes a
        new effect.  The executable ``_attempt`` remains intentionally stricter.
        """
        _positive(generation)
        row = connection.execute(
            "SELECT a.*,w.job_id,w.contract_id,w.snapshot_id,w.operation_kind,w.state AS work_state,"
            "j.project_id,j.state AS job_state FROM work_attempts a "
            "JOIN work_items w ON w.id=a.work_item_id JOIN work_jobs j ON j.id=w.job_id WHERE a.id=?",
            (attempt_id,),
        ).fetchone()
        if row is None or row["generation"] != generation:
            raise ContractConflict("work attempt identity changed")
        latest = connection.execute(
            "SELECT id FROM work_attempts WHERE work_item_id=? ORDER BY generation DESC LIMIT 1",
            (row["work_item_id"],),
        ).fetchone()[0]
        executable = row["state"] in {"planned", "sent", "acknowledged", "running"} and row[
            "work_state"
        ] not in {"succeeded", "failed", "cancelled", "reconcile"}
        accepted_terminal = row["state"] == "finished" and row["work_state"] == "succeeded"
        if (
            latest != attempt_id
            or not (executable or accepted_terminal)
            or row["job_state"] in {"revoked", "completed", "failed"}
            or row["lease_expires_at"] <= time.time()
        ):
            raise ContractConflict("attempt no longer permits continuity description")
        return row

    @staticmethod
    def _relationships(connection, attempt, contract):
        if (
            attempt["contract_id"],
            attempt["operation_kind"],
            attempt["provider"],
            attempt["snapshot_id"],
        ) != (contract.id, contract.operation_kind, contract.provider, contract.snapshot.id):
            raise ContractConflict("contract does not match its durable work attempt")
        permissions = WorkContracts._permissions(contract)
        if any(str(Path(path).resolve()) != path for path in permissions.paths):
            raise ContractConflict("contract permission paths must remain canonical")
        resources = contract.resources
        try:
            root = Path(resources.checkout_root).resolve(strict=True)
            if str(root) != resources.checkout_root or not root.is_dir():
                raise ContractConflict("contract checkout must remain a canonical directory")
            if not any(root.is_relative_to(Path(path)) for path in permissions.paths):
                raise AuthorityDenied("contract checkout exceeds its path permission")
            if resources.write_paths:
                _, paths = WorkReservations._paths(root, resources.write_paths)
                if tuple(item[0] for item in paths) != resources.write_paths:
                    raise ContractConflict("contract write paths must remain canonical")
                if not all(
                    any(Path(path).is_relative_to(Path(boundary)) for boundary in permissions.paths)
                    for path in resources.write_paths
                ):
                    raise AuthorityDenied("contract writes exceed its path permission")
        except (OSError, RuntimeError) as error:
            raise ContractConflict("contract paths cannot be verified") from error
        for dependency in resources.dependencies:
            row = connection.execute(
                "SELECT job_id FROM work_items WHERE id=?", (dependency,)
            ).fetchone()
            if row is None or row[0] != attempt["job_id"] or dependency == attempt["work_item_id"]:
                raise ContractConflict("contract dependencies must be other work in the same job")

    @staticmethod
    def _snapshot_relationships(connection, snapshot, attempt, contract):
        if (
            snapshot.job_id != attempt["job_id"]
            or snapshot.id != contract.snapshot.id
            or snapshot.delivered_hash != contract.snapshot.delivered_hash
            or snapshot.scope_id
            != (attempt["project_id"] if snapshot.scope == "project" else attempt["job_id"])
        ):
            raise ContractConflict("snapshot does not match its admitted order")
        try:
            DelegationSnapshots._validate_ancestry(connection, snapshot, attempt["work_item_id"])
        except SnapshotConflict as error:
            raise ContractConflict(
                "snapshot inheritance requires its durable parent chain"
            ) from error

    @staticmethod
    def _view(row, contract):
        return WorkContractBinding(
            attempt_id=row["attempt_id"],
            generation=row["generation"],
            job_id=row["job_id"],
            work_item_id=row["work_item_id"],
            principal_id=row["principal_id"],
            grant_id=row["grant_id"],
            grant_revision=row["grant_revision"],
            contract=contract,
            contract_hash=row["contract_hash"],
            created_at=row["created_at"],
        )

    def bind(
        self,
        *,
        principal,
        attempt_id,
        generation,
        expected_attempt_revision,
        grant_id,
        expected_grant_revision,
        contract,
    ):
        with self.repository.transaction() as connection:
            return self._bind(
                connection,
                principal=principal,
                attempt_id=attempt_id,
                generation=generation,
                expected_attempt_revision=expected_attempt_revision,
                grant_id=grant_id,
                expected_grant_revision=expected_grant_revision,
                contract=contract,
            )

    def _bind(
        self,
        connection,
        *,
        principal,
        attempt_id,
        generation,
        expected_attempt_revision,
        grant_id,
        expected_grant_revision,
        contract,
    ):
        """Compose in caller's verified BEGIN IMMEDIATE; never commits or executes."""
        self._transaction(connection)
        WorkAuthority._principal(principal)
        _positive(expected_attempt_revision)
        contract = self._contract(contract)
        attempt = self._attempt(connection, attempt_id, generation)
        if attempt["state"] != "planned" or attempt["revision"] != expected_attempt_revision:
            raise ContractConflict("new binding requires the current planned attempt revision")
        self._relationships(connection, attempt, contract)
        WorkAuthority(self.repository)._authorize(
            connection,
            principal,
            job_id=attempt["job_id"],
            grant_id=grant_id,
            expected_grant_revision=expected_grant_revision,
            provider=contract.provider,
            requested_permissions=self._permissions(contract),
        )
        policy = KnowledgePolicy(
            self.repository, attempt["job_id"], grant_id, expected_grant_revision
        )
        snapshot = DelegationSnapshots(self.repository, policy=policy)._load(
            connection,
            principal,
            connection.execute(
                "SELECT * FROM work_delegation_snapshots WHERE id=?", (contract.snapshot.id,)
            ).fetchone(),
        )
        self._snapshot_relationships(connection, snapshot, attempt, contract)
        encoded, digest = self._projection(contract), contract.canonical_hash()
        existing = self._existing(
            connection,
            attempt_id=attempt_id,
            generation=generation,
            principal_id=principal.id,
            grant_id=grant_id,
            grant_revision=expected_grant_revision,
            contract=contract,
        )
        if existing is not None:
            return existing
        other = connection.execute(
            "SELECT contract_hash FROM work_dispatch_bindings WHERE contract_id=? LIMIT 1",
            (contract.id,),
        ).fetchone()
        if other is not None and other[0] != digest:
            raise ContractConflict("contract identity already describes different evidence")
        connection.execute(
            "INSERT INTO work_dispatch_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                attempt_id,
                generation,
                attempt["job_id"],
                attempt["work_item_id"],
                principal.id,
                grant_id,
                expected_grant_revision,
                contract.id,
                encoded,
                digest,
                snapshot.id,
                time.time(),
            ),
        )
        if isinstance(contract, EffectiveWorkContractV2):
            connection.execute(
                "INSERT INTO work_dispatch_v2_evidence VALUES (?,?,?,?)",
                (attempt_id, generation, contract.canonical_json(), digest),
            )
        connection.execute(
            "UPDATE work_items SET revision=revision+1 WHERE id=?", (attempt["work_item_id"],)
        )
        self.repository._append_event(
            connection,
            job_id=attempt["job_id"],
            work_item_id=attempt["work_item_id"],
            attempt_id=attempt_id,
            actor_id=principal.id,
            event_type="contract.bound",
            metadata={"contract_id": contract.id, "contract_hash": digest},
        )
        row = connection.execute(
            "SELECT * FROM work_dispatch_bindings WHERE attempt_id=? AND generation=?",
            (attempt_id, generation),
        ).fetchone()
        return self._view(row, contract)

    def _existing(
        self,
        connection,
        *,
        attempt_id,
        generation,
        principal_id,
        grant_id,
        grant_revision,
        contract,
    ):
        """Compare immutable replay identity only; this grants no live authority."""
        self._transaction(connection)
        _positive(generation)
        _positive(grant_revision)
        contract = self._contract(contract)
        row = connection.execute(
            "SELECT * FROM work_dispatch_bindings WHERE attempt_id=? AND generation=?",
            (attempt_id, generation),
        ).fetchone()
        if row is None:
            return None
        stored = self._stored_contract(connection, row)
        if (
            row["principal_id"],
            row["grant_id"],
            row["grant_revision"],
            row["contract_json"],
            row["contract_hash"],
            row["contract_id"],
            row["snapshot_id"],
        ) != (
            principal_id,
            grant_id,
            grant_revision,
            self._projection(contract),
            contract.canonical_hash(),
            contract.id,
            contract.snapshot.id,
        ) or stored != contract:
            raise ContractConflict("attempt is already bound to another immutable order")
        return self._view(row, contract)

    def revalidate_order(self, attempt_id, *, generation):
        """Trusted dispatcher only: current authority for this exact durable order."""
        with self.repository.read_snapshot() as connection:
            return self._revalidate_order(connection, attempt_id, generation=generation)

    def _revalidate_order(self, connection, attempt_id, *, generation):
        """No Principal reconstruction and no authority for a different operation."""
        return self._revalidate_binding(
            connection, attempt_id, generation=generation, attempt_loader=self._attempt
        )

    def _revalidate_continuation(self, connection, attempt_id, *, generation):
        """Validate a read-only continuity description, including an accepted terminal result.

        This is deliberately a separate entry point from ``_revalidate_order``:
        dispatch callers retain the terminal rejection enforced by ``_attempt``.
        It does not create a reservation, destination, replay authority, or
        permission for the prior adapter to execute again.
        """
        return self._revalidate_binding(
            connection,
            attempt_id,
            generation=generation,
            attempt_loader=self._continuation_attempt,
        )

    def _revalidate_launch_origin(self, connection, *, attempt, binding, contract):
        """Fail closed for v21 launch work; legacy is an explicit parent marker."""
        item = connection.execute(
            "SELECT origin_protocol,request_hash,idempotency_key FROM work_items WHERE id=?",
            (attempt["work_item_id"],),
        ).fetchone()
        if item is None:
            raise ContractConflict("attempt has no durable parent work")
        origin = connection.execute(
            "SELECT * FROM work_launch_origin_bindings WHERE attempt_id=? AND generation=?",
            (attempt["id"], attempt["generation"]),
        ).fetchone()
        if item["origin_protocol"] == "legacy":
            if origin is not None:
                raise ContractConflict("legacy work contradicts its launch origin binding")
            return
        if item["origin_protocol"] != "launch-v1":
            raise ContractConflict("work origin protocol is unknown")
        if origin is None:
            raise ContractConflict("v21 launch work has no durable origin binding")
        if (
            origin["schema_version"],
            origin["origin_kind"],
            origin["principal_id"],
            origin["requester_principal_id"],
            origin["executor_principal_id"],
            origin["job_id"],
            origin["work_item_id"],
            origin["request_hash"],
            origin["idempotency_key"],
        ) != (
            1,
            "launch",
            binding["principal_id"],
            binding["principal_id"],
            binding["principal_id"],
            binding["job_id"],
            binding["work_item_id"],
            item["request_hash"],
            item["idempotency_key"],
        ):
            raise ContractConflict("launch origin binding does not match its order")
        ref = ProvisionRef(
            id=origin["provision_id"],
            principal_id=origin["principal_id"],
            selector=origin["selector"],
            revision=origin["provision_revision"],
        )
        try:
            from cli_agent_orchestrator.services.work_provisioning import (
                ProvisionUnavailable,
                WorkProvisioning,
            )

            provision = WorkProvisioning(self.repository)._revalidate_bound_launch(
                connection, ref=ref, fingerprint=origin["provision_fingerprint"]
            )
        except (ProvisionUnavailable, ValueError) as error:
            raise ContractConflict("launch origin provision is no longer valid") from error
        if (
            provision.job_id,
            provision.grant_id,
            provision.grant_revision,
            provision.contract_hash,
            provision.snapshot_id,
            provision.snapshot_hash,
        ) != (
            binding["job_id"],
            binding["grant_id"],
            binding["grant_revision"],
            binding["contract_hash"],
            binding["snapshot_id"],
            contract.snapshot.delivered_hash,
        ):
            raise ContractConflict("launch origin provision differs from its order")

    def _revalidate_lineage_origin(self, connection, *, attempt, binding, contract):
        """Validate v22 managed lineage against this exact bound child attempt.

        A child lineage row is evidence for one already-admitted order; it never
        supplies authority and it must not be repaired by looking for a newer
        attempt.  Consequently every lookup below is keyed by the durable
        attempt/generation tuple carried by the binding.
        """
        item = connection.execute(
            "SELECT lineage_protocol,request_hash,idempotency_key FROM work_items WHERE id=?",
            (attempt["work_item_id"],),
        ).fetchone()
        if item is None:
            raise ContractConflict("attempt has no durable lineage work")
        origin = connection.execute(
            "SELECT * FROM work_child_origin_bindings WHERE child_attempt_id=? "
            "AND child_generation=?",
            (attempt["id"], attempt["generation"]),
        ).fetchone()
        if item["lineage_protocol"] == "legacy":
            if origin is not None:
                raise ContractConflict("legacy work contradicts its lineage binding")
            return
        if item["lineage_protocol"] != "managed-v1":
            raise ContractConflict("work lineage protocol is unknown")
        if origin is None:
            raise ContractConflict("managed work has no durable lineage binding")
        integrity = connection.execute(
            "SELECT * FROM work_lineage_integrity WHERE child_attempt_id=? "
            "AND child_generation=?",
            (origin["child_attempt_id"], origin["child_generation"]),
        ).fetchone()
        try:
            fingerprint = lineage_integrity_fingerprint(origin)
        except (TypeError, ValueError):
            raise ContractConflict(
                "managed lineage integrity is unavailable or contradictory"
            ) from None
        if integrity is None or (
            integrity["schema_version"],
            integrity["canonicalization_version"],
            integrity["fingerprint"],
        ) != (1, 1, fingerprint):
            raise ContractConflict("managed lineage integrity is unavailable or contradictory")
        if (
            origin["schema_version"],
            origin["kind"],
            origin["job_id"],
            origin["child_attempt_id"],
            origin["child_generation"],
            origin["child_work_item_id"],
            origin["child_subject_id"],
            origin["child_authorization_kind"],
            origin["child_grant_id"],
            origin["child_grant_revision"],
            origin["child_contract_hash"],
            origin["snapshot_id"],
            origin["snapshot_hash"],
            origin["request_hash"],
            origin["idempotency_key"],
        ) != (
            1,
            origin["kind"],
            binding["job_id"],
            attempt["id"],
            attempt["generation"],
            binding["work_item_id"],
            binding["principal_id"],
            "child",
            binding["grant_id"],
            binding["grant_revision"],
            binding["contract_hash"],
            binding["snapshot_id"],
            contract.snapshot.delivered_hash,
            item["request_hash"],
            item["idempotency_key"],
        ) or origin[
            "kind"
        ] not in {
            "child",
            "handoff",
        }:
            raise ContractConflict("managed lineage binding does not match its child order")
        delivery = connection.execute(
            "SELECT * FROM work_delivery_orders WHERE attempt_id=? AND generation=?",
            (attempt["id"], attempt["generation"]),
        ).fetchone()
        if delivery is None or (
            origin["delivery_id"],
            origin["delivery_hash"],
            delivery["contract_hash"],
        ) != (
            f"{attempt['id']}:{attempt['generation']}",
            delivery["delivery_hash"],
            binding["contract_hash"],
        ):
            raise ContractConflict("managed lineage delivery does not match its child order")
        try:
            from cli_agent_orchestrator.services.work_delivery import WorkDeliveries

            if (
                WorkDeliveries(self.repository)._load(connection, self._view(binding, contract))
                is None
            ):
                raise ContractConflict("managed lineage delivery is unavailable")
        except ContractConflict:
            raise
        except (TypeError, ValueError):
            raise ContractConflict("managed lineage delivery is invalid") from None
        parent_attempt = connection.execute(
            "SELECT a.id,a.generation,a.work_item_id,w.job_id FROM work_attempts a "
            "JOIN work_items w ON w.id=a.work_item_id WHERE a.id=? AND a.generation=?",
            (origin["parent_attempt_id"], origin["parent_generation"]),
        ).fetchone()
        parent_binding = connection.execute(
            "SELECT * FROM work_dispatch_bindings WHERE attempt_id=? AND generation=?",
            (origin["parent_attempt_id"], origin["parent_generation"]),
        ).fetchone()
        if (
            parent_attempt is None
            or parent_binding is None
            or (
                parent_attempt["work_item_id"],
                parent_attempt["job_id"],
                parent_binding["work_item_id"],
                parent_binding["job_id"],
                parent_binding["contract_hash"],
                origin["requester_principal_id"],
                origin["executor_principal_id"],
            )
            != (
                origin["parent_work_item_id"],
                binding["job_id"],
                origin["parent_work_item_id"],
                binding["job_id"],
                origin["parent_contract_hash"],
                parent_binding["principal_id"] if parent_binding is not None else None,
                parent_binding["principal_id"] if parent_binding is not None else None,
            )
        ):
            raise ContractConflict("managed lineage parent does not match its exact order")
        if origin["parent_work_item_id"] == binding["work_item_id"]:
            raise ContractConflict("managed lineage parent cannot be its child")
        cycle = connection.execute(
            "WITH RECURSIVE ancestors(work_item_id) AS ("
            " SELECT parent_work_item_id FROM work_items WHERE id=? "
            " AND parent_work_item_id IS NOT NULL"
            " UNION"
            " SELECT w.parent_work_item_id FROM work_items w "
            " JOIN ancestors a ON a.work_item_id=w.id "
            " WHERE w.parent_work_item_id IS NOT NULL"
            ") SELECT 1 FROM ancestors WHERE work_item_id=? LIMIT 1",
            (origin["parent_work_item_id"], binding["work_item_id"]),
        ).fetchone()
        if cycle is not None:
            raise ContractConflict("managed lineage parent chain contains a cycle")
        authority = WorkAuthority(self.repository)
        try:
            authority._revalidate_origin_authorization(
                connection,
                subject_id=origin["child_subject_id"],
                subject_revision=origin["child_subject_revision"],
                authorization_kind=origin["child_authorization_kind"],
                authorization_revision=origin["child_authorization_revision"],
                grant_id=origin["child_grant_id"],
                grant_revision=origin["child_grant_revision"],
                job_id=origin["job_id"],
                action="admit_child" if origin["kind"] == "child" else "admit_handoff",
            )
            authority._revalidate_origin_authorization(
                connection,
                subject_id=origin["receiver_subject_id"],
                subject_revision=origin["receiver_subject_revision"],
                authorization_kind=origin["receiver_authorization_kind"],
                authorization_revision=origin["receiver_authorization_revision"],
                grant_id=origin["receiver_grant_id"],
                grant_revision=origin["receiver_grant_revision"],
                job_id=origin["job_id"],
                action="task_received",
            )
        except AuthorityDenied as error:
            raise ContractConflict("managed lineage authority is no longer valid") from error

    def _revalidate_binding(self, connection, attempt_id, *, generation, attempt_loader):
        """Shared integrity/authority checks after a caller-specific attempt gate."""
        self._transaction(connection)
        attempt = attempt_loader(connection, attempt_id, generation)
        row = connection.execute(
            "SELECT * FROM work_dispatch_bindings WHERE attempt_id=? AND generation=?",
            (attempt_id, generation),
        ).fetchone()
        if row is None:
            raise ContractConflict("attempt has no durable work contract binding")
        contract = self._stored_contract(connection, row)
        self._relationships(connection, attempt, contract)
        chain, job = WorkAuthority(self.repository)._chain(
            connection, row["grant_id"], row["grant_revision"]
        )
        # _chain already proves leaf permissions/providers are subsets of every
        # live ancestor. The additional job allowlist and frozen order must hold.
        if (
            job["id"] != row["job_id"]
            or row["job_id"] != attempt["job_id"]
            or row["work_item_id"] != attempt["work_item_id"]
            or chain[0].principal_id != row["principal_id"]
            or contract.provider not in chain[0].providers
            or contract.provider not in job["allowed_providers"]
            or not self._permissions(contract).is_subset_of(chain[0].permissions)
            or any("knowledge.read" not in grant.permissions.tools for grant in chain)
        ):
            raise AuthorityDenied("admitted order exceeds its current grant")
        snapshot = DelegationSnapshots._load_authorized(
            connection,
            connection.execute(
                "SELECT * FROM work_delegation_snapshots WHERE id=?", (row["snapshot_id"],)
            ).fetchone(),
        )
        self._snapshot_relationships(connection, snapshot, attempt, contract)
        self._revalidate_launch_origin(connection, attempt=attempt, binding=row, contract=contract)
        self._revalidate_lineage_origin(connection, attempt=attempt, binding=row, contract=contract)
        return self._view(row, contract)
