"""Self-tests for the managed cao-server fixtures.

Run with: ``uv run pytest -m e2e test/fixtures/test_cao_server.py -v``.

Each test owns its own lifecycle except where it depends on the session
fixtures (``cao_server``, ``cao_server_with_auth``). The parallelism and
idempotency tests spawn ad-hoc cao-server instances via ``_start_cao_server``
so they don't disturb the session-scoped server.
"""

from __future__ import annotations

import builtins
import concurrent.futures
import contextlib
import signal
import subprocess
import test.fixtures.cao_server as cao_server_module
import time
from pathlib import Path
from test.conftest import mint_test_token
from test.fixtures.cao_server import (
    AuthCaoServer,
    CaoServer,
    _fixture_health_timeout,
    _JWKSServer,
    _pick_free_port,
    _seed_omp_e2e_state,
    _session_rsa_keys,
    _start_cao_server,
    _wait_for_health,
)
from unittest.mock import Mock

import pytest
import requests

pytestmark = pytest.mark.e2e


def test_fixture_health_timeout_uses_default_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CAO_TEST_SERVER_HEALTH_TIMEOUT", raising=False)

    assert _fixture_health_timeout() == 30.0


@pytest.mark.parametrize("value", ("not-a-number", "0", "120.1"))
def test_fixture_health_timeout_rejects_invalid_override(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("CAO_TEST_SERVER_HEALTH_TIMEOUT", value)

    with pytest.raises(ValueError, match="CAO_TEST_SERVER_HEALTH_TIMEOUT"):
        _fixture_health_timeout()


def test_fixture_health_timeout_accepts_bounded_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAO_TEST_SERVER_HEALTH_TIMEOUT", "60")

    assert _fixture_health_timeout() == 60.0


def test_health_wait_allows_a_bounded_localhost_read_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A slow cold localhost response is not a failed CAO startup."""
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"status": "ok"}
    request = Mock(return_value=response)
    monkeypatch.setattr("test.fixtures.cao_server.requests.get", request)

    process = Mock()
    process.poll.return_value = None
    _wait_for_health(
        "http://127.0.0.1:45678",
        deadline=1.0,
        process=process,
        log_path=tmp_path / "server.log",
    )

    timeout = request.call_args.kwargs["timeout"]
    assert isinstance(timeout, tuple)
    assert timeout[0] <= 0.5
    assert 0 < timeout[1] <= 2.0


# ---------------------------------------------------------------------------
# cao_server (session) — basic shape
# ---------------------------------------------------------------------------


def test_health_reachable(cao_server: CaoServer) -> None:
    resp = requests.get(f"{cao_server.url}/health", timeout=2.0)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "cli-agent-orchestrator"


def test_url_uses_dynamic_port(cao_server: CaoServer) -> None:
    assert cao_server.port != 9889, "fixture must allocate a free port, not the default"
    assert f":{cao_server.port}" in cao_server.url
    assert cao_server.url.startswith("http://127.0.0.1:")


def test_home_isolated(cao_server: CaoServer) -> None:
    """Subprocess writes land under the redirected HOME, not the dev's real one."""
    cao_root = cao_server.home_dir / ".aws" / "cli-agent-orchestrator"
    assert cao_root.exists(), f"subprocess never wrote anything under {cao_root}"
    # The logs subdir is created during lifespan setup_logging().
    assert (cao_root / "logs").exists()


def test_omp_e2e_state_is_non_secret_and_complete(cao_server: CaoServer) -> None:
    home = cao_server.home_dir
    assert (home / ".omp" / "agent" / "config.yml").read_text(
        encoding="utf-8"
    ) == "setupVersion: 1\n"
    store = home / ".aws" / "cli-agent-orchestrator" / "agent-store"
    for name in ("data_analyst", "report_generator", "analysis_supervisor"):
        assert (store / f"{name}.md").exists()


def test_omp_e2e_seed_is_idempotent(tmp_path: Path) -> None:
    _seed_omp_e2e_state(tmp_path)
    _seed_omp_e2e_state(tmp_path)

    assert (tmp_path / ".omp" / "agent" / "config.yml").read_text(
        encoding="utf-8"
    ) == "setupVersion: 1\n"


def test_log_file_populated(cao_server: CaoServer) -> None:
    assert cao_server.log_path.exists()
    content = cao_server.log_path.read_text(errors="replace")
    # uvicorn or our logger prints something during startup. Don't pin on a
    # specific banner — just confirm bytes flowed.
    assert content.strip(), "subprocess produced no stdout/stderr output"


# ---------------------------------------------------------------------------
# Ad-hoc server (idempotency, parallelism, failure-diagnostics) — uses
# _start_cao_server directly so we don't kill the session fixture.
# ---------------------------------------------------------------------------


def test_stop_is_idempotent(tmp_path_factory: pytest.TempPathFactory) -> None:
    home = tmp_path_factory.mktemp("cao_idempotent")
    server = _start_cao_server(home, _pick_free_port())
    try:
        server.stop()
        server.stop()  # second call must not raise
    finally:
        with contextlib.suppress(Exception):
            server.stop()


def test_parallel_ports_distinct(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Two concurrent _start_cao_server calls get distinct ports and both work."""
    homes = [
        tmp_path_factory.mktemp("cao_par_a"),
        tmp_path_factory.mktemp("cao_par_b"),
    ]

    def _spawn(home: Path) -> CaoServer:
        return _start_cao_server(home, _pick_free_port())

    servers: list[CaoServer] = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_spawn, h) for h in homes]
            servers = [f.result() for f in futures]
        a, b = servers
        assert a.port != b.port, "concurrent allocations collided on the same port"
        for srv in servers:
            r = requests.get(f"{srv.url}/health", timeout=2.0)
            assert r.status_code == 200
    finally:
        for srv in servers:
            with contextlib.suppress(Exception):
                srv.stop()


@pytest.mark.offline_database
def test_health_early_exit_raises_with_safe_log_diagnostic(tmp_path: Path) -> None:
    """A startup failure exposes status and size, not private log contents."""
    log_sentinel = "private-early-exit-log-sentinel"
    log_path = tmp_path / "server.log"
    log_path.write_text(log_sentinel, encoding="utf-8")
    process = Mock()
    process.poll.return_value = 17
    process.returncode = 17

    with pytest.raises(RuntimeError, match="exited with code 17") as excinfo:
        _wait_for_health(
            "http://127.0.0.1:12345",
            deadline=4.0,
            process=process,
            log_path=log_path,
        )
    msg = str(excinfo.value)
    assert "log_bytes=" in msg
    assert log_sentinel not in msg
    assert log_path.read_text(encoding="utf-8") == log_sentinel


def test_startup_failure_raises_with_safe_log_diagnostic(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """An invalid import-time port setting fails through the real child lifecycle."""
    home = tmp_path_factory.mktemp("cao_crash")
    with pytest.raises(RuntimeError) as excinfo:
        _start_cao_server(
            home,
            _pick_free_port(),
            extra_env={"CAO_API_PORT": "not-an-int"},
            deadline=4.0,
        )

    message = str(excinfo.value)
    assert "exited with code" in message
    assert "log_bytes=" in message
    assert "not-an-int" not in message
    assert "Traceback" not in message

    private_log = (home / "server.log").read_text(encoding="utf-8", errors="replace")
    assert "invalid literal for int()" in private_log
    assert "not-an-int" in private_log


class _PersistentFakeProcess:
    """Popen substitute that remains alive until the test releases it."""

    pid = 43210

    def __init__(self) -> None:
        self.alive = True
        self.wait_calls = 0

    @property
    def returncode(self) -> int | None:
        return None if self.alive else 0

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if self.alive:
            raise subprocess.TimeoutExpired("fake-cao-server", timeout)
        return 0


class _FakeTargetContext:
    def __init__(self, targets: object) -> None:
        self.targets = targets
        self.enter_calls = 0
        self.exit_calls = 0

    def __enter__(self) -> object:
        self.enter_calls += 1
        return self.targets

    def __exit__(self, *exc_info: object) -> bool:
        self.exit_calls += 1
        return False


def _patch_fake_server_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    health_error: BaseException | None = None,
) -> tuple[
    _PersistentFakeProcess,
    _FakeTargetContext,
    Path,
    list[object],
    list[tuple[int, int]],
    list[object],
]:
    """Make _start_cao_server fully offline while retaining lifecycle effects."""
    runtime_config = tmp_path / "private-runtime-targets.json"
    runtime_config.write_text("{}", encoding="utf-8")
    targets = type("Targets", (), {"config_file": runtime_config, "main": object()})()
    target_context = _FakeTargetContext(targets)
    process = _PersistentFakeProcess()
    popen_calls: list[object] = []
    signals: list[tuple[int, int]] = []
    opened_logs: list[object] = []
    original_open = builtins.open

    def tracked_open(file: object, *args: object, **kwargs: object) -> object:
        handle = original_open(file, *args, **kwargs)
        if Path(file) == tmp_path / "server.log":
            opened_logs.append(handle)
        return handle

    def fake_popen(*args: object, **kwargs: object) -> _PersistentFakeProcess:
        popen_calls.append((args, kwargs))
        return process

    def fake_health(*args: object, **kwargs: object) -> None:
        if health_error is not None:
            raise health_error

    monkeypatch.setattr(cao_server_module, "_seed_packaged_skills", lambda *_: None)
    monkeypatch.setattr(cao_server_module, "_seed_omp_e2e_state", lambda *_: None)
    monkeypatch.setattr(cao_server_module, "_subprocess_env", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        cao_server_module,
        "_runtime_only_database_targets",
        lambda *_: (targets, runtime_config),
    )
    monkeypatch.setattr(cao_server_module, "_wait_for_health", fake_health)
    monkeypatch.setattr(cao_server_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cao_server_module.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(
        "test.postgres.fixtures.isolated_live_targets",
        lambda *_args, **_kwargs: target_context,
    )
    monkeypatch.setattr(cao_server_module, "open", tracked_open, raising=False)
    return process, target_context, runtime_config, popen_calls, signals, opened_logs


@pytest.mark.offline_database
def test_terminate_keeps_retry_state_and_log_open_until_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = _PersistentFakeProcess()
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        cao_server_module.os,
        "killpg",
        lambda pid, sig: signals.append((pid, sig)),
    )
    log_handle = (tmp_path / "server.log").open("ab")
    stopped = [False]

    try:
        with pytest.raises(RuntimeError, match="did not stop"):
            cao_server_module._terminate(process, log_handle, stopped)

        assert stopped == [False]
        assert not log_handle.closed
        assert process.wait_calls == 2

        process.alive = False
        cao_server_module._terminate(process, log_handle, stopped)
        assert stopped == [True]
        assert log_handle.closed
        assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]

        cao_server_module._terminate(process, log_handle, stopped)
        assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
    finally:
        process.alive = False
        if not log_handle.closed:
            log_handle.close()


@pytest.mark.offline_database
def test_stop_retains_owned_resources_until_process_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (
        process,
        target_context,
        runtime_config,
        popen_calls,
        signals,
        opened_logs,
    ) = _patch_fake_server_start(monkeypatch, tmp_path)
    server = _start_cao_server(tmp_path, 45679)
    try:
        args, kwargs = popen_calls[0]
        assert kwargs["start_new_session"] is True
        assert args

        with pytest.raises(RuntimeError, match="did not stop"):
            server.stop()

        assert process.alive
        assert process.wait_calls == 2
        assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
        assert target_context.exit_calls == 0
        assert runtime_config.exists()
        assert server in cao_server_module._LIVE_SERVERS
        assert len(opened_logs) == 1 and not opened_logs[0].closed

        process.alive = False
        server.stop()
        assert target_context.exit_calls == 1
        assert not runtime_config.exists()
        assert server not in cao_server_module._LIVE_SERVERS
        assert opened_logs[0].closed

        server.stop()
        assert target_context.exit_calls == 1
        assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
    finally:
        process.alive = False
        server.stop()


@pytest.mark.offline_database
def test_startup_failure_retains_cleanup_owner_if_child_is_still_alive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    startup_error = RuntimeError("fake startup failure")
    (
        process,
        target_context,
        runtime_config,
        popen_calls,
        signals,
        opened_logs,
    ) = _patch_fake_server_start(
        monkeypatch,
        tmp_path,
        health_error=startup_error,
    )

    try:
        with pytest.raises(RuntimeError, match="fake startup failure") as excinfo:
            _start_cao_server(tmp_path, 45680)

        assert excinfo.value is startup_error
        assert excinfo.value.__cause__ is not None
        assert str(excinfo.value.__cause__) == (
            "cao-server process did not stop; owned resources retained for retry"
        )
        assert "cao-server startup cleanup incomplete; see chained cause" in getattr(
            excinfo.value, "__notes__", []
        )
        assert popen_calls
        assert popen_calls[0][1]["start_new_session"] is True
        assert process.alive
        assert process.wait_calls == 2
        assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
        assert target_context.exit_calls == 0
        assert runtime_config.exists()
        assert len(opened_logs) == 1 and not opened_logs[0].closed

        owners = [
            server for server in cao_server_module._LIVE_SERVERS if server.home_dir == tmp_path
        ]
        assert len(owners) == 1
        process.alive = False
        owners[0].stop()
        assert target_context.exit_calls == 1
        assert not runtime_config.exists()
        assert owners[0] not in cao_server_module._LIVE_SERVERS
        assert opened_logs[0].closed
    finally:
        process.alive = False
        for owner in tuple(cao_server_module._LIVE_SERVERS):
            if owner.home_dir == tmp_path:
                owner.stop()


# ---------------------------------------------------------------------------
# JWKS server — direct unit
# ---------------------------------------------------------------------------


def test_jwks_server_returns_expected_key() -> None:
    _, public_jwk = _session_rsa_keys()
    server = _JWKSServer(public_jwk)
    server.start()
    try:
        resp = requests.get(server.url, timeout=2.0)
        assert resp.status_code == 200
        body = resp.json()
        assert "keys" in body
        assert len(body["keys"]) == 1
        assert body["keys"][0]["kid"] == "test-kid"
        assert body["keys"][0]["kty"] == "RSA"
    finally:
        server.stop()


def test_jwks_server_stop_is_idempotent() -> None:
    _, public_jwk = _session_rsa_keys()
    server = _JWKSServer(public_jwk)
    server.start()
    server.stop()
    server.stop()  # second call must not raise


def test_jwks_server_404_on_other_paths() -> None:
    _, public_jwk = _session_rsa_keys()
    server = _JWKSServer(public_jwk)
    server.start()
    try:
        base = server.url.rsplit("/", 1)[0]
        resp = requests.get(f"{base}/some-other-path", timeout=2.0)
        assert resp.status_code == 404
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# cao_server_with_auth — subprocess sees real Auth0 enforcement
# ---------------------------------------------------------------------------


def test_auth_rejects_no_token(cao_server_with_auth: AuthCaoServer) -> None:
    """Subprocess with AUTH0_DOMAIN set must reject unauthenticated mutations.

    Targets a write-scoped endpoint (DELETE /sessions/...) which is gated
    on cao:admin. With no Authorization header the dependency chain stops
    at 401 before reaching the not-found check.
    """
    resp = requests.delete(
        f"{cao_server_with_auth.server.url}/sessions/nonexistent-test",
        timeout=2.0,
    )
    assert (
        resp.status_code == 401
    ), f"expected 401 with no Authorization header, got HTTP {resp.status_code}"


def test_auth_accepts_valid_token(cao_server_with_auth: AuthCaoServer) -> None:
    """Subprocess accepts a JWT signed with the matching JWKS keypair.

    With a full-scope token the same DELETE that returned 401 above should
    advance past the auth gate. The session itself doesn't exist, so the
    handler returns 404 / 500 — anything but 401 proves auth passed.
    """
    token = mint_test_token(cao_server_with_auth.private_pem)
    resp = requests.delete(
        f"{cao_server_with_auth.server.url}/sessions/nonexistent-test",
        headers={"Authorization": f"Bearer {token}"},
        timeout=2.0,
    )
    assert (
        resp.status_code != 401
    ), f"expected non-401 with valid Bearer token, got HTTP {resp.status_code}"


# ---------------------------------------------------------------------------
# cao_terminal — gated by explicit isolated provider authorization
# ---------------------------------------------------------------------------


def test_legacy_provider_error_wording_classifier_is_characterized() -> None:
    """Keep the legacy phrase classifier independent of live-fixture skip policy."""
    from test.fixtures.cao_server import _provider_is_unavailable_response

    assert _provider_is_unavailable_response(
        400,
        "Kiro engine 'v2' cannot start because 'kiro-cli' was not found.",
    )
    assert _provider_is_unavailable_response(
        500,
        "OpenCode CLI initialization timed out after 120 seconds",
    )
    assert not _provider_is_unavailable_response(
        400,
        "Agent profile not found: developer",
    )


@pytest.mark.parametrize(
    "cao_terminal",
    [
        pytest.param({"provider": "codex", "agent_profile": "developer"}, id="codex"),
        pytest.param({"provider": "claude_code", "agent_profile": "developer"}, id="claude"),
        pytest.param({"provider": "opencode_cli", "agent_profile": "developer"}, id="opencode"),
    ],
    indirect=True,
)
def test_cao_terminal_create_and_get(
    cao_server: CaoServer,
    cao_terminal: str,
) -> None:
    assert isinstance(cao_terminal, str) and cao_terminal
    # The terminal endpoint should be reachable while the fixture holds the
    # session open. The status field is provider-dependent — we only assert
    # that the resource exists and the API responds.
    resp = requests.get(
        f"{cao_server.url}/terminals/{cao_terminal}",
        timeout=2.0,
    )
    assert resp.status_code == 200, f"GET /terminals/{cao_terminal} returned {resp.status_code}"
