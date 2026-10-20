import os,json,time,tempfile,shutil,hashlib,traceback
from pathlib import Path
import requests,frontmatter
from test.fixtures.cao_server import _start_cao_server,_pick_free_port
repo=Path('/mnt/c/users/ferna/onedrive/escritorio/caos');project=repo.parent/'cao-turn-recovery-demo-20261002-f9086a25';root=Path(tempfile.mkdtemp(prefix='cao-spec008-repair-copilot-',dir='/var/tmp'));root.chmod(0o700);Path('/tmp/spec008-repair-copilot-pointer').write_text(str(root));report={'provider':'copilot_cli','root':str(root),'status':'running','notes_before':hashlib.sha256((project/'notes.json').read_bytes()).hexdigest()};server=None;session=None
for name in ['.config/gh/hosts.yml','.config/gh/config.yml']:
 src=Path('/home/felni')/name
 if src.exists():
  dst=root/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst);dst.chmod(0o600)
try:
 server=_start_cao_server(root,_pick_free_port(),extra_env={'CAO_HOME_DIR':str(root/'.aws/cli-agent-orchestrator'),'PATH':str(repo/'.venv/bin')+':'+os.environ['PATH'],'CAO_MEMORY_ENABLED':'false','CAO_PROVIDER_INIT_TIMEOUT':'90'},deadline=120)
 store=root/'.aws/cli-agent-orchestrator/agent-store';store.mkdir(parents=True,exist_ok=True);profile=frontmatter.Post('Perform only the supplied read-only smoke task. Never edit files or credentials. Use the exact requested final marker.',name='repair-copilot',description='Native read-only validation',provider='copilot_cli',allowedTools=['fs_read']);(store/'repair-copilot.md').write_text(frontmatter.dumps(profile))
 x=requests.post(server.url+'/agents/profiles/install',json={'source':'repair-copilot','provider':'copilot_cli'},timeout=120);report['install_http']=x.status_code;x.raise_for_status()
 x=requests.post(server.url+'/sessions',params={'provider':'copilot_cli','agent_profile':'repair-copilot','session_name':'repair-'+root.name[-8:],'working_directory':str(project)},timeout=150);report['create_http']=x.status_code
 if x.status_code!=201:report['status']='initialization_unavailable';report['error']=x.json()
 else:
  terminal=x.json();session=terminal['session_name'];marker='COPILOT_REPAIR_'+root.name[-8:];report['terminal_id']=terminal['id'];x=requests.post(server.url+'/terminals/'+terminal['id']+'/input',params={'message':'Read only: reply with exactly '+marker+'. No tools or file changes required.'},timeout=120);report['input_http']=x.status_code;x.raise_for_status();deadline=time.monotonic()+150
  while time.monotonic()<deadline:
   t=requests.get(server.url+'/terminals/'+terminal['id'],timeout=15).json();report['terminal_status']=t.get('status')
   if t.get('status')=='completed':
    x=requests.get(server.url+'/terminals/'+terminal['id']+'/output',params={'mode':'last'},timeout=30);report['result_http']=x.status_code;report['marker_present']=marker in x.text;report['status']='passed' if x.status_code==200 and marker in x.text else 'failed';break
   if t.get('status') in {'error','waiting_user_answer','waiting_quota'}:report['status']='native_prerequisite_blocked';break
   time.sleep(2)
  else:report['status']='no_verified_native_completion'
except Exception as exc:report['status']='unavailable';report['exception_type']=type(exc).__name__;print('native exception',type(exc).__name__,flush=True)
finally:
 if server and session:
  try:report['cleanup_http']=requests.delete(server.url+'/sessions/'+session,timeout=120).status_code
  except Exception:report['cleanup_http']=None
 if server:server.stop()
 for name in ['.config/gh/hosts.yml','.config/gh/config.yml']:(root/name).unlink(missing_ok=True)
 report['notes_preserved']=hashlib.sha256((project/'notes.json').read_bytes()).hexdigest()==report['notes_before'];report['closed']=True;(root/'result.json').write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
