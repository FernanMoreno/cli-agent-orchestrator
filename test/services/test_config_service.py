"""Tests for config_service — the unified ConfigService (issue #357).

Covers the precedence chain (env > file > default), legacy config.json +
settings.json migration, and the memory.compile_mode env/file conflict named
in the issue.
"""

import json
import os
import subprocess
import sys

import pytest

from cli_agent_orchestrator.services import config_service as cs
from cli_agent_orchestrator.services import settings_service as ss
from cli_agent_orchestrator.services.config_service import ConfigService


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """Redirect both the unified file and the legacy config.json to temp paths.

    Prevents a real dev/CI machine's ``~/.aws/cli-agent-orchestrator/{settings,config}.json``
    from leaking into these tests, and clears every env var this module reads
    so each test starts from a clean slate.
    """
    fake_settings = tmp_path / "settings.json"
    fake_legacy = tmp_path / "config.json"
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.settings_service.SETTINGS_FILE", fake_settings
    )
    monkeypatch.setattr("cli_agent_orchestrator.services.settings_service.CAO_HOME_DIR", tmp_path)
    monkeypatch.setattr(cs, "LEGACY_CONFIG_FILE", fake_legacy)
    monkeypatch.setattr(ss, "_server_settings_cache", None)
    for env_name in cs.ENV_REGISTRY:
        monkeypatch.delenv(env_name, raising=False)
    return {"settings": fake_settings, "legacy": fake_legacy}


class TestPrecedence:
    """CLI override > CAO_* env var > settings.json > built-in default."""

    def test_returns_builtin_default_when_nothing_set(self, _isolated_settings):
        assert ConfigService.get("terminal.backend") == "tmux"

    def test_file_value_beats_default(self, _isolated_settings):
        _isolated_settings["settings"].write_text(json.dumps({"terminal": {"backend": "herdr"}}))
        assert ConfigService.get("terminal.backend") == "herdr"

    def test_env_var_beats_file_value(self, _isolated_settings, monkeypatch):
        _isolated_settings["settings"].write_text(json.dumps({"terminal": {"backend": "herdr"}}))
        monkeypatch.setenv("CAO_TERMINAL_BACKEND", "tmux")
        assert ConfigService.get("terminal.backend") == "tmux"

    def test_cli_override_beats_env_var(self, _isolated_settings, monkeypatch):
        monkeypatch.setenv("CAO_TERMINAL_BACKEND", "herdr")
        assert ConfigService.get("terminal.backend", override="tmux") == "tmux"

    def test_env_var_beats_default_when_no_file(self, _isolated_settings, monkeypatch):
        monkeypatch.setenv("CAO_MCP_APPS_ENABLED", "true")
        assert ConfigService.get("apps.enabled") is True

    def test_invalid_env_value_falls_back_to_file(self, _isolated_settings, monkeypatch):
        """A malformed env value (e.g. non-numeric int) is ignored, not raised."""
        _isolated_settings["settings"].write_text(
            json.dumps({"server": {"mcp_request_timeout": 45}})
        )
        monkeypatch.setenv("CAO_MCP_REQUEST_TIMEOUT", "not-a-number")
        assert ConfigService.get("server.mcp_request_timeout") == 45


class TestLegacyMigration:
    """On first read, legacy config.json's terminal_backend/herdr_session
    should be folded into the unified settings.json under 'terminal'."""

    def test_migrates_legacy_config_json_into_settings_json(self, _isolated_settings):
        _isolated_settings["legacy"].write_text(
            json.dumps({"terminal_backend": "herdr", "herdr_session": "my-sess"})
        )
        assert ConfigService.get("terminal.backend") == "herdr"
        assert ConfigService.get("terminal.herdr_session") == "my-sess"

        # The migration persisted into settings.json itself.
        on_disk = json.loads(_isolated_settings["settings"].read_text())
        assert on_disk["terminal"] == {"backend": "herdr", "herdr_session": "my-sess"}

    def test_no_migration_when_settings_json_already_has_terminal_section(self, _isolated_settings):
        """Once migrated (or hand-configured), the legacy file is not re-read."""
        _isolated_settings["settings"].write_text(json.dumps({"terminal": {"backend": "tmux"}}))
        _isolated_settings["legacy"].write_text(json.dumps({"terminal_backend": "herdr"}))
        assert ConfigService.get("terminal.backend") == "tmux"

    def test_missing_legacy_file_is_a_noop(self, _isolated_settings):
        assert not _isolated_settings["legacy"].exists()
        assert ConfigService.get("terminal.backend") == "tmux"

    def test_malformed_legacy_file_falls_back_to_default(self, _isolated_settings):
        _isolated_settings["legacy"].write_text("not valid json {{{")
        assert ConfigService.get("terminal.backend") == "tmux"


