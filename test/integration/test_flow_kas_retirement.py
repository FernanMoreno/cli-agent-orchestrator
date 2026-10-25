"""Flow owns exact KAS stop evidence and its private proof pair through SQL."""

from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.backends.base import TerminalCleanupOutcome, TerminalCleanupResult
from cli_agent_orchestrator.clients import database as db
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.providers.manager import ProviderManager
from cli_agent_orchestrator.services import flow_service as flow
from cli_agent_orchestrator.services import kiro_profiles as kas
from cli_agent_orchestrator.services import remote_terminal_service, terminal_service

KEY = "1234abcd"
SESSION = "cao-flow-kas-retirement"
WINDOW = "kas-worker"


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    assert db.engine.dialect.name == "postgresql"
    profile = AgentProfile(name="review", description="Review", engine="kas", allowedTools=[])
    monkeypatch.setattr(kas, "KIRO_AGENTS_DIR", tmp_path)
    monkeypatch.setattr(terminal_service, "TERMINAL_LOG_DIR", tmp_path / "logs")
    grant = kas.install_runtime_kas_profile(KEY, profile)
    proofs = [grant, kas._runtime_kas_source_path(tmp_path, KEY)]
    db.create_terminal(
        KEY,
        SESSION,
        WINDOW,
        "kiro_cli",
        "review",
        engine="kas",
        session_incarnation_id="flow-kas-incarnation",
        new_session_incarnation=True,
    )
    assert terminal_service._publish_deferred_failure_fallback(KEY, {"kind": "controlled"})
    assert terminal_service._publish_deferred_init_complete_fallback(KEY)
    proofs += [
        terminal_service._deferred_failure_fallback_path(KEY),
        terminal_service._deferred_init_complete_fallback_path(KEY),
    ]
    # Controlled adapter: exercise real manager leases/cache without launching Kiro.
    manager = ProviderManager()
    adapter = manager.create_provider("mock_cli", KEY, SESSION, WINDOW)
    adapter.cleanup = Mock(return_value=True)
    monkeypatch.setattr(flow, "provider_manager", manager)
    monkeypatch.setattr(remote_terminal_service, "placement", lambda _key: None)
    monkeypatch.setattr(remote_terminal_service, "session_terminals", lambda _name: None)
    backend = Mock()
    backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
        TerminalCleanupOutcome.ABSENT
    )
    monkeypatch.setattr(flow, "get_backend", lambda: backend)
    monkeypatch.setattr(flow, "_is_terminal_busy", lambda _key: False)
    monkeypatch.setattr(flow, "fifo_manager", Mock())
    monkeypatch.setattr(flow, "status_monitor", Mock())
    monkeypatch.setattr(flow, "should_retain_deferred_failure_tombstone", lambda *_a: False)
    return backend, manager, adapter, proofs


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("outcome", [TerminalCleanupOutcome.ABSENT, TerminalCleanupOutcome.DELETED])
def test_flow_removes_only_its_kas_pair_after_acknowledged_row_delete(
    runtime, monkeypatch, live, outcome
):
    backend, manager, adapter, proofs = runtime
    backend.session_exists.return_value = live
    backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(outcome)
    before = [p.read_bytes() for p in proofs]
    other_profile = AgentProfile(name="other", description="Other", engine="kas", allowedTools=[])
    other = kas.install_runtime_kas_profile("5678efab", other_profile)
    other_pair = [other, kas._runtime_kas_source_path(other.parent, "5678efab")]
    other_bytes = [p.read_bytes() for p in other_pair]
    calls = []

    def delete(ids):
        assert ids == [KEY]
        assert [p.read_bytes() for p in proofs] == before
        result = db.delete_terminals_by_ids(ids)
        calls.append(result)
        return result

    monkeypatch.setattr(flow, "delete_terminals_by_ids", delete)
    assert flow._recycle_flow_session("kas-flow", SESSION) is True
    assert calls == [1]
    assert db.get_terminal_metadata(KEY) is None
    assert KEY not in manager._providers
    adapter.cleanup.assert_called_once_with()
    assert not any(p.exists() for p in proofs)
    assert [p.read_bytes() for p in other_pair] == other_bytes
    backend.cleanup_terminal_exact.assert_called_once_with(KEY, SESSION, WINDOW, close=False)


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("proof", ["unknown", "present", "exception"])
def test_unproven_kas_stop_preserves_row_cache_and_private_evidence(
    runtime, monkeypatch, live, proof
):
    backend, manager, adapter, proofs = runtime
    backend.session_exists.return_value = live
    if proof == "exception":
        backend.cleanup_terminal_exact.side_effect = RuntimeError("controlled unavailable identity")
    else:
        backend.cleanup_terminal_exact.return_value = TerminalCleanupResult(
            TerminalCleanupOutcome.UNKNOWN
            if proof == "unknown"
            else TerminalCleanupOutcome.STILL_PRESENT
        )
    before = [p.read_bytes() for p in proofs]
    delete = Mock(side_effect=db.delete_terminals_by_ids)
    monkeypatch.setattr(flow, "delete_terminals_by_ids", delete)
    assert flow._recycle_flow_session("kas-flow", SESSION) is False
    delete.assert_not_called()
    adapter.cleanup.assert_not_called()
    assert db.get_terminal_metadata(KEY) is not None
    assert manager._providers[KEY] is adapter
    assert [p.read_bytes() for p in proofs] == before


