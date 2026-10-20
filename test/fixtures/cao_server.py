"""Managed cao-server subprocess fixtures for integration tests.

Contract
--------
- ``cao_server`` (session-scoped): spawns a real ``cao-server`` subprocess on a
  free localhost port, redirects ``$HOME`` to a per-session tmp dir and provisions
  private main/auth/peer PostgreSQL targets so server state and files are isolated,
  disables optional lifespan subsystems (agent-card, A2A, OTel),
  waits for ``/health``, and yields a ``CaoServer`` dataclass with a
  ``stop()`` callable. Idempotent teardown via SIGTERM-to-process-group with
  SIGKILL escalation.

- ``cao_server_with_auth`` (session-scoped): wraps the same machinery with
  Auth0 enforcement enabled. Spawns an in-process stdlib JWKS HTTP server
  serving the public half of a fresh RSA-2048 keypair, points the subprocess
  at it via ``CAO_AUTH_JWKS_URI``, and yields an ``AuthCaoServer`` bundle
  (server + jwks + keys) so tests can mint tokens via ``mint_test_token``.

- ``cao_terminal`` (function-scoped): creates a fresh terminal via
  ``POST /sessions``, yields its ``terminal_id``, and cleans up on teardown.
  Provider/profile are parameterizable via indirect parametrization. The
  default provider remains ``kiro_cli`` for compatibility, but its fixture
  gate marks it N/A until that provider has an isolated auth manifest.

Design deviations (documented for review)
-------------------------------------------------------------------
- JWKS server uses stdlib ``http.server.ThreadingHTTPServer`` rather than
  ``aiohttp``. ``aiohttp`` is not in ``uv.lock`` (verified) and adding it
  pulls ~60 transitive deps for what is fundamentally a "serve one JSON
  document on one path" requirement.
- Audience is ``cao://test`` rather than ``cao://localhost`` — matches the
  default of ``test.conftest.mint_test_token`` so tests don't need to
  override the kwarg.
- ``cao_server_with_auth`` is session-scoped, not function-scoped. With
  ``CAO_AUTH_JWKS_CACHE_TTL=0`` there is no JWKS state to reset between
  tests; amortizing subprocess startup matters more for the 4-scenario
  matrix.

DO NOT use ``test.conftest.mock_jwks`` with the auth variant. That fixture
patches ``requests.get`` in the *test* process and is invisible to the
subprocess. The in-process ``_JWKSServer`` here is the correct path.
"""

from __future__ import annotations

import atexit
import contextlib
import http.server
import importlib
import json
import math
import os
import pkgutil
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterator, Mapping, NoReturn, Optional

import pytest
import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HEALTH_POLL_INTERVAL = 0.05
# Server import/startup has meaningful cold-path variance (editable source,
# cache misses, and Windows-mounted WSL worktrees). Eight seconds turns normal
# bootstrap into a false infrastructure failure; thirty seconds is still a
# bounded test failure while covering a real CAO start.
_HEALTH_TIMEOUT_DEFAULT = 30.0
_HEALTH_TIMEOUT_ENV = "CAO_TEST_SERVER_HEALTH_TIMEOUT"


def _provider_is_unavailable_response(status_code: int, body: str) -> bool:
    """Classify legacy provider-error wording for a pure characterization test.

    Selected live-provider fixtures do not use response wording to skip after
    an authorized session POST; those failures must remain visible to the test.
    """
    if status_code < 400:
        return False
    message = body.lower()
    unavailable_markers = (
        "initialization timed out",
        "not installed",
        "command not found",
    )
    if any(marker in message for marker in unavailable_markers):
        return True
    return "cannot start because" in message and "not found" in message


_HEALTH_TIMEOUT_MIN_SECONDS = 1.0
_HEALTH_TIMEOUT_MAX_SECONDS = 120.0
_STOP_GRACE_SECONDS = 5.0
_STOP_UNCONFIRMED_MESSAGE = "cao-server process did not stop; owned resources retained for retry"
_STARTUP_CLEANUP_NOTE = "cao-server startup cleanup incomplete; see chained cause"

