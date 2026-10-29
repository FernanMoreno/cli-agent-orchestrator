"""Shared private native sandbox; protocols do not own lifecycle orchestration."""

import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def receipt_from_request(body, *, prompt_prefix="T220 controlled native turn"):
    """Bind the receipt to the last user message, never system/assistant text."""
    users = [m for m in body["messages"] if m.get("role") == "user"]
    assert users
    text = users[-1]["content"]
    if isinstance(text, list):
        text = "\n".join(p["text"] for p in text if p.get("type") == "text")
    assert isinstance(text, str)
    receipts = set(re.findall(r"CAO-TURN-RECEIPT-[0-9a-f]{32}", text))
    assert len(receipts) == 1
    assert prompt_prefix in text
    return receipts.pop()


def isolated_wrapper(root, args, *, gateway, native_path, prepare_environment, validate_args):
    root = Path(root).resolve(strict=True)
    manifest = json.loads((root / "manifest.json").read_text())
    binary = Path(manifest["binary"])
    assert sha(binary) == manifest["binary_sha256"]
    if args == ["--version"]:
        os.execv(str(binary), [str(binary), *args])
    validate_args(args)
    home = Path(os.environ["HOME"]).resolve(strict=True)
    assert home.is_relative_to(root / "case")
    tid = os.environ["CAO_TERMINAL_ID"]
    assert re.fullmatch(r"[a-f0-9]{8}", tid)
    attempts = root / "observations" / tid
    attempts.mkdir(parents=True, mode=0o700, exist_ok=True)
    assert len(list(attempts.glob("attempt-*"))) < 2
    entry = Path(tempfile.mkdtemp(prefix="attempt-", dir=attempts))
    fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
    save(
        entry / "wrapper.json",
        {"pid": os.getpid(), "birth": fields[19], "uid": os.getuid(), "terminal_id": tid},
    )
    gateway = Path(gateway).resolve(strict=True)
    assert sha(gateway) == manifest["gateway_sha256"]
    # Native subprocess sees the private HOME, but cannot see database secrets.
    masks = []
    for key in ("CAO_DATABASE_CONFIG_FILE",):
        if os.environ.get(key) and Path(os.environ[key]).is_file():
            masks += ["--ro-bind", "/dev/null", os.environ[key]]
    if (home / ".cao-postgres").exists():
        masks += ["--tmpfs", str(home / ".cao-postgres")]
    bridge_mount = []
    if manifest.get("profile_case") in {"mcp_execute_authorized", "mcp_execute_denied"}:
        from test.live_provider.native_read_bridge import NATIVE_SOCKET

        state = json.loads((root / "case-result.json").read_text())
        bridge = Path(os.environ["CAO_NATIVE_READ_SOCKET"])
        assert bridge == Path(state["tmux_root"]) / "read.sock"
        info = bridge.lstat()
        assert (
            stat.S_ISSOCK(info.st_mode)
            and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600
        )
        bridge_mount = ["--ro-bind", str(bridge), NATIVE_SOCKET]
    command = [
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--uid",
        "0",
        "--gid",
        "0",
        "--cap-add",
        "CAP_NET_ADMIN",
        "--ro-bind",
        "/",
        "/",
        "--tmpfs",
        "/mnt/c",
        "--ro-bind",
        manifest["repo"],
        manifest["repo"],
        "--tmpfs",
        "/home/felni",
        "--ro-bind",
        "/home/felni/.local/share/uv/python",
        "/home/felni/.local/share/uv/python",
        "--bind",
        str(home),
        str(home),
        *masks,
        "--tmpfs",
        "/tmp",
        "--bind",
        str(entry),
        "/tmp/evidence",
        *bridge_mount,
        "--ro-bind",
        str(gateway),
        "/tmp/gateway.py",
        "--ro-bind",
        str(binary),
        native_path,
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--chdir",
        str(home),
        "--",
        sys.executable,
        "/tmp/gateway.py",
        "backend",
        *args,
    ]
    env = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "TERM": os.environ.get("TERM", "xterm-256color"),
    }
    env["PYTHONPATH"] = manifest["repo"]
    env = prepare_environment(env)
    os.execve("/usr/bin/bwrap", command, env)


def send_turn_once(request, terminal_id, message, record):
    """Bind one admitted input to its prior counter, including native startup turns."""
    before = request("GET", terminal_id)["turn_sequence"]
    assert type(before) is int and before >= 0
    value = request("POST", terminal_id, "/input", params={"message": message})
    record(terminal_id, before, value)  # Preserve failures before evaluating predicates.
    assert value["success"] is True
    assert type(value["turn_sequence"]) is int and value["turn_sequence"] == before + 1
    return value
