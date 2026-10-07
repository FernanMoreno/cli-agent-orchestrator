"""Bound libtmux's shared subprocess boundary, including neo listing calls.

libtmux 0.51 has no timeout hook: listings bypass Server.cmd and factories use
that same shared tmux_cmd class. Adapt its constructor once in this process;
keep the original argument assembly, result shape and object model intact.
"""

from __future__ import annotations

import shutil
import subprocess
from subprocess import TimeoutExpired
from typing import TYPE_CHECKING, Any, Literal, Sequence, overload

if TYPE_CHECKING:
    from libtmux._internal.query_list import QueryList
    from libtmux.session import Session

import libtmux
from libtmux import common, exc

COMMAND_TIMEOUT_SECONDS = 5.0


class TmuxCommandTimeout(RuntimeError):
    """The transport outcome is unknown; never infer absence or retry a paste."""


@overload
def run_tmux(
    argv: Sequence[str], *, text: Literal[True], **kwargs: Any
) -> subprocess.CompletedProcess[str]: ...


@overload
def run_tmux(
    argv: Sequence[str], *, text: Literal[False] = False, **kwargs: Any
) -> subprocess.CompletedProcess[bytes]: ...


def run_tmux(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """Bound a CLI invocation without exposing a task payload in timeout errors."""
    try:
        return subprocess.run(argv, timeout=COMMAND_TIMEOUT_SECONDS, **kwargs)
    except TimeoutExpired:
        operation = next((str(a) for a in argv[1:] if not str(a).startswith("-")), "command")
        raise TmuxCommandTimeout(
            f"tmux {operation} did not respond within {COMMAND_TIMEOUT_SECONDS:g}s; "
            "the operation outcome is unknown"
        ) from None


def _bounded_command_init(self, *args):
    binary = shutil.which("tmux")
    if not binary:
        raise exc.TmuxCommandNotFound
    self.cmd = [binary, *(str(arg) for arg in args)]
    result = run_tmux(self.cmd, capture_output=True, text=True, errors="backslashreplace")
    self.process = result
    self.returncode = result.returncode
    self.stdout = result.stdout.split("\n")
    while self.stdout and self.stdout[-1] == "":
        self.stdout.pop()
    self.stderr = [line for line in result.stderr.split("\n") if line]
    if "has-session" in self.cmd and self.stderr and not self.stdout:
        self.stdout = [self.stderr[0]]


def install_bounded_libtmux_transport():
    # Imported references in common/server/neo all point at this one class.
    # Replacing that constructor is idempotent and requires no global lock or
    # per-request patch/unpatch, which would race other terminals' commands.
    common.tmux_cmd.__init__ = _bounded_command_init


class BoundedTmuxServer(libtmux.Server):
    def __init__(self, *args, **kwargs):
        install_bounded_libtmux_transport()
        super().__init__(*args, **kwargs)

    @property
    def sessions(self) -> QueryList[Session]:
        # Upstream Server.sessions swallows EVERY exception and returns an
        # empty list. A timeout is not evidence that the session is absent.
        from libtmux._internal.query_list import QueryList
        from libtmux.neo import fetch_objs
        from libtmux.session import Session

        try:
            rows = fetch_objs(server=self, list_cmd="list-sessions")
        except exc.LibTmuxException as error:
            # Empty servers genuinely have no sessions; retain that behavior,
            # but never swallow a timeout or an unrelated transport failure.
            detail = str(error)
            if "no server running on" in detail or "no sessions" in detail:
                return QueryList([])
            raise
        return QueryList([Session(server=self, **row) for row in rows])
