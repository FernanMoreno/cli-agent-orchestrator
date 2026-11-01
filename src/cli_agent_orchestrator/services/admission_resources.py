"""Bounded Linux resource observations; no placement or authorization effects."""

import os
import re
import stat
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Literal

from cli_agent_orchestrator.services.admission_policy import (
    CapacityObservation,
    Coverage,
    ResourceHeadroom,
    validate_clock,
)

ResourceIssue = Literal[
    "unsupported_cgroup",
    "invalid_cgroup_path",
    "invalid_resource_data",
    "resource_unavailable",
    "cpu_warming",
    "hidden_ancestors",
    "storage_unknown",
    "future_storage",
    "unverified_hierarchy",
]


@dataclass(frozen=True)
class StorageObservation:
    """Verified post-reserve units, using the same process-local clock.

    None means visibility is insufficient, never an unrestricted budget.
    """

    observed_at: float
    available_slots: int | None

    def __post_init__(self) -> None:
        validate_clock(self.observed_at)
        value = self.available_slots
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ValueError("storage slots must be nonnegative integer units or unknown")


@dataclass(frozen=True)
class ResourceSample:
    observation: CapacityObservation
    issues: tuple[ResourceIssue, ...]


class _ReadingError(ValueError):
    def __init__(self, issue: ResourceIssue):
        self.issue = issue


@dataclass(frozen=True)
class _Directory:
    path: Path
    descriptor: int
    mount_id: int


@dataclass(frozen=True)
class _Group:
    identity: tuple[int, int]
    cpu_limit: int | None
    usage_usec: int
    memory_limit: int | None
    memory_available: int | None


@dataclass(frozen=True)
class _Reading:
    at: float
    memory_limit: int
    memory_available: int
    cpu_limit: int
    host_cpu: tuple[tuple[int, int, int], ...]
    groups: tuple[_Group, ...]
    hidden_ancestors: bool
    memory_domains: tuple[ResourceHeadroom, ...]


def _unsigned(token: str) -> int:
    if not token.isascii() or not token.isdecimal():
        raise ValueError("invalid unsigned resource counter")
    return int(token)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="surrogateescape")


def _finite_limit(token: str) -> int | None:
    if token.strip() == "max":
        return None
    value = _unsigned(token.strip())
    if value == 0:
        raise ValueError("effective resource limit must be positive")
    return value


def _affinity() -> frozenset[int]:
    if not hasattr(os, "sched_getaffinity"):
        raise OSError("effective CPU affinity unavailable")
    return frozenset(os.sched_getaffinity(0))


def _mount_path(token: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), token)


