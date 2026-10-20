from pathlib import Path
import time,subprocess,json,re,sys
root=Path(Path('/tmp/spec008-autonomy-pointer').read_text());python='/mnt/c/users/ferna/onedrive/escritorio/caos/.venv/bin/python';results=[];end=time.monotonic()+3900
for phase in [1,2,3]:
 project=root/('project-after-'+str(phase))
 while not project.exists() and time.monotonic()<end:
  r=json.loads((root/'public-results.json').read_text())
  if r.get('ended_at'):break
  time.sleep(5)
 if not project.exists():print('missing_phase_snapshot',phase,flush=True);break
 # Copy completion is observed only once the controller emitted its private phase record.
 while not (root/('phase-'+str(phase)+'-private.json')).exists():time.sleep(1)
 item={'phase':phase}
 for name,cmd in [('http',[python,'/tmp/spec008-autonomy-functional.py',str(phase)]),('unit',[python,'-m','unittest','discover','-v']),('syntax',['node','--check','frontend/app.js'])]:
  x=subprocess.run(cmd,cwd=project,capture_output=True,text=True,timeout=150);(root/(name+'-phase-'+str(phase)+'.log')).write_text(x.stdout+x.stderr);item[name]={'exit_code':x.returncode}
  if name=='unit':
   found=re.search(r'Ran (\d+) tests?',x.stdout+x.stderr);item[name]['test_count']=int(found.group(1)) if found else None
  print('independent_phase',phase,name,x.returncode,item[name].get('test_count'),flush=True)
 results.append(item);(root/'independent-proof.json').write_text(json.dumps(results,indent=2))
print('independent_phase_checks_closed',flush=True)
