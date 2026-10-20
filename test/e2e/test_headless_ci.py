"""End-to-end test for the headless CI runner example.

Invokes ``examples/headless-ci/run.sh`` against the ``ci_developer`` profile
and asserts the script exits 0 (agent reached IDLE/COMPLETED) within the
configured timeout.

Requires:
- ``CAO_RUN_HEADLESS_REMOTE_SMOKE=1`` and a selected provider in the explicit
  auth/model manifest
- ``cao`` and ``uvx`` on PATH
- ``ci_developer`` profile, staged in the managed test HOME

This example deliberately uses ``uvx ...@main`` for its MCP server. Its result
belongs to the separate remote-release smoke lane and does not validate the
current checkout's MCP server.

The CAO server itself is started automatically by the session-scoped
``require_cao_server`` fixture in ``test/e2e/conftest.py`` — no manual
``cao-server`` is needed.

Run:
    CAO_RUN_HEADLESS_REMOTE_SMOKE=1 CAO_HEADLESS_REMOTE_PROVIDER=codex \
      CAO_HEADLESS_REMOTE_REF=<full-commit-id> \
      uv run pytest -m e2e test/e2e/test_headless_ci.py -v
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from test.e2e.conftest import _prepare_live_provider
from test.fixtures.cao_server import CaoServer, _subprocess_env

import frontmatter
import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = REPO_ROOT / "examples" / "headless-ci" / "run.sh"
_REMOTE_SMOKE_OPT_IN = "CAO_RUN_HEADLESS_REMOTE_SMOKE"
_REMOTE_PROVIDER_ENV = "CAO_HEADLESS_REMOTE_PROVIDER"
_REMOTE_REF_ENV = "CAO_HEADLESS_REMOTE_REF"
_REMOTE_PACKAGE = "git+https://github.com/awslabs/cli-agent-orchestrator.git"


def _headless_remote_smoke_requested(environ: dict[str, str] | None = None) -> bool:
    """Keep the upstream ``uvx @main`` smoke separate from local E2E opt-in."""
    values = os.environ if environ is None else environ
    return values.get(_REMOTE_SMOKE_OPT_IN) == "1"


def _validate_headless_remote_ref(value: str) -> str:
    """Require an immutable full Git object ID for the remote-only smoke."""
    reference = value.strip()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", reference) is None:
        raise ValueError(f"{_REMOTE_REF_ENV} must be a full lowercase Git object ID")
    return reference


def _pin_headless_remote_ref(profile_path: Path, reference: str) -> None:
    """Pin staged remote package and remove local PYTHONPATH from that child."""
    reference = _validate_headless_remote_ref(reference)
    profile = frontmatter.loads(profile_path.read_text(encoding="utf-8"))
    mcp_server = profile.metadata.get("mcpServers", {}).get("cao-mcp-server")
    if not isinstance(mcp_server, dict):
        raise ValueError("staged ci_developer profile is missing its CAO MCP server")
    if mcp_server.get("command") != "uvx" or mcp_server.get("args") != [
        "--from",
        _REMOTE_PACKAGE + "@main",
        "cao-mcp-server",
    ]:
        raise ValueError("staged ci_developer profile does not match the reviewed remote command")
    mcp_server["command"] = "env"
    mcp_server["args"] = [
        "-u",
        "PYTHONPATH",
        "uvx",
        "--from",
        _REMOTE_PACKAGE + "@" + reference,
        "cao-mcp-server",
    ]
    profile_path.write_text(frontmatter.dumps(profile), encoding="utf-8")


@pytest.mark.e2e
def test_headless_ci_run_script_exits_clean(
    require_cao_server: CaoServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Separate opt-in smoke for the example that invokes upstream ``uvx @main``."""
    if not _headless_remote_smoke_requested():
        pytest.skip(
            "headless-ci uses a remote uvx @main MCP server; set "
            f"{_REMOTE_SMOKE_OPT_IN}=1 for its separate smoke lane"
        )
    provider = os.environ.get(_REMOTE_PROVIDER_ENV, "").strip()
    if not provider:
        pytest.fail(f"{_REMOTE_PROVIDER_ENV} must select one manifest-authorized provider")
    _prepare_live_provider(
        require_cao_server,
        provider,
        monkeypatch,
        opt_in_env=_REMOTE_SMOKE_OPT_IN,
        profile_names=("ci_developer",),
        install_opencode_profiles=False,
    )
    remote_ref = _validate_headless_remote_ref(os.environ.get(_REMOTE_REF_ENV, ""))
    staged_profile = (
        require_cao_server.home_dir
        / ".aws"
        / "cli-agent-orchestrator"
        / "agent-store"
        / "ci_developer.md"
    )
    _pin_headless_remote_ref(staged_profile, remote_ref)
    if provider == "opencode_cli":
        response = requests.post(
            f"{require_cao_server.url}/agents/profiles/install",
            json={"source": "ci_developer", "provider": provider},
            timeout=30,
        )
        if response.status_code != 200:
            pytest.fail(f"OpenCode profile installation failed: HTTP {response.status_code}")

    assert RUN_SCRIPT.exists(), f"missing {RUN_SCRIPT}"
    assert os.access(RUN_SCRIPT, os.X_OK), f"{RUN_SCRIPT} is not executable"
    assert shutil.which("cao") is not None, "cao CLI not on PATH"
    assert shutil.which("uvx") is not None, "uvx CLI not on PATH"

    cli_home = tmp_path / "headless-cli-home"
    cli_home.mkdir(mode=0o700)
    env = _subprocess_env(
        cli_home,
        require_cao_server.port,
        extra={"CAO_DATABASE_CONFIG_FILE": str(require_cao_server.database_config_path)},
    )
    for private_setting in (
        "CAO_RUN_LIVE_PROVIDER_TESTS",
        "CAO_REAL_PROVIDER_E2E_PROVIDERS",
        "CAO_REAL_PROVIDER_E2E_AUTH_HOME",
        _REMOTE_SMOKE_OPT_IN,
        _REMOTE_PROVIDER_ENV,
        _REMOTE_REF_ENV,
    ):
        env.pop(private_setting, None)
    # The staged profile removes PYTHONPATH from the server-side uvx child.
    # Keep the client-side wrapper isolated too.
    env.pop("PYTHONPATH", None)
    env.setdefault("CAO_CI_TIMEOUT", "180")
    env.setdefault("CAO_CI_POLL_INTERVAL", "3")

    result = subprocess.run(
        [str(RUN_SCRIPT), "Print the literal text HEADLESS_CI_OK and end your turn."],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )

    assert result.returncode == 0, (
        f"run.sh exited {result.returncode}; "
        f"stdout_bytes={len(result.stdout)} stderr_bytes={len(result.stderr)}; "
        "process output omitted"
    )
