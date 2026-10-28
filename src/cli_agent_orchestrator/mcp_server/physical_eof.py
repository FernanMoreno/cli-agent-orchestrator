"""Opt-in physical stdio closure for the tested native MCP runtime.

Signals request bounded shutdown. Normal return requires settled, flushed RPCs
and either peer EOF or an explicitly enabled local input-resource closure.
Local closure never claims peer EOF. No synthetic bytes or extra IO worker.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import select
import signal
import socket
import stat
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager, suppress
from importlib.metadata import version
from typing import Any, NoReturn, TextIO, cast

from anyio import ClosedResourceError
from mcp.types import JSONRPCMessage

from cli_agent_orchestrator.utils.mcp_owner_stop import (
    MCP_STOP_ALLOWANCE_SECONDS,
    SIGNAL_STOP_ENV,
)

ENABLE_ENV = "CAO_INTERNAL_MCP_PHYSICAL_EOF"
SHUTDOWN_SECONDS = MCP_STOP_ALLOWANCE_SECONDS
SUPPORTED_RUNTIME = {"fastmcp": "3.2.0", "mcp": "1.28.1", "anyio": "4.15.1"}


class GuardError(RuntimeError):
    """Content-free transport refusal, never an authorization result."""


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise GuardError("ambiguous_frame")
        value[key] = item
    return value


def _constant(_: str) -> NoReturn:
    raise GuardError("invalid_frame")


class WireState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.in_tail = bytearray()
        self.out_tail = bytearray()
        self.pending_client: set[str | int] = set()
        self.pending_server: set[str | int] = set()
        self.unflushed_replies: set[str | int] = set()
        self.physical_eof = False
        self.local_input_closed = False
        self.allow_signal_close = False
        self.unsafe: str | None = None
        self.deadline: float | None = None
        self.first_signal: int | None = None
        self.signal_count = 0
        self.input_frames = 0
        self.output_frames = 0
        self.output_busy = 0
        self.wakers: set[int] = set()
        self.on_eof: Callable[[], None] | None = None

    def fail(self, code: str) -> None:
        with self.lock:
            self.unsafe = self.unsafe or code

    def begin_close(self) -> None:
        with self.lock:
            if self.deadline is None:
                self.deadline = time.monotonic() + SHUTDOWN_SECONDS
            self.wake()
            if self.on_eof is not None:
                self.on_eof()

    def request_stop(self, signum: int) -> None:
        with self.lock:
            self.signal_count += 1
            if self.first_signal is None:
                self.first_signal = int(signum)
            if self.deadline is None:
                self.deadline = time.monotonic() + SHUTDOWN_SECONDS
            self.wake()

    def wake(self) -> None:
        with self.lock:
            for fd in self.wakers:
                try:
                    os.write(fd, b"x")
                except BlockingIOError:
                    pass
                except OSError:
                    self.fail("wake_failed")

    def remaining(self) -> float | None:
        with self.lock:
            if self.deadline is None:
                return None
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                self.fail("shutdown_deadline")
                raise GuardError("shutdown_deadline")
            return remaining

    def _frame(self, raw: bytes, *, inbound: bool) -> None:
        try:
            frame = json.loads(
                raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant
            )
            if not isinstance(frame, dict):
                raise GuardError("invalid_frame")
            if "id" in frame and type(frame["id"]) not in (str, int):
                raise GuardError("invalid_rpc_id")
            JSONRPCMessage.model_validate(frame)
        except Exception:
            self.fail("invalid_frame")
            raise GuardError("invalid_frame") from None
        if "method" in frame:
            if "id" in frame:
                pending = self.pending_client if inbound else self.pending_server
                if frame["id"] in pending:
                    self.fail("duplicate_rpc_id")
                    raise GuardError("duplicate_rpc_id")
                pending.add(frame["id"])
            elif inbound and frame["method"] != "notifications/initialized":
                # The SDK does not expose a public notification completion hook.
                # Preserve execution but refuse to certify unobserved teardown.
                self.fail("unobserved_notification")
        else:
            identity = frame["id"]
            pending = self.pending_server if inbound else self.pending_client
            if identity not in pending or (not inbound and identity in self.unflushed_replies):
                self.fail("unbound_rpc_response")
                raise GuardError("unbound_rpc_response")
            if inbound:
                pending.remove(identity)
            else:
                self.unflushed_replies.add(identity)

    def _feed(self, data: bytes, *, inbound: bool) -> None:
        with self.lock:
            tail = self.in_tail if inbound else self.out_tail
            start = len(tail)
            tail.extend(data)
            while True:
                end = tail.find(b"\n", start)
                if end < 0:
                    return
                raw = bytes(tail[: end + 1])
                del tail[: end + 1]
                start = 0
                try:
                    self._frame(raw, inbound=inbound)
                except BaseException:
                    self.fail("frame_observer_failed")
                    self.begin_close()
                    raise
                if inbound:
                    self.input_frames += 1
                else:
                    self.output_frames += 1

    def feed_input(self, data: bytes) -> None:
        self._feed(data, inbound=True)

    def feed_output(self, data: bytes) -> None:
        self._feed(data, inbound=False)

    def input_eof(self) -> None:
        with self.lock:
            self.physical_eof = True
            if self.deadline is None:
                self.deadline = time.monotonic() + SHUTDOWN_SECONDS
            self.wake()
            if self.on_eof is not None:
                self.on_eof()
            if self.in_tail:
                self.fail("partial_input_frame")
                raise GuardError("partial_input_frame")

    def output_flushed(self) -> None:
        with self.lock:
            self.pending_client.difference_update(self.unflushed_replies)
            self.unflushed_replies.clear()
            if self.allow_signal_close and self.signal_count:
                self.wake()

    def quiescent(self) -> bool:
        with self.lock:
            return not (
                self.unsafe
                or self.in_tail
                or self.out_tail
                or self.pending_client
                or self.pending_server
                or self.unflushed_replies
                or self.output_busy
            )

    def mark_local_input_closed(self) -> None:
        with self.lock:
            if (
                not self.allow_signal_close
                or not self.signal_count
                or self.physical_eof
                or not self.quiescent()
            ):
                raise GuardError("unsafe_local_input_close")
            self.local_input_closed = True

    def verify_normal(self) -> None:
        with self.lock:
            self.remaining()
            if self.unsafe:
                raise GuardError(self.unsafe)
            if self.physical_eof and self.local_input_closed:
                raise GuardError("ambiguous_input_close")
            if not self.physical_eof and not (
                self.allow_signal_close and self.local_input_closed and self.signal_count
            ):
                raise GuardError("physical_eof_missing")
            if (
                self.in_tail
                or self.out_tail
                or self.pending_client
                or self.pending_server
                or self.unflushed_replies
                or self.output_busy
            ):
                raise GuardError("transport_unsettled")

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "physical_eof": self.physical_eof,
                "local_input_closed": self.local_input_closed,
                "pending_client": len(self.pending_client),
                "pending_server": len(self.pending_server),
                "input_tail_bytes": len(self.in_tail),
                "output_tail_bytes": len(self.out_tail),
                "unflushed_replies": len(self.unflushed_replies),
                "output_busy": self.output_busy,
                "input_frames": self.input_frames,
                "output_frames": self.output_frames,
                "first_signal": self.first_signal,
                "signal_count": self.signal_count,
                "unsafe": self.unsafe,
            }


class ObservedPipe(io.BufferedIOBase):
    """Own duplicates, preserve the original descriptors and interrupt IO waits."""

    def __init__(
        self, fd: int, *, inbound: bool, state: WireState, owner: io.IOBase | None = None
    ) -> None:
        super().__init__()
        self.fd = self.wake_read = self.wake_write = -1
        self.inbound, self.state, self.owner = inbound, state, owner
        self.was_blocking = True
        self.have_flags = self.ready_to_io = self.input_socket = False
        try:
            mode = os.fstat(fd).st_mode
            if not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
                raise GuardError("stdio_pipe_required")
            if inbound and state.allow_signal_close and (owner is None or owner.fileno() != fd):
                raise GuardError("owned_input_required")
            self.input_socket = stat.S_ISSOCK(mode)
            self.fd = os.dup(fd)
            self.was_blocking = os.get_blocking(self.fd)
            self.have_flags = True
            self.wake_read, self.wake_write = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
            os.set_blocking(self.fd, False)
            with state.lock:
                state.wakers.add(self.wake_write)
            self.ready_to_io = True
        except BaseException:
            self.close()
            raise

    def readable(self) -> bool:
        return self.inbound

    def writable(self) -> bool:
        return not self.inbound

    def seekable(self) -> bool:
        return False

    def fileno(self) -> int:
        return self.fd

    def isatty(self) -> bool:
        return os.isatty(self.fd)

    def _close_on_stop(self) -> bool:
        # Only this existing SDK reader owns the select/read/close sequence.
        # The peer can still write after the poll; stop is a local resource
        # close, never a guarantee that future peer writes cannot be discarded.
        with self.state.lock:
            if (
                not self.inbound
                or self.closed
                or not self.state.allow_signal_close
                or not self.state.signal_count
                or self.state.physical_eof
                or self.owner is None
                or not self.state.quiescent()
            ):
                return False
            if select.select([self.fd], [], [], 0)[0]:
                return False
            null_fd = -1
            try:
                if self.input_socket:
                    with socket.socket(fileno=os.dup(self.fd)) as endpoint:
                        endpoint.shutdown(socket.SHUT_RD)
                null_fd = os.open("/dev/null", os.O_WRONLY | os.O_CLOEXEC)
                # Release the original pipe endpoint atomically. Reserve its
                # number with a non-readable resource: no reuse race, no EOF.
                os.dup2(null_fd, self.owner.fileno(), inheritable=False)
                self.close()
                self.state.mark_local_input_closed()
                return True
            except BaseException:
                self.state.fail("input_close_failed")
                self.state.begin_close()
                raise
            finally:
                if null_fd >= 0:
                    os.close(null_fd)

    def _ready(self) -> None:
        while True:
            if self._close_on_stop():
                raise ClosedResourceError
            remaining = self.state.remaining()
            reads = [self.wake_read] + ([self.fd] if self.inbound else [])
            writes = [] if self.inbound else [self.fd]
            ready_read, ready_write, _ = select.select(reads, writes, [], remaining)
            if self.wake_read in ready_read:
                try:
                    while os.read(self.wake_read, 4096):
                        pass
                except BlockingIOError:
                    pass
                continue
            self.state.remaining()
            if self.fd in ready_read or self.fd in ready_write:
                return

    def read1(self, size: int = -1) -> bytes:
        if not self.inbound or self.closed:
            raise GuardError("input_unavailable")
        if size == 0:
            return b""
        while True:
            self._ready()
            try:
                data = os.read(self.fd, size if size > 0 else 65536)
                break
            except BlockingIOError:
                continue
            except OSError:
                self.state.fail("input_io_failed")
                self.state.begin_close()
                raise GuardError("input_io_failed") from None
        if data:
            self.state.feed_input(data)
        else:
            self.state.input_eof()
        return data

    def read(self, size: int | None = -1) -> bytes:
        size = -1 if size is None else size
        if size >= 0:
            return self.read1(size)
        parts = []
        while data := self.read1():
            parts.append(data)
        return b"".join(parts)

    def readinto(self, target: Any) -> int:
        data = self.read1(len(target))
        target[: len(data)] = data
        return len(data)

    def write(self, data: Any) -> int:
        if self.inbound or self.closed:
            raise GuardError("output_unavailable")
        data = bytes(data)
        with self.state.lock:
            self.state.output_busy += 1
        try:
            self.state.feed_output(data)
            view = memoryview(data)
            while view:
                self._ready()
                try:
                    written = os.write(self.fd, view)
                except BlockingIOError:
                    continue
                if written <= 0:
                    raise GuardError("output_write_incomplete")
                view = view[written:]
            return len(data)
        except BaseException:
            self.state.fail("output_io_failed")
            self.state.begin_close()
            raise
        finally:
            with self.state.lock:
                self.state.output_busy -= 1

    def flush(self) -> None:
        if not self.closed and not self.inbound and self.ready_to_io:
            self.state.remaining()
            # This raw pipe has no userspace output buffer. Every write above
            # completed against the kernel before the SDK's flush reaches here.
            self.state.output_flushed()

    def close(self) -> None:
        if self.closed:
            return
        try:
            super().close()
        finally:
            self.ready_to_io = False
            with self.state.lock:
                if self.wake_write >= 0:
                    self.state.wakers.discard(self.wake_write)
                    os.close(self.wake_write)
                    self.wake_write = -1
            if self.fd >= 0:
                try:
                    if self.have_flags:
                        os.set_blocking(self.fd, self.was_blocking)
                finally:
                    os.close(self.fd)
                    self.fd = -1
            if self.wake_read >= 0:
                os.close(self.wake_read)
                self.wake_read = -1


class _TextView:
    encoding = "utf-8"
    errors = "strict"

    def __init__(self, original: TextIO, buffer: ObservedPipe) -> None:
        self.original, self.buffer = original, buffer

    def write(self, value: str) -> int:
        self.buffer.write(value.encode("utf-8"))
        return len(value)

    def flush(self) -> None:
        return self.buffer.flush()

    def read(self, *_: Any) -> NoReturn:
        self.buffer.state.fail("direct_text_input")
        raise GuardError("direct_text_input")

    readline = read

    def __getattr__(self, name: str) -> Any:
        return getattr(self.original, name)


class PhysicalEofGuard:
    def __init__(self, *, allow_signal_close: bool = False) -> None:
        if sys.platform != "linux" or any(version(k) != v for k, v in SUPPORTED_RUNTIME.items()):
            raise GuardError("unsupported_stdio_runtime")
        self.state = WireState()
        self.state.allow_signal_close = allow_signal_close
        self.started = False
        self.run_active = False
        self.timer_armed = False
        self.exit_pending = False

    def _arm_process_deadline(self) -> None:
        if self.run_active and not self.timer_armed:
            try:
                remaining = self.state.remaining()
            except GuardError:
                os._exit(70)
            if remaining is not None:
                signal.setitimer(signal.ITIMER_REAL, remaining)
                self.timer_armed = True

    def run(self, function: Callable[[], Any]) -> Any:
        """Keep the deadline alive through SDK executor and loop teardown."""
        if (
            self.run_active
            or self.started
            or threading.current_thread() is not threading.main_thread()
        ):
            raise GuardError("invalid_guard_lifetime")
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        if any(previous_timer):
            raise GuardError("existing_process_timer")
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_stops = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

        def expired(
            _signum: int, _frame: Any, terminate: Callable[[int], NoReturn] = os._exit
        ) -> NoReturn:
            terminate(70)

        signal.signal(signal.SIGALRM, expired)
        self.run_active = True
        normal = False
        try:
            result = function()
            self.state.verify_normal()
            normal = True
            return result
        finally:
            self.exit_pending = normal and self.state.allow_signal_close
            if not self.exit_pending:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous_handler)
                for signum, handler in previous_stops.items():
                    signal.signal(signum, handler)
            # Local-stop mode is a single owned server PROCESS lifetime, not
            # reusable in-process IO. Keep its original bounded timer and stop
            # handlers through normal interpreter/atexit cleanup; no exit(0)
            # shortcut and no reset of the first six-second deadline.
            self.run_active = False

    def _closing_stop(self, signum: int, _frame: Any) -> None:
        self.state.request_stop(signum)

    @contextmanager
    def streams(self) -> Iterator[None]:
        originals = sys.stdin, sys.stdout
        reader = writer = None
        try:
            originals[1].flush()
            incoming, outgoing = os.fstat(originals[0].fileno()), os.fstat(originals[1].fileno())
            if (
                self.state.allow_signal_close
                and stat.S_ISSOCK(incoming.st_mode)
                and (incoming.st_dev, incoming.st_ino) == (outgoing.st_dev, outgoing.st_ino)
            ):
                raise GuardError("shared_stdio_socket_unsupported")
            reader = ObservedPipe(
                originals[0].fileno(),
                inbound=True,
                state=self.state,
                owner=cast(io.IOBase, originals[0]) if self.state.allow_signal_close else None,
            )
            writer = ObservedPipe(originals[1].fileno(), inbound=False, state=self.state)
            sys.stdin, sys.stdout = cast(TextIO, _TextView(originals[0], reader)), cast(
                TextIO, _TextView(originals[1], writer)
            )
            yield
        finally:
            sys.stdin, sys.stdout = originals
            try:
                if writer is not None:
                    writer.close()
            finally:
                if reader is not None:
                    reader.close()

    @asynccontextmanager
    async def lifespan(self, _server: object) -> AsyncIterator[dict[str, Any]]:
        if (
            not self.run_active
            or self.started
            or threading.current_thread() is not threading.main_thread()
        ):
            raise GuardError("invalid_guard_lifetime")
        self.started = True
        loop = asyncio.get_running_loop()
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        stopped = asyncio.Event()
        installed = []

        def stop(signum: int) -> None:
            self.state.request_stop(signum)
            self._arm_process_deadline()
            stopped.set()

        async def watchdog() -> None:
            await stopped.wait()
            try:
                await asyncio.sleep(max(0, self.state.remaining() or 0))
            except GuardError:
                pass
            self.state.fail("shutdown_deadline")
            self.state.wake()
            # FastMCP can shield an active handler during transport teardown.
            # At the fixed deadline, fail this owned process without returning
            # from mcp.run or allowing its normal-completion observer to seal.
            os._exit(70)

        def eof_on_loop() -> None:
            self._arm_process_deadline()
            stopped.set()

        def eof_wake() -> None:
            loop.call_soon_threadsafe(eof_on_loop)

        try:
            self.state.on_eof = eof_wake
            for signum in previous:
                loop.add_signal_handler(signum, stop, signum)
                installed.append(signum)
            with self.streams():
                alarm = asyncio.create_task(watchdog())
                try:
                    yield {}
                    self.state.verify_normal()
                finally:
                    self.state.begin_close()
                    self._arm_process_deadline()
                    alarm.cancel()
                    with suppress(asyncio.CancelledError):
                        await alarm
                self.state.verify_normal()
        finally:
            self.state.on_eof = None
            for signum in installed:
                loop.remove_signal_handler(signum)
                if self.state.allow_signal_close and (
                    self.state.local_input_closed or self.state.physical_eof
                ):
                    signal.signal(signum, self._closing_stop)
                else:
                    signal.signal(signum, previous[signum])


def guard_from_environment(environment: Mapping[str, str]) -> PhysicalEofGuard | None:
    physical = environment.get(ENABLE_ENV)
    local = environment.get(SIGNAL_STOP_ENV)
    if physical is None and local is None:
        return None
    if physical is not None and local is not None:
        raise GuardError("guard_mode_conflict")
    value = physical if physical is not None else local
    if value != "1":
        raise GuardError("guard_activation_invalid")
    return PhysicalEofGuard(allow_signal_close=local is not None)
