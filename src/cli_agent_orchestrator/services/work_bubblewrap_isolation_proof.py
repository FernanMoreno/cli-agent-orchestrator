"""Attempt-bound evidence for a live Bubblewrap worker and runtime snapshot."""

from __future__ import annotations

import os
import select
import stat
from dataclasses import dataclass, field
from pathlib import Path

from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.services.work_bubblewrap_runtime_snapshot import RuntimeSnapshot

_PROOF_SEAL = object()


class WorkBubblewrapIsolationExpired(RuntimeError):
    """The process, snapshot, or durable attempt binding is no longer current."""


@dataclass(frozen=True, slots=True)
class WorkBubblewrapRuntimeIsolationProof:
    attempt_id: str
    generation: int
    attempt_revision: int
    contract_hash: str
    snapshot_digest: str
    process_identity_sha256: str
    worker_socket_identity: tuple[int, int] | None
    monitor_pid: int
    monitor_start_time_ticks: int
    init_pid: int
    init_start_time_ticks: int
    pid_namespace_identity: tuple[int, int]
    user_namespace_inode: int
    _repository: WorkRepository = field(repr=False, compare=False)
    _snapshot: RuntimeSnapshot = field(repr=False, compare=False)
    _monitor_pidfd: int = field(repr=False, compare=False)
    _init_pidfd: int = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)

    def require_current(self, attempt_id: str, generation: int, contract_hash: str) -> None:
        if (
            self._seal is not _PROOF_SEAL
            or type(attempt_id) is not str
            or (attempt_id, generation, contract_hash)
            != (self.attempt_id, self.generation, self.contract_hash)
            or type(generation) is not int
            or type(contract_hash) is not str
        ):
            raise WorkBubblewrapIsolationExpired("isolation proof binding differs")
        try:
            self._snapshot.validate()
            if self._snapshot.digest != self.snapshot_digest:
                raise WorkBubblewrapIsolationExpired("runtime snapshot digest changed")
            self._require_pidfd(self._monitor_pidfd, self.monitor_pid)
            self._require_pidfd(self._init_pidfd, self.init_pid)
            identity = self._repository.read_bubblewrap_process_identity(
                self.attempt_id, self.generation
            )
            if (
                identity is None
                or identity.get("identity_sha256") != self.process_identity_sha256
                or identity.get("monitor_pid") != self.monitor_pid
                or identity.get("monitor_start_time_ticks") != self.monitor_start_time_ticks
                or identity.get("init_pid") != self.init_pid
                or identity.get("init_start_time_ticks") != self.init_start_time_ticks
                or tuple(identity.get("pid_namespace", ())) != self.pid_namespace_identity
                or _process_start_time_ticks(self.monitor_pid) != self.monitor_start_time_ticks
                or _process_start_time_ticks(self.init_pid) != self.init_start_time_ticks
                or _namespace_identity(self.init_pid, "pid") != self.pid_namespace_identity
                or _namespace_inode(self.init_pid, "user") != self.user_namespace_inode
                or _require_yama_ptrace_scope() != 1
            ):
                raise WorkBubblewrapIsolationExpired("Bubblewrap process identity changed")
        except WorkBubblewrapIsolationExpired:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkBubblewrapIsolationExpired("isolation evidence is unavailable") from exc

    def close(self) -> None:
        for descriptor in (self._monitor_pidfd, self._init_pidfd):
            try:
                os.close(descriptor)
            except OSError:
                pass

    @staticmethod
    def _require_pidfd(descriptor: int, expected_pid: int) -> None:
        info = Path(f"/proc/self/fdinfo/{descriptor}").read_text(encoding="ascii")
        values = [
            line.split(":", 1)[1].strip() for line in info.splitlines() if line.startswith("Pid:")
        ]
        if len(values) != 1 or int(values[0]) != expected_pid:
            raise WorkBubblewrapIsolationExpired("pinned Bubblewrap process identity changed")
        poller = select.poll()
        poller.register(descriptor, select.POLLIN | select.POLLHUP | select.POLLERR)
        if poller.poll(0):
            raise WorkBubblewrapIsolationExpired("Bubblewrap process has exited")


