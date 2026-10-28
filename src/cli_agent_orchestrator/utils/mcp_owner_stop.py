"""Private, exact-identity lifecycle records for opt-in CAO MCP children.

This module deliberately does not import the MCP server. The server publishes
its record before ``run()`` and may publish a separate normal-return receipt
after the guarded SDK call returns. The terminal owner uses these records to
request SIGINT only when the persisted launch configuration and live kernel
identity still agree.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import select
import signal
import stat
import sys
import time
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from cli_agent_orchestrator.constants import CAO_HOME_DIR
from cli_agent_orchestrator.utils.mcp_readiness import READY_FILE_ENV, READY_TOKEN_ENV
from cli_agent_orchestrator.utils.mcp_resolution import is_cao_mcp_server

SIGNAL_STOP_ENV = "CAO_INTERNAL_MCP_SIGNAL_STOP"
PHYSICAL_EOF_ENV = "CAO_INTERNAL_MCP_PHYSICAL_EOF"
OWNER_DIR_ENV = "CAO_MCP_OWNER_DIR"
MCP_STOP_ALLOWANCE_SECONDS = 6.0

_NONCE = re.compile(r"^[a-f0-9]{32}$")
_RECORD_NAME = re.compile(r"^(?P<pid>[1-9][0-9]*)\.(?P<nonce>[a-f0-9]{32})\.json$")
_NORMAL_NAME = re.compile(r"^[1-9][0-9]*\.[a-f0-9]{32}\.normal\.json$")
_MAX_METADATA_BYTES = 1024 * 1024
_MAX_PROC_ENV_BYTES = 1024 * 1024
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_DIR_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC
_PIDFD_EXIT_EVENTS = getattr(select, "POLLIN", 0) | getattr(select, "POLLHUP", 0)
_PIDFD_ERROR_EVENTS = getattr(select, "POLLERR", 0) | getattr(select, "POLLNVAL", 0)

_RECORD_FIELDS = frozenset(
    {
        "version",
        "terminal_id",
        "pid",
        "uid",
        "start_ticks",
        "boot_id",
        "pid_namespace",
        "ready_token",
        "ready_file",
        "owner_dir",
        "cmdline_sha256",
        "nonce",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "version",
        "terminal_id",
        "pid",
        "start_ticks",
        "boot_id",
        "nonce",
        "origin",
        "normal_return",
        "pending_client",
        "pending_server",
        "input_tail_bytes",
        "output_tail_bytes",
        "unflushed_replies",
        "output_busy",
    }
)
_RELEVANT_ENV = (
    "CAO_TERMINAL_ID",
    READY_FILE_ENV,
    READY_TOKEN_ENV,
    OWNER_DIR_ENV,
    SIGNAL_STOP_ENV,
    PHYSICAL_EOF_ENV,
)
_REQUIRED_ENV = frozenset(name for name in _RELEVANT_ENV if name != PHYSICAL_EOF_ENV)


class PrestopOutcome(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    UNSUPPORTED = "unsupported"
    RETIRED_NORMAL = "retired_normal"
    RETIRED_UNSEALED = "retired_unsealed"
    STILL_LIVE = "still_live"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class OwnedMcpRegistration:
    """Opaque reference to this process's private owner record."""

    terminal_id: str
    pid: int
    nonce: str
    owner_dir: Path = field(repr=False)


@dataclass(frozen=True, slots=True)
class PrestopResult:
    """Content-free aggregate; intentionally contains no process or path data."""

    outcome: PrestopOutcome
    identity_count: int
    signaled_count: int
    retired_count: int
    normal_count: int
    unknown_count: int
    still_live_count: int
    elapsed_ms: int


@dataclass(frozen=True, slots=True)
class _ProcIdentity:
    pid: int
    uid: int
    start_ticks: int
    boot_id: str
    pid_namespace: tuple[int, int]
    cmdline_sha256: str
    exe_path: str = field(repr=False)
    argv: tuple[bytes, ...] = field(repr=False)
    relevant_env: Mapping[bytes, bytes] = field(repr=False)
    zombie: bool = False


@dataclass(frozen=True, slots=True)
class _OwnerRecord:
    terminal_id: str
    pid: int
    uid: int
    start_ticks: int
    boot_id: str
    pid_namespace: tuple[int, int]
    ready_token: str = field(repr=False)
    ready_file: str = field(repr=False)
    owner_dir: str = field(repr=False)
    cmdline_sha256: str = field(repr=False)
    nonce: str
    name: str
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class _ConfigEntry:
    argv: tuple[str, ...] = field(repr=False)
    env: Mapping[str, str | None] = field(repr=False)
    document_sha256: str


@dataclass(slots=True)
class _PinnedOwner:
    record: _OwnerRecord
    fd: int
    config: _ConfigEntry
    home: Path | None = field(repr=False)
    status: str = "ready"
    signaled: bool = False


@dataclass(slots=True)
class _Counts:
    identity_count: int = 0
    signaled_count: int = 0
    retired_count: int = 0
    normal_count: int = 0
    unknown_count: int = 0
    still_live_count: int = 0
    unsupported: bool = False
    unsupported_count: int = 0


