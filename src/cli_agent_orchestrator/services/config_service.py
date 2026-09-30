"""ConfigService — single reader for CAO's unified configuration.

Unifies the two config surfaces named in issue #357:

- ``~/.aws/cli-agent-orchestrator/settings.json`` (the ``agent_dirs``,
  ``extra_agent_dirs``, ``extra_skill_dirs``, ``server``, ``memory`` sections
  already served by :mod:`services.settings_service`)
- ``~/.aws/cli-agent-orchestrator/config.json`` (``terminal_backend`` /
  ``herdr_session``, previously read inline by ``backends/factory.py``)

into one ``settings.json`` file plus a single ``CAO_*`` env-var registry,
resolved through one precedence chain everywhere:

    CLI flag > CAO_* env var > config file > built-in default

``ConfigService.get()`` is the front door. For sections already backed by
tested logic in ``settings_service`` (agents, skills, server, memory) it
delegates there so existing validation/clamping behavior is preserved
byte-for-byte. For the sections that had no home before this issue
(terminal, apps, auth, network, logging) it owns the file section directly
under the *nested* schema described in issue #357.

``.env`` handling (``utils/env.py``) is out of scope — untouched.
"""

import json
import logging
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from cli_agent_orchestrator.constants import CAO_HOME_DIR
from cli_agent_orchestrator.services.vault.config import VaultConfig

logger = logging.getLogger(__name__)


# =============================================================================
# Schema — the typed shape of the unified config (issue #357). Each section
# below is a Pydantic model so the schema is self-documenting and validated;
# ``get_config()`` assembles one from resolved values (env > file > default).
# =============================================================================


class AgentsConfig(BaseModel):
    dirs: Dict[str, str] = Field(default_factory=dict)
    extra_dirs: List[str] = Field(default_factory=list)
    disabled_dirs: List[str] = Field(default_factory=list)
    roles: Dict[str, List[str]] = Field(default_factory=dict)


class SkillsConfig(BaseModel):
    extra_dirs: List[str] = Field(default_factory=list)


class ServerConfig(BaseModel):
    mcp_request_timeout: int = 30
    event_bus_max_queue_size: int = 1024
    provider_init_timeout: int = 120
    startup_prompt_handler_timeout: int = 20
    state_buffer_max: int = 32768
    max_terminals: Optional[int] = None


class MemoryConfig(BaseModel):
    enabled: bool = True
    compile_mode: str = "llm"
    flush_threshold: float = 0.85
    compile_timeout_s: float = 120.0
    lint_enabled: bool = True
    vault: VaultConfig = Field(default_factory=VaultConfig)
    learning_enabled: bool = False
    instruction_promotion_enabled: bool = False
    workflow_journal_capture_output: bool = False
    workflow_journal_output_cap_bytes: int = 8192
    workflow_journal_retention_days: int = 30
    workflow_journal_retention_count: int = 100


class WorkflowConfig(BaseModel):
    require_approval: bool = False


class TerminalConfig(BaseModel):
    backend: str = "tmux"
    herdr_session: str = "cao"


class AppsConfig(BaseModel):
    enabled: bool = False
    static_dir: Optional[str] = None


class NetworkConfig(BaseModel):
    """env-var only; settings.json values are not yet honored.

    ``constants.py`` builds ``CORS_ORIGINS``/``ALLOWED_HOSTS``/``WS_ALLOWED_CLIENTS``
    as module-level lists at import time and Starlette's CORS/TrustedHost
    middleware are instantiated once, holding a reference to those exact list
    objects (``add_local_cors_origins`` relies on this — see constants.py).
    Rewiring them through ConfigService would require either mutating those
    lists after settings.json changes (no invalidation mechanism exists yet)
    or restructuring the middleware wiring — out of scope for this PR. Only
    the ``CAO_ALLOWED_HOSTS``/``CAO_CORS_ORIGINS``/``CAO_WS_ALLOWED_CLIENTS``/
    ``CAO_WS_ALLOWED_ORIGINS`` env vars are read (in ``constants.py``, not
    through this schema).
    """

    allowed_hosts: List[str] = Field(default_factory=list)
    cors_origins: List[str] = Field(default_factory=list)
    ws_allowed_clients: List[str] = Field(default_factory=list)
    ws_allowed_origins: List[str] = Field(default_factory=list)


class AuthConfig(BaseModel):
    """env-var only; settings.json values are not yet honored.

    ``security/auth.py`` is the actual authentication *enforcement* boundary
    (not a UX gate) and is kept on direct ``os.getenv`` reads to avoid
    changing security-critical resolution behavior in this PR. See the
    "Config note" in that module's docstring.
    """

    jwks_uri: str = ""
    audience: str = ""
    issuer: str = ""


