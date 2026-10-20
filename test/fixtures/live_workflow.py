"""Opt-in workflow acceptance with an isolated subscription-authenticated provider."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from test.fixtures.cao_server import (
    CaoServer,
    _authorize_live_provider,
    _pick_free_port,
    _stage_live_provider_auth,
    _start_cao_server,
)
from typing import Iterator

import pytest


@pytest.fixture
def live_workflow_server(tmp_path: Path) -> Iterator[CaoServer]:
    provider = os.environ.get("CAO_LIVE_WORKFLOW_PROVIDER", "claude_code")
    authorization = _authorize_live_provider(provider)
    auth_files = authorization.auth_files
    auth_home = authorization.auth_home
    assert shutil.which("tmux"), "tmux is required"
    home = tmp_path / "live-workflow-home"
    home.mkdir(mode=0o700)
    if provider == "codex":
        auth = json.loads((auth_home / ".codex/auth.json").read_text(encoding="utf-8"))
        assert auth.get("auth_mode") == "chatgpt" and not auth.get(
            "OPENAI_API_KEY"
        ), "Zero-extra-cost acceptance requires a ChatGPT subscription login"
    assert all((auth_home / relative).is_file() for relative in auth_files)
    # Copy only manifest-listed login records; never mutate the auth source.
    _stage_live_provider_auth(home, authorization)
    cao_home = home / ".aws" / "cli-agent-orchestrator"
    store = cao_home / "agent-store"
    store.mkdir(parents=True, mode=0o700)
    for name in ("reviewer", "live-workflow"):
        (store / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: Live workflow acceptance\n"
            f"provider: {provider}\nmodel: {authorization.model}\nrole: reviewer\n---\n"
            "Answer briefly. Always include the requested CAO-TURN-RECEIPT token verbatim as "
            "the final output line after completing the task, even when asked for output only. "
            "Do not edit files, delegate, or start background work.\n",
            encoding="utf-8",
        )
    # ``_start_cao_server`` removes inherited provider credentials and uses
    # the private HOME above; don't pass secret-shaped placeholder values.
    isolated = {
        "CAO_HOME_DIR": str(cao_home),
        "CAO_TERMINAL_BACKEND": "tmux",
        "CODEX_HOME": str(home / ".codex"),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "OPENCODE_CONFIG": "",
        "OPENCODE_CONFIG_DIR": str(home / ".config/opencode"),
    }
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
