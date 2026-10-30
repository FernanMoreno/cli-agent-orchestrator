"""Owned Linux process observations for the real mock_cli capacity ramp."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class Process:
    pid: int
    birth: str
    uid: int
    state: str
    ppid: int
    rss_bytes: int

    @property
    def identity(self):
        return self.pid, self.birth, self.uid

    @property
    def alive(self):
        return self.state not in {"Z", "X", "x"}


def process(pid: int) -> Process | None:
    root = Path("/proc") / str(pid)
    try:
        fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
        uid = root.stat().st_uid
        if uid != os.geteuid():
            raise RuntimeError("process_uid_changed")
        return Process(
            pid,
            fields[19],
            uid,
            fields[0],
            int(fields[1]),
            int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
        )
    except FileNotFoundError:
        return None


def same_live(expected: Process, read: Callable = process) -> bool:
    current = read(expected.pid)
    return current is not None and current.identity == expected.identity and current.alive


def tree(root: Process) -> dict[int, Process]:
    if not same_live(root):
        return {}
    pending = [root.pid]
    result = {}
    while pending:
        pid = pending.pop()
        item = process(pid)
        if item is None or not item.alive:
            continue
        result[pid] = item
        try:
            pending.extend(
                int(x)
                for x in (Path("/proc") / str(pid) / "task" / str(pid) / "children")
                .read_text()
                .split()
            )
        except FileNotFoundError:
            continue
    if not same_live(root):
        return {}
    return result


def command(pid: int) -> tuple[str, ...]:
    try:
        return tuple(
            x.decode()
            for x in (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
            if x
        )
    except FileNotFoundError:
        return ()


def owned_api(home: Path, port: int) -> Process:
    me = process(os.getpid())
    assert me is not None
    matches = []
    for item in tree(me).values():
        argv = command(item.pid)
        if "cli_agent_orchestrator.api.main" not in argv or "--port" not in argv:
            continue
        if argv[argv.index("--port") + 1] != str(port):
            continue
        if Path("/proc", str(item.pid), "cwd").readlink() == home and same_live(item):
            matches.append(item)
    assert len(matches) == 1, "expected exactly one owned API process"
    return matches[0]


def mock_process(pane: Process, binary: Path) -> Process:
    matches = [p for p in tree(pane).values() if str(binary) in command(p.pid)]
    assert len(matches) == 1 and same_live(matches[0]), "mock process identity not established"
    return matches[0]


def sleeping_child(mock: Process, seconds: int) -> Process | None:
    matches = [p for p in tree(mock).values() if command(p.pid) == ("sleep", str(seconds))]
    assert len(matches) <= 1, "ambiguous owned processing witness"
    return matches[0] if matches else None


def confirmed_overlap(samples: dict[str, Process], read: Callable = process) -> int:
    """Each pinned lifetime brackets this call's entry; distinct workers required."""
    if len({p.identity for p in samples.values()}) != len(samples):
        raise AssertionError("one process cannot witness multiple workers")
    return sum(same_live(p, read) for p in samples.values())


def available_mib() -> int:
    value = next(
        line.split()[1]
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    return int(value) // 1024


def admit_rung(workers: int, previous: dict | None, memory_mib: int) -> None:
    assert workers in (2, 4, 8, 12, 20, 32)
    assert memory_mib >= 6144, "insufficient available memory for next capacity rung"
    if workers != 2:
        predecessor = {4: 2, 8: 4, 12: 8, 20: 12, 32: 20}[workers]
        assert previous is not None and previous.get("workers") == predecessor
        assert previous.get("passed") is True and previous.get("cleanup_confirmed") is True


class Sampler:
    """Sample pinned API/pane trees; summed RSS includes shared pages, not PG."""

    def __init__(self, api: Process):
        self.roots = [api]
        self.peak_rss_bytes = 0
        self.peak_processes = 0
        self.min_available_mib = available_mib()
        self.samples = 0
        self.errors = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                observed = {}
                for root in tuple(self.roots):
                    observed.update(tree(root))
                self.peak_rss_bytes = max(
                    self.peak_rss_bytes, sum(p.rss_bytes for p in observed.values())
                )
                self.peak_processes = max(self.peak_processes, len(observed))
                self.min_available_mib = min(self.min_available_mib, available_mib())
                self.samples += 1
            except BaseException as error:
                self.errors.append(type(error).__name__)
                return
            self.stop_event.wait(0.1)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=5)
        assert (
            not self.thread.is_alive() and not self.errors and self.samples
        ), "process sampling incomplete"
        return {
            "sample_count": self.samples,
            "peak_aggregate_rss_bytes": self.peak_rss_bytes,
            "peak_owned_processes": self.peak_processes,
            "min_available_mib": self.min_available_mib,
            "scope": "sampled API and pane descendants; summed RSS double counts shared pages; PostgreSQL excluded",
        }


def evidence_process(item: Process) -> dict:
    return asdict(item)
