from pathlib import Path
import os,sys,tempfile,shutil,json,hashlib,time,traceback
import requests
sys.path.insert(0,'/mnt/c/users/ferna/onedrive/escritorio/caos')
from test.fixtures.cao_server import _start_cao_server,_pick_free_port
from test.e2e.test_real_provider_matrix import _write_profile,_delete_session
repo=Path('/mnt/c/users/ferna/onedrive/escritorio/caos')
project=repo.parent/'cao-turn-recovery-demo-20261002-f9086a25'
root=Path(tempfile.mkdtemp(prefix='cao-spec008-demo-improvements-',dir='/var/tmp'));root.chmod(0o700)
Path('/tmp/spec008-real-demo-pointer').write_text(str(root))
shutil.copytree(project,root/'project-before')
secret_names=['.codex/auth.json','.claude/.credentials.json','.claude.json','.local/share/opencode/auth.json']
for name in secret_names:
 src=Path('/home/felni')/name
 if src.is_file():
  dst=root/name;dst.parent.mkdir(parents=True,exist_ok=True,mode=0o700);shutil.copyfile(src,dst);dst.chmod(0o600)
(root/'.codex').mkdir(exist_ok=True,mode=0o700)
(root/'.codex/config.toml').write_text('approval_policy = "never"\nsandbox_mode = "danger-full-access"\n')
report={'project':str(project),'root':str(root),'branch':'main','started_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'steps':[],'baseline':{str(p.relative_to(project)):hashlib.sha256(p.read_bytes()).hexdigest() for p in project.rglob('*') if p.is_file()}}
def save(): (root/'public-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
save();print('private_evidence',root,flush=True)
server=None;session=None
common='Trabaja SOLO en este proyecto existente de notas. No borres ni reescribas notes.json, no commits/push, no paquetes globales, no otros agentes ni tareas fuera del demo. Lee archivos actuales antes de cambiar. No escribas recibos ni credenciales en archivos. Completa una única vez esta tarea acotada y devuelve resumen en español y el recibo exacto CAO del turno actual. '
prompts=[('codex','gpt-6.1-sol','demo-api-improve',common+'Mejora api/server.py: añade DELETE /api/notes/{id} con 200 y JSON de confirmación, 404 si no existe. Mantén GET/POST/PATCH y forma {id,text,done}. Valida text: espacios se recortan, 1..2000 caracteres, vacíos/no-string/excesivos 400. Evita pérdida de actualizaciones concurrentes: protege lectura-modificación-escritura con lock y publicación JSON atómica. No cambies frontend. Añade pruebas unittest del contrato en test_api.py usando un fichero temporal y un servidor real en puerto libre (nunca notes.json existente); cubre DELETE, validación, persistencia y creación concurrente. Ejecuta las pruebas. Actualiza CONTRACT.md con cambios. No arranques servidor background persistente.'),('opencode_cli','opencode-go/kimi-k3','demo-ui-improve',common+'Mejora frontend/index.html, frontend/app.js y styles.css: búsqueda por texto sin distinguir mayúsculas; filtro Todas/Pendientes/Completadas; contadores total y pendientes; botón Eliminar por nota usando DELETE /api/notes/{id}. Lee CONTRACT.md actual primero. Conserva añadir, marcar, recargar, fetch relativo, diseño español y textContent seguro. Añade labels accesibles para búsqueda/filtro/checkbox y eliminar. Errores visibles en una región role=alert; evita envíos duplicados mientras petición está pendiente; sólo limpia input tras POST correcto. No modifiques backend, datos ni test_api.py. Verifica sintaxis con node --check frontend/app.js, y escribe UI_IMPROVEMENTS.md breve con controles/contrato. No servidor background.'),('claude_code','sonnet','demo-review-improve',common+'Revisa e integra mejoras actuales API y frontend. Ejecuta python3 -m unittest -v test_api. Lee fuentes actuales, CONTRACT.md, UI_IMPROVEMENTS.md y comprueba coherencia DELETE, longitud 2000, filtros/contadores, accesibilidad y errores. Añade sólo pruebas útiles que falten en test_api.py y actualiza README.md y IMPROVEMENTS_REVIEW.md con evidencia auténtica. Si encuentras fallo real pequeño, corrígelo en demo y repite prueba pertinente; no afirmes pruebas de navegador porque operador hará Chromium externamente. No servidor background. No inventes colaboración/mensajes que no hayas realizado.')]
try:
 server=_start_cao_server(root,_pick_free_port(),extra_env={'CAO_HOME_DIR':str(root/'.aws/cli-agent-orchestrator'),'PATH':str(repo/'.venv/bin')+':'+os.environ['PATH'],'CAO_MEMORY_ENABLED':'false','CAO_PROVIDER_INIT_TIMEOUT':'120'},deadline=120)
 report['server_url']=server.url;save();print('real_server_ready',flush=True)
 _write_profile(server,'demo-parent','claude_code','sonnet')
 response=requests.post(server.url+'/sessions',params={'provider':'claude_code','agent_profile':'demo-parent','session_name':'demo-improve-'+root.name[-8:],'working_directory':str(project)},timeout=180);response.raise_for_status();parent=response.json();session=parent['session_name'];report['parent_id']=parent['id'];report['session_name']=session;save();print('actual_claude_parent',parent['id'],flush=True)
 for provider,model,profile,prompt in prompts:
  _write_profile(server,profile,provider,model)
  step={'provider':provider,'model':model,'profile':profile,'status':'started'};report['steps'].append(step);save();print('step_started',provider,flush=True)
  response=requests.post(server.url+'/terminals/run-step',json={'provider':provider,'agent':profile,'prompt':prompt,'session_name':session,'caller_id':parent['id'],'working_directory':str(project),'teardown':False,'timeout':660,'prompt_redelivery':False},timeout=760)
  data=response.json();(root/(provider+'-response-private.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2));step['http_status']=response.status_code;step['response_status']=data.get('status');step['terminal_id']=data.get('terminal_id') or (data.get('detail',{}).get('terminal_id') if isinstance(data.get('detail'),dict) else None)
  children=requests.get(server.url+'/terminals/'+parent['id']+'/children',timeout=30);children.raise_for_status();receipts=children.json();(root/'children-private.json').write_text(json.dumps(receipts,indent=2));receipt=next((x for x in receipts if x.get('terminal_id')==step['terminal_id']),None)
  if receipt: step['receipt']={k:receipt.get(k) for k in ['id','terminal_id','parent_terminal_id','state','settled_at','error_kind']}
  step['status']='passed' if response.status_code==200 and receipt and receipt.get('state')=='succeeded' else 'unsettled_or_failed';save();print('step_result',provider,step['http_status'],step['response_status'],step.get('receipt',{}).get('state'),flush=True)
  if step['status']!='passed':break
 report['after']={str(p.relative_to(project)):hashlib.sha256(p.read_bytes()).hexdigest() for p in project.rglob('*') if p.is_file()};report['changed_files']=[k for k,v in report['after'].items() if report['baseline'].get(k)!=v];report['notes_preserved']=report['baseline']['notes.json']==report['after']['notes.json'];save()
except BaseException as e:
 report['error_type']=type(e).__name__;save();print('run_error',type(e).__name__,flush=True);traceback.print_exc()
finally:
 if server and session:
  try:_delete_session(server,session);report['session_cleanup']='passed'
  except BaseException as e:report['session_cleanup']=type(e).__name__
 if server:server.stop()
 for name in secret_names:
  (root/name).unlink(missing_ok=True)
 report['credential_copies_removed']=True;report['ended_at']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());save();print('isolated_cleanup_done',flush=True)