@pytest.mark.parametrize("live", [False, True])
def test_sql_exception_preserves_kas_proofs_and_original_error(runtime, monkeypatch, live):
    backend, manager, adapter, proofs = runtime
    backend.session_exists.return_value = live
    failure = RuntimeError("controlled uncertain SQL outcome")
    monkeypatch.setattr(flow, "delete_terminals_by_ids", Mock(side_effect=failure))
    before = [p.read_bytes() for p in proofs]
    with pytest.raises(RuntimeError) as raised:
        flow._recycle_flow_session("kas-flow", SESSION)
    assert raised.value is failure
    assert db.get_terminal_metadata(KEY) is not None
    assert [p.read_bytes() for p in proofs] == before


@pytest.mark.parametrize("live", [False, True])
def test_provider_cleanup_refusal_retains_kas_proofs(runtime, live):
    backend, manager, adapter, proofs = runtime
    backend.session_exists.return_value = live
    adapter.cleanup.return_value = False
    before = [p.read_bytes() for p in proofs]
    assert flow._recycle_flow_session("kas-flow", SESSION) is False
    assert db.get_terminal_metadata(KEY) is not None
    assert manager._providers[KEY] is adapter
    assert [p.read_bytes() for p in proofs] == before


def test_missing_session_retains_excluded_tombstone_and_all_evidence(runtime, monkeypatch):
    backend, manager, adapter, proofs = runtime
    backend.session_exists.return_value = False
    monkeypatch.setattr(flow, "should_retain_deferred_failure_tombstone", lambda *_a: True)
    before = [p.read_bytes() for p in proofs]
    assert flow._recycle_flow_session("kas-flow", SESSION) is True
    backend.cleanup_terminal_exact.assert_not_called()
    adapter.cleanup.assert_not_called()
    assert db.get_terminal_metadata(KEY) is not None
    assert manager._providers[KEY] is adapter
    assert [p.read_bytes() for p in proofs] == before


def test_missing_session_mixed_selection_preserves_retained_and_non_kas_proofs(
    runtime, monkeypatch
):
    backend, manager, adapter, proofs = runtime
    backend.session_exists.return_value = False
    retained, ordinary = "abcd1234", "8765abcd"
    foreign_paths = []
    for key, engine in [(retained, "kas"), (ordinary, None)]:
        db.create_terminal(
            key,
            SESSION,
            key,
            "mock_cli",
            "review",
            engine=engine,
            session_incarnation_id="flow-kas-incarnation",
        )
        profile = AgentProfile(name="other", description="Other", engine="kas", allowedTools=[])
        grant = kas.install_runtime_kas_profile(key, profile)
        foreign_paths += [grant, kas._runtime_kas_source_path(grant.parent, key)]
    monkeypatch.setattr(
        flow, "should_retain_deferred_failure_tombstone", lambda key, _row: key == retained
    )
    selected = [row["id"] for row in db.list_terminals_by_session(SESSION) if row["id"] != retained]
    before = [p.read_bytes() for p in foreign_paths]
    delete = Mock(side_effect=db.delete_terminals_by_ids)
    monkeypatch.setattr(flow, "delete_terminals_by_ids", delete)
    assert flow._recycle_flow_session("kas-flow", SESSION) is True
    delete.assert_called_once_with(selected)
    backend.cleanup_terminal_exact.assert_called_once_with(KEY, SESSION, WINDOW, close=False)
    assert db.get_terminal_metadata(KEY) is None
    assert db.get_terminal_metadata(ordinary) is None
    assert db.get_terminal_metadata(retained) is not None
    assert not any(p.exists() for p in proofs)
    assert [p.read_bytes() for p in foreign_paths] == before


@pytest.mark.parametrize("live", [False, True])
def test_mixed_batch_waits_for_all_kas_stop_proofs_before_any_provider_cleanup(
    runtime, monkeypatch, live
):
    backend, manager, adapter, proofs = runtime
    backend.session_exists.return_value = live
    for key, engine in [("abcd1234", "kas"), ("8765abcd", None)]:
        db.create_terminal(
            key,
            SESSION,
            key,
            "mock_cli",
            "review",
            engine=engine,
            session_incarnation_id="flow-kas-incarnation",
        )
    original = db.list_terminals_by_session(SESSION)
    # Pin a deliberate batch order; filtering/incarnation selection is covered separately.
    rows = sorted(original, key=lambda row: (row["id"] != KEY, row["id"]))
    monkeypatch.setattr(flow, "list_current_session_terminals", lambda *_a, **_k: rows)
    backend.cleanup_terminal_exact.side_effect = [
        TerminalCleanupResult(TerminalCleanupOutcome.ABSENT),
        TerminalCleanupResult(TerminalCleanupOutcome.UNKNOWN),
    ]
    before = [p.read_bytes() for p in proofs]
    delete = Mock(side_effect=db.delete_terminals_by_ids)
    monkeypatch.setattr(flow, "delete_terminals_by_ids", delete)
    assert flow._recycle_flow_session("kas-flow", SESSION) is False
    assert db.list_terminals_by_session(SESSION) == original
    assert backend.cleanup_terminal_exact.call_count == 2
    adapter.cleanup.assert_not_called()
    delete.assert_not_called()
    assert [p.read_bytes() for p in proofs] == before