_AUTH_DOMAIN = "test.local"
_AUTH_AUDIENCE = "cao://test"
_LIVE_PROVIDER_BINARIES = {
    "codex": "codex",
    "claude_code": "claude",
    "opencode_cli": "opencode",
}
_LIVE_PROVIDER_OPT_IN = "CAO_RUN_LIVE_PROVIDER_TESTS"
_LIVE_PROVIDER_MANIFEST = "CAO_REAL_PROVIDER_E2E_PROVIDERS"
_LIVE_PROVIDER_AUTH_HOME = "CAO_REAL_PROVIDER_E2E_AUTH_HOME"
_LIVE_PROVIDER_MAX_MANIFEST_BYTES = 64 * 1024
_CLAUDE_OAUTH_EXPIRY_SAFETY_MARGIN_SECONDS = 30.0
_TEST_HARNESS_ENV_NAMES = frozenset(
    {
        _LIVE_PROVIDER_OPT_IN,
        _LIVE_PROVIDER_MANIFEST,
        _LIVE_PROVIDER_AUTH_HOME,
        "CAO_REAL_PROVIDER_E2E_TEST_FILTER",
        "CAO_REAL_PROVIDER_E2E_PAIRS",
        "CAO_REAL_PROVIDER_E2E_STRICT",
        "CAO_LIVE_WORKFLOW_PROVIDER",
        "CAO_RUN_HEADLESS_REMOTE_SMOKE",
        "CAO_HEADLESS_REMOTE_PROVIDER",
        "CAO_HEADLESS_REMOTE_REF",
    }
)
_TEST_HARNESS_ENV_PREFIXES = ("CAO_TEST_LIVE_PROVIDER_MODEL_",)
_EXTERNAL_CREDENTIAL_SUFFIXES = (
    "_API_KEY",
    "_API_TOKEN",
    "_ACCESS_TOKEN",
    "_AUTH_TOKEN",
    "_OAUTH_TOKEN",
    "_REFRESH_TOKEN",
    "_SESSION_TOKEN",
    "_SECRET",
    "_SECRET_KEY",
    "_SECRET_ACCESS_KEY",
    "_PASSWORD",
    "_PRIVATE_KEY",
    "_CREDENTIAL",
    "_CREDENTIALS",
    "_CREDENTIALS_FILE",
    "_TOKEN",
    "_BASE_URL",
    "_API_URL",
)
_EXTERNAL_CREDENTIAL_NAMES = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "AWS_CONFIG_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
        "AZURE_CONFIG_DIR",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_KEYFILE_JSON",
    }
)
_PROVIDER_CONFIG_PREFIXES = (
    # AWS SDKs accept credential-chain and metadata selectors beyond the
    # standard region/endpoint names, so reject ambient AWS settings as a set.
    "AWS_",
    "CODEX_",
    "OPENAI_",
    "ANTHROPIC_",
    "CLAUDE_",
    "OPENCODE_",
    "GOOGLE_CLOUD_",
    "GOOGLE_GENAI_",
    "VERTEXAI_",
)
_PROVIDER_ROUTING_ENV_NAMES = frozenset(
    {
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_ENDPOINT_URL",
        "AWS_USE_FIPS_ENDPOINT",
        "AWS_USE_DUALSTACK_ENDPOINT",
        "AWS_STS_REGIONAL_ENDPOINTS",
        "AWS_BEDROCK_RUNTIME_ENDPOINT",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_CLOUD_REGION",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "CLOUD_ML_REGION",
        "VERTEXAI_PROJECT",
        "VERTEXAI_LOCATION",
        "GEMINI_CLI_HOME",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
    }
)
_PRIVATE_PROVIDER_CONFIG_ENV_NAMES = frozenset(
    {
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_DIR",
        "GEMINI_CLI_HOME",
    }
)


@dataclass(frozen=True)
class LiveProviderAuthorization:
    """One explicitly selected provider and its isolated auth-file inputs."""

    name: str
    model: str
    binary: str
    auth_home: Path
    auth_files: tuple[Path, ...]


def _live_provider_failure(message: str) -> NoReturn:
    pytest.fail(f"Invalid {_LIVE_PROVIDER_MANIFEST} provider authorization: {message}")


def _authorization_string(value: object, *, field_name: str, provider: str) -> str:
    if not isinstance(value, str):
        _live_provider_failure(f"{provider!r} field {field_name!r} must be a string")
    result = value.strip()
    if not result or any(character in result for character in "\x00\r\n"):
        _live_provider_failure(f"{provider!r} field {field_name!r} must be a non-empty single line")
    return result


def _validate_claude_oauth_stage(auth_home: Path, auth_files: list[Path]) -> None:
    """Reject a stale Claude OAuth stage before launching the provider.

    Claude Code can leave a stale access token in an otherwise renewable
    receipt.  In an isolated HOME the CLI then waits for its login flow until
    CAO's task deadline expires, which looks like a provider ``504`` rather
    than an actionable fixture error.  The live lane owns the selected auth
    stage, so it can validate the non-secret expiry metadata without touching
    the operator's login or attempting a refresh.

    Older manifests for other providers and synthetic Claude archive fixtures
    intentionally use different auth layouts; the guard is active only when
    the canonical Claude credential file is explicitly selected.
    """
    credential = Path(".claude/.credentials.json")
    if credential not in auth_files:
        return
    path = auth_home / credential
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _live_provider_failure("provider 'claude_code' selected credential file is not valid JSON")
    oauth = payload.get("claudeAiOauth") if isinstance(payload, dict) else None
    if not isinstance(oauth, dict):
        _live_provider_failure(
            "provider 'claude_code' selected credential file has no OAuth receipt"
        )
    for field_name in ("accessToken", "refreshToken"):
        value = oauth.get(field_name)
        if not isinstance(value, str) or not value.strip():
            _live_provider_failure(f"provider 'claude_code' OAuth receipt is missing {field_name}")
    expires_at = oauth.get("expiresAt")
    if expires_at is None:
        # Claude versions predating the expiry field remain supported; the
        # provider itself still decides whether the receipt can be used.
        return
    if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
        _live_provider_failure("provider 'claude_code' OAuth receipt has an invalid access expiry")
    # Claude has emitted both Unix seconds and Unix milliseconds over time.
    expiry_ms = float(expires_at)
    if not math.isfinite(expiry_ms):
        _live_provider_failure("provider 'claude_code' OAuth receipt has an invalid access expiry")
    if expiry_ms < 100_000_000_000:
        expiry_ms *= 1000.0
    if expiry_ms <= (time.time() + _CLAUDE_OAUTH_EXPIRY_SAFETY_MARGIN_SECONDS) * 1000.0:
        _live_provider_failure(
            "provider 'claude_code' selected access credential is expired; refresh the explicit auth stage"
        )


