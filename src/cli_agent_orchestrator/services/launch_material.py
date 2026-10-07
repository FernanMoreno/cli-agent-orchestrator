"""Capture provider policy from an authorized immutable Work provision.

A profile is diagnostic/configuration material, never a replacement for the
server-owned contract, delivery template, executable pins or snapshot authority.
"""

import fnmatch
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

import frontmatter

from cli_agent_orchestrator.utils import agent_profiles
from cli_agent_orchestrator.utils.profile_value_resolution import (
    AuthorityResolver,
    ProfileValueRefusal,
    _resolve_body_value,
    _resolve_metadata_value,
)
from cli_agent_orchestrator.utils.tool_mapping import resolve_allowed_tools


class LaunchMaterialError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def freeze_kas_resources(profile, checkout_root):
    """Pin bounded KAS runtime resources under approved workspace/skills roots."""
    from cli_agent_orchestrator.constants import SKILLS_DIR
    from cli_agent_orchestrator.services.kiro_profiles import kas_runtime_resources
    from cli_agent_orchestrator.services.secret_gate import scan_for_secrets

    roots = (Path(checkout_root).resolve(strict=True), Path(SKILLS_DIR).expanduser().resolve())
    patterns = kas_runtime_resources(profile)
    if len(patterns) > 64:
        raise LaunchMaterialError("profile_resource_bound_exceeded")
    files, canonical_patterns = {}, []
    visited_count = 0
    total_bytes = 0

    def contained(path):
        return any(path == root or root in path.parents for root in roots)

    def pin(path):
        nonlocal total_bytes
        actual = path.resolve(strict=True)
        if not contained(actual) or not actual.is_file():
            raise LaunchMaterialError("profile_resource_escape")
        if str(actual) in files:
            return
        if len(files) >= 256 or actual.stat().st_size > 65536:
            raise LaunchMaterialError("profile_resource_bound_exceeded")
        data = actual.read_bytes()
        total_bytes += len(data)
        if len(data) > 65536 or total_bytes > 131072:
            raise LaunchMaterialError("profile_resource_bound_exceeded")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            raise LaunchMaterialError("profile_resource_encoding_invalid") from None
        if scan_for_secrets(content):
            raise LaunchMaterialError("profile_resource_credentials_unsupported")
        files[str(actual)] = {
            "path": str(actual),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content": content,
        }

    for resource in patterns:
        if not isinstance(resource, str) or not resource.startswith(("skill://", "file://")):
            raise LaunchMaterialError("profile_resource_identity_invalid")
        value = resource.split("://", 1)[1]
        if (
            not value
            or "${" in value
            or "{{" in value
            or "\x00" in value
            or ".." in Path(value).parts
        ):
            raise LaunchMaterialError("profile_resource_reference_unresolved")
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = roots[0] / candidate
        pattern = str(candidate)
        wildcard = next((i for i, char in enumerate(pattern) if char in "*?["), None)
        if wildcard is None:
            if not contained(candidate.resolve(strict=True)):
                raise LaunchMaterialError("profile_resource_escape")
            canonical_patterns.append(pattern)
            pin(candidate)
            continue
        prefix = Path(pattern[:wildcard]).parent
        # A separator before the wildcard means its complete preceding directory
        # is the static prefix; a partial filename uses its parent instead.
        if pattern[:wildcard].endswith(os.sep):
            prefix = Path(pattern[:wildcard])
        actual_prefix = prefix.resolve()
        if not contained(actual_prefix):
            raise LaunchMaterialError("profile_resource_escape")
        canonical_patterns.append(pattern)
        if not actual_prefix.exists():
            continue
        if not actual_prefix.is_dir():
            raise LaunchMaterialError("profile_resource_identity_invalid")
        seen_directories = set()
        for directory, names, filenames in os.walk(prefix, followlinks=True):
            actual_directory = Path(directory).resolve(strict=True)
            if not contained(actual_directory):
                raise LaunchMaterialError("profile_resource_escape")
            if actual_directory in seen_directories:
                names[:] = []
                continue
            seen_directories.add(actual_directory)
            visited_count += 1 + len(names) + len(filenames)
            if visited_count > 4096:
                raise LaunchMaterialError("profile_resource_bound_exceeded")
            for name in names:
                if not contained((Path(directory) / name).resolve(strict=True)):
                    raise LaunchMaterialError("profile_resource_escape")
            for name in sorted(filenames):
                path = Path(directory) / name
                spelling = str(path)
                if fnmatch.fnmatchcase(spelling, pattern) or (
                    "**/" in pattern and fnmatch.fnmatchcase(spelling, pattern.replace("**/", ""))
                ):
                    pin(path)
    return {
        "patterns": sorted(set(canonical_patterns)),
        "files": [files[key] for key in sorted(files)],
    }


