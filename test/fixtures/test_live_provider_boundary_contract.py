"""Offline contracts for explicitly authorized live-provider test setup."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.offline_database


def _manifest(provider: str, *, model: str = "reviewed-model") -> str:
    binaries = {"codex": "codex", "claude_code": "claude", "opencode_cli": "opencode"}
    return json.dumps(
        {
            provider: {
                "model": model,
                "binary": binaries[provider],
                "auth_env": [],
                "auth_files": ["login/auth.json"],
                "capabilities": [],
            }
        }
    )


def test_subprocess_env_drops_provider_secrets_and_operator_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test.fixtures.cao_server import _subprocess_env

    runner_home = tmp_path / "runner-home"
    monkeypatch.setenv("HOME", str(runner_home))
    monkeypatch.setenv("CODEX_HOME", str(runner_home / ".codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(runner_home / ".claude"))
    monkeypatch.setenv("OPENAI_API_KEY", "codex-secret-test-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-secret-test-value")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "claude-token-test-value")
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "opencode-config-sentinel")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("AWS_REGION", "operator-region-sentinel")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "operator-default-region-sentinel")
    monkeypatch.setenv("AWS_ENDPOINT_URL_BEDROCK", "https://operator.invalid")
    ambient_aws_auth_chain = {
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/sentinel",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://127.0.0.1/credentials-sentinel",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/runner/token-file-sentinel",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN": "runner-token-sentinel",
        "AWS_EC2_METADATA_SERVICE_ENDPOINT": "http://127.0.0.1/metadata-sentinel",
        "AWS_EC2_METADATA_SERVICE_ENDPOINT_MODE": "IPv6",
        "AWS_EC2_METADATA_DISABLED": "false",
        "AWS_PROFILE": "operator-profile-sentinel",
        "AWS_CONFIG_FILE": str(runner_home / ".aws" / "config"),
        "AWS_SHARED_CREDENTIALS_FILE": str(runner_home / ".aws" / "credentials"),
    }
    for name, value in ambient_aws_auth_chain.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "operator-project-sentinel")
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-test-value")
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_PROVIDERS", '{"codex":{}}')
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_AUTH_HOME", str(runner_home / ".codex"))
    monkeypatch.setenv("CAO_TEST_LIVE_PROVIDER_MODEL_CODEX", "private-test-model")
    monkeypatch.setenv("CAO_DATABASE_CONFIG_FILE", str(tmp_path / "owned-db.json"))
    monkeypatch.setenv("CAO_TEST_SERVER_HEALTH_TIMEOUT", "37")

    private_home = tmp_path / "server-home"
    child = _subprocess_env(private_home, 12345)

    assert child["HOME"] == str(private_home)
    assert child["CODEX_HOME"] == str(private_home / ".codex")
    assert child["CLAUDE_CONFIG_DIR"] == str(private_home / ".claude")
    assert child["XDG_CONFIG_HOME"] == str(private_home / ".config")
    assert child["XDG_DATA_HOME"] == str(private_home / ".local" / "share")
    assert child["OPENCODE_CONFIG"] == ""
    assert child["OPENCODE_CONFIG_DIR"] == str(private_home / ".config" / "opencode")
    assert child["AWS_CONFIG_FILE"] == str(private_home / ".aws" / "config")
    assert child["AWS_SHARED_CREDENTIALS_FILE"] == str(private_home / ".aws" / "credentials")
    assert child["AWS_EC2_METADATA_DISABLED"] == "true"
    assert child["CAO_DATABASE_CONFIG_FILE"] == str(tmp_path / "owned-db.json")
    assert child["CAO_TEST_SERVER_HEALTH_TIMEOUT"] == "37"
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENCODE_CONFIG_CONTENT",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_ENDPOINT_URL_BEDROCK",
        "GOOGLE_CLOUD_PROJECT",
        "GITHUB_TOKEN",
    ):
        assert key not in child
    for key in ambient_aws_auth_chain.keys() - {
        "AWS_CONFIG_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_EC2_METADATA_DISABLED",
    }:
        assert key not in child
    for key in (
        "CAO_RUN_LIVE_PROVIDER_TESTS",
        "CAO_REAL_PROVIDER_E2E_PROVIDERS",
        "CAO_REAL_PROVIDER_E2E_AUTH_HOME",
        "CAO_TEST_LIVE_PROVIDER_MODEL_CODEX",
    ):
        assert key not in child
    assert os.environ["HOME"] == str(runner_home)
    assert os.environ["CODEX_HOME"] == str(runner_home / ".codex")


def test_subprocess_env_preserves_explicit_port_fault_and_pins_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test.fixtures.cao_server import _subprocess_env

    monkeypatch.setenv("CAO_API_HOST", "198.51.100.12")
    monkeypatch.setenv("CAO_API_PORT", "54321")

    child = _subprocess_env(
        tmp_path / "server-home",
        12345,
        extra={"CAO_API_PORT": "not-an-int"},
    )

    assert child["CAO_API_HOST"] == "127.0.0.1"
    assert child["CAO_API_PORT"] == "not-an-int"
    assert os.environ["CAO_API_HOST"] == "198.51.100.12"
    assert os.environ["CAO_API_PORT"] == "54321"


def test_subprocess_env_only_forwards_explicitly_allowlisted_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test.fixtures.cao_server import _subprocess_env

    monkeypatch.setenv("OPENAI_API_KEY", "ambient-openai-test-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-anthropic-test-value")
    explicit_value = "manifest-authorized-test-value"

    child = _subprocess_env(
        tmp_path / "server-home",
        12345,
        extra={"OPENAI_API_KEY": explicit_value},
        allowed_external_credentials=frozenset({"OPENAI_API_KEY"}),
    )

    assert child["OPENAI_API_KEY"] == explicit_value
    assert "ANTHROPIC_API_KEY" not in child


def test_subprocess_env_allows_only_manifest_named_provider_routing_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test.fixtures.cao_server import _subprocess_env

    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "ambient-route")
    monkeypatch.setenv("AWS_REGION", "ambient-region")

    child = _subprocess_env(
        tmp_path / "server-home",
        12345,
        extra={
            "CLAUDE_CODE_USE_BEDROCK": "selected-route",
            "AWS_REGION": "selected-region",
            "AWS_EC2_METADATA_DISABLED": "false",
        },
        allowed_external_credentials=frozenset(
            {"CLAUDE_CODE_USE_BEDROCK", "AWS_REGION", "AWS_EC2_METADATA_DISABLED"}
        ),
    )

    assert child["CLAUDE_CODE_USE_BEDROCK"] == "selected-route"
    assert child["AWS_REGION"] == "selected-region"
    assert child["AWS_EC2_METADATA_DISABLED"] == "false"


def test_subprocess_env_rejects_unapproved_provider_routing_extra(tmp_path: Path) -> None:
    from test.fixtures.cao_server import _subprocess_env

    with pytest.raises(ValueError, match="without an explicit manifest allowlist") as error:
        _subprocess_env(
            tmp_path / "server-home",
            12345,
            extra={"OPENCODE_CONFIG_CONTENT": "must-not-appear-in-error"},
        )

    assert "must-not-appear-in-error" not in str(error.value)


def test_subprocess_env_rejects_unapproved_credential_extra(tmp_path: Path) -> None:
    from test.fixtures.cao_server import _subprocess_env

    with pytest.raises(ValueError, match="without an explicit manifest allowlist") as error:
        _subprocess_env(
            tmp_path / "server-home",
            12345,
            extra={"OPENAI_API_KEY": "must-not-appear-in-error"},
        )

    assert "must-not-appear-in-error" not in str(error.value)


def test_matrix_server_auth_environment_uses_only_selected_manifest_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test.e2e.test_real_provider_matrix import (
        MatrixCell,
        MatrixProvider,
        _matrix_server_auth_environment,
    )

    parent = MatrixProvider(
        "codex",
        "reviewed-model",
        "codex",
        ("OPENAI_API_KEY",),
        (),
        frozenset({"native_children"}),
    )
    child = MatrixProvider(
        "claude_code",
        "reviewed-model",
        "claude",
        ("ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK"),
        (),
        frozenset({"native_children"}),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "selected-openai-test-value")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "selected-anthropic-test-value")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "manifest-selected-route")
    monkeypatch.setenv("AWS_REGION", "ambient-region")
    monkeypatch.setenv("GITHUB_TOKEN", "unselected-github-test-value")

    values, allowed = _matrix_server_auth_environment(MatrixCell(parent, child))

    assert values == {
        "OPENAI_API_KEY": "selected-openai-test-value",
        "ANTHROPIC_API_KEY": "selected-anthropic-test-value",
        "CLAUDE_CODE_USE_BEDROCK": "manifest-selected-route",
    }
    assert allowed == frozenset({"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK"})
    assert "AWS_REGION" not in values
    assert "GITHUB_TOKEN" not in values


def test_live_provider_gate_skips_before_manifest_or_cli_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test.fixtures import cao_server

    monkeypatch.delenv("CAO_RUN_LIVE_PROVIDER_TESTS", raising=False)
    monkeypatch.delenv("CAO_REAL_PROVIDER_E2E_PROVIDERS", raising=False)
    monkeypatch.setattr(
        cao_server.shutil,
        "which",
        lambda command: pytest.fail(f"looked up provider binary before opt-in: {command}"),
    )

    with pytest.raises(pytest.skip.Exception, match="CAO_RUN_LIVE_PROVIDER_TESTS=1"):
        cao_server._authorize_live_provider("codex")


def test_unsupported_kiro_terminal_is_skipped_before_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test.fixtures import cao_server

    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.delenv("CAO_REAL_PROVIDER_E2E_PROVIDERS", raising=False)
    monkeypatch.setattr(
        cao_server.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("cao_terminal POST happened before provider gate"),
    )
    fixture = cao_server.cao_terminal._fixture_function

    with pytest.raises(pytest.skip.Exception, match="kiro_cli.*unsupported"):
        next(
            fixture(
                SimpleNamespace(home_dir=Path("/private/server"), url="http://127.0.0.1"),
                SimpleNamespace(),
            )
        )


def test_unsupported_antigravity_fixture_is_explicit_na_before_cli_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test.e2e import conftest

    monkeypatch.setattr(
        conftest.shutil,
        "which",
        lambda command: pytest.fail(f"unsupported provider looked up binary: {command}"),
    )

    with pytest.raises(pytest.skip.Exception, match="antigravity_cli.*unsupported"):
        conftest.require_antigravity.__wrapped__()


@pytest.mark.parametrize(
    ("provider", "binary"),
    [("codex", "codex"), ("claude_code", "claude"), ("opencode_cli", "opencode")],
)
def test_selected_provider_requires_explicit_model_manifest_and_file_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    binary: str,
) -> None:
    from test.fixtures import cao_server

    auth_home = tmp_path / "auth-home"
    auth_file = auth_home / "login" / "auth.json"
    auth_file.parent.mkdir(parents=True)
    auth_file.write_text('{"login":"test-only"}\n', encoding="utf-8")
    (auth_home / "unlisted-secret.json").write_text("not selected\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "runner-home"))
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_AUTH_HOME", str(auth_home))
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_PROVIDERS", _manifest(provider))
    monkeypatch.setattr(cao_server.shutil, "which", lambda command: f"/bin/{binary}")

    authorization = cao_server._authorize_live_provider(provider)
    server_home = tmp_path / "server-home"
    cao_server._stage_live_provider_auth(server_home, authorization)

    staged = server_home / "login" / "auth.json"
    assert staged.read_bytes() == auth_file.read_bytes()
    assert staged.stat().st_mode & 0o777 == 0o600
    assert not (server_home / "unlisted-secret.json").exists()
    assert auth_file.read_text(encoding="utf-8") == '{"login":"test-only"}\n'
    assert authorization.model == "reviewed-model"
    assert authorization.binary == binary


def _claude_oauth_manifest() -> str:
    return json.dumps(
        {
            "claude_code": {
                "model": "reviewed-claude-model",
                "binary": "claude",
                "auth_env": [],
                "auth_files": [".claude/.credentials.json", ".claude.json"],
                "capabilities": ["native_children"],
            }
        }
    )


def _write_claude_oauth_stage(auth_home: Path, *, expires_at: int) -> None:
    credentials = auth_home / ".claude/.credentials.json"
    credentials.parent.mkdir(parents=True, exist_ok=True)
    credentials.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "private-access-token-sentinel",
                    "refreshToken": "private-refresh-token-sentinel",
                    "expiresAt": expires_at,
                }
            }
        ),
        encoding="utf-8",
    )
    (auth_home / ".claude.json").write_text("{}\n", encoding="utf-8")


def test_claude_live_gate_rejects_expired_oauth_before_cli_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale isolated receipt must fail fast instead of becoming a 180s 504."""
    from test.fixtures import cao_server

    auth_home = tmp_path / "auth-home"
    _write_claude_oauth_stage(auth_home, expires_at=int((time.time() - 60) * 1000))
    monkeypatch.setenv("HOME", str(tmp_path / "runner-home"))
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_AUTH_HOME", str(auth_home))
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_PROVIDERS", _claude_oauth_manifest())
    monkeypatch.setattr(
        cao_server.shutil,
        "which",
        lambda command: pytest.fail(f"stale auth looked up provider binary: {command}"),
    )

    with pytest.raises(pytest.fail.Exception, match="access credential is expired") as error:
        cao_server._authorize_live_provider("claude_code")
    assert "private-access-token-sentinel" not in str(error.value)
    assert "private-refresh-token-sentinel" not in str(error.value)


