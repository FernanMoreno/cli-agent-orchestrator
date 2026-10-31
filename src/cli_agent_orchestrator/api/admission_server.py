"""Embedded loopback-only resource control, without the central API lifespan."""

from __future__ import annotations

import asyncio
import socket
from contextlib import contextmanager
from typing import Iterator

import uvicorn
from fastapi import FastAPI

from cli_agent_orchestrator.api.admission_routes import router
from cli_agent_orchestrator.services.inbox_admission_runtime import (
    AdmissionRuntimeUnavailable,
    InboxAdmissionRuntime,
)
from cli_agent_orchestrator.services.work_blocking import run_owned_async


class _LoopbackServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, ready: asyncio.Future[None]):
        super().__init__(config)
        self.servers: list[asyncio.Server] = []
        self.ready = ready
        self.shutdown_complete = False

    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        # Bridge owns SIGINT/SIGTERM, including during HTTP startup/shutdown.
        yield

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        if not self.ready.done():
            if self.started and not self.should_exit:
                self.ready.set_result(None)
            else:
                self.ready.set_exception(
                    AdmissionRuntimeUnavailable("resource control stopped at startup")
                )

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        await super().shutdown(sockets)
        self.shutdown_complete = True


class AdmissionControlServer:
    """Owns HTTP only. Its caller owns runtime start, admission close and stop.

    Always call stop in finally, even if start fails or its awaiter is cancelled.
    The strong server task and listener remain owned until stop has joined them.
    """

    def __init__(self, runtime: InboxAdmissionRuntime):
        self.runtime = runtime
        self.app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self.app.state.inbox_admission_runtime = runtime
        self.app.include_router(router)
        self._server: _LoopbackServer | None = None
        self._socket: socket.socket | None = None
        self._task: asyncio.Task[None] | None = None
        self._used = False
        self._closing = False

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    @property
    def url(self) -> str | None:
        if (
            self._closing
            or self._server is None
            or not self._server.started
            or self._task is None
            or self._task.done()
            or self._socket is None
        ):
            return None
        return f"http://127.0.0.1:{self._socket.getsockname()[1]}"

    async def start(self) -> None:
        if self._used or self._closing or not self.runtime.available:
            raise AdmissionRuntimeUnavailable("resource control cannot start")
        self._used = True
        ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        ready.add_done_callback(lambda f: None if f.cancelled() else f.exception())
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket = listener
        listener.bind(("127.0.0.1", 0))
        listener.listen(socket.SOMAXCONN)
        listener.setblocking(False)
        server = _LoopbackServer(
            uvicorn.Config(self.app, access_log=False, log_level="warning", lifespan="off"), ready
        )
        self._server = server

        async def serve() -> None:
            # An eager task factory must hand back ownership before listening.
            await asyncio.sleep(0)
            await server.serve(sockets=[listener])

        operation = serve()
        try:
            self._task = asyncio.create_task(operation)
        except BaseException:
            operation.close()
            raise

        def completed(task: asyncio.Task[None]) -> None:
            error = None if task.cancelled() else task.exception()
            if not self._closing:
                # Close input before supervision wakes after an unexpected exit.
                self.runtime.close_admission()
            if not ready.done():
                ready.set_exception(
                    error or AdmissionRuntimeUnavailable("resource control stopped at startup")
                )

        self._task.add_done_callback(completed)
        await asyncio.shield(ready)

    async def stop(self) -> None:
        self._closing = True
        if self._server is not None:
            self._server.should_exit = True

        async def close() -> None:
            try:
                if self._task is not None:
                    try:
                        await self._task
                    except BaseException:
                        # start/supervision owns the error. Cleanup must still
                        # close late startup listeners and outstanding requests.
                        pass
                server = self._server
                if (
                    server is not None
                    and hasattr(server, "lifespan")
                    and not server.shutdown_complete
                ):
                    await server.shutdown(
                        sockets=[self._socket] if self._socket is not None else None
                    )
            finally:
                if self._server is not None:
                    for listener in getattr(self._server, "servers", ()):
                        listener.close()
                        await listener.wait_closed()
                if self._socket is not None:
                    self._socket.close()

        await run_owned_async(close())
