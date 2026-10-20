import sys,json,importlib.util,tempfile,threading,concurrent.futures,subprocess
from pathlib import Path
from http.server import ThreadingHTTPServer
import requests
root=Path(Path('/tmp/spec008-autonomy-pointer').read_text());phase=int(sys.argv[1]);project=Path(sys.argv[2]) if len(sys.argv)>2 else root/('project-after-'+str(phase));spec=importlib.util.spec_from_file_location('demo_autonomous_final',project/'api/server.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);storage=root/('http-functional-'+str(phase));storage.mkdir(exist_ok=True);m.NOTES_FILE=storage/'notes.json';m.NOTES_FILE.write_text('[]\n');m.FRONTEND=project/'frontend';server=ThreadingHTTPServer(('127.0.0.1',0),m.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url='http://127.0.0.1:'+str(server.server_port);p={'phase':phase,'real_http':True,'fixture_data_separate':True,'checks':[]}
def check(name,cond):
 if not cond:raise AssertionError(name)
 p['checks'].append(name)
try:
 x=requests.post(url+'/api/notes',json={'text':'Primera'});check('POST existing behavior',x.status_code==201);n=x.json();id=n['id'];path='/api/notes/'+id
 x=requests.patch(url+path,json={'done':True});check('done existing behavior',x.status_code==200)
 x=requests.patch(url+path,json={'text':'  Editada ñ  '});check('text edit',x.status_code==200);check('edit preserves ID and done',x.json()=={'id':id,'text':'Editada ñ','done':True})
 x=requests.patch(url+path,json={'text':'Ambos','done':False});check('atomic text and done',x.status_code==200 and x.json()=={'id':id,'text':'Ambos','done':False})
 for payload in [{},{'text':''},{'text':'  '},{'text':3},{'text':'x'*2001},{'text':'No guardar','done':'false'}]:
  x=requests.patch(url+path,json=payload);check('invalid edit rejected '+str(len(p['checks'])),x.status_code==400);check('invalid edit preserves data '+str(len(p['checks'])),requests.get(url+'/api/notes').json()==[{'id':id,'text':'Ambos','done':False}])
 x=requests.patch(url+'/api/notes/missing',json={'text':'Valid'});check('missing edit',x.status_code==404)
 if phase>=2:
  x=requests.post(url+'/api/notes',json={'text':'Segunda'});n2=x.json();requests.patch(url+'/api/notes/'+n2['id'],json={'done':True});stats=requests.get(url+'/api/stats');check('stats endpoint',stats.status_code==200);v=stats.json();check('stats correct',v.get('total')==2 and v.get('pending')==1 and v.get('completed')==1)
 if phase>=3:
  x=requests.delete(url+'/api/notes/completed');check('bulk cleanup',x.status_code==200 and x.json().get('deleted_count')==1);check('pending preserved',requests.get(url+'/api/notes').json()==[{'id':id,'text':'Ambos','done':False}]);x=requests.delete(url+'/api/notes/completed');check('empty cleanup idempotent',x.status_code==200 and x.json().get('deleted_count')==0);check('stats after cleanup',requests.get(url+'/api/stats').json().get('total')==1)
 check('DELETE individual retained',requests.delete(url+path).status_code==200)
 for path in ['/','/app.js','/styles.css']:check('static '+path,requests.get(url+path).status_code==200)
 def create(i):return requests.post(url+'/api/notes',json={'text':f'Concurrente {i}'},timeout=30)
 with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:rs=list(pool.map(create,range(24)))
 notes=requests.get(url+'/api/notes').json();check('24 concurrent creates persist',all(x.status_code==201 for x in rs) and len(notes)==24 and len({n['id'] for n in notes})==24);check('disk matches HTTP',json.loads(m.NOTES_FILE.read_text())==notes)
 p['status']='passed'
except BaseException as e:p['status']='failed';p['error']=str(e);raise
finally:
 server.shutdown();server.server_close();(root/('http-phase-'+str(phase)+'.json')).write_text(json.dumps(p,indent=2));print(json.dumps(p))
