"""Real-Herdr coverage for non-native provider status observations.

This uses the repository's credential-free ``mock_cli`` provider, but starts
an isolated Herdr server and reaches it through a real CAO server. It is
opt-in because it starts a local Herdr process and is excluded from the
ordinary test selection.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from test.fixtures.cao_server import (
    CaoServer,
    _fixture_health_timeout,
    _pick_free_port,
    _start_cao_server,
)
from typing import Iterator
from unittest.mock import patch

import pytest
import requests

from cli_agent_orchestrator.backends.herdr_backend import HerdrBackend
from cli_agent_orchestrator.models.terminal import TerminalStatus

pytestmark = pytest.mark.e2e
_RUN_HERDR_E2E = "CAO_RUN_HERDR_GENERIC_STATUS_E2E"
_OMIT_ENV_PREFIXES = (
    "ANTHROPIC_",
    "AWS_",
    "AZURE_",
    "CAO_",
    "GCP_",
    "GITHUB_",
    "GOOGLE_",
    "HERDR_",
    "OPENAI_",
    "XDG_",
)
_OMIT_ENV_MARKERS = (
    "ACCESS_KEY",
    "API_KEY",
    "AUTH",
    "CREDENTIAL",
    "PASSWORD",
    "SECRET",
    "TOKEN",
)


def _isolated_environment(home: Path, config_home: Path) -> dict[str, str]:
    """Keep operator credentials and config paths out of local test processes."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(_OMIT_ENV_PREFIXES)
        and not any(marker in key.upper() for marker in _OMIT_ENV_MARKERS)
    }
    environment.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(config_home),
        }
    )
    return environment


def _session_name(home: Path) -> str:
    """Return the isolated Herdr session name stored with the temporary HOME."""
    return (home / ".t063-herdr-session").read_text(encoding="utf-8").strip()


