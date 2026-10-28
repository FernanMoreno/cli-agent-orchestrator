"""Offline admission of a frozen Linux Codex0.164 package; never starts a CLI."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

_ERROR = "codex_runtime_snapshot_unavailable"
_REQUIRED = frozenset(
    {
        "codex-package.json",
        "bin/codex",
        "bin/codex-code-mode-host",
        "codex-path/rg",
        "codex-resources/bwrap",
    }
)
_EXECUTABLES = _REQUIRED - {"codex-package.json"}
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _refuse() -> None:
    raise RuntimeError(_ERROR)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            _refuse()
        result[key] = value
    return result


def _metadata(path: Path, *, private: bool = False) -> os.stat_result:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or info.st_mode & 0o022
        or (private and stat.S_IMODE(info.st_mode) != 0o600)
    ):
        _refuse()
    return info


def _read(path: Path, *, json_file: bool = False) -> bytes | str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > (
            65536 if json_file else 512 * 1024 * 1024
        ):
            _refuse()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            if json_file:
                payload = stream.read(65537)
                if len(payload) > 65536:
                    _refuse()
                return payload
            digest = hashlib.sha256()
            observed = 0
            for chunk in iter(lambda: stream.read(65536), b""):
                observed += len(chunk)
                if observed > 512 * 1024 * 1024:
                    _refuse()
                digest.update(chunk)
            after = os.fstat(descriptor)
            if observed != info.st_size or (after.st_size, after.st_mtime_ns) != (
                info.st_size,
                info.st_mtime_ns,
            ):
                _refuse()
            return digest.hexdigest()
    finally:
        os.close(descriptor)


def _json(payload: bytes) -> Any:
    return json.loads(payload, object_pairs_hook=_pairs, parse_constant=lambda _: _refuse())


def verify_codex_runtime_snapshot(
    binary: Path, manifest: Path, *, expected_manifest_sha256: str
) -> dict[str, Any]:
    """Reject loose binaries and asset drift before credential or service setup.

    The caller freezes the manifest independently from the selected snapshot.
    Hashes are not derived from a receipt that the snapshot itself can rewrite.
    This admission is intentionally scoped to the pinned Linux package version.
    """
    try:
        if (
            not isinstance(expected_manifest_sha256, str)
            or _HASH.fullmatch(expected_manifest_sha256) is None
            or not manifest.is_absolute()
            or manifest.resolve(strict=True) != manifest
            or os.environ.get("CODEX_CODE_MODE_HOST_PATH") is not None
        ):
            _refuse()
        _metadata(manifest, private=True)
        payload = _read(manifest, json_file=True)
        if hashlib.sha256(payload).hexdigest() != expected_manifest_sha256:
            _refuse()
        data = _json(payload)
        if (
            not isinstance(data, dict)
            or set(data) != {"schema", "root", "assets"}
            or data["schema"] != "caos.codex.runtime-snapshot.v1"
        ):
            _refuse()
        root = Path(data["root"])
        if (
            not root.is_absolute()
            or root.resolve(strict=True) != root
            or binary != root / "bin/codex"
        ):
            _refuse()
        info = root.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            _refuse()
        assets = data["assets"]
        if (
            not isinstance(assets, dict)
            or not 5 <= len(assets) <= 64
            or not _REQUIRED <= assets.keys()
        ):
            _refuse()
        if len({name.casefold() for name in assets}) != len(assets):
            _refuse()
        for name, expected in assets.items():
            path = PurePosixPath(name)
            if (
                not name
                or len(name) > 255
                or "\\" in name
                or path.is_absolute()
                or ".." in path.parts
                or str(path) != name
                or (
                    name != "codex-package.json"
                    and path.parts[0] not in {"bin", "codex-resources", "codex-path"}
                )
                or not isinstance(expected, str)
                or _HASH.fullmatch(expected) is None
            ):
                _refuse()
        inventory = {"codex-package.json"}
        entries, total = 0, 0
        for directory in ("bin", "codex-resources", "codex-path"):
            folder = root / directory
            if folder.is_symlink() or not folder.is_dir():
                _refuse()
            for path in folder.rglob("*"):
                entries += 1
                if entries > 256 or path.is_symlink():
                    _refuse()
                if path.is_dir():
                    continue
                total += _metadata(path).st_size
                if total > 1024 * 1024 * 1024:
                    _refuse()
                inventory.add(path.relative_to(root).as_posix())
        if inventory != set(assets):
            _refuse()
        for name, expected in assets.items():
            path = root / name
            info = _metadata(path)
            if name in _EXECUTABLES and not info.st_mode & 0o111:
                _refuse()
            if _read(path) != expected:
                _refuse()
        metadata = _json(_read(root / "codex-package.json", json_file=True))
        if (
            not isinstance(metadata, dict)
            or type(metadata.get("layoutVersion")) is not int
            or metadata
            != {
                "layoutVersion": 1,
                "version": "0.164.0",
                "target": "x86_64-unknown-linux-musl",
                "variant": "codex",
                "entrypoint": "bin/codex",
                "resourcesDir": "codex-resources",
                "pathDir": "codex-path",
            }
        ):
            _refuse()
        return {
            "version": "0.164.0",
            "asset_count": len(assets),
            "code_mode_host": str(root / "bin/codex-code-mode-host"),
        }
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        raise RuntimeError(_ERROR) from None