def _valid_terminal_id(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= 244
    except UnicodeEncodeError:
        return False


def _home_path(home: Path | None = None) -> Path:
    return Path(home if home is not None else CAO_HOME_DIR).expanduser().resolve()


def _registry_path(terminal_id: str, home: Path | None = None) -> Path:
    return _home_path(home) / "tmp" / f"{terminal_id}.mcp-owners"


def _record_name(pid: int, nonce: str) -> str:
    return f"{pid}.{nonce}.json"


def _receipt_name(record: _OwnerRecord | OwnedMcpRegistration) -> str:
    return f"{record.pid}.{record.nonce}.normal.json"


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate metadata key")
        value[key] = item
    return value


def _read_bounded(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining:
        chunk = os.read(fd, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > limit:
        raise ValueError("metadata size exceeded")
    return data


def _open_dir(path: Path) -> int:
    return os.open(path, _DIR_FLAGS)


def _check_private_dir(fd: int, *, created: bool = False) -> None:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PermissionError("owner directory rejected")
    if created:
        os.fchmod(fd, 0o700)
        info = os.fstat(fd)
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise PermissionError("owner directory permissions rejected")


def _open_registry(
    terminal_id: str, home: Path | None = None, *, create: bool = False
) -> tuple[int, Path]:
    return _open_registry_path(_registry_path(terminal_id, home), terminal_id, create=create)


def _validated_registry_path(path: Path, terminal_id: str) -> Path:
    path = Path(path)
    if (
        not path.is_absolute()
        or path.name != f"{terminal_id}.mcp-owners"
        or path.parent.name != "tmp"
        or ".." in path.parts
        or str(path) != os.path.normpath(str(path))
    ):
        raise ValueError("owner directory path rejected")
    home = path.parent.parent
    if home.resolve(strict=True) != home:
        raise ValueError("owner directory root is not canonical")
    return path


def _open_tmp_root(path: Path) -> int:
    home_fd = _open_dir(path.parent.parent)
    try:
        home_info = os.fstat(home_fd)
        if (
            not stat.S_ISDIR(home_info.st_mode)
            or home_info.st_uid != os.getuid()
            or stat.S_IMODE(home_info.st_mode) != 0o700
        ):
            raise PermissionError("owner home rejected")
        tmp_fd = os.open("tmp", _DIR_FLAGS, dir_fd=home_fd)
    finally:
        os.close(home_fd)
    try:
        tmp_info = os.fstat(tmp_fd)
        if (
            not stat.S_ISDIR(tmp_info.st_mode)
            or tmp_info.st_uid != os.getuid()
            or stat.S_IMODE(tmp_info.st_mode) & 0o022
        ):
            raise PermissionError("owner temporary root rejected")
    except BaseException:
        os.close(tmp_fd)
        raise
    return tmp_fd


def _open_registry_path(path: Path, terminal_id: str, *, create: bool = False) -> tuple[int, Path]:
    path = _validated_registry_path(path, terminal_id)
    parent_fd = _open_tmp_root(path)
    created = False
    try:
        if create:
            try:
                os.mkdir(path.name, mode=0o700, dir_fd=parent_fd)
                created = True
            except FileExistsError:
                pass
        fd = os.open(path.name, _DIR_FLAGS, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    try:
        _check_private_dir(fd, created=created)
    except BaseException:
        os.close(fd)
        raise
    return fd, path


def _write_exclusive_at(dir_fd: int, name: str, payload: bytes) -> bool:
    nonce = secrets.token_hex(12)
    temporary = f".tmp-{os.getpid()}-{nonce}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC
    fd = -1
    linked = False
    try:
        fd = os.open(temporary, flags, 0o600, dir_fd=dir_fd)
        os.fchmod(fd, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError(errno.EIO, "short metadata write")
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        # link() is an atomic no-replace publication. The final name never
        # exposes a partial file and an existing record/receipt is not replaced.
        os.link(
            temporary,
            name,
            src_dir_fd=dir_fd,
            dst_dir_fd=dir_fd,
            follow_symlinks=False,
        )
        linked = True
        os.unlink(temporary, dir_fd=dir_fd)
        os.fsync(dir_fd)
        return True
    except (OSError, ValueError):
        if linked:
            # Publication already happened. It is still a complete immutable
            # file, so leave it for the owner to inspect on the next pass.
            return False
        return False
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(temporary, dir_fd=dir_fd)
        except OSError:
            pass


def _boot_id() -> str:
    value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    if not value or len(value) > 128:
        raise ValueError("boot identity unavailable")
    return value


def _pid_namespace(pid: int) -> tuple[int, int]:
    info = os.stat(f"/proc/{pid}/ns/pid")
    return info.st_dev, info.st_ino


def _parse_proc_stat(raw: bytes, expected_pid: int) -> tuple[int, bool]:
    close = raw.rfind(b")")
    if close < 0:
        raise ValueError("process stat malformed")
    prefix = raw[: raw.find(b"(")].strip()
    if prefix != str(expected_pid).encode("ascii"):
        raise ValueError("process identity changed")
    fields = raw[close + 1 :].split()
    # The first item after the parenthesized command is field 3; starttime is 22.
    if len(fields) <= 19 or len(fields[0]) != 1:
        raise ValueError("process stat incomplete")
    return int(fields[19]), fields[0] == b"Z"


def _read_proc_status_uid(pid: int) -> int:
    fd = os.open(f"/proc/{pid}/status", _READ_FLAGS)
    try:
        raw = _read_bounded(fd, 256 * 1024)
    finally:
        os.close(fd)
    for line in raw.splitlines():
        if line.startswith(b"Uid:"):
            fields = line.split()[1:]
            if len(fields) != 4:
                break
            uids = [int(item) for item in fields]
            if len(set(uids)) != 1:
                raise PermissionError("process UID changed")
            return uids[0]
    raise ValueError("process UID unavailable")


def _read_relevant_environment(pid: int) -> Mapping[bytes, bytes]:
    fd = os.open(f"/proc/{pid}/environ", _READ_FLAGS)
    try:
        raw = _read_bounded(fd, _MAX_PROC_ENV_BYTES)
    finally:
        os.close(fd)
    wanted = {os.fsencode(name) for name in _RELEVANT_ENV}
    found: dict[bytes, bytes] = {}
    seen: set[bytes] = set()
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        name, value = item.split(b"=", 1)
        if name not in wanted:
            continue
        if name in seen:
            raise ValueError("ambiguous process environment")
        seen.add(name)
        found[name] = value
    if not {os.fsencode(name) for name in _REQUIRED_ENV}.issubset(seen):
        raise ValueError("required process environment unavailable")
    return found


def _capture_proc(pid: int) -> _ProcIdentity:
    root = Path("/proc") / str(pid)
    stat_fd = os.open(root / "stat", _READ_FLAGS)
    try:
        raw_stat = _read_bounded(stat_fd, 64 * 1024)
    finally:
        os.close(stat_fd)
    start_ticks, zombie = _parse_proc_stat(raw_stat, pid)
    uid = _read_proc_status_uid(pid)
    ns = _pid_namespace(pid)
    cmd_fd = os.open(root / "cmdline", _READ_FLAGS)
    try:
        cmdline = _read_bounded(cmd_fd, _MAX_METADATA_BYTES)
    finally:
        os.close(cmd_fd)
    if not cmdline:
        raise ValueError("process command unavailable")
    argv = tuple(part for part in cmdline.split(b"\0") if part)
    if not argv:
        raise ValueError("process command malformed")
    env = _read_relevant_environment(pid)
    exe_path = os.readlink(root / "exe")
    return _ProcIdentity(
        pid=pid,
        uid=uid,
        start_ticks=start_ticks,
        boot_id=_boot_id(),
        pid_namespace=ns,
        cmdline_sha256=hashlib.sha256(cmdline).hexdigest(),
        exe_path=exe_path,
        argv=argv,
        relevant_env=env,
        zombie=zombie,
    )


def _record_from_current_environment() -> tuple[dict[str, Any], Path] | None:
    env = os.environ
    if env.get(SIGNAL_STOP_ENV) != "1" or PHYSICAL_EOF_ENV in env:
        return None
    terminal_id = env.get("CAO_TERMINAL_ID")
    ready_file = env.get(READY_FILE_ENV)
    ready_token = env.get(READY_TOKEN_ENV)
    owner_dir = env.get(OWNER_DIR_ENV)
    if (
        not _valid_terminal_id(terminal_id)
        or not isinstance(ready_file, str)
        or not ready_file
        or "\x00" in ready_file
        or not isinstance(ready_token, str)
        or not ready_token
        or "\x00" in ready_token
        or not isinstance(owner_dir, str)
        or "\x00" in owner_dir
    ):
        return None
    assert isinstance(terminal_id, str)
    try:
        registry_path = _validated_registry_path(Path(owner_dir), terminal_id)
    except (OSError, ValueError):
        return None
    if owner_dir != str(registry_path):
        return None
    pid = os.getpid()
    uid = os.getuid()
    if pid <= 1 or os.geteuid() != uid:
        return None
    proc = _capture_proc(pid)
    if proc.uid != uid or proc.zombie:
        return None
    if any(
        proc.relevant_env.get(os.fsencode(name)) != os.fsencode(env.get(name, ""))
        for name in _RELEVANT_ENV
        if name != PHYSICAL_EOF_ENV
    ):
        return None
    if proc.relevant_env.get(os.fsencode(PHYSICAL_EOF_ENV)) is not None:
        return None
    record: dict[str, Any] = {
        "version": 1,
        "terminal_id": terminal_id,
        "pid": pid,
        "uid": uid,
        "start_ticks": proc.start_ticks,
        "boot_id": proc.boot_id,
        "pid_namespace": {"device": proc.pid_namespace[0], "inode": proc.pid_namespace[1]},
        "ready_token": ready_token,
        "ready_file": ready_file,
        "owner_dir": owner_dir,
        "cmdline_sha256": proc.cmdline_sha256,
        "nonce": secrets.token_hex(16),
    }
    return record, registry_path


def register_owned_mcp() -> OwnedMcpRegistration | None:
    """Publish this opt-in MCP process's immutable private owner record.

    This function never blocks MCP startup. Unsupported runtimes, malformed
    opt-in values, or filesystem/process-inspection failures return ``None``.
    """

    if not sys.platform.startswith("linux") or not _O_DIRECTORY or not _O_NOFOLLOW:
        return None
    try:
        captured = _record_from_current_environment()
        if captured is None:
            return None
        record, registry_path = captured
        registry_fd, _ = _open_registry_path(registry_path, str(record["terminal_id"]), create=True)
        try:
            name = _record_name(record["pid"], record["nonce"])
            payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if not _write_exclusive_at(registry_fd, name, payload):
                return None
        finally:
            os.close(registry_fd)
        return OwnedMcpRegistration(
            terminal_id=record["terminal_id"],
            pid=record["pid"],
            nonce=record["nonce"],
            owner_dir=registry_path,
        )
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
        return None


def _required_int(snapshot: Mapping[str, Any], name: str) -> int:
    value = snapshot.get(name)
    if type(value) is not int or value < 0:
        raise ValueError("invalid shutdown snapshot")
    return value


def _settled_snapshot(snapshot: object) -> tuple[str, dict[str, int]] | None:
    if (
        not isinstance(snapshot, Mapping)
        or "unsafe" not in snapshot
        or snapshot["unsafe"] is not None
    ):
        return None
    try:
        counts = {
            name: _required_int(snapshot, name)
            for name in (
                "pending_client",
                "pending_server",
                "input_tail_bytes",
                "output_tail_bytes",
                "unflushed_replies",
                "output_busy",
            )
        }
    except (TypeError, ValueError):
        return None
    if any(counts.values()):
        return None
    physical = snapshot.get("physical_eof")
    local = snapshot.get("local_input_closed")
    if physical is True and local is False:
        return "peer_eof", counts
    if physical is False and local is True:
        signal_count = snapshot.get("signal_count")
        if type(signal_count) is int and signal_count > 0:
            return "local_stop_after_signal", counts
    return None


def _parse_record(
    raw: bytes, name: str, terminal_id: str, registry_path: Path, info
) -> _OwnerRecord:
    parsed_name = _RECORD_NAME.fullmatch(name)
    if parsed_name is None:
        raise ValueError("record filename malformed")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    if not isinstance(value, dict) or set(value) != _RECORD_FIELDS:
        raise ValueError("record fields malformed")
    if value["version"] != 1 or type(value["version"]) is not int:
        raise ValueError("record version unsupported")
    if value["terminal_id"] != terminal_id or not _valid_terminal_id(value["terminal_id"]):
        raise ValueError("record terminal mismatch")
    if type(value["pid"]) is not int or value["pid"] <= 1:
        raise ValueError("record PID malformed")
    if str(value["pid"]) != parsed_name.group("pid"):
        raise ValueError("record PID filename mismatch")
    if type(value["uid"]) is not int or value["uid"] < 0:
        raise ValueError("record UID malformed")
    if type(value["start_ticks"]) is not int or value["start_ticks"] < 0:
        raise ValueError("record birth malformed")
    if not isinstance(value["boot_id"], str) or not value["boot_id"] or len(value["boot_id"]) > 128:
        raise ValueError("record boot malformed")
    namespace = value["pid_namespace"]
    if (
        not isinstance(namespace, dict)
        or set(namespace) != {"device", "inode"}
        or type(namespace["device"]) is not int
        or type(namespace["inode"]) is not int
        or namespace["device"] < 0
        or namespace["inode"] <= 0
    ):
        raise ValueError("record namespace malformed")
    if not isinstance(value["ready_token"], str) or not value["ready_token"]:
        raise ValueError("record token malformed")
    if not isinstance(value["ready_file"], str) or not value["ready_file"]:
        raise ValueError("record ready file malformed")
    if value["owner_dir"] != str(registry_path):
        raise ValueError("record owner directory mismatch")
    if (
        not isinstance(value["cmdline_sha256"], str)
        or re.fullmatch(r"[a-f0-9]{64}", value["cmdline_sha256"]) is None
    ):
        raise ValueError("record command digest malformed")
    if not isinstance(value["nonce"], str) or _NONCE.fullmatch(value["nonce"]) is None:
        raise ValueError("record nonce malformed")
    if value["nonce"] != parsed_name.group("nonce"):
        raise ValueError("record nonce filename mismatch")
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise PermissionError("record ownership rejected")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise PermissionError("record permissions rejected")
    return _OwnerRecord(
        terminal_id=terminal_id,
        pid=value["pid"],
        uid=value["uid"],
        start_ticks=value["start_ticks"],
        boot_id=value["boot_id"],
        pid_namespace=(namespace["device"], namespace["inode"]),
        ready_token=value["ready_token"],
        ready_file=value["ready_file"],
        owner_dir=value["owner_dir"],
        cmdline_sha256=value["cmdline_sha256"],
        nonce=value["nonce"],
        name=name,
        device=info.st_dev,
        inode=info.st_ino,
    )


def _read_json_at(
    dir_fd: int, name: str, *, max_bytes: int = _MAX_METADATA_BYTES
) -> tuple[bytes, os.stat_result]:
    fd = os.open(name, _READ_FLAGS, dir_fd=dir_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise PermissionError("metadata ownership rejected")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise PermissionError("metadata permissions rejected")
        raw = _read_bounded(fd, max_bytes)
    finally:
        os.close(fd)
    return raw, info


def _scan_records(
    dir_fd: int, terminal_id: str, registry_path: Path
) -> tuple[list[_OwnerRecord], int, int, set[str]]:
    records: list[_OwnerRecord] = []
    unknown = 0
    identities = 0
    names = set(os.listdir(dir_fd))
    for name in names:
        if _NORMAL_NAME.fullmatch(name):
            # Orphan receipts are incomplete lifecycle metadata, not evidence
            # that the associated child is absent.
            record_name = name.removesuffix(".normal.json") + ".json"
            if record_name not in names:
                identities += 1
                unknown += 1
            continue
        if not name.endswith(".json"):
            # A leftover in-progress publication cannot authorize a signal.
            if name.startswith(".tmp-"):
                identities += 1
                unknown += 1
            continue
        identities += 1
        try:
            raw, info = _read_json_at(dir_fd, name)
            records.append(_parse_record(raw, name, terminal_id, registry_path, info))
        except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            unknown += 1
    return records, identities, unknown, names


def _read_config(terminal_id: str, registry_path: Path) -> tuple[list[_ConfigEntry], bool, bool]:
    """Return (eligible entries, has_stop_config, malformed)."""

    tmp_fd = _open_dir(registry_path.parent)
    name = f"{terminal_id}.mcp.json"
    try:
        raw, _ = _read_json_at(tmp_fd, name)
    finally:
        os.close(tmp_fd)
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
    if not isinstance(document, dict) or set(document) != {"mcpServers"}:
        raise ValueError("MCP config malformed")
    servers = document["mcpServers"]
    if not isinstance(servers, dict):
        raise ValueError("MCP server map malformed")
    eligible: list[_ConfigEntry] = []
    document_sha256 = hashlib.sha256(raw).hexdigest()
    has_stop_config = False
    malformed = False
    for name, entry in servers.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        env = entry.get("env")
        if not isinstance(env, dict):
            continue
        if env.get(SIGNAL_STOP_ENV) != "1" or not is_cao_mcp_server(name, entry):
            continue
        has_stop_config = True
        command = entry.get("command")
        args = entry.get("args", [])
        if isinstance(command, str):
            command_parts = [command]
        elif (
            isinstance(command, list) and command and all(isinstance(item, str) for item in command)
        ):
            command_parts = command
        else:
            malformed = True
            continue
        if (
            not all(command_parts)
            or not isinstance(args, list)
            or not all(isinstance(item, str) for item in args)
        ):
            malformed = True
            continue
        relevant = {key: env.get(key) for key in _RELEVANT_ENV}
        if (
            relevant.get("CAO_TERMINAL_ID") != terminal_id
            or relevant.get(READY_FILE_ENV) is None
            or not isinstance(relevant.get(READY_FILE_ENV), str)
            or not relevant.get(READY_FILE_ENV)
            or relevant.get(READY_TOKEN_ENV) is None
            or not isinstance(relevant.get(READY_TOKEN_ENV), str)
            or not relevant.get(READY_TOKEN_ENV)
            or relevant.get(OWNER_DIR_ENV) != str(registry_path)
            or relevant.get(SIGNAL_STOP_ENV) != "1"
            or relevant.get(PHYSICAL_EOF_ENV) is not None
            or not all(value is None or isinstance(value, str) for value in relevant.values())
        ):
            malformed = True
            continue
        eligible.append(_ConfigEntry(tuple([*command_parts, *args]), relevant, document_sha256))
    return eligible, has_stop_config, malformed


def _record_environment(record: _OwnerRecord) -> dict[str, str]:
    return {
        "CAO_TERMINAL_ID": record.terminal_id,
        READY_FILE_ENV: record.ready_file,
        READY_TOKEN_ENV: record.ready_token,
        OWNER_DIR_ENV: record.owner_dir,
        SIGNAL_STOP_ENV: "1",
    }


def _record_matches_proc(record: _OwnerRecord, proc: _ProcIdentity) -> bool:
    if (
        proc.pid != record.pid
        or proc.uid != record.uid
        or proc.uid != os.getuid()
        or proc.start_ticks != record.start_ticks
        or proc.boot_id != record.boot_id
        or proc.pid_namespace != record.pid_namespace
        or proc.cmdline_sha256 != record.cmdline_sha256
        or proc.zombie
    ):
        return False
    expected = _record_environment(record)
    for name, value in expected.items():
        if proc.relevant_env.get(os.fsencode(name)) != os.fsencode(value):
            return False
    return proc.relevant_env.get(os.fsencode(PHYSICAL_EOF_ENV)) is None


def _argv_matches_config(proc: _ProcIdentity, config: _ConfigEntry) -> bool:
    expected = tuple(os.fsencode(item) for item in config.argv)
    if proc.argv == expected:
        return True
    # Linux inserts a script's shebang interpreter before the exact configured
    # script argv. Admit only a direct absolute interpreter, exact script tail,
    # and matching /proc executable; never accept an arbitrary argv prefix.
    if not expected or proc.argv[1:] != expected or not Path(config.argv[0]).is_absolute():
        return False
    try:
        fd = os.open(config.argv[0], _READ_FLAGS)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                return False
            first_line = _read_bounded(fd, 512).splitlines()[0]
        finally:
            os.close(fd)
    except (OSError, ValueError, IndexError):
        return False
    if not first_line.startswith(b"#!"):
        return False
    shebang_parts = first_line[2:].strip().split()
    if len(shebang_parts) != 1 or not shebang_parts[0].startswith(b"/"):
        return False
    interpreter = os.fsdecode(shebang_parts[0])
    try:
        actual_executable = os.path.realpath(proc.exe_path)
        expected_executable = os.path.realpath(interpreter)
    except (OSError, ValueError):
        return False
    return proc.argv[0] == shebang_parts[0] and actual_executable == expected_executable


def _config_matches(
    record: _OwnerRecord, proc: _ProcIdentity, entries: list[_ConfigEntry]
) -> _ConfigEntry | None:
    for entry in entries:
        env = entry.env
        expected = _record_environment(record)
        if any(env.get(name) != value for name, value in expected.items()):
            continue
        if _argv_matches_config(proc, entry):
            return entry
    return None


def _pidfd_ready(fd: int) -> bool | None:
    """Return true for observed exit, false if not ready, and none on poll errors."""

    try:
        poller = select.poll()
        poller.register(fd, _PIDFD_EXIT_EVENTS | _PIDFD_ERROR_EVENTS)
        events = poller.poll(0)
        found = False
        unexpected_fd = False
        flags = 0
        for event_fd, event_flags in events:
            if event_fd == fd:
                found = True
                flags |= event_flags
            else:
                unexpected_fd = True
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if not found:
        return None if unexpected_fd else False
    if unexpected_fd:
        return None
    if flags & _PIDFD_ERROR_EVENTS:
        return None
    if flags & _PIDFD_EXIT_EVENTS:
        return True
    return None


def _pin_record(
    record: _OwnerRecord, current_boot: str, current_ns: tuple[int, int]
) -> tuple[str, int | None, _ProcIdentity | None]:
    if record.boot_id != current_boot:
        return "retired", None, None
    if record.pid_namespace != current_ns:
        return "unsupported", None, None
    if record.pid == os.getpid():
        return "unknown", None, None
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        return "unsupported", None, None
    try:
        fd = os.pidfd_open(record.pid, 0)
    except ProcessLookupError:
        return "retired", None, None
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return "retired", None, None
        if exc.errno in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
            return "unsupported", None, None
        return "unknown", None, None
    ready = _pidfd_ready(fd)
    if ready is True:
        os.close(fd)
        return "retired", None, None
    if ready is None:
        os.close(fd)
        return "unknown", None, None
    try:
        proc = _capture_proc(record.pid)
    except ProcessLookupError:
        if _pidfd_ready(fd) is True:
            os.close(fd)
            return "retired", None, None
        os.close(fd)
        return "unknown", None, None
    except FileNotFoundError:
        if _pidfd_ready(fd) is True:
            os.close(fd)
            return "retired", None, None
        os.close(fd)
        return "unknown", None, None
    except (OSError, ValueError, TypeError, UnicodeError):
        os.close(fd)
        return "unknown", None, None
    if proc.start_ticks != record.start_ticks:
        os.close(fd)
        return "retired", None, None
    if proc.zombie:
        os.close(fd)
        return "retired", None, None
    ready = _pidfd_ready(fd)
    if ready is True:
        os.close(fd)
        return "retired", None, None
    if ready is None:
        os.close(fd)
        return "unknown", None, None
    if not _record_matches_proc(record, proc):
        os.close(fd)
        return "unknown", None, None
    return "live", fd, proc


def _recheck_before_signal(
    owner: _PinnedOwner, current_boot: str, current_ns: tuple[int, int]
) -> bool:
    record = owner.record
    try:
        ready = _pidfd_ready(owner.fd)
        if ready is True:
            owner.status = "retired"
            return False
        if ready is None:
            owner.status = "unknown"
            return False
        if record.boot_id != current_boot or record.pid_namespace != current_ns:
            owner.status = "unknown"
            return False
        proc = _capture_proc(record.pid)
    except FileNotFoundError:
        if _pidfd_ready(owner.fd) is True:
            owner.status = "retired"
        else:
            owner.status = "unknown"
        return False
    except (OSError, ValueError, TypeError, UnicodeError):
        owner.status = "unknown"
        return False
    if proc.start_ticks != record.start_ticks:
        owner.status = "retired"
        return False
    if proc.zombie or not _record_matches_proc(record, proc):
        owner.status = "unknown"
        return False
    if not _argv_matches_config(proc, owner.config):
        owner.status = "unknown"
        return False
    try:
        entries, has_stop_config, malformed = _load_config_state(
            record.terminal_id, _registry_path(record.terminal_id, owner.home)
        )
    except (OSError, ValueError, TypeError):
        owner.status = "unknown"
        return False
    if malformed or not has_stop_config or owner.config not in entries:
        owner.status = "unknown"
        return False
    return True


def _read_receipt(dir_fd: int, record: _OwnerRecord) -> tuple[bool, os.stat_result | None, bool]:
    """Return (normal, stat, malformed-or-unreadable)."""

    name = _receipt_name(record)
    try:
        raw, info = _read_json_at(dir_fd, name)
    except FileNotFoundError:
        return False, None, False
    except (OSError, ValueError):
        return False, None, True
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs)
        if not isinstance(value, dict) or set(value) != _RECEIPT_FIELDS:
            raise ValueError("receipt fields malformed")
        if (
            type(value["version"]) is not int
            or value["version"] != 1
            or value["terminal_id"] != record.terminal_id
            or value["pid"] != record.pid
            or type(value["pid"]) is not int
            or value["start_ticks"] != record.start_ticks
            or type(value["start_ticks"]) is not int
            or value["boot_id"] != record.boot_id
            or value["nonce"] != record.nonce
            or value["origin"] not in {"peer_eof", "local_stop_after_signal"}
            or value["normal_return"] is not True
        ):
            raise ValueError("receipt identity malformed")
        for name in (
            "pending_client",
            "pending_server",
            "input_tail_bytes",
            "output_tail_bytes",
            "unflushed_replies",
            "output_busy",
        ):
            if type(value[name]) is not int or value[name] != 0:
                raise ValueError("receipt transport unsettled")
        return True, info, False
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return False, info, True


def _unlink_same_owned_file(dir_fd: int, name: str, info: os.stat_result) -> bool:
    try:
        current = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if (
            current.st_dev != info.st_dev
            or current.st_ino != info.st_ino
            or current.st_uid != os.getuid()
            or not stat.S_ISREG(current.st_mode)
            or stat.S_IMODE(current.st_mode) != 0o600
        ):
            return False
        os.unlink(name, dir_fd=dir_fd)
        return True
    except OSError:
        return False


def _retire_record(dir_fd: int, record: _OwnerRecord) -> tuple[bool, bool]:
    normal, receipt_info, receipt_invalid = _read_receipt(dir_fd, record)
    if receipt_invalid:
        return False, False
    try:
        info = os.stat(record.name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return False, False
    if (
        info.st_dev != record.device
        or info.st_ino != record.inode
        or info.st_uid != os.getuid()
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        return False, False
    # Remove the receipt first. If interrupted, a retained record remains
    # conservatively classifiable as unsealed on the next reconciliation.
    if receipt_info is not None and not _unlink_same_owned_file(
        dir_fd, _receipt_name(record), receipt_info
    ):
        return False, False
    if not _unlink_same_owned_file(dir_fd, record.name, info):
        return False, False
    return True, normal


def _result(counts: _Counts, elapsed_ms: int) -> PrestopResult:
    if counts.identity_count == 0:
        outcome = PrestopOutcome.NOT_APPLICABLE
    elif counts.unsupported and counts.unknown_count == counts.unsupported_count:
        outcome = PrestopOutcome.UNSUPPORTED
    elif counts.unknown_count:
        outcome = PrestopOutcome.UNKNOWN
    elif counts.still_live_count:
        outcome = PrestopOutcome.STILL_LIVE
    elif counts.retired_count == counts.identity_count and counts.retired_count:
        outcome = (
            PrestopOutcome.RETIRED_NORMAL
            if counts.normal_count == counts.retired_count
            else PrestopOutcome.RETIRED_UNSEALED
        )
    else:
        outcome = PrestopOutcome.UNKNOWN
    return PrestopResult(
        outcome=outcome,
        identity_count=counts.identity_count,
        signaled_count=counts.signaled_count,
        retired_count=counts.retired_count,
        normal_count=counts.normal_count,
        unknown_count=counts.unknown_count,
        still_live_count=counts.still_live_count,
        elapsed_ms=max(0, elapsed_ms),
    )


def _unsupported_platform_counts(terminal_id: str, home: Path | None) -> _Counts:
    """Keep legacy no-record cleanup a no-op on platforms without pidfds."""

    if not _valid_terminal_id(terminal_id):
        return _Counts(identity_count=1, unknown_count=1)
    path = _registry_path(terminal_id, home)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return _missing_registry_counts(terminal_id, path)
    except OSError:
        return _Counts(identity_count=1, unknown_count=1)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        return _Counts(identity_count=1, unknown_count=1)
    try:
        names = set(os.listdir(path))
    except OSError:
        return _Counts(identity_count=1, unknown_count=1)
    if not names:
        return _Counts()
    record_names = {name for name in names if _RECORD_NAME.fullmatch(name)}
    paired_receipts = {
        name
        for name in names
        if _NORMAL_NAME.fullmatch(name)
        and name.removesuffix(".normal.json") + ".json" in record_names
    }
    orphan_receipts = {
        name for name in names if _NORMAL_NAME.fullmatch(name) and name not in paired_receipts
    }
    unknown_names = names - record_names - paired_receipts - orphan_receipts
    identity_count = len(record_names) + len(orphan_receipts) + len(unknown_names)
    if unknown_names or orphan_receipts or not record_names:
        count = max(1, identity_count)
        return _Counts(identity_count=count, unknown_count=count)
    count = len(record_names)
    return _Counts(
        identity_count=count,
        unknown_count=count,
        unsupported=True,
        unsupported_count=count,
    )


def _normal_payload(record: _OwnerRecord, origin: str, counts: Mapping[str, int]) -> bytes:
    value = {
        "version": 1,
        "terminal_id": record.terminal_id,
        "pid": record.pid,
        "start_ticks": record.start_ticks,
        "boot_id": record.boot_id,
        "nonce": record.nonce,
        "origin": origin,
        "normal_return": True,
        **counts,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def publish_owned_mcp_normal_return(
    registration: OwnedMcpRegistration | None, state_snapshot: object
) -> bool:
    """Publish the separate normal witness after genuine guarded ``run()`` return."""

    if not isinstance(registration, OwnedMcpRegistration):
        return False
    if not _valid_terminal_id(registration.terminal_id):
        return False
    if type(registration.pid) is not int or registration.pid != os.getpid():
        return False
    if not isinstance(registration.nonce, str) or _NONCE.fullmatch(registration.nonce) is None:
        return False
    settled = _settled_snapshot(state_snapshot)
    if settled is None:
        return False
    origin, counts = settled
    try:
        registry_fd, registry_path = _open_registry_path(
            registration.owner_dir, registration.terminal_id
        )
        try:
            name = _record_name(registration.pid, registration.nonce)
            raw, info = _read_json_at(registry_fd, name)
            record = _parse_record(raw, name, registration.terminal_id, registry_path, info)
            proc = _capture_proc(os.getpid())
            if not _record_matches_proc(record, proc):
                return False
            payload = _normal_payload(record, origin, counts)
            return _write_exclusive_at(registry_fd, _receipt_name(record), payload)
        finally:
            os.close(registry_fd)
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
        return False


def _load_config_state(
    terminal_id: str, registry_path: Path
) -> tuple[list[_ConfigEntry], bool, bool]:
    try:
        return _read_config(terminal_id, registry_path)
    except FileNotFoundError:
        return [], False, False
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return [], False, True


def _confirm_registry_absent(registry_path: Path) -> bool:
    """Recheck the exact final path before accepting a no-registry result."""

    try:
        os.lstat(registry_path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _missing_registry_counts(terminal_id: str, registry_path: Path) -> _Counts:
    entries, has_stop_config, malformed = _load_config_state(terminal_id, registry_path)
    if not _confirm_registry_absent(registry_path):
        return _Counts(identity_count=1, unknown_count=1)
    if malformed or (has_stop_config and entries):
        count = max(1, len(entries))
        return _Counts(identity_count=count, unknown_count=count)
    return _Counts()


def _safe_deadline(deadline: float) -> bool:
    return type(deadline) in (int, float) and deadline >= 0 and deadline < float("inf")


def prestop_owned_mcp(
    terminal_id: str, deadline: float, *, home: Path | None = None
) -> PrestopResult:
    """Request SIGINT of the exact configured MCP child within ``deadline``."""

    return prestop_owned_mcp_many([terminal_id], deadline, home=home).get(
        terminal_id,
        PrestopResult(PrestopOutcome.NOT_APPLICABLE, 0, 0, 0, 0, 0, 0, 0),
    )


def prestop_owned_mcp_many(
    terminal_ids: Iterable[str], deadline: float, *, home: Path | None = None
) -> dict[str, PrestopResult]:
    """Signal every authorized owner before waiting, using one absolute deadline."""

    started = time.monotonic()
    try:
        requested = list(dict.fromkeys(terminal_ids))
    except (TypeError, ValueError):
        return {}
    result: dict[str, PrestopResult] = {}
    counts_by_terminal: dict[str, _Counts] = {}
    names_by_terminal: dict[str, set[str]] = {}
    dirfds: dict[str, int] = {}
    pinned: list[_PinnedOwner] = []
    try:
        if not sys.platform.startswith("linux") or not _O_DIRECTORY or not _O_NOFOLLOW:
            for terminal_id in requested:
                counts = _unsupported_platform_counts(
                    terminal_id,
                    home,
                )
                result[terminal_id] = _result(counts, int((time.monotonic() - started) * 1000))
            return result
        if not _safe_deadline(deadline):
            for terminal_id in requested:
                counts = _Counts(identity_count=1, unknown_count=1)
                result[terminal_id] = _result(counts, int((time.monotonic() - started) * 1000))
            return result
        current_boot = _boot_id()
        current_ns = _pid_namespace(os.getpid())
        for terminal_id in requested:
            counts = _Counts()
            counts_by_terminal[terminal_id] = counts
            names_by_terminal[terminal_id] = set()
            if not _valid_terminal_id(terminal_id):
                counts.identity_count = 1
                counts.unknown_count = 1
                continue
            registry_path = _registry_path(terminal_id, home)
            try:
                os.lstat(registry_path)
            except FileNotFoundError:
                missing = _missing_registry_counts(terminal_id, registry_path)
                counts.identity_count = missing.identity_count
                counts.unknown_count = missing.unknown_count
                continue
            except OSError:
                counts.identity_count = 1
                counts.unknown_count = 1
                continue
            try:
                dir_fd, _ = _open_registry(terminal_id, home)
            except FileNotFoundError:
                missing = _missing_registry_counts(terminal_id, registry_path)
                counts.identity_count = missing.identity_count
                counts.unknown_count = missing.unknown_count
                continue
            except (OSError, ValueError):
                counts.identity_count = 1
                counts.unknown_count = 1
                continue
            dirfds[terminal_id] = dir_fd
            try:
                records, identities, unknown, scanned_names = _scan_records(
                    dir_fd, terminal_id, registry_path
                )
                counts.identity_count = identities
                counts.unknown_count += unknown
                names_by_terminal[terminal_id] = scanned_names
                entries, has_stop_config, malformed = _load_config_state(terminal_id, registry_path)
                config_unusable = malformed or (has_stop_config and not entries)
                if counts.identity_count == 0 and has_stop_config and entries:
                    counts.identity_count += len(entries)
                    counts.unknown_count += len(entries)
                    continue
                if config_unusable and not records:
                    counts.identity_count = max(1, counts.identity_count)
                    counts.unknown_count += 1
                    continue
            except (OSError, ValueError, TypeError, AttributeError):
                counts.unknown_count += max(1, counts.identity_count)
                continue
            for record in records:
                state, fd, proc = _pin_record(record, current_boot, current_ns)
                if state == "retired":
                    counts.retired_count += 1
                    removed, normal = _retire_record(dir_fd, record)
                    if normal:
                        counts.normal_count += 1
                    if not removed:
                        counts.unknown_count += 1
                    continue
                if state == "unsupported":
                    counts.unsupported = True
                    counts.unsupported_count += 1
                    counts.unknown_count += 1
                    continue
                if state == "unknown" or fd is None or proc is None:
                    counts.unknown_count += 1
                    continue
                config = None if config_unusable else _config_matches(record, proc, entries)
                if config is None:
                    os.close(fd)
                    if config_unusable:
                        counts.unknown_count += 1
                    else:
                        counts.still_live_count += 1
                    continue
                pinned.append(_PinnedOwner(record=record, fd=fd, config=config, home=home))

        # Revalidate and signal all authorized owners before the single shared
        # wait. No raw PID signal or process-name scan is used.
        for owner in pinned:
            counts = counts_by_terminal[owner.record.terminal_id]
            if time.monotonic() >= deadline:
                owner.status = "still_live"
                counts.still_live_count += 1
                continue
            if not _recheck_before_signal(owner, current_boot, current_ns):
                if owner.status == "retired":
                    counts.retired_count += 1
                elif owner.status == "unsupported":
                    counts.unsupported = True
                    counts.unsupported_count += 1
                    counts.unknown_count += 1
                else:
                    counts.unknown_count += 1
                continue
            try:
                signal.pidfd_send_signal(owner.fd, signal.SIGINT)
                owner.signaled = True
                owner.status = "signaled"
                counts.signaled_count += 1
            except ProcessLookupError:
                owner.status = "retired"
                counts.retired_count += 1
            except OSError as exc:
                if exc.errno == errno.ESRCH:
                    owner.status = "retired"
                    counts.retired_count += 1
                elif exc.errno in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
                    owner.status = "unsupported"
                    counts.unsupported = True
                    counts.unsupported_count += 1
                    counts.unknown_count += 1
                else:
                    owner.status = "unknown"
                    counts.unknown_count += 1

        pending = [owner for owner in pinned if owner.status == "signaled"]
        while pending and time.monotonic() < deadline:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if remaining_ms <= 0:
                break
            try:
                poller = select.poll()
                for owner in pending:
                    poller.register(owner.fd, _PIDFD_EXIT_EVENTS | _PIDFD_ERROR_EVENTS)
                poll_events = poller.poll(remaining_ms)
            except (OSError, ValueError, TypeError, AttributeError):
                for owner in pending:
                    owner.status = "unknown"
                    counts_by_terminal[owner.record.terminal_id].unknown_count += 1
                pending = []
                break
            if not poll_events:
                break
            events_by_fd: dict[int, int] = {}
            for event_fd, event_flags in poll_events:
                events_by_fd[event_fd] = events_by_fd.get(event_fd, 0) | event_flags
            pending_after_poll: list[_PinnedOwner] = []
            for owner in pending:
                owner_flags = events_by_fd.pop(owner.fd, None)
                if owner_flags is None:
                    pending_after_poll.append(owner)
                    continue
                counts = counts_by_terminal[owner.record.terminal_id]
                if owner_flags & _PIDFD_ERROR_EVENTS:
                    owner.status = "unknown"
                    counts.unknown_count += 1
                elif owner_flags & _PIDFD_EXIT_EVENTS:
                    owner.status = "retired"
                    counts.retired_count += 1
                else:
                    owner.status = "unknown"
                    counts.unknown_count += 1
            if events_by_fd:
                # A poll result for an unregistered descriptor cannot prove any
                # of our pinned owners exited; stop instead of spinning.
                for owner in pending_after_poll:
                    owner.status = "unknown"
                    counts_by_terminal[owner.record.terminal_id].unknown_count += 1
                pending_after_poll = []
            pending = pending_after_poll
        for owner in pending:
            ready = _pidfd_ready(owner.fd)
            if ready is True:
                owner.status = "retired"
                counts_by_terminal[owner.record.terminal_id].retired_count += 1
            elif ready is None:
                owner.status = "unknown"
                counts_by_terminal[owner.record.terminal_id].unknown_count += 1
            else:
                owner.status = "still_live"
                counts_by_terminal[owner.record.terminal_id].still_live_count += 1

        for owner in pinned:
            if owner.status != "retired":
                continue
            owner_dir_fd = dirfds.get(owner.record.terminal_id)
            if owner_dir_fd is None:
                counts_by_terminal[owner.record.terminal_id].unknown_count += 1
                continue
            removed, normal = _retire_record(owner_dir_fd, owner.record)
            if normal:
                counts_by_terminal[owner.record.terminal_id].normal_count += 1
            if not removed:
                counts_by_terminal[owner.record.terminal_id].unknown_count += 1

        # One bounded rescan catches registrations published after the initial
        # snapshot. New records are left untouched and force a retry by caller.
        for terminal_id, dir_fd in dirfds.items():
            counts = counts_by_terminal[terminal_id]
            registry_path = _registry_path(terminal_id, home)
            try:
                after = set(os.listdir(dir_fd))
            except OSError:
                counts.unknown_count += 1
                continue
            new_names = after - names_by_terminal.get(terminal_id, set())
            for name in new_names:
                if name.endswith(".normal.json"):
                    record_name = name.removesuffix(".normal.json") + ".json"
                    if record_name not in after:
                        counts.unknown_count += 1
                        counts.identity_count += 1
                elif name.endswith(".json") or name.startswith(".tmp-"):
                    counts.still_live_count += 1
                    counts.identity_count += 1
                # A receipt arriving for an already-counted record is not a new
                # identity; it only completes the evidence for that record.
        for terminal_id, counts in counts_by_terminal.items():
            result[terminal_id] = _result(counts, int((time.monotonic() - started) * 1000))
        return result
    except (OSError, ValueError, TypeError, OverflowError):
        for terminal_id in requested:
            counts = counts_by_terminal.get(terminal_id, _Counts(identity_count=1))
            counts.unknown_count += max(1, counts.identity_count)
            result[terminal_id] = _result(counts, int((time.monotonic() - started) * 1000))
        return result
    finally:
        for owner in pinned:
            try:
                os.close(owner.fd)
            except OSError:
                pass
        for fd in dirfds.values():
            try:
                os.close(fd)
            except OSError:
                pass


def reconcile_owned_mcp(terminal_id: str, *, home: Path | None = None) -> bool:
    """Prune only owner records whose exact process identity is positively gone."""

    if not _valid_terminal_id(terminal_id):
        return False
    if not sys.platform.startswith("linux") or not _O_DIRECTORY or not _O_NOFOLLOW:
        counts = _unsupported_platform_counts(terminal_id, home)
        return counts.identity_count == 0
    registry_path = _registry_path(terminal_id, home)
    # The owner registry is optional on shared Windows-backed homes. A missing
    # final path is a no-op; any existing path still uses the strict open below.
    try:
        os.lstat(registry_path)
    except FileNotFoundError:
        return _confirm_registry_absent(registry_path)
    except OSError:
        return False
    try:
        dir_fd, registry_path = _open_registry(terminal_id, home)
    except FileNotFoundError:
        return _confirm_registry_absent(registry_path)
    except (OSError, ValueError):
        return False
    try:
        records, _, unknown, _scan_names = _scan_records(dir_fd, terminal_id, registry_path)
        if unknown:
            return False
        current_boot = _boot_id()
        current_ns = _pid_namespace(os.getpid())
        all_retired = True
        for record in records:
            state, fd, _ = _pin_record(record, current_boot, current_ns)
            if state != "retired":
                all_retired = False
                if fd is not None:
                    os.close(fd)
                continue
            removed, _ = _retire_record(dir_fd, record)
            if not removed:
                all_retired = False
        if not all_retired:
            return False
        try:
            current_names = set(os.listdir(dir_fd))
        except OSError:
            return False
        return not (current_names - _scan_names)
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
        return False
    finally:
        os.close(dir_fd)
