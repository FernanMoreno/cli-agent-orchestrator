"""Cross-boundary regressions for the integration's execution guarantees."""

from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cli_agent_orchestrator import constants
from cli_agent_orchestrator.clients.work_repository import WorkRepository
from cli_agent_orchestrator.models.terminal import AgentStepResult, TerminalStatus
from cli_agent_orchestrator.models.workflow import WorkflowSpec, WorkflowStep
from cli_agent_orchestrator.providers import manager as pm
from cli_agent_orchestrator.providers.codex import CodexProvider
from cli_agent_orchestrator.services import agent_step as step
from cli_agent_orchestrator.services import terminal_service as terminal
from cli_agent_orchestrator.services import work_terminal, workflow_journal, workflow_service


def test_first_verification_schedule_has_a_deadline_when_diagnostic_store_is_down():
    provider = CodexProvider("008-start", "audit", "audit")
    provider.prepare_input("task")
    try:
        with (
            patch.object(
                terminal,
                "mark_terminal_turn_verification_started",
                side_effect=RuntimeError("store unavailable"),
            ),
            patch.object(terminal.threading, "Timer") as timer,
            patch.object(terminal.threading, "Thread") as worker,
        ):
            assert terminal.schedule_receipt_result_verification("008-start", provider)
            assert timer.call_count == 1
            assert timer.call_args.args[0] == 55.0
            timer.return_value.start.assert_called_once()
            worker.return_value.start.assert_called_once()
    finally:
        terminal._receipt_verification_state.pop("008-start", None)
        terminal._receipt_verification_inflight.discard("008-start")


@pytest.fixture
def isolated_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "DATABASE_FILE", tmp_path / "workflow.db")
    workflow_service.run_registry.clear()
    workflow_service._active_drives.clear()
    workflow_service.step_output_store._store.clear()
    workflow_journal._connect().close()
    WorkRepository(constants.DATABASE_FILE).initialize()
    yield
    workflow_service.run_registry.clear()
    workflow_service._active_drives.clear()
    workflow_service.step_output_store._store.clear()


@pytest.mark.asyncio
async def test_real_post_delivery_timeout_cannot_start_another_workflow_worker(isolated_workflow):
    with (
        patch.object(step.status_monitor, "get_status", return_value=TerminalStatus.PROCESSING),
        patch.object(step.terminal_service, "probe_post_turn_receipt_result", return_value=None),
    ):
        with pytest.raises(step.StepExecutionError) as caught:
            await step._wait_for_completion("aaaa1111", 0, prompt_redelivery=False)
    timeout = caught.value
    deliveries = []

    async def worker(**kwargs):
        fields = step._effective_step_fields(
            provider=kwargs["provider"],
            agent=kwargs["agent"],
            allowed_tools=None,
            engine=kwargs["engine"],
            model=None,
            working_directory=None,
            use_worktree=False,
            created_here=True,
            timeout=kwargs["timeout"],
            ready_timeout=step.DEFAULT_READY_TIMEOUT,
            teardown=True,
            prompt_redelivery=True,
        )
        kwargs["pre_delivery_recorder"].record_pre_delivery("v2:008-timeout", fields)
        deliveries.append(kwargs["prompt"])
        if len(deliveries) == 1:
            raise timeout
        return AgentStepResult(
            terminal_id="bbbb2222", last_message="duplicate", status=TerminalStatus.COMPLETED
        )

    spec = WorkflowSpec(
        name="timeout",
        mode="sequential",
        steps=[
            WorkflowStep(id="one", provider="claude_code", agent="dev", prompt="effect", retries=1)
        ],
    )
    with patch.object(workflow_service, "run_agent_step", AsyncMock(side_effect=worker)):
        result = await workflow_service.start_run(spec, {}, "008-timeout")
    assert deliveries == ["effect"]
    assert timeout.delivery_may_have_occurred is True
    assert result.state.value == "failed"
    assert result.steps[0].attempts == 1


