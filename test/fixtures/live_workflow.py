"""Opt-in workflow acceptance with an isolated subscription-authenticated provider."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from test.fixtures.cao_server import CaoServer, _pick_free_port, _start_cao_server
from typing import Iterator

import pytest


@pytest.fixture
def live_workflow_server(tmp_path: Path) -> Iterator[CaoServer]:
    if os.environ.get("CAO_RUN_LIVE_PROVIDER_TESTS") != "1":
        pytest.skip("Set CAO_RUN_LIVE_PROVIDER_TESTS=1 for real workflow acceptance")
    provider = os.environ.get("CAO_LIVE_WORKFLOW_PROVIDER", "claude_code")
    choices = {
        "claude_code": (
            "claude",
            "claude-haiku-4-5",
            (".claude/.credentials.json", ".claude.json"),
        ),
        "codex": ("codex", "gpt-6-luna", (".codex/auth.json",)),
        "opencode_cli": (
            "opencode",
            "opencode-go/longcat-2.5-preview-free",
            (".local/share/opencode/auth.json",),
        ),
    }
    assert provider in choices, "Select an explicitly supported live provider"
    binary, model, auth_files = choices[provider]
    assert shutil.which(binary) and shutil.which("tmux"), "Provider CLI and tmux are required"
    auth_home = Path(os.environ.get("CAO_REAL_PROVIDER_E2E_AUTH_HOME", str(Path.home())))
    home = tmp_path / "live-workflow-home"
    home.mkdir(mode=0o700)
    if provider == "codex":
        auth = json.loads((auth_home / ".codex/auth.json").read_text(encoding="utf-8"))
        assert auth.get("auth_mode") == "chatgpt" and not auth.get(
            "OPENAI_API_KEY"
        ), "Zero-extra-cost acceptance requires a ChatGPT subscription login"
    assert all((auth_home / relative).is_file() for relative in auth_files)
    # Copy only the documented login records; never mutate the operator's profile.
    for relative in auth_files:
        source = auth_home / relative
        assert source.is_file(), f"Required subscription login record absent: {relative}"
        target = home / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source, target)
        target.chmod(0o600)
    cao_home = home / ".aws" / "cli-agent-orchestrator"
    store = cao_home / "agent-store"
    store.mkdir(parents=True, mode=0o700)
    for name in ("reviewer", "live-workflow"):
        (store / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: Live workflow acceptance\n"
            f"provider: {provider}\nmodel: {model}\nrole: reviewer\n---\n"
            "Answer briefly. Always include the requested CAO-TURN-RECEIPT token verbatim as "
            "the final output line after completing the task, even when asked for output only. "
            "Do not edit files, delegate, or start background work.\n",
            encoding="utf-8",
        )
    # Remove paid/API authentication inputs inherited by the managed subprocess.
    isolated = {
        key: ""
        for key in os.environ
        if key.endswith(("API_KEY", "API_TOKEN", "BASE_URL"))
        or key in {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN", "OPENAI_ACCESS_TOKEN"}
    }
    isolated.update(
        {
            "CAO_HOME_DIR": str(cao_home),
            "CAO_TERMINAL_BACKEND": "tmux",
            "CODEX_HOME": str(home / ".codex"),
            "CLAUDE_CONFIG_DIR": str(home / ".claude"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "OPENCODE_CONFIG": "",
            "OPENCODE_CONFIG_DIR": str(home / ".config/opencode"),
        }
    )
    tmux_tmp = Path(tempfile.mkdtemp(prefix="cw-"))
    isolated["TMUX_TMPDIR"] = str(tmux_tmp)
    server = None
    try:
        server = _start_cao_server(home, _pick_free_port(), extra_env=isolated)
        yield server
    finally:
        if server is not None:
            server.stop()
        subprocess.run(
            ["tmux", "-S", str(tmux_tmp / f"tmux-{os.getuid()}" / "default"), "kill-server"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        shutil.rmtree(tmux_tmp)
        for relative in auth_files:
            (home / relative).unlink(missing_ok=True)
