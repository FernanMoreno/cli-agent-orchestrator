"""Gemini CLI provider.

This adapter deliberately does *not* revive CAO's retired Gemini integration.
That implementation rewrote ``~/.gemini/settings.json`` and temporary
``GEMINI.md`` files in the user's checkout.  Those paths are shared by every
Gemini process, so concurrent CAO children could inherit one another's MCP
identity or leave state behind after a failed cleanup.

Instead every terminal gets a private, mode-0600 runtime ``settings.json``
under ``CAO_HOME_DIR``.  Gemini's documented
``GEMINI_CLI_SYSTEM_SETTINGS_PATH`` override selects that file only for the
one launched process.  The process still starts in the real project working
directory and keeps the user's normal authentication mechanism (OAuth cache,
``GEMINI_API_KEY``, or Vertex credentials) untouched.

The public Gemini CLI contract used here is intentionally small and stable:

* ``gemini --approval-mode=yolo`` for unattended tool execution;
* ``--model`` for an explicit model selection;
* ``-i`` / ``--prompt-interactive`` for role bootstrap while keeping a REPL;
* ``/quit`` for a graceful REPL exit; and
* ``settings.json`` ``mcpServers`` for MCP discovery.

The adapter does not materialise CAO-managed or profile-provided secrets in a
command line or its runtime file. An existing Gemini system policy is a
separate administrator-owned input which Gemini requires CAO to copy into its
per-terminal system override; CAO refuses administrator-required MCP server
configuration until it can model that contract without weakening isolation.
``CAO_TERMINAL_ID`` is propagated to each profile MCP subprocess by a
documented environment-variable reference, not copied as a literal terminal
identity.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import stat
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from cli_agent_orchestrator.backends.registry import get_backend
from cli_agent_orchestrator.constants import CAO_HOME_DIR
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.models.terminal import TerminalInputBlockedError, TerminalStatus
from cli_agent_orchestrator.providers.base import BaseProvider
from cli_agent_orchestrator.utils.agent_profiles import (
    load_agent_profile_unresolved,
)
from cli_agent_orchestrator.utils.atomic_file import locked_atomic_write
from cli_agent_orchestrator.utils.mcp_resolution import resolve_mcp_server_config
from cli_agent_orchestrator.utils.terminal import wait_for_shell
from cli_agent_orchestrator.utils.text import strip_terminal_escapes

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Raised when a Gemini-specific launch invariant cannot be satisfied."""


# Gemini has used two documented Ink composer layouts.  Current builds render
# a bare ``>`` compositor; earlier builds render ``* Type your message ...``.
# Keep both while they are covered by captured historical fixtures. Processing
# and permission markers take precedence over either composer: it can remain
# drawn while a model/tool turn is active.
_IDLE_COMPOSER_PATTERN = re.compile(r"^\s*>\s*$")
_LEGACY_IDLE_COMPOSER_PATTERN = re.compile(
    r"^\s*\*\s+Type your message(?:\s+or\s+@path/to/file)?\s*$", re.IGNORECASE
)
_LEGACY_ACTIVE_COMPOSER_PATTERN = re.compile(r"^\s*\*\s+\S.*$")
# Gemini's user echo begins with ``> ``, but Markdown blockquotes in an
# assistant answer use the same glyph.  A line match alone is consequently
# never sufficient to establish a turn boundary; the adapter pairs it with
# the active receipt echo below before trusting it for a receipt-governed turn.
_QUERY_PATTERN = re.compile(r"^\s*>\s+(?P<text>\S.*)$")
_PROCESSING_PATTERN = re.compile(
    r"^\s*(?:Responding\s+with\b|.*\besc\s+to\s+cancel\b)", re.IGNORECASE
)
_SPINNER_PATTERN = re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏].*\(esc to cancel", re.IGNORECASE)
_WAITING_PATTERN = re.compile(
    r"(?:do you trust this (?:folder|workspace)|"
    r"allow execution of|allow once|allow for this session|"
    r"(?:select|choose) (?:an )?(?:authentication|login) method|"
    r"sign in with google|paste (?:your )?(?:api )?key|"
    r"(?:↑|\^)(?:/|\s*)(?:↓|v).*?(?:select|navigate)|"
    r"press enter to (?:confirm|continue)|\[\s*y\s*/\s*n\s*\])",
    re.IGNORECASE,
)
_ERROR_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:error:|error\s{2,}|fatal:|"
    r"authentication failed|invalid configuration|unauthenticated|"
    r"you do not have permission|failed to (?:start|connect|load)|"
    r"(?:gemini:\s*)?(?:command not found|not found)|"
    r"CAO_GEMINI_CLI_NOT_FOUND|unknown (?:option|argument|flag))",
    re.IGNORECASE,
)
_SEPARATOR_PATTERN = re.compile(r"^\s*[▄▀─]{20,}\s*$")
_CHROME_PATTERN = re.compile(
    r"(?:^\s*(?:Gemini CLI\b|Authenticated with\b|Responding with\b|"
    r"Tip:|\? for shortcuts|YOLO mode\b|[✓✗●▸])|"
    r"^\s*\.\.\./.*\b(?:sandbox|Auto)\b|"
    r"\(esc to cancel|^\s*[▄▀─]{20,}\s*$)",
    re.IGNORECASE,
)

_BOOTSTRAP_READY_MARKER = "CAO_GEMINI_BOOTSTRAP_READY"
_BOOTSTRAP_READY_PATTERN = re.compile(rf"(?m)^\s*(?:✦\s*)?{_BOOTSTRAP_READY_MARKER}\s*$")
_CLI_NOT_FOUND_MARKER = "CAO_GEMINI_CLI_NOT_FOUND"
_TURN_RECEIPT_PREFIX = "CAO_GEMINI_TURN_"
_TURN_RECEIPT_BYTES = 16  # 128 bits: prevents accidental/stale receipt matches.
_TURN_RECEIPT_PATTERN = re.compile(rf"^{_TURN_RECEIPT_PREFIX}[0-9a-f]{{32}}$")
_TURN_RECEIPT_TOKEN_PATTERN = re.compile(rf"{_TURN_RECEIPT_PREFIX}[0-9a-f]{{32}}")
_TURN_RECEIPT_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TURN_GENERATION_PATTERN = re.compile(r"^[0-9a-f]{32}$")

# Gemini CLI's schema warns that underscores in an MCP server alias can make
# policy parsing ambiguous.  Profiles may predate that rule, so reject rather
# than silently launch a server whose tools cannot be reliably policy-gated.
_MCP_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
# A profile MCP environment is copied to a terminal-private JSON file.  Make
# every profile-supplied value a Gemini-side reference rather than trying to
# infer whether a name such as ``TOKEN`` or ``LOG_LEVEL`` is secret.  That
# keeps CAO from serialising a literal credential merely because it used an
# unfamiliar name.  The adapter adds its own non-secret terminal reference
# after this validation.
_MCP_ENV_REFERENCE_PATTERN = re.compile(
    r"^\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})$"
)
# Gemini's MCP schema evolves independently of CAO.  The adapter writes a
# durable, terminal-private system settings file, so it must not pass unknown
# profile fields through and hope a particular Gemini version interprets them
# safely. Version 1 is intentionally limited to the transport fields CAO can
# validate, map into a container, and keep credential-free. In particular,
# arbitrary ``headers`` are not supported: they would let a profile serialize
# a literal Authorization/API-key value outside the validated ``env`` map.
_GEMINI_MCP_PROFILE_FIELDS_V1 = frozenset({"command", "args", "cwd", "env", "url", "httpUrl"})
_MCP_SENSITIVE_ARGUMENT_PATTERN = re.compile(
    # Match complete hyphen/underscore-delimited flag components, not only a
    # sensitive word immediately after ``--``. Real CLI wrappers commonly use
    # ``--github-token``, ``--private-token``, ``--access-token``, or
    # ``--x-api-key``; each would otherwise serialize a literal credential in
    # terminal-private settings. Requiring a component boundary keeps ordinary
    # non-secret words such as ``--tokenizer`` out of the conservative block.
    r"^(?:-H(?:$|=|.)|--?(?:[A-Za-z0-9]+[-_])*"
    r"(?:api[-_]?key|auth(?:orization)?|bearer|cookie|credential|header(?:s)?|"
    r"password|secret|token)(?:[-_][A-Za-z0-9]+)*(?:=|$))",
    re.IGNORECASE,
)
_TERMINAL_ID_MAX_BYTES = 512


