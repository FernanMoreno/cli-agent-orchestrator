"""Bind each v2 result mode to its private owner and selected launch tree."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from cli_agent_orchestrator.utils.opencode_ownership import PrivateConfigOwnership

BINDING_FILENAME = ".cao-v2-result-binding.json"
BINDING_LIMIT = 4096
CONFIG_LIMIT = 4 * 1024 * 1024
DATABASE_LIMIT = 64 * 1024 * 1024
AGENT_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class ResultBindingError(ValueError):
    """A private v2 source or database binding cannot be proven."""


@dataclass(frozen=True)
class V2ResultBinding:
    ownership: PrivateConfigOwnership
    root: Path
    owner_claim: dict[str, Any]
    mode: str
    agent_id: str | None
    database_path: Path | None
    database_identity: tuple[int, int] | None
    source_sha256: str | None
    agent_config_sha256: str | None


def _refuse() -> NoReturn:
    raise ResultBindingError("OpenCode v2 result binding is unavailable")


def _check_deadline(stop: float | None) -> None:
    if stop is not None and time.monotonic() >= stop:
        _refuse()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _refuse()
        result[key] = value
    return result


def _read_private_file(path: Path, *, limit: int, stop: float | None = None) -> bytes:
    _check_deadline(stop)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (OSError, ValueError):
        _refuse()
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size > limit
        ):
            _refuse()
        chunks = bytearray()
        while len(chunks) <= limit:
            _check_deadline(stop)
            chunk = os.read(descriptor, min(65536, limit + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) != info.st_size or len(chunks) > limit:
            _refuse()
        _check_deadline(stop)
        return bytes(chunks)
    except OSError:
        _refuse()
    finally:
        os.close(descriptor)


def _config_agent_entry(
    config_path: Path, agent_id: str, *, stop: float | None = None
) -> tuple[dict[str, Any], str]:
    try:
        raw = _read_private_file(config_path, limit=CONFIG_LIMIT, stop=stop)
        config = json.loads(raw, object_pairs_hook=_unique_object)
    except ResultBindingError:
        raise
    except (UnicodeError, ValueError, TypeError):
        _refuse()
    if type(config) is not dict or type(config.get("agents")) is not dict:
        _refuse()
    entry = config["agents"].get(agent_id)
    if type(entry) is not dict:
        _refuse()
    try:
        canonical = json.dumps(
            entry,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    except (UnicodeError, ValueError, TypeError):
        _refuse()
    _check_deadline(stop)
    return entry, hashlib.sha256(canonical).hexdigest()


def _write_binding_file(path: Path, payload: bytes) -> None:
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
    except OSError:
        _refuse()
    try:
        os.fchmod(descriptor, 0o600)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            _refuse()
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        root_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
    except OSError:
        _refuse()
    finally:
        os.close(descriptor)


def write_v2_result_binding(
    ownership: PrivateConfigOwnership,
    selected_agent: tuple[str, str] | None,
    config_path: Path,
) -> None:
    """Persist a private result mode and, when selected, its agent identity."""
    if selected_agent is not None and (
        type(selected_agent) is not tuple
        or len(selected_agent) != 2
        or type(selected_agent[0]) is not str
        or AGENT_PATTERN.fullmatch(selected_agent[0]) is None
        or type(selected_agent[1]) is not str
        or not selected_agent[1]
        or len(selected_agent[1].encode("utf-8", errors="strict")) > CONFIG_LIMIT
    ):
        _refuse()
    try:
        root, claim = ownership.active_v2_root_and_claim()
        expected_config = root / "opencode.json"
        if config_path != expected_config or config_path.resolve(strict=True) != expected_config:
            _refuse()
        if selected_agent is None:
            mode = "rendered"
            agent_id = None
            source_sha256 = None
            agent_config_sha256 = None
        else:
            mode = "native_text"
            agent_id = selected_agent[0]
            _entry, agent_config_sha256 = _config_agent_entry(config_path, agent_id)
            source_sha256 = hashlib.sha256(
                selected_agent[1].encode("utf-8", errors="strict")
            ).hexdigest()
        document = {
            "version": 1,
            "terminal_id": ownership.terminal_id,
            "owner_claim": claim,
            "mode": mode,
            "agent_id": agent_id,
            "source_sha256": source_sha256,
            "agent_config_sha256": agent_config_sha256,
        }
        payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        if len(payload) > BINDING_LIMIT:
            _refuse()
        _write_binding_file(root / BINDING_FILENAME, payload)
    except ResultBindingError:
        raise
    except Exception:
        _refuse()


def _private_directory(path: Path, *, exact_mode: int = 0o700) -> None:
    try:
        info = path.lstat()
    except OSError:
        _refuse()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != exact_mode
    ):
        _refuse()


def _owned_database(root: Path) -> tuple[Path, tuple[int, int]]:
    _private_directory(root)
    data = root / "data"
    opencode_data = data / "opencode"
    _private_directory(data)
    _private_directory(opencode_data)
    database = opencode_data / "opencode.db"
    try:
        info = database.lstat()
        if (
            database.resolve(strict=True) != database
            or not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_size > DATABASE_LIMIT
        ):
            _refuse()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(database) + suffix)
            try:
                side_info = sidecar.lstat()
            except FileNotFoundError:
                continue
            if (
                not stat.S_ISREG(side_info.st_mode)
                or stat.S_ISLNK(side_info.st_mode)
                or side_info.st_uid != os.getuid()
                or side_info.st_nlink != 1
                or side_info.st_size > DATABASE_LIMIT
            ):
                _refuse()
    except ResultBindingError:
        raise
    except (OSError, RuntimeError, ValueError):
        _refuse()
    return database, (info.st_dev, info.st_ino)


def read_v2_result_binding(
    ownership: PrivateConfigOwnership,
    expected_agent: str | None = None,
    *,
    expected_major: int = 2,
    stop: float | None = None,
) -> V2ResultBinding:
    """Read the private result mode and verify every binding it requires."""
    if (
        type(expected_major) is not int
        or expected_major != 2
        or (
            expected_agent is not None
            and (type(expected_agent) is not str or AGENT_PATTERN.fullmatch(expected_agent) is None)
        )
    ):
        _refuse()
    try:
        _check_deadline(stop)
        root, claim = ownership.active_v2_root_and_claim()
        if root.resolve(strict=True) != root:
            _refuse()
        raw = _read_private_file(root / BINDING_FILENAME, limit=BINDING_LIMIT, stop=stop)
        document = json.loads(raw, object_pairs_hook=_unique_object)
        if (
            type(document) is not dict
            or set(document)
            != {
                "version",
                "terminal_id",
                "owner_claim",
                "mode",
                "agent_id",
                "source_sha256",
                "agent_config_sha256",
            }
            or type(document["version"]) is not int
            or document["version"] != 1
            or document["terminal_id"] != ownership.terminal_id
            or document["owner_claim"] != claim
            or document["mode"] not in {"rendered", "native_text"}
        ):
            _refuse()
        mode = document["mode"]
        if mode == "rendered":
            if (
                document["agent_id"] is not None
                or document["source_sha256"] is not None
                or document["agent_config_sha256"] is not None
                or expected_agent is not None
            ):
                _refuse()
            agent_id = None
            actual_config_sha256 = None
            source_sha256 = None
            database = None
            identity = None
        else:
            if (
                type(document["agent_id"]) is not str
                or AGENT_PATTERN.fullmatch(document["agent_id"]) is None
                or (expected_agent is not None and document["agent_id"] != expected_agent)
                or type(document["source_sha256"]) is not str
                or HASH_PATTERN.fullmatch(document["source_sha256"]) is None
                or type(document["agent_config_sha256"]) is not str
                or HASH_PATTERN.fullmatch(document["agent_config_sha256"]) is None
            ):
                _refuse()
            agent_id = document["agent_id"]
            _entry, actual_config_sha256 = _config_agent_entry(
                root / "opencode.json", agent_id, stop=stop
            )
            if actual_config_sha256 != document["agent_config_sha256"]:
                _refuse()
            source_sha256 = document["source_sha256"]
            database, identity = _owned_database(root)
        _check_deadline(stop)
        return V2ResultBinding(
            ownership=ownership,
            root=root,
            owner_claim=claim,
            mode=mode,
            agent_id=agent_id,
            database_path=database,
            database_identity=identity,
            source_sha256=source_sha256,
            agent_config_sha256=actual_config_sha256,
        )
    except ResultBindingError:
        raise
    except Exception:
        _refuse()


def assert_v2_result_binding_unchanged(
    binding: V2ResultBinding,
    *,
    stop: float | None = None,
) -> None:
    """Revalidate result mode, owner and any native DB identity after reading."""
    current = read_v2_result_binding(
        binding.ownership,
        binding.agent_id if binding.mode == "native_text" else None,
        stop=stop,
    )
    if (
        current.root != binding.root
        or current.owner_claim != binding.owner_claim
        or current.mode != binding.mode
        or current.source_sha256 != binding.source_sha256
        or current.agent_config_sha256 != binding.agent_config_sha256
        or current.agent_id != binding.agent_id
        or current.database_path != binding.database_path
        or current.database_identity != binding.database_identity
    ):
        _refuse()
    _check_deadline(stop)


def assert_v2_database_unchanged(
    binding: V2ResultBinding,
    *,
    stop: float | None = None,
) -> None:
    """Require and revalidate a native-text database binding."""
    if binding.mode != "native_text" or binding.agent_id is None:
        _refuse()
    assert_v2_result_binding_unchanged(binding, stop=stop)