def test_failed_provider_restoration_is_not_published_and_next_read_restores_receipt():
    manager = pm.ProviderManager()
    metadata = dict(provider="codex", tmux_session="audit", tmux_window="audit", agent_profile=None)
    proof = CodexProvider("audit", "audit", "audit")
    proof.prepare_input("synthetic task")
    receipt = proof.pending_turn_receipt_state()
    receipt["phase"] = "sent"
    with (
        patch.object(pm, "registered_provider_descriptor", return_value=None),
        patch.object(pm, "get_terminal_metadata", return_value=metadata),
        patch.object(
            pm,
            "get_terminal_turn_receipt",
            side_effect=[RuntimeError("store unavailable"), receipt],
        ) as read,
        patch.object(pm, "get_terminal_turn_recovery", return_value=None),
    ):
        with pytest.raises(RuntimeError):
            manager.get_provider("audit")
        assert "audit" not in manager._providers
        restored = manager.get_provider("audit")
    assert read.call_count == 2
    assert restored.pending_turn_receipt_state()["generation"] == receipt["generation"]
    assert restored.blocks_new_task_input_for_reconciliation


@pytest.mark.parametrize("phase", ["unknown", "sent", "result_verified"])
def test_corrupt_receipt_cannot_publish_an_unfenced_provider(phase):
    manager = pm.ProviderManager()
    metadata = dict(provider="codex", tmux_session="audit", tmux_window="audit", agent_profile=None)
    with (
        patch.object(pm, "registered_provider_descriptor", return_value=None),
        patch.object(pm, "get_terminal_metadata", return_value=metadata),
        patch.object(
            pm,
            "get_terminal_turn_receipt",
            return_value={
                "phase": phase,
                "generation": "1" * 32,
                "receipt_sha256": "invalid",
                "result_sha256": "invalid",
            },
        ),
        patch.object(pm, "get_terminal_turn_recovery", return_value=None),
    ):
        with pytest.raises(ValueError):
            manager.get_provider("audit")
    assert "audit" not in manager._providers


def test_durable_active_turn_with_missing_provider_receipt_never_returns_history():
    provider = CodexProvider("audit", "audit", "audit")
    manager = MagicMock()
    manager.get_provider.return_value = provider
    backend = MagicMock()
    backend.get_history.return_value = "old answer"
    with (
        patch.object(
            terminal,
            "get_terminal_metadata",
            return_value={"tmux_session": "audit", "tmux_window": "audit"},
        ),
        patch.object(
            terminal,
            "get_terminal_turn_recovery",
            return_value={"generation": "1" * 32, "state": "pending"},
        ),
        patch.object(terminal, "provider_manager", manager),
        patch.object(terminal, "get_backend", return_value=backend),
        patch.object(terminal.status_monitor, "get_buffer", return_value="old answer"),
        patch.object(provider, "extract_last_message_from_script", return_value="old answer"),
        patch.object(work_terminal, "terminal_dispatch_lock", return_value=nullcontext()),
    ):
        with pytest.raises(terminal.TurnResultUnavailableError):
            terminal.get_output("audit", terminal.OutputMode.LAST)


@pytest.mark.parametrize("deadline", [False, True])
def test_capture_and_deadline_budgets_survive_diagnostic_store_outage(deadline):
    tid, fingerprint = "008-budget", ("1" * 32, "a" * 64)
    terminal._receipt_verification_state[tid] = terminal._ReceiptVerificationState(fingerprint)
    try:
        with (
            patch.object(
                terminal,
                "record_terminal_turn_verification_failure",
                side_effect=RuntimeError("store unavailable"),
            ),
            patch.object(terminal, "transition_native_child"),
            patch.object(terminal.status_monitor, "publish_receipt_reconciliation"),
        ):
            if deadline:
                terminal._expire_receipt_verification(tid, fingerprint)
            else:
                terminal._record_receipt_verification_failure(tid, fingerprint, reason="no answer")
                state = terminal._receipt_verification_state[tid]
                assert state.attempts == 1
                assert state.next_retry_at > terminal.time.monotonic()
                for _ in range(2):
                    terminal._record_receipt_verification_failure(
                        tid, fingerprint, reason="no answer"
                    )
            state = terminal._receipt_verification_state[tid]
            assert state.attempts == 3
            assert state.reconciled
            assert state.next_retry_at == float("inf")
    finally:
        terminal._receipt_verification_state.pop(tid, None)


