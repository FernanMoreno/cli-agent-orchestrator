"""Engine-aware Kiro profile adapters and atomic artifact rendering."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from pydantic_core import PydanticSerializationError

from cli_agent_orchestrator.constants import KIRO_AGENTS_DIR
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.models.kiro_agent import KiroAgentConfig
from cli_agent_orchestrator.models.kiro_engine import KiroEngine
from cli_agent_orchestrator.models.kiro_kas import KASAgentConfig
from cli_agent_orchestrator.utils.kiro_policy import (
    CompiledKiroPolicy,
    KiroPolicyError,
    compile_kiro_policy,
)
from cli_agent_orchestrator.utils.tool_mapping import kiro_agent_tools

_SAFE_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


logger = logging.getLogger(__name__)


def kiro_artifact_path(directory: Path, name: str, engine: KiroEngine) -> Path:
    """Return distinct deterministic artifact identities for v2 and KAS."""
    if not _SAFE_PROFILE_NAME_RE.fullmatch(name):
        raise ValueError("Kiro profile name must match [A-Za-z0-9_-]{1,64}")
    suffix = ".json" if engine == KiroEngine.V2 else ".kas.json"
    return directory / f"{name}{suffix}"


def kiro_summary_path(directory: Path, name: str, engine: KiroEngine) -> Path:
    """Return the redacted-policy sidecar path for a KAS artifact (ADR-006).

    Reuses ``_SAFE_PROFILE_NAME_RE`` — the same regex ``kiro_artifact_path``
    applies — so the traversal defense is identical by construction rather than
    reimplemented (SEC-U7-6). The extension deliberately differs from the
    artifact's (``.kas.summary.json`` vs ``.kas.json``) so the sidecar can never
    be mistaken for KAS engine input (SEC-U7-7).
    """
    if not _SAFE_PROFILE_NAME_RE.fullmatch(name):
        raise ValueError("Kiro profile name must match [A-Za-z0-9_-]{1,64}")
    if engine != KiroEngine.KAS:
        raise ValueError("Only KAS profiles have a compiled policy summary")
    return directory / f"{name}.kas.summary.json"


def redacted_policy_summary(policy: CompiledKiroPolicy) -> dict[str, object]:
    """Build the audit summary for one compiled policy (FR-105, NFR-104).

    An explicit dict **literal** — a whitelist, not a filtered projection of the
    policy object (SEC-U7-1). A filter would leak any newly-added policy
    attribute by default; a literal leaks nothing unless someone types it in.

    Cedar rule *bodies* never appear: only the existing ``allow_rule_count`` /
    ``deny_rule_count`` properties are read (SEC-U7-2). The prompt and MCP
    ``env`` values are not reachable from the parameter type at all — the narrow
    input is itself the control (SEC-U7-3/4).

    Key names are a pinned cross-unit contract. Note ``excluded_tools``
    (snake_case, consistent with the other five keys) sources
    ``CompiledKiroPolicy.denied_tools``; it is deliberately *not* named after
    either that attribute or the rendered envelope's ``KASPermissions.
    excludedTools``. Do not "correct" it to match either.
    """
    return {
        "policy_source": str(policy.source),
        "unrestricted": policy.unrestricted,
        "visible_tools": list(policy.visible_tools),
        "excluded_tools": list(policy.denied_tools),
        "allow_rule_count": policy.allow_rule_count,
        "deny_rule_count": policy.deny_rule_count,
    }


def redacted_policy_summary_json(policy: CompiledKiroPolicy) -> str:
    """Serialise the redacted summary for an atomic sidecar write (NFR-103)."""
    return json.dumps(redacted_policy_summary(policy), indent=2) + "\n"


def render_kiro_v2(
    profile: AgentProfile,
    allowed_tools: list[str],
    resources: list[str],
    mcp_servers: Optional[dict[str, object]],
) -> str:
    """Render the existing v2 JSON shape without changing serialization."""
    raw_prompt = profile.prompt.strip() if profile.prompt and profile.prompt.strip() else None
    config = KiroAgentConfig(
        name=profile.name,
        description=profile.description,
        tools=profile.tools if profile.tools is not None else kiro_agent_tools(allowed_tools),
        allowedTools=allowed_tools,
        resources=resources,
        prompt=raw_prompt,
        mcpServers=mcp_servers,
        toolAliases=profile.toolAliases,
        toolsSettings=profile.toolsSettings,
        hooks=profile.hooks,
        model=profile.model,
    )
    return config.model_dump_json(indent=2, exclude_none=True)


def render_kiro_kas(
    profile: AgentProfile,
    resources: list[str],
    mcp_servers: Optional[dict[str, object]],
) -> tuple[str, CompiledKiroPolicy]:
    """Validate, compile, and render one KAS profile."""
    policy = compile_kiro_policy(profile)
    all_resources = resources + (profile.resources or [])
    if len(set(all_resources)) != len(all_resources):
        raise KiroPolicyError("contradictory-resource", "KAS resources contain duplicates")
    unsupported = [
        resource for resource in all_resources if not resource.startswith(("file://", "skill://"))
    ]
    if unsupported:
        raise KiroPolicyError(
            "unknown-resource",
            f"unsupported KAS resource identity {unsupported[0]!r}",
        )
    raw_prompt = profile.prompt.strip() if profile.prompt and profile.prompt.strip() else None
    config = KASAgentConfig(
        name=profile.name,
        description=profile.description,
        tools=list(policy.visible_tools),
        permissions=policy.permissions,
        resources=all_resources,
        prompt=raw_prompt,
        mcpServers=mcp_servers,
        hooks=profile.hooks,
        model=profile.model,
    )
    try:
        rendered = config.model_dump_json(indent=2, exclude_none=True)
    except (PydanticSerializationError, TypeError, ValueError) as exc:
        raise KiroPolicyError(
            "serialization-error", f"KAS profile serialization failed: {exc}"
        ) from exc
    return rendered, policy


def atomic_write_text(destination: Path, content: str) -> None:
    """Atomically replace one UTF-8 artifact after complete serialization."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def runtime_kas_profile_name(terminal_id: str) -> str:
    import re

    if not re.fullmatch(r"[0-9a-f]{8}", terminal_id):
        raise ValueError("Invalid terminal identity for KAS artifact")
    return f"cao-runtime-{terminal_id}"