def _herdr_ready(
    process: subprocess.Popen,
    binary: str,
    session: str,
    env: dict[str, str],
    socket_path: Path,
    log_path: Path,
) -> None:
    """Wait for the isolated socket to answer a real, read-only CLI request."""
    deadline = time.monotonic() + 30.0
    last_error = "Herdr socket has not appeared"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        if socket_path.exists():
            try:
                result = subprocess.run(
                    [binary, "--session", session, "workspace", "list"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                last_error = "Herdr workspace list timed out"
            else:
                if result.returncode == 0:
                    return
                last_error = result.stderr.strip() or f"exit code {result.returncode}"
        time.sleep(0.1)

    log = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    raise RuntimeError(
        f"isolated Herdr session did not become ready: {last_error}; "
        f"server exit={process.poll()}; log tail:\n{log}"
    )


def _stop_process_group(process: subprocess.Popen) -> None:
    """Stop only the Herdr process group created by this fixture."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGTERM)
    if process.poll() is None:
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5.0)
    else:
        # The CLI could exit after handing work to a child in the same group.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)


@pytest.fixture(scope="session")
def cao_server() -> Iterator[CaoServer]:
    """Start CAO and Herdr with separate, disposable state directories."""
    herdr = shutil.which("herdr")
    if herdr is None:
        pytest.skip("Herdr CLI is not installed; real-backend T063 acceptance is unavailable")
    if os.environ.get(_RUN_HERDR_E2E) != "1":
        pytest.skip(f"Set {_RUN_HERDR_E2E}=1 to start the isolated Herdr acceptance server")

    # Herdr stores its socket below XDG_CONFIG_HOME/herdr/sessions/<name>.
    # pytest's default basetemp can exceed Unix sun_path's 108-byte limit.
    home = Path(tempfile.mkdtemp(prefix="cao-herdr-t063-"))
    xdg_config_home = home / ".config"
    session = f"cao-t063-{uuid.uuid4().hex[:12]}"
    (home / ".t063-herdr-session").write_text(session, encoding="utf-8")
    server_env = _isolated_environment(home, xdg_config_home)
    socket_path = xdg_config_home / "herdr" / "sessions" / session / "herdr.sock"
    herdr_log = home / "herdr-server.log"
    herdr_log_handle = herdr_log.open("ab")
    herdr_process: subprocess.Popen | None = None
    cao: CaoServer | None = None
    try:
        herdr_process = subprocess.Popen(
            [herdr, "--session", session, "server"],
            env=server_env,
            stdout=herdr_log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _herdr_ready(herdr_process, herdr, session, server_env, socket_path, herdr_log)
        port = _pick_free_port()
        health_timeout = _fixture_health_timeout()
        with patch.dict(os.environ, server_env, clear=True):
            cao = _start_cao_server(
                home,
                port,
                extra_env={
                    "XDG_CONFIG_HOME": str(xdg_config_home),
                    "CAO_TERMINAL_BACKEND": "herdr",
                    "CAO_HERDR_SESSION": session,
                },
                deadline=health_timeout,
            )
        yield cao
    finally:
        try:
            if cao is not None:
                cao.stop()
        finally:
            try:
                if herdr_process is not None:
                    _stop_process_group(herdr_process)
            finally:
                herdr_log_handle.close()
                shutil.rmtree(home, ignore_errors=True)


def _get_status(server: CaoServer, terminal_id: str) -> str:
    response = requests.get(f"{server.url}/terminals/{terminal_id}", timeout=5.0)
    assert response.status_code == 200, response.text
    return response.json()["status"]


def _wait_for_status(
    server: CaoServer, terminal_id: str, expected: TerminalStatus, timeout: float = 15.0
) -> None:
    deadline = time.monotonic() + timeout
    observed = "unknown"
    while time.monotonic() < deadline:
        observed = _get_status(server, terminal_id)
        if observed == expected.value:
            return
        time.sleep(0.1)
    raise AssertionError(f"expected {expected.value}, last observed {observed}")


def _herdr_snapshot(backend: HerdrBackend) -> dict:
    """Read current Herdr resources, failing on an unavailable or malformed API."""
    result = backend._run_herdr(["api", "snapshot"], check=False)
    assert result.returncode == 0, result.stderr
    data = backend._parse_herdr_json(result.stdout)
    snapshot = data.get("snapshot")
    assert isinstance(snapshot, dict), f"unexpected Herdr snapshot: {data!r}"
    return snapshot


def _wait_for_mock_output(
    backend: HerdrBackend,
    session_name: str,
    window_name: str,
    expected_output: str,
    timeout: float = 15.0,
) -> str:
    """Require mock_cli's exact response to appear in the live pane history."""
    deadline = time.monotonic() + timeout
    observed = ""
    while time.monotonic() < deadline:
        observed = backend.get_non_native_status_observation(session_name, window_name) or ""
        if expected_output in observed:
            return observed
        time.sleep(0.1)
    raise AssertionError(
        f"mock_cli did not emit {expected_output!r} in live Herdr pane history; "
        f"last observation: {observed[-2000:]!r}"
    )


def _wait_for_herdr_cleanup(
    backend: HerdrBackend,
    session_name: str,
    workspace_id: str,
    tab_id: str,
    pane_id: str,
    timeout: float = 15.0,
) -> None:
    """Require the deleted CAO session's Herdr workspace, tab, and pane to be gone."""
    deadline = time.monotonic() + timeout
    last_snapshot: dict = {}
    while time.monotonic() < deadline:
        last_snapshot = _herdr_snapshot(backend)
        workspaces = last_snapshot.get("workspaces", [])
        tabs = last_snapshot.get("tabs", [])
        panes = last_snapshot.get("panes", [])
        workspace_gone = not any(
            ws.get("workspace_id") == workspace_id or ws.get("label") == session_name
            for ws in workspaces
        )
        tab_gone = not any(
            tab.get("tab_id") == tab_id or tab.get("workspace_id") == workspace_id for tab in tabs
        )
        pane_gone = not any(
            pane.get("pane_id") == pane_id or pane.get("tab_id") == tab_id for pane in panes
        )
        if workspace_gone and tab_gone and pane_gone:
            return
        time.sleep(0.1)
    raise AssertionError(
        "Herdr resources survived CAO session deletion: "
        f"session={session_name!r}, workspace_id={workspace_id!r}, "
        f"tab_id={tab_id!r}, pane_id={pane_id!r}; "
        f"last snapshot={last_snapshot!r}"
    )


def test_real_herdr_history_drives_unrecognized_provider_status(cao_server: CaoServer):
    """A real Herdr pane observation reaches the registered provider parser."""
    herdr_session = _session_name(cao_server.home_dir)
    api_session = f"t063-{uuid.uuid4().hex[:10]}"
    terminal_id: str | None = None
    session_deleted = False
    try:
        created = requests.post(
            f"{cao_server.url}/sessions",
            params={
                "provider": "mock_cli",
                "agent_profile": "developer",
                "session_name": api_session,
            },
            timeout=30.0,
        )
        assert created.status_code in (200, 201), created.text
        terminal = created.json()
        terminal_id = terminal["id"]
        api_session = terminal["session_name"]

        _wait_for_status(cao_server, terminal_id, TerminalStatus.IDLE)

        # Verify the real Herdr backend cannot classify this provider natively
        # and can transport its live pane text to the provider-specific parser.
        isolated_env = _isolated_environment(cao_server.home_dir, cao_server.home_dir / ".config")
        with patch.dict(os.environ, isolated_env, clear=True):
            backend = HerdrBackend(herdr_session=herdr_session)
            assert backend.get_native_status(api_session, terminal["name"]) is None
            observation = backend.get_non_native_status_observation(api_session, terminal["name"])
            assert observation is not None and "MockCli ready." in observation
            assert "> MOCK: slept 2s" not in observation

            snapshot = _herdr_snapshot(backend)
            workspaces = [
                ws for ws in snapshot.get("workspaces", []) if ws.get("label") == api_session
            ]
            assert len(workspaces) == 1, f"expected one Herdr workspace: {snapshot!r}"
            workspace_id = workspaces[0]["workspace_id"]
            tabs = [
                tab
                for tab in snapshot.get("tabs", [])
                if tab.get("workspace_id") == workspace_id and tab.get("label") == terminal["name"]
            ]
            assert len(tabs) == 1, f"expected one Herdr tab for terminal: {snapshot!r}"
            tab_id = tabs[0]["tab_id"]
            panes = [pane for pane in snapshot.get("panes", []) if pane.get("tab_id") == tab_id]
            assert len(panes) == 1, f"expected one Herdr pane for terminal: {snapshot!r}"
            pane_id = panes[0]["pane_id"]

        sent = requests.post(
            f"{cao_server.url}/terminals/{terminal_id}/input",
            params={"message": "__mock_sleep_2"},
            timeout=10.0,
        )
        assert sent.status_code == 200, sent.text
        _wait_for_status(cao_server, terminal_id, TerminalStatus.PROCESSING)
        # The API's 200 only means CAO accepted the input. Require the exact
        # mock_cli response from Herdr's live pane history to prove the shell
        # received and handled this input. This is not a durable Work receipt.
        with patch.dict(os.environ, isolated_env, clear=True):
            observed = _wait_for_mock_output(
                backend,
                api_session,
                terminal["name"],
                "> MOCK: slept 2s",
            )
        assert "> MOCK: slept 2s" in observed
        _wait_for_status(cao_server, terminal_id, TerminalStatus.COMPLETED)

        deleted = requests.delete(f"{cao_server.url}/sessions/{api_session}", timeout=15.0)
        assert deleted.status_code == 200, deleted.text
        deletion = deleted.json()
        assert deletion.get("success") is True, deletion
        assert api_session in deletion.get("deleted", []), deletion
        assert deletion.get("errors") == [], deletion
        session_deleted = True

        # CAO's response is not enough: query the live isolated Herdr server
        # until the workspace, tab and pane created above have all disappeared.
        with patch.dict(os.environ, isolated_env, clear=True):
            _wait_for_herdr_cleanup(backend, api_session, workspace_id, tab_id, pane_id)
    finally:
        if terminal_id is not None and not session_deleted:
            with contextlib.suppress(requests.RequestException):
                requests.delete(f"{cao_server.url}/sessions/{api_session}", timeout=5.0)
