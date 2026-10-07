"""Spec008 authoring regressions: canonical publication and existing CAS contract."""

import hashlib
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from cli_agent_orchestrator.clients.database import _migrate_workflow_index
from cli_agent_orchestrator.services import workflow_spec_service as svc


@pytest.fixture
def authoring_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", tmp_path / "index.db")
    _migrate_workflow_index()
    root = Path.home() / ".cao-test-workflows" / uuid.uuid4().hex
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root)


def revision(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def test_create_is_ast_only_and_returns_exact_source_revision(authoring_dir):
    marker = authoring_dir / "must-not-execute"
    source = f"INPUTS = {{}}\nopen({str(marker)!r}, 'w').write('executed')\n"
    result = svc.create_workflow("new", source, scan_dir=str(authoring_dir))
    assert result == {"name": "new", "content": source, "source_hash": revision(source)}
    assert (authoring_dir / "new.py").read_bytes() == source.encode()
    assert not marker.exists()


def test_concurrent_creators_publish_exactly_one_complete_source(authoring_dir):
    barrier = Barrier(2)
    sources = ["INPUTS = {}\n# author A\n", "INPUTS = {}\n# author B\n"]

    def create(source):
        barrier.wait(timeout=5)
        try:
            return svc.create_workflow("race", source, scan_dir=str(authoring_dir))
        except FileExistsError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, sources))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert (authoring_dir / "race.py").read_text() == winners[0]["content"]
    assert not list(authoring_dir.glob(".*.tmp"))


def test_index_failure_cannot_revert_published_update(authoring_dir, monkeypatch):
    before, after = "INPUTS = {}\n# before\n", "INPUTS = {}\n# after\n"
    target = authoring_dir / "edit.py"
    target.write_text(before)
    original_upsert = svc.upsert_index

    def unavailable(spec, *args, **kwargs):
        # Reads may rebuild old rows; fail precisely after the new revision lands.
        if getattr(spec, "source", None) == after:
            raise OSError("derived index unavailable")
        return original_upsert(spec, *args, **kwargs)

    monkeypatch.setattr(svc, "upsert_index", unavailable)
    result = svc.update_workflow("edit", after, revision(before), str(authoring_dir))
    assert result["source_hash"] == revision(after)
    assert target.read_text() == after
    monkeypatch.setattr(svc, "upsert_index", original_upsert)
    assert svc.get_workflow_source("edit", str(authoring_dir))["content"] == after


def test_update_refuses_existing_non_syntax_lint_error(authoring_dir):
    before = "INPUTS = {}\n"
    target = authoring_dir / "edit.py"
    target.write_text(before)
    invalid = "import cao_workflow\ncao_workflow.step('p', 'a', 'prompt')\n"
    with pytest.raises(ValueError, match="missing-recovery-policy"):
        svc.update_workflow("edit", invalid, revision(before), str(authoring_dir))
    assert target.read_text() == before


def test_update_refuses_named_symlink_without_mutating_destination(authoring_dir):
    source = "INPUTS = {}\n"
    target = authoring_dir / "real.py"
    target.write_text(source)
    alias = authoring_dir / "alias.py"
    alias.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        svc.update_workflow("alias", source + "# changed\n", revision(source), str(authoring_dir))
    assert target.read_text() == source
    assert alias.is_symlink()


def test_concurrent_updates_preserve_required_sha_compare_and_swap(authoring_dir):
    before = "INPUTS = {}\n"
    target = authoring_dir / "edit.py"
    target.write_text(before)
    barrier = Barrier(2)

    def update(source):
        barrier.wait(timeout=5)
        try:
            return svc.update_workflow("edit", source, revision(before), str(authoring_dir))
        except svc.WorkflowRevisionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, [before + "# A\n", before + "# B\n"]))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert target.read_text() == winners[0]["content"]


def test_existing_yaml_update_and_source_dto_remain_supported(authoring_dir):
    before = "name: yaml-edit\nmode: sequential\nsteps:\n  - id: only\n    provider: claude_code\n    agent: developer\n    prompt: old\n"
    after = before.replace("prompt: old", "prompt: new")
    (authoring_dir / "yaml-edit.yaml").write_text(before)
    assert svc.update_workflow("yaml-edit", after, revision(before), str(authoring_dir)) == {
        "name": "yaml-edit",
        "content": after,
        "source_hash": revision(after),
    }
