"""Shared fixtures for end-to-end tests.

Local E2E tests require a managed CAO server and tmux. Real-provider cases are
disabled unless explicitly selected in the isolated auth/model manifest; the
legacy lane currently supports Codex, Claude Code, and OpenCode only.

The CAO server is started automatically by the ``cao_server`` fixture from
``test.fixtures.cao_server``. New tests should consume ``cao_server`` /
``cao_terminal`` directly. The ``API_BASE_URL`` patch loop below is a
back-compat shim for the 12 older modules that import the constant at
module top.

Run with: uv run pytest -m e2e test/e2e/ -v
"""

import os
import shutil
import time
from pathlib import Path
from test.fixtures.cao_server import (
    CaoServer,
    LiveProviderAuthorization,
    _authorize_live_provider,
    _patch_api_base_url_for_e2e,
    _stage_live_provider_auth,
)

import pytest
import requests

from cli_agent_orchestrator.constants import API_BASE_URL


@pytest.fixture(scope="session", autouse=True)
def require_cao_server(cao_server: CaoServer):
    """Delegates to the managed ``cao_server`` subprocess fixture.

    Also patches ``API_BASE_URL`` everywhere it has been bound at import
    time by the 12 older e2e modules, so their helper f-strings resolve
    to the dynamically-allocated port. Originals are restored on teardown.
    """
    restore = _patch_api_base_url_for_e2e(cao_server)
    try:
        yield cao_server
    finally:
        restore()


@pytest.fixture(scope="session", autouse=True)
def require_tmux():
    """Skip all E2E tests if tmux is not installed."""
    if not shutil.which("tmux"):
        pytest.skip("tmux not installed")


_LIVE_PROVIDER_PROFILE_NAMES = (
    "developer",
    "reviewer",
    "code_supervisor",
    "analysis_supervisor",
    "data_analyst",
    "report_generator",
)


def _stage_live_provider_profiles(
    server: CaoServer,
    authorization: LiveProviderAuthorization,
    *,
    profile_names: tuple[str, ...] = _LIVE_PROVIDER_PROFILE_NAMES,
    install_opencode_profiles: bool = True,
) -> None:
    """Copy known E2E profiles and pin provider/model in the private store."""
    import frontmatter

    repo_root = Path(__file__).resolve().parents[2]
    example_sources = {
        "analysis_supervisor": repo_root / "examples" / "assign" / "analysis_supervisor.md",
        "data_analyst": repo_root / "examples" / "assign" / "data_analyst.md",
        "report_generator": repo_root / "examples" / "assign" / "report_generator.md",
        "ci_developer": repo_root / "examples" / "headless-ci" / "ci_developer.md",
    }
    store = server.home_dir / ".aws" / "cli-agent-orchestrator" / "agent-store"
    store.mkdir(parents=True, mode=0o700, exist_ok=True)
    store.chmod(0o700)
    for name in profile_names:
        source = example_sources.get(name)
        if source is None:
            source = repo_root / "src" / "cli_agent_orchestrator" / "agent_store" / f"{name}.md"
        if not source.is_file():
            pytest.fail(f"No checked-in source profile exists for live E2E profile {name!r}")
        post = frontmatter.loads(source.read_text(encoding="utf-8"))
        post.metadata["provider"] = authorization.name
        post.metadata["model"] = authorization.model
        target = store / f"{name}.md"
        target.write_text(frontmatter.dumps(post), encoding="utf-8")
        target.chmod(0o600)

        if authorization.name == "opencode_cli" and install_opencode_profiles:
            response = requests.post(
                f"{server.url}/agents/profiles/install",
                json={"source": name, "provider": authorization.name},
                timeout=30,
            )
            if response.status_code != 200:
                pytest.fail(
                    f"OpenCode profile installation failed for {name!r}: "
                    f"HTTP {response.status_code}"
                )


