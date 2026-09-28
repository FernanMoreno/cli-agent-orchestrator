"""Isolated paths for durable-work tests; never open the operator's store."""

from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class WorkStorePaths:
    database: Path
    artifacts: Path


@pytest.fixture
def work_store_paths(tmp_path: Path) -> WorkStorePaths:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(mode=0o700)
    return WorkStorePaths(tmp_path / "work.sqlite3", artifacts)
