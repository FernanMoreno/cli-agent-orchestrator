"""Atomic admission of a server-resolved order, without starting an external process.

Backend registry and contracts are server dependencies, not public request bodies.
Admission only queues work: a separate dispatcher must acquire paths and capacity,
commit intent, and recheck authority at the protected external-effect boundary.
"""

import asyncio
import logging
import os
import time
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

from cli_agent_orchestrator.backends.base import ProcessRestrictionContract, TerminalBackend
from cli_agent_orchestrator.clients.work_repository import WorkConflict, WorkRepository
from cli_agent_orchestrator.constants import FIFO_DIR
from cli_agent_orchestrator.models.work_contract import EffectiveWorkContractV2
from cli_agent_orchestrator.models.work_origin import ProvisionedLaunch
from cli_agent_orchestrator.services.delegation_snapshot import (
    DelegationSnapshots,
    SnapshotConflict,
    SnapshotUnavailable,
)
from cli_agent_orchestrator.services.work_attempt_credential import (
    WorkAttemptCredentials,
    create_attempt_credential_descriptor,
)
from cli_agent_orchestrator.services.work_authority import (
    AuthorityDenied,
    GrantConflict,
    WorkAuthority,
)
from cli_agent_orchestrator.services.work_contract import ContractConflict, WorkContracts
from cli_agent_orchestrator.services.work_delivery import WorkDeliveries
from cli_agent_orchestrator.services.work_provisioning import (
    ProvisionUnavailable,
    WorkProvisioning,
)
from cli_agent_orchestrator.services.work_reducer import TransitionEvidence
from cli_agent_orchestrator.services.work_reservations import ReservationConflict, WorkReservations
from cli_agent_orchestrator.services.work_scheduler import WorkScheduler
from cli_agent_orchestrator.services.work_service import WorkService
from cli_agent_orchestrator.services.work_terminal import terminal_dispatch_lock

logger = logging.getLogger(__name__)


class _SelectionChanged(Exception):
    """Another dispatcher won during preflight; retry selection, never its effect."""


class _WorkEffectPort:
    """Only protected effects with the exact frozen restriction and server guard."""

    def __init__(
        self,
        backend,
        restriction,
        guard,
        release,
        terminal_id,
        expected_target=None,
        binding=None,
        guard_connection=None,
        attempt_revision=None,
        attempt_credential_fd=None,
        receiver_credential_fd=None,
    ):
        self._backend, self._restriction, self._guard, self._release = (
            backend,
            restriction,
            guard,
            release,
        )
        self._terminal_id = terminal_id
        self._binding = binding
        self._guard_connection = guard_connection
        self._attempt_revision = attempt_revision
        self._attempt_credential_fd = attempt_credential_fd
        self._receiver_credential_fd = receiver_credential_fd
        self._expected_target = expected_target
        self._closed = False
        from cli_agent_orchestrator.backends.work_backend import WorkBackendView

        self._view = WorkBackendView(
            backend,
            restriction,
            self._before_effect,
            terminal_id=terminal_id,
            expected_target=expected_target,
            assert_open=self._assert_open,
        )

    def _assert_open(self):
        if self._closed:
            raise WorkConflict("delivery callback ended; effect capability is closed")

    @property
    def expected_target(self):
        """The immutable session/window reserved by the restored delivery payload."""
        self._assert_open()
        return self._expected_target

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                for attribute in ("_attempt_credential_fd", "_receiver_credential_fd"):
                    descriptor = getattr(self, attribute)
                    if descriptor is not None:
                        os.close(descriptor)
                        setattr(self, attribute, None)
            finally:
                self._release()

    def _before_effect(
        self, effect, terminal_id, session_name=None, window_name=None, file_path=None
    ):
        self._assert_open()
        self._guard(effect, terminal_id, session_name, window_name, file_path)

    def revalidate(self):
        """Check the live order fence before a managed delivery boundary."""
        self._assert_open()
        self._view._revalidate_before_effect()

    def create_session(self, *args, **kwargs):
        return self._view.create_session(*args, **kwargs)

    def create_window(self, *args, **kwargs):
        return self._view.create_window(*args, **kwargs)

    def send_keys(self, *args, **kwargs):
        return self._view.send_keys(*args, **kwargs)

    def bind_terminal_target(self, terminal_id, session_name, window_name):
        return self._view.bind_terminal_target(terminal_id, session_name, window_name)

    def revalidate_target(self, terminal_id, session_name, window_name):
        return self._view._revalidate_before_effect(terminal_id, session_name, window_name)

    def execute_process(self, command_token, worker_input):
        """Run the one server-selected process effect; terminal methods are unavailable."""
        self._assert_open()
        from cli_agent_orchestrator.models.work_contract import EffectiveWorkContractV2

        binding = self._binding
        if binding is None or not isinstance(binding.contract, EffectiveWorkContractV2):
            raise WorkConflict("process launch requires a bound V2 work contract")
        mapped = tuple(
            item
            for item in binding.contract.executable_identities
            if item.command_token == command_token
        )
        if len(binding.contract.executable_identities) != 1 or len(mapped) != 1:
            raise WorkConflict("process launch requires one exact immutable executable mapping")
        if not isinstance(worker_input, bytes) or len(worker_input) > 32768:
            raise WorkConflict("process worker input is outside its byte bound")
        executor = getattr(self._backend, "execute_bound_process", None)
        if (
            not callable(executor)
            or not callable(self._guard_connection)
            or type(self._attempt_credential_fd) is not int
        ):
            raise WorkConflict("selected Work backend has no protected process capability")
        authorize = lambda connection: self._guard_connection(
            connection, "execute_process", command_token
        )
        process_options = dict(
            binding=binding,
            command_token=command_token,
            worker_input=worker_input,
            expected_attempt_revision=self._attempt_revision,
            attempt_credential_fd=self._attempt_credential_fd,
            before_effect=lambda: self._before_effect(
                "execute_process", None, file_path=command_token
            ),
            authorize_setup=authorize,
            authorize_go=authorize,
        )
        if self._receiver_credential_fd is not None:
            process_options["receiver_credential_fd"] = self._receiver_credential_fd
        return executor(self._restriction, **process_options)

    def backend_scope(self):
        from cli_agent_orchestrator.backends.registry import work_backend_scope

        return work_backend_scope(self._view)