class LoggingConfig(BaseModel):
    level: str = "INFO"


class CAOConfig(BaseModel):
    """The full unified schema. See issue #357."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    skills: SkillsConfig = Field(default_factory=SkillsConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    terminal: TerminalConfig = Field(default_factory=TerminalConfig)
    apps: AppsConfig = Field(default_factory=AppsConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    workflow: WorkflowConfig = Field(default_factory=WorkflowConfig)


# Deprecated second surface. Read once for migration, then ignored.
LEGACY_CONFIG_FILE = CAO_HOME_DIR / "config.json"

# Dotted schema path -> on-disk key path, for the two sections whose
# settings_service-managed keys predate the nested "agents"/"skills"
# schema and are stored flat. Every other section (server, memory,
# terminal, apps, auth, network, logging) nests 1:1 with its dotted path.
_LEGACY_KEY_MAP: Dict[str, Tuple[str, ...]] = {
    "agents.dirs": ("agent_dirs",),
    "agents.extra_dirs": ("extra_agent_dirs",),
    "agents.disabled_dirs": ("disabled_agent_dirs",),
    "agents.roles": ("roles",),
    "skills.extra_dirs": ("extra_skill_dirs",),
}

# Built-in defaults for the sections this module owns directly (terminal,
# apps, auth, network, logging). Agents/skills/server/memory defaults live in
# settings_service, which this module delegates to for those sections.
_OWNED_DEFAULTS: Dict[str, Any] = {
    "terminal.backend": "tmux",
    "terminal.herdr_session": "cao",
    "apps.enabled": False,
    "apps.static_dir": None,
    "auth.jwks_uri": "",
    "auth.audience": "",
    "auth.issuer": "",
    "logging.level": "INFO",
    "network.allowed_hosts": [],
    "network.cors_origins": [],
    "network.ws_allowed_clients": [],
    "network.ws_allowed_origins": [],
}

# Env-var registry: every CAO_* var this schema recognizes, mapped to its
# config path, value type, and default. Backs both ``get()``'s env-precedence
# tier and the ``cao config list`` introspection view. Types: "str", "bool",
# "int", "float", "list" (comma-separated).
ENV_REGISTRY: Dict[str, Tuple[str, str, Any]] = {
    "CAO_TERMINAL_BACKEND": ("terminal.backend", "str", "tmux"),
    "CAO_HERDR_SESSION": ("terminal.herdr_session", "str", "cao"),
    "CAO_MCP_APPS_ENABLED": ("apps.enabled", "bool", False),
    "CAO_MCP_APPS_STATIC_DIR": ("apps.static_dir", "str", None),
    "CAO_AUTH_JWKS_URI": ("auth.jwks_uri", "str", ""),
    "CAO_AUTH_AUDIENCE": ("auth.audience", "str", ""),
    "CAO_AUTH_ISSUER": ("auth.issuer", "str", ""),
    "CAO_LOG_LEVEL": ("logging.level", "str", "INFO"),
    "CAO_ALLOWED_HOSTS": ("network.allowed_hosts", "list", []),
    "CAO_CORS_ORIGINS": ("network.cors_origins", "list", []),
    "CAO_WS_ALLOWED_CLIENTS": ("network.ws_allowed_clients", "list", []),
    "CAO_WS_ALLOWED_ORIGINS": ("network.ws_allowed_origins", "list", []),
    "CAO_MEMORY_ENABLED": ("memory.enabled", "bool", True),
    "CAO_MEMORY_LINT_ENABLED": ("memory.lint_enabled", "bool", True),
    "CAO_MEMORY_COMPILE_MODE": ("memory.compile_mode", "str", "llm"),
    "CAO_MEMORY_FLUSH_THRESHOLD": ("memory.flush_threshold", "float", 0.85),
    "CAO_MEMORY_VAULT_ENABLED": ("memory.vault.enabled", "bool", False),
    "CAO_MCP_REQUEST_TIMEOUT": ("server.mcp_request_timeout", "int", 30),
    "CAO_EVENT_BUS_MAX_QUEUE_SIZE": ("server.event_bus_max_queue_size", "int", 1024),
    "CAO_PROVIDER_INIT_TIMEOUT": ("server.provider_init_timeout", "int", 120),
    "CAO_STARTUP_PROMPT_HANDLER_TIMEOUT": (
        "server.startup_prompt_handler_timeout",
        "int",
        20,
    ),
    "CAO_STATE_BUFFER_MAX": ("server.state_buffer_max", "int", 32768),
    "CAO_MAX_TERMINALS": ("server.max_terminals", "int", None),
    "CAO_MEMORY_LEARNING_ENABLED": ("memory.learning_enabled", "bool", False),
    "CAO_MEMORY_INSTRUCTION_PROMOTION_ENABLED": (
        "memory.instruction_promotion_enabled",
        "bool",
        False,
    ),
    "CAO_WORKFLOW_REQUIRE_APPROVAL": ("workflow.require_approval", "bool", False),
}

# Reverse index: dotted path -> env var name, for get()'s env-precedence lookup.
_PATH_TO_ENV: Dict[str, str] = {path: env for env, (path, _, _) in ENV_REGISTRY.items()}

# Version describes this registry contract, not the user's settings document.
# Unknown stored keys remain untouched for forward/backward compatibility.
CONFIG_REGISTRY_VERSION = 1


@dataclass(frozen=True)
class ConfigOption:
    kind: Literal["str", "bool", "int", "float", "list", "dict"]
    default: Any
    env: Optional[str] = None
    runtime_source: Literal["settings", "environment_only"] = "settings"
    precedence: str = "override > environment > file > default"
    application: Literal["next_read", "restart"] = "next_read"
    # This field describes propagation of process environment. Shared-file
    # readers can observe later writes; already constructed consumers cannot.
    existing_workers: str = "inherited_environment_unchanged"


@dataclass(frozen=True)
class ExternalEnvironmentOption:
    """Inventory, not a new resolver: consumer owns parsing/defaults/enforcement."""

    consumer: str
    kind: Literal["str", "bool", "int", "float", "list"] = "str"
    application: Literal["next_read", "restart", "spawn"] = "restart"
    secret: bool = False
    runtime_context: bool = False
    runtime_source: str = "environment_only"
    existing_workers: str = "unchanged"


# Explicit consumer ownership avoids activating inert auth/network file settings
# or treating injected identity and credentials as editable user preferences.
EXTERNAL_ENV_REGISTRY: Dict[str, ExternalEnvironmentOption] = {
    "CAO_HOME_DIR": ExternalEnvironmentOption("constants"),
    "CAO_AGENTS_DIR": ExternalEnvironmentOption("constants"),
    "CAO_API_HOST": ExternalEnvironmentOption("constants"),
    "CAO_API_PORT": ExternalEnvironmentOption("constants", "int"),
    "CAO_GRAPH_EXPORT_ROOT": ExternalEnvironmentOption("constants", application="next_read"),
    "CAO_PYTE_STATUS": ExternalEnvironmentOption("constants", "bool"),
    "CAO_PYTE_MIDBURST_PROBE_S": ExternalEnvironmentOption("constants", "float"),
    "CAO_EAGER_INBOX_DELIVERY": ExternalEnvironmentOption("constants", "bool"),
    "CAO_FORWARDED_ALLOW_IPS": ExternalEnvironmentOption("constants", "list"),
    "CAO_PROFILE_ALLOWED_HOSTS": ExternalEnvironmentOption(
        "services.install_service", "list", "next_read"
    ),
    "AUTH0_DOMAIN": ExternalEnvironmentOption("security.auth", application="next_read"),
    "AUTH0_AUDIENCE": ExternalEnvironmentOption("security.auth", application="next_read"),
    "CAO_AUTH_LOCAL_TOKEN": ExternalEnvironmentOption(
        "security.auth", application="next_read", secret=True
    ),
    "CAO_PROJECT_ID": ExternalEnvironmentOption("services.memory_service", application="next_read"),
    "CAO_MEMORY_API_URL": ExternalEnvironmentOption(
        "services.memory_gateway", application="next_read"
    ),
    "CAO_ENABLE_WORKING_DIRECTORY": ExternalEnvironmentOption("mcp_server.server", "bool"),
    "CAO_ENABLE_SENDER_ID_INJECTION": ExternalEnvironmentOption("utils.orchestration", "bool"),
    "CAO_AGUI_ENABLED": ExternalEnvironmentOption("services.agui_enablement", "bool"),
    "CAO_AGUI_HEARTBEAT_SECONDS": ExternalEnvironmentOption("services.agui.run_plane", "float"),
    "CAO_ELASTIC_BROKER_URL": ExternalEnvironmentOption("utils.fleet", application="next_read"),
    "CAO_ELASTIC_BROKER_TOKEN": ExternalEnvironmentOption(
        "utils.fleet", application="next_read", secret=True
    ),
    "CAO_ELASTIC_WORKER_READY_WAIT": ExternalEnvironmentOption(
        "mcp_server.server", "float", "next_read"
    ),
    "CAO_ADVERTISED_URL": ExternalEnvironmentOption("utils.orchestration", application="next_read"),
    "CAO_ELASTIC_CALLBACK_URL": ExternalEnvironmentOption(
        "mcp_server.server", application="next_read"
    ),
    "CAO_TMP_DIR": ExternalEnvironmentOption("providers.cursor_cli", application="next_read"),
    "CAO_MOCK_CLI_SCRIPTED_PROMPTS": ExternalEnvironmentOption(
        "providers.mock_cli", "bool", "next_read"
    ),
    "GROK_HOME": ExternalEnvironmentOption("providers.grok_cli", application="next_read"),
    "GEMINI_CLI_SYSTEM_SETTINGS_PATH": ExternalEnvironmentOption(
        "providers.gemini_cli", application="next_read"
    ),
    "MINIMAX_DATA_DIR": ExternalEnvironmentOption(
        "providers.minimax_code", application="next_read"
    ),
    "CLAUDE_CODE_OAUTH_TOKEN": ExternalEnvironmentOption(
        "providers.claude_code", application="next_read", secret=True
    ),
    "XDG_CONFIG_HOME": ExternalEnvironmentOption("backends.herdr_backend", application="next_read"),
    "ProgramData": ExternalEnvironmentOption("providers.gemini_cli", application="next_read"),
    "SHELL": ExternalEnvironmentOption("cli.commands.terminal", application="next_read"),
    "PATH": ExternalEnvironmentOption(
        "services.script_runner", application="spawn", runtime_context=True
    ),
    "HOME": ExternalEnvironmentOption(
        "services.script_runner", application="spawn", runtime_context=True
    ),
    "OTEL_SDK_DISABLED": ExternalEnvironmentOption("telemetry", "bool"),
}
for _name in (
    "CAO_PIPE_LIVENESS_TAIL_LINES",
    "CAO_PIPE_LIVENESS_STALL_CHECKS",
    "CAO_PIPE_LIVENESS_MAX_REARM_FAILURES",
    "CAO_PIPE_LIVENESS_MAX_COLD_START_ATTEMPTS",
    "CAO_PIPE_LIVENESS_MAX_PROBE_FAILURES",
):
    EXTERNAL_ENV_REGISTRY[_name] = ExternalEnvironmentOption("constants", "int")
for _name in ("CAO_PIPE_LIVENESS_CHECK_INTERVAL_S", "CAO_PIPE_LIVENESS_COLD_START_GRACE_S"):
    EXTERNAL_ENV_REGISTRY[_name] = ExternalEnvironmentOption("constants", "float")
for _name in (
    "CAO_HERMES_IDLE_PROMPT_REGEX",
    "CAO_HERMES_IDLE_LOG_REGEX",
    "CAO_HERMES_PROCESSING_REGEX",
    "CAO_HERMES_USER_PREFIX_REGEX",
    "CAO_HERMES_ASSISTANT_HEADER_REGEX",
):
    EXTERNAL_ENV_REGISTRY[_name] = ExternalEnvironmentOption("providers.hermes")
EXTERNAL_ENV_REGISTRY["CAO_HERMES_MAX_STABLE_IDLE_POLLS"] = ExternalEnvironmentOption(
    "providers.hermes", "int"
)
for _name in (
    "CAO_CALLBACK_URL",
    "CAO_CALLBACK_TERMINAL_ID",
    "CAO_ELASTIC_WORKER_ID",
    "CAO_ELASTIC_RELEASE_TOKEN",
    "CAO_TERMINAL_ID",
    "CAO_SESSION_NAME",
    "CAO_WORKFLOW_RUN_ID",
    "CAO_WORKFLOW_STEP_ID",
    "CAO_WORKFLOW_GENERATION",
    "CAO_API_BASE_URL",
    "CAO_WORKFLOW_INPUTS",
    "CAO_WORKFLOW_RESUME",
):
    EXTERNAL_ENV_REGISTRY[_name] = ExternalEnvironmentOption(
        "runtime_context",
        application="spawn",
        runtime_context=True,
        secret=_name == "CAO_ELASTIC_RELEASE_TOKEN",
    )


CONFIG_REGISTRY: Dict[str, ConfigOption] = {
    path: ConfigOption(kind, default, env) for env, (path, kind, default) in ENV_REGISTRY.items()
}
CONFIG_REGISTRY.update(
    {
        "agents.dirs": ConfigOption("dict", {}),
        "agents.extra_dirs": ConfigOption("list", []),
        "agents.disabled_dirs": ConfigOption("list", []),
        "agents.roles": ConfigOption("dict", {}),
        "skills.extra_dirs": ConfigOption("list", []),
        "memory.vault": ConfigOption("dict", {}),
        "memory.compile_timeout_s": ConfigOption("float", 120.0),
        "memory.workflow_journal_capture_output": ConfigOption("bool", False),
        "memory.workflow_journal_output_cap_bytes": ConfigOption("int", 8192),
        "memory.workflow_journal_retention_days": ConfigOption("int", 30),
        "memory.workflow_journal_retention_count": ConfigOption("int", 100),
    }
)
for _path, _option in tuple(CONFIG_REGISTRY.items()):
    if _path.startswith(("auth.", "network.")):
        CONFIG_REGISTRY[_path] = replace(
            _option,
            runtime_source="environment_only",
            precedence="consumer-owned environment; file is schema-only",
            application="restart" if _path.startswith("network.") else "next_read",
        )
    elif _path.startswith(("terminal.", "apps.", "logging.")) or _path in (
        "server.event_bus_max_queue_size",
        "server.state_buffer_max",
    ):
        CONFIG_REGISTRY[_path] = replace(_option, application="restart")
for _path, _precedence in {
    "memory.lint_enabled": "override > either-source-false veto > default",
    "workflow.require_approval": "override > environment-enable-only > file > default",
    "memory.learning_enabled": "override > memory parent gate > environment > file > default",
    "memory.instruction_promotion_enabled": "override > learning parent gate > environment > file > default",
}.items():
    CONFIG_REGISTRY[_path] = replace(CONFIG_REGISTRY[_path], precedence=_precedence)
CONFIG_REGISTRY["server.provider_init_timeout"] = replace(
    CONFIG_REGISTRY["server.provider_init_timeout"], existing_workers="unchanged"
)


def _validate_value(path: str, value: Any) -> Any:
    """Validate known writes/overrides before touching storage; keep unknown keys."""
    option = CONFIG_REGISTRY.get(path)
    if option is None:
        return value
    if value is None and path in ("apps.static_dir", "server.max_terminals"):
        return None
    kind = option.kind
    valid = {
        "bool": isinstance(value, bool),
        "int": isinstance(value, int) and not isinstance(value, bool),
        "float": isinstance(value, (int, float)) and not isinstance(value, bool),
        "str": isinstance(value, str),
        "list": isinstance(value, list) and all(isinstance(v, str) for v in value),
        "dict": isinstance(value, dict),
    }[kind]
    if not valid:
        raise ValueError(f"{path} must be {kind}")
    if kind in ("int", "float"):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        minimum = (
            0
            if path
            in ("memory.workflow_journal_retention_days", "memory.workflow_journal_retention_count")
            else None
        )
        if (minimum == 0 and value < 0) or (minimum is None and value <= 0):
            raise ValueError(f"{path} is out of range")
        if path == "memory.flush_threshold" and value > 1:
            raise ValueError(f"{path} must be <= 1")
    choices = {
        "terminal.backend": ("tmux", "herdr"),
        "memory.compile_mode": ("llm", "append"),
        "logging.level": (
            "DEBUG",
            "INFO",
            "WARNING",
            "WARN",
            "ERROR",
            "CRITICAL",
            "FATAL",
            "NOTSET",
        ),
    }.get(path)
    if path == "logging.level":
        value = value.upper()
    if choices is not None and value not in choices:
        raise ValueError(f"{path} must be one of {choices}")
    return value


_migration_logged = False


def _coerce_env_value(raw: str, kind: str) -> Any:
    if kind == "bool":
        normalized = raw.strip().lower()
        if normalized not in ("1", "true", "yes", "0", "false", "no"):
            raise ValueError("expected a boolean")
        return normalized in ("1", "true", "yes")
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    if kind == "list":
        return [item.strip() for item in raw.split(",") if item.strip()]
    return raw


def _get_nested(data: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    node: Any = data
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _set_nested(data: Dict[str, Any], keys: Tuple[str, ...], value: Any) -> None:
    node = data
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value


def _settings_file() -> Path:
    """Return the unified settings file path.

    Delegates to ``settings_service.SETTINGS_FILE`` (rather than duplicating
    the constant) so both modules always agree on one path, including in
    tests that patch ``settings_service.SETTINGS_FILE``.
    """
    from cli_agent_orchestrator.services import settings_service

    return settings_service.SETTINGS_FILE


def _load_raw() -> Dict[str, Any]:
    """Load the unified settings file, migrating legacy config.json in place.

    On first run after upgrading, if a legacy ``config.json`` exists and the
    unified file has no ``terminal`` section yet, its ``terminal_backend`` /
    ``herdr_session`` keys are copied into ``settings.json`` under
    ``terminal`` and the move is logged. ``config.json`` itself is left on
    disk (untouched) but is no longer read once migrated.

    This is a plain read-modify-write with no file lock: it assumes a single
    CAO process touches ``settings.json`` at a time (the same assumption
    ``settings_service._load``/``_save`` already make). A concurrent ``set()``
    from a second process during migration could be lost. Acceptable for a
    single-operator local tool; would need a lock (e.g. ``filelock``) if CAO
    ever supports multiple concurrent writers to the same settings file.
    """
    global _migration_logged

    settings_file = _settings_file()
    data: Dict[str, Any] = {}
    if settings_file.exists():
        try:
            loaded = json.loads(settings_file.read_text())
            if isinstance(loaded, dict):
                data = loaded
        except Exception as e:
            logger.warning(f"Failed to read {settings_file}: {e}")

    if "terminal" not in data and LEGACY_CONFIG_FILE.exists():
        try:
            legacy = json.loads(LEGACY_CONFIG_FILE.read_text())
        except Exception as e:
            logger.warning(f"Failed to read legacy {LEGACY_CONFIG_FILE}: {e}")
            legacy = {}
        if isinstance(legacy, dict) and legacy:
            terminal_section = {}
            if "terminal_backend" in legacy:
                terminal_section["backend"] = legacy["terminal_backend"]
            if "herdr_session" in legacy:
                terminal_section["herdr_session"] = legacy["herdr_session"]
            if terminal_section:
                data["terminal"] = terminal_section
                _save_raw(data)
                if not _migration_logged:
                    logger.info(
                        f"Migrated legacy {LEGACY_CONFIG_FILE} into {settings_file} "
                        "under the 'terminal' key. config.json is deprecated; "
                        "this migration runs once."
                    )
                    _migration_logged = True
    return data


def _save_raw(data: Dict[str, Any]) -> None:
    settings_file = _settings_file()
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(data, indent=2))


def _get_from_file(path: str) -> Any:
    """Resolve a dotted path from the unified file, honoring legacy flat keys."""
    data = _load_raw()
    keys = _LEGACY_KEY_MAP.get(path)
    if keys is not None:
        return _get_nested(data, keys)
    return _get_nested(data, tuple(path.split(".")))


def _get_owned_section(path: str, default: Any) -> Any:
    """Delegate reads for agents/skills/server/memory to settings_service."""
    from cli_agent_orchestrator.services import settings_service

    section, _, key = path.partition(".")
    if path == "agents.dirs":
        return settings_service.get_agent_dirs()
    if path == "agents.extra_dirs":
        return settings_service.get_extra_agent_dirs()
    if path == "agents.disabled_dirs":
        return settings_service.get_disabled_agent_dirs()
    if path == "agents.roles":
        data = settings_service._load()
        # Nested format: {"agents": {"roles": {...}}}
        nested = data.get("agents", {})
        if isinstance(nested, dict) and "roles" in nested and isinstance(nested["roles"], dict):
            return nested["roles"]
        # Legacy flat format: {"roles": {...}}
        return data.get("roles", {})
    if path == "skills.extra_dirs":
        return settings_service.get_extra_skill_dirs()
    if section == "server":
        if key == "max_terminals":
            return settings_service.get_max_terminals()
        return settings_service.get_server_settings().get(key, default)
    if section == "memory":
        if key == "enabled":
            return settings_service.is_memory_enabled()
        if key == "lint_enabled":
            return settings_service.is_memory_lint_enabled()
        if key == "compile_mode":
            return settings_service.get_compile_mode()
        if key == "compile_timeout_s":
            return settings_service.get_compile_timeout_s()
        if key == "learning_enabled":
            return settings_service.is_learning_enabled()
        if key == "instruction_promotion_enabled":
            return settings_service.is_instruction_promotion_enabled()
        if path in CONFIG_REGISTRY:
            default = CONFIG_REGISTRY[path].default
        return settings_service.get_memory_settings().get(key, default)
    if path == "workflow.require_approval":
        return settings_service.is_workflow_approval_required()
    raise KeyError(path)


_OWNED_SECTIONS = frozenset({"agents", "skills", "server", "memory", "workflow"})


def _get_value(path: str, default: Any = None, override: Optional[Any] = None) -> Any:
    """Resolve a config value: CLI-flag override > env var > file > default.

    ``path`` is a dotted schema path, e.g. ``"terminal.backend"`` or
    ``"apps.enabled"``. ``override`` represents an explicit CLI-flag value
    from the caller (e.g. ``cao-server --terminal herdr``) and, when not
    ``None``, always wins.
    """
    if override is not None:
        # BackendFactory owns ConfigurationError (including CLI error handling).
        # Preserve its fail-fast contract instead of silently selecting tmux.
        if path == "terminal.backend":
            return override
        return _validate_value(path, override)

    if path == "memory.vault" or path == "memory.vault.enabled":
        from cli_agent_orchestrator.services import settings_service

        try:
            vault = settings_service.get_vault_config()
        except ValueError as exc:
            logger.warning("Invalid vault configuration disabled for config lookup: %s", exc)
            vault = VaultConfig()
        return vault.model_dump(mode="json") if path == "memory.vault" else vault.enabled
    # Delegate before generic env coercion: these readers own validation and
    # safety exceptions (lint veto, approval enable-only, learning parent gates).
    section = path.split(".", 1)[0]
    if section in _OWNED_SECTIONS:
        try:
            value = _get_owned_section(path, default)
        except KeyError:
            value = None
        return value if value is not None else default

    env_name = _PATH_TO_ENV.get(path)
    if env_name is not None:
        import os

        raw = os.environ.get(env_name)
        if raw is not None and raw != "":
            _, kind, _ = ENV_REGISTRY[env_name]
            try:
                value = _coerce_env_value(raw, kind)
                return value if path == "terminal.backend" else _validate_value(path, value)
            except ValueError:
                logger.warning(
                    f"Ignoring invalid {env_name}={raw!r} for {path}; using file/default"
                )

    file_value = _get_from_file(path)
    if file_value is not None:
        if path == "terminal.backend":
            return file_value
        try:
            return _validate_value(path, file_value)
        except ValueError:
            logger.warning("Ignoring invalid setting %s; using default", path)
    return _OWNED_DEFAULTS.get(path, default)


def _set_value(path: str, value: Any) -> Any:
    """Persist a config value under its dotted schema path.

    Agents/skills sections route through settings_service's existing
    setters (preserving their validation); all other sections write
    directly into the unified file's nested structure.
    """
    from cli_agent_orchestrator.services import settings_service

    # Existing memory setters own their validation/error contract.
    if not path.startswith("memory."):
        value = _validate_value(path, value)
    if path == "agents.extra_dirs":
        return settings_service.set_extra_agent_dirs(value)
    if path == "agents.disabled_dirs":
        return settings_service.set_disabled_agent_dirs(value)
    if path == "skills.extra_dirs":
        return settings_service.set_extra_skill_dirs(value)
    if path.startswith("agents.dirs."):
        provider = path.split(".", 2)[2]
        return settings_service.set_agent_dirs({provider: value})
    if path == "agents.roles" or path.startswith("agents.roles."):
        # Write both nested and flat for backward compat
        data = _load_raw()
        if path == "agents.roles":
            roles = value
        else:
            role_name = path.split(".", 2)[2]
            roles = data.get("roles", {})
            if not isinstance(roles, dict):
                roles = {}
            roles[role_name] = value
        # Nested format
        agents_section = data.get("agents", {})
        if not isinstance(agents_section, dict):
            agents_section = {}
        agents_section["roles"] = roles
        data["agents"] = agents_section
        # Flat format
        data["roles"] = roles
        _save_raw(data)
        return roles
    if path.startswith("memory."):
        key = path.split(".", 1)[1]
        return settings_service.set_memory_setting(key, value)

    keys = _LEGACY_KEY_MAP.get(path, tuple(path.split(".")))
    data = _load_raw()
    _set_nested(data, keys, value)
    _save_raw(data)
    return value


_ALL_PATHS = sorted(CONFIG_REGISTRY)


class ConfigService:
    """Single reader/writer for CAO's unified configuration.

    Stateless — every method re-resolves from env/file on each call (settings
    files are small and infrequently read; see ``settings_service.get_server_settings``
    for the hot-path cache keyed by file identity, mtime and environment).
    """

    @staticmethod
    def registry() -> Dict[str, Any]:
        """Describe support and application timing, never effective credentials.

        next_read means a fresh reader observes updates, not that a previously
        created consumer is mutated. Process environment is inherited at spawn;
        changing the supervisor environment never updates existing workers.
        Env-only security consumers retain their own resolution contracts.
        """
        return {
            "version": CONFIG_REGISTRY_VERSION,
            "options": {path: asdict(option) for path, option in CONFIG_REGISTRY.items()},
            "external_environment": {
                name: asdict(option) for name, option in EXTERNAL_ENV_REGISTRY.items()
            },
        }

    @staticmethod
    def get(path: str, default: Any = None, override: Optional[Any] = None) -> Any:
        """Resolve ``path`` via CLI override > CAO_* env var > file > default."""
        return _get_value(path, default=default, override=override)

    @staticmethod
    def set(path: str, value: Any) -> Any:
        """Persist ``value`` at dotted schema ``path`` in the unified settings file."""
        return _set_value(path, value)

    @staticmethod
    def path() -> Path:
        """Return the absolute path to the unified settings file."""
        return _settings_file()

    @staticmethod
    def list_all() -> Dict[str, Any]:
        """Return every known config path resolved to its effective value.

        Reflects the same precedence ``get()`` uses (env beats file beats
        default). Intended for ``cao config list`` and debugging.
        """
        values = {
            path: _get_value(path)
            for path in _ALL_PATHS
            if path not in {"memory.vault", "memory.vault.enabled"}
        }
        vault = _get_value("memory.vault")
        values["memory.vault"] = vault
        values["memory.vault.enabled"] = vault["enabled"]
        return values

    @staticmethod
    def get_config() -> CAOConfig:
        """Assemble and validate the full typed config from resolved values."""
        return CAOConfig(
            agents=AgentsConfig(
                dirs=_get_value("agents.dirs", default={}),
                extra_dirs=_get_value("agents.extra_dirs", default=[]),
                disabled_dirs=_get_value("agents.disabled_dirs", default=[]),
                roles=_get_value("agents.roles", default={}),
            ),
            skills=SkillsConfig(extra_dirs=_get_value("skills.extra_dirs", default=[])),
            server=ServerConfig(
                state_buffer_max=_get_value("server.state_buffer_max", default=32768),
                max_terminals=_get_value("server.max_terminals"),
                mcp_request_timeout=_get_value("server.mcp_request_timeout", default=30),
                event_bus_max_queue_size=_get_value(
                    "server.event_bus_max_queue_size", default=1024
                ),
                provider_init_timeout=_get_value("server.provider_init_timeout", default=120),
                startup_prompt_handler_timeout=_get_value(
                    "server.startup_prompt_handler_timeout", default=20
                ),
            ),
            memory=MemoryConfig(
                learning_enabled=_get_value("memory.learning_enabled"),
                instruction_promotion_enabled=_get_value("memory.instruction_promotion_enabled"),
                workflow_journal_capture_output=_get_value(
                    "memory.workflow_journal_capture_output"
                ),
                workflow_journal_output_cap_bytes=_get_value(
                    "memory.workflow_journal_output_cap_bytes"
                ),
                workflow_journal_retention_days=_get_value(
                    "memory.workflow_journal_retention_days"
                ),
                workflow_journal_retention_count=_get_value(
                    "memory.workflow_journal_retention_count"
                ),
                enabled=_get_value("memory.enabled", default=True),
                lint_enabled=_get_value("memory.lint_enabled", default=True),
                compile_mode=_get_value("memory.compile_mode", default="llm"),
                flush_threshold=_get_value("memory.flush_threshold", default=0.85),
                compile_timeout_s=_get_value("memory.compile_timeout_s", default=120.0),
                vault=_get_value("memory.vault", default={}),
            ),
            terminal=TerminalConfig(
                backend=_get_value("terminal.backend", default="tmux"),
                herdr_session=_get_value("terminal.herdr_session", default="cao"),
            ),
            apps=AppsConfig(
                enabled=_get_value("apps.enabled", default=False),
                static_dir=_get_value("apps.static_dir", default=None),
            ),
            network=NetworkConfig(
                allowed_hosts=_get_value("network.allowed_hosts", default=[]),
                cors_origins=_get_value("network.cors_origins", default=[]),
                ws_allowed_clients=_get_value("network.ws_allowed_clients", default=[]),
                ws_allowed_origins=_get_value("network.ws_allowed_origins", default=[]),
            ),
            auth=AuthConfig(
                jwks_uri=_get_value("auth.jwks_uri", default=""),
                audience=_get_value("auth.audience", default=""),
                issuer=_get_value("auth.issuer", default=""),
            ),
            logging=LoggingConfig(level=_get_value("logging.level", default="INFO")),
            workflow=WorkflowConfig(require_approval=_get_value("workflow.require_approval")),
        )