def _prepare_live_provider(
    server: CaoServer,
    provider: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    opt_in_env: str = "CAO_RUN_LIVE_PROVIDER_TESTS",
    profile_names: tuple[str, ...] = _LIVE_PROVIDER_PROFILE_NAMES,
    install_opencode_profiles: bool = True,
) -> LiveProviderAuthorization:
    authorization = _authorize_live_provider(provider, opt_in_env=opt_in_env)
    _stage_live_provider_auth(server.home_dir, authorization)
    _stage_live_provider_profiles(
        server,
        authorization,
        profile_names=profile_names,
        install_opencode_profiles=install_opencode_profiles,
    )
    monkeypatch.setenv(f"CAO_TEST_LIVE_PROVIDER_MODEL_{provider.upper()}", authorization.model)
    return authorization


def _provider_launch_params(provider: str, params: dict[str, object]) -> dict[str, object]:
    """Attach the manifest-selected model to a legacy session API request."""
    result = dict(params)
    model = os.environ.get(f"CAO_TEST_LIVE_PROVIDER_MODEL_{provider.upper()}")
    if model:
        result["model"] = model
    return result


@pytest.fixture()
def require_codex(
    require_cao_server: CaoServer, monkeypatch: pytest.MonkeyPatch
) -> LiveProviderAuthorization:
    """Require manifest auth/model and isolate the Codex child."""
    return _prepare_live_provider(require_cao_server, "codex", monkeypatch)


@pytest.fixture()
def require_claude(
    require_cao_server: CaoServer, monkeypatch: pytest.MonkeyPatch
) -> LiveProviderAuthorization:
    """Require manifest auth/model and isolate the Claude child."""
    return _prepare_live_provider(require_cao_server, "claude_code", monkeypatch)


@pytest.fixture()
def require_kiro():
    """Mark Kiro legacy-provider cases N/A until isolated auth is supported."""
    pytest.skip("provider 'kiro_cli' is unsupported in the isolated live-provider lane")


@pytest.fixture()
def require_kimi():
    """Mark Kimi legacy-provider cases N/A until isolated auth is supported."""
    pytest.skip("provider 'kimi_cli' is unsupported in the isolated live-provider lane")


@pytest.fixture()
def require_gemini():
    """Mark Gemini legacy-provider cases N/A until isolated auth is supported."""
    pytest.skip("provider 'gemini_cli' is unsupported in the isolated live-provider lane")


@pytest.fixture()
def require_copilot():
    """Mark Copilot legacy-provider cases N/A until isolated auth is supported."""
    pytest.skip("provider 'copilot_cli' is unsupported in the isolated live-provider lane")


@pytest.fixture()
def require_opencode(
    require_cao_server: CaoServer, monkeypatch: pytest.MonkeyPatch
) -> LiveProviderAuthorization:
    """Require manifest auth/model and isolate the OpenCode child."""
    return _prepare_live_provider(require_cao_server, "opencode_cli", monkeypatch)


@pytest.fixture()
def require_omp():
    """Mark OMP legacy-provider cases N/A until isolated auth is supported."""
    pytest.skip("provider 'omp' is unsupported in the isolated live-provider lane")


@pytest.fixture()
def require_hermes():
    """Mark Hermes legacy-provider cases N/A until isolated auth is supported."""
    pytest.skip("provider 'hermes' is unsupported in the isolated live-provider lane")


@pytest.fixture()
def require_cursor():
    """Mark Cursor legacy-provider cases N/A until isolated auth is supported."""
    pytest.skip("provider 'cursor_cli' is unsupported in the isolated live-provider lane")


@pytest.fixture()
def require_grok():
    """Mark Grok legacy-provider cases N/A until isolated auth is supported."""
    pytest.skip("provider 'grok_cli' is unsupported in the isolated live-provider lane")


@pytest.fixture()
def require_minimax_code():
    """Mark MiniMax legacy-provider cases N/A until isolated auth is supported."""
    pytest.skip("provider 'mcode' is unsupported in the isolated live-provider lane")


@pytest.fixture()
def require_antigravity():
    """Mark Antigravity cases N/A until isolated auth is supported."""
    pytest.skip("provider 'antigravity_cli' is unsupported in the isolated live-provider lane")


