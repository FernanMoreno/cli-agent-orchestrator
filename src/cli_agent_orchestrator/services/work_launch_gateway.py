"""Injectable, inactive application gateway for durable launch admission.

The gateway deliberately has no transport wiring and does not construct trusted
authority.  A server-owned provider supplies the already-composed runtime for
the verified principal and opaque selector; this gateway maps only public launch
content into ``LaunchIntent`` and returns the receipt from that exact admission.
"""

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from cli_agent_orchestrator.backends.base import TerminalBackend
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.security.auth import Principal
from cli_agent_orchestrator.services.work_agent_step import (
    agent_step_adapter,
    agent_step_process_adapter,
)
from cli_agent_orchestrator.services.work_authority import AuthorityDenied, WorkAuthority
from cli_agent_orchestrator.services.work_launch import (
    process_launch_adapter,
)
from cli_agent_orchestrator.services.work_launch_runtime import (
    LaunchIntent,
    LaunchReceipt,
    LaunchRuntime,
    LaunchRuntimeError,
)


class DurableLaunchGatewayError(Exception):
    """Sanitized gateway failure suitable for a future transport mapping."""

    def __init__(self, code: str, message: str, *, retryable: bool, required_action: str):
        self.code = code
        self.message = message
        self.retryable = retryable
        self.required_action = required_action
        super().__init__(message)

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "required_action": self.required_action,
        }


@runtime_checkable
class LaunchRuntimeProvider(Protocol):
    """Server dependency that resolves a pre-existing runtime without authority minting."""

    def runtime_for(self, principal: Principal, selection: str | None) -> LaunchRuntime | None: ...


class _ComposedLaunchRuntimeProvider:
    """Return shared infrastructure; per-request authority stays inside ``LaunchRuntime``."""

    def __init__(self, runtime: LaunchRuntime):
        self._runtime = runtime

    def runtime_for(self, principal: Principal, selection: str | None) -> LaunchRuntime:
        return self._runtime

    async def dispatch_registered_next(self) -> dict | None:
        """Use the same server-owned runtime for its internal queue dispatcher."""
        return await self._runtime.dispatch_registered_next()

    async def prepare_registered_dispatch(self):
        """Admit one registered order and hand off its private delivery capability."""
        return await self._runtime.prepare_registered_dispatch()

    async def deliver_registered_dispatch(self, prepared) -> dict:
        """Run the already-admitted registered delivery to its full result."""
        return await self._runtime.deliver_registered_dispatch(prepared)

    async def abandon_registered_dispatch(self, prepared) -> None:
        """Reconcile a committed handoff that could not acquire a delivery task."""
        await self._runtime.abandon_registered_dispatch(prepared)

    async def wait_for_registered_dispatch_cleanup(self) -> None:
        """Drain preparation abandonment before the shared runtime is released."""
        await self._runtime.wait_for_registered_dispatch_cleanup()


@dataclass(frozen=True, slots=True)
class DurableLaunchRequest:
    """Only the non-privileged launch inputs admitted by this application boundary."""

    selection: str | None
    agent_profile: str
    session_name: str
    message: str
    allowed_tools: tuple[str, ...]

    def intent(self) -> LaunchIntent:
        return LaunchIntent(
            agent_profile=self.agent_profile,
            session_name=self.session_name,
            message=self.message,
            allowed_tools=self.allowed_tools,
        )


