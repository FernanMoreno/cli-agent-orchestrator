"""Required interaction waits compose with agent-step and eager inbox delivery."""

from __future__ import annotations

import asyncio
import threading
import time
from functools import partial
from test.integration.test_interaction_explicit_flow import (  # noqa: F401
    _plain,
    interaction_http_context,
)

import pytest

pytestmark = pytest.mark.postgres


async def _await_wait_started_signal(wait_started, waiting, render_trace):
    try:
        await asyncio.wait_for(wait_started.wait(), timeout=1.0)
    except asyncio.TimeoutError as wait_timeout:
        task_error = None
        if waiting.cancelled():
            task_state = "cancelled"
        elif waiting.done():
            task_error = waiting.exception()
            task_state = (
                f"raised:{type(task_error).__module__}.{type(task_error).__qualname__}"
                if task_error is not None
                else "returned"
            )
        else:
            task_state = "pending"
        wait_timeout.args = (
            f"required-wait signal missed unchanged 1.0 s deadline; "
            f"task={task_state}; trace={render_trace()}",
        )
        if task_error is not None:
            raise wait_timeout from task_error
        raise


def _register_sibling_source(context, terminal_id="ac010002"):
    from cli_agent_orchestrator.clients import database

    session_name = "interaction-independent-sibling"
    incarnation_id = "interaction-independent-sibling-incarnation"
    database.create_terminal(
        terminal_id,
        session_name,
        "worker",
        "codex",
        allowed_tools=["@cao-mcp-server"],
        session_incarnation_id=incarnation_id,
        new_session_incarnation=True,
    )
    context.service.register_source(
        terminal_id=terminal_id,
        incarnation_id=incarnation_id,
        session_name=session_name,
        provider="codex",
        principal=context.owner,
    )
    return terminal_id, incarnation_id