def create_terminal(
    provider: str,
    agent_profile: str,
    session_name: str,
    retries: int = 1,
    retry_delay: float = 30.0,
):
    """Create a CAO session + terminal via the API.

    Returns (terminal_id, actual_session_name).

    If creation fails with a 500 error (typically an init timeout caused by
    API rate limiting), retries up to ``retries`` times with ``retry_delay``
    seconds between attempts. The retry uses a fresh session name to avoid
    conflicts with partially-created resources from the failed attempt.
    """
    last_resp = None
    for attempt in range(1 + retries):
        # Use a fresh session name suffix on retries to avoid collisions
        # with partially-created sessions from failed attempts.
        if attempt > 0:
            import uuid

            retry_suffix = uuid.uuid4().hex[:6]
            attempt_session_name = f"{session_name}-r{retry_suffix}"
            time.sleep(retry_delay)
        else:
            attempt_session_name = session_name

        resp = requests.post(
            f"{API_BASE_URL}/sessions",
            params=_provider_launch_params(
                provider,
                {
                    "provider": provider,
                    "agent_profile": agent_profile,
                    "session_name": attempt_session_name,
                },
            ),
        )
        last_resp = resp
        if resp.status_code in (200, 201):
            data = resp.json()
            return data["id"], data["session_name"]

        # Only retry on server errors (500) — likely rate-limit-induced init timeout
        if resp.status_code != 500 or attempt >= retries:
            break

    if last_resp is None or last_resp.status_code not in (200, 201):
        status = "no response" if last_resp is None else f"HTTP {last_resp.status_code}"
        raise RuntimeError(f"Session creation failed: {status}; response body omitted")


def get_terminal_status(terminal_id: str) -> str:
    """Get live terminal status via provider.get_status()."""
    resp = requests.get(f"{API_BASE_URL}/terminals/{terminal_id}")
    if resp.status_code != 200:
        return "unknown"
    return resp.json().get("status", "unknown")


def wait_for_status(
    terminal_id: str, target: str, timeout: float = 90.0, poll: float = 3.0
) -> bool:
    """Poll terminal status until target is reached or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        status = get_terminal_status(terminal_id)
        if status == target:
            return True
        if status == "error":
            return False
        time.sleep(poll)
    return False


def send_handoff_message(terminal_id: str, message: str, provider: str) -> None:
    """Send a message to the terminal, with [CAO Handoff] prefix for codex."""
    if provider == "codex":
        full_message = (
            "[CAO Handoff] Supervisor terminal ID: test-supervisor-e2e. "
            "This is a blocking handoff \u2014 the orchestrator will automatically "
            "capture your response when you finish. Complete the task and output "
            "your results directly. Do NOT use send_message to notify the supervisor "
            "unless explicitly needed \u2014 just do the work and present your deliverables.\n\n"
            f"{message}"
        )
    else:
        full_message = message

    resp = requests.post(
        f"{API_BASE_URL}/terminals/{terminal_id}/input",
        params={"message": full_message},
    )
    assert (
        resp.status_code == 200
    ), f"Send message failed: HTTP {resp.status_code}; response body omitted"


def extract_output(terminal_id: str) -> str:
    """Extract the last assistant message from the terminal."""
    resp = requests.get(
        f"{API_BASE_URL}/terminals/{terminal_id}/output",
        params={"mode": "last"},
    )
    assert (
        resp.status_code == 200
    ), f"Output extraction failed: HTTP {resp.status_code}; response body omitted"
    return resp.json().get("output", "")


def cleanup_terminal(terminal_id: str, session_name: str) -> None:
    """Send /exit and delete the session."""
    try:
        requests.post(f"{API_BASE_URL}/terminals/{terminal_id}/exit")
    except Exception:
        pass
    time.sleep(2)
    try:
        requests.delete(f"{API_BASE_URL}/sessions/{session_name}")
    except Exception:
        pass
