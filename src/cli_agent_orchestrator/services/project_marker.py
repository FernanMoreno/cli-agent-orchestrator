"""Opt-in memory identity continuity; marker bytes confer no Work authority."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from pathlib import Path

from sqlalchemy.orm import Session

from cli_agent_orchestrator.clients import database


def enabled() -> bool:
    raw = os.environ.get("CAO_MEMORY_PROJECT_MARKER")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes"}
    try:
        from cli_agent_orchestrator.services.settings_service import get_memory_settings

        return get_memory_settings().get("project_marker", False) is True
    except Exception:
        return False


def _read(directory_fd):
    try:
        descriptor = os.open(
            "project_id", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
        )
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024:
            raise ValueError("invalid marker")
        value = json.loads(stream.read(1025))
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "project_id", "nonce"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or not isinstance(value["project_id"], str)
        or not re.fullmatch(r"[0-9a-f]{12}", value["project_id"])
        or not isinstance(value["nonce"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", value["nonce"])
    ):
        raise ValueError("invalid marker")
    return value


def _write(directory_fd, payload, *, replace=False):
    temporary = ".project-id-" + secrets.token_hex(12)
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True))
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, "project_id", src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        else:
            os.link(
                temporary,
                "project_id",
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def resolve(cwd: Path) -> str | None:
    if not enabled():
        return None
    from cli_agent_orchestrator import constants
    from cli_agent_orchestrator.services.work_terminal import terminal_dispatch_lock

    try:
        root = cwd.resolve(strict=True)
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode):
            return None
        with terminal_dispatch_lock(constants.DATABASE_FILE, "memory-project-marker"):
            directory = root / ".cao"
            if directory.exists() and (directory.is_symlink() or not directory.is_dir()):
                return None
            with database.SessionLocal() as db:
                # Check actual registry availability before any filesystem write.
                db.query(database.ProjectMarkerModel).limit(1).all()
                if not directory.exists():
                    directory.mkdir(mode=0o700)
                directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    return _resolve_locked(db, root, info, directory_fd)
                finally:
                    os.close(directory_fd)
    except Exception:
        return None


def _resolve_locked(db: Session, root: Path, info: os.stat_result, directory_fd: int) -> str | None:
    payload = _read(directory_fd)
    if payload is not None:
        row = (
            db.query(database.ProjectMarkerModel)
            .filter_by(project_id=payload["project_id"])
            .first()
        )
        if row is None or row.nonce != payload["nonce"]:
            return None
        if (row.device, row.inode) == (info.st_dev, info.st_ino):
            row.realpath = str(root)
            db.commit()
            return row.project_id
        if row.realpath != str(root) and not Path(row.realpath).exists():
            # A genuine move may cross filesystems. The private known
            # nonce links memory history; target/grant authority remains
            # physical and is revalidated separately by Work.
            row.realpath = str(root)
            row.device = info.st_dev
            row.inode = info.st_ino
            db.commit()
            return row.project_id
        # Surviving copied directory receives a new memory identity.
        if row.realpath == str(root):
            return None
    nonce = secrets.token_hex(16)
    identity = hashlib.sha256((str(root) + nonce).encode()).hexdigest()[:12]
    new = {"version": 1, "project_id": identity, "nonce": nonce}
    _write(directory_fd, new, replace=payload is not None)
    db.add(
        database.ProjectMarkerModel(
            project_id=identity,
            nonce=nonce,
            realpath=str(root),
            device=info.st_dev,
            inode=info.st_ino,
        )
    )
    db.commit()
    return identity
