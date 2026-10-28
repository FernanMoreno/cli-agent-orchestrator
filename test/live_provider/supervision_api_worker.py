"""Opt-in production API worker with content-free target/action observations."""

import json
import os
import re
import stat
import threading
from pathlib import Path

_ROUTE = re.compile(r"/terminals/([a-f0-9]{8})(?:/turn/(verify|cancel))?")


def _private_json(path, value):
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w"
    ) as out:
        json.dump(value, out)


def read_observations(path):
    path = Path(path)
    if (
        path.is_symlink()
        or stat.S_IMODE(path.stat().st_mode) != 0o600
        or path.stat().st_size > 65536
    ):
        raise RuntimeError("invalid_supervision_audit")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if len(rows) > 256:
        raise RuntimeError("supervision_audit_limit")
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"method", "terminal_id", "action", "status"}
            or type(row["terminal_id"]) is not str
            or not re.fullmatch(r"[a-f0-9]{8}", row["terminal_id"])
            or type(row["status"]) is not int
            or not 0 <= row["status"] <= 599
            or (row["method"], row["action"])
            not in {("GET", "metadata"), ("POST", "verify"), ("POST", "cancel")}
        ):
            raise RuntimeError("invalid_supervision_observation")
    return rows


def install_observer(app, root, path):
    root, path = Path(root), Path(path)
    if (
        not root.is_absolute()
        or root.is_symlink()
        or stat.S_IMODE(root.stat().st_mode) != 0o700
        or path.parent != root
        or path.is_symlink()
    ):
        raise RuntimeError("invalid_supervision_root")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w"):
        pass
    lock = threading.Lock()

    def record(method, target, action, status):
        wire = (
            json.dumps(
                {"method": method, "terminal_id": target, "action": action, "status": status},
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        with lock:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
            try:
                if os.write(fd, wire) != len(wire):
                    raise RuntimeError("supervision_audit_write_failed")
            finally:
                os.close(fd)

    @app.middleware("http")
    async def observe(request, call_next):
        match = _ROUTE.fullmatch(request.url.path)
        if match and match[2] and request.method == "POST":
            record("POST", match[1], match[2], 0)
        response = await call_next(request)
        if match and not match[2] and request.method == "GET":
            record("GET", match[1], "metadata", response.status_code)
        return response

    _private_json(root / "audit-ready.json", {"schema": "caos.supervision-observer.v1"})


def main():
    if os.environ.get("CAO_SPEC025_SUPERVISION_ENABLE") != "1":
        raise RuntimeError("supervision_worker_disabled")
    root = Path(os.environ["CAO_SPEC025_SUPERVISION_ROOT"])
    path = Path(os.environ["CAO_SPEC025_SUPERVISION_AUDIT"])
    from cli_agent_orchestrator.api import main as api

    install_observer(api.app, root, path)
    api.main()


if __name__ == "__main__":
    main()