def _authorize_live_provider(
    provider: str,
    *,
    opt_in_env: str = _LIVE_PROVIDER_OPT_IN,
) -> LiveProviderAuthorization:
    """Validate a selected provider before any CLI lookup or provider request.

    The legacy live E2E lane accepts only the three auth-file-backed providers
    reviewed by the current harness. It never discovers local CLIs on its own,
    and an explicit manifest entry must choose both a model and auth files.
    """
    expected_binary = _LIVE_PROVIDER_BINARIES.get(provider)
    if expected_binary is None:
        pytest.skip(f"provider {provider!r} is unsupported in the isolated live-provider lane")
    if os.environ.get(opt_in_env) != "1":
        pytest.skip(f"Real provider E2E disabled; set {opt_in_env}=1 for this separate lane.")

    raw_manifest = os.environ.get(_LIVE_PROVIDER_MANIFEST, "").strip()
    if not raw_manifest:
        _live_provider_failure(f"set {_LIVE_PROVIDER_MANIFEST} to an explicit provider manifest")
    if len(raw_manifest.encode("utf-8")) > _LIVE_PROVIDER_MAX_MANIFEST_BYTES:
        _live_provider_failure(
            f"manifest must not exceed {_LIVE_PROVIDER_MAX_MANIFEST_BYTES} UTF-8 bytes"
        )
    try:
        manifest = json.loads(raw_manifest)
    except json.JSONDecodeError as error:
        _live_provider_failure(f"manifest JSON is invalid at byte {error.pos}")
    if not isinstance(manifest, dict):
        _live_provider_failure("manifest must be a JSON object")

    selected = manifest.get(provider)
    if selected is None:
        pytest.skip(f"provider {provider!r} is not selected in the explicit provider manifest")
    if not isinstance(selected, dict):
        _live_provider_failure(f"provider {provider!r} must map to an object")
    allowed_fields = {
        "model",
        "binary",
        "auth_env",
        "auth_files",
        "capabilities",
        "exclude_reason",
    }
    unexpected = sorted(set(selected) - allowed_fields)
    if unexpected:
        _live_provider_failure(
            f"provider {provider!r} has unsupported fields: {', '.join(unexpected)}"
        )
    excluded = selected.get("exclude_reason")
    if excluded is not None:
        reason = _authorization_string(excluded, field_name="exclude_reason", provider=provider)
        pytest.skip(f"provider {provider!r} is explicitly excluded: {reason}")

    model = _authorization_string(selected.get("model"), field_name="model", provider=provider)
    binary = _authorization_string(selected.get("binary"), field_name="binary", provider=provider)
    if binary != expected_binary:
        _live_provider_failure(
            f"provider {provider!r} must use reviewed binary {expected_binary!r}"
        )

    auth_env = selected.get("auth_env", [])
    if not isinstance(auth_env, list) or any(not isinstance(value, str) for value in auth_env):
        _live_provider_failure(f"provider {provider!r} auth_env must be a JSON array")
    if auth_env:
        _live_provider_failure(
            f"provider {provider!r} must use isolated auth_files; inherited auth_env is disabled"
        )

    raw_auth_files = selected.get("auth_files")
    if not isinstance(raw_auth_files, list) or not raw_auth_files:
        _live_provider_failure(f"provider {provider!r} needs explicit auth_files")
    auth_files: list[Path] = []
    for value in raw_auth_files:
        relative = _authorization_string(value, field_name="auth_files", provider=provider)
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or "\\" in relative:
            _live_provider_failure(f"provider {provider!r} auth_files must be safe relative paths")
        if path in auth_files:
            _live_provider_failure(f"provider {provider!r} auth_files contains duplicates")
        auth_files.append(path)

    raw_auth_home = os.environ.get(_LIVE_PROVIDER_AUTH_HOME, "").strip()
    if not raw_auth_home:
        _live_provider_failure(f"set {_LIVE_PROVIDER_AUTH_HOME} to an isolated auth directory")
    auth_home = Path(raw_auth_home)
    runner_home = os.environ.get("HOME")
    if not auth_home.is_absolute() or not auth_home.is_dir():
        _live_provider_failure(f"{_LIVE_PROVIDER_AUTH_HOME} must be an existing absolute directory")
    auth_home = auth_home.resolve()
    if runner_home and auth_home == Path(runner_home).resolve():
        _live_provider_failure(f"{_LIVE_PROVIDER_AUTH_HOME} must be outside runner HOME")
    for relative in auth_files:
        source = auth_home / relative
        try:
            resolved_source = source.resolve(strict=True)
            resolved_source.relative_to(auth_home)
        except (OSError, ValueError):
            _live_provider_failure(
                f"provider {provider!r} selected auth file is missing or escapes its auth home"
            )
        if not resolved_source.is_file():
            _live_provider_failure(f"provider {provider!r} selected auth path is not a file")

    if provider == "claude_code":
        _validate_claude_oauth_stage(auth_home, auth_files)

    if shutil.which(binary) is None:
        pytest.skip(f"selected provider {provider!r} CLI {binary!r} is unavailable on PATH")
    return LiveProviderAuthorization(
        name=provider,
        model=model,
        binary=binary,
        auth_home=auth_home,
        auth_files=tuple(auth_files),
    )


def _stage_live_provider_auth(home_dir: Path, authorization: LiveProviderAuthorization) -> None:
    """Copy only selected auth files into a disposable private provider HOME."""
    home_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    home_dir.chmod(0o700)
    for relative in authorization.auth_files:
        source = authorization.auth_home / relative
        resolved_source = source.resolve(strict=True)
        try:
            resolved_source.relative_to(authorization.auth_home)
        except ValueError:
            pytest.fail("selected auth file resolves outside its isolated auth home")
        target = home_dir / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            target.parent.resolve().relative_to(home_dir.resolve())
        except ValueError:
            pytest.fail("private auth destination resolves outside its isolated HOME")
        parent = target.parent
        while parent != home_dir.parent:
            parent.chmod(0o700)
            if parent == home_dir:
                break
            parent = parent.parent
        if target.exists() or target.is_symlink():
            if target.is_symlink() or target.read_bytes() != resolved_source.read_bytes():
                pytest.fail(f"Refusing to replace existing private auth path {target}")
            target.chmod(0o600)
            continue
        shutil.copyfile(resolved_source, target)
        target.chmod(0o600)