def freeze_launch_material(profile_name, provision):
    """Freeze complete current profile plus actual Work execution configuration."""
    raw = agent_profiles._read_agent_profile_source(profile_name)
    # Environment references in permission-bearing fields cannot silently become
    # authority. Typed server provisions already carry the resolved authority.
    parsed = frontmatter.loads(raw)
    names = set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", raw))
    resolver = AuthorityResolver(authority_env={}, credential_names=names)
    try:
        _resolve_metadata_value(dict(parsed.metadata), resolver.resolve)
        _resolve_body_value(parsed.content, resolver.resolve)
    except ProfileValueRefusal as error:
        raise LaunchMaterialError(error.code) from None
    unresolved = agent_profiles.parse_agent_profile_text(raw, profile_name)
    from cli_agent_orchestrator.agent_plugins.mcp_delivery import (
        apply_plugin_mcp_servers,
        grantable_server_names,
    )

    allowed = resolve_allowed_tools(
        unresolved.allowedTools, unresolved.role, grantable_server_names(unresolved)
    )
    if getattr(unresolved.engine, "value", unresolved.engine) == "kas":
        from cli_agent_orchestrator.services.kiro_profiles import resolve_kas_launch_profile

        unresolved = resolve_kas_launch_profile(unresolved, allowed, unresolved.model)
    else:
        unresolved = unresolved.model_copy(deep=True)
        apply_plugin_mcp_servers(
            unresolved,
            provider=provision.contract.provider,
            persisted=True,
            normalize_existing=True,
        )
    profile = unresolved.model_dump(mode="json")
    from cli_agent_orchestrator.services.secret_gate import scan_for_secrets
    from cli_agent_orchestrator.utils.env import load_env_vars

    credential_names = set(load_env_vars())
    for server in (unresolved.mcpServers or {}).values():
        if not isinstance(server, dict):
            raise LaunchMaterialError("profile_mcp_configuration_invalid")
        for name, value in (server.get("env") or {}).items():
            if value and (
                name in credential_names
                or re.search(
                    r"(?i)(token|secret|password|passwd|api.?key|authorization|access.?key)", name
                )
            ):
                raise LaunchMaterialError("mcp_credential_destination_unsupported")
            if not isinstance(value, str) or scan_for_secrets(value):
                raise LaunchMaterialError("mcp_credential_destination_unsupported")

    contract = provision.contract
    if json.loads(provision.delivery_template.payload_json).get("agent_profile") != profile_name:
        raise LaunchMaterialError("profile_delivery_conflict")
    if unresolved.provider not in (None, contract.provider):
        raise LaunchMaterialError("profile_provider_conflict")
    # External configuration/reference paths are included exactly and verified;
    # no uninspected include file is accepted as an executable dependency.
    dependencies = []

    def inspect(value, key=""):
        if isinstance(value, dict):
            for name, child in value.items():
                inspect(child, str(name))
        elif isinstance(value, list):
            for child in value:
                inspect(child, key)
        elif isinstance(value, str):
            if "${" in value or "{{" in value:
                raise LaunchMaterialError("profile_reference_unresolved")
            if key in {"include", "config_file", "config_path", "file", "instructions_file"}:
                path = Path(value).expanduser().resolve(strict=True)
                if not path.is_file() or path.stat().st_size > 262144:
                    raise LaunchMaterialError("profile_dependency_unavailable")
                data = path.read_bytes()
                dependencies.append(
                    {
                        "path": str(path),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "content": data.decode("utf-8"),
                    }
                )

    inspect(profile)
    executables = []
    for server in (unresolved.mcpServers or {}).values():
        if not isinstance(server, dict):
            raise LaunchMaterialError("profile_mcp_configuration_invalid")
        command = server.get("command")
        if command is None:
            continue  # A remote URL remains exact frozen configuration.
        if not isinstance(command, str):
            raise LaunchMaterialError("profile_mcp_configuration_invalid")
        located = shutil.which(command)
        if located is None:
            raise LaunchMaterialError("profile_executable_unavailable")
        path = Path(located).resolve(strict=True)
        if not path.is_file() or path.stat().st_size > 16777216:
            raise LaunchMaterialError("profile_executable_unavailable")
        executables.append(
            {
                "command": command,
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
        for arg in server.get("args", ()):
            if isinstance(arg, str) and Path(arg).is_absolute() and Path(arg).is_file():
                data = Path(arg).read_bytes()
                if len(data) > 262144:
                    raise LaunchMaterialError("profile_dependency_unavailable")
                dependencies.append(
                    {
                        "path": str(Path(arg).resolve()),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "content": data.decode("utf-8"),
                    }
                )
    kiro_digest = None
    resource_material = None
    if getattr(unresolved.engine, "value", unresolved.engine) == "kas":
        from cli_agent_orchestrator.utils.kiro_launch_guard import kas_policy_digest

        kiro_digest = kas_policy_digest(unresolved)
        resource_material = freeze_kas_resources(unresolved, contract.resources.checkout_root)
    return {
        "profile_name": profile_name,
        "profile": profile,
        "profile_source": raw,
        "profile_hash": hashlib.sha256(raw.encode()).hexdigest(),
        "allowed_tools": allowed,
        "provider": contract.provider,
        "model": (contract.model.value if contract.model.status == "known" else unresolved.model),
        "engine": getattr(unresolved.engine, "value", unresolved.engine),
        "dependencies": dependencies,
        "executables": executables,
        "kiro_policy_digest": kiro_digest,
        "resource_material": resource_material,
        "contract": contract.model_dump(mode="json"),
        "contract_hash": provision.contract_hash,
        "delivery": provision.delivery_template.model_dump(mode="json"),
        "delivery_hash": provision.delivery_template_hash,
        "provision_id": provision.ref.id,
        "provision_revision": provision.ref.revision,
        "provision_fingerprint": provision.provision_fingerprint,
    }


def verify_dependency_closure(material):
    """Refuse drift; execution still consumes its frozen Work contract/delivery."""
    for executable in material.get("executables", ()):
        located = shutil.which(executable["command"])
        path = Path(executable["path"])
        if (
            located is None
            or str(Path(located).resolve()) != executable["path"]
            or not path.is_file()
            or path.stat().st_size > 16777216
        ):
            raise LaunchMaterialError("profile_executable_unavailable")
        if hashlib.sha256(path.read_bytes()).hexdigest() != executable["sha256"]:
            raise LaunchMaterialError("profile_executable_changed")
    for dependency in material.get("dependencies", ()):
        path = Path(dependency["path"])
        if not path.is_file() or path.stat().st_size > 262144:
            raise LaunchMaterialError("profile_dependency_unavailable")
        if hashlib.sha256(path.read_bytes()).hexdigest() != dependency["sha256"]:
            raise LaunchMaterialError("profile_dependency_changed")
    # Plugin delivery/config changes alter the effective closure even when raw
    # source does not. Rebuild configuration only to compare, never to authorize.
    from types import SimpleNamespace

    from cli_agent_orchestrator.models.work_delivery import WorkDeliveryEnvelope
    from cli_agent_orchestrator.services.work_contract import WorkContracts

    provision = SimpleNamespace(
        contract=WorkContracts._from_json(
            json.dumps(
                material["contract"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        ),
        contract_hash=material["contract_hash"],
        delivery_template=WorkDeliveryEnvelope.model_validate_json(
            json.dumps(material["delivery"])
        ),
        delivery_template_hash=material["delivery_hash"],
        ref=SimpleNamespace(id=material["provision_id"], revision=material["provision_revision"]),
        provision_fingerprint=material["provision_fingerprint"],
    )
    current = freeze_launch_material(material["profile_name"], provision)
    if current.get("resource_material") != material.get("resource_material"):
        raise LaunchMaterialError("profile_resource_changed")
    if (
        current["profile"] != material["profile"]
        or current["allowed_tools"] != material["allowed_tools"]
    ):
        raise LaunchMaterialError("profile_effective_policy_changed")
    # A profile edit must not silently alter provider install behavior that still
    # reads that profile; refuse until a new exact plan is prepared.
    raw = agent_profiles._read_agent_profile_source(material["profile_name"])
    if hashlib.sha256(raw.encode()).hexdigest() != material["profile_hash"]:
        raise LaunchMaterialError("profile_changed")
