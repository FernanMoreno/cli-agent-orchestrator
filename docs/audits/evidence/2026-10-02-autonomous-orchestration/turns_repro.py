import logging, re
from unittest.mock import MagicMock,patch
from cli_agent_orchestrator.services import terminal_service as ts
from cli_agent_orchestrator.providers.codex import CodexProvider
logging.disable(logging.CRITICAL)
fingerprint=('1'*32,'a'*64)
provider=MagicMock(requires_turn_receipt=True)
provider.pending_turn_receipt_state.return_value=dict(generation=fingerprint[0],receipt_sha256=fingerprint[1])
threads=[]; timers=[]
class Thread:
 def __init__(self,target,**kw):self.target=target
 def start(self):threads.append(self.target)
class Timer:
 def __init__(self,delay,target,args=()):self.delay=delay;self.target=target;self.args=args
 def start(self):timers.append(self)
 def cancel(self):pass
with patch.object(ts.threading,'Thread',Thread),patch.object(ts.threading,'Timer',Timer),patch.object(ts,'mark_terminal_turn_verification_started',return_value={'state':'verifying','attempts':0}),patch.object(ts,'record_terminal_turn_verification_failure',side_effect=RuntimeError('database unavailable')),patch.object(ts,'get_output',side_effect=ts.TurnResultUnavailableError('no receipt')):
 for i in range(5):
  ts.schedule_receipt_result_verification('audit',provider)
  try:threads.pop(0)()
  except RuntimeError:pass
  s=ts._receipt_verification_state['audit']
  print('DB outage observation',i+1,'attempts',s.attempts,'reconciled',s.reconciled,'retry_delay',timers[-1].delay)
 ts._receipt_verification_state.clear();ts._receipt_verification_inflight.clear()
with patch.object(ts.threading,'Thread',Thread):
 ts._receipt_verification_state['late']=ts._ReceiptVerificationState(fingerprint,reconciled=True)
 print('Late receipt scheduled?',ts.schedule_receipt_result_verification('late',provider),'new worker count',len(threads))
 ts._receipt_verification_state.clear()
p=CodexProvider('audit','audit','audit');prepared=p.prepare_input('Task')
receipt=re.search(r'CAO-TURN-RECEIPT-[0-9a-f]{32}',prepared).group()
output=('› Task\n• completed the requested task\n'+receipt+'\n\n  done 1:43 PM\n\n  Approaching rate limits\n  Switch to gpt-5.6-luna for lower credit usage?\n\n› 1. Switch to gpt-5.6-luna                 Fast and affordable model.\n  2. Keep current model\n  3. Keep current model (never show again)\n\n  Press enter to confirm or esc to go back\n')
state=p.pending_turn_receipt_state();state['phase']='sent'
result=p.extract_post_turn_completion_result(output)
print('Picker prior restart',repr(result.replace(receipt, '[synthetic receipt]')),p.receipt_result_terminal_status(output,result))
p.restore_turn_receipt_state(state)
print('Picker after restart',repr(p.extract_post_turn_completion_result(output)),p.receipt_result_terminal_status(output,result),'hash still matches',p._result_has_active_receipt(result))
from cli_agent_orchestrator.providers import manager as pm
m=pm.ProviderManager()
metadata=dict(provider='codex',tmux_session='audit',tmux_window='audit',agent_profile=None)
with patch.object(pm,'registered_provider_descriptor',return_value=None),patch.object(pm,'get_terminal_metadata',return_value=metadata),patch.object(pm,'get_terminal_turn_receipt',side_effect=RuntimeError('database unavailable')) as read:
 try:m.get_provider('audit')
 except RuntimeError:print('First provider lookup failed restoration')
 cached=m.get_provider('audit')
 print('Second lookup reads DB count',read.call_count,'cached active receipt',cached.pending_turn_receipt_state(),'blocks input',cached.blocks_new_task_input_for_reconciliation)
from contextlib import nullcontext
from cli_agent_orchestrator.services import work_terminal
backend=MagicMock()
backend.get_history.return_value='Historical assistant transcript without current receipt'
pending=dict(generation='1'*32,state='pending',attempts=0,result_text=None)
with patch.object(ts,'get_terminal_metadata',return_value=metadata),patch.object(ts,'get_terminal_turn_recovery',return_value=pending),patch.object(ts.status_monitor,'get_buffer',return_value='Historical assistant transcript without current receipt'),patch.object(ts,'provider_manager',m),patch.object(ts,'get_backend',return_value=backend),patch.object(cached,'extract_last_message_from_script',return_value='Historical answer WITHOUT active receipt'),patch.object(ts,'settle_terminal_turn_receipt_result') as settle,patch.object(work_terminal,'terminal_dispatch_lock',return_value=nullcontext()):
 result=ts.get_output('audit',ts.OutputMode.LAST)
 print('LAST with durable pending and cached empty receipt:',repr(result),'settle calls',settle.call_count)