class TestMemoryCompileModeConflict:
    """CAO_MEMORY_COMPILE_MODE must win over a conflicting settings.json value."""

    def test_env_var_wins_over_file_value(self, _isolated_settings, monkeypatch):
        _isolated_settings["settings"].write_text(json.dumps({"memory": {"compile_mode": "llm"}}))
        monkeypatch.setenv("CAO_MEMORY_COMPILE_MODE", "append")
        assert ConfigService.get("memory.compile_mode") == "append"

    def test_file_value_used_when_no_env_var(self, _isolated_settings):
        _isolated_settings["settings"].write_text(
            json.dumps({"memory": {"compile_mode": "append"}})
        )
        assert ConfigService.get("memory.compile_mode") == "append"

    def test_default_llm_when_neither_set(self, _isolated_settings):
        assert ConfigService.get("memory.compile_mode") == "llm"


class TestGetConfig:
    """ConfigService.get_config() assembles a validated CAOConfig."""

    def test_assembles_full_typed_config(self, _isolated_settings, monkeypatch):
        _isolated_settings["settings"].write_text(
            json.dumps(
                {
                    "terminal": {"backend": "herdr", "herdr_session": "s1"},
                    "server": {"mcp_request_timeout": 99},
                }
            )
        )
        monkeypatch.setenv("CAO_MEMORY_COMPILE_MODE", "append")
        cfg = ConfigService.get_config()
        assert cfg.terminal.backend == "herdr"
        assert cfg.terminal.herdr_session == "s1"
        assert cfg.server.mcp_request_timeout == 99
        assert cfg.memory.compile_mode == "append"
        # Untouched sections keep built-in defaults.
        assert cfg.apps.enabled is False
        assert cfg.logging.level == "INFO"


class TestSetAndPath:
    def test_set_persists_and_get_reads_it_back(self, _isolated_settings):
        ConfigService.set("terminal.backend", "herdr")
        assert ConfigService.get("terminal.backend") == "herdr"
        on_disk = json.loads(_isolated_settings["settings"].read_text())
        assert on_disk["terminal"]["backend"] == "herdr"

    def test_set_agents_extra_dirs_routes_through_settings_service(self, _isolated_settings):
        ConfigService.set("agents.extra_dirs", ["/a", "/b"])
        assert ConfigService.get("agents.extra_dirs") == ["/a", "/b"]

    def test_path_returns_settings_service_settings_file(self, _isolated_settings):
        assert ConfigService.path() == _isolated_settings["settings"]


class TestListAll:
    def test_list_all_includes_known_sections(self, _isolated_settings):
        result = ConfigService.list_all()
        assert "terminal.backend" in result
        assert "memory.compile_mode" in result
        assert "server.mcp_request_timeout" in result
        assert result["terminal.backend"] == "tmux"


