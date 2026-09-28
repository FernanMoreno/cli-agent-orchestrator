"""Launch command for CLI Agent Orchestrator CLI."""

import os
import re
import time

import click
import requests

from cli_agent_orchestrator.backends.registry import get_backend
from cli_agent_orchestrator.constants import (
    API_BASE_URL,
    DEFAULT_PROVIDER,
    PROVIDERS,
    SERVER_HOST,
    SERVER_PORT,
)
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.security.auth import get_local_bearer
from cli_agent_orchestrator.services.settings_service import get_server_settings
from cli_agent_orchestrator.utils.forwarded_env import (
    ForwardedEnvError,
    validate_forwarded_env,
)
from cli_agent_orchestrator.utils.terminal import (
    poll_until_done,
    sync_backend_from_server,
    wait_until_terminal_status,
)

# Match the complete public error envelopes accepted by the API and MCP boundaries.
_SAFE_WORK_LAUNCH_ERROR_DETAILS = frozenset(
    {
        (
            "launch_identity_required",
            "Verified launch identity is required.",
            False,
            "authenticate",
        ),
        (
            "launch_intent_invalid",
            "Launch intent is invalid.",
            False,
            "correct_launch_intent",
        ),
        (
            "launch_idempotency_conflict",
            "Launch operation conflicts with an existing server-owned identity.",
            False,
            "inspect_existing_launch",
        ),
        (
            "launch_context_unavailable",
            "Trusted launch context is unavailable.",
            False,
            "provision_launch_context",
        ),
        (
            "launch_store_unavailable",
            "Verified work store is unavailable.",
            True,
            "retry_same_intent",
        ),
        (
            "launch_runtime_unavailable",
            "Trusted launch runtime is unavailable.",
            False,
            "inspect_server_configuration",
        ),
        (
            "launch_internal_error",
            "Unable to process the launch request.",
            False,
            "inspect_server_configuration",
        ),
    }
    | {
        (
            "launch_authority_denied",
            "Verified launch authority is required.",
            False,
            required_action,
        )
        for required_action in ("authenticate", "reauthorize")
    }
    | {
        (
            "launch_context_invalid",
            "Trusted launch context is no longer valid.",
            False,
            required_action,
        )
        for required_action in ("refresh_launch_context", "resolve_launch_again")
    }
)


def _safe_cli_work_launch_detail(detail):
    """Return only the message and action from an exact public Work error envelope."""
    if not isinstance(detail, dict) or set(detail) != {
        "code",
        "message",
        "retryable",
        "required_action",
    }:
        return None
    code = detail["code"]
    message = detail["message"]
    retryable = detail["retryable"]
    required_action = detail["required_action"]
    if not (
        isinstance(code, str)
        and isinstance(message, str)
        and type(retryable) is bool
        and isinstance(required_action, str)
    ):
        return None
    if (code, message, retryable, required_action) not in _SAFE_WORK_LAUNCH_ERROR_DETAILS:
        return None
    return message, required_action


# Providers that require workspace folder access
PROVIDERS_REQUIRING_WORKSPACE_ACCESS = {
    "antigravity_cli",
    "claude_code",
    "codex",
    "copilot_cli",
    "cursor_cli",
    "gemini_cli",
    "grok_cli",
    "hermes",
    "kimi_cli",
    "kiro_cli",
    "mcode",
    "opencode_cli",
    "omp",
}

# Validation constraints for ``--env`` forwarded vars live in
# ``utils.forwarded_env`` (shared with the ops-MCP ``launch_session`` tool so
# the two client paths cannot drift) and are mirrored server-side in
# ``TmuxClient._merge_extra_env``. See issue #248.


def _parse_env_pairs(pairs):
    """Parse repeated ``KEY=VALUE`` entries into a validated dict.

    Splitting each ``KEY=VALUE`` string (and the last-wins duplicate handling)
    is CLI-specific, but every validation rule is delegated to the shared
    ``validate_forwarded_env`` so ``--env`` and the ops-MCP ``launch_session``
    tool can never drift. Each shared message begins with ``env ``; prefixing
    with ``--`` reproduces the historical ``--env ...`` CLI messages exactly.
    """
    parsed: dict[str, str] = {}
    for raw in pairs:
        if "=" not in raw:
            raise click.ClickException(
                f"--env expects KEY=VALUE (got {raw!r}); did you forget the '='?"
            )
        key, value = raw.split("=", 1)
        parsed[key] = value  # last-wins on a duplicate key
    try:
        return validate_forwarded_env(parsed)
    except ForwardedEnvError as exc:
        raise click.ClickException(f"--{exc}") from exc


