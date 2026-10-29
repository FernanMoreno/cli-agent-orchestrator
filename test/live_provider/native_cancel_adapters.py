"""Native configuration only; lifecycle and acceptance predicates are shared."""

import json
import shlex
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class NativeCancelAdapter:
    provider: str
    model: str
    primary_instruction: str
    prompt_prefix: str
    progress_prefix: str
    profile_prefix: str
    environment: dict[str, str]
    credential_names: frozenset[str]
    configure: Callable[[Path], None]
    profile_options: str = ""
    allowed_tools: tuple[str, ...] = ()
    expected_mcp_tools: tuple[str, ...] = ()
    native_operation: str = ""
    scoped_discovery: bool = False
    discovery_variant: str = ""
    mcp_profile: str = "collaboration"


def _configure_opencode(home):
    path = home / ".aws/opencode/opencode.json"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "tools": {"*": False},
                "mcp": {"servers": {}},
                "providers": {
                    "opencode-go": {
                        "env": ["OPENCODE_API_KEY"],
                        "package": "@opencode/ai/providers/openai-compatible",
                        "settings": {"baseURL": "http://127.0.0.1:43871/v1"},
                        "models": {
                            "glm-5.3-flash": {"limit": {"context": 196608, "output": 32768}}
                        },
                    }
                },
            }
        )
    )


def _configure_claude(home):
    # Synthetic private HOME only, without operator credentials/configuration.
    config = {
        "hasCompletedOnboarding": True,
        "customApiKeyResponses": {"approved": ["synthetic-local-control"[-20:]], "rejected": []},
    }
    # Cover both native config locations in this disposable home; no login is staged.
    for path in (home / ".claude.json", home / ".claude/.claude.json"):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(json.dumps(config))
        path.chmod(0o600)


OPENCODE = NativeCancelAdapter(
    provider="opencode_cli",
    model="opencode-go/glm-5.3-flash",
    primary_instruction="T220_NATIVE_PRIMARY_INSTRUCTION",
    prompt_prefix="T220 controlled native turn",
    progress_prefix="T220-progress-",
    profile_prefix="t220",
    environment={"OPENCODE_API_KEY": "synthetic-local-control"},
    credential_names=frozenset({"OPENCODE_API_KEY"}),
    configure=_configure_opencode,
)
CLAUDE = NativeCancelAdapter(
    provider="claude_code",
    model="claude-sonnet-4-6",
    primary_instruction="T223_NATIVE_PRIMARY_INSTRUCTION",
    prompt_prefix="T223 controlled native turn",
    progress_prefix="T223-progress-",
    profile_prefix="t223",
    environment={"ANTHROPIC_API_KEY": "synthetic-local-control"},
    credential_names=frozenset({"ANTHROPIC_API_KEY"}),
    configure=_configure_claude,
    profile_options="permissionMode: default\n",
)


def _configure_claude_inspection(home):
    repo = Path(__file__).resolve().parents[2]
    roots = (repo, Path("/home/felni/.local/share/uv/python"))
    executable = Path(sys.executable)
    assert executable.is_absolute() and all(
        any(candidate.is_relative_to(root) for root in roots)
        for candidate in (executable, executable.resolve(strict=True))
    ), "hook_interpreter_outside_verified_namespace"
    _configure_claude(home)
    command = shlex.join(
        [sys.executable, "-m", "test.live_provider.claude_completion_observer", "/tmp/evidence"]
    )
    settings = {
        "hooks": {
            event: [{"hooks": [{"type": "command", "command": command}]}]
            for event in ("SessionStart", "Stop", "StopFailure")
        }
    }
    path = home / ".claude/settings.json"
    path.write_text(json.dumps(settings))
    path.chmod(0o600)


CLAUDE_INSPECT = replace(CLAUDE, configure=_configure_claude_inspection)


CLAUDE_MCP_OFFER = replace(
    CLAUDE,
    allowed_tools=("@cao-mcp-server",),
    expected_mcp_tools=("mcp__cao-mcp-server__list_siblings",),
    profile_options=(
        "permissionMode: default\n"
        "mcpServers:\n"
        "  cao-mcp-server:\n"
        "    command: cao-mcp-server\n"
        "    cao_tool_profile: collaboration\n"
        "    enabled_tools: [list_siblings]\n"
    ),
)


def native_profile_text(name, adapter):
    return (
        f"---\nname: {name}\ndescription: Native concurrent cancellation control\n"
        f"provider: {adapter.provider}\nmodel: {adapter.model}\nrole: developer\n"
        f"allowedTools: {json.dumps(list(adapter.allowed_tools))}\n"
        f"{adapter.profile_options}---\n{adapter.primary_instruction}: Follow the user request.\n"
    )


def claude_adapter_for_manifest(manifest):
    profile_case = manifest.get("profile_case", "")
    if profile_case.startswith("mcp_catalog:"):
        from cli_agent_orchestrator.utils.mcp_tool_policy import TOOL_PROFILES

        assert manifest["case_kind"] == "preflight", "catalog is preflight-only"
        profile = profile_case.split(":", 1)[1]
        if profile not in {"interaction", "collaboration", "coordination"}:
            raise ValueError("unknown_native_catalog_profile")
        tools = sorted(TOOL_PROFILES[profile])
        return replace(
            CLAUDE_MCP_OFFER,
            mcp_profile=profile,
            expected_mcp_tools=tuple("mcp__cao-mcp-server__" + tool for tool in tools),
            profile_options=(
                "permissionMode: default\nmcpServers:\n  cao-mcp-server:\n"
                "    command: cao-mcp-server\n    cao_tool_profile: "
                + profile
                + "\n    enabled_tools: "
                + json.dumps(tools + ["delete_terminal"])
                + "\n"
            ),
        )
    if profile_case.startswith("mcp_discovery_variant:"):
        from test.live_provider.native_discovery_cases import discovery_case

        assert manifest["case_kind"] == "preflight", "scoped discovery is preflight-only"
        variant = profile_case.split(":", 1)[1]
        case = discovery_case(variant)
        return replace(
            CLAUDE_MCP_OFFER,
            native_operation=case["mode"],
            scoped_discovery=True,
            discovery_variant=variant,
            allowed_tools=(
                ("@cao-mcp-server",)
                if case["mode"] == "denied"
                else ("@cao-mcp-server", "discovery")
            ),
        )
    if profile_case == "mcp_discovery_scoped":
        assert manifest["case_kind"] == "preflight", "scoped discovery is preflight-only"
        return replace(
            CLAUDE_MCP_OFFER,
            native_operation="authorized",
            scoped_discovery=True,
            allowed_tools=("@cao-mcp-server", "discovery"),
        )
    if profile_case in {"mcp_execute_authorized", "mcp_execute_denied"}:
        assert manifest["case_kind"] == "preflight", "MCP execution is preflight-only"
        mode = profile_case.removeprefix("mcp_execute_")
        return replace(
            CLAUDE_MCP_OFFER,
            native_operation=mode,
            allowed_tools=(
                ("@cao-mcp-server", "discovery") if mode == "authorized" else ("@cao-mcp-server",)
            ),
        )
    if profile_case == "mcp_offer":
        assert manifest["case_kind"] == "preflight", "MCP offer is a preflight-only gate"
        return CLAUDE_MCP_OFFER
    assert profile_case == "", "unknown native profile case"
    return CLAUDE_INSPECT if manifest["case_kind"] == "inspect" else CLAUDE
