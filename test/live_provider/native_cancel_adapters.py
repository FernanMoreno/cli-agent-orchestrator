"""Native configuration only; lifecycle and acceptance predicates are shared."""

import json
from dataclasses import dataclass
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
