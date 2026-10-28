"""Private, read-only Docker identity witnesses for native diagnostic owners.

Metadata identifies a missing container; it does not identify its deleting client.
"""

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise RuntimeError(code)


def capture_container_inventory(root: Path, phase: str, *, write_json: Callable) -> set[str]:
    _require(type(phase) is str and phase in {"before", "after"}, "inventory_phase_invalid")
    template = (
        '{"id":{{json .ID}},"name":{{json .Names}},'
        '"image":{{json .Image}},'
        '"session_id":{{json (.Label "org.testcontainers.session-id")}}}'
    )
    wire = subprocess.check_output(
        ["docker", "ps", "-a", "--no-trunc", "--format", template], text=True, timeout=5
    )
    rows = [json.loads(line) for line in wire.splitlines()]
    for row in rows:
        _require(
            type(row) is dict and set(row) == {"id", "name", "image", "session_id"},
            "inventory_row_invalid",
        )
        _require(
            type(row["id"]) is str and re.fullmatch(r"[0-9a-f]{64}", row["id"]),
            "inventory_ids_invalid",
        )
        _require(
            all(
                type(row[key]) is str and len(row[key]) <= limit
                for key, limit in [("name", 512), ("image", 1024), ("session_id", 128)]
            ),
            "inventory_metadata_invalid",
        )
    ids = {row["id"] for row in rows}
    _require(len(ids) == len(rows), "inventory_duplicate_id")
    payload = {
        "schema": "caos.container-inventory.v2",
        "phase": phase,
        "containers": sorted(rows, key=lambda row: row["id"]),
    }
    path = root / ("containers-" + phase + ".json")
    write_json(path, payload)
    _require(
        path.stat().st_mode & 0o777 == 0o600 and json.loads(path.read_text()) == payload,
        "inventory_roundtrip_invalid",
    )
    return ids
