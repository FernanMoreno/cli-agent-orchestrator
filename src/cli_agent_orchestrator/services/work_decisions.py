"""Durable local human-decision receipts for already bound work effects.

This service records and claims a local SQLite receipt.  It neither grants
authority nor executes a provider effect; callers must perform an external
effect only after ``consume_effect`` returns ``True``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services.work_authority import Permissions, WorkAuthority
from cli_agent_orchestrator.services.work_contract import ContractConflict, WorkContracts


class DecisionConflict(ValueError):
    """A decision request is stale, divergent, or conflicts with durable history."""


class DecisionRevoked(DecisionConflict):
    """A durable revocation prevents a new local effect claim."""


@dataclass(frozen=True)
class HumanDecision:
    """Immutable decision evidence; ``consumed_at`` is its first local claim."""

    id: str
    job_id: str
    work_item_id: str
    attempt_id: str
    generation: int
    contract_hash: str
    idempotency_key: str
    actor_id: str
    created_at: float
    action: str
    reason: str
    evidence_refs: tuple[str, ...]
    authorized_effects: tuple[str, ...]
    consumed_at: float | None
    revoked_at: float | None


@dataclass(frozen=True)
class DecisionRevocationRecord:
    """Immutable reduction of a decision's remaining local consumability."""

    decision_id: str
    actor_id: str
    revoked_at: float
    reason: str


