import importlib.util,json,threading,requests,hashlib
from pathlib import Path
from http.server import ThreadingHTTPServer
root=Path(Path('/tmp/spec008-autonomy-pointer').read_text());project=Path('/mnt/c/users/ferna/onedrive/escritorio/cao-turn-recovery-demo-20261002-f9086a25');s=importlib.util.spec_from_file_location('actual_demo',project/'api/server.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);fixture=root/'cleanup-http-fixture';fixture.mkdir(exist_ok=True);m.NOTES_FILE=fixture/'notes.json';m.NOTES_FILE.write_text('[]\n');m.FRONTEND=project/'frontend';server=ThreadingHTTPServer(('127.0.0.1',0),m.Handler);threading.Thread(target=server.serve_forever,daemon=True).start();url=f'http://127.0.0.1:{server.server_port}';out={'checks':{},'source_sha256':hashlib.sha256((project/'api/server.py').read_bytes()).hexdigest(),'separate_fixture_data':True}
def request(method,path,**kw):return requests.request(method,url+path,timeout=10,**kw)
try:
 a=request('POST','/api/notes',json={'text':'keep'}).json();b=request('POST','/api/notes',json={'text':'remove'}).json();request('PATCH','/api/notes/'+b['id'],json={'done':True})
 x=request('GET','/api/stats');out['checks']['stats_endpoint']={'passed':x.status_code==200,'http_status':x.status_code}
 x=request('DELETE','/api/notes/completed');out['checks']['cleanup_deleted_count_one']={'passed':x.status_code==200 and x.json()=={'deleted_count':1},'http_status':x.status_code,'response':x.json()}
 remaining=request('GET','/api/notes').json();out['checks']['pending_preserved']={'passed':len(remaining)==1 and remaining[0]['id']==a['id'] and remaining[0]['done']==False}
 x=request('DELETE','/api/notes/completed');out['checks']['cleanup_idempotent']={'passed':x.status_code==200 and x.json()=={'deleted_count':0}}
 out['status']='passed' if all(c['passed'] for c in out['checks'].values()) else 'failed'
finally:server.shutdown();server.server_close();(root/'cleanup-http-functional.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))