def test_agent_step_waits_for_required_answer_even_when_agent_omits_wait_tool(
    interaction_http_context, monkeypatch
):
    from uuid import UUID

    from cli_agent_orchestrator.mcp_server import server as mcp_server
    from cli_agent_orchestrator.models.terminal import TerminalStatus
    from cli_agent_orchestrator.services import agent_step, terminal_service
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        InteractionAnswerPayload,
        QuestionInput,
    )

    context = interaction_http_context
    ask_result = _plain(
        asyncio.run(
            mcp_server.cao_ask_user(
                operation_key="wait-omission-required-operation-0001",
                questions=[
                    mcp_server._InteractionQuestionInput(
                        client_key="required-choice",
                        kind="text",
                        prompt="Which behavior should the same task use?",
                        required=True,
                    )
                ],
                wait_for_required=False,
            )
        )
    )
    group_id = ask_result["group_id"]
    question_id = ask_result["questions"][0]["question_id"]
    initial_source_status = context.service.source_status(
        context.terminal_id, context.incarnation_id
    )
    required_deadline = initial_source_status.deadline
    assert required_deadline is not None
    answer_accepted = False

    # The agent intentionally does not call cao_wait_for_user_answers. The
    # task completion observer still sees its persisted dependency edge.
    original_probe = terminal_service.probe_post_turn_receipt_result
    probe_calls: list[str] = []

    def probe(_terminal_id):
        probe_calls.append(_terminal_id)
        return "verified result after answer" if answer_accepted else None

    monkeypatch.setattr(terminal_service, "probe_post_turn_receipt_result", probe)
    monkeypatch.setattr(
        terminal_service,
        "settle_post_turn_receipt_result",
        lambda _terminal_id, result: result,
    )
    redelivery_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        terminal_service,
        "send_input",
        lambda *args, **kwargs: redelivery_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        terminal_service.status_monitor,
        "get_status",
        lambda _terminal_id: (
            TerminalStatus.WAITING_USER_ANSWER if not answer_accepted else TerminalStatus.COMPLETED
        ),
    )
    monkeypatch.setattr(
        terminal_service, "_interaction_service", lambda: context.service, raising=False
    )
    monkeypatch.setattr(agent_step, "_interaction_service", lambda: context.service, raising=False)
    monkeypatch.setattr(agent_step, "_COMPLETION_POLL_INTERVAL", 0.01)

    async def exercise_same_step():
        wait_started = asyncio.Event()
        trace_started_ns = time.monotonic_ns()
        trace_rows = []
        trace_lock = threading.Lock()
        recording_trace = True

        def trace(boundary, outcome):
            with trace_lock:
                if recording_trace and len(trace_rows) < 32:
                    trace_rows.append((time.monotonic_ns() - trace_started_ns, boundary, outcome))

        def render_trace():
            with trace_lock:
                rows = tuple(trace_rows)
            return ";".join(
                f"{elapsed}:{boundary}:{outcome}" for elapsed, boundary, outcome in rows
            )

        async def trace_async(boundary, operation, *args, **kwargs):
            started_ns = time.monotonic_ns()
            trace(boundary, "enter")
            try:
                result = await operation(*args, **kwargs)
            except BaseException as error:
                trace(
                    boundary,
                    f"raise:{type(error).__module__}.{type(error).__qualname__}:"
                    f"{time.monotonic_ns() - started_ns}ns",
                )
                raise
            trace(boundary, f"return:{time.monotonic_ns() - started_ns}ns")
            return result

        def trace_sync(boundary, operation, *args, **kwargs):
            started_ns = time.monotonic_ns()
            trace(boundary, "enter")
            try:
                result = operation(*args, **kwargs)
            except BaseException as error:
                trace(
                    boundary,
                    f"raise:{type(error).__module__}.{type(error).__qualname__}:"
                    f"{time.monotonic_ns() - started_ns}ns",
                )
                raise
            trace(boundary, f"return:{time.monotonic_ns() - started_ns}ns")
            return result

        original_snapshot = agent_step._interaction_wait_snapshot
        monkeypatch.setattr(
            agent_step,
            "_interaction_wait_snapshot",
            partial(trace_async, "snapshot", original_snapshot),
        )

        original_native_pause = agent_step._pause_timeout_for_native_wait
        monkeypatch.setattr(
            agent_step,
            "_pause_timeout_for_native_wait",
            partial(trace_async, "native_pause", original_native_pause),
        )

        original_status = terminal_service.status_monitor.get_status
        monkeypatch.setattr(
            terminal_service.status_monitor,
            "get_status",
            partial(trace_sync, "status", original_status),
        )

        original_wait = context.service.wait_for_required

        async def signal_live_wait(
            terminal_id,
            incarnation_id,
            *,
            timeout_seconds,
            expected_generation=None,
        ):
            nonlocal recording_trace
            if timeout_seconds > 0:
                trace("required_wait_positive", "enter")
                wait_started.set()
                recording_trace = False
                return await original_wait(
                    terminal_id,
                    incarnation_id,
                    timeout_seconds=timeout_seconds,
                    expected_generation=expected_generation,
                )
            return await trace_async(
                "required_wait_zero",
                original_wait,
                terminal_id,
                incarnation_id,
                timeout_seconds=timeout_seconds,
                expected_generation=expected_generation,
            )

        monkeypatch.setattr(context.service, "wait_for_required", signal_live_wait)
        waiting = asyncio.create_task(
            agent_step._wait_for_completion(
                context.terminal_id,
                timeout=2.0,
                prompt="the already-delivered task",
            )
        )
        await _await_wait_started_signal(wait_started, waiting, render_trace)
        # Keep the question pending beyond the active task budget. This proves
        # the required interaction suspends active time even when acceptance
        # and the MCP pull happen later; the larger post-release budget leaves
        # room for real PostgreSQL status/probe/receipt work.
        await asyncio.sleep(2.1)
        assert not waiting.done(), "required answer must keep the dependent task from completing"
        assert probe_calls, "the same completion wait should observe the live task"
        accepted = context.service.accept_answer(
            context.owner,
            question_id,
            1,
            InteractionAnswerPayload(kind="text", text="Use the stable behavior."),
            "wait-omission-answer-operation-0001",
        )
        assert _plain(accepted)["delivery_state"] == "accepted"
        nonlocal answer_accepted
        answer_accepted = True

        # Acceptance records the answer, but does not show that this source
        # model has read it. Keep the source's required edge until its own MCP
        # wait invocation pulls the returned answer.
        assert context.service.has_required_wait(context.terminal_id, context.incarnation_id)
        accepted_source_status = context.service.source_status(
            context.terminal_id,
            context.incarnation_id,
        )
        assert accepted_source_status.deadline == required_deadline
        accepted_graph = context.service.wait_graph(context.owner)
        assert any(
            edge["from"] == f"task:{context.terminal_id}"
            and edge["to"] == f"question_group:{group_id}"
            and edge["required"] is True
            for edge in accepted_graph["edges"]
        )
        accepted_wait = await context.service.wait_for_required(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=0,
        )
        assert _plain(accepted_wait)["state"] == "waiting"
        await asyncio.sleep(0.1)
        assert (
            not waiting.done()
        ), "accepted answer must not release the task before the same source reads it"

        pulled = _plain(
            await mcp_server.cao_wait_for_user_answers(
                group_id=UUID(group_id),
                wait_seconds=0,
            )
        )
        assert pulled["state"] == "ready"
        assert len(pulled["answers"]) == 1
        assert pulled["answers"][0]["question_id"] == question_id
        assert pulled["answers"][0]["response"]["text"] == "Use the stable behavior."
        assert (
            context.service.has_required_wait(context.terminal_id, context.incarnation_id) is False
        )
        returned_source_status = context.service.source_status(
            context.terminal_id,
            context.incarnation_id,
        )
        assert returned_source_status.deadline is None
        returned_graph = context.service.wait_graph(context.owner)
        assert not any(
            edge["from"] == f"task:{context.terminal_id}"
            and edge["to"] == f"question_group:{group_id}"
            for edge in returned_graph["edges"]
        )
        returned_wait = await context.service.wait_for_required(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=0,
        )
        assert _plain(returned_wait)["state"] == "released"
        assert all(not item["path"].endswith("/ack") for item in context.requests_seen)
        return await asyncio.wait_for(waiting, timeout=8.0)

    result = asyncio.run(exercise_same_step())
    assert result == "verified result after answer"
    assert group_id
    assert redelivery_calls == []

    # Optional requests do not create a dependency edge, so they do not pause
    # the post-turn observer for a later independent task.
    optional_group = _plain(
        context.service.create_group(
            context.source_token,
            CreateGroupRequest(
                questions=(
                    QuestionInput(
                        kind="text",
                        prompt="Optional preference for the next task.",
                        required=False,
                    ),
                ),
            ),
            "wait-omission-optional-operation-0001",
        )
    )
    assert not context.service.has_required_wait(context.terminal_id, context.incarnation_id)
    # This optional request reuses the same already-completed registered-source
    # receipt. Keep the real snapshot, native-wait, PostgreSQL, two-observation,
    # and receipt-settlement paths; the controlled functional clock avoids making
    # this test a host/DB latency measurement. asyncio's own real clock is untouched,
    # and the original 8-second integration budget still bounds a stuck operation.
    from types import SimpleNamespace

    real_monotonic = agent_step.time.monotonic
    virtual_now = [real_monotonic()]

    def controlled_monotonic():
        current = virtual_now[0]
        virtual_now[0] += 0.01
        return current

    monkeypatch.setattr(agent_step, "time", SimpleNamespace(monotonic=controlled_monotonic))
    optional_probe_start = len(probe_calls)
    optional_result = asyncio.run(
        asyncio.wait_for(
            agent_step._wait_for_completion(
                context.terminal_id,
                timeout=0.5,
            ),
            timeout=8.0,
        )
    )
    assert len(probe_calls) - optional_probe_start >= 2
    assert optional_result == "verified result after answer"
    assert optional_group["questions"][0]["required"] is False
    monkeypatch.setattr(terminal_service, "probe_post_turn_receipt_result", original_probe)