class GeminiCliProvider(BaseProvider):
    """Run an interactive Gemini CLI session in a CAO terminal.

    A profile's MCP configuration is materialised in a terminal-private
    settings file.  The user project's cwd is never changed, no ``GEMINI.md``
    is written, and authentication stays in Gemini's own supported stores.
    """

    # The renderer/parser is intentionally staged behind the default raw
    # monitor path until we capture a real Gemini transcript for the installed
    # version.  Opting in early would turn a guessed TUI footer into a global
    # completion signal -- precisely the terminal/task mismatch CAO must not
    # reintroduce.  Native backends still use BaseProvider's native state.
    supports_screen_detection = False
    supports_direct_status_probe = False
    requires_turn_receipt = True

    def __init__(
        self,
        terminal_id: str,
        session_name: str,
        window_name: str,
        agent_profile: Optional[str] = None,
        allowed_tools: Optional[list] = None,
        model: Optional[str] = None,
        skill_prompt: Optional[str] = None,
    ) -> None:
        super().__init__(terminal_id, session_name, window_name, allowed_tools, skill_prompt)
        self._agent_profile = agent_profile
        self._model = model
        self._initialized = False
        self._turns = 0
        self._runtime_dir: Optional[Path] = None
        self._runtime_settings_path: Optional[Path] = None
        self._pending_turn_receipt: Optional[str] = None
        self._pending_turn_receipt_sha256: Optional[str] = None
        self._pending_turn_generation: Optional[str] = None
        self._pending_prepared_input: Optional[str] = None
        self._restored_turn_receipt = False

    @property
    def paste_enter_count(self) -> int:
        """Gemini submits a settled bracketed paste with one Enter."""
        return 1

    @property
    def paste_submit_delay(self) -> float:
        """Give Gemini's Ink composer a moment to consume the paste end marker."""
        return 1.0

    @property
    def blocks_orchestrated_input_while_waiting_user_answer(self) -> bool:
        """A trust/login/approval picker must not receive a task as its answer."""
        return True

    def _runtime_paths(self) -> tuple[Path, Path]:
        """Return a path that cannot derive a filename from untrusted terminal text."""
        encoded_terminal_id = self.terminal_id.encode("utf-8")
        if not encoded_terminal_id or len(encoded_terminal_id) > _TERMINAL_ID_MAX_BYTES:
            raise ProviderError("invalid terminal identifier for Gemini runtime configuration")
        digest = hashlib.sha256(encoded_terminal_id).hexdigest()
        runtime_dir = CAO_HOME_DIR / "providers" / "gemini_cli" / digest
        return runtime_dir, runtime_dir / "settings.json"

    @staticmethod
    def _assert_inside(child: Path, parent: Path) -> None:
        """Reject an unexpected symlink escape before creating or deleting data."""
        resolved_parent = parent.resolve(strict=False)
        resolved_child = child.resolve(strict=False)
        try:
            resolved_child.relative_to(resolved_parent)
        except ValueError as exc:
            raise ProviderError("Gemini runtime path escapes CAO_HOME_DIR") from exc

    def _ensure_private_runtime_dir(self) -> tuple[Path, Path]:
        """Create and validate CAO-owned runtime storage with restrictive modes."""
        runtime_dir, settings_path = self._runtime_paths()
        provider_root = runtime_dir.parent
        cao_root = CAO_HOME_DIR.resolve(strict=False)
        self._assert_inside(provider_root, cao_root)

        for path in (CAO_HOME_DIR / "providers", provider_root, runtime_dir):
            if path.is_symlink():
                raise ProviderError(f"refusing symlinked Gemini runtime path: {path}")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.is_symlink():
                raise ProviderError(f"refusing symlinked Gemini runtime path: {path}")
            self._assert_inside(path, cao_root)
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass

        if settings_path.exists() and settings_path.is_symlink():
            raise ProviderError(f"refusing symlinked Gemini runtime settings: {settings_path}")
        self._runtime_dir = runtime_dir
        self._runtime_settings_path = settings_path
        return runtime_dir, settings_path

    @staticmethod
    def _system_settings_path() -> Optional[Path]:
        """Locate an existing Gemini system policy file without modifying it.

        CAO writes a private *copy* as the per-process system override.  This
        preserves enterprise ``admin`` controls instead of bypassing the real
        system settings just because a child needs an isolated MCP identity.
        """
        configured = os.environ.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH", "").strip()
        if configured:
            path = Path(configured).expanduser()
            if not path.is_file():
                raise ProviderError(
                    "GEMINI_CLI_SYSTEM_SETTINGS_PATH is set but is not a readable settings file"
                )
            return path
        if sys.platform.startswith("linux"):
            candidate = Path("/etc/gemini-cli/settings.json")
        elif sys.platform == "darwin":
            candidate = Path("/Library/Application Support/GeminiCli/settings.json")
        else:
            program_data = os.environ.get("ProgramData", r"C:\\ProgramData")
            candidate = Path(program_data) / "gemini-cli" / "settings.json"
        return candidate if candidate.is_file() else None

    def _read_system_policy(self) -> dict[str, Any]:
        """Read policy settings copied into a private per-terminal overlay."""
        source = self._system_settings_path()
        if source is None:
            return {}
        try:
            loaded = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProviderError(f"cannot read Gemini system settings {source}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ProviderError("Gemini system settings must be a JSON object")
        # JSON round-trip makes a detached JSON-compatible deep copy and rejects
        # accidental non-serializable test doubles at the boundary.
        return json.loads(json.dumps(loaded))

    @staticmethod
    def _validate_system_policy(settings: dict[str, Any]) -> None:
        """Fail closed when an existing system policy forbids CAO's posture."""
        security = settings.get("security")
        if security is not None and not isinstance(security, dict):
            raise ProviderError("Gemini system settings security section must be an object")
        if isinstance(security, dict) and security.get("disableYoloMode") is True:
            raise ProviderError(
                "Gemini system policy disables YOLO mode, which conflicts with CAO's "
                "unattended approval-mode=yolo launch"
            )
        mcp = settings.get("mcp")
        if mcp is not None and not isinstance(mcp, dict):
            raise ProviderError("Gemini system settings mcp section must be an object")
        if isinstance(mcp, dict):
            if mcp.get("enabled") is False:
                # The private overlay could still contain mcpServers and an
                # allowlist, but Gemini would silently disable all of them. That
                # would launch a worker unable to call CAO's coordination tools,
                # which is worse than rejecting a policy CAO cannot satisfy.
                raise ProviderError("Gemini system policy disables MCP servers")
            if mcp.get("serverCommand") not in (None, ""):
                # ``serverCommand`` is outside the profile-owned mcpServers
                # allowlist and can launch an ambient MCP endpoint even when
                # CAO's private list is otherwise narrow. Removing it would
                # weaken an administrator policy; copying it would make the
                # effective worker MCP set unprovable. Refuse both.
                raise ProviderError(
                    "Gemini system policy has mcp.serverCommand; CAO cannot safely "
                    "isolate its effective MCP servers"
                )
        admin = settings.get("admin")
        if admin is not None and not isinstance(admin, dict):
            raise ProviderError("Gemini system settings admin section must be an object")
        if isinstance(admin, dict):
            if admin.get("secureModeEnabled") is True:
                raise ProviderError(
                    "Gemini system policy enables secure mode, which forbids CAO's unattended "
                    "approval-mode=yolo launch"
                )
            mcp_admin = admin.get("mcp")
            if mcp_admin is not None and not isinstance(mcp_admin, dict):
                raise ProviderError("Gemini system settings admin.mcp section must be an object")
            if isinstance(mcp_admin, dict):
                if mcp_admin.get("enabled") is False:
                    raise ProviderError("Gemini system policy disables MCP servers")
                admin_config = mcp_admin.get("config")
                if admin_config is not None:
                    if not isinstance(admin_config, dict):
                        raise ProviderError(
                            "Gemini system settings admin.mcp.config must be an object"
                        )
                    if admin_config:
                        # Gemini applies administrative MCP config after the
                        # profile overlay. CAO cannot yet project that merged,
                        # version-specific effective configuration or prove it
                        # retains the per-terminal identity reference, so do
                        # not claim a profile-only MCP contract around it.
                        raise ProviderError(
                            "Gemini system policy has admin.mcp.config; CAO cannot safely "
                            "account for administrator-configured MCP servers"
                        )
                if mcp_admin.get("requiredConfig") is not None:
                    # Gemini can administratively inject a required MCP server
                    # independently of the profile.  CAO currently cannot
                    # enumerate it in the effective-MCP contract or prove its
                    # credential/configuration isolation, so preserving the
                    # opaque object in a terminal-private file would be a
                    # false claim that only profile-owned servers can run.
                    # Reject before the private overlay is written; a later
                    # version can support this only with an explicit,
                    # versioned admin-MCP projection and live matrix.
                    raise ProviderError(
                        "Gemini system policy has admin.mcp.requiredConfig; CAO cannot "
                        "safely isolate or account for administrator-required MCP servers"
                    )

    @staticmethod
    def _profile_server_config(server_config: Any) -> dict[str, Any]:
        """Accept CAO profile models/dicts without mutating their source object."""
        if isinstance(server_config, dict):
            config = dict(server_config)
        elif hasattr(server_config, "model_dump"):
            config = server_config.model_dump(exclude_none=True)
        else:
            raise ProviderError("Gemini profile MCP server configuration must be an object")
        if not isinstance(config, dict):
            raise ProviderError("Gemini profile MCP server configuration must be an object")
        return config

    @staticmethod
    def _looks_like_absolute_path(value: str) -> bool:
        """Recognise POSIX and drive-qualified host paths without guessing URLs."""
        return value.startswith("/") or bool(re.match(r"^[A-Za-z]:[\\/]", value))

    def _require_container_path(
        self, path: str, profile: Optional[AgentProfile], purpose: str
    ) -> str:
        """Translate a host-only path or fail before a container loses policy.

        ``BaseProvider._translate_path`` deliberately leaves an unknown path
        unchanged for legacy providers.  Gemini's private system-settings file
        contains its MCP allowlist and policy, so an unchanged host path inside
        a container would silently launch Gemini without that isolation.  A
        declared container profile therefore has to explicitly map every host
        path CAO puts in Gemini's runtime config.
        """
        if profile is None or profile.container is None:
            return path
        path_maps = profile.container.path_maps or []
        best_mapping = None
        best_host = ""
        for mapping in path_maps:
            raw_host = mapping.host
            if not raw_host:
                continue
            if raw_host in {"/", "\\"}:
                host = raw_host
                matches = path.startswith(raw_host)
            else:
                host = raw_host.rstrip("/\\")
                if not host:
                    continue
                boundary = host + ("\\" if "\\" in raw_host and "/" not in raw_host else "/")
                matches = path == host or path.startswith(boundary)
            if matches and (best_mapping is None or len(host) > len(best_host)):
                best_mapping = mapping
                best_host = host
        if best_mapping is None:
            raise ProviderError(
                f"Gemini container profile has no path map for {purpose}: {path}. "
                "Add an explicit host-to-guest path_maps entry before launch."
            )
        # Do this locally rather than calling BaseProvider._translate_path:
        # the base helper intentionally preserves legacy unmatched paths and
        # historically renders a root map with a doubled slash.  Here an
        # explicit map is a policy boundary, so normalize it deterministically.
        suffix = path[len(best_host) :].lstrip("/\\")
        guest = best_mapping.guest.rstrip("/\\") or best_mapping.guest
        separator = "\\" if "\\" in guest and "/" not in guest else "/"
        translated = guest if not suffix else guest + separator + suffix
        # An explicitly identity-mapped path is valid (for example a mounted
        # /opt/cao); a missing map is rejected above rather than inferred from
        # the translated value matching the source.
        return translated

    def _translate_mcp_container_paths(
        self, config: dict[str, Any], profile: Optional[AgentProfile], server_name: str
    ) -> dict[str, Any]:
        """Map explicit host paths in a Gemini MCP entry for container workers."""
        if profile is None or profile.container is None:
            return config
        translated = dict(config)
        for field in ("command", "cwd"):
            value = translated.get(field)
            if isinstance(value, str) and self._looks_like_absolute_path(value):
                translated[field] = self._require_container_path(
                    value, profile, f"MCP server '{server_name}' {field}"
                )
        args = translated.get("args")
        if args is not None:
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                raise ProviderError(f"Gemini MCP server '{server_name}' args must be a string list")
            mapped_args: list[str] = []
            for arg in args:
                if self._looks_like_absolute_path(arg):
                    mapped_args.append(
                        self._require_container_path(
                            arg, profile, f"MCP server '{server_name}' argument"
                        )
                    )
                    continue
                # Preserve command-line syntax while handling common
                # --config=/host/path and KEY=/host/path forms without trying
                # to rewrite arbitrary relative arguments or URLs.
                prefix, separator, value = arg.partition("=")
                if separator and self._looks_like_absolute_path(value):
                    mapped_args.append(
                        prefix
                        + separator
                        + self._require_container_path(
                            value, profile, f"MCP server '{server_name}' argument"
                        )
                    )
                else:
                    mapped_args.append(arg)
            translated["args"] = mapped_args
        return translated

    @staticmethod
    def _validate_mcp_server_shape(config: dict[str, Any], server_name: str) -> None:
        """Reject ambiguous MCP transport shapes before launcher resolution.

        ``resolve_mcp_server_config`` intentionally accepts legacy loose
        dictionaries.  That is useful for old providers, but a Gemini private
        settings file is a durable executable contract: a string ``args``
        would otherwise be coerced to one character per argument. Validate the
        profile-owned shape before any resolver can reinterpret it.
        """
        unsupported_fields = sorted(set(config).difference(_GEMINI_MCP_PROFILE_FIELDS_V1))
        if unsupported_fields:
            raise ProviderError(
                f"Gemini MCP server '{server_name}' uses unsupported v1 fields: "
                + ", ".join(unsupported_fields)
            )

        transport_keys = [
            key for key in ("command", "url", "httpUrl") if config.get(key) is not None
        ]
        if len(transport_keys) != 1:
            raise ProviderError(
                f"Gemini MCP server '{server_name}' needs exactly one of command, url, or httpUrl"
            )
        transport = transport_keys[0]
        value = config[transport]
        if not isinstance(value, str) or not value.strip():
            raise ProviderError(
                f"Gemini MCP server '{server_name}' {transport} must be a non-empty string"
            )
        # Gemini expands environment references in MCP configuration.  A
        # reference outside the dedicated ``env`` map can turn an otherwise
        # harmless command argument or URL path into an exfiltration endpoint
        # (for example ``https://host/${GEMINI_API_KEY}``).  There is no need
        # for non-env transport fields to be dynamic, and rejecting every
        # dollar sign rather than only today's documented spellings is the only
        # forward-compatible fail-closed boundary.  This runs before command
        # resolution or private settings creation, so the raw reference never
        # reaches a persisted Gemini configuration.
        if "$" in value:
            raise ProviderError(
                f"Gemini MCP server '{server_name}' {transport} may not contain '$' or "
                "environment interpolation; use the validated env map instead"
            )

        args = config.get("args")
        if args is not None:
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                raise ProviderError(f"Gemini MCP server '{server_name}' args must be a string list")
            if transport != "command":
                raise ProviderError(
                    f"Gemini MCP server '{server_name}' args require a command transport"
                )
            if any(_MCP_SENSITIVE_ARGUMENT_PATTERN.match(arg) for arg in args):
                raise ProviderError(
                    f"Gemini MCP server '{server_name}' cannot put credential-like command "
                    "arguments in terminal-private settings; use an env reference"
                )
            if any("$" in arg for arg in args):
                raise ProviderError(
                    f"Gemini MCP server '{server_name}' args may not contain '$' or "
                    "environment interpolation; use the validated env map instead"
                )

        cwd = config.get("cwd")
        if cwd is not None:
            if transport != "command":
                raise ProviderError(
                    f"Gemini MCP server '{server_name}' cwd requires a command transport"
                )
            if not isinstance(cwd, str) or not cwd.strip():
                raise ProviderError(
                    f"Gemini MCP server '{server_name}' cwd must be a non-empty string"
                )
            if "$" in cwd:
                raise ProviderError(
                    f"Gemini MCP server '{server_name}' cwd may not contain '$' or "
                    "environment interpolation; use the validated env map instead"
                )

        if transport in {"url", "httpUrl"}:
            endpoint = urlsplit(value)
            if endpoint.scheme not in {"http", "https"} or not endpoint.netloc:
                raise ProviderError(
                    f"Gemini MCP server '{server_name}' {transport} must be an absolute HTTP(S) URL"
                )
            if endpoint.username is not None or endpoint.password is not None:
                raise ProviderError(
                    f"Gemini MCP server '{server_name}' {transport} may not contain URL credentials"
                )
            if endpoint.query or endpoint.fragment:
                raise ProviderError(
                    f"Gemini MCP server '{server_name}' {transport} may not contain a query or fragment; "
                    "put credentials in a validated env reference instead"
                )

    def _runtime_mcp_servers(self, profile: Optional[AgentProfile]) -> dict[str, dict[str, Any]]:
        """Resolve profile MCP entries and attach process-local terminal identity."""
        configured = profile.mcpServers if profile is not None else {}
        configured = configured or {}
        if not isinstance(configured, dict):
            raise ProviderError("Gemini profile mcpServers must be an object")

        result: dict[str, dict[str, Any]] = {}
        canonical_names: dict[str, str] = {}
        for raw_name, raw_config in configured.items():
            if not isinstance(raw_name, str) or not _MCP_ALIAS_PATTERN.fullmatch(raw_name):
                raise ProviderError(
                    "Gemini MCP server names must use 1-128 letters, digits, or hyphens "
                    "and may not contain underscores"
                )
            canonical_name = raw_name.casefold()
            existing_name = canonical_names.get(canonical_name)
            if existing_name is not None:
                raise ProviderError(
                    "duplicate Gemini MCP server name under case-insensitive policy: "
                    f"{existing_name}, {raw_name}"
                )
            canonical_names[canonical_name] = raw_name
            # This config is persisted in a terminal-private settings file.
            # Its launcher must therefore survive an interpreter/venv upgrade
            # between this terminal's initial launch and a later restart.
            profile_config = self._profile_server_config(raw_config)
            self._validate_mcp_server_shape(profile_config, raw_name)
            config = resolve_mcp_server_config(profile_config, persisted=True)
            config = self._translate_mcp_container_paths(config, profile, raw_name)
            env = config.get("env", {})
            if env is None:
                env = {}
            if not isinstance(env, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in env.items()
            ):
                raise ProviderError(f"Gemini MCP server '{raw_name}' env must be a string map")
            invalid_env = [
                key for key, value in env.items() if not _MCP_ENV_REFERENCE_PATTERN.fullmatch(value)
            ]
            if invalid_env:
                formatted_keys = ", ".join(sorted(invalid_env))
                raise ProviderError(
                    f"Gemini MCP server '{raw_name}' env values must be $VAR or ${{VAR}} "
                    f"references, not literals (invalid: {formatted_keys})"
                )
            # Gemini intentionally redacts sensitive host variables unless a
            # config explicitly references them. This non-secret identity is
            # explicitly propagated *by reference* so each private process
            # gets its own value without serialising an ID into settings.json.
            env = dict(env)
            env["CAO_TERMINAL_ID"] = "$CAO_TERMINAL_ID"
            config["env"] = env
            result[raw_name] = config
        return result

    def _write_runtime_settings(self, profile: Optional[AgentProfile]) -> Path:
        """Materialise profile MCP config in a private per-terminal settings file.

        The settings file is an overlay of an existing system policy, not a
        mutation of it.  A system policy that has an explicit MCP allowlist is
        authoritative: CAO refuses to widen it rather than smuggling in a new
        orchestration server.
        """
        system_settings = self._read_system_policy()
        self._validate_system_policy(system_settings)
        # Validate policy before creating any private overlay. An unsupported
        # administrator-required MCP contract must leave no settings file for
        # a later worker to inherit accidentally.
        runtime_servers = self._runtime_mcp_servers(profile)
        if not runtime_servers:
            # Gemini only applies mcp.allowed when it contains a server name;
            # an empty list is not a proven deny-all for user/workspace MCP
            # scopes. Until a version-pinned live matrix demonstrates a real
            # deny-all grammar, launching an empty profile would be an
            # unbounded ambient-MCP worker. Require an explicit profile-owned
            # server rather than claim that {} is isolation.
            raise ProviderError(
                "Gemini requires at least one profile MCP server until CAO has a "
                "version-verified deny-all MCP policy"
            )

        # ``GEMINI_CLI_SYSTEM_SETTINGS_PATH`` replaces Gemini's system override
        # rather than adding a lower-precedence fragment.  Preserve every
        # system policy section (security, sandbox, extensions, telemetry,
        # tool allowlists, etc.) or a CAO child could accidentally bypass one.
        # The only deliberate exception is ``mcpServers``: carrying its ambient
        # definitions into this terminal-private file could expose unrelated
        # servers and copy their credentials.  We validate collisions below
        # and replace the map with exactly the profile-owned servers instead.
        settings: dict[str, Any] = json.loads(json.dumps(system_settings))
        settings.pop("mcpServers", None)

        current_servers = system_settings.get("mcpServers", {})
        if current_servers is None:
            current_servers = {}
        if not isinstance(current_servers, dict):
            raise ProviderError("Gemini system settings mcpServers must be an object")
        reserved_aliases: dict[str, str] = {}
        for raw_name in current_servers:
            if not isinstance(raw_name, str):
                raise ProviderError("Gemini system settings MCP server names must be strings")
            canonical_name = raw_name.casefold()
            existing_name = reserved_aliases.get(canonical_name)
            if existing_name is not None:
                raise ProviderError(
                    "Gemini system settings contain case-insensitively duplicate MCP server names: "
                    f"{existing_name}, {raw_name}"
                )
            reserved_aliases[canonical_name] = raw_name
        for name in runtime_servers:
            reserved_name = reserved_aliases.get(name.casefold())
            if reserved_name is not None:
                raise ProviderError(
                    "Gemini system settings already reserve MCP server alias "
                    f"'{reserved_name}' (conflicts with profile alias '{name}')"
                )
        settings["mcpServers"] = runtime_servers

        # System-level mcp.allowed is a real policy boundary.  Check that it
        # permits every profile server, but then narrow this *process-private*
        # configuration to exactly the profile set.  Keeping extra names from
        # a user/system allowlist would let a user or project MCP server leak
        # into a CAO worker despite the profile declaring none.
        mcp = settings.get("mcp", {})
        if mcp is None:
            mcp = {}
        if not isinstance(mcp, dict):
            raise ProviderError("Gemini system settings mcp section must be an object")
        configured_allowed = mcp.get("allowed")
        if configured_allowed is not None:
            if not isinstance(configured_allowed, list) or not all(
                isinstance(value, str) for value in configured_allowed
            ):
                raise ProviderError("Gemini system settings mcp.allowed must be a list of strings")
            # Gemini's settings loader resolves the allowlist against emitted
            # aliases exactly. Case-folding here would let CAO approve
            # ``CAO-ORCHESTRATOR`` yet write ``cao-orchestrator`` into the
            # private server map; the child would then launch without its
            # claimed coordination tools. Preserve the profile spelling and
            # require the system allowlist to authorize that exact alias.
            allowed_aliases = set(configured_allowed)
            missing = sorted(name for name in runtime_servers if name not in allowed_aliases)
            if missing:
                raise ProviderError(
                    "Gemini system MCP allowlist does not permit CAO profile servers: "
                    + ", ".join(missing)
                )
        configured_excluded = mcp.get("excluded")
        if configured_excluded is not None:
            if not isinstance(configured_excluded, list) or not all(
                isinstance(value, str) for value in configured_excluded
            ):
                raise ProviderError("Gemini system settings mcp.excluded must be a list of strings")
            excluded_aliases = {value.casefold() for value in configured_excluded}
            blocked = sorted(
                name for name in runtime_servers if name.casefold() in excluded_aliases
            )
            if blocked:
                raise ProviderError(
                    "Gemini system MCP exclusion blocks CAO profile servers: " + ", ".join(blocked)
                )
        mcp["allowed"] = sorted(runtime_servers)
        settings["mcp"] = mcp

        # Every policy/transport validation above intentionally happens before
        # creating private storage. A rejected launch must leave no settings
        # file or directory that a later worker could accidentally inherit.
        _, settings_path = self._ensure_private_runtime_dir()

        try:
            payload = json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        except (TypeError, ValueError) as exc:
            raise ProviderError(
                f"Gemini runtime settings are not JSON serializable: {exc}"
            ) from exc

        # Set 0600 on the temp file before the atomic replace. A chmod after
        # publication would leave a brief umask-controlled disclosure window
        # for the policy and private MCP configuration.
        locked_atomic_write(
            settings_path,
            payload,
            overwrite=True,
            mode=stat.S_IRUSR | stat.S_IWUSR,
        )
        self._runtime_settings_path = settings_path
        return settings_path

    def _load_profile(self) -> Optional[AgentProfile]:
        if self._agent_profile is None:
            return None
        # Everything on an AgentProfile can ultimately be serialized into the
        # Gemini process boundary: the role bootstrap is passed with ``-i``,
        # the selected model becomes ``--model``, and MCP/container values go
        # into the terminal-private settings file.  The ordinary CAO profile
        # loader expands ``${VAR}`` from CAO's managed .env first.  That is
        # useful for legacy providers, but would leak a managed secret into
        # tmux history/process arguments or a Gemini config here.  Gemini
        # therefore intentionally uses the raw, schema-validated profile for
        # every field it serializes.  A literal ``${VAR}`` remains literal in
        # the role prompt; MCP settings retain Gemini's documented expansion
        # semantics in the worker environment.
        try:
            return load_agent_profile_unresolved(
                self._agent_profile,
                resolve_operational_fields=True,
            )
        except Exception as exc:
            raise ProviderError(
                f"Failed to load non-interpolated Gemini profile '{self._agent_profile}': {exc}"
            ) from exc

    def _assert_supported_tool_policy(self, profile: Optional[AgentProfile]) -> None:
        """Reject a restrictive policy that Gemini cannot prove it enforces.

        Gemini documents ``tools.core`` as a native all-built-in-tools
        allowlist.  Its accepted identifiers have changed between released
        builds (the documentation itself contains both legacy class-style and
        current command-style spellings), however.  CAO must not translate its
        higher-level categories into guessed strings and claim that a child is
        constrained when the installed CLI might ignore or reject them.

        Until the installed Gemini version is fingerprinted and passes the
        provider matrix for a particular mapping, the only safe general
        behaviour is an explicit fail-closed refusal for a restrictive
        ``allowed_tools`` policy.  An unrestricted worker is still supported;
        its authority is explicit rather than accidentally weakened.
        """
        effective_allowed_tools = self._allowed_tools
        if effective_allowed_tools is None and profile is not None:
            effective_allowed_tools = profile.allowedTools
        if effective_allowed_tools is not None and "*" not in effective_allowed_tools:
            raise ProviderError(
                "Gemini CLI restricted allowed_tools cannot be launched safely: "
                "CAO has no version-validated native tools.core mapping for this "
                "Gemini CLI. Use allowed_tools: ['*'] for an explicitly unrestricted "
                "Gemini worker, or choose a provider with verified native restriction "
                "enforcement."
            )

    def _bootstrap_prompt(self, profile: Optional[AgentProfile]) -> str:
        """Build an interactive role bootstrap without disclosing terminal identity."""
        system_prompt = ""
        role_name = "CAO-managed Gemini CLI agent"
        if profile is not None:
            role_name = profile.name or role_name
            system_prompt = profile.system_prompt or profile.prompt or ""
        system_prompt = self._apply_skill_prompt(system_prompt)
        if not system_prompt.strip():
            system_prompt = (
                "You are operating under CLI Agent Orchestrator. Follow only the task "
                "you receive through this session and report a concise, evidence-based result."
            )
        return (
            f"{system_prompt}\n\n---\n"
            f"You are the {role_name}. Reply with exactly {_BOOTSTRAP_READY_MARKER} on one "
            "line, then wait for tasks. Do not take action or use tools until you receive a "
            "specific task."
        )

    def prepare_input(self, message: str) -> str:
        """Append a unique, one-turn completion receipt to a task delivery.

        Gemini's current Ink renderer can keep a bare composer visible while
        it is still working, so that compositor is not a completion receipt.
        The opaque nonce is generated for this delivery only, pasted with the
        task after CAO has cleared its status buffer, and accepted only when it
        appears after Gemini's activity evidence and before a new settled
        compositor.  It is evidence from the cooperative agent, not an
        authentication primitive; downstream ``run-step`` still requires a
        nonempty provider-extracted result before it records success.
        """
        if self._pending_turn_receipt_sha256 is not None:
            raise ProviderError(
                "Gemini has an active task receipt; reconcile or verify its result before "
                "sending another task"
            )
        receipt = f"{_TURN_RECEIPT_PREFIX}{secrets.token_hex(_TURN_RECEIPT_BYTES)}"
        prepared = (
            f"{message.rstrip()}\n\n"
            "CAO completion receipt requirement: this delivery contract takes precedence "
            "over any incompatible output-format instruction in the task. After you have "
            "fully completed the task and written a concise final result, write one final line containing "
            f"exactly this receipt: {receipt}\n"
            "Do not quote or emit that receipt before the task is complete, and do not "
            "perform further tool calls after it."
        )
        self._pending_turn_receipt = receipt
        self._pending_turn_receipt_sha256 = hashlib.sha256(receipt.encode("utf-8")).hexdigest()
        # Keep generation distinct from the receipt hash so the database can
        # compare-and-swap state transitions without persisting the nonce.
        self._pending_turn_generation = secrets.token_hex(_TURN_RECEIPT_BYTES)
        self._pending_prepared_input = prepared
        self._restored_turn_receipt = False
        return prepared

    def prepared_input_for_redelivery(self) -> Optional[str]:
        """Reuse the exact task text and nonce if a paste must be retried."""
        return self._pending_prepared_input

    @property
    def blocks_new_task_input_for_reconciliation(self) -> bool:
        """Never overwrite a dispatched or restart-restored Gemini turn."""
        return self._pending_turn_receipt_sha256 is not None

    def pending_turn_receipt_state(self) -> Optional[dict[str, str]]:
        """Expose only opaque state for CAO's private receipt store."""
        if self._pending_turn_generation is None or self._pending_turn_receipt_sha256 is None:
            return None
        return {
            "generation": self._pending_turn_generation,
            "receipt_sha256": self._pending_turn_receipt_sha256,
        }

    def restore_turn_receipt_state(self, state: dict[str, str]) -> None:
        """Restore an active receipt after a server restart without its nonce.

        The original task text and plaintext receipt intentionally never enter
        the database.  Consequently a restored turn cannot be auto-redelivered;
        CAO can still recognize its hash in terminal history and verify an
        extracted result, but a new task remains blocked until that happens.
        """
        generation = state.get("generation")
        receipt_sha256 = state.get("receipt_sha256")
        phase = state.get("phase")
        if (
            not isinstance(generation, str)
            or not _TURN_GENERATION_PATTERN.fullmatch(generation)
            or not isinstance(receipt_sha256, str)
            or not _TURN_RECEIPT_HASH_PATTERN.fullmatch(receipt_sha256)
            or phase not in {"prepared", "sent"}
        ):
            raise ProviderError("invalid persisted Gemini turn receipt state")
        self._pending_turn_receipt = None
        self._pending_turn_generation = generation
        self._pending_turn_receipt_sha256 = receipt_sha256
        self._pending_prepared_input = None
        self._restored_turn_receipt = True
        # A restored active task must never be misclassified as an initial
        # Gemini composer.  It is an in-flight/reconcile task until the
        # hash-backed completion witness is observed and extracted.
        self._turns = max(self._turns, 1)

    def mark_turn_receipt_sent(self) -> None:
        """Note that the private receipt store accepted the post-paste CAS."""
        self._restored_turn_receipt = False

    def mark_turn_receipt_result_verified(self) -> None:
        """Release local nonce data only after CAO persisted a result digest."""
        self._pending_turn_receipt = None
        self._pending_turn_generation = None
        self._pending_turn_receipt_sha256 = None
        self._pending_prepared_input = None
        self._restored_turn_receipt = False

    def abandon_unpersisted_turn_receipt(self) -> None:
        """Forget a local nonce only when it was never claimed durably."""
        if not self._restored_turn_receipt:
            self.mark_turn_receipt_result_verified()

    def _build_gemini_command(self) -> str:
        """Build the documented interactive Gemini launch command.

        ``-p`` is intentionally absent: it exits after one response and cannot
        participate in CAO inbox, assign, handoff, or child-join workflows.
        ``--skip-trust`` is also intentionally absent: workspace trust remains
        an operator-owned decision and surfaces as ``WAITING_USER_ANSWER``.
        """
        profile = self._load_profile()
        self._assert_supported_tool_policy(profile)
        # An explicit child model is part of the immutable task contract and
        # therefore wins over a profile default.  Validate the profile form
        # before materialising private settings: a rejected interpolation must
        # leave no runtime artifact behind.
        model = self._model or (profile.model if profile is not None else None)
        if self._model is None and isinstance(model, str) and "$" in model:
            # A profile model is rendered into a command line.  Resolving it
            # through CAO's managed .env could disclose a secret there, while
            # passing an unresolved reference makes Gemini choose no model.
            # The explicit API/CLI model override is already literal (never
            # CAO-interpolated), so operators can use it for dynamic routing.
            raise ProviderError(
                "Gemini profile model may not use environment interpolation; "
                "pass a literal --model override instead"
            )
        # Always pass a private settings overlay for the explicitly declared
        # profile MCP set. Gemini merges configuration layers; omitting the
        # overlay would expose ambient user/project MCP servers. An empty set
        # is rejected by _write_runtime_settings until Gemini has a
        # version-verified deny-all grammar for every inherited scope.
        settings_path = self._write_runtime_settings(profile)

        # Use the public command name so terminal diagnostics are recognizable
        # and executable selection follows the worker shell's PATH policy.
        try:
            # Do not let a server/session inherited headless override silently
            # turn this interactive worker into a trusted workspace.  The
            # adapter intentionally has no ``--skip-trust`` mode: a new trust
            # decision must be visible to the operator. ``env -u`` changes
            # only Gemini's child environment, preserving normal operator
            # authentication variables such as GEMINI_API_KEY and
            # GOOGLE_API_KEY without putting their values in terminal history.
            command_parts = [
                "env",
                "-u",
                "GEMINI_CLI_TRUST_WORKSPACE",
                f"CAO_TERMINAL_ID={self.terminal_id}",
            ]
            command_parts.append(
                "GEMINI_CLI_SYSTEM_SETTINGS_PATH="
                + self._require_container_path(
                    str(settings_path), profile, "private Gemini settings"
                )
            )
            command_parts.extend(["gemini", "--approval-mode=yolo"])
            if model:
                command_parts.extend(["--model", model])
            command_parts.extend(["-i", self._bootstrap_prompt(profile)])
            launch = shlex.join(command_parts)
        except Exception:
            # Settings are private, but not harmless: leaving one after a
            # failed container-path translation lets a later manual process
            # inherit an overlay CAO never successfully launched. No command
            # has been handed to the terminal at this point, so cleanup is
            # safe and strictly preferable to a reconciliation hold.
            self._cleanup_runtime()
            raise

        # Resolve the executable in the *worker shell*, not in the server
        # process.  tmux shells commonly source nvm/asdf/Homebrew setup that
        # a long-running server has not inherited.  A server-side
        # ``shutil.which`` would reject an otherwise runnable worker; this
        # marker instead becomes a terminal ERROR with an actionable cause.
        return (
            "if command -v gemini >/dev/null 2>&1; then "
            f"{launch}; "
            f"else printf '%s\\n' '{_CLI_NOT_FOUND_MARKER}'; fi"
        )

    @staticmethod
    def _is_ready_line(line: str) -> bool:
        return bool(
            _IDLE_COMPOSER_PATTERN.fullmatch(line) or _LEGACY_IDLE_COMPOSER_PATTERN.fullmatch(line)
        )

    @staticmethod
    def _is_legacy_active_composer(line: str) -> bool:
        return bool(
            _LEGACY_ACTIVE_COMPOSER_PATTERN.fullmatch(line)
            and not _LEGACY_IDLE_COMPOSER_PATTERN.fullmatch(line)
        )

    async def initialize(self) -> bool:
        """Launch Gemini and wait for a real composer or an explicit dialog."""
        profile = self._load_profile()
        # Reject before touching the worker terminal.  In particular, a
        # restrictive profile must not wait for a shell or partially launch
        # Gemini only to discover that CAO cannot prove its tool policy.
        self._assert_supported_tool_policy(profile)
        init_timeout = self.get_init_timeout(profile)
        if not await wait_for_shell(self.terminal_id, timeout=init_timeout):
            raise TimeoutError(f"Shell initialization timed out after {init_timeout}s")

        launch_sent = False
        try:
            command = await asyncio.to_thread(self._build_gemini_command)
            from cli_agent_orchestrator.services.status_monitor import status_monitor

            # A launch is a new status epoch, but it is not a task delivery:
            # don't call mark_input_received() and therefore don't turn the
            # bootstrap acknowledgement into a completed task.
            status_monitor.notify_input_sent(self.terminal_id)
            try:
                await asyncio.to_thread(
                    get_backend().send_keys,
                    self.session_name,
                    self.window_name,
                    command,
                    plain_shell=True,
                )
            except Exception as exc:
                # A terminal transport cannot prove a command was not pasted:
                # tmux may have started Gemini before reporting its own error.
                # Keep both the worker and its private settings intact for a
                # reconciliation probe instead of deleting a live process.
                raise TerminalInputBlockedError(
                    f"Gemini launch for terminal {self.terminal_id} may have reached the "
                    "terminal; reconcile before retrying launch.",
                    action="reconcile",
                    delivery_may_have_occurred=True,
                ) from exc
            launch_sent = True
            bootstrap_status = await self._wait_for_bootstrap_ready(init_timeout)
            if bootstrap_status is None:
                raise TerminalInputBlockedError(
                    f"Gemini launch for terminal {self.terminal_id} was sent but bootstrap "
                    f"was not observed within {init_timeout}s; reconcile before retrying launch.",
                    action="reconcile",
                    delivery_may_have_occurred=True,
                )
            # This direct capture can beat the FIFO's first TUI repaint.  In
            # particular, a trust/login picker must become the monitor's
            # authoritative WAITING_USER_ANSWER state before deferred or
            # synchronous task delivery considers typing into the terminal.
            status_monitor.publish_observed_status(self.terminal_id, bootstrap_status)
            self._initialized = True
            return True
        except TerminalInputBlockedError:
            # This is a post-dispatch ambiguity, not a known failed launch.
            # The caller must retain the process plus its private config until
            # a reconciling observation can prove what happened.
            raise
        except Exception as exc:
            if launch_sent:
                raise TerminalInputBlockedError(
                    f"Gemini launch for terminal {self.terminal_id} may still be live after "
                    "an uncertain bootstrap failure; reconcile before retrying launch.",
                    action="reconcile",
                    delivery_may_have_occurred=True,
                ) from exc
            # A failed launch must not strand a settings file that a future
            # operator-run Gemini session might accidentally inherit.
            await asyncio.to_thread(self._cleanup_runtime)
            raise

    async def _wait_for_bootstrap_ready(self, timeout: float) -> Optional[TerminalStatus]:
        """Require Gemini's positive bootstrap receipt before declaring init ready.

        A shell's native ``IDLE`` state is not sufficient evidence: it is also
        what a failed ``gemini`` command leaves behind.  The unique marker is
        generated by the interactive `-i` bootstrap, so it proves the CLI
        accepted the command and answered it.  A following ready compositor
        proves the REPL is able to receive CAO's first task.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            output = await asyncio.to_thread(
                get_backend().get_history,
                self.session_name,
                self.window_name,
                strip_escapes=True,
            )
            clean = strip_terminal_escapes(output or "")
            if _ERROR_PATTERN.search(clean):
                raise ProviderError(
                    "Gemini CLI failed during bootstrap; inspect the terminal for its error "
                    "and install/authenticate the official Gemini CLI before retrying"
                )
            if self._status_from_buffer(clean) == TerminalStatus.WAITING_USER_ANSWER:
                # Trust/authentication is an explicit, operator-owned gate.
                # Leave the terminal alive so the normal answer_user_prompt
                # path can resolve it; do not turn it into a hidden timeout.
                return TerminalStatus.WAITING_USER_ANSWER
            if _BOOTSTRAP_READY_PATTERN.search(clean):
                # The marker itself proves Gemini completed the bootstrap. A
                # current or legacy ready compositor proves it has returned to
                # the REPL even if the raw stream still retains an old
                # "Responding with" paint frame.
                if self._last_idle_index(self._tail_lines(clean)) >= 0:
                    return TerminalStatus.IDLE
            await asyncio.sleep(0.5)
        return None

    @staticmethod
    def _tail_lines(output: str, count: int = 64) -> list[str]:
        return output.splitlines()[-count:]

    @staticmethod
    def _last_idle_index(lines: list[str]) -> int:
        index = -1
        for idx, line in enumerate(lines):
            if GeminiCliProvider._is_ready_line(line):
                index = idx
        return index

    @staticmethod
    def _last_pattern_line_index(lines: list[str], pattern: re.Pattern[str]) -> int:
        index = -1
        for idx, line in enumerate(lines):
            if pattern.search(line):
                index = idx
        return index

    @classmethod
    def _last_processing_index(cls, lines: list[str]) -> int:
        index = -1
        for idx, line in enumerate(lines):
            if (
                _PROCESSING_PATTERN.search(line)
                or _SPINNER_PATTERN.search(line)
                or cls._is_legacy_active_composer(line)
            ):
                index = idx
        return index

    def _expected_active_query_start(self) -> Optional[str]:
        """Return the first nonempty line of the in-memory task, if available.

        It is intentionally an in-memory hint only.  A server restart restores
        the receipt hash but not task text, so a restart can never recover this
        boundary by reading plaintext from durable state.
        """
        if self._pending_prepared_input is None:
            return None
        for line in self._pending_prepared_input.splitlines():
            candidate = line.strip()
            if candidate:
                return candidate
        return None

    def _is_active_user_query_line(self, line: str) -> bool:
        """Whether one rendered ``>`` line can be the active user echo.

        Without an active receipt this retains the legacy structural extractor.
        With one, an arbitrary Markdown quote is never enough: it must precede
        the echoed active receipt (handled by ``_last_query_before``) and agree
        with the known first line of the in-memory prompt. If the bounded
        buffer has truncated that evidence, extraction must widen history or
        reconcile rather than reinterpret an answer quote as the query.
        """
        match = _QUERY_PATTERN.fullmatch(line)
        if match is None:
            return False
        if self._pending_turn_receipt_sha256 is None:
            return True
        expected = self._expected_active_query_start()
        if expected is None:
            return False
        actual = match.group("text").strip()
        # Ink can wrap a long first query line, so a visible prefix of the
        # known user echo is acceptable. The reverse relationship is unsafe:
        # an assistant Markdown quote can extend the prompt's first words and
        # would then be mistaken for the task boundary after the real echo has
        # rolled out of a bounded capture.
        return actual == expected or expected.startswith(actual)

    def _last_query_before(self, lines: list[str], exclusive_end: int) -> int:
        """Find the active query boundary without confusing answer blockquotes.

        For an active receipt-bearing task, the exact receipt is normally
        echoed once in the user message and later once by the assistant.  The
        first nonempty ``>`` line after the previous ready compositor and
        before that *echoed* receipt is the current query.  Assistant Markdown
        quotes occur after the echoed receipt, so cannot replace it.  If a
        bounded buffer has already evicted the echo, use the in-memory prompt
        prefix; restart-restored tasks deliberately have no such fallback and
        rely on the stricter assistant-attributed status path instead.
        """
        if self._pending_turn_receipt_sha256 is None:
            for idx in range(exclusive_end - 1, -1, -1):
                if self._is_active_user_query_line(lines[idx]):
                    return idx
            return -1

        # Gemini commonly renders the required nonce *inside* the echoed
        # instruction sentence rather than on a line by itself. The user echo
        # is nevertheless recoverable without trusting arbitrary Markdown
        # quotes: find the first query after the preceding ready compositor,
        # then require the exact receipt token to occur before the first
        # response/activity marker. An assistant quote can imitate the prompt
        # prefix only after response activity, so it cannot manufacture this
        # user-owned boundary when the real echo has scrolled out.
        start = 0
        for idx in range(exclusive_end - 1, -1, -1):
            if self._is_ready_line(lines[idx]):
                start = idx + 1
                break

        query_index = -1
        expected_query_start = self._expected_active_query_start()
        for idx in range(start, exclusive_end):
            if expected_query_start is None:
                if _QUERY_PATTERN.fullmatch(lines[idx]):
                    query_index = idx
                    break
            elif self._is_active_user_query_line(lines[idx]):
                query_index = idx
                break
        if query_index < 0:
            return -1

        for idx in range(query_index, exclusive_end):
            if self._line_contains_active_turn_receipt(lines[idx]):
                return query_index
            if (
                _PROCESSING_PATTERN.search(lines[idx])
                or _SPINNER_PATTERN.search(lines[idx])
                or self._is_legacy_active_composer(lines[idx])
            ):
                # Receipt after activity could be an assistant quote. It is
                # not proof that the active user echo is still in this bounded
                # transcript.
                break

        # With an active receipt, a matching nonce must have appeared in the
        # user echo before an arbitrary ``>`` line can delimit the answer. If
        # the user echo was truncated, only a wider history capture may recover
        # it; falling back to a prompt-prefix match here would let an assistant
        # blockquote manufacture a false result boundary.
        return -1

    @classmethod
    def _last_processing_index_between(
        cls, lines: list[str], start: int, exclusive_end: int
    ) -> int:
        """Find positive activity after one echoed query and before its receipt."""
        index = -1
        for idx in range(max(start, 0), min(exclusive_end, len(lines))):
            line = lines[idx]
            if (
                _PROCESSING_PATTERN.search(line)
                or _SPINNER_PATTERN.search(line)
                or cls._is_legacy_active_composer(line)
            ):
                index = idx
        return index

    @staticmethod
    def _is_assistant_attributed_line(line: str) -> bool:
        """Whether a transcript line carries Gemini's assistant glyph."""
        return bool(re.match(r"^\s*✦\s+", line))

    def _last_turn_receipt_index(self, lines: list[str]) -> int:
        """Find the active receipt, never a historical or guessed marker."""
        if self._pending_turn_receipt_sha256 is None:
            return -1
        index = -1
        for idx, line in enumerate(lines):
            # Gemini commonly prefixes assistant prose with ``✦`` while the
            # user's echoed query begins with ``>``.  Keep the prefix optional
            # for supported current layouts; ordering against activity and a
            # later ready compositor is what prevents an echoed task nonce
            # from becoming a false completion.
            candidate = re.sub(r"^\s*✦\s*", "", line).strip()
            if self._matches_active_turn_receipt(candidate):
                index = idx
        return index

    def _is_turn_receipt_line(self, line: str) -> bool:
        if self._pending_turn_receipt_sha256 is None:
            return False
        candidate = re.sub(r"^\s*✦\s*", "", line).strip()
        return self._matches_active_turn_receipt(candidate)

    def _line_contains_active_turn_receipt(self, line: str) -> bool:
        """Return whether a line contains the active opaque receipt token."""
        for token in _TURN_RECEIPT_TOKEN_PATTERN.findall(line):
            if self._matches_active_turn_receipt(token):
                return True
        return False

    def _matches_active_turn_receipt(self, candidate: str) -> bool:
        """Match an in-memory nonce or a restart-restored hash safely."""
        if not _TURN_RECEIPT_PATTERN.fullmatch(candidate):
            return False
        if self._pending_turn_receipt is not None and candidate == self._pending_turn_receipt:
            return True
        receipt_sha256 = self._pending_turn_receipt_sha256
        return (
            receipt_sha256 is not None
            and hashlib.sha256(candidate.encode("utf-8")).hexdigest() == receipt_sha256
        )

    def _ready_status(self) -> TerminalStatus:
        return TerminalStatus.COMPLETED if self._turns > 0 else TerminalStatus.IDLE

    def _status_from_buffer(self, raw: str) -> TerminalStatus:
        """Classify a raw or rendered Gemini transcript conservatively.

        Ink redraws leave previous footers in raw pipe-pane output.  Ordering
        is therefore evidence for the legacy asterisk compositor: a later
        empty composer ends that historical turn.  The current bare ``>``
        compositor, however, remains painted under some active response
        frames.  Treat a preceding activity marker plus that ambiguous modern
        compositor as PROCESSING until a version-calibrated completion witness
        is available.  A false active state is recoverable; a false completed
        state lets a parent consume a child result that does not exist.
        """
        if not raw:
            return TerminalStatus.UNKNOWN
        clean = strip_terminal_escapes(raw)
        lines = self._tail_lines(clean)
        if not lines:
            return TerminalStatus.UNKNOWN
        ready_index = self._last_idle_index(lines)
        processing_index = self._last_processing_index(lines)
        receipt_index = self._last_turn_receipt_index(lines)
        waiting_index = self._last_pattern_line_index(lines, _WAITING_PATTERN)
        error_index = self._last_pattern_line_index(lines, _ERROR_PATTERN)

        if waiting_index > max(ready_index, processing_index, error_index, receipt_index):
            return TerminalStatus.WAITING_USER_ANSWER
        if error_index > max(processing_index, receipt_index):
            return TerminalStatus.ERROR
        query_index = self._last_query_before(lines, receipt_index) if receipt_index >= 0 else -1
        current_processing_index = (
            self._last_processing_index_between(lines, query_index + 1, receipt_index)
            if query_index >= 0 and receipt_index >= 0
            else -1
        )
        assistant_receipt = receipt_index >= 0 and self._is_assistant_attributed_line(
            lines[receipt_index]
        )
        if ready_index > receipt_index >= 0 and (
            current_processing_index >= 0
            # A restarted capture can retain the exact active nonce and
            # Gemini's assistant glyph while omitting the echoed query.  The
            # glyph is a stronger attribution boundary than the generic raw
            # stream; still require positive activity before it.
            or (
                # A restart has only the receipt hash, not plaintext task
                # input. Preserve its conservative recovery route, but a live
                # turn with an in-memory expected query must first expose the
                # echoed receipt/query boundary above. Otherwise an answer
                # blockquote that begins like the prompt could falsely settle
                # the current task.
                assistant_receipt
                and self._expected_active_query_start() is None
                and receipt_index > processing_index >= 0
            )
        ):
            # Receipt + a later settled compositor is the current-layout
            # completion contract. A bare receipt needs activity after the
            # current query; an assistant-attributed receipt supports the
            # conservative restart fallback above. A nonce alone, or a nonce
            # while the renderer is still active, stays PROCESSING.
            return TerminalStatus.COMPLETED
        if processing_index > ready_index:
            return TerminalStatus.PROCESSING
        if (
            processing_index >= 0
            and ready_index >= 0
            and _IDLE_COMPOSER_PATTERN.fullmatch(lines[ready_index])
        ):
            # Unlike the legacy footer, the current bare composer is not a
            # durable completion receipt.  Its redraw can follow an active
            # ``Responding with`` line in both a raw stream and a composited
            # frame, so never let that combination complete a dispatched task.
            return TerminalStatus.PROCESSING
        if ready_index >= 0:
            # A ready compositor alone cannot settle a receipt-bearing Gemini
            # task. This covers both the current bare ``>`` and legacy footer:
            # a retained/lagging footer is not proof that the new task ever
            # ran. Completion above requires activity + the active receipt +
            # a later ready compositor.
            if self._pending_turn_receipt_sha256 is not None:
                return TerminalStatus.PROCESSING
            return self._ready_status()
        if error_index >= 0:
            return TerminalStatus.ERROR
        return TerminalStatus.UNKNOWN

    def get_status(self, output: Optional[str]) -> TerminalStatus:
        """Classify Gemini without letting a native ``done`` bypass its receipt.

        Herdr/native terminals may report a pane as idle/done while the Gemini
        Ink TUI still has a task in flight.  Native ready observations never
        settle a receipt-bearing task; only the terminal transcript's
        hash-backed completion witness can do so.  That witness also wins over
        a lagging native working/waiting observation, while native ERROR stays
        fatal.
        """
        native = self._resolve_native_status(output)
        parsed = self._status_from_buffer(self._resolve_buffer(output))
        # A current transcript satisfying activity < exact receipt < ready is
        # stronger evidence than a native backend's transient PROCESSING or
        # WAITING verdict.  It is the only route to Gemini COMPLETED while an
        # active receipt exists, and lets a fast completed turn settle even if
        # Herdr has not refreshed its pane state yet.  A native ERROR remains
        # terminal-fatal and is never masked by old transcript content.
        if native == TerminalStatus.ERROR:
            return native
        if parsed == TerminalStatus.COMPLETED:
            return parsed
        if native in {TerminalStatus.PROCESSING, TerminalStatus.WAITING_USER_ANSWER}:
            return native
        if parsed in {
            TerminalStatus.PROCESSING,
            TerminalStatus.WAITING_USER_ANSWER,
            TerminalStatus.ERROR,
        }:
            return parsed
        if native in {TerminalStatus.IDLE, TerminalStatus.COMPLETED}:
            if self._pending_turn_receipt_sha256 is not None:
                return TerminalStatus.PROCESSING
            return native
        return parsed

    def receipt_result_terminal_status(
        self,
        transcript: str,
        result: str,
    ) -> Optional[TerminalStatus]:
        """Bind Gemini's adapter-specific receipt grammar to the shared CAS.

        Gemini predates the provider-neutral ``CAO_TURN_RECEIPT`` spelling and
        deliberately keeps its own hash-backed ``CAO_GEMINI_TURN`` parser.
        Do not let BaseProvider's generic token grammar weaken that existing
        recovery route; the transcript still has to prove Gemini's complete
        activity → receipt → ready sequence.
        """
        # Gemini's result extractor deliberately removes its final receipt
        # line from user-visible prose.  Its adapter parser already proves
        # assistant attribution plus activity/ready ordering from the full
        # transcript, so validate that witness there rather than requiring a
        # token to survive extraction.
        if not any(self._is_turn_receipt_line(line) for line in transcript.splitlines()):
            return None
        return (
            TerminalStatus.COMPLETED
            if self.get_status(transcript) == TerminalStatus.COMPLETED
            else None
        )

    def get_status_from_screen(self, screen_lines: list[str]) -> TerminalStatus:
        """Classify a pyte-composited Gemini viewport.

        Unlike the raw stream, a screen has already applied Ink's in-place
        redraws, making the final composer/footer state a valid turn witness.
        """
        return self._status_from_buffer("\n".join(screen_lines))

    def get_idle_pattern_for_log(self) -> str:
        """Expose the composer marker used by inbox fast-path polling."""
        return r"^\s*(?:>\s*|\*\s+Type your message(?:\s+or\s+@path/to/file)?)\s*$"

    def extract_last_message_from_script(self, script_output: str) -> str:
        """Extract text following Gemini's last echoed user query.

        The current CLI renders ordinary prose and tool result rows rather than
        a stable assistant-only prefix, so the structural boundary is the last
        ``> <query>`` followed by the next blank composer.  Known TUI chrome is
        removed; an empty result remains an explicit extraction failure.
        """
        clean = strip_terminal_escapes(script_output or "")
        lines = clean.splitlines()
        receipt_index = self._last_turn_receipt_index(lines)
        query_index = self._last_query_before(
            lines,
            receipt_index if receipt_index >= 0 else len(lines),
        )
        if query_index < 0:
            raise ValueError("No Gemini CLI user query found in terminal output")

        body: list[str] = []
        # The marker instruction is part of the echoed user task. When this
        # active Gemini turn has a processing row, begin after the last such
        # row so neither the original request nor the receipt instruction can
        # be misreported as the assistant's result. Historical transcripts
        # without that row retain the legacy structural boundary.
        response_start = query_index + 1
        last_response_start = -1
        for index, line in enumerate(lines[query_index + 1 :], start=query_index + 1):
            if _PROCESSING_PATTERN.search(line) or _SPINNER_PATTERN.search(line):
                last_response_start = index + 1
        if last_response_start >= 0:
            response_start = last_response_start

        for line in lines[response_start:]:
            if self._is_turn_receipt_line(line):
                break
            if self._is_ready_line(line) and body:
                break
            stripped = line.strip()
            if not stripped or _SEPARATOR_PATTERN.fullmatch(line) or _CHROME_PATTERN.search(line):
                continue
            # A repeated nonempty ``>`` line starts the next transcript query;
            # do not accidentally attribute it to the previous response.
            if self._is_active_user_query_line(line):
                break
            body.append(re.sub(r"^✦\s*", "", stripped))

        message = "\n".join(body).strip()
        if not message:
            raise ValueError("Empty Gemini CLI response after the last user query")
        return message

    def exit_cli(self) -> str:
        """Gemini documents ``/quit`` as the interactive REPL exit command."""
        return "/quit"

    def _cleanup_runtime(self) -> bool:
        """Remove only the exact CAO-owned per-terminal runtime directory.

        Return ``False`` rather than forgetting a failed removal.  The caller
        then retains terminal metadata, which is the durable retry handle after
        a server restart instead of falsely claiming the private settings were
        cleaned up.
        """
        runtime_dir = self._runtime_dir
        if runtime_dir is None:
            try:
                runtime_dir, _ = self._runtime_paths()
            except ProviderError:
                return False
        provider_root = CAO_HOME_DIR / "providers" / "gemini_cli"
        providers_root = CAO_HOME_DIR / "providers"
        success = False
        try:
            # A restart reconstructs these paths from the terminal id, so
            # re-check every mutable ancestor before touching the filesystem.
            # In particular, ``Path.exists()`` is False for a broken symlink;
            # check links first so a swapped runtime is never misreported as
            # already cleaned.
            for path in (providers_root, provider_root, runtime_dir):
                if path.is_symlink():
                    logger.error("Refusing symlinked Gemini cleanup path: %s", path)
                    return False
            self._assert_inside(providers_root, CAO_HOME_DIR)
            self._assert_inside(runtime_dir, provider_root)
            if not runtime_dir.exists():
                success = True
                return True
            for child in runtime_dir.iterdir():
                if child.is_symlink():
                    logger.error("Refusing to remove Gemini runtime containing symlink: %s", child)
                    return False
            shutil.rmtree(runtime_dir)
            success = not runtime_dir.exists()
            if not success:
                logger.warning(
                    "Gemini runtime directory still exists after cleanup: %s", runtime_dir
                )
            return success
        except (OSError, ProviderError) as exc:
            logger.warning("Failed to remove Gemini runtime directory %s: %s", runtime_dir, exc)
            return False
        finally:
            if success:
                self._runtime_dir = None
                self._runtime_settings_path = None

    def cleanup(self) -> bool:
        """Synchronously erase the small terminal-private settings directory."""
        self._initialized = False
        cleaned = self._cleanup_runtime()
        if cleaned:
            self.mark_turn_receipt_result_verified()
        return cleaned

    def mark_input_received(self) -> None:
        """Split an initial ready composer from a genuinely finished task."""
        super().mark_input_received()
        self._turns += 1
