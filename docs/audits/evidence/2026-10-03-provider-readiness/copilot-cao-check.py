import os,sys,json,time,shutil,hashlib,sqlite3
from pathlib import Path
import requests,frontmatter
repo=Path('/mnt/c/users/ferna/onedrive/escritorio/caos');sys.path.insert(0,str(repo))
from test.fixtures.cao_server import _start_cao_server,_pick_free_port
root=Path('/var/tmp/cao-providers-20261003-u29582w6');home=root/'cao-home';home.mkdir(mode=0o700)
for name in ('.config/gh/hosts.yml','.copilot/config.json'):
 src=Path('/home/felni')/name
 if src.exists():
  dst=home/name;dst.parent.mkdir(parents=True,exist_ok=True,mode=0o700);shutil.copyfile(src,dst);dst.chmod(0o600)
project=repo.parent/'cao-turn-recovery-demo-20261002-f9086a25'
notes=hashlib.sha256((project/'notes.json').read_bytes()).hexdigest();port=_pick_free_port()
extra={'CAO_HOME_DIR':str(home/'.aws/cli-agent-orchestrator'),'PATH':str(repo/'.venv/bin')+':'+os.environ['PATH'],'PYTHONPATH':str(repo/'src')+':'+str(repo),'CAO_MEMORY_ENABLED':'false','CAO_PROVIDER_INIT_TIMEOUT':'90'}
server=None;session=None;result={'provider':'copilot_cli','started_at':time.time(),'checks':{},'turns':[]}
def save(): (root/'copilot-cao-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
try:
 server=_start_cao_server(home,port,extra_env=extra,deadline=90)
 store=home/'.aws/cli-agent-orchestrator/agent-store';store.mkdir(parents=True,exist_ok=True)
 meta={'name':'copilot-check','description':'Native provider acceptance','provider':'copilot_cli','role':'developer','skills':[],'allowedTools':['fs_*','execute_bash','@cao-mcp-server'],'provider_init_timeout':90}
 prompt='Complete only the bounded task received. Read-only project. No edits, installs, commits, subagents, polling or repeated tasks. After completing task, give final response and stop. Use native tools as needed.'
 (store/'copilot-check.md').write_text(frontmatter.dumps(frontmatter.Post(prompt,**meta)))
 r=requests.post(server.url+'/agents/profiles/install',json={'source':'copilot-check','provider':'copilot_cli'},timeout=120);result['profile_install_http']=r.status_code;r.raise_for_status()
 r=requests.post(server.url+'/sessions',params={'provider':'copilot_cli','agent_profile':'copilot-check','session_name':'cao-copilot-20261003','working_directory':str(project)},timeout=150);result['create_http']=r.status_code
 if not r.ok:result['create_error']=r.text;save();r.raise_for_status()
 term=r.json();tid=term['id'];session=term['session_name'];result['terminal_id']=tid;result['session_name']=session;save();print('terminal_created',tid,flush=True)
 for marker,message in [('CAO_COPILOT_DEMO_DONE','Run python3 -m unittest -q test_api in this project. Read api/server.py and frontend/app.js. Report exact test count and one concrete improvement suggestion in Spanish. Do not edit anything. End with CAO_COPILOT_DEMO_DONE.'),('CAO_COPILOT_SECOND_DONE','New independent turn: only read README.md and identify project purpose in one Spanish sentence. Do not run tests again or edit files. End with CAO_COPILOT_SECOND_DONE.')]:
  r=requests.post(server.url+f'/terminals/{tid}/input',params={'message':message},timeout=30);r.raise_for_status();turn={'marker':marker,'input':r.json(),'status':'pending'};result['turns'].append(turn);save();end=time.time()+180
  while time.time()<end:
   t=requests.get(server.url+f'/terminals/{tid}',timeout=15).json();turn['last_status']=t.get('status')
   if t.get('status') in ('completed','idle'):
    output=requests.get(server.url+f'/terminals/{tid}/output',params={'mode':'last'},timeout=20)
    if output.ok and marker in output.json().get('output',''):
     turn['status']='passed';turn['output']=output.json()['output'];break
   if t.get('status')=='error':turn['status']='error';break
   time.sleep(2)
  if turn['status']=='pending':turn['status']='timeout'
  save();print('turn_result',turn['status'],turn['last_status'],flush=True)
  if turn['status']!='passed':break
 result['checks']={'two_distinct_native_turns':len(result['turns'])==2 and all(t['status']=='passed' for t in result['turns']),'notes_unchanged':notes==hashlib.sha256((project/'notes.json').read_bytes()).hexdigest(),'no_operator_corrective_inputs':True}
except Exception as e:result['error_type']=type(e).__name__;print('error',type(e).__name__,flush=True)
finally:
 if server and session:
  try:result['cleanup_http']=requests.delete(server.url+'/sessions/'+session,timeout=90).status_code
  except Exception as e:result['cleanup_error']=type(e).__name__
 if server:server.stop()
 for name in ('.config/gh/hosts.yml','.copilot/config.json'):(home/name).unlink(missing_ok=True)
 result['credential_copies_removed']=True;result['ended_at']=time.time();save();print('check_closed',flush=True)