def test_manual_only_required_credential_stays_blocking_after_optional_answer(
    interaction_http_context,
):
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        InteractionAnswerPayload,
        QuestionInput,
    )
    from cli_agent_orchestrator.services.interactions.service import InteractionForbidden

    context = interaction_http_context
    group = _plain(
        context.service.create_group(
            context.source_token,
            CreateGroupRequest(
                questions=(
                    QuestionInput(
                        kind="text",
                        prompt="Please provide your API key to continue.",
                        required=True,
                    ),
                    QuestionInput(
                        kind="text",
                        prompt="Optional preference while the private auth flow runs.",
                        required=False,
                    ),
                ),
                wait_for_required=False,
            ),
            "credential-manual-only-operation-0001",
        )
    )
    credential, optional = group["questions"]
    assert credential["lifecycle"] == "manual_only"
    assert optional["lifecycle"] == "open"
    assert context.service.has_required_wait(context.terminal_id, context.incarnation_id)

    with pytest.raises(InteractionForbidden):
        context.service.accept_answer(
            context.owner,
            credential["question_id"],
            1,
            InteractionAnswerPayload(kind="text", text="must never be accepted"),
            "credential-answer-must-be-rejected-001",
        )

    context.service.accept_answer(
        context.owner,
        optional["question_id"],
        1,
        InteractionAnswerPayload(kind="text", text="The preference is optional."),
        "credential-optional-answer-accepted-001",
    )
    pulled = _plain(
        asyncio.run(
            context.service.wait_for_answers(
                context.source_token,
                group["group_id"],
                timeout_seconds=0,
            )
        )
    )
    assert pulled["state"] == "pending"
    assert pulled["answers"] == []
    assert context.service.has_required_wait(context.terminal_id, context.incarnation_id)

    wait_status = asyncio.run(
        context.service.wait_for_required(
            context.terminal_id,
            context.incarnation_id,
            timeout_seconds=0,
        )
    )
    assert _plain(wait_status)["state"] == "waiting"


