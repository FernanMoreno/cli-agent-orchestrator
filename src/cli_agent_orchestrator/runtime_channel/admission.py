"""Bridge participant in the shared process resource controller."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from cli_agent_orchestrator.services.delivery_admission import DeliveryAdmission

if TYPE_CHECKING:
    from cli_agent_orchestrator.runtime_channel.bridge import Bridge


class BridgeAdmissionParticipant:
    def __init__(self, bridge: Bridge):
        self.bridge = bridge

    def bind_admission(
        self,
        admission: DeliveryAdmission | None,
        *,
        clock: Callable[[], float],
        retirement: Callable[[], bool] | None,
    ) -> None:
        bridge = self.bridge
        if bridge._resource_retirement is not None and not bridge._resource_retirement():
            raise RuntimeError("previous admission runtime must retire before replacement")
        if (
            bridge._tasks
            or bridge._closing_tasks
            or bridge._ws is not None
            or bridge._pending_ws is not None
            or not bridge._reader_retired.is_set()
        ):
            raise RuntimeError("bridge reader and handlers must retire before replacement")
        previous = bridge._resource_admission
        if previous is not None and previous.snapshot(now=bridge._resource_clock()).inflight:
            raise RuntimeError("previous delivery leases must retire before replacement")
        if admission is None:
            raise ValueError("bridge resource participant requires an admission owner")
        bridge._resource_admission = admission
        bridge._resource_clock = clock
        bridge._resource_retirement = retirement

    def start_accepting(self) -> None:
        owner = self.bridge._resource_admission
        if owner is None:
            raise RuntimeError("bridge admission is not bound")
        owner.start_accepting()

    def stop_accepting(self) -> None:
        owner = self.bridge._resource_admission
        if owner is not None:
            owner.stop_accepting()
        self.bridge.stop()

    def notify_admission_changed(self, terminal_id: str | None = None) -> None:
        # Bridge has no local delivery queue: every new command checks this
        # same owner; a denied delivery remains pending at the central Inbox.
        pass

    async def drain(self, *, timeout_s: float) -> None:
        # Native operations retain their own deadlines. A shutdown timeout is
        # not evidence that an admitted physical worker has retired.
        await self.bridge.drain()
