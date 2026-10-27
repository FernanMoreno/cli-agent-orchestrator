"""Bounded OS witness for a native shell child, independent of model prose."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import select
import shlex
import sys
import time
from pathlib import Path

from native_paired_protocol import HarnessError

# Longer than the unchanged 75s journey budget: natural expiry cannot prove cancel.
HOLD_SECONDS = 120
HOLD_CODE = "import time; time.sleep(120)"


class ActivityError(HarnessError):
    pass


@dataclasses.dataclass(frozen=True)
class Identity:
    pid: int
    uid: int
    birth: int


@dataclasses.dataclass(frozen=True)
class Witness:
    child: Identity
    anchor: Identity
    chain: tuple[Identity, ...]
    expires: float
    pidfd: int = dataclasses.field(compare=False)


def process_identity(pid: int) -> Identity | None:
    process = Path("/proc") / str(pid)
    try:
        fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return Identity(pid, process.stat().st_uid, int(fields[19]))
    except (FileNotFoundError, ProcessLookupError):
        return None


def command(nonce: str) -> list[str]:
    if re.fullmatch(r"[a-f0-9]{32}", nonce) is None:
        raise ActivityError("invalid_probe_nonce")
    return [sys.executable, "-I", "-c", HOLD_CODE, nonce]


def shell_command(nonce: str) -> str:
    return "exec " + shlex.join(command(nonce))


def _read(pid: int, name: str) -> bytes:
    with (Path("/proc") / str(pid) / name).open("rb") as stream:
        data = stream.read(131073)
    if len(data) > 131072:
        raise ActivityError("process_observation_too_large")
    return data


def _arguments(pid: int) -> list[str]:
    raw = _read(pid, "cmdline")
    if not raw.endswith(b"\0"):
        raise ActivityError("process_arguments_invalid")
    return [p.decode("utf8") for p in raw[:-1].split(b"\0")]


def capture(
    *, candidates, nonce, terminal_id, socket_path, anchor, anchor_executable, anchor_options
) -> Witness | None:
    """Only exact command matches are candidates; unreadable owned state fails."""
    expected = command(nonce)
    matches = []
    for candidate in candidates:
        if process_identity(candidate.pid) != candidate:
            continue
        try:
            arguments = _arguments(candidate.pid)
        except (FileNotFoundError, ProcessLookupError):
            continue
        if arguments != expected:
            continue
        if matches:
            raise ActivityError("probe_not_unique")
        matches.append(candidate)
    if not matches:
        return None
    child = matches[0]
    if child.uid != os.getuid() or anchor.uid != os.getuid() or child.birth < anchor.birth:
        raise ActivityError("probe_identity_unowned")
    if (Path("/proc") / str(child.pid) / "exe").resolve(strict=True) != Path(
        sys.executable
    ).resolve():
        raise ActivityError("probe_executable_mismatch")
    environment = _read(child.pid, "environ").split(b"\0")
    if environment.count(b"CAO_TERMINAL_ID=" + terminal_id.encode()) != 1:
        raise ActivityError("probe_terminal_mismatch")
    tmux_values = [p for p in environment if p.startswith(b"TMUX=")]
    if len(tmux_values) != 1 or tmux_values[0].split(b",", 1)[0] != b"TMUX=" + os.fsencode(
        socket_path
    ):
        raise ActivityError("probe_socket_mismatch")
    chain = []
    current = child
    for _ in range(32):
        if current is None or current.uid != anchor.uid or current.birth < anchor.birth:
            raise ActivityError("probe_ancestry_mismatch")
        chain.append(current)
        if current == anchor:
            break
        fields = _read(current.pid, "stat").rsplit(b")", 1)[1].split()
        current = process_identity(int(fields[1]))
    else:
        raise ActivityError("probe_ancestry_too_deep")
    if (
        current != anchor
        or (Path("/proc") / str(anchor.pid) / "exe").resolve(strict=True) != anchor_executable
    ):
        raise ActivityError("probe_anchor_mismatch")
    args = _arguments(anchor.pid)
    for option, value in anchor_options.items():
        if args.count(option) != 1 or args[-1] == option or args[args.index(option) + 1] != value:
            raise ActivityError("probe_anchor_arguments_mismatch")
    if any(process_identity(i.pid) != i for i in chain) or _arguments(child.pid) != expected:
        raise ActivityError("probe_changed_during_read")
    expires = child.birth / os.sysconf("SC_CLK_TCK") + HOLD_SECONDS
    # /proc starttime uses the boot clock, including suspended time.
    if time.clock_gettime(time.CLOCK_BOOTTIME) >= expires:
        raise ActivityError("probe_natural_budget_expired")
    fd = os.pidfd_open(child.pid)
    witness = Witness(child, anchor, tuple(chain), expires, fd)
    if process_identity(child.pid) != child or exited(witness):
        os.close(fd)
        raise ActivityError("probe_exited_during_capture")
    return witness


def exited(witness: Witness) -> bool:
    return bool(select.select([witness.pidfd], [], [], 0)[0])


def require_stopped(witness: Witness, *, deadline: float) -> None:
    remaining = min(
        deadline - time.monotonic(), witness.expires - time.clock_gettime(time.CLOCK_BOOTTIME)
    )
    if remaining <= 0 or not select.select([witness.pidfd], [], [], remaining)[0]:
        raise ActivityError("probe_stop_not_proven_before_deadline")
    if (
        time.clock_gettime(time.CLOCK_BOOTTIME) >= witness.expires
        or process_identity(witness.child.pid) == witness.child
    ):
        raise ActivityError("probe_stop_not_proven_before_natural_expiry")


def require_worker_stopped(witness: Witness, *, deadline: float) -> None:
    while process_identity(witness.anchor.pid) == witness.anchor:
        remaining = min(
            deadline - time.monotonic(), witness.expires - time.clock_gettime(time.CLOCK_BOOTTIME)
        )
        if remaining <= 0:
            raise ActivityError("probe_worker_stop_not_proven_before_deadline")
        time.sleep(min(0.01, remaining))
    if time.monotonic() >= deadline or time.clock_gettime(time.CLOCK_BOOTTIME) >= witness.expires:
        raise ActivityError("probe_worker_stop_not_proven_before_deadline")


def projection(witness: Witness) -> dict:
    return {
        "schema": "caos.native-tool-activity.v1",
        "child": dataclasses.asdict(witness.child),
        "anchor": dataclasses.asdict(witness.anchor),
        "ancestry": [dataclasses.asdict(i) for i in witness.chain],
        "command_sha256": hashlib.sha256(HOLD_CODE.encode()).hexdigest(),
        "hold_seconds": HOLD_SECONDS,
    }