def test_cancelling_optional_question_preserves_required_group_wait(interaction_http_context):
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        InteractionAnswerPayload,
        QuestionInput,
    )

    context = interaction_http_context
    group = _plain(
        context.service.create_group(
            context.source_token,
            CreateGroupRequest(
                questions=(
                    QuestionInput(kind="text", prompt="Required answer.", required=True),
                    QuestionInput(kind="text", prompt="Optional extra detail.", required=False),
                ),
                wait_for_required=False,
            ),
            "optional-cancel-required-wait-operation-01",
        )
    )
    required, optional = group["questions"]

    cancelled = context.service.cancel(
        context.owner,
        optional["question_id"],
        optional["version"],
        "The optional clarification is no longer needed.",
    )
    assert cancelled["lifecycle"] == "cancelled"
    assert context.service.has_required_wait(context.terminal_id, context.incarnation_id)
    waiting = _plain(
        asyncio.run(
            context.service.wait_for_answers(
                context.source_token,
                group["group_id"],
                timeout_seconds=0,
            )
        )
    )
    assert waiting["state"] == "pending"
    assert waiting["answers"] == []

    context.service.accept_answer(
        context.owner,
        required["question_id"],
        required["version"],
        InteractionAnswerPayload(kind="text", text="The dependent task can continue."),
        "optional-cancel-required-answer-key-01",
    )
    ready = _plain(
        asyncio.run(
            context.service.wait_for_answers(
                context.source_token,
                group["group_id"],
                timeout_seconds=0,
            )
        )
    )
    assert ready["state"] == "ready"
    assert [answer["question_id"] for answer in ready["answers"]] == [required["question_id"]]


