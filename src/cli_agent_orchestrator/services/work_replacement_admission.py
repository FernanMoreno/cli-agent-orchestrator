"""Explicit trusted admission for a Work launch replacement generation."""

from cli_agent_orchestrator.clients.work_repository import WorkConflict


class WorkReplacementAdmission:
    """Compose existing authority, launch, delivery and scheduler owners atomically."""

    def __init__(self, admission):
        self.admission = admission
        self.repository = admission.repository
        self.retry_origins = admission.contracts.retry_origins

    def _validate_runtime(self, connection, *, principal, resolved, lock_work):
        runtime = self.admission
        launch = resolved.launch
        contract = runtime.contracts._contract(launch.contract)
        delivery = runtime.deliveries.envelope(launch.envelope, contract.operation_kind)
        if delivery != launch.envelope or contract.operation_kind != "launch":
            raise WorkConflict("replacement requires its sealed launch delivery")

        # Use the provisioning owner's selector lock before grant/job locks so
        # replacement admission observes the same active provision at commit.
        runtime.provisioning._lock_selector(connection, "launch", principal.id, launch.selection)
        authority_args = dict(
            job_id=launch.job_id,
            grant_id=launch.grant_id,
            expected_grant_revision=launch.grant_revision,
            provider=contract.provider,
            requested_permissions=runtime.contracts._permissions(contract),
        )
        runtime.authority._authorize(connection, principal, **authority_args)
        current_provision = runtime.provisioning._resolve_launch(
            connection,
            principal,
            launch.selection,
            expected_ref=launch.provision.ref,
        )
        if (
            current_provision != launch.provision
            or current_provision.fingerprint() != launch.provision_fingerprint
            or current_provision.contract.canonical_hash() != contract.canonical_hash()
            or current_provision.job_id != launch.job_id
            or current_provision.grant_id != launch.grant_id
            or current_provision.grant_revision != launch.grant_revision
        ):
            raise WorkConflict("replacement current provision changed after resolution")
        if launch.selection_auto_resolved:
            raise WorkConflict("replacement admission requires an explicit launch selector")

        if lock_work:
            self.repository._lock_work_item_for_write(connection, resolved.work_item_id)
        work = self.repository._work(connection, resolved.work_item_id)
        if (
            work["job_id"] != launch.job_id
            or work["origin_protocol"] != "launch-v1"
            or work["lineage_protocol"] != "legacy"
            or work["operation_kind"] != "launch"
            or work["contract_id"] != contract.id
            or work["snapshot_id"] != contract.snapshot.id
            or work["attempts"][-1]["provider"] != contract.provider
            or work["attempts"][-1]["id"] != resolved.expected_attempt_id
            or work["attempts"][-1]["generation"] != resolved.generation
        ):
            raise WorkConflict("replacement work does not match the current launch context")
        existing = connection.execute(
            "SELECT * FROM work_launch_retry_bindings WHERE attempt_id=%s AND generation=%s",
            (resolved.expected_attempt_id, resolved.generation),
        ).fetchone()
        # A new admission must observe the live release/replan events; an existing
        # order may rely on its immutable copies once retention pruned them.
        history = self.retry_origins._history(
            connection,
            work=work,
            generation=resolved.generation,
            principal_id=principal.id,
            recorded=existing,
        )
        if existing is None:
            collision = connection.execute(
                "SELECT attempt_id,generation FROM work_launch_retry_bindings "
                "WHERE retry_admission_key=%s",
                (resolved.retry_idempotency_key,),
            ).fetchone()
            if collision is not None:
                raise WorkConflict("replacement admission identity belongs to another generation")
            self.retry_origins.validate_new(
                connection,
                work=work,
                generation=resolved.generation,
                principal_id=principal.id,
                expected_work_revision=resolved.expected_work_revision,
                expected_attempt_id=resolved.expected_attempt_id,
                expected_attempt_revision=resolved.planned_attempt_revision,
            )
            return work, history, None, contract, delivery

        binding = runtime.contracts._existing(
            connection,
            attempt_id=resolved.expected_attempt_id,
            generation=resolved.generation,
            principal_id=principal.id,
            grant_id=launch.grant_id,
            grant_revision=launch.grant_revision,
            contract=contract,
        )
        if binding is None:
            raise WorkConflict("replacement retry origin has no current contract binding")
        self.retry_origins.replay(
            connection,
            history=history,
            binding=binding,
            handoff=resolved,
            principal=principal,
            deliveries=runtime.deliveries,
        )
        return work, history, binding, contract, delivery

    def admit(self, principal, resolved):
        """Bind a fresh order and enqueue exactly this planned generation."""
        runtime = self.admission
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            work, _, binding, contract, delivery = self._validate_runtime(
                connection, principal=principal, resolved=resolved, lock_work=False
            )
            if binding is not None:
                return work

        prepared = runtime.deliveries.prepare(delivery, contract.operation_kind, contract=contract)
        runtime._preflight(contract)

        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            work, history, binding, contract, delivery = self._validate_runtime(
                connection, principal=principal, resolved=resolved, lock_work=True
            )
            if binding is not None:
                return work
            current = history["current"]
            binding = runtime.contracts._bind(
                connection,
                principal=principal,
                attempt_id=current["id"],
                generation=current["generation"],
                expected_attempt_revision=resolved.planned_attempt_revision,
                grant_id=resolved.launch.grant_id,
                expected_grant_revision=resolved.launch.grant_revision,
                contract=contract,
            )
            runtime.deliveries._bind(connection, binding, prepared, request=delivery)
            runtime.scheduler._enqueue(
                connection,
                attempt_id=current["id"],
                generation=current["generation"],
                expected_attempt_revision=resolved.planned_attempt_revision,
                units=contract.resources.units,
                dependencies=contract.resources.dependencies,
                actor_id=principal.id,
            )
            self.retry_origins.bind(
                connection,
                history=history,
                binding=binding,
                handoff=resolved,
                principal=principal,
            )
            return self.repository._work(connection, work["id"])
