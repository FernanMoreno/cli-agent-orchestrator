import os,sys,json,shutil,tempfile,time,hashlib,sqlite3,re,traceback
from pathlib import Path
import requests,frontmatter
sys.path.insert(0,'/mnt/c/users/ferna/onedrive/escritorio/caos')
from test.fixtures.cao_server import _start_cao_server,_pick_free_port
repo=Path('/mnt/c/users/ferna/onedrive/escritorio/caos');project=repo.parent/'cao-turn-recovery-demo-20261002-f9086a25'
root=Path(tempfile.mkdtemp(prefix='cao-spec008-autonomy-',dir='/var/tmp'));root.chmod(0o700);Path('/tmp/spec008-autonomy-pointer').write_text(str(root));shutil.copytree(project,root/'project-before')
secrets=['.codex/auth.json','.claude/.credentials.json','.claude.json','.local/share/opencode/auth.json']
for name in secrets:
 src=Path('/home/felni')/name
 if src.exists():
  dst=root/name;dst.parent.mkdir(parents=True,exist_ok=True,mode=0o700);shutil.copyfile(src,dst);dst.chmod(0o600)
(root/'.codex').mkdir(exist_ok=True,mode=0o700);(root/'.codex/config.toml').write_text('approval_policy = "never"\nsandbox_mode = "danger-full-access"\n')
fault=root/'fault-instrumentation';fault.mkdir();gate=root/'receipt-gate.json';events=root/'receipt-fault-events.jsonl'
site='''import json,time,hashlib\nfrom pathlib import Path\nfrom cli_agent_orchestrator.services import terminal_service as ts\noriginal=ts._verify_receipt_bearing_result\ngate=Path(GATE_PATH)\nevents=Path(EVENTS_PATH)\ndef delayed(terminal_id,provider,transcript,result,**kwargs):\n try: config=json.loads(gate.read_text())\n except (FileNotFoundError,ValueError): config={}\n if config.get('active') and config.get('terminal_id')==terminal_id:\n  witness=(provider.receipt_result_viewport_terminal_status if kwargs.get('rendered_viewport') else provider.receipt_result_terminal_status)(transcript,result)\n  if witness is not None:\n   with events.open('a') as stream: stream.write(json.dumps({'at':time.time(),'terminal_id':terminal_id,'native_receipt_witness':True,'result_sha256':hashlib.sha256(result.encode()).hexdigest()})+'\\n')\n  raise ts.TurnResultUnavailableError('Controlled acceptance fault: genuine receipt temporarily unavailable')\n return original(terminal_id,provider,transcript,result,**kwargs)\nts._verify_receipt_bearing_result=delayed\n'''
site=site.replace('GATE_PATH',repr(str(gate))).replace('EVENTS_PATH',repr(str(events)));(fault/'sitecustomize.py').write_text(site)
port=_pick_free_port();extra={'CAO_HOME_DIR':str(root/'.aws/cli-agent-orchestrator'),'PATH':str(repo/'.venv/bin')+':'+os.environ['PATH'],'PYTHONPATH':str(fault)+':'+str(repo/'src')+':'+str(repo),'CAO_MEMORY_ENABLED':'false','CAO_PROVIDER_INIT_TIMEOUT':'120'}
report={'root':str(root),'project':str(project),'branch':'main','port':port,'started_at':time.time(),'phases':[],'operator_corrective_messages':0,'operator_verify_calls':0,'operator_retry_calls':0,'notes_before_sha256':hashlib.sha256((project/'notes.json').read_bytes()).hexdigest(),'fault_instrumentation':'Private sitecustomize only refuses receipt verification for one selected native terminal; never generates receipt/output and never changes product source.'}
def save(): (root/'public-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
def stamp():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
save();print('private_evidence',root,flush=True)
server=None;session=None
store=root/'.aws/cli-agent-orchestrator/agent-store';store.mkdir(parents=True,exist_ok=True)
common='You participate in a real CAO autonomous acceptance on the existing notes demo. Work only in the assigned project; never change notes.json, credentials or other repositories. No commits/push, global installs, native subagents or extra workers. Use your own native tools and the configured CAO MCP server for communication; official cao agent CLI is also available with the same identity if needed. Never print environment/credentials or persist completion-receipt text in files. Every bounded turn ends with the exact CAO receipt supplied for that current turn, including inbox-triggered turns. Do not wait for messages inside a turn: send your result, finish, then react to messages in subsequent turns. User-facing documents in Spanish. '
supervisor=common+'You coordinate: delegate implementation to exactly two existing profiles auto-api (Codex backend/tests) and auto-web (OpenCode frontend). You may read files, use CAO tools and run validation commands; never write implementation yourself or run shell edits. Ask the workers to exchange a PEER_CONTRACT and PEER_ACK over CAO using their actual IDs. Their results must return to you over CAO. Use stable operation keys for assignment if available; never allocate another worker after uncertain acceptance. Pending/reconcile is not success, not authorization to resend or cancel. End your initial turn after assigning and reporting real IDs; do not poll indefinitely. On inbox results, inspect the real current source and child receipts, coordinate fixes through the same workers if needed, run tests, and only declare the requested AUTONOMY_COMPLETE marker after both native children have succeeded and acceptance is proven. Each result/inbox turn is bounded and ends with its own exact receipt. Do not declare final success based only on model claims.'
workers={'auto-api':('codex','gpt-6.1-sol',common+'You own api/server.py and tests (test_api.py or another isolated unittest module) plus CONTRACT.md. Do not edit frontend files. Coordinate the actual HTTP contract with the peer frontend worker via CAO; send PEER_ACK when asked. Report implementation and real test results to the recorded supervisor using send_message without receiver_id. Use temporary notes data and a real HTTP server on a free port for tests. If you receive a peer message after finishing, answer that specific message without repeating the implementation.'),'auto-web':('opencode_cli','opencode-go/kimi-k3',common+'You own frontend files and UI documentation, not api/server.py, tests or notes.json. Read current contract and coordinate with the actual API peer using PEER_CONTRACT and wait for its PEER_ACK in a subsequent turn, never a blocking loop. Send your result to the recorded supervisor using send_message without receiver_id. Verify JavaScript syntax. After receiving an ACK, notify the supervisor and finish that turn; do not reimplement a completed task.')}
def write_profiles():
 mcp={'cao-mcp-server':{'type':'stdio','command':str(repo/'.venv/bin/cao-mcp-server'),'args':[],'env':{'CAO_API_HOST':'127.0.0.1','CAO_API_PORT':str(port),'CAO_HOME_DIR':extra['CAO_HOME_DIR']}}}
 all_profiles={'auto-supervisor':('claude_code','sonnet',supervisor),**workers}
 for name,(provider,model,prompt) in all_profiles.items():
  meta={'name':name,'description':'Real autonomous demo acceptance','provider':provider,'model':model,'role':'supervisor' if name=='auto-supervisor' else 'developer','skills':[],'mcpServers':mcp,'allowedTools':['fs_*','execute_bash','@cao-mcp-server'],'provider_init_timeout':120}
  if provider=='claude_code':meta['permissionMode']='bypassPermissions'
  f=store/(name+'.md');f.write_text(frontmatter.dumps(frontmatter.Post(prompt,**meta)));f.chmod(0o600)
 response=requests.post(server.url+'/agents/profiles/install',json={'source':'auto-web','provider':'opencode_cli'},timeout=120);response.raise_for_status()
def sql_snapshot(parent_id):
 db=root/'.aws/cli-agent-orchestrator/db/cli-agent-orchestrator.db';conn=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True,timeout=5);conn.row_factory=sqlite3.Row
 try:
  children=[dict(x) for x in conn.execute('SELECT id,parent_terminal_id,terminal_id,provider,agent_profile,state,error_kind,settled_at FROM native_children WHERE parent_terminal_id=? ORDER BY created_at',(parent_id,))];ids=[parent_id]+[x['terminal_id'] for x in children];marks=','.join('?' for _ in ids)
  turns=[dict(x) for x in conn.execute(f'SELECT terminal_id,generation,state,attempts,reason,completion_detected_at,updated_at FROM terminal_turn_recovery WHERE terminal_id IN ({marks}) ORDER BY created_at',ids)]
  receipts=[dict(x) for x in conn.execute(f'SELECT terminal_id,generation,phase,created_at,verified_at FROM terminal_turn_receipts WHERE terminal_id IN ({marks})',ids)]
  messages=[dict(x) for x in conn.execute(f'SELECT id,sender_id,receiver_id,message,status,created_at FROM inbox WHERE receiver_id IN ({marks}) OR sender_id IN ({marks}) ORDER BY id',ids+ids)]
  intents=[dict(x) for x in conn.execute('SELECT assignment_id,parent_terminal_id,operation_key,state,request_hash FROM assignment_intents WHERE parent_terminal_id=?',(parent_id,))]
  terminals=[dict(x) for x in conn.execute(f'SELECT id,provider,agent_profile,tmux_session,tmux_window,session_incarnation_id FROM terminals WHERE id IN ({marks})',ids)]
  results=[dict(x) for x in conn.execute('SELECT generation,state,result_text FROM terminal_turn_recovery WHERE terminal_id=?',(parent_id,))]
  return {'at':stamp(),'children':children,'turns':turns,'receipts':receipts,'messages':messages,'intents':intents,'terminals':terminals,'supervisor_results':results}
 finally:conn.close()