def kas_runtime_resources(profile: AgentProfile) -> list[str]:
    """Exact runtime resource identities shared by rendering and plan freezing."""
    from cli_agent_orchestrator.constants import SKILLS_DIR

    return [
        f"skill://{SKILLS_DIR}/**/SKILL.md",
        f"skill://{SKILLS_DIR}/*/SKILL.md",
        *(profile.resources or []),
    ]


def _runtime_kas_text(terminal_id: str, profile: AgentProfile) -> str:
    from cli_agent_orchestrator.services.install_service import _inject_kiro_mcp_timeout

    name = runtime_kas_profile_name(terminal_id)
    effective = profile.model_copy(update={"name": name, "resources": []})
    resources = kas_runtime_resources(profile)
    rendered, _ = render_kiro_kas(
        effective, resources, _inject_kiro_mcp_timeout(profile.mcpServers)
    )
    return rendered


def install_runtime_kas_profile(
    terminal_id: str, profile: AgentProfile, *, directory: Optional[Path] = None
) -> Path:
    """Write a private per-terminal grant without modifying shared installed profiles."""
    directory = directory or KIRO_AGENTS_DIR
    name = runtime_kas_profile_name(terminal_id)
    path = kiro_artifact_path(directory, name, KiroEngine.KAS)
    rendered = _runtime_kas_text(terminal_id, profile)
    _publish_private_runtime_text(path, rendered)
    _publish_private_runtime_text(
        _runtime_kas_source_path(directory, terminal_id), profile.model_dump_json()
    )
    return path


def _runtime_kas_source_path(directory: Path, terminal_id: str) -> Path:
    return directory / f".{runtime_kas_profile_name(terminal_id)}.kas.source.json"


