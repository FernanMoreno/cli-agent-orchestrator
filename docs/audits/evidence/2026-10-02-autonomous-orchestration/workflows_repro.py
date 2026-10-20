"""Run from repository root: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python docs/audits/evidence/2026-10-02-autonomous-orchestration/workflows_repro.py"""
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock
from cli_agent_orchestrator import constants
from cli_agent_orchestrator.models.terminal import TerminalStatus, AgentStepResult
from cli_agent_orchestrator.models.workflow import WorkflowSpec, WorkflowStep
from cli_agent_orchestrator.services import agent_step as a, workflow_service as w, workflow_journal as j
from cli_agent_orchestrator.clients.work_repository import WorkRepository

async def main():
    with tempfile.TemporaryDirectory() as directory, patch.object(constants, 'DATABASE_FILE', Path(directory) / 'audit.db'):
        j._connect().close()
        WorkRepository(constants.DATABASE_FILE).initialize()
        with patch.object(a.status_monitor, 'get_status', return_value=TerminalStatus.PROCESSING), patch.object(a.terminal_service, 'probe_post_turn_receipt_result', return_value=None):
            try:
                await a._wait_for_completion('aaaa1111', 0, prompt_redelivery=False)
            except a.StepExecutionError as error:
                captured = error
        print('POST_SEND_TIMEOUT', captured.kind, 'delivery_may_have_occurred=', captured.delivery_may_have_occurred)
        calls = 0
        async def mocked_step(**kwargs):
            nonlocal calls
            calls += 1
            fields = a._effective_step_fields(provider=kwargs['provider'], agent=kwargs['agent'], allowed_tools=None, engine=kwargs['engine'], model=None, working_directory=None, use_worktree=False, created_here=True, timeout=kwargs['timeout'], ready_timeout=a.DEFAULT_READY_TIMEOUT, teardown=True, prompt_redelivery=True)
            kwargs['pre_delivery_recorder'].record_pre_delivery('v2:audit-timeout', fields)
            if calls == 1:
                raise captured
            return AgentStepResult(terminal_id='bbbb2222', last_message='done', status=TerminalStatus.COMPLETED)
        spec = WorkflowSpec(name='audit', mode='sequential', steps=[WorkflowStep(id='s1', provider='claude_code', agent='dev', prompt='side effect', retries=1)])
        with patch.object(w, 'run_agent_step', AsyncMock(side_effect=mocked_step)):
            result = await w.start_run(spec, {}, 'audit-timeout')
        print('WORKFLOW_AFTER_POST_SEND_TIMEOUT calls=', calls, 'state=', result.state.value, 'attempts=', result.steps[0].attempts)
        with patch.object(w, 'run_agent_step', AsyncMock(side_effect=ValueError('extractor failed'))):
            try:
                await w.start_run(spec, {}, 'audit-extractor')
            except Exception as error:
                print('RAISED', type(error).__name__)
        print('STATE', j.get_run('audit-extractor').state, 'STEP', j.get_steps('audit-extractor')[0].state, 'ACTIVE', 'audit-extractor' in w._active_drives)

asyncio.run(main())