@pytest.mark.parametrize("live", [False, True])
def test_non_kas_row_keeps_foreign_proof_files_and_receives_no_exact_probe(runtime, live):
    backend, manager, adapter, proofs = runtime
    backend.session_exists.return_value = live
    key = "8765abcd"
    profile = AgentProfile(name="other", description="Other", engine="kas", allowedTools=[])
    grant = kas.install_runtime_kas_profile(key, profile)
    other_pair = [grant, kas._runtime_kas_source_path(grant.parent, key)]
    before = [p.read_bytes() for p in other_pair]
    db.create_terminal(
        key, SESSION, key, "mock_cli", "review", session_incarnation_id="flow-kas-incarnation"
    )
    other_adapter = manager.create_provider("mock_cli", key, SESSION, key)
    other_adapter.cleanup = Mock(return_value=True)
    assert flow._recycle_flow_session("kas-flow", SESSION) is True
    assert db.get_terminal_metadata(key) is None
    other_adapter.cleanup.assert_called_once_with()
    backend.cleanup_terminal_exact.assert_called_once_with(KEY, SESSION, WINDOW, close=False)
    assert [p.read_bytes() for p in other_pair] == before


@pytest.mark.parametrize("live", [False, True])
def test_provider_fence_refusal_keeps_row_and_private_evidence(runtime, monkeypatch, live):
    from contextlib import nullcontext

    backend, manager, adapter, proofs = runtime
    backend.session_exists.return_value = live
    monkeypatch.setattr(manager, "retirement_fence", lambda _key: nullcontext(False))
    before = [p.read_bytes() for p in proofs]
    assert flow._recycle_flow_session("kas-flow", SESSION) is False
    adapter.cleanup.assert_called_once_with()
    assert db.get_terminal_metadata(KEY) is not None
    assert [p.read_bytes() for p in proofs] == before


@pytest.mark.parametrize("live", [False, True])
def test_commit_then_error_retains_evidence_without_claiming_row_survives(
    runtime, monkeypatch, live
):
    backend, manager, adapter, proofs = runtime
    backend.session_exists.return_value = live
    failure = RuntimeError("controlled result lost after commit")

    def delete(ids):
        db.delete_terminals_by_ids(ids)
        raise failure

    monkeypatch.setattr(flow, "delete_terminals_by_ids", delete)
    before = [p.read_bytes() for p in proofs]
    with pytest.raises(RuntimeError) as raised:
        flow._recycle_flow_session("kas-flow", SESSION)
    assert raised.value is failure
    assert db.get_terminal_metadata(KEY) is None
    assert [p.read_bytes() for p in proofs] == before


def test_busy_live_flow_never_enters_kas_retirement(runtime, monkeypatch):
    backend, manager, adapter, proofs = runtime
    backend.session_exists.return_value = True
    monkeypatch.setattr(flow, "_is_terminal_busy", lambda _key: True)
    before = [p.read_bytes() for p in proofs]
    assert flow._recycle_flow_session("kas-flow", SESSION) is False
    backend.kill_session.assert_not_called()
    backend.cleanup_terminal_exact.assert_not_called()
    adapter.cleanup.assert_not_called()
    assert [p.read_bytes() for p in proofs] == before


@pytest.mark.parametrize("live", [False, True])
def test_active_startup_refuses_flow_before_native_or_private_effects(runtime, live):
    from cli_agent_orchestrator.services.provider_lifecycle import finish_startup, reserve_startup

    backend, manager, adapter, proofs = runtime
    backend.session_exists.return_value = live
    owner = reserve_startup(KEY)
    before = [p.read_bytes() for p in proofs]
    try:
        assert flow._recycle_flow_session("kas-flow", SESSION) is False
    finally:
        finish_startup(owner)
    backend.kill_session.assert_not_called()
    backend.cleanup_terminal_exact.assert_not_called()
    adapter.cleanup.assert_not_called()
    assert [p.read_bytes() for p in proofs] == before


@pytest.mark.parametrize("live", [False, True])
def test_proof_unlink_error_keeps_existing_best_effort_cleanup_contract(
    runtime, monkeypatch, live, caplog
):
    from pathlib import Path

    backend, manager, adapter, proofs = runtime
    backend.session_exists.return_value = live
    grant, source = proofs[:2]
    unlink = Path.unlink

    def fail_grant(path, *args, **kwargs):
        if path == grant:
            raise OSError("controlled proof unlink unavailable")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_grant)
    assert flow._recycle_flow_session("kas-flow", SESSION) is True
    assert db.get_terminal_metadata(KEY) is None
    assert grant.exists() and not source.exists()
    assert not any(p.exists() for p in proofs[2:])
    assert "KAS runtime private proof cleanup deferred" in caplog.text