@pytest.mark.asyncio
async def test_blocking_drive_cannot_leave_running_without_owner_after_substrate_exception(
    isolated_workflow,
):
    spec = WorkflowSpec(
        name="extract",
        mode="sequential",
        steps=[
            WorkflowStep(id="one", provider="claude_code", agent="dev", prompt="task", retries=1)
        ],
    )
    with patch.object(
        workflow_service, "run_agent_step", AsyncMock(side_effect=ValueError("extractor failed"))
    ):
        try:
            await workflow_service.start_run(spec, {}, "008-extract")
        except ValueError:
            pass
    assert workflow_journal.get_run("008-extract").state != "running"
    assert workflow_journal.get_steps("008-extract")[0].state != "running"
    assert "008-extract" not in workflow_service._active_drives


@pytest.mark.asyncio
async def test_failed_managed_recovery_preserves_original_pending_work(isolated_workflow):
    spec = WorkflowSpec(
        name="managed",
        mode="sequential",
        steps=[WorkflowStep(id="one", provider="claude_code", agent="dev", prompt="task")],
    )
    admit = AsyncMock(return_value={"id": "work", "attempts": [{"id": "attempt", "generation": 1}]})
    await workflow_service.start_run(spec, {}, "008-managed", managed_step_admitters={"one": admit})
    workflow_service.run_registry.pop("008-managed")
    admit.side_effect = RuntimeError("store temporarily unavailable")
    with pytest.raises(workflow_service.ResumeNotAllowedError):
        await workflow_service.resume_from_last_completed(
            "008-managed", managed_step_admitters={"one": admit}
        )
    assert workflow_journal.get_run("008-managed").state == "running"
    saved = workflow_journal.get_steps("008-managed")[0]
    assert saved.state == "work_pending"
    assert saved.attempts == 1


def test_concurrent_provider_readers_wait_for_complete_receipt_restoration():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    manager = pm.ProviderManager()
    metadata = dict(provider="codex", tmux_session="audit", tmux_window="audit", agent_profile=None)
    proof = CodexProvider("audit", "audit", "audit")
    proof.prepare_input("synthetic task")
    receipt = proof.pending_turn_receipt_state()
    receipt["phase"] = "sent"
    restoring, release_restoration, second_entered, second_finished = (
        Event(),
        Event(),
        Event(),
        Event(),
    )

    def read_receipt(_terminal_id):
        restoring.set()
        assert release_restoration.wait(5), "test did not release restoration"
        return receipt

    def second_reader():
        second_entered.set()
        provider = manager.get_provider("audit")
        second_finished.set()
        return provider

    with (
        patch.object(pm, "registered_provider_descriptor", return_value=None),
        patch.object(pm, "get_terminal_metadata", return_value=metadata),
        patch.object(pm, "get_terminal_turn_receipt", side_effect=read_receipt) as read,
        patch.object(pm, "get_terminal_turn_recovery", return_value=None),
        ThreadPoolExecutor(max_workers=2) as readers,
    ):
        first = readers.submit(manager.get_provider, "audit")
        try:
            assert restoring.wait(5)
            second = readers.submit(second_reader)
            assert second_entered.wait(5)
            assert "audit" not in manager._providers
            assert not second_finished.wait(0.1)
        finally:
            release_restoration.set()
        restored = first.result(timeout=5)
        assert second.result(timeout=5) is restored
        assert restored.pending_turn_receipt_state()["generation"] == receipt["generation"]
        assert restored.blocks_new_task_input_for_reconciliation
        assert read.call_count == 1