phases=[('normal','Añade edición del texto de notas existentes, conservando su ID y estado done, validación 1..2000 tras recorte, y un control accesible Editar/Guardar/Cancelar en cada nota. PATCH debe seguir admitiendo done y ahora permitir text; las peticiones vacías o con valores inválidos se rechazan sin modificar datos. Coordina implementación con auto-api y auto-web, intercambio real de contrato y revisión/validación. No indiques éxito hasta pruebas reales.'),('late_receipt','Añade estadísticas: GET /api/stats devuelve total, pending y completed calculados del mismo almacenamiento, y la interfaz muestra los tres contadores coherentes también después de añadir/editar/marcar/eliminar. Coordina auto-api y auto-web, intercambio real de contrato y prueba aislada de datos. El entorno puede demorar evidencia de un recibo; conserva identidades y no repitas tareas ante incertidumbre.'),('restart','Añade limpieza de notas completadas: DELETE /api/notes/completed devuelve deleted_count, conserva todas las pendientes y funciona también si no hay completadas. Añade botón accesible Limpiar completadas con confirmación y actualización de lista/contadores. Conserva DELETE individual y edición. Coordina auto-api y auto-web, intercambio real de contrato y pruebas aisladas. El servidor CAO puede reiniciarse durante el trabajo; usa los mismos trabajadores y mensajes pendientes, sin reasignación ni repetir implementación.')]
try:
 server=_start_cao_server(root,port,extra_env=extra,deadline=120);write_profiles();print('native_profiles_and_mcp_ready',flush=True)
 for number,(kind,goal) in enumerate(phases,1):
  gate.write_text(json.dumps({'active':False}));phase={'number':number,'kind':kind,'status':'running','initial_orders':1,'snapshots':[],'fault':None,'started_at':stamp()};report['phases'].append(phase);save()
  session_name='auto-'+root.name[-8:]+'-'+str(number)
  response=requests.post(server.url+'/sessions',params={'provider':'claude_code','agent_profile':'auto-supervisor','session_name':session_name,'working_directory':str(project)},timeout=180);response.raise_for_status();parent=response.json();session=parent['session_name'];phase['supervisor_id']=parent['id'];phase['session_name']=session;marker='AUTONOMY_COMPLETE_'+root.name[-8:]+'_'+str(number);phase['completion_marker']=marker
  prompt='Proyecto existente: '+str(project)+'. '+goal+' Preserva notes.json original, no commits/push ni agentes adicionales. Tú debes decidir reparto, instrucciones y coordinación. Usa dos trabajadores exactamente, perfiles auto-api y auto-web. Termina con '+marker+' sólo después de comprobar los dos recibos succeeded y pruebas correctas. No supongas que el operador ayudará ni lanzará tareas de trabajadores. Cada turno debe terminar con su recibo CAO auténtico.'
  phase['initial_prompt']=prompt;save();response=requests.post(server.url+'/terminals/'+parent['id']+'/input',params={'message':prompt},timeout=180);phase['initial_http_status']=response.status_code;response.raise_for_status();save();print('single_order_sent',number,kind,parent['id'],flush=True)
  deadline=time.monotonic()+1200;lastsignature=None;reconcile_at=None;gate_armed=False;restarted=False;final_snapshot=None
  while time.monotonic()<deadline:
   snap=sql_snapshot(parent['id']);phase['latest']=snap;signature=json.dumps({k:snap[k] for k in ['children','turns','receipts','messages','intents']},sort_keys=True)
   if signature!=lastsignature:
    phase['snapshots'].append({k:v for k,v in snap.items() if k!='supervisor_results'});lastsignature=signature;save()
   running=[c for c in snap['children'] if c['state'] in ['sent','running','acknowledged','reconcile']]
   if kind=='late_receipt' and not gate_armed:
    child=next((c for c in running if c['provider']=='codex'),None)
    if child:
     gate.write_text(json.dumps({'active':True,'terminal_id':child['terminal_id']}));phase['fault']={'type':'receipt_visibility_refusal','terminal_id':child['terminal_id'],'armed_at':stamp(),'released':False};gate_armed=True;save();print('receipt_fault_armed',child['terminal_id'],flush=True)
   if kind=='late_receipt' and gate_armed and not phase['fault']['released']:
    tid=phase['fault']['terminal_id'];recs=[t for t in snap['turns'] if t['terminal_id']==tid and t['state']=='reconcile']
    if recs and reconcile_at is None:reconcile_at=time.monotonic();phase['fault']['reconcile_observed_at']=stamp();save();print('real_reconcile_observed',tid,flush=True)
    if reconcile_at is not None and time.monotonic()-reconcile_at>=65:
     gate.write_text(json.dumps({'active':False}));phase['fault']['released']=True;phase['fault']['released_at']=stamp();save();print('receipt_visibility_restored_no_input',tid,flush=True)
   if kind=='restart' and not restarted and len(snap['children'])==2 and running and all(any(t['terminal_id']==c['terminal_id'] and t['phase'] in ['sent','result_verified'] for t in snap['receipts']) for c in snap['children']) and any(t['terminal_id']==parent['id'] and t['state']=='verified' for t in snap['turns']):
    phase['fault']={'type':'server_restart','before':snap,'started_at':stamp()};save();print('controlled_server_restart_begin',flush=True);server.stop();time.sleep(8);server=_start_cao_server(root,port,extra_env=extra,deadline=120);phase['fault']['after']=sql_snapshot(parent['id']);phase['fault']['ended_at']=stamp();restarted=True;save();print('controlled_server_restart_ready',flush=True)
   finished=any(x['state']=='verified' and re.search(r'(?m)^\s*'+re.escape(marker)+r'\s*$',x['result_text'] or '') for x in snap['supervisor_results']) and len(snap['children'])==2 and all(c['state']=='succeeded' for c in snap['children']) and all(t['phase']=='result_verified' for t in snap['receipts']) and all(m['status']=='delivered' for m in snap['messages'])
   if finished:
    final_snapshot=snap;phase['status']='supervisor_completed';phase['ended_at']=stamp();save();print('supervisor_completed',number,kind,flush=True);break
   if len(snap['children'])>2:phase['status']='failed_extra_worker';final_snapshot=snap;break
   time.sleep(5)
  if final_snapshot is None:final_snapshot=sql_snapshot(parent['id']);phase['status']='failed_deadline';phase['ended_at']=stamp();print('phase_deadline',number,flush=True)
  phase['final']=final_snapshot;children=final_snapshot['children'];ids={c['terminal_id'] for c in children};msgs=final_snapshot['messages'];peer=[m for m in msgs if m['sender_id'] in ids and m['receiver_id'] in ids]
  phase['checks']={'exactly_two_children':len(children)==2,'expected_providers':{c['provider'] for c in children}=={'codex','opencode_cli'},'children_succeeded':len(children)==2 and all(c['state']=='succeeded' for c in children),'peer_contract_delivered':any(m['status']=='delivered' and 'PEER_CONTRACT' in m['message'] for m in peer),'peer_ack_delivered':any(m['status']=='delivered' and 'PEER_ACK' in m['message'] for m in peer),'both_results_delivered_to_supervisor':all(any(m['sender_id']==tid and m['receiver_id']==parent['id'] and m['status']=='delivered' for m in msgs) for tid in ids) and len(ids)==2,'all_messages_delivered':all(m['status']=='delivered' for m in msgs),'no_duplicate_assignment':len(final_snapshot['intents'])==2 and len({a['operation_key'] for a in final_snapshot['intents']})==2,'notes_unchanged':hashlib.sha256((project/'notes.json').read_bytes()).hexdigest()==report['notes_before_sha256']}
  if kind=='late_receipt':phase['checks']['late_fault_exercised']=bool(phase['fault'] and phase['fault'].get('reconcile_observed_at') and phase['fault']['released']);phase['checks']['genuine_native_witness_observed']=events.exists() and any(json.loads(line).get('native_receipt_witness') for line in events.read_text().splitlines())
  if kind=='restart':phase['checks']['restart_exercised']=restarted;phase['checks']['identities_retained']=restarted and {(c['id'],c['terminal_id']) for c in phase['fault']['before']['children']}=={(c['id'],c['terminal_id']) for c in phase['fault']['after']['children']}
  phase['status']='passed' if phase['status']=='supervisor_completed' and all(phase['checks'].values()) else 'failed';save();print('phase_result',number,phase['status'],json.dumps(phase['checks']),flush=True)
  # Snapshot the phase before teardown; no result reads or recovery calls from the operator.
  shutil.copytree(project,root/('project-after-'+str(number)),ignore=shutil.ignore_patterns('__pycache__','*.pyc'));(root/('phase-'+str(number)+'-private.json')).write_text(json.dumps(phase,ensure_ascii=False,indent=2))
  response=requests.delete(server.url+'/sessions/'+session,timeout=120);phase['cleanup_http_status']=response.status_code;session=None;gate.write_text(json.dumps({'active':False}));save()
except BaseException as e:
 report['error_type']=type(e).__name__;save();print('run_error',type(e).__name__,flush=True);traceback.print_exc()
finally:
 gate.write_text(json.dumps({'active':False}))
 if server and session:
  try:requests.delete(server.url+'/sessions/'+session,timeout=120)
  except Exception:pass
 if server:server.stop()
 for name in secrets:(root/name).unlink(missing_ok=True)
 report['credential_copies_removed']=True;report['notes_after_sha256']=hashlib.sha256((project/'notes.json').read_bytes()).hexdigest();report['ended_at']=time.time();save();print('autonomy_run_closed',flush=True)