def test_expired_required_question_returns_reconcile_without_resending_task(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.models.terminal import TerminalStatus
    from cli_agent_orchestrator.services import agent_step, terminal_service
    from cli_agent_orchestrator.services.agent_step import StepExecutionError
    from cli_agent_orchestrator.services.interactions import service as service_module
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        InteractionStale,
        QuestionInput,
    )

    context = interaction_http_context
    group = _plain(
        context.service.create_group(
            context.source_token,
            CreateGroupRequest(
                questions=(
                    QuestionInput(
                        kind="text",
                        prompt="A response that expires before the operator answers.",
                        required=True,
                    ),
                ),
                expires_in_seconds=1,
            ),
            "expired-required-reconcile-operation-001",
        )
    )
    question = group["questions"][0]
    now = service_module._now()
    monkeypatch.setattr(service_module, "_now", lambda: now + 2)
    monkeypatch.setattr(
        terminal_service, "_interaction_service", lambda: context.service, raising=False
    )
    monkeypatch.setattr(agent_step, "_interaction_service", lambda: context.service, raising=False)
    monkeypatch.setattr(
        terminal_service.status_monitor,
        "get_status",
        lambda _terminal_id: TerminalStatus.WAITING_USER_ANSWER,
    )
    monkeypatch.setattr(
        terminal_service, "probe_post_turn_receipt_result", lambda _terminal_id: None
    )

    post_dispatch_inputs: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        terminal_service,
        "send_input",
        lambda *args, **kwargs: post_dispatch_inputs.append((args, kwargs)),
    )
    with pytest.raises(StepExecutionError) as failure:
        asyncio.run(
            agent_step._wait_for_completion(
                context.terminal_id,
                timeout=1,
                prompt="the already-dispatched initial task",
            )
        )
    assert failure.value.kind == "reconcile"
    assert failure.value.action == "wait_for_interaction"
    assert failure.value.delivery_may_have_occurred is True
    assert post_dispatch_inputs == []

    with pytest.raises(InteractionStale):
        context.service.accept_answer(
            context.owner,
            question["question_id"],
            question["version"],
            {"kind": "text", "text": "Too late to release the old task."},
            "expired-answer-must-not-release-001",
        )
    assert not context.service.has_required_wait(context.terminal_id, context.incarnation_id)
    expired_pull = _plain(
        asyncio.run(
            context.service.wait_for_answers(
                context.source_token,
                group["group_id"],
                timeout_seconds=0,
            )
        )
    )
    assert expired_pull["state"] == "expired"
    assert expired_pull["answers"] == []


def test_revoked_profile_cannot_accept_required_answer_or_release_same_agent_step(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.models.terminal import TerminalStatus
    from cli_agent_orchestrator.services import agent_step, terminal_service
    from cli_agent_orchestrator.services.agent_step import StepExecutionError
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        InteractionAnswerPayload,
        InteractionForbidden,
        QuestionInput,
    )

    context = interaction_http_context
    group = _plain(
        context.service.create_group(
            context.source_token,
            CreateGroupRequest(
                questions=(
                    QuestionInput(kind="text", prompt="A required answer from the current source."),
                )
            ),
            "revoked-profile-wait-operation-0001",
        )
    )
    question = group["questions"][0]
    monkeypatch.setattr(agent_step, "_interaction_service", lambda: context.service, raising=False)
    monkeypatch.setattr(
        terminal_service, "_interaction_service", lambda: context.service, raising=False
    )
    monkeypatch.setattr(
        terminal_service.status_monitor,
        "get_status",
        lambda _terminal_id: TerminalStatus.WAITING_USER_ANSWER,
    )
    monkeypatch.setattr(
        terminal_service, "probe_post_turn_receipt_result", lambda _terminal_id: None
    )
    redeliveries: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        terminal_service,
        "send_input",
        lambda *args, **kwargs: redeliveries.append((args, kwargs)),
    )

    async def revoke_while_the_original_step_waits():
        wait_started = asyncio.Event()
        original_wait = context.service.wait_for_required

        async def signal_wait(*args, **kwargs):
            if kwargs.get("timeout_seconds", 0) > 0:
                wait_started.set()
            return await original_wait(*args, **kwargs)

        monkeypatch.setattr(context.service, "wait_for_required", signal_wait)
        waiting = asyncio.create_task(
            agent_step._wait_for_completion(
                context.terminal_id,
                timeout=2,
                prompt="the already-dispatched task",
            )
        )
        await asyncio.wait_for(wait_started.wait(), timeout=1)
        # Removing the bundled tool from the live profile invalidates the
        # issued source capability, even though the old question row remains.
        with database.SessionLocal() as connection:
            terminal = connection.get(database.TerminalModel, context.terminal_id)
            assert terminal is not None
            terminal.allowed_tools = "[]"
            connection.commit()

        with pytest.raises(InteractionForbidden):
            context.service.accept_answer(
                context.owner,
                question["question_id"],
                question["version"],
                InteractionAnswerPayload(
                    kind="text", text="The revoked source cannot release this step."
                ),
                "revoked-profile-answer-rejected-001",
            )
        with pytest.raises(StepExecutionError) as failure:
            await asyncio.wait_for(waiting, timeout=1)
        return failure.value

    failure = asyncio.run(revoke_while_the_original_step_waits())
    assert failure.kind == "reconcile"
    assert failure.delivery_may_have_occurred is True
    assert (
        context.service.source_status(context.terminal_id, context.incarnation_id).state
        == "inactive"
    )
    assert redeliveries == []