def _fixture_health_timeout() -> float:
    """Return the opt-in server-start budget used by shared E2E fixtures.

    The default is intentionally long enough for a cold CAO bootstrap. A
    real-provider run on a particularly slow mounted worktree may set an
    explicit bounded override, keeping infrastructure variance out of provider
    lifecycle assertions without weakening them.
    """
    raw_value = os.environ.get(_HEALTH_TIMEOUT_ENV)
    if raw_value is None or not raw_value.strip():
        return _HEALTH_TIMEOUT_DEFAULT

    try:
        timeout = float(raw_value)
    except ValueError as error:
        raise ValueError(
            f"{_HEALTH_TIMEOUT_ENV} must be a number of seconds, got {raw_value!r}"
        ) from error

    if not _HEALTH_TIMEOUT_MIN_SECONDS <= timeout <= _HEALTH_TIMEOUT_MAX_SECONDS:
        raise ValueError(
            f"{_HEALTH_TIMEOUT_ENV} must be between "
            f"{_HEALTH_TIMEOUT_MIN_SECONDS:g} and {_HEALTH_TIMEOUT_MAX_SECONDS:g} "
            f"seconds, got {timeout:g}"
        )
    return timeout


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaoServer:
    """Handle to a managed cao-server subprocess.

    Fields:
        url: ``http://127.0.0.1:<port>`` — base URL for all HTTP calls.
        port: The TCP port the subprocess is listening on.
        home_dir: The redirected ``$HOME``. All persistent cao paths live
            under ``home_dir / ".aws" / "cli-agent-orchestrator" / ...``.
        database_targets: Private PostgreSQL main/auth/peer targets used by the
            child server. The server owns and drops them unless supplied by a
            caller that keeps a target context alive across restarts.
        database_config_path: Private target configuration passed to the child.
        log_path: Private file where the subprocess's merged stdout+stderr is
            appended; startup errors report its size without exposing content.
        stop: Idempotent teardown callable.
    """

    url: str
    port: int
    home_dir: Path
    database_targets: Any = field(repr=False, compare=False, hash=False)
    database_config_path: Path
    log_path: Path
    stop: Callable[[], None]

    @property
    def database_target(self) -> Any:
        """Return the main runtime-only PostgreSQL target."""
        return self.database_targets.main


@dataclass(frozen=True)
class AuthCaoServer:
    """Bundle of an auth-enabled cao-server + the JWKS server + the keypair.

    ``private_pem`` is what tests pass to ``mint_test_token``. ``public_jwk``
    is what the in-process JWKS server is serving.
    """

    server: CaoServer
    jwks: "_JWKSServer"
    private_pem: bytes
    public_jwk: Any  # authlib.jose.JsonWebKey — typed Any to avoid an import here


# ---------------------------------------------------------------------------
# Process-wide registry for atexit cleanup
# ---------------------------------------------------------------------------

_LIVE_SERVERS: "set[CaoServer]" = set()


def _atexit_cleanup() -> None:
    for srv in list(_LIVE_SERVERS):
        with contextlib.suppress(Exception):
            srv.stop()


atexit.register(_atexit_cleanup)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pick_free_port(host: str = "127.0.0.1") -> int:
    """Allocate a free TCP port on ``host`` and return it.

    The kernel does not immediately re-issue the port to another caller, but a
    TOCTOU window exists between close and the subsequent ``bind`` in
    ``Popen``. Under normal load this is microseconds; under heavy parallel
    spawn it can collide. The fixture surfaces a clear log-tail diagnostic if
    that happens.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def _log_byte_count(path: Path) -> Optional[int]:
    """Return a private log's byte count without exposing log content."""
    try:
        return path.stat().st_size
    except OSError:
        return None


