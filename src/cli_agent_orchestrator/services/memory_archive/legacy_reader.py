"""Validate frozen native format-v1 before mapping into the current importer.

Archive paths, source subjects and row IDs never become local authority. Explicit
current target scope and the existing store/secret/vault gates own every write.
"""

import json
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import frontmatter

from cli_agent_orchestrator.services.memory_archive.legacy_format import (
    CONTENT_HASH_RE,
    DUMP_ROW_REQUIRED_KEYS,
    EXPORTED_BY_RE,
    KEY_REGEX,
    MANIFEST_CAP_BYTES,
    MANIFEST_REQUIRED_KEYS,
    PROJECT_ID_RE,
    SUPPORTED_FORMAT_VERSION,
    VALID_SCOPES,
    ArchiveRejection,
    compute_content_hash,
)


def _validate_manifest(raw: bytes) -> tuple:
    """T4. Return ``(manifest_or_None, rejection_or_None)``."""
    if len(raw) > MANIFEST_CAP_BYTES:
        return None, ArchiveRejection(
            member="manifest.json", reason="size_exceeds_cap", detail="manifest cap"
        )
    try:
        obj = json.loads(raw)
    except Exception:
        return None, ArchiveRejection(member="manifest.json", reason="manifest_invalid")
    if not isinstance(obj, dict):
        return None, ArchiveRejection(member="manifest.json", reason="manifest_invalid")
    if set(obj.keys()) != MANIFEST_REQUIRED_KEYS:
        return None, ArchiveRejection(
            member="manifest.json",
            reason="manifest_invalid",
            detail="key set mismatch",
        )

    # Per-key type checks.
    fv = obj["format_version"]
    if not (isinstance(fv, int) and not isinstance(fv, bool)):
        return None, ArchiveRejection(member="manifest.json", reason="format_version_unsupported")
    if fv != SUPPORTED_FORMAT_VERSION:
        return None, ArchiveRejection(member="manifest.json", reason="format_version_unsupported")

    pid = obj["project_id"]
    if not (isinstance(pid, str) and PROJECT_ID_RE.match(pid)):
        return None, ArchiveRejection(member="manifest.json", reason="manifest_invalid")

    id_kind = obj["id_kind"]
    if not (isinstance(id_kind, str) and id_kind):
        return None, ArchiveRejection(member="manifest.json", reason="manifest_invalid")

    created_at = obj["created_at"]
    if not isinstance(created_at, str):
        return None, ArchiveRejection(member="manifest.json", reason="manifest_invalid")
    try:
        datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None, ArchiveRejection(member="manifest.json", reason="manifest_invalid")

    eb = obj["exported_by"]
    if not (isinstance(eb, str) and EXPORTED_BY_RE.match(eb)):
        return None, ArchiveRejection(member="manifest.json", reason="manifest_invalid")

    ss = obj["scope_set"]
    if not (isinstance(ss, list) and ss):
        return None, ArchiveRejection(member="manifest.json", reason="manifest_invalid")
    for s in ss:
        if not (isinstance(s, str) and s in VALID_SCOPES):
            return None, ArchiveRejection(member="manifest.json", reason="manifest_invalid")

    nwf = obj["n_wiki_files"]
    nmr = obj["n_metadata_rows"]
    if not (isinstance(nwf, int) and not isinstance(nwf, bool) and nwf >= 0):
        return None, ArchiveRejection(member="manifest.json", reason="manifest_invalid")
    if not (isinstance(nmr, int) and not isinstance(nmr, bool) and nmr >= 0):
        return None, ArchiveRejection(member="manifest.json", reason="manifest_invalid")

    ch = obj["content_hash"]
    if not (isinstance(ch, str) and CONTENT_HASH_RE.match(ch)):
        return None, ArchiveRejection(member="manifest.json", reason="manifest_invalid")

    return obj, None