def test_native_manual_notice_direct_input_and_exact_closure_resume_same_step(
    interaction_http_context, monkeypatch
):
    import cli_agent_orchestrator.services.remote_terminal_service as remote
    from cli_agent_orchestrator.models.terminal import TerminalStatus
    from cli_agent_orchestrator.providers.base import BaseProvider
    from cli_agent_orchestrator.services import agent_step, terminal_service
    from cli_agent_orchestrator.services.status_monitor import StatusMonitor, status_monitor

    context = interaction_http_context
    metadata = terminal_service.get_terminal_metadata(context.terminal_id)
    assert metadata is not None
    monkeypatch.setattr(remote, "placement", lambda _terminal_id: None)
    monkeypatch.setattr(
        terminal_service, "_interaction_service", lambda: context.service, raising=False
    )
    monkeypatch.setattr(agent_step, "_interaction_service", lambda: context.service, raising=False)

    class ManualSignalProvider(BaseProvider):
        async def initialize(self):
            return True

        def get_status(self, _buffer):
            return TerminalStatus.UNKNOWN

        def extract_last_message_from_script(self, script_output):
            return script_output

        def exit_cli(self):
            return "/exit"

        def cleanup(self):
            return None

    provider = ManualSignalProvider(
        context.terminal_id,
        metadata["tmux_session"],
        metadata["tmux_window"],
    )
    monitor = StatusMonitor()
    monitor._observe_native_interaction(
        context.terminal_id,
        provider,
        TerminalStatus.WAITING_USER_ANSWER,
        ["Provider requires manual action"],
    )
    pin = monitor._native_observation_pins[context.terminal_id]
    initial_generation = pin["turn_generation"]
    with context.service.repository.read_connection() as connection:
        notice = connection.execute(
            "SELECT question_id,lifecycle FROM interaction_questions WHERE observation_id=%s",
            (pin["observation_id"],),
        ).fetchone()
    question_id = notice["question_id"]
    assert notice["lifecycle"] == "manual_only"

    monkeypatch.setattr(status_monitor, "get_status", lambda _terminal_id: TerminalStatus.COMPLETED)
    monkeypatch.setattr(agent_step, "_COMPLETION_POLL_INTERVAL", 0.1)
    monkeypatch.setattr(
        terminal_service,
        "probe_post_turn_receipt_result",
        lambda _terminal_id: "verified result from the existing task",
    )
    settlements: list[tuple[str, str]] = []

    def settle_existing_result(terminal_id, result):
        settlements.append((terminal_id, result))
        return result

    monkeypatch.setattr(terminal_service, "settle_post_turn_receipt_result", settle_existing_result)
    redeliveries: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        terminal_service,
        "send_input",
        lambda *args, **kwargs: redeliveries.append((args, kwargs)),
    )

    async def resume_after_direct_input_and_exact_dialog_closure():
        native_wait_started = asyncio.Event()
        wait_generations: list[int] = []
        original_wait = context.service.wait_for_native_manual
        original_reconcile = context.service.reconcile_native_observation
        reconciliation_errors: list[Exception] = []

        def record_reconciliation(*args, **kwargs):
            try:
                return original_reconcile(*args, **kwargs)
            except Exception as error:
                reconciliation_errors.append(error)
                raise

        monkeypatch.setattr(context.service, "reconcile_native_observation", record_reconciliation)

        async def record_native_wait(*args, **kwargs):
            wait_generations.append(kwargs["expected_generation"])
            result = await original_wait(*args, **kwargs)
            if result.state == "waiting":
                native_wait_started.set()
            return result

        monkeypatch.setattr(context.service, "wait_for_native_manual", record_native_wait)
        waiting = asyncio.create_task(
            agent_step._wait_for_completion(
                context.terminal_id,
                timeout=5,
                prompt="the already-dispatched native-dialog task",
            )
        )
        await asyncio.wait_for(native_wait_started.wait(), timeout=1)
        # Hold the exact native notice longer than the active completion
        # budget. The wait must keep the same dispatched task alive until its
        # provider-owned closure evidence arrives.
        await asyncio.sleep(5.1)
        assert not waiting.done(), "native manual wait must suspend the active task timeout"

        # This is the authorized live-terminal input fence used by the
        # WebSocket attachment. It advances identity before the user's bytes
        # reach the provider and does not synthesize an answer in the ledger.
        new_generation = terminal_service.record_direct_interaction_input(
            context.terminal_id,
            reason="websocket_input",
            idempotency_key="native-manual-input-advance-0001",
        )
        assert new_generation == initial_generation + 1
        assert (
            context.service.source_status(context.terminal_id, context.incarnation_id).state
            == "current"
        )
        assert monitor._native_observation_pins[context.terminal_id] == pin
        assert pin["target"] == (
            context.terminal_id,
            metadata["tmux_session"],
            metadata["tmux_window"],
        )

        # A provider-owned status transition supplies the exact closure proof
        # for the pinned old dialog. Generic status prose is never an answer.
        monitor._observe_native_interaction(
            context.terminal_id,
            provider,
            TerminalStatus.IDLE,
            ["Ready for input after the manual choice"],
        )
        with context.service.repository.read_connection() as connection:
            first_closure_observation = connection.execute(
                "SELECT lifecycle FROM interaction_questions WHERE question_id=%s",
                (question_id,),
            ).fetchone()
        assert first_closure_observation["lifecycle"] == "stale"

        # A second stable provider viewport confirms that the old dialog has
        # actually disappeared, rather than treating one transient redraw as
        # evidence that the operator answered it.
        monitor._observe_native_interaction(
            context.terminal_id,
            provider,
            TerminalStatus.IDLE,
            ["Ready for input after the manual choice"],
        )
        with context.service.repository.read_connection() as connection:
            reconciled = connection.execute(
                "SELECT lifecycle FROM interaction_questions WHERE question_id=%s",
                (question_id,),
            ).fetchone()
        assert reconciled["lifecycle"] == "reconciled_external", reconciliation_errors
        # This case exercises real PostgreSQL reconciliation plus stable
        # receipt settlement; timeout is intentionally an active-time limit,
        # while this outer bound protects the test from a stuck task.
        result = await asyncio.wait_for(waiting, timeout=8)
        return result, wait_generations

    result, wait_generations = asyncio.run(resume_after_direct_input_and_exact_dialog_closure())
    assert result == "verified result from the existing task"
    assert initial_generation in wait_generations
    assert initial_generation + 1 in wait_generations
    assert settlements == [(context.terminal_id, result)]
    assert redeliveries == []
    assert (
        context.service.source_status(
            context.terminal_id, context.incarnation_id
        ).current_generation
        == initial_generation + 1
    )