def test_claude_live_gate_rejects_oauth_expiring_during_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test.fixtures import cao_server

    auth_home = tmp_path / "auth-home"
    _write_claude_oauth_stage(auth_home, expires_at=int((time.time() + 10) * 1000))
    monkeypatch.setenv("HOME", str(tmp_path / "runner-home"))
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_AUTH_HOME", str(auth_home))
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_PROVIDERS", _claude_oauth_manifest())
    monkeypatch.setattr(cao_server.shutil, "which", lambda command: f"/bin/{command}")

    with pytest.raises(pytest.fail.Exception, match="access credential is expired"):
        cao_server._authorize_live_provider("claude_code")


def test_claude_live_gate_accepts_fresh_oauth_without_mutating_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test.fixtures import cao_server

    auth_home = tmp_path / "auth-home"
    _write_claude_oauth_stage(auth_home, expires_at=int((time.time() + 600) * 1000))
    before = (auth_home / ".claude/.credentials.json").read_bytes()
    monkeypatch.setenv("HOME", str(tmp_path / "runner-home"))
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_AUTH_HOME", str(auth_home))
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_PROVIDERS", _claude_oauth_manifest())
    monkeypatch.setattr(cao_server.shutil, "which", lambda command: f"/bin/{command}")

    authorization = cao_server._authorize_live_provider("claude_code")
    private_home = tmp_path / "private-home"
    cao_server._stage_live_provider_auth(private_home, authorization)

    assert (auth_home / ".claude/.credentials.json").read_bytes() == before
    assert (private_home / ".claude/.credentials.json").read_bytes() == before
    assert (private_home / ".claude/.credentials.json").stat().st_mode & 0o777 == 0o600