def _subprocess_env(
    home_dir: Path,
    port: int,
    *,
    extra: Optional[Mapping[str, str]] = None,
    allowed_external_credentials: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Compose the env passed to the subprocess.

    Starts from a copy of the parent env, removes inherited provider
    credentials, provider configuration/routing selectors, and operator
    provider-home selectors, deletes any leaked
    Auth0 vars (so a developer's exported ``AUTH0_DOMAIN`` doesn't accidentally
    enable auth in the no-auth variant), then applies the standard test
    overrides plus any ``extra`` knobs. Provider credential files are staged
    separately from an explicit test manifest into this private HOME.
    """
    explicit_extra = dict(extra or {})
    allowlisted_provider_env = frozenset(allowed_external_credentials)
    invalid_allowlist = sorted(
        name
        for name in allowlisted_provider_env
        if not _is_external_credential_env(name) and not _is_provider_config_env(name)
    )
    if invalid_allowlist:
        raise ValueError(
            "allowed_external_credentials contains unrelated environment names: "
            + ", ".join(invalid_allowlist)
        )
    explicit_credentials = {name for name in explicit_extra if _is_external_credential_env(name)}
    unapproved_credentials = sorted(explicit_credentials - allowlisted_provider_env)
    if unapproved_credentials:
        raise ValueError(
            "extra contains provider credentials without an explicit manifest allowlist: "
            + ", ".join(unapproved_credentials)
        )
    explicit_provider_config = {name for name in explicit_extra if _is_provider_config_env(name)}
    unapproved_provider_config = sorted(
        explicit_provider_config - allowlisted_provider_env - _PRIVATE_PROVIDER_CONFIG_ENV_NAMES
    )
    if unapproved_provider_config:
        raise ValueError(
            "extra contains provider routing variables without an explicit manifest allowlist: "
            + ", ".join(unapproved_provider_config)
        )
    manifest_authorized_extra = frozenset(explicit_extra) & allowlisted_provider_env

    env = os.environ.copy()
    explicit_cao_home = extra.get("CAO_HOME_DIR") if extra is not None else None
    for leaked in ("AUTH0_DOMAIN", "AUTH0_AUDIENCE", "CAO_AUTH_JWKS_URI"):
        env.pop(leaked, None)
    for name in tuple(env):
        if _is_external_credential_env(name) or _is_provider_config_env(name):
            env.pop(name, None)

    if explicit_extra:
        env.update(explicit_extra)

    for name in tuple(env):
        if (
            _is_external_credential_env(name) or _is_provider_config_env(name)
        ) and name not in manifest_authorized_extra:
            env.pop(name, None)
        if name in _TEST_HARNESS_ENV_NAMES or name.startswith(_TEST_HARNESS_ENV_PREFIXES):
            env.pop(name, None)

    env.update(
        {
            "HOME": str(home_dir),
            "CAO_HOME_DIR": str(explicit_cao_home or home_dir / ".aws" / "cli-agent-orchestrator"),
            "CAO_API_HOST": "127.0.0.1",
            # Explicit test overrides support import-time configuration fault injection.
            # Host binding remains pinned to loopback above.
            "CAO_API_PORT": explicit_extra.get("CAO_API_PORT", str(port)),
            "CAO_A2A_DISABLED": "true",
            "OTEL_SDK_DISABLED": "true",
            "PYTHONUNBUFFERED": "1",
            "CODEX_HOME": str(home_dir / ".codex"),
            "CLAUDE_CONFIG_DIR": str(home_dir / ".claude"),
            "XDG_CONFIG_HOME": str(home_dir / ".config"),
            "XDG_DATA_HOME": str(home_dir / ".local" / "share"),
            "XDG_CACHE_HOME": str(home_dir / ".cache"),
            "OPENCODE_CONFIG": "",
            "OPENCODE_CONFIG_DIR": str(home_dir / ".config" / "opencode"),
            "GEMINI_CLI_HOME": str(home_dir / ".gemini"),
            "AWS_CONFIG_FILE": str(home_dir / ".aws" / "config"),
            "AWS_SHARED_CREDENTIALS_FILE": str(home_dir / ".aws" / "credentials"),
            # Do not let the child silently obtain the runner's instance role.
            # A selected manifest can explicitly allow an override.
            "AWS_EC2_METADATA_DISABLED": env.get("AWS_EC2_METADATA_DISABLED", "true"),
        }
    )
    # Child subprocesses run with cwd=home_dir. Relative PYTHONPATH entries
    # would therefore resolve inside the temporary home and fall through to
    # an installed editable checkout, which can use a different source tree
    # and schema manifest. Pin imports to this checkout with absolute paths.
    project_root = Path(__file__).resolve().parents[2]
    python_paths = [str(project_root / "src"), str(project_root)]
    for entry in env.get("PYTHONPATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry)
        if not candidate.is_absolute():
            candidate = project_root / candidate
        resolved = str(candidate.resolve())
        if resolved not in python_paths:
            python_paths.append(resolved)
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    return env


def _is_external_credential_env(name: str) -> bool:
    """Recognize environment names that can authorize or redirect a provider."""
    if name.startswith("CAO_"):
        return False
    return name in _EXTERNAL_CREDENTIAL_NAMES or name.endswith(_EXTERNAL_CREDENTIAL_SUFFIXES)


def _is_provider_config_env(name: str) -> bool:
    """Recognize provider settings that can redirect or configure a backend."""
    if name.startswith(_PROVIDER_CONFIG_PREFIXES):
        return True
    return name in _PROVIDER_ROUTING_ENV_NAMES or name.startswith("BEDROCK_")


def _wait_for_health(
    url: str,
    *,
    deadline: float,
    process: subprocess.Popen,
    log_path: Path,
) -> None:
    """Poll ``GET {url}/health`` until 200 or ``deadline``.

    Raises ``RuntimeError`` with status and log size if the subprocess dies
    during startup or the deadline expires. Raw log contents remain available
    in the private log file for local inspection.
    """
    end = time.monotonic() + deadline
    last_error: Optional[str] = None
    while time.monotonic() < end:
        if process.poll() is not None:
            raise RuntimeError(
                f"cao-server exited with code {process.returncode} before "
                f"/health became ready (log_bytes={_log_byte_count(log_path)})"
            )
        try:
            # A Windows-mounted WSL worktree can briefly schedule the freshly
            # started ASGI process slowly enough that it writes a successful
            # access log after a 0.5 s client read timeout.  Keep the bounded
            # startup deadline, but give one localhost response a realistic
            # read window instead of classifying a healthy server as failed.
            remaining = max(0.05, end - time.monotonic())
            resp = requests.get(
                f"{url}/health",
                timeout=(min(0.5, remaining), min(2.0, remaining)),
            )
            if resp.status_code == 200 and resp.json().get("status") == "ok":
                return
            last_error = f"HTTP {resp.status_code}; response body omitted"
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = type(exc).__name__
        time.sleep(_HEALTH_POLL_INTERVAL)

    raise RuntimeError(
        f"cao-server at {url} did not become healthy within {deadline}s "
        f"(last_error={last_error}, log_bytes={_log_byte_count(log_path)})"
    )


def _terminate(
    process: subprocess.Popen,
    log_handle: Any,
    stopped: list[bool],
) -> None:
    """Idempotent SIGTERM-to-group → wait → SIGKILL escalation."""
    if stopped[0]:
        return

    pid = process.pid
    if process.poll() is None:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pid, signal.SIGTERM)
        try:
            process.wait(timeout=_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(pid, signal.SIGKILL)
            try:
                process.wait(timeout=_STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                if process.poll() is None:
                    raise RuntimeError(_STOP_UNCONFIRMED_MESSAGE) from None

    # A successful wait or an already-exited poll must confirm ownership has
    # ended before the log, PostgreSQL target context, or runtime config is
    # released. Keep ``stopped`` false on every unconfirmed path so a caller
    # (including atexit cleanup) can retry later.
    if process.poll() is None:
        raise RuntimeError(_STOP_UNCONFIRMED_MESSAGE) from None
    stopped[0] = True

    if log_handle is not None and not log_handle.closed:
        with contextlib.suppress(Exception):
            log_handle.close()


# ---------------------------------------------------------------------------
# JWKS server (in-process, stdlib)
# ---------------------------------------------------------------------------


class _JWKSServer:
    """Tiny stdlib HTTP server that serves one JWKS document.

    Used by ``cao_server_with_auth`` to let the cao-server subprocess verify
    test-minted JWTs against a public key we control. The subprocess fetches
    via ``CAO_AUTH_JWKS_URI``; this class binds a daemon thread to a fresh
    free port and serves ``GET /.well-known/jwks.json``.
    """

    _JWKS_PATH = "/.well-known/jwks.json"

    def __init__(self, public_jwk: Any, host: str = "127.0.0.1") -> None:
        self._host = host
        self._public_jwk = public_jwk
        self._server: Optional[socketserver.TCPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port: int = 0

    def start(self) -> None:
        if self._server is not None:
            return

        jwks_payload = json.dumps({"keys": [self._public_jwk.as_dict()]}).encode("utf-8")
        target_path = self._JWKS_PATH

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — stdlib API name
                if self.path != target_path:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(jwks_payload)))
                self.end_headers()
                self.wfile.write(jwks_payload)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return  # Silence default stderr noise.

        self._server = http.server.ThreadingHTTPServer((self._host, 0), _Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="JWKSServer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        with contextlib.suppress(Exception):
            self._server.shutdown()
        with contextlib.suppress(Exception):
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._server = None
        self._thread = None

    @property
    def url(self) -> str:
        if self.port == 0:
            raise RuntimeError("JWKS server has not been started")
        return f"http://{self._host}:{self.port}{self._JWKS_PATH}"


# ---------------------------------------------------------------------------
# Core spawn helper (exposed for self-tests)
# ---------------------------------------------------------------------------


def _seed_packaged_skills(home_dir: Path) -> None:
    """Copy package-bundled skills into the redirected HOME.

    With HOME isolated to a tmp dir, ``SKILLS_DIR`` (=``$HOME/.aws/cli-agent-
    orchestrator/skills``) is empty. Older tests assumed the
    developer had run ``cao install <name>`` and that the skill was on disk.
    Seeding the bundled skills here restores that expectation without
    leaking state into the developer's real ``~/.aws/``.
    """
    import shutil

    import cli_agent_orchestrator.skills as _skills_pkg

    src = Path(_skills_pkg.__file__).parent
    dest = home_dir / ".aws" / "cli-agent-orchestrator" / "skills"
    dest.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        if entry.name.startswith("_") or entry.name == "__pycache__":
            continue
        target = dest / entry.name
        if target.exists():
            continue
        if entry.is_dir():
            shutil.copytree(entry, target)
        else:
            shutil.copy2(entry, target)


def _seed_omp_e2e_state(home_dir: Path) -> None:
    """Seed non-secret OMP setup state and assign profiles for real OMP E2E tests.

    OMP stores a first-run completion marker under HOME. The managed server
    deliberately redirects HOME, so without this marker OMP opens its setup
    wizard and cannot reach a CAO-ready terminal. Authentication remains in
    normal environment-based providers; this writes no credential material.
    """
    import shutil

    omp_config = home_dir / ".omp" / "agent" / "config.yml"
    omp_config.parent.mkdir(parents=True, exist_ok=True)
    if not omp_config.exists():
        omp_config.write_text("setupVersion: 1\n", encoding="utf-8")

    examples_dir = Path(__file__).resolve().parents[2] / "examples" / "assign"
    store_dir = home_dir / ".aws" / "cli-agent-orchestrator" / "agent-store"
    store_dir.mkdir(parents=True, exist_ok=True)
    for name in ("data_analyst", "report_generator", "analysis_supervisor"):
        target = store_dir / f"{name}.md"
        if not target.exists():
            shutil.copy2(examples_dir / f"{name}.md", target)


def _runtime_only_database_targets(database_targets: Any, home_dir: Path):
    """Strip migration-owner credentials before giving targets to a server child."""
    from dataclasses import replace
    from test.postgres.fixtures import PostgresTargets, _write_private

    targets = tuple(database_targets[scope] for scope in ("main", "auth", "peer"))
    if all(
        target.migration_user is None and target.migration_secret_file is None for target in targets
    ):
        return database_targets, None

    config_dir = home_dir / ".cao-postgres-runtime"
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(config_dir, 0o700)
    config_targets = {
        scope: {
            "host": target.host,
            "port": target.port,
            "database": target.database,
            "user": target.user,
            "schema": target.schema,
            "store_id": target.store_id,
            "secret_file": str(target.secret_file.resolve()),
            "sslmode": target.sslmode,
        }
        for scope, target in zip(("main", "auth", "peer"), targets, strict=True)
    }
    config_path = config_dir / "database-config.json"
    _write_private(config_path, json.dumps({"version": 1, "targets": config_targets}, indent=2))
    runtime_targets = tuple(
        replace(
            target,
            home=config_dir,
            migration_user=None,
            migration_secret_file=None,
        )
        for target in targets
    )
    return (
        PostgresTargets(
            main=runtime_targets[0],
            auth=runtime_targets[1],
            peer=runtime_targets[2],
            home=config_dir,
            config_file=config_path,
        ),
        config_path,
    )


def _remove_runtime_database_config(config_path: Path | None) -> None:
    if config_path is None:
        return
    config_path.unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        config_path.parent.rmdir()


def _start_cao_server(
    home_dir: Path,
    port: int,
    *,
    extra_env: Optional[Mapping[str, str]] = None,
    deadline: float = _HEALTH_TIMEOUT_DEFAULT,
    database_targets: Any | None = None,
    postgres_service: Any | None = None,
    allowed_external_credentials: frozenset[str] = frozenset(),
) -> CaoServer:
    """Spawn a cao-server subprocess and return a managed ``CaoServer``.

    Used directly by both the session fixture and the self-tests that exercise
    parallelism / idempotency. Callers own the lifetime via the returned
    ``stop`` callable.
    """
    home_dir.mkdir(parents=True, exist_ok=True)
    _seed_packaged_skills(home_dir)
    _seed_omp_e2e_state(home_dir)
    owned_target_context = None
    if database_targets is None:
        from test.postgres.fixtures import isolated_live_targets

        owned_target_context = isolated_live_targets(
            home_dir / ".cao-postgres", service=postgres_service
        )
        database_targets = owned_target_context.__enter__()
    if database_targets is None:
        if owned_target_context is not None:
            owned_target_context.__exit__(None, None, None)
        raise RuntimeError("PostgreSQL targets were not provisioned for the managed server")

    derived_config_path: Path | None = None
    try:
        database_targets, derived_config_path = _runtime_only_database_targets(
            database_targets, home_dir
        )
    except BaseException as error:
        if owned_target_context is not None:
            owned_target_context.__exit__(type(error), error, error.__traceback__)
        raise

    log_path = home_dir / "server.log"
    try:
        log_handle = open(log_path, "ab")  # noqa: SIM115 — lifetime is in stop()
    except BaseException as error:
        if owned_target_context is not None:
            owned_target_context.__exit__(type(error), error, error.__traceback__)
        _remove_runtime_database_config(derived_config_path)
        raise
    process: subprocess.Popen[bytes] | None = None
    stopped = [False]
    url = f"http://127.0.0.1:{port}"
    server: CaoServer | None = None

    def _stop(startup_error: BaseException | None = None) -> None:
        if stopped[0]:
            return
        if process is None:
            # Popen did not produce a child, so there is no process to wait for.
            stopped[0] = True
            if not log_handle.closed:
                with contextlib.suppress(Exception):
                    log_handle.close()
        else:
            _terminate(process, log_handle, stopped)

        try:
            if owned_target_context is not None:
                if startup_error is None:
                    owned_target_context.__exit__(None, None, None)
                else:
                    owned_target_context.__exit__(
                        type(startup_error),
                        startup_error,
                        startup_error.__traceback__,
                    )
        finally:
            try:
                _remove_runtime_database_config(derived_config_path)
            finally:
                if server is not None:
                    _LIVE_SERVERS.discard(server)

    server = CaoServer(
        url=url,
        port=port,
        home_dir=home_dir,
        database_targets=database_targets,
        database_config_path=database_targets.config_file,
        log_path=log_path,
        stop=_stop,
    )
    _LIVE_SERVERS.add(server)

    try:
        child_extra = dict(extra_env or {})
        # Do not let a parent function-scoped test target or inherited operator
        # config redirect the managed subprocess away from this server's owned
        # main/auth/peer database set.
        child_extra["CAO_DATABASE_CONFIG_FILE"] = str(database_targets.config_file)
        env = _subprocess_env(
            home_dir,
            port,
            extra=child_extra,
            allowed_external_credentials=allowed_external_credentials,
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "cli_agent_orchestrator.api.main",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=env,
            # Manifest capture must describe this disposable test workspace. Using
            # the operator's checkout adds unrelated files and OneDrive latency to
            # every script submit, exceeding the real HTTP fixture's five-second gate.
            # An absent Git baseline is recorded explicitly by the production code.
            cwd=home_dir,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _wait_for_health(url, deadline=deadline, process=process, log_path=log_path)
    except BaseException as error:
        # Register the cleanup owner before Popen/health checks. If SIGKILL plus
        # the bounded wait cannot confirm exit, _stop raises and deliberately
        # retains the log, config, target context, and this registry reference.
        try:
            _stop(error)
        except BaseException as cleanup_error:
            if cleanup_error is error:
                raise
            note_adder = getattr(error, "add_note", None)
            if callable(note_adder):
                note_adder(_STARTUP_CLEANUP_NOTE)
            else:
                notes = list(getattr(error, "__notes__", ()))
                notes.append(_STARTUP_CLEANUP_NOTE)
                error.__notes__ = notes
            raise error from cleanup_error
        raise

    assert server is not None
    return server


# ---------------------------------------------------------------------------
# Session-scoped RSA keypair
# ---------------------------------------------------------------------------


_SESSION_RSA_CACHE: dict[str, tuple[bytes, Any]] = {}


def _session_rsa_keys() -> tuple[bytes, Any]:
    """Session-scoped RSA-2048 keypair (matches ``test.conftest.rsa_keys``).

    ``test.conftest.rsa_keys`` is function-scoped, which is right for unit
    tests but wrong for our session-scoped auth fixture. This helper mirrors
    the keypair-generation logic and caches the result for the session.
    """
    if "keys" not in _SESSION_RSA_CACHE:
        from authlib.jose import JsonWebKey
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_pem = private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        public_jwk = JsonWebKey.import_key(
            public_pem,
            {"kty": "RSA", "use": "sig", "kid": "test-kid"},
        )
        _SESSION_RSA_CACHE["keys"] = (private_pem, public_jwk)
    return _SESSION_RSA_CACHE["keys"]


# ---------------------------------------------------------------------------
# Public fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cao_server(
    tmp_path_factory: pytest.TempPathFactory, postgres_service: Any
) -> Iterator[CaoServer]:
    """Spawn a managed cao-server subprocess for the whole session."""
    home = tmp_path_factory.mktemp("cao_home_session")
    port = _pick_free_port()
    server = _start_cao_server(
        home, port, deadline=_fixture_health_timeout(), postgres_service=postgres_service
    )
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture(scope="session")
def cao_server_with_auth(
    tmp_path_factory: pytest.TempPathFactory,
    postgres_service: Any,
) -> Iterator[AuthCaoServer]:
    """Spawn a managed cao-server subprocess with Auth0 enforcement enabled.

    The subprocess is pointed at an in-process JWKS HTTP server that serves
    the public half of a fresh RSA-2048 keypair. Tests mint matching tokens
    via ``test.conftest.mint_test_token`` using the bundle's ``private_pem``.
    """
    private_pem, public_jwk = _session_rsa_keys()

    jwks = _JWKSServer(public_jwk)
    jwks.start()

    server: Optional[CaoServer] = None
    try:
        home = tmp_path_factory.mktemp("cao_home_authed_session")
        port = _pick_free_port()
        server = _start_cao_server(
            home,
            port,
            deadline=_fixture_health_timeout(),
            postgres_service=postgres_service,
            extra_env={
                "AUTH0_DOMAIN": _AUTH_DOMAIN,
                "AUTH0_AUDIENCE": _AUTH_AUDIENCE,
                "CAO_AUTH_JWKS_URI": jwks.url,
                "CAO_AUTH_JWKS_CACHE_TTL": "0",
            },
        )
        yield AuthCaoServer(
            server=server,
            jwks=jwks,
            private_pem=private_pem,
            public_jwk=public_jwk,
        )
    finally:
        if server is not None:
            server.stop()
        jwks.stop()


@pytest.fixture
def cao_terminal(
    cao_server: CaoServer,
    request: pytest.FixtureRequest,
) -> Iterator[str]:
    """Create a terminal on ``cao_server`` and clean it up on teardown.

    Provider/profile are parameterizable via indirect parametrization:

        @pytest.mark.parametrize(
            "cao_terminal",
            [{"provider": "claude_code", "agent_profile": "supervisor"}],
            indirect=True,
        )

    Default remains ``("kiro_cli", "developer")`` for existing callers.
    The authorization gate runs before binary lookup or the session POST.
    """
    params = getattr(request, "param", None) or {}
    provider = params.get("provider", "kiro_cli")
    profile = params.get("agent_profile", "developer")
    authorization = _authorize_live_provider(provider)
    _stage_live_provider_auth(cao_server.home_dir, authorization)
    session_name = f"caotest-{uuid.uuid4().hex[:12]}"

    resp = requests.post(
        f"{cao_server.url}/sessions",
        params={
            "provider": provider,
            "agent_profile": profile,
            "session_name": session_name,
            "model": authorization.model,
        },
    )
    if resp.status_code not in (200, 201):
        # Authorization already established the selected provider, binary,
        # model, and isolated credentials. A failure after POST is therefore
        # a test failure, and the response body may contain provider output.
        raise RuntimeError(f"POST /sessions failed: HTTP {resp.status_code}; response body omitted")
    data = resp.json()
    terminal_id = data["id"]
    actual_session = data["session_name"]

    try:
        yield terminal_id
    finally:
        with contextlib.suppress(Exception):
            requests.post(f"{cao_server.url}/terminals/{terminal_id}/exit")
        time.sleep(2)
        with contextlib.suppress(Exception):
            requests.delete(f"{cao_server.url}/sessions/{actual_session}")


# ---------------------------------------------------------------------------
# E2E conftest support — module-attribute patch for back-compat with the
# older e2e modules that import ``API_BASE_URL`` from
# ``cli_agent_orchestrator.constants`` at module top.
# ---------------------------------------------------------------------------


def _patch_api_base_url_for_e2e(
    cao_server_obj: CaoServer,
) -> Callable[[], None]:
    """Rewrite ``API_BASE_URL`` everywhere it has been bound by import-time.

    The older e2e modules do ``from cli_agent_orchestrator.constants
    import API_BASE_URL`` at module top, binding their own local copy of the
    string. Pytest collection imports those modules before any fixture runs,
    so simply mutating the constants module after the fact won't reach them.

    This helper:
      1. Snapshots and rewrites ``API_BASE_URL`` / ``SERVER_PORT`` on the
         constants module itself.
      2. Walks ``test.e2e`` via ``pkgutil``, importing each module, and on
         any module that has its own ``API_BASE_URL`` attribute, snapshots
         and overwrites it.

    Returns a ``restore()`` callable that puts every snapshotted attribute
    back to its original value.
    """
    import cli_agent_orchestrator.constants as _constants

    patches: list[tuple[ModuleType, str, Any]] = [
        (_constants, "API_BASE_URL", _constants.API_BASE_URL),
        (_constants, "SERVER_PORT", _constants.SERVER_PORT),
    ]
    _constants.API_BASE_URL = cao_server_obj.url
    _constants.SERVER_PORT = cao_server_obj.port

    try:
        import test.e2e as _e2e_pkg
    except ImportError:
        _e2e_pkg = None  # type: ignore[assignment]

    if _e2e_pkg is not None:
        for info in pkgutil.iter_modules(_e2e_pkg.__path__, prefix="test.e2e."):
            try:
                mod = importlib.import_module(info.name)
            except Exception:
                continue
            if hasattr(mod, "API_BASE_URL"):
                patches.append((mod, "API_BASE_URL", mod.API_BASE_URL))
                mod.API_BASE_URL = cao_server_obj.url

    def restore() -> None:
        for mod, attr, original in patches:
            setattr(mod, attr, original)

    return restore