class TestVersionedResolution:
    @pytest.mark.parametrize(
        "path,env,raw,expected",
        [
            ("server.provider_init_timeout", "CAO_PROVIDER_INIT_TIMEOUT", "-2", 120),
            ("server.state_buffer_max", "CAO_STATE_BUFFER_MAX", "0", 32768),
            ("memory.flush_threshold", "CAO_MEMORY_FLUSH_THRESHOLD", "2", 0.85),
            ("memory.compile_mode", "CAO_MEMORY_COMPILE_MODE", "bogus", "llm"),
            ("memory.compile_mode", "CAO_MEMORY_COMPILE_MODE", " APPEND ", "append"),
        ],
    )
    def test_registry_does_not_bypass_runtime_validation(
        self, monkeypatch, path, env, raw, expected
    ):
        monkeypatch.setenv(env, raw)
        assert ConfigService.get(path) == expected

    def test_server_env_change_invalidates_cache(self, monkeypatch):
        assert ss.get_server_settings()["provider_init_timeout"] == 120
        monkeypatch.setenv("CAO_PROVIDER_INIT_TIMEOUT", "41")
        assert ss.get_server_settings()["provider_init_timeout"] == 41
        assert ConfigService.get("server.provider_init_timeout") == 41

    def test_invalid_app_env_falls_back_to_file(self, _isolated_settings, monkeypatch):
        _isolated_settings["settings"].write_text('{"apps": {"enabled": true}}')
        monkeypatch.setenv("CAO_MCP_APPS_ENABLED", "typo")
        assert ConfigService.get("apps.enabled") is True

    def test_logging_case_remains_compatible_with_runtime(self, monkeypatch):
        monkeypatch.setenv("CAO_LOG_LEVEL", "debug")
        assert str(ConfigService.get("logging.level")).upper() == "DEBUG"

    def test_auth_file_remains_inert_and_auth0_precedence_unchanged(self, monkeypatch):
        from cli_agent_orchestrator.security import auth

        monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
        ConfigService.set("auth.jwks_uri", "https://file.invalid/jwks")
        assert auth.is_auth_enabled() is False
        monkeypatch.setenv("AUTH0_DOMAIN", "tenant.example")
        assert auth.get_jwks_uri() == "https://tenant.example/.well-known/jwks.json"
        monkeypatch.setenv("CAO_AUTH_JWKS_URI", "https://generic.example/jwks")
        assert auth.get_jwks_uri() == "https://generic.example/jwks"

    @pytest.mark.parametrize(
        "path,value",
        [
            ("server.state_buffer_max", 0),
            ("server.mcp_request_timeout", True),
            ("server.max_terminals", -1),
            ("terminal.backend", "unknown"),
            ("apps.enabled", "false"),
            ("logging.level", "invalid"),
            ("memory.compile_timeout_s", float("inf")),
            ("memory.compile_mode", "invalid"),
        ],
    )
    def test_invalid_write_preserves_file(self, _isolated_settings, path, value):
        file = _isolated_settings["settings"]
        file.write_text('{"future": {"opaque": [1, 2]}}')
        before = file.read_bytes()
        with pytest.raises(ValueError):
            ConfigService.set(path, value)
        assert file.read_bytes() == before

    def test_new_file_settings_round_trip_preserving_unknown_keys(self, _isolated_settings):
        file = _isolated_settings["settings"]
        file.write_text('{"memory": {"future_option": [1]}, "future": {"opaque": true}}')
        ConfigService.set("memory.compile_mode", "append")
        ConfigService.set("memory.compile_timeout_s", 12.5)
        ConfigService.set("server.max_terminals", 2)
        ConfigService.set("workflow.require_approval", True)
        assert json.loads(file.read_text())["memory"]["future_option"] == [1]
        assert json.loads(file.read_text())["future"] == {"opaque": True}
        assert ConfigService.get("memory.compile_timeout_s") == 12.5
        assert ConfigService.get("server.max_terminals") == 2
        assert ConfigService.get("workflow.require_approval") is True

    def test_typed_view_includes_effective_parent_gates_and_registered_settings(self, monkeypatch):
        monkeypatch.setenv("CAO_MAX_TERMINALS", "2")
        monkeypatch.setenv("CAO_STATE_BUFFER_MAX", "50000")
        monkeypatch.setenv("CAO_MEMORY_ENABLED", "false")
        monkeypatch.setenv("CAO_MEMORY_LEARNING_ENABLED", "true")
        monkeypatch.setenv("CAO_MEMORY_INSTRUCTION_PROMOTION_ENABLED", "true")
        cfg = ConfigService.get_config()
        assert cfg.server.state_buffer_max == 50000
        assert cfg.server.max_terminals == 2
        assert cfg.memory.learning_enabled is False
        assert cfg.memory.instruction_promotion_enabled is False
        assert cfg.memory.workflow_journal_retention_days == 30
        assert cfg.workflow.require_approval is False
        values = ConfigService.list_all()
        assert values["server.max_terminals"] == 2
        assert values["memory.learning_enabled"] is False

    def test_security_exceptions_survive_unification(self, monkeypatch):
        ConfigService.set("memory.lint_enabled", False)
        ConfigService.set("workflow.require_approval", True)
        monkeypatch.setenv("CAO_MEMORY_LINT_ENABLED", "true")
        monkeypatch.setenv("CAO_WORKFLOW_REQUIRE_APPROVAL", "false")
        assert ConfigService.get("memory.lint_enabled") is False
        assert ConfigService.get("workflow.require_approval") is True

    def test_registry_reports_security_and_worker_lifecycle_without_credentials(self, monkeypatch):
        monkeypatch.setenv("CAO_AUTH_LOCAL_TOKEN", "do-not-export-this-secret")
        manifest = ConfigService.registry()
        assert manifest["version"] >= 1
        entries = manifest["options"]
        assert entries["server.max_terminals"]["env"] == "CAO_MAX_TERMINALS"
        assert entries["auth.jwks_uri"]["runtime_source"] == "environment_only"
        assert entries["terminal.backend"]["application"] == "restart"
        assert entries["server.provider_init_timeout"]["existing_workers"] == "unchanged"
        external = manifest["external_environment"]
        assert external["CAO_AUTH_LOCAL_TOKEN"]["secret"] is True
        assert external["CAO_HOME_DIR"]["application"] == "restart"
        assert external["CAO_WORKFLOW_RUN_ID"]["runtime_context"] is True
        assert external["CAO_PROFILE_ALLOWED_HOSTS"]["consumer"] == "services.install_service"
        assert "do-not-export-this-secret" not in json.dumps(manifest)

    def test_restarted_reader_sees_file_and_new_env_existing_process_keeps_its_env(
        self, _isolated_settings, monkeypatch
    ):
        ConfigService.set("server.provider_init_timeout", 44)
        code = (
            "import sys; from pathlib import Path; "
            "from cli_agent_orchestrator.services import settings_service as s; "
            "from cli_agent_orchestrator.services.config_service import ConfigService as C; "
            "s.SETTINGS_FILE=Path(sys.argv[1]); "
            "print(C.get('server.provider_init_timeout'), flush=True); "
            "input(); print(C.get('server.provider_init_timeout'), flush=True)"
        )
        with subprocess.Popen(
            [sys.executable, "-c", code, str(_isolated_settings["settings"])],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env=dict(os.environ),
        ) as worker:
            assert worker.stdout.readline().strip() == "44"
            monkeypatch.setenv("CAO_PROVIDER_INIT_TIMEOUT", "55")
            assert ConfigService.get("server.provider_init_timeout") == 55
            output, _ = worker.communicate("\n", timeout=10)
            assert output.strip() == "44"
            assert worker.returncode == 0