@click.command()
@click.argument("message", required=False, default=None)
@click.option("--agents", required=True, help="Agent profile to launch")
@click.option("--session-name", help="Name of the session (default: auto-generated)")
@click.option(
    "--queue-work",
    is_flag=True,
    help="Admit queued Work only; does not launch a provider or terminal. "
    "Requires --session-name and MESSAGE; omitted selectors use one active provision.",
)
@click.option(
    "--work-selection",
    metavar="SELECTOR",
    help="Opaque server-provisioned Work selector used with --queue-work.",
)
@click.option("--headless", is_flag=True, help="Launch in detached mode")
@click.option(
    "--provider",
    default=None,
    help=f"Provider to use (default: profile provider or {DEFAULT_PROVIDER})",
)
@click.option(
    "--engine",
    "engine",
    type=click.Choice(["v2", "kas"], case_sensitive=True),
    default=None,
    help="Explicit Kiro engine (default: profile engine or v2).",
)
@click.option(
    "--allowed-tools",
    multiple=True,
    help="Override allowedTools (CAO format: execute_bash, fs_read, @cao-mcp-server). Repeatable.",
)
@click.option(
    "--async",
    "is_async",
    is_flag=True,
    help="Send message and return immediately without waiting for completion",
)
@click.option(
    "--auto-approve",
    is_flag=True,
    help="Skip confirmation prompt (restrictions still enforced).",
)
@click.option(
    "--yolo",
    is_flag=True,
    help="[DANGEROUS] Unrestricted tool access AND skip confirmation prompts. "
    "Agent can execute ANY command including aws, rm, curl.",
)
@click.option(
    "--working-directory",
    default=None,
    help="Working directory for the session (default: current directory)",
)
@click.option(
    "--memory",
    "memory",
    is_flag=True,
    help="Also launch a context-manager (memory_manager) terminal for curated memory injection.",
)
@click.option(
    "--env",
    "env_pairs",
    multiple=True,
    metavar="KEY=VALUE",
    help="Forward an env var to the supervisor AND every worker spawned later "
    "in the same session. Repeatable. Values travel in the request body, not "
    "the URL. Blocked prefixes (CLAUDE/CODEX_/__MISE_) and >=2048-byte values "
    "are rejected. See issue #248.",
)
@click.option(
    "--resume-session-id",
    "resume_session_id",
    default=None,
    metavar="SESSION_ID",
    help="Resume a prior Claude Code conversation in the launched supervisor "
    "(claude --resume <id>). claude_code provider only.",
)
def launch(
    message,
    agents,
    session_name,
    queue_work,
    work_selection,
    headless,
    is_async,
    provider,
    engine,
    allowed_tools,
    auto_approve,
    yolo,
    working_directory,
    memory,
    env_pairs,
    resume_session_id,
):
    """Launch cao session with specified agent profile."""
    try:
        if work_selection is not None and not queue_work:
            raise click.ClickException("--work-selection requires --queue-work")
        if queue_work:
            incompatible_options = (
                ("--provider", provider is not None),
                ("--engine", engine is not None),
                ("--headless", headless),
                ("--async", is_async),
                ("--auto-approve", auto_approve),
                ("--yolo", yolo),
                ("--working-directory", working_directory is not None),
                ("--memory", memory),
                ("--env", bool(env_pairs)),
                ("--resume-session-id", resume_session_id is not None),
            )
            for option, supplied in incompatible_options:
                if supplied:
                    raise click.ClickException(
                        f"{option} cannot be used with --queue-work; queued admission does not launch a provider or terminal"
                    )
            if not session_name:
                raise click.ClickException("--session-name is required for --queue-work")
            if message is None:
                raise click.ClickException("MESSAGE is required for --queue-work")

            identity_pattern = r"[A-Za-z0-9._:-]+"
            identities = [("agent profile", agents)]
            if work_selection is not None:
                identities.append(("work selection", work_selection))
            identities.append(("session name", session_name))
            for label, value in identities:
                if len(value) > 128 or re.fullmatch(identity_pattern, value) is None:
                    raise click.ClickException(
                        f"Invalid {label} for --queue-work; expected 1 to 128 letters, digits, '.', '_', ':', or '-'."
                    )
            if len(message) > 32768:
                raise click.ClickException(
                    "Invalid MESSAGE for --queue-work; maximum length is 32768 characters"
                )
        display_dir = working_directory or os.path.realpath(os.getcwd())
        explicit_provider = provider is not None  # True only when --provider was passed
        forwarded_env = _parse_env_pairs(env_pairs) if env_pairs else {}

        # Resolve allowedTools: --yolo > --allowed-tools CLI > profile/role defaults
        from cli_agent_orchestrator.utils.agent_profiles import load_agent_profile
        from cli_agent_orchestrator.utils.tool_mapping import (
            format_tool_summary,
            get_disallowed_tools,
            resolve_allowed_tools,
        )

        resolved_allowed_tools = None
        no_role_set = False
        if yolo:
            resolved_allowed_tools = ["*"]
        elif allowed_tools:
            resolved_allowed_tools = list(allowed_tools)
        else:
            # Load profile to get role-based defaults
            try:
                profile = load_agent_profile(agents)
                mcp_server_names = list(profile.mcpServers.keys()) if profile.mcpServers else None
                no_role_set = not profile.role and not profile.allowedTools
                resolved_allowed_tools = resolve_allowed_tools(
                    profile.allowedTools, profile.role, mcp_server_names
                )
            except (FileNotFoundError, RuntimeError):
                # Profile not found — use developer defaults (backward compatible)
                no_role_set = True
                resolved_allowed_tools = resolve_allowed_tools(None, None, None)

        if queue_work:
            work_tools = list(resolved_allowed_tools or [])
            if len(work_tools) > 128:
                raise click.ClickException(
                    "Invalid allowed tools for --queue-work; maximum is 128 tools"
                )
            for tool in work_tools:
                if (
                    not isinstance(tool, str)
                    or len(tool) > 128
                    or re.fullmatch(r"[A-Za-z0-9._:-]+", tool) is None
                ):
                    raise click.ClickException(
                        "Invalid allowed tool for --queue-work; expected a 1 to 128 character Work identity"
                    )
            work_bearer = get_local_bearer()
            if not work_bearer:
                raise click.ClickException(
                    "--queue-work requires a configured bearer token for authenticated Work admission"
                )
            request_timeout = get_server_settings()["mcp_request_timeout"]
            work_launch_body = {
                "agent_profile": agents,
                "session_name": session_name,
                "message": message,
                "allowed_tools": work_tools,
            }
            if work_selection is not None:
                work_launch_body["selection"] = work_selection
            response = requests.post(
                f"{API_BASE_URL}/work-launches",
                json=work_launch_body,
                headers={"Authorization": f"Bearer {work_bearer}"},
                timeout=request_timeout,
            )
            response.raise_for_status()
            receipt = response.json()
            if (
                not isinstance(receipt, dict)
                or not isinstance(receipt.get("work_item_id"), str)
                or not receipt["work_item_id"]
                or not isinstance(receipt.get("attempt_id"), str)
                or not receipt["attempt_id"]
                or isinstance(receipt.get("generation"), bool)
                or not isinstance(receipt.get("generation"), int)
                or receipt["generation"] <= 0
                or receipt.get("state") != "queued"
            ):
                raise click.ClickException("cao-server returned an invalid queued Work receipt")
            click.echo("Queued Work receipt:")
            click.echo(f"  work_item_id: {receipt['work_item_id']}")
            click.echo(f"  attempt_id: {receipt['attempt_id']}")
            click.echo(f"  generation: {receipt['generation']}")
            click.echo(f"  state: {receipt['state']}")
            return

        # Honour profile.provider whenever the user did not pass --provider
        # explicitly. This runs regardless of which permission-resolution
        # branch above fired — provider selection ("which CLI runs this
        # agent?") is orthogonal to tool restrictions ("what is the agent
        # allowed to do?"). Previously this lookup lived inside the ``else``
        # branch and ``--yolo`` / ``--allowed-tools`` silently bypassed the
        # profile's ``provider:`` field, breaking heterogeneous-panel
        # workflows. See issue #239. ``resolve_provider`` falls back to
        # ``DEFAULT_PROVIDER`` when the profile is missing or has no
        # ``provider`` key, so the trailing fallback is no longer needed.
        if provider is None:
            from cli_agent_orchestrator.utils.agent_profiles import resolve_provider

            provider = resolve_provider(agents, DEFAULT_PROVIDER)

        # Validate provider
        if provider not in PROVIDERS:
            raise click.ClickException(
                f"Invalid provider '{provider}'. Available providers: {', '.join(PROVIDERS)}"
            )
        # Confirmation / warning prompts
        if provider in PROVIDERS_REQUIRING_WORKSPACE_ACCESS:
            if yolo:
                # --yolo: warn but don't block
                click.echo(click.style("\n[WARNING] --yolo mode enabled", fg="yellow", bold=True))
                click.echo(
                    f"  Agent '{agents}' launching UNRESTRICTED on {provider}.\n"
                    f"  Agent can execute ANY command (aws, rm, curl, read credentials).\n"
                    f"  Directory: {display_dir}\n"
                )
                if provider == "kiro_cli":
                    # The kiro-cli TUI blocks on an interactive "Yes, I accept"
                    # consent dialog when --trust-all-tools is set. CAO answers
                    # it automatically after launch (the provider verifies the
                    # dialog first), so no --legacy-ui suppression is needed —
                    # and --legacy-ui must not be used, because it selects the
                    # v1 engine, which serves the agent no MCP tools.
                    click.echo(
                        "  Note: kiro_cli's --trust-all-tools consent dialog will be "
                        "auto-answered at startup.\n"
                    )
                elif provider == "opencode_cli":
                    # opencode's TUI has no runtime skip-permissions flag
                    # (tracked upstream in sst/opencode#8463). Permissions are
                    # install-time only, so --yolo cannot loosen them here.
                    click.echo(
                        click.style(
                            "  Note: --yolo has no runtime effect on opencode_cli.\n"
                            "  Permissions are set at cao install time. To get unrestricted\n"
                            "  access, set 'allowedTools: [\"*\"]' in the profile and re-run\n"
                            "  'cao install'. See docs/opencode-cli.md for details.\n",
                            fg="yellow",
                        )
                    )
            else:
                # Normal launch: show tool summary and confirm
                tool_summary = format_tool_summary(resolved_allowed_tools)
                blocked = get_disallowed_tools(provider, resolved_allowed_tools)
                blocked_summary = ", ".join(blocked) if blocked else "(none)"

                click.echo(
                    f"\nAgent '{agents}' launching on {provider}:\n"
                    f"  Allowed:  {tool_summary}\n"
                    f"  Blocked:  {blocked_summary}\n"
                    f"  Directory: {display_dir}\n"
                )
                if no_role_set:
                    click.echo(
                        "  Note: No role or allowedTools set — defaulting to 'developer'.\n"
                        "  Add 'role' or 'allowedTools' to your agent profile to control tool access.\n"
                        "  Docs: https://github.com/awslabs/cli-agent-orchestrator/blob/main/docs/tool-restrictions.md\n"
                    )
                click.echo(
                    "  To skip this prompt next time, relaunch with --auto-approve\n"
                    "  To remove all restrictions, relaunch with --yolo\n"
                )
                if not auto_approve and not click.confirm("Proceed?", default=True):
                    raise click.ClickException("Launch cancelled by user")

        # Call API to create session — pass working_directory only if explicitly
        # provided. When omitted, the server defaults to its own CWD.
        url = f"http://{SERVER_HOST}:{SERVER_PORT}/sessions"
        params = {
            "agent_profile": agents,
            "working_directory": working_directory or os.getcwd(),
        }
        if explicit_provider:
            params["provider"] = provider
        if engine is not None:
            params["engine"] = engine
        if session_name:
            params["session_name"] = session_name
        if resolved_allowed_tools:
            # Pass as comma-separated string for query param
            params["allowed_tools"] = ",".join(resolved_allowed_tools)
        if memory:
            params["memory_manager"] = "true"
        if resume_session_id:
            params["resume_session_id"] = resume_session_id

        # Forwarded env vars travel in the JSON body so values (which may
        # contain secrets) don't end up in cao-server's HTTP access log.
        # See issue #248.
        request_timeout = get_server_settings()["mcp_request_timeout"]
        post_kwargs: dict = {"params": params, "timeout": request_timeout}
        if forwarded_env:
            post_kwargs["json"] = {"env_vars": forwarded_env}

        response = requests.post(url, **post_kwargs)
        response.raise_for_status()

        terminal = response.json()

        click.echo(f"Session created: {terminal['session_name']}")
        click.echo(f"Terminal created: {terminal['name']}")

        # Attach to tmux session unless headless. Wait for the provider to
        # finish initializing first — otherwise tmux attach races with the
        # TUI's input handler wiring, resizes the pty mid-init, and the TUI
        # silently drops keystrokes. See issue #220. The wait is advisory:
        # if it times out we still attach so the user can inspect the
        # half-initialized session rather than orphan it in tmux.
        if not headless:
            # Align the CLI's backend singleton with the running server.
            # Without this, ``cao-server --terminal herdr`` + no config.json
            # entry causes the CLI to default to tmux. See issue #308.
            sync_backend_from_server()
            ready = wait_until_terminal_status(
                terminal["id"],
                {TerminalStatus.IDLE, TerminalStatus.COMPLETED},
                timeout=120,
            )
            if not ready:
                click.echo(
                    click.style(
                        f"  Warning: {terminal['id']} did not reach idle within 120s — "
                        "attaching anyway; input may be unreliable until init completes.",
                        fg="yellow",
                    )
                )
            get_backend().attach_session(terminal["session_name"])
        elif message:
            ready = wait_until_terminal_status(
                terminal["id"],
                {TerminalStatus.IDLE, TerminalStatus.COMPLETED},
                timeout=120,
            )
            if not ready:
                raise click.ClickException(
                    f"Conductor {terminal['id']} did not become ready within 120s"
                )
            request_timeout = get_server_settings()["mcp_request_timeout"]
            response = requests.post(
                f"{API_BASE_URL}/terminals/{terminal['id']}/input",
                params={"message": message},
                timeout=request_timeout,
            )
            response.raise_for_status()
            time.sleep(3)
            if is_async:
                click.echo(f"Message sent to {terminal['name']}. Running in background.")
                return
            poll_until_done(terminal["id"], timeout=300)
            request_timeout = get_server_settings()["mcp_request_timeout"]
            output_resp = requests.get(
                f"{API_BASE_URL}/terminals/{terminal['id']}/output",
                params={"mode": "last"},
                timeout=request_timeout,
            )
            output_resp.raise_for_status()
            output = output_resp.json().get("output", "")
            if output:
                click.echo(output)

    except requests.exceptions.HTTPError as e:
        if queue_work and e.response is not None:
            try:
                error_payload = e.response.json()
            except (ValueError, TypeError, AttributeError):
                error_payload = None
            detail = (
                error_payload.get("detail")
                if isinstance(error_payload, dict)
                else None
            )
            safe_detail = _safe_cli_work_launch_detail(detail)
            if safe_detail is not None:
                message, required_action = safe_detail
                raise click.ClickException(
                    f"cao-server rejected queued Work (HTTP {e.response.status_code}): "
                    f"{message} Required action: {required_action}"
                ) from e
            raise click.ClickException(
                f"cao-server rejected queued Work (HTTP {e.response.status_code})"
            ) from e
        raise click.ClickException(f"Failed to connect to cao-server: {str(e)}")
    except requests.exceptions.RequestException as e:
        raise click.ClickException(f"Failed to connect to cao-server: {str(e)}")
    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(str(e))