def _issue_runtime_isolation_proof(
    *,
    repository: WorkRepository,
    snapshot: RuntimeSnapshot,
    attempt_id: str,
    generation: int,
    attempt_revision: int,
    contract_hash: str,
    process_identity: dict[str, object],
    monitor_pidfd: int,
    init_pidfd: int,
    worker_socket_fd: int | None,
) -> WorkBubblewrapRuntimeIsolationProof:
    """Issue proof only from the acknowledged, persisted launch boundary."""
    snapshot.validate()
    if _require_yama_ptrace_scope() != 1:
        raise WorkBubblewrapIsolationExpired("Yama must restrict ptrace to process descendants")
    monitor_pid = process_identity.get("monitor_pid")
    init_pid = process_identity.get("init_pid")
    monitor_start_time_ticks = process_identity.get("monitor_start_time_ticks")
    init_start_time_ticks = process_identity.get("init_start_time_ticks")
    pid_namespace = process_identity.get("pid_namespace")
    identity_digest = process_identity.get("identity_sha256")
    if (
        type(monitor_pid) is not int
        or type(init_pid) is not int
        or type(monitor_start_time_ticks) is not int
        or monitor_start_time_ticks <= 0
        or type(init_start_time_ticks) is not int
        or init_start_time_ticks <= 0
        or type(pid_namespace) not in {list, tuple}
        or len(pid_namespace) != 2
        or any(type(part) is not int or part <= 0 for part in pid_namespace)
        or type(identity_digest) is not str
        or process_identity.get("kind") != "bubblewrap"
        or process_identity.get("init_parent_pid") != monitor_pid
    ):
        raise WorkBubblewrapIsolationExpired("Bubblewrap process identity is invalid")
    pid_namespace_identity = (pid_namespace[0], pid_namespace[1])
    _require_live_pidfd(monitor_pidfd, monitor_pid)
    _require_live_pidfd(init_pidfd, init_pid)
    if (
        _process_start_time_ticks(monitor_pid) != monitor_start_time_ticks
        or _process_start_time_ticks(init_pid) != init_start_time_ticks
        or _namespace_identity(init_pid, "pid") != pid_namespace_identity
    ):
        raise WorkBubblewrapIsolationExpired("Bubblewrap starttime or PID namespace changed")
    user_inode = _namespace_inode(init_pid, "user")
    parent_user_inode = _namespace_inode(os.getpid(), "user")
    if user_inode == parent_user_inode:
        raise WorkBubblewrapIsolationExpired("attempt did not enter a distinct user namespace")
    socket_identity = None
    if worker_socket_fd is not None:
        socket_stat = os.fstat(worker_socket_fd)
        if not stat.S_ISSOCK(socket_stat.st_mode):
            raise WorkBubblewrapIsolationExpired("worker endpoint is not a socket")
        socket_identity = (socket_stat.st_dev, socket_stat.st_ino)
    monitor_copy = os.dup(monitor_pidfd)
    try:
        init_copy = os.dup(init_pidfd)
    except BaseException:
        os.close(monitor_copy)
        raise
    return WorkBubblewrapRuntimeIsolationProof(
        attempt_id=attempt_id,
        generation=generation,
        attempt_revision=attempt_revision,
        contract_hash=contract_hash,
        snapshot_digest=snapshot.digest,
        process_identity_sha256=identity_digest,
        worker_socket_identity=socket_identity,
        monitor_pid=monitor_pid,
        monitor_start_time_ticks=monitor_start_time_ticks,
        init_pid=init_pid,
        init_start_time_ticks=init_start_time_ticks,
        pid_namespace_identity=pid_namespace_identity,
        user_namespace_inode=user_inode,
        _repository=repository,
        _snapshot=snapshot,
        _monitor_pidfd=monitor_copy,
        _init_pidfd=init_copy,
        _seal=_PROOF_SEAL,
    )


def _namespace_inode(pid: int, namespace: str) -> int:
    return os.stat(f"/proc/{pid}/ns/{namespace}").st_ino


def _namespace_identity(pid: int, namespace: str) -> tuple[int, int]:
    item = os.stat(f"/proc/{pid}/ns/{namespace}")
    return item.st_dev, item.st_ino


def _process_start_time_ticks(pid: int) -> int:
    stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    close = stat_text.rfind(")")
    if close < 0:
        raise ValueError("process stat record has no command terminator")
    fields = stat_text[close + 2 :].split()
    if len(fields) <= 19:
        raise ValueError("process stat record omits start time")
    return int(fields[19])


def _require_yama_ptrace_scope() -> int:
    try:
        value = int(Path("/proc/sys/kernel/yama/ptrace_scope").read_text(encoding="ascii").strip())
    except (OSError, ValueError) as exc:
        raise WorkBubblewrapIsolationExpired("Yama ptrace policy is unavailable") from exc
    if value != 1:
        raise WorkBubblewrapIsolationExpired("Yama must restrict ptrace to process descendants")
    return value


def _require_live_pidfd(descriptor: int, expected_pid: int) -> None:
    WorkBubblewrapRuntimeIsolationProof._require_pidfd(descriptor, expected_pid)
