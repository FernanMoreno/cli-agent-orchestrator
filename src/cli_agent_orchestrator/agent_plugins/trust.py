"""Local approval of exact package bytes; never a claim of publisher identity."""

from __future__ import annotations

import hashlib
import json
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Dict

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion

from cli_agent_orchestrator.agent_plugins.models import (
    PluginRecord,
    PluginSource,
    PluginValidationReport,
)
from cli_agent_orchestrator.agent_plugins.validation import validate_plugin


def content_digest(root: Path) -> str:
    """Hash names, file bytes and link targets without following package links."""
    digest = hashlib.sha256()

    def add(value: bytes) -> None:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    for base, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        files.sort()
        for name in sorted(dirs + files):
            path = Path(base) / name
            add(path.relative_to(root).as_posix().encode())
            if path.is_symlink():
                add(b"link")
                add(os.readlink(path).encode())
            elif path.is_file():
                add(b"file")
                add(path.read_bytes())
                add(str(path.stat().st_mode & 0o111).encode())
            else:
                add(b"directory")
    return digest.hexdigest()


def evidence(
    root: Path,
    source: PluginSource,
    resolved_ref: str | None,
    report: PluginValidationReport | None = None,
) -> Dict[str, Any]:
    report = report if report is not None else validate_plugin(root)
    manifest = report.manifest
    permissions = [f"skill:{name}" for name in report.skill_names]
    permissions.extend(f"@{server.name}" for server in report.mcp_servers)
    raw = json.loads((root / "plugin.json").read_text()) if manifest else {}
    extensions = raw.get("extensions")
    extension = extensions.get("org.cao.trust", {}) if isinstance(extensions, dict) else {}
    compatibility = "compatible" if report.loadable else "incompatible"
    requirement = None
    if not isinstance(extension, dict):
        compatibility = "incompatible"
    else:
        requirement = extension.get("cao")
        declared = extension.get("permissions", [])
        if not isinstance(declared, list) or not all(isinstance(p, str) for p in declared):
            compatibility = "incompatible"
        else:
            permissions.extend(declared)
        if requirement is not None:
            try:
                cao_version = version("cli-agent-orchestrator")
                if not isinstance(requirement, str) or cao_version not in SpecifierSet(requirement):
                    compatibility = "incompatible"
            except (InvalidSpecifier, InvalidVersion, PackageNotFoundError, TypeError):
                compatibility = "incompatible"
    data = {
        "producer": manifest.author.to_dict() if manifest and manifest.author else None,
        "producer_status": "unverified",
        "producer_reason": "No trusted publisher attestation is available; a commit pin identifies content only.",
        "source": source.to_dict(),
        "resolved_ref": resolved_ref,
        "version": manifest.version if manifest else None,
        "schema_id": manifest.schema_id if manifest else None,
        "content_sha256": content_digest(root),
        "compatibility": compatibility,
        "cao_requirement": requirement,
        "permissions": sorted(set(permissions)),
    }
    data["review_id"] = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return data


def review_record(record: PluginRecord, root: Path) -> Dict[str, Any]:
    current = evidence(root, record.source, record.resolved_ref)
    current["integrity"] = (
        "unverified" if not record.trust else "matched" if record.trust == current else "changed"
    )
    current["enabled"] = (
        current["integrity"] == "matched"
        and current["compatibility"] == "compatible"
        and record.approval == current["review_id"]
    )
    current["policy"] = "explicit-local-approval"
    current["decision_reason"] = (
        "Exact content and requested permissions approved locally."
        if current["enabled"]
        else "Enable requires approval of the current review and every requested permission; changed or incompatible content is denied."
    )
    return current


def delivery_allowed(record: PluginRecord, root: Path) -> bool:
    try:
        return bool(review_record(record, root)["enabled"])
    except (OSError, ValueError, TypeError, AttributeError):
        return False