def _text(value, label: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be a bounded nonempty string")
    return value


def _generation(value) -> int:
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise DecisionConflict("positive bounded generation required")
    return value


def _tokens(value, label: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or not value or len(value) > 128:
        raise ValueError(f"{label} must be a bounded nonempty sequence")
    normalized = tuple(_text(item, label, maximum=4096) for item in value)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} cannot contain duplicates")
    return normalized


def _canonical_payload(
    *,
    actor_id: str,
    binding,
    idempotency_key: str,
    evidence_refs: tuple[str, ...],
    action: str,
    reason: str,
    authorized_effects: tuple[str, ...],
) -> str:
    return json.dumps(
        {
            "action": action,
            "actor_id": actor_id,
            "attempt_id": binding.attempt_id,
            "authorized_effects": authorized_effects,
            "contract_hash": binding.contract_hash,
            "evidence_refs": evidence_refs,
            "generation": binding.generation,
            "idempotency_key": idempotency_key,
            "job_id": binding.job_id,
            "reason": reason,
            "work_item_id": binding.work_item_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class WorkDecisions:
    """Append-only decisions and at-most-once local claims in a work store."""

    def __init__(self, repository: WorkRepository):
        self.repository = repository
        self._contracts = WorkContracts(repository)
        self._authority = WorkAuthority(repository)

    def _binding(
        self, connection, *, principal, work_item_id: str, attempt_id: str, generation: int
    ):
        binding = self._contracts._revalidate_order(connection, attempt_id, generation=generation)
        if binding.work_item_id != work_item_id:
            raise DecisionConflict("decision work item does not match the bound attempt")
        self._authority._authorize(
            connection,
            principal,
            job_id=binding.job_id,
            grant_id=binding.grant_id,
            expected_grant_revision=binding.grant_revision,
            provider=binding.contract.provider,
            requested_permissions=Permissions(**binding.contract.permissions.model_dump()),
        )
        return binding

    @staticmethod
    def _decision(connection, decision_id: str) -> HumanDecision:
        row = connection.execute(
            "SELECT d.*,MIN(c.consumed_at) AS consumed_at,r.revoked_at "
            "FROM work_human_decisions d "
            "LEFT JOIN work_human_decision_claims c ON c.decision_id=d.id "
            "LEFT JOIN work_human_decision_revocations r ON r.decision_id=d.id "
            "WHERE d.id=? GROUP BY d.id",
            (decision_id,),
        ).fetchone()
        if row is None:
            raise KeyError("human decision not found")
        try:
            evidence_refs = _tokens(json.loads(row["evidence_refs"]), "stored evidence_refs")
            authorized_effects = _tokens(
                json.loads(row["authorized_effects"]), "stored authorized_effects"
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise DecisionConflict("stored human decision is invalid") from error
        return HumanDecision(
            id=row["id"],
            job_id=row["job_id"],
            work_item_id=row["work_item_id"],
            attempt_id=row["attempt_id"],
            generation=row["generation"],
            contract_hash=row["contract_hash"],
            idempotency_key=row["idempotency_key"],
            actor_id=row["actor_id"],
            created_at=row["created_at"],
            action=row["action"],
            reason=row["reason"],
            evidence_refs=evidence_refs,
            authorized_effects=authorized_effects,
            consumed_at=row["consumed_at"],
            revoked_at=row["revoked_at"],
        )

    @staticmethod
    def _revocation(connection, decision_id: str) -> DecisionRevocationRecord | None:
        row = connection.execute(
            "SELECT decision_id,actor_id,revoked_at,reason "
            "FROM work_human_decision_revocations WHERE decision_id=?",
            (decision_id,),
        ).fetchone()
        if row is None:
            return None
        return DecisionRevocationRecord(
            decision_id=row["decision_id"],
            actor_id=row["actor_id"],
            revoked_at=row["revoked_at"],
            reason=row["reason"],
        )

    @staticmethod
    def _matches(
        decision: HumanDecision,
        *,
        actor_id: str,
        binding,
        idempotency_key: str,
        evidence_refs: tuple[str, ...],
        action: str,
        reason: str,
        authorized_effects: tuple[str, ...],
    ) -> bool:
        return _canonical_payload(
            actor_id=decision.actor_id,
            binding=decision,
            idempotency_key=decision.idempotency_key,
            evidence_refs=decision.evidence_refs,
            action=decision.action,
            reason=decision.reason,
            authorized_effects=decision.authorized_effects,
        ) == _canonical_payload(
            actor_id=actor_id,
            binding=binding,
            idempotency_key=idempotency_key,
            evidence_refs=evidence_refs,
            action=action,
            reason=reason,
            authorized_effects=authorized_effects,
        )

    def decide_work(
        self,
        *,
        principal,
        work_item_id,
        attempt_id,
        generation,
        idempotency_key,
        evidence_refs,
        action,
        reason,
        authorized_effects,
    ) -> HumanDecision:
        """Record one immutable, live-authorized decision for an exact binding."""
        WorkAuthority._principal(principal)
        work_item_id = _text(work_item_id, "work_item_id", maximum=512)
        attempt_id = _text(attempt_id, "attempt_id", maximum=512)
        generation = _generation(generation)
        idempotency_key = _text(idempotency_key, "idempotency_key", maximum=512)
        evidence_refs = _tokens(evidence_refs, "evidence_refs")
        action = _text(action, "action", maximum=256)
        reason = _text(reason, "reason")
        authorized_effects = _tokens(authorized_effects, "authorized_effects")
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            binding = self._binding(
                connection,
                principal=principal,
                work_item_id=work_item_id,
                attempt_id=attempt_id,
                generation=generation,
            )
            existing = connection.execute(
                "SELECT id FROM work_human_decisions WHERE attempt_id=? AND generation=? "
                "AND idempotency_key=?",
                (attempt_id, generation, idempotency_key),
            ).fetchone()
            if existing is not None:
                decision = self._decision(connection, existing["id"])
                if not self._matches(
                    decision,
                    actor_id=principal.id,
                    binding=binding,
                    idempotency_key=idempotency_key,
                    evidence_refs=evidence_refs,
                    action=action,
                    reason=reason,
                    authorized_effects=authorized_effects,
                ):
                    raise DecisionConflict(
                        "idempotency key is already bound to different decision evidence"
                    )
                return decision
            decision_id = uuid4().hex
            created_at = __import__("time").time()
            connection.execute(
                "INSERT INTO work_human_decisions "
                "(id,job_id,work_item_id,attempt_id,generation,contract_hash,idempotency_key,actor_id,"
                "created_at,action,reason,evidence_refs,authorized_effects) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    decision_id,
                    binding.job_id,
                    binding.work_item_id,
                    binding.attempt_id,
                    binding.generation,
                    binding.contract_hash,
                    idempotency_key,
                    principal.id,
                    created_at,
                    action,
                    reason,
                    json.dumps(evidence_refs, separators=(",", ":"), allow_nan=False),
                    json.dumps(authorized_effects, separators=(",", ":"), allow_nan=False),
                ),
            )
            self.repository._append_event(
                connection,
                job_id=binding.job_id,
                work_item_id=binding.work_item_id,
                attempt_id=binding.attempt_id,
                actor_id=principal.id,
                event_type="decision.recorded",
                metadata={
                    "action": action,
                    "contract_hash": binding.contract_hash,
                    "decision_id": decision_id,
                },
            )
            return self._decision(connection, decision_id)

    def consume_effect(
        self,
        *,
        principal,
        decision_id,
        attempt_id,
        generation,
        effect,
    ) -> bool:
        """Atomically claim one local effect, never executing an external provider call."""
        WorkAuthority._principal(principal)
        decision_id = _text(decision_id, "decision_id", maximum=512)
        attempt_id = _text(attempt_id, "attempt_id", maximum=512)
        generation = _generation(generation)
        effect = _text(effect, "effect", maximum=4096)
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            decision = self._decision(connection, decision_id)
            if (decision.attempt_id, decision.generation) != (attempt_id, generation):
                raise DecisionConflict("effect claim does not match the decision attempt")
            if effect not in decision.authorized_effects:
                raise DecisionConflict("effect is not authorized by this decision")
            binding = self._binding(
                connection,
                principal=principal,
                work_item_id=decision.work_item_id,
                attempt_id=attempt_id,
                generation=generation,
            )
            if (
                decision.job_id,
                decision.work_item_id,
                decision.attempt_id,
                decision.generation,
                decision.contract_hash,
            ) != (
                binding.job_id,
                binding.work_item_id,
                binding.attempt_id,
                binding.generation,
                binding.contract_hash,
            ):
                raise DecisionConflict("decision belongs to an obsolete contract binding")
            if self._revocation(connection, decision_id) is not None:
                raise DecisionRevoked("human decision was revoked before this effect claim")
            claimed = connection.execute(
                "INSERT INTO work_human_decision_claims (decision_id,effect,actor_id,consumed_at) "
                "VALUES (?,?,?,?) ON CONFLICT(decision_id,effect) DO NOTHING",
                (decision_id, effect, principal.id, __import__("time").time()),
            ).rowcount
            if not claimed:
                return False
            self.repository._append_event(
                connection,
                job_id=binding.job_id,
                work_item_id=binding.work_item_id,
                attempt_id=binding.attempt_id,
                actor_id=principal.id,
                event_type="decision.effect_claimed",
                metadata={"decision_id": decision_id, "effect": effect},
            )
            return True

    def revoke(self, *, principal, decision_id, reason) -> DecisionRevocationRecord:
        """Durably block future claims without deleting the decision or prior claims."""
        WorkAuthority._principal(principal)
        decision_id = _text(decision_id, "decision_id", maximum=512)
        reason = _text(reason, "reason")
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            decision = self._decision(connection, decision_id)
            if decision.actor_id != principal.id:
                raise DecisionConflict("only the original decision actor may revoke it")
            prior = self._revocation(connection, decision_id)
            if prior is not None:
                if (prior.actor_id, prior.reason) != (principal.id, reason):
                    raise DecisionConflict("revocation is already recorded with different evidence")
                return prior
            revoked_at = __import__("time").time()
            connection.execute(
                "INSERT INTO work_human_decision_revocations "
                "(decision_id,actor_id,revoked_at,reason) VALUES (?,?,?,?)",
                (decision_id, principal.id, revoked_at, reason),
            )
            self.repository._append_event(
                connection,
                job_id=decision.job_id,
                work_item_id=decision.work_item_id,
                attempt_id=decision.attempt_id,
                actor_id=principal.id,
                event_type="decision.revoked",
                metadata={"decision_id": decision_id},
            )
            return DecisionRevocationRecord(decision_id, principal.id, revoked_at, reason)

    def get(self, decision_id) -> HumanDecision:
        """Read retained decision history; no current effect authority is inferred."""
        decision_id = _text(decision_id, "decision_id", maximum=512)
        with self.repository.read_snapshot() as connection:
            return self._decision(connection, decision_id)
