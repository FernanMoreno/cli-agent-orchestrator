"""Tests for freezing a script-tier run's execution manifest (issue #583 Bolt 2, ``manifest-freeze``).

Two of these carry the unit's load:

* ``test_a_secret_in_inputs_does_not_reach_the_manifest`` is the concrete reason
  ``execution_manifest.build`` is the only door. ``inputs`` is arbitrary user JSON and the most likely
  place a credential appears in a workflow run.
* ``test_changing_the_source_hash_changes_the_plan_id`` converts this unit's transitive-coverage
  ARGUMENT into a PROPERTY. Six of FR-8's nine manifest fields are omitted because they have no
  run-level source in this tier; the omission is only safe because the script's own source chooses
  them, so a source change must move the identifier. If this test fails, the omission is unsafe and a
  changed provider could execute under a stale approval.
"""

import json
import os
import subprocess

import pytest

from cli_agent_orchestrator.services import (
    approval_gate,
    approval_store,
    execution_manifest,
    manifest_freeze,
)
from cli_agent_orchestrator.utils import git_baseline

SECRET = "AKIAIOSFODNN7EXAMPLE"  # matches secret_gate's ``aws_access_key`` pattern

OMITTED = ("provider", "model", "profile", "permissions", "limits", "retry_policy")


@pytest.fixture(autouse=True)
def manifest_repository(tmp_path, monkeypatch):
    """Exercise real baseline capture without inspecting the developer checkout.

    Keep production limits and inclusion policy intact. Git state, identity and
    hooks belong to this disposable fixture, not the invoking user's settings.
    """
    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repository = tmp_path / "manifest-repository"
    repository.mkdir()
    git = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], cwd=repository, check=True, capture_output=True)
    (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run([*git, "add", "tracked.txt"], cwd=repository, check=True, capture_output=True)
    subprocess.run(
        [
            *git,
            "-c",
            "user.name=Manifest Test",
            "-c",
            "user.email=manifest@example.invalid",
            "commit",
            "-qm",
            "fixture baseline",
        ],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    monkeypatch.chdir(repository)
    return repository


def _frozen(**overrides) -> dict:
    kwargs = {"source_hash": "abc123", "inputs": {"k": "v"}}
    kwargs.update(overrides)
    value = manifest_freeze.build_manifest_json(**kwargs)
    assert value is not None
    return json.loads(value)


# ---------------------------------------------------------------------------
# The two load-bearing properties
# ---------------------------------------------------------------------------


def test_a_secret_in_inputs_does_not_reach_the_manifest():
    """Everything reaching the column passes through ``build``'s tree redaction."""
    value = manifest_freeze.build_manifest_json(source_hash="h", inputs={"tok": SECRET})
    assert value is not None
    assert SECRET not in value
    assert json.loads(value)["redacted"] is True


def test_changing_the_source_hash_changes_the_plan_id():
    """The transitive-coverage property. See the module docstring.

    The six omitted fields are chosen by the script's own source, so a source change must move the
    identifier. This is what makes omitting them safe rather than merely convenient.
    """
    a = _frozen(source_hash="hash-of-script-v1")["plan_id"]
    b = _frozen(source_hash="hash-of-script-v2")["plan_id"]
    assert a != b


# ---------------------------------------------------------------------------
# What is recorded, and what is not
# ---------------------------------------------------------------------------


def test_the_six_absent_fields_are_omitted_not_null():
    """A null would assert "exists but empty"; these fields have no run-level existence in this tier."""
    document = _frozen()
    for name in OMITTED:
        assert name not in document, f"{name} should be omitted, not present as null"


def test_the_note_explaining_the_omission_travels_in_the_envelope():
    """The envelope is what an agent reads when diagnosing a failed run, so the reason lives there."""
    document = _frozen()
    assert "notes" in document
    for name in OMITTED:
        assert name in document["notes"]
    assert "source_hash" in document["notes"]


def test_the_three_recorded_fields_are_present():
    document = _frozen()
    assert document["source_hash"] == "abc123"
    assert document["inputs"] == {"k": "v"}
    assert "repo_baseline" in document
    assert document["plan_id"].startswith("plan-v1:")


def test_the_memory_record_is_empty_at_freeze_time():
    """There is no resolved memory at run start; ``memory-resolve-once`` (pass 2B) fills it."""
    memory = _frozen()["memory"]
    assert memory["content"] == ""
    assert memory["truncated"] is False


def test_the_frozen_value_round_trips_through_parse():
    value = manifest_freeze.build_manifest_json(source_hash="h", inputs={"a": [1, 2]})
    assert value is not None
    assert execution_manifest.parse(value) is not None


# ---------------------------------------------------------------------------
# Totality and identity stability
# ---------------------------------------------------------------------------


def test_returns_none_and_logs_rather_than_raising(monkeypatch, caplog):
    """Best-effort: the run must not fail because its manifest could not be assembled.

    A ``None`` writes NULL, which the approval gate reads as "never approved" and REFUSES — so the
    failure direction is fail-closed, never an unapproved run that executes.
    """

    def _boom(*_args, **_kwargs):
        raise RuntimeError("assembly exploded")

    monkeypatch.setattr(manifest_freeze.plan_identifier, "compute", _boom)
    with caplog.at_level("WARNING"):
        assert manifest_freeze.build_manifest_json(source_hash="h", inputs={}) is None
    assert any("manifest freeze failed" in r.message for r in caplog.records)


def test_the_plan_id_does_not_depend_on_the_working_directory(monkeypatch, tmp_path):
    """Path-independence: two machines running an identical plan must agree.

    The baseline deliberately excludes the worktree path for exactly this reason, so a differing cwd
    with the same repository state cannot move the identifier.
    """
    monkeypatch.setattr(
        manifest_freeze,
        "derive_baseline",
        lambda _cwd: {"available": True, "commit": "c", "dirty": False},
    )
    a = _frozen(cwd=str(tmp_path))["plan_id"]
    b = _frozen(cwd="/some/other/path")["plan_id"]
    assert a == b


def test_a_missing_baseline_returns_no_manifest(tmp_path, caplog):
    """An unavailable baseline has no identifier that could be approved or replayed."""
    non_repository = tmp_path / "not-a-repository"
    non_repository.mkdir()
    assert git_baseline.derive_baseline(str(non_repository)) == {"available": False}
    assert (
        manifest_freeze.build_manifest_json(
            source_hash="abc123", inputs={"k": "v"}, cwd=str(non_repository)
        )
        is None
    )
    assert "unavailable repository baseline" in caplog.text


def test_real_tracked_and_untracked_changes_both_change_the_plan(manifest_repository):
    clean = _frozen()
    assert clean["repo_baseline"]["worktree_state"] == {"status": "clean"}
    (manifest_repository / "tracked.txt").write_text("modified\n", encoding="utf-8")
    tracked = _frozen()
    (manifest_repository / "untracked.txt").write_text("new content\n", encoding="utf-8")
    untracked = _frozen()
    assert len({document["plan_id"] for document in (clean, tracked, untracked)}) == 3
    assert tracked["repo_baseline"]["worktree_state"]["status"] == "dirty"
    assert untracked["repo_baseline"]["worktree_state"]["status"] == "dirty"


def test_real_over_budget_baseline_cannot_reuse_an_approved_plan(
    manifest_repository, monkeypatch, caplog
):
    approved = _frozen()
    monkeypatch.setattr(approval_gate, "is_workflow_approval_required", lambda: True)
    monkeypatch.setattr(
        approval_store,
        "approval_state",
        lambda plan_id: (
            approval_store.APPROVED if plan_id == approved["plan_id"] else approval_store.ABSENT
        ),
    )
    approval_gate.ensure_plan_approved(tier="script", manifest_json=json.dumps(approved))
    # Sparse allocation exercises the real unchanged limit without allocating
    # or hashing a large payload. A partial snapshot must never become a plan.
    with (manifest_repository / "over-budget.bin").open("wb") as handle:
        handle.truncate(git_baseline._UNTRACKED_HASH_BUDGET_BYTES + 1)
    baseline = git_baseline.derive_baseline(str(manifest_repository))
    assert baseline["available"] is False
    assert baseline["worktree_state"] == {"status": "unavailable"}
    assert baseline["commit"] == approved["repo_baseline"]["commit"]
    manifest = manifest_freeze.build_manifest_json(source_hash="abc123", inputs={"k": "v"})
    assert manifest is None
    assert "unavailable repository baseline" in caplog.text
    with pytest.raises(approval_gate.PlanApprovalUnavailableError) as error:
        approval_gate.ensure_plan_approved(tier="script", manifest_json=manifest)
    assert error.value.plan_id is None


def test_an_unavailable_worktree_snapshot_has_no_approvable_plan_id(monkeypatch):
    monkeypatch.setattr(
        manifest_freeze,
        "derive_baseline",
        lambda _cwd: {
            "available": False,
            "commit": "deadbeef",
            "worktree_state": {"status": "unavailable"},
        },
    )

    assert manifest_freeze.build_manifest_json(source_hash="abc123", inputs={"k": "v"}) is None


def test_an_unavailable_snapshot_reaches_the_gate_without_a_plan_id(monkeypatch):
    available = {
        "available": True,
        "commit": "deadbeef",
        "worktree_state": {"status": "dirty", "digest": "sha256:first"},
    }
    unavailable = {
        "available": False,
        "commit": "deadbeef",
        "worktree_state": {"status": "unavailable"},
    }
    baselines = iter((available, unavailable))
    monkeypatch.setattr(manifest_freeze, "derive_baseline", lambda _cwd: next(baselines))
    monkeypatch.setattr(approval_gate, "is_workflow_approval_required", lambda: True)

    approved_manifest = manifest_freeze.build_manifest_json(source_hash="abc123", inputs={"k": "v"})
    assert approved_manifest is not None
    approved_plan_id = json.loads(approved_manifest)["plan_id"]
    monkeypatch.setattr(
        approval_store,
        "approval_state",
        lambda plan_id: (
            approval_store.APPROVED if plan_id == approved_plan_id else approval_store.ABSENT
        ),
    )
    approval_gate.ensure_plan_approved(tier="script", manifest_json=approved_manifest)

    unavailable_manifest = manifest_freeze.build_manifest_json(
        source_hash="abc123", inputs={"k": "v"}
    )
    with pytest.raises(approval_gate.PlanApprovalUnavailableError) as excinfo:
        approval_gate.ensure_plan_approved(tier="script", manifest_json=unavailable_manifest)

    assert excinfo.value.plan_id is None


def test_an_available_baseline_is_part_of_the_identity_and_unavailable_baselines_are_not(
    monkeypatch,
):
    """Two available commits are distinct plans; an unavailable snapshot has no plan."""
    monkeypatch.setattr(
        manifest_freeze,
        "derive_baseline",
        lambda _cwd: {
            "available": True,
            "commit": "first",
            "worktree_state": {"status": "clean"},
        },
    )
    a = _frozen()["plan_id"]
    monkeypatch.setattr(
        manifest_freeze,
        "derive_baseline",
        lambda _cwd: {
            "available": True,
            "commit": "second",
            "worktree_state": {"status": "clean"},
        },
    )
    b = _frozen()["plan_id"]
    assert a != b
    monkeypatch.setattr(
        manifest_freeze,
        "derive_baseline",
        lambda _cwd: {
            "available": False,
            "commit": "second",
            "worktree_state": {"status": "unavailable"},
        },
    )
    assert manifest_freeze.build_manifest_json(source_hash="abc123", inputs={"k": "v"}) is None