def test_live_provider_rejects_ambient_auth_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test.fixtures import cao_server

    auth_home = tmp_path / "auth-home"
    (auth_home / "login").mkdir(parents=True)
    (auth_home / "login" / "auth.json").write_text("test only\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "runner-home"))
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_AUTH_HOME", str(auth_home))
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    config = json.loads(_manifest("codex"))
    config["codex"]["auth_env"] = ["OPENAI_API_KEY"]
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_PROVIDERS", json.dumps(config))
    monkeypatch.setattr(cao_server.shutil, "which", lambda command: f"/bin/{command}")

    with pytest.raises(pytest.fail.Exception, match="inherited auth_env is disabled"):
        cao_server._authorize_live_provider("codex")


def test_live_provider_request_params_carry_selected_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test.e2e.conftest import _provider_launch_params

    monkeypatch.setenv("CAO_TEST_LIVE_PROVIDER_MODEL_CODEX", "gpt-reviewed")
    params = _provider_launch_params("codex", {"agent_profile": "developer"})

    assert params == {"agent_profile": "developer", "model": "gpt-reviewed"}
    assert _provider_launch_params("claude_code", {"agent_profile": "developer"}) == {
        "agent_profile": "developer"
    }


@pytest.mark.parametrize(
    ("provider", "binary"),
    [("codex", "codex"), ("claude_code", "claude"), ("opencode_cli", "opencode")],
)
def test_cao_terminal_fixture_posts_selected_provider_and_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    binary: str,
) -> None:
    from test.fixtures import cao_server

    auth_home = tmp_path / "auth-home"
    auth_file = auth_home / "login" / "auth.json"
    auth_file.parent.mkdir(parents=True)
    auth_file.write_text("fixture-test-only\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "runner-home"))
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_AUTH_HOME", str(auth_home))
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_PROVIDERS", _manifest(provider))
    monkeypatch.setattr(cao_server.shutil, "which", lambda command: f"/bin/{binary}")
    monkeypatch.setattr(cao_server.time, "sleep", lambda seconds: None)

    events: list[tuple[str, str, dict[str, str] | None]] = []

    class _Response:
        status_code = 201
        text = ""

        @staticmethod
        def json() -> dict[str, str]:
            return {"id": "fixture-terminal", "session_name": "fixture-session"}

    def _post(url: str, *, params: dict[str, str] | None = None, **kwargs: object) -> _Response:
        events.append(("POST", url, params))
        return _Response()

    def _delete(url: str, **kwargs: object) -> _Response:
        events.append(("DELETE", url, None))
        return _Response()

    monkeypatch.setattr(cao_server.requests, "post", _post)
    monkeypatch.setattr(cao_server.requests, "delete", _delete)

    server = SimpleNamespace(url="http://127.0.0.1:12345", home_dir=tmp_path / "server-home")
    request = SimpleNamespace(param={"provider": provider, "agent_profile": "developer"})
    fixture = cao_server.cao_terminal._fixture_function
    terminal = fixture(server, request)

    assert next(terminal) == "fixture-terminal"
    with pytest.raises(StopIteration):
        next(terminal)

    assert events[0][0:2] == ("POST", "http://127.0.0.1:12345/sessions")
    assert events[0][2] is not None
    params = events[0][2]
    assert params["provider"] == provider
    assert params["agent_profile"] == "developer"
    assert params["session_name"].startswith("caotest-")
    assert params["model"] == "reviewed-model"
    assert events[1] == (
        "POST",
        "http://127.0.0.1:12345/terminals/fixture-terminal/exit",
        None,
    )
    assert events[2] == ("DELETE", "http://127.0.0.1:12345/sessions/fixture-session", None)


def test_selected_provider_startup_error_fails_without_response_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test.fixtures import cao_server

    auth_home = tmp_path / "auth-home"
    auth_file = auth_home / "login" / "auth.json"
    auth_file.parent.mkdir(parents=True)
    auth_file.write_text("fixture-test-only\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "runner-home"))
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_AUTH_HOME", str(auth_home))
    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_PROVIDERS", _manifest("codex"))
    monkeypatch.setattr(cao_server.shutil, "which", lambda command: "/bin/codex")
    monkeypatch.setattr(cao_server.time, "sleep", lambda seconds: None)

    sentinel = "private-provider-response-sentinel"

    class _Response:
        status_code = 500
        text = f"Initialization timed out; {sentinel}"

    monkeypatch.setattr(cao_server.requests, "post", lambda *args, **kwargs: _Response())
    fixture = cao_server.cao_terminal._fixture_function

    with pytest.raises(RuntimeError, match="HTTP 500") as error:
        next(
            fixture(
                SimpleNamespace(home_dir=tmp_path / "server-home", url="http://127.0.0.1"),
                SimpleNamespace(param={"provider": "codex", "agent_profile": "developer"}),
            )
        )

    assert sentinel not in str(error.value)
    assert "Initialization timed out" not in str(error.value)


def test_legacy_create_terminal_failure_omits_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test.e2e import conftest

    sentinel = "private-session-response-sentinel"
    monkeypatch.setattr(
        conftest.requests,
        "post",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=500,
            text=f"Initialization timed out; {sentinel}",
        ),
    )
    monkeypatch.setattr(conftest.time, "sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match="HTTP 500") as error:
        conftest.create_terminal("codex", "developer", "fixture-session", retries=0)

    assert sentinel not in str(error.value)
    assert "Initialization timed out" not in str(error.value)


def test_health_failure_does_not_include_response_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test.fixtures import cao_server

    sentinel = "private-health-response-sentinel"
    log_sentinel = "private-server-log-sentinel"
    log_path = tmp_path / "server.log"
    log_path.write_text(log_sentinel, encoding="utf-8")
    monkeypatch.setattr(
        cao_server.requests,
        "get",
        lambda *args, **kwargs: SimpleNamespace(status_code=503, text=sentinel),
    )
    ticks = iter((0.0, 0.01, 0.5, 2.0))
    monkeypatch.setattr(cao_server.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(cao_server.time, "sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match="HTTP 503") as error:
        cao_server._wait_for_health(
            "http://127.0.0.1:12345",
            deadline=1.0,
            process=SimpleNamespace(poll=lambda: None),
            log_path=log_path,
        )

    assert sentinel not in str(error.value)
    assert log_sentinel not in str(error.value)
    assert f"log_bytes={len(log_sentinel)}" in str(error.value)
    assert log_path.read_text(encoding="utf-8") == log_sentinel


def test_terminal_factory_error_does_not_skip_or_echo_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test.fixtures import terminal_factory

    sentinel = "private-terminal-response-sentinel"
    monkeypatch.setattr(
        terminal_factory.requests,
        "post",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=500,
            text=f"Initialization timed out; {sentinel}",
        ),
    )

    with pytest.raises(RuntimeError, match="HTTP 500") as error:
        terminal_factory.TerminalFactory.create(
            SimpleNamespace(url="http://127.0.0.1:12345"), provider="mock_cli"
        )

    assert sentinel not in str(error.value)


def test_live_provider_profile_stage_pins_model_without_changing_prompt(
    tmp_path: Path,
) -> None:
    from test.e2e.conftest import _stage_live_provider_profiles
    from test.fixtures.cao_server import LiveProviderAuthorization

    import frontmatter

    source = Path("src/cli_agent_orchestrator/agent_store/developer.md")
    original = frontmatter.loads(source.read_text(encoding="utf-8"))
    authorization = LiveProviderAuthorization(
        name="codex",
        model="gpt-reviewed-test",
        binary="codex",
        auth_home=tmp_path / "auth-home",
        auth_files=(Path("login/auth.json"),),
    )
    server = SimpleNamespace(home_dir=tmp_path / "server-home")

    _stage_live_provider_profiles(server, authorization, profile_names=("developer",))

    staged = frontmatter.loads(
        (server.home_dir / ".aws/cli-agent-orchestrator/agent-store/developer.md").read_text(
            encoding="utf-8"
        )
    )
    assert staged.content == original.content
    assert staged.metadata["provider"] == "codex"
    assert staged.metadata["model"] == "gpt-reviewed-test"


def test_headless_remote_main_smoke_has_a_separate_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test.e2e.test_headless_ci import _headless_remote_smoke_requested

    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.delenv("CAO_RUN_HEADLESS_REMOTE_SMOKE", raising=False)
    assert not _headless_remote_smoke_requested()

    monkeypatch.setenv("CAO_RUN_HEADLESS_REMOTE_SMOKE", "1")
    assert _headless_remote_smoke_requested()


def test_live_provider_opt_in_does_not_start_remote_headless_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test.e2e import test_headless_ci

    monkeypatch.setenv("CAO_RUN_LIVE_PROVIDER_TESTS", "1")
    monkeypatch.delenv("CAO_RUN_HEADLESS_REMOTE_SMOKE", raising=False)
    monkeypatch.setattr(
        test_headless_ci.shutil,
        "which",
        lambda command: pytest.fail(f"remote smoke reached CLI lookup: {command}"),
    )

    with pytest.raises(pytest.skip.Exception, match="remote uvx @main MCP server"):
        test_headless_ci.test_headless_ci_run_script_exits_clean(
            SimpleNamespace(), tmp_path, monkeypatch
        )


def test_headless_opencode_installs_only_the_pinned_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test.e2e import test_headless_ci

    import frontmatter

    auth_home = tmp_path / "auth-home"
    auth_file = auth_home / "login" / "auth.json"
    auth_file.parent.mkdir(parents=True)
    auth_file.write_text("fixture-test-only\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "runner-home"))
    monkeypatch.setenv("CAO_RUN_HEADLESS_REMOTE_SMOKE", "1")
    monkeypatch.setenv("CAO_HEADLESS_REMOTE_PROVIDER", "opencode_cli")
    monkeypatch.setenv("CAO_HEADLESS_REMOTE_REF", "c" * 40)
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_AUTH_HOME", str(auth_home))
    monkeypatch.setenv("CAO_REAL_PROVIDER_E2E_PROVIDERS", _manifest("opencode_cli"))
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", "headless-config-sentinel")
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "headless-route-sentinel")
    monkeypatch.setenv("AWS_REGION", "headless-region-sentinel")
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "/headless/credentials-sentinel")
    monkeypatch.setenv(
        "AWS_CONTAINER_CREDENTIALS_FULL_URI", "http://127.0.0.1/headless-credentials"
    )
    monkeypatch.setenv("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE", "/headless/token-file-sentinel")
    monkeypatch.setenv("AWS_CONTAINER_AUTHORIZATION_TOKEN", "headless-token-sentinel")
    monkeypatch.setenv("AWS_EC2_METADATA_SERVICE_ENDPOINT", "http://127.0.0.1/headless-metadata")
    monkeypatch.setenv("AWS_EC2_METADATA_SERVICE_ENDPOINT_MODE", "IPv6")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "false")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "headless-project-sentinel")
    monkeypatch.setattr(test_headless_ci.shutil, "which", lambda command: f"/bin/{command}")

    server_home = tmp_path / "server-home"
    server_home.mkdir(mode=0o700)
    server = SimpleNamespace(
        url="http://127.0.0.1:12345",
        port=12345,
        home_dir=server_home,
        database_config_path=tmp_path / "database-config.json",
    )
    source_profile = Path("examples/headless-ci/ci_developer.md")
    source_bytes = source_profile.read_bytes()
    installed_profiles: list[dict[str, object]] = []

    def _post(url: str, *, json: dict[str, str], timeout: int) -> SimpleNamespace:
        assert url == f"{server.url}/agents/profiles/install"
        staged = server_home / ".aws/cli-agent-orchestrator/agent-store/ci_developer.md"
        metadata = frontmatter.loads(staged.read_text(encoding="utf-8")).metadata
        installed_profiles.append(metadata["mcpServers"]["cao-mcp-server"])
        assert json == {"source": "ci_developer", "provider": "opencode_cli"}
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(test_headless_ci.requests, "post", _post)
    captured_runs: list[dict[str, object]] = []

    def _run(
        args: list[str], *, cwd: str, env: dict[str, str], **kwargs: object
    ) -> SimpleNamespace:
        captured_runs.append({"args": args, "cwd": cwd, "env": env})
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(test_headless_ci.subprocess, "run", _run)

    test_headless_ci.test_headless_ci_run_script_exits_clean(server, tmp_path, monkeypatch)

    assert len(installed_profiles) == 1
    assert installed_profiles[0]["command"] == "env"
    assert installed_profiles[0]["args"][0:4] == ["-u", "PYTHONPATH", "uvx", "--from"]
    assert installed_profiles[0]["args"][4] == (
        "git+https://github.com/awslabs/cli-agent-orchestrator.git@" + "c" * 40
    )
    assert source_profile.read_bytes() == source_bytes
    assert len(captured_runs) == 1
    child_env = captured_runs[0]["env"]
    assert isinstance(child_env, dict)
    assert "PYTHONPATH" not in child_env
    assert "CAO_RUN_HEADLESS_REMOTE_SMOKE" not in child_env
    assert "CAO_HEADLESS_REMOTE_REF" not in child_env
    for name in (
        "OPENCODE_CONFIG_CONTENT",
        "CLAUDE_CODE_USE_VERTEX",
        "AWS_REGION",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN",
        "AWS_EC2_METADATA_SERVICE_ENDPOINT",
        "AWS_EC2_METADATA_SERVICE_ENDPOINT_MODE",
        "AWS_PROFILE",
        "GOOGLE_CLOUD_PROJECT",
    ):
        assert name not in child_env
    assert child_env["OPENCODE_CONFIG"] == ""
    assert child_env["AWS_EC2_METADATA_DISABLED"] == "true"
    assert child_env["OPENCODE_CONFIG_DIR"] == str(
        tmp_path / "headless-cli-home" / ".config" / "opencode"
    )


def test_headless_smoke_pins_remote_source_and_clears_local_pythonpath(
    tmp_path: Path,
) -> None:
    from test.e2e import test_headless_ci

    import frontmatter

    source = Path("examples/headless-ci/ci_developer.md")
    staged = tmp_path / "ci_developer.md"
    staged.write_bytes(source.read_bytes())
    reference = "a" * 40

    test_headless_ci._pin_headless_remote_ref(staged, reference)

    original = frontmatter.loads(source.read_text(encoding="utf-8"))
    result = frontmatter.loads(staged.read_text(encoding="utf-8"))
    server = result.metadata["mcpServers"]["cao-mcp-server"]
    assert result.content == original.content
    assert server["command"] == "env"
    assert server["args"] == [
        "-u",
        "PYTHONPATH",
        "uvx",
        "--from",
        "git+https://github.com/awslabs/cli-agent-orchestrator.git@" + reference,
        "cao-mcp-server",
    ]
    assert original.metadata["mcpServers"]["cao-mcp-server"]["command"] == "uvx"

    with pytest.raises(ValueError, match="full lowercase Git object ID"):
        test_headless_ci._validate_headless_remote_ref("main")
