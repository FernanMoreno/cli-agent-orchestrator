#!/usr/bin/env python3
"""Generate deterministic release provenance using Python stdlib only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


def build_manifest(
    root: Path, dist: Path, revision: str, source_locks: list[Path] | None = None
) -> dict:
    """Describe built distribution bytes and every committed dependency lock."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("revision must be an exact 40-character Git commit SHA")
    metadata = (root / "pyproject.toml").read_text().split("[project]", 1)[1].split("\n[", 1)[0]
    project = {
        key: json.loads(value)
        for key, value in re.findall(r'^(name|version)\s*=\s*("[^"\n]*")\s*$', metadata, re.M)
    }
    if set(project) != {"name", "version"}:
        raise ValueError("project name/version unavailable")
    artifacts = []
    for path in sorted(dist.iterdir()):
        if path.name == "release-manifest.json":
            continue
        if path.is_symlink():
            raise ValueError(f"artifact symlink is not allowed: {path.name}")
        if not path.is_file():
            continue
        data = path.read_bytes()
        artifacts.append(
            {"name": path.name, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        )
    if not artifacts:
        raise ValueError("no built artifacts found")
    dependencies = []
    locks = []
    # Only source locks, never package-manager caches or installed environments.
    if source_locks is None:
        source_locks = []
        excluded = {"node_modules", ".venv", ".git", "target", "dist", "build", "graphify-out"}
        for directory, directories, filenames in os.walk(root):
            directories[:] = [name for name in directories if name not in excluded]
            source_locks.extend(
                Path(directory) / name
                for name in filenames
                if name in {"uv.lock", "Cargo.lock", "package-lock.json", "bun.lock"}
            )
    candidates = sorted(source_locks)
    for path in candidates:
        relative = path.relative_to(root)
        if any(
            part in {"node_modules", ".venv", ".git", "target", "dist", "build", "graphify-out"}
            for part in relative.parts
        ):
            continue
        if path.is_symlink():
            raise ValueError(f"dependency lock symlink is not allowed: {relative}")
        raw = path.read_bytes()
        locks.append({"path": relative.as_posix(), "sha256": hashlib.sha256(raw).hexdigest()})
        if path.name == "package-lock.json":
            packages = json.loads(raw).get("packages", {})
            for name, package in sorted(packages.items()):
                if name and package.get("version"):
                    dependencies.append(
                        {
                            "ecosystem": "npm",
                            "environment": relative.parent.as_posix(),
                            "name": name.rsplit("node_modules/", 1)[-1],
                            "version": package["version"],
                        }
                    )
        elif path.name == "bun.lock":
            # Bun's generated text lock is JSON with trailing commas. Identity
            # records have one canonical line; parse only those package records.
            records = re.findall(r'^\s*"[^"\n]+": \["([^"\n]+)",', raw.decode(), re.M)
            if not records:
                raise ValueError(f"unavailable Bun package identities in {relative}")
            for identity in records:
                name, version = identity.rsplit("@", 1)
                dependencies.append(
                    {
                        "ecosystem": "bun",
                        "environment": relative.parent.as_posix(),
                        "name": name,
                        "version": version,
                    }
                )
        else:
            ecosystem = "python" if path.name == "uv.lock" else "cargo"
            for section in raw.decode().split("[[package]]")[1:]:
                package = {
                    key: json.loads(value)
                    for key, value in re.findall(
                        r'^(name|version)\s*=\s*("[^"\n]*")\s*$', section, re.M
                    )
                }
                if set(package) != {"name", "version"}:
                    raise ValueError(f"unavailable package identity in {relative}")
                dependencies.append(
                    {
                        "ecosystem": ecosystem,
                        "environment": relative.parent.as_posix(),
                        "name": package["name"],
                        "version": package["version"],
                    }
                )
    if not locks or not dependencies:
        raise ValueError("dependency inventory unavailable: source lockfiles are required")
    return {
        "schema_version": 1,
        "name": project["name"],
        "version": project["version"],
        "revision": revision,
        "artifacts": artifacts,
        "dependency_locks": locks,
        "dependencies": sorted(
            dependencies,
            key=lambda item: (
                item["environment"],
                item["ecosystem"],
                item["name"],
                item["version"],
            ),
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--output", type=Path, default=Path("release-manifest.json"))
    args = parser.parse_args()
    revision = subprocess.check_output(
        ["git", "-C", str(args.root), "rev-parse", "HEAD"], text=True
    ).strip()
    tracked = subprocess.check_output(["git", "-C", str(args.root), "ls-files", "-z"])
    source_locks = [
        args.root / name
        for name in tracked.decode().split("\0")
        if name and Path(name).name in {"uv.lock", "Cargo.lock", "package-lock.json", "bun.lock"}
    ]
    for path in [args.root / "pyproject.toml", *source_locks]:
        relative = path.relative_to(args.root).as_posix()
        committed = subprocess.check_output(
            ["git", "-C", str(args.root), "show", f"{revision}:{relative}"]
        )
        if path.is_symlink() or path.read_bytes() != committed:
            raise ValueError(f"candidate source differs from committed revision: {relative}")
    manifest = build_manifest(args.root, args.dist, revision, source_locks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
