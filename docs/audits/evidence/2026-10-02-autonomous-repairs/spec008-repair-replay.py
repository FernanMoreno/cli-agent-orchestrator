import hashlib,json,re,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'src'))
from cli_agent_orchestrator.providers.opencode_cli import OpenCodeCliProvider
root=Path('/var/tmp/cao-spec008-repair-native-vvd9akof')
p=root/'.aws/cli-agent-orchestrator/logs/terminal/cf829139.scrollback'
raw=p.read_text()
receipts=list(dict.fromkeys(re.findall(r'CAO[-_]TURN[-_]RECEIPT[-_][0-9a-f]{32}',raw)))
records=[]
for nonce in receipts:
 provider=OpenCodeCliProvider('offline','offline','offline');provider.restore_runtime_variant('opencode-v2')
 provider.restore_turn_receipt_state({'phase':'sent','generation':'1'*32,'receipt_sha256':hashlib.sha256(nonce.encode()).hexdigest()})
 try:
  result=provider.extract_last_message_from_script(raw)
  witness=provider.receipt_result_terminal_status(raw,result)
  records.append({'receipt_digest':hashlib.sha256(nonce.encode()).hexdigest(),'status':provider.get_status(raw).value,'witness':witness.value if witness else None,'result_sha256':hashlib.sha256(result.encode()).hexdigest(),'stale_transport_response':'failed to send message' in result.lower()})
 except Exception as e:records.append({'receipt_digest':hashlib.sha256(nonce.encode()).hexdigest(),'exception':type(e).__name__})
report={'artifact_sha256':hashlib.sha256(raw.encode()).hexdigest(),'offline_replay_only':True,'records':records}
Path('/tmp/spec008-repair-native-replay.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