class LinuxResourceSampler:
    """Read visible cgroup v2 ancestors and host headroom, never host-only fallbacks.

    CPU availability requires two comparable samples. Hidden ancestors, missing
    metrics and unsupported layouts remain explicit incomplete coverage. No raw
    filesystem paths, process identifiers or exception messages leave this class.
    """

    def __init__(
        self,
        *,
        proc_root: Path = Path("/proc"),
        affinity: Callable[[], frozenset[int]] = _affinity,
        clock: Callable[[], float] = time.monotonic,
        complete_hierarchy: bool = False,
        root_is_host: bool = False,
        mount_of: Callable[[int], int] | None = None,
    ):
        if not isinstance(complete_hierarchy, bool) or not isinstance(root_is_host, bool):
            raise ValueError("hierarchy attestations must be explicit booleans")
        if root_is_host and not complete_hierarchy:
            raise ValueError("host root requires a trusted complete-hierarchy attestation")
        if mount_of is not None and not callable(mount_of):
            raise ValueError("mount descriptor reader must be callable")
        self._proc = proc_root
        self._mount_of = mount_of if mount_of is not None else self._descriptor_mount
        self._affinity = affinity
        self._clock = clock
        self._previous: _Reading | None = None
        # These are trusted bootstrap assertions, not inferred from mount paths
        # or accepted from ordinary requests. Namespace views can hide parents.
        self._complete_hierarchy = complete_hierarchy
        self._root_is_host = root_is_host

    def reset_history(self) -> None:
        """Called by the single sampling owner after a gap or binding change."""
        self._previous = None

    def _descriptor_mount(self, descriptor: int) -> int:
        rows = [
            line.split()
            for line in _text(self._proc / "self/fdinfo" / str(descriptor)).splitlines()
            if line.partition(":")[0] == "mnt_id"
        ]
        if len(rows) != 1 or len(rows[0]) != 2 or rows[0][0] != "mnt_id:":
            raise _ReadingError("invalid_cgroup_path")
        value = _unsigned(rows[0][1])
        if not value:
            raise _ReadingError("invalid_cgroup_path")
        return value

    def _on_mount(self, descriptor: int, mount_id: int) -> bool:
        actual = self._mount_of(descriptor)
        return type(actual) is int and actual > 0 and actual == mount_id

    @contextmanager
    def _groups(self) -> Iterator[tuple[tuple[_Directory, ...], bool]]:
        # A pathname in mountinfo may be covered by ANY other mount, including
        # a hidden prefix or an inner/file bind mount. The kernel's descriptor
        # mount ID proves resolution; do not infer visibility from line order.
        membership = _text(self._proc / "self/cgroup")
        mountinfo = _text(self._proc / "self/mountinfo")
        unified = []
        for line in membership.splitlines():
            fields = line.split(":", 2)
            if len(fields) == 3 and fields[:2] == ["0", ""]:
                unified.append(fields[2])
        if len(unified) != 1:
            raise _ReadingError("unsupported_cgroup")
        member = PurePosixPath(unified[0])
        if not member.is_absolute() or ".." in member.parts:
            raise _ReadingError("invalid_cgroup_path")
        mounts: list[tuple[PurePosixPath, Path, int]] = []
        ids = set()
        for line in mountinfo.splitlines():
            left, separator, right = line.partition(" - ")
            if not separator or not right.startswith("cgroup2 "):
                continue
            fields = left.split()
            if len(fields) < 6:
                raise ValueError("invalid controller mount")
            mount_id = _unsigned(fields[0])
            if not mount_id or mount_id in ids:
                raise ValueError("invalid controller mount identity")
            ids.add(mount_id)
            root = PurePosixPath(_mount_path(fields[3]))
            mount = Path(_mount_path(fields[4]))
            if (
                not root.is_absolute()
                or not mount.is_absolute()
                or ".." in root.parts
                or ".." in mount.parts
            ):
                raise _ReadingError("invalid_cgroup_path")
            if member.is_relative_to(root):
                mounts.append((root, mount, mount_id))
        # Prefer the widest proven view, not the deepest bind alias. Keep the
        # verified directory handles through controller reads, closing each
        # rejected candidate before trying another. Work grows with ancestry,
        # not cgroup process count; cgroup.procs is never scanned.
        for root, mount, mount_id in sorted(mounts, key=lambda item: len(item[0].parts)):
            stack = ExitStack()
            directories = []
            current = mount.joinpath(*member.relative_to(root).parts)
            try:
                while True:
                    descriptor = os.open(
                        current, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
                    )
                    stack.callback(os.close, descriptor)
                    if not self._on_mount(descriptor, mount_id):
                        raise _ReadingError("invalid_cgroup_path")
                    directories.append(_Directory(current, descriptor, mount_id))
                    if current == mount:
                        break
                    current = current.parent
            except (OSError, ValueError):
                stack.close()
                continue
            except BaseException:
                stack.close()
                raise
            try:
                yield tuple(directories), root != PurePosixPath("/")
                if (
                    _text(self._proc / "self/cgroup") != membership
                    or _text(self._proc / "self/mountinfo") != mountinfo
                ):
                    raise _ReadingError("invalid_cgroup_path")
            finally:
                stack.close()
            return
        raise _ReadingError("unsupported_cgroup")

    def _text_at(self, directory: _Directory, name: str) -> str:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory.descriptor,
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode) or not self._on_mount(
                descriptor, directory.mount_id
            ):
                raise _ReadingError("invalid_cgroup_path")
            # Controller records are small. An unexpected source cannot make a
            # sample read a FIFO or allocate without bound.
            with os.fdopen(
                descriptor, encoding="utf-8", errors="surrogateescape", closefd=False
            ) as stream:
                text = stream.read(65537)
            if len(text) > 65536:
                raise ValueError("controller record exceeds observation bound")
            return text
        finally:
            os.close(descriptor)

    def _group(self, directory: _Directory, *, host_root: bool = False) -> _Group:
        info = os.fstat(directory.descriptor)
        if host_root:
            # The actual hierarchy root has no non-root controller limits.
            # Its finite resource bounds come from host memory and affinity.
            return _Group((info.st_dev, info.st_ino), None, 0, None, None)
        memory_current = _unsigned(self._text_at(directory, "memory.current").strip())
        limits = [
            value
            for name in ("memory.max", "memory.high")
            if (value := _finite_limit(self._text_at(directory, name))) is not None
        ]
        memory_limit = min(limits) if limits else None
        quota = self._text_at(directory, "cpu.max").split()
        if len(quota) != 2:
            raise ValueError("invalid CPU quota")
        period = _unsigned(quota[1])
        if not period:
            raise ValueError("CPU period must be positive")
        cpu_limit = None if quota[0] == "max" else _unsigned(quota[0]) * 1000 // period
        if cpu_limit == 0:
            raise ValueError("CPU quota is below observable precision")
        usage = [
            fields[1]
            for line in self._text_at(directory, "cpu.stat").splitlines()
            if len(fields := line.split()) == 2 and fields[0] == "usage_usec"
        ]
        if len(usage) != 1:
            raise ValueError("CPU usage counter unavailable")
        return _Group(
            (info.st_dev, info.st_ino),
            cpu_limit,
            _unsigned(usage[0]),
            memory_limit,
            max(0, memory_limit - memory_current) if memory_limit is not None else None,
        )

    def _read(self, at: float) -> _Reading:
        memory = {}
        for line in _text(self._proc / "meminfo").splitlines():
            fields = line.split()
            if fields and fields[0] in ("MemTotal:", "MemAvailable:"):
                if len(fields) != 3 or fields[2] != "kB" or fields[0] in memory:
                    raise ValueError("invalid host memory observation")
                memory[fields[0]] = _unsigned(fields[1]) * 1024
        if set(memory) != {"MemTotal:", "MemAvailable:"}:
            raise ValueError("host memory coverage unavailable")
        if not memory["MemTotal:"] or memory["MemAvailable:"] > memory["MemTotal:"]:
            raise ValueError("invalid host memory limits")
        affinity = self._affinity()
        if not affinity or any(
            isinstance(cpu, bool) or not isinstance(cpu, int) or cpu < 0 for cpu in affinity
        ):
            raise ValueError("effective CPU affinity unavailable")
        cpu_rows = {}
        for line in _text(self._proc / "stat").splitlines():
            fields = line.split()
            if not fields or not re.fullmatch(r"cpu[0-9]+", fields[0]):
                continue
            cpu = int(fields[0][3:])
            if cpu not in affinity:
                continue
            if len(fields) < 9 or cpu in cpu_rows:
                raise ValueError("invalid host CPU counters")
            # guest fields are already included in user/nice; do not count twice.
            values = [_unsigned(value) for value in fields[1:9]]
            total = sum(values)
            cpu_rows[cpu] = (total, total - values[3] - values[4])
        if set(cpu_rows) != set(affinity):
            raise ValueError("host CPU coverage unavailable")
        with self._groups() as (directories, hidden):
            groups = tuple(
                self._group(
                    directory,
                    host_root=self._root_is_host and not hidden and index == len(directories) - 1,
                )
                for index, directory in enumerate(directories)
            )
        memory_domains = (ResourceHeadroom(memory["MemAvailable:"], memory["MemTotal:"]),) + tuple(
            ResourceHeadroom(group.memory_available, group.memory_limit)
            for group in groups
            if group.memory_limit is not None and group.memory_available is not None
        )
        return _Reading(
            at,
            min([memory["MemTotal:"]] + [g.memory_limit for g in groups if g.memory_limit]),
            min(
                [memory["MemAvailable:"]]
                + [g.memory_available for g in groups if g.memory_available is not None]
            ),
            min([len(affinity) * 1000] + [g.cpu_limit for g in groups if g.cpu_limit]),
            tuple((cpu, *cpu_rows[cpu]) for cpu in sorted(affinity)),
            groups,
            hidden,
            memory_domains,
        )

    @staticmethod
    def _cpu_domains(
        current: _Reading, previous: _Reading | None
    ) -> tuple[ResourceHeadroom, ...] | None:
        if previous is None or current.at <= previous.at:
            return None
        if tuple(g.identity for g in current.groups) != tuple(g.identity for g in previous.groups):
            return None
        if tuple(row[0] for row in current.host_cpu) != tuple(row[0] for row in previous.host_cpu):
            return None
        host_free = 0
        for (_, total, active), (_, old_total, old_active) in zip(
            current.host_cpu, previous.host_cpu
        ):
            delta_total, delta_active = total - old_total, active - old_active
            if delta_total <= 0 or not 0 <= delta_active <= delta_total:
                return None
            host_free += (delta_total - delta_active) * 1000 // delta_total
        elapsed_usec = int((current.at - previous.at) * 1_000_000)
        if elapsed_usec <= 0:
            return None
        domains = [ResourceHeadroom(host_free, len(current.host_cpu) * 1000)]
        for group, old in zip(current.groups, previous.groups):
            delta = group.usage_usec - old.usage_usec
            if delta < 0:
                return None
            if group.cpu_limit is not None:
                used = (delta * 1000 + elapsed_usec - 1) // elapsed_usec
                domains.append(ResourceHeadroom(max(0, group.cpu_limit - used), group.cpu_limit))
        return tuple(domains)

    def sample(self, *, storage: StorageObservation) -> ResourceSample:
        at = self._clock()
        validate_clock(at)
        issues: list[ResourceIssue] = []
        slots = storage.available_slots
        if storage.observed_at > at:
            issues.append("future_storage")
            slots = None
        elif slots is None:
            issues.append("storage_unknown")
        observed_at = min(at, storage.observed_at)
        try:
            reading = self._read(at)
        except (_ReadingError, OSError, ValueError) as error:
            self._previous = None
            issue: ResourceIssue = (
                error.issue
                if isinstance(error, _ReadingError)
                else (
                    "resource_unavailable"
                    if isinstance(error, OSError)
                    else "invalid_resource_data"
                )
            )
            return ResourceSample(
                CapacityObservation(observed_at, None, None, None, None, slots, "unknown"),
                (issue, *issues),
            )
        cpu_domains = self._cpu_domains(reading, self._previous)
        if cpu_domains and self._previous is not None:
            observed_at = min(observed_at, self._previous.at)
        cpu = min(domain.available_units for domain in cpu_domains) if cpu_domains else None
        self._previous = reading
        if cpu is None:
            issues.append("cpu_warming")
        if reading.hidden_ancestors:
            issues.append("hidden_ancestors")
        if not self._complete_hierarchy:
            issues.append("unverified_hierarchy")
        coverage: Coverage = "partial" if issues else "known"
        return ResourceSample(
            CapacityObservation(
                observed_at,
                reading.memory_available,
                reading.memory_limit,
                cpu,
                reading.cpu_limit,
                slots,
                coverage,
                memory_domains=reading.memory_domains,
                cpu_domains=cpu_domains or (),
            ),
            tuple(issues),
        )
