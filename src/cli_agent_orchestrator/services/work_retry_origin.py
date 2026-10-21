"""Immutable provenance for explicitly admitted Work replacement generations."""

import json
import time

from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator.models.work_origin import ProvisionRef
from cli_agent_orchestrator.services.work_provisioning import (
    ProvisionUnavailable,
    WorkProvisioning,
)


class WorkRetryOrigins:
    """Bind and revalidate one generation's replacement chain without granting authority."""

    def __init__(self, repository: WorkRepository):
        self.repository = repository

    @staticmethod
    def _event(connection, *, job_id, work_item_id, attempt_id, event_type, recorded=None):
        """Return the live event, or recorded facts only for a legitimately pruned one."""
        rows = connection.execute(
            "SELECT * FROM work_events WHERE job_id=%s AND work_item_id=%s AND attempt_id=%s "
            "AND event_type=%s ORDER BY sequence",
            (job_id, work_item_id, attempt_id, event_type),
        ).fetchall()
        if len(rows) > 1 or (not rows and recorded is None):
            raise WorkConflict("replacement has no unique durable release/replan event")
        if rows:
            if recorded is not None and (
                rows[0]["event_id"] != recorded["event_id"]
                or rows[0]["sequence"] != recorded["sequence"]
            ):
                raise WorkConflict("replacement release/replan event differs from its origin")
            return rows[0]
        head = connection.execute(
            "SELECT retained_after FROM work_event_sequences WHERE job_id=%s", (job_id,)
        ).fetchone()
        if head is None or recorded["sequence"] > head["retained_after"]:
            raise WorkConflict("replacement release/replan event is missing but not pruned")
        return recorded

    @staticmethod
    def _recorded_events(row):
        """Immutable copies of the verified release/replan facts taken at admission."""
        if row is None:
            return None, None
        release_metadata = json.dumps(
            {
                "scheduler_reservation_id": row["prior_scheduler_request_id"],
                "reservation_revision": row["prior_scheduler_revision"],
                "stop_evidence_ref": row["stop_evidence_ref"],
            }
        )
        return (
            {
                "event_id": row["release_event_id"],
                "sequence": row["release_event_sequence"],
                "actor_id": row["retry_actor_id"],
                "metadata": release_metadata,
            },
            {
                "event_id": row["retry_event_id"],
                "sequence": row["retry_event_sequence"],
                "actor_id": row["retry_actor_id"],
            },
        )

    def _history(self, connection, *, work, generation, principal_id, recorded=None):
        """Verify the exact chain; ``recorded`` is this generation's retry row, if bound."""
        if (
            work["origin_protocol"] != "launch-v1"
            or work["lineage_protocol"] != "legacy"
            or work["operation_kind"] != "launch"
            or type(generation) is not int
            or generation <= 1
            or len(work["attempts"]) != generation
        ):
            raise WorkConflict("replacement requires the latest managed launch generation")
        current = work["attempts"][-1]
        prior = work["attempts"][-2]
        root = work["attempts"][0]
        if (
            current["generation"] != generation
            or prior["generation"] != generation - 1
            or root["generation"] != 1
            or prior["state"] != "reconcile"
            or prior["cleanup_state"] == "pending"
        ):
            raise WorkConflict("replacement predecessor is not the exact reconciled generation")
        if (
            connection.execute(
                "SELECT 1 FROM work_attempts WHERE work_item_id=%s AND generation>%s LIMIT 1",
                (work["id"], generation),
            ).fetchone()
            is not None
        ):
            raise WorkConflict("replacement generation is no longer current")

        root_origin = connection.execute(
            "SELECT * FROM work_launch_origin_bindings " "WHERE attempt_id=%s AND generation=1",
            (root["id"],),
        ).fetchone()
        if (
            root_origin is None
            or root_origin["schema_version"] != 1
            or root_origin["job_id"] != work["job_id"]
            or root_origin["work_item_id"] != work["id"]
            or root_origin["origin_kind"] != "launch"
            or root_origin["principal_id"] != root_origin["requester_principal_id"]
            or root_origin["principal_id"] != root_origin["executor_principal_id"]
            or root_origin["request_hash"] != work["request_hash"]
            or root_origin["idempotency_key"] != work["idempotency_key"]
        ):
            raise WorkConflict("replacement has no immutable launch root")
        if (
            connection.execute(
                "SELECT 1 FROM work_launch_origin_bindings WHERE attempt_id=%s AND generation=%s",
                (current["id"], generation),
            ).fetchone()
            is not None
        ):
            raise WorkConflict("replacement cannot copy an initial launch origin")

        scheduler = connection.execute(
            "SELECT * FROM work_scheduler_requests WHERE attempt_id=%s AND generation=%s",
            (prior["id"], prior["generation"]),
        ).fetchone()
        if (
            scheduler is None
            or scheduler["work_item_id"] != work["id"]
            or scheduler["job_id"] != work["job_id"]
            or scheduler["state"] != "released"
            or not isinstance(scheduler["stop_evidence_ref"], str)
            or not scheduler["stop_evidence_ref"]
        ):
            raise WorkConflict("replacement predecessor has no exact released stop evidence")

        recorded_release, recorded_retry = self._recorded_events(recorded)
        released = self._event(
            connection,
            job_id=work["job_id"],
            work_item_id=work["id"],
            attempt_id=prior["id"],
            event_type="scheduler.released",
            recorded=recorded_release,
        )
        replanned = self._event(
            connection,
            job_id=work["job_id"],
            work_item_id=work["id"],
            attempt_id=current["id"],
            event_type="attempt.replanned",
            recorded=recorded_retry,
        )
        try:
            release_metadata = json.loads(released["metadata"])
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise WorkConflict("replacement release evidence is malformed") from error
        if (
            released["actor_id"] != principal_id
            or replanned["actor_id"] != principal_id
            or released["sequence"] >= replanned["sequence"]
            or release_metadata.get("scheduler_reservation_id") != scheduler["id"]
            or release_metadata.get("reservation_revision") != scheduler["revision"]
            or release_metadata.get("stop_evidence_ref") != scheduler["stop_evidence_ref"]
        ):
            raise WorkConflict(
                "replacement release/replan evidence does not match its actor and CAS"
            )

        return {
            "current": current,
            "prior": prior,
            "root": root,
            "root_origin": root_origin,
            "scheduler": scheduler,
            "release_event": released,
            "retry_event": replanned,
        }

    @staticmethod
    def _prior_revision_is_current(history, row):
        """The admission-time predecessor revision is a historical CAS fact.

        ``_history`` already requires the predecessor to remain ``reconcile`` with
        cleanup not pending; the only later writer of such an attempt is cleanup
        observation, which advances its revision without touching the released
        scheduler row, stop proof or current generation compared exactly here.
        """
        return history["prior"]["revision"] >= row["prior_attempt_revision"]

    def validate_new(
        self,
        connection,
        *,
        work,
        generation,
        principal_id,
        expected_work_revision,
        expected_attempt_id,
        expected_attempt_revision,
    ):
        history = self._history(
            connection, work=work, generation=generation, principal_id=principal_id
        )
        current = history["current"]
        if (
            work["state"] != "queued"
            or work["revision"] != expected_work_revision
            or current["id"] != expected_attempt_id
            or current["revision"] != expected_attempt_revision
            or current["state"] != "planned"
        ):
            raise WorkConflict("replacement work or attempt CAS changed before admission")
        if (
            connection.execute(
                "SELECT 1 FROM work_dispatch_bindings WHERE attempt_id=%s AND generation=%s",
                (current["id"], generation),
            ).fetchone()
            is not None
        ):
            raise WorkConflict("replacement already has a contract binding")
        if (
            connection.execute(
                "SELECT 1 FROM work_scheduler_requests WHERE attempt_id=%s AND generation=%s",
                (current["id"], generation),
            ).fetchone()
            is not None
        ):
            raise WorkConflict("replacement already has a scheduler request")
        if (
            connection.execute(
                "SELECT 1 FROM work_delivery_orders WHERE attempt_id=%s AND generation=%s",
                (current["id"], generation),
            ).fetchone()
            is not None
        ):
            raise WorkConflict("replacement already has a delivery order")
        return history

    def bind(self, connection, *, history, binding, handoff, principal):
        """Persist the exact root, predecessor, current authority and bound order."""
        current, prior, root, root_origin, scheduler = (
            history["current"],
            history["prior"],
            history["root"],
            history["root_origin"],
            history["scheduler"],
        )
        delivery = connection.execute(
            "SELECT request_hash,delivery_hash,contract_hash FROM work_delivery_orders "
            "WHERE attempt_id=%s AND generation=%s",
            (binding.attempt_id, binding.generation),
        ).fetchone()
        if delivery is None or delivery["contract_hash"] != binding.contract_hash:
            raise WorkConflict("replacement delivery binding is incomplete")
        scheduler_request = connection.execute(
            "SELECT * FROM work_scheduler_requests WHERE attempt_id=%s AND generation=%s",
            (binding.attempt_id, binding.generation),
        ).fetchone()
        if (
            scheduler_request is None
            or scheduler_request["state"] != "queued"
            or scheduler_request["job_id"] != binding.job_id
            or scheduler_request["work_item_id"] != binding.work_item_id
            or scheduler_request["units"] != binding.contract.resources.units
            or scheduler_request["dependencies"]
            != json.dumps(binding.contract.resources.dependencies, separators=(",", ":"))
        ):
            raise WorkConflict("replacement scheduler order is incomplete")
        launch = handoff.launch
        if (
            binding.attempt_id != current["id"]
            or binding.generation != current["generation"]
            or binding.work_item_id != handoff.work_item_id
            or binding.job_id != launch.job_id
            or binding.principal_id != principal.id
            or binding.grant_id != launch.grant_id
            or binding.grant_revision != launch.grant_revision
            or binding.contract_hash != launch.contract.canonical_hash()
            or binding.contract.snapshot.id != launch.contract.snapshot.id
        ):
            raise WorkConflict("replacement bindings do not match the sealed current context")
        connection.execute(
            "INSERT INTO work_launch_retry_bindings "
            "(attempt_id,generation,work_item_id,job_id,root_attempt_id,root_generation,"
            "prior_attempt_id,prior_generation,prior_attempt_revision,prior_scheduler_request_id,"
            "prior_scheduler_revision,stop_evidence_ref,retry_actor_id,release_event_id,"
            "release_event_sequence,retry_event_id,retry_event_sequence,"
            "replanned_work_revision,planned_attempt_revision,principal_id,selector,provision_id,"
            "provision_revision,provision_fingerprint,grant_id,grant_revision,contract_id,contract_hash,snapshot_id,"
            "snapshot_hash,delivery_request_hash,delivery_hash,retry_admission_key,retry_request_hash,"
            "current_scheduler_request_id,created_at,schema_version) "
            "VALUES (" + ",".join(["%s"] * 36) + ",1)",
            (
                current["id"],
                current["generation"],
                handoff.work_item_id,
                launch.job_id,
                root["id"],
                root["generation"],
                prior["id"],
                prior["generation"],
                prior["revision"],
                scheduler["id"],
                scheduler["revision"],
                scheduler["stop_evidence_ref"],
                principal.id,
                history["release_event"]["event_id"],
                history["release_event"]["sequence"],
                history["retry_event"]["event_id"],
                history["retry_event"]["sequence"],
                handoff.expected_work_revision,
                handoff.planned_attempt_revision,
                principal.id,
                launch.selection,
                launch.provision.ref.id,
                launch.provision.ref.revision,
                launch.provision_fingerprint,
                binding.grant_id,
                binding.grant_revision,
                binding.contract.id,
                binding.contract_hash,
                binding.contract.snapshot.id,
                binding.contract.snapshot.delivered_hash,
                delivery["request_hash"],
                delivery["delivery_hash"],
                handoff.retry_idempotency_key,
                handoff.retry_request_hash,
                scheduler_request["id"],
                time.time(),
            ),
        )

    def replay(
        self,
        connection,
        *,
        history,
        binding,
        handoff,
        principal,
        deliveries,
    ):
        """Compare one existing order exactly; this path never requeues or sends."""
        current = history["current"]
        row = connection.execute(
            "SELECT * FROM work_launch_retry_bindings WHERE attempt_id=%s AND generation=%s",
            (current["id"], current["generation"]),
        ).fetchone()
        if row is None:
            raise WorkConflict("replacement replay has no durable retry origin")
        launch = handoff.launch
        expected = (
            handoff.work_item_id,
            launch.job_id,
            history["root"]["id"],
            history["root"]["generation"],
            history["prior"]["id"],
            history["prior"]["generation"],
            history["scheduler"]["id"],
            history["scheduler"]["revision"],
            history["scheduler"]["stop_evidence_ref"],
            history["release_event"]["event_id"],
            history["release_event"]["sequence"],
            history["retry_event"]["event_id"],
            history["retry_event"]["sequence"],
            principal.id,
            handoff.expected_work_revision,
            handoff.planned_attempt_revision,
            principal.id,
            launch.selection,
            launch.provision.ref.id,
            launch.provision.ref.revision,
            launch.provision_fingerprint,
            binding.grant_id,
            binding.grant_revision,
            binding.contract.id,
            binding.contract_hash,
            binding.contract.snapshot.id,
            binding.contract.snapshot.delivered_hash,
            handoff.retry_idempotency_key,
            handoff.retry_request_hash,
        )
        actual = (
            row["work_item_id"],
            row["job_id"],
            row["root_attempt_id"],
            row["root_generation"],
            row["prior_attempt_id"],
            row["prior_generation"],
            row["prior_scheduler_request_id"],
            row["prior_scheduler_revision"],
            row["stop_evidence_ref"],
            row["release_event_id"],
            row["release_event_sequence"],
            row["retry_event_id"],
            row["retry_event_sequence"],
            row["retry_actor_id"],
            row["replanned_work_revision"],
            row["planned_attempt_revision"],
            row["principal_id"],
            row["selector"],
            row["provision_id"],
            row["provision_revision"],
            row["provision_fingerprint"],
            row["grant_id"],
            row["grant_revision"],
            row["contract_id"],
            row["contract_hash"],
            row["snapshot_id"],
            row["snapshot_hash"],
            row["retry_admission_key"],
            row["retry_request_hash"],
        )
        if (
            row["schema_version"] != 1
            or actual != expected
            or not self._prior_revision_is_current(history, row)
        ):
            raise WorkConflict("replacement retry identity contradicts its durable order")
        delivery = connection.execute(
            "SELECT request_hash,delivery_hash,contract_hash FROM work_delivery_orders "
            "WHERE attempt_id=%s AND generation=%s",
            (current["id"], current["generation"]),
        ).fetchone()
        if (
            delivery is None
            or delivery["request_hash"] != row["delivery_request_hash"]
            or delivery["delivery_hash"] != row["delivery_hash"]
            or delivery["contract_hash"] != binding.contract_hash
        ):
            raise WorkConflict("replacement retry delivery differs from its durable order")
        deliveries._compare(connection, binding, launch.envelope)
        scheduler = connection.execute(
            "SELECT * FROM work_scheduler_requests WHERE id=%s AND attempt_id=%s AND generation=%s",
            (row["current_scheduler_request_id"], current["id"], current["generation"]),
        ).fetchone()
        if (
            scheduler is None
            or scheduler["job_id"] != binding.job_id
            or scheduler["work_item_id"] != binding.work_item_id
            or scheduler["attempt_id"] != current["id"]
            or scheduler["generation"] != current["generation"]
            or scheduler["units"] != binding.contract.resources.units
            or scheduler["dependencies"]
            != json.dumps(binding.contract.resources.dependencies, separators=(",", ":"))
        ):
            raise WorkConflict("replacement scheduler order is incomplete")
        return row

    def revalidate(self, connection, *, attempt, binding, contract):
        """Prove this later generation retains its root, exact stop and current context."""
        work = self.repository._work(connection, attempt["work_item_id"])
        row = connection.execute(
            "SELECT * FROM work_launch_retry_bindings WHERE attempt_id=%s AND generation=%s",
            (attempt["id"], attempt["generation"]),
        ).fetchone()
        if row is None:
            raise WorkConflict("replacement generation has no durable retry origin")
        history = self._history(
            connection,
            work=work,
            generation=attempt["generation"],
            principal_id=binding["principal_id"],
            recorded=row,
        )
        if (
            connection.execute(
                "SELECT 1 FROM work_launch_origin_bindings WHERE attempt_id=%s AND generation=%s",
                (attempt["id"], attempt["generation"]),
            ).fetchone()
            is not None
        ):
            raise WorkConflict("replacement generation contradicts its initial launch origin")
        if (
            row["work_item_id"],
            row["job_id"],
            row["root_attempt_id"],
            row["root_generation"],
            row["prior_attempt_id"],
            row["prior_generation"],
            row["prior_scheduler_request_id"],
            row["prior_scheduler_revision"],
            row["stop_evidence_ref"],
            row["release_event_id"],
            row["release_event_sequence"],
            row["retry_event_id"],
            row["retry_event_sequence"],
            row["retry_actor_id"],
            row["principal_id"],
            row["grant_id"],
            row["grant_revision"],
            row["contract_id"],
            row["contract_hash"],
            row["snapshot_id"],
            row["snapshot_hash"],
        ) != (
            work["id"],
            work["job_id"],
            history["root"]["id"],
            history["root"]["generation"],
            history["prior"]["id"],
            history["prior"]["generation"],
            history["scheduler"]["id"],
            history["scheduler"]["revision"],
            history["scheduler"]["stop_evidence_ref"],
            history["release_event"]["event_id"],
            history["release_event"]["sequence"],
            history["retry_event"]["event_id"],
            history["retry_event"]["sequence"],
            binding["principal_id"],
            binding["principal_id"],
            binding["grant_id"],
            binding["grant_revision"],
            binding["contract_id"],
            binding["contract_hash"],
            binding["snapshot_id"],
            contract.snapshot.delivered_hash,
        ) or not self._prior_revision_is_current(
            history, row
        ):
            raise WorkConflict("replacement lineage or current dispatch binding changed")
        ref = ProvisionRef(
            id=row["provision_id"],
            principal_id=row["principal_id"],
            selector=row["selector"],
            revision=row["provision_revision"],
        )
        try:
            provision = WorkProvisioning(self.repository)._revalidate_bound_launch(
                connection, ref=ref, fingerprint=row["provision_fingerprint"]
            )
        except (ProvisionUnavailable, ValueError) as error:
            raise WorkConflict("replacement current provision is no longer valid") from error
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
            raise WorkConflict("replacement provision differs from its current order")
        delivery = connection.execute(
            "SELECT request_hash,delivery_hash,contract_hash FROM work_delivery_orders "
            "WHERE attempt_id=%s AND generation=%s",
            (attempt["id"], attempt["generation"]),
        ).fetchone()
        if (
            delivery is None
            or delivery["request_hash"] != row["delivery_request_hash"]
            or delivery["delivery_hash"] != row["delivery_hash"]
            or delivery["contract_hash"] != binding["contract_hash"]
        ):
            raise WorkConflict("replacement delivery provenance is no longer valid")
        scheduler = connection.execute(
            "SELECT * FROM work_scheduler_requests WHERE id=%s AND attempt_id=%s AND generation=%s",
            (row["current_scheduler_request_id"], attempt["id"], attempt["generation"]),
        ).fetchone()
        if (
            scheduler is None
            or scheduler["job_id"] != work["job_id"]
            or scheduler["work_item_id"] != work["id"]
            or scheduler["units"] != contract.resources.units
            or scheduler["dependencies"]
            != json.dumps(contract.resources.dependencies, separators=(",", ":"))
        ):
            raise WorkConflict("replacement scheduler provenance is no longer valid")
