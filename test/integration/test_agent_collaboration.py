"""Local admission composition with real HTTP, SQLite and isolated runtime.

No model is called: refused launch paths must fail before creating tmux objects.
Native provider collaboration is a separate opt-in acceptance runner.
"""

import json
import shutil
from test.fixtures.cao_server import _pick_free_port, _start_cao_server

import pytest
import requests


@pytest.mark.skipif(
    shutil.which("claude") is None,
    reason="Claude executable needed for launch precheck; no model call",
)
def test_registered_project_admission_rejects_escape_over_real_http(tmp_path):
    project, outside, home, sockets = [
        tmp_path / name for name in ("project", "outside", "home", "tmux")
    ]
    for path in (project, outside, home, sockets):
        path.mkdir()
    (project / "escape").symlink_to(outside, target_is_directory=True)
    store = home / ".aws/cli-agent-orchestrator/agent-store"
    store.mkdir(parents=True)
    (store / "proof.md").write_text(
        "---\nname: proof\ndescription: deterministic refusal proof\nprovider: claude_code\nrole: reviewer\nallowedTools: [fs_read]\n---\nRead only.\n"
    )
    server = _start_cao_server(
        home,
        _pick_free_port(),
        extra_env={
            "CAO_REGISTERED_PROJECTS": json.dumps([str(project)]),
            "CAO_ENABLE_WORKING_DIRECTORY": "true",
            "TMUX_TMPDIR": str(sockets),
        },
        deadline=90,
    )
    try:
        for path in (outside, project / "escape"):
            response = requests.post(
                server.url + "/sessions",
                params={
                    "provider": "claude_code",
                    "agent_profile": "proof",
                    "working_directory": str(path),
                },
                timeout=15,
            )
            assert response.status_code == 400, response.text
            assert "registered project" in response.text
        assert requests.get(server.url + "/sessions", timeout=10).json() == []
        assert not list(sockets.rglob("default")), "refused launch created a tmux server"
        assert server.db_path.is_file(), "the real application database was not initialized"
    finally:
        server.stop()