def test_real_inbox_delivery_guard_keeps_required_target_pending_and_sibling_independent(
    interaction_http_context, monkeypatch
):
    from cli_agent_orchestrator.clients import database
    from cli_agent_orchestrator.models.inbox import MessageStatus
    from cli_agent_orchestrator.models.terminal import TerminalStatus
    from cli_agent_orchestrator.services import inbox_service, terminal_service
    from cli_agent_orchestrator.services.inbox_service import InboxService
    from cli_agent_orchestrator.services.interactions.models import (
        CreateGroupRequest,
        QuestionInput,
    )

    context = interaction_http_context
    context.service.create_group(
        context.source_token,
        CreateGroupRequest(
            questions=(
                QuestionInput(
                    kind="text",
                    prompt="Required dependency; the caller sets wait_for_required false.",
                    required=True,
                ),
            ),
            wait_for_required=False,
        ),
        "inbox-required-wait-operation-0001",
    )
    sibling_id, _ = _register_sibling_source(context)

    sent_keys: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class RecordingBackend:
        def send_keys(self, *args, **kwargs):
            sent_keys.append((args, kwargs))

    class EagerProvider:
        accepts_input_while_processing = True
        blocks_orchestrated_input_while_waiting_user_answer = False
        blocks_new_task_input_for_reconciliation = False
        requires_turn_receipt = False
        assume_processing_on_dispatch = False
        paste_enter_count = 1
        paste_submit_delay = 0.0

        def mark_input_received(self):
            pass

        def record_dispatched_message(self, _message):
            pass

    provider = EagerProvider()
    monkeypatch.setattr(terminal_service, "get_backend", lambda: RecordingBackend())
    monkeypatch.setattr(
        terminal_service, "_interaction_service", lambda: context.service, raising=False
    )
    monkeypatch.setattr(terminal_service, "inject_memory_context", lambda message, *_: message)
    monkeypatch.setattr(
        inbox_service.provider_manager,
        "get_provider",
        lambda _terminal_id: provider,
    )
    status_for_terminal = lambda terminal_id: (
        TerminalStatus.WAITING_USER_ANSWER
        if terminal_id == context.terminal_id
        else TerminalStatus.PROCESSING
    )
    monkeypatch.setattr(inbox_service.status_monitor, "get_status", status_for_terminal)
    monkeypatch.setattr(terminal_service.status_monitor, "get_status", status_for_terminal)
    monkeypatch.setattr(inbox_service, "EAGER_INBOX_DELIVERY", True)
    monkeypatch.setattr(
        terminal_service.status_monitor,
        "notify_input_sent",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        terminal_service.status_monitor, "clear_rolling_buffer", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        terminal_service.status_monitor, "notify_input_delivered", lambda *_a, **_k: None
    )
    monkeypatch.setattr(terminal_service.status_monitor, "abort_turn", lambda *_a, **_k: None)

    target_message = database.create_inbox_message(
        sender_id="interaction-supervisor",
        receiver_id=context.terminal_id,
        message="An unrelated task must not overtake the required answer.",
    )
    sibling_message = database.create_inbox_message(
        sender_id="interaction-supervisor",
        receiver_id=sibling_id,
        message="The sibling task remains independent.",
    )
    inbox = InboxService()
    inbox.deliver_pending(context.terminal_id)
    inbox.deliver_pending(sibling_id)

    target_rows = database.get_inbox_messages(context.terminal_id, status=MessageStatus.PENDING)
    sibling_rows = database.get_inbox_messages(sibling_id, status=MessageStatus.DELIVERED)
    assert [message.id for message in target_rows] == [target_message.id]
    assert [message.id for message in sibling_rows] == [sibling_message.id]
    assert len(sent_keys) == 1
    assert sent_keys[0][0][0] == "interaction-independent-sibling"