def _read_private_runtime_text(path: Path) -> str:
    """Open a bounded private regular file without following links or blocking on FIFOs."""
    import stat

    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ValueError("KAS runtime private proof unavailable") from exc
    with os.fdopen(descriptor, encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        limit = 1024 * 1024
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > limit:
            raise ValueError("KAS runtime proof is not a private bounded regular file")
        material = stream.read(limit + 1)
        if len(material.encode("utf-8")) > limit:
            raise ValueError("KAS runtime proof exceeds its material bound")
        return material


def _publish_private_runtime_text(path: Path, rendered: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if _read_private_runtime_text(path) != rendered:
                raise ValueError("Competing KAS runtime artifact has different material") from None
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def verify_runtime_kas_profile(
    terminal_id: str,
    profile: AgentProfile,
    expected_digest: str,
    *,
    directory: Optional[Path] = None,
) -> str:
    """Prove both persisted effective policy and the artifact the wrapper will read."""
    import os
    import stat

    from cli_agent_orchestrator.utils.kiro_launch_guard import kas_policy_digest

    if not expected_digest or kas_policy_digest(profile) != expected_digest:
        raise ValueError("KAS runtime policy proof differs from current material")
    directory = directory or KIRO_AGENTS_DIR
    name = runtime_kas_profile_name(terminal_id)
    path = kiro_artifact_path(directory, name, KiroEngine.KAS)
    try:
        material = _read_private_runtime_text(path)
    except OSError as exc:
        raise ValueError("KAS runtime artifact unavailable") from exc
    if material != _runtime_kas_text(terminal_id, profile):
        raise ValueError("KAS runtime artifact differs from admitted material")
    return name


def resolve_kas_launch_profile(
    profile: AgentProfile, allowed_tools: Optional[list[str]], model: Optional[str] = None
) -> AgentProfile:
    """Build effective native grant; explicit empty policy remains closed."""
    from cli_agent_orchestrator.agent_plugins.mcp_delivery import apply_plugin_mcp_servers

    effective = profile.model_copy(deep=True)
    effective.engine = KiroEngine.KAS
    if allowed_tools is not None:
        effective.allowedTools = list(allowed_tools)
    if model is not None:
        effective.model = model
    apply_plugin_mcp_servers(
        effective, provider="kiro_cli", persisted=True, normalize_existing=True
    )
    return effective


def guard_existing_kas_runtime(terminal_id: str, metadata: Optional[dict]) -> AgentProfile:
    """Restore the private immutable launch snapshot, never a mutable current profile."""
    import stat

    from cli_agent_orchestrator.models.kiro_launch import KiroLaunchRefusedError
    from cli_agent_orchestrator.utils.kiro_launch_guard import assert_kas_launch_allowed

    digest = metadata.get("kiro_policy_digest") if metadata else None
    if not metadata or not digest:
        assert_kas_launch_allowed(engine=KiroEngine.KAS, require_persisted_proof=True)
    assert metadata is not None and isinstance(digest, str)
    path = _runtime_kas_source_path(KIRO_AGENTS_DIR, terminal_id)
    try:
        parsed = AgentProfile.model_validate_json(_read_private_runtime_text(path))
        if parsed.name != metadata.get("agent_profile") or parsed.allowedTools != metadata.get(
            "allowed_tools"
        ):
            raise ValueError("KAS snapshot does not match terminal policy identity")
        assert_kas_launch_allowed(
            engine=KiroEngine.KAS,
            profile=parsed,
            expected_digest=digest,
            require_persisted_proof=True,
        )
        verify_runtime_kas_profile(terminal_id, parsed, digest)
        return parsed
    except KiroLaunchRefusedError:
        raise
    except (OSError, ValueError) as exc:
        raise KiroLaunchRefusedError(
            code="policy-proof-invalid",
            message="KAS runtime launch proof is unavailable or changed; reconcile this runtime.",
        ) from exc


def delete_runtime_kas_proof(terminal_id: str) -> None:
    """Release exact per-terminal proof only after confirmed runtime/row teardown."""
    name = runtime_kas_profile_name(terminal_id)
    for path in (
        kiro_artifact_path(KIRO_AGENTS_DIR, name, KiroEngine.KAS),
        _runtime_kas_source_path(KIRO_AGENTS_DIR, terminal_id),
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # A private orphan is preferable to losing a durable teardown result.
            logger.warning("KAS runtime private proof cleanup deferred for %s", terminal_id)