class WorkAdmission:
    def __init__(
        self,
        repository: WorkRepository,
        *,
        backends: Mapping[str, TerminalBackend],
        delivery_adapters=None,
        origins=None,
        workflow_origins=None,
    ):
        if not isinstance(backends, Mapping) or any(
            not isinstance(name, str) or not name or not isinstance(backend, TerminalBackend)
            for name, backend in backends.items()
        ):
            raise ValueError("explicit server backend registry required")
        if origins is not None:
            from cli_agent_orchestrator.services.work_origin import WorkOrigins

            if not isinstance(origins, WorkOrigins) or origins.repository is not repository:
                raise ValueError("managed lineage origin must share this work runtime")
        if workflow_origins is not None:
            from cli_agent_orchestrator.services.work_workflow import WorkWorkflowOrigins

            if (
                not isinstance(workflow_origins, WorkWorkflowOrigins)
                or workflow_origins.repository is not repository
            ):
                raise ValueError("managed workflow origin must share this work runtime")
        self.repository = repository
        self.origins = origins
        self.workflow_origins = workflow_origins
        self.backends = dict(backends)
        self.authority = WorkAuthority(repository)
        self.contracts = WorkContracts(repository)
        self.provisioning = WorkProvisioning(repository)
        self.deliveries = WorkDeliveries(repository, delivery_adapters)
        self.attempt_credentials = WorkAttemptCredentials(repository, origins=origins)
        self.scheduler = WorkScheduler(repository)
        self.reservations = WorkReservations(repository)
        self._pending_abandonments: set[asyncio.Task[None]] = set()
        if origins is not None:
            origins._bind_admission(self)
        if workflow_origins is not None:
            workflow_origins._bind_admission(self)

    @staticmethod
    def _restriction(contract):
        return ProcessRestrictionContract(
            paths=contract.resources.write_paths,
            commands=contract.permissions.commands,
            network=contract.permissions.network,
            tools=contract.permissions.tools,
            read_paths=contract.permissions.paths,
            checkout_root=contract.resources.checkout_root,
            executable_identities=(
                contract.executable_identities
                if isinstance(contract, EffectiveWorkContractV2)
                else ()
            ),
        )

    @staticmethod
    def _check_terminal_effect_target(
        connection,
        *,
        attempt_id,
        bound_terminal_id,
        effect,
        terminal_id,
        session_name,
        window_name,
        file_path=None,
    ):
        """Re-read the attempt and terminal registry on the same SQLite snapshot."""
        if not isinstance(bound_terminal_id, str) or not bound_terminal_id:
            raise WorkConflict("dispatch attempt has no durable terminal binding")
        if terminal_id != bound_terminal_id:
            raise WorkConflict("effect terminal differs from the durable attempt binding")
        actual = connection.execute(
            "SELECT terminal_id FROM work_attempts WHERE id=?", (attempt_id,)
        ).fetchone()
        if actual is None or actual["terminal_id"] != bound_terminal_id:
            raise WorkConflict("durable terminal binding changed before effect")

        if effect == "revalidate":
            return
        if not isinstance(session_name, str) or not session_name:
            raise WorkConflict("effect session is required")
        if effect in {"kill_session", "kill_window", "rollback_created_session"}:
            raise WorkConflict("terminal teardown requires a server-instance-fenced identity")
        if not isinstance(window_name, str) or not window_name:
            raise WorkConflict("effect window is required")
        try:
            target = connection.execute(
                "SELECT id,tmux_session,tmux_window FROM terminals WHERE id=?",
                (bound_terminal_id,),
            ).fetchone()
            session_owner = connection.execute(
                "SELECT 1 FROM terminals WHERE tmux_session=? AND id<>? LIMIT 1",
                (session_name, bound_terminal_id),
            ).fetchone()
        except Exception as error:
            # Work and terminal rows share the configured SQLite database. An
            # absent/unreadable terminal table is not evidence of ownership.
            raise WorkConflict("terminal registry cannot verify the effect target") from error
        if effect == "create_session":
            if target is not None or session_owner is not None:
                raise WorkConflict("new Work session identity is already registered")
            return
        if (
            target is None
            or target["tmux_session"] != session_name
            or target["tmux_window"] != window_name
        ):
            raise WorkConflict("session/window does not match the durable terminal row")
        if effect == "create_window":
            raise WorkConflict("new Work windows require a durable target reservation")
        if effect == "pipe_pane":
            expected_path = str(FIFO_DIR / f"{bound_terminal_id}.fifo")
            if file_path != expected_path:
                raise WorkConflict("pipe path does not match the terminal FIFO")

    def _preflight(self, contract):
        try:
            backend = self.backends[contract.backend]
        except KeyError:
            raise ValueError("effective backend is not registered") from None
        # Grant paths are a read-only ceiling; reservations alone grant writes.
        # Backends must compile both modes or reject before any external effect.
        backend.preflight_work(self._restriction(contract))

    def admit_managed_lineage(self, handoff):
        """Consume only a private ``WorkOrigins`` handoff; generic callers stay legacy."""
        from cli_agent_orchestrator.services.work_origin import WorkOrigins

        origin = getattr(handoff, "_origin", None)
        if not isinstance(origin, WorkOrigins):
            raise WorkConflict("managed lineage requires an internal origin handoff")
        if self.origins is not None and origin is not self.origins:
            raise WorkConflict("managed lineage origin belongs to another runtime")
        return origin._consume(self, handoff)

    @staticmethod
    def _requires_managed_lineage(request) -> bool:
        return request["parent_work_item_id"] is not None or request["operation_kind"] in {
            "child",
            "handoff",
        }

    @staticmethod
    def _legacy_replay(work):
        if work["lineage_protocol"] != "legacy":
            raise WorkConflict("managed lineage must use its internal origin")
        return work

    def _replay(
        self, connection, *, request, principal, grant_id, grant_revision, contract, delivery
    ):
        existing = connection.execute(
            "SELECT id FROM work_items WHERE job_id=? AND operation_kind=? AND idempotency_key=?",
            (request["job_id"], request["operation_kind"], request["idempotency_key"]),
        ).fetchone()
        if existing is None:
            return None
        # Reuse the repository's exact request comparison, without creating a row.
        work = self.repository._admit_work(connection, **request)
        original = work["attempts"][0]
        binding = self.contracts._existing(
            connection,
            attempt_id=original["id"],
            generation=original["generation"],
            principal_id=principal.id,
            grant_id=grant_id,
            grant_revision=grant_revision,
            contract=contract,
        )
        queued = connection.execute(
            "SELECT id FROM work_scheduler_requests WHERE attempt_id=? AND generation=?",
            (original["id"], original["generation"]),
        ).fetchone()
        if binding is None or queued is None:
            raise WorkConflict("existing operation has no complete admitted order")
        self.deliveries._compare(connection, binding, delivery)
        return work

    @staticmethod
    def _launch_origin(origin, fingerprint):
        if (
            not isinstance(origin, ProvisionedLaunch)
            or not isinstance(fingerprint, str)
            or origin.fingerprint() != fingerprint
        ):
            raise WorkConflict("launch origin handoff is not immutable")
        return origin

    def _revalidate_launch_origin(self, connection, *, principal, origin, fingerprint, request):
        """Fence one admission transaction to the exact provision in the handoff."""
        origin = self._launch_origin(origin, fingerprint)
        current = self.provisioning._resolve_launch(
            connection, principal, origin.ref.selector, expected_ref=origin.ref
        )
        if (
            current != origin
            or current.fingerprint() != fingerprint
            or (
                origin.job_id,
                origin.grant_id,
                origin.grant_revision,
                origin.contract.id,
                origin.contract_hash,
                origin.snapshot_id,
                origin.snapshot_hash,
                origin.lease_seconds,
                origin.adapter_version,
            )
            != (
                request["job_id"],
                request["grant_id"],
                request["grant_revision"],
                request["contract"].id,
                request["contract"].canonical_hash(),
                request["contract"].snapshot.id,
                request["contract"].snapshot.delivered_hash,
                request["lease_seconds"],
                request["delivery"].adapter_version,
            )
        ):
            raise WorkConflict("launch origin does not match the admitted order")
        require_unique_selection = request.get("require_unique_selection", False)
        if type(require_unique_selection) is not bool:
            raise WorkConflict("launch selection resolution is not immutable")
        if require_unique_selection:
            unique = self.provisioning._resolve_unique_active_launch(connection, principal)
            if unique != origin or unique.fingerprint() != fingerprint:
                raise ProvisionUnavailable("durable launch provision changed")
        return origin

    def _replay_launch(
        self,
        connection,
        *,
        request,
        principal,
        grant_id,
        grant_revision,
        contract,
        delivery,
        origin,
        fingerprint,
    ):
        """Replay only the immutable launch origin, including cross-job key collisions."""
        row = connection.execute(
            "SELECT * FROM work_launch_origin_bindings WHERE principal_id=? AND selector=? "
            "AND idempotency_key=?",
            (principal.id, origin.ref.selector, request["idempotency_key"]),
        ).fetchone()
        if row is None:
            existing = connection.execute(
                "SELECT id FROM work_items WHERE job_id=? AND operation_kind=? AND idempotency_key=?",
                (request["job_id"], request["operation_kind"], request["idempotency_key"]),
            ).fetchone()
            if existing is not None:
                raise WorkConflict("launch idempotency key names a legacy operation")
            return None
        if (
            row["schema_version"],
            row["origin_kind"],
            row["principal_id"],
            row["selector"],
            row["provision_id"],
            row["provision_revision"],
            row["provision_fingerprint"],
            row["requester_principal_id"],
            row["executor_principal_id"],
            row["job_id"],
            row["request_hash"],
            row["idempotency_key"],
        ) != (
            1,
            "launch",
            principal.id,
            origin.ref.selector,
            origin.ref.id,
            origin.ref.revision,
            fingerprint,
            principal.id,
            principal.id,
            request["job_id"],
            request["request_hash"],
            request["idempotency_key"],
        ):
            raise WorkConflict("launch idempotency key conflicts with durable origin")
        work = self.repository._admit_work(connection, **request)
        original = work["attempts"][0]
        marker = connection.execute(
            "SELECT origin_protocol FROM work_items WHERE id=?", (work["id"],)
        ).fetchone()
        binding = self.contracts._existing(
            connection,
            attempt_id=original["id"],
            generation=original["generation"],
            principal_id=principal.id,
            grant_id=grant_id,
            grant_revision=grant_revision,
            contract=contract,
        )
        queued = connection.execute(
            "SELECT id FROM work_scheduler_requests WHERE attempt_id=? AND generation=?",
            (original["id"], original["generation"]),
        ).fetchone()
        if marker is None or marker[0] != "launch-v1" or binding is None or queued is None:
            raise WorkConflict("launch replay has no complete durable origin")
        self.deliveries._compare(connection, binding, delivery)
        return work

    def _replay_legacy_launch(
        self,
        connection,
        *,
        request,
        principal,
        grant_id,
        grant_revision,
        contract,
        delivery,
    ):
        """Read one complete pre-v21 launch order; this path can never admit one."""
        existing = connection.execute(
            "SELECT id,origin_protocol FROM work_items WHERE job_id=? AND operation_kind=? "
            "AND idempotency_key=?",
            (request["job_id"], request["operation_kind"], request["idempotency_key"]),
        ).fetchone()
        if existing is None:
            raise WorkConflict("preexisting legacy launch does not exist")
        if existing["origin_protocol"] != "legacy":
            raise WorkConflict("preexisting launch is not legacy")
        # The existence check above is deliberately before _admit_work: its
        # idempotency comparison is now read-only and cannot insert this row.
        work = self.repository._admit_work(connection, **request)
        original = work["attempts"][0]
        origin = connection.execute(
            "SELECT attempt_id FROM work_launch_origin_bindings WHERE attempt_id=? AND generation=?",
            (original["id"], original["generation"]),
        ).fetchone()
        if origin is not None:
            raise WorkConflict("legacy launch contradicts its durable origin binding")
        binding = self.contracts._existing(
            connection,
            attempt_id=original["id"],
            generation=original["generation"],
            principal_id=principal.id,
            grant_id=grant_id,
            grant_revision=grant_revision,
            contract=contract,
        )
        queued = connection.execute(
            "SELECT id FROM work_scheduler_requests WHERE attempt_id=? AND generation=?",
            (original["id"], original["generation"]),
        ).fetchone()
        if binding is None or queued is None:
            raise WorkConflict("preexisting legacy launch has no complete admitted order")
        self.deliveries._compare(connection, binding, delivery)
        return work

    def _bind_launch_origin(self, connection, *, binding, origin, fingerprint, request, principal):
        """Insert the immutable companion row in the caller's final transaction."""
        work = connection.execute(
            "SELECT origin_protocol,request_hash,idempotency_key FROM work_items WHERE id=?",
            (binding.work_item_id,),
        ).fetchone()
        if (
            work is None
            or work["origin_protocol"] != "launch-v1"
            or (work["request_hash"], work["idempotency_key"])
            != (request["request_hash"], request["idempotency_key"])
        ):
            raise WorkConflict("launch origin marker is missing")
        connection.execute(
            "INSERT INTO work_launch_origin_bindings "
            "(attempt_id,generation,schema_version,origin_kind,principal_id,selector,provision_id,"
            "provision_revision,provision_fingerprint,requester_principal_id,executor_principal_id,"
            "job_id,work_item_id,request_hash,idempotency_key,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                binding.attempt_id,
                binding.generation,
                1,
                "launch",
                origin.ref.principal_id,
                origin.ref.selector,
                origin.ref.id,
                origin.ref.revision,
                fingerprint,
                principal.id,
                principal.id,
                binding.job_id,
                binding.work_item_id,
                request["request_hash"],
                request["idempotency_key"],
                time.time(),
            ),
        )

    def _replay_retained_inbox(
        self, connection, *, request, principal, grant_id, grant_revision, contract
    ):
        """Recover a reservation only while it cannot be selected for dispatch."""
        existing = connection.execute(
            "SELECT id FROM work_items WHERE job_id=? AND operation_kind=? AND idempotency_key=?",
            (request["job_id"], request["operation_kind"], request["idempotency_key"]),
        ).fetchone()
        if existing is None:
            return None
        work = self.repository._admit_work(connection, **request)
        original = work["attempts"][0]
        binding = self.contracts._existing(
            connection,
            attempt_id=original["id"],
            generation=original["generation"],
            principal_id=principal.id,
            grant_id=grant_id,
            grant_revision=grant_revision,
            contract=contract,
        )
        queued = connection.execute(
            "SELECT id FROM work_scheduler_requests WHERE attempt_id=? AND generation=?",
            (original["id"], original["generation"]),
        ).fetchone()
        order = connection.execute(
            "SELECT attempt_id FROM work_delivery_orders WHERE attempt_id=? AND generation=?",
            (original["id"], original["generation"]),
        ).fetchone()
        if binding is None:
            raise WorkConflict("retained managed inbox operation has no dispatch binding")
        if queued is not None or order is not None:
            bridge = self.repository._inbox_binding(
                connection, attempt_id=original["id"], generation=original["generation"]
            )
            if bridge is None:
                raise WorkConflict("existing operation is already enabled for dispatch")
        return work

    def admit(
        self,
        *,
        principal,
        job_id,
        idempotency_key,
        request_hash,
        grant_id,
        expected_grant_revision,
        contract,
        lease_seconds=60,
        parent_work_item_id=None,
        delivery=None,
        launch_origin=None,
        launch_fingerprint=None,
        require_unique_selection=False,
        _workflow_origin=None,
    ):
        contract = self.contracts._contract(contract)
        if delivery is not None:
            delivery = self.deliveries.envelope(delivery, contract.operation_kind)
        authority_args = dict(
            job_id=job_id,
            grant_id=grant_id,
            expected_grant_revision=expected_grant_revision,
            provider=contract.provider,
            requested_permissions=self.contracts._permissions(contract),
        )
        request = dict(
            job_id=job_id,
            operation_kind=contract.operation_kind,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            contract_id=contract.id,
            snapshot_id=contract.snapshot.id,
            provider=contract.provider,
            actor_id=getattr(principal, "id", None),
            lease_seconds=lease_seconds,
            parent_work_item_id=parent_work_item_id,
        )
        if (launch_origin is None) != (launch_fingerprint is None):
            raise WorkConflict("launch origin handoff is incomplete")
        origin = (
            self._launch_origin(launch_origin, launch_fingerprint)
            if launch_origin is not None
            else None
        )
        if type(require_unique_selection) is not bool:
            raise WorkConflict("launch selection resolution is not immutable")
        if require_unique_selection and origin is None:
            raise WorkConflict("unique selection admission requires a launch origin")
        if origin is not None and contract.operation_kind == "launch" and delivery is None:
            raise WorkConflict("launch origin admission requires a delivery envelope")
        if origin is None and contract.operation_kind == "launch":
            raise WorkConflict("new launch admission requires a durable origin")
        workflow_origin = None
        if _workflow_origin is not None:
            if self.workflow_origins is None:
                raise WorkConflict("managed workflow origin is unavailable")
            workflow_origin = self.workflow_origins._accept_handoff(_workflow_origin)
            if origin is not None or parent_work_item_id is not None:
                raise WorkConflict("workflow step cannot combine with another origin")
            if (
                workflow_origin.principal.id != getattr(principal, "id", None)
                or workflow_origin.job_id != job_id
                or workflow_origin.grant_id != grant_id
                or workflow_origin.grant_revision != expected_grant_revision
                or workflow_origin.idempotency_key != idempotency_key
                or workflow_origin.request_hash != request_hash
                or workflow_origin.lease_seconds != lease_seconds
                or workflow_origin.contract.canonical_hash() != contract.canonical_hash()
                or workflow_origin.delivery != delivery
                or contract.operation_kind != "agent_step"
                or delivery is None
            ):
                raise WorkConflict("workflow origin does not match its admitted step")
        elif isinstance(idempotency_key, str) and idempotency_key.startswith("workflow-step-v1:"):
            raise WorkConflict("managed workflow origin required for reserved step identity")
        launch_request = dict(
            request,
            grant_id=grant_id,
            grant_revision=expected_grant_revision,
            contract=contract,
            delivery=delivery,
            lease_seconds=lease_seconds,
            require_unique_selection=require_unique_selection,
        )
        replay_args = dict(
            request=request,
            principal=principal,
            grant_id=grant_id,
            grant_revision=expected_grant_revision,
            contract=contract,
            delivery=delivery,
        )
        requires_managed_origin = self._requires_managed_lineage(request)
        # A replay needs current authority but no backend, live lease, or live parent.
        # Never run preflight for an unauthenticated/unauthorized selector.
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self.authority._authorize(connection, principal, **authority_args)
            if workflow_origin is not None:
                self.workflow_origins._revalidate(connection, workflow_origin)
            if origin is not None:
                self._revalidate_launch_origin(
                    connection,
                    principal=principal,
                    origin=origin,
                    fingerprint=launch_fingerprint,
                    request=launch_request,
                )
                existing = self._replay_launch(
                    connection,
                    **replay_args,
                    origin=origin,
                    fingerprint=launch_fingerprint,
                )
            else:
                existing = self._replay(connection, **replay_args)
            if existing is not None:
                if workflow_origin is not None:
                    self.workflow_origins._confirm_replay(connection, workflow_origin, existing)
                    return existing
                return self._legacy_replay(existing)
            if requires_managed_origin:
                raise WorkConflict("managed lineage origin required")
        prepared_delivery = (
            self.deliveries.prepare(delivery, contract.operation_kind, contract=contract)
            if delivery is not None
            else None
        )
        self._preflight(contract)
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self.authority._authorize(connection, principal, **authority_args)
            if workflow_origin is not None:
                self.workflow_origins._revalidate(connection, workflow_origin)
            # A competing admission can win while this caller was in preflight.
            if origin is not None:
                self._revalidate_launch_origin(
                    connection,
                    principal=principal,
                    origin=origin,
                    fingerprint=launch_fingerprint,
                    request=launch_request,
                )
                existing = self._replay_launch(
                    connection,
                    **replay_args,
                    origin=origin,
                    fingerprint=launch_fingerprint,
                )
            else:
                existing = self._replay(connection, **replay_args)
            if existing is not None:
                if workflow_origin is not None:
                    self.workflow_origins._confirm_replay(connection, workflow_origin, existing)
                    return existing
                return self._legacy_replay(existing)
            if requires_managed_origin:
                raise WorkConflict("managed lineage origin required")
            work = self.repository._admit_work(connection, **request)
            original = work["attempts"][0]
            if origin is not None:
                marked = connection.execute(
                    "UPDATE work_items SET origin_protocol='launch-v1' "
                    "WHERE id=? AND origin_protocol='legacy'",
                    (work["id"],),
                )
                if marked.rowcount != 1:
                    raise WorkConflict("launch origin marker changed during admission")
            binding = self.contracts._bind(
                connection,
                principal=principal,
                attempt_id=original["id"],
                generation=original["generation"],
                expected_attempt_revision=original["revision"],
                grant_id=grant_id,
                expected_grant_revision=expected_grant_revision,
                contract=contract,
            )
            if origin is not None:
                self._bind_launch_origin(
                    connection,
                    binding=binding,
                    origin=origin,
                    fingerprint=launch_fingerprint,
                    request=launch_request,
                    principal=principal,
                )
            self.deliveries._bind(connection, binding, prepared_delivery, request=delivery)
            if workflow_origin is not None:
                self.workflow_origins._bind_admitted(
                    connection,
                    workflow_origin,
                    work=work,
                    contract_binding=binding,
                )
            self.scheduler._enqueue(
                connection,
                attempt_id=original["id"],
                generation=original["generation"],
                expected_attempt_revision=original["revision"],
                units=contract.resources.units,
                dependencies=contract.resources.dependencies,
                actor_id=principal.id,
            )
            return self.repository._work(connection, work["id"])

    def replay_legacy_launch(
        self,
        *,
        principal,
        job_id,
        idempotency_key,
        request_hash,
        grant_id,
        expected_grant_revision,
        contract,
        lease_seconds=60,
        parent_work_item_id=None,
        delivery=None,
    ):
        """Replay an exact preexisting legacy launch without creating or queueing work."""
        contract = self.contracts._contract(contract)
        if contract.operation_kind != "launch":
            raise WorkConflict("legacy replay requires a launch contract")
        if delivery is not None:
            delivery = self.deliveries.envelope(delivery, contract.operation_kind)
        authority_args = dict(
            job_id=job_id,
            grant_id=grant_id,
            expected_grant_revision=expected_grant_revision,
            provider=contract.provider,
            requested_permissions=self.contracts._permissions(contract),
        )
        request = dict(
            job_id=job_id,
            operation_kind=contract.operation_kind,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            contract_id=contract.id,
            snapshot_id=contract.snapshot.id,
            provider=contract.provider,
            actor_id=getattr(principal, "id", None),
            lease_seconds=lease_seconds,
            parent_work_item_id=parent_work_item_id,
        )
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self.authority._authorize(connection, principal, **authority_args)
            return self._replay_legacy_launch(
                connection,
                request=request,
                principal=principal,
                grant_id=grant_id,
                grant_revision=expected_grant_revision,
                contract=contract,
                delivery=delivery,
            )

    def reserve_managed_inbox(
        self,
        *,
        principal,
        job_id,
        idempotency_key,
        request_hash,
        grant_id,
        expected_grant_revision,
        contract,
        managed_store_context,
        lease_seconds=60,
        parent_work_item_id=None,
    ):
        """Reserve an inbox operation before any inbox row or dispatchable order exists.

        This is intentionally a server-only staging hook. It creates the immutable
        contract binding but neither a delivery order nor a scheduler request, so a
        cross-store interruption leaves a visible non-dispatchable reservation.
        """
        contract = self.contracts._contract(contract)
        authority_args = dict(
            job_id=job_id,
            grant_id=grant_id,
            expected_grant_revision=expected_grant_revision,
            provider=contract.provider,
            requested_permissions=self.contracts._permissions(contract),
        )
        request = dict(
            job_id=job_id,
            operation_kind=contract.operation_kind,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            contract_id=contract.id,
            snapshot_id=contract.snapshot.id,
            provider=contract.provider,
            actor_id=getattr(principal, "id", None),
            lease_seconds=lease_seconds,
            parent_work_item_id=parent_work_item_id,
        )
        replay_args = dict(
            request=request,
            principal=principal,
            grant_id=grant_id,
            grant_revision=expected_grant_revision,
            contract=contract,
        )
        requires_managed_origin = self._requires_managed_lineage(request)
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self.repository._validate_managed_inbox_store_context(
                connection, expected=managed_store_context
            )
            self.authority._authorize(connection, principal, **authority_args)
            existing = self._replay_retained_inbox(connection, **replay_args)
            if existing is not None:
                return self._legacy_replay(existing), False
            if requires_managed_origin:
                raise WorkConflict("managed lineage origin required")
        self._preflight(contract)
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self.repository._validate_managed_inbox_store_context(
                connection, expected=managed_store_context
            )
            self.authority._authorize(connection, principal, **authority_args)
            existing = self._replay_retained_inbox(connection, **replay_args)
            if existing is not None:
                return self._legacy_replay(existing), False
            if requires_managed_origin:
                raise WorkConflict("managed lineage origin required")
            work = self.repository._admit_work(connection, **request)
            original = work["attempts"][0]
            self.contracts._bind(
                connection,
                principal=principal,
                attempt_id=original["id"],
                generation=original["generation"],
                expected_attempt_revision=original["revision"],
                grant_id=grant_id,
                expected_grant_revision=expected_grant_revision,
                contract=contract,
            )
            return self.repository._work(connection, work["id"]), True

    def enable_managed_inbox(self, *, attempt_id, generation, delivery, managed_store_context):
        """Bind the durable inbox order and queue it only after its bridge exists."""
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            self.repository._validate_managed_inbox_store_context(
                connection, expected=managed_store_context
            )
            binding = self.contracts._revalidate_order(
                connection, attempt_id, generation=generation
            )
            bridge = self.repository._inbox_binding(
                connection, attempt_id=attempt_id, generation=generation
            )
            if bridge is None:
                raise WorkConflict("managed inbox cannot enable without a durable bridge")
            attempt = connection.execute(
                "SELECT * FROM work_attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if (
                attempt is None
                or attempt["generation"] != generation
                or attempt["state"] != "planned"
            ):
                raise WorkConflict("managed inbox reservation is no longer dispatchable")
            delivery = self.deliveries.envelope(delivery, binding.contract.operation_kind)
            prepared = self.deliveries.prepare(
                delivery, binding.contract.operation_kind, contract=binding.contract
            )
            self.deliveries._bind(connection, binding, prepared, request=delivery)
            self.scheduler._enqueue(
                connection,
                attempt_id=attempt_id,
                generation=generation,
                expected_attempt_revision=attempt["revision"],
                units=binding.contract.resources.units,
                dependencies=binding.contract.resources.dependencies,
                actor_id=binding.principal_id,
            )
            return self.repository._work(connection, binding.work_item_id)

    def _retire_unexecutable(self, connection, request):
        """Fail only never-sent work; retiring a queue entry never releases a writer."""
        actor = "system:work-dispatcher"
        attempt = connection.execute(
            "SELECT * FROM work_attempts WHERE id=?", (request["attempt_id"],)
        ).fetchone()
        if attempt is None:
            raise WorkConflict("scheduler request has no attempt")
        if attempt["state"] == "planned":
            self.repository._transition_attempt(
                connection,
                attempt_id=attempt["id"],
                generation=attempt["generation"],
                expected_revision=attempt["revision"],
                expected_state="planned",
                target="failed",
                actor_id=actor,
                event_id=uuid4().hex,
                evidence=TransitionEvidence(
                    generation=attempt["generation"], expected_generation=attempt["generation"]
                ),
            )
            attempt = connection.execute(
                "SELECT * FROM work_attempts WHERE id=?", (attempt["id"],)
            ).fetchone()
        if attempt["state"] in {"finished", "failed", "cancelled"}:
            self.scheduler._withdraw_queued(
                connection,
                request["id"],
                generation=request["generation"],
                expected_revision=request["revision"],
                expected_attempt_revision=attempt["revision"],
                actor_id=actor,
            )

    def _reserve_order(self, connection, binding, request):
        resources = binding.contract.resources
        if not resources.write_paths:
            return None  # Backend denies all path access, never inherits grant paths.
        attempt = connection.execute(
            "SELECT * FROM work_attempts WHERE id=?", (binding.attempt_id,)
        ).fetchone()
        return self.reservations._reserve(
            connection,
            job_id=binding.job_id,
            work_item_id=binding.work_item_id,
            attempt_id=binding.attempt_id,
            generation=binding.generation,
            expected_attempt_revision=attempt["revision"],
            checkout_root=Path(resources.checkout_root),
            paths=resources.write_paths,
            expires_at=min(attempt["lease_expires_at"], request["expires_at"]),
            actor_id=binding.principal_id,
        )

    def _eligible(self, connection, *, registered_only=False):
        """Use owners' checks under savepoints; no duplicated path-conflict rules."""
        eligible = {}
        requests = connection.execute(
            "SELECT * FROM work_scheduler_requests WHERE state='queued' ORDER BY queued_at,rowid"
        ).fetchall()
        for request in requests:
            try:
                binding = self.contracts._revalidate_order(
                    connection, request["attempt_id"], generation=request["generation"]
                )
            except (
                AuthorityDenied,
                GrantConflict,
                ContractConflict,
                SnapshotConflict,
                SnapshotUnavailable,
            ):
                self._retire_unexecutable(connection, request)
                continue
            if binding.contract.backend not in self.backends:
                continue  # A missing server adapter is not permission to substitute one.
            if registered_only:
                try:
                    if not self.deliveries._executable(connection, binding):
                        continue
                except ContractConflict:
                    logger.warning(
                        "Registered delivery is unavailable; leaving operation queued",
                        extra={"attempt_id": binding.attempt_id, "generation": binding.generation},
                    )
                    continue
            connection.execute("SAVEPOINT work_path_probe")
            try:
                self._reserve_order(connection, binding, request)
            except ReservationConflict:
                continue
            else:
                eligible[binding.attempt_id] = binding
            finally:
                connection.execute("ROLLBACK TO work_path_probe")
                connection.execute("RELEASE work_path_probe")
        return eligible

    def _preview(self, *, registered_only=False):
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            eligible = self._eligible(connection, registered_only=registered_only)
            connection.execute("SAVEPOINT work_queue_probe")
            try:
                selected = self.scheduler._claim_next(
                    connection,
                    actor_id="system:work-dispatcher",
                    eligible_attempts=frozenset(eligible),
                )
                return eligible[selected.attempt_id] if selected is not None else None
            finally:
                connection.execute("ROLLBACK TO work_queue_probe")
                connection.execute("RELEASE work_queue_probe")

    def _commit_dispatch(
        self,
        preview,
        *,
        registered_only=False,
        expected_terminal_id=None,
        expected_terminal_target=None,
    ):
        with self.repository.transaction() as connection:
            self.repository._verify(connection)
            eligible = self._eligible(connection, registered_only=registered_only)
            selected = self.scheduler._claim_next(
                connection,
                actor_id="system:work-dispatcher",
                eligible_attempts=frozenset(eligible),
            )
            if selected is None:
                return None
            binding = eligible[selected.attempt_id]
            if (binding.attempt_id, binding.generation, binding.contract_hash) != (
                preview.attempt_id,
                preview.generation,
                preview.contract_hash,
            ):
                raise _SelectionChanged()
            request = self.scheduler._row(connection, selected.id)
            paths = self._reserve_order(connection, binding, request)
            target_identity = None
            if registered_only:
                target_identity = self.deliveries._terminal_target(connection, binding)
                terminal_id = (
                    target_identity[0]
                    if target_identity is not None
                    else self.deliveries._terminal_identity(connection, binding)
                )
                if terminal_id != expected_terminal_id:
                    raise _SelectionChanged()
                if target_identity != expected_terminal_target:
                    raise WorkConflict("registered delivery target changed during dispatch")
                if terminal_id is not None:
                    self.repository._attach_dispatch_terminal(
                        connection,
                        binding.attempt_id,
                        binding.generation,
                        terminal_id=terminal_id,
                        actor_id=binding.principal_id,
                    )
            attempt = connection.execute(
                "SELECT * FROM work_attempts WHERE id=?", (binding.attempt_id,)
            ).fetchone()
            sent = self.repository._transition_attempt(
                connection,
                attempt_id=binding.attempt_id,
                generation=binding.generation,
                expected_revision=attempt["revision"],
                expected_state="planned",
                target="sent",
                actor_id=binding.principal_id,
                event_id=uuid4().hex,
                evidence=TransitionEvidence(
                    generation=binding.generation,
                    expected_generation=binding.generation,
                    contract_confirmed=True,
                    grant_confirmed=True,
                    capacity_confirmed=True,
                    reservations_confirmed=True,
                ),
            )
            sent_attempt = sent["attempts"][-1]
            issued_credential = self.attempt_credentials.issue_in_transaction(
                connection,
                binding,
                expected_attempt_revision=sent_attempt["revision"],
            )
            credential_fd = None
            receiver_credential_fd = None
            try:
                credential_fd = create_attempt_credential_descriptor(issued_credential.secret)
                if issued_credential.receiver_secret is not None:
                    receiver_credential_fd = create_attempt_credential_descriptor(
                        issued_credential.receiver_secret
                    )
            except BaseException:
                for descriptor in (receiver_credential_fd, credential_fd):
                    if descriptor is not None:
                        os.close(descriptor)
                raise
            finally:
                del issued_credential
            return (
                binding,
                selected,
                paths,
                sent,
                target_identity,
                credential_fd,
                receiver_credential_fd,
            )

    def _prepare_dispatch(self, *, registered_only=False):
        """Commit the selected order and return its short-lived effect capability."""
        for _ in range(3):
            preview = self._preview(registered_only=registered_only)
            if preview is None:
                return None
            self._preflight(preview.contract)  # No database lock during backend work.
            try:
                if registered_only:
                    with self.repository.read_snapshot() as connection:
                        preview_target = self.deliveries._terminal_target(connection, preview)
                        preview_terminal_id = (
                            preview_target[0]
                            if preview_target is not None
                            else self.deliveries._terminal_identity(connection, preview)
                        )
                    if preview_terminal_id is None:
                        committed = self._commit_dispatch(
                            preview,
                            registered_only=True,
                            expected_terminal_id=None,
                            expected_terminal_target=preview_target,
                        )
                    else:
                        with terminal_dispatch_lock(self.repository.path, preview_terminal_id):
                            committed = self._commit_dispatch(
                                preview,
                                registered_only=True,
                                expected_terminal_id=preview_terminal_id,
                                expected_terminal_target=preview_target,
                            )
                else:
                    committed = self._commit_dispatch(preview, registered_only=False)
            except _SelectionChanged:
                continue
            if committed is None:
                return None
            (
                binding,
                capacity,
                paths,
                sent,
                target_identity,
                credential_fd,
                receiver_credential_fd,
            ) = committed
            attempt = sent["attempts"][-1]

            def check_effect_connection(
                connection,
                effect,
                target_terminal_id,
                session_name=None,
                window_name=None,
                file_path=None,
            ):
                self.repository._register_writer_effect(connection, writer_id=binding.attempt_id)
                current = self.contracts._revalidate_order(
                    connection,
                    binding.attempt_id,
                    generation=binding.generation,
                )
                if current != binding:
                    raise WorkConflict("admitted order changed before effect")
                actual = connection.execute(
                    "SELECT state,revision,terminal_id FROM work_attempts WHERE id=?",
                    (binding.attempt_id,),
                ).fetchone()
                if (actual["state"], actual["revision"]) != ("sent", attempt["revision"]):
                    raise WorkConflict("dispatch fence changed before effect")
                if effect == "execute_process":
                    if (
                        actual["terminal_id"] is not None
                        or target_terminal_id is not None
                        or session_name is not None
                        or window_name is not None
                        or file_path not in binding.contract.permissions.commands
                    ):
                        raise WorkConflict("process effect differs from its durable mapping")
                else:
                    self._check_terminal_effect_target(
                        connection,
                        attempt_id=binding.attempt_id,
                        bound_terminal_id=attempt.get("terminal_id"),
                        effect=effect,
                        terminal_id=target_terminal_id,
                        session_name=session_name,
                        window_name=window_name,
                        file_path=file_path,
                    )
                self.scheduler._checked(
                    connection,
                    capacity.id,
                    generation=binding.generation,
                    expected_revision=capacity.revision,
                    expected_attempt_revision=attempt["revision"],
                    active=True,
                )
                if paths is not None:
                    self.reservations._checked(
                        connection,
                        paths.id,
                        generation=binding.generation,
                        expected_revision=paths.revision,
                        expected_attempt_revision=attempt["revision"],
                        active=True,
                    )

            def before_effect(
                effect, target_terminal_id, session_name=None, window_name=None, file_path=None
            ):
                # WorkService holds the writer effect through the adapter call.
                # This wrapper gives ordinary backends a fresh transaction while
                # the process backend may pass the same guard into its GO txn.
                with self.repository.transaction() as connection:
                    check_effect_connection(
                        connection,
                        effect,
                        target_terminal_id,
                        session_name,
                        window_name,
                        file_path,
                    )

            def process_effect_in_transaction(connection, effect, command_token):
                check_effect_connection(connection, effect, None, file_path=command_token)

            def release_writer():
                # ``_send_committed`` / ``_send_committed_async`` registered the
                # writer before they invoked this delivery adapter and release it
                # in their own ``finally``.  Closing the port must not consume the
                # same registry row first, or their release turns a delivered
                # effect into an uncertain duplicate-cleanup failure.
                return None

            port = _WorkEffectPort(
                self.backends[binding.contract.backend],
                self._restriction(binding.contract),
                before_effect,
                release_writer,
                attempt.get("terminal_id"),
                expected_target=(
                    (target_identity[1], target_identity[2])
                    if target_identity is not None
                    else None
                ),
                binding=binding,
                guard_connection=process_effect_in_transaction,
                attempt_revision=attempt["revision"],
                attempt_credential_fd=credential_fd,
                receiver_credential_fd=receiver_credential_fd,
            )

            return binding, port, sent
        return None

    def dispatch_next(self, send):
        """Send only for the winner of atomic capacity, paths and intent selection.

        Trusted server adapter only; no callbacks or admission booleans from HTTP.
        A crash never reconstructs effect permission. None means no stable eligible
        claim in this bounded pass; polling may retry after resource changes.
        """
        if not callable(send):
            raise ValueError("server delivery adapter required")
        prepared = self._prepare_dispatch()
        if prepared is None:
            return None
        binding, port, sent = prepared

        def deliver():
            try:
                return send(binding, port)
            finally:
                port.close()

        return WorkService(self.repository, origins=self.origins)._send_committed(
            sent,
            deliver,
            actor_id=binding.principal_id,
        )

    async def _abandon_preparation(self, worker) -> None:
        # Cancelling to_thread does not stop its transaction. Observe its outcome
        # without ever invoking the adapter, including after repeated cancellation.
        try:
            prepared = await worker
            if prepared is not None:
                binding, port, sent = prepared
                port.close()
                await asyncio.to_thread(
                    WorkService(self.repository, origins=self.origins)._reconcile,
                    sent,
                    actor_id=binding.principal_id,
                )
        except Exception:
            # A store failure leaves the committed intent sent, never queued.
            # Do not log exception bodies that a backend might populate with input.
            logger.error("Cancelled dispatch preparation requires reconciliation")

    async def dispatch_next_async(self, send):
        """Prepare off-loop, deliver on the caller's loop, retain uncertain ownership.

        This supports async terminal/provider adapters without holding a SQLite
        transaction across readiness waits. Cancellation is not proof of process
        termination and therefore never releases resources or authorizes a resend.
        """
        return await self._dispatch_async(send)

    async def dispatch_registered_next(self) -> dict | None:
        """Recover delivery exclusively from its immutable order and server registry."""

        async def deliver(binding, port):
            with self.repository.read_snapshot() as connection:
                self.contracts._revalidate_order(
                    connection,
                    binding.attempt_id,
                    generation=binding.generation,
                )
                if not self.deliveries._executable(connection, binding):
                    raise WorkConflict("delivery adapter is unavailable")
                envelope = self.deliveries._load(connection, binding)
                snapshot = DelegationSnapshots._load_authorized(
                    connection,
                    connection.execute(
                        "SELECT * FROM work_delegation_snapshots WHERE id=?",
                        (binding.contract.snapshot.id,),
                    ).fetchone(),
                )
                adapter, payload = self.deliveries._restore(connection, binding)
            return await adapter.send(binding, payload, snapshot, port)

        result: dict | None = await self._dispatch_async(deliver, registered_only=True)
        return result

    async def _dispatch_async(self, send, *, registered_only=False):
        if not callable(send):
            raise ValueError("server delivery adapter required")
        worker = asyncio.create_task(
            asyncio.to_thread(
                self._prepare_dispatch,
                registered_only=registered_only,
            )
        )
        try:
            prepared = await asyncio.shield(worker)
        except asyncio.CancelledError:
            cleanup = asyncio.create_task(self._abandon_preparation(worker))
            self._pending_abandonments.add(cleanup)
            cleanup.add_done_callback(self._pending_abandonments.discard)
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                pass  # Retained cleanup continues; a dying process leaves sent.
            raise
        if prepared is None:
            return None
        binding, port, sent = prepared

        async def deliver():
            try:
                return await send(binding, port)
            finally:
                port.close()

        return await WorkService(self.repository, origins=self.origins)._send_committed_async(
            sent,
            deliver,
            actor_id=binding.principal_id,
        )