def _validate_dump_row(row: Any, manifest: dict, target_project_id: str) -> tuple:
    """T5. Returns ``(ok, action_or_rejection)``.

    ``federated`` is treated as a plain scope value: no ``is_federated`` flag
    and no federation invariant check. Only the agent ban (T11) and scope
    elevation (T10) gate the scope field.
    """
    if not isinstance(row, dict):
        return False, ArchiveRejection(member="sqlite-dump.json", reason="dump_row_invalid")
    if set(row.keys()) != DUMP_ROW_REQUIRED_KEYS:
        return False, ArchiveRejection(
            member="sqlite-dump.json",
            reason="dump_row_invalid",
            detail="row key set mismatch",
        )

    scope = row.get("scope")
    if not (isinstance(scope, str) and scope in VALID_SCOPES):
        return False, ArchiveRejection(
            member="sqlite-dump.json", reason="scope_invalid", detail=str(scope)
        )
    if scope == "agent":
        return False, ArchiveRejection(
            member="sqlite-dump.json", reason="scope_invalid", detail="agent ban (T11)"
        )
    if scope not in manifest.get("scope_set", []):
        return False, ArchiveRejection(
            member="sqlite-dump.json",
            reason="scope_elevation",
            detail=f"row scope={scope} not in scope_set",
        )

    key = row.get("key")
    if not (isinstance(key, str) and KEY_REGEX.match(key)):
        return False, ArchiveRejection(
            member="sqlite-dump.json", reason="key_invalid", detail=str(key)
        )

    ac = row.get("access_count", 0)
    if not (isinstance(ac, int) and not isinstance(ac, bool) and ac >= 0):
        return False, ArchiveRejection(
            member="sqlite-dump.json", reason="dump_row_invalid", detail="access_count"
        )

    # Strict validation for important free-form fields.
    for ts_field in ("created_at", "updated_at"):
        v = row.get(ts_field)
        if v is None:
            continue
        if not isinstance(v, str):
            return False, ArchiveRejection(
                member="sqlite-dump.json", reason="dump_row_invalid", detail=ts_field
            )
        try:
            datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return False, ArchiveRejection(
                member="sqlite-dump.json",
                reason="dump_row_invalid",
                detail=f"{ts_field} ISO-8601-Z",
            )

    if scope == "global" and row.get("scope_id") is not None:
        return False, ArchiveRejection(
            member="sqlite-dump.json",
            reason="scope_invalid",
            detail="global rows must have scope_id NULL",
        )

    return True, row


@contextmanager
def as_current_bundle(root: Path):
    marker = root / "manifest.json"
    if not marker.exists():
        yield root
        return
    manifest, rejection = _validate_manifest(marker.read_bytes())
    if rejection:
        raise ValueError("Invalid native archive manifest")
    rows = json.loads((root / "sqlite-dump.json").read_bytes())
    if not isinstance(rows, list) or len(rows) > 4096:
        raise ValueError("Native archive row limit")
    names = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    allowed = {"manifest.json", "sqlite-dump.json", "index.md"}
    wiki = {}
    for name in names - allowed:
        match = re.fullmatch(r"wiki/([a-z0-9-]{1,60})\.md", name)
        if not match:
            raise ValueError("Unexpected native archive member")
        wiki[match[1]] = (root / name).read_bytes()
    if manifest["n_metadata_rows"] != len(rows) or manifest["n_wiki_files"] != len(wiki):
        raise ValueError("Native archive counts differ")
    index = (root / "index.md").read_bytes() if (root / "index.md").exists() else b""
    if (
        compute_content_hash(manifest=manifest, dump_rows=rows, index_md=index, wiki_files=wiki)
        != manifest["content_hash"]
    ):
        raise ValueError("Native archive hash differs")
    seen = set()
    prepared = []
    for row in rows:
        valid, _ = _validate_dump_row(row, manifest, "")
        if not valid or row["scope"] not in {"global", "project", "federated"}:
            raise ValueError("Native archive scope or row invalid")
        key = row["key"]
        if key in seen or key not in wiki:
            raise ValueError("Native archive key missing or ambiguous")
        seen.add(key)
        text = wiki[key].decode("utf-8")
        from cli_agent_orchestrator.services.secret_gate import scan_for_secrets

        if scan_for_secrets(text):
            raise ValueError("Native archive contains credential patterns")
        # Retain every historical fact as content. The current importer escapes
        # archived structural markers; archived row IDs never become authority.
        body = text
        tags = row["tags"] or ""
        if not isinstance(tags, str) or len(tags) > 32768:
            raise ValueError("Native archive tags invalid")
        metadata = {"type": row["memory_type"], "tags": tags, "timestamp": row["updated_at"]}
        prepared.append((key, frontmatter.dumps(frontmatter.Post(body, **metadata))))
    if seen != set(wiki):
        raise ValueError("Native archive orphan content")
    with tempfile.TemporaryDirectory(prefix="cao-native-memory-") as temporary:
        destination = Path(temporary)
        for key, text in prepared:
            (destination / (key + ".md")).write_text(text, encoding="utf-8")
        yield destination