class DurableLaunchGateway:
    """Resolve and admit through an injected runtime, without dispatching work."""

    def __init__(self, launch_runtime_provider: LaunchRuntimeProvider | None):
        self._launch_runtime_provider = launch_runtime_provider

    async def dispatch_registered_next(self) -> dict | None:
        """Dispatch one queued Work order through the composed server runtime.

        HTTP ingress calls :meth:`admit` only. The app lifespan may call this
        method from its internal dispatcher when a Work backend is registered.
        Injected admission-only providers cannot silently acquire dispatch power.
        """
        dispatcher = getattr(self._launch_runtime_provider, "dispatch_registered_next", None)
        if not callable(dispatcher):
            raise self._error(
                "launch_runtime_unavailable",
                "Trusted launch runtime is unavailable.",
                retryable=False,
                required_action="inspect_server_configuration",
            )
        result: dict | None = await dispatcher()
        return result

    async def prepare_registered_dispatch(self):
        """Prepare one order without holding the API dispatcher for its delivery."""
        prepare = getattr(self._launch_runtime_provider, "prepare_registered_dispatch", None)
        if not callable(prepare) or not all(
            callable(getattr(self._launch_runtime_provider, name, None))
            for name in ("deliver_registered_dispatch", "abandon_registered_dispatch")
        ):
            raise self._error(
                "launch_runtime_unavailable",
                "Trusted launch runtime is unavailable.",
                retryable=False,
                required_action="inspect_server_configuration",
            )
        return await prepare()

    async def deliver_registered_dispatch(self, prepared) -> dict:
        """Complete one prepared order through its exact composed runtime."""
        deliver = getattr(self._launch_runtime_provider, "deliver_registered_dispatch", None)
        if not callable(deliver):
            raise self._error(
                "launch_runtime_unavailable",
                "Trusted launch runtime is unavailable.",
                retryable=False,
                required_action="inspect_server_configuration",
            )
        result: dict = await deliver(prepared)
        return result

    async def abandon_registered_dispatch(self, prepared) -> None:
        """Close and reconcile a committed handoff that could not be delivered."""
        abandon = getattr(self._launch_runtime_provider, "abandon_registered_dispatch", None)
        if not callable(abandon):
            raise self._error(
                "launch_runtime_unavailable",
                "Trusted launch runtime is unavailable.",
                retryable=False,
                required_action="inspect_server_configuration",
            )
        await abandon(prepared)

    async def wait_for_registered_dispatch_cleanup(self) -> None:
        """Join abandoned preparation cleanup when the provider owns any."""
        drain = getattr(
            self._launch_runtime_provider,
            "wait_for_registered_dispatch_cleanup",
            None,
        )
        if callable(drain):
            await drain()

    @staticmethod
    def _error(
        code: str, message: str, *, retryable: bool, required_action: str
    ) -> DurableLaunchGatewayError:
        return DurableLaunchGatewayError(
            code, message, retryable=retryable, required_action=required_action
        )

    @classmethod
    def _runtime_error(cls, error: LaunchRuntimeError) -> DurableLaunchGatewayError:
        return cls._error(
            error.code,
            error.message,
            retryable=error.retryable,
            required_action=error.required_action,
        )

    @classmethod
    def _require_principal(cls, principal: Principal) -> None:
        try:
            WorkAuthority._principal(principal)
        except AuthorityDenied as error:
            raise cls._error(
                "launch_authority_denied",
                "Verified launch authority is required.",
                retryable=False,
                required_action="authenticate",
            ) from error

    def _runtime_for(self, principal: Principal, selection: str | None) -> LaunchRuntime:
        provider = self._launch_runtime_provider
        if not isinstance(provider, LaunchRuntimeProvider):
            raise self._error(
                "launch_context_unavailable",
                "Trusted launch context is unavailable.",
                retryable=False,
                required_action="provision_launch_context",
            )
        try:
            runtime = provider.runtime_for(principal, selection)
        except Exception as error:
            raise self._error(
                "launch_context_unavailable",
                "Trusted launch context is unavailable.",
                retryable=False,
                required_action="provision_launch_context",
            ) from error
        if not isinstance(runtime, LaunchRuntime):
            raise self._error(
                "launch_context_unavailable",
                "Trusted launch context is unavailable.",
                retryable=False,
                required_action="provision_launch_context",
            )
        return runtime

    def admit(self, principal: Principal, request: DurableLaunchRequest) -> LaunchReceipt:
        """Return only this operation's persisted admission receipt; never dispatch or ACK."""
        self._require_principal(principal)
        if not isinstance(request, DurableLaunchRequest):
            raise self._error(
                "launch_intent_invalid",
                "Launch intent is invalid.",
                retryable=False,
                required_action="correct_launch_intent",
            )
        try:
            intent = request.intent()
        except (TypeError, ValueError) as error:
            raise self._error(
                "launch_intent_invalid",
                "Launch intent is invalid.",
                retryable=False,
                required_action="correct_launch_intent",
            ) from error
        runtime = self._runtime_for(principal, request.selection)
        try:
            resolved = runtime.resolve_launch(principal, request.selection, intent)
            if (
                resolved.principal_id != principal.id
                or resolved.selection_auto_resolved != (request.selection is None)
                or (request.selection is not None and resolved.selection != request.selection)
            ):
                raise self._error(
                    "launch_context_unavailable",
                    "Trusted launch context is unavailable.",
                    retryable=False,
                    required_action="provision_launch_context",
                )
            receipt = runtime.admit_launch(principal, resolved)
        except DurableLaunchGatewayError:
            raise
        except LaunchRuntimeError as error:
            raise self._runtime_error(error) from error
        except Exception as error:
            raise self._error(
                "launch_runtime_unavailable",
                "Trusted launch runtime is unavailable.",
                retryable=False,
                required_action="inspect_server_configuration",
            ) from error
        if not isinstance(receipt, LaunchReceipt):
            raise self._error(
                "launch_runtime_unavailable",
                "Trusted launch runtime is unavailable.",
                retryable=False,
                required_action="inspect_server_configuration",
            )
        return receipt


def build_durable_launch_gateway(
    repository: WorkRepository,
    *,
    backends: Mapping[str, TerminalBackend],
    resource_admission=None,
    resource_clock=time.monotonic,
) -> DurableLaunchGateway:
    """Compose trusted root launches and private managed child deliveries."""
    if not isinstance(repository, WorkRepository):
        raise ValueError("verified work repository required")
    repository.verify_schema()
    # Process-managed child steps are acceptance-only and require the locally
    # configured Docker backend. Keep v2 out of default and non-Docker runtimes.
    from cli_agent_orchestrator.backends.docker_backend import DockerWorkBackend

    delivery_adapters = {
        ("launch", 2): process_launch_adapter(),
        # This is reachable only through WorkOrigins' server-sealed child
        # admission path; DurableLaunchGateway exposes no agent-step intake.
        ("agent_step", 1): agent_step_adapter(),
    }
    if os.environ.get("CAO_T020_ACCEPTANCE") == "1" and isinstance(
        backends.get("docker-local"), DockerWorkBackend
    ):
        delivery_adapters[("agent_step", 2)] = agent_step_process_adapter()
    runtime = LaunchRuntime(
        repository,
        backends=backends,
        delivery_adapters=delivery_adapters,
        resource_admission=resource_admission,
        resource_clock=resource_clock,
    )
    return DurableLaunchGateway(_ComposedLaunchRuntimeProvider(runtime))
