import importlib.util,json,sys,tempfile,threading,concurrent.futures,hashlib
from pathlib import Path
from http.server import ThreadingHTTPServer
import requests
project=Path('/mnt/c/users/ferna/onedrive/escritorio/cao-turn-recovery-demo-20261002-f9086a25')
root=Path(Path('/tmp/spec008-real-demo-pointer').read_text())
spec=importlib.util.spec_from_file_location('demo_real_http',project/'api/server.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
data=root/'http-fixture';data.mkdir(exist_ok=True);m.NOTES_FILE=data/'notes.json';m.NOTES_FILE.write_text('[]\n');m.FRONTEND=project/'frontend'
server=ThreadingHTTPServer(('127.0.0.1',0),m.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url='http://127.0.0.1:'+str(server.server_port);proof={'url':url,'checks':[],'real_http':True,'fixture_data_separate_from_project':True}
def check(name,cond):
 if not cond:raise AssertionError(name)
 proof['checks'].append(name)
try:
 check('health',requests.get(url+'/api/health').json()=={'ok':True})
 for path in ['/','/app.js','/styles.css']:check('static '+path,requests.get(url+path).status_code==200)
 for text in ['', '  ',None,42,'x'*2001]:check('invalid text '+str(type(text).__name__)+' '+str(len(text) if isinstance(text,str) else 0),requests.post(url+'/api/notes',json={'text':text}).status_code==400)
 x=requests.post(url+'/api/notes',json={'text':'  Nota real áé  '});check('create',x.status_code==201);n=x.json();check('trim and shape',n['text']=='Nota real áé' and n['done'] is False and set(n)=={'id','text','done'})
 check('PATCH boolean',requests.patch(url+'/api/notes/'+n['id'],json={'done':True}).status_code==200)
 check('invalid PATCH',requests.patch(url+'/api/notes/'+n['id'],json={'done':'true'}).status_code==400)
 check('persisted completion',json.loads(m.NOTES_FILE.read_text())[0]['done'] is True)
 check('DELETE',requests.delete(url+'/api/notes/'+n['id']).status_code==200)
 check('repeat DELETE missing',requests.delete(url+'/api/notes/'+n['id']).status_code==404)
 def create(i):return requests.post(url+'/api/notes',json={'text':f'Concurrente {i}'},timeout=30)
 with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:responses=list(pool.map(create,range(24)))
 check('24 simultaneous creations',all(x.status_code==201 for x in responses))
 notes=requests.get(url+'/api/notes').json();check('no lost update',len(notes)==24 and len({n['id'] for n in notes})==24 and {n['text'] for n in notes}=={f'Concurrente {i}' for i in range(24)})
 check('disk matches HTTP',json.loads(m.NOTES_FILE.read_text())==notes)
 check('malformed JSON',requests.post(url+'/api/notes',data='{bad',headers={'Content-Type':'application/json'}).status_code==400)
 proof['status']='passed'
finally:
 server.shutdown();server.server_close();(root/'http-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2));print(json.dumps(proof,ensure_ascii=False))
