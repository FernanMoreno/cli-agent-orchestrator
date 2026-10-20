"""Real CLI repair over a native PostgreSQL metadata store.

Repair reconciles canonical files; schema installation is owner-only and an
unsupported/corrupt legacy database must be validated before offline import.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from psycopg import sql

from cli_agent_orchestrator.clients.postgres import IntegrityError, connect


def _home(tmp_path):
    home = tmp_path / "cao-home"
    (home / "memory" / "global" / "wiki" / "global").mkdir(parents=True)
    return home


def _topic(home, key="shared"):
    path = home / "memory" / "global" / "wiki" / "global" / f"{key}.md"
    path.write_text(
        f"# {key}\n<!-- id: 11111111-1111-1111-1111-111111111111 | scope: global | type: reference | tags:  -->\n\n## 2026-07-15T01:00:00Z\ndurable body\n"
    )
    return path


def _run_repair(home, *args):
    env = os.environ.copy()
    env.update(CAO_HOME_DIR=str(home), PATH="")
    for name in ("AUTH0_DOMAIN", "CAO_AUTH_JWKS_URI"):
        env.pop(name, None)
    return subprocess.run(
        [sys.executable, "-m", "cli_agent_orchestrator.cli.main", "memory", "repair", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_real_repair_preserves_topic_and_creates_native_metadata(tmp_path, isolated_memory_db):
    home = _home(tmp_path)
    path = _topic(home)
    before = path.read_bytes()
    result = _run_repair(home, "--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    assert path.read_bytes() == before
    with connect(isolated_memory_db) as connection:
        row = connection.execute(
            "SELECT key,file_path FROM memory_metadata WHERE key='shared'"
        ).fetchone()
        assert tuple(row) == ("shared", str(path))
        with pytest.raises(IntegrityError):
            connection.execute(
                "INSERT INTO memory_metadata(id,key,memory_type,scope,scope_id,file_path,tags,source_kind) VALUES ('duplicate','shared','reference','global',NULL,%s,'','native')",
                (str(path),),
            )


def test_real_repair_dry_run_leaves_files_and_metadata_unchanged(tmp_path, isolated_memory_db):
    home = _home(tmp_path)
    path = _topic(home)
    before = path.read_bytes()
    result = _run_repair(home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert path.read_bytes() == before
    with connect(isolated_memory_db) as connection:
        assert connection.execute("SELECT count(*) FROM memory_metadata").fetchone()[0] == 0


def test_real_repair_empty_store_keeps_native_unique_index(tmp_path, isolated_memory_db):
    home = _home(tmp_path)
    result = _run_repair(home, "--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    with connect(isolated_memory_db) as connection:
        assert connection.execute("SELECT count(*) FROM memory_metadata").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT 1 FROM pg_indexes WHERE schemaname=%s AND indexname='uq_memory_key_scope_null'",
                (isolated_memory_db.schema,),
            ).fetchone()
            is not None
        )


@pytest.mark.parametrize(
    "timestamp", [datetime(2026, 7, 15), datetime(2026, 7, 15, tzinfo=timezone.utc)]
)
def test_real_repair_handles_native_utc_and_naive_timestamps(
    tmp_path, isolated_memory_db, timestamp
):
    home = _home(tmp_path)
    path = _topic(home)
    with connect(isolated_memory_db) as connection:
        connection.execute(
            "INSERT INTO memory_metadata(id,key,memory_type,scope,scope_id,file_path,tags,source_kind,created_at,updated_at) VALUES ('existing','shared','reference','global',NULL,%s,'','native',%s,%s)",
            (str(path), timestamp, timestamp),
        )
    result = _run_repair(home, "--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "durable body" in path.read_text()
    with connect(isolated_memory_db) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM memory_metadata WHERE key='shared'"
            ).fetchone()[0]
            == 1
        )


def test_real_repair_refuses_schema_damage_before_file_effects(tmp_path, isolated_memory_db):
    home = _home(tmp_path)
    path = _topic(home)
    before = path.read_bytes()
    with connect(isolated_memory_db.migration_target()) as owner:
        owner.execute(
            sql.SQL("DROP INDEX {}.uq_memory_key_scope_null").format(
                sql.Identifier(isolated_memory_db.schema)
            )
        )
    result = _run_repair(home, "--apply")
    assert result.returncode != 0
    assert path.read_bytes() == before
    assert not (path.parent / "index.md").exists()
    with connect(isolated_memory_db) as connection:
        assert connection.execute("SELECT count(*) FROM memory_metadata").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT 1 FROM pg_indexes WHERE schemaname=%s AND indexname='uq_memory_key_scope_null'",
                (isolated_memory_db.schema,),
            ).fetchone()
            is None
        )
